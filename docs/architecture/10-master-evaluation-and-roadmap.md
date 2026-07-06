# JanusGate 主基线文档：评估、架构与研发总计划

> **本文档是 JanusGate 项目后续推进的唯一权威方案。**
> 它合并了 `00-final-evaluation.md`（v1.0）与 `09-jumpserver-reassessment-2026-07.md` 的全部评估结论，
> 基于项目当前实际代码状态（Phase 1-2 已完成），定义了从 Phase 3 到 Phase 5 的完整研发任务计划。
> 后续所有架构决策、任务拆分、验收标准均以本文档为依据。
>
> - 评估基线：jumpserver `dev` 分支提交 `87f3b2b`（2026-07-01）
> - JanusGate 基线：`feature/p3-e2e-backend-fixes-mac` 分支，提交 `f075a1628`
> - 文档版本：v2.0
> - 日期：2026-07-02
> - 编制：opencode-glm-5.2

---

## 目录

1. [执行摘要](#1-执行摘要)
2. [评估基线与方法](#2-评估基线与方法)
3. [JumpServer 问题全清单](#3-jumpserver-问题全清单)
4. [JanusGate 当前实现状态](#4-janusgate-当前实现状态)
5. [目标定位与核心原则](#5-目标定位与核心原则)
6. [目标架构](#6-目标架构)
7. [技术选型](#7-技术选型)
8. [领域模型与 Bounded Context](#8-领域模型与-bounded-context)
9. [安全基线设计](#9-安全基线设计)
10. [核心子系统设计](#10-核心子系统设计)
11. [研发路线图与任务计划](#11-研发路线图与任务计划)
12. [验收标准与质量门禁](#12-验收标准与质量门禁)
13. [团队分工与协作规则](#13-团队分工与协作规则)

---

## 1. 执行摘要

### 1.1 核心判断

JumpServer v4.0 是功能最完整的开源 PAM 平台，但**不适合作为 JanusGate 的直接二次开发底座**。原因：

1. **安全默认不安全**：20 个 Critical/High 级别安全问题（CSRF 可绕过、AES-ECB 凭据加密、SSH 弱算法、Celery pickle RCE、OIDC 默认关 SSL 等）
2. **架构边界退化**：common 模块 189 文件大杂烩、conf.py 1159 行、xpack 100+ 条件判断侵入核心
3. **工程实践缺失**：测试覆盖 0.15%、CI 无门禁、51 处 except:pass、dependabot 禁用
4. **供应链风险**：5 个私有 fork 无签名、itsdangerous 被弃用 API 锁死、passlib 停维、Python 3.14 过于激进

### 1.2 重构路线

**参考 JumpServer 的业务功能与领域经验，不复制旧代码，重新设计 JanusGate 的产品边界、安全模型、策略引擎、连接器协议和审计体系。**

JanusGate 定位为**面向企业和云原生环境的策略驱动 PAM / 零信任访问网关**。

### 1.3 当前进度

| 阶段 | 状态 | 关键产物 |
|------|------|----------|
| Phase 0：评估与重构决策 | ✅ 完成 | 本文档 + 00-09 系列文档 |
| Phase 1：基础重构基线 | ✅ 完成 | FastAPI 后端 + 安全基座 + 核心领域模型 + CI/CD |
| Phase 2：Workflow/JIT 审批流 | ✅ 完成 | JIT 申请/审批/Grant + Policy/Session/Audit 接入 |
| **Phase 3：产品化 MVP** | **✅ MVP Go** | 前端控制台 + E2E 主链路 + 部署收口 + QA Go/No-Go |
| Phase 4：企业级能力增强 | 进行中 | 多租户 + 凭据轮换 + SSH CA + 会话录制 |
| Phase 5：生产化与商业化 | 进行中 | 高可用 + 合规报表 + 性能压测 + License/Edition 边界 + 文档站 |

### 1.4 第一闭环

```
用户登录 → 策略决策 → 资产授权 → 短时连接 token
  → Connector 建立 SSH 会话 → 命令审计
  → 会话结束 → 审计事件不可抵赖落库
```

---

## 2. 评估基线与方法

### 2.1 评估对象

- **JumpServer**：`https://github.com/jumpserver/jumpserver.git`，`dev` 分支，提交 `87f3b2b`（2026-07-01）
- 代码规模：`apps/` 下 1,338 个 Python 文件，107,867 行
- 评估维度：安全漏洞、产品设计、架构技术栈、代码质量、依赖供应链（5 维度并行深度审计）

### 2.2 与上次评估的差异

JumpServer 上游在 2026-06-26 → 07-01 期间进行了重大技术栈升级：

| 维度 | 上次评估（ec9e76e） | 本次评估（87f3b2b） | 影响 |
|------|---------------------|---------------------|------|
| Django | 4.1（EOL，5 CVE 含 2 SQL 注入） | 5.2.13（LTS） | ✅ Django 层 CVE 已消除 |
| Python | 未明确 | >=3.14 | ⚠️ 新风险：部署门槛极高 |
| 依赖管理 | poetry | uv + uv.lock | 改善，但 dependabot 禁用 |
| 测试 | 9 文件/0.7% | 17 文件/0.15% | ⚠️ 仍近乎为零 |

---

## 3. JumpServer 问题全清单

> 合并 `00-final-evaluation.md` 与 `09-jumpserver-reassessment-2026-07.md` 的全部发现，统一分级，去重编号。

### 3.1 P0 — Critical（必须立即规避，共 20 项）

| # | 维度 | 问题 | 证据 | 来源 |
|---|------|------|------|------|
| 1 | 安全 | **SECRET_KEY / BOOTSTRAP_TOKEN 默认空字符串** | `conf.py:186-187` | 00 |
| 2 | 安全 | **BOOTSTRAP_TOKEN 非恒定时间比较（timing attack）** | `permissions.py:51` `==` 比较 | 00+09 |
| 3 | 安全 | **CSRF 可被 DOMAINS=\* 全局绕过** | `middleware.py:23,207-211` `IGNORE_CSRF_CHECK` | 09 |
| 4 | 安全 | **ALLOWED_HOSTS=['\*'] + USE_X_FORWARDED_HOST=True** | `base.py:90` / `libs.py:128` | 00+09 |
| 5 | 安全 | **CSRF_TRUSTED_ORIGINS=['http://\*','https://\*']** | `base.py:96` | 00+09 |
| 6 | 安全 | **Session/CSRF Secure Cookie 默认 False** | `conf.py:677-679` | 00+09 |
| 7 | 安全 | **Dockerfile SSH 弱配置（MITM + 降级）** | `Dockerfile:50` StrictHostKeyChecking no + aes128-cbc + diffie-hellman-group1-sha1 + ssh-rsa | 00+09 |
| 8 | 安全 | **AES-ECB 凭据加密 + SECRET_KEY 复用** | `rsa_aes.py:37-62,113-119` 默认 key=SECRET_KEY | 00+09 |
| 9 | 安全 | **SM4-ECB 配置文件加密** | `conf.py:88-116` `crypt_ecb` | 00+09 |
| 10 | 安全 | **Celery pickle 序列化（反序列化 RCE）** | `libs.py:206-209` | 00+09 |
| 11 | 安全 | **TempToken 可暴力破解且缺少限流** | `authentication/backends/token.py:17-18` | 00 |
| 12 | 安全 | **OAuth2 回调 state 校验不完整 + CORS:\* 泄露 client_id** | `oauth2/views.py:53-79` / `oauth2_provider/views.py:39,74` | 00 |
| 13 | 安全 | **SuperConnectionToken 对象级授权越权风险** | `connection_token.py:748-824` | 00 |
| 14 | 安全 | **OIDC 默认关闭 SSL 证书校验 + monkey-patch** | `conf.py:367` 默认 True / `oidc/decorator.py:24-62` 全局 verify=False | 09 |
| 15 | 安全 | **私钥以明文写入 tmp 目录** | `accounts/models/base.py:139-155` 文件名仅 md5 | 09 |
| 16 | 安全 | **sshpass 命令行传递明文密码** | `inventory.py:95-97` `sshpass -p password` | 09 |
| 17 | 安全 | **Paramiko 全局 AutoAddPolicy（主机密钥不校验）** | `remote_client.py:135` / `sftp.py:27` / `manager.py:28,30` / `inventory.py:81` | 09 |
| 18 | 依赖 | **itsdangerous 1.1.0 被弃用 API 反向锁死** | `encode.py:16-19` TimedJSONWebSignatureSerializer | 09 |
| 19 | 依赖 | **passlib 停止维护拖累 bcrypt 升级** | `encode.py:240` passlib 1.7.4（5 年无 release） | 09 |
| 20 | 依赖 | **5 个私有 fork 无签名无 SBOM** | `pyproject.toml:202-206`（2 个在个人账号 ibuler） | 09 |

### 3.2 P1 — High（重构第一阶段必须解决，共 15 项）

| # | 维度 | 问题 | 证据 | 来源 |
|---|------|------|------|------|
| 1 | 安全 | RSA 使用 PKCS1v1.5 填充（padding oracle） | `rsa_aes.py:175-196` | 09 |
| 2 | 安全 | DES/低轮次密码哈希残留 | `encode.py:239-261` des_crypt + sha512 rounds=5000 | 09 |
| 3 | 安全 | ServiceAccountSignature AES-ECB 时间戳签名（无 nonce 可重放） | `permissions.py:61-90` | 09 |
| 4 | 安全 | SSRF：download_file 无超时无校验 | `file.py:46-51` + OIDC/OAuth2 多处无 timeout | 09 |
| 5 | 安全 | from_pyfile 使用 exec 加载配置 | `conf.py:1008-1010` | 09 |
| 6 | 安全 | SAML2/OAuth2/Flower 大面积 csrf_exempt | `saml2/views.py:305` / `oauth2_provider/views.py:13` / `celery_flower.py:14` | 00+09 |
| 7 | 安全 | SSO 回调 next_url 校验弱（开放重定向） | `sso.py:76-78` `startswith('/')` 可被 `//evil.com` 绕 | 09 |
| 8 | 安全 | 默认弱密码策略（min_length=6，复杂度全 False） | `conf.py:618-623` | 09 |
| 9 | 安全 | LDAP 密码缓存到 Redis | `users/models/user/_auth.py:279-306` | 00 |
| 10 | 产品 | common 模块大杂烩（189 文件/16,471 行，含 xpack 逻辑） | `apps/common/` | 09 |
| 11 | 产品 | xpack 100+ 条件判断侵入 20+ 核心文件 | orgs/users/acls/terminal/assets/accounts/common/rbac 等 | 00+09 |
| 12 | 产品 | 部署复杂度高（多组件 + 200+ 配置 + 6 步启动） | `jms` / `Dockerfile` | 09 |
| 13 | 架构 | 4 种并发模型并存（eventlet+celery+channels+daphne） | `pyproject.toml:90,93,117,170` | 09 |
| 14 | 代码 | 测试覆盖 0.15%（17 空壳文件 vs 107,867 行业务代码） | `apps/*/tests.py` | 00+09 |
| 15 | 代码 | CI 无 lint/test/安全扫描 + dependabot 禁用 | `.github/workflows/` + `dependabot.yml.bak` | 09 |

### 3.3 P2 — Medium（中长期治理，共 18 项）

| # | 维度 | 问题 | 证据 |
|---|------|------|------|
| 1 | 产品 | terminal 模块职责过多（114 文件/9,988 行/7+ 职责） | `apps/terminal/` |
| 2 | 产品 | ops 与 assets/automations 职责重叠（循环依赖风险） | `ops/mixin.py` ↔ `assets/automations/base.py` |
| 3 | 产品 | i18n 翻译耦合在 Core（139,559 行外部组件翻译） | `apps/i18n/` |
| 4 | 产品 | settings 与 conf.py 配置职责分裂（1159 行 vs DB 配置） | `conf.py` / `apps/settings/` |
| 5 | 产品 | 前后端分离不彻底（翻译耦合 + 静态资源代理） | `urls.py:85` / `apps/i18n/` |
| 6 | 产品 | ORG 隔离 Root 组织无过滤（跨 org 数据泄露风险） | `orgs/utils.py:144-146` |
| 7 | 产品 | ORG 信号处理链过长（新模型需手动注册缓存刷新） | `orgs/signal_handlers/` |
| 8 | 架构 | 多数据库驱动并存（mysqlclient+psycopg2+pymssql+oracledb+vastbase） | `pyproject.toml:105,106,138,191,198` |
| 9 | 架构 | 174 处 objects.all() 无过滤（N+1/OOM 风险） | `reports/mixins.py` / `notifications.py:327` |
| 10 | 架构 | 基类 null=True 反模式全局传播（705 处） | `common/db/models.py:31-35` |
| 11 | 架构 | 自造加密轮子（gmssl 纯 Python 1057 行） | `common/utils/gmssl_python.py` |
| 12 | 代码 | 51 处 except:pass + 37 处裸 except + 307 处 except Exception | 认证/配置/DB 路径 |
| 13 | 代码 | 255 处 print() 代替日志 | `assets/automations/base/manager.py` 20+ 处 |
| 14 | 代码 | Prometheus metrics 端点输出非法格式（`##` 双井号） | `terminal/utils/components.py:113,128,148` |
| 15 | 代码 | 无类型检查（4.7% 函数有注解）+ 438 Mixin + 100+ import * + 4 monkey-patch | 全量统计 |
| 16 | 代码 | 巨型 migration（112 个文件，最大 2223 行内嵌 JSON+RunPython） | `apps/assets/migrations/` |
| 17 | 依赖 | uvicorn 0.22.0 / websockets 11.0.3 落后约 2 年 | `pyproject.toml:97-98` |
| 18 | 依赖 | 7+ 弃用库残留（future/enum-compat/six/olefile/unicodecsv/msrestazure/adal） | `pyproject.toml` |

### 3.4 JumpServer 的积极方面（可借鉴）

1. 功能广度完整：资产/账号/认证/审计/自动化/RBAC/工单/多组织
2. 配置体系可定制项丰富
3. 凭据查看需 MFA 二次确认
4. Ansible Jinja2 注入防护
5. OAuth2 Provider 强制 PKCE
6. RBAC 权限收敛（不走 is_superuser 直通）
7. shell=True 禁用

### 3.5 商业 PAM 能力差距（JumpServer 缺失）

| 能力 | JumpServer 状态 | JanusGate 目标 |
|------|----------------|----------------|
| JIT 即时权限 | 无 | Phase 2 已实现 |
| 统一策略决策 + explain | 分散判断 | Phase 1 已实现 |
| SSH CA / 临时证书 | 无 | Phase 4 |
| 连接器零信任（独立身份） | 共享 BOOTSTRAP_TOKEN | Phase 1 已实现 |
| 审计链式哈希 + WORM | 无 | Phase 1 已实现，Phase 5 增强 |
| 会话全文搜索 | 无 | Phase 4 |
| Webhook/API 集成 | 无 | Phase 4 |
| Vault 后端（KMS/HSM） | 不完整 | Phase 1 骨架，Phase 4 生产级 |

---

## 4. JanusGate 当前实现状态

> 基于 `feature/p3-e2e-backend-fixes-mac` 分支，提交 `f075a1628`，2026-07-02 核实。

### 4.1 代码规模

| 指标 | 数值 |
|------|------|
| 后端 Python 文件 | 41 |
| 后端代码行数 | 4,993 |
| 测试文件 | 17 |
| 测试代码行数 | 4,533 |
| 测试/代码比 | 91%（远超 JumpServer 的 0.15%） |

### 4.2 已实现模块

| 模块 | 文件 | 行数 | 能力 |
|------|------|------|------|
| **核心基础设施** | `core/config.py` | 64 | pydantic-settings，fail-closed SECRET_KEY 校验（<32 字符拒绝启动） |
| **安全基座** | `core/security.py` | 117 | bcrypt(rounds=12) 密码哈希、JWT access/refresh/mfa token(jti)、AES-256-GCM 字段加密 |
| **数据库** | `core/database.py` | 41 | SQLAlchemy async + asyncpg |
| **异常处理** | `core/exceptions.py` | 41 | 统一 ErrorResponse（code/message/detail/request_id） |
| **依赖注入** | `core/deps.py` | 106 | current_user 查库、JWT 黑名单、MFA token 拒绝 |
| **Identity/Auth** | `api/auth.py` | 238 | 登录、MFA challenge（Redis SET NX EX 原子消费）、API Key 哈希存储 |
| **Inventory** | `api/assets.py` + `models/asset.py` | 160 | Asset/Platform 模型与 API、SSRF 防护、DNS rebinding 防护 |
| **PolicyDecisionService** | `policy/decision.py` | 181 | deny-by-default、MFA/审批/租户/connector trust 校验、explain_trace、obligations |
| **Connector API v2** | `connectors/registry.py` | 153 | enrollment token digest 存储、一次性消费、短期 jgt_ token、policy deny fail-closed |
| **Credential Vault** | `vault/provider.py` | 94 | AES-GCM(32字节 key + 12字节 nonce + secret_id 作 AAD)、revoke/rotate |
| **Session Gateway** | `api/sessions/service.py` | 634 | 会话创建、策略校验、短期 connection token、状态流转、grant 绑定 |
| **Workflow/JIT** | `api/workflows/service.py` + `workflows/repository.py` + `models/workflow.py` | 1590 | JIT 申请/审批/Grant 状态机、SQLAlchemy 持久化、防自审批、grant revoke 断连 |
| **Audit/SIEM** | `api/audits/service.py` | 171 | append-only sequence/hash chain、SIEM 投递、metadata 脱敏 |
| **CI/CD** | `.github/workflows/ci.yml` | — | ruff + mypy + pytest + bandit + pip-audit |
| **部署** | `Dockerfile` + `deploy/helm/` | — | Docker/Compose/Helm，existingSecret 边界 |

### 4.3 已关闭的关键风险

| 风险 | 处理结果 |
|------|----------|
| MFA challenge 可当 access token | 独立 type=mfa，current_user 拒绝 |
| MFA challenge 可重复使用 | Redis SET NX EX 原子一次性消费 |
| JWT 不可撤销 | jti blacklist + current_user 查库 + 改密时间校验 |
| SSRF / DNS rebinding | 资产/allowlist 限制 + resolved public IP 建连 |
| Connector enrollment token 明文/可复用 | digest 存储 + 一次性/过期/绑定 |
| Session client_ip 被请求体污染 | 使用 Request.client.host，不信任 XFF |
| Helm ConfigMap 暴露 DB 凭据 | 凭据移入 Secret/existingSecret |
| Audit/SIEM 泄露敏感信息 | metadata 脱敏 + 回归测试 |
| AES-ECB 凭据加密 | AES-256-GCM + 独立 nonce |
| Celery pickle 序列化 | 不使用 Celery（FastAPI 原生 async） |
| BOOTSTRAP_TOKEN 共享 | 每连接器独立 enrollment token |

### 4.4 残余风险与待增强

1. **多租户、账号托管与 SSH CA**：Phase 4 #t42 已建立后端 Organization/Team/Project 模型、`scoped_select()` 租户过滤基座、租户隔离的 Organization/Team/Project 管理 API、前端 `/tenancy` 只读组织结构页，以及 PolicyRule 的 organization/team/project 资源维度绑定；Phase 4 #t43 已启动 `Account` 持久化模型与租户/项目作用域账号列表、创建 API，并补 `CredentialRotation` 调度记录、账号作用域 API、到期轮换执行 worker、旧/新 secret 引用记录、completed rotation 回滚和前端 `/accounts` 账号托管/轮换调度页；Phase 4 #t44 已启动 `SshCertificateAuthority` / `SshCertificate` 后端模型、资产 SSH CA 信任配置字段、CA 管理/禁用 API、签发/撤销服务契约、临时证书签发/撤销 REST API、API 路由可用的 Vault-backed OpenSSH signer、连接器/资产侧可读取的 SSH CA trust bundle API，以及前端 `/ssh-ca` SSH CA / 临时证书入口
2. **策略持久化**：Phase 4 #t48 已启动 `ApprovalPolicyModel` 的租户隔离管理 API，`GET/POST /api/v1/workflows/approval-policies` 可创建和列出当前租户的 approval policy template，并要求 `workflow:admin` 或 `admin` 权限；`PolicyDecisionService` 已可接收 approval policy template，并把匹配 selector 且落入 deterministic rollout bucket 的请求 fail-closed 转为 `APPROVAL_REQUIRED` obligations；`POST /api/v1/workflows/approval-policies/simulate` 已可在当前租户内复用同一决策引擎做策略模拟；approval policy family/version 基础已完成，`POST /api/v1/workflows/approval-policies/{policy_id}/versions` 可在当前租户内创建递增版本并停用旧 active 版本，列表与模拟默认只读取 active/latest 版本；`POST /api/v1/workflows/approval-policies/{policy_id}/rollback` 已可在当前租户内显式回滚到同 family 指定版本；`rollout_percentage` 灰度百分比已完成；DSL `context_equals` 精确匹配、`context_in` 枚举匹配、`context_not_equals` 排除匹配和 `context_not_in` 枚举排除匹配已完成，复杂表达式与更多操作符仍待后续切片
3. **Connector 生产级信任链**：Phase 4 #t45 已启动 Connector Registry 心跳租约，connection token 签发前会 fail-closed 校验 active 状态与 heartbeat TTL；mTLS 证书指纹绑定已接入 registry token 签发路径；enrollment-token 绑定的 attestation nonce/digest 已接入注册路径，缺失或不匹配 attestation 时 fail-closed；active connector public key rotation 已补齐并记录 previous/current fingerprint 与轮换时间；持久化 Connector 管理 API 已补齐租户隔离的列表/创建、heartbeat 和 key rotation；轻量 Connector SDK 已覆盖 create/heartbeat/rotate-key 并避免 SDK 异常泄露 bearer token
4. **Vault 生产级后端**：Phase 4 #t50 已启动 envelope encryption provider foundation：`KmsKeyProvider` 协议负责包装/解包每条 secret 的 data key，`EnvelopeEncryptedSecretProvider` 使用随机 32 字节 DEK 加密 secret 并保存 wrapped data key；KMS unwrap 失败时 fail-closed。Envelope provider 已支持可替换 `SecretRecordStore`，并新增 `SecretRecordModel` 与 `SqlAlchemySecretRecordStore`，可在数据库中持久化 envelope record，同时不保存凭据明文；`LocalKmsEnvelopeKeyProvider` 已提供本地 AES-GCM wrapped DEK adapter，可用 base64 形式的 32 字节 `VAULT_LOCAL_KMS_MASTER_KEY` 装配；`unwrap_after_approval()` 已提供审批后解包 guard，要求审批当前有效、携带 grant/workflow 标识，并显式绑定目标 secret；真实云 KMS/HSM/Vault adapter 与 break-glass 流程仍待后续切片
5. **Session 持久化与真实通道**：当前 session lifecycle 仍以内存 store 为主；Phase 4 #t46 已启动租户隔离的 SessionRecording 元数据与命令事件持久化，并提供录制创建、Connector 命令事件上报、录制命令时间线读取、命令检索和录制关闭 API；命令检索已补 PostgreSQL `to_tsvector` / `plainto_tsquery` 与 GIN 索引优化，SQLite 测试环境保留 `ILIKE` fallback；前端 `/sessions` 已提供按 Recording ID 加载的只读回放命令时间线入口
6. **通知/WebHook 集成**：Phase 4 #t47 已启动租户隔离的 `WebhookEndpoint` 持久化模型与 `GET/POST /api/v1/webhook-endpoints/` 管理 API；响应不返回 signing secret 明文或摘要。当前已补 `NotificationRule` 持久化模型与 `GET/POST /api/v1/notification-rules/` 管理 API，规则必须引用当前租户 active WebHook endpoint；并补 `NotificationDelivery` 队列记录与租户隔离的入队/列表 API。`NotificationDeliveryWorker` 已提供到期投递、失败重试与最大次数后 dead-letter 状态机，sender 只接收已脱敏 payload。真实外部 HTTP/IM sender 和多级审批仍待后续切片。
7. **审计投递可靠性与报表中心**：SIEM 投递失败不阻断；Phase 4 #t49 已启动当前租户审计报表汇总 API，前端 `/audits` 已展示报表总事件、高危事件和 SIEM failed 聚合卡片；Phase 5 #t54 已启动合规报表导出基础，`GET /api/v1/audits/reports/compliance` 返回当前租户指定模板的事件 ID、hash chain 起止、报告期间、报表签名、签名算法/key id 和正式 JSON 导出格式元数据且不泄露 metadata/message/resource/session 明细；后端已保留可替换 compliance report signer 边界，当前默认本地 HMAC signer，并已提供配置驱动 external HMAC signer adapter foundation，缺少外部 key id 或 signing secret 时 fail-closed；当前导出会写入 append-only WORM 归档元数据 `worm_record_id`、`worm_sequence_number` 与 `worm_content_hash`；前端 `/audits` 已提供 SOC2 合规报表 JSON 下载入口，使用后端返回的安全文件名和 vendor JSON media type，并只展示签名摘要；外部 WORM 存储和真实云 KMS/证书签章服务仍待后续切片
8. **可观测性**：Phase 4 #t51 已启动 Prometheus metrics foundation：后端 `GET /metrics` 以 Prometheus 文本格式暴露 HTTP 请求总数与延迟 histogram，标签只包含 method、路由模板 path 和 status_code；OpenTelemetry 分布式追踪、Loki 日志管道和部署层 scrape 暴露策略仍待后续切片
9. **Automation Worker**：Phase 4 #t52 已启动后台任务队列、消费循环与调度 API foundation：`AutomationJobQueue` 使用 Redis Streams 风格 `xadd` 写入 JSON-only 消息，任务类型白名单限定为 `asset.scan`、`credential.rotate` 和 `ansible.playbook`，并拒绝 password/token/secret/private key 等敏感 payload 字段；`AutomationWorker` 可通过 consumer group 读取 JSON 消息、按 job type 分发到显式 handler，并仅在 handler 成功后 ack；`POST /api/v1/automation/jobs/asset-scans` 可按当前租户调度 `asset.scan` 作业；`POST /api/v1/automation/jobs/credential-rotations` 会先按 actor scope 确认账号可见，再调度 `credential.rotate` 作业且 payload 不携带 secret；`POST /api/v1/automation/jobs/playbooks` 已可调度 `ansible.playbook` 作业且只接收 playbook 名称、目标资产 ID 列表和 check mode；`AssetScanWorkerHandler` 已按当前租户确认 active asset，并把不含 legacy credential 的目标摘要传给显式扫描执行器；`CredentialRotateWorkerHandler` 已按当前租户确认 active account，创建轮换记录并调用显式改密执行器，队列 payload 不携带 secret；`AnsiblePlaybookWorkerHandler` 已按当前租户确认 active 目标资产，并把 playbook 名称、check mode 与无凭据目标摘要传给显式 runner 契约；`LocalAnsiblePlaybookRunner` 已提供本地 `ansible-playbook` adapter、playbook root 路径收敛、临时 JSON inventory 渲染、check mode 传递、不继承 secret/token 环境变量的 runtime 目录沙箱基础、执行超时和超时子进程回收，并可通过 `ANSIBLE_PLAYBOOK_ROOT`、`ANSIBLE_RUNTIME_ROOT`、`ANSIBLE_PLAYBOOK_EXECUTABLE`、`ANSIBLE_PLAYBOOK_TIMEOUT_SECONDS` 装配；`AutomationJobRun` 已持久化 `ansible.playbook` 执行状态、message id、请求人、playbook 名称、check mode、目标数量和脱敏错误码，不保存 inventory、stdout、stderr 或 secret payload，并已补当前租户只读查询 API `GET /api/v1/automation/jobs/runs`
10. **高可用与水平扩展**：Phase 5 #t53 已启动无状态 Core 前置切片，Session connection token store 可通过 `SESSION_CONNECTION_TOKEN_STORE=redis` 切换到 Redis-backed 单次消费存储，使用 Redis TTL 与 `GETDEL` 原子消费原始 token digest，降低多副本部署对粘性会话的依赖；后端 Redis client 已支持 `REDIS_MODE=single|sentinel|cluster`，Sentinel 通过 `REDIS_SENTINEL_URLS` / `REDIS_SENTINEL_MASTER_NAME` 装配，Cluster 通过 `REDIS_CLUSTER_URLS` 装配；Helm chart 已提供可选 HPA 模板，启用 autoscaling 时要求 `config.sessionConnectionTokenStore=redis`，否则模板 fail-closed；数据库读副本 foundation 已提供可选 `DATABASE_READ_REPLICA_URL`，资产列表、资产详情、平台列表、账号列表、账号轮换列表、会话列表、会话录制命令时间线、命令检索、Tenancy Organization/Team/Project 列表、WebHook endpoint 列表、通知规则列表、通知投递列表、Connector 列表、SSH CA 列表、SSH CA trust bundle、SSH certificate 列表、Automation job run 列表、approval policy 列表、认证态用户详情 `/api/v1/auth/me`、Workflow request 列表/详情以及 active JIT grant 列表 GET 路由已接入 read session dependency；`tests/test_database_routing.py` 已补全 GET 路由 read-routing inventory，当前 audit events/summary/compliance GET 路由因仍读取进程内 audit service 被登记为显式 DB-free 例外；`scripts/phase5-ha-config-smoke.sh` 已补配置级 HA smoke，覆盖 Compose read-replica env 渲染、Helm HPA memory-store fail-closed、Redis token store + Sentinel + HPA + read-replica Secret 渲染；`scripts/phase5-k8s-multi-replica-smoke.sh` 已补真实 Kubernetes 多副本 smoke 脚本基础，可在显式提供 kube context、PostgreSQL writer/read replica、Redis 和 SECRET_KEY 时部署 Helm release、等待 rollout/ready pods 并通过 Service port-forward 校验 `/health`。
11. **性能压测与容量模型**：Phase 5 #t55 已启动容量模型 smoke foundation，`scripts/phase5-capacity-model-smoke.sh` 可用真实压测摘要或默认样例输入校验 core API p95、错误率和 `capacity_headroom_percent` SLO；`docs/performance/phase5-capacity-model.md` 记录当前 SLO、输入字段、容量余量公式与下一步真实环境压测证据要求。当前已补真实压测脚本 foundation：`scripts/phase5-core-api-load-test.sh` 可对目标环境执行认证 GET endpoint mix，并把 RPS、p95 和错误率聚合结果喂给容量模型 smoke；`docs/performance/phase5-load-test-evidence-template.json` 已固定目标环境元数据、runner 配置、容量模型结果、原始输出 artifact manifest 和脱敏边界，`scripts/phase5-load-test-evidence-smoke.sh` 已接入 CI 校验证据归档合约；真实环境运行、容量曲线和已填充证据包归档仍待后续切片。
12. **运行时安全**：Phase 5 #t56 已启动 supply-chain security foundation：CI 通过 Trivy 对仓库文件系统执行 high/critical vulnerability gate；release tag 镜像推送后为 digest 生成 SPDX JSON SBOM artifact，并通过 GitHub OIDC + Cosign keyless signing 对 backend 镜像 digest 签名；`scripts/phase5-supply-chain-security-smoke.sh` 校验 CI wiring。当前已补 runtime monitoring smoke foundation，`scripts/phase5-runtime-monitoring-smoke.sh` 校验 Compose/Helm 运行时加固、Prometheus `/metrics` 回归契约和 `deploy/monitoring/phase5-runtime-alerts.yaml` 告警规则基线；当前告警覆盖高 5xx、p95 延迟和 metrics 缺失；真实 Alertmanager 接入和目标环境告警演练仍待后续切片
13. **版本发布与回滚**：Phase 5 #t57 已启动 release readiness foundation，`scripts/phase5-release-readiness-smoke.sh` 校验后端包版本、FastAPI metadata、`/health`、Helm chart/appVersion/image tag 的语义版本一致性，并确认 release tag pipeline、Alembic 迁移检查点、迁移前备份要求和 `helm rollback` runbook 文档被 CI 门禁覆盖；真实生产发布演练、破坏性迁移演练和数据回滚恢复证据仍待后续切片
14. **License / Edition 边界**：Phase 5 #t58 已启动后端 feature flag 与 license 摘要 foundation，并已在前端 `/settings` 接入 License / Edition 摘要和最小 License lifecycle 激活表单：默认 community edition 启用基础能力；enterprise 配置必须携带验签通过且未过期的 license 才启用声明的 enterprise feature，缺失、非法、过期或签名不匹配时 fail-closed 回退 community；当前支持 HMAC-SHA256、离线 Ed25519 公钥验签与 `external-http` 外部商业授权服务验证 foundation，部署环境不需要保存签名私钥；外部验证只向 HTTPS validation endpoint 发送 opaque license key，service token 仅从环境注入，不写入 DB；`GET /api/v1/admin/license-summary` 仅允许 admin 读取 configured/effective edition、license 状态和 feature 列表，不返回 license key、签名 secret、公钥、validation token 或原始 payload；`POST /api/v1/admin/license-config` 已提供 admin-only 持久化配置 foundation，summary 优先读取 DB 中的激活配置，无记录时回退环境变量，响应仍只返回脱敏摘要；前端可提交 configured edition、verifier、license key、signing secret 或 public key，并支持选择 `external-http` verifier；`external-http` 只提交 opaque license key，validation endpoint 与 service token 仍只从环境读取；页面只展示提交后的脱敏摘要，不回显 license key、签名 secret、公钥或原始 payload；成功写入 license 配置会追加 `admin.license_config.updated` 审计事件，metadata 只保存版本、状态、verifier、密钥材料是否已配置和启用 feature 摘要，不保存商业授权密钥材料；`docs/site/fixtures/license-operations-evidence.json` 已固定 external-http 授权服务 SLA、timeout budget、fail-closed drill、目标环境、升级联系人、key custody owner、rotation cadence 与 revocation process 证据字段，并明确不得归档 license key、signing secret、validation token、私钥或原始授权 payload。真实目标环境联调仍待后续切片。

---

## 5. 目标定位与核心原则

### 5.1 产品定位

**面向企业和云原生环境的策略驱动 PAM / 零信任访问网关。**

核心叙事：让正确的人，在正确时间，以正确权限，经过正确审批和审计，安全连接正确资源。

### 5.2 核心能力

1. 身份统一认证（密码 + MFA + SSO + API Key）
2. 策略驱动授权（deny-by-default + explain + 模拟）
3. 即时权限 JIT（按需申请 → 审批 → 限时使用 → 自动回收）
4. Secret / Vault 治理（envelope encryption + KMS/HSM）
5. 多协议连接网关（SSH / RDP / K8s / DB / RemoteApp）
6. 全链路不可抵赖审计（append-only + hash chain + SIEM）
7. 连接器插件生态（独立身份 + 能力声明 + SDK）
8. 安全默认、云原生部署、可观测治理

### 5.3 核心原则

1. **安全默认 fail-closed**：生产环境必须拒绝不安全默认值
2. **策略中心化**：所有访问动作必须经过统一 PolicyDecisionService
3. **凭据最小暴露**：连接器只拿短时凭据或一次性 token
4. **连接器零信任**：每个连接器独立身份、独立密钥、独立授权范围
5. **审计不可抵赖**：审计事件追加写、链式哈希、外部投递
6. **模块化单体优先**：按领域边界设计，不盲目微服务化
7. **可测试优先**：权限、token、secret、审计必须有回归测试
8. **不从 JumpServer 搬旧代码**：JumpServer 只作 PRD 和业务路径参考

---

## 6. 目标架构

### 6.1 分层架构

| 层 | 职责 | 组件 |
|----|------|------|
| **Interface** | 对外 API 入口 | REST API、WebSocket/Streaming API、Connector API、Webhook API、Admin API |
| **Application Service** | 业务用例编排 | AuthService、InventoryService、PolicyDecisionService、SessionOrchestrator、CredentialService、AuditService、WorkflowService |
| **Domain** | 核心领域模型 | User/Identity、Asset/Resource、Account/Credential、Policy/Rule、Session、AuditEvent、AccessRequest |
| **Infrastructure** | 基础支撑 | PostgreSQL、Redis、Object Storage、KMS/Vault、SIEM/Webhook、Connector Registry |

### 6.2 当前包结构（已实现）

```
backend/app/
├── core/           # 配置、数据库、安全、异常、依赖注入
├── models/         # SQLAlchemy ORM（User、Asset、Workflow）
├── schemas/        # Pydantic DTO
├── api/            # REST 路由
│   ├── auth.py     # Identity & Auth
│   ├── assets.py   # Inventory
│   ├── sessions/   # Session Gateway
│   ├── workflows/  # Workflow/JIT
│   └── audits/     # Audit
├── policy/         # PolicyDecisionService
├── connectors/     # Connector Registry
├── vault/          # SecretProvider
├── services/       # 业务 service
└── workflows/      # Workflow repository
```

### 6.3 目标包结构（Phase 4+ 演进）

```
backend/app/
├── core/           # 配置、数据库、安全、异常、依赖注入
├── models/         # SQLAlchemy ORM
├── schemas/        # Pydantic DTO
├── api/            # REST 路由（按 Bounded Context 组织）
│   ├── auth/       # Identity & Auth
│   ├── assets/     # Inventory
│   ├── accounts/   # Account/Credential 管理
│   ├── sessions/   # Session Gateway
│   ├── workflows/  # Workflow/JIT
│   ├── audits/     # Audit
│   ├── connectors/ # Connector 管理
│   ├── notifications/ # 通知
│   └── settings/   # 系统设置
├── policy/         # PolicyDecisionService
├── connectors/     # Connector Registry + SDK
├── vault/          # SecretProvider（DB/KMS/Vault 后端）
├── services/       # 业务 service 层
├── workflows/      # Workflow engine
└── observability/  # OTel + Prometheus metrics
```

---

## 7. 技术选型

| 领域 | 选型 | 对比 JumpServer | 状态 |
|------|------|-----------------|------|
| 后端框架 | FastAPI + Pydantic v2 | 替代 Django 5.2 + DRF | ✅ 已实现 |
| ORM | SQLAlchemy 2.x async + Alembic | 替代 Django ORM | ✅ 已实现 |
| 语言 | Python >=3.12 | 替代 >=3.14（部署友好） | ✅ 已实现 |
| 数据库 | PostgreSQL（asyncpg） | 同（但移除多 DB 驱动冗余） | ✅ 已实现 |
| 缓存/短期状态 | Redis | 同（不存长期敏感凭据） | ✅ 已实现 |
| 前端 | React 19 + TypeScript + Vite + Ant Design | 替代 Lina(Vue) + 翻译耦合 | 🔄 Phase 3 |
| 加密 | AES-256-GCM / ChaCha20-Poly1305 + KMS/Vault | 替代 AES-ECB/SM4-ECB + SECRET_KEY | ✅ 已实现（本地），KMS 待 P4 |
| 密码哈希 | bcrypt(rounds=12) | 替代 passlib + des_crypt | ✅ 已实现 |
| JWT | HS256 + jti + blacklist | 替代 itsdangerous 1.x | ✅ 已实现 |
| 策略引擎 | 自研 PolicyDecisionService → 评估 OPA/Rego | 替代分散权限判断 | ✅ 骨架，持久化待 P4 |
| 连接器安全 | enrollment token + 短期 jgt_ token → mTLS | 替代共享 BOOTSTRAP_TOKEN | ✅ 骨架，mTLS 待 P4 |
| 消息队列 | Redis Streams / NATS（禁止 pickle） | 替代 Celery+pickle | 待 P4 |
| 部署 | Docker Compose + Helm/K8s | 替代多组件手动协同 | ✅ 基线 |
| 可观测 | OpenTelemetry + Prometheus + Loki | 替代非法 Prometheus + 无追踪 | 🔄 P3 metrics，OTel 待 P4 |
| CI/CD | ruff + mypy + pytest + bandit + pip-audit + Trivy + release SBOM/signing | 替代无门禁 CI | ✅ 基线，semgrep/运行时监控待 P5 |

---

## 8. 领域模型与 Bounded Context

### 8.1 Bounded Context 划分

| # | Context | 职责 | 当前状态 |
|---|---------|------|----------|
| 1 | **Identity & Auth** | 用户、组织、认证源、MFA、SSO、API Token | ✅ Phase 1 |
| 2 | **Inventory** | 资产、节点、平台、协议、标签、云同步 | ✅ Phase 1（基础） |
| 3 | **Credential Vault** | 账号、密钥、改密、推送、校验、轮换 | ✅ Phase 1（骨架） |
| 4 | **Policy & Permission** | RBAC、资产授权、ACL、条件访问、审批策略、策略模拟 | ✅ Phase 1（骨架） |
| 5 | **Session Gateway** | 连接 token、会话生命周期、连接器调度 | ✅ Phase 1 |
| 6 | **Audit & Compliance** | 登录、操作、命令、文件、录像、报表、SIEM | ✅ Phase 1 |
| 7 | **Workflow & JIT** | 工单、审批、通知、即时权限 | ✅ Phase 2 |
| 8 | **Connector Platform** | 连接器注册、能力声明、版本兼容、SDK | ✅ Phase 1（骨架） |
| 9 | **Automation Worker** | Ansible、扫描、改密等高风险任务独立队列 | 待 Phase 4 |

### 8.2 核心领域模型（已实现）

```
User ──┬── WorkflowRequest ──── JitGrant
       │         │                  │
       │         └── ApprovalPolicy  │
       │                            │
       ├── Asset ── Account ── SecretRecord
       │    │
       │    └── Platform
       │
       ├── Session ── ConnectionToken
       │
       └── AuditEvent ── (hash chain)
```

### 8.3 Phase 4+ 待新增模型

- `Organization` / `Team` / `Project`（多租户）
- `CredentialRotation`（凭据轮换任务）
- `SshCertificateAuthority` / `SshCertificate`（已启动后端模型与服务契约）
- `SessionRecording` / `CommandLog`
- `NotificationRule`（已启动后端模型与管理 API） / `NotificationDelivery`（已启动可靠投递队列基础） / `NotificationChannel`
- `WebhookEndpoint`（已启动后端模型与管理 API）

---

## 9. 安全基线设计

### 9.1 已实现安全基线（Phase 1-2）

| 安全控制 | 实现 | 对应 JumpServer P0 |
|----------|------|-------------------|
| SECRET_KEY 非空强制 | config.py enforce_secrets 校验 <32 字符拒绝启动 | P0#1 |
| Token 恒定时间比较 | enrollment token 用 sha256 digest | P0#2 |
| CSRF 防护 | FastAPI 无 Django CSRF 问题；CORS 显式配置 | P0#3,5 |
| ALLOWED_HOSTS | FastAPI 无 Host 头信任问题 | P0#4 |
| Secure Cookie | 生产环境强制 HTTPS | P0#6 |
| SSH 安全 | 资产级连接策略（待真实 Connector 接入） | P0#7 |
| 凭据加密 | AES-256-GCM + 独立 nonce + secret_id 作 AAD | P0#8,9 |
| 序列化 | 不使用 Celery/pickle | P0#10 |
| Token 防暴力 | 登录限流 5/min + 全局限流 120/min | P0#11 |
| OAuth2 | 强制 PKCE + state 校验（待 SSO 接入） | P0#12 |
| 连接器身份 | 每连接器独立 enrollment token + 短期 jgt_ token | P0#2(bootstrap) |
| 私钥管理 | 不落盘，内存传递（待真实 Connector 验证） | P0#15 |
| 密码哈希 | bcrypt(rounds=12)，无 passlib/des | P1#2 |

### 9.2 Phase 3 安全加固任务（#t39）

| # | 任务 | 对应 JumpServer 问题 |
|---|------|---------------------|
| 1 | OAuth2/OIDC SSO 接入时强制 SSL 校验，禁止 monkey-patch | P0#14 |
| 2 | SSO 回调 next_url 用 safe_next_url，拒绝 `//` | P1#7 |
| 3 | 凭据落盘策略：优先内存型 ssh-agent，禁止明文写 tmp | P0#15 |
| 4 | ServiceAccount 签名改 HMAC-SHA256 + nonce 防重放 | P1#3 |
| 5 | SSRF 防护：所有 HTTP 调用加 timeout + URL 白名单 | P1#4 |
| 6 | 审计 metadata 脱敏回归测试覆盖 | P1#6 |
| 7 | 密码策略：min_length≥12 + 强制复杂度 | P1#8 |

### 9.3 Phase 4-5 安全增强

| # | 任务 |
|---|------|
| 1 | Connector mTLS 证书绑定 + attestation |
| 2 | Vault KMS/HSM/云 Vault 后端 + 审批后 unwrap + break-glass |
| 3 | 镜像签名 + SBOM + 漏洞扫描门禁 |
| 4 | 运行时监控 + 异常检测 |
| 5 | WORM 审计存储 + 合规报表 |

---

## 10. 核心子系统设计

### 10.1 PolicyDecisionService（已实现，Phase 4 增强）

**当前**（`policy/decision.py`，181 行）：
- deny-by-default
- preflight 校验：unknown subject/resource/action、tenant mismatch、connector not trusted
- 规则匹配：MFA required、approval required（grant 过期/撤销/不匹配 deny）
- 输出：decision、reason_code、explain_trace、obligations、ttl_seconds、audit_event_id

**Phase 4 增强**：
- 持久化策略规则（ApprovalPolicyModel 已有，需接入 PolicyRule）
- 策略版本管理 + 灰度 + 回滚
- 策略模拟（dry-run）
- ABAC 条件（时间窗口、来源 IP、设备指纹、风险评分）
- 评估 OPA/Rego 作为策略语言

### 10.2 Connector API v2（已实现骨架，Phase 4 增强）

**当前**（`connectors/registry.py`，153 行）：
- enrollment token：digest 存储、一次性消费、过期/绑定校验
- 注册：验证 enrollment token + 公钥指纹格式
- connection token：connector 必须 active + policy allow → 签发 jgt_ 前缀短期 token

**Phase 4 增强**：
- SSH CA / 临时证书服务已启动后端模型、CA 管理/禁用 API、签发/撤销服务契约、临时证书签发/撤销 REST API、API 路由可用的 Vault-backed OpenSSH signer、连接器/资产侧可读取的 SSH CA trust bundle API，以及前端 `/ssh-ca` 管理入口
- mTLS 证书绑定
- key rotation
- capability reporting（协议/能力声明）
- heartbeat 与健康状态
- 审计事件批量投递
- Connector SDK

### 10.3 Credential Vault（已实现骨架，Phase 4 增强）

**当前**（`vault/provider.py`）：
- AES-GCM：32 字节 master key + 12 字节随机 nonce + secret_id 作 AAD
- create_secret / unwrap / rotate / revoke
- 篡改密文解密失败（InvalidTag）
- 内存存储（开发用）
- `KmsKeyProvider` 协议与 `EnvelopeEncryptedSecretProvider` foundation：每条 secret 生成独立 32 字节 DEK，DEK 只以 KMS-wrapped 形式随记录保存，KMS unwrap 失败时 fail-closed；provider 已支持可替换 `SecretRecordStore`，并新增 `SecretRecordModel` 与 `SqlAlchemySecretRecordStore` 持久化 adapter，不再绑定进程内 dict
- `LocalKmsEnvelopeKeyProvider` 本地 AES-GCM KMS adapter：用 32 字节 master key 加密 wrapped DEK，支持从 base64 master key 装配；wrapped key 格式带版本前缀，错误 key、损坏数据或不支持格式均 fail-closed 为 `KMS_UNWRAP_DENIED`

**Phase 4 增强**：
- 后续补真实云 KMS/HSM/Vault adapter
- 审批后 unwrap
- break-glass 控制
- 凭据轮换调度 + 双写迁移 + 回滚

### 10.4 Session Gateway（已实现，Phase 4 增强）

**当前**（`api/sessions/service.py`，634 行）：
- 会话创建必须 policy allow + 签发短期 token
- policy deny 不 consume token
- token 过期/主体/资产/账号/connector mismatch fail-closed
- JIT grant 绑定 + single-use consume
- grant revoke 关闭活跃 session
- client_ip 使用 Request.client.host，不信任 XFF

**Phase 4 增强**：
- 真实 Connector 通道接入
- 会话录制元数据管理
- 命令审计实时上报
- 文件传输审计
- 会话共享/监控

### 10.5 Workflow/JIT（已实现，Phase 4 增强）

**当前**（`api/workflows/service.py` 956 行 + `workflows/repository.py` 528 行 + `models/workflow.py` 106 行）：
- 状态机：draft→pending→approved/rejected/expired/revoked
- JitGrant：active→used/expired/revoked
- 防自审批
- SQLAlchemy 持久化（WorkflowRequestModel / JitGrantModel / ApprovalPolicyModel）
- Policy 接入：approval required → deny APPROVAL_REQUIRED + obligations
- Session 接入：grant 绑定 + single-use + revoke 断连
- Audit 接入：全链路事件

**Phase 4 增强**：
- 多级审批流
- 审批策略 DSL
- 风险评分自动审批
- 外部 ITSM 双向同步
- 真实通知（Slack/飞书/钉钉/企微）

### 10.6 Audit/SIEM（已实现，Phase 5 增强）

**当前**（`api/audits/service.py`，171 行）：
- append-only sequence + hash chain
- SIEM 投递（失败不阻断 + 记录补偿时间）
- metadata 脱敏（authorization/cookie/credential/ssh_key + password/passwd/secret/token 键名）

**Phase 5 增强**：
- 可靠队列 + 重试 + 死信 + 告警
- WORM 存储
- 合规报表
- 会话全文搜索

---

## 11. 研发路线图与任务计划

### 11.1 Phase 3：产品化 MVP（当前阶段）

**目标**：把 Phase 1/2 已完成的后端能力产品化，形成可演示、可部署、可端到端验收的 MVP。

**成功标准**：在 6 个页面内跑通 PAM 最核心闭环——登录 → 资产 → JIT 申请审批 → 会话 → 撤销断连 → 审计追踪。

#### 11.1.1 MVP 范围

**6 个页面**：
1. 登录页（用户名/密码 + MFA）
2. 资产列表页（表格 + 搜索 + 筛选 + JIT 申请入口）
3. 会话列表页（状态 + 关闭 + 审计入口）
4. Workflow/JIT 页（我的申请 + 待审批 + Active grants + 申请/审批/撤销）
5. 审计日志页（事件表格 + 筛选 + 搜索 + 详情抽屉）
6. 系统设置页（版本 + 安全配置摘要 + 部署信息）

**1 条 E2E 主链路**：
登录 → 资产列表 → 发起 JIT 申请 → 审批 → 创建会话 → 撤销 → 查看审计

**明确延后**：Dashboard、多租户 UI、通知中心、报表导出、会话录制、SSH CA、凭据轮换 UI、WebHook UI、审批策略 DSL、License 管理。

#### 11.1.2 任务分解

| 任务 ID | 任务 | Owner | 交付物 | 状态 |
|---------|------|-------|--------|------|
| **#t35** | PRD / IA / 里程碑计划 | architect | `08-phase3-mvp-prd-ia.md` | ✅ 完成 |
| **#t36** | 前端控制台骨架 | frontend-engineer | `frontend/` 工程 + 路由/Layout/API client + 登录页 + 导航 + server-backed sessions page | ✅ 完成 |
| **#t37** | API 契约补齐 | backend-engineer | `docs/api-contract.md` + OpenAPI ErrorResponse | ✅ 完成 |
| **#t38** | E2E 主链路联调 | qa-engineer | `backend/tests/test_phase3_api_smoke.py` API-level smoke + Docker/CI 环境 smoke | ✅ API-level smoke + CI deploy config/render smoke + Compose `/health` smoke 完成 |
| **#t39** | 安全加固与威胁模型复核 | security-auditor | Phase 3 安全验收矩阵已由 `docs/qa/phase3-go-no-go.md` 和安全回归测试收口；OAuth/OIDC 等未启用能力留作 Phase 4+ 威胁模型复核 | ✅ Phase 3 收口 |
| **#t40** | DevOps 收口 | devops-engineer | Docker/Compose/Helm 验证 + CI 门禁 + 发布回滚 | ✅ CI Compose config + Helm lint/template + Compose `/health` smoke 已补 |
| **#t41** | QA Go/No-Go | qa-engineer | `docs/qa/phase3-go-no-go.md` 验收矩阵 + CI 覆盖率门禁 + Go/No-Go 证据包 | ✅ 完成 |

#### 11.1.3 里程碑

| 里程碑 | 周期 | 交付 |
|--------|------|------|
| M1：PRD/IA/API 契约锁定 | 0.5 天 | ✅ 完成 |
| M2：前端控制台骨架 | 1 天 | frontend/ 工程 + 登录页 + 导航 |
| M3：核心页面切片 | 1-2 天 | Assets/Sessions/Workflow/Audits/Settings 页面 |
| M4：端到端联调与部署收口 | 1-2 天 | ✅ API-level 主链路 smoke + CI Compose/Helm 渲染门禁 + Compose `/health` smoke + QA Go/No-Go 证据包 |
| M5：Phase 3 关闭 | 0.5 天 | ✅ Phase 3 收口证据 + Phase 4 范围建议 |

#### 11.1.4 验收标准

**产品验收**：
- 6 个 MVP 页面均可访问
- API-level 主链路 smoke 可重复跑通；CI deploy config/render smoke 已覆盖 Compose 与 Helm 静态渲染；Compose `/health` smoke 可重复执行；QA Go/No-Go 证据包已收口
- 用户能明确看到申请/审批/grant/会话状态
- 审计页能追踪主链路关键事件

**安全验收**：
- 登录态失效后不能访问受保护页面
- 普通用户不能审批自己的申请
- 普通用户不能查看他人的申请/grant/会话/审计详情
- grant 过期/撤销/资源不匹配时 Session 创建 fail-closed
- revoke grant 后绑定 active session 必须关闭
- 审计 metadata 不泄露 token/密码/连接串/secret

**技术验收**：
- 后端：ruff check + mypy + pytest 通过，coverage ≥ 80%
- 前端：lint + typecheck + build 通过，关键页面 smoke 通过
- 集成：E2E 主链路通过 + Docker/Compose /health smoke + Helm template/lint 通过

---

### 11.2 Phase 4：企业级能力增强

**目标**：从 MVP 走向更完整的 PAM 产品能力。

**启动条件**：Phase 3 MVP 可演示、可部署、可回归 + QA Go/No-Go 通过。

#### 11.2.1 任务分解

| 任务 ID | 任务 | Owner | 范围 | 优先级 |
|---------|------|-------|------|--------|
| **#t42** | 多租户与组织/团队/项目维度权限 | architect + backend | 后端 Organization/Team/Project 模型 + DB 层 `tenant_id` scope helper + Organization/Team/Project 管理 API + `/tenancy` 只读控制台页 + PolicyRule 组织/团队/项目资源维度绑定已完成；后续业务接入跟随 #t43/#t44/#t45 | 高 |
| **#t43** | 资产账号托管与凭据轮换 | backend + frontend | Account 模型 + 租户/项目作用域账号列表与创建 API 已启动；CredentialRotation 调度 API、到期轮换执行 worker、旧/新 secret 引用记录与 completed rotation 回滚已完成；前端 `/accounts` 账号托管页已可查看 secret 引用、轮换记录并调度凭据轮换 | 高 |
| **#t44** | SSH CA / 临时证书 | backend + security | `SshCertificateAuthority` / `SshCertificate` 模型 + `Asset.trusted_ssh_ca_id` 信任配置字段 + CA 管理/禁用 API + 后端签发/撤销服务契约 + 临时证书签发/撤销 REST API + API 路由可用的 Vault-backed OpenSSH signer + SSH CA trust bundle API + 前端 `/ssh-ca` CA/trust bundle/临时证书入口已完成；后续补连接器生产级信任链或签发体验增强 | 高 |
| **#t45** | 连接器/边缘网关架构 | architect + backend | Connector Registry 心跳租约与过期 fail-closed 签发检查已启动；mTLS 证书指纹绑定已接入注册与 token 签发路径；enrollment-token 绑定的 attestation nonce/digest 已接入注册路径；active connector public key rotation 已完成；持久化 Connector 管理 API 已提供租户隔离的列表/创建、heartbeat 和 key rotation，响应不泄露 token、attestation digest 或私钥材料；轻量 Connector SDK 已覆盖 create/heartbeat/rotate-key 并保留不泄露 bearer token 的错误契约 | 高 |
| **#t46** | 会话录制、回放、命令检索 | backend + frontend | SessionRecording 与命令事件持久化模型已启动；录制创建、命令事件追加、Connector 命令事件上报、录制命令时间线读取、录制关闭和租户隔离命令检索 API 已完成，命令输出摘要会脱敏 token/password/secret/credential 赋值文本；命令检索已补 PostgreSQL full-text predicate、GIN 搜索索引和同租户倒序读取索引；前端 `/sessions` 已提供按 Recording ID 加载的只读回放命令时间线入口 | 中 |
| **#t47** | WebHook / 通知中心 / 工单系统增强 | backend | WebhookEndpoint 持久化模型与租户隔离 `GET/POST /api/v1/webhook-endpoints/` 管理 API 已启动，响应不泄露 signing secret；NotificationRule 持久化模型与租户隔离 `GET/POST /api/v1/notification-rules/` 管理 API 已启动，规则必须引用同租户 active WebHook endpoint；NotificationDelivery 队列记录与租户隔离入队/列表 API 已启动，事件类型必须同时匹配 rule 和 endpoint，响应不回显 payload；NotificationDeliveryWorker 已补到期投递、失败重试和 dead-letter 状态机；HttpWebhookNotificationSender 已可向 HTTPS WebHook endpoint 投递已脱敏 payload，非 2xx 或网络错误 fail-closed 进入重试/死信且不泄露 payload 或下游响应体；后续补 IM sender 和多级审批 | 中 |
| **#t48** | JIT 策略模板、审批策略 DSL | architect + backend | `ApprovalPolicyModel` 已通过租户隔离的 `GET/POST /api/v1/workflows/approval-policies` 提供策略模板创建和列表 API，要求 `workflow:admin` 或 `admin` 权限；`PolicyDecisionService` 已可接收 approval policy template 并对匹配 selector 且落入 deterministic rollout bucket 的请求返回 `APPROVAL_REQUIRED` obligations；`POST /api/v1/workflows/approval-policies/simulate` 已可在当前租户内复用同一决策引擎做策略模拟；approval policy family/version 基础已完成，`POST /api/v1/workflows/approval-policies/{policy_id}/versions` 可在当前租户内创建递增版本并停用旧 active 版本，列表与模拟默认只读取 active/latest 版本；`POST /api/v1/workflows/approval-policies/{policy_id}/rollback` 已可在当前租户内显式回滚到同 family 指定版本；`rollout_percentage` 灰度百分比已完成；DSL `context_equals` 精确匹配、`context_in` 枚举匹配、`context_not_equals` 排除匹配和 `context_not_in` 枚举排除匹配已完成，context 不匹配或 DSL 结构异常时 fail-closed；后续补复杂表达式与更多 DSL 操作符 | 中 |
| **#t49** | SIEM/告警/报表中心 | backend + frontend | 当前租户审计报表汇总 API `GET /api/v1/audits/reports/summary` 已启动，返回 total、severity、category、SIEM delivery 状态和高危计数且不泄露 metadata；前端 `/audits` 已展示报表总事件、高危事件和 SIEM failed 聚合卡片；后续补可靠队列、告警、合规报表导出和 Dashboard 深化 | 中 |
| **#t50** | Vault 生产级后端 | backend + security | `KmsKeyProvider` 协议与 `EnvelopeEncryptedSecretProvider` foundation 已启动，支持每条 secret 独立 DEK + KMS-wrapped data key + unwrap fail-closed；Envelope provider 已支持可替换 `SecretRecordStore`，并新增 `SecretRecordModel` 与 `SqlAlchemySecretRecordStore` 数据库持久化 adapter，record 不保存凭据明文；`LocalKmsEnvelopeKeyProvider` 已提供本地 AES-GCM wrapped DEK adapter，可由 `VAULT_LOCAL_KMS_MASTER_KEY` 装配；`unwrap_after_approval()` 已补审批后解包 guard，审批必须当前有效、携带 grant/workflow 标识并绑定目标 secret；后续补真实云 KMS/HSM/Vault adapter 与 break-glass | 高 |
| **#t51** | 可观测性体系 | devops | Prometheus metrics foundation 已启动：`GET /metrics` 暴露 HTTP 请求总数与延迟 histogram，指标标签只包含 method、路由模板 path 和 status_code；后续补 OpenTelemetry 分布式追踪、Loki 日志和部署层 scrape 策略 | 中 |
| **#t52** | Automation Worker | backend | Redis Streams 风格 JSON-only 队列、消费循环与调度 API foundation 已启动：`AutomationJobQueue` 白名单支持 `asset.scan`、`credential.rotate`、`ansible.playbook`，拒绝未知任务类型和敏感 payload 字段，不使用 pickle 或任意 Python 对象派发；`AutomationWorker` 可通过 consumer group 读取 JSON 消息、按 job type 分发到显式 handler，并仅在 handler 成功后 ack；`POST /api/v1/automation/jobs/asset-scans` 可按当前租户和当前用户调度 `asset.scan` 作业；`POST /api/v1/automation/jobs/credential-rotations` 会先按当前用户 actor scope 确认账号可见，再调度 `credential.rotate` 作业且 payload 不携带 secret；`POST /api/v1/automation/jobs/playbooks` 可调度 `ansible.playbook` 作业，payload 只包含 playbook 名称、目标资产 ID 列表和 check mode，额外字段 fail-closed；`AssetScanWorkerHandler` 已按当前租户确认 active asset 并只传递无凭据目标摘要；`CredentialRotateWorkerHandler` 已按当前租户确认 active account，创建轮换记录并调用显式改密执行器；`AnsiblePlaybookWorkerHandler` 已按当前租户确认 active 目标资产并只传递无凭据目标摘要、playbook 名称和 check mode；`LocalAnsiblePlaybookRunner` 已补本地 `ansible-playbook` adapter、playbook root 路径收敛、临时 JSON inventory 渲染、check mode 传递、不继承 secret/token 环境变量的 runtime 目录基础沙箱、执行超时和超时子进程回收，并已支持从 `ANSIBLE_PLAYBOOK_ROOT`、`ANSIBLE_RUNTIME_ROOT`、`ANSIBLE_PLAYBOOK_EXECUTABLE`、`ANSIBLE_PLAYBOOK_TIMEOUT_SECONDS`、`ANSIBLE_PLAYBOOK_MEMORY_LIMIT_MB`、`ANSIBLE_PLAYBOOK_CPU_LIMIT_SECONDS` 装配；CPU/内存限制在支持 POSIX `setrlimit` 的本地执行环境中应用于子进程；`AutomationJobRun` 已补 `ansible.playbook` 执行状态持久化，记录 running/completed/failed、message id、请求人、playbook 名称、check mode、目标数量和脱敏错误码，并已提供当前租户只读查询 API `GET /api/v1/automation/jobs/runs`；更强容器级/cgroup 隔离仍可作为后续执行器增强 | 中 |

#### 11.2.2 Phase 4 里程碑建议

| 里程碑 | 周期 | 交付 |
|--------|------|------|
| M1：多租户 + 凭据轮换 + Vault 生产级 | 2-3 周 | #t42 + #t43 + #t50 |
| M2：SSH CA + 连接器生产级 | 2-3 周 | #t44 + #t45 |
| M3：会话录制 + 通知 + 策略 DSL | 2-3 周 | #t46 + #t47 + #t48 |
| M4：SIEM + 可观测 + Automation | 1-2 周 | #t49 + #t51 + #t52 |

---

### 11.3 Phase 5：生产化与商业化准备

**目标**：面向真实部署、运维、安全审计和商业化交付。

**启动条件**：Phase 4 核心能力（多租户 + 凭据轮换 + SSH CA + 连接器生产级 + Vault 生产级）完成。

#### 11.3.1 任务分解

| 任务 ID | 任务 | Owner | 范围 |
|---------|------|-------|------|
| **#t53** | 高可用部署与水平扩展 | devops + architect | 无状态 Core 前置切片已启动：Session connection token store 可通过 `SESSION_CONNECTION_TOKEN_STORE=redis` 切换到 Redis-backed 单次消费存储，使用 `REDIS_URL` 和 `SESSION_CONNECTION_TOKEN_REDIS_KEY_PREFIX` 装配；Redis 模式只保存 token digest key 和 JSON 元数据，签发时写入 TTL，消费时通过 `GETDEL` 原子删除，默认 `memory` store 继续服务本地开发和单副本部署；后端 Redis client 已支持 `REDIS_MODE=single|sentinel|cluster`，Sentinel 通过 `REDIS_SENTINEL_URLS` / `REDIS_SENTINEL_MASTER_NAME` 装配，Cluster 通过 `REDIS_CLUSTER_URLS` 装配，Helm chart 已暴露对应 `config.redis*` 参数；Helm chart 已提供可选 HPA 模板，启用 `autoscaling.enabled=true` 时必须显式设置 `config.sessionConnectionTokenStore=redis`，否则模板渲染 fail-closed；数据库读副本 foundation 已启动，`DATABASE_READ_REPLICA_URL` 可配置独立只读 engine，默认空值复用写库 engine，Compose/Helm 已提供可选注入；资产列表、资产详情、平台列表、账号列表、账号轮换列表、会话列表、会话录制命令时间线、命令检索、Tenancy Organization/Team/Project 列表、WebHook endpoint 列表、通知规则列表、通知投递列表、Connector 列表、SSH CA 列表、SSH CA trust bundle、SSH certificate 列表、Automation job run 列表、approval policy 列表、认证态用户详情 `/api/v1/auth/me`、Workflow request 列表/详情以及 active JIT grant 列表 GET 路由已接入 read session dependency，audit events/summary/compliance GET 仍是进程内 audit service 的显式 DB-free 读取面；`tests/test_database_routing.py` 已覆盖所有 GET 路由必须被归类为 read-routed 或 DB-free；`scripts/phase5-ha-config-smoke.sh` 已接入 CI，覆盖 Compose read-replica env 渲染、Helm HPA memory-store fail-closed、Redis token store + Sentinel + HPA + read-replica Secret 渲染；`scripts/phase5-k8s-multi-replica-smoke.sh` 已提供真实 Kubernetes 多副本 smoke，CI 中仅在显式设置 repository variable `JANUSGATE_RUN_K8S_SMOKE=1` 且配置 smoke secrets 时执行；登录、2FA、refresh token、MFA/密码/API key 变更、connection token 签发、会话创建/关闭、写路由、轮换调度、命令事件上报、录制关闭、WebHook endpoint 创建、通知规则创建、通知投递入队、Connector 创建/心跳/key rotation、SSH CA 创建/禁用、SSH certificate 签发/撤销、Automation job 调度、approval policy 创建/版本/回滚/模拟以及 Workflow request 创建/提交/审批/拒绝/撤销继续使用 writer session；真实集群的 connection token 主链路与读副本延迟验收仍需在目标环境执行 |
| **#t54** | 审计合规报表 | backend + frontend | 合规报表导出基础已启动：`GET /api/v1/audits/reports/compliance` 按当前租户和模板返回事件 ID 列表、hash chain 起止、报告期间、报表签名、签名算法/key id、正式 JSON 导出格式元数据和安全下载文件名，不返回 metadata、message、resource_id、session_id 或任何凭据相关明细字段；后端已保留可替换 compliance report signer 边界，当前默认本地 HMAC signer，并已提供配置驱动 external HMAC signer adapter foundation，缺少外部 key id 或 signing secret 时 fail-closed；当前导出会写入 append-only WORM 归档元数据 `worm_record_id`、`worm_sequence_number` 与基于无敏感摘要的 `worm_content_hash`；前端 `/audits` 已提供 SOC2 合规报表 JSON 下载入口，使用后端返回的 vendor JSON media type 与安全文件名，并展示事件数与报表签名摘要，不展示原始审计 metadata；外部 WORM 存储和真实云 KMS/证书签章服务仍待后续切片 |
| **#t55** | 性能压测与容量模型 | qa + devops | 容量模型 smoke foundation 已启动：`scripts/phase5-capacity-model-smoke.sh` 接受 `JANUSGATE_LOAD_TEST_RPS`、`JANUSGATE_LOAD_TEST_P95_MS`、`JANUSGATE_LOAD_TEST_ERROR_RATE_PERCENT` 和 `JANUSGATE_CAPACITY_MODEL_MAX_CORE_RPS`，按 `docs/performance/phase5-capacity-model.md` 定义的 p95、错误率和 `capacity_headroom_percent` SLO 做可复现门禁，并已接入 CI Helm 门禁；真实压测脚本 foundation 已补 `scripts/phase5-core-api-load-test.sh`，可对目标环境执行认证 GET endpoint mix 并把聚合结果喂给容量模型 smoke；证据归档合约已补 `docs/performance/phase5-load-test-evidence-template.json` 和 `scripts/phase5-load-test-evidence-smoke.sh`，固定目标环境元数据、runner 配置、容量模型结果、artifact manifest 与敏感字段边界并接入 CI；真实环境运行、容量曲线和已填充证据包归档仍待后续切片 |
| **#t56** | 安全基线扫描与 SBOM | security + devops | Supply-chain security foundation 已启动：CI 新增 `scripts/phase5-supply-chain-security-smoke.sh` 校验 SBOM、签名和扫描 wiring；Trivy 对仓库文件系统执行 high/critical vulnerability gate；release tag 镜像推送后对 backend image digest 生成 SPDX JSON SBOM artifact，并通过 GitHub OIDC + Cosign keyless signing 对 digest 签名；runtime monitoring smoke foundation 已启动，`scripts/phase5-runtime-monitoring-smoke.sh` 校验 Compose/Helm 运行时加固、Prometheus `/metrics` 回归契约和 `deploy/monitoring/phase5-runtime-alerts.yaml` 告警规则基线；当前告警覆盖高 5xx、p95 延迟和 metrics 缺失；真实 Alertmanager 接入和目标环境告警演练仍待后续切片 |
| **#t57** | 版本发布流程、升级迁移、回滚策略 | devops | Release readiness foundation 已启动：`scripts/phase5-release-readiness-smoke.sh` 校验后端、健康检查和 Helm 版本号一致且符合 `MAJOR.MINOR.PATCH`，确认 `v*` release tag pipeline、Docker semver metadata、release-only image publish、Alembic 迁移检查点、迁移前备份要求和 `helm rollback` runbook 文档被 CI 覆盖；真实生产发布演练、破坏性迁移演练和数据回滚恢复证据仍待后续切片 |
| **#t58** | 管理后台、License/Edition 边界 | architect + backend + frontend | Feature Flag 与 License 摘要 foundation 已启动：`JANUSGATE_EDITION=community|enterprise` 控制期望版本，默认 community 启用 `core_pam`、`workflow_jit`、`audit_reports`；enterprise 只有在 `JANUSGATE_LICENSE_KEY` 通过配置的 license verifier 校验且未过期时才生效，否则 fail-closed 回退 community；当前支持 `JANUSGATE_LICENSE_VERIFIER=hmac` + `JANUSGATE_LICENSE_SIGNING_SECRET` 的 HMAC-SHA256 验签、`JANUSGATE_LICENSE_VERIFIER=ed25519` + `JANUSGATE_LICENSE_PUBLIC_KEY` 的离线公钥验签，以及 `JANUSGATE_LICENSE_VERIFIER=external-http` + `JANUSGATE_LICENSE_VALIDATION_URL` 的外部商业授权服务验证 foundation；external-http 只向 HTTPS endpoint 发送 opaque license key，service token 通过环境注入且不写入 DB，服务不可用或响应无效时 fail-closed；`GET /api/v1/admin/license-summary` 仅 admin 可读，不泄露 license key、签名 secret、公钥、validation token 或原始 payload；`POST /api/v1/admin/license-config` 已提供 admin-only 持久化配置 foundation，summary 优先读取 DB 中的激活配置，无记录时回退环境变量，响应只返回脱敏 `LicenseSummary`；前端 `/settings` 已展示 configured/effective edition、license status、启用与禁用能力，并提供最小 License lifecycle 激活表单提交持久化配置，可选择 `hmac`、`ed25519` 或 `external-http` verifier；external-http 表单只提交 opaque license key，validation endpoint 与 service token 仍只从环境读取；页面不回显 license key、签名 secret、公钥或原始 payload；成功写入 license 配置会追加脱敏 `admin.license_config.updated` 审计事件，记录状态、verifier、密钥材料是否配置和 enabled feature 摘要；`docs/site/fixtures/license-operations-evidence.json` 已补 external-http SLA、fail-closed drill、timeout budget、key custody owner、rotation cadence 和 revocation process 证据合约，并明确不得归档 license key、signing secret、validation token、私钥或原始授权 payload；真实目标环境联调仍待后续切片 |
| **#t59** | 文档站、安装手册、管理员手册、API 文档 | technical-writer | 文档站 foundation 已启动：`docs/site/index.md` 提供安装、管理员、API 文档和操作 runbook 入口；`docs/site/install.md` 覆盖 Compose、Helm、多副本前置条件和回滚入口；`docs/site/admin.md` 覆盖管理面、License / Edition、安全边界、审计和运维门禁；`docs/site/admin-screenshots.md` 已补管理员截图证据清单，固定 Settings License / Edition、Audits SOC2 export、Sessions recording timeline、Tenancy organization inventory、Accounts credential rotation 和 SSH CA trust bundle 的截图目标、脱敏要求、截图资产路径、真实截图归档合约与前端回归测试来源；`docs/site/assets/screenshots/` 已归档六张脱敏 SVG 截图证据，并随静态发布包 manifest 一起交付；`docs/site/fixtures/admin-screenshot-data.json` 已补截图测试数据夹具，固定每个 evidence id 的路由、脱敏 API 响应、capture actions、必须可见文字和禁止出现的敏感字段，并随静态发布包 manifest 一起交付；`docs/site/fixtures/admin-screenshot-archive.json` 已补真实运行环境截图归档合约，固定每个 evidence id 的 route、artifact、回归来源、`JANUSGATE_FRONTEND_BASE_URL` 捕获入口和脱敏检查项；`docs/site/api.md` 汇总稳定 API 分组并指向 `scripts/export-openapi-json.sh`；`docs/site/runbooks.md` 已补发布 checklist、多副本 smoke checklist、回滚 checklist、文档截图 checklist、operation evidence manifest、license operations evidence manifest 和 secret handling 操作边界；`docs/site/fixtures/operation-runbook-evidence.json` 已补操作 runbook evidence manifest，固定 release、多副本 smoke、rollback 和 secret handling 必须记录的命令、结果与密钥边界，并随静态发布包 manifest 一起交付；`docs/site/fixtures/license-operations-evidence.json` 已补 License 运营 evidence manifest，固定外部授权服务 SLA 和密钥托管证据字段；OpenAPI 自动生成 foundation 已接入 CI，通过 FastAPI `app.openapi()` 导出 `docs/site/openapi.json`，避免手写 schema 漂移；当前静态站点发布 smoke 会生成包含 Markdown、截图资产、截图 fixture、截图 archive manifest、操作 runbook evidence manifest、license operations evidence manifest、`openapi.json`、`manifest.json` 和截图捕获元数据的 `dist/docs-site` 发布包并接入 CI；真实浏览器截图流水线 foundation 已启动，`scripts/phase5-docs-browser-screenshots-smoke.sh` 默认校验截图证据 wiring，显式设置 `JANUSGATE_CAPTURE_DOC_SCREENSHOTS=1` 后可在具备 Playwright 的前端环境注入 fixture、执行页面操作、校验 must_show/must_not_show 合约并重抓真实浏览器截图；live PNG 已在真实 Vite 前端环境刷新，现场刷新产物写入 `docs/site/assets/screenshots/live-screenshots/*.png`，现有 SVG 继续作为静态发布包脱敏基线证据；后续仅剩目标交付环境按同一脚本重新刷新并复核 PNG 文件 |

---

## 12. 验收标准与质量门禁

### 12.1 CI 门禁（所有 PR 必须通过）

| 检查 | 工具 | 要求 |
|------|------|------|
| Lint | ruff check | 0 error |
| 类型检查 | mypy | 0 error |
| 单元测试 | pytest | 全部通过 |
| 覆盖率 | pytest --cov | ≥ 80%（核心路径 ≥ 90%） |
| 安全扫描 | bandit | 0 high |
| 依赖扫描 | pip-audit / uv audit | 0 high |
| Supply-chain 漏洞扫描 | Trivy | 0 high / critical |
| Release 镜像 SBOM / 签名 | anchore/sbom-action + Cosign | release tag 生成 SBOM artifact 并签名镜像 digest |
| 容器构建 | docker build | 成功 |
| Helm 校验 | helm lint + template | 通过 |

### 12.2 安全回归测试（必须覆盖）

- OAuth2 state 校验 + PKCE
- JWT 生命周期（签发/刷新/撤销/黑名单/改密失效）
- MFA challenge 一次性消费
- 速率限制（登录/TempToken/全局）
- timing-safe token 比较
- SSRF 防护（私有地址/loopback/link-local/DNS rebinding）
- 审计 metadata 脱敏
- 策略 deny-by-default
- grant 过期/撤销/资源不匹配 fail-closed
- 防自审批
- 租户隔离

### 12.3 Phase 3 Go/No-Go 标准

| 维度 | 标准 |
|------|------|
| 产品 | 6 页面可访问 + E2E 主链路跑通 |
| 安全 | 安全验收全部通过（见 11.1.4） |
| 后端 | ruff + mypy + pytest 通过，coverage ≥ 80% |
| 前端 | lint + typecheck + build 通过，关键页面 smoke 通过 |
| 部署 | Docker/Compose /health 通过 + Helm lint 通过 |
| 文档 | API 契约文档 + 部署说明更新 |

---

## 13. 团队分工与协作规则

### 13.1 角色与职责

| 角色 | 职责 |
|------|------|
| **architect** | 总体架构、产品边界、任务拆分、进度调度、关键决策 |
| **security-auditor** | 安全设计、威胁模型、安全加固、安全 review |
| **backend-engineer** | 后端 API、数据模型、领域逻辑、集成实现 |
| **frontend-engineer** | 前端控制台、UI 状态、路由、可访问性 |
| **qa-engineer** | 测试策略、回归覆盖、E2E、验收矩阵、Go/No-Go |
| **devops-engineer** | CI/CD、Docker/Helm、部署回滚、可观测 |
| **debugger** | 疑难失败、CI/测试/集成问题定位 |
| **technical-writer** | 开发文档、用户文档、API 文档、runbook |

### 13.2 协作规则

1. **Git 仓库是唯一共享事实源**：所有可复用代码、文档、设计结论必须提交到 `origin/dev`
2. **开始工作前必须先同步**：`git fetch origin && git pull --ff-only origin dev`
3. **禁止本地口头约定替代仓库文档**：架构、接口、任务拆分、验收标准必须落到 `docs/` 或代码内测试
4. **避免同文件并发修改**：按目录和模块划分 owner；跨 owner 修改先确认
5. **不从 JumpServer 搬旧代码**：JumpServer 只作 PRD 和业务路径参考
6. **安全关键路径必须经过代码审查与 QA 证据确认**
7. **每个阶段必须有可验证产物、任务看板状态和质量门禁证据**

### 13.3 提交规范

- 文档：`docs: ...`
- 架构：`arch: ...`
- 功能：`feat: ...`
- 安全：`security: ...`
- 测试：`test: ...`
- 重构：`refactor: ...`
- CI：`ci: ...`

每次提交必须说明可验证结果；安全关键路径必须有测试或检查脚本。

### 13.4 目录 owner 划分

| 路径 | Owner |
|------|-------|
| `backend/app/core/` | architect |
| `backend/app/models/` | architect + backend |
| `backend/app/api/auth/` | architect |
| `backend/app/api/assets/` | backend |
| `backend/app/api/sessions/` | backend |
| `backend/app/api/workflows/` | backend |
| `backend/app/api/audits/` | backend |
| `backend/app/policy/` | architect |
| `backend/app/connectors/` | architect |
| `backend/app/vault/` | architect + security |
| `frontend/` | frontend-engineer |
| `backend/tests/` | qa-engineer |
| `deploy/` | devops-engineer |
| `.github/workflows/` | devops-engineer |
| `docs/architecture/` | architect |
| `docs/security/` | security-auditor |

---

## 14. 附录

### 14.1 评估证据索引

#### 安全评估关键文件（JumpServer）

| 文件 | 关键行 | 问题编号 |
|------|--------|----------|
| `apps/jumpserver/middleware.py` | 23, 207-211 | P0#3 |
| `apps/common/utils/crypto/rsa_aes.py` | 37-62, 113-119, 175-196 | P0#8, P1#1 |
| `apps/jumpserver/conf.py` | 88-116, 186-187, 367, 618-623, 677-679, 1008-1010 | P0#1,9, P0#14, P1#5,8 |
| `Dockerfile` | 50 | P0#7 |
| `apps/jumpserver/settings/libs.py` | 128, 206-209, 248 | P0#4,10 |
| `apps/jumpserver/settings/base.py` | 90, 96, 386 | P0#4,5 |
| `apps/common/permissions.py` | 37-58, 51, 61-90 | P0#2, P1#3 |
| `apps/accounts/models/base.py` | 139-155 | P0#15 |
| `apps/ops/ansible/inventory.py` | 81, 95-97 | P0#16,17 |
| `apps/authentication/backends/oidc/decorator.py` | 24-62 | P0#14 |
| `apps/authentication/backends/token.py` | 17-18 | P0#11 |
| `apps/authentication/backends/oauth2/views.py` | 53-79 | P0#12 |
| `apps/authentication/api/connection_token.py` | 748-824 | P0#13 |
| `apps/users/models/user/_auth.py` | 279-306 | P1#9 |

#### 代码质量统计（JumpServer）

| 指标 | 数值 |
|------|------|
| Python 源文件 | 1,338 |
| 代码总行数 | 107,867 |
| 测试文件 | 17（全为空壳） |
| 测试/业务比 | 0.15% |
| except:pass | 51 处 |
| 裸 except: | 37 处 |
| except Exception | 307 处 |
| objects.all() 无过滤 | 174 处 |
| print() 代替日志 | 255 处 |
| 类型注解覆盖率 | 4.7% |
| null=True | 705 处 |
| Mixin 数量 | 438 个 |
| import * | 100+ 处 |
| monkey-patch | 4 处 |
| migration 文件 | 112 个 |
| CI lint/test/扫描 | 0 |

#### 依赖统计（JumpServer）

| 指标 | 数值 |
|------|------|
| uv.lock 包数 | 318 |
| 私有 fork 依赖 | 5 |
| 弃用/过时依赖 | 7+ |
| 云 SDK（xpack） | 20+ |
| dependabot | 禁用 |
| 依赖漏洞扫描 | 无 |

### 14.2 JanusGate 当前代码统计

| 指标 | 数值 |
|------|------|
| 后端 Python 文件 | 41 |
| 后端代码行数 | 4,993 |
| 测试文件 | 17 |
| 测试代码行数 | 4,533 |
| 测试/代码比 | 91% |
| CI 门禁 | ruff + mypy + pytest + bandit + pip-audit |
| 加密 | AES-256-GCM |
| 密码哈希 | bcrypt(rounds=12) |
| 序列化 | 无 pickle |
| 连接器模型 | 独立 enrollment token + 短期 jgt_ token |

### 14.3 文档关系

| 文档 | 状态 | 说明 |
|------|------|------|
| **`10-master-evaluation-and-roadmap.md`** | **当前权威** | **本文档：合并评估 + 架构 + 研发总计划** |
| `api-contract.md` | 技术参考 | Phase 3 API 契约与错误码规范 |
| `00-final-evaluation.md` | 历史参考 | v1.0 评估基线（2026-06-26）；结论已合并入本文档 |
| `02-policy-decision-service.md` | 技术参考 | PolicyDecisionService 专项设计 |
| `03-connector-api-v2.md` | 技术参考 | Connector API v2 专项设计 |
| `04-credential-vault.md` | 技术参考 | Credential Vault 专项设计 |
| `06-phase2-workflow-jit-prd-architecture.md` | 技术参考 | Phase 2 Workflow/JIT PRD 与架构 |
| `08-phase3-mvp-prd-ia.md` | 技术参考 | Phase 3 MVP 页面与信息架构；路线图和任务状态以本文档为准 |
| `01-rebuild-collaboration.md` | 已删除 | 过程协作文档，已由本文档第 13 章替代 |
| `05-phase1-closure-baseline.md` | 已删除 | 阶段收口过程报告，已由本文档第 4 章吸收 |
| `07-overall-rd-roadmap.md` | 已删除 | 旧路线图，已被本文档替代 |
| `docs/superpowers/plans/*` | 已删除 | agent 执行过程计划，不作为项目长期文档保留 |
| `09-jumpserver-reassessment-2026-07.md` | 未单独保留 | 重新评估报告内容已合并入本文档 |

### 14.4 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-06-29 | `00-final-evaluation.md` 初始评估基线 |
| v1.1 | 2026-07-02 | `09-jumpserver-reassessment-2026-07.md` 基于最新代码重新评估 |
| **v2.0** | **2026-07-02** | **本文档：合并 00+09 全部评估，基于实际代码状态，定义 Phase 3-5 完整研发任务计划，作为唯一推进依据** |
