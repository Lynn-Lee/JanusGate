# 数据库协议代理通道

#t71 在 Connector 进程内实现 PostgreSQL Simple Query 与 MySQL / MariaDB `COM_QUERY` 代理（`backend/app/connectors/postgres_proxy.py`、`mysql_proxy.py`），把每一条 SQL 映射为对齐 #t46 命令事件管线的 `CommandEvent`，并与 SSH / K8s 通道复用同一条命令过滤与脱敏管线。实现为纯 Python `asyncio` 线协议子集，不 fork `psql` / `mysql` 客户端，也不依赖 `psycopg` / `PyMySQL`。

## 语义要点

- **PostgreSQL**：前端/后端协议的 Simple Query（`Q` 消息）+ `AuthenticationCleartextPassword`。每条 SQL 独立建连、执行、关闭。
- **MySQL / MariaDB**：4.1+ 握手 + `mysql_native_password` + `COM_QUERY`（`0x03`）。MariaDB 资产协议映射到同一条 MySQL 通道。
- **命令事件**：字段与 SSH / K8s 同构：`sequence`、`command`（SQL 原文）、`exit_code`（服务端错误为 `1`）、`output_excerpt`（截断至 4096 字符，已走 #t65 `mask`）。
- **执行前策略**：`CommandPolicyGuard.authorize` 在**打开远端连接之前**判定。`DENY` / `REVIEW` 抛 `PG_COMMAND_DENIED` / `MYSQL_COMMAND_DENIED`，假 server 收不到任何查询。

## 安全约束

以下约束由 `backend/tests/connectors/test_postgres_proxy.py` 与 `test_mysql_proxy.py` 证明关闭。

- **TLS 强校验（对标 P0#17）**：`require_tls=True` 时必须预置 CA（PEM）。PostgreSQL 先发 SSLRequest，收到 `S` 后再 `start_tls`；MySQL 在握手后发带 `CLIENT_SSL` 的 SSLRequest 再升级。`check_hostname` + `CERT_REQUIRED`，缺 CA 拒绝（`PG_TLS_CA_MISSING` / `MYSQL_TLS_CA_MISSING`），绝不 TOFU，也没有关闭校验的开关。服务端拒绝 SSL 时 PostgreSQL 报 `PG_TLS_NOT_AVAILABLE`，不回退明文。
- **凭据仅内存（对标 P0#15 / P0#16）**：密码由 Vault unwrap 后仅存在 `PostgresCredential` / `MysqlCredential`，`repr` 屏蔽；不经 URL、argv 或磁盘临时文件。
- **结果脱敏**：查询输出经 `PolicyDecisionService.mask`（累计应用全部命中规则），不得旁路。

## 生产接线

`AssetVaultSessionConnectionResolver` 在 `protocol=postgresql|mysql|mariadb` 时：

- 从资产读取地址、端口；`namespace` 复用为默认库名（空则 PostgreSQL 用 `postgres`、MySQL 用 `mysql`）。
- 从 Vault 解开账号密码（仅内存）。
- 资产上预置了 `server_ca` 则强制 TLS；未预置 CA 时允许内网明文（文档化边界，与 K8s 的 HTTPS-only 不同）。
- 不扫描 SSH 主机密钥。

`ConnectorSessionRuntime` 增加 `db_postgresql` / `db_mysql` 模式，打开对应查询通道。连接列表沿用平台 `connect_protocols`（数据库资产展示 postgresql / mysql / mariadb），无新页面。

## 已知边界

- 无 extended query / prepared statement、无交互式 REPL、无 Oracle / SQL Server。
- `mysql_native_password` 的 SHA-1 是线协议强制算法，不是本地口令哈希；`caching_sha2_password` 未实现。
- PostgreSQL 仅明文密码认证（类型 3）；SCRAM 未实现。
- 列级结构化脱敏仍不解析 SQL AST；本切片对整段文本结果做 #t65 累计打码。
