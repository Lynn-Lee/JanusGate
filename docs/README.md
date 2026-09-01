# JanusGate 文档入口

JanusGate 后续研发推进以 [`architecture/10-master-evaluation-and-roadmap.md`](architecture/10-master-evaluation-and-roadmap.md) 为唯一权威方案。

保留的其他文档只作为 API 契约、历史评估或核心专项设计参考，不承载研发路线图、任务状态或推进顺序。如果任何文档与 `10-master-evaluation-and-roadmap.md` 冲突，以 `10-master-evaluation-and-roadmap.md` 为准。

Phase 5 #t59 文档站 foundation 从 [`docs/site/index.md`](site/index.md) 开始，覆盖安装手册、管理员手册、管理员脱敏截图证据资产、截图测试数据夹具、真实截图归档合约、API 文档入口、操作 runbook、操作证据 manifest、license operations evidence manifest 和 runtime alert evidence manifest；OpenAPI JSON 通过 `scripts/export-openapi-json.sh` 自动生成。静态发布包可通过 `scripts/build-docs-site.sh dist/docs-site` 生成，CI 会执行该 smoke，确保文档入口、runbook、截图证据资产、截图 fixture、截图 archive manifest、操作 runbook evidence manifest、license operations evidence manifest、runtime alert evidence manifest 和 OpenAPI schema 可一起交付。截图证据已覆盖 License / Edition、审计报表、会话回放、Tenancy、账号轮换和 SSH CA。截图 capture 模式已支持 fixture-driven 前端 mock：浏览器注入脱敏 API 响应、按 evidence 执行页面操作，并在截图前校验 must_show/must_not_show 合约；真实浏览器 PNG 已完成刷新，产物位于 `docs/site/assets/screenshots/live-screenshots/*.png`，现有 SVG 继续作为静态发布包脱敏基线证据；`docs/site/fixtures/admin-screenshot-archive.json` 固定真实运行环境截图归档时的 route、静态 SVG artifact、live PNG artifact、回归来源和 `JANUSGATE_FRONTEND_BASE_URL` 捕获入口。

Phase 5 #t55 性能证据归档合约位于 [`performance/phase5-load-test-evidence-template.json`](performance/phase5-load-test-evidence-template.json)，用于真实 endpoint mix 压测后记录目标环境、runner 配置、聚合结果、容量模型结果和 artifact manifest；`scripts/phase5-load-test-evidence-smoke.sh` 会校验必填字段和敏感字段名边界。

Phase 6 已交付的专项文档同样纳入静态发布包：[`site/connectors-ssh.md`](site/connectors-ssh.md)（#t69 SSH / SFTP / PTY 通道的安全约束与命令事件契约）、[`site/connectors-k8s.md`](site/connectors-k8s.md)（#t72 K8s exec 通道的 namespace 作用域、TLS 强校验与凭据边界）、[`site/acl-command-filter.md`](site/acl-command-filter.md)（#t65 命令过滤 ACL 与命令组的判定语义和租户 scope 加载）、[`site/acl-data-masking.md`](site/acl-data-masking.md)（#t65 数据脱敏规则的累计应用语义）、[`site/asset-tree-authorization.md`](site/asset-tree-authorization.md)（#t64 资产树授权）、[`site/rbac.md`](site/rbac.md)（#t63 RBAC 角色权限）。这些文档随 `scripts/build-docs-site.sh` 一并拷贝并登记进 `manifest.json` 的 pages 列表。
