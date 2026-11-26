from __future__ import annotations

import json
import os
import runpy
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import httpx
import pytest
import yaml

import config
import main
import main_multi
import os_cloudgames

if TYPE_CHECKING:
    from typing import Dict, List

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SOURCE = SCRIPTS.parents[1]
ACCOUNT_TEMPLATE = SOURCE / "config" / "config.yaml.example"
BOT_TOKEN = "123:fixture-private-token"


@pytest.fixture
def account_config() -> Dict[str, object]:
    """
    Build a complete fixture without enabled network tasks.

    Returns:
        Dict[str, object]: A configuration that can run offline.
    """
    result = yaml.safe_load(ACCOUNT_TEMPLATE.read_text(encoding="utf-8"))
    result["mihoyobbs"]["enable"] = False
    result["games"]["cn"]["enable"] = False
    result["account"]["cookie"] = "token=fixture-cookie"
    return cast("Dict[str, object]", result)


@pytest.fixture
def workflow() -> Dict[str, object]:
    """
    Load the actual workflow for tests of its public outputs.

    Returns:
        Dict[str, object]: Values parsed from the checked-in workflow.
    """
    path = SCRIPTS.parent / "workflows" / "checkin.yml"
    return cast("Dict[str, object]", yaml.safe_load(path.read_text(encoding="utf-8")))


def _run_step(
    workflow: Dict[str, object], job: str, step: str, project: Path, **env: str
) -> subprocess.CompletedProcess[str]:
    jobs = cast("Dict[str, Dict[str, List[Dict[str, str]]]]", workflow["jobs"])
    script = next(
        item["run"] for item in jobs[job]["steps"] if item.get("name") == step
    )
    script = script.replace(".venv/bin/python", shlex.quote(sys.executable))
    return subprocess.run(
        ["bash", "-eo", "pipefail", "-c", script],
        cwd=project,
        env={
            **os.environ,
            **env,
            "GITHUB_ACTIONS": "true",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )


def _copy_scripts(project: Path) -> Path:
    scripts = project / ".github" / "scripts"
    shutil.copytree(SCRIPTS, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    return scripts


@pytest.mark.parametrize("secret", ["[]", '{"alias":"fixture-private"}', "[bad-secret"])
def test_invalid_matrix_inputs_have_safe_errors(
    workflow: Dict[str, object], tmp_path: Path, secret: str
) -> None:
    """
    Reject a malformed or empty Secret before starting any account Job.
    """
    output = tmp_path / "matrix"
    process = _run_step(
        workflow,
        "build-matrix",
        "生成账号矩阵",
        SOURCE,
        MIHOYO_ACCOUNTS=secret,
        GITHUB_OUTPUT=str(output),
    )
    assert process.returncode == 1
    assert process.stdout == ""
    assert process.stderr == "MIHOYO_ACCOUNTS 须为包含 1 至 256 项的 JSON 数组。\n"
    assert not output.exists()


def test_matrix_jobs_use_the_native_telegram_flow(
    workflow: Dict[str, object], tmp_path: Path, account_config: Dict[str, object]
) -> None:
    """
    Observe native Telegram payloads from the valid Jobs around an invalid entry.
    """
    _copy_scripts(tmp_path)
    (tmp_path / "main.py").write_text(
        f"""
import json
import os
import runpy
import sys
from pathlib import Path
from urllib.parse import parse_qs

import httpx

sys.dont_write_bytecode = True
sys.path.insert(0, {str(SOURCE)!r})
import config
import push

def send(request):
    directory = Path(os.environ["AutoMihoyoBBS_config_path"])
    assert directory == Path(os.environ["AutoMihoyoBBS_push_path"])
    assert directory.stat().st_mode & 0o077 == 0
    assert (directory / "config.yaml").stat().st_mode & 0o077 == 0
    assert (directory / "push.ini").stat().st_mode & 0o077 == 0
    assert not {{"GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "MIHOYO_ACCOUNTS",
                 "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}} & os.environ.keys()
    payload = parse_qs(request.content.decode())
    assert request.url.host == "api.telegram.org"
    assert request.url.path == "/bot123:fixture-private-token/sendMessage"
    assert payload["chat_id"] == ["456"]
    record = Path(__file__).with_name("received.json")
    with record.open("a", encoding="utf-8") as output:
        output.write(json.dumps([str(directory), config.config["account"]["cookie"],
                                 payload["text"][0]]) + "\\n")
    return httpx.Response(200, json={{"ok": True}})

with httpx.Client(transport=httpx.MockTransport(send)) as client:
    push.get_new_session = lambda: client
    print("fixture-raw-stdout")
    print("fixture-raw-stderr", file=sys.stderr)
    runpy.run_path({str(SOURCE / "main.py")!r}, run_name="__main__")
""",
        encoding="utf-8",
    )
    accounts = json.dumps(
        [
            {"alias": "fixture-private-alias", "config": account_config},
            {"config": {"account": {"cookie": "fixture-invalid-account"}}},
            {
                "config": {
                    **account_config,
                    "account": {"cookie": "token=second-fixture-cookie"},
                    "enable": False,
                }
            },
        ]
    )
    output = tmp_path / "matrix"
    process = _run_step(
        workflow,
        "build-matrix",
        "生成账号矩阵",
        tmp_path,
        MIHOYO_ACCOUNTS=accounts,
        GITHUB_OUTPUT=str(output),
    )
    assert (process.returncode, process.stdout, process.stderr) == (0, "", "")
    matrix = output.read_text(encoding="utf-8")
    assert matrix == "accounts=[1, 2, 3]\n"

    expected = {
        1: (0, "completed", "执行结束"),
        2: (1, "config-error", "未执行（配置无效）"),
        3: (0, "completed", "执行结束"),
    }
    for index in json.loads(matrix.split("=", 1)[1]):
        exit_code, status, label = expected[index]
        output = tmp_path / f"status-{index}"
        summary = tmp_path / f"summary-{index}"
        process = _run_step(
            workflow,
            "account",
            "执行上游签到和推送",
            tmp_path,
            ACCOUNT_INDEX=str(index),
            MIHOYO_ACCOUNTS=accounts,
            TELEGRAM_BOT_TOKEN=f"  {BOT_TOKEN}\r\n",
            TELEGRAM_CHAT_ID="456",
            GITHUB_OUTPUT=str(output),
            GITHUB_STEP_SUMMARY=str(summary),
        )
        assert (process.returncode, process.stdout, process.stderr) == (
            exit_code,
            "",
            "",
        )
        assert output.read_text(encoding="utf-8") == f"status={status}\n"
        outcome = "failure" if exit_code else "success"
        _assert_summary(workflow, summary, status, outcome, label)

    received = (tmp_path / "received.json").read_text(encoding="utf-8").splitlines()
    first, second = map(json.loads, received)
    assert first[1:] == ["token=fixture-cookie", "「米游社签到」执行成功!\r\n"]
    assert second[1:] == [
        "token=second-fixture-cookie",
        "「米游社签到」执行失败!\r\nConfig 未启用！",
    ]
    assert first[0] != second[0]
    assert all(not Path(directory).exists() for directory in (first[0], second[0]))


def _assert_summary(
    workflow: Dict[str, object],
    summary: Path,
    status: str,
    outcome: str,
    expected: str,
) -> None:
    process = _run_step(
        workflow,
        "account",
        "发布摘要",
        SOURCE,
        ACCOUNT_STATUS=status,
        ACCOUNT_OUTCOME=outcome,
        GITHUB_STEP_SUMMARY=str(summary),
    )
    assert (process.returncode, process.stdout, process.stderr) == (0, "", "")
    assert summary.read_text(encoding="utf-8") == f"### 签到执行\n\n{expected}\n"


@pytest.mark.parametrize(
    ("child_code", "status", "summary"),
    [
        ("time.sleep(60)", "timed-out", "执行超时"),
        ("raise RuntimeError('fixture exception')", "process-error", "进程异常"),
    ],
)
def test_failing_job_publishes_safe_execution_status(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    workflow: Dict[str, object],
    tmp_path: Path,
    account_config: Dict[str, object],
    child_code: str,
    status: str,
    summary: str,
) -> None:
    """
    Kill a hanging process, remove its private directory, and publish its status.
    """
    scripts = _copy_scripts(tmp_path)
    marker = tmp_path / "started"
    (tmp_path / "main.py").write_text(
        f"import os, time\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).write_text(os.environ['AutoMihoyoBBS_config_path'])\n"
        f"print('fixture-private-output', flush=True)\n{child_code}\n",
        encoding="utf-8",
    )
    output = tmp_path / "status"
    script = scripts / "actions_account.py"
    monkeypatch.syspath_prepend(str(scripts))
    monkeypatch.setattr(sys, "argv", [str(script), "1"])
    monkeypatch.setenv("MIHOYO_ACCOUNTS", json.dumps([{"config": account_config}]))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "456")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    run = subprocess.run

    def run_with_short_timeout(
        args: List[str],
        *,
        cwd: Path,
        env: Dict[str, str],
        stdin: int,
        stdout: int,
        stderr: int,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[bytes]:
        assert timeout == 240
        return run(
            args,
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            timeout=2,
            check=check,
        )

    with monkeypatch.context() as process:
        process.setattr(subprocess, "run", run_with_short_timeout)
        with pytest.raises(SystemExit) as result:
            runpy.run_path(str(script), run_name="__main__")

    assert result.value.code == 1
    assert capfd.readouterr() == ("", "")
    directory = Path(marker.read_text(encoding="utf-8"))
    assert not directory.exists()
    assert output.read_text(encoding="utf-8") == f"status={status}\n"
    _assert_summary(workflow, tmp_path / "summary", status, "failure", summary)


@pytest.mark.parametrize(
    ("secret", "chat_id", "index"),
    [
        ('[{"config":{"account":{"cookie":"fixture-private"}}}]', "456", 1),
        ("[]", "456", 1),
        ('[{"config":{"enable":true,"version":15,"account":{}}}]', "-456", 1),
        ('[{"config":{"enable":true,"version":15,"account":{}}}]', "456", 2),
    ],
)
def test_invalid_job_input_fails_before_running_upstream(
    workflow: Dict[str, object], tmp_path: Path, secret: str, chat_id: str, index: int
) -> None:
    """
    Report a bad account or Telegram configuration without exposing its contents.
    """
    _copy_scripts(tmp_path)
    output = tmp_path / "status"
    process = _run_step(
        workflow,
        "account",
        "执行上游签到和推送",
        tmp_path,
        ACCOUNT_INDEX=str(index),
        MIHOYO_ACCOUNTS=secret,
        TELEGRAM_BOT_TOKEN=BOT_TOKEN,
        TELEGRAM_CHAT_ID=chat_id,
        GITHUB_OUTPUT=str(output),
    )
    assert (process.returncode, process.stdout, process.stderr) == (1, "", "")
    assert output.read_text(encoding="utf-8") == "status=config-error\n"
    _assert_summary(
        workflow, tmp_path / "summary", "config-error", "failure", "未执行（配置无效）"
    )


def test_preparation_failure_has_a_public_summary(
    workflow: Dict[str, object], tmp_path: Path
) -> None:
    """
    Publish a fixed result even when the Python execution step was skipped.
    """
    _assert_summary(
        workflow, tmp_path / "summary", "", "skipped", "未执行（环境准备失败）"
    )


def test_overseas_cloud_client_sends_string_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Keep the upstream cloud game client's HTTP header regression covered.
    """
    requests: List[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "retcode": 0,
                "data": {
                    "free_time": {"send_freetime": 5, "free_time": 65},
                    "play_card": {"short_msg": "无"},
                    "coin": {"coin_num": 10},
                },
            },
        )

    main.clear_push_message()
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(os_cloudgames, "get_new_session", lambda: client)
        os_cloudgames.CloudGenshin("fixture-token", "en-us").sign_account()

    assert "签到成功，已获得 5 分钟免费时长" in main.drain_push_message()
    assert len(requests) == 1
    assert requests[0].headers["x-rpc-client_type"] == "3"
    assert requests[0].headers["x-rpc-channel_id"] == "1"


def test_existing_entry_points_use_local_yaml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    account_config: Dict[str, object],
) -> None:
    """Keep the upstream single and multi-account YAML entry points working."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
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
