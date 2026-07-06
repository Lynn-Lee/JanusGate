import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, parseApiError } from './client';

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe('parseApiError', () => {
  it('prefers Phase 3 ErrorResponse message and keeps request id', async () => {
    const response = new Response(
      JSON.stringify({
        code: 'SELF_APPROVAL_NOT_ALLOWED',
        message: '不能审批自己的申请',
        detail: 'SELF_APPROVAL_NOT_ALLOWED',
        request_id: 'req-1'
      }),
      { status: 403, headers: { 'Content-Type': 'application/json' } }
    );

    await expect(parseApiError(response)).resolves.toMatchObject({
      code: 'SELF_APPROVAL_NOT_ALLOWED',
      message: '不能审批自己的申请',
      requestId: 'req-1',
      status: 403
    });
  });

  it('maps legacy FastAPI detail string into stable message', async () => {
    const response = new Response(JSON.stringify({ detail: '用户名或密码错误' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' }
    });

    await expect(parseApiError(response)).resolves.toMatchObject({
      code: 'UNAUTHORIZED',
      message: '用户名或密码错误',
      status: 401
    });
  });
});

describe('ApiClient docs screenshot fixture', () => {
  it('serves configured screenshot fixture responses without calling fetch', async () => {
    localStorage.setItem(
      'janusgate-doc-screenshot-fixture',
      JSON.stringify({
        evidence: [
          {
            id: 'admin-settings-license-summary',
            route: '/settings',
            api_responses: {
              '/api/v1/admin/license-summary': {
                configured_edition: 'enterprise',
                effective_edition: 'community',
                license_status: 'invalid'
              }
            }
          }
        ]
      })
    );
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(new ApiClient().get('/api/v1/admin/license-summary')).resolves.toEqual({
      configured_edition: 'enterprise',
      effective_edition: 'community',
      license_status: 'invalid'
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('serves shared screenshot fixture responses for common API calls', async () => {
    localStorage.setItem(
      'janusgate-doc-screenshot-fixture',
      JSON.stringify({
        api_responses: {
          '/api/v1/auth/me': {
            username: 'admin',
            display_name: 'Docs Admin'
          }
        },
        evidence: []
      })
    );
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(new ApiClient().get('/api/v1/auth/me')).resolves.toEqual({
      username: 'admin',
      display_name: 'Docs Admin'
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
