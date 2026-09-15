import { Button, Card, Empty, Form, Input, Modal, Select, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';
import { useAuth } from '../../auth/AuthContext';
import { ErrorState, LoadingState } from '../../components/StatusView';
import { UserSelect } from '../../components/UserSelect';
import { getErrorMessage, useApiData, useApiMessage } from '../pageUtils';
import type { ListResponse } from '../types';

type ChannelType =
  | 'webhook'
  | 'dingtalk'
  | 'feishu'
  | 'lark'
  | 'wecom'
  | 'slack'
  | 'sms'
  | 'email'
  | 'inbox';

type NotificationChannel = {
  id: number;
  name: string;
  url: string;
  channel_type: ChannelType;
  event_types: string[];
  status: string;
  credential_configured: boolean;
};

type SystemMessageSubscription = {
  id: number;
  name: string;
  event_types: string[];
  webhook_endpoint_id: number;
  webhook_endpoint_name: string;
  channel_type: ChannelType;
  recipient_user_id: string | null;
  status: string;
};

type InAppMessage = {
  id: number;
  event_type: string;
  title: string;
  created_at: string | null;
};

const CHANNEL_OPTIONS = [
  { value: 'webhook', label: '通用 WebHook' },
  { value: 'dingtalk', label: '钉钉' },
  { value: 'feishu', label: '飞书' },
  { value: 'lark', label: 'Lark' },
  { value: 'wecom', label: '企业微信' },
  { value: 'slack', label: 'Slack' },
  { value: 'sms', label: '短信网关' },
  { value: 'email', label: '邮件网关' },
  { value: 'inbox', label: '站内信' }
];

const CREDENTIAL_CHANNELS = new Set<ChannelType>([
  'dingtalk',
  'feishu',
  'lark',
  'wecom',
  'slack',
  'sms',
  'email'
]);

function channelLabel(channelType: string): string {
  return CHANNEL_OPTIONS.find((item) => item.value === channelType)?.label ?? channelType;
}

export function NotificationChannelPanel({ canWrite }: { canWrite: boolean }) {
  const { api } = useAuth();
  const messages = useApiMessage();
  const channels = useApiData(() => api.get<ListResponse<NotificationChannel>>('/api/v1/webhook-endpoints/'), []);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{
    name: string;
    channel_type: ChannelType;
    url?: string;
    credential?: string;
    event_types: string[];
  }>();

  const openCreate = () => {
    form.resetFields();
    form.setFieldsValue({
      name: '',
      channel_type: 'webhook',
      url: '',
      credential: '',
      event_types: ['audit.event.created']
    });
    setOpen(true);
  };

  const columns: ColumnsType<NotificationChannel> = [
    { title: '名称', dataIndex: 'name' },
    {
      title: '渠道',
      dataIndex: 'channel_type',
      render: (value: ChannelType) => channelLabel(value)
    },
    {
      title: '目标',
      dataIndex: 'url',
      render: (url: string, record) => (record.channel_type === 'inbox' ? '当前租户站内信' : url)
    },
    {
      title: '凭据',
      dataIndex: 'credential_configured',
      render: (configured: boolean) => (configured ? <Tag color="green">已配置</Tag> : <Tag>未配置</Tag>)
    },
    {
      title: '事件',
      dataIndex: 'event_types',
      render: (types: string[]) => (types ?? []).join('、')
    }
  ];

  const items = channels.data?.items ?? [];

  return (
    <>
      <Card
        title="通知渠道"
        extra={
          canWrite ? (
            <Button type="primary" onClick={openCreate}>
              创建渠道
            </Button>
          ) : null
        }
      >
        <Typography.Paragraph type="secondary">
          IM 只允许官方 host；机器人凭据会从 URL 剥离后加密保存，页面不回显。邮件/短信只走 HTTPS 网关，不直连 SMTP。站内信不能绑通知规则，请用系统消息订阅。
        </Typography.Paragraph>
        {channels.loading ? <LoadingState /> : null}
        {channels.error ? <ErrorState message={channels.error} onRetry={channels.reload} /> : null}
        {!channels.loading && !channels.error && items.length === 0 ? (
          <Empty description="还没有通知渠道">
            {canWrite ? (
              <Button type="primary" onClick={openCreate}>
                创建渠道
              </Button>
            ) : null}
          </Empty>
        ) : null}
        {!channels.loading && !channels.error && items.length > 0 ? (
          <Table rowKey="id" pagination={false} dataSource={items} columns={columns} />
        ) : null}
      </Card>

      <Modal title="创建通知渠道" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} destroyOnHidden>
        <Form
          form={form}
          layout="vertical"
          onFinish={async (values) => {
            try {
              await api.post('/api/v1/webhook-endpoints/', {
                name: values.name,
                channel_type: values.channel_type,
                url: values.channel_type === 'inbox' ? undefined : values.url,
                credential: values.credential || undefined,
                event_types: values.event_types
              });
              setOpen(false);
              messages.success('已保存');
              channels.reload();
            } catch (err) {
              messages.error(getErrorMessage(err));
            }
          }}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item label="渠道类型" name="channel_type" rules={[{ required: true }]}>
            <Select options={CHANNEL_OPTIONS} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, next) => prev.channel_type !== next.channel_type}>
            {({ getFieldValue }) =>
              getFieldValue('channel_type') === 'inbox' ? null : (
                <Form.Item label="HTTPS URL" name="url" rules={[{ required: true, message: '请输入 HTTPS URL' }]}>
                  <Input placeholder="https://..." autoComplete="off" />
                </Form.Item>
              )
            }
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, next) => prev.channel_type !== next.channel_type}>
            {({ getFieldValue }) =>
              CREDENTIAL_CHANNELS.has(getFieldValue('channel_type')) ? (
                <Form.Item label="网关或机器人凭据（可留空，若已写在 URL 中）" name="credential">
                  <Input.Password autoComplete="new-password" />
                </Form.Item>
              ) : null
            }
          </Form.Item>
          <Form.Item label="事件类型" name="event_types" rules={[{ required: true, message: '请输入事件类型' }]}>
            <Select mode="tags" tokenSeparators={[',']} placeholder="例如 audit.event.created" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

export function SystemMessageSubscriptionPanel({ canWrite }: { canWrite: boolean }) {
  const { api } = useAuth();
  const messages = useApiMessage();
  const subscriptions = useApiData(
    () => api.get<ListResponse<SystemMessageSubscription>>('/api/v1/notification-subscriptions/'),
    []
  );
  const channels = useApiData(() => api.get<ListResponse<NotificationChannel>>('/api/v1/webhook-endpoints/'), []);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{
    name: string;
    webhook_endpoint_id: number;
    event_types: string[];
    recipient_user_id?: string;
  }>();

  const openCreate = () => {
    form.resetFields();
    form.setFieldsValue({
      name: '',
      event_types: ['audit.event.created']
    });
    setOpen(true);
  };

  const columns: ColumnsType<SystemMessageSubscription> = [
    { title: '名称', dataIndex: 'name' },
    {
      title: '渠道',
      render: (_: unknown, record) => `${record.webhook_endpoint_name}（${channelLabel(record.channel_type)}）`
    },
    {
      title: '事件',
      dataIndex: 'event_types',
      render: (types: string[]) => (types ?? []).join('、')
    },
    {
      title: '接收人',
      dataIndex: 'recipient_user_id',
      render: (value: string | null) => value || '—'
    },
    {
      title: '状态',
      dataIndex: 'status',
      render: (status: string) => (status === 'active' ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>)
    }
  ];

  const items = subscriptions.data?.items ?? [];
  const channelOptions = (channels.data?.items ?? []).map((item) => ({
    value: item.id,
    label: `${item.name}（${channelLabel(item.channel_type)}）`,
    channel_type: item.channel_type
  }));

  return (
    <>
      <Card
        title="系统消息订阅"
        extra={
          canWrite ? (
            <Button type="primary" onClick={openCreate}>
              创建订阅
            </Button>
          ) : null
        }
      >
        <Typography.Paragraph type="secondary">按事件类型扇出到已配置渠道。站内信必须指定接收人。</Typography.Paragraph>
        {subscriptions.loading ? <LoadingState /> : null}
        {subscriptions.error ? (
          <ErrorState message={subscriptions.error} onRetry={subscriptions.reload} />
        ) : null}
        {!subscriptions.loading && !subscriptions.error && items.length === 0 ? (
          <Empty description="还没有系统消息订阅">
            {canWrite ? (
              <Button type="primary" onClick={openCreate}>
                创建订阅
              </Button>
            ) : null}
          </Empty>
        ) : null}
        {!subscriptions.loading && !subscriptions.error && items.length > 0 ? (
          <Table rowKey="id" pagination={false} dataSource={items} columns={columns} />
        ) : null}
      </Card>

      <Modal title="创建系统消息订阅" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} destroyOnHidden>
        <Form
          form={form}
          layout="vertical"
          onFinish={async (values) => {
            const selected = channelOptions.find((item) => item.value === values.webhook_endpoint_id);
            if (selected?.channel_type === 'inbox' && !values.recipient_user_id) {
              messages.error('站内信必须指定接收人');
              return;
            }
            try {
              await api.post('/api/v1/notification-subscriptions/', {
                name: values.name,
                event_types: values.event_types,
                webhook_endpoint_id: values.webhook_endpoint_id,
                recipient_user_id: values.recipient_user_id || undefined
              });
              setOpen(false);
              messages.success('已保存');
              subscriptions.reload();
            } catch (err) {
              messages.error(getErrorMessage(err));
            }
          }}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item label="通知渠道" name="webhook_endpoint_id" rules={[{ required: true, message: '请选择渠道' }]}>
            <Select options={channelOptions} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, next) => prev.webhook_endpoint_id !== next.webhook_endpoint_id}>
            {({ getFieldValue }) => {
              const selected = channelOptions.find((item) => item.value === getFieldValue('webhook_endpoint_id'));
              const inbox = selected?.channel_type === 'inbox';
              return (
                <Form.Item
                  label={inbox ? '接收人' : '接收人（站内信必填）'}
                  name="recipient_user_id"
                  rules={inbox ? [{ required: true, message: '站内信必须指定接收人' }] : undefined}
                >
                  <UserSelect />
                </Form.Item>
              );
            }}
          </Form.Item>
          <Form.Item label="事件类型" name="event_types" rules={[{ required: true, message: '请输入事件类型' }]}>
            <Select mode="tags" tokenSeparators={[',']} placeholder="例如 audit.event.created" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

export function InAppMessagePanel() {
  const { api } = useAuth();
  const inbox = useApiData(() => api.get<ListResponse<InAppMessage>>('/api/v1/in-app-messages/'), []);
  const items = inbox.data?.items ?? [];
  const columns: ColumnsType<InAppMessage> = [
    { title: '标题', dataIndex: 'title' },
    { title: '事件', dataIndex: 'event_type' },
    { title: '时间', dataIndex: 'created_at', render: (value: string | null) => value || '—' }
  ];

  return (
    <Card title="站内信">
      <Typography.Paragraph type="secondary">只显示当前登录用户在当前租户收到的消息。</Typography.Paragraph>
      {inbox.loading ? <LoadingState /> : null}
      {inbox.error ? <ErrorState message={inbox.error} onRetry={inbox.reload} /> : null}
      {!inbox.loading && !inbox.error && items.length === 0 ? <Empty description="还没有站内信" /> : null}
      {!inbox.loading && !inbox.error && items.length > 0 ? (
        <Table rowKey="id" pagination={false} dataSource={items} columns={columns} />
      ) : null}
    </Card>
  );
}
