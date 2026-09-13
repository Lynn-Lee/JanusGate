import { Button, Card, Form, Input, Select, Space, Switch, Table, Tag, Typography } from 'antd';
import { PlayCircleOutlined, PlusOutlined } from '@ant-design/icons';
import { useMemo, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from './pageUtils';
import type { Account, Asset, ListResponse, OpsExecution, OpsJob, OpsPlaybook } from './types';

type PlaybookForm = {
  name: string;
  relative_path: string;
  description?: string;
};

type JobForm = {
  name: string;
  job_type: 'playbook' | 'adhoc';
  playbook_id?: number;
  adhoc_module?: string;
  command?: string;
  runas_account_id: number;
  target_asset_ids: number[];
  cron_expr?: string;
  check_mode: boolean;
};

type AdhocForm = {
  runas_account_id: number;
  target_asset_ids: number[];
  adhoc_module: string;
  command: string;
  check_mode: boolean;
};

function statusTag(status: string) {
  const color = status === 'active' || status === 'completed' || status === 'queued' ? 'green' : status === 'failed' ? 'red' : 'blue';
  return <Tag color={color}>{status}</Tag>;
}

export function JobsPage() {
  const { api } = useAuth();
  const toast = useApiMessage();
  const [playbookForm] = Form.useForm<PlaybookForm>();
  const [jobForm] = Form.useForm<JobForm>();
  const [adhocForm] = Form.useForm<AdhocForm>();
  const [savingPlaybook, setSavingPlaybook] = useState(false);
  const [savingJob, setSavingJob] = useState(false);
  const [runningAdhoc, setRunningAdhoc] = useState(false);
  const [ticking, setTicking] = useState(false);
  const [runningJobIds, setRunningJobIds] = useState<Set<number>>(new Set());
  const jobType = Form.useWatch('job_type', jobForm);

  const playbooks = useApiData(() => api.get<ListResponse<OpsPlaybook>>('/api/v1/ops/playbooks'), []);
  const jobs = useApiData(() => api.get<ListResponse<OpsJob>>('/api/v1/ops/jobs'), []);
  const executions = useApiData(() => api.get<ListResponse<OpsExecution>>('/api/v1/ops/executions'), []);
  const assets = useApiData(() => api.get<Asset[]>('/api/v1/assets/'), []);
  const accounts = useApiData(() => api.get<ListResponse<Account>>('/api/v1/accounts/'), []);

  const sshAccounts = useMemo(
    () => (accounts.data?.items ?? []).filter((item) => item.protocol.toLowerCase() === 'ssh' && item.status === 'active'),
    [accounts.data]
  );

  const loading = playbooks.loading || jobs.loading || executions.loading;
  const error = playbooks.error || jobs.error || executions.error;

  const createPlaybook = async (values: PlaybookForm) => {
    setSavingPlaybook(true);
    try {
      await api.post('/api/v1/ops/playbooks', {
        name: values.name,
        relative_path: values.relative_path,
        description: values.description ?? ''
      });
      toast.success('已登记 Playbook');
      playbookForm.resetFields();
      playbooks.reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setSavingPlaybook(false);
    }
  };

  const createJob = async (values: JobForm) => {
    setSavingJob(true);
    try {
      await api.post('/api/v1/ops/jobs', {
        name: values.name,
        job_type: values.job_type,
        playbook_id: values.job_type === 'playbook' ? values.playbook_id : null,
        adhoc_module: values.job_type === 'adhoc' ? values.adhoc_module : null,
        command: values.job_type === 'adhoc' ? values.command : null,
        runas_account_id: values.runas_account_id,
        target_asset_ids: values.target_asset_ids,
        cron_expr: values.cron_expr || null,
        check_mode: values.check_mode
      });
      toast.success('已创建作业');
      jobForm.resetFields();
      jobs.reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setSavingJob(false);
    }
  };

  const runJob = async (jobId: number) => {
    setRunningJobIds((prev) => new Set(prev).add(jobId));
    try {
      await api.post(`/api/v1/ops/jobs/${jobId}/run`, {});
      toast.success('已入队执行');
      executions.reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setRunningJobIds((prev) => {
        const next = new Set(prev);
        next.delete(jobId);
        return next;
      });
    }
  };

  const runAdhoc = async (values: AdhocForm) => {
    setRunningAdhoc(true);
    try {
      await api.post('/api/v1/ops/adhoc', values);
      toast.success('临时命令已入队');
      adhocForm.resetFields();
      executions.reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setRunningAdhoc(false);
    }
  };

  const tickScheduler = async () => {
    setTicking(true);
    try {
      const result = await api.post<{ queued_execution_ids: number[] }>('/api/v1/ops/scheduler/tick', {});
      toast.success(`已扫描周期作业，入队 ${result.queued_execution_ids.length} 条`);
      jobs.reload();
      executions.reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setTicking(false);
    }
  };

  if (loading && !playbooks.data) {
    return <LoadingState title="正在加载作业中心" />;
  }
  if (error && !playbooks.data) {
    return <ErrorState message={error} onRetry={() => { playbooks.reload(); jobs.reload(); executions.reload(); }} />;
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        作业中心
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        Playbook、周期作业与临时命令走 JSON-only 队列。命令、变量和执行身份从执行记录加载，不进 Redis payload，也不把凭据写进 Ansible argv。
      </Typography.Paragraph>

      <Card title="Playbook 目录" extra={<Button icon={<PlusOutlined />} onClick={() => playbookForm.submit()} loading={savingPlaybook}>登记</Button>}>
        <Form form={playbookForm} layout="inline" onFinish={createPlaybook} style={{ marginBottom: 16 }}>
          <Form.Item name="name" rules={[{ required: true, message: '填写名称' }]}>
            <Input placeholder="名称" />
          </Form.Item>
          <Form.Item name="relative_path" rules={[{ required: true, message: '填写相对路径' }]}>
            <Input placeholder="相对路径，如 linux-baseline.yml" style={{ width: 280 }} />
          </Form.Item>
          <Form.Item name="description">
            <Input placeholder="说明（可选）" />
          </Form.Item>
        </Form>
        <Table
          rowKey="id"
          pagination={false}
          dataSource={playbooks.data?.items ?? []}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '路径', dataIndex: 'relative_path' },
            { title: '状态', dataIndex: 'status', render: statusTag }
          ]}
        />
      </Card>

      <Card
        title="作业"
        extra={
          <Space>
            <Button onClick={() => void tickScheduler()} loading={ticking}>扫描周期任务</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => jobForm.submit()} loading={savingJob}>
              创建作业
            </Button>
          </Space>
        }
      >
        <Form form={jobForm} layout="vertical" onFinish={createJob} initialValues={{ job_type: 'playbook', check_mode: false }}>
          <Space wrap size={12} align="start">
            <Form.Item name="name" label="名称" rules={[{ required: true, message: '填写名称' }]}>
              <Input placeholder="作业名称" />
            </Form.Item>
            <Form.Item name="job_type" label="类型">
              <Select
                style={{ width: 140 }}
                options={[
                  { value: 'playbook', label: 'Playbook' },
                  { value: 'adhoc', label: '临时命令' }
                ]}
              />
            </Form.Item>
            {jobType === 'playbook' ? (
              <Form.Item name="playbook_id" label="Playbook" rules={[{ required: true, message: '选择 Playbook' }]}>
                <Select
                  style={{ width: 220 }}
                  options={(playbooks.data?.items ?? []).map((item) => ({ value: item.id, label: item.name }))}
                />
              </Form.Item>
            ) : (
              <>
                <Form.Item name="adhoc_module" label="模块" initialValue="command">
                  <Select
                    style={{ width: 140 }}
                    options={[
                      { value: 'command', label: 'command' },
                      { value: 'shell', label: 'shell' }
                    ]}
                  />
                </Form.Item>
                <Form.Item name="command" label="命令" rules={[{ required: true, message: '填写命令' }]}>
                  <Input placeholder="uptime" style={{ width: 240 }} />
                </Form.Item>
              </>
            )}
            <Form.Item name="runas_account_id" label="执行身份" rules={[{ required: true, message: '选择 SSH 账号' }]}>
              <Select
                style={{ width: 200 }}
                options={sshAccounts.map((item) => ({ value: item.id, label: `${item.username}#${item.id}` }))}
              />
            </Form.Item>
            <Form.Item name="target_asset_ids" label="目标资产" rules={[{ required: true, message: '选择资产' }]}>
              <Select
                mode="multiple"
                style={{ minWidth: 220 }}
                options={(assets.data ?? []).map((item) => ({ value: item.id, label: item.name }))}
              />
            </Form.Item>
            <Form.Item name="cron_expr" label="Cron（UTC）">
              <Input placeholder="留空即手动，如 */5 * * * *" style={{ width: 200 }} />
            </Form.Item>
            <Form.Item name="check_mode" label="Check mode" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
        </Form>
        <Table
          rowKey="id"
          pagination={false}
          dataSource={jobs.data?.items ?? []}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '类型', dataIndex: 'job_type' },
            { title: 'Cron', dataIndex: 'cron_expr', render: (value: string | null) => value || '手动' },
            { title: '状态', dataIndex: 'status', render: statusTag },
            {
              title: '操作',
              render: (_: unknown, row: OpsJob) => (
                <Button
                  size="small"
                  icon={<PlayCircleOutlined />}
                  loading={runningJobIds.has(row.id)}
                  onClick={() => void runJob(row.id)}
                >
                  立即执行
                </Button>
              )
            }
          ]}
        />
      </Card>

      <Card title="临时命令" extra={<Button type="primary" onClick={() => adhocForm.submit()} loading={runningAdhoc}>入队</Button>}>
        <Form form={adhocForm} layout="inline" onFinish={runAdhoc} initialValues={{ adhoc_module: 'command', check_mode: false }}>
          <Form.Item name="runas_account_id" rules={[{ required: true, message: '选择 SSH 账号' }]}>
            <Select
              placeholder="执行身份"
              style={{ width: 180 }}
              options={sshAccounts.map((item) => ({ value: item.id, label: `${item.username}#${item.id}` }))}
            />
          </Form.Item>
          <Form.Item name="target_asset_ids" rules={[{ required: true, message: '选择资产' }]}>
            <Select
              mode="multiple"
              placeholder="目标资产"
              style={{ minWidth: 200 }}
              options={(assets.data ?? []).map((item) => ({ value: item.id, label: item.name }))}
            />
          </Form.Item>
          <Form.Item name="adhoc_module">
            <Select
              style={{ width: 120 }}
              options={[
                { value: 'command', label: 'command' },
                { value: 'shell', label: 'shell' }
              ]}
            />
          </Form.Item>
          <Form.Item name="command" rules={[{ required: true, message: '填写命令' }]}>
            <Input placeholder="命令，入队前走命令过滤 ACL" style={{ width: 280 }} />
          </Form.Item>
          <Form.Item name="check_mode" valuePropName="checked">
            <Switch checkedChildren="check" unCheckedChildren="执行" />
          </Form.Item>
        </Form>
      </Card>

      <Card title="执行记录">
        <Table
          rowKey="id"
          pagination={false}
          dataSource={executions.data?.items ?? []}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 80 },
            { title: '类型', dataIndex: 'job_type' },
            { title: 'Playbook', dataIndex: 'playbook_name' },
            { title: '命令', dataIndex: 'command', render: (value: string | null) => value || '—' },
            { title: '状态', dataIndex: 'status', render: statusTag },
            { title: '错误码', dataIndex: 'error_code', render: (value: string | null) => value || '—' },
            { title: '请求人', dataIndex: 'requested_by' }
          ]}
        />
      </Card>
    </Space>
  );
}
