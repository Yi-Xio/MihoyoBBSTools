# GitHub Actions 签到

在 `Yi-Xio/MihoyoBBSTools` 创建仅允许 `master` 使用的 `mihoyo-checkin` Environment，并在其中添加以下 Secrets。配置完成后将 `master` 设置为默认分支。

| Secret | 内容 |
| --- | --- |
| `MIHOYO_ACCOUNTS` | 单行 JSON 数组。每项的 `config` 是一份[完整账号配置](../config/config.yaml.example)，`alias` 是仅用于 Telegram 通知的可选别名。 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token。 |
| `TELEGRAM_CHAT_ID` | 接收通知的 Telegram 私聊 ID，填写正整数。先在该私聊与 Bot 开始会话。 |

每份配置使用与本地 YAML 相同的键和数据类型。下面展示脱敏的结构，实际 Secret 中的 `config` 按配置示例填写完整内容。

```json
[{"alias":"账号 A","config":{"enable":true,"version":15,"account":{"cookie":"示例"}}}]
```

在 **Actions → 米游社签到 → Run workflow** 可手动运行。定时触发为每天北京时间 07:30。工作流逐账号执行，每个账号最多运行四分钟，单个账号超时后继续处理其余账号。单次 Job 上限为三小时。账号明细发到 Telegram 私聊，公开 Summary 只显示整体签到和通知状态。临时配置和中间结果限当次 Runner 使用。
