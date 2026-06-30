# JanusGate Phase 2 Workflow / JIT PRD 与架构设计

> 状态：正式版，已由 @tc-codex-architect 与 @deepseek-architect 双人复核确认  
> 基线：`phase1-baseline` / `origin/dev`  
> 适用任务：#t8 Workflow / JIT 审批流模块


## 0. 双人确认记录

- @tc-codex-architect：完成 PRD 与架构草案，并按复核意见完成更新。
- @deepseek-architect：第一轮复核补充审批人类型、防自审批、grant revoke 关闭会话、SQLAlchemy 持久化和 grant 使用策略。
- @deepseek-architect：第二轮确认 5 点修改意见均已吸收，可转正式版。

## 1. 背景与目标

JanusGate Phase 1 已完成 PAM 基础底座：Identity/Auth、Inventory、PolicyDecisionService、Connector API v2、Credential Vault、Session Gateway、Audit/SIEM、DevOps 与测试门禁。

Phase 2 首个目标是实现 Workflow / JIT（Just-In-Time）审批流，让高风险/高权限访问从“静态长期授权”转为“按需申请、审批授权、限时使用、自动回收、全链路审计”。

## 2. 产品定位

### 2.1 核心定位

Workflow/JIT 是 JanusGate 的 **临时特权授权控制面**，负责回答：

> 某个用户在某个时间，因某个理由，是否可以临时获得对某个资产/账号/操作的高权限访问？如果可以，授权多久、受哪些限制、如何审计与回收？

### 2.2 用户价值

- 降低长期高权限暴露面。
- 让敏感访问具备审批、时效、可追溯、可撤销能力。
- 支持安全团队和运维团队在不降低效率的前提下实现最小权限。
- 为后续工单、合规报表、风险评分和自动化授权打基础。

## 3. Phase 2 范围边界

### 3.1 本阶段必须实现

1. **JIT 申请单**
   - 用户发起临时访问申请。
   - 申请目标包括资产、账号、协议、动作、原因、期望时长。
   - 支持申请状态查询。

2. **审批流状态机**
   - `draft` / `pending` / `approved` / `rejected` / `expired` / `revoked`。
   - 支持提交、审批通过、拒绝、撤销、过期。
   - 状态变更必须具备合法 transition 校验。

3. **临时授权 Grant**
   - 审批通过后生成 JIT Grant。
   - Grant 绑定 subject、asset、account、protocol/action、tenant、有效期。
   - Grant 可被 PolicyDecisionService 使用。
   - Grant 到期自动失效，可主动 revoke。

4. **PolicyDecisionService 接入**
   - 访问策略需要审批时，如果没有有效 grant，则 deny 并返回 `APPROVAL_REQUIRED`。
   - 有有效 grant 时，策略允许继续评估并输出约束。

5. **Session Gateway 接入**
   - 高风险/高权限 session 创建必须携带有效 JIT grant 或由 policy context 查到有效 grant。
   - grant 与 session 绑定，避免 grant 被跨资产/跨账号复用。

6. **Audit/SIEM 接入**
   - 申请、提交、审批、拒绝、撤销、过期、grant 使用都必须产生审计事件。
   - 审计 metadata 继续沿用 Phase 1 脱敏策略。

7. **最小 API 与测试**
   - 提供 JIT request / approval / grant API。
   - 覆盖状态机、越权审批、grant 到期、grant revoke、Policy/Session 接入的核心测试。

### 3.2 本阶段暂不实现

- 多级复杂 BPMN 流程设计器。
- 复杂审批组织树与任意审批链编排。
- 前端低代码审批表单。
- 外部 ITSM 双向同步。
- 风险评分自动审批。
- Slack/飞书/钉钉真实通知发送。
- 跨区域高可用审批队列。
- 持久化 WORM 审计存储。

这些进入 Phase 3 或专项任务。

## 4. 角色与权限模型

| 角色 | 权限 |
|---|---|
| requester | 创建申请、查看自己的申请、撤销自己的 pending 申请 |
| approver | 查看待审批申请、approve/reject 被授权范围内的申请 |
| manager_approver | 作为申请人直属上级审批其申请 |
| auditor | 查看申请、审批、grant 使用历史 |
| admin | 配置审批策略、审批人映射、默认 TTL、紧急 revoke |
| system | 过期扫描、grant 校验、审计投递 |

权限原则：

- requester 不能审批自己的申请；Phase 2 不开放自审批例外，break-glass 另做专项。
- approver 只能审批自己租户和授权范围内的资源。
- Phase 2 至少支持两类审批人：指定审批人和直属上级审批人。
- admin 操作必须审计。
- 所有跨租户访问默认拒绝。

## 5. 核心领域模型

### 5.1 WorkflowRequest

字段建议：

- `id`
- `tenant_id`
- `requester_id`
- `requester_username`
- `resource_type`
- `asset_id`
- `account_id`
- `protocol`
- `action`
- `reason`
- `requested_ttl_seconds`
- `status`
- `created_at`
- `submitted_at`
- `decided_at`
- `expires_at`
- `revoked_at`
- `decision_reason`
- `approver_id`
- `approver_username`
- `metadata`

### 5.2 JitGrant

字段建议：

- `id`
- `tenant_id`
- `workflow_request_id`
- `subject_id`
- `asset_id`
- `account_id`
- `protocol`
- `action`
- `status`: `active` / `used` / `expired` / `revoked`
- `issued_at`
- `expires_at`
- `revoked_at`
- `max_session_ttl_seconds`
- `constraints`

### 5.3 ApprovalPolicy

Phase 2 可先用内存/配置型策略，后续持久化：

- `id`
- `tenant_id`
- `resource_selector`
- `action_selector`
- `approver_subject_ids`
- `approver_mode`: `named_user` / `manager`
- `require_mfa_for_requester`
- `require_mfa_for_approver`
- `max_grant_ttl_seconds`
- `allow_self_approval`
- `risk_level`

## 6. 状态机设计

### 6.1 WorkflowRequest 状态迁移

```text
draft -> pending
pending -> approved
pending -> rejected
pending -> revoked
pending -> expired
approved -> expired
approved -> revoked
rejected -> terminal
expired -> terminal
revoked -> terminal
```

非法迁移必须 fail-closed。

### 6.2 JitGrant 状态迁移

```text
active -> used
active -> expired
active -> revoked
used -> expired
used -> revoked
expired -> terminal
revoked -> terminal
```

Phase 2 建议 grant 使用方式由策略 obligations / constraints 控制。默认建议：

- 高风险操作（生产环境、root/admin、高危命令、DDL/DROP 等）：single-use。
- 普通临时访问：TTL 内 limited-use，并配置最大使用次数。
- 策略配置优先于默认值，PolicyDecisionService 应在 obligations 中返回 grant 使用类型与限制。

## 7. API 草案

统一前缀：`/api/v1/workflows`

### 7.1 创建申请

`POST /api/v1/workflows/requests`

请求：

```json
{
  "asset_id": "asset-1",
  "account_id": "root",
  "protocol": "ssh",
  "action": "session.connect",
  "reason": "数据库故障排查",
  "requested_ttl_seconds": 1800,
  "metadata": {
    "ticket_id": "INC-1001"
  }
}
```

响应：

```json
{
  "id": "wr_...",
  "status": "draft",
  "requested_ttl_seconds": 1800
}
```

### 7.2 提交申请

`POST /api/v1/workflows/requests/{request_id}/submit`

- 校验 requester 权限。
- 校验资源、租户、TTL 上限。
- 状态 `draft -> pending`。
- 写审计事件 `workflow.request.submitted`。

### 7.3 审批通过

`POST /api/v1/workflows/requests/{request_id}/approve`

请求：

```json
{
  "decision_reason": "允许 30 分钟排障",
  "grant_ttl_seconds": 1800
}
```

效果：

- 校验 approver 权限。
- 校验不得自审批。
- 状态 `pending -> approved`。
- 创建 `JitGrant(active)`。
- 写审计事件 `workflow.request.approved` 与 `jit.grant.issued`。

### 7.4 审批拒绝

`POST /api/v1/workflows/requests/{request_id}/reject`

- 状态 `pending -> rejected`。
- 必须填写拒绝原因。
- 写审计事件 `workflow.request.rejected`。

### 7.5 撤销

`POST /api/v1/workflows/requests/{request_id}/revoke`

- requester 可撤销 pending 申请。
- approver/admin 可撤销 pending/approved 申请和 active grant。
- 撤销 active grant 时，必须通知 Session Gateway 终止与该 grant 绑定的活跃 session。
- 写审计事件 `workflow.request.revoked` / `jit.grant.revoked` / `session.revoked_by_jit_grant`。

### 7.6 查询

- `GET /api/v1/workflows/requests`
- `GET /api/v1/workflows/requests/{request_id}`
- `GET /api/v1/workflows/grants/active`

## 8. 与现有模块集成

### 8.1 PolicyDecisionService

新增/扩展 `ApprovalState`：

- `status`: `not_required` / `pending` / `approved` / `denied` / `revoked` / `expired`
- `grant_id`
- `workflow_request_id`
- `expires_at`
- `constraints`

决策逻辑：

1. 如果策略不需要审批，按现有逻辑。
2. 如果策略需要审批但没有 grant，返回：
   - `decision=deny`
   - `reason_code=APPROVAL_REQUIRED`
   - `obligations.workflow_required=true`
3. 如果 grant 存在但过期/撤销/不匹配资源，返回 deny。
4. 如果 grant 有效，继续校验 MFA、connector trust、tenant、resource 等约束。

### 8.2 Session Gateway

Session 创建扩展：

- 请求可携带 `jit_grant_id`。
- SessionGatewayService 在 policy context 中带上 `jit_grant_id`。
- policy allow 后，Session 记录 `workflow_request_id` / `jit_grant_id`。
- 若 grant single-use，Session 创建成功后将 grant 标记为 used。
- 若 grant 被 revoke，Session Gateway 必须关闭该 grant 绑定的 active session，并写审计事件。

### 8.3 Audit / SIEM

新增事件类型：

- `workflow.request.created`
- `workflow.request.submitted`
- `workflow.request.approved`
- `workflow.request.rejected`
- `workflow.request.revoked`
- `workflow.request.expired`
- `jit.grant.issued`
- `jit.grant.used`
- `jit.grant.expired`
- `jit.grant.revoked`

### 8.4 Vault

Phase 2 只做接口预留：

- secret unwrap 可要求有效 grant。
- grant constraints 可限制 unwrap 次数、目标 asset/account、TTL。

真实审批后 unwrap 策略可进入 Phase 3。

## 9. 安全边界

1. **默认拒绝**：无申请、无审批、grant 不匹配、grant 过期一律拒绝。
2. **租户隔离**：request、grant、policy、session 必须同 tenant。
3. **防自审批**：默认禁止 requester 审批自己的申请。
4. **TTL 上限**：审批人不能突破审批策略配置的最大 TTL。
5. **最小授权**：grant 必须绑定 subject、asset、account、protocol、action。
6. **防重放**：single-use grant 使用后不能再次创建 session。
7. **审计不可绕过**：所有状态变更与 grant 使用必须写 audit。
8. **敏感信息脱敏**：reason/metadata 不应写入 secret/token/cookie 明文。
9. **审批人权限**：approver 必须具备对应资源范围权限。
10. **并发一致性**：approve/revoke/use grant 需要原子状态迁移；Phase 2 内存实现要抽象出 repository 接口，后续数据库实现用事务/乐观锁。

## 10. 技术方案

### 10.1 后端包结构

建议新增：

```text
backend/app/api/workflows/
  __init__.py
  routes.py
  schemas.py
  service.py
```

Phase 2 最小实现先沿用 Phase 1 风格：

- 进程内 repository / store 完成行为闭环与测试。
- 路由、service、schema 边界保持稳定。
- 后续持久化时替换 repository，不改 API 契约。

### 10.2 持久化策略

Phase 2 直接引入 SQLAlchemy 持久化模型，而不是仅做内存闭环。原因：项目已经具备数据库基础设施，Workflow/JIT 的 request/grant/approval 具有审计和恢复要求，后续再迁移会增加状态兼容风险。

建议新增模型：

- `WorkflowRequestModel`：保存申请单与审批状态。
- `JitGrantModel`：保存临时授权与使用/撤销状态。
- `ApprovalPolicyModel`：保存审批策略和审批人模式。

Service 层仍保留 repository 接口，测试可使用内存 fake；生产路径使用 SQLAlchemy repository。

### 10.3 测试策略

必须覆盖：

- 状态机合法/非法迁移。
- requester 创建/提交申请。
- approver approve/reject。
- requester 不能自审批。
- grant 过期/撤销/资源不匹配时 policy deny。
- grant 有效时 policy allow。
- grant revoke 后 active session 被关闭。
- Session 创建绑定 grant。
- single-use grant 防重复使用。
- Audit/SIEM 事件写入。
- metadata 脱敏。

## 11. 任务拆分建议

### Task P2-1：Workflow/JIT PRD 与接口定稿

Owner：@tc-codex-architect + @deepseek-architect  
输出：正式版 PRD/架构文档，双方确认。

### Task P2-2：Workflow 持久化模型 + API + 状态机

Owner：后端开发  
范围：SQLAlchemy models/repository、`backend/app/api/workflows/`、状态机、申请/提交/审批/拒绝/撤销 API、测试。

### Task P2-3：JitGrant 与 Policy 接入

Owner：架构/后端  
范围：Grant 校验、PolicyDecisionService approval/grant 语义、测试。

### Task P2-4：Session Gateway 接入 JIT

Owner：Session owner  
范围：Session 创建携带/绑定 grant、single-use consume、grant revoke 关闭活跃 session、测试。

### Task P2-5：Audit/SIEM 事件扩展

Owner：Audit owner  
范围：workflow/jit 事件类型、metadata 脱敏回归、SIEM 投递测试。

### Task P2-6：QA / 安全 Review / DevOps

Owner：QA、Reviewer、DevOps  
范围：风险矩阵、回归测试、CI 门禁、部署配置变更。

## 12. 待双人确认的问题

1. Phase 2 是否只做单级审批？建议：是，多级审批后移，但审批人类型支持指定用户 + 直属上级。
2. Grant 默认 single-use 还是 TTL 内多次使用？建议：按策略配置，高风险默认 single-use，普通临时访问 TTL 内 limited-use。
3. 是否允许自审批？建议：默认禁止，break-glass 另做专项。
4. 是否接真实通知系统？建议：Phase 2 只做通知接口和审计事件，不接真实 IM。
5. Workflow/JIT 是否需要持久化数据库？建议：Phase 2 直接补 SQLAlchemy 持久化模型，同时保留 repository 接口便于测试。
6. Grant revoke 是否影响已建立会话？建议：必须同步关闭绑定 grant 的 active session。

## 13. 初步确认建议

@tc-codex-architect 与 @deepseek-architect 当前共识草案：Phase 2 做单级审批 + 两类审批人（指定用户/直属上级）+ JIT Grant + SQLAlchemy 持久化 + Policy/Session/Audit 接入闭环；不引入复杂 BPMN、真实通知、外部 ITSM 和风险评分自动审批。

本草案已吸收 @deepseek-architect 第一轮复核意见，待二次确认后转为确定版并拆任务。
