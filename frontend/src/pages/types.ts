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
  namespace?: string;
  has_server_ca?: boolean;
  connect_protocols?: string[];
  zone_id?: number | null;
};

export type Zone = {
  id: number;
  name: string;
  gateway_count: number;
  gateway_asset_ids: number[];
};

export type GatewayCandidate = {
  id: number;
  name: string;
  address: string;
  asset_type: string;
  is_active: boolean;
};

export type Platform = {
  id: number;
  name: string;
  category: string;
  protocols: string;
  is_active: boolean;
};

export type TicketStep = {
  id?: string;
  level: number;
  status: string;
  approver_user_ids: string[];
  decided_by_id?: string;
  decided_by_username?: string;
  decided_at?: string | null;
  decision_reason?: string;
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
  ticket_flow_id?: string;
  current_level?: number;
  total_levels?: number;
  steps?: TicketStep[];
};

export type TicketFlow = {
  id: string;
  name: string;
  flow_type: string;
  enabled: boolean;
  level_count: number;
  levels: Array<{ level: number; approver_user_ids: string[] }>;
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

export type FileTransferLog = {
  id: number;
  tenant_id: string;
  recording_id: number;
  session_id: string;
  asset_id: string;
  account_id: string;
  remote_path: string;
  direction: string;
  size_bytes: number;
  sha256: string;
  status: string;
  error_code: string;
  audit_event_id: string;
  occurred_at: string | null;
};

export type OperateLog = {
  id: number;
  tenant_id: string;
  actor_id: string;
  actor_username: string;
  resource_type: string;
  resource_id: string;
  action: string;
  summary: string;
  audit_event_id: string;
  occurred_at: string | null;
};

export type PasswordChangeLog = {
  id: number;
  tenant_id: string;
  user_id: string;
  username: string;
  method: string;
  audit_event_id: string;
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
  schema_version: string;
  export_format: string;
  content_type: string;
  download_filename: string;
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
  report_signature_algorithm: string;
  report_signature_key_id: string;
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

export type AccountTemplate = {
  id: number;
  name: string;
  protocol: string;
  default_username: string;
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
  use_token_request?: boolean;
  token_ttl_seconds?: number;
  template_id?: number | null;
  verify_status?: string;
  last_verify_message_id?: string | null;
};

export type AutomationJobRun = {
  message_id: string;
  job_type: string;
  status: string;
  requested_by: string;
  playbook_name: string | null;
  check_mode: boolean | null;
  target_count: number | null;
  error_code: string | null;
  reason?: string | null;
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

export type AssetNode = {
  id: string;
  tenant_id: string;
  parent_id: string | null;
  name: string;
  is_root: boolean;
  ancestor_ids: string[];
};

export type TreeAsset = {
  id: number;
  name: string;
  address: string;
  node_id: string | null;
  location_label: string;
  zone_id?: number | null;
};

export type AssetGrant = {
  id: string;
  tenant_id: string;
  subject_id: string;
  subject_type: 'user' | 'user_group';
  resource_type: string;
  resource_id: string;
  account_id: string;
  protocol: string;
  action: string;
  expires_at: string | null;
  from_ticket: string | null;
  expired: boolean;
  inherited: boolean;
  inherited_from_node_id: string | null;
  inherited_from_node_name: string | null;
};

export type ConnectImpact = {
  lost: Array<{ subject_id: string; asset_id: string; asset_name: string }>;
};
