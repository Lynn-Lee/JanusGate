# JanusGate Phase 1 收口基线报告

> 基线日期：2026-06-30  
> 基线分支：`origin/dev`  
> 基线提交：`14b36b07c feat: add clean session gateway`  
> 基线标签：`phase1-baseline`  
> 协作原则：共享 Git 仓库为唯一协作源

## 1. 收口结论

JanusGate Phase 1 已达到收口条件：核心后端骨架、安全基座、身份认证、资产管理、策略决策、连接器协议、凭据 Vault、Session Gateway、审计/SIEM、DevOps 基线、测试覆盖和首轮安全 review 均已完成并合入 `dev`。

当前未完成项仅剩 `#t8 Workflow / JIT 审批流模块`，该项已明确后移到 Phase 2，不阻塞 Phase 1 基线封版。

## 2. 已完成范围

| 任务 | 模块 | 状态 | 说明 |
|---|---|---:|---|
| #t2 | 安全基座 / Identity / Auth / Inventory | done | 登录、2FA、API Key、用户、资产、平台与连接测试基础能力 |
| #t3 | PolicyDecisionService | done | deny-by-default、MFA/审批/租户/connector trust、explain/audit/obligations |
| #t4 | Connector API v2 | done | connector 注册握手、enrollment token、短期 token、inactive/deny 拒绝 |
| #t5 | Credential Vault / SecretProvider | done | AES-GCM、明文不持久化、revoke/rotate 语义 |
| #t6 | Session Gateway | done | 会话创建、策略校验、短期连接 token、会话状态流转 |
| #t7 | Audit / SIEM | done | 审计事件、append-only sequence/hash chain、SIEM 投递、敏感 metadata 脱敏 |
| #t9 | CI/CD / Docker / Compose / Helm | done | CI、Docker/Compose、Helm、发布回滚基线 |
| #t10 | QA 风险测试矩阵 | done | 风险矩阵与质量门禁 |
| #t11 | Smoke / 安全回归 | done | 非 Docker 门禁与容器级 smoke 均通过 |
| #t12 | 首轮代码审查门禁 | done | P1 review 队列已清空 |
| #t15-#t24 | Review P1 修复与覆盖率补测 | done | 安全、测试、部署相关阻断项已关闭 |

## 3. 核心能力基线

### 3.1 安全与认证

- 使用 FastAPI + Pydantic + SQLAlchemy async 作为新后端基础。
- 密码哈希使用安全哈希策略。
- JWT access / refresh token 包含 `jti` / `iat` 并支持黑名单校验。
- 当前用户校验查库并拒绝禁用用户、改密前旧 token、黑名单 token。
- MFA 登录使用独立 `mfa` challenge token，并通过 Redis `SET NX EX` 做原子一次性消费。
- API Key 使用哈希存储与校验，不持久化明文。
- CORS、配置、安全头等基础设施已纳入安全基座。

### 3.2 资产与连接测试

- Asset / Platform 模型与基础 API 已完成。
- `/api/v1/assets/platforms` 静态路由优先级已覆盖回归测试，避免被 `/{asset_id}` 遮蔽。
- 连接测试默认限制为登记资产或显式 allowlist。
- SSRF 防护覆盖私有地址、loopback、link-local、reserved、unspecified、hostname 解析等场景。
- hostname 连接使用已校验 resolved public IP，避免二次 DNS 解析导致 DNS rebinding。

### 3.3 策略决策

- PolicyDecisionService 采用 deny-by-default。
- 支持租户、主体、资产、连接器信任、MFA、审批等上下文判断。
- 输出包含 decision、reason、obligations、audit/explain 信息。
- 当前为 Phase 1 策略骨架；生产级持久化策略和复杂策略语言留待后续增强。

### 3.4 Connector API v2

- 支持 connector 注册、握手、enrollment token 校验、短期 token 签发。
- enrollment token 已改为 digest 存储，并支持一次性、过期、绑定校验。
- inactive connector 与 policy deny 均 fail-closed。
- 当前为协议与注册握手骨架；生产级 mTLS、key rotation、connector attestation 留待后续增强。

### 3.5 Credential Vault

- SecretProvider 接口与本地 AES-GCM provider 已完成。
- 明文 secret 不持久化。
- 篡改密文会失败。
- 支持 revoke / rotate 语义。
- 当前为 Phase 1 provider 抽象；生产级 KMS/HSM、审批后 unwrap、break-glass 控制留待后续增强。

### 3.6 Session Gateway

- 已提供 Session Gateway 最小闭环：创建会话、策略决策、短期 connection token、会话状态流转。
- 会话创建必须 policy allow 并签发短期 token。
- policy deny 不 consume token。
- token 过期、主体/资产/账号/connector mismatch 均 fail-closed。
- 请求体 `client_ip` 不进入策略上下文，使用 `Request.client.host`，默认不信任 `X-Forwarded-For`。
- 当前是内存 SessionStore / 依赖注入骨架；真实 Connector/Vault/审计持久化接入后需端到端二次 review。

### 3.7 Audit / SIEM

- 支持审计事件 API、append-only sequence/hash chain、查询与 SIEM 投递。
- SIEM 投递失败不阻断主流程，并记录补偿时间。
- metadata 脱敏覆盖 `authorization`、`cookie`、`credential`、`credentials`、`ssh_key` 以及包含 password/passwd/secret/token/authorization/cookie/credential 的键名。
- 创建响应、查询结果、SIEM 投递 payload 均有回归测试确保不出现敏感明文。

### 3.8 DevOps / 部署

- CI workflow、Dockerfile、docker-compose、Helm chart、部署说明已建立。
- Helm 不再将数据库凭据放入 ConfigMap。
- 支持 existingSecret 边界，ConfigMap 不包含 `DATABASE_URL` / DSN。
- DevOps 验证包括 YAML、Helm lint/template、ruff、mypy、pytest、bandit、pip-audit。
- 容器级 smoke 已通过：backend Docker build、Compose 拉起 postgres/redis/backend healthy、`/health` 返回正常。

## 4. 验证基线

Phase 1 收口前最近一轮合入验证：

```bash
ruff check app tests
mypy app
pytest -q
```

验证结果：

- `ruff check app tests`：通过
- `mypy app`：通过
- `pytest -q`：74 passed
- 容器 smoke：backend build 成功，Compose healthy，`/health` 返回 `{"status":"ok","version":"0.1.0"}`

## 5. 已关闭的关键风险

| 风险 | 处理结果 |
|---|---|
| MFA challenge 可被当 access token 使用 | 独立 `type=mfa`，current_user 拒绝 |
| MFA challenge 可重复使用 | Redis `SET NX EX` 原子一次性消费 |
| JWT 不可撤销/不查库 | `jti` blacklist + current_user 查库 + 改密时间校验 |
| refresh token blacklist 缺失 | refresh endpoint 已查 Redis blacklist |
| Asset/Platform API 权限缺失 | 接入权限门禁 |
| SSRF / DNS rebinding | 资产/allowlist 限制 + resolved public IP 建连 |
| Connector enrollment token 明文/可复用 | digest 存储 + 一次性/过期/绑定 |
| Session client_ip 被请求体污染 | 使用 `Request.client.host`，默认不信任 XFF |
| Helm ConfigMap 暴露 DB 凭据 | 凭据移入 Secret/existingSecret 边界 |
| Audit/SIEM 泄露 token/cookie/credential | metadata 敏感键脱敏并补测试 |
| stale 分支反向回退主线 | 已要求重建最小分支，最终 #t6/#t7 已干净合入 |

## 6. 残余风险与后续增强

这些不是 Phase 1 阻断，但进入生产级之前需要在后续阶段继续推进：

1. **Workflow/JIT 审批流**：尚未实现，已作为 #t8 / Phase 2。
2. **策略持久化与复杂策略语言**：当前 PolicyDecisionService 是 Phase 1 骨架，需要接入持久化策略、策略版本、灰度和回滚。
3. **Connector 生产级信任链**：mTLS、connector attestation、key rotation、连接器升级策略需 Phase 2/3 设计。
4. **Vault 生产级后端**：需要接入 KMS/HSM/云 Vault，并设计审批后 unwrap 与 break-glass 控制。
5. **Session Gateway 持久化与真实通道**：当前为内存 store / 依赖注入骨架，需要接入真实 connector、Vault、审计持久化和会话录制。
6. **审计投递可靠性**：SIEM 投递失败当前不阻断并记录补偿时间，后续需补可靠队列、重试、死信与告警。
7. **部署运行时安全**：需要补充生产环境 secret 管理、镜像签名、SBOM、漏洞扫描门禁和运行时监控。
8. **前端与产品体验**：Phase 1 主要完成后端与基础设施，前端控制台、审批界面、审计检索体验仍需后续立项。

## 7. Phase 2 建议

Phase 2 建议从 #t8 Workflow / JIT 开始，先做 PRD 与架构设计，不直接写代码。

建议拆分：

1. **Workflow/JIT PRD**：申请场景、审批角色、授权时长、撤销规则。
2. **审批状态机**：draft / pending / approved / rejected / expired / revoked。
3. **Policy 接入**：策略命中时触发审批，审批通过后生成临时授权。
4. **Session Gateway 接入**：高权限会话必须绑定有效 JIT grant。
5. **Audit/SIEM 扩展**：申请、审批、授权、使用、撤销全链路审计。
6. **通知机制**：审批通知、超时提醒、结果通知。
7. **安全 Review**：越权审批、防重放、审批人权限、审批过期、最小授权。

## 8. 基线操作建议

1. 将当前 `origin/dev` 作为 Phase 1 基线。
2. 打 tag：`phase1-baseline`。
3. 后续 Phase 2 从该 tag / 当前 dev 继续开发。
4. Phase 2 开工前，先确认 #t8 的 PRD 与架构设计文档。
