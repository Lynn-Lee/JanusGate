export type Asset = {
  id: number;
  name: string;
  address: string;
  platform_id: number;
  port: number;
  username: string;
  is_active: boolean;
  description: string;
  created_at: string;
};

export type Platform = {
  id: number;
  name: string;
  category: string;
  protocols: string;
  is_active: boolean;
};

export type WorkflowRequest = {
  id: string;
  tenant_id: string;
  requester_id: string;
  requester_username: string;
  asset_id: string;
  account_id: string;
  protocol: string;
  action: string;
  reason: string;
  requested_ttl_seconds: number;
  status: string;
  created_at: string;
  submitted_at: string | null;
  decided_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  decision_reason: string;
  approver_id: string;
  approver_username: string;
  grant_id: string;
  metadata: Record<string, unknown>;
};

export type JitGrant = {
  id: string;
  tenant_id: string;
  workflow_request_id: string;
  subject_id: string;
  asset_id: string;
  account_id: string;
  protocol: string;
  action: string;
  status: string;
  issued_at: string;
  expires_at: string;
  revoked_at: string | null;
  max_session_ttl_seconds: number;
  constraints: Record<string, unknown>;
};

export type ListResponse<T> = { items: T[]; total: number };

export type SessionRecord = {
  id: string;
  asset_id: string;
  account_id: string;
  connector_id: string;
  protocol: string;
  status: string;
  connection_url: string;
  workflow_request_id: string;
  jit_grant_id: string;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  audit_event_ids: string[];
};

export type AuditEvent = {
  id: string;
  tenant_id: string;
  actor_id: string;
  actor_username: string;
  event_type: string;
  category: string;
  action: string;
  resource_type: string;
  resource_id: string;
  session_id: string | null;
  severity: string;
  message: string | null;
  metadata: Record<string, unknown>;
  sequence_number: number;
  created_at: string;
};

export type AuditListResponse = { items: AuditEvent[]; total: number; limit: number; offset: number };
