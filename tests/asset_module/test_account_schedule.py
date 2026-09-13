"""帳號盤點掛進夜跑排程：預設開、可關、失敗不拖累掃描、排在服務之後。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import scan_service  # noqa: E402


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _swap(fake):
    sys.modules["account_inventory_backup"] = sys.modules.get("account_inventory")
    sys.modules["account_inventory"] = fake


def _restore():
    if sys.modules.get("account_inventory_backup"):
        sys.modules["account_inventory"] = sys.modules.pop("account_inventory_backup")
    else:
        sys.modules.pop("account_inventory", None)


def test_預設開啟且會收帳號():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            called = {}

            class Fake:
                @staticmethod
                def collect_accounts(c, trigger=None, **kw):
                    called["trigger"] = trigger
                    return {"status": "ok"}

            _swap(Fake)
            try:
                scan_service._post_scan_collect_accounts(conn)
            finally:
                _restore()
            assert called.get("trigger") == "schedule"
        finally:
            conn.close()


def test_關掉開關就不收():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            db.set_setting(conn, "account_collect_enabled", "0")
            called = {}

            class Fake:
                @staticmethod
                def collect_accounts(c, trigger=None, **kw):
                    called["hit"] = True
                    return {}

            _swap(Fake)
            try:
                scan_service._post_scan_collect_accounts(conn)
            finally:
                _restore()
            assert "hit" not in called
        finally:
            conn.close()


def test_採集炸掉不外拋():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            class Boom:
                @staticmethod
                def collect_accounts(c, trigger=None, **kw):
                    raise RuntimeError("ssh 全掛")

            _swap(Boom)
            try:
                scan_service._post_scan_collect_accounts(conn)   # 不該拋
            finally:
                _restore()
        finally:
            conn.close()


def test_帳號收集排在服務之後():
    """順序：掃描→納管→服務→帳號。用原始碼位置釘住，避免後人調換。"""
    src = Path(scan_service.__file__).read_text(encoding="utf-8")
    svc = src.index("_post_scan_collect_services(conn)  #")
    acct = src.index("_post_scan_collect_accounts(conn)  #")
    assert svc < acct
