import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Drop ANSI SGR escapes from `text`.

    Typer 0.25 renders --help via Rich panels; even with NO_COLOR=1, Rich
    still wraps flag names in `\\x1b[1m...\\x1b[0m` bold spans that split
    `--mode` into `\\x1b[1m-\\x1b[0m\\x1b[1m-mode\\x1b[0m`. Tests that look
    for flag substrings in help output must strip these first.
    """
    return _ANSI_RE.sub("", text)
