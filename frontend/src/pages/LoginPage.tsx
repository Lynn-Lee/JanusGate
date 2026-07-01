import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Form, Input, Typography } from 'antd';
import { useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';

export function LoginPage() {
  const { login, verifyTwoFa, cancelTwoFa, pendingTwoFa, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  if (isAuthenticated) {
    return <Navigate to="/assets" replace />;
  }

  return (
    <main className="jg-login-page">
      <Card className="jg-login-card">
        <Typography.Title level={2}>登录 JanusGate</Typography.Title>
        <Typography.Paragraph type="secondary">
          {pendingTwoFa ? '账号已完成密码校验，请输入认证器中的 6 位动态验证码。' : '进入 Phase 3 MVP 控制台，完成 JIT 申请、审批、会话和审计闭环。'}
        </Typography.Paragraph>
        {error ? <Alert showIcon type="error" message={error} className="jg-block" /> : null}
        {pendingTwoFa ? (
          <Form
            layout="vertical"
            onFinish={async (values: { totpCode: string }) => {
              setError('');
              setLoading(true);
              try {
                await verifyTwoFa(values.totpCode);
                navigate('/assets');
              } catch (err) {
                setError(err instanceof Error ? err.message : 'MFA 验证失败');
              } finally {
                setLoading(false);
              }
            }}
          >
            <Typography.Title level={3}>输入 MFA 验证码</Typography.Title>
            <Form.Item
              label="6 位 TOTP 验证码"
              name="totpCode"
              rules={[
                { required: true, message: '请输入 6 位 TOTP 验证码' },
                { pattern: /^\d{6}$/, message: '验证码必须为 6 位数字' }
              ]}
            >
              <Input prefix={<LockOutlined />} autoComplete="one-time-code" inputMode="numeric" maxLength={6} />
            </Form.Item>
            <div className="jg-login-actions">
              <Button
                block
                onClick={() => {
                  cancelTwoFa();
                  setError('');
                }}
                disabled={loading}
              >
                返回账号密码登录
              </Button>
              <Button block type="primary" htmlType="submit" size="large" loading={loading} aria-label="验证并登录">
                验证并登录
              </Button>
            </div>
          </Form>
        ) : (
          <Form
            layout="vertical"
            onFinish={async (values: { username: string; password: string }) => {
              setError('');
              setLoading(true);
              try {
                const result = await login(values.username, values.password);
                if (result.status === 'authenticated') {
                  navigate('/assets');
                }
              } catch (err) {
                setError(err instanceof Error ? err.message : '登录失败');
              } finally {
                setLoading(false);
              }
            }}
          >
            <Form.Item label="用户名 / 邮箱" name="username" rules={[{ required: true, message: '请输入用户名或邮箱' }]}>
              <Input prefix={<UserOutlined />} autoComplete="username" />
            </Form.Item>
            <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}>
              <Input.Password prefix={<LockOutlined />} autoComplete="current-password" />
            </Form.Item>
            <Button block type="primary" htmlType="submit" size="large" loading={loading} aria-label="登录">
              登录
            </Button>
          </Form>
        )}
      </Card>
    </main>
  );
}
