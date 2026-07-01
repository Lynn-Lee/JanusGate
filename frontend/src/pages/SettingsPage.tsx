import { Card, Descriptions, Space, Tag, Typography } from 'antd';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { useApiData } from './pageUtils';

type Health = { status: string; version?: string };

export function SettingsPage() {
  const { api, user } = useAuth();
  const health = useApiData(() => api.get<Health>('/health'), []);
  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || '同源 / Vite 代理';

  return (
    <section className="jg-page">
      <div className="jg-page-header">
        <div>
          <Typography.Title level={2}>系统设置</Typography.Title>
          <Typography.Text type="secondary">展示运行时和安全配置摘要；MVP 不提供密钥在线编辑。</Typography.Text>
        </div>
      </div>
      <div className="jg-card-grid">
        <Card title="运行时状态">
          {health.loading ? <LoadingState /> : null}
          {health.error ? <ErrorState message={health.error} onRetry={health.reload} /> : null}
          {health.data ? (
            <Descriptions column={1} size="small">
              <Descriptions.Item label="当前版本">{health.data.version ?? '0.1.0'}</Descriptions.Item>
              <Descriptions.Item label="健康状态"><Tag color={health.data.status === 'ok' ? 'green' : 'red'}>{health.data.status}</Tag></Descriptions.Item>
              <Descriptions.Item label="API base URL">{apiBaseUrl}</Descriptions.Item>
            </Descriptions>
          ) : null}
        </Card>
        <Card title="安全配置摘要">
          <Space direction="vertical">
            <Tag color={user?.totp_enabled ? 'green' : 'gold'}>{user?.totp_enabled ? 'MFA 已启用' : 'MFA 未启用'}</Tag>
            <Tag color="blue">JWT Bearer 访问令牌</Tag>
            <Tag color="blue">统一 ErrorResponse / request_id 追踪</Tag>
            <Tag color="purple">Secret 通过环境变量 / SecretProvider 注入</Tag>
          </Space>
        </Card>
        <Card title="部署信息摘要">
          <Descriptions column={1} size="small">
            <Descriptions.Item label="环境">Docker Compose / Helm 均有基线</Descriptions.Item>
            <Descriptions.Item label="数据库">PostgreSQL / SQLAlchemy async</Descriptions.Item>
            <Descriptions.Item label="缓存">Redis 用于 MFA / Token 状态</Descriptions.Item>
            <Descriptions.Item label="边界">不展示或编辑真实密钥、Token、连接串</Descriptions.Item>
          </Descriptions>
        </Card>
      </div>
    </section>
  );
}
