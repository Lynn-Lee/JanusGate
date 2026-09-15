# 通知渠道扩展

#t75 在 #t47 WebHook / 通知规则 / 投递队列之上扩展多渠道投递：钉钉、飞书、Lark、企业微信、Slack、SMS、邮件与站内信，并补系统消息订阅。约束与 #t47 相同——payload 入库前脱敏，错误与响应不回显 payload、机器人 token 或下游响应体；失败走重试与 dead-letter。

## 渠道类型

`WebhookEndpoint.channel_type` 取值：

| 类型 | 投递方式 | URL 约束 |
|------|----------|----------|
| `webhook` | HTTPS JSON `POST` | 任意 HTTPS host；保留业务 query，禁止 userinfo |
| `dingtalk` | 官方文本机器人 | 仅 `oapi.dingtalk.com` |
| `feishu` | 官方文本机器人 | 仅 `open.feishu.cn` |
| `lark` | 官方文本机器人 | 仅 `open.larksuite.com` |
| `wecom` | 官方文本机器人 | 仅 `qyapi.weixin.qq.com` |
| `slack` | Incoming Webhook | 仅 `hooks.slack.com` |
| `sms` / `email` | HTTPS 网关 `Authorization: Bearer` | HTTPS-only，不直连 SMTP |
| `inbox` | 写入当前租户 `InAppMessage` | 不外呼；公开 URL 为空 |

非官方 IM host 返回 `CHANNEL_HOST_NOT_ALLOWED`。明文 HTTP 或带 userinfo 返回 `INVALID_WEBHOOK_URL`。IM / 网关缺少可剥离凭据返回 `CHANNEL_CREDENTIAL_REQUIRED`。

## 凭据处理

创建渠道时，机器人 token 从 URL query（钉钉 `access_token`、企微 `key` 等）或 path（飞书/Lark 末段、Slack `/services/` 之后）剥离，经 AES-256-GCM 写入 `credential_encrypted`。响应与列表只返回不含凭据的公开 URL，以及 `credential_configured` 布尔值，不返回密文、明文或 digest。

投递时 worker 仅在内存中重建目标 URL 或附加 Bearer，不把凭据写入响应、审计或 `last_error`。

## 规则、订阅与站内信

- 通知规则仍绑定 active 渠道，但 **禁止绑定 `inbox`**，错误码 `INBOX_CHANNEL_REQUIRES_SUBSCRIPTION`。
- 系统消息订阅 `POST /api/v1/notification-subscriptions/` 按事件类型扇出；inbox 必须带 `recipient_user_id`，否则 `INBOX_RECIPIENT_REQUIRED`。
- `POST /api/v1/notification-events/` 同时匹配当前租户 active 规则与订阅，写入 #t47 投递队列；响应只返回 `created` 与 `delivery_ids`，不回显 payload。
- `GET /api/v1/in-app-messages/` 只返回当前用户在当前租户的站内信，不要求 `notifications:*` 权限。

## 管理面

设置页提供三块：

- **通知渠道**：`webhooks:read` / `webhooks:write` 或 admin / 超管
- **系统消息订阅**：`notifications:read` / `notifications:write` 或 admin / 超管
- **站内信**：登录用户即可查看自己的消息

页面不展示机器人 token、signing secret 或下游响应体。

## 相关 API

详见 `docs/api-contract.md` 的 #t47 / #t75 段与导出的 `openapi.json`。
