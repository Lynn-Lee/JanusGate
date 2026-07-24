# 数据脱敏规则（#t65）

#t65 ACL 体系的数据脱敏规则：对会话命令输出 / 数据库查询结果中的敏感数据打码。应用统一由 `backend/app/policy/decision.py` 的 `PolicyDecisionService.mask` 承担（#t65「不得旁路」约束），与命令过滤 ACL 共享 `subject/asset/account` 选择器与优先级范式。本能力是 #t71 数据库协议代理「SQL 语句级审计并接入命令事件、与数据脱敏规则联动」的前置依赖。

## 数据模型（`backend/app/models/acl.py`）

**`DataMaskingRuleModel`（`data_masking_rules` 表）**：

- `priority`：应用顺序，**小者先应用**（同 BaseACL 惯例）。
- `match_type`：`regex`（按正则）或 `keyword`（按字面子串，内部转义）。
- `patterns_json`：JSON 字符串数组，存正则或关键字。
- `mask_method`：`full`（整体替换为 `placeholder`）或 `partial`（保留前后缀、中间打码）。
- `keep_prefix` / `keep_suffix`：`partial` 保留的前 / 后字符数。
- `placeholder`：`full` 的替换占位符（默认 `***`）。
- `subject_ids_json` / `asset_ids_json` / `account_ids_json`：作用对象选择器，`"*"` 通配。

迁移：`backend/alembic/versions/20260724_2358-4eb764da4aab_add_data_masking_rule.py`（down_revision=`b75f09654bda`），过 sqlite 一致性门禁与真 PG16 实测。

## 应用语义（`mask`）

与命令过滤 ACL 的**首个命中即止**不同，脱敏是**转换**而非放行/拒绝决策，因此**累计应用**所有命中选择器的活跃规则：

- 收集租户内 `is_active` 且选择器匹配的规则，按 `priority` 升序（确定性）**逐条对文本全局替换**，覆盖多类敏感数据。
- 每条规则的每个匹配整体作为待打码值，按 `mask_method` 打码：
  - `full` → 替换为 `placeholder`。
  - `partial` → 保留前 `keep_prefix` / 后 `keep_suffix` 字符，中间以等长 `*` 打码；**保留长度 ≥ 值长度时整体打码**，绝不泄露原值。
- `keyword` 类型对模式做 `re.escape` 后按字面匹配；`regex` 直接用模式。
- **非法正则安全跳过**（视为不命中），绝不因配置错误抛异常打断会话；后续有效规则不受影响。
- `subject.tenant_id` 与 `resource.tenant_id` 不一致时**不应用任何规则**（返回原文，trace 记 `tenant_mismatch`），避免跨租户规则误伤。

## 输入 / 输出契约（`backend/app/policy/schemas.py`）

- 输入 `MaskingRequest`：`subject`、`resource`（资产）、`account_id`、`text`。
- 输出 `MaskingResponse`：`masked_text`、`redaction_count`（替换总次数，0 表示原样）、`applied_rule_ids`（实际产生替换的规则 ID，按应用顺序）、`explain_trace`、`audit_event_id`。

## 持久化加载（租户 scope）

脱敏规则与命令过滤 ACL 共用 `backend/app/policy/repository.py` 的加载路径：`AclRepository.list_data_masking_rules` 经 `scoped_select` 按租户加载活跃规则，`build_tenant_policy_service` 把它们接入 `PolicyDecisionService.mask`。详见[命令过滤 ACL](acl-command-filter.md)的「持久化加载」一节（同一 scope helper、同一租户过滤特性）。

## 已知边界

- 本切片交付模型 + 应用服务 + 迁移 + 租户 scope 加载器；尚未提供管理 CRUD 路由与连接器接线。连接器（#t69 SSH / #t72 K8s exec）在命令事件入库前对 `output_excerpt` 调 `mask`、以及 #t71 DB 代理对查询结果调 `mask`，为后续接线步骤。
- 打码为**幂等文本替换**，不解析结构化列语义（如「仅脱敏某数据库某列」）；列级脱敏留待与 #t71 SQL 语句解析联动的后续切片。
