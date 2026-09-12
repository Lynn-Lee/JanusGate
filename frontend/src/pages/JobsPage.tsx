import { Button, Card, Form, Input, Modal, Select, Space, Table, Tag, Typography } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useMemo, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from './pageUtils';
import type { Account, Asset, ListResponse, OpsJob, OpsJobExecution, OpsJobVariable, OpsPlaybook } from './types';

type PlaybookForm = { name: string; filename: string; description?: string };
type JobForm = {
  name: string;
  job_kind: 'playbook' | 'adhoc';
  playbook_id?: number;
  adhoc_module?: 'shell' | 'command';
  adhoc_command?: string;
  extra_vars_text?: string;
  target_asset_ids: number[];
  runas_account_id: number;
  cron_expr?: string;
  timezone?: string;
};

function statusTag(status: string) {
  const color = status === 'completed' || status === 'queued' ? 'green' : status === 'failed' ? 'red' : 'blue';
  return <Tag color={color}>{status}</Tag>;
}

export function JobsPage() {
  const { api } = useAuth();
  const toast = useApiMessage();
  const [playbookOpen, setPlaybookOpen] = useState(false);
  const [jobOpen, setJobOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [runningId, setRunningId] = useState<number | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [playbookForm] = Form.useForm<PlaybookForm>();
  const [jobForm] = Form.useForm<JobForm>();
  const jobKind = Form.useWatch('job_kind', jobForm);

  const playbooks = useApiData(() => api.get<ListResponse<OpsPlaybook>>('/api/v1/job-center/playbooks'), []);
  const jobs = useApiData(() => api.get<ListResponse<OpsJob>>('/api/v1/job-center/jobs'), []);
  const executions = useApiData(() => api.get<ListResponse<OpsJobExecution>>('/api/v1/job-center/executions'), []);
  const accounts = useApiData(() => api.get<ListResponse<Account>>('/api/v1/accounts/'), []);
  const assets = useApiData(() => api.get<Asset[]>('/api/v1/assets/'), []);
  const variables = useApiData(
    () =>
      selectedJobId
        ? api.get<ListResponse<OpsJobVariable>>(`/api/v1/job-center/jobs/${selectedJobId}/variables`)
        : Promise.resolve({ items: [], total: 0 }),
    [selectedJobId]
  );

  const assetOptions = useMemo(
    () => (assets.data ?? []).map((item) => ({ value: item.id, label: `${item.name} (${item.address})` })),
    [assets.data]
  );
  const accountOptions = useMemo(
    () =>
      (accounts.data?.items ?? [])
        .filter((item) => item.protocol.toLowerCase() === 'ssh')
        .map((item) => ({ value: item.id, label: `${item.username}#${item.id}` })),
    [accounts.data]
  );
  const playbookOptions = useMemo(
    () => (playbooks.data?.items ?? []).map((item) => ({ value: item.id, label: `${item.name} (${item.filename})` })),
    [playbooks.data]
  );

  const createPlaybook = async (values: PlaybookForm) => {
    setSaving(true);
    try {
      await api.post('/api/v1/job-center/playbooks', {
        name: values.name,
        filename: values.filename,
        description: values.description ?? ''
      });
      toast.success('Playbook 已登记');
      setPlaybookOpen(false);
      playbookForm.resetFields();
      playbooks.reload();
    } catch (error: unknown) {
      toast.error(getErrorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  const createJob = async (values: JobForm) => {
    setSaving(true);
    try {
      let extraVars: Record<string, unknown> = {};
      if (values.extra_vars_text?.trim()) {
        extraVars = JSON.parse(values.extra_vars_text) as Record<string, unknown>;
      }
      await api.post('/api/v1/job-center/jobs', {
        name: values.name,
        job_kind: values.job_kind,
        playbook_id: values.job_kind === 'playbook' ? values.playbook_id : null,
        adhoc_module: values.job_kind === 'adhoc' ? values.adhoc_module : null,
        adhoc_command: values.job_kind === 'adhoc' ? values.adhoc_command : null,
        extra_vars: extraVars,
        target_asset_ids: values.target_asset_ids,
        runas_account_id: values.runas_account_id,
        cron_expr: values.cron_expr || null,
        timezone: values.timezone || 'UTC'
      });
      toast.success('作业已创建');
      setJobOpen(false);
      jobForm.resetFields();
      jobs.reload();
    } catch (error: unknown) {
      toast.error(getErrorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  const runJob = async (jobId: number) => {
    setRunningId(jobId);
    try {
      await api.post(`/api/v1/job-center/jobs/${jobId}/run`);
      toast.success('作业已入队');
      executions.reload();
    } catch (error: unknown) {
      toast.error(getErrorMessage(error));
    } finally {
      setRunningId(null);
    }
  };

  if (playbooks.loading || jobs.loading) return <LoadingState />;
  if (playbooks.error) return <ErrorState message={playbooks.error} />;
  if (jobs.error) return <ErrorState message={jobs.error} />;

  return (
    <section className="jg-page">
      <div className="jg-page-header">
        <div>
          <Typography.Title level={2}>作业中心</Typography.Title>
          <Typography.Text type="secondary">
            Playbook / 临时命令 / 参数化 / runas / 周期调度。队列只传 job_id，不使用 pickle。
          </Typography.Text>
        </div>
        <Space>
          <Button onClick={() => setPlaybookOpen(true)}>登记 Playbook</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setJobOpen(true)}>
            新建作业
          </Button>
        </Space>
      </div>
      <div className="jg-card-grid">
        <Card title="Playbook 目录">
          <Table
            rowKey="id"
            pagination={false}
            dataSource={playbooks.data?.items ?? []}
            locale={{ emptyText: '还没有 Playbook' }}
            columns={[
              { title: '名称', dataIndex: 'name' },
              { title: '文件', dataIndex: 'filename' },
              { title: '说明', dataIndex: 'description' }
            ]}
          />
        </Card>
        <Card title="作业">
          <Table
            rowKey="id"
            pagination={false}
            dataSource={jobs.data?.items ?? []}
            locale={{ emptyText: '还没有作业' }}
            onRow={(record) => ({ onClick: () => setSelectedJobId(record.id) })}
            columns={[
              { title: '名称', dataIndex: 'name' },
              { title: '类型', dataIndex: 'job_kind' },
              { title: 'Runas 账号', dataIndex: 'runas_account_id' },
              { title: 'Cron', dataIndex: 'cron_expr', render: (value: string | null) => value || '手动' },
              {
                title: '操作',
                render: (_: unknown, record: OpsJob) => (
                  <Button size="small" loading={runningId === record.id} onClick={() => void runJob(record.id)}>
                    立即执行
                  </Button>
                )
              }
            ]}
          />
        </Card>
        <Card title={selectedJobId ? `作业 #${selectedJobId} 变量` : '作业变量'}>
          <Table
            rowKey="id"
            pagination={false}
            dataSource={variables.data?.items ?? []}
            locale={{ emptyText: '选择作业查看变量' }}
            columns={[
              { title: '名称', dataIndex: 'name' },
              { title: '值', dataIndex: 'value' }
            ]}
          />
        </Card>
        <Card title="执行记录">
          {executions.error ? <ErrorState message={executions.error} /> : null}
          <Table
            rowKey="message_id"
            pagination={false}
            dataSource={executions.data?.items ?? []}
            locale={{ emptyText: '还没有执行记录' }}
            columns={[
              { title: '作业', dataIndex: 'ops_job_id' },
              { title: '类型', dataIndex: 'job_type' },
              { title: '状态', dataIndex: 'status', render: (value: string) => statusTag(value) },
              { title: '参数键', dataIndex: 'extra_var_keys', render: (value: string[]) => (value ?? []).join(', ') },
              { title: '错误码', dataIndex: 'error_code' }
            ]}
          />
        </Card>
      </div>
      <Modal title="登记 Playbook" open={playbookOpen} onCancel={() => setPlaybookOpen(false)} footer={null} destroyOnHidden>
        <Form form={playbookForm} layout="vertical" onFinish={(values) => void createPlaybook(values)}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="filename" label="文件名" rules={[{ required: true }]} extra="仅允许 playbook root 内相对 .yml/.yaml 文件名">
            <Input placeholder="linux-baseline.yml" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saving}>
            保存
          </Button>
        </Form>
      </Modal>
      <Modal title="新建作业" open={jobOpen} onCancel={() => setJobOpen(false)} footer={null} destroyOnHidden>
        <Form form={jobForm} layout="vertical" initialValues={{ job_kind: 'playbook', timezone: 'UTC' }} onFinish={(values) => void createJob(values)}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="job_kind" label="类型" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'playbook', label: 'Playbook' },
                { value: 'adhoc', label: '临时命令' }
              ]}
            />
          </Form.Item>
          {jobKind === 'playbook' ? (
            <Form.Item name="playbook_id" label="Playbook" rules={[{ required: true }]}>
              <Select options={playbookOptions} />
            </Form.Item>
          ) : (
            <>
              <Form.Item name="adhoc_module" label="模块" rules={[{ required: true }]}>
                <Select
                  options={[
                    { value: 'command', label: 'command' },
                    { value: 'shell', label: 'shell' }
                  ]}
                />
              </Form.Item>
              <Form.Item name="adhoc_command" label="命令" rules={[{ required: true }]}>
                <Input placeholder="uptime" />
              </Form.Item>
            </>
          )}
          <Form.Item name="target_asset_ids" label="目标资产" rules={[{ required: true }]}>
            <Select mode="multiple" options={assetOptions} />
          </Form.Item>
          <Form.Item name="runas_account_id" label="Runas 账号" rules={[{ required: true }]}>
            <Select options={accountOptions} />
          </Form.Item>
          <Form.Item name="extra_vars_text" label="参数 JSON" extra="禁止 password/token/secret 等键，也不会进入队列">
            <Input.TextArea rows={3} placeholder='{"cluster":"prod"}' />
          </Form.Item>
          <Form.Item name="cron_expr" label="Cron（可选）">
            <Input placeholder="0 1 * * *" />
          </Form.Item>
          <Form.Item name="timezone" label="时区">
            <Input />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saving}>
            保存
          </Button>
        </Form>
      </Modal>
    </section>
  );
}
