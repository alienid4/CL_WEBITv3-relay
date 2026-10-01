"""服務採集掛進排程：順序（納管在前）、開關、失敗不拖累掃描。"""
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


def test_預設開啟且會呼叫採集():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            called = {}

            class FakeInventory:
                @staticmethod
                def collect_services(c, trigger=None, **kw):
                    called["trigger"] = trigger
                    return {"status": "ok"}

            sys.modules["service_inventory_backup"] = sys.modules.get("service_inventory")
            sys.modules["service_inventory"] = FakeInventory
            try:
                scan_service._post_scan_collect_services(conn)
            finally:
                if sys.modules.get("service_inventory_backup"):
                    sys.modules["service_inventory"] = sys.modules.pop("service_inventory_backup")
            assert called.get("trigger") == "schedule"
        finally:
            conn.close()


def test_關掉開關就不收():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            db.set_setting(conn, "service_collect_enabled", "0")
            called = {}

            class FakeInventory:
                @staticmethod
                def collect_services(c, trigger=None, **kw):
                    called["hit"] = True
                    return {}

            sys.modules["service_inventory_backup"] = sys.modules.get("service_inventory")
            sys.modules["service_inventory"] = FakeInventory
            try:
                scan_service._post_scan_collect_services(conn)
            finally:
                if sys.modules.get("service_inventory_backup"):
                    sys.modules["service_inventory"] = sys.modules.pop("service_inventory_backup")
            assert "hit" not in called
        finally:
            conn.close()


def test_採集炸掉不能外拋():
    """掃描已經成功了，服務收不到不該讓整次掃描被記成 failed。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            class Boom:
                @staticmethod
                def collect_services(c, trigger=None, **kw):
                    raise RuntimeError("ssh 全掛")

            sys.modules["service_inventory_backup"] = sys.modules.get("service_inventory")
            sys.modules["service_inventory"] = Boom
            try:
                scan_service._post_scan_collect_services(conn)   # 不該拋
            finally:
                if sys.modules.get("service_inventory_backup"):
                    sys.modules["service_inventory"] = sys.modules.pop("service_inventory_backup")
        finally:
            conn.close()


def test_採集排在自動納管之後():
    """順序寫在程式裡容易被後人調換，用測試釘住：這一輪剛納管的機器要能立刻被收到服務。"""
    src = Path(scan_service.__file__).read_text(encoding="utf-8")
    onboard_at = src.index("_post_scan_auto_onboard(conn)  #")
    services_at = src.index("_post_scan_collect_services(conn)  #")
    assert onboard_at < services_at
