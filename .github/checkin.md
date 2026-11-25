# GitHub Actions 签到

在运行签到的仓库中创建仅允许 `master` 使用的 `mihoyo-checkin` Environment，并在其中添加以下 Secrets。将 `master` 设为默认分支以启用定时触发。

| Secret | 内容 |
| --- | --- |
| `MIHOYO_ACCOUNTS` | 单行 JSON 数组。每项的 `config` 是一份[完整账号配置](../config/config.yaml.example)，`alias` 是仅用于 Telegram 通知的可选别名。 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token。 |
| `TELEGRAM_CHAT_ID` | 接收通知的 Telegram 私聊 ID，填写正整数。先在该私聊与 Bot 开始会话。 |

每份配置使用与本地 YAML 相同的键和数据类型。下面展示脱敏的结构，实际 Secret 中的 `config` 按配置示例填写完整内容。

```json
[{"alias":"账号 A","config":{"enable":true,"version":15,"account":{"cookie":"示例"}}}]
```

已有两份本地配置时，可在仓库根目录运行 `./set-mihoyo-accounts.sh`。脚本读取 `config/account-1.yaml` 和 `config/account-2.yaml`，将账号数组写入 Git 忽略的本地文件 `MIHOYO_ACCOUNTS.txt`。将文件内容手动填入 `MIHOYO_ACCOUNTS` Secret。账号配置在 Runner 内按次生成，更新凭据后需同步更新 Secret。

在 **Actions → 米游社签到 → Run workflow** 可手动运行。GitHub 在默认分支 `master` 上按每天北京时间 07:30 调度，可在同一页面查看定时运行记录。工作流逐账号执行，每个账号最多运行四分钟，单个账号超时后继续处理其余账号。单次 Job 上限为三小时。[GitHub 定时触发](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

Telegram 私聊由上游推送入口发送，按别名显示识别到的米游币、游戏签到、奖励和待处理事项，末尾附账号汇总。公开 Summary 只显示整体签到和通知状态。账号临时配置、脱敏中间结果和推送配置限当次 Runner 使用。
