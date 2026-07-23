#!/usr/bin/env bash
# 模型与 Alembic 迁移一致性门禁（#t60）。
#
# 无外部数据库依赖：用临时 aiosqlite 文件库执行 `alembic upgrade head`，再用
# `alembic check` 断言「ORM 模型 <-> 迁移链」无漂移（新增/修改模型却漏写迁移会失败），
# 最后 `downgrade base` 验证回滚链完整。PostgreSQL 专有对象（GIN 表达式索引）按方言
# 守卫，在 sqlite 下跳过，不影响一致性判定。
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root/backend"

python_bin=".venv/bin/python"
alembic_bin=".venv/bin/alembic"
if [[ ! -x "$alembic_bin" ]]; then
  python_bin="python"
  alembic_bin="alembic"
fi

# Settings 在导入期校验，提供确定性的非生产值，避免依赖开发者 .env。
export APP_ENV="${APP_ENV:-development}"
export SECRET_KEY="${SECRET_KEY:-migration-check-secret-key-migration-check-secret-key}"
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://janusgate:janusgate@localhost:5432/janusgate}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"

tmp_db="$(mktemp -t janusgate-migrations.XXXXXX.sqlite)"
cleanup() { rm -f "$tmp_db"; }
trap cleanup EXIT

db_url="sqlite+aiosqlite:///${tmp_db}"

printf 'Applying migrations to head...\n'
"$alembic_bin" -x "db_url=${db_url}" upgrade head

printf 'Checking models are in sync with migrations...\n'
"$alembic_bin" -x "db_url=${db_url}" check

printf 'Verifying full downgrade to base...\n'
"$alembic_bin" -x "db_url=${db_url}" downgrade base

printf 'Migration consistency check passed\n'
