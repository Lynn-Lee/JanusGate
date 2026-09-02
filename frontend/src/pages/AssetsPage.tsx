import { useMemo, useState } from 'react';
import {
  Button,
  Card,
  DatePicker,
  Empty,
  Form,
  Input,
  Modal,
  Segmented,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tree,
  Typography
} from 'antd';
import type { DataNode } from 'antd/es/tree';
import { Link } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { ErrorState, LoadingState } from '../components/StatusView';
import { UserSelect } from '../components/UserSelect';
import { getErrorMessage, useApiData, useApiMessage } from './pageUtils';
import type {
  Asset,
  AssetGrant,
  AssetNode,
  ConnectImpact,
  Platform,
  TreeAsset
} from './types';

type Surface = 'manage' | 'connect';

function tokenPermissions(token: string | null): string[] {
  if (!token) {
    return [];
  }
  const payload = token.split('.')[1];
  if (!payload) {
    return [];
  }
  try {
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
    const json = JSON.parse(atob(normalized)) as { permissions?: unknown };
    return Array.isArray(json.permissions) ? json.permissions.map(String) : [];
  } catch {
    return [];
  }
}

function canManageAssets(isSuperuser: boolean, token: string | null): boolean {
  const permissions = tokenPermissions(token);
  return isSuperuser || permissions.includes('admin');
}

const HIDDEN_CONNECT_PROTOCOLS = new Set(['k8s', 'kubernetes']);

export function connectProtocolsForPlatform(platform?: Platform | null): string[] {
  let raw = ['ssh'];
  if (platform?.protocols) {
    try {
      const parsed = JSON.parse(platform.protocols) as unknown;
      if (Array.isArray(parsed) && parsed.length) {
        raw = parsed.map(String);
      }
    } catch {
      raw = ['ssh'];
    }
  }
  return raw.filter((protocol) => !HIDDEN_CONNECT_PROTOCOLS.has(protocol.toLowerCase()));
}

function impactCopy(impact: ConnectImpact): string {
  if (!impact.lost.length) {
    return '没有人会因此失去连接。';
  }
  return impact.lost
    .map((row) => `${row.subject_id} 将无法连接 ${row.asset_name}`)
    .join('\n');
}

export function AssetsPage() {
  const { user, token } = useAuth();
  const canManage = canManageAssets(Boolean(user?.is_superuser), token);
  const [surface, setSurface] = useState<Surface>('manage');
  const showManage = canManage && surface === 'manage';

  return (
    <section className="jg-page">
      <div className="jg-page-header">
        <div>
          <Typography.Title level={2}>资产列表</Typography.Title>
          <Typography.Text type="secondary">
            {showManage ? '把资产挂到树上，给人开通 connect。' : '选择当前可连接的资产。'}
          </Typography.Text>
        </div>
        <Space>
          {canManage ? (
            <Segmented
              value={surface}
              onChange={(value) => setSurface(value as Surface)}
              options={[
                { label: '管理', value: 'manage' },
                { label: '连接', value: 'connect' }
              ]}
            />
          ) : null}
          {!showManage ? (
            <Link to="/workflow">
              <Button type="primary">发起 JIT 申请</Button>
            </Link>
          ) : null}
        </Space>
      </div>
      {showManage ? <AssetManagePanel /> : <AssetConnectPanel />}
    </section>
  );
}

function AssetConnectPanel() {
  const { api } = useAuth();
  const assets = useApiData(() => api.get<Asset[]>('/api/v1/assets/'), []);
  const platforms = useApiData(() => api.get<Platform[]>('/api/v1/assets/platforms'), []);
  const platformMap = new Map((platforms.data ?? []).map((item) => [item.id, item]));
  const rows = (assets.data ?? []).filter((asset) => {
    const protocols = connectProtocolsForPlatform(platformMap.get(asset.platform_id));
    return protocols.length > 0;
  });

  return (
    <Card>
      {assets.loading ? <LoadingState /> : null}
      {assets.error ? <ErrorState message={assets.error} onRetry={assets.reload} /> : null}
      {!assets.loading && !assets.error && rows.length === 0 ? (
        <Empty description="没有可连接的资产。" />
      ) : null}
      {!assets.loading && !assets.error && rows.length > 0 ? (
        <Table
          rowKey="id"
          dataSource={rows}
          pagination={{ pageSize: 10 }}
          columns={[
            { title: '资产名称', dataIndex: 'name' },
            { title: '地址', dataIndex: 'address' },
            {
              title: '平台',
              dataIndex: 'platform_id',
              render: (id: number) => platformMap.get(id)?.name ?? `平台 #${id}`
            },
            { title: '端口', dataIndex: 'port' },
            {
              title: '状态',
              dataIndex: 'is_active',
              render: (active: boolean) => (
                <Tag color={active ? 'green' : 'red'}>{active ? '可访问' : '停用'}</Tag>
              )
            },
            {
              title: '操作',
              render: (_: unknown, record: Asset) => {
                const protocols = connectProtocolsForPlatform(platformMap.get(record.platform_id));
                return (
                  <Space>
                    {protocols.map((protocol) => (
                      <Link
                        key={protocol}
                        to="/workflow"
                        state={{
                          assetId: String(record.id),
                          accountId: record.username || 'default',
                          protocol
                        }}
                      >
                        <Button type="link">{protocols.length === 1 ? '连接' : `连接 ${protocol}`}</Button>
                      </Link>
                    ))}
                  </Space>
                );
              }
            }
          ]}
        />
      ) : null}
    </Card>
  );
}

function AssetManagePanel() {
  const { api } = useAuth();
  const nodesQuery = useApiData(() => api.get<{ items: AssetNode[] }>('/api/v1/asset-nodes/'), []);
  const [selectedId, setSelectedId] = useState<string>('');
  const [rightTab, setRightTab] = useState('assets');
  const [focusAssetId, setFocusAssetId] = useState<number | null>(null);
  const nodes = useMemo(() => nodesQuery.data?.items ?? [], [nodesQuery.data]);
  const selected = nodes.find((node) => node.id === selectedId) ?? nodes.find((node) => node.is_root);

  const treeData = useMemo(() => buildTree(nodes), [nodes]);

  const reloadAll = () => {
    nodesQuery.reload();
  };

  return (
    <div className="jg-asset-layout">
      <Card className="jg-asset-tree" title="树">
        {nodesQuery.loading ? <LoadingState /> : null}
        {nodesQuery.error ? <ErrorState message={nodesQuery.error} onRetry={nodesQuery.reload} /> : null}
        {!nodesQuery.loading && nodes.length === 0 ? (
          <Empty
            description="还没有节点"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          >
            <CreateNodeButton parentId={null} onDone={reloadAll} />
          </Empty>
        ) : null}
        {treeData.length ? (
          <Tree
            blockNode
            selectedKeys={selected ? [selected.id] : []}
            treeData={treeData}
            defaultExpandAll
            onSelect={(keys) => {
              const id = String(keys[0] || '');
              setSelectedId(id);
              setFocusAssetId(null);
              setRightTab('assets');
            }}
          />
        ) : null}
      </Card>
      <Card>
        {selected?.is_root ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Typography.Paragraph>根节点只用于组织树，不能挂资产或授权。</Typography.Paragraph>
            <CreateNodeButton parentId={selected.id} onDone={reloadAll} label="创建节点" />
          </Space>
        ) : selected ? (
          <NodeWorkspace
            node={selected}
            nodes={nodes}
            rightTab={rightTab}
            focusAssetId={focusAssetId}
            onTab={setRightTab}
            onFocusAsset={setFocusAssetId}
            onTreeChanged={reloadAll}
          />
        ) : (
          <Empty description="还没有节点" />
        )}
      </Card>
    </div>
  );
}

function NodeWorkspace({
  node,
  nodes,
  rightTab,
  focusAssetId,
  onTab,
  onFocusAsset,
  onTreeChanged
}: {
  node: AssetNode;
  nodes: AssetNode[];
  rightTab: string;
  focusAssetId: number | null;
  onTab: (tab: string) => void;
  onFocusAsset: (id: number | null) => void;
  onTreeChanged: () => void;
}) {
  const { api } = useAuth();
  const notify = useApiMessage();
  const assets = useApiData(
    () => api.get<{ items: TreeAsset[] }>(`/api/v1/asset-nodes/${node.id}/assets`),
    [node.id]
  );
  const grantsPath =
    focusAssetId != null
      ? `/api/v1/asset-permissions/by-asset/${focusAssetId}`
      : `/api/v1/asset-nodes/${node.id}/permissions`;
  const grants = useApiData(() => api.get<{ items: AssetGrant[] }>(grantsPath), [grantsPath]);

  const rename = () => {
    let name = node.name;
    Modal.confirm({
      title: '重命名',
      content: <Input defaultValue={node.name} onChange={(event) => { name = event.target.value; }} />,
      onOk: async () => {
        await api.patch(`/api/v1/asset-nodes/${node.id}`, { name });
        onTreeChanged();
      }
    });
  };

  const moveTo = () => {
    const options = nodes
      .filter((item) => item.id !== node.id && !item.ancestor_ids.includes(node.id))
      .map((item) => ({ value: item.id, label: item.name }));
    let parentId = options[0]?.value as string | undefined;
    Modal.confirm({
      title: '移动到…',
      content: (
        <Select
          style={{ width: '100%' }}
          defaultValue={parentId}
          options={options}
          onChange={(value) => {
            parentId = value;
          }}
        />
      ),
      onOk: async () => {
        if (!parentId) {
          return;
        }
        const impact = await api.get<ConnectImpact>(
          `/api/v1/asset-nodes/${node.id}/move-impact?parent_id=${encodeURIComponent(parentId)}`
        );
        await new Promise<void>((resolve, reject) => {
          Modal.confirm({
            title: '确认移动',
            content: <pre style={{ whiteSpace: 'pre-wrap' }}>{impactCopy(impact)}</pre>,
            onOk: async () => {
              await api.post(`/api/v1/asset-nodes/${node.id}/move`, { parent_id: parentId });
              onTreeChanged();
              resolve();
            },
            onCancel: () => reject(new Error('cancelled'))
          });
        }).catch(() => undefined);
      }
    });
  };

  const removeNode = async () => {
    try {
      await api.delete(`/api/v1/asset-nodes/${node.id}`);
      onTreeChanged();
    } catch (error) {
      notify.error(getErrorMessage(error));
    }
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space wrap>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {node.name}
        </Typography.Title>
        <CreateNodeButton parentId={node.id} onDone={onTreeChanged} label="创建子节点" />
        <Button onClick={rename}>重命名</Button>
        <Button onClick={moveTo}>移动到…</Button>
        <Button danger onClick={() => void removeNode()}>
          删除
        </Button>
      </Space>
      <Tabs
        activeKey={rightTab}
        onChange={(key) => {
          if (key === 'assets') {
            onFocusAsset(null);
          }
          onTab(key);
        }}
        items={[
          {
            key: 'assets',
            label: '直属资产',
            children: (
              <DirectAssetsTab
                node={node}
                items={assets.data?.items ?? []}
                loading={assets.loading}
                error={assets.error}
                onReload={() => {
                  assets.reload();
                  onTreeChanged();
                }}
                onWhoCanConnect={(assetId) => {
                  onFocusAsset(assetId);
                  onTab('grants');
                }}
              />
            )
          },
          {
            key: 'grants',
            label: '谁能连',
            children: (
              <WhoCanConnectTab
                node={node}
                assetId={focusAssetId}
                items={grants.data?.items ?? []}
                loading={grants.loading}
                error={grants.error}
                onReload={grants.reload}
              />
            )
          }
        ]}
      />
    </Space>
  );
}

function DirectAssetsTab({
  node,
  items,
  loading,
  error,
  onReload,
  onWhoCanConnect
}: {
  node: AssetNode;
  items: TreeAsset[];
  loading: boolean;
  error: string;
  onReload: () => void;
  onWhoCanConnect: (assetId: number) => void;
}) {
  const { api } = useAuth();

  const hang = async () => {
    const picker = await api.get<{ items: TreeAsset[] }>('/api/v1/asset-nodes/ungrouped-assets');
    const options = picker.items
      .filter((item) => item.node_id !== node.id)
      .map((item) => ({ value: item.id, label: `${item.name}（${item.location_label}）` }));
    let assetId = options[0]?.value as number | undefined;
    Modal.confirm({
      title: '挂已有资产',
      content: (
        <Select
          style={{ width: '100%' }}
          defaultValue={assetId}
          options={options}
          onChange={(value) => {
            assetId = value;
          }}
        />
      ),
      onOk: async () => {
        if (assetId == null) {
          return;
        }
        const impact = await api.get<ConnectImpact>(
          `/api/v1/asset-nodes/${node.id}/hang-impact?asset_id=${assetId}`
        );
        await new Promise<void>((resolve, reject) => {
          Modal.confirm({
            title: '确认挂载',
            content: <pre style={{ whiteSpace: 'pre-wrap' }}>{impactCopy(impact)}</pre>,
            onOk: async () => {
              await api.post(`/api/v1/asset-nodes/${node.id}/assets`, { asset_id: assetId });
              onReload();
              resolve();
            },
            onCancel: () => reject(new Error('cancelled'))
          });
        }).catch(() => undefined);
      }
    });
  };

  const ungroup = async (assetId: number) => {
    const impact = await api.get<ConnectImpact>(
      `/api/v1/asset-nodes/ungroup-impact?asset_id=${assetId}`
    );
    Modal.confirm({
      title: '移出',
      content: <pre style={{ whiteSpace: 'pre-wrap' }}>{impactCopy(impact)}</pre>,
      onOk: async () => {
        await api.post('/api/v1/asset-nodes/ungroup', { asset_id: assetId });
        onReload();
      }
    });
  };

  if (loading) {
    return <LoadingState />;
  }
  if (error) {
    return <ErrorState message={error} onRetry={onReload} />;
  }
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Button onClick={() => void hang()}>挂已有资产</Button>
      {items.length === 0 ? (
        <Empty description="这个节点下还没有资产" />
      ) : (
        <Table
          rowKey="id"
          dataSource={items}
          pagination={false}
          columns={[
            { title: '资产', dataIndex: 'name' },
            { title: '地址', dataIndex: 'address' },
            {
              title: '操作',
              render: (_: unknown, record: TreeAsset) => (
                <Space>
                  <Button type="link" onClick={() => onWhoCanConnect(record.id)}>
                    谁能连
                  </Button>
                  <Button type="link" onClick={() => void ungroup(record.id)}>
                    移出
                  </Button>
                </Space>
              )
            }
          ]}
        />
      )}
    </Space>
  );
}

function WhoCanConnectTab({
  node,
  assetId,
  items,
  loading,
  error,
  onReload
}: {
  node: AssetNode;
  assetId: number | null;
  items: AssetGrant[];
  loading: boolean;
  error: string;
  onReload: () => void;
}) {
  const { api } = useAuth();
  const [open, setOpen] = useState(false);

  const remove = async (grant: AssetGrant) => {
    if (grant.inherited) {
      return;
    }
    await api.delete(`/api/v1/asset-permissions/${grant.id}`);
    onReload();
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Button type="primary" onClick={() => setOpen(true)}>
        添加
      </Button>
      {loading ? <LoadingState /> : null}
      {error ? <ErrorState message={error} onRetry={onReload} /> : null}
      {!loading && !error && items.length === 0 ? <Empty description="还没有人能连" /> : null}
      {!loading && !error && items.length > 0 ? (
        <Table
          rowKey="id"
          dataSource={items}
          pagination={false}
          columns={[
            {
              title: '主体',
              render: (_: unknown, record: AssetGrant) =>
                `${record.subject_type === 'user_group' ? '用户组' : '用户'}: ${record.subject_id}`
            },
            {
              title: '来源',
              render: (_: unknown, record: AssetGrant) =>
                record.inherited && record.inherited_from_node_name
                  ? `来自节点 ${record.inherited_from_node_name}`
                  : '直接授权'
            },
            {
              title: '到期',
              render: (_: unknown, record: AssetGrant) =>
                record.expired ? <Tag>已过期</Tag> : record.expires_at || '长期'
            },
            {
              title: '操作',
              render: (_: unknown, record: AssetGrant) =>
                record.inherited ? null : (
                  <Button type="link" danger onClick={() => void remove(record)}>
                    删除
                  </Button>
                )
            }
          ]}
        />
      ) : null}
      <AddGrantModal
        open={open}
        nodeId={node.id}
        assetId={assetId}
        onClose={() => setOpen(false)}
        onDone={() => {
          setOpen(false);
          onReload();
        }}
      />
    </Space>
  );
}

function AddGrantModal({
  open,
  nodeId,
  assetId,
  onClose,
  onDone
}: {
  open: boolean;
  nodeId: string;
  assetId: number | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { api } = useAuth();
  const notify = useApiMessage();
  const [form] = Form.useForm();

  return (
    <Modal
      title="谁能连接"
      open={open}
      onCancel={onClose}
      onOk={() => {
        form.submit();
      }}
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={async (values: {
          subject_id: string;
          subject_type: 'user' | 'user_group';
          expires_at?: { toISOString?: () => string };
          from_ticket?: string;
        }) => {
          const body = {
            subject_id: String(values.subject_id),
            subject_type: values.subject_type,
            expires_at: values.expires_at?.toISOString?.() ?? null,
            from_ticket: values.from_ticket?.trim() || null
          };
          try {
            if (assetId != null) {
              await api.post(`/api/v1/asset-permissions/by-asset/${assetId}`, body);
            } else {
              await api.post(`/api/v1/asset-nodes/${nodeId}/permissions`, body);
            }
            form.resetFields();
            onDone();
          } catch (error) {
            notify.error(getErrorMessage(error));
          }
        }}
      >
        <Form.Item name="subject_type" label="主体类型" initialValue="user">
          <Select options={[{ value: 'user', label: '用户' }, { value: 'user_group', label: '用户组' }]} />
        </Form.Item>
        <Form.Item noStyle shouldUpdate={(prev, next) => prev.subject_type !== next.subject_type}>
          {({ getFieldValue }) =>
            getFieldValue('subject_type') === 'user_group' ? (
              <Form.Item name="subject_id" label="主体 ID" rules={[{ required: true, message: '请输入主体 ID' }]}>
                <Input placeholder="用户组 ID" />
              </Form.Item>
            ) : (
              <Form.Item name="subject_id" label="用户" rules={[{ required: true, message: '请选择用户' }]}>
                <UserSelect />
              </Form.Item>
            )
          }
        </Form.Item>
        <Form.Item name="expires_at" label="到期">
          <DatePicker showTime placeholder="长期" style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="from_ticket" label="来源工单">
          <Input placeholder="可选，例如 ticket-64" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

function CreateNodeButton({
  parentId,
  onDone,
  label = '创建节点'
}: {
  parentId: string | null;
  onDone: () => void;
  label?: string;
}) {
  const { api } = useAuth();
  return (
    <Button
      type="primary"
      onClick={() => {
        let name = '';
        Modal.confirm({
          title: label,
          content: <Input placeholder="节点名称" onChange={(event) => { name = event.target.value; }} />,
          onOk: async () => {
            if (!name.trim()) {
              throw new Error('请填写名称');
            }
            await api.post('/api/v1/asset-nodes/', { name: name.trim(), parent_id: parentId });
            onDone();
          }
        });
      }}
    >
      {label}
    </Button>
  );
}

function buildTree(nodes: AssetNode[]): DataNode[] {
  const children = new Map<string | null, AssetNode[]>();
  for (const node of nodes) {
    const key = node.parent_id;
    const list = children.get(key) ?? [];
    list.push(node);
    children.set(key, list);
  }
  const walk = (parentId: string | null): DataNode[] =>
    (children.get(parentId) ?? []).map((node) => ({
      key: node.id,
      title: node.name,
      children: walk(node.id)
    }));
  const roots = nodes.filter((node) => node.is_root);
  if (roots.length) {
    return roots.map((root) => ({
      key: root.id,
      title: root.name,
      children: walk(root.id)
    }));
  }
  return walk(null);
}
