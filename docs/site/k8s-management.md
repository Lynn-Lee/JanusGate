# K8s 容器纳管（#t68）

JanusGate 对标 JumpServer 的 **Cloud 资产 + k8s 协议** 容器纳管能力：在 Cloud 资产上登记 Kubernetes 集群连接信息，账号层配置 namespace 作用域与默认 Pod，连接器经 Vault 解包 token 并可选调用 TokenRequest API 签发短期 token，再经 #t72 exec 通道执行审计化命令。

## 概念

| 概念 | 说明 |
|------|------|
| **Cloud 资产** | `asset_type=cloud` 的资产，承载 K8s 集群 |
| **K8sCluster** | 集群 API Server、CA 与集群级 namespace 白名单 |
| **K8s 账号** | `protocol=k8s`，凭据类型限定 token，账号 namespace 与集群 namespace 取交集 |
| **TokenRequest** | 可选：用 Vault 中的长期 token 向 API Server 申请短期 Bearer token |

## 管理 API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/v1/k8s/clusters/{asset_id}` | `assets:read` | 读取集群配置 |
| PUT | `/api/v1/k8s/clusters/{asset_id}` | `assets:write` | 创建/更新集群配置 |

账号托管 API（`/api/v1/accounts/`）在创建 `protocol=k8s` 账号时支持以下字段：

- `k8s_namespaces` — 账号级 namespace 授权
- `k8s_service_account` — TokenRequest 目标 ServiceAccount
- `k8s_default_pod` / `k8s_default_container` — exec 默认目标
- `k8s_use_short_lived_token` / `k8s_token_ttl_seconds` — 是否签发短期 token

## 连接器装配

生产 HTTP 装配使用 `RoutingSessionConnectionResolver`：

- `k8s` 协议 → `K8sVaultSessionConnectionResolver`（集群表 + Vault + TokenRequest）
- 其余 SSH 家族 → `AssetVaultSessionConnectionResolver`（含网域 ProxyJump）

解析结果为 `SessionConnectionSpec(mode=K8S_EXEC, k8s=K8sConnectionBundle)`，由 `K8sExecChannel` 消费。

## 安全约束

- 集群 token 走 envelope encryption + 审批后 `unwrap_after_approval`（关闭 P0#8 在容器场景的放大风险）
- 可选 TokenRequest 签发短期 token，bootstrap token 仅经 `Authorization` 头内存传递
- namespace 作用域在集群层与账号层双重约束，取交集后在 exec 通道建连前强制
- API Server 必须 `https://`，CA PEM 必填，拒绝 TOFU

## 测试

- `tests/test_k8s_api.py` — 集群管理 API
- `tests/test_k8s_validation.py` — namespace 与凭据校验
- `tests/test_k8s_token_request.py` — TokenRequest 客户端
- `tests/connectors/test_k8s_vault_resolver.py` — 生产 resolver 与路由
- `tests/connectors/test_k8s_exec.py` — exec 通道（#t72）
