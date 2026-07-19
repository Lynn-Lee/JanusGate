# JanusGate Agent Rules

本文件是 JanusGate 项目级 Codex / Agent 规则，优先级高于上级工作区通用规则。未覆盖事项继续遵循 `../AGENTS.md`。

## 云 ECS 默认入口

- 默认云 ECS 登录方式使用本机 shell alias：

```bash
alias schema='ssh -i ~/.ssh/zovjudan.pem ecs-user@47.102.195.159 -p 2222'
```

- 需要登录云 ECS 时优先使用 `schema` alias，不要临时猜测 SSH 用户、端口、私钥或主机地址。
- 云 ECS 上做临时部署、验证部署或并行测试部署时，如任务没有指定固定端口，部署端口可在 `8000-9000` 范围内选择一个可用随机端口；最终使用的端口应记录在交付说明或 run log 中。
