# 通知渠道扩展（#t75）

在 Phase 4 #t47 的 WebHook endpoint、通知规则、可靠投递队列和 HTTPS sender 之上，#t75 扩展 IM / 短信 / 邮件 / 站内信，并补上系统消息订阅。

## 渠道类型

`WebhookEndpoint.channel_type`：

- `webhook`：任意 HTTPS URL（#t47 默认）
- `dingtalk` / `feishu` / `lark` / `wecom` / `slack`：仅官方 host
- `sms`：HTTPS 短信网关
- `email`：`smtps://` 或 `smtp://host:587`（拒绝 25 明文）
- `inbox`：固定 `inbox://local`，写入站内信

## 安全约束

- 投递 payload 入库前脱敏 token/password/secret/credential。
- 渠道凭据 AES-256-GCM 落库，接口只返回 `credential_configured`。
- URL 禁止 query/fragment，响应会去掉 query，避免机器人 token 回显。
- IM 非官方 host 返回 `CHANNEL_HOST_NOT_ALLOWED`。
- Worker 失败进入重试 / 死信；`last_error` 不含 payload、凭据或下游响应体。

## API

- `GET/POST /api/v1/webhook-endpoints/`：渠道 CRUD 入口（创建）。
- `GET/POST /api/v1/notification-subscriptions/`：系统消息订阅。
- `POST /api/v1/notification-events/`：按规则与订阅扇出到投递队列。
- `GET /api/v1/inbox-messages/`、`POST /api/v1/inbox-messages/{id}/read`：当前用户站内信。
- 投递队列与死信契约仍走 `NotificationDeliveryWorker`。

设置页提供「通知渠道 / 系统消息订阅 / 站内信」。真实运营商短信专有协议与 SMTP 账号池可后续切片。
