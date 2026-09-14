import { Button, Card, Empty, Form, Input, Modal, Select, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';
import { useAuth } from '../../auth/AuthContext';
import { ErrorState, LoadingState } from '../../components/StatusView';
import { UserSelect } from '../../components/UserSelect';
import { getErrorMessage, useApiData, useApiMessage } from '../pageUtils';
import type {
  InAppMessage,
  ListResponse,
  NotificationChannel,
  NotificationChannelType,
  SystemMessageSubscription
} from '../types';

const CHANNEL_OPTIONS: Array<{ value: NotificationChannelType; label: string }> = [
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

function channelLabel(value: string): string {
  return CHANNEL_OPTIONS.find((item) => item.value === value)?.label ?? value;
}

export function NotificationChannelPanels({ canWrite }: { canWrite: boolean }) {
  const { api } = useAuth();
  const messages = useApiMessage();
  const channels = useApiData(() => api.get<ListResponse<NotificationChannel>>('/api/v1/webhook-endpoints/'), []);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{
    name: string;
    channel_type: NotificationChannelType;
    url: string;
    credential?: string;
    event_types: string[];
  }>();
  const channelType = Form.useWatch('channel_type', form);

  const openCreate = () => {
    form.resetFields();
    form.setFieldsValue({ name: '', channel_type: 'webhook', url: '', event_types: [] });
    setOpen(true);
  };

  const submit = async (values: {
    name: string;
    channel_type: NotificationChannelType;
    url: string;
    credential?: string;
    event_types: string[];
  }) => {
    try {
      await api.post('/api/v1/webhook-endpoints/', {
        name: values.name,
        channel_type: values.channel_type,
        url: values.channel_type === 'inbox' ? '' : values.url,
        credential: values.credential || undefined,
        event_types: values.event_types
      });
      messages.success('通知渠道已创建');
      setOpen(false);
      channels.reload();
    } catch (err) {
      messages.error(getErrorMessage(err));
    }
  };

  const columns: ColumnsType<NotificationChannel> = [
    { title: '名称', dataIndex: 'name' },
    { title: '类型', dataIndex: 'channel_type', render: (value: string) => channelLabel(value) },
    { title: '目标', dataIndex: 'url', render: (url: string) => url || '—' },
    {
      title: '事件',
      dataIndex: 'event_types',
      render: (types: string[]) => (types ?? []).map((item) => <Tag key={item}>{item}</Tag>)
    }
  ];

  return (
    <>
      <Card
        title="通知渠道"
        extra={canWrite ? <Button type="primary" onClick={openCreate}>创建渠道</Button> : null}
      >
        {channels.loading ? <LoadingState /> : null}
        {channels.error ? <ErrorState message={channels.error} onRetry={channels.reload} /> : null}
        {!channels.loading && !channels.error && (channels.data?.items.length ?? 0) === 0 ? (
          <Empty description="还没有通知渠道">
            {canWrite ? <Button onClick={openCreate}>创建渠道</Button> : null}
          </Empty>
        ) : null}
        {(channels.data?.items.length ?? 0) > 0 ? (
          <Table rowKey="id" size="small" pagination={false} columns={columns} dataSource={channels.data?.items} />
        ) : null}
      </Card>
      <Modal title="创建通知渠道" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} destroyOnHidden>
        <Form form={form} layout="vertical" onFinish={submit}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="channel_type" label="类型" rules={[{ required: true }]}>
            <Select options={CHANNEL_OPTIONS} />
          </Form.Item>
          {channelType !== 'inbox' ? (
            <Form.Item name="url" label="URL" rules={[{ required: true, message: '请输入 HTTPS URL' }]}>
              <Input placeholder="https://" autoComplete="off" />
            </Form.Item>
          ) : null}
          {channelType && channelType !== 'webhook' && channelType !== 'inbox' ? (
            <Form.Item name="credential" label="凭据">
              <Input.Password placeholder="可留空，若 URL 已含 token" autoComplete="off" />
            </Form.Item>
          ) : null}
          <Form.Item name="event_types" label="事件类型" rules={[{ required: true, message: '请填写事件类型' }]}>
            <Select mode="tags" placeholder="例如 audit.event.created" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

export function SystemMessageSubscriptionPanels({ canWrite }: { canWrite: boolean }) {
  const { api } = useAuth();
  const messages = useApiMessage();
  const subscriptions = useApiData(
    () => api.get<ListResponse<SystemMessageSubscription>>('/api/v1/system-message-subscriptions/'),
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

  const submit = async (values: {
    name: string;
    webhook_endpoint_id: number;
    event_types: string[];
    recipient_user_id?: string;
  }) => {
    try {
      await api.post('/api/v1/system-message-subscriptions/', values);
      messages.success('系统消息订阅已创建');
      setOpen(false);
      subscriptions.reload();
    } catch (err) {
      messages.error(getErrorMessage(err));
    }
  };

  const columns: ColumnsType<SystemMessageSubscription> = [
    { title: '名称', dataIndex: 'name' },
    { title: '渠道', dataIndex: 'webhook_endpoint_name' },
    { title: '类型', dataIndex: 'channel_type', render: (value: string) => channelLabel(value) },
    {
      title: '事件',
      dataIndex: 'event_types',
      render: (types: string[]) => (types ?? []).map((item) => <Tag key={item}>{item}</Tag>)
    }
  ];

  return (
    <>
      <Card
        title="系统消息订阅"
        extra={canWrite ? <Button type="primary" onClick={() => { form.resetFields(); setOpen(true); }}>创建订阅</Button> : null}
      >
        {subscriptions.loading ? <LoadingState /> : null}
        {subscriptions.error ? <ErrorState message={subscriptions.error} onRetry={subscriptions.reload} /> : null}
        {!subscriptions.loading && !subscriptions.error && (subscriptions.data?.items.length ?? 0) === 0 ? (
          <Empty description="还没有系统消息订阅">
            {canWrite ? <Button onClick={() => { form.resetFields(); setOpen(true); }}>创建订阅</Button> : null}
          </Empty>
        ) : null}
        {(subscriptions.data?.items.length ?? 0) > 0 ? (
          <Table rowKey="id" size="small" pagination={false} columns={columns} dataSource={subscriptions.data?.items} />
        ) : null}
      </Card>
      <Modal title="创建系统消息订阅" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} destroyOnHidden>
        <Form form={form} layout="vertical" onFinish={submit}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="webhook_endpoint_id" label="渠道" rules={[{ required: true, message: '请选择渠道' }]}>
            <Select
              options={(channels.data?.items ?? []).map((item) => ({
                value: item.id,
                label: `${item.name}（${channelLabel(item.channel_type)}）`
              }))}
            />
          </Form.Item>
          <Form.Item name="event_types" label="事件类型" rules={[{ required: true, message: '请填写事件类型' }]}>
            <Select mode="tags" placeholder="例如 audit.event.created" />
          </Form.Item>
          <Form.Item name="recipient_user_id" label="站内信接收人">
            <UserSelect placeholder="仅站内信需要" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

export function InAppMessagePanel() {
  const { api } = useAuth();
  const inbox = useApiData(() => api.get<ListResponse<InAppMessage>>('/api/v1/in-app-messages/'), []);

  const columns: ColumnsType<InAppMessage> = [
    { title: '事件', dataIndex: 'event_type' },
    { title: '标题', dataIndex: 'title' },
    {
      title: '状态',
      dataIndex: 'read_at',
      render: (value: string | null) => (value ? <Tag>已读</Tag> : <Tag color="blue">未读</Tag>)
    }
  ];

  return (
    <Card title="站内信">
      <Typography.Paragraph type="secondary">只显示当前登录用户的消息，正文已脱敏。</Typography.Paragraph>
      {inbox.loading ? <LoadingState /> : null}
      {inbox.error ? <ErrorState message={inbox.error} onRetry={inbox.reload} /> : null}
      {!inbox.loading && !inbox.error && (inbox.data?.items.length ?? 0) === 0 ? (
        <Empty description="还没有站内信" />
      ) : null}
      {(inbox.data?.items.length ?? 0) > 0 ? (
        <Table rowKey="id" size="small" pagination={false} columns={columns} dataSource={inbox.data?.items} />
      ) : null}
    </Card>
  );
}
