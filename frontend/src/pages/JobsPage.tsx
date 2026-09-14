import { PlusOutlined } from '@ant-design/icons';
import { Button, Card, Form, Input, InputNumber, Select, Space, Switch, Table, Tag, Typography } from 'antd';
import { useMemo, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from './pageUtils';
import type { Account, Asset, JobDefinition, JobExecution, JobPlaybook, JobVariable, ListResponse } from './types';

type PlaybookForm = { name: string; filename: string; content: string };
type VariableForm = { name: string; extra_vars_json: string };
type JobForm = {
  name: string;
  kind: 'playbook' | 'adhoc';
  playbook_id?: number;
  adhoc_args?: string;
  target_asset_ids: number[];
  extra_var_names?: string[];
  runas_account_id?: number | null;
  interval_seconds?: number | null;
  check_mode: boolean;
  enabled: boolean;
};

function statusTag(status: string) {
  const color = status === 'completed' || status === 'queued' ? (status === 'completed' ? 'green' : 'blue') : status === 'failed' ? 'red' : 'blue';
  return <Tag color={color}>{status}</Tag>;
}

export function JobsPage() {
  const { api } = useAuth();
  const toast = useApiMessage();
  const [playbookForm] = Form.useForm<PlaybookForm>();
  const [variableForm] = Form.useForm<VariableForm>();
  const [jobForm] = Form.useForm<JobForm>();
  const kind = Form.useWatch('kind', jobForm) ?? 'playbook';
  const [creatingPlaybook, setCreatingPlaybook] = useState(false);
  const [creatingVariable, setCreatingVariable] = useState(false);
  const [creatingJob, setCreatingJob] = useState(false);
  const [runningId, setRunningId] = useState<number | null>(null);
  const [ticking, setTicking] = useState(false);

  const playbooks = useApiData(() => api.get<ListResponse<JobPlaybook>>('/api/v1/job-center/playbooks/'), []);
  const variables = useApiData(() => api.get<ListResponse<JobVariable>>('/api/v1/job-center/variables/'), []);
  const jobs = useApiData(() => api.get<ListResponse<JobDefinition>>('/api/v1/job-center/jobs/'), []);
  const executions = useApiData(() => api.get<ListResponse<JobExecution>>('/api/v1/job-center/executions/'), []);
  const assets = useApiData(() => api.get<Asset[]>('/api/v1/assets/'), []);
  const accounts = useApiData(() => api.get<ListResponse<Account>>('/api/v1/accounts/'), []);

  const assetOptions = useMemo(
    () => (assets.data ?? []).map((item) => ({ label: `${item.name} (${item.address})`, value: item.id })),
    [assets.data]
  );
  const accountOptions = useMemo(
    () => (accounts.data?.items ?? []).map((item) => ({ label: `${item.username}#${item.id}`, value: item.id })),
    [accounts.data]
  );

  const loading = playbooks.loading || variables.loading || jobs.loading || executions.loading;
  const error = playbooks.error || variables.error || jobs.error || executions.error;
  if (loading && !playbooks.data) return <LoadingState />;
  if (error && !playbooks.data) return <ErrorState message={error} />;

  const createPlaybook = async (values: PlaybookForm) => {
    setCreatingPlaybook(true);
    try {
      await api.post('/api/v1/job-center/playbooks/', values);
      toast.success('已创建 Playbook');
      playbookForm.resetFields();
      playbooks.reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setCreatingPlaybook(false);
    }
  };

  const createVariable = async (values: VariableForm) => {
    setCreatingVariable(true);
    try {
      const extra_vars = JSON.parse(values.extra_vars_json || '{}') as Record<string, unknown>;
      await api.post('/api/v1/job-center/variables/', { name: values.name, extra_vars });
      toast.success('已创建变量');
      variableForm.resetFields();
      variables.reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setCreatingVariable(false);
    }
  };

  const createJob = async (values: JobForm) => {
    setCreatingJob(true);
    try {
      await api.post('/api/v1/job-center/jobs/', {
        name: values.name,
        kind: values.kind,
        playbook_id: values.kind === 'playbook' ? values.playbook_id : null,
        adhoc_module: 'command',
        adhoc_args: values.kind === 'adhoc' ? values.adhoc_args ?? '' : '',
        target_asset_ids: values.target_asset_ids,
        extra_var_names: values.extra_var_names ?? [],
        runas_account_id: values.runas_account_id || null,
        interval_seconds: values.interval_seconds || null,
        check_mode: values.check_mode,
        enabled: values.enabled
      });
      toast.success('已创建作业');
      jobForm.resetFields();
      jobForm.setFieldsValue({ kind: 'playbook', check_mode: false, enabled: true });
      jobs.reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setCreatingJob(false);
    }
  };

  const runJob = async (job: JobDefinition) => {
    setRunningId(job.id);
    try {
      await api.post(`/api/v1/job-center/jobs/${job.id}/run`, {});
      toast.success('已入队');
      executions.reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setRunningId(null);
    }
  };

  const tickScheduler = async () => {
    setTicking(true);
    try {
      const result = await api.post<{ queued: number }>('/api/v1/job-center/scheduler/tick', {});
      toast.success(`已调度 ${result.queued} 个到期作业`);
      executions.reload();
      jobs.reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setTicking(false);
    }
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space align="center" style={{ width: '100%', justifyContent: 'space-between' }}>
        <div>
          <Typography.Title level={3} style={{ marginBottom: 0 }}>
            作业中心
          </Typography.Title>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            Playbook / 临时命令走 JSON-only 队列，不使用 pickle；runas 只带账号 ID，凭据不进队列。
          </Typography.Paragraph>
        </div>
        <Button onClick={() => void tickScheduler()} loading={ticking}>
          调度到期作业
        </Button>
      </Space>

      <Card title="Playbook 目录">
        <Form form={playbookForm} layout="inline" onFinish={(values) => void createPlaybook(values)} style={{ marginBottom: 16 }}>
          <Form.Item name="name" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="名称" />
          </Form.Item>
          <Form.Item name="filename" rules={[{ required: true, message: '请输入文件名' }]}>
            <Input placeholder="linux-baseline.yml" />
          </Form.Item>
          <Form.Item name="content" rules={[{ required: true, message: '请输入 YAML' }]}>
            <Input.TextArea placeholder="---" rows={1} style={{ width: 280 }} />
          </Form.Item>
          <Form.Item>
            <Button htmlType="submit" type="primary" icon={<PlusOutlined />} loading={creatingPlaybook}>
              创建 Playbook
            </Button>
          </Form.Item>
        </Form>
        <Table
          rowKey="id"
          pagination={false}
          dataSource={playbooks.data?.items ?? []}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '文件名', dataIndex: 'filename' }
          ]}
        />
      </Card>

      <Card title="作业变量">
        <Form form={variableForm} layout="inline" onFinish={(values) => void createVariable(values)} style={{ marginBottom: 16 }}>
          <Form.Item name="name" rules={[{ required: true, message: '请输入变量名' }]}>
            <Input placeholder="region" />
          </Form.Item>
          <Form.Item name="extra_vars_json" initialValue="{}" rules={[{ required: true, message: '请输入 JSON' }]}>
            <Input.TextArea placeholder='{"region":"ap-east"}' rows={1} style={{ width: 320 }} />
          </Form.Item>
          <Form.Item>
            <Button htmlType="submit" type="primary" loading={creatingVariable}>
              创建变量
            </Button>
          </Form.Item>
        </Form>
        <Table
          rowKey="id"
          pagination={false}
          dataSource={variables.data?.items ?? []}
          columns={[
            { title: '名称', dataIndex: 'name' },
            {
              title: 'extra vars',
              dataIndex: 'extra_vars',
              render: (value: Record<string, unknown>) => JSON.stringify(value)
            }
          ]}
        />
      </Card>

      <Card title="作业">
        <Form
          form={jobForm}
          layout="inline"
          initialValues={{ kind: 'playbook', check_mode: false, enabled: true }}
          onFinish={(values) => void createJob(values)}
          style={{ marginBottom: 16 }}
        >
          <Form.Item name="name" rules={[{ required: true, message: '请输入作业名' }]}>
            <Input placeholder="作业名" />
          </Form.Item>
          <Form.Item name="kind">
            <Select
              style={{ width: 120 }}
              options={[
                { value: 'playbook', label: 'Playbook' },
                { value: 'adhoc', label: '临时命令' }
              ]}
            />
          </Form.Item>
          {kind === 'playbook' ? (
            <Form.Item name="playbook_id" rules={[{ required: true, message: '请选择 Playbook' }]}>
              <Select
                placeholder="Playbook"
                style={{ width: 180 }}
                options={(playbooks.data?.items ?? []).map((item) => ({ label: item.name, value: item.id }))}
              />
            </Form.Item>
          ) : (
            <Form.Item name="adhoc_args" rules={[{ required: true, message: '请输入 command 参数' }]}>
              <Input placeholder="uptime" />
            </Form.Item>
          )}
          <Form.Item name="target_asset_ids" rules={[{ required: true, message: '请选择目标资产' }]}>
            <Select mode="multiple" placeholder="目标资产" style={{ minWidth: 200 }} options={assetOptions} />
          </Form.Item>
          <Form.Item name="extra_var_names">
            <Select
              mode="multiple"
              placeholder="变量"
              style={{ minWidth: 140 }}
              options={(variables.data?.items ?? []).map((item) => ({ label: item.name, value: item.name }))}
            />
          </Form.Item>
          <Form.Item name="runas_account_id">
            <Select allowClear placeholder="runas 账号" style={{ width: 160 }} options={accountOptions} />
          </Form.Item>
          <Form.Item name="interval_seconds">
            <InputNumber min={60} placeholder="周期秒" />
          </Form.Item>
          <Form.Item name="check_mode" valuePropName="checked">
            <Switch checkedChildren="check" unCheckedChildren="执行" />
          </Form.Item>
          <Form.Item name="enabled" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="停用" />
          </Form.Item>
          <Form.Item>
            <Button htmlType="submit" type="primary" loading={creatingJob}>
              创建作业
            </Button>
          </Form.Item>
        </Form>
        <Table
          rowKey="id"
          pagination={false}
          dataSource={jobs.data?.items ?? []}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '类型', dataIndex: 'kind' },
            {
              title: '目标',
              dataIndex: 'target_asset_ids',
              render: (ids: number[]) => ids.join(', ')
            },
            {
              title: '周期',
              dataIndex: 'interval_seconds',
              render: (value: number | null) => value ?? '一次性'
            },
            {
              title: '状态',
              dataIndex: 'enabled',
              render: (enabled: boolean) => <Tag color={enabled ? 'green' : 'default'}>{enabled ? '启用' : '停用'}</Tag>
            },
            {
              title: '操作',
              render: (_: unknown, job: JobDefinition) => (
                <Button size="small" onClick={() => void runJob(job)} loading={runningId === job.id}>
                  立即执行
                </Button>
              )
            }
          ]}
        />
      </Card>

      <Card title="执行记录">
        <Table
          rowKey="id"
          pagination={false}
          dataSource={executions.data?.items ?? []}
          columns={[
            { title: '作业 ID', dataIndex: 'job_id' },
            { title: 'message_id', dataIndex: 'message_id' },
            { title: '状态', dataIndex: 'status', render: (value: string) => statusTag(value) },
            { title: '错误码', dataIndex: 'error_code' }
          ]}
        />
      </Card>
    </Space>
  );
}
