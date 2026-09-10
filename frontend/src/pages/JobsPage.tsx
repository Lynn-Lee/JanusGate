import { Button, Card, Form, Input, Select, Space, Switch, Table, Tag, Typography } from 'antd';
import { useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from './pageUtils';
import type { JobDefinition, JobRun, ListResponse } from './types';

const JOB_TYPE_OPTIONS = [
  { value: 'ansible.playbook', label: 'Playbook' },
  { value: 'adhoc.command', label: '临时命令' },
  { value: 'batch.command', label: '批量命令' }
];

export function JobsPage() {
  const { api } = useAuth();
  const apiMessage = useApiMessage();
  const jobs = useApiData(() => api.get<ListResponse<JobDefinition>>('/api/v1/job-center/jobs'), []);
  const [running, setRunning] = useState('');
  const [form] = Form.useForm();

  const createJob = async (values: {
    name: string;
    job_type: string;
    playbook_name?: string;
    command?: string;
    target_asset_ids: string;
    extra_variables?: string;
    cron_expression?: string;
    check_mode?: boolean;
    run_as_user_id?: string;
  }) => {
    const target_asset_ids = values.target_asset_ids
      .split(',')
      .map((item) => Number(item.trim()))
      .filter((item) => Number.isInteger(item) && item > 0);
    const extra_variables = values.extra_variables ? (JSON.parse(values.extra_variables) as Record<string, unknown>) : {};
    const payload =
      values.job_type === 'ansible.playbook'
        ? { playbook_name: values.playbook_name, target_asset_ids, check_mode: Boolean(values.check_mode) }
        : { command: values.command, target_asset_ids };
    try {
      await api.post('/api/v1/job-center/jobs', {
        name: values.name,
        job_type: values.job_type,
        payload,
        extra_variables,
        cron_expression: values.cron_expression || null,
        run_as_user_id: values.run_as_user_id || null
      });
      form.resetFields();
      jobs.reload();
      apiMessage.success('作业已保存');
    } catch (error) {
      apiMessage.error(getErrorMessage(error));
    }
  };

  const runJob = async (job: JobDefinition) => {
    setRunning(job.id);
    try {
      const result = await api.post<JobRun>(`/api/v1/job-center/jobs/${job.id}/run`, { extra_variables: {} });
      apiMessage.success(`已入队 ${result.message_id}`);
    } catch (error) {
      apiMessage.error(getErrorMessage(error));
    } finally {
      setRunning('');
    }
  };

  const dispatchCron = async () => {
    try {
      const result = await api.post<{ dispatched_job_ids: string[] }>('/api/v1/job-center/cron/dispatch', {});
      apiMessage.success(`周期调度 ${result.dispatched_job_ids.length} 个作业`);
    } catch (error) {
      apiMessage.error(getErrorMessage(error));
    }
  };

  return (
    <section className="jg-page">
      <div className="jg-page-header">
        <div>
          <Typography.Title level={2}>作业中心</Typography.Title>
          <Typography.Text type="secondary">
            管理 Playbook、临时/批量命令、参数化变量与周期任务。队列仅接受 JSON，不会下发凭据或 pickle。
          </Typography.Text>
        </div>
        <Button onClick={() => void dispatchCron()}>调度到期周期任务</Button>
      </div>
      <Card title="新建作业">
        <Form form={form} layout="vertical" onFinish={(values) => void createJob(values)} initialValues={{ job_type: 'ansible.playbook' }}>
          <Space wrap size="large">
            <Form.Item name="name" label="名称" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item name="job_type" label="类型" rules={[{ required: true }]}>
              <Select options={JOB_TYPE_OPTIONS} style={{ minWidth: 160 }} />
            </Form.Item>
            <Form.Item name="playbook_name" label="Playbook">
              <Input placeholder="linux-baseline.yml" />
            </Form.Item>
            <Form.Item name="command" label="命令">
              <Input placeholder="uptime" />
            </Form.Item>
            <Form.Item name="target_asset_ids" label="目标资产 ID" rules={[{ required: true }]}>
              <Input placeholder="1,2" />
            </Form.Item>
            <Form.Item name="extra_variables" label="变量 JSON">
              <Input placeholder="{}" />
            </Form.Item>
            <Form.Item name="cron_expression" label="Cron">
              <Input placeholder="*/15 * * * *" />
            </Form.Item>
            <Form.Item name="run_as_user_id" label="执行身份">
              <Input placeholder="默认当前用户" />
            </Form.Item>
            <Form.Item name="check_mode" label="Check mode" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit">
                保存作业
              </Button>
            </Form.Item>
          </Space>
        </Form>
      </Card>
      <Card>
        {jobs.loading ? <LoadingState /> : null}
        {jobs.error ? <ErrorState message={jobs.error} onRetry={jobs.reload} /> : null}
        <Table
          rowKey="id"
          loading={jobs.loading}
          dataSource={jobs.data?.items ?? []}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '类型', dataIndex: 'job_type', render: (value: string) => <Tag>{value}</Tag> },
            { title: 'Cron', dataIndex: 'cron_expression', render: (value: string | null) => value || '-' },
            { title: 'Runas', dataIndex: 'run_as_user_id', render: (value: string | null) => value || '触发人' },
            {
              title: '启用',
              dataIndex: 'enabled',
              render: (value: boolean) => <Tag color={value ? 'green' : 'default'}>{value ? '是' : '否'}</Tag>
            },
            {
              title: '操作',
              render: (_: unknown, record: JobDefinition) => (
                <Button type="link" loading={running === record.id} onClick={() => void runJob(record)}>
                  立即执行
                </Button>
              )
            }
          ]}
        />
      </Card>
    </section>
  );
}
