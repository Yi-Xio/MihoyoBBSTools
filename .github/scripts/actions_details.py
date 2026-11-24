from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import List, Tuple


GAME_NAMES = {
    "崩坏学园2",
    "崩坏3",
    "未定事件簿",
    "原神",
    "崩坏：星穹铁道",
    "绝区零",
}
TASK_NAMES = GAME_NAMES | {"米游社", "云原神", "云绝区零"}
TASK_STARTED = re.compile(r"正在进行「(.+)」签到")
CONSECUTIVE_DAYS = re.compile(r".{0,80}已连续签到\s*([0-9]{1,5})\s*天")
REWARD_NAME = r"[\u4e00-\u9fffA-Za-z0-9 ·・（）()'’\-]{1,40}"
CN_REWARD = re.compile(rf"今天获得的奖励是「({REWARD_NAME})」x([0-9]{{1,6}})")
OS_REWARD = re.compile(rf"今天获得的奖励是：([0-9]{{1,6}})x 「({REWARD_NAME})」")
COIN_COUNT = re.compile(
    r"(一共获得|今天已经获得|还能获得|目前有) ([0-9]{1,9}) 个米游币"
)
CLOUD_GAIN = re.compile(r"签到成功，已获得 ([0-9]{1,6}) 分钟免费时长")
CLOUD_BALANCE = re.compile(
    r"你当前拥有免费时长 ([0-9]{1,5}) 小时 ([0-9]{1,2}) 分钟，"
    r"畅玩卡状态为 .+，拥有(米云币|邦邦点) ([0-9]{1,9}) 枚"
)


def started_game(message: str) -> str | None:
    """Identify a known game from the upstream task-start log."""
    match = TASK_STARTED.fullmatch(message)
    if not match:
        return None
    name = "崩坏3" if match[1] == "崩坏3rd" else match[1]
    return name if name in GAME_NAMES else None


def notification_details(message: str, overseas_errors: Mapping[str, str]) -> List[str]:
    """Format known business fields from the upstream report for the private chat."""
    # Upstream includes each overseas API error verbatim after its game heading.
    for game, raw_error in overseas_errors.items():
        heading = f"\n{game}：\n"
        message = message.replace(f"{heading}{raw_error}", f"{heading}本次签到失败", 1)

    report: List[Tuple[str, List[str]]] = []
    overseas = False
    discard_response = False
    task = ""
    for raw in message.splitlines():
        line = raw.strip()
        if line.endswith((":", "：")):
            heading = line[:-1]
            if heading == "海外版":
                overseas = True
                task = ""
                continue
            if heading in TASK_NAMES:
                task = heading
                name = f"{task}（国际服）" if overseas and task in GAME_NAMES else task
                report.append((name, []))
                discard_response = False
                continue
        if not task or discard_response:
            continue
        if line.startswith("脚本签到失败，json 文本："):
            report[-1][1].append("签到失败")
            discard_response = True
            continue
        detail = _detail(task, line)
        if detail:
            name, parts = report[-1]
            if detail.startswith("连续签到 ") and parts:
                report.append((name, [detail]))
            else:
                parts.append(detail)
    return [f"{name} · {'，'.join(parts)}" for name, parts in report if parts]


def _detail(task: str, line: str) -> str | None:
    if task == "米游社":
        if line == "今天已经全部完成了！":
            return "今日任务已完成"
        count = COIN_COUNT.fullmatch(line)
        if count:
            label = {
                "一共获得": "今日获得",
                "今天已经获得": "今日获得",
                "还能获得": "还能获得",
                "目前有": "当前余额",
            }[count[1]]
            return f"{label} {int(count[2]):,} 米游币"
    if task in GAME_NAMES:
        days = CONSECUTIVE_DAYS.fullmatch(line)
        if days:
            return f"连续签到 {int(days[1])} 天"
        cn = CN_REWARD.fullmatch(line)
        if cn:
            return f"今日奖励 {cn[1]} ×{int(cn[2]):,}"
        os_reward = OS_REWARD.fullmatch(line)
        if os_reward:
            return f"今日奖励 {os_reward[2]} ×{int(os_reward[1]):,}"
    if task in {"云原神", "云绝区零"}:
        gain = CLOUD_GAIN.fullmatch(line)
        if gain:
            return f"获得 {int(gain[1])} 分钟免费时长"
        balance = CLOUD_BALANCE.fullmatch(line)
        if balance:
            duration = f"剩余 {int(balance[1])} 小时 {int(balance[2])} 分钟"
            return f"{duration}，{balance[3]} {int(balance[4]):,}"
    if line == "今天已经签到过":
        return "今日已签到"
    if line == "请手动签到一次":
        return "首次绑定，请手动签到一次"
    if line == f"并没有绑定任何{task}账号":
        return "暂无已绑定角色"
    if line.endswith("，触发验证码，本次签到失败"):
        return "需要验证码"
    if line.endswith("，本次签到失败") or line in {
        "本次签到失败",
        "脚本签到发生异常，请查看日志",
    }:
        return "签到失败"
    if line in {"token 失效/防沉迷", "云原神 token 失效"}:
        return "凭据异常"
    return None
