from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import httpx

from actions_checkin import AccountStatus, batch_status, load_results
from push import PushHandler

if TYPE_CHECKING:
    from typing import List

    from actions_checkin import AccountResult


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


def _notification_lines(results: List[AccountResult] | None) -> List[str]:
    if results is None:
        return ["签到结果待核实。"]
    if not results:
        return ["签到未执行，请检查账号配置。"]

    lines: List[str] = []
    for result in results:
        status = AccountStatus(result["status"])
        details = result.get("details", [])
        label = result["alias"]
        if status != AccountStatus.DONE or not details:
            label += f" · {STATUS_TEXT[status]}"
        lines.extend([label, *details, ""])

    successful = sum(result["status"] == AccountStatus.DONE for result in results)
    skipped = sum(result["status"] == AccountStatus.SKIPPED for result in results)
    failed = len(results) - successful - skipped
    summary = f"共 {len(results)} 个账号，成功 {successful} 个"
    if skipped:
        summary += f"，未执行 {skipped} 个"
    if failed:
        summary += f"，异常 {failed} 个"
    lines.append(summary)
    return lines


def _push_status(results: List[AccountResult] | None) -> int:
    if batch_status(results) == AccountStatus.DONE:
        return 0
    outcomes = {result["status"] for result in results or []}
    if outcomes - {AccountStatus.DONE, AccountStatus.SKIPPED} == {
        AccountStatus.CAPTCHA
    }:
        return 3
    return 2 if AccountStatus.DONE in outcomes else 1


def _send_notification(directory: Path, token: str, chat_id: str) -> None:
    results = load_results(directory)
    status_id = _push_status(results)
    with TemporaryDirectory(dir=directory) as config_dir:
        config_path = Path(config_dir) / "push.ini"
        handler = PushHandler(str(config_path))
        if handler.get_config_path() != str(config_path):
            raise ValueError("Unexpected push configuration path")
        handler.cfg.read_dict(
            {
                "setting": {"enable": "true", "push_server": "telegram"},
                "telegram": {
                    "api_url": "api.telegram.org",
                    "bot_token": token,
                    "chat_id": chat_id,
                },
            }
        )
        with config_path.open("w", encoding="utf-8") as config_file:
            handler.cfg.write(config_file)
        if not isinstance(handler.http, httpx.Client):
            raise TypeError("Telegram requires HTTPX on the Actions runner")

        with handler.http as client:
            client.event_hooks["response"].append(_check_receipt)
            message = ""
            for line in _notification_lines(results):
                if len(message) + len(line) > 1800:
                    if handler.push(status_id, message.rstrip()) != 0:
                        raise ValueError("Telegram notification failed")
                    message = ""
                message += f"{line}\n"
            if handler.push(status_id, message.rstrip()) != 0:
                raise ValueError("Telegram notification failed")


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
