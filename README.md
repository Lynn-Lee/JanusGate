# JanusGate

> 策略驱动的 PAM / 零信任访问网关

JanusGate 是企业级特权访问管理（PAM）平台，基于 JumpServer 业务功能参考，全新架构重写。提供统一、安全、可审计的 SSH、RDP、Kubernetes、数据库和远程应用访问入口。

## 架构

```
Interface    REST / WebSocket / Connector API
Services     Auth / Policy / Inventory / Session / Audit / Vault
Domain       Identity / Asset / Credential / Policy / Session / AuditEvent
Infra        PostgreSQL / Redis / Object Storage / KMS
```

## 快速启动

```bash
cp .env.example .env
# 编辑 .env 设置 SECRET_KEY（必须不少于 32 字符）
docker compose up -d
```

## 文档

- [最终评估报告](docs/architecture/00-final-evaluation.md) — 基于 JumpServer 的完整评估与重构基线
- 产品设计文档（待补充）
- API 规范（待补充）

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.12+ / FastAPI / SQLAlchemy 2.0 async |
| 数据库 | PostgreSQL 16 |
| 缓存/队列 | Redis |
| 加密 | AES-256-GCM / bcrypt / JWT (HS256) |
| 部署 | Docker Compose / Kubernetes + Helm |

## License

待定
