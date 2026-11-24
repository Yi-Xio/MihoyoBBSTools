from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, TypedDict, cast

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import List, Never, Tuple, TypeGuard


class AccountStatus(IntEnum):
    """Private exit codes exchanged with an account subprocess."""

    DONE = 10
    FAILED = 11
    SKIPPED = 12
    CAPTCHA = 13
    CREDENTIALS = 14
    UNVERIFIED = 15
    TIMEOUT = 16
    CONFIG_ERROR = 17


class AccountResult(TypedDict):
    """An account label and the outcome of its run."""

    alias: str
    status: int
    details: NotRequired[List[str]]


SENSITIVE_FIELDS = {
    "alias",
    "cookie",
    "stoken",
    "stuid",
    "mid",
    "uid",
    "token",
    "id",
    "fp",
}
ACCOUNT_TIMEOUT_SECONDS = 240


def load_results(directory: Path) -> List[AccountResult] | None:
    """Read the preceding step's results, or return None if they are unavailable."""
    try:
        result: object = json.loads(
            (directory / "accounts.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(result, list) or not all(
        isinstance(item, dict)
        and isinstance(item.get("alias"), str)
        and type(item.get("status")) is int
        and item["status"] in AccountStatus
        and _is_detail_list(item.get("details", []))
        for item in result
    ):
        return None
    return cast("List[AccountResult]", result)


def batch_status(results: List[AccountResult] | None) -> AccountStatus:
    """Summarize a batch without including account-level information."""
    if results is None:
        return AccountStatus.UNVERIFIED
    statuses = {result["status"] for result in results}
    if not statuses:
        return AccountStatus.CONFIG_ERROR
    if statuses == {AccountStatus.SKIPPED}:
        return AccountStatus.SKIPPED
    if statuses <= {AccountStatus.DONE, AccountStatus.SKIPPED}:
        return AccountStatus.DONE
    return AccountStatus.FAILED


def _register_masks(value: object, field: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _register_masks(item, key)
    elif isinstance(value, list):
        for item in value:
            _register_masks(item, field)
    elif field in SENSITIVE_FIELDS and type(value) in (str, int):
        secret = str(value)
        _mask(secret)
        if field == "cookie":
            for part in secret.split(";"):
                _mask(part.partition("=")[2].strip())


def _mask(value: str) -> None:
    if value.strip():
        escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::add-mask::{escaped}", flush=True)


def _reject_json_constant(value: str) -> Never:
    raise ValueError("Non-standard JSON number")


def _is_detail_list(value: object) -> TypeGuard[List[str]]:
    return isinstance(value, list) and all(isinstance(line, str) for line in value)


def _read_account_details(details_file: Path) -> List[str]:
    try:
        details: object = json.loads(details_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return []
    return details if _is_detail_list(details) else []


def _run_account(
    configuration: Mapping[str, object], directory: Path
) -> Tuple[AccountStatus, List[str]]:
    project = Path(__file__).resolve().parents[2]
    worker = Path(__file__).with_name("actions_account.py")
    try:
        with tempfile.TemporaryDirectory(dir=directory) as config_directory:
            config_file = Path(config_directory) / "config.yaml"
            details_file = Path(config_directory) / "details.json"
            config_file.write_text(
                yaml.safe_dump(dict(configuration)), encoding="utf-8"
            )
            environment = {
                key: os.environ[key]
                for key in ("PATH", "LANG", "LC_ALL", "GITHUB_ACTIONS")
                if key in os.environ
            }
            environment.update(
                {
                    "PYTHONPATH": str(project),
                    "AutoMihoyoBBS_config_path": config_directory,
                    "MIHOYO_ACTIONS_CHECKIN": "1",
                }
            )
            result = subprocess.run(
                [sys.executable, str(worker), str(details_file)],
                cwd=project,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=ACCOUNT_TIMEOUT_SECONDS,
                check=False,
            )
            return AccountStatus(result.returncode), _read_account_details(details_file)
    except subprocess.TimeoutExpired:
        return AccountStatus.TIMEOUT, []
    except (OSError, ValueError, yaml.YAMLError):
        return AccountStatus.FAILED, []


def _checkin(directory: Path) -> int:
    try:
        accounts: object = json.loads(
            os.environ.pop("MIHOYO_ACCOUNTS", ""), parse_constant=_reject_json_constant
        )
    except (ValueError, RecursionError):
        accounts = []

    results: List[AccountResult] = []
    if isinstance(accounts, list):
        if os.environ.get("GITHUB_ACTIONS") == "true":
            _register_masks(accounts)
        for number, account in enumerate(accounts, start=1):
            alias = f"账号 {number}"
            status = AccountStatus.CONFIG_ERROR
            details: List[str] = []
            if isinstance(account, dict):
                name = account.get("alias")
                if isinstance(name, str) and name.strip():
                    alias = " ".join(name.split())[:64]
                configuration = account.get("config")
                if (
                    isinstance(configuration, dict)
                    and isinstance(configuration.get("enable"), bool)
                    and type(configuration.get("version")) is int
                    and isinstance(configuration.get("account"), dict)
                ):
                    status, details = _run_account(configuration, directory)
            result = AccountResult(alias=alias, status=status.value)
            if details:
                result["details"] = details
            results.append(result)

    (directory / "accounts.json").write_text(json.dumps(results), encoding="utf-8")
    return 0


def _summary(directory: Path) -> int:
    status = batch_status(load_results(directory))
    try:
        notified = (directory / "telegram.txt").read_text(encoding="utf-8") == "sent"
    except OSError:
        notified = False

    checkin_result = {
        AccountStatus.DONE: "签到完成",
        AccountStatus.SKIPPED: "签到未执行",
        AccountStatus.CONFIG_ERROR: "签到未执行",
    }.get(status, "签到异常")
    notify_result = "已发送" if notified else "发送失败"
    summary = f"## 米游社签到\n\n{checkin_result} · Telegram {notify_result}\n"
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as file:
        file.write(summary)
    return 0 if status == AccountStatus.DONE and notified else 1


def main() -> int:
    """Run or summarize the check-in batch without exposing account details."""
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=("run", "summary"))
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.step == "run":
            return _checkin(arguments.directory)
        return _summary(arguments.directory)
    except Exception:
        print("签到工作流处理失败。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
