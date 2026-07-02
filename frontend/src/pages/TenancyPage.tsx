import { Card, Space, Table, Tag, Typography } from 'antd';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { useApiData } from './pageUtils';
import type { ListResponse, Organization, Project, Team } from './types';

function statusTag(status: string) {
  return <Tag color={status === 'active' ? 'green' : 'default'}>{status}</Tag>;
}

export function TenancyPage() {
  const { api } = useAuth();
  const organizations = useApiData(
    () => api.get<ListResponse<Organization>>('/api/v1/tenancy/organizations'),
    []
  );
  const teams = useApiData(() => api.get<ListResponse<Team>>('/api/v1/tenancy/teams'), []);
  const projects = useApiData(() => api.get<ListResponse<Project>>('/api/v1/tenancy/projects'), []);
  const loading = organizations.loading || teams.loading || projects.loading;

  return (
    <section className="jg-page">
      <div className="jg-page-header">
        <div>
          <Typography.Title level={2}>多租户组织结构</Typography.Title>
          <Typography.Text type="secondary">
            查看当前登录用户可见的 Organization、Team 与 Project 边界。
          </Typography.Text>
        </div>
        <Space>
          <Tag color="blue">{organizations.data?.total ?? 0} Organizations</Tag>
          <Tag color="cyan">{teams.data?.total ?? 0} Teams</Tag>
          <Tag color="purple">{projects.data?.total ?? 0} Projects</Tag>
        </Space>
      </div>

      {loading ? <LoadingState /> : null}
      {organizations.error ? <ErrorState message={organizations.error} onRetry={organizations.reload} /> : null}
      {teams.error ? <ErrorState message={teams.error} onRetry={teams.reload} /> : null}
      {projects.error ? <ErrorState message={projects.error} onRetry={projects.reload} /> : null}

      <div className="jg-card-grid">
        <Card title="Organizations">
          <Table
            rowKey="id"
            dataSource={organizations.data?.items ?? []}
            pagination={false}
            size="small"
            columns={[
              { title: '名称', dataIndex: 'name' },
              { title: 'ID', dataIndex: 'id' },
              { title: '租户', dataIndex: 'tenant_id' },
              { title: '状态', dataIndex: 'status', render: statusTag }
            ]}
          />
        </Card>

        <Card title="Teams">
          <Table
            rowKey="id"
            dataSource={teams.data?.items ?? []}
            pagination={false}
            size="small"
            columns={[
              { title: '名称', dataIndex: 'name' },
              { title: 'ID', dataIndex: 'id' },
              { title: 'Organization', dataIndex: 'organization_id' },
              { title: '租户', dataIndex: 'tenant_id' }
            ]}
          />
        </Card>

        <Card title="Projects">
          <Table
            rowKey="id"
            dataSource={projects.data?.items ?? []}
            pagination={false}
            size="small"
            columns={[
              { title: '名称', dataIndex: 'name' },
              { title: 'ID', dataIndex: 'id' },
              { title: 'Organization', dataIndex: 'organization_id' },
              { title: 'Team', dataIndex: 'team_id', render: (value: string | null) => value ?? '未绑定' },
              { title: '状态', dataIndex: 'status', render: statusTag }
            ]}
          />
        </Card>
      </div>
    </section>
  );
}
