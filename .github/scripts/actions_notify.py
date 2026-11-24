import logging
import os
import sys
from pathlib import Path

import httpx

from actions_checkin import AccountStatus, batch_status, load_results
from push import PushHandler

STATUS_TEXT = {
    AccountStatus.DONE: "已签到",
    AccountStatus.FAILED: "签到失败",
    AccountStatus.SKIPPED: "未执行",
    AccountStatus.CAPTCHA: "需要验证码",
    AccountStatus.CREDENTIALS: "凭据失效",
    AccountStatus.UNVERIFIED: "结果待核实",
    AccountStatus.TIMEOUT: "执行超时",
    AccountStatus.CONFIG_ERROR: "配置无效",
}


def _check_receipt(response: httpx.Response) -> None:
    response.raise_for_status()
    response.read()
    data = response.json()
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise ValueError("Telegram did not accept the notification")


def _send_notification(directory: Path, token: str, chat_id: str) -> None:
    results = load_results(directory)
    lines = [
        f"{result['alias']} · {STATUS_TEXT[AccountStatus(result['status'])]}"
        for result in results or []
    ]
    if results is None:
        lines = ["签到结果待核实。"]
    elif not results:
        lines = ["签到未执行，请检查账号配置。"]
    status_id = 0 if batch_status(results) == AccountStatus.DONE else 1

    handler = PushHandler()
    handler.cfg.read_dict(
        {
            "telegram": {
                "api_url": "api.telegram.org",
                "bot_token": token,
                "chat_id": chat_id,
            }
        }
    )
    if not isinstance(handler.http, httpx.Client):
        raise TypeError("Telegram requires HTTPX on the Actions runner")

    with handler.http as client:
        client.event_hooks["response"].append(_check_receipt)
        message = ""
        for line in lines:
            if len(message) + len(line) > 1800:
                handler.telegram(status_id, message)
                message = ""
            message += f"{line}\n"
        handler.telegram(status_id, message)


def main(directory: Path) -> int:
    """Send account outcomes to Telegram and record a fixed workflow status."""
    token = os.environ.pop("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.pop("TELEGRAM_CHAT_ID", "").strip()
    sent = False
    previous_limit = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        # Telegram assigns positive dialog IDs to users and negative IDs to groups.
        if (
            token
            and chat_id.isascii()
            and chat_id.isdecimal()
            and 0 < int(chat_id) <= 0xFFFFFFFFFF
        ):
            _send_notification(directory, token, chat_id)
            sent = True
    except Exception:
        sent = False
    finally:
        logging.disable(previous_limit)

    try:
        (directory / "telegram.txt").write_text(
            "sent" if sent else "failed", encoding="utf-8"
        )
    except OSError:
        sent = False
    if not sent:
        print("Telegram 通知发送失败。", file=sys.stderr)
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
