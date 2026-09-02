# 数据库协议代理（#t71）

#t71 在 Connector 进程内实现了 **PostgreSQL Simple Query** 代理通道（`backend/app/connectors/postgres_proxy.py`），把每条 SQL 映射为对齐 #t46 命令事件管线的 `CommandEvent`，与 SSH/K8s 通道复用同一审计、命令过滤与数据脱敏管线。纯 Python `asyncio` 实现 PostgreSQL 3.0 线协议的 `Q`（Query）子集，不 fork `psql`、不依赖 `psycopg2`。

## 语义要点

- **每条 SQL 一次连接**：Simple Query 语义下 `PostgresQueryChannel.run_query` 每次打开独立 TCP/TLS 连接，完成 startup/auth 后发送单条 SQL，读取结果直至 `ReadyForQuery`。
- **命令事件字段**：`command` 为操作员 SQL 原文；`output_excerpt` 合并查询结果行或 `CommandComplete` 标签与 `ErrorResponse` 摘要（截断至 4096 字符）；`exit_code` 成功为 `0`，`ErrorResponse` 为 `1`。
- **生产装配**：`RoutingSessionConnectionResolver` 将 `postgresql` 协议路由至 `DatabaseVaultSessionConnectionResolver`；SSH 与 K8s 路由不变。

## 安全约束

以下约束由 `backend/tests/connectors/test_postgres_proxy.py` 与 `test_db_vault_resolver.py` 证明：

- **凭据仅内存**（P0#15/P0#16）：密码经 startup/auth 报文传递，`PostgresCredential` 的 `repr` 屏蔽，不进日志/URL/命令行。
- **TLS 强校验**（可选）：`require_tls=True` 时须预置 `server_ca` PEM，缺 CA 拒绝 TOFU（`PG_TLS_CA_MISSING`）。
- **命令策略执行前强制**：`CommandPolicyGuard.authorize(sql)` 在发往数据库前阻断（`PG_COMMAND_DENIED`）。
- **结果脱敏**：`CommandPolicyGuard.mask_text` 在写入 `CommandEvent.output_excerpt` 前应用（与 #t65 数据脱敏规则联动）。

## 目标与凭据

- **`PostgresTarget`**：`host`、`port`、`database`、`username`、可选 `require_tls` + `server_ca`。
- **`PostgresCredential`**：`password`（内存字符串，`repr` 屏蔽）。

## 管理面

- **资产**：`asset_type=database` 的 Cloud 旁路数据库资产，平台协议含 `postgresql`。
- **账号**：`protocol=postgresql`，凭据类型 `password`，经 Vault `unwrap` 解包。

## 已知边界

- 本切片仅覆盖 **PostgreSQL Simple Query**；MySQL wire protocol、prepared statement、extended query、交互式 psql REPL 仍待后续切片。
- 默认连接数据库名为 `postgres`（`DatabaseVaultSessionConnectionResolver.default_database`）； per-account 库名配置待后续模型扩展。
- 连接列表仍隐藏数据库协议入口；无新前端页面。

## 测试

- `tests/connectors/test_postgres_proxy.py` — 进程内 PG wire server 端到端 + 安全约束
- `tests/connectors/test_db_vault_resolver.py` — 生产 resolver 与路由
