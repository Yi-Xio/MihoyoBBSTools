from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from configparser import ConfigParser
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, cast

import yaml

from actions_checkin import load_accounts

if TYPE_CHECKING:
    from typing import Dict

ACCOUNT_TIMEOUT_SECONDS = 240
BOT_TOKEN = re.compile(r"[0-9]+:[A-Za-z0-9_-]+")


class RunStatus(StrEnum):
    """
    Fixed execution statuses for the public summary.
    """

    COMPLETED = "completed"
    TIMED_OUT = "timed-out"
    PROCESS_ERROR = "process-error"
    CONFIG_ERROR = "config-error"


def main() -> int:
    """
    Run the upstream CLI and export its execution status.

    Returns:
        int: Zero for a normal exit, one for a timeout or failure.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("account", type=int)
    arguments = parser.parse_args()
    status = _run_account(arguments.account)
    try:
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"status={status}\n")
    except (KeyError, OSError):
        print("无法输出账号执行状态。", file=sys.stderr)
        return 1
    return 0 if status == RunStatus.COMPLETED else 1


def _run_account(index: int) -> RunStatus:
    token = os.environ.pop("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.pop("TELEGRAM_CHAT_ID", "").strip()
    try:
        configuration = _account_config(index)
        push_config = _push_config(token, chat_id)
    except ValueError:
        return RunStatus.CONFIG_ERROR

    project = Path(__file__).resolve().parents[2]
    try:
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "config.yaml").write_text(
                yaml.safe_dump(configuration), encoding="utf-8"
            )
            with (directory / "push.ini").open("w", encoding="utf-8") as file:
                push_config.write(file)
            environment = {
                key: os.environ[key]
                for key in ("PATH", "LANG", "LC_ALL", "GITHUB_ACTIONS")
                if key in os.environ
            }
            environment.update(
                AutoMihoyoBBS_config_path=str(directory),
                AutoMihoyoBBS_push_path=str(directory),
                MIHOYO_ACTIONS_CHECKIN="1",
            )
            result = subprocess.run(
                [sys.executable, str(project / "main.py")],
                cwd=project,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=ACCOUNT_TIMEOUT_SECONDS,
                check=False,
            )
    except subprocess.TimeoutExpired:
        return RunStatus.TIMED_OUT
    except (OSError, ValueError, RecursionError, yaml.YAMLError):
        return RunStatus.PROCESS_ERROR

    return RunStatus.PROCESS_ERROR if result.returncode else RunStatus.COMPLETED


def _account_config(index: int) -> Dict[str, object]:
    accounts = load_accounts()
    if not 1 <= index <= len(accounts):
        raise ValueError("Invalid account position")
    account = accounts[index - 1]
    if not isinstance(account, dict):
        raise ValueError("Invalid account entry")
    configuration: object = account.get("config")
    if (
        not isinstance(configuration, dict)
        or not isinstance(configuration.get("enable"), bool)
        or type(configuration.get("version")) is not int
        or not isinstance(configuration.get("account"), dict)
    ):
        raise ValueError("Invalid account configuration")
    return cast("Dict[str, object]", configuration)


def _push_config(token: str, chat_id: str) -> ConfigParser:
    if (
        not BOT_TOKEN.fullmatch(token)
        or not chat_id.isascii()
        or not chat_id.isdecimal()
        or not chat_id.strip("0")
    ):
        raise ValueError("Invalid Telegram configuration")
    configuration = ConfigParser()
    configuration.read_dict(
        {
            "setting": {"enable": "true", "push_server": "telegram"},
            "telegram": {
                "api_url": "api.telegram.org",
                "bot_token": token,
                "chat_id": chat_id,
            },
        }
    )
    return configuration


if __name__ == "__main__":
    raise SystemExit(main())
