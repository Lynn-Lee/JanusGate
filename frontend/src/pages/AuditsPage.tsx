import { Button, Card, Descriptions, Drawer, Input, Select, Space, Statistic, Table, Tag, Typography } from 'antd';
import { useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from './pageUtils';
import type { AuditComplianceReport, AuditEvent, AuditListResponse, AuditReportSummary, FileTransferLog, ListResponse, OperateLog, PasswordChangeLog } from './types';

export const auditMetadataRedactedKeys = [
  'password',
  'passwd',
  'secret',
  'token',
  'access_token',
  'refresh_token',
  'api_key',
  'private_key',
  'authorization',
  'cookie',
  'credential',
  'credentials',
  'ssh_key',
  'connection_string',
  'dsn'
];

export function safeMetadata(metadata: Record<string, unknown>) {
  return JSON.stringify(
    metadata,
    (key, value) => (auditMetadataRedactedKeys.some((item) => key.toLowerCase().includes(item)) ? '******' : value),
    2
  );
}

export function AuditsPage() {
  const { api } = useAuth();
  const apiMessage = useApiMessage();
  const [selected, setSelected] = useState<AuditEvent | null>(null);
  const [downloadingCompliance, setDownloadingCompliance] = useState(false);
  const [complianceReport, setComplianceReport] = useState<AuditComplianceReport | null>(null);
  const summary = useApiData(() => api.get<AuditReportSummary>('/api/v1/audits/reports/summary'), []);
  const events = useApiData(() => api.get<AuditListResponse>('/api/v1/audits/events'), []);
  const transfers = useApiData(() => api.get<ListResponse<FileTransferLog>>('/api/v1/file-transfers/'), []);
  const operateLogs = useApiData(() => api.get<ListResponse<OperateLog>>('/api/v1/operate-logs/'), []);
  const passwordChanges = useApiData(() => api.get<ListResponse<PasswordChangeLog>>('/api/v1/password-change-logs/'), []);

  const downloadComplianceReport = async () => {
    setDownloadingCompliance(true);
    try {
      const report = await api.get<AuditComplianceReport>('/api/v1/audits/reports/compliance?template=soc2-access');
      setComplianceReport(report);
      const blob = new Blob([JSON.stringify(report, null, 2)], { type: report.content_type });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = report.download_filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      apiMessage.success('合规报表已生成');
    } catch (error) {
      apiMessage.error(getErrorMessage(error));
    } finally {
      setDownloadingCompliance(false);
    }
  };

  return (
    <section className="jg-page">
      <div className="jg-page-header">
        <div>
          <Typography.Title level={2}>审计日志</Typography.Title>
          <Typography.Text type="secondary">追踪登录、申请、审批、会话、文件传输、操作、改密、撤销和断连等关键安全事件。</Typography.Text>
        </div>
        <Button type="primary" loading={downloadingCompliance} onClick={downloadComplianceReport}>
          下载 SOC2 报表
        </Button>
      </div>
      <div className="jg-card-grid">
        <Card>
          {summary.loading ? <LoadingState /> : null}
          {summary.error ? <ErrorState message={summary.error} onRetry={summary.reload} /> : null}
          {!summary.loading && !summary.error ? (
            <Statistic title="报表总事件" value={summary.data?.total ?? 0} />
          ) : null}
        </Card>
        <Card>
          <Statistic title="高危事件" value={summary.data?.high_or_critical_total ?? 0} valueStyle={{ color: '#cf1322' }} />
        </Card>
        <Card>
          <Statistic title="SIEM failed" value={summary.data?.by_siem_delivery_status.failed ?? 0} />
        </Card>
        <Card>
          <Statistic title="合规报表事件" value={complianceReport?.total ?? 0} />
          {complianceReport ? (
            <Typography.Text type="secondary" copyable>
              {complianceReport.report_signature}
            </Typography.Text>
          ) : null}
        </Card>
      </div>
      <Card>
        <Space className="jg-block" wrap>
          <Input.Search placeholder="关键词 / 资源 / actor" style={{ width: 260 }} />
          <Select placeholder="事件类型" style={{ width: 180 }} allowClear options={[{ value: 'workflow', label: 'Workflow' }, { value: 'session', label: 'Session' }, { value: 'auth', label: 'Auth' }]} />
        </Space>
        {events.loading ? <LoadingState /> : null}
        {events.error ? <ErrorState message={events.error} onRetry={events.reload} /> : null}
        {!events.loading && !events.error ? (
          <Table
            rowKey="id"
            dataSource={events.data?.items ?? []}
            pagination={{ pageSize: 10 }}
            locale={{ emptyText: '暂无审计事件。主链路触发后会记录在这里。' }}
            columns={[
              { title: '时间', dataIndex: 'created_at' },
              { title: 'Actor', dataIndex: 'actor_username' },
              { title: '事件类型', dataIndex: 'event_type' },
              { title: '资源', render: (_: unknown, record: AuditEvent) => `${record.resource_type}:${record.resource_id}` },
              { title: '级别', dataIndex: 'severity', render: (value: string) => <Tag color={value === 'critical' || value === 'high' ? 'red' : 'blue'}>{value}</Tag> },
              { title: '结果', dataIndex: 'message', render: (value: string | null) => value || '-' },
              { title: '详情', render: (_: unknown, record: AuditEvent) => <a onClick={() => setSelected(record)}>查看脱敏 metadata</a> }
            ]}
          />
        ) : null}
      </Card>
      <Card title="文件传输日志">
        {transfers.loading ? <LoadingState /> : null}
        {transfers.error ? <ErrorState message={transfers.error} onRetry={transfers.reload} /> : null}
        {!transfers.loading && !transfers.error ? (
          <Table
            rowKey="id"
            dataSource={transfers.data?.items ?? []}
            pagination={{ pageSize: 10 }}
            locale={{ emptyText: '暂无文件传输日志。SFTP 传输入库后会出现在这里。' }}
            columns={[
              { title: '时间', dataIndex: 'occurred_at' },
              { title: '会话', dataIndex: 'session_id', ellipsis: true },
              { title: '方向', dataIndex: 'direction' },
              { title: '路径', dataIndex: 'remote_path', ellipsis: true },
              { title: '字节', dataIndex: 'size_bytes' },
              {
                title: '状态',
                dataIndex: 'status',
                render: (value: string) => <Tag color={value === 'failed' ? 'red' : 'green'}>{value}</Tag>
              }
            ]}
          />
        ) : null}
      </Card>
      <Card title="操作日志">
        {operateLogs.loading ? <LoadingState /> : null}
        {operateLogs.error ? <ErrorState message={operateLogs.error} onRetry={operateLogs.reload} /> : null}
        {!operateLogs.loading && !operateLogs.error ? (
          <Table
            rowKey="id"
            dataSource={operateLogs.data?.items ?? []}
            pagination={{ pageSize: 10 }}
            locale={{ emptyText: '暂无操作日志。管理面写操作入库后会出现在这里。' }}
            columns={[
              { title: '时间', dataIndex: 'occurred_at' },
              { title: '操作者', dataIndex: 'actor_username' },
              { title: '动作', dataIndex: 'action' },
              { title: '资源', render: (_: unknown, record: OperateLog) => `${record.resource_type}:${record.resource_id}` },
              { title: '摘要', dataIndex: 'summary', ellipsis: true }
            ]}
          />
        ) : null}
      </Card>
      <Card title="改密日志">
        {passwordChanges.loading ? <LoadingState /> : null}
        {passwordChanges.error ? <ErrorState message={passwordChanges.error} onRetry={passwordChanges.reload} /> : null}
        {!passwordChanges.loading && !passwordChanges.error ? (
          <Table
            rowKey="id"
            dataSource={passwordChanges.data?.items ?? []}
            pagination={{ pageSize: 10 }}
            locale={{ emptyText: '暂无改密日志。用户改密成功后会出现在这里。' }}
            columns={[
              { title: '时间', dataIndex: 'occurred_at' },
              { title: '用户', dataIndex: 'username' },
              { title: '方式', dataIndex: 'method' }
            ]}
          />
        ) : null}
      </Card>
      <Drawer title="审计详情" open={Boolean(selected)} onClose={() => setSelected(null)} width={560}>
        {selected ? (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="事件 ID">{selected.id}</Descriptions.Item>
            <Descriptions.Item label="动作">{selected.action}</Descriptions.Item>
            <Descriptions.Item label="会话">{selected.session_id || '-'}</Descriptions.Item>
            <Descriptions.Item label="metadata"><pre>{safeMetadata(selected.metadata)}</pre></Descriptions.Item>
          </Descriptions>
        ) : null}
      </Drawer>
    </section>
  );
}
