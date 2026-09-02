# 命令过滤 ACL（#t65）

#t65 落地 ACL 访问控制体系的首个派生类型——**命令过滤 ACL**，以及其引用的**命令组**。判定统一由 `backend/app/policy/decision.py` 的 `PolicyDecisionService.evaluate_command` 承担，**不在连接器或路由旁路**（#t65 约束）。本切片同时确立所有 ACL 共享的 BaseACL 语义（优先级 + 动作 + 复核人）；数据脱敏规则与登录 / 资产登录 / 连接方式 overlay ACL 已复用同一判定入口。

## 数据模型（`backend/app/models/acl.py`）

- **`CommandGroupModel`（`command_groups` 表）**：一组字面命令或正则。
  - `match_type`：`command`（按**词边界**匹配字面命令名）或 `regex`（按正则 `search` 部分匹配）。
  - `patterns_json`：JSON 字符串数组，存字面命令或正则模式。
- **`CommandFilterAclModel`（`command_filter_acls` 表）**：BaseACL + 选择器 + 命令组引用。
  - `priority`：**1-100，小者优先**（BaseACL 语义）；判定按其升序取首个命中者。
  - `action`：`reject` / `accept` / `review` / `warning` / `notice` / `notify_and_warn`（`face_verify`/`face_online` 不做；`change_secret` 属账号类 ACL，不在命令过滤范围）。
  - `reviewer_subject_ids_json`：`review` 动作的复核人主体 ID。
  - `subject_ids_json` / `asset_ids_json` / `account_ids_json`：作用对象选择器，`"*"` 通配。
  - `command_group_ids_json`：本 ACL 关联的命令组 ID。

迁移：`backend/alembic/versions/20260724_2346-b75f09654bda_add_command_filter_acl.py`（down_revision=`4b6380add4e5`），过 sqlite 一致性门禁（`scripts/check-migrations.sh`）。

## 判定语义（`evaluate_command`）

命令过滤是叠加在**已授权会话**之上的精炼层——会话本身已由 `evaluate` 走 deny-by-default 授权。因此命令级判定与会话级**相反**：

- **无任何 ACL 命中时默认放行**（`accept`）。否则每个 ACL 都要显式放行才可用，不可运维。
- 按 `priority` 升序取**首个**「选择器匹配且命令命中其任一命令组」的 ACL，其动作决定归一化效果：
  - `reject` → `deny`
  - `review` → `review`（响应带 `reviewer_subject_ids`；命令复核触发工单需与 #t74 联调）
  - `accept` / `warning` / `notice` / `notify_and_warn` → `allow`，并带 `warn` / `notify` obligation
- 选择器不匹配或命令不在其命令组内的 ACL 让位给下一优先级。
- `subject.tenant_id` 与 `resource.tenant_id` 不一致直接 `deny`（`TENANT_MISMATCH`）。
- 每次判定产出 `explain_trace`（逐 ACL 记录 matched / selector_not_matched / command_not_in_groups），并入审计。

### 匹配细节

- **字面命令词边界**：`command` 类型用 `\b<pattern>\b` 匹配——`rm` 命中 `sudo rm -rf /`，但不误伤 `charmander`。
- **正则安全**：`regex` 类型用 `re.search`；**非法正则安全跳过**（视为不匹配），绝不因配置错误抛异常打断会话。
- 仅 `is_active` 的 ACL 与命令组参与判定。

## 输入 / 输出契约（`backend/app/policy/schemas.py`）

- 输入 `CommandDecisionRequest`：`subject`、`resource`（资产）、`account_id`、`command`、`context`。
- 输出 `CommandDecisionResponse`：`effect`（`allow`/`deny`/`review`）、`action`（命中 ACL 原始动作，无命中为 `accept`）、`reason_code`、`matched_acl_id`、`matched_command_group_id`、`reviewer_subject_ids`、`obligations`、`explain_trace`、`audit_event_id`。

## 持久化加载（租户 scope）

`backend/app/policy/repository.py` 的 `AclRepository` 把命令过滤 ACL、命令组、数据脱敏规则从数据库按租户加载，`build_tenant_policy_service(session, actor_scope)` 装配出一个已加载对应租户规则、可直接 `evaluate_command` / `mask` 的 `PolicyDecisionService`。

- **强制走 scope helper**：所有加载查询经 `app/tenancy/scope.py` 的 `scoped_select`（落实 #t64「授权查询强制走 scope helper」、关闭 P2#9）。ACL 模型只带 `tenant_id`、不带 org/team/project 列，故 `scoped_select` 对其只施加租户过滤——正是「装载某租户全量 ACL 供判定」所需，不会被调用者的 org/team 子范围误缩小。
- 仅加载 `is_active` 记录；失活 ACL / 命令组不参与判定。

## 已知边界

- 管理 CRUD：命令过滤 ACL 与数据脱敏规则见本页与[数据脱敏规则](acl-data-masking.md)；登录 / 资产登录 / 连接方式 overlay ACL 的 CRUD 已落地（跨租户 get/update/delete 一律 404 fail-closed）。`GET/POST/PATCH/DELETE /api/v1/command-filter-acls/`（命令组作为 ACL 内嵌写入面一并持久化）。
- 执行前守卫：SSH exec、SSH PTY、K8s exec 落到远端之前走 `CommandPolicyGuard` → `PolicyDecisionService.evaluate_command`。`DENY` 与 `REVIEW`（#t74 前按 DENY）不落远程。生产组装按会话租户经 `AclRepository` / `build_tenant_policy_service` 加载规则；无 ACL 命中仍 overlay 放行。库连不上 fail-closed（`COMMAND_POLICY_STORE_UNAVAILABLE`），拒绝写 #t61，命令不落远端。
- 入库：`session_recordings` 落库前同样 `evaluate_command` + `mask`。`DENY` / `REVIEW` / evaluate 失败 → 403、#t61（只存 `command_sha256`，不落明文），不持久化命令事件。
- 命令复核触发工单需与 #t74 联调。命令过滤只作用于**语句/命令**粒度；数据库 SQL 语句级过滤与列级脱敏待 #t71。
