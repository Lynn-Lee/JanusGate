import {
  ApartmentOutlined,
  AuditOutlined,
  ClusterOutlined,
  DesktopOutlined,
  SafetyCertificateOutlined,
  SettingOutlined
} from '@ant-design/icons';
import { Layout, Menu, Space, Tag, Typography, Button } from 'antd';
import type { MenuProps } from 'antd';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';

const { Header, Sider, Content } = Layout;

const navItems: Required<MenuProps>['items'] = [
  { key: '/assets', icon: <ClusterOutlined />, label: <Link to="/assets">资产</Link> },
  { key: '/sessions', icon: <DesktopOutlined />, label: <Link to="/sessions">会话</Link> },
  { key: '/workflow', icon: <SafetyCertificateOutlined />, label: <Link to="/workflow">Workflow/JIT</Link> },
  { key: '/tenancy', icon: <ApartmentOutlined />, label: <Link to="/tenancy">多租户</Link> },
  { key: '/audits', icon: <AuditOutlined />, label: <Link to="/audits">审计日志</Link> },
  { key: '/settings', icon: <SettingOutlined />, label: <Link to="/settings">系统设置</Link> }
];

export function AppShell() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const selected = navItems.find((item) => pathname.startsWith(String(item?.key)))?.key ?? '/assets';

  return (
    <Layout className="jg-shell">
      <Sider width={236} theme="dark">
        <div className="jg-logo">JanusGate</div>
        <Menu theme="dark" mode="inline" selectedKeys={[String(selected)]} items={navItems} />
      </Sider>
      <Layout>
        <Header className="jg-header">
          <Space direction="vertical" size={0}>
            <Typography.Text strong>JanusGate 控制台</Typography.Text>
            <Typography.Text type="secondary">策略驱动的 PAM / 零信任访问网关</Typography.Text>
          </Space>
          <Space>
            <Tag color="blue">Phase 3 MVP</Tag>
            <Typography.Text>{user?.display_name || user?.username || '已登录用户'}</Typography.Text>
            <Button
              onClick={() => {
                logout();
                navigate('/login');
              }}
            >
              退出
            </Button>
          </Space>
        </Header>
        <Content className="jg-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
