from __future__ import annotations

import importlib
import json
import logging
import os
import runpy
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs

import httpx
import pytest
import yaml

import config
import main
import main_multi
import push

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Dict, List, Literal, Tuple


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ACCOUNT_TEMPLATE = SCRIPTS.parents[1] / "config" / "config.yaml.example"


@pytest.fixture
def account_config() -> Dict[str, object]:
    """Build a complete account configuration that makes no network requests."""
    result = yaml.safe_load(ACCOUNT_TEMPLATE.read_text(encoding="utf-8"))
    result["mihoyobbs"]["enable"] = False
    result["games"]["cn"]["enable"] = False
    result["account"]["cookie"] = "token=fixture-cookie-value"
    return cast("Dict[str, object]", result)


@pytest.fixture(autouse=True)
def script_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the same import paths as the Actions steps."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def _run_script(monkeypatch: pytest.MonkeyPatch, name: str, *args: str) -> int:
    script = SCRIPTS / name
    monkeypatch.setattr(sys, "argv", [str(script), *args])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(script), run_name="__main__")
    return cast(int, exit_info.value.code)


@pytest.mark.parametrize(
    "secret", ["", "[invalid fixture-secret", "[]", "{}", '[{"alias": NaN}]']
)
def test_invalid_batch_still_publishes_a_safe_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    secret: str,
) -> None:
    """Report missing or invalid input without echoing it or skipping the summary."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("MIHOYO_ACCOUNTS", secret)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert _run_script(monkeypatch, "actions_checkin.py", "run", str(tmp_path)) == 0
    assert _run_script(monkeypatch, "actions_checkin.py", "summary", str(tmp_path)) == 1
    assert summary.read_text() == "## 米游社签到\n\n签到未执行 · Telegram 发送失败\n"
    assert capsys.readouterr() == ("", "")


def test_timeout_and_invalid_item_do_not_stop_later_accounts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    account_config: Dict[str, object],
) -> None:
    """Keep account inputs separate and continue after timeout and invalid input."""
    seen_configs: List[Path] = []
    run_process = subprocess.run

    def run(
        args: List[str],
        *,
        env: Mapping[str, str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        config_file = Path(env["AutoMihoyoBBS_config_path"]) / "config.yaml"
        assert config_file.is_file()
        assert config_file.parent.stat().st_mode & 0o077 == 0
        assert "MIHOYO_ACCOUNTS" not in env
        assert "TELEGRAM_BOT_TOKEN" not in env
        assert "GITHUB_STEP_SUMMARY" not in env
        assert all(not path.exists() for path in seen_configs)
        seen_configs.append(config_file)
        if len(seen_configs) == 1:
            raise subprocess.TimeoutExpired(args, 1)
        return run_process(args, env=env, capture_output=True, check=False)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", "unused")
    monkeypatch.setenv(
        "MIHOYO_ACCOUNTS",
        json.dumps(
            [
                {"alias": "timeout", "config": account_config},
                {"alias": "invalid", "config": {}},
                {"alias": "later", "config": {**account_config, "enable": False}},
            ]
        ),
    )

    assert _run_script(monkeypatch, "actions_checkin.py", "run", str(tmp_path)) == 0
    assert len(seen_configs) == 2
    assert all(not path.exists() for path in seen_configs)
    assert json.loads((tmp_path / "accounts.json").read_text()) == [
        {"alias": "timeout", "status": 16},
        {"alias": "invalid", "status": 17},
        {"alias": "later", "status": 12},
    ]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("今天已经签到过", 10),
        ("玩家，本次签到失败", 11),
        ("仅有未知响应", 15),
        ("签到失败，未获得免费时长，可能是已经签到过了或者超出免费时长上限", 15),
    ],
)
def test_worker_checks_the_business_result_even_with_zero_status(
    monkeypatch: pytest.MonkeyPatch,
    account_config: Dict[str, object],
    message: str,
    expected: int,
) -> None:
    """Distinguish a confirmed check-in from a failed or ambiguous upstream result."""
    worker = importlib.import_module("actions_account")
    monkeypatch.setattr(config, "config", account_config)

    def sign() -> Tuple[int, str]:
        return 0, message

    monkeypatch.setattr(main, "main", sign)
    assert worker.main() == expected


def test_missing_os_cookie_is_not_reported_as_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    account_config: Dict[str, object],
) -> None:
    """Report a missing credential when upstream disables an enabled task."""
    games = cast("Dict[str, Dict[str, object]]", account_config["games"])
    games["os"].update({"enable": True, "genshin": {"checkin": True}, "cookie": ""})
    monkeypatch.setenv("MIHOYO_ACCOUNTS", json.dumps([{"config": account_config}]))

    assert _run_script(monkeypatch, "actions_checkin.py", "run", str(tmp_path)) == 0
    assert json.loads((tmp_path / "accounts.json").read_text()) == [
        {"alias": "账号 1", "status": 17},
    ]


@pytest.mark.parametrize("missing_cloud_token", [False, True])
def test_known_incomplete_task_cannot_be_hidden_by_another_success(
    monkeypatch: pytest.MonkeyPatch,
    account_config: Dict[str, object],
    missing_cloud_token: bool,
) -> None:
    """Retain a failure signal even when another enabled game returns success."""
    worker = importlib.import_module("actions_account")
    if missing_cloud_token:
        games = cast("Dict[str, Dict[str, object]]", account_config["cloud_games"])
        games["cn"].update({"enable": True, "genshin": {"enable": True, "token": ""}})
    monkeypatch.setattr(config, "config", account_config)

    def sign() -> Tuple[int, str]:
        if not missing_cloud_token:
            logging.warning("玩家「fixture-name」是第一次绑定米游社，请先手动签到一次")
        return 0, "另一位玩家已连续签到1天"

    monkeypatch.setattr(main, "main", sign)
    assert worker.main() == (17 if missing_cloud_token else 11)


@pytest.mark.parametrize(
    ("receipt", "first_status", "sent"),
    [
        ("ok", 14, True),
        ("ok", 10, True),
        ("rejected", 10, False),
        ("timeout", 14, False),
    ],
)
def test_telegram_and_public_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    receipt: Literal["ok", "rejected", "timeout"],
    first_status: int,
    sent: bool,
) -> None:
    """Use the existing Telegram sender and publish only aggregate statuses."""
    summary = tmp_path / "summary.md"
    requests: List[httpx.Request] = []
    (tmp_path / "accounts.json").write_text(
        json.dumps(
            [
                {"alias": "fixture-private-alias", "status": first_status},
                {"alias": "fixture-second-account", "status": 10},
            ]
        )
    )
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fixture-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1234567890")

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if receipt == "timeout":
            raise httpx.ReadTimeout(f"fixture-raw-error {request.url}", request=request)
        return httpx.Response(
            200, json={"ok": sent, "description": "fixture-raw-error"}
        )

    def create_client() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(push, "get_new_session", create_client)
    notification_exit = _run_script(monkeypatch, "actions_notify.py", str(tmp_path))
    assert notification_exit == (0 if sent else 1)
    assert len(requests) == 1
    assert requests[0].url.path == "/botfixture-token/sendMessage"
    payload = parse_qs(requests[0].content.decode())
    assert payload["chat_id"] == ["1234567890"]
    first_result = "已签到" if first_status == 10 else "凭据失效"
    assert f"fixture-private-alias · {first_result}" in payload["text"][0]
    assert "fixture-second-account · 已签到" in payload["text"][0]

    summary_exit = _run_script(
        monkeypatch, "actions_checkin.py", "summary", str(tmp_path)
    )
    assert summary_exit == (0 if first_status == 10 and sent else 1)
    expected = "已发送" if sent else "发送失败"
    batch_result = "签到完成" if first_status == 10 else "签到异常"
    assert (
        summary.read_text()
        == f"## 米游社签到\n\n{batch_result} · Telegram {expected}\n"
    )
    assert "fixture-" not in "".join(capsys.readouterr())


@pytest.mark.parametrize(
    "destination", ["-12345", "@fixture-channel", "0", "1099511627776"]
)
def test_telegram_rejects_non_private_destinations_before_sending(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    destination: str,
) -> None:
    """Keep account information from being sent to a group or channel."""
    clients_created = []

    def create_client() -> httpx.Client:
        clients_created.append(True)
        raise AssertionError("Must validate the destination first")

    monkeypatch.setattr(push, "get_new_session", create_client)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fixture-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", destination)

    assert _run_script(monkeypatch, "actions_notify.py", str(tmp_path)) == 1
    assert not clients_created
    assert capsys.readouterr() == ("", "Telegram 通知发送失败。\n")


@pytest.mark.parametrize("state", ["{}", '[{"alias":"fixture-private","status":999}]'])
def test_unreadable_intermediate_state_still_produces_a_public_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    state: str,
) -> None:
    """Publish an abnormal summary when the previous step's records are invalid."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    (tmp_path / "accounts.json").write_text(state)

    assert _run_script(monkeypatch, "actions_checkin.py", "summary", str(tmp_path)) == 1
    assert summary.read_text() == "## 米游社签到\n\n签到异常 · Telegram 发送失败\n"
    assert capsys.readouterr() == ("", "")


def test_workflow_preparation_failure_still_writes_a_public_summary(
    tmp_path: Path,
) -> None:
    """Run the workflow's shell step without a venv after preparation failed."""
    workflow_path = SCRIPTS.parent / "workflows" / "checkin.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    step = workflow["jobs"]["checkin"]["steps"][-1]
    assert step["if"] == "${{ !cancelled() }}"
    summary = tmp_path / "summary.md"
    summary_environment = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(summary),
        "PREPARE_OUTCOME": "failure",
    }
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env=summary_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert summary.read_text() == "## 米游社签到\n\n签到未执行 · Telegram 未执行\n"


def test_github_mask_commands_cannot_be_split_by_secret_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Escape GitHub command delimiters before registering values from a JSON Secret."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    cookie = "token=value%; other=raw\r\n::warning::bad"
    accounts = [{"config": {"account": {"cookie": cookie}}}]
    monkeypatch.setenv("MIHOYO_ACCOUNTS", json.dumps(accounts))
    assert _run_script(monkeypatch, "actions_checkin.py", "run", str(tmp_path)) == 0
    stdout, stderr = capsys.readouterr()
    assert all(line.startswith("::add-mask::") for line in stdout.splitlines())
    assert "::add-mask::value%25" in stdout.splitlines()
    assert "::add-mask::raw%0D%0A::warning::bad" in stdout.splitlines()
    assert stderr == ""


def test_existing_entry_points_use_local_yaml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    account_config: Dict[str, object],
) -> None:
    """Keep the upstream single and multi-account YAML entry points working."""
    configuration = yaml.safe_dump(account_config)
    (tmp_path / "config.yaml").write_text(configuration)
    (tmp_path / "second.yaml").write_text(configuration)
    monkeypatch.setattr(config, "path", str(tmp_path))
    monkeypatch.setattr(config, "config_Path", str(tmp_path / "config.yaml"))
    monkeypatch.setattr(config, "config_prefix", "")
    monkeypatch.setattr(main_multi.time, "sleep", lambda _seconds: None)

    assert main.main() == (0, "")
    status, message = main_multi.main_multi(autorun=True)
    assert status == 0
    assert "成功2个" in message


def test_actions_entry_requires_an_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """Require explicit approval for the upstream entry point under Actions."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("MIHOYO_ACTIONS_CHECKIN", raising=False)
    with pytest.raises(SystemExit):
        main.check_github_actions()

    monkeypatch.setenv("MIHOYO_ACTIONS_CHECKIN", "1")
    main.check_github_actions()
