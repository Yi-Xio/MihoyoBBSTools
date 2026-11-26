from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Never

MAX_MATRIX_JOBS = 256


def load_accounts() -> Sequence[object]:
    """
    Read the account array from the Environment Secret.

    Returns:
        Sequence[object]: Account entries in the configured order.

    Raises:
        ValueError: When the Secret cannot define a GitHub Actions matrix.
    """
    secret = os.environ.pop("MIHOYO_ACCOUNTS", "")
    try:
        accounts: object = json.loads(secret, parse_constant=_reject_json_constant)
    except (ValueError, RecursionError):
        raise ValueError("Invalid account array") from None
    if not isinstance(accounts, list) or not 1 <= len(accounts) <= MAX_MATRIX_JOBS:
        raise ValueError("Invalid account array length")
    return cast("Sequence[object]", accounts)


def main() -> int:
    """
    Emit only numbered account positions for the matrix.

    Returns:
        int: Zero when the matrix is written, one on failure.
    """
    try:
        accounts = load_accounts()
    except ValueError:
        print("MIHOYO_ACCOUNTS 须为包含 1 至 256 项的 JSON 数组。", file=sys.stderr)
        return 1

    try:
        positions = json.dumps(list(range(1, len(accounts) + 1)))
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"accounts={positions}\n")
    except (KeyError, OSError):
        print("无法输出账号矩阵。", file=sys.stderr)
        return 1
    return 0


def _reject_json_constant(value: str) -> Never:
    raise ValueError("Non-standard JSON number")


if __name__ == "__main__":
    raise SystemExit(main())
