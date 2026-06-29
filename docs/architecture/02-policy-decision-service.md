# PolicyDecisionService 设计

## 目标

PolicyDecisionService 是 JanusGate 的统一授权决策点，所有会话创建、连接器 token 签发、Vault 解包、JIT 审批消费都必须通过该服务。默认策略是 **deny-by-default**。

## 请求契约

字段：
- `subject`：访问主体，包含 `id`、`type`、`tenant_id`、`roles`。
- `action`：动作，例如 `asset.connect`、`secret.unwrap`。
- `resource`：目标资源，包含 `id`、`type`、`tenant_id`、`labels`。
- `context`：连接器、来源 IP、设备、会话等上下文。
- `risk_signals`：风险信号。
- `mfa_verified`：是否完成 MFA。
- `approval`：JIT/Workflow 审批状态和过期时间。
- `connector_trusted`：连接器是否可信。

## 响应契约

字段：
- `decision`：`allow` 或 `deny`。
- `reason_code`：机器可读原因。
- `explain_trace`：可解释决策轨迹。
- `obligations`：调用方必须执行的约束，例如 session TTL。
- `ttl_seconds`：决策有效期。
- `audit_event_id`：审计事件关联 ID。

## 初版规则

1. 未知主体、未知资源、缺失 action、租户不一致、连接器不可信一律拒绝。
2. 没有显式匹配策略一律拒绝。
3. 需要 MFA 但未完成 MFA 一律拒绝。
4. 需要审批但审批缺失、过期、撤销或拒绝一律拒绝。
5. 只有显式匹配策略且所有约束满足时才允许。
