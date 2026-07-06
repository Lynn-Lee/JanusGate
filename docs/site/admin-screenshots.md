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
- 截图测试数据固定在 `docs/site/fixtures/admin-screenshot-data.json`，记录每个 evidence id 的路由、脱敏 API 响应、必须可见文字和禁止出现的敏感字段；真实截图流水线可复用该夹具构造稳定测试状态。
- CI 默认运行 `scripts/phase5-docs-browser-screenshots-smoke.sh`，验证截图证据文件、文档清单和发布包 manifest wiring；默认模式不启动浏览器。
- 需要从真实前端页面重新捕获截图时，先提供可访问且已配置测试数据的控制台地址，再显式运行：

  ```bash
  JANUSGATE_CAPTURE_DOC_SCREENSHOTS=1 \
  JANUSGATE_FRONTEND_BASE_URL=http://127.0.0.1:5173 \
  scripts/phase5-docs-browser-screenshots-smoke.sh
  ```

- capture 模式要求 `frontend/` 环境可解析 Playwright，并会向浏览器 localStorage 注入 `janusgate-access-token` 测试 token 和 `janusgate-doc-screenshot-fixture` 测试数据；目标后端或前端 mock 必须返回上表要求的脱敏状态。
- 截图只保留页面状态、列表摘要和安全脱敏结果，不包含 bearer token、license key、signing secret、连接串、私钥或真实客户数据。
- 每张截图必须能回溯到上表中的 regression source；当 UI 文案或路由变化时，先更新对应测试，再更新本页。
- 发布包必须包含本页、`assets/screenshots/` 和 `fixtures/admin-screenshot-data.json`，使管理员手册、截图证据、OpenAPI、runbook 和截图测试数据可以一起交付。
