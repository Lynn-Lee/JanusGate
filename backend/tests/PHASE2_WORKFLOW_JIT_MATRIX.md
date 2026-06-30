# Phase 2 Workflow/JIT 测试矩阵与验收门禁

基线文档：`docs/architecture/06-phase2-workflow-jit-prd-architecture.md`
适用任务：#t30，配合 #t25-#t29 的实现和回归验证。
角色分工：QA 维护矩阵、门禁和放行结论；Tester 负责落地 pytest/API 契约/安全回归执行；模块 owner 负责实现缺陷修复与可测性补强。

## 发布门禁

| 门禁 | Go 条件 | No-Go 条件 |
|---|---|---|
| 静态检查 | `ruff check app tests` 通过，`mypy app` 通过 | 任一失败且无批准豁免 |
| 测试通过率 | `pytest -q` 全部通过 | 任何 P0/P1 用例失败 |
| 覆盖率 | 不低于 Phase 1 门禁：总覆盖率 >= 80%，新增 Workflow/JIT 核心模块建议 >= 85% | 总覆盖率低于 80%，或新增安全关键路径缺自动化覆盖 |
| API 契约 | Workflow request / approval / grant API 成功与失败路径稳定 | 状态码/响应结构不稳定或未覆盖真实路由 |
| 安全回归 | 自审批、越权审批、跨租户、grant 过期/撤销/复用均 fail-closed | 任一安全绕过可复现 |
| 集成闭环 | Policy、Session、Audit/SIEM 至少各有一条端到端或集成级验证 | grant 未被策略/会话/审计正确消费 |

## P0 阻断级测试矩阵

| 风险面 | 必测场景 | 建议验证方式 | Owner |
|---|---|---|---|
| 状态机 fail-closed | 合法迁移：draft->pending->approved/rejected；pending/approved->revoked/expired；非法迁移 rejected/expired/revoked 后继续变更 | Service 单元测试 + API 契约测试 | Tester + Workflow owner |
| requester 权限 | requester 只能创建、查看、提交、撤销自己的 pending 申请；不能查看/操作其他租户申请 | API 契约 + 权限回归 | Tester + Workflow owner |
| 审批权限 | 指定审批人与直属上级审批；非授权 approver 拒绝；跨租户审批拒绝 | API 契约 + 安全回归 | Tester + Workflow owner |
| 自审批阻断 | requester 与 approver 相同必须拒绝；break-glass 不在 Phase 2 放行范围 | API 契约 + Service 单元测试 | Tester + QA |
| TTL 上限 | requested/grant TTL 超过策略上限时被截断或拒绝；审批人不能突破 max TTL | Service 单元测试 + API 契约 | Tester + Workflow owner |
| Grant 绑定 | grant 必须绑定 subject、tenant、asset、account、protocol、action；任一不匹配 policy/session deny | Policy 集成测试 + Session API 测试 | Tester + Policy/Session owner |
| Grant 生命周期 | active->used/expired/revoked；expired/revoked grant 不可用；single-use grant 二次使用拒绝 | Service 单元测试 + 集成测试 | Tester + Policy/Session owner |
| Policy 接入 | 需要审批但无 grant 返回 deny + `APPROVAL_REQUIRED`；有效 grant 才继续约束评估 | Policy 单元/集成测试 | Tester + Policy owner |
| Session 接入 | session 创建携带/绑定 jit_grant_id；single-use consume；grant revoke 关闭 active session | Session API/Service 集成测试 | Tester + Session owner |
| Audit/SIEM | request created/submitted/approved/rejected/revoked/expired、grant issued/used/expired/revoked 全部写审计；metadata 脱敏 | Audit service 测试 + SIEM mock | Tester + Audit owner |
| 并发一致性 | approve/revoke/use grant 原子化；重复审批、并发 revoke/use 不产生双 grant 或重复 session | Repository/Service 单元测试 | Tester + Workflow owner |

## P1 高风险补充矩阵

| 风险面 | 必测场景 | 建议验证方式 |
|---|---|---|
| 查询 API | requester、approver、auditor、admin 的列表/详情范围正确 | API 契约测试 |
| 拒绝原因 | reject 必须填写原因，审计记录不泄露 secret/token/cookie | API + Audit 测试 |
| metadata 脱敏 | reason/metadata 中疑似 password/token/cookie/private key 被脱敏或拒绝 | Audit/SIEM 回归 |
| Manager approver | 直属上级缺失、跨租户 manager、manager 被禁用时 fail-closed | Service 单元测试 |
| Grant 使用策略 | 高风险默认 single-use，普通临时访问 limited-use，策略配置优先 | Policy/Grant 单元测试 |
| Vault 预留接口 | secret unwrap 可接收 grant constraints；Phase 2 不要求真实审批后 unwrap | 接口/契约测试或显式豁免 |
| 通知接口 | Phase 2 只要求接口/审计，不接真实 IM | 契约测试或显式非范围说明 |

## 最小验收用例清单

1. 创建申请成功：返回 `draft`，写 `workflow.request.created`。
2. 提交申请成功：`draft -> pending`，写 `workflow.request.submitted`。
3. 非 requester 提交/撤销他人申请被拒绝。
4. 非授权 approver 审批被拒绝。
5. requester 自审批被拒绝。
6. approve 成功：`pending -> approved`，创建 `JitGrant(active)`，写 request approved + grant issued 审计。
7. reject 成功：`pending -> rejected`，拒绝原因必填。
8. grant 过期后 Policy deny。
9. grant 被 revoke 后 Policy deny，并关闭绑定 session。
10. grant 资源/账号/协议/action/tenant 不匹配时 Session 创建拒绝。
11. single-use grant 第一次 session 成功后标记 used，第二次使用拒绝。
12. Workflow/JIT 事件 SIEM mock 投递成功/失败路径可观测。
13. 真实 API 路由契约覆盖 `/api/v1/workflows/requests` 与 `/api/v1/workflows/grants/active`。
14. 全量 `ruff check app tests`、`mypy app`、`pytest -q`、coverage gate 通过。

## 残余风险与豁免规则

- #t8 若 Phase 2 只实现单级审批，复杂多级 BPMN、真实通知、外部 ITSM、风险评分自动审批不作为本阶段阻断项。
- Vault unwrap 与真实审批联动仅做接口预留时，必须在 PR/任务中写明 Phase 3 跟进项。
- 任一 P0 场景未自动化覆盖时，必须在任务板记录豁免原因、临时缓解、owner 和补测截止时间；否则不得发布。
