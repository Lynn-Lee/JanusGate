# Connector API v2 设计

## 目标

Connector API v2 替代 JumpServer 共享 `BOOTSTRAP_TOKEN` 模式，连接器必须具备独立身份、能力声明、状态管理和短期 token 申请流程。

## 注册流程

1. 管理端生成单连接器 enrollment token。
2. 连接器提交名称、环境、能力列表、公钥指纹。
3. 服务端验证 enrollment token、公钥指纹格式，以及可选的 mTLS 证书指纹绑定。
4. 注册成功后生成 `connector_id`，记录状态为 `active`，并保存 enrollment token 绑定的 mTLS 证书指纹。

## Token 签发流程

1. 连接器必须为 `active` 状态。
2. 若连接器记录绑定了 mTLS 证书指纹，token 申请必须提供相同的 presented certificate fingerprint。
3. token 申请必须调用 PolicyDecisionService。
4. 策略拒绝时不签发 token。
5. 策略允许时签发 `jgt_` 前缀短期 token，并绑定 `policy_audit_event_id`。

## 扩展点

- mTLS 证书绑定（已接入 registry 注册与 token 签发路径）。
- key rotation。
- capability reporting。
- heartbeat 与健康状态。
- 审计事件批量投递。
