# 平台治理（#t79）

本切片在现有 License / 审计报表之上补齐标签、动态系统配置、用户偏好、泄露密码库和报表目录，不复制 JumpServer 表结构。

## 标签

- `GET/POST /api/v1/governance/labels/`
- `PATCH/DELETE /api/v1/governance/labels/{id}`
- `PUT /api/v1/governance/labels/{id}/assets` 用当前租户资产 ID 全量替换绑定
- 权限：读 `assets:read`（或 `admin`），写 `assets:write`
- 删除标签会同时删除绑定；跨租户资产 ID 返回 `ASSET_NOT_FOUND`

## 动态系统配置

白名单键（拒绝 `smtp_password` 等未知键，避免把密钥写进动态配置）：

- `session_idle_timeout_minutes`（1–1440，默认 30）
- `password_min_length`（8–128，默认 8；改密时与复杂度策略取更严者）
- `weak_password_check_enabled`（默认 true）
- `ui_timezone`（默认 `Asia/Singapore`）

`PUT /api/v1/governance/settings` 仅 `admin`。每次有效变更写入 `tenant_setting_revisions`，响应与修订记录只含 JSON 值，不含密钥材料。

## 用户偏好

`GET/PUT /api/v1/governance/preferences` 作用于当前登录用户：

- `locale`：`zh-CN` / `en-US`
- `page_size`：10–100
- `theme`：`light` / `dark` / `system`

## 泄露密码库

- 内置 5 条常见复杂度口令的 SHA-256（`Password1!` 等），明文不落库
- 租户可 `POST /api/v1/governance/leak-passwords` 提交明文或 64 位 hex；列表只回哈希
- `POST /api/v1/governance/leak-passwords/check` 只返回 `{leaked}`
- `AuthService.create_user` / `change_password` 在复杂度检查之后走同一比对；命中返回「密码出现在泄露密码库中，请更换」，不回显口令

## 报表中心

内置模板：

- `audit-summary` → 现有 `GET /api/v1/audits/reports/summary`
- `soc2-access` → 现有合规导出（剥离 message 等明细字段）

`POST /api/v1/governance/reports` 保存具名报表；`POST /api/v1/governance/reports/run` 按 `template_key` 或 `report_id` 执行。权限 `audit:read` / `audit:write`。

## 控制台

系统设置页：资源标签、用户偏好、系统配置、泄露密码库。审计页：报表中心列出内置模板。
