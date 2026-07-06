# JanusGate 文档站

Phase 5 #t59 文档站 foundation 面向安装、运维和 API 集成读者，当前以仓库内 Markdown 作为单一来源，后续可接入静态站点生成器。

## 导航

- [安装手册](install.md)：本地 Docker Compose、Helm 安装、密钥和回滚入口。
- [管理员手册](admin.md)：登录后常用管理面、License / Edition 摘要、审计和运行门禁。
- [管理员截图证据](admin-screenshots.md)：管理控制台关键截图点、脱敏截图文件、脱敏要求和回归测试来源，当前覆盖 License、审计报表、会话回放、Tenancy、账号轮换和 SSH CA。
- [API 文档](api.md)：稳定 API 分组、OpenAPI 导出和前端/集成方消费方式。
- [操作 Runbook](runbooks.md)：发布、多副本 smoke、回滚和密钥处理清单。

## 版本边界

- 研发路线图仍以 `docs/architecture/10-master-evaluation-and-roadmap.md` 为唯一权威来源。
- API 契约仍以 `docs/api-contract.md` 为稳定契约来源。
- OpenAPI JSON 由 `scripts/export-openapi-json.sh` 从 FastAPI 应用导出，避免手写 schema 漂移。
- 静态站点发布包由 `scripts/build-docs-site.sh dist/docs-site` 生成，包含本目录 Markdown、管理员截图资产、截图 fixture、`openapi.json` 和 `manifest.json`。
- 管理员截图证据由 `scripts/phase5-docs-browser-screenshots-smoke.sh` 做 CI wiring smoke；显式设置 `JANUSGATE_CAPTURE_DOC_SCREENSHOTS=1` 后可在具备 Playwright 的前端环境注入 fixture、执行页面操作、校验截图合约并捕获真实浏览器截图。
