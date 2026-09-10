import { Button, Card, Empty, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMemo, useState } from 'react';
import { useAuth } from '../../auth/AuthContext';
import { ErrorState, LoadingState } from '../../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from '../pageUtils';
import type { Asset, ListResponse } from '../types';

type SettingItem = { key: string; value: boolean | number | string };
type LabelItem = { id: number; name: string; color: string; asset_ids: number[] };
type LeakItem = { id: number; sha256: string; created_at: string };
type LeakList = { items: LeakItem[]; total: number; builtin_count: number };

const SETTING_LABELS: Record<string, string> = {
  session_idle_timeout_minutes: '会话空闲超时（分钟）',
  password_min_length: '密码最小长度',
  weak_password_check_enabled: '启用泄露密码校验',
  ui_timezone: '界面时区'
};

export function GovernancePanels({
  canAdmin,
  canReadLabels,
  canWriteLabels
}: {
  canAdmin: boolean;
  canReadLabels: boolean;
  canWriteLabels: boolean;
}) {
  const { api } = useAuth();
  const messages = useApiMessage();
  const labels = useApiData(
    () =>
      canReadLabels
        ? api.get<ListResponse<LabelItem>>('/api/v1/governance/labels/')
        : Promise.resolve({ items: [], total: 0 }),
    [canReadLabels]
  );
  const settings = useApiData(
    () => (canAdmin ? api.get<{ items: SettingItem[] }>('/api/v1/governance/settings') : Promise.resolve({ items: [] })),
    [canAdmin]
  );
  const preferences = useApiData(() => api.get<{ items: SettingItem[] }>('/api/v1/governance/preferences'), []);
  const leaks = useApiData(
    () => (canAdmin ? api.get<LeakList>('/api/v1/governance/leak-passwords') : Promise.resolve({ items: [], total: 0, builtin_count: 0 })),
    [canAdmin]
  );
  const assets = useApiData(
    () => (canWriteLabels ? api.get<Asset[]>('/api/v1/assets/') : Promise.resolve([])),
    [canWriteLabels]
  );
  const [labelOpen, setLabelOpen] = useState(false);
  const [bindOpen, setBindOpen] = useState(false);
  const [editing, setEditing] = useState<LabelItem | null>(null);
  const [labelForm] = Form.useForm<{ name: string; color: string }>();
  const [bindForm] = Form.useForm<{ asset_ids: number[] }>();
  const [settingForm] = Form.useForm<Record<string, boolean | number | string>>();
  const [prefForm] = Form.useForm<Record<string, boolean | number | string>>();
  const [leakForm] = Form.useForm<{ password: string }>();

  const settingMap = useMemo(
    () => Object.fromEntries((settings.data?.items ?? []).map((item) => [item.key, item.value])),
    [settings.data]
  );
  const prefMap = useMemo(
    () => Object.fromEntries((preferences.data?.items ?? []).map((item) => [item.key, item.value])),
    [preferences.data]
  );

  const columns: ColumnsType<LabelItem> = [
    { title: '名称', dataIndex: 'name' },
    {
      title: '颜色',
      dataIndex: 'color',
      render: (color: string) => <Tag color={color}>{color}</Tag>
    },
    {
      title: '资产数',
      render: (_: unknown, record: LabelItem) => record.asset_ids.length
    },
    ...(canWriteLabels
      ? [
          {
            title: '操作',
            render: (_: unknown, record: LabelItem) => (
              <Space>
                <Button
                  type="link"
                  onClick={() => {
                    setEditing(record);
                    bindForm.setFieldsValue({ asset_ids: record.asset_ids });
                    setBindOpen(true);
                  }}
                >
                  标注资产
                </Button>
                <Button
                  type="link"
                  danger
                  onClick={() => {
                    Modal.confirm({
                      title: '确定删除这个标签？',
                      okText: '删除',
                      okType: 'danger',
                      onOk: async () => {
                        await api.delete(`/api/v1/governance/labels/${record.id}`);
                        messages.success('已删除');
                        labels.reload();
                      }
                    });
                  }}
                >
                  删除
                </Button>
              </Space>
            )
          } as ColumnsType<LabelItem>[number]
        ]
      : [])
  ];

  return (
    <>
      {canReadLabels ? (
        <Card
        title="资源标签"
        extra={
          canWriteLabels ? (
            <Button
              type="primary"
              onClick={() => {
                setEditing(null);
                labelForm.resetFields();
                setLabelOpen(true);
              }}
            >
              新建标签
            </Button>
          ) : null
        }
      >
        {labels.loading ? <LoadingState /> : null}
        {labels.error ? <ErrorState message={labels.error} onRetry={labels.reload} /> : null}
        {!labels.loading && !labels.error && (labels.data?.items.length ?? 0) === 0 ? (
          <Empty description="还没有资源标签" />
        ) : null}
        {!labels.loading && !labels.error && (labels.data?.items.length ?? 0) > 0 ? (
          <Table rowKey="id" pagination={false} dataSource={labels.data?.items} columns={columns} />
        ) : null}
      </Card>
      ) : null}

      <Card title="用户偏好">
        {preferences.loading ? <LoadingState /> : null}
        {preferences.error ? <ErrorState message={preferences.error} onRetry={preferences.reload} /> : null}
        {preferences.data ? (
          <Form
            form={prefForm}
            layout="vertical"
            key={JSON.stringify(prefMap)}
            initialValues={prefMap}
            onFinish={async (values) => {
              await api.put('/api/v1/governance/preferences', {
                items: Object.entries(values).map(([key, value]) => ({ key, value }))
              });
              messages.success('偏好已保存');
              preferences.reload();
            }}
          >
            <Form.Item label="语言" name="locale">
              <Select options={[{ value: 'zh-CN', label: '简体中文' }, { value: 'en-US', label: 'English' }]} />
            </Form.Item>
            <Form.Item label="每页条数" name="page_size">
              <InputNumber min={10} max={100} />
            </Form.Item>
            <Form.Item label="主题" name="theme">
              <Select
                options={[
                  { value: 'light', label: '浅色' },
                  { value: 'dark', label: '深色' },
                  { value: 'system', label: '跟随系统' }
                ]}
              />
            </Form.Item>
            <Button type="primary" htmlType="submit">
              保存偏好
            </Button>
          </Form>
        ) : null}
      </Card>

      {canAdmin ? (
        <Card title="系统配置">
          {settings.loading ? <LoadingState /> : null}
          {settings.error ? <ErrorState message={settings.error} onRetry={settings.reload} /> : null}
          {settings.data ? (
            <Form
              form={settingForm}
              layout="vertical"
              key={JSON.stringify(settingMap)}
              initialValues={settingMap}
              onFinish={async (values) => {
                try {
                  await api.put('/api/v1/governance/settings', {
                    items: Object.entries(values).map(([key, value]) => ({ key, value }))
                  });
                  messages.success('系统配置已保存');
                  settings.reload();
                } catch (error) {
                  messages.error(getErrorMessage(error));
                }
              }}
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
              <Button type="primary" htmlType="submit">
                保存配置
              </Button>
            </Form>
          ) : null}
        </Card>
      ) : null}

      {canAdmin ? (
        <Card title="泄露密码库">
          {leaks.loading ? <LoadingState /> : null}
          {leaks.error ? <ErrorState message={leaks.error} onRetry={leaks.reload} /> : null}
          {!leaks.loading && !leaks.error ? (
            <Space direction="vertical" style={{ width: '100%' }}>
              <Tag>内置哈希 {leaks.data?.builtin_count ?? 0} 条</Tag>
              <Form
                form={leakForm}
                layout="inline"
                onFinish={async (values) => {
                  await api.post('/api/v1/governance/leak-passwords', { password: values.password });
                  leakForm.resetFields();
                  messages.success('已加入泄露库（仅保存哈希）');
                  leaks.reload();
                }}
              >
                <Form.Item name="password" rules={[{ required: true, message: '请输入口令' }]}>
                  <Input.Password placeholder="明文只用于计算哈希" autoComplete="new-password" />
                </Form.Item>
                <Button type="primary" htmlType="submit">
                  添加
                </Button>
              </Form>
              {(leaks.data?.items.length ?? 0) === 0 ? <Empty description="还没有租户自定义哈希" /> : null}
              {(leaks.data?.items.length ?? 0) > 0 ? (
                <Table
                  rowKey="id"
                  pagination={false}
                  dataSource={leaks.data?.items}
                  columns={[
                    { title: 'SHA-256', dataIndex: 'sha256' },
                    { title: '加入时间', dataIndex: 'created_at' }
                  ]}
                />
              ) : null}
            </Space>
          ) : null}
        </Card>
      ) : null}

      <Modal
        title="新建标签"
        open={labelOpen}
        onCancel={() => setLabelOpen(false)}
        onOk={() => labelForm.submit()}
        destroyOnHidden
      >
        <Form
          form={labelForm}
          layout="vertical"
          initialValues={{ color: '#2563eb' }}
          onFinish={async (values) => {
            await api.post('/api/v1/governance/labels/', values);
            setLabelOpen(false);
            messages.success('已创建');
            labels.reload();
          }}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="颜色" name="color" rules={[{ required: true }]}>
            <Input placeholder="#2563eb" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={editing ? `标注资产 · ${editing.name}` : '标注资产'}
        open={bindOpen}
        onCancel={() => setBindOpen(false)}
        onOk={() => bindForm.submit()}
        destroyOnHidden
      >
        <Form
          form={bindForm}
          layout="vertical"
          onFinish={async (values) => {
            if (!editing) {
              return;
            }
            await api.put(`/api/v1/governance/labels/${editing.id}/assets`, {
              asset_ids: values.asset_ids ?? []
            });
            setBindOpen(false);
            messages.success('已更新标注');
            labels.reload();
          }}
        >
          <Form.Item label="资产" name="asset_ids">
            <Select
              mode="multiple"
              options={(assets.data ?? []).map((item) => ({ value: item.id, label: item.name }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
