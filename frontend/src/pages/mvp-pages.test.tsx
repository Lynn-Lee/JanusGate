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
const auditReportSummary = {
  tenant_id: 'tenant-a',
  total: 12,
  high_or_critical_total: 3,
  by_severity: { high: 2, critical: 1, medium: 4, low: 5 },
  by_category: { workflow: 5, session: 4, connector: 3 },
  by_siem_delivery_status: { delivered: 9, failed: 3 }
};
const auditComplianceReport = {
  schema_version: 'janusgate.audit-compliance.v1',
  export_format: 'json',
  content_type: 'application/vnd.janusgate.audit-compliance+json;version=1',
  download_filename: 'janusgate-soc2-access-tenant-a-20260705T154000Z.json',
  tenant_id: 'tenant-a',
  template: 'soc2-access',
  total: 2,
  event_ids: ['audit-1', 'audit-2'],
  hash_chain_start: 'sha256-start',
  hash_chain_end: 'sha256-end',
  period_start: '2026-07-05T15:30:00Z',
  period_end: '2026-07-05T15:35:00Z',
  generated_at: '2026-07-05T15:40:00Z',
  report_signature: 'hmac-sha256-signed',
  report_signature_algorithm: 'hmac-sha256',
  report_signature_key_id: 'local-secret-key',
  worm_storage_status: 'recorded',
  worm_record_id: 'worm-report-1',
  worm_sequence_number: 1,
  worm_content_hash: 'sha256-report-payload'
};
const licenseSummary = {
  configured_edition: 'enterprise',
  effective_edition: 'community',
  license_status: 'invalid',
  enabled_features: ['audit_reports', 'core_pam', 'workflow_jit'],
  disabled_features: ['admin_console', 'edition_feature_flags', 'license_management'],
  expires_at: null
};
const session = { id: 'session-1', asset_id: '1', account_id: 'root', connector_id: 'connector-1', protocol: 'ssh', status: 'active', connection_url: 'ssh://10.0.0.10', workflow_request_id: 'req-1', jit_grant_id: 'grant-1', created_at: '2026-07-01T00:03:00Z', updated_at: '2026-07-01T00:03:00Z', closed_at: null, audit_event_ids: [] };
const sessionCommand = {
  id: 11,
  tenant_id: 'tenant-a',
  recording_id: 1,
  session_id: 'session-1',
  sequence: 1,
  command: 'sudo systemctl restart nginx',
  exit_code: 0,
  output_excerpt: 'password=[REDACTED]',
  occurred_at: '2026-07-04T10:10:00Z'
};
const organization = { id: 'org-a', tenant_id: 'tenant-a', name: 'Tenant A Ops', status: 'active' };
const team = { id: 'team-a', tenant_id: 'tenant-a', organization_id: 'org-a', name: 'Ops Team' };
const project = { id: 'project-a', tenant_id: 'tenant-a', organization_id: 'org-a', team_id: 'team-a', name: 'Production Project', status: 'active' };
const account = {
  id: 1,
  tenant_id: 'tenant-a',
  asset_id: 1,
  username: 'deploy',
  protocol: 'ssh',
  secret_id: 'sec_tenant_a_deploy',
  organization_id: 'org-a',
  team_id: 'team-a',
  project_id: 'project-a',
  status: 'active',
  rotation_policy: 'manual'
};
const rotation = {
  id: 7,
  tenant_id: 'tenant-a',
  account_id: 1,
  status: 'scheduled',
  reason: 'quarterly rotation',
  requested_by: 'admin',
  scheduled_at: '2026-07-04T10:00:00Z'
};
const sshCa = {
  id: 3,
  tenant_id: 'tenant-a',
  name: 'Tenant A SSH CA',
  public_key: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITenantACa',
  status: 'active',
  validity_seconds: 1800
};
const sshTrustBundle = {
  items: [
    {
      ca_id: 3,
      name: 'Tenant A SSH CA',
      public_key: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITenantACa',
      trusted_asset_ids: [1]
    }
  ],
  total: 1
};
const sshCertificate = {
  id: 5,
  tenant_id: 'tenant-a',
  ca_id: 3,
  asset_id: 1,
  account_id: 1,
  principal: 'deploy',
  public_key: 'ssh-ed25519 AAAAC3NzaClient',
  serial: 'serial-5',
  certificate_body: 'ssh-ed25519-cert-v01@openssh.com AAAAissued',
  requested_by: 'admin',
  valid_after: '2026-07-04T10:00:00Z',
  valid_before: '2026-07-04T10:30:00Z',
  status: 'issued',
  revoked_at: null,
  revoke_reason: null
};

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
    if (url.endsWith('/api/v1/session-recordings/1/commands') && method === 'GET') return Response.json({ items: [sessionCommand], total: 1 });
    if (url.endsWith('/api/v1/audits/events')) return Response.json({ items: [audit], total: 1, limit: 50, offset: 0 });
    if (url.endsWith('/api/v1/audits/reports/summary')) return Response.json(auditReportSummary);
    if (url.endsWith('/api/v1/audits/reports/compliance?template=soc2-access')) return Response.json(auditComplianceReport);
    if (url.endsWith('/api/v1/admin/license-summary')) return Response.json(licenseSummary);
    if (url.endsWith('/api/v1/admin/license-config') && method === 'POST') {
      return Response.json({
        ...licenseSummary,
        effective_edition: 'enterprise',
        license_status: 'active',
        enabled_features: ['admin_console', 'audit_reports', 'core_pam', 'license_management', 'workflow_jit'],
        disabled_features: []
      });
    }
    if (url.endsWith('/api/v1/tenancy/organizations')) return Response.json({ items: [organization], total: 1 });
    if (url.endsWith('/api/v1/tenancy/teams')) return Response.json({ items: [team], total: 1 });
    if (url.endsWith('/api/v1/tenancy/projects')) return Response.json({ items: [project], total: 1 });
    if (url.endsWith('/api/v1/accounts/') && method === 'GET') return Response.json({ items: [account], total: 1 });
    if (url.endsWith('/api/v1/accounts/1/rotations') && method === 'GET') {
      return Response.json({ items: [rotation], total: 1 });
    }
    if (url.endsWith('/api/v1/accounts/1/rotations') && method === 'POST') {
      return Response.json({ ...rotation, id: 8, reason: 'console requested rotation' }, { status: 201 });
    }
    if (url.endsWith('/api/v1/ssh-certificate-authorities/') && method === 'GET') {
      return Response.json({ items: [sshCa], total: 1 });
    }
    if (url.endsWith('/api/v1/ssh-certificate-authorities/trust-bundle')) {
      return Response.json(sshTrustBundle);
    }
    if (url.endsWith('/api/v1/ssh-certificates/') && method === 'GET') {
      return Response.json({ items: [sshCertificate], total: 1 });
    }
    if (url.endsWith('/api/v1/ssh-certificates/5/revoke') && method === 'POST') {
      return Response.json({ ...sshCertificate, status: 'revoked', revoke_reason: 'console revoked' });
    }
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

  it('loads a session recording command timeline for playback review', async () => {
    const fetchMock = installFetch();
    vi.stubGlobal('fetch', fetchMock);
    history.pushState(null, '', '/sessions');
    render(<App />);

    expect(await screen.findByRole('heading', { name: '会话列表' })).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText('Recording ID'));
    await userEvent.type(screen.getByLabelText('Recording ID'), '1');
    await userEvent.click(screen.getByRole('button', { name: '加载回放时间线' }));

    expect(await screen.findByText('sudo systemctl restart nginx')).toBeInTheDocument();
    expect(screen.getByText('password=[REDACTED]')).toBeInTheDocument();
    expect(screen.queryByText('raw-secret')).not.toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/session-recordings/1/commands',
        expect.any(Object)
      )
    );
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

  it('shows audit report summary without exposing raw audit details', async () => {
    const fetchMock = installFetch();
    vi.stubGlobal('fetch', fetchMock);
    history.pushState(null, '', '/audits');
    render(<App />);

    expect(await screen.findByRole('heading', { name: '审计日志' })).toBeInTheDocument();
    expect(await screen.findByText('报表总事件')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('高危事件')).toBeInTheDocument();
    expect(screen.getByText('SIEM failed')).toBeInTheDocument();
    expect(screen.getAllByText('3')).toHaveLength(2);
    expect(screen.queryByText('secret-token')).not.toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/audits/reports/summary',
        expect.any(Object)
      )
    );
  });

  it('downloads a signed compliance report without exposing raw audit details', async () => {
    const fetchMock = installFetch();
    vi.stubGlobal('fetch', fetchMock);
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:compliance-report') });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    const clickMock = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    history.pushState(null, '', '/audits');
    render(<App />);

    expect(await screen.findByRole('heading', { name: '审计日志' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '下载 SOC2 报表' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/audits/reports/compliance?template=soc2-access',
        expect.any(Object)
      )
    );
    expect(await screen.findByText('hmac-sha256-signed')).toBeInTheDocument();
    expect(clickMock).toHaveBeenCalled();
    const clickedAnchor = clickMock.mock.contexts[0] as HTMLAnchorElement;
    expect(clickedAnchor.download).toBe(auditComplianceReport.download_filename);
    expect((URL.createObjectURL as unknown as ReturnType<typeof vi.fn>).mock.calls[0]?.[0].type).toBe(
      auditComplianceReport.content_type
    );
    expect(screen.queryByText('secret-token')).not.toBeInTheDocument();
  });

  it('shows Settings runtime and security summaries', async () => {
    const fetchMock = installFetch();
    vi.stubGlobal('fetch', fetchMock);
    history.pushState(null, '', '/settings');
    render(<App />);

    expect(await screen.findByRole('heading', { name: '系统设置' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('运行时状态')).toBeInTheDocument());
    expect(screen.getByText('JWT Bearer 访问令牌')).toBeInTheDocument();
    expect(screen.getByText('PostgreSQL / SQLAlchemy async')).toBeInTheDocument();
    expect(await screen.findByText('License / Edition 边界')).toBeInTheDocument();
    expect(screen.getByText('configured: enterprise')).toBeInTheDocument();
    expect(screen.getByText('effective: community')).toBeInTheDocument();
    expect(screen.getByText('invalid')).toBeInTheDocument();
    expect(screen.getByText('audit_reports')).toBeInTheDocument();
    expect(screen.getByText('license_management')).toBeInTheDocument();
    expect(screen.queryByText('JANUSGATE_LICENSE_KEY')).not.toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/v1/admin/license-summary', expect.any(Object))
    );
  });

  it('activates a persisted license config without echoing secrets', async () => {
    const fetchMock = installFetch();
    vi.stubGlobal('fetch', fetchMock);
    history.pushState(null, '', '/settings');
    render(<App />);

    expect(await screen.findByRole('heading', { name: '系统设置' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '保存 License 配置' }));
    await userEvent.type(screen.getByLabelText('License key'), 'signed.enterprise.license');
    await userEvent.type(screen.getByLabelText('Signing secret'), 'super-secret-signing-key');
    await userEvent.click(screen.getByRole('button', { name: '激活 License' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/admin/license-config',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            configured_edition: 'enterprise',
            license_verifier: 'hmac',
            license_key: 'signed.enterprise.license',
            license_signing_secret: 'super-secret-signing-key',
            license_public_key: ''
          })
        })
      )
    );
    expect(await screen.findByText('active')).toBeInTheDocument();
    expect(screen.getByText('effective: enterprise')).toBeInTheDocument();
    expect(screen.queryByText('signed.enterprise.license')).not.toBeInTheDocument();
    expect(screen.queryByText('super-secret-signing-key')).not.toBeInTheDocument();
  });

  it('shows Phase 4 tenancy organization, team, and project inventory', async () => {
    history.pushState(null, '', '/tenancy');
    render(<App />);

    expect(await screen.findByRole('heading', { name: '多租户组织结构' })).toBeInTheDocument();
    expect(screen.getByText('Tenant A Ops')).toBeInTheDocument();
    expect(screen.getByText('Ops Team')).toBeInTheDocument();
    expect(screen.getByText('Production Project')).toBeInTheDocument();
  });

  it('shows account custody and schedules credential rotation without exposing secrets', async () => {
    const fetchMock = installFetch();
    vi.stubGlobal('fetch', fetchMock);
    history.pushState(null, '', '/accounts');
    render(<App />);

    expect(await screen.findByRole('heading', { name: '账号托管与凭据轮换' })).toBeInTheDocument();
    expect(screen.getByText('deploy')).toBeInTheDocument();
    expect(screen.getByText('sec_tenant_a_deploy')).toBeInTheDocument();
    expect(await screen.findByText('quarterly rotation')).toBeInTheDocument();
    expect(screen.queryByText('plaintext-password')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '调度轮换' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/accounts/1/rotations',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ reason: 'console requested rotation' })
        })
      )
    );
  });

  it('shows SSH CA authorities, trust bundle, and issued certificates without private key references', async () => {
    const fetchMock = installFetch();
    vi.stubGlobal('fetch', fetchMock);
    history.pushState(null, '', '/ssh-ca');
    render(<App />);

    expect(await screen.findByRole('heading', { name: 'SSH CA 与临时证书' })).toBeInTheDocument();
    expect(screen.getByText('Tenant A SSH CA')).toBeInTheDocument();
    expect(screen.getByText('serial-5')).toBeInTheDocument();
    expect(screen.getByText('1 trusted assets')).toBeInTheDocument();
    expect(screen.queryByText('private_key_secret_id')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '撤销证书 serial-5' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/ssh-certificates/5/revoke',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ reason: 'console revoked' })
        })
      )
    );
  });
});
