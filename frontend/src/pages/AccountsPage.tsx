import { Button, Card, Form, Input, InputNumber, Modal, Space, Switch, Table, Tag, Typography } from 'antd';
import { EditOutlined, KeyOutlined } from '@ant-design/icons';
import { useEffect, useMemo, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from './pageUtils';
import type { Account, CredentialRotation, ListResponse } from './types';

const TOKEN_TTL_DEFAULT = 900;
const TOKEN_TTL_MIN = 60;
const TOKEN_TTL_MAX = 3600;

function statusTag(status: string) {
  const color = status === 'active' || status === 'completed' ? 'green' : status === 'failed' ? 'red' : 'blue';
  return <Tag color={color}>{status}</Tag>;
}

function isK8sProtocol(protocol: string): boolean {
  const value = protocol.toLowerCase();
  return value === 'k8s' || value === 'kubernetes';
}

type AccountEditValues = {
  secret_id: string;
  status: string;
  rotation_policy: string;
  use_token_request: boolean;
  token_ttl_seconds?: number | null;
};

export function AccountsPage() {
  const { api } = useAuth();
  const toast = useApiMessage();
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [scheduling, setScheduling] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editForm] = Form.useForm<AccountEditValues>();
  const useTokenRequest = Form.useWatch('use_token_request', editForm);
  const accounts = useApiData(() => api.get<ListResponse<Account>>('/api/v1/accounts/'), []);
  const rotations = useApiData(
    () =>
      selectedAccountId
        ? api.get<ListResponse<CredentialRotation>>(`/api/v1/accounts/${selectedAccountId}/rotations`)
        : Promise.resolve({ items: [], total: 0 }),
    [selectedAccountId]
  );

  const selectedAccount = useMemo(
    () => accounts.data?.items.find((item) => item.id === selectedAccountId) ?? null,
    [accounts.data, selectedAccountId]
  );

  useEffect(() => {
    if (!selectedAccountId && accounts.data?.items[0]) {
      setSelectedAccountId(accounts.data.items[0].id);
    }
  }, [accounts.data, selectedAccountId]);

  const scheduleRotation = async () => {
    if (!selectedAccountId) return;
    setScheduling(true);
    try {
      await api.post<CredentialRotation>(`/api/v1/accounts/${selectedAccountId}/rotations`, {
        reason: 'console requested rotation'
      });
      toast.success('已调度凭据轮换');
      rotations.reload();
    } catch (error) {
      toast.error(getErrorMessage(error));
    } finally {
      setScheduling(false);
    }
  };

  const openEdit = () => {
    if (!selectedAccount) return;
    editForm.setFieldsValue({
      secret_id: selectedAccount.secret_id,
      status: selectedAccount.status,
      rotation_policy: selectedAccount.rotation_policy,
      use_token_request: Boolean(selectedAccount.use_token_request),
      token_ttl_seconds: selectedAccount.token_ttl_seconds ?? TOKEN_TTL_DEFAULT
    });
    setEditOpen(true);
  };

  const normalizeTtlOnBlur = () => {
    const raw = editForm.getFieldValue('token_ttl_seconds');
    const numeric = typeof raw === 'number' ? raw : Number(raw);
    if (!Number.isFinite(numeric) || numeric < TOKEN_TTL_MIN || numeric > TOKEN_TTL_MAX) {
      editForm.setFieldValue('token_ttl_seconds', TOKEN_TTL_DEFAULT);
    }
  };

  const saveEdit = async (values: AccountEditValues) => {
    if (!selectedAccount) return;
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        secret_id: values.secret_id,
        status: values.status,
        rotation_policy: values.rotation_policy
      };
      if (isK8sProtocol(selectedAccount.protocol)) {
        payload.use_token_request = Boolean(values.use_token_request);
        if (values.use_token_request) {
          let ttl = typeof values.token_ttl_seconds === 'number' ? values.token_ttl_seconds : Number(values.token_ttl_seconds);
          if (!Number.isFinite(ttl) || ttl < TOKEN_TTL_MIN || ttl > TOKEN_TTL_MAX) {
            ttl = TOKEN_TTL_DEFAULT;
          }
          payload.token_ttl_seconds = ttl;
        }
      }
      await api.patch<Account>(`/api/v1/accounts/${selectedAccount.id}`, payload);
      toast.success('账号已更新');
      setEditOpen(false);
      accounts.reload();
    } catch (error) {
      toast.error(getErrorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  const loading = accounts.loading || rotations.loading;

  return (
    <section className="jg-page">
      <div className="jg-page-header">
        <div>
          <Typography.Title level={2}>账号托管与凭据轮换</Typography.Title>
          <Typography.Text type="secondary">
            查看当前租户可见资产账号，调度后端 CredentialRotation 任务；控制台只展示 Vault secret 引用。
          </Typography.Text>
        </div>
        <Space>
          <Tag color="blue">{accounts.data?.total ?? 0} Accounts</Tag>
          <Tag color="cyan">{rotations.data?.total ?? 0} Rotations</Tag>
          <Button
            icon={<EditOutlined />}
            aria-label="编辑账号"
            disabled={!selectedAccount}
            onClick={openEdit}
          >
            编辑账号
          </Button>
          <Button
            type="primary"
            icon={<KeyOutlined />}
            aria-label="调度轮换"
            loading={scheduling}
            disabled={!selectedAccountId}
            onClick={scheduleRotation}
          >
            调度轮换
          </Button>
        </Space>
      </div>

      {loading ? <LoadingState /> : null}
      {accounts.error ? <ErrorState message={accounts.error} onRetry={accounts.reload} /> : null}
      {rotations.error ? <ErrorState message={rotations.error} onRetry={rotations.reload} /> : null}

      <div className="jg-card-grid">
        <Card title="Accounts">
          <Table
            rowKey="id"
            dataSource={accounts.data?.items ?? []}
            pagination={false}
            size="small"
            rowSelection={{
              type: 'radio',
              selectedRowKeys: selectedAccountId ? [selectedAccountId] : [],
              onChange: (keys) => setSelectedAccountId(Number(keys[0]))
            }}
            columns={[
              { title: '账号', dataIndex: 'username' },
              { title: '资产', dataIndex: 'asset_id' },
              { title: '协议', dataIndex: 'protocol' },
              { title: 'Secret 引用', dataIndex: 'secret_id' },
              { title: 'Project', dataIndex: 'project_id', render: (value: string | null) => value ?? '未绑定' },
              { title: '状态', dataIndex: 'status', render: statusTag },
              { title: '轮换策略', dataIndex: 'rotation_policy' }
            ]}
          />
        </Card>

        <Card title="Credential rotations">
          <Table
            rowKey="id"
            dataSource={rotations.data?.items ?? []}
            pagination={false}
            size="small"
            columns={[
              { title: 'ID', dataIndex: 'id' },
              { title: '状态', dataIndex: 'status', render: statusTag },
              { title: '原因', dataIndex: 'reason' },
              { title: '请求人', dataIndex: 'requested_by' },
              { title: '计划时间', dataIndex: 'scheduled_at', render: (value: string | null) => value ?? '立即' }
            ]}
          />
        </Card>
      </div>

      <Modal
        title="编辑账号"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => editForm.submit()}
        confirmLoading={saving}
        destroyOnHidden
      >
        <Form form={editForm} layout="vertical" onFinish={(values) => void saveEdit(values)}>
          <Form.Item label="Secret 引用" name="secret_id" rules={[{ required: true, message: '请输入 Secret 引用' }]}>
            <Input />
          </Form.Item>
          <Form.Item label="状态" name="status" rules={[{ required: true, message: '请输入状态' }]}>
            <Input />
          </Form.Item>
          <Form.Item label="轮换策略" name="rotation_policy" rules={[{ required: true, message: '请输入轮换策略' }]}>
            <Input />
          </Form.Item>
          {selectedAccount && isK8sProtocol(selectedAccount.protocol) ? (
            <>
              <Form.Item
                label="开启短期令牌"
                name="use_token_request"
                valuePropName="checked"
                extra="开启后，连接使用短期令牌，过期需重新建连。"
              >
                <Switch />
              </Form.Item>
              {useTokenRequest ? (
                <Form.Item label="令牌有效期（秒）" name="token_ttl_seconds">
                  <InputNumber
                    min={TOKEN_TTL_MIN}
                    max={TOKEN_TTL_MAX}
                    style={{ width: '100%' }}
                    onBlur={normalizeTtlOnBlur}
                  />
                </Form.Item>
              ) : null}
            </>
          ) : null}
        </Form>
      </Modal>
    </section>
  );
}
