import { Button, Card, Empty, Form, Input, Modal, Space, Switch, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';
import { useAuth } from '../../auth/AuthContext';
import { ErrorState, LoadingState } from '../../components/StatusView';
import { UserSelect } from '../../components/UserSelect';
import { getErrorMessage, useApiData, useApiMessage } from '../pageUtils';
import type { ListResponse, TicketFlow } from '../types';

export function TicketFlowPanels({ canWrite }: { canWrite: boolean }) {
  const { api } = useAuth();
  const messages = useApiMessage();
  const flows = useApiData(() => api.get<ListResponse<TicketFlow>>('/api/v1/workflows/ticket-flows'), []);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<TicketFlow | null>(null);
  const [form] = Form.useForm<{
    name: string;
    enabled: boolean;
    levels: Array<{ approver_user_ids: string[] }>;
  }>();

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({
      name: '',
      enabled: false,
      levels: [{ approver_user_ids: [] }]
    });
    setOpen(true);
  };

  const openEdit = (row: TicketFlow) => {
    setEditing(row);
    form.setFieldsValue({
      name: row.name,
      enabled: row.enabled,
      levels: (row.levels ?? []).map((level) => ({
        approver_user_ids: level.approver_user_ids ?? []
      }))
    });
    setOpen(true);
  };

  const confirmDelete = (row: TicketFlow) => {
    Modal.confirm({
      title: '确定删除这条审批流？',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await api.delete(`/api/v1/workflows/ticket-flows/${row.id}`);
          messages.success('已删除');
          flows.reload();
        } catch (err) {
          messages.error(getErrorMessage(err));
          throw err;
        }
      }
    });
  };

  const columns: ColumnsType<TicketFlow> = [
    { title: '名称', dataIndex: 'name' },
    { title: '级数', dataIndex: 'level_count', render: (count: number) => `${count} 级` },
    {
      title: '状态',
      dataIndex: 'enabled',
      render: (enabled: boolean) => (enabled ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>)
    },
    ...(canWrite
      ? [
          {
            title: '操作',
            render: (_: unknown, record: TicketFlow) => (
              <Space>
                <Button type="link" onClick={() => openEdit(record)}>
                  编辑
                </Button>
                <Button type="link" danger onClick={() => confirmDelete(record)}>
                  删除
                </Button>
              </Space>
            )
          } as ColumnsType<TicketFlow>[number]
        ]
      : [])
  ];

  const items = flows.data?.items ?? [];
  const enabledOther = items.find((item) => item.enabled && item.id !== editing?.id);

  const saveFlow = async (values: {
    name: string;
    enabled: boolean;
    levels: Array<{ approver_user_ids: string[] }>;
  }) => {
    const levels = (values.levels ?? [])
      .map((level) => ({
        approver_user_ids: (level.approver_user_ids ?? []).map(String).filter(Boolean)
      }))
      .filter((level) => level.approver_user_ids.length > 0);
    if (
      levels.length < 1 ||
      levels.length > 3 ||
      levels.some((level) => level.approver_user_ids.length < 1 || level.approver_user_ids.length > 3)
    ) {
      messages.error('请配置 1–3 级审批，且每级 1–3 名用户');
      return;
    }
    const payload = {
      name: values.name.trim(),
      enabled: Boolean(values.enabled),
      levels
    };
    const doSave = async () => {
      if (editing) {
        await api.patch(`/api/v1/workflows/ticket-flows/${editing.id}`, payload);
      } else {
        await api.post('/api/v1/workflows/ticket-flows', payload);
      }
      setOpen(false);
      messages.success('已保存');
      flows.reload();
    };
    if (payload.enabled && enabledOther) {
      Modal.confirm({
        title: '将停用当前启用的审批流',
        okText: '继续',
        cancelText: '取消',
        onOk: () => doSave()
      });
      return;
    }
    await doSave();
  };

  return (
    <>
      <Card
        title="审批流"
        extra={
          canWrite ? (
            <Button type="primary" onClick={openCreate}>
              创建审批流
            </Button>
          ) : null
        }
      >
        {flows.loading ? <LoadingState /> : null}
        {flows.error ? <ErrorState message={flows.error} onRetry={flows.reload} /> : null}
        {!flows.loading && !flows.error && items.length === 0 ? (
          <Empty description="还没有审批流">
            {canWrite ? (
              <Button type="primary" onClick={openCreate}>
                创建审批流
              </Button>
            ) : null}
          </Empty>
        ) : null}
        {!flows.loading && !flows.error && items.length > 0 ? (
          <Table rowKey="id" pagination={false} dataSource={items} columns={columns} />
        ) : null}
      </Card>

      <Modal
        title={editing ? '编辑审批流' : '创建审批流'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
        width={640}
      >
        <Form form={form} layout="vertical" onFinish={(values) => void saveFlow(values)}>
          <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item label="类型">
            <Input value="固定资产授权" disabled />
          </Form.Item>
          <Form.Item label="启用" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
            任一级拒绝整单拒，全过才签发。
          </Typography.Paragraph>
          <Form.List name="levels">
            {(fields, { add, remove }) => (
              <>
                {fields.map((field, index) => (
                  <Space key={field.key} align="baseline" style={{ display: 'flex', marginBottom: 8 }}>
                    <Form.Item
                      {...field}
                      label={`第 ${index + 1} 级审批人`}
                      name={[field.name, 'approver_user_ids']}
                      rules={[{ required: true, message: '请选择审批人' }]}
                    >
                      <UserSelect mode="multiple" maxCount={3} placeholder="每级 1–3 名用户" />
                    </Form.Item>
                    {fields.length > 1 ? (
                      <Button type="link" danger onClick={() => remove(field.name)}>
                        删除本级
                      </Button>
                    ) : null}
                  </Space>
                ))}
                {fields.length < 3 ? (
                  <Button type="dashed" onClick={() => add({ approver_user_ids: [] })} block>
                    添加一级
                  </Button>
                ) : null}
              </>
            )}
          </Form.List>
        </Form>
      </Modal>
    </>
  );
}
