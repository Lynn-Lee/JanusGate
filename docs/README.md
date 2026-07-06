# JanusGate 文档入口

JanusGate 后续研发推进以 [`architecture/10-master-evaluation-and-roadmap.md`](architecture/10-master-evaluation-and-roadmap.md) 为唯一权威方案。

保留的其他文档只作为 API 契约、历史评估或核心专项设计参考，不承载研发路线图、任务状态或推进顺序。如果任何文档与 `10-master-evaluation-and-roadmap.md` 冲突，以 `10-master-evaluation-and-roadmap.md` 为准。

Phase 5 #t59 文档站 foundation 从 [`docs/site/index.md`](site/index.md) 开始，覆盖安装手册、管理员手册、管理员脱敏截图证据资产、截图测试数据夹具、API 文档入口和操作 runbook；OpenAPI JSON 通过 `scripts/export-openapi-json.sh` 自动生成。静态发布包可通过 `scripts/build-docs-site.sh dist/docs-site` 生成，CI 会执行该 smoke，确保文档入口、runbook、截图证据资产、截图 fixture 和 OpenAPI schema 可一起交付。截图证据已覆盖 License / Edition、审计报表、会话回放、Tenancy、账号轮换和 SSH CA。截图 capture 模式已支持 fixture-driven 前端 mock：浏览器注入脱敏 API 响应、按 evidence 执行页面操作，并在截图前校验 must_show/must_not_show 合约。
