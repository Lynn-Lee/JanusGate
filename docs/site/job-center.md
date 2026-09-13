# 作业中心（#t77）

作业中心提供 Playbook 登记、可重复作业、临时命令、参数变量、周期调度与执行记录。复用 #t52 已建立的 JSON-only Redis Streams 队列与 Ansible runner，**不引入 pickle**（P0#10 不得回退）。

## 数据模型

- **`OpsPlaybook`（`ops_playbooks`）**：只登记官方 playbook root 内的相对 `.yml/.yaml` 路径，不入库正文或凭据。
- **`OpsJob`（`ops_jobs`）**：`playbook` 绑定目录项；`adhoc` 走内置 `janusgate-adhoc.yml`。执行身份是租户内 active SSH 账号（runas）。
- **`OpsJobVariable`（`ops_job_variables`）**：作业参数默认值，入库前必须经过 sanitizer。
- **`OpsJobExecution`（`ops_job_executions`）**：一次执行的规格与状态。队列 payload **只引用本表 id**。

## 安全约束

- 队列 `job_type=ops.job`，payload 仅 `{"execution_id": int, "check_mode": bool}`。命令、变量、资产列表、runas 从执行记录加载。
- extra vars 仅标量；拒绝 `password`/`token`/`secret` 等敏感键、`janusgate_` 前缀与 Jinja（`{{` `{%` `{#`）。
- inventory 最多带 `ansible_user`；凭据不进队列、argv 或 runner 环境。extra vars 通过临时 `--extra-vars @file.json` 传递，不把命令拼进 argv。
- 临时命令入队前走 `PolicyDecisionService.evaluate_command`。**DENY 与 REVIEW 均拒绝**（`OPS_COMMAND_DENIED` / `OPS_COMMAND_REVIEW_REQUIRED`）；本切片不开命令复核工单。
- Playbook 路径禁止绝对路径、`..` 与非 YAML 后缀。

## API

前缀 `/api/v1/ops/`，权限复用 `automation:read` / `automation:write` / `admin`。

- `GET/POST /ops/playbooks`
- `GET/POST /ops/jobs`
- `POST /ops/jobs/{id}/run`
- `POST /ops/adhoc`
- `GET /ops/executions`
- `POST /ops/scheduler/tick`：扫描当前租户到期 cron 作业并入队（5 段 cron，UTC）

## 控制台

控制台「作业中心」页可登记 Playbook、创建作业、下发临时命令、查看执行记录，并手动触发周期扫描。
