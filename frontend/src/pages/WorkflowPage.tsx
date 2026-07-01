import { Button, Card, Form, Input, InputNumber, Space, Table, Tag, Typography } from 'antd';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage, useSessionCache } from './pageUtils';
import type { JitGrant, ListResponse, SessionRecord, WorkflowRequest } from './types';

type WorkflowFormValues = {
  asset_id: string;
  account_id: string;
  protocol: string;
  reason: string;
  requested_ttl_seconds: number;
};

type LocationState = { assetId?: string; accountId?: string; protocol?: string } | null;

export function WorkflowPage() {
  const { api } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const state = location.state as LocationState;
  const requests = useApiData(() => api.get<ListResponse<WorkflowRequest>>('/api/v1/workflows/requests'), []);
  const grants = useApiData(() => api.get<ListResponse<JitGrant>>('/api/v1/workflows/grants/active'), []);
  const msg = useApiMessage();
  const cache = useSessionCache();

  const refresh = () => {
    requests.reload();
    grants.reload();
  };

  const createRequest = async (values: WorkflowFormValues) => {
    try {
      const created = await api.post<WorkflowRequest>('/api/v1/workflows/requests', {
        ...values,
        action: 'session.connect',
        metadata: { source: 'frontend-console' }
      });
      await api.post<WorkflowRequest>(`/api/v1/workflows/requests/${created.id}/submit`);
      msg.success('JIT 申请已提交');
      refresh();
    } catch (err) {
      msg.error(getErrorMessage(err));
    }
  };

  const decide = async (request: WorkflowRequest, action: 'approve' | 'reject') => {
    try {
      await api.post<WorkflowRequest>(`/api/v1/workflows/requests/${request.id}/${action}`, action === 'approve' ? { decision_reason: 'MVP 控制台审批通过', grant_ttl_seconds: 1800 } : { decision_reason: 'MVP 控制台拒绝' });
      msg.success(action === 'approve' ? '申请已批准' : '申请已拒绝');
      refresh();
    } catch (err) {
      msg.error(getErrorMessage(err));
    }
  };

  const revoke = async (request: WorkflowRequest) => {
    try {
      await api.post<WorkflowRequest>(`/api/v1/workflows/requests/${request.id}/revoke`, { reason: 'console_revoked' });
      msg.success('Grant 已撤销，绑定 active session 将被关闭');
      refresh();
    } catch (err) {
      msg.error(getErrorMessage(err));
    }
  };

  const createSession = async (grant: JitGrant) => {
    try {
      const session = await api.post<SessionRecord>('/api/v1/sessions/', {
        asset_id: grant.asset_id,
        account_id: grant.account_id,
        protocol: grant.protocol,
        connection_token: `jit-${grant.id}`,
        jit_grant_id: grant.id
      });
      const next = [session, ...cache.read().filter((item) => item.id !== session.id)];
      cache.write(next);
      msg.success('会话已创建');
      navigate('/sessions');
    } catch (err) {
      msg.error(getErrorMessage(err));
    }
  };

  return (
    <section className="jg-page">
      <div className="jg-page-header">
        <div>
          <Typography.Title level={2}>Workflow/JIT 申请审批</Typography.Title>
          <Typography.Text type="secondary">覆盖申请、审批、active grant、创建会话和撤销闭环。</Typography.Text>
        </div>
      </div>
      <div className="jg-card-grid">
        <Card title="发起 JIT 申请">
          <Form layout="vertical" initialValues={{ asset_id: state?.assetId ?? '', account_id: state?.accountId ?? '', protocol: state?.protocol ?? 'ssh', requested_ttl_seconds: 1800 }} onFinish={(values) => void createRequest(values as WorkflowFormValues)}>
            <Form.Item label="资产 ID" name="asset_id" rules={[{ required: true, message: '请输入资产 ID' }]}><Input /></Form.Item>
            <Form.Item label="账号" name="account_id" rules={[{ required: true, message: '请输入目标账号' }]}><Input /></Form.Item>
            <Form.Item label="协议" name="protocol" rules={[{ required: true, message: '请输入协议' }]}><Input /></Form.Item>
            <Form.Item label="访问理由" name="reason" rules={[{ required: true, message: '请输入访问理由' }]}><Input.TextArea rows={3} /></Form.Item>
            <Form.Item label="申请时长（秒）" name="requested_ttl_seconds" rules={[{ required: true, message: '请输入申请时长' }]}><InputNumber min={60} max={86400} style={{ width: '100%' }} /></Form.Item>
            <Button type="primary" htmlType="submit">提交申请</Button>
          </Form>
        </Card>
        <Card title="Active grants">
          {grants.loading ? <LoadingState /> : null}
          {grants.error ? <ErrorState message={grants.error} onRetry={grants.reload} /> : null}
          {!grants.loading && !grants.error ? (
            <Table
              size="small"
              rowKey="id"
              dataSource={grants.data?.items ?? []}
              pagination={false}
              locale={{ emptyText: '暂无可用 active grant。审批通过后会显示在这里。' }}
              columns={[
                { title: 'Grant', dataIndex: 'id', ellipsis: true },
                { title: '资产', dataIndex: 'asset_id' },
                { title: '状态', dataIndex: 'status', render: (status: string) => <Tag color="green">{status}</Tag> },
                { title: '操作', render: (_: unknown, grant: JitGrant) => <Button type="primary" onClick={() => void createSession(grant)}>创建会话</Button> }
              ]}
            />
          ) : null}
        </Card>
      </div>
      <Card title="我的申请 / 待审批">
        {requests.loading ? <LoadingState /> : null}
        {requests.error ? <ErrorState message={requests.error} onRetry={requests.reload} /> : null}
        {!requests.loading && !requests.error ? (
          <Table
            rowKey="id"
            dataSource={requests.data?.items ?? []}
            pagination={{ pageSize: 8 }}
            locale={{ emptyText: '暂无 JIT 申请。请先提交申请。' }}
            columns={[
              { title: '申请 ID', dataIndex: 'id', ellipsis: true },
              { title: '资产', dataIndex: 'asset_id' },
              { title: '账号', dataIndex: 'account_id' },
              { title: '理由', dataIndex: 'reason', ellipsis: true },
              { title: '状态', dataIndex: 'status', render: (status: string) => <Tag color={status === 'approved' ? 'green' : status === 'rejected' ? 'red' : 'blue'}>{status}</Tag> },
              { title: 'Grant', dataIndex: 'grant_id', ellipsis: true, render: (value: string) => value || '-' },
              { title: '操作', render: (_: unknown, record: WorkflowRequest) => <Space><Button disabled={record.status !== 'pending'} onClick={() => void decide(record, 'approve')}>批准</Button><Button disabled={record.status !== 'pending'} onClick={() => void decide(record, 'reject')}>拒绝</Button><Button disabled={!['approved', 'pending'].includes(record.status)} danger onClick={() => void revoke(record)}>撤销</Button></Space> }
            ]}
          />
        ) : null}
      </Card>
    </section>
  );
}
