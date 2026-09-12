# 通知渠道

#t75 在 #t47 WebHook / 通知规则 / 投递队列之上扩展 IM、SMS、邮件和站内信，并保持脱敏 payload 与 dead-letter 契约。

## 渠道类型

`POST /api/v1/webhook-endpoints/` 的 `channel_type`：

| 类型 | 说明 | URL 约束 |
|------|------|----------|
| `webhook` | 通用 HTTPS 回调 | 必须 `https://`，禁止 userinfo |
| `dingtalk` / `feishu` / `lark` / `wecom` / `slack` | IM | 仅官方 host；机器人 token 从 query/路径剥离后 AES-256-GCM 落库 |
| `sms` / `email` | HTTPS 网关 | 必须 `https://`；API key 走 `Authorization`；`recipient` 必填 |
| `inbox` | 站内信 | URL 固定为 `inbox://local`，`recipient` 为用户 ID |

响应只返回 `credential_configured`，不回显 token 或 signing secret。列表 URL 不含机器人 token。邮件本切片走 HTTPS 网关，SMTP 直连可后续切片。

## 系统消息订阅

`POST /api/v1/notification-subscriptions/` 把事件类型绑定到当前租户渠道。`user_id` 为空表示租户级订阅。

`POST /api/v1/notification-events/` 按规则与订阅扇出 `NotificationDelivery`。payload 入库前脱敏。

## 投递

`NotificationDeliveryWorker` 到期投递、失败重试、超过次数 dead-letter。`ChannelNotificationSender` 按 `channel_type` 分发。失败信息只有稳定错误码/HTTP 状态，不含 payload、凭据或下游响应体。

`GET /api/v1/in-app-messages/` 只返回当前用户的站内信。

设置页「通知渠道 / 系统消息订阅 / 站内信」提供管理入口，不回显凭据。
