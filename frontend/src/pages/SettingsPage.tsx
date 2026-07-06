import { Alert, Button, Card, Descriptions, Form, Input, Select, Space, Tag, Typography } from 'antd';
import { useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from './pageUtils';

type Health = { status: string; version?: string };
type LicenseSummary = {
  configured_edition: string;
  effective_edition: string;
  license_status: string;
  enabled_features: string[];
  disabled_features: string[];
  expires_at: string | null;
};
type LicenseConfigForm = {
  configured_edition: 'community' | 'enterprise';
  license_verifier: 'hmac' | 'ed25519' | 'external-http';
  license_key: string;
  license_signing_secret?: string;
  license_public_key?: string;
};

const licenseStatusColor = (status: string) => {
  if (status === 'active') return 'green';
  if (status === 'not_configured') return 'gold';
  return 'red';
};

export function SettingsPage() {
  const { api, user } = useAuth();
  const messages = useApiMessage();
  const health = useApiData(() => api.get<Health>('/health'), []);
  const license = useApiData(() => api.get<LicenseSummary>('/api/v1/admin/license-summary'), []);
  const [licenseFormOpen, setLicenseFormOpen] = useState(false);
  const [licenseSubmitError, setLicenseSubmitError] = useState('');
  const [licenseSubmitting, setLicenseSubmitting] = useState(false);
  const [savedLicense, setSavedLicense] = useState<LicenseSummary | null>(null);
  const licenseSummary = savedLicense ?? license.data;
  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || '同源 / Vite 代理';

  const saveLicenseConfig = async (values: LicenseConfigForm) => {
    setLicenseSubmitting(true);
    setLicenseSubmitError('');
    try {
      const summary = await api.post<LicenseSummary>('/api/v1/admin/license-config', {
        configured_edition: values.configured_edition,
        license_verifier: values.license_verifier,
        license_key: values.license_key,
        license_signing_secret: values.license_signing_secret ?? '',
        license_public_key: values.license_public_key ?? ''
      });
      setSavedLicense(summary);
      setLicenseFormOpen(false);
      messages.success('License 配置已保存');
    } catch (err: unknown) {
      setLicenseSubmitError(getErrorMessage(err));
    } finally {
      setLicenseSubmitting(false);
    }
  };

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
        <Card title="License / Edition 边界">
          {license.loading ? <LoadingState /> : null}
          {license.error ? <ErrorState message={license.error} onRetry={license.reload} /> : null}
          {licenseSummary ? (
            <Space direction="vertical" size="middle">
              <Descriptions column={1} size="small">
                <Descriptions.Item label="Configured edition">
                  configured: {licenseSummary.configured_edition}
                </Descriptions.Item>
                <Descriptions.Item label="Effective edition">
                  effective: {licenseSummary.effective_edition}
                </Descriptions.Item>
                <Descriptions.Item label="License status">
                  <Tag color={licenseStatusColor(licenseSummary.license_status)}>
                    {licenseSummary.license_status}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="Expires at">{licenseSummary.expires_at ?? '未配置'}</Descriptions.Item>
              </Descriptions>
              <Space direction="vertical" size="small">
                <Typography.Text type="secondary">启用能力</Typography.Text>
                <Space wrap>
                  {licenseSummary.enabled_features.map((feature) => (
                    <Tag color="green" key={feature}>{feature}</Tag>
                  ))}
                </Space>
                <Typography.Text type="secondary">禁用能力</Typography.Text>
                <Space wrap>
                  {licenseSummary.disabled_features.map((feature) => (
                    <Tag key={feature}>{feature}</Tag>
                  ))}
                </Space>
              </Space>
              <Button onClick={() => setLicenseFormOpen((open) => !open)}>保存 License 配置</Button>
              {licenseFormOpen ? (
                <Form
                  layout="vertical"
                  initialValues={{
                    configured_edition: 'enterprise',
                    license_verifier: 'hmac',
                    license_key: '',
                    license_signing_secret: '',
                    license_public_key: ''
                  }}
                  onFinish={(values) => void saveLicenseConfig(values as LicenseConfigForm)}
                >
                  {licenseSubmitError ? <Alert showIcon type="error" message={licenseSubmitError} /> : null}
                  <Form.Item label="Configured edition" name="configured_edition" rules={[{ required: true }]}>
                    <Select
                      options={[
                        { value: 'community', label: 'community' },
                        { value: 'enterprise', label: 'enterprise' }
                      ]}
                    />
                  </Form.Item>
                  <Form.Item label="License verifier" name="license_verifier" rules={[{ required: true }]}>
                    <Select
                      options={[
                        { value: 'hmac', label: 'hmac' },
                        { value: 'ed25519', label: 'ed25519' },
                        { value: 'external-http', label: 'external-http' }
                      ]}
                    />
                  </Form.Item>
                  <Form.Item label="License key" name="license_key" rules={[{ required: true, message: '请输入 license key' }]}>
                    <Input.TextArea rows={3} autoComplete="off" />
                  </Form.Item>
                  <Form.Item label="Signing secret" name="license_signing_secret">
                    <Input.Password autoComplete="off" />
                  </Form.Item>
                  <Form.Item label="Public key" name="license_public_key">
                    <Input.TextArea rows={2} autoComplete="off" />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" loading={licenseSubmitting}>
                    激活 License
                  </Button>
                </Form>
              ) : null}
            </Space>
          ) : null}
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
