# 作业中心

Phase 6 #t77 作业中心在 #t52 JSON-only Automation Worker 之上提供 Playbook 目录、作业变量、作业定义、临时命令（Ansible `command` 模块）与执行记录。控制台入口为 `/jobs`。

## 能力边界

- **Playbook 目录**：租户内保存相对 `.yml`/`.yaml` 文件名与 YAML 内容；禁止绝对路径和 `..`。
- **作业变量**：JSON extra vars，创建与入队时拒绝 `password` / `token` / `secret` 等敏感键。
- **作业**：`playbook` 或 `adhoc`；可指定目标资产、变量名、`runas_account_id`、可选 `interval_seconds` 周期。
- **临时命令**：只允许 Ansible `command` 模块，拒绝 `|` `;` `&` `` ` `` `$()` 等 shell 元字符，不使用 `shell=True`。
- **执行身份**：队列只带 `runas_account_id`，inventory 最多写 `ansible_user`，不写密码或 `secret_id`。
- **周期任务**：`POST /api/v1/job-center/scheduler/tick` 拾取 `next_run_at <= now` 的启用作业并入队，再把 `next_run_at` 推到下一窗口。
- **队列契约**：沿用 #t52。`job.adhoc` 加入 `ALLOWED_JOB_TYPES`。`payload_format=json`，**禁止 pickle**（对应关闭 JumpServer P0#10 Celery pickle RCE，本任务不得回退）。

## API

- `GET/POST /api/v1/job-center/playbooks/`
- `GET/POST /api/v1/job-center/variables/`
- `GET/POST /api/v1/job-center/jobs/`
- `POST /api/v1/job-center/jobs/{job_id}/run`
- `GET /api/v1/job-center/executions/`
- `POST /api/v1/job-center/scheduler/tick`

权限：`automation:read` / `automation:write` 或 `admin`。租户由当前登录用户决定，不接受客户端传入 `tenant_id`。

## 安全语义

- 队列消息字段均为字符串，payload 只经 `json.dumps` 写入 `payload_json`。
- extra vars 与队列 payload 递归拒绝敏感键。
- Playbook 执行可把目录 YAML 写到 runtime 临时目录（basename），不把内容以外的路径交给 `ansible-playbook`。
- `AutomationJobRun` / `JobExecution` 只保存状态元数据，不保存 stdout、stderr、inventory 或凭据。
