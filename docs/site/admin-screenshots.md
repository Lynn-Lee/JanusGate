# JanusGate 管理员截图证据

本页记录当前管理员手册需要保留的控制台截图证据点。截图文件随静态文档站发布包归档；本清单把截图目标、验收文字和对应回归测试固定下来，避免管理员手册只停留在文字说明。

## Screenshot Evidence Matrix

| Evidence ID | Screenshot file | Screen | Required visible evidence | Regression source |
| --- | --- | --- | --- | --- |
| `admin-settings-license-summary` | `assets/screenshots/admin-settings-license-summary.svg` | Settings - License / Edition | `configured: enterprise`、`effective: community`、`invalid`、enabled/disabled feature 列表；不得出现 `JANUSGATE_LICENSE_KEY`、signing secret 或原始 payload | `frontend/src/pages/mvp-pages.test.tsx` |
| `admin-audits-soc2-export` | `assets/screenshots/admin-audits-soc2-export.svg` | Audits - SOC2 report export | 报表总事件、高危事件、SIEM failed、SOC2 JSON 下载动作和签名摘要；不得展示原始审计 metadata 或 secret token | `frontend/src/pages/mvp-pages.test.tsx` |
| `admin-sessions-recording-timeline` | `assets/screenshots/admin-sessions-recording-timeline.svg` | Sessions - recording command timeline | Recording ID 输入、加载回放时间线、命令摘要和 `password=[REDACTED]` 脱敏输出；不得展示原始 secret | `frontend/src/pages/mvp-pages.test.tsx` |

## Capture Contract

- 截图资产使用当前前端回归测试固定的脱敏 UI 状态归档；接入真实浏览器截图流水线后，必须保持相同 evidence id 和文件路径。
- 截图只保留页面状态、列表摘要和安全脱敏结果，不包含 bearer token、license key、signing secret、连接串、私钥或真实客户数据。
- 每张截图必须能回溯到上表中的 regression source；当 UI 文案或路由变化时，先更新对应测试，再更新本页。
- 发布包必须包含本页和 `assets/screenshots/`，使管理员手册、截图证据、OpenAPI 和 runbook 可以一起交付。
