import { Button, Card, Space, Table, Tag, Typography } from 'antd';
import { KeyOutlined } from '@ant-design/icons';
import { useEffect, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from './pageUtils';
import type { Account, CredentialRotation, ListResponse } from './types';

function statusTag(status: string) {
  const color = status === 'active' || status === 'completed' ? 'green' : status === 'failed' ? 'red' : 'blue';
  return <Tag color={color}>{status}</Tag>;
}

export function AccountsPage() {
  const { api } = useAuth();
  const toast = useApiMessage();
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [scheduling, setScheduling] = useState(false);
  const accounts = useApiData(() => api.get<ListResponse<Account>>('/api/v1/accounts/'), []);
  const rotations = useApiData(
    () =>
      selectedAccountId
        ? api.get<ListResponse<CredentialRotation>>(`/api/v1/accounts/${selectedAccountId}/rotations`)
        : Promise.resolve({ items: [], total: 0 }),
    [selectedAccountId]
  );

  useEffect(() => {
    if (!selectedAccountId && accounts.data?.items[0]) {
      setSelectedAccountId(accounts.data.items[0].id);
    }
  }, [accounts.data, selectedAccountId]);

  const scheduleRotation = async () => {
    if (!selectedAccountId) return;
    setScheduling(true);
    try {
      await api.post<CredentialRotation>(`/api/v1/accounts/${selectedAccountId}/rotations`, {
        reason: 'console requested rotation'
      });
      toast.success('已调度凭据轮换');
      rotations.reload();
    } catch (error) {
      toast.error(getErrorMessage(error));
    } finally {
      setScheduling(false);
    }
  };

  const loading = accounts.loading || rotations.loading;

  return (
    <section className="jg-page">
      <div className="jg-page-header">
        <div>
          <Typography.Title level={2}>账号托管与凭据轮换</Typography.Title>
          <Typography.Text type="secondary">
            查看当前租户可见资产账号，调度后端 CredentialRotation 任务；控制台只展示 Vault secret 引用。
          </Typography.Text>
        </div>
        <Space>
          <Tag color="blue">{accounts.data?.total ?? 0} Accounts</Tag>
          <Tag color="cyan">{rotations.data?.total ?? 0} Rotations</Tag>
          <Button
            type="primary"
            icon={<KeyOutlined />}
            aria-label="调度轮换"
            loading={scheduling}
            disabled={!selectedAccountId}
            onClick={scheduleRotation}
          >
            调度轮换
          </Button>
        </Space>
      </div>

      {loading ? <LoadingState /> : null}
      {accounts.error ? <ErrorState message={accounts.error} onRetry={accounts.reload} /> : null}
      {rotations.error ? <ErrorState message={rotations.error} onRetry={rotations.reload} /> : null}

      <div className="jg-card-grid">
        <Card title="Accounts">
          <Table
            rowKey="id"
            dataSource={accounts.data?.items ?? []}
            pagination={false}
            size="small"
            rowSelection={{
              type: 'radio',
              selectedRowKeys: selectedAccountId ? [selectedAccountId] : [],
              onChange: (keys) => setSelectedAccountId(Number(keys[0]))
            }}
            columns={[
              { title: '账号', dataIndex: 'username' },
              { title: '资产', dataIndex: 'asset_id' },
              { title: '协议', dataIndex: 'protocol' },
              { title: 'Secret 引用', dataIndex: 'secret_id' },
              { title: 'Project', dataIndex: 'project_id', render: (value: string | null) => value ?? '未绑定' },
              { title: '状态', dataIndex: 'status', render: statusTag },
              { title: '轮换策略', dataIndex: 'rotation_policy' }
            ]}
          />
        </Card>

        <Card title="Credential rotations">
          <Table
            rowKey="id"
            dataSource={rotations.data?.items ?? []}
            pagination={false}
            size="small"
            columns={[
              { title: 'ID', dataIndex: 'id' },
              { title: '状态', dataIndex: 'status', render: statusTag },
              { title: '原因', dataIndex: 'reason' },
              { title: '请求人', dataIndex: 'requested_by' },
              { title: '计划时间', dataIndex: 'scheduled_at', render: (value: string | null) => value ?? '立即' }
            ]}
          />
        </Card>
      </div>
    </section>
  );
}
