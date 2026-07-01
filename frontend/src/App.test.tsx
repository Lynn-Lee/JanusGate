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
    expect(await screen.findByText('堡垒机资产')).toBeInTheDocument();
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
