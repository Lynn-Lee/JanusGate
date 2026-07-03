import { Button, Card, Space, Table, Tag, Typography } from 'antd';
import { StopOutlined } from '@ant-design/icons';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { getErrorMessage, useApiData, useApiMessage } from './pageUtils';
import type {
  ListResponse,
  SshCertificate,
  SshCertificateAuthority,
  SshCertificateAuthorityTrustBundle
} from './types';

function statusTag(status: string) {
  const color = status === 'active' || status === 'issued' ? 'green' : status === 'revoked' ? 'red' : 'blue';
  return <Tag color={color}>{status}</Tag>;
}

export function SshCaPage() {
  const { api } = useAuth();
  const toast = useApiMessage();
  const authorities = useApiData(
    () => api.get<ListResponse<SshCertificateAuthority>>('/api/v1/ssh-certificate-authorities/'),
    []
  );
  const trustBundle = useApiData(
    () => api.get<SshCertificateAuthorityTrustBundle>('/api/v1/ssh-certificate-authorities/trust-bundle'),
    []
  );
  const certificates = useApiData(
    () => api.get<ListResponse<SshCertificate>>('/api/v1/ssh-certificates/'),
    []
  );

  const revokeCertificate = async (certificate: SshCertificate) => {
    try {
      await api.post<SshCertificate>(`/api/v1/ssh-certificates/${certificate.id}/revoke`, {
        reason: 'console revoked'
      });
      toast.success('已撤销临时证书');
      certificates.reload();
    } catch (error) {
      toast.error(getErrorMessage(error));
    }
  };

  const loading = authorities.loading || trustBundle.loading || certificates.loading;

  return (
    <section className="jg-page">
      <div className="jg-page-header">
        <div>
          <Typography.Title level={2}>SSH CA 与临时证书</Typography.Title>
          <Typography.Text type="secondary">
            查看当前租户 SSH CA、公钥信任分发与已签发临时证书；控制台不展示 CA 私钥或 Vault 私钥引用。
          </Typography.Text>
        </div>
        <Space>
          <Tag color="blue">{authorities.data?.total ?? 0} CAs</Tag>
          <Tag color="cyan">{certificates.data?.total ?? 0} Certificates</Tag>
        </Space>
      </div>

      {loading ? <LoadingState /> : null}
      {authorities.error ? <ErrorState message={authorities.error} onRetry={authorities.reload} /> : null}
      {trustBundle.error ? <ErrorState message={trustBundle.error} onRetry={trustBundle.reload} /> : null}
      {certificates.error ? <ErrorState message={certificates.error} onRetry={certificates.reload} /> : null}

      <div className="jg-card-grid">
        <Card title="Certificate authorities">
          <Table
            rowKey="id"
            dataSource={authorities.data?.items ?? []}
            pagination={false}
            size="small"
            columns={[
              { title: '名称', dataIndex: 'name' },
              { title: '状态', dataIndex: 'status', render: statusTag },
              { title: '有效期', dataIndex: 'validity_seconds', render: (value: number) => `${value}s` },
              { title: '公钥', dataIndex: 'public_key', ellipsis: true }
            ]}
          />
        </Card>

        <Card title="Trust bundle">
          <Table
            rowKey="ca_id"
            dataSource={trustBundle.data?.items ?? []}
            pagination={false}
            size="small"
            columns={[
              { title: 'CA', dataIndex: 'ca_id', render: (value: number) => `CA #${value}` },
              {
                title: '受信资产',
                dataIndex: 'trusted_asset_ids',
                render: (ids: number[]) => `${ids.length} trusted assets`
              },
              { title: '公钥', dataIndex: 'public_key', ellipsis: true }
            ]}
          />
        </Card>

        <Card title="Temporary certificates">
          <Table
            rowKey="id"
            dataSource={certificates.data?.items ?? []}
            pagination={false}
            size="small"
            columns={[
              { title: 'Serial', dataIndex: 'serial' },
              { title: 'Principal', dataIndex: 'principal' },
              { title: '账号', dataIndex: 'account_id' },
              { title: '状态', dataIndex: 'status', render: statusTag },
              { title: '签发人', dataIndex: 'requested_by' },
              { title: '有效期至', dataIndex: 'valid_before' },
              {
                title: '操作',
                render: (_: unknown, record: SshCertificate) => (
                  <Button
                    icon={<StopOutlined />}
                    aria-label={`撤销证书 ${record.serial}`}
                    disabled={record.status !== 'issued'}
                    onClick={() => void revokeCertificate(record)}
                  >
                    撤销
                  </Button>
                )
              }
            ]}
          />
        </Card>
      </div>
    </section>
  );
}
