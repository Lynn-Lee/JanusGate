import { Alert, Button, Card, Descriptions, Form, Input, Modal, Select, Space, Table, Tag, TimePicker, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import { useMemo, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { UserSelect, type DirectoryUser } from '../components/UserSelect';
import { getErrorMessage, useApiData, useApiMessage } from './pageUtils';
import type { Asset, AssetNode, ListResponse } from './types';

type Health = { status: string; version?: string };
type LicenseSummary = {
  configured_edition: string;
  effective_edition: string;
  license_status: string;
  enabled_features: string[];
  disabled_features: string[];
  expires_at: string | null;
};
type LicenseConfigForm = {
  configured_edition: 'community' | 'enterprise';
  license_verifier: 'hmac' | 'ed25519' | 'external-http';
  license_key: string;
  license_signing_secret?: string;
  license_public_key?: string;
};

type OverlayAction = 'accept' | 'reject';
type LoginAcl = {
  id: string;
  name: string;
  priority: number;
  action: OverlayAction;
  subject_id: string;
  subject_username?: string;
};
type LoginAssetAcl = {
  id: string;
  name: string;
  priority: number;
  action: OverlayAction;
  resource_type: 'node' | 'asset';
  resource_id: string;
  ip_cidr: string | null;
  time_start: string | null;
  time_end: string | null;
};
type ConnectMethodAcl = {
  id: string;
  name: string;
  priority: number;
  action: OverlayAction;
  protocol: string;
  resource_type: string | null;
  resource_id: string | null;
};

const ACTION_OPTIONS = [
  { value: 'accept', label: '允许' },
  { value: 'reject', label: '拒绝' }
];
const PROTOCOL_OPTIONS = [
  { value: 'ssh', label: 'ssh' },
  { value: 'k8s', label: 'k8s' },
  { value: 'sftp', label: 'sftp' }
];

const licenseStatusColor = (status: string) => {
  if (status === 'active') return 'green';
  if (status === 'not_configured') return 'gold';
  return 'red';
};

function tokenPermissions(token: string | null): string[] {
  if (!token) {
    return [];
  }
  const payload = token.split('.')[1];
  if (!payload) {
    return [];
  }
  try {
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
    const json = JSON.parse(atob(normalized)) as { permissions?: unknown };
    return Array.isArray(json.permissions) ? json.permissions.map(String) : [];
  } catch {
    return [];
  }
}

function effectivePermissions(userPermissions: string[] | undefined, token: string | null): string[] {
  if (userPermissions && userPermissions.length > 0) {
    return userPermissions;
  }
  return tokenPermissions(token);
}

function canReadAcls(isSuperuser: boolean, permissions: string[]): boolean {
  return isSuperuser || permissions.includes('admin') || permissions.includes('acl:read');
}

function canWriteAcls(isSuperuser: boolean, permissions: string[]): boolean {
  return isSuperuser || permissions.includes('admin') || permissions.includes('acl:write');
}

function actionLabel(action: OverlayAction): string {
  return action === 'accept' ? '允许' : '拒绝';
}

function looksLikeUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

function loginAclUserLabel(record: LoginAcl, users: DirectoryUser[]): string {
  if (record.subject_username) {
    return record.subject_username;
  }
  const match = users.find((item) => String(item.id) === String(record.subject_id));
  if (match?.username) {
    return match.username;
  }
  if (!record.subject_id || looksLikeUuid(record.subject_id)) {
    return '';
  }
  return '';
}

export function SettingsPage() {
  const { api, user, token } = useAuth();
  const messages = useApiMessage();
  const health = useApiData(() => api.get<Health>('/health'), []);
  const license = useApiData(() => api.get<LicenseSummary>('/api/v1/admin/license-summary'), []);
  const [licenseFormOpen, setLicenseFormOpen] = useState(false);
  const [licenseSubmitError, setLicenseSubmitError] = useState('');
  const [licenseSubmitting, setLicenseSubmitting] = useState(false);
  const [savedLicense, setSavedLicense] = useState<LicenseSummary | null>(null);
  const licenseSummary = savedLicense ?? license.data;
  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || '同源 / Vite 代理';
  const permissions = effectivePermissions(user?.permissions, token);
  const showOverlayAcls = canReadAcls(Boolean(user?.is_superuser), permissions);
  const writeOverlayAcls = canWriteAcls(Boolean(user?.is_superuser), permissions);

  const saveLicenseConfig = async (values: LicenseConfigForm) => {
    setLicenseSubmitting(true);
    setLicenseSubmitError('');
    try {
      const summary = await api.post<LicenseSummary>('/api/v1/admin/license-config', {
        configured_edition: values.configured_edition,
        license_verifier: values.license_verifier,
        license_key: values.license_key,
        license_signing_secret: values.license_signing_secret ?? '',
        license_public_key: values.license_public_key ?? ''
      });
      setSavedLicense(summary);
      setLicenseFormOpen(false);
      messages.success('License 配置已保存');
    } catch (err: unknown) {
      setLicenseSubmitError(getErrorMessage(err));
    } finally {
      setLicenseSubmitting(false);
    }
  };

  return (
    <section className="jg-page">
      <div className="jg-page-header">
        <div>
          <Typography.Title level={2}>系统设置</Typography.Title>
          <Typography.Text type="secondary">展示运行时和安全配置摘要；MVP 不提供密钥在线编辑。</Typography.Text>
        </div>
      </div>
      <div className="jg-card-grid">
        <Card title="运行时状态">
          {health.loading ? <LoadingState /> : null}
          {health.error ? <ErrorState message={health.error} onRetry={health.reload} /> : null}
          {health.data ? (
            <Descriptions column={1} size="small">
              <Descriptions.Item label="当前版本">{health.data.version ?? '0.1.0'}</Descriptions.Item>
              <Descriptions.Item label="健康状态"><Tag color={health.data.status === 'ok' ? 'green' : 'red'}>{health.data.status}</Tag></Descriptions.Item>
              <Descriptions.Item label="API base URL">{apiBaseUrl}</Descriptions.Item>
            </Descriptions>
          ) : null}
        </Card>
        <Card title="安全配置摘要">
          <Space direction="vertical">
            <Tag color={user?.totp_enabled ? 'green' : 'gold'}>{user?.totp_enabled ? 'MFA 已启用' : 'MFA 未启用'}</Tag>
            <Tag color="blue">JWT Bearer 访问令牌</Tag>
            <Tag color="blue">统一 ErrorResponse / request_id 追踪</Tag>
            <Tag color="purple">Secret 通过环境变量 / SecretProvider 注入</Tag>
          </Space>
        </Card>
        <Card title="License / Edition 边界">
          {license.loading ? <LoadingState /> : null}
          {license.error ? <ErrorState message={license.error} onRetry={license.reload} /> : null}
          {licenseSummary ? (
            <Space direction="vertical" size="middle">
              <Descriptions column={1} size="small">
                <Descriptions.Item label="Configured edition">
                  configured: {licenseSummary.configured_edition}
                </Descriptions.Item>
                <Descriptions.Item label="Effective edition">
                  effective: {licenseSummary.effective_edition}
                </Descriptions.Item>
                <Descriptions.Item label="License status">
                  <Tag color={licenseStatusColor(licenseSummary.license_status)}>
                    {licenseSummary.license_status}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="Expires at">{licenseSummary.expires_at ?? '未配置'}</Descriptions.Item>
              </Descriptions>
              <Space direction="vertical" size="small">
                <Typography.Text type="secondary">启用能力</Typography.Text>
                <Space wrap>
                  {licenseSummary.enabled_features.map((feature) => (
                    <Tag color="green" key={feature}>{feature}</Tag>
                  ))}
                </Space>
                <Typography.Text type="secondary">禁用能力</Typography.Text>
                <Space wrap>
                  {licenseSummary.disabled_features.map((feature) => (
                    <Tag key={feature}>{feature}</Tag>
                  ))}
                </Space>
              </Space>
              <Button onClick={() => setLicenseFormOpen((open) => !open)}>保存 License 配置</Button>
              {licenseFormOpen ? (
                <Form
                  layout="vertical"
                  initialValues={{
                    configured_edition: 'enterprise',
                    license_verifier: 'hmac',
                    license_key: '',
                    license_signing_secret: '',
                    license_public_key: ''
                  }}
                  onFinish={(values) => void saveLicenseConfig(values as LicenseConfigForm)}
                >
                  {licenseSubmitError ? <Alert showIcon type="error" message={licenseSubmitError} /> : null}
                  <Form.Item label="Configured edition" name="configured_edition" rules={[{ required: true }]}>
                    <Select
                      options={[
                        { value: 'community', label: 'community' },
                        { value: 'enterprise', label: 'enterprise' }
                      ]}
                    />
                  </Form.Item>
                  <Form.Item label="License verifier" name="license_verifier" rules={[{ required: true }]}>
                    <Select
                      options={[
                        { value: 'hmac', label: 'hmac' },
                        { value: 'ed25519', label: 'ed25519' },
                        { value: 'external-http', label: 'external-http' }
                      ]}
                    />
                  </Form.Item>
                  <Form.Item label="License key" name="license_key" rules={[{ required: true, message: '请输入 license key' }]}>
                    <Input.TextArea rows={3} autoComplete="off" />
                  </Form.Item>
                  <Form.Item label="Signing secret" name="license_signing_secret">
                    <Input.Password autoComplete="off" />
                  </Form.Item>
                  <Form.Item label="Public key" name="license_public_key">
                    <Input.TextArea rows={2} autoComplete="off" />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" loading={licenseSubmitting}>
                    激活 License
                  </Button>
                </Form>
              ) : null}
            </Space>
          ) : null}
        </Card>
        <Card title="部署信息摘要">
          <Descriptions column={1} size="small">
            <Descriptions.Item label="环境">Docker Compose / Helm 均有基线</Descriptions.Item>
            <Descriptions.Item label="数据库">PostgreSQL / SQLAlchemy async</Descriptions.Item>
            <Descriptions.Item label="缓存">Redis 用于 MFA / Token 状态</Descriptions.Item>
            <Descriptions.Item label="边界">不展示或编辑真实密钥、Token、连接串</Descriptions.Item>
          </Descriptions>
        </Card>
        {showOverlayAcls ? <OverlayAclPanels canWrite={writeOverlayAcls} /> : null}
      </div>
    </section>
  );
}

function OverlayAclPanels({ canWrite }: { canWrite: boolean }) {
  const { api } = useAuth();
  const messages = useApiMessage();
  const loginAcls = useApiData(() => api.get<ListResponse<LoginAcl>>('/api/v1/login-acls/'), []);
  const loginAssetAcls = useApiData(() => api.get<ListResponse<LoginAssetAcl>>('/api/v1/login-asset-acls/'), []);
  const connectMethodAcls = useApiData(() => api.get<ListResponse<ConnectMethodAcl>>('/api/v1/connect-method-acls/'), []);
  const directoryUsers = useApiData(() => api.get<{ items: DirectoryUser[]; total: number }>('/api/v1/users/'), []);
  const nodes = useApiData(() => api.get<ListResponse<AssetNode>>('/api/v1/asset-nodes/'), []);
  const assets = useApiData(() => api.get<Asset[]>('/api/v1/assets/'), []);
  const [loginOpen, setLoginOpen] = useState(false);
  const [loginEditing, setLoginEditing] = useState<LoginAcl | null>(null);
  const [assetOpen, setAssetOpen] = useState(false);
  const [assetEditing, setAssetEditing] = useState<LoginAssetAcl | null>(null);
  const [methodOpen, setMethodOpen] = useState(false);
  const [methodEditing, setMethodEditing] = useState<ConnectMethodAcl | null>(null);
  const [loginForm] = Form.useForm();
  const [assetForm] = Form.useForm();
  const [methodForm] = Form.useForm();

  const nodeOptions = useMemo(
    () => (nodes.data?.items ?? []).filter((node) => !node.is_root).map((node) => ({ value: node.id, label: node.name })),
    [nodes.data]
  );
  const assetOptions = useMemo(
    () => (assets.data ?? []).map((asset) => ({ value: String(asset.id), label: asset.name })),
    [assets.data]
  );

  const confirmDelete = (onOk: () => Promise<void>) => {
    Modal.confirm({
      title: '确定删除这条？',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk
    });
  };

  const writeButtons = (onAdd: () => void) =>
    canWrite ? (
      <Button type="primary" onClick={onAdd}>
        新增
      </Button>
    ) : null;

  const loginColumns: ColumnsType<LoginAcl> = [
    { title: '名称', dataIndex: 'name' },
    {
      title: '用户',
      render: (_: unknown, record: LoginAcl) => loginAclUserLabel(record, directoryUsers.data?.items ?? [])
    },
    { title: '优先级', dataIndex: 'priority', width: 90 },
    { title: '动作', dataIndex: 'action', render: (value: OverlayAction) => actionLabel(value) },
    ...(canWrite
      ? [
          {
            title: '操作',
            render: (_: unknown, record: LoginAcl) => (
              <Space>
                <Button
                  type="link"
                  onClick={() => {
                    setLoginEditing(record);
                    loginForm.setFieldsValue(record);
                    setLoginOpen(true);
                  }}
                >
                  编辑
                </Button>
                <Button
                  type="link"
                  danger
                  onClick={() =>
                    confirmDelete(async () => {
                      await api.delete(`/api/v1/login-acls/${record.id}`);
                      messages.success('已删除');
                      loginAcls.reload();
                    })
                  }
                >
                  删除
                </Button>
              </Space>
            )
          }
        ]
      : [])
  ];

  const assetColumns: ColumnsType<LoginAssetAcl> = [
    { title: '名称', dataIndex: 'name' },
    { title: '作用对象', render: (_: unknown, record: LoginAssetAcl) => `${record.resource_type}:${record.resource_id}` },
    { title: 'CIDR', dataIndex: 'ip_cidr', render: (value: string | null) => value || '任意 IP' },
    {
      title: '时段',
      render: (_: unknown, record: LoginAssetAcl) =>
        record.time_start && record.time_end ? `${record.time_start}-${record.time_end}` : '全天'
    },
    { title: '优先级', dataIndex: 'priority', width: 90 },
    { title: '动作', dataIndex: 'action', render: (value: OverlayAction) => actionLabel(value) },
    ...(canWrite
      ? [
          {
            title: '操作',
            render: (_: unknown, record: LoginAssetAcl) => (
              <Space>
                <Button
                  type="link"
                  onClick={() => {
                    setAssetEditing(record);
                    assetForm.setFieldsValue({
                      ...record,
                      time_window:
                        record.time_start && record.time_end
                          ? [dayjs(record.time_start, 'HH:mm'), dayjs(record.time_end, 'HH:mm')]
                          : undefined
                    });
                    setAssetOpen(true);
                  }}
                >
                  编辑
                </Button>
                <Button
                  type="link"
                  danger
                  onClick={() =>
                    confirmDelete(async () => {
                      await api.delete(`/api/v1/login-asset-acls/${record.id}`);
                      messages.success('已删除');
                      loginAssetAcls.reload();
                    })
                  }
                >
                  删除
                </Button>
              </Space>
            )
          }
        ]
      : [])
  ];

  const methodColumns: ColumnsType<ConnectMethodAcl> = [
    { title: '名称', dataIndex: 'name' },
    { title: '协议', dataIndex: 'protocol' },
    {
      title: '作用对象',
      render: (_: unknown, record: ConnectMethodAcl) =>
        record.resource_type && record.resource_id ? `${record.resource_type}:${record.resource_id}` : '全部资产'
    },
    { title: '优先级', dataIndex: 'priority', width: 90 },
    { title: '动作', dataIndex: 'action', render: (value: OverlayAction) => actionLabel(value) },
    ...(canWrite
      ? [
          {
            title: '操作',
            render: (_: unknown, record: ConnectMethodAcl) => (
              <Space>
                <Button
                  type="link"
                  onClick={() => {
                    setMethodEditing(record);
                    methodForm.setFieldsValue({
                      ...record,
                      apply_to: record.resource_type || 'all'
                    });
                    setMethodOpen(true);
                  }}
                >
                  编辑
                </Button>
                <Button
                  type="link"
                  danger
                  onClick={() =>
                    confirmDelete(async () => {
                      await api.delete(`/api/v1/connect-method-acls/${record.id}`);
                      messages.success('已删除');
                      connectMethodAcls.reload();
                    })
                  }
                >
                  删除
                </Button>
              </Space>
            )
          }
        ]
      : [])
  ];

  return (
    <>
      <Card
        title="登录 ACL"
        extra={writeButtons(() => {
          setLoginEditing(null);
          loginForm.resetFields();
          setLoginOpen(true);
        })}
      >
        <Typography.Paragraph type="secondary">
          只限制从网页或客户端登录，用 API Key 访问不受影响。
        </Typography.Paragraph>
        {loginAcls.loading ? <LoadingState /> : null}
        {loginAcls.error ? <ErrorState message={loginAcls.error} onRetry={loginAcls.reload} /> : null}
        <Table rowKey="id" pagination={false} dataSource={loginAcls.data?.items ?? []} columns={loginColumns} />
      </Card>
      <Card
        title="资产登录 ACL"
        extra={writeButtons(() => {
          setAssetEditing(null);
          assetForm.resetFields();
          setAssetOpen(true);
        })}
      >
        {loginAssetAcls.loading ? <LoadingState /> : null}
        {loginAssetAcls.error ? <ErrorState message={loginAssetAcls.error} onRetry={loginAssetAcls.reload} /> : null}
        <Table rowKey="id" pagination={false} dataSource={loginAssetAcls.data?.items ?? []} columns={assetColumns} />
      </Card>
      <Card
        title="连接方式 ACL"
        extra={writeButtons(() => {
          setMethodEditing(null);
          methodForm.resetFields();
          setMethodOpen(true);
        })}
      >
        {connectMethodAcls.loading ? <LoadingState /> : null}
        {connectMethodAcls.error ? <ErrorState message={connectMethodAcls.error} onRetry={connectMethodAcls.reload} /> : null}
        <Table rowKey="id" pagination={false} dataSource={connectMethodAcls.data?.items ?? []} columns={methodColumns} />
      </Card>

      <Modal
        title={loginEditing ? '编辑登录 ACL' : '新增登录 ACL'}
        open={loginOpen}
        onCancel={() => setLoginOpen(false)}
        onOk={() => loginForm.submit()}
        destroyOnHidden
      >
        <Form
          form={loginForm}
          layout="vertical"
          initialValues={{ action: 'reject', priority: 50 }}
          onFinish={async (values) => {
            const payload = {
              name: values.name,
              priority: values.priority,
              action: values.action,
              subject_id: String(values.subject_id)
            };
            if (loginEditing) {
              await api.patch(`/api/v1/login-acls/${loginEditing.id}`, payload);
            } else {
              await api.post('/api/v1/login-acls/', payload);
            }
            setLoginOpen(false);
            messages.success('已保存');
            loginAcls.reload();
          }}
        >
          <Form.Item label="名称" name="name">
            <Input />
          </Form.Item>
          <Form.Item label="用户" name="subject_id" rules={[{ required: true, message: '请选择用户' }]}>
            <UserSelect />
          </Form.Item>
          <Form.Item label="优先级" name="priority" rules={[{ required: true }]}>
            <Select options={Array.from({ length: 100 }, (_, index) => ({ value: index + 1, label: String(index + 1) }))} />
          </Form.Item>
          <Form.Item label="动作" name="action" rules={[{ required: true }]}>
            <Select options={ACTION_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={assetEditing ? '编辑资产登录 ACL' : '新增资产登录 ACL'}
        open={assetOpen}
        onCancel={() => setAssetOpen(false)}
        onOk={() => assetForm.submit()}
        destroyOnHidden
      >
        <Form
          form={assetForm}
          layout="vertical"
          initialValues={{ action: 'reject', priority: 50, resource_type: 'asset' }}
          onFinish={async (values) => {
            const window = values.time_window as [Dayjs, Dayjs] | undefined;
            const payload = {
              name: values.name,
              priority: values.priority,
              action: values.action,
              resource_type: values.resource_type,
              resource_id: values.resource_id,
              ip_cidr: values.ip_cidr || null,
              time_start: window?.[0]?.format('HH:mm') ?? null,
              time_end: window?.[1]?.format('HH:mm') ?? null
            };
            if (payload.time_start && payload.time_end && payload.time_start > payload.time_end) {
              messages.error('时间窗口不能跨午夜');
              return;
            }
            if (assetEditing) {
              await api.patch(`/api/v1/login-asset-acls/${assetEditing.id}`, payload);
            } else {
              await api.post('/api/v1/login-asset-acls/', payload);
            }
            setAssetOpen(false);
            messages.success('已保存');
            loginAssetAcls.reload();
          }}
        >
          <Form.Item label="名称" name="name">
            <Input />
          </Form.Item>
          <Form.Item label="作用对象类型" name="resource_type" rules={[{ required: true }]}>
            <Select options={[{ value: 'node', label: '节点' }, { value: 'asset', label: '资产' }]} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, next) => prev.resource_type !== next.resource_type}>
            {({ getFieldValue }) => (
              <Form.Item label="作用对象" name="resource_id" rules={[{ required: true, message: '请选择节点或资产' }]}>
                <Select options={getFieldValue('resource_type') === 'node' ? nodeOptions : assetOptions} showSearch optionFilterProp="label" />
              </Form.Item>
            )}
          </Form.Item>
          <Form.Item label="源 IP CIDR（可选）" name="ip_cidr">
            <Input placeholder="例如 10.0.0.0/8，空表示任意 IP" />
          </Form.Item>
          <Form.Item label="每日时段（可选，不跨午夜）" name="time_window">
            <TimePicker.RangePicker format="HH:mm" />
          </Form.Item>
          <Form.Item label="优先级" name="priority" rules={[{ required: true }]}>
            <Select options={Array.from({ length: 100 }, (_, index) => ({ value: index + 1, label: String(index + 1) }))} />
          </Form.Item>
          <Form.Item label="动作" name="action" rules={[{ required: true }]}>
            <Select options={ACTION_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={methodEditing ? '编辑连接方式 ACL' : '新增连接方式 ACL'}
        open={methodOpen}
        onCancel={() => setMethodOpen(false)}
        onOk={() => methodForm.submit()}
        destroyOnHidden
      >
        <Form
          form={methodForm}
          layout="vertical"
          initialValues={{ action: 'reject', priority: 50, apply_to: 'all', protocol: 'ssh' }}
          onFinish={async (values) => {
            const applyTo = values.apply_to as string;
            const payload = {
              name: values.name,
              priority: values.priority,
              action: values.action,
              protocol: values.protocol,
              resource_type: applyTo === 'all' ? null : applyTo,
              resource_id: applyTo === 'all' ? null : values.resource_id
            };
            if (methodEditing) {
              await api.patch(`/api/v1/connect-method-acls/${methodEditing.id}`, payload);
            } else {
              await api.post('/api/v1/connect-method-acls/', payload);
            }
            setMethodOpen(false);
            messages.success('已保存');
            connectMethodAcls.reload();
          }}
        >
          <Form.Item label="名称" name="name">
            <Input />
          </Form.Item>
          <Form.Item label="协议" name="protocol" rules={[{ required: true }]}>
            <Select options={PROTOCOL_OPTIONS} />
          </Form.Item>
          <Form.Item label="作用对象" name="apply_to" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'all', label: '全部资产' },
                { value: 'node', label: '节点' },
                { value: 'asset', label: '资产' }
              ]}
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, next) => prev.apply_to !== next.apply_to}>
            {({ getFieldValue }) =>
              getFieldValue('apply_to') === 'all' ? null : (
                <Form.Item label="节点或资产" name="resource_id" rules={[{ required: true }]}>
                  <Select options={getFieldValue('apply_to') === 'node' ? nodeOptions : assetOptions} showSearch optionFilterProp="label" />
                </Form.Item>
              )
            }
          </Form.Item>
          <Form.Item label="优先级" name="priority" rules={[{ required: true }]}>
            <Select options={Array.from({ length: 100 }, (_, index) => ({ value: index + 1, label: String(index + 1) }))} />
          </Form.Item>
          <Form.Item label="动作" name="action" rules={[{ required: true }]}>
            <Select options={ACTION_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
