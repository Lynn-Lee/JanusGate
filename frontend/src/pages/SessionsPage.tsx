import { Button, Card, Space, Table, Tag, Typography } from 'antd';
import { useAuth } from '../auth/AuthContext';
import { useApiMessage, useSessionCache, getErrorMessage } from './pageUtils';
import type { SessionRecord } from './types';
import { useState } from 'react';

export function SessionsPage() {
  const { api } = useAuth();
  const { read, write } = useSessionCache();
  const [sessions, setSessions] = useState<SessionRecord[]>(read);
  const [closing, setClosing] = useState('');
  const msg = useApiMessage();

  const closeSession = async (session: SessionRecord) => {
    setClosing(session.id);
    try {
      const updated = await api.post<SessionRecord>(`/api/v1/sessions/${session.id}/close`, { reason: 'console_requested' });
      const next = sessions.map((item) => (item.id === updated.id ? updated : item));
      setSessions(next);
      write(next);
      msg.success('会话已关闭');
    } catch (err) {
      msg.error(getErrorMessage(err));
    } finally {
      setClosing('');
    }
  };

  return (
    <section className="jg-page">
      <div className="jg-page-header">
        <div>
          <Typography.Title level={2}>会话列表</Typography.Title>
          <Typography.Text type="secondary">展示本控制台创建的会话，并提供关闭与审计追踪入口。</Typography.Text>
        </div>
      </div>
      <Card>
        <Table
          rowKey="id"
          dataSource={sessions}
          locale={{ emptyText: '当前尚无会话。请在 Workflow/JIT 页面使用 active grant 创建会话。' }}
          columns={[
            { title: '会话 ID', dataIndex: 'id', ellipsis: true },
            { title: '资产', dataIndex: 'asset_id' },
            { title: '账号', dataIndex: 'account_id' },
            { title: '协议', dataIndex: 'protocol' },
            { title: 'JIT Grant', dataIndex: 'jit_grant_id', ellipsis: true },
            { title: '状态', dataIndex: 'status', render: (status: string) => <Tag color={status === 'active' ? 'green' : 'default'}>{status}</Tag> },
            { title: '开始时间', dataIndex: 'created_at' },
            { title: '关闭时间', dataIndex: 'closed_at', render: (value: string | null) => value || '-' },
            { title: '操作', render: (_: unknown, record: SessionRecord) => <Space><Button disabled={record.status !== 'active'} loading={closing === record.id} onClick={() => void closeSession(record)}>关闭会话</Button><Button href="/audits">查看审计</Button></Space> }
          ]}
        />
      </Card>
    </section>
  );
}
