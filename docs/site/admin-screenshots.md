# JanusGate 管理员截图证据

本页记录当前管理员手册需要保留的控制台截图证据点。截图文件可由后续静态站点流水线或人工验收归档到发布包；本清单先把截图目标、验收文字和对应回归测试固定下来，避免管理员手册只停留在文字说明。

## Screenshot Evidence Matrix

| Evidence ID | Screen | Required visible evidence | Regression source |
| --- | --- | --- | --- |
| `admin-settings-license-summary` | Settings - License / Edition | `configured: enterprise`、`effective: community`、`invalid`、enabled/disabled feature 列表；不得出现 `JANUSGATE_LICENSE_KEY`、signing secret 或原始 payload | `frontend/src/pages/mvp-pages.test.tsx` |
| `admin-audits-soc2-export` | Audits - SOC2 report export | 报表总事件、高危事件、SIEM failed、SOC2 JSON 下载动作和签名摘要；不得展示原始审计 metadata 或 secret token | `frontend/src/pages/mvp-pages.test.tsx` |
| `admin-sessions-recording-timeline` | Sessions - recording command timeline | Recording ID 输入、加载回放时间线、命令摘要和 `password=[REDACTED]` 脱敏输出；不得展示原始 secret | `frontend/src/pages/mvp-pages.test.tsx` |

## Capture Contract

- 截图应基于当前前端控制台路由，而不是手工重绘的 UI。
- 截图只保留页面状态、列表摘要和安全脱敏结果，不包含 bearer token、license key、signing secret、连接串、私钥或真实客户数据。
- 每张截图必须能回溯到上表中的 regression source；当 UI 文案或路由变化时，先更新对应测试，再更新本页。
- 发布包必须包含本页，使管理员手册、OpenAPI 和 runbook 可以一起交付。
