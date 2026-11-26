# GitHub Actions 签到

在运行签到的仓库中创建仅允许 `master` 使用的 `mihoyo-checkin` Environment，并在其中添加以下 Secrets。将 `master` 设为默认分支以启用定时触发。

| Secret | 内容 |
| --- | --- |
| `MIHOYO_ACCOUNTS` | 单行 JSON 数组。每项的 `config` 是一份[完整账号配置](../config/config.yaml.example)。账号序号对应数组顺序，已有的 `alias` 字段可以保留。 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token。 |
| `TELEGRAM_CHAT_ID` | 接收通知的 Telegram 私聊 ID，填写正整数。先在该私聊与 Bot 开始会话。 |

每份配置使用与本地 YAML 相同的键和数据类型。下面展示脱敏的结构，实际 Secret 中的 `config` 按配置示例填写完整内容。

```json
[{"config":{"enable":true,"version":15,"account":{"cookie":"示例"}}}]
```

已有两份本地配置时，可在仓库根目录运行 `./set-mihoyo-accounts.sh`。脚本读取 `config/account-1.yaml` 和 `config/account-2.yaml`，将账号数组写入 Git 忽略的本地文件 `MIHOYO_ACCOUNTS.txt`。将文件内容手动填入 `MIHOYO_ACCOUNTS` Secret。账号配置在 Runner 内按次生成，更新凭据后需同步更新 Secret。

在 **Actions → 米游社签到 → Run workflow** 可手动运行。默认分支 `master` 的定时配置为每天北京时间 07:30。工作流为每个账号生成独立 Job，每次执行一个。单个入口最多运行四分钟，失败或超时后其余账号继续执行。[GitHub 定时触发](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

每个账号通过上游 `main.py` 生成并发送原版 Telegram 通知。公开 Summary 只显示该账号的进程状态。绿色 Job 表示上游进程正常退出，任务结果以私聊回执为准。进程在推送前结束时，可在账号的 Summary 查看超时或异常。

可在运行页面单独重跑某个账号的 Job。账号 Job 启动时读取当时的 Secret，重跑沿用原序号。建议等当前批次结束后再调整数组顺序或删减账号，调整后手动启动新一轮。[GitHub Secrets 读取时间](https://docs.github.com/en/actions/reference/security/secrets#when-github-actions-reads-secrets) · [GitHub 重跑文档](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs#re-running-a-specific-job-in-a-workflow)
