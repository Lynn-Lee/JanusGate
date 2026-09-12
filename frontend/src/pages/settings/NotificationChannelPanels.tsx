import { Button, Card, Empty, Form, Input, Modal, Select, Table, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';
import { useAuth } from '../../auth/AuthContext';
import { ErrorState, LoadingState } from '../../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from '../pageUtils';

type ListResponse<T> = { items: T[]; total: number };

type Channel = {
  id: number;
  name: string;
  channel_type: string;
  url: string;
  event_types: string[];
  credential_configured: boolean;
  recipient: string | null;
};

type Subscription = {
  id: number;
  event_type: string;
  webhook_endpoint_id: number;
  user_id: string;
};

type InboxItem = {
  id: number;
  event_type: string;
  title: string;
  body: string;
};

const CHANNEL_OPTIONS = [
  { value: 'webhook', label: 'WebHook' },
  { value: 'dingtalk', label: '钉钉' },
  { value: 'feishu', label: '飞书' },
  { value: 'lark', label: 'Lark' },
  { value: 'wecom', label: '企业微信' },
  { value: 'slack', label: 'Slack' },
  { value: 'sms', label: 'SMS' },
  { value: 'email', label: '邮件' },
  { value: 'inbox', label: '站内信' }
];

export function NotificationChannelPanels({ canWrite }: { canWrite: boolean }) {
  const { api } = useAuth();
  const messages = useApiMessage();
  const channels = useApiData(() => api.get<ListResponse<Channel>>('/api/v1/webhook-endpoints/'), []);
  const subscriptions = useApiData(
    () => api.get<ListResponse<Subscription>>('/api/v1/notification-subscriptions/'),
    []
  );
  const inbox = useApiData(() => api.get<ListResponse<InboxItem>>('/api/v1/in-app-messages/'), []);
  const [channelOpen, setChannelOpen] = useState(false);
  const [subOpen, setSubOpen] = useState(false);
  const [channelForm] = Form.useForm<{
    name: string;
    channel_type: string;
    url: string;
    event_types: string;
    credential?: string;
    recipient?: string;
  }>();
  const [subForm] = Form.useForm<{ event_type: string; webhook_endpoint_id: number }>();

  const channelItems = channels.data?.items ?? [];
  const subItems = subscriptions.data?.items ?? [];
  const inboxItems = inbox.data?.items ?? [];

  const channelColumns: ColumnsType<Channel> = [
    { title: '名称', dataIndex: 'name' },
    { title: '类型', dataIndex: 'channel_type' },
    { title: 'URL', dataIndex: 'url' },
    {
      title: '凭据',
      render: (_: unknown, record: Channel) => (record.credential_configured ? '已配置' : '未配置')
    }
  ];

  return (
    <>
      <Card
        title="通知渠道"
        extra={
          canWrite ? (
            <Button type="primary" onClick={() => { channelForm.resetFields(); setChannelOpen(true); }}>
              创建渠道
            </Button>
          ) : null
        }
      >
        <Typography.Paragraph type="secondary">
          IM 只允许官方域名。机器人 token 不会出现在列表 URL 里。
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
            <Button onClick={() => { subForm.resetFields(); setSubOpen(true); }}>创建订阅</Button>
          ) : null
        }
      >
        {subscriptions.loading ? <LoadingState /> : null}
        {subscriptions.error ? <ErrorState message={subscriptions.error} onRetry={subscriptions.reload} /> : null}
        {!subscriptions.loading && !subscriptions.error && subItems.length === 0 ? (
          <Empty description="还没有系统消息订阅" />
        ) : null}
        {!subscriptions.loading && !subscriptions.error && subItems.length > 0 ? (
          <Table
            rowKey="id"
            pagination={false}
            dataSource={subItems}
            columns={[
              { title: '事件', dataIndex: 'event_type' },
              { title: '渠道 ID', dataIndex: 'webhook_endpoint_id' }
            ]}
          />
        ) : null}
      </Card>

      <Card title="站内信">
        {inbox.loading ? <LoadingState /> : null}
        {inbox.error ? <ErrorState message={inbox.error} onRetry={inbox.reload} /> : null}
        {!inbox.loading && !inbox.error && inboxItems.length === 0 ? (
          <Empty description="还没有站内信" />
        ) : null}
        {!inbox.loading && !inbox.error && inboxItems.length > 0 ? (
          <Table
            rowKey="id"
            pagination={false}
            dataSource={inboxItems}
            columns={[
              { title: '事件', dataIndex: 'event_type' },
              { title: '标题', dataIndex: 'title' },
              { title: '正文', dataIndex: 'body' }
            ]}
          />
        ) : null}
      </Card>

      <Modal
        title="创建通知渠道"
        open={channelOpen}
        onCancel={() => setChannelOpen(false)}
        onOk={() => channelForm.submit()}
        destroyOnHidden
      >
        <Form
          form={channelForm}
          layout="vertical"
          initialValues={{ channel_type: 'webhook' }}
          onFinish={async (values) => {
            try {
              await api.post('/api/v1/webhook-endpoints/', {
                name: values.name,
                channel_type: values.channel_type,
                url: values.url ?? '',
                event_types: values.event_types.split(',').map((item) => item.trim()).filter(Boolean),
                credential: values.credential || undefined,
                recipient: values.recipient || undefined
              });
              setChannelOpen(false);
              messages.success('已保存');
              channels.reload();
            } catch (err: unknown) {
              messages.error(getErrorMessage(err));
            }
          }}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="渠道类型" name="channel_type" rules={[{ required: true }]}>
            <Select options={CHANNEL_OPTIONS} />
          </Form.Item>
          <Form.Item label="URL" name="url">
            <Input placeholder="HTTPS 或 smtps://；站内信可留空" />
          </Form.Item>
          <Form.Item label="事件类型（逗号分隔）" name="event_types" rules={[{ required: true }]}>
            <Input placeholder="audit.event.created" />
          </Form.Item>
          <Form.Item label="凭据" name="credential">
            <Input.Password autoComplete="off" />
          </Form.Item>
          <Form.Item label="接收人" name="recipient">
            <Input placeholder="站内信用户 ID / 邮件地址 / SMS 号码" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="创建系统消息订阅"
        open={subOpen}
        onCancel={() => setSubOpen(false)}
        onOk={() => subForm.submit()}
        destroyOnHidden
      >
        <Form
          form={subForm}
          layout="vertical"
          onFinish={async (values) => {
            try {
              await api.post('/api/v1/notification-subscriptions/', values);
              setSubOpen(false);
              messages.success('已保存');
              subscriptions.reload();
            } catch (err: unknown) {
              messages.error(getErrorMessage(err));
            }
          }}
        >
          <Form.Item label="事件类型" name="event_type" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="渠道" name="webhook_endpoint_id" rules={[{ required: true }]}>
            <Select
              options={channelItems.map((item) => ({ value: item.id, label: `${item.name} (${item.channel_type})` }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

export function canReadNotificationChannels(isSuperuser: boolean, permissions: string[]): boolean {
  return (
    isSuperuser ||
    permissions.includes('admin') ||
    permissions.includes('webhooks:read') ||
    permissions.includes('notifications:read')
  );
}

export function canWriteNotificationChannels(isSuperuser: boolean, permissions: string[]): boolean {
  return (
    isSuperuser ||
    permissions.includes('admin') ||
    permissions.includes('webhooks:write') ||
    permissions.includes('notifications:write')
  );
}

