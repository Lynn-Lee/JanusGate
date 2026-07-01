import { Button, Card, Form, Input, Select, Space, Table, Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { useApiData } from './pageUtils';
import type { Asset, Platform } from './types';

export function AssetsPage() {
  const { api } = useAuth();
  const assets = useApiData(() => api.get<Asset[]>('/api/v1/assets/'), []);
  const platforms = useApiData(() => api.get<Platform[]>('/api/v1/assets/platforms'), []);
  const platformMap = new Map((platforms.data ?? []).map((item) => [item.id, item]));

  return (
    <section className="jg-page">
      <div className="jg-page-header">
        <div>
          <Typography.Title level={2}>资产列表</Typography.Title>
          <Typography.Text type="secondary">选择目标资产，发起 JIT 临时访问申请。</Typography.Text>
        </div>
        <Link to="/workflow"><Button type="primary">发起 JIT 申请</Button></Link>
      </div>
      <Card>
        <Form layout="inline" className="jg-block">
          <Form.Item label="搜索">
            <Input.Search placeholder="资产名称 / 地址" allowClear />
          </Form.Item>
          <Form.Item label="协议">
            <Select style={{ width: 160 }} placeholder="全部协议" allowClear options={[{ value: 'ssh', label: 'SSH' }, { value: 'rdp', label: 'RDP' }, { value: 'mysql', label: 'MySQL' }]} />
          </Form.Item>
        </Form>
        {assets.loading ? <LoadingState /> : null}
        {assets.error ? <ErrorState message={assets.error} onRetry={assets.reload} /> : null}
        {!assets.loading && !assets.error ? (
          <Table
            rowKey="id"
            dataSource={assets.data ?? []}
            pagination={{ pageSize: 10 }}
            columns={[
              { title: '资产名称', dataIndex: 'name' },
              { title: '地址', dataIndex: 'address' },
              { title: '平台', dataIndex: 'platform_id', render: (id: number) => platformMap.get(id)?.name ?? `平台 #${id}` },
              { title: '端口', dataIndex: 'port' },
              { title: '状态', dataIndex: 'is_active', render: (active: boolean) => <Tag color={active ? 'green' : 'red'}>{active ? '可访问' : '停用'}</Tag> },
              { title: '操作', render: (_: unknown, record: Asset) => <Space><Link to="/workflow" state={{ assetId: String(record.id), accountId: record.username || 'default', protocol: 'ssh' }}><Button type="link">发起 JIT 申请</Button></Link></Space> }
            ]}
          />
        ) : null}
      </Card>
    </section>
  );
}
