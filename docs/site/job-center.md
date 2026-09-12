# 作业中心

#t77 在 Phase 4 `#t52` JSON-only 队列与 Ansible runner 之上提供作业 / Playbook 目录 / 临时命令 / 变量 / 执行记录，以及周期调度与 runas 身份策略。

## 能力边界

- Playbook 目录只登记 `playbook root` 内相对 `.yml/.yaml` 文件名，不入库 Playbook 正文。
- 作业类型：`playbook` 或 `adhoc`（`command` / `shell`）。
- 每个作业必须指定租户内 active SSH `runas_account_id`。队列与 runner 只使用账号用户名作为 `ansible_user`，不把 Vault secret、密码或私钥写入队列、argv 或环境变量。
- 参数化通过作业 `extra_vars` 与作业变量完成。敏感键名（password / token / secret / private_key 等）与 Jinja 模板标记 fail-closed。
- 周期作业使用 5 段 cron，API `POST /api/v1/job-center/scheduler/tick` 扫描当前租户到期作业。
- 执行记录复用 `AutomationJobRun`，响应只返回状态、参数键名与错误码，不返回命令输出或凭据。

## 安全约束（P0#10）

- 入队 `job_type` 仅 `job.playbook` / `job.adhoc`。
- 队列 payload **只含** `{"job_id": <int>}`，`payload_format=json`。
- 禁止 pickle 或任意 Python 对象派发；非 JSON format 不得 ack。
- Worker 从数据库加载命令、变量与目标资产，再调用既有 `LocalAnsiblePlaybookRunner`。临时命令生成 runtime 内联 playbook，命令放进 extra-vars 文件而不是进程命令行。

## API

- `GET/POST /api/v1/job-center/playbooks`
- `GET/POST /api/v1/job-center/jobs`
- `GET/DELETE /api/v1/job-center/jobs/{job_id}`
- `GET/POST /api/v1/job-center/jobs/{job_id}/variables`
- `POST /api/v1/job-center/jobs/{job_id}/run`
- `GET /api/v1/job-center/executions`
- `POST /api/v1/job-center/scheduler/tick`

鉴权：`automation:read` / `automation:write` 或 `admin`。

控制台入口：`/jobs`。
