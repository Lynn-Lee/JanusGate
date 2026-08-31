import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (url.endsWith('/api/v1/auth/login') && method === 'POST') {
      return Response.json({ access_token: 'token-1', refresh_token: 'refresh-1', token_type: 'Bearer' });
    }
    if (url.endsWith('/api/v1/auth/me')) {
      return Response.json({ id: 1, username: 'admin', display_name: '管理员', email: 'admin@example.com', is_superuser: true, totp_enabled: false });
    }
    if (url.endsWith('/api/v1/assets/')) {
      return Response.json([{ id: 1, name: '堡垒机资产', address: '10.0.0.10', platform_id: 1, port: 22, username: 'root', is_active: true, description: '核心主机', created_at: '2026-07-01T00:00:00Z' }]);
    }
    if (url.endsWith('/api/v1/assets/platforms')) {
      return Response.json([{ id: 1, name: 'Linux', category: 'host', protocols: '["ssh"]', is_active: true }]);
    }
    if (url.endsWith('/api/v1/asset-nodes/')) {
      return Response.json({ items: [{ id: 'node-root', tenant_id: 'default', parent_id: null, name: '根', is_root: true, ancestor_ids: [] }] });
    }
    if (url.endsWith('/api/v1/workflows/requests')) {
      return Response.json({ items: [], total: 0 });
    }
    if (url.endsWith('/api/v1/workflows/grants/active')) {
      return Response.json({ items: [], total: 0 });
    }
    if (url.endsWith('/api/v1/audits/events')) {
      return Response.json({ items: [], total: 0, limit: 50, offset: 0 });
    }
    if (url.endsWith('/health')) {
      return Response.json({ status: 'ok', version: '0.1.0' });
    }
    return Response.json({ detail: `Unhandled ${method} ${url}` }, { status: 404 });
  });
}

beforeEach(() => {
  localStorage.clear();
  history.pushState(null, '', '/');
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', mockFetch());
});

describe('JanusGate console app', () => {
  it('redirects protected routes to login and signs in to Assets', async () => {
    history.pushState(null, '', '/assets');
    render(<App />);

    expect(await screen.findByRole('heading', { name: '登录 JanusGate' })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('用户名 / 邮箱'), 'admin');
    await userEvent.type(screen.getByLabelText('密码'), 'password');
    await userEvent.click(screen.getByRole('button', { name: '登录' }));

    expect(await screen.findByRole('heading', { name: '资产列表' })).toBeInTheDocument();
    await userEvent.click(await screen.findByText('连接'));
    expect(await screen.findByText('堡垒机资产')).toBeInTheDocument();
  });

  it('completes login through the real MFA challenge endpoint when 2FA is required', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.endsWith('/api/v1/auth/login') && method === 'POST') {
        return Response.json({
          access_token: '',
          refresh_token: '',
          token_type: 'Bearer',
          requires_2fa: true,
          two_fa_token: 'mfa-challenge-token'
        });
      }
      if (url.endsWith('/api/v1/auth/login/2fa') && method === 'POST') {
        expect(JSON.parse(String(init?.body))).toEqual({
          two_fa_token: 'mfa-challenge-token',
          totp_code: '123456'
        });
        return Response.json({ access_token: 'token-2fa', refresh_token: 'refresh-2fa', token_type: 'Bearer' });
      }
      if (url.endsWith('/api/v1/auth/me')) {
        return Response.json({ id: 1, username: 'admin', display_name: '管理员', email: 'admin@example.com', is_superuser: true, totp_enabled: true });
      }
      if (url.endsWith('/api/v1/assets/')) {
        return Response.json([{ id: 1, name: 'MFA 资产', address: '10.0.0.11', platform_id: 1, port: 22, username: 'root', is_active: true, description: '二步登录后可见', created_at: '2026-07-01T00:00:00Z' }]);
      }
      if (url.endsWith('/api/v1/assets/platforms')) {
        return Response.json([{ id: 1, name: 'Linux', category: 'host', protocols: '["ssh"]', is_active: true }]);
      }
      if (url.endsWith('/api/v1/asset-nodes/')) {
        return Response.json({ items: [{ id: 'node-root', tenant_id: 'default', parent_id: null, name: '根', is_root: true, ancestor_ids: [] }] });
      }
      return Response.json({ detail: `Unhandled ${method} ${url}` }, { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);
    history.pushState(null, '', '/login');

    render(<App />);

    await userEvent.type(screen.getByLabelText('用户名 / 邮箱'), 'admin');
    await userEvent.type(screen.getByLabelText('密码'), 'password');
    await userEvent.click(screen.getByRole('button', { name: '登录' }));

    expect(await screen.findByRole('heading', { name: '输入 MFA 验证码' })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('6 位 TOTP 验证码'), '123456');
    await userEvent.click(screen.getByRole('button', { name: '验证并登录' }));

    expect(await screen.findByRole('heading', { name: '资产列表' })).toBeInTheDocument();
    await userEvent.click(await screen.findByText('连接'));
    expect(await screen.findByText('MFA 资产')).toBeInTheDocument();
    expect(localStorage.getItem('janusgate-access-token')).toBe('token-2fa');
    expect(localStorage.getItem('janusgate-refresh-token')).toBe('refresh-2fa');
  });

  it('renders the five MVP console navigation entries after auth', async () => {
    localStorage.setItem('janusgate-access-token', 'token-1');
    history.pushState(null, '', '/assets');
    render(<App />);

    await waitFor(() => expect(screen.getByText('JanusGate 控制台')).toBeInTheDocument());
    expect(screen.getByRole('menuitem', { name: /资产/ })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /会话/ })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /Workflow/ })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /审计日志/ })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /系统设置/ })).toBeInTheDocument();
  });
});
