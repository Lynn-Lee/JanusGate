# 数据库协议代理（#t71）

#t71 在 Connector 进程内实现了数据库 wire protocol 代理，把每条 SQL 映射为对齐 #t46 命令事件管线的 `CommandEvent`，与 SSH/K8s 通道复用同一审计、命令过滤与数据脱敏管线。

## 已交付切片

| 引擎 | 模块 | 协议子集 |
|------|------|----------|
| PostgreSQL | `postgres_proxy.py` | Simple Query（`Q` 消息） |
| MySQL / MariaDB | `mysql_proxy.py` | COM_QUERY（`0x03`）+ `mysql_native_password` |

纯 Python `asyncio` 实现，不 fork 客户端子进程、不依赖 `psycopg2` / `mysqlclient`。

## 语义要点

- **每条 SQL 一次连接**：与 K8s exec 类似，每次 `run_query` 独立建连、认证、执行、读取结果。
- **命令事件字段**：`command` 为 SQL 原文；`output_excerpt` 为结果行或错误摘要（截断 4096 字符）；`exit_code` 成功 `0`，错误 `1`。
- **生产装配**：`RoutingSessionConnectionResolver` 将 `postgresql` / `mysql` / `mariadb` 路由至 `DatabaseVaultSessionConnectionResolver`。

## 安全约束

- **凭据仅内存**（P0#15/P0#16）：密码经 wire protocol 认证传递，`repr` 屏蔽，不进日志/URL/命令行。
- **TLS 强校验**（可选）：`require_tls=True` 时须预置 `server_ca` PEM，拒绝 TOFU。
- **命令策略执行前强制**：`CommandPolicyGuard.authorize(sql)` 在发往数据库前阻断。
- **结果脱敏**：`CommandPolicyGuard.mask_text` 写入 `CommandEvent` 前应用（#t65 联动）。

## 管理面

- **资产**：`asset_type=database`
- **账号**：`protocol=postgresql|mysql|mariadb`，凭据类型 `password`，经 Vault `unwrap` 解包
- 默认库名：`postgres`（PG）/ `mysql`（MySQL）

## 已知边界

- 不支持 prepared statement、extended query、交互式 REPL
- per-account 库名配置待后续模型扩展
- 连接列表仍隐藏数据库协议入口

## 测试

- `tests/connectors/test_postgres_proxy.py`
- `tests/connectors/test_mysql_proxy.py`
- `tests/connectors/test_db_vault_resolver.py`
