import { Button, Card, Empty, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMemo, useState } from 'react';
import { useAuth } from '../../auth/AuthContext';
import { ErrorState, LoadingState } from '../../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from '../pageUtils';

type SettingItem = { key: string; value: boolean | number | string };
type SettingList = { items: SettingItem[] };
type LabelRecord = { id: number; name: string; color: string; asset_ids: number[] };
type LabelList = { items: LabelRecord[]; total: number };
type LeakRecord = { id: number; sha256: string; created_at: string };
type LeakList = { items: LeakRecord[]; total: number; builtin_count: number };
type ReportRecord = {
  id: number | null;
  name: string;
  template_key: string;
  description: string;
  builtin: boolean;
};
type ReportList = { items: ReportRecord[]; total: number };
type ReportRun = { template_key: string; result: Record<string, unknown> };

const SETTING_LABELS: Record<string, string> = {
  session_idle_timeout_minutes: '会话空闲超时（分钟）',
  password_min_length: '密码最小长度',
  weak_password_check_enabled: '启用泄露密码检查',
  ui_timezone: '界面时区'
};

const PREFERENCE_LABELS: Record<string, string> = {
  locale: '语言',
  page_size: '每页条数',
  theme: '主题'
};

function settingValue(items: SettingItem[] | undefined, key: string): boolean | number | string | undefined {
  return items?.find((item) => item.key === key)?.value;
}

export function GovernancePreferenceCard() {
  const { api } = useAuth();
  const messages = useApiMessage();
  const prefs = useApiData(() => api.get<SettingList>('/api/v1/governance/preferences'), []);
  const [saving, setSaving] = useState(false);

  const save = async (values: { locale: string; page_size: number; theme: string }) => {
    setSaving(true);
    try {
      await api.put('/api/v1/governance/preferences', {
        items: [
          { key: 'locale', value: values.locale },
          { key: 'page_size', value: values.page_size },
          { key: 'theme', value: values.theme }
        ]
      });
      messages.success('已保存偏好');
      prefs.reload();
    } catch (error) {
      messages.error(getErrorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  const items = prefs.data?.items ?? [];

  return (
    <Card title="用户偏好">
      {prefs.loading ? <LoadingState /> : null}
      {prefs.error ? <ErrorState message={prefs.error} onRetry={prefs.reload} /> : null}
      {!prefs.loading && !prefs.error ? (
        <Form
          layout="vertical"
          key={JSON.stringify(items)}
          initialValues={{
            locale: settingValue(items, 'locale') ?? 'zh-CN',
            page_size: settingValue(items, 'page_size') ?? 20,
            theme: settingValue(items, 'theme') ?? 'light'
          }}
          onFinish={(values) => void save(values as { locale: string; page_size: number; theme: string })}
        >
          <Form.Item label={PREFERENCE_LABELS.locale} name="locale">
            <Select options={[{ value: 'zh-CN', label: '中文' }, { value: 'en-US', label: 'English' }]} />
          </Form.Item>
          <Form.Item label={PREFERENCE_LABELS.page_size} name="page_size">
            <InputNumber min={10} max={100} />
          </Form.Item>
          <Form.Item label={PREFERENCE_LABELS.theme} name="theme">
            <Select
              options={[
                { value: 'light', label: '浅色' },
                { value: 'dark', label: '深色' },
                { value: 'system', label: '跟随系统' }
              ]}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saving}>
            保存偏好
          </Button>
        </Form>
      ) : null}
    </Card>
  );
}

export function GovernanceLabelPanels({ canWrite }: { canWrite: boolean }) {
  const { api } = useAuth();
  const messages = useApiMessage();
  const labels = useApiData(() => api.get<LabelList>('/api/v1/governance/labels/'), []);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{ name: string; color: string }>();

  const columns: ColumnsType<LabelRecord> = [
    { title: '名称', dataIndex: 'name' },
    {
      title: '颜色',
      dataIndex: 'color',
      render: (color: string) => <Tag color={color}>{color}</Tag>
    },
    { title: '资产数', render: (_: unknown, record: LabelRecord) => record.asset_ids.length }
  ];

  const items = labels.data?.items ?? [];

  return (
    <>
      <Card
        title="资源标签"
        extra={
          canWrite ? (
            <Button type="primary" onClick={() => setOpen(true)}>
              创建标签
            </Button>
          ) : null
        }
      >
        {labels.loading ? <LoadingState /> : null}
        {labels.error ? <ErrorState message={labels.error} onRetry={labels.reload} /> : null}
        {!labels.loading && !labels.error && items.length === 0 ? (
          <Empty description="还没有资源标签">
            {canWrite ? (
              <Button type="primary" onClick={() => setOpen(true)}>
                创建标签
              </Button>
            ) : null}
          </Empty>
        ) : null}
        {!labels.loading && !labels.error && items.length > 0 ? (
          <Table rowKey="id" pagination={false} dataSource={items} columns={columns} />
        ) : null}
      </Card>
      <Modal
        title="创建标签"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ name: '', color: '#2563eb' }}
          onFinish={async (values) => {
            await api.post('/api/v1/governance/labels/', values);
            messages.success('已创建标签');
            setOpen(false);
            labels.reload();
          }}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item label="颜色" name="color" rules={[{ required: true }]}>
            <Input placeholder="#2563eb" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

export function GovernanceSettingCard() {
  const { api } = useAuth();
  const messages = useApiMessage();
  const settings = useApiData(() => api.get<SettingList>('/api/v1/governance/settings'), []);
  const [saving, setSaving] = useState(false);
  const items = settings.data?.items ?? [];

  const save = async (values: {
    session_idle_timeout_minutes: number;
    password_min_length: number;
    weak_password_check_enabled: boolean;
    ui_timezone: string;
  }) => {
    setSaving(true);
    try {
      await api.put('/api/v1/governance/settings', {
        items: [
          { key: 'session_idle_timeout_minutes', value: values.session_idle_timeout_minutes },
          { key: 'password_min_length', value: values.password_min_length },
          { key: 'weak_password_check_enabled', value: values.weak_password_check_enabled },
          { key: 'ui_timezone', value: values.ui_timezone }
        ]
      });
      messages.success('已保存系统配置');
      settings.reload();
    } catch (error) {
      messages.error(getErrorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title="系统配置">
      {settings.loading ? <LoadingState /> : null}
      {settings.error ? <ErrorState message={settings.error} onRetry={settings.reload} /> : null}
      {!settings.loading && !settings.error ? (
        <Form
          layout="vertical"
          key={JSON.stringify(items)}
          initialValues={{
            session_idle_timeout_minutes: settingValue(items, 'session_idle_timeout_minutes') ?? 30,
            password_min_length: settingValue(items, 'password_min_length') ?? 8,
            weak_password_check_enabled: settingValue(items, 'weak_password_check_enabled') ?? true,
            ui_timezone: settingValue(items, 'ui_timezone') ?? 'Asia/Singapore'
          }}
          onFinish={(values) =>
            void save(
              values as {
                session_idle_timeout_minutes: number;
                password_min_length: number;
                weak_password_check_enabled: boolean;
                ui_timezone: string;
              }
            )
          }
        >
          <Form.Item label={SETTING_LABELS.session_idle_timeout_minutes} name="session_idle_timeout_minutes">
            <InputNumber min={1} max={1440} />
          </Form.Item>
          <Form.Item label={SETTING_LABELS.password_min_length} name="password_min_length">
            <InputNumber min={8} max={128} />
          </Form.Item>
          <Form.Item label={SETTING_LABELS.weak_password_check_enabled} name="weak_password_check_enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item label={SETTING_LABELS.ui_timezone} name="ui_timezone">
            <Input />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saving}>
            保存系统配置
          </Button>
        </Form>
      ) : null}
    </Card>
  );
}

export function GovernanceLeakPasswordCard() {
  const { api } = useAuth();
  const messages = useApiMessage();
  const leaks = useApiData(() => api.get<LeakList>('/api/v1/governance/leak-passwords'), []);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{ password?: string; sha256?: string }>();
  const items = leaks.data?.items ?? [];

  return (
    <>
      <Card
        title="泄露密码库"
        extra={
          <Space>
            <Typography.Text type="secondary">内置 {leaks.data?.builtin_count ?? 0} 条</Typography.Text>
            <Button type="primary" onClick={() => setOpen(true)}>
              添加哈希
            </Button>
          </Space>
        }
      >
        {leaks.loading ? <LoadingState /> : null}
        {leaks.error ? <ErrorState message={leaks.error} onRetry={leaks.reload} /> : null}
        {!leaks.loading && !leaks.error && items.length === 0 ? <Empty description="还没有租户哈希" /> : null}
        {!leaks.loading && !leaks.error && items.length > 0 ? (
          <Table
            rowKey="id"
            pagination={false}
            dataSource={items}
            columns={[
              { title: 'SHA-256', dataIndex: 'sha256' },
              { title: '添加时间', dataIndex: 'created_at' }
            ]}
          />
        ) : null}
      </Card>
      <Modal
        title="添加泄露密码哈希"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={async (values) => {
            await api.post('/api/v1/governance/leak-passwords', values);
            messages.success('已写入哈希，未保存明文');
            setOpen(false);
            form.resetFields();
            leaks.reload();
          }}
        >
          <Form.Item label="明文（仅用于计算哈希，不会回显）" name="password">
            <Input.Password autoComplete="off" />
          </Form.Item>
          <Form.Item label="或直接填写 SHA-256" name="sha256">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

export function GovernanceReportCard() {
  const { api } = useAuth();
  const messages = useApiMessage();
  const reports = useApiData(() => api.get<ReportList>('/api/v1/governance/reports'), []);
  const [runningKey, setRunningKey] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<ReportRun | null>(null);
  const items = reports.data?.items ?? [];

  const columns = useMemo<ColumnsType<ReportRecord>>(
    () => [
      { title: '名称', dataIndex: 'name' },
      { title: '模板', dataIndex: 'template_key' },
      {
        title: '类型',
        render: (_: unknown, record: ReportRecord) => (record.builtin ? '内置' : '已保存')
      },
      {
        title: '操作',
        render: (_: unknown, record: ReportRecord) => (
          <Button
            type="link"
            loading={runningKey === record.template_key + String(record.id)}
            onClick={() => {
              const key = record.template_key + String(record.id);
              setRunningKey(key);
              const body = record.id == null ? { template_key: record.template_key } : { report_id: record.id };
              void api
                .post<ReportRun>('/api/v1/governance/reports/run', body)
                .then((result) => {
                  setLastRun(result);
                  messages.success('报表已生成');
                })
                .catch((error: unknown) => messages.error(getErrorMessage(error)))
                .finally(() => setRunningKey(null));
            }}
          >
            运行
          </Button>
        )
      }
    ],
    [api, messages, runningKey]
  );

  return (
    <Card title="报表中心">
      {reports.loading ? <LoadingState /> : null}
      {reports.error ? <ErrorState message={reports.error} onRetry={reports.reload} /> : null}
      {!reports.loading && !reports.error ? (
        <Table rowKey={(row) => `${row.template_key}-${row.id ?? 'builtin'}`} pagination={false} dataSource={items} columns={columns} />
      ) : null}
      {lastRun ? (
        <Typography.Paragraph copyable style={{ marginTop: 12 }}>
          {String(lastRun.result.report_signature ?? lastRun.result.total ?? lastRun.template_key)}
        </Typography.Paragraph>
      ) : null}
    </Card>
  );
}
