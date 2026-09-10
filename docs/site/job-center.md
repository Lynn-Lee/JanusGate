# 作业中心

#t77 在 #t52 JSON-only 自动化队列之上补齐作业 / Playbook / 临时命令 / 变量 / 执行记录，以及周期任务与执行身份（runas）。

## 能力边界

- 作业定义保存在 `job_definitions`：`job_type`、JSON `payload`、JSON `extra_variables`、可选五段 cron、`run_as_user_id`。
- 立即执行：`POST /api/v1/job-center/jobs/{id}/run`。
- 临时/批量命令：`POST /api/v1/job-center/adhoc`，命令进入 Ansible extra var `janusgate_adhoc_command`，playbook 固定为 `adhoc-command.yml`，队列 `job_type` 仍为 `ansible.playbook`。
- 周期任务：保存 cron 后由 `POST /api/v1/job-center/cron/dispatch` 扫描到期作业入队并推进 `next_run_at`。
- 控制台入口：侧栏「作业中心」。

## 安全约束（P0#10 不得回退）

- 队列继续只写 JSON（`payload_format=json`），禁止 pickle / 任意 Python 对象派发。
- 敏感键（password / token / secret / private_key 等）在作业定义、变量和入队载荷上 fail-closed。
- 非 admin 不能把 `run_as_user_id` 设成他人。
- 响应与执行记录不返回凭据明文或 Ansible stdout。

## 主要 API

- `GET/POST /api/v1/job-center/jobs`
- `PATCH/DELETE /api/v1/job-center/jobs/{job_id}`
- `POST /api/v1/job-center/jobs/{job_id}/run`
- `GET /api/v1/job-center/jobs/{job_id}/runs`
- `POST /api/v1/job-center/adhoc`
- `POST /api/v1/job-center/cron/dispatch`
