# Connector API v2 设计

## 目标

Connector API v2 替代 JumpServer 共享 `BOOTSTRAP_TOKEN` 模式，连接器必须具备独立身份、能力声明、状态管理和短期 token 申请流程。

## 注册流程

1. 管理端生成单连接器 enrollment token。
2. 连接器提交名称、环境、能力列表、公钥指纹。
3. 服务端验证 enrollment token 和指纹格式。
4. 注册成功后生成 `connector_id` 并记录状态为 `active`。

## Token 签发流程

1. 连接器必须为 `active` 状态。
2. token 申请必须调用 PolicyDecisionService。
3. 策略拒绝时不签发 token。
4. 策略允许时签发 `jgt_` 前缀短期 token，并绑定 `policy_audit_event_id`。

## 扩展点

- mTLS 证书绑定。
- key rotation。
- capability reporting。
- heartbeat 与健康状态。
- 审计事件批量投递。
