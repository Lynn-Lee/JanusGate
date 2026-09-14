# 平台治理（#t79）

`#t79` 在现有 License / 审计报表之上补齐 JumpServer 平台治理的五个入口：资源标签、动态系统配置、用户偏好、泄露密码库、报表目录。全部查询走当前租户 `scoped_select()`，密钥类字段不得进入动态配置。

## 资源标签

- `GET/POST /api/v1/governance/labels/`
- `PATCH/DELETE /api/v1/governance/labels/{label_id}`
- `PUT /api/v1/governance/labels/{label_id}/assets`

本切片只标注资产。绑定资产必须属于当前租户，否则 `404 ASSET_NOT_FOUND`，不泄露跨租户存在性。删除标签会一并删除绑定。

权限：`assets:read` 可列表，`assets:write` 可写。

## 动态系统配置

- `GET/PUT /api/v1/governance/settings`
- `GET /api/v1/governance/settings/revisions`

仅 `admin` 可读写。白名单键：

| 键 | 类型 | 默认 |
|----|------|------|
| `session_idle_timeout_minutes` | int 1–1440 | 30 |
| `password_min_length` | int 8–128 | 8 |
| `weak_password_check_enabled` | bool | true |
| `ui_timezone` | str | Asia/Singapore |

未知键与密钥类字段名（含 `secret` / `token` / `private` / `credential`）一律 `SETTING_KEY_NOT_ALLOWED`。变更写入 `tenant_setting_revisions`，只保存新旧 JSON 值。

`password_min_length` 是 overlay：只能把全局下限抬高，不能低于 `PASSWORD_MIN_LENGTH`。

## 用户偏好

- `GET/PUT /api/v1/governance/preferences`

登录用户只能读写自己的 `locale` / `page_size` / `theme`。同租户其他用户看不到。

## 泄露密码库

- `GET/POST /api/v1/governance/leak-passwords`
- `POST /api/v1/governance/leak-passwords/check`

只存 SHA-256，响应永不回显明文。内置 5 条常见复杂度口令哈希。创建用户与改密走 `AuthService`，命中内置或租户库时拒绝。`weak_password_check_enabled=false` 时跳过库比对，复杂度策略仍生效。

## 报表目录

- `GET/POST /api/v1/governance/reports`
- `POST /api/v1/governance/reports/run`

内置模板 `audit-summary` 与 `soc2-access`，复用 #t49 / #t54 汇总与合规导出。运行结果去掉 `metadata` / `message` / `resource_id` / `session_id`。自定义 SQL 报表不做。

## 控制台

- 设置页：用户偏好、资源标签、系统配置、泄露密码库
- 审计页：报表中心

## 不做

更广资源类型标注、自定义 SQL 报表、密码历史仍可后续切片。
