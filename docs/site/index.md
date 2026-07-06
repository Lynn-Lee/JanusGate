# JanusGate 文档站

Phase 5 #t59 文档站 foundation 面向安装、运维和 API 集成读者，当前以仓库内 Markdown 作为单一来源，后续可接入静态站点生成器。

## 导航

- [安装手册](install.md)：本地 Docker Compose、Helm 安装、密钥和回滚入口。
- [管理员手册](admin.md)：登录后常用管理面、License / Edition 摘要、审计和运行门禁。
- [API 文档](api.md)：稳定 API 分组、OpenAPI 导出和前端/集成方消费方式。

## 版本边界

- 研发路线图仍以 `docs/architecture/10-master-evaluation-and-roadmap.md` 为唯一权威来源。
- API 契约仍以 `docs/api-contract.md` 为稳定契约来源。
- OpenAPI JSON 由 `scripts/export-openapi-json.sh` 从 FastAPI 应用导出，避免手写 schema 漂移。
- 静态站点发布包由 `scripts/build-docs-site.sh dist/docs-site` 生成，包含本目录 Markdown、`openapi.json` 和 `manifest.json`。
