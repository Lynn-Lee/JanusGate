# 资产类型与协议（#t66）

`#t66` 提供声明式协议目录与 Platform 协议约束，对标 JumpServer 19 种协议 + GPT 扩展位。

## 资产类型

| 类型 | 说明 |
|------|------|
| `host` | 主机 |
| `database` | 数据库 |
| `device` | 网络设备 |
| `web` | Web 应用 |
| `cloud` | 云/K8s |
| `custom` | 自定义 |
| `directory_service` | 目录服务 |
| `gpt` | GPT 扩展位（仅协议占位，不实现 AI 代理） |

## 协议目录

全局 `protocols` 表按租户无关方式种子 20 条内置协议（19 对标 + `gpt`）。每条协议声明：

- 默认端口
- 适用资产类型
- 凭据类型（password / private_key / token / certificate）
- 可选 `driver_module`（数据库驱动按需加载，核心镜像不内置）

## Platform 约束

`platform_protocols` 关联表记录 Platform 允许的协议、端口覆盖与 `is_primary` 主协议。创建 Platform 时仍可使用 `protocols` JSON 字段，系统会同步到关联表。

## API

- `GET /api/v1/protocols/` — 列出全部协议
- `GET /api/v1/protocols/by-asset-type/{asset_type}` — 按资产类型过滤
- `GET /api/v1/assets/platforms/{platform_id}/protocols` — Platform 协议约束

创建资产时 `asset_type` 必须与 Platform 的 `asset_type` 一致，否则返回 400 `PLATFORM_ASSET_TYPE_MISMATCH`。

## 验证

- 单元测试覆盖协议目录、Platform 绑定校验、API 列表与资产创建校验
- Alembic 迁移 `f8a1d2c3b4e5` 增加 `protocols`、`platform_protocols` 与 `assets.asset_type` / `platforms.asset_type`
