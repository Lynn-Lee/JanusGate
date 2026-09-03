# 账号自动化与账号治理（#t73）

`#t73` 提供 JumpServer 对标的 8 类账号自动化，以及 `AccountTemplate` / `AccountRisk` 治理模型。执行走 #t52 JSON-only 队列，改密复用 #t43 `CredentialRotation` 与 #t69 asyncssh 通道。

## 八类自动化

| job_type | 语义 |
|----------|------|
| `account.push` | 按模板向资产推送账号，生成密码写入 Vault 后创建托管 `Account` |
| `account.change_secret` | 远程改密；经 `chpasswd` **stdin** 传密，命令行不含密码 |
| `account.verify` | 用托管凭据尝试 SSH 登录校验 |
| `account.remove` | 远程删除账号，并将托管记录标为 `removed` |
| `account.gather` | 发现主机 passwd 账号；特权账号与缺失的托管账号记入风险 |
| `account.verify_gateway` | 网关账号校验（#t67 落地前校验托管记录存在） |
| `account.check` | 弱密码 / 特权账号检测，写入 `AccountRisk` |
| `account.backup` | 备份账号元数据与 `secret_id` 引用，**不含明文** |

队列 payload 只含 `account_id` / `asset_id` / `template_id` / `reason`。含 `password` / `secret` / `private_key` 等键会被 `AutomationJobQueue` 拒绝。

## 安全约束

- **P0#16**：不调用 `ssh` / `sshpass` 子进程；改密命令固定为 `chpasswd`，密码只出现在 asyncssh `input=`。
- **P2#13**：执行路径使用 `structlog` 结构化日志，敏感键递归替换为 `[REDACTED]`，禁止 `print()`。
- 私钥与密码仅内存传递，API 响应不回传明文或新 secret。

## API

- `GET/POST /api/v1/account-templates/` — 账号模板
- `GET /api/v1/account-risks/`、`POST /api/v1/account-risks/{id}/resolve` — 账号风险
- `GET /api/v1/account-automation/runs` — 执行记录摘要
- `POST /api/v1/automation/jobs/account-*` — 八类任务入队（需 `accounts:automate` 或 `automation:write`）

权限：`accounts:read` 查看模板/风险；`accounts:write` 创建模板；`accounts:automate` 调度自动化。系统管理员与组织管理员内置角色已包含上述权限。

## 验证

- 单元测试覆盖 8 类 handler、租户隔离、队列拒密、风险落库
- 进程内 asyncssh 端到端证明改密密码只走 stdin
- Alembic 迁移 `a1b2c3d4e5f6` 增加 `account_templates` / `account_risks` / `account_automation_runs` / `account_backups`
