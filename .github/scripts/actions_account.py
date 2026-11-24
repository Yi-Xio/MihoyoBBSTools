from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import config
import main as checkin
from actions_checkin import AccountStatus
from actions_details import notification_details, started_game
from error import CookieError, StokenError

if TYPE_CHECKING:
    from typing import Dict, List

UNCERTAIN_CLOUD_RESULT = (
    "签到失败，未获得免费时长，可能是已经签到过了或者超出免费时长上限"
)


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

    outcome = _account_status(status, message or "", signals)
    if details_file is not None and outcome != AccountStatus.SKIPPED:
        details = notification_details(message or "", signals.overseas_errors)
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


def _account_status(status: int, message: str, signals: TaskSignals) -> AccountStatus:
    if not config.config["enable"]:
        return AccountStatus.SKIPPED
    if status == checkin.StatusCode.CAPTCHA_TRIGGERED.value:
        return AccountStatus.CAPTCHA
    if status != checkin.StatusCode.SUCCESS.value:
        return AccountStatus.FAILED

    if "token 失效" in message:
        return AccountStatus.CREDENTIALS
    if signals.missing_cookie or _cloud_token_missing():
        return AccountStatus.CONFIG_ERROR
    confirmed = message.replace(UNCERTAIN_CLOUD_RESULT, "")
    if signals.manual_games or any(
        text in confirmed for text in ("失败", "签到发生异常", "请手动签到一次")
    ):
        return AccountStatus.FAILED
    web_activity = config.config["web_activity"]
    if (
        signals.error
        or UNCERTAIN_CLOUD_RESULT in message
        or (web_activity["enable"] and web_activity["activities"])
    ):
        return AccountStatus.UNVERIFIED
    if not message:
        return _empty_result()
    if "没有绑定任何" in message or re.search(r"还能获得 [1-9]\d* 个米游币", message):
        return AccountStatus.UNVERIFIED
    if re.search(
        r"已经签到过|已连续签到\s*\d+\s*天|今天获得的奖励是|签到成功，已获得"
        r"|今天已经全部完成了|还能获得 0 个米游币",
        message,
    ):
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
