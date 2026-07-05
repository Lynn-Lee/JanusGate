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

export type SessionCommandEvent = {
  id: number;
  tenant_id: string;
  recording_id: number;
  session_id: string;
  sequence: number;
  command: string;
  exit_code: number | null;
  output_excerpt: string;
  occurred_at: string | null;
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

export type AuditReportSummary = {
  tenant_id: string;
  total: number;
  high_or_critical_total: number;
  by_severity: Record<string, number>;
  by_category: Record<string, number>;
  by_siem_delivery_status: Record<string, number>;
};

export type AuditComplianceReport = {
  tenant_id: string;
  template: string;
  total: number;
  event_ids: string[];
  hash_chain_start: string;
  hash_chain_end: string;
  period_start: string | null;
  period_end: string | null;
  generated_at: string;
  report_signature: string;
  worm_storage_status: string;
  worm_record_id: string;
  worm_sequence_number: number;
  worm_content_hash: string;
};

export type Organization = {
  id: string;
  tenant_id: string;
  name: string;
  status: string;
};

export type Team = {
  id: string;
  tenant_id: string;
  organization_id: string;
  name: string;
};

export type Project = {
  id: string;
  tenant_id: string;
  organization_id: string;
  team_id: string | null;
  name: string;
  status: string;
};

export type Account = {
  id: number;
  tenant_id: string;
  asset_id: number;
  username: string;
  protocol: string;
  secret_id: string;
  organization_id: string | null;
  team_id: string | null;
  project_id: string | null;
  status: string;
  rotation_policy: string;
};

export type CredentialRotation = {
  id: number;
  tenant_id: string;
  account_id: number;
  status: string;
  reason: string;
  requested_by: string;
  scheduled_at: string | null;
};

export type SshCertificateAuthority = {
  id: number;
  tenant_id: string;
  name: string;
  public_key: string;
  status: string;
  validity_seconds: number;
};

export type SshCertificateAuthorityTrustBundleItem = {
  ca_id: number;
  name: string;
  public_key: string;
  trusted_asset_ids: number[];
};

export type SshCertificateAuthorityTrustBundle = {
  items: SshCertificateAuthorityTrustBundleItem[];
  total: number;
};

export type SshCertificate = {
  id: number;
  tenant_id: string;
  ca_id: number;
  asset_id: number;
  account_id: number;
  principal: string;
  public_key: string;
  serial: string;
  certificate_body: string;
  requested_by: string;
  valid_after: string;
  valid_before: string;
  status: string;
  revoked_at: string | null;
  revoke_reason: string | null;
};
