from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import config
import main as checkin
from actions_checkin import AccountStatus
from actions_details import CLOUD_ERROR_PREFIX, parse_report, started_game
from error import CookieError, StokenError

if TYPE_CHECKING:
    from collections.abc import Set
    from typing import Dict, List


class TaskSignals(logging.Handler):
    """Capture upstream status signals and identify raw API errors for removal."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.error = False
        self.missing_cookie = False
        self.current_game: str | None = None
        self.manual_games: List[str] = []
        self.overseas = False
        self.overseas_errors: Dict[str, str] = {}
        self.cloud_errors: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Recognize incomplete outcomes that upstream only records in logs."""
        if record.levelno >= logging.ERROR:
            self.error = True
            if self.overseas and self.current_game:
                self.overseas_errors.setdefault(self.current_game, record.getMessage())
            return
        message = record.getMessage()
        if message == "海外版：":
            self.overseas = True
            self.current_game = None
        elif message in {"正在进行云游戏国际版签到", "正在进行米游社网页活动任务"}:
            self.overseas = False
            self.current_game = None
        elif game := started_game(message):
            self.current_game = game
        elif message == "国际服未配置 Cookie！":
            self.missing_cookie = True
        elif message.startswith(CLOUD_ERROR_PREFIX):
            self.cloud_errors.append(message)
        elif message.endswith("，请先手动签到一次"):
            game = self.current_game or "游戏签到"
            if game not in self.manual_games:
                self.manual_games.append(game)


def main(details_file: Path | None = None) -> AccountStatus:
    """Run the upstream entry point and save only safe notification details."""
    signals = TaskSignals()
    logging.getLogger().addHandler(signals)
    logging.getLogger("AutoMihoyoBBS").addHandler(signals)
    try:
        status, message = checkin.main()
    except (CookieError, StokenError):
        return AccountStatus.CREDENTIALS
    except (KeyError, TypeError, ValueError):
        return AccountStatus.UNVERIFIED
    except Exception:
        return AccountStatus.FAILED
    finally:
        logging.getLogger().removeHandler(signals)
        logging.getLogger("AutoMihoyoBBS").removeHandler(signals)

    outcomes, details = parse_report(
        message or "", signals.overseas_errors, signals.cloud_errors
    )
    outcome = _account_status(status, bool(message), outcomes, signals)
    if details_file is not None and outcome != AccountStatus.SKIPPED:
        details.extend(
            f"{game} · 首次绑定，请手动签到一次" for game in signals.manual_games
        )
        if signals.missing_cookie:
            details.append("国际服 · 缺少 Cookie")
        try:
            details_file.write_text(
                json.dumps(details, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass
    return outcome


def _account_status(
    status: int, has_report: bool, outcomes: Set[AccountStatus], signals: TaskSignals
) -> AccountStatus:
    if not config.config["enable"]:
        return AccountStatus.SKIPPED
    if AccountStatus.CAPTCHA in outcomes:
        return AccountStatus.CAPTCHA
    # Upstream also derives its CAPTCHA exit status from the unparsed message.
    if status not in (
        checkin.StatusCode.SUCCESS.value,
        checkin.StatusCode.CAPTCHA_TRIGGERED.value,
    ):
        return AccountStatus.FAILED

    if AccountStatus.CREDENTIALS in outcomes:
        return AccountStatus.CREDENTIALS
    if signals.missing_cookie or _cloud_token_missing():
        return AccountStatus.CONFIG_ERROR
    if signals.manual_games or AccountStatus.FAILED in outcomes:
        return AccountStatus.FAILED
    web_activity = config.config["web_activity"]
    if (
        signals.error
        or AccountStatus.UNVERIFIED in outcomes
        or (web_activity["enable"] and web_activity["activities"])
    ):
        return AccountStatus.UNVERIFIED
    if not has_report:
        return _empty_result()
    if AccountStatus.DONE in outcomes:
        return AccountStatus.DONE
    return AccountStatus.UNVERIFIED


def _cloud_token_missing() -> bool:
    return any(
        isinstance(game, dict) and game.get("enable") and not game.get("token")
        for region in config.config["cloud_games"].values()
        if region["enable"]
        for game in region.values()
    )


def _empty_result() -> AccountStatus:
    configuration = config.config
    if configuration["mihoyobbs"]["enable"]:
        return AccountStatus.UNVERIFIED
    has_enabled_task = any(
        region["enable"]
        and any(isinstance(game, dict) and game.get(flag) for game in region.values())
        for section, flag in (("games", "checkin"), ("cloud_games", "enable"))
        for region in configuration[section].values()
    )
    return AccountStatus.UNVERIFIED if has_enabled_task else AccountStatus.SKIPPED


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
