import { Button, Card, Form, Input, Select, Space, Switch, Table, Tag, Typography } from 'antd';
import { PlayCircleOutlined, PlusOutlined } from '@ant-design/icons';
import { useMemo, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from './pageUtils';
import type { Asset, Job, JobExecution, JobPlaybook, ListResponse } from './types';

function statusTag(status: string) {
  const color =
    status === 'completed' || status === 'success'
      ? 'green'
      : status === 'failed'
        ? 'red'
        : status === 'running'
          ? 'blue'
          : 'default';
  return <Tag color={color}>{status}</Tag>;
}

type PlaybookFormValues = {
  name: string;
  playbook_name: string;
  description?: string;
};

type JobFormValues = {
  name: string;
  playbook_id: number;
  target_asset_ids: number[];
  check_mode: boolean;
  description?: string;
};

export function JobsPage() {
  const { api } = useAuth();
  const toast = useApiMessage();
  const [creatingPlaybook, setCreatingPlaybook] = useState(false);
  const [creatingJob, setCreatingJob] = useState(false);
  const [runningIds, setRunningIds] = useState<Set<number>>(new Set());
  const [playbookForm] = Form.useForm<PlaybookFormValues>();
  const [jobForm] = Form.useForm<JobFormValues>();

  const playbooks = useApiData(() => api.get<ListResponse<JobPlaybook>>('/api/v1/job-center/playbooks/'), []);
  const jobs = useApiData(() => api.get<ListResponse<Job>>('/api/v1/job-center/jobs/'), []);
  const executions = useApiData(
    () => api.get<ListResponse<JobExecution>>('/api/v1/job-center/executions/'),
    []
  );
  const assets = useApiData(() => api.get<ListResponse<Asset>>('/api/v1/assets/'), []);

  const playbookOptions = useMemo(
    () =>
      (playbooks.data?.items ?? [])
        .filter((item) => item.is_active)
        .map((item) => ({ value: item.id, label: `${item.name} (${item.playbook_name})` })),
    [playbooks.data]
  );

  const assetOptions = useMemo(
    () =>
      (assets.data?.items ?? [])
        .filter((item) => item.is_active)
        .map((item) => ({ value: item.id, label: `${item.name} (${item.address})` })),
    [assets.data]
  );

  const createPlaybook = async (values: PlaybookFormValues) => {
    setCreatingPlaybook(true);
    try {
      await api.post('/api/v1/job-center/playbooks/', {
        name: values.name,
        playbook_name: values.playbook_name,
        description: values.description || '',
        is_active: true
      });
      toast.success('Playbook 已创建');
      playbookForm.resetFields();
      await playbooks.reload();
    } catch (error) {
      toast.error(getErrorMessage(error, '创建 Playbook 失败'));
    } finally {
      setCreatingPlaybook(false);
    }
  };

  const createJob = async (values: JobFormValues) => {
    setCreatingJob(true);
    try {
      await api.post('/api/v1/job-center/jobs/', {
        name: values.name,
        playbook_id: values.playbook_id,
        target_asset_ids: values.target_asset_ids,
        check_mode: values.check_mode,
        description: values.description || '',
        is_active: true
      });
      toast.success('作业已创建');
      jobForm.resetFields();
      await jobs.reload();
    } catch (error) {
      toast.error(getErrorMessage(error, '创建作业失败'));
    } finally {
      setCreatingJob(false);
    }
  };

  const runJob = async (jobId: number) => {
    setRunningIds((prev) => new Set(prev).add(jobId));
    try {
      await api.post(`/api/v1/job-center/jobs/${jobId}/run`, {});
      toast.success('作业已入队');
      await executions.reload();
    } catch (error) {
      toast.error(getErrorMessage(error, '触发作业失败'));
    } finally {
      setRunningIds((prev) => {
        const next = new Set(prev);
        next.delete(jobId);
        return next;
      });
    }
  };

  if (playbooks.loading || jobs.loading || executions.loading || assets.loading) {
    return <LoadingState tip="加载作业中心…" />;
  }
  if (playbooks.error || jobs.error || executions.error || assets.error) {
    return <ErrorState message="无法加载作业中心数据" />;
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        作业中心
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        Playbook 目录与可复用作业定义；执行经 JSON-only 自动化队列下发，不携带凭据。
      </Typography.Paragraph>

      <Card title="注册 Playbook">
        <Form form={playbookForm} layout="inline" onFinish={createPlaybook}>
          <Form.Item name="name" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="显示名称" />
          </Form.Item>
          <Form.Item
            name="playbook_name"
            rules={[{ required: true, message: '请输入相对路径，如 noop.yml' }]}
          >
            <Input placeholder="noop.yml" style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="description">
            <Input placeholder="说明（可选）" style={{ width: 220 }} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" icon={<PlusOutlined />} loading={creatingPlaybook}>
              创建
            </Button>
          </Form.Item>
        </Form>
        <Table
          style={{ marginTop: 16 }}
          rowKey="id"
          pagination={false}
          dataSource={playbooks.data?.items ?? []}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '文件', dataIndex: 'playbook_name' },
            { title: '说明', dataIndex: 'description' },
            {
              title: '状态',
              dataIndex: 'is_active',
              render: (active: boolean) => (active ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>)
            }
          ]}
        />
      </Card>

      <Card title="作业定义">
        <Form
          form={jobForm}
          layout="inline"
          onFinish={createJob}
          initialValues={{ check_mode: false, target_asset_ids: [] }}
        >
          <Form.Item name="name" rules={[{ required: true, message: '请输入作业名' }]}>
            <Input placeholder="作业名称" />
          </Form.Item>
          <Form.Item name="playbook_id" rules={[{ required: true, message: '请选择 Playbook' }]}>
            <Select placeholder="Playbook" options={playbookOptions} style={{ width: 220 }} />
          </Form.Item>
          <Form.Item name="target_asset_ids" rules={[{ required: true, message: '请选择目标资产' }]}>
            <Select
              mode="multiple"
              placeholder="目标资产"
              options={assetOptions}
              style={{ minWidth: 240 }}
            />
          </Form.Item>
          <Form.Item name="check_mode" label="check" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" icon={<PlusOutlined />} loading={creatingJob}>
              创建作业
            </Button>
          </Form.Item>
        </Form>
        <Table
          style={{ marginTop: 16 }}
          rowKey="id"
          pagination={false}
          dataSource={jobs.data?.items ?? []}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: 'Playbook', dataIndex: 'playbook_name' },
            {
              title: '目标',
              dataIndex: 'target_asset_ids',
              render: (ids: number[]) => ids.join(', ')
            },
            {
              title: 'check',
              dataIndex: 'check_mode',
              render: (value: boolean) => (value ? '是' : '否')
            },
            {
              title: '操作',
              render: (_: unknown, record: Job) => (
                <Button
                  size="small"
                  type="link"
                  icon={<PlayCircleOutlined />}
                  loading={runningIds.has(record.id)}
                  disabled={!record.is_active}
                  onClick={() => void runJob(record.id)}
                >
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
            { title: 'ID', dataIndex: 'id', width: 80 },
            { title: '作业', dataIndex: 'job_id', width: 80 },
            { title: 'Playbook', dataIndex: 'playbook_name' },
            { title: '状态', dataIndex: 'status', render: statusTag },
            { title: '目标数', dataIndex: 'target_count', width: 90 },
            { title: '消息 ID', dataIndex: 'message_id' },
            { title: '错误码', dataIndex: 'error_code', render: (value: string | null) => value || '—' }
          ]}
        />
      </Card>
    </Space>
  );
}
