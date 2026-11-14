"""#43 主機履歷／事件與維運紀錄 功能驗收（ESJE 第 8 點：新增、查詢、空狀態都驗過即 100%）。

test_host_ops.py 已驗寫入規則與權限；這裡補三件缺的：
1. 新增 → 用同一支查詢 API 讀回來看得到（不是只看 MD）；
2. 查詢：期間切換、別台查不到這筆；
3. 空狀態：一台完全沒事件、沒來源資料 → 事件 0 筆但標「未取得」，不是「正常」；畫面空白文字講清楚下一步。
資料全虛構（10.99.x）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "asset_module"))

from test_host_ops import _h, env  # noqa: E402,F401 - 共用同一個 API 測試環境（fixture）

FE = ROOT / "APP" / "asset-module" / "frontend" / "components" / "v2" / "HostOps.vue"


def test_新增後_查詢API讀得回來_別台看不到(env):
    c = env.conn()
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    c.close()
    a = env.client("alice")
    ev = a.get("/api/v2/machine/ops", params={"key": "APPDB01", "period": "90d"}).json()["events"][0]
    assert ev["notes"] == [], "還沒人填"
    w = a.post("/api/v2/machine/ops/notes", json={"key": "APPDB01", "event_id": ev["id"], "kind": "action", "text": "清暫存"})
    assert w.status_code == 200
    back = a.get("/api/v2/machine/ops", params={"key": "APPDB01", "period": "90d"}).json()
    got = next(e for e in back["events"] if e["id"] == ev["id"])
    assert [n["text"] for n in got["notes"]] == ["清暫存"] and got["notes"][0]["created_by"].startswith("alice")
    other = a.get("/api/v2/machine/ops", params={"key": "APPWEB02", "period": "90d"}).json()
    assert not any(n.get("text") == "清暫存" for e in other["events"] for n in e["notes"]), "別台不得出現這筆"


def test_查詢_期間外的事件不列_處理紀錄列孤兒不消失(env):
    c = env.conn()
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    c.close()
    a = env.client("alice")
    ev = a.get("/api/v2/machine/ops", params={"key": "APPDB01", "period": "90d"}).json()["events"][0]
    a.post("/api/v2/machine/ops/notes", json={"key": "APPDB01", "event_id": ev["id"], "kind": "action", "text": "清暫存"})
    c = env.conn()      # 告警已解除：事件只在 10-20，「今天」查不到它
    c.execute("INSERT INTO whatsup_alert_history (at, device_id, name, ip, event, problem) VALUES "
              "('2026-10-20 09:00:00','d1','appdb01','10.99.1.10','cleared','Disk Free Space - C:')")
    c.commit()
    c.close()
    today =a.get("/api/v2/machine/ops", params={"key": "APPDB01", "period": "today"}).json()
    assert ev["id"] not in [e["id"] for e in today["events"]]
    assert any(n["event_id"] == ev["id"] for n in today.get("orphan_notes") or []), "期間外的處理紀錄要講得出來在哪"


def test_空狀態_沒事件沒來源_不是正常(env):
    a = env.client("vera")
    d = a.get("/api/v2/machine/ops", params={"key": "APPWEB02", "period": "30d"}).json()
    assert d["events"] == [] and d["evidence_any"] is False
    assert all(x["state"] != "has_data" for x in d["coverage"]), "沒有來源資料不能顯示有資料"


def test_空狀態_畫面文字講下一步_示範資料標明():
    v = FE.read_text(encoding="utf-8")
    assert "來源沒資料不等於正常" in v and "不代表正常" in v
    assert "示範資料" in v and "不會存進系統" in v
