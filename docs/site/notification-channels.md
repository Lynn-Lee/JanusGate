# 通知渠道与系统消息订阅

#t75 在 #t47 WebHook / 通知规则 / 投递队列之上扩展渠道类型：钉钉、飞书、Lark、企业微信、Slack、SMS、邮件和站内信，并增加系统消息订阅。投递仍走 `NotificationDeliveryWorker` 的 pending → delivered / failed / dead_letter 契约，payload 入库前脱敏。

## 渠道类型

渠道复用 `POST /api/v1/webhook-endpoints/`，用 `channel_type` 区分：

| channel_type | URL 约束 | 说明 |
|---|---|---|
| `webhook` | `https://` | 原 #t47 JSON 信封 |
| `dingtalk` | host 必须是 `oapi.dingtalk.com` | 自定义机器人 text；可选加签 secret |
| `feishu` | `open.feishu.cn` | text 消息 |
| `lark` | `open.larksuite.com` | 与飞书同 payload，官方国际域 |
| `wecom` | `qyapi.weixin.qq.com` | 群机器人 text |
| `slack` | `hooks.slack.com` | Incoming Webhook text |
| `sms` | `https://` | 向配置的 HTTPS 网关 POST 已脱敏 JSON，Bearer 凭据仅内存解密 |
| `email` | `smtp://` 或 `smtps://` | SMTPS 或 STARTTLS，拒绝明文 SMTP |
| `inbox` | 固定 `inbox://local` | 写入站内信，不访问外网 |

响应中的 `url` 会去掉 query 与 userinfo，避免把机器人 `access_token` 回显到控制台。`credential` 只以 AES-256-GCM 密文落库，接口只返回 `credential_configured`。

## 系统消息订阅

- `GET/POST /api/v1/notification-subscriptions/`：当前租户用户订阅某条渠道上的事件类型。
- `POST /api/v1/notification-events/`：按订阅扇出 `NotificationDelivery`（`notification_rule_id` 为空），沿用脱敏与死信。
- 无匹配订阅时 `enqueued=0`，不调用外部渠道。

## 站内信

- `GET /api/v1/in-app-messages/`：只返回当前用户、当前租户的信件。
- `POST /api/v1/in-app-messages/{id}/read`：标记已读；跨用户返回 `IN_APP_MESSAGE_NOT_FOUND`。

## 失败模式

- IM 渠道打到非官方 host → `INVALID_CHANNEL_HOST`，创建与投递均 fail-closed。
- HTTP 非 2xx 或网络错误 → worker 重试，耗尽后 `dead_letter`；`last_error` 不含 payload、token、下游响应体。
- 邮件缺收件人、站内信缺 `recipient_user_id` / `target` → 投递失败并进入重试/死信。
