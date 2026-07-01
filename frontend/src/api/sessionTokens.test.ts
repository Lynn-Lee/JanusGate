import { describe, expect, it, vi } from 'vitest';

import { ApiClient } from './client';
import { createSessionWithConnectionToken } from './sessionTokens';

describe('createSessionWithConnectionToken', () => {
  it('issues a real connection token before creating the session', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
      if (url === '/api/v1/sessions/connection-token') {
        expect(body).toEqual({
          jit_grant_id: 'grant-1',
          asset_id: 'asset-1',
          account_id: 'root',
          protocol: 'ssh',
          action: 'session.connect'
        });
        return Response.json({
          connection_token: 'jgt-real-token',
          expires_at: '2026-07-01T12:25:00+00:00',
          jit_grant_id: 'grant-1',
          workflow_request_id: 'wr-1',
          asset_id: 'asset-1',
          account_id: 'root',
          protocol: 'ssh',
          action: 'session.connect'
        }, { status: 201 });
      }
      if (url === '/api/v1/sessions/') {
        expect(body).toMatchObject({
          jit_grant_id: 'grant-1',
          asset_id: 'asset-1',
          account_id: 'root',
          protocol: 'ssh',
          connection_token: 'jgt-real-token'
        });
        return Response.json({
          id: 'session-1',
          asset_id: 'asset-1',
          account_id: 'root',
          connector_id: 'connector-1',
          protocol: 'ssh',
          status: 'active',
          connection_url: 'ssh://asset-1',
          workflow_request_id: 'wr-1',
          jit_grant_id: 'grant-1',
          created_at: '2026-07-01T12:25:00+00:00',
          updated_at: '2026-07-01T12:25:00+00:00',
          closed_at: null,
          audit_event_ids: []
        });
      }
      return Response.json({ detail: `Unexpected ${url}` }, { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    const api = new ApiClient();
    const session = await createSessionWithConnectionToken(api, {
      id: 'grant-1',
      asset_id: 'asset-1',
      account_id: 'root',
      protocol: 'ssh',
      action: 'session.connect'
    });

    expect(session.id).toBe('session-1');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
