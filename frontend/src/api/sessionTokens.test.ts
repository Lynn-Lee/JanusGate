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

  it('creates a k8s session with modal pod and optional container', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
      if (url === '/api/v1/sessions/connection-token') {
        expect(body).toEqual({
          jit_grant_id: 'grant-k8s',
          asset_id: 'asset-k8s',
          account_id: 'deploy',
          protocol: 'k8s',
          action: 'session.connect'
        });
        expect(body).not.toHaveProperty('namespace');
        expect(body).not.toHaveProperty('pod');
        expect(body).not.toHaveProperty('container');
        return Response.json({
          connection_token: 'jgt-k8s-token',
          expires_at: '2026-07-01T12:25:00+00:00',
          jit_grant_id: 'grant-k8s',
          workflow_request_id: 'wr-k8s',
          asset_id: 'asset-k8s',
          account_id: 'deploy',
          protocol: 'k8s',
          action: 'session.connect'
        }, { status: 201 });
      }
      if (url === '/api/v1/sessions/') {
        expect(body).toEqual({
          jit_grant_id: 'grant-k8s',
          asset_id: 'asset-k8s',
          account_id: 'deploy',
          protocol: 'k8s',
          connection_token: 'jgt-k8s-token',
          pod: 'web-0',
          container: 'app'
        });
        expect(body).not.toHaveProperty('namespace');
        return Response.json({
          id: 'session-k8s',
          asset_id: 'asset-k8s',
          account_id: 'deploy',
          connector_id: 'connector-1',
          protocol: 'k8s',
          status: 'active',
          connection_url: 'connector-runtime://cs-k8s',
          workflow_request_id: 'wr-k8s',
          jit_grant_id: 'grant-k8s',
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
      id: 'grant-k8s',
      asset_id: 'asset-k8s',
      account_id: 'deploy',
      protocol: 'k8s',
      action: 'session.connect'
    }, { pod: 'web-0', container: 'app' });

    expect(session.id).toBe('session-k8s');
    expect(session.protocol).toBe('k8s');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('omits empty container so the default container is used', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
      if (url === '/api/v1/sessions/connection-token') {
        return Response.json({
          connection_token: 'jgt-k8s-token',
          expires_at: '2026-07-01T12:25:00+00:00',
          jit_grant_id: 'grant-k8s',
          workflow_request_id: 'wr-k8s',
          asset_id: 'asset-k8s',
          account_id: 'deploy',
          protocol: 'k8s',
          action: 'session.connect'
        }, { status: 201 });
      }
      if (url === '/api/v1/sessions/') {
        expect(body).toEqual({
          jit_grant_id: 'grant-k8s',
          asset_id: 'asset-k8s',
          account_id: 'deploy',
          protocol: 'k8s',
          connection_token: 'jgt-k8s-token',
          pod: 'web-0'
        });
        expect(body).not.toHaveProperty('container');
        expect(body).not.toHaveProperty('namespace');
        return Response.json({
          id: 'session-k8s',
          asset_id: 'asset-k8s',
          account_id: 'deploy',
          connector_id: 'connector-1',
          protocol: 'k8s',
          status: 'active',
          connection_url: 'connector-runtime://cs-k8s',
          workflow_request_id: 'wr-k8s',
          jit_grant_id: 'grant-k8s',
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
    await createSessionWithConnectionToken(api, {
      id: 'grant-k8s',
      asset_id: 'asset-k8s',
      account_id: 'deploy',
      protocol: 'k8s',
      action: 'session.connect'
    }, { pod: 'web-0' });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
