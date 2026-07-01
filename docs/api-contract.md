# JanusGate API Contract

更新时间：2026-07-01  
范围：Phase 3 MVP 前端/后端联调契约。

## Session connection token（#t42）

前端创建会话前必须先向后端换取真实 `connection_token`，不得在前端伪造 token。

### POST `/api/v1/sessions/connection-token`

用途：基于已批准且仍有效的 JIT grant 签发短期、一次性会话连接 token。该 token 随后用于 `POST /api/v1/sessions/` 的 `connection_token` 字段。

鉴权：需要登录态；后端使用当前用户 `id` 与 `tenant_id` 作为 token subject/tenant 绑定来源，不接受前端传入 subject/tenant。

请求体：

```json
{
  "jit_grant_id": "grant-1",
  "asset_id": "asset-1",
  "account_id": "root",
  "protocol": "ssh",
  "action": "session.connect"
}
```

响应 `201`：

```json
{
  "connection_token": "jgt_xxx",
  "expires_at": "2026-07-01T12:25:00+00:00",
  "jit_grant_id": "grant-1",
  "workflow_request_id": "wr-1",
  "asset_id": "asset-1",
  "account_id": "root",
  "protocol": "ssh",
  "action": "session.connect"
}
```

安全语义：

- token 最长 5 分钟，并且不会超过 JIT grant 的 `expires_at`。
- token 一次性消费；重复使用返回 `CONNECTION_TOKEN_NOT_FOUND`。
- token 绑定当前用户、租户、资产、账号、协议、动作、JIT grant 和 workflow request。
- JIT grant 必须是 `active`，且 subject/asset/account/protocol/action 全部匹配；过期、撤销、跨用户、跨资产或跨账号均 fail-closed。
- 后端只按 token 摘要索引内部存储；审计事件不记录明文 token。

错误码（当前通过 HTTP `detail` 返回）：

| HTTP | detail | 说明 |
| --- | --- | --- |
| 403 | `JIT_GRANT_NOT_FOUND` | grant 不存在或不属于当前租户 |
| 403 | `JIT_GRANT_NOT_ACTIVE:<status>` | grant 已 used/revoked/expired 等 |
| 403 | `JIT_GRANT_EXPIRED` | grant 已过期 |
| 403 | `JIT_GRANT_SUBJECT_MISMATCH` | 当前用户不是 grant subject |
| 403 | `JIT_GRANT_ASSET_MISMATCH` | 资产不匹配 |
| 403 | `JIT_GRANT_ACCOUNT_MISMATCH` | 账号不匹配 |
| 403 | `JIT_GRANT_PROTOCOL_MISMATCH` | 协议不匹配 |
| 403 | `JIT_GRANT_ACTION_MISMATCH` | 动作不匹配 |

### POST `/api/v1/sessions/`

前端必须把上一步响应中的 `connection_token` 原样传给会话创建接口，同时继续传同一个 `jit_grant_id`：

```json
{
  "asset_id": "asset-1",
  "account_id": "root",
  "protocol": "ssh",
  "jit_grant_id": "grant-1",
  "connection_token": "jgt_xxx"
}
```

后端会再次走 JIT grant reserve/consume、PolicyDecisionService 和 connection token consume/绑定校验；任一环节不匹配都拒绝创建会话。
