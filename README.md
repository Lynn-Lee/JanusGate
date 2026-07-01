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

## Phase 3 前端控制台

Phase 3 MVP 前端位于 `frontend/`，采用 React + TypeScript + Vite + Ant Design。当前范围按 `docs/architecture/08-phase3-mvp-prd-ia.md` 锁定为 6 个页面：登录、资产、会话、Workflow/JIT、审计日志、系统设置。

```bash
npm --prefix frontend install
npm --prefix frontend run dev
npm --prefix frontend run test
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run build
```

开发服务默认代理 `/api` 和 `/health` 到 `http://127.0.0.1:8000`；也可通过 `VITE_API_BASE_URL` 指向独立后端。当前后端只暴露会话创建/关闭接口，尚无会话列表接口，因此前端会话页临时展示本控制台创建/关闭的本地会话缓存；后续后端提供列表后应替换为服务端数据源。
