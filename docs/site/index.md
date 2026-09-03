# JanusGate 文档站

Phase 5 #t59 文档站 foundation 面向安装、运维和 API 集成读者，当前以仓库内 Markdown 作为单一来源，后续可接入静态站点生成器。

## 导航

- [安装手册](install.md)：本地 Docker Compose、Helm 安装、密钥和回滚入口。
- [管理员手册](admin.md)：登录后常用管理面、License / Edition 摘要、审计和运行门禁。
- [管理员截图证据](admin-screenshots.md)：管理控制台关键截图点、脱敏截图文件、脱敏要求、真实截图归档合约和回归测试来源，当前覆盖 License、审计报表、会话回放、Tenancy、账号轮换和 SSH CA。
- [API 文档](api.md)：稳定 API 分组、OpenAPI 导出和前端/集成方消费方式。
- [操作 Runbook](runbooks.md)：发布、多副本 smoke、回滚、密钥处理、License 运营和运行时告警演练清单；配套 `fixtures/operation-runbook-evidence.json`、`fixtures/license-operations-evidence.json` 与 `fixtures/runtime-alert-evidence.json` 固定操作证据归档字段。
- [SSH 连接器预研通道](connectors-ssh.md)：#t69 预研切片真实 SSH 执行通道的安全约束（P0#7/15/16/17）、现代算法白名单要点和命令事件入库端点用法。
- [K8s exec 连接器通道](connectors-k8s.md)：#t72 真实 `kubectl exec` 语义通道（WebSocket `v4.channel.k8s.io`）的 namespace 作用域强制、TLS 强校验、凭据仅内存约束和命令事件管线复用。
- [K8s 容器纳管](k8s-management.md)：#t68 Cloud 资产集群登记、账号 namespace 作用域、TokenRequest 短期 token 与生产 `K8sVaultSessionConnectionResolver` 装配。
- [数据库协议代理](connectors-database.md)：#t71 PostgreSQL Simple Query 代理通道、SQL 命令审计与 #t65 脱敏联动。
- [命令过滤 ACL](acl-command-filter.md)：#t65 ACL 体系首个派生类型——命令过滤 ACL + 命令组的数据模型、优先级/动作/复核人语义，以及统一进 `PolicyDecisionService.evaluate_command` 的 deny-overlay 判定。
- [数据脱敏规则](acl-data-masking.md)：#t65 数据脱敏规则的数据模型、full/partial 打码方式，以及统一进 `PolicyDecisionService.mask` 的累计应用语义（#t71 DB 代理联动的前置）。
- [资产树与 AssetPermission](asset-tree-authorization.md)：#t64 的节点树、资产挂载、用户/用户组授权、账号/协议/动作/有效期/来源工单选择器，以及会话授权 explain 链路。
- [RBAC 角色与权限](rbac.md)：#t63 的 Role / RoleBinding / 对象级 Permission、system+org 双 scope、内置角色、菜单权限与登录 token 签发接入。
- [资产类型与协议](asset-types-protocols.md)：#t66 的声明式协议目录、Platform 协议约束、8 种资产类型与 19+1 协议种子。
- [网域与网关中转](zones-gateways.md)：#t67 的 Zone + Gateway 模型、连通性探测与 SSH ProxyJump 建连语义。

## 版本边界

- 研发路线图仍以 `docs/architecture/10-master-evaluation-and-roadmap.md` 为唯一权威来源。
- API 契约仍以 `docs/api-contract.md` 为稳定契约来源。
- OpenAPI JSON 由 `scripts/export-openapi-json.sh` 从 FastAPI 应用导出，避免手写 schema 漂移。
- 静态站点发布包由 `scripts/build-docs-site.sh dist/docs-site` 生成，包含本目录 Markdown、管理员截图资产、截图 fixture、真实截图归档合约、操作 runbook evidence manifest、license operations evidence manifest、runtime alert evidence manifest、`openapi.json` 和 `manifest.json`。
- 管理员截图证据由 `scripts/phase5-docs-browser-screenshots-smoke.sh` 做 CI wiring smoke；显式设置 `JANUSGATE_CAPTURE_DOC_SCREENSHOTS=1` 后可在具备 Playwright 的前端环境注入 fixture、执行页面操作、校验截图合约并捕获真实浏览器 PNG 截图到 `docs/site/assets/screenshots/live-screenshots/`。
