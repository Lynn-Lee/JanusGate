# Phase 3 API 契约与错误码规范

> 面向 #t36 前端控制台、#t38 E2E smoke 和 #t41 QA 门禁。本文件记录当前后端稳定契约，后续新增 API 默认沿用。

## 基础约定

- API 前缀：业务接口统一在 `/api/v1/*` 下暴露；健康检查保留 `/health`。
- 认证：默认使用 Bearer access token；未认证或 token 失效返回 `401`。
- 租户/权限：接口必须通过 `current_user` / `require_permission` 或业务层 actor-aware 查询控制租户与资源边界。
- 时间字段：响应中业务时间优先使用 ISO 8601 字符串或 OpenAPI `date-time` schema，不返回本地化展示文案。
- 分页/列表：现阶段列表响应优先返回 `items + total`；资产等历史接口仍保持数组响应，前端需按 OpenAPI 读取。

## 统一错误响应

所有由 FastAPI `HTTPException`、业务 `AppError`、`ValueError` 与请求体验证触发的错误，统一返回 `ErrorResponse`：

```json
{
  "code": "WORKFLOW_REQUEST_NOT_FOUND",
  "message": "WORKFLOW_REQUEST_NOT_FOUND",
  "detail": "WORKFLOW_REQUEST_NOT_FOUND",
  "request_id": ""
}
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `code` | 前端和 QA 可稳定断言的错误码。若 `detail` 是大写业务码（如 `SELF_APPROVAL_NOT_ALLOWED`），直接提升为 `code`；否则按 HTTP 状态映射。 |
| `message` | 可展示或可记录的错误摘要。中文业务错误会原样保留。 |
| `detail` | 兼容 FastAPI 旧契约的详情字段；已有前端可继续读取。422 校验错误为字段错误数组。 |
| `request_id` | 请求追踪 ID；当前未接入网关时为空字符串，若上游传 `X-Request-ID` 会回显。 |

默认状态码映射：

| HTTP | 默认 `code` |
| --- | --- |
| 400 | `BAD_REQUEST` |
| 401 | `UNAUTHORIZED` |
| 403 | `FORBIDDEN` |
| 404 | `NOT_FOUND` |
| 422 | `VALIDATION_ERROR` |

## OpenAPI 约定

- `/openapi.json` 必须包含 `components.schemas.ErrorResponse`。
- 常见 `400/401/403/404/422` 响应在 OpenAPI 中统一引用 `ErrorResponse`，便于前端 client 生成或手写类型。
- 新增路由必须声明 `response_model`；若返回列表，优先使用 `{items,total}` 包装，避免前端无法区分页码和全集合。

## Phase 3 核心 API 分组

- Auth：`/api/v1/auth/*`，登录、2FA、refresh、当前用户、密码/API key。
- Assets：`/api/v1/assets/*`，资产、平台、受控连接测试。
- Sessions：`/api/v1/sessions/*`，会话创建/关闭，JIT grant 绑定。
- Workflow/JIT：`/api/v1/workflows/*`，申请、提交、审批、拒绝、撤销、active grant。
- Audit/SIEM：`/api/v1/audits/events`，审计事件创建和检索。

## 前端解析建议

1. 先读 `code` 做流程判断；业务码优先于 HTTP 状态。
2. 展示文案优先 `message`，必要时展开 `detail`。
3. 422 表单错误按 `detail[].loc` 映射字段。
4. 不要依赖未声明字段；新增字段必须先进入 OpenAPI 和本文件。
