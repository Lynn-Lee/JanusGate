import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';

const user = { id: 1, username: 'admin', display_name: '管理员', email: 'admin@example.com', is_superuser: true, totp_enabled: false };
const asset = { id: 1, name: '生产 SSH 主机', address: '10.0.0.10', platform_id: 1, port: 22, username: 'root', is_active: true, description: '核心主机', created_at: '2026-07-01T00:00:00Z' };
const platform = { id: 1, name: 'Linux', category: 'host', protocols: '["ssh"]', is_active: true };
const request = { id: 'req-1', tenant_id: 'default', requester_id: '1', requester_username: 'admin', asset_id: '1', account_id: 'root', protocol: 'ssh', action: 'session.connect', reason: '排障', requested_ttl_seconds: 1800, status: 'pending', created_at: '2026-07-01T00:00:00Z', submitted_at: null, decided_at: null, expires_at: null, revoked_at: null, decision_reason: '', approver_id: '', approver_username: '', grant_id: '', metadata: {} };
const grant = { id: 'grant-1', tenant_id: 'default', workflow_request_id: 'req-1', subject_id: '1', asset_id: '1', account_id: 'root', protocol: 'ssh', action: 'session.connect', status: 'active', issued_at: '2026-07-01T00:01:00Z', expires_at: '2026-07-01T00:31:00Z', revoked_at: null, max_session_ttl_seconds: 1800, constraints: {} };
const audit = { id: 'audit-1', tenant_id: 'default', actor_id: '1', actor_username: 'admin', event_type: 'workflow.request.approved', category: 'workflow', action: 'approve', resource_type: 'workflow_request', resource_id: 'req-1', session_id: null, severity: 'medium', message: '审批通过', metadata: { token: 'secret-token', safe: 'visible' }, sequence_number: 1, created_at: '2026-07-01T00:02:00Z' };
const session = { id: 'session-1', asset_id: '1', account_id: 'root', connector_id: 'connector-1', protocol: 'ssh', status: 'active', connection_url: 'ssh://10.0.0.10', workflow_request_id: 'req-1', jit_grant_id: 'grant-1', created_at: '2026-07-01T00:03:00Z', updated_at: '2026-07-01T00:03:00Z', closed_at: null, audit_event_ids: [] };
const organization = { id: 'org-a', tenant_id: 'tenant-a', name: 'Tenant A Ops', status: 'active' };
const team = { id: 'team-a', tenant_id: 'tenant-a', organization_id: 'org-a', name: 'Ops Team' };
const project = { id: 'project-a', tenant_id: 'tenant-a', organization_id: 'org-a', team_id: 'team-a', name: 'Production Project', status: 'active' };

function installFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (url.endsWith('/api/v1/auth/me')) return Response.json(user);
    if (url.endsWith('/api/v1/assets/') && method === 'GET') return Response.json([asset]);
    if (url.endsWith('/api/v1/assets/platforms')) return Response.json([platform]);
    if (url.endsWith('/api/v1/workflows/requests') && method === 'GET') return Response.json({ items: [request], total: 1 });
    if (url.endsWith('/api/v1/workflows/grants/active')) return Response.json({ items: [grant], total: 1 });
    if (url.endsWith('/api/v1/sessions/') && method === 'GET') return Response.json({ items: [session], total: 1 });
    if (url.endsWith('/api/v1/audits/events')) return Response.json({ items: [audit], total: 1, limit: 50, offset: 0 });
    if (url.endsWith('/api/v1/tenancy/organizations')) return Response.json({ items: [organization], total: 1 });
    if (url.endsWith('/api/v1/tenancy/teams')) return Response.json({ items: [team], total: 1 });
    if (url.endsWith('/api/v1/tenancy/projects')) return Response.json({ items: [project], total: 1 });
    if (url.endsWith('/health')) return Response.json({ status: 'ok', version: '0.1.0' });
    return Response.json(session);
  });
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('janusgate-access-token', 'token-1');
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', installFetch());
});

describe('MVP pages', () => {
  it('shows Assets JIT CTA linked to Workflow', async () => {
    history.pushState(null, '', '/assets');
    render(<App />);

    expect(await screen.findByRole('heading', { name: '资产列表' })).toBeInTheDocument();
    expect(screen.getByText('生产 SSH 主机')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: '发起 JIT 申请' })[0]).toHaveAttribute('href', '/workflow');
  });

  it('shows Workflow request and active grant panels and creates a server-backed session', async () => {
    history.pushState(null, '', '/workflow');
    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Workflow/JIT 申请审批' })).toBeInTheDocument();
    expect(screen.getByText('Active grants')).toBeInTheDocument();
    expect(screen.getByText('req-1')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '创建会话' }));
    expect(await screen.findByRole('heading', { name: '会话列表' })).toBeInTheDocument();
    expect(await screen.findByText('session-1')).toBeInTheDocument();
  });

  it('loads Sessions from the backend when local cache is empty', async () => {
    history.pushState(null, '', '/sessions');
    render(<App />);

    expect(await screen.findByRole('heading', { name: '会话列表' })).toBeInTheDocument();
    expect(await screen.findByText('session-1')).toBeInTheDocument();
    expect(screen.queryByText('当前尚无会话。请在 Workflow/JIT 页面使用 active grant 创建会话。')).not.toBeInTheDocument();
  });

  it('redacts sensitive audit metadata in detail drawer', async () => {
    history.pushState(null, '', '/audits');
    render(<App />);

    expect(await screen.findByRole('heading', { name: '审计日志' })).toBeInTheDocument();
    await userEvent.click(screen.getByText('查看脱敏 metadata'));
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText(/visible/)).toBeInTheDocument();
    expect(within(drawer).getByText(/\*\*\*\*\*\*/)).toBeInTheDocument();
    expect(screen.queryByText('secret-token')).not.toBeInTheDocument();
  });

  it('shows Settings runtime and security summaries', async () => {
    history.pushState(null, '', '/settings');
    render(<App />);

    expect(await screen.findByRole('heading', { name: '系统设置' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('运行时状态')).toBeInTheDocument());
    expect(screen.getByText('JWT Bearer 访问令牌')).toBeInTheDocument();
    expect(screen.getByText('PostgreSQL / SQLAlchemy async')).toBeInTheDocument();
  });

  it('shows Phase 4 tenancy organization, team, and project inventory', async () => {
    history.pushState(null, '', '/tenancy');
    render(<App />);

    expect(await screen.findByRole('heading', { name: '多租户组织结构' })).toBeInTheDocument();
    expect(screen.getByText('Tenant A Ops')).toBeInTheDocument();
    expect(screen.getByText('Ops Team')).toBeInTheDocument();
    expect(screen.getByText('Production Project')).toBeInTheDocument();
  });
});
