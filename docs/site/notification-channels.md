# 通知渠道扩展

Phase 6 #t75 在 #t47 WebHook / 通知规则 / 投递队列之上扩展 IM、邮件/短信 HTTPS 网关、站内信和系统消息订阅。投递仍走同一条脱敏 payload 与 dead-letter 契约。

## 渠道类型

`WebhookEndpoint.channel_type` 支持：

- `webhook`：通用 HTTPS 回调。保留 query，禁止 URL userinfo，避免误伤 SIEM 地址。
- `dingtalk` / `feishu` / `lark` / `wecom` / `slack`：只允许官方 host。机器人 token 从 query 或路径剥离后以 AES-256-GCM 落库，列表 URL 不含凭据。
- `sms` / `email`：本切片走 HTTPS 网关，`Authorization: Bearer`，不直连 SMTP 或运营商专有协议。
- `inbox`：站内信。不发起外呼，由 worker 写入当前租户、指定接收人的 `InAppMessage`。

官方 host：

- 钉钉 `oapi.dingtalk.com`
- 飞书 `open.feishu.cn`
- Lark `open.larksuite.com`
- 企业微信 `qyapi.weixin.qq.com`
- Slack `hooks.slack.com`

非官方 host 返回 `400 INVALID_CHANNEL_HOST`。明文 HTTP 或带 userinfo 的 URL 返回 `400 INVALID_WEBHOOK_URL`。

## 系统消息订阅与扇出

- `GET/POST /api/v1/system-message-subscriptions/`：租户隔离。站内信渠道必须带 `recipient_user_id`，否则 `400 INBOX_RECIPIENT_REQUIRED`。
- 通知规则不能绑定站内信渠道，返回 `400 INBOX_CHANNEL_REQUIRES_SUBSCRIPTION`。
- `POST /api/v1/notification-events/`：按当前租户 active 规则与订阅扇出到投递队列。同一渠道 + 接收人只入队一次。响应只返回 `queued` / `inbox_queued`，不回显 payload。

## 站内信

`GET /api/v1/in-app-messages/` 只返回当前用户在当前租户的消息。跨用户、跨租户都是空列表。正文沿用 #t47 脱敏规则。无渠道管理权限的用户仍可查看自己的站内信。

## 投递失败

`ChannelAwareNotificationSender` 在 IM / 网关失败时只保留稳定错误（如 `notification delivery failed with status 503`）。`last_error`、异常信息和 API 响应都不包含 payload、机器人 token、Bearer 或下游响应体。达到最大尝试次数后仍标记 `dead_letter`。

## 控制台

设置页提供「通知渠道」「系统消息订阅」「站内信」。渠道管理需要 `webhooks:read` / `webhooks:write`（或 `admin`）；订阅需要 `notifications:read` / `notifications:write`；站内信对已登录用户可见。
