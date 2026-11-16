"""R9 預設帳號：shell 拿不到（None／空字串）時不可判 pass（拿不到資料 != 通過）。W221-011。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_rules  # noqa: E402


def _acc(shell, can_login, **kw):
    a = {"kind": "default", "os_family": "linux", "pw_status": "locked_not", "shell": shell,
         "can_login": can_login, "uid": 0, "username": "root"}
    a["pw_status"] = kw.pop("pw_status", "set")
    a.update(kw)
    return a


def test_shell為None時不判pass():
    v, _ = account_rules._r9_default_account(_acc(None, False), {})
    assert v == "unknown"


def test_shell為空字串時不判pass():
    v, _ = account_rules._r9_default_account(_acc("", False), {})
    assert v == "unknown"


def test_明確nologin仍判pass():
    v, _ = account_rules._r9_default_account(_acc("/sbin/nologin", False), {})
    assert v == "pass"


def test_可登入仍判fail():
    v, _ = account_rules._r9_default_account(_acc("/bin/bash", True), {})
    assert v == "fail"


def test_已鎖定仍判pass():
    v, _ = account_rules._r9_default_account(_acc(None, False, pw_status="locked"), {})
    assert v == "pass"
