# JanusGate 覆盖率与发布质量门禁

## 覆盖率门禁

| 范围 | 最低门禁 | 说明 |
|---|---:|---|
| 后端整体行覆盖率 | 80% | Phase 1 基线；不得低于主干历史值 |
| 新增/变更业务模块 | 85% | 按 PR diff 口径检查 |
| 安全关键路径 | 90% | `auth`、`core/security.py`、`core/deps.py`、`vault`、`policy` |
| P0 风险矩阵场景 | 100% 有测试或显式豁免 | 豁免必须说明原因、残余风险和补测时间 |

建议本地命令（在 `backend/` 目录）：

```bash
ruff check .
pytest --cov=app --cov-report=term-missing --cov-fail-under=80
```

> CI 接入属于 `.github/workflows/` owner 范围，QA 不直接修改；需要 DevOps 将上述命令纳入 PR 必跑检查。

## Go / No-Go 标准

### Go 条件

- P0 风险矩阵全部有自动化测试或已批准临时豁免。
- `ruff check .` 通过。
- `pytest` 通过，且覆盖率满足门禁。
- 安全关键 PR 已完成 double review。
- Auth、Policy、Vault、Session、Audit 至少各有 1 条 API/集成 smoke 覆盖。

### No-Go 条件

- 认证、授权、加密、Vault、策略默认拒绝任一路径存在未关闭 P0 缺陷。
- 覆盖率低于门禁且无明确豁免。
- 新增 API 无契约测试或错误码不稳定。
- 审计事件缺失导致安全关键操作不可追溯。
- 生产环境配置可用弱密钥、默认密钥或调试端点。

## CI 与任务板追踪

- 每个 PR 必须在描述中标注对应任务号、影响模块、风险矩阵覆盖项和测试命令。
- CI 需要暴露 lint、typecheck、unit/integration、coverage、security scan 的独立检查结果。
- P0 矩阵项缺失测试时，在对应任务中记录 blocker；不得仅以口头说明放行。
- 覆盖率豁免必须在任务板或 PR 中引用本文件的豁免模板，便于发布前复核。

## 豁免模板

```text
风险项：
影响模块：
未覆盖原因：
残余风险：
临时缓解：
补测 owner：
补测截止日期：
批准人：
```
