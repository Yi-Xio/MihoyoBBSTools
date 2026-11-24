import logging
import re

import config
import main as checkin
from actions_checkin import AccountStatus
from error import CookieError, StokenError

UNCERTAIN_CLOUD_RESULT = (
    "签到失败，未获得免费时长，可能是已经签到过了或者超出免费时长上限"
)


class TaskSignals(logging.Handler):
    """Capture upstream warning and error signals without retaining log messages."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.error = False
        self.missing_cookie = False
        self.requires_manual_checkin = False

    def emit(self, record: logging.LogRecord) -> None:
        """Recognize incomplete outcomes that upstream only records in logs."""
        if record.levelno >= logging.ERROR:
            self.error = True
            return
        message = record.getMessage()
        if message == "国际服未配置 Cookie！":
            self.missing_cookie = True
        if message.endswith("，请先手动签到一次"):
            self.requires_manual_checkin = True


def main() -> AccountStatus:
    """Call the existing check-in entry point and report a private exit code."""
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
    if signals.requires_manual_checkin or any(
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
    raise SystemExit(main())
