# 数据库迁移（Alembic）

JanusGate 的 schema 变更全部经 Alembic 管理。ORM 模型定义在 `app/models/`，
`Base.metadata` 是唯一事实源；每次模型变更都必须附带对应迁移，且通过一致性门禁。

## 连接串来源

`alembic/env.py` 按以下优先级解析目标库：

1. 命令行 `-x db_url=<url>`（离线生成 / CI 自检用）；
2. 回落到应用 `Settings.DATABASE_URL`（生产 asyncpg）。

生产 URL 用 async 驱动（`postgresql+asyncpg://...`）；env.py 走 async 引擎运行迁移。

## 常用命令

在 `backend/` 目录下执行（本地用 `.venv/bin/alembic`）：

```bash
# 应用全部迁移到最新
alembic upgrade head

# 查看当前版本
alembic current

# 生成新迁移（改动模型后）——建议对空 PostgreSQL 生成以获得最忠实的方言 DDL
alembic revision --autogenerate -m "描述本次变更"

# 回滚一步 / 回滚到基线
alembic downgrade -1
alembic downgrade base
```

## 一致性门禁

CI（`.github/workflows/ci.yml` 的 backend-quality）会跑 `scripts/check-migrations.sh`：
用临时 aiosqlite 文件库 `upgrade head` → `alembic check` → `downgrade base`，
断言「模型 ↔ 迁移链」无漂移。**改了模型却忘记生成迁移，该门禁会失败。**

本地提交前自查：

```bash
scripts/check-migrations.sh
```

## PostgreSQL 专有对象与 alembic check 限制

`session_command_events` 的全文检索 GIN 表达式索引（`to_tsvector(...)`）仅
PostgreSQL 支持，模型侧以 `ddl_if(dialect="postgresql")` 守卫。基线迁移中对应
`op.create_index(...)` 同样按 `dialect.name == "postgresql"` 守卫。

- **表达式必须 IMMUTABLE**：索引表达式用 `command || ' ' || output_excerpt`（`||`
  是 IMMUTABLE）而非 `concat(...)`（STABLE）。PostgreSQL 会拒绝索引表达式中的非
  IMMUTABLE 函数（`InvalidObjectDefinition`）。模型与迁移需保持一致。
- **一致性门禁走 sqlite**：`scripts/check-migrations.sh` 用 sqlite，autogenerate 无法
  反射表达式索引会自动跳过它，故门禁干净且能覆盖其余全部对象。
- **对真 PG 跑 `alembic check` / `--autogenerate` 会崩**：alembic 的 PG 索引比较在渲染
  该表达式（含 `'simple'::regconfig` 字面量）时抛 `CompileError`，这是 alembic 对函数
  索引的已知限制，`include_object` 无法拦截（崩溃发生在过滤器之前）。**故请勿对生产
  PG 直接 autogenerate**；改这个索引时手工同步「模型 + 基线迁移」两处。`alembic upgrade
  head` 本身在 PostgreSQL 16 上已实测通过（索引可正常创建），部署路径不受影响。

## 注意

- 基线迁移（`versions/*_baseline_schema.py`）覆盖首次建库的全部 23 张表。
- 离线用 sqlite 生成时，`func.now()` 会被编译为 sqlite 语法；基线已手工归一为
  PostgreSQL 的 `now()`。新增迁移若在 sqlite 下生成，请复核 server_default 等方言细节。
- 生产迁移前务必备份（见 `deploy/README.md` 的发布与回滚 runbook）。
