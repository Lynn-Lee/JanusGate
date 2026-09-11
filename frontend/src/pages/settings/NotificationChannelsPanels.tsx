import { Button, Card, Empty, Form, Input, Select, Space, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';
import { useAuth } from '../../auth/AuthContext';
import { ErrorState, LoadingState } from '../../components/StatusView';
import { UserSelect } from '../../components/UserSelect';
import { useApiData, useApiMessage } from '../pageUtils';
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
};

type NotificationSubscription = {
  id: number;
  name: string;
  user_id: string;
  event_types: string[];
  webhook_endpoint_id: number;
  webhook_endpoint_name: string;
  channel_type: ChannelType;
  status: string;
};

type InboxMessage = {
  id: number;
  event_type: string;
  title: string;
  body: string;
  read_at: string | null;
};

const CHANNEL_OPTIONS = [
  { value: 'webhook', label: 'WebHook' },
  { value: 'dingtalk', label: '钉钉' },
  { value: 'feishu', label: '飞书' },
  { value: 'lark', label: 'Lark' },
  { value: 'wecom', label: '企业微信' },
  { value: 'slack', label: 'Slack' },
  { value: 'sms', label: '短信' },
  { value: 'email', label: '邮件' },
  { value: 'inbox', label: '站内信' }
];

function channelLabel(value: ChannelType): string {
  return CHANNEL_OPTIONS.find((item) => item.value === value)?.label ?? value;
}

export function NotificationChannelsPanels({ canWrite }: { canWrite: boolean }) {
  const { api } = useAuth();
  const messages = useApiMessage();
  const channels = useApiData(() => api.get<ListResponse<NotificationChannel>>('/api/v1/webhook-endpoints/'), []);
  const subscriptions = useApiData(
    () => api.get<ListResponse<NotificationSubscription>>('/api/v1/notification-subscriptions/'),
    []
  );
  const inbox = useApiData(() => api.get<ListResponse<InboxMessage>>('/api/v1/inbox-messages/'), []);
  const [channelOpen, setChannelOpen] = useState(false);
  const [subOpen, setSubOpen] = useState(false);
  const [channelForm] = Form.useForm<{
    name: string;
    channel_type: ChannelType;
    url: string;
    event_types: string;
    credential?: string;
  }>();
  const [subForm] = Form.useForm<{
    name: string;
    user_id: string;
    event_types: string;
    webhook_endpoint_id: number;
  }>();

  const channelColumns: ColumnsType<NotificationChannel> = [
    { title: '名称', dataIndex: 'name' },
    { title: '类型', dataIndex: 'channel_type', render: (value: ChannelType) => channelLabel(value) },
    { title: '地址', dataIndex: 'url' },
    {
      title: '凭据',
      dataIndex: 'credential_configured',
      render: (configured: boolean) => (configured ? <Tag color="blue">已配置</Tag> : <Tag>未配置</Tag>)
    }
  ];

  const subscriptionColumns: ColumnsType<NotificationSubscription> = [
    { title: '名称', dataIndex: 'name' },
    { title: '用户', dataIndex: 'user_id' },
    { title: '渠道', dataIndex: 'webhook_endpoint_name' },
    { title: '事件', dataIndex: 'event_types', render: (value: string[]) => value.join(', ') }
  ];

  const inboxColumns: ColumnsType<InboxMessage> = [
    { title: '标题', dataIndex: 'title' },
    { title: '事件', dataIndex: 'event_type' },
    { title: '正文', dataIndex: 'body' },
    {
      title: '状态',
      dataIndex: 'read_at',
      render: (value: string | null) => (value ? '已读' : '未读')
    }
  ];

  const channelItems = channels.data?.items ?? [];
  const subscriptionItems = subscriptions.data?.items ?? [];
  const inboxItems = inbox.data?.items ?? [];

  return (
    <>
      <Card
        title="通知渠道"
        extra={
          canWrite ? (
            <Button type="primary" onClick={() => setChannelOpen(true)}>
              创建渠道
            </Button>
          ) : null
        }
      >
        <Typography.Paragraph type="secondary">
          IM 只允许官方 host；地址不含 query，凭据加密保存且页面不回显。
        </Typography.Paragraph>
        {channels.loading ? <LoadingState /> : null}
        {channels.error ? <ErrorState message={channels.error} onRetry={channels.reload} /> : null}
        {!channels.loading && !channels.error && channelItems.length === 0 ? (
          <Empty description="还没有通知渠道">
            {canWrite ? (
              <Button type="primary" onClick={() => setChannelOpen(true)}>
                创建渠道
              </Button>
            ) : null}
          </Empty>
        ) : null}
        {!channels.loading && !channels.error && channelItems.length > 0 ? (
          <Table rowKey="id" pagination={false} dataSource={channelItems} columns={channelColumns} />
        ) : null}
      </Card>

      <Card
        title="系统消息订阅"
        extra={
          canWrite ? (
            <Button type="primary" onClick={() => setSubOpen(true)}>
              创建订阅
            </Button>
          ) : null
        }
      >
        {subscriptions.loading ? <LoadingState /> : null}
        {subscriptions.error ? <ErrorState message={subscriptions.error} onRetry={subscriptions.reload} /> : null}
        {!subscriptions.loading && !subscriptions.error && subscriptionItems.length === 0 ? (
          <Empty description="还没有系统消息订阅" />
        ) : null}
        {!subscriptions.loading && !subscriptions.error && subscriptionItems.length > 0 ? (
          <Table rowKey="id" pagination={false} dataSource={subscriptionItems} columns={subscriptionColumns} />
        ) : null}
      </Card>

      <Card title="站内信">
        {inbox.loading ? <LoadingState /> : null}
        {inbox.error ? <ErrorState message={inbox.error} onRetry={inbox.reload} /> : null}
        {!inbox.loading && !inbox.error && inboxItems.length === 0 ? <Empty description="还没有站内信" /> : null}
        {!inbox.loading && !inbox.error && inboxItems.length > 0 ? (
          <Table rowKey="id" pagination={false} dataSource={inboxItems} columns={inboxColumns} />
        ) : null}
      </Card>

      <Form
        form={channelForm}
        layout="vertical"
        initialValues={{ channel_type: 'webhook', event_types: 'audit.event.created' }}
        onFinish={async (values) => {
          await api.post('/api/v1/webhook-endpoints/', {
            name: values.name,
            channel_type: values.channel_type,
            url: values.channel_type === 'inbox' ? 'inbox://local' : values.url,
            event_types: values.event_types.split(',').map((item) => item.trim()).filter(Boolean),
            credential: values.credential || undefined
          });
          setChannelOpen(false);
          channelForm.resetFields();
          messages.success('已保存');
          channels.reload();
        }}
      >
        {channelOpen ? (
          <Card title="创建通知渠道">
            <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入名称' }]}>
              <Input />
            </Form.Item>
            <Form.Item label="类型" name="channel_type" rules={[{ required: true }]}>
              <Select options={CHANNEL_OPTIONS} />
            </Form.Item>
            <Form.Item noStyle shouldUpdate={(prev, next) => prev.channel_type !== next.channel_type}>
              {({ getFieldValue }) =>
                getFieldValue('channel_type') === 'inbox' ? null : (
                  <Form.Item label="地址" name="url" rules={[{ required: true, message: '请输入地址' }]}>
                    <Input placeholder="https://... 或 smtps://host:465" />
                  </Form.Item>
                )
              }
            </Form.Item>
            <Form.Item label="事件类型" name="event_types" rules={[{ required: true }]}>
              <Input placeholder="逗号分隔，例如 audit.event.created" />
            </Form.Item>
            <Form.Item label="渠道凭据" name="credential">
              <Input.Password autoComplete="off" placeholder="机器人 token / SMTP 密码，不会回显" />
            </Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                保存
              </Button>
              <Button onClick={() => setChannelOpen(false)}>取消</Button>
            </Space>
          </Card>
        ) : null}
      </Form>

      <Form
        form={subForm}
        layout="vertical"
        onFinish={async (values) => {
          await api.post('/api/v1/notification-subscriptions/', {
            name: values.name,
            user_id: String(values.user_id),
            event_types: values.event_types.split(',').map((item) => item.trim()).filter(Boolean),
            webhook_endpoint_id: values.webhook_endpoint_id
          });
          setSubOpen(false);
          subForm.resetFields();
          messages.success('已保存');
          subscriptions.reload();
        }}
      >
        {subOpen ? (
          <Card title="创建系统消息订阅">
            <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入名称' }]}>
              <Input />
            </Form.Item>
            <Form.Item label="用户" name="user_id" rules={[{ required: true, message: '请选择用户' }]}>
              <UserSelect />
            </Form.Item>
            <Form.Item label="渠道" name="webhook_endpoint_id" rules={[{ required: true, message: '请选择渠道' }]}>
              <Select
                options={channelItems.map((item) => ({
                  value: item.id,
                  label: `${item.name}（${channelLabel(item.channel_type)}）`
                }))}
              />
            </Form.Item>
            <Form.Item label="事件类型" name="event_types" rules={[{ required: true }]}>
              <Input placeholder="逗号分隔" />
            </Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                保存
              </Button>
              <Button onClick={() => setSubOpen(false)}>取消</Button>
            </Space>
          </Card>
        ) : null}
      </Form>
    </>
  );
}
