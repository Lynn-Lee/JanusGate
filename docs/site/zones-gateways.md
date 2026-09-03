# 网域与网关中转（#t67）

JanusGate 对标 JumpServer 的 **Zone + Gateway** 网域中转能力：资产可归属网域，建连时从网域内随机选取活跃网关，经 **SSH ProxyJump** 到达内网目标。网关凭据与目标凭据均经 Vault 内存解析，不经命令行传递（关闭 P0#16）。

## 概念

| 概念 | 说明 |
|------|------|
| **Zone（网域）** | 分段网络的逻辑分组，含名称与网关集合 |
| **Gateway（网关）** | 普通 `Asset`（Host 类型），通过 `zone_gateways` 关联到网域 |
| **ProxyJump** | 连接器经 `asyncssh` 先连网关，再以 `tunnel=` 连到目标资产 |

## 管理 API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/v1/zones/` | `assets:read` | 列出网域 |
| POST | `/api/v1/zones/` | `assets:write` | 创建网域 |
| GET | `/api/v1/zones/{zone_id}` | `assets:read` | 网域详情 |
| DELETE | `/api/v1/zones/{zone_id}` | `assets:write` | 删除网域（仍有关联资产时 409） |
| GET | `/api/v1/zones/{zone_id}/gateways` | `assets:read` | 列出网关 |
| POST | `/api/v1/zones/{zone_id}/gateways` | `assets:write` | 登记网关资产 |
| DELETE | `/api/v1/zones/{zone_id}/gateways/{gateway_asset_id}` | `assets:write` | 移除网关 |
| POST | `/api/v1/zones/{zone_id}/gateways/{gateway_asset_id}/probe` | `assets:test` | TCP 连通性探测（已登记资产，允许内网地址） |

资产可通过创建时的 `zone_id` 或 `PATCH /api/v1/assets/{id}` 挂载到网域。连接器侧由 `AssetVaultSessionConnectionResolver` 在解析连接参数时自动选取网关并探测可达性。

## 安全约束

- 网关与目标主机密钥均须 **已审批**（fail-closed，禁止 TOFU）
- 内网目标（有 `zone_id`）可跳过直连扫描，但必须已有 approved 公钥
- 网关选取前做 TCP 探测（`probe_registered_host`，不对已登记跳板机做 SSRF 私网封锁），不可达则 `ZONE_GATEWAY_UNREACHABLE`
- 凭据仅经 Vault `unwrap` 进入内存，走 `asyncssh` 库调用，无 `sshpass`/子进程；网关 hop 与目标 hop 同一约束（P0#16）

## 测试

- `tests/test_zones_api.py` — 管理 API
- `tests/test_zone_resolver.py` — 随机网关选取与 resolver ProxyJump
- `tests/connectors/test_zone_proxy_jump.py` — 进程内双跳 SSH 端到端
