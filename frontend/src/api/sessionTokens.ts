import type { ApiClient } from './client';
import type { JitGrant, SessionRecord } from '../pages/types';

export type SessionConnectionTokenRequest = {
  jit_grant_id: string;
  asset_id: string;
  account_id: string;
  protocol: string;
  action: string;
};

export type SessionConnectionTokenResponse = SessionConnectionTokenRequest & {
  connection_token: string;
  expires_at: string;
  workflow_request_id: string;
};

export type ConnectionTokenGrantInput = Pick<JitGrant, 'id' | 'asset_id' | 'account_id' | 'protocol' | 'action'>;

export async function issueConnectionToken(
  api: ApiClient,
  grant: ConnectionTokenGrantInput
): Promise<SessionConnectionTokenResponse> {
  return api.post<SessionConnectionTokenResponse>('/api/v1/sessions/connection-token', {
    jit_grant_id: grant.id,
    asset_id: grant.asset_id,
    account_id: grant.account_id,
    protocol: grant.protocol,
    action: grant.action || 'session.connect'
  });
}

export async function createSessionWithConnectionToken(
  api: ApiClient,
  grant: ConnectionTokenGrantInput
): Promise<SessionRecord> {
  const issue = await issueConnectionToken(api, grant);
  return api.post<SessionRecord>('/api/v1/sessions/', {
    asset_id: grant.asset_id,
    account_id: grant.account_id,
    protocol: grant.protocol,
    connection_token: issue.connection_token,
    jit_grant_id: grant.id
  });
}
