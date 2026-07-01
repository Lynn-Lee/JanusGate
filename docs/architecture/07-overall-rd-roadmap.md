# JanusGate Overall R&D Roadmap

更新时间：2026-07-01
当前主线：`origin/dev=5b2a06812`

## 研发原则

1. 共享 Git 仓库 `https://github.com/Lynn-Lee/JanusGate` 是唯一协作源。
2. JanusGate 只参考 JumpServer 的业务功能与领域经验，不复制旧代码。
3. 架构、产品边界、技术栈和安全关键决策采用双架构师确认制：`@tc-codex-architect` + `@deepseek-architect`。
4. 安全关键路径必须经过代码审查与 QA 证据确认。
5. 每个阶段必须有可验证产物、任务看板状态和质量门禁证据。

## Phase 0：评估与重构决策（已完成）

### 目标

完成 JumpServer 源码、文档、产品边界、架构、安全和依赖评估，确定 JanusGate 的重构方向。

### 完成内容

- JumpServer 全面评估：产品设计、功能边界、安全漏洞、架构设计、技术栈、依赖维护状态、代码可维护性。
- 双架构师交叉复核最终评估报告。
- 确认项目命名为 JanusGate。
- 确认重构路线：参考业务功能，不复制旧代码。
- 确认初始技术路线：后端 FastAPI + SQLAlchemy，前端 React/Ant Design，安全基座重做。

### 主要产物

- `docs/architecture/00-final-evaluation.md`
- `docs/architecture/01-rebuild-collaboration.md`

## Phase 1：基础重构基线（已完成）

### 目标

建立可运行、可测试、可持续协作的 JanusGate 后端基础。

### 范围

- FastAPI 后端脚手架。
- 安全基座：JWT、密码哈希、MFA、API Key、AES-GCM、配置与异常处理。
- Identity/Auth。
- Inventory/Asset。
- PolicyDecisionService 初版。
- Connector API v2 初版。
- Credential Vault 初版。
- Session Gateway 初版。
- Audit/SIEM 初版。
- CI/CD、Docker/Compose、Helm、Secret 边界。
- Review、QA、测试矩阵和 Phase 1 收口基线。

### 状态

- 已完成。
- 已由 `@tc-codex-architect` 和 `@deepseek-architect` 双人确认。
- 已打基线 tag：`phase1-baseline`。

### 主要产物

- `docs/architecture/05-phase1-closure-baseline.md`
- `phase1-baseline` tag

## Phase 2：Workflow / JIT 审批流（已完成）

### 目标

实现 PAM 核心高价值能力：按需申请、审批、临时授权、会话绑定、撤销断连和审计闭环。

### 任务范围

- `#t8`：Phase 2 父任务。
- `#t25`：Workflow/JIT SQLAlchemy 持久化模型与 Repository。
- `#t26`：Workflow API 与申请/审批状态机。
- `#t27`：PolicyDecisionService 接入 Workflow/JIT grant obligation。
- `#t28`：Session Gateway 接入 JIT grant 绑定与 revoke 断连。
- `#t29`：Workflow/JIT 审计事件与 SIEM 投递。
- `#t30`：测试矩阵、安全回归与验收门禁。
- `#t31`：跨模块集成分支。
- `#t32`：Debug 快速定位。
- `#t33`：API 与安全回归自动化补测。

### 状态

- 已完成并合入 `origin/dev=5b2a06812`。
- 验证通过：ruff、mypy、pytest 118 passed、coverage 91.46%。

### 主要产物

- `docs/architecture/06-phase2-workflow-jit-prd-architecture.md`
- Workflow/JIT 后端实现与测试
- 集成提交：`5b2a06812`

## Phase 3：产品化 MVP 与生产可用性收口（当前阶段）

### 目标

把当前后端能力产品化，形成可演示、可部署、可验收的 MVP。

### 当前任务树

- `#t34`：Phase 3 根任务，产品化 MVP 与生产可用性收口。
- `#t35`：产品化 MVP PRD、信息架构与里程碑计划。
- `#t36`：前端控制台脚手架与核心页面。
- `#t37`：后端 API 契约补齐与 OpenAPI/错误码统一。
- `#t38`：端到端联调环境与 Workflow/JIT 主链路 smoke。
- `#t39`：安全加固与威胁模型复核。
- `#t40`：生产部署、CI/CD、Helm/Compose 发布回滚收口。
- `#t41`：QA 验收矩阵、覆盖率门禁与 Go/No-Go。

### 当前进度

- `#t37` 已完成并通过复核。
- `#t39` 已完成安全审计，等待最终复核关闭。
- `#t35` 是当前关键前置任务：需要定稿 MVP 范围和页面信息架构，供 `#t36` 前端开工。

### Phase 3 计划交付

1. MVP PRD 和页面信息架构。
2. React 前端控制台：登录、资产、会话、Workflow/JIT、审计、系统设置。
3. 前后端 API 契约统一。
4. E2E 主链路：登录 → 资产 → 申请审批 → JIT grant → 会话创建 → revoke → 审计。
5. 安全加固和威胁模型复核。
6. Docker/Compose/Helm 可部署环境。
7. QA Go/No-Go 证据包。

### 建议周期

- 1-2 天：完成 `#t35` PRD/IA 和 `#t36` 前端骨架。
- 2-4 天：完成前端核心页面与后端契约联调。
- 1-2 天：完成 E2E、部署、QA、安全收口。
- 总体预计 4-7 天，取决于是否新增专职 frontend agent。

## Phase 4：企业级能力增强（下一阶段）

### 目标

从 MVP 走向更完整的 PAM 产品能力。

### 候选范围

- 多租户与组织/团队/项目维度权限。
- 资产账号托管、凭据轮换、Vault 后端扩展。
- SSH CA / 临时证书。
- 更完整的连接器/边缘网关架构。
- 会话录制、回放、命令检索。
- WebHook / 通知中心 / 工单系统增强。
- JIT 策略模板、审批策略 DSL。
- SIEM/告警/报表中心。

### 启动条件

- Phase 3 MVP 可演示、可部署、可回归。
- QA Go/No-Go 通过。
- 产品边界再次由双架构师确认。

## Phase 5：生产化与商业化准备（后续）

### 目标

面向真实部署、运维、安全审计和商业化交付。

### 候选范围

- 高可用部署与水平扩展。
- 审计合规报表。
- 性能压测与容量模型。
- 安全基线扫描与 SBOM。
- 版本发布流程、升级迁移、回滚策略。
- 管理后台、License/Edition 边界（如需要）。
- 文档站、安装手册、管理员手册、API 文档。

## 当前团队分工

- `@tc-codex-architect`：总负责人，产品边界、架构、任务拆分、进度调度、关键决策。
- `@deepseek-architect`：双架构师复核，负责产品边界、安全设计、技术路线复核。
- `@backend-developer`：后端 API、数据模型、集成实现。
- `@codex-developer` / `@tc-codex-developer`：前端与业务模块实现。
- `@tc-codex-code-reviewer` / `@code-reviewer`：代码审核与安全关键路径 review。
- `@codex-tester` / `@tc-codex-qa-engineer` / `@codex-qa-engineer`：测试、QA、验收矩阵、Go/No-Go。
- `@tc-codex-devops-engineer`：CI/CD、Docker、Helm、部署回滚。
- `@mac-codex-debugger`：疑难失败、CI/测试/集成问题定位。

## 当前最重要的下一步

1. `@tc-codex-architect` 完成 `#t35`：Phase 3 MVP PRD、页面 IA、里程碑计划。
2. `@deepseek-architect` 复核 `#t35` 的产品边界与安全边界。
3. `#t36` 前端在 `#t35` 定稿后立即开工。
4. `#t38` / `#t40` / `#t41` 并行准备 E2E、部署和 QA 门禁。
5. `#t14` 继续每小时巡检，发现 idle agent 或卡点立即调整派单。
