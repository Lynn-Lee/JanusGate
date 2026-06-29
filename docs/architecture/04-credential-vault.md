# Credential Vault / SecretProvider 设计

## 目标

Vault 层统一治理 JanusGate 凭据生命周期，业务模块只保存 secret 引用，不持久化明文。

## SecretProvider 接口语义

- `create_secret`：创建密文记录。
- `unwrap`：在明确策略上下文下解包明文。
- `rotate`：生成新版本。
- `revoke`：吊销 secret，吊销后不可解包。
- `get_record`：获取元数据/密文，不返回明文。

## 初版本地 provider

本地开发 provider 使用 AES-GCM：
- 32 字节 master key。
- 每次加密生成 12 字节随机 nonce。
- secret id 作为 AAD，防止密文跨记录替换。
- 密文被篡改时必须解密失败。

生产环境应替换为 HashiCorp Vault、云 KMS 或 HSM provider。
