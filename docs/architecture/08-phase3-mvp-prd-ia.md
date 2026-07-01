# JanusGate Phase 3 MVP PRD / IA / Milestone Plan

更新时间：2026-07-01
负责人：`@tc-codex-architect`
复核：`@deepseek-architect`
关联任务：`#t34` / `#t35` / `#t36` / `#t38` / `#t40` / `#t41`
当前主线：`origin/dev=6c8962ae7`

## 1. 阶段目标

Phase 3 的目标不是继续扩充后端功能，而是把 Phase 1/2 已完成的能力产品化，形成一个可以演示、可以部署、可以端到端验收的 JanusGate MVP。

MVP 必须证明 JanusGate 的核心价值闭环：

> 管理员配置资产与权限边界，用户按需申请 JIT 访问，审批后创建受控会话，撤销后立即断连，审计链路可追踪。

## 2. MVP 范围决策

### 2.1 本阶段必须包含

MVP 固定为 6 个页面和 1 条端到端主链路。

#### 6 个页面

1. 登录页
2. 资产列表页
3. 会话列表页
4. Workflow/JIT 申请审批页
5. 审计日志页
6. 系统设置页

#### 1 条 E2E 主链路

登录 → 资产列表 → 发起 JIT 申请 → 审批 → 创建会话 → 撤销 → 查看审计

### 2.2 本阶段明确延后

以下能力不进入 Phase 3 MVP，避免范围失控：

- Dashboard 统计页
- 多租户组织管理 UI
- 通知中心
- 报表导出
- 会话录制与回放
- SSH CA / 临时证书
- 凭据轮换 UI
- WebHook 管理 UI
- 审批策略 DSL 编辑器
- License / Edition 管理

这些能力进入 Phase 4/5 候选池。

## 3. 用户角色与核心场景

### 3.1 角色

| 角色 | 目标 | MVP 权限边界 |
| --- | --- | --- |
| 普通用户 | 申请临时访问、查看自己的会话和申请 | 只能查看/操作自己的申请、grant 和会话 |
| 审批人 | 审批或拒绝 JIT 申请 | 不能自审批，只能审批可见范围内申请 |
| 管理员 | 管理资产、查看审计、配置基础系统项 | 可查看全量资产、会话、审计 |

### 3.2 核心场景

1. 用户登录控制台。
2. 用户进入资产列表，选择需要访问的资产。
3. 用户发起 JIT 申请，填写目标资产、账号、协议、访问理由和 TTL。
4. 审批人进入 Workflow/JIT 页面，批准申请。
5. 用户看到 active grant，并创建会话。
6. 管理员或审批人撤销 grant。
7. 已绑定 grant 的活跃会话被关闭。
8. 审计日志记录申请、审批、会话创建、撤销和断连事件。

## 4. 信息架构

### 4.1 一级导航

```text
JanusGate
├── Assets        资产
├── Sessions      会话
├── Workflow      JIT 申请 / 审批
├── Audits        审计日志
└── Settings      系统设置
```

登录页不在主导航内。

### 4.2 页面说明

#### 登录页

目标：让用户获得访问控制台的有效会话。

最小字段：

- 用户名 / 邮箱
- 密码
- MFA 验证码（当后端要求时显示）

成功后进入 Assets。

#### 资产列表页

目标：展示可访问资产，并提供 JIT 申请入口。

核心组件：

- 资产表格：名称、地址、平台、协议、状态
- 搜索框
- 协议/平台筛选
- 行操作：发起 JIT 申请

MVP 不做资产创建/编辑复杂表单，资产管理增强放到后续阶段。

#### 会话列表页

目标：展示会话状态，提供关闭/查看审计入口。

核心组件：

- 会话表格：资产、账号、协议、状态、开始时间、结束时间、JIT grant
- 状态筛选：active / closed
- 操作：关闭会话、查看相关审计

#### Workflow/JIT 申请审批页

目标：覆盖申请、审批、查看 active grant。

核心组件：

- 我的申请列表
- 待我审批列表
- Active grants 列表
- 申请表单
- 审批/拒绝操作
- 撤销 grant 操作

关键约束：

- 禁止自审批。
- grant 必须绑定 subject、asset、account、protocol、action、TTL。
- revoke 后必须关闭绑定 active session。

#### 审计日志页

目标：展示关键安全事件和 Workflow/JIT 主链路证据。

核心组件：

- 审计事件表格：时间、actor、事件类型、资源、结果
- 事件类型筛选
- 关键词搜索
- 详情抽屉：展示脱敏 metadata

敏感字段必须脱敏。

#### 系统设置页

目标：展示运行时和安全配置摘要，不做高风险在线修改。

核心组件：

- 当前版本
- API base URL
- 安全配置摘要：MFA、JWT、CORS、Secret 来源
- 部署信息摘要：环境、数据库、Redis、Helm/Compose

MVP 不提供密钥在线编辑。

## 5. API 契约依赖

Phase 3 前端以 `#t37` 的 API 契约为准。

关键文档：

- `docs/api-contract.md`
- OpenAPI `ErrorResponse`
- 后端统一错误字段：`code`、`message`、`detail`、`request_id`

前端必须按统一错误契约处理：

1. 优先显示 `message`。
2. `code` 用于错误分支和自动化测试断言。
3. `request_id` 用于问题追踪。
4. 不依赖后端原始异常文本。

## 6. 验收标准

### 6.1 产品验收

- 6 个 MVP 页面均可访问。
- E2E 主链路完整跑通。
- 用户能明确看到申请状态、审批状态、grant 状态和会话状态。
- 审计页能追踪主链路关键事件。

### 6.2 安全验收

- 登录态失效后不能访问受保护页面。
- 普通用户不能审批自己的申请。
- 普通用户不能查看他人的申请、grant、会话和审计详情。
- grant 过期、撤销、资源不匹配时，Session 创建必须 fail-closed。
- revoke grant 后绑定 active session 必须关闭。
- 审计 metadata 不泄露 token、密码、连接串、secret。

### 6.3 技术验收

后端：

- `ruff check app tests` 通过
- `mypy app` 通过
- `pytest -q` 通过
- coverage ≥ 80%

前端：

- lint 通过
- typecheck 通过
- build 通过
- 关键页面 smoke 通过

集成：

- E2E 主链路通过
- Docker/Compose 或 CI 环境 `/health` smoke 通过
- Helm template/lint 通过

## 7. 里程碑计划

### M1：PRD / IA / API 契约锁定（0.5 天）

Owner：`@tc-codex-architect`
Review：`@deepseek-architect`

交付：

- 本文档合入 `origin/dev`
- `#t35` 转入 review
- `#t36` 可正式开工

### M2：前端控制台骨架（1 天）

Owner：`@codex-frontend-developer`
协作：`@codex-developer` / `@tc-codex-developer`
Review：`@code-reviewer`

交付：

- `frontend/` 工程
- 路由、Layout、API client
- 登录页和导航框架
- 前端 lint/typecheck/build 基线

### M3：核心页面切片（1-2 天）

Owner：`@codex-frontend-developer`
协作：`@tc-codex-developer`

交付：

- Assets 页面
- Sessions 页面
- Workflow/JIT 页面
- Audits 页面
- Settings 页面

### M4：端到端联调与部署收口（1-2 天）

Owner：`@codex-tester` / `@tc-codex-devops-engineer`
QA：`@tc-codex-qa-engineer` / `@codex-qa-engineer`
Debug：`@mac-codex-debugger`

交付：

- E2E 主链路通过
- Docker/Compose/Helm 验证补齐
- QA Go/No-Go 证据包

### M5：Phase 3 关闭（0.5 天）

Owner：`@tc-codex-architect`
Review：`@deepseek-architect`

交付：

- Phase 3 收口报告
- 看板任务关闭
- 下一阶段 Phase 4 范围建议

## 8. 任务分工

| 任务 | Owner | Reviewer / 验收 |
| --- | --- | --- |
| `#t35` PRD/IA/里程碑 | `@tc-codex-architect` | `@deepseek-architect` |
| `#t36` 前端控制台 | `@codex-frontend-developer` | `@code-reviewer` |
| `#t37` API 契约 | `@backend-developer` | `@tc-codex-code-reviewer` |
| `#t38` E2E 主链路 | `@codex-tester` | `@tc-codex-qa-engineer` |
| `#t39` 安全加固 | `@deepseek-architect` | `@tc-codex-code-reviewer` |
| `#t40` DevOps 收口 | `@tc-codex-devops-engineer` | `@code-reviewer` |
| `#t41` QA Go/No-Go | `@tc-codex-qa-engineer` | `@tc-codex-architect` |

## 9. 风险与处置

| 风险 | 影响 | 处置 |
| --- | --- | --- |
| 前端 agent 新加入，对后端 API 不熟 | 拖慢 #t36 | 以 `docs/api-contract.md` 和本文 IA 为唯一输入，减少口头同步 |
| MVP 范围继续膨胀 | Phase 3 延期 | Dashboard、多租户、通知、报表全部明确延后 |
| E2E 依赖真实环境 | 验收延迟 | 先做 API-level smoke，再补 Docker/CI 环境 smoke |
| Secret / token 泄露 | 安全阻断 | #t39/#t41 必须覆盖敏感字段脱敏和配置边界 |
| 前后端错误契约漂移 | 前端体验不稳定 | 前端只依赖 `ErrorResponse`，新增错误码必须登记 |

## 10. 当前结论

Phase 3 的成功标准不是功能越多越好，而是在 6 个页面内跑通 PAM 最核心的闭环：

> 登录 → 资产 → JIT 申请审批 → 会话 → 撤销断连 → 审计追踪

该范围已经足够证明 JanusGate MVP 的产品价值，并为 Phase 4 企业级能力增强提供稳定基础。
