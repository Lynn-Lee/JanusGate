import { Button, Card, Empty, Form, Input, Modal, Select, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';
import { useAuth } from '../../auth/AuthContext';
import { ErrorState, LoadingState } from '../../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from '../pageUtils';
import type { ListResponse } from '../types';

type ChannelType = 'webhook' | 'dingtalk' | 'feishu' | 'lark' | 'wecom' | 'slack' | 'sms' | 'email' | 'inbox';

type NotificationChannel = {
  id: number;
  name: string;
  url: string;
  channel_type: ChannelType;
  event_types: string[];
  status: string;
  credential_configured: boolean;
  recipient: string | null;
};

type Subscription = {
  id: number;
  event_type: string;
  webhook_endpoint_id: number;
  user_id: string;
  status: string;
};

type InboxMessage = {
  id: number;
  event_type: string;
  title: string;
  body: string;
  created_at: string | null;
};

const CHANNEL_OPTIONS = [
  { value: 'webhook', label: 'WebHook' },
  { value: 'dingtalk', label: '钉钉' },
  { value: 'feishu', label: '飞书' },
  { value: 'lark', label: 'Lark' },
  { value: 'wecom', label: '企业微信' },
  { value: 'slack', label: 'Slack' },
  { value: 'sms', label: '短信网关' },
  { value: 'email', label: '邮件网关' },
  { value: 'inbox', label: '站内信' }
];

const CHANNEL_LABEL: Record<ChannelType, string> = {
  webhook: 'WebHook',
  dingtalk: '钉钉',
  feishu: '飞书',
  lark: 'Lark',
  wecom: '企业微信',
  slack: 'Slack',
  sms: '短信网关',
  email: '邮件网关',
  inbox: '站内信'
};

export function NotificationChannelPanels({
  canManage,
  canWrite
}: {
  canManage: boolean;
  canWrite: boolean;
}) {
  const { api } = useAuth();
  const messages = useApiMessage();
  const channels = useApiData(
    () =>
      canManage
        ? api.get<ListResponse<NotificationChannel>>('/api/v1/webhook-endpoints/')
        : Promise.resolve<ListResponse<NotificationChannel>>({ items: [], total: 0 }),
    [canManage]
  );
  const subscriptions = useApiData(
    () =>
      canManage
        ? api.get<ListResponse<Subscription>>('/api/v1/notification-subscriptions/')
        : Promise.resolve<ListResponse<Subscription>>({ items: [], total: 0 }),
    [canManage]
  );
  const inbox = useApiData(() => api.get<ListResponse<InboxMessage>>('/api/v1/in-app-messages/'), []);
  const [channelOpen, setChannelOpen] = useState(false);
  const [subscriptionOpen, setSubscriptionOpen] = useState(false);
  const [channelForm] = Form.useForm<{
    name: string;
    channel_type: ChannelType;
    url: string;
    credential?: string;
    recipient?: string;
    event_types: string;
  }>();
  const [subscriptionForm] = Form.useForm<{
    event_type: string;
    webhook_endpoint_id: number;
    user_id?: string;
  }>();
  const channelType = Form.useWatch('channel_type', channelForm) as ChannelType | undefined;

  const channelColumns: ColumnsType<NotificationChannel> = [
    { title: '名称', dataIndex: 'name' },
    {
      title: '渠道',
      dataIndex: 'channel_type',
      render: (value: ChannelType) => CHANNEL_LABEL[value] ?? value
    },
    { title: 'URL', dataIndex: 'url', ellipsis: true },
    {
      title: '凭据',
      dataIndex: 'credential_configured',
      render: (configured: boolean) => (configured ? <Tag color="green">已配置</Tag> : <Tag>未配置</Tag>)
    },
    { title: '事件', dataIndex: 'event_types', render: (types: string[]) => types.join(', ') }
  ];

  const subscriptionColumns: ColumnsType<Subscription> = [
    { title: '事件类型', dataIndex: 'event_type' },
    { title: '渠道 ID', dataIndex: 'webhook_endpoint_id' },
    { title: '用户', dataIndex: 'user_id', render: (value: string) => value || '租户级' },
    { title: '状态', dataIndex: 'status' }
  ];

  const inboxColumns: ColumnsType<InboxMessage> = [
    { title: '事件', dataIndex: 'event_type' },
    { title: '标题', dataIndex: 'title' },
    { title: '正文', dataIndex: 'body', ellipsis: true }
  ];

  const createChannel = async (values: {
    name: string;
    channel_type: ChannelType;
    url: string;
    credential?: string;
    recipient?: string;
    event_types: string;
  }) => {
    try {
      await api.post('/api/v1/webhook-endpoints/', {
        name: values.name,
        channel_type: values.channel_type,
        url: values.url || '',
        credential: values.credential || undefined,
        recipient: values.recipient || undefined,
        event_types: values.event_types
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean)
      });
      setChannelOpen(false);
      messages.success('通知渠道已创建');
      channels.reload();
    } catch (err: unknown) {
      messages.error(getErrorMessage(err));
    }
  };

  const createSubscription = async (values: {
    event_type: string;
    webhook_endpoint_id: number;
    user_id?: string;
  }) => {
    try {
      await api.post('/api/v1/notification-subscriptions/', {
        event_type: values.event_type,
        webhook_endpoint_id: values.webhook_endpoint_id,
        user_id: values.user_id || ''
      });
      setSubscriptionOpen(false);
      messages.success('系统消息订阅已创建');
      subscriptions.reload();
    } catch (err: unknown) {
      messages.error(getErrorMessage(err));
    }
  };

  const needsUrl = channelType !== 'inbox';
  const needsCredential = channelType === 'sms' || channelType === 'email' || Boolean(channelType && channelType !== 'webhook' && channelType !== 'inbox');
  const needsRecipient = channelType === 'inbox' || channelType === 'sms' || channelType === 'email';

  return (
    <>
      {canManage ? (
      <Card
        title="通知渠道"
        extra={
          canWrite ? (
            <Button type="primary" onClick={() => { channelForm.resetFields(); channelForm.setFieldsValue({ channel_type: 'webhook' }); setChannelOpen(true); }}>
              创建通知渠道
            </Button>
          ) : null
        }
      >
        {channels.loading ? <LoadingState /> : null}
        {channels.error ? <ErrorState message={channels.error} onRetry={channels.reload} /> : null}
        {!channels.loading && !channels.error && (channels.data?.items.length ?? 0) === 0 ? (
          <Empty description="还没有通知渠道">
            {canWrite ? (
              <Button type="primary" onClick={() => { channelForm.resetFields(); channelForm.setFieldsValue({ channel_type: 'webhook' }); setChannelOpen(true); }}>
                创建通知渠道
              </Button>
            ) : null}
          </Empty>
        ) : null}
        {(channels.data?.items.length ?? 0) > 0 ? (
          <Table rowKey="id" size="small" pagination={false} columns={channelColumns} dataSource={channels.data?.items} />
        ) : null}
        <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
          IM 只接受官方域名；机器人 token 会从 URL 剥离后加密保存，列表不回显凭据。邮件和短信本切片走 HTTPS 网关。
        </Typography.Paragraph>
      </Card>
      ) : null}

      {canManage ? (
      <Card
        title="系统消息订阅"
        extra={
          canWrite ? (
            <Button onClick={() => { subscriptionForm.resetFields(); setSubscriptionOpen(true); }}>创建订阅</Button>
          ) : null
        }
      >
        {subscriptions.loading ? <LoadingState /> : null}
        {subscriptions.error ? <ErrorState message={subscriptions.error} onRetry={subscriptions.reload} /> : null}
        {!subscriptions.loading && !subscriptions.error && (subscriptions.data?.items.length ?? 0) === 0 ? (
          <Empty description="还没有系统消息订阅" />
        ) : null}
        {(subscriptions.data?.items.length ?? 0) > 0 ? (
          <Table rowKey="id" size="small" pagination={false} columns={subscriptionColumns} dataSource={subscriptions.data?.items} />
        ) : null}
      </Card>
      ) : null}

      <Card title="站内信">
        {inbox.loading ? <LoadingState /> : null}
        {inbox.error ? <ErrorState message={inbox.error} onRetry={inbox.reload} /> : null}
        {!inbox.loading && !inbox.error && (inbox.data?.items.length ?? 0) === 0 ? (
          <Empty description="还没有站内信" />
        ) : null}
        {(inbox.data?.items.length ?? 0) > 0 ? (
          <Table rowKey="id" size="small" pagination={false} columns={inboxColumns} dataSource={inbox.data?.items} />
        ) : null}
      </Card>

      <Modal title="创建通知渠道" open={channelOpen} onCancel={() => setChannelOpen(false)} onOk={() => channelForm.submit()} destroyOnHidden>
        <Form form={channelForm} layout="vertical" initialValues={{ channel_type: 'webhook' }} onFinish={(values) => void createChannel(values)}>
          <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item label="渠道类型" name="channel_type" rules={[{ required: true }]}>
            <Select options={CHANNEL_OPTIONS} />
          </Form.Item>
          {needsUrl ? (
            <Form.Item label="URL" name="url" rules={[{ required: true, message: '请输入 HTTPS URL' }]}>
              <Input placeholder="https://" autoComplete="off" />
            </Form.Item>
          ) : null}
          {needsCredential ? (
            <Form.Item label="凭据" name="credential">
              <Input.Password autoComplete="off" />
            </Form.Item>
          ) : null}
          {needsRecipient ? (
            <Form.Item label={channelType === 'inbox' ? '用户 ID' : '收件人'} name="recipient" rules={[{ required: true, message: '请输入收件人' }]}>
              <Input autoComplete="off" />
            </Form.Item>
          ) : null}
          <Form.Item label="事件类型" name="event_types" rules={[{ required: true, message: '请输入事件类型' }]}>
            <Input placeholder="workflow.request.approved,audit.event.created" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="创建订阅" open={subscriptionOpen} onCancel={() => setSubscriptionOpen(false)} onOk={() => subscriptionForm.submit()} destroyOnHidden>
        <Form form={subscriptionForm} layout="vertical" onFinish={(values) => void createSubscription(values)}>
          <Form.Item label="事件类型" name="event_type" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="通知渠道" name="webhook_endpoint_id" rules={[{ required: true, message: '请选择渠道' }]}>
            <Select
              options={(channels.data?.items ?? []).map((item) => ({
                value: item.id,
                label: `${item.name}（${CHANNEL_LABEL[item.channel_type]}）`
              }))}
            />
          </Form.Item>
          <Form.Item label="用户 ID（空表示租户级）" name="user_id">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
