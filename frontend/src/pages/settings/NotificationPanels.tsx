import { Button, Card, Empty, Form, Input, Modal, Select, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';
import { useAuth } from '../../auth/AuthContext';
import { ErrorState, LoadingState } from '../../components/StatusView';
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
  target: string;
  event_types: string[];
  status: string;
  credential_configured: boolean;
};

type NotificationSubscription = {
  id: number;
  user_id: string;
  webhook_endpoint_id: number;
  webhook_endpoint_name: string;
  channel_type: ChannelType;
  event_types: string[];
  status: string;
};

type InAppMessage = {
  id: number;
  event_type: string;
  title: string;
  payload: Record<string, unknown>;
  read_at: string | null;
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

const CHANNEL_LABEL: Record<ChannelType, string> = {
  webhook: 'WebHook',
  dingtalk: '钉钉',
  feishu: '飞书',
  lark: 'Lark',
  wecom: '企业微信',
  slack: 'Slack',
  sms: 'SMS',
  email: '邮件',
  inbox: '站内信'
};

function defaultUrl(channelType: ChannelType): string {
  if (channelType === 'inbox') return 'inbox://local';
  if (channelType === 'email') return 'smtps://mail.example.test:465';
  if (channelType === 'dingtalk') return 'https://oapi.dingtalk.com/robot/send';
  if (channelType === 'feishu') return 'https://open.feishu.cn/open-apis/bot/v2/hook/';
  if (channelType === 'lark') return 'https://open.larksuite.com/open-apis/bot/v2/hook/';
  if (channelType === 'wecom') return 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send';
  if (channelType === 'slack') return 'https://hooks.slack.com/services/';
  return 'https://';
}

export function NotificationPanels({ canWrite }: { canWrite: boolean }) {
  const { api } = useAuth();
  const messages = useApiMessage();
  const channels = useApiData(() => api.get<ListResponse<NotificationChannel>>('/api/v1/webhook-endpoints/'), []);
  const subscriptions = useApiData(
    () => api.get<ListResponse<NotificationSubscription>>('/api/v1/notification-subscriptions/'),
    []
  );
  const inbox = useApiData(() => api.get<ListResponse<InAppMessage>>('/api/v1/in-app-messages/'), []);
  const [channelOpen, setChannelOpen] = useState(false);
  const [subscriptionOpen, setSubscriptionOpen] = useState(false);
  const [channelForm] = Form.useForm<{
    name: string;
    channel_type: ChannelType;
    url: string;
    target: string;
    event_types: string;
    credential?: string;
  }>();
  const [subscriptionForm] = Form.useForm<{
    user_id: string;
    webhook_endpoint_id: number;
    event_types: string;
  }>();
  const channelType = Form.useWatch('channel_type', channelForm) as ChannelType | undefined;

  const channelColumns: ColumnsType<NotificationChannel> = [
    { title: '名称', dataIndex: 'name' },
    {
      title: '渠道',
      dataIndex: 'channel_type',
      render: (value: ChannelType) => CHANNEL_LABEL[value] ?? value
    },
    { title: '地址', dataIndex: 'url' },
    { title: '目标', dataIndex: 'target', render: (value: string) => value || '-' },
    {
      title: '凭据',
      dataIndex: 'credential_configured',
      render: (value: boolean) => (value ? <Tag color="green">已配置</Tag> : <Tag>未配置</Tag>)
    }
  ];

  const subscriptionColumns: ColumnsType<NotificationSubscription> = [
    { title: '用户', dataIndex: 'user_id' },
    { title: '渠道', dataIndex: 'webhook_endpoint_name' },
    {
      title: '类型',
      dataIndex: 'channel_type',
      render: (value: ChannelType) => CHANNEL_LABEL[value] ?? value
    },
    { title: '事件', dataIndex: 'event_types', render: (value: string[]) => value.join(', ') },
    ...(canWrite
      ? [
          {
            title: '操作',
            render: (_: unknown, record: NotificationSubscription) => (
              <Button
                type="link"
                danger
                onClick={() => {
                  Modal.confirm({
                    title: '确定删除这条订阅？',
                    okText: '删除',
                    okType: 'danger',
                    cancelText: '取消',
                    onOk: async () => {
                      await api.delete(`/api/v1/notification-subscriptions/${record.id}`);
                      messages.success('已删除');
                      subscriptions.reload();
                    }
                  });
                }}
              >
                删除
              </Button>
            )
          } as ColumnsType<NotificationSubscription>[number]
        ]
      : [])
  ];

  const inboxColumns: ColumnsType<InAppMessage> = [
    { title: '事件', dataIndex: 'event_type' },
    {
      title: '状态',
      render: (_: unknown, record: InAppMessage) => (record.read_at ? '已读' : '未读')
    },
    {
      title: '操作',
      render: (_: unknown, record: InAppMessage) =>
        record.read_at ? (
          '-'
        ) : (
          <Button
            type="link"
            onClick={async () => {
              await api.post(`/api/v1/in-app-messages/${record.id}/read`);
              inbox.reload();
            }}
          >
            标为已读
          </Button>
        )
    }
  ];

  return (
    <>
      <Card
        title="通知渠道"
        extra={
          canWrite ? (
            <Button type="primary" onClick={() => setChannelOpen(true)}>
              添加渠道
            </Button>
          ) : null
        }
      >
        <Typography.Paragraph type="secondary">
          投递已脱敏事件。IM 只发到官方地址，响应里不会出现机器人 token。
        </Typography.Paragraph>
        {channels.loading ? <LoadingState /> : null}
        {channels.error ? <ErrorState message={channels.error} onRetry={channels.reload} /> : null}
        {!channels.loading && !channels.error && (channels.data?.items.length ?? 0) === 0 ? (
          <Empty description="还没有通知渠道" />
        ) : null}
        {!channels.loading && !channels.error && (channels.data?.items.length ?? 0) > 0 ? (
          <Table rowKey="id" pagination={false} dataSource={channels.data?.items} columns={channelColumns} />
        ) : null}
      </Card>

      <Card
        title="系统消息订阅"
        extra={
          canWrite ? (
            <Button type="primary" onClick={() => setSubscriptionOpen(true)}>
              添加订阅
            </Button>
          ) : null
        }
      >
        {subscriptions.loading ? <LoadingState /> : null}
        {subscriptions.error ? <ErrorState message={subscriptions.error} onRetry={subscriptions.reload} /> : null}
        {!subscriptions.loading && !subscriptions.error && (subscriptions.data?.items.length ?? 0) === 0 ? (
          <Empty description="还没有系统消息订阅" />
        ) : null}
        {!subscriptions.loading && !subscriptions.error && (subscriptions.data?.items.length ?? 0) > 0 ? (
          <Table
            rowKey="id"
            pagination={false}
            dataSource={subscriptions.data?.items}
            columns={subscriptionColumns}
          />
        ) : null}
      </Card>

      <Card title="站内信">
        {inbox.loading ? <LoadingState /> : null}
        {inbox.error ? <ErrorState message={inbox.error} onRetry={inbox.reload} /> : null}
        {!inbox.loading && !inbox.error && (inbox.data?.items.length ?? 0) === 0 ? (
          <Empty description="还没有站内信" />
        ) : null}
        {!inbox.loading && !inbox.error && (inbox.data?.items.length ?? 0) > 0 ? (
          <Table rowKey="id" pagination={false} dataSource={inbox.data?.items} columns={inboxColumns} />
        ) : null}
      </Card>

      <Modal
        title="添加通知渠道"
        open={channelOpen}
        onCancel={() => setChannelOpen(false)}
        onOk={() => channelForm.submit()}
        destroyOnHidden
      >
        <Form
          form={channelForm}
          layout="vertical"
          initialValues={{ channel_type: 'webhook', url: 'https://', event_types: 'audit.event.created' }}
          onFinish={async (values) => {
            const eventTypes = values.event_types
              .split(',')
              .map((item) => item.trim())
              .filter(Boolean);
            try {
              await api.post('/api/v1/webhook-endpoints/', {
                name: values.name,
                channel_type: values.channel_type,
                url: values.channel_type === 'inbox' ? 'inbox://local' : values.url,
                target: values.target ?? '',
                event_types: eventTypes,
                credential: values.credential ?? ''
              });
              setChannelOpen(false);
              messages.success('已保存');
              channels.reload();
            } catch (err: unknown) {
              messages.error(getErrorMessage(err));
            }
          }}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item label="渠道类型" name="channel_type" rules={[{ required: true }]}>
            <Select
              options={CHANNEL_OPTIONS}
              onChange={(value: ChannelType) => {
                channelForm.setFieldValue('url', defaultUrl(value));
              }}
            />
          </Form.Item>
          {channelType === 'inbox' ? null : (
            <Form.Item label="地址" name="url" rules={[{ required: true, message: '请输入地址' }]}>
              <Input placeholder={defaultUrl(channelType ?? 'webhook')} />
            </Form.Item>
          )}
          {channelType === 'webhook' || channelType === 'inbox' ? null : (
            <Form.Item label="目标（邮箱 / 手机号 / Slack 频道）" name="target">
              <Input />
            </Form.Item>
          )}
          <Form.Item
            label="事件类型"
            name="event_types"
            rules={[{ required: true, message: '请输入事件类型' }]}
          >
            <Input placeholder="audit.event.created,session.recording.closed" />
          </Form.Item>
          {channelType === 'inbox' ? null : (
            <Form.Item label="渠道密钥" name="credential">
              <Input.Password autoComplete="new-password" placeholder="可选，保存后不会回显" />
            </Form.Item>
          )}
        </Form>
      </Modal>

      <Modal
        title="添加系统消息订阅"
        open={subscriptionOpen}
        onCancel={() => setSubscriptionOpen(false)}
        onOk={() => subscriptionForm.submit()}
        destroyOnHidden
      >
        <Form
          form={subscriptionForm}
          layout="vertical"
          initialValues={{ event_types: 'audit.event.created' }}
          onFinish={async (values) => {
            try {
              await api.post('/api/v1/notification-subscriptions/', {
                user_id: values.user_id,
                webhook_endpoint_id: values.webhook_endpoint_id,
                event_types: values.event_types
                  .split(',')
                  .map((item) => item.trim())
                  .filter(Boolean)
              });
              setSubscriptionOpen(false);
              messages.success('已保存');
              subscriptions.reload();
            } catch (err: unknown) {
              messages.error(getErrorMessage(err));
            }
          }}
        >
          <Form.Item label="用户 ID" name="user_id" rules={[{ required: true, message: '请输入用户 ID' }]}>
            <Input />
          </Form.Item>
          <Form.Item label="渠道" name="webhook_endpoint_id" rules={[{ required: true, message: '请选择渠道' }]}>
            <Select
              options={(channels.data?.items ?? []).map((item) => ({
                value: item.id,
                label: `${item.name}（${CHANNEL_LABEL[item.channel_type]}）`
              }))}
            />
          </Form.Item>
          <Form.Item label="事件类型" name="event_types" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
