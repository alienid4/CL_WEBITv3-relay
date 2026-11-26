"""2-1 標籤欄＋批次加標籤＋密碼代號自動標「試過A失敗」＋下方表研判同一份判斷（v1.468.6）。

2026-10-19 使用者（公司機）：「我納管失敗的，可同時幫它們上備註標籤嗎」「譬如我測試過 A 密碼，失敗」。
同一份機器標籤（asset_tags，掛在「台」上），不另開存放處。沒登記的位址沒有「台」——
每一列都回結果「未登記，無法加標籤」，不靜默略過。密碼只記代號，**密碼字串絕不進標籤／備註**。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import asset_tags as at  # noqa: E402
import db  # noqa: E402
import fingerprint  # noqa: E402
import host_kind as hk  # noqa: E402

FE = ROOT / "APP" / "asset-module" / "frontend"
ADOPT = (FE / "pages" / "adopt.vue").read_text(encoding="utf-8")
BATCH = (FE / "components" / "BatchOnboardModal.vue").read_text(encoding="utf-8")
ONB = (FE / "components" / "OnboardModal.vue").read_text(encoding="utf-8")
TAGM = (FE / "components" / "TagBatchModal.vue").read_text(encoding="utf-8")
ROUTER = (ROOT / "APP" / "asset-module" / "backend" / "routers" / "assets.py").read_text(encoding="utf-8")


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="S-1", hostname="app01", ip="10.99.3.11", asset_status="使用中")
    db.insert_hardware(c, asset_serial="S-2", hostname="app02", ip="10.99.3.12", asset_status="使用中")
    c.commit()
    return c


def test_批次加標籤_每列回結果_沒登記的寫出來(tmp_path):
    c = _conn(tmp_path)
    out = at.add_tag_batch(c, [{"ip": "10.99.3.11"}, {"ip": "10.99.3.12", "asset_serial": "S-2"},
                               {"ip": "10.99.3.99"}], "試過A失敗", "2026-10-19 21:00 帳密被拒", by="tester")
    assert out["ok"] == 2 and out["failed"] == 1
    by = {r["ip"]: r for r in out["results"]}
    assert by["10.99.3.11"]["asset_serial"] == "S-1", "沒給序號就用 IP 找登記"
    assert by["10.99.3.99"] == {"ip": "10.99.3.99", "asset_serial": None, "ok": False,
                                "reason": at.UNREGISTERED_REASON}
    assert [t["tag"] for t in at.list_tags(c, "S-1")] == ["試過A失敗"], "同一份標籤，資產頁看得到"


def test_整批查標籤_跟單台查同一份(tmp_path):
    c = _conn(tmp_path)
    at.add_tag(c, "S-1", "密碼待 PAM 確認", "問過了", by="a")
    at.add_tag(c, "S-1", "試過A失敗", "", by="a")
    got = at.tags_by_serial(c, ["S-1", "S-2", "NOPE"])
    assert sorted(t["tag"] for t in got["S-1"]) == sorted(t["tag"] for t in at.list_tags(c, "S-1"))
    assert "S-2" not in got and "NOPE" not in got


def test_標籤太長或空的擋下(tmp_path):
    c = _conn(tmp_path)
    for bad in ("", "x" * 21):
        try:
            at.add_tag_batch(c, [{"ip": "10.99.3.11"}], bad)
        except ValueError:
            continue
        raise AssertionError(f"應該擋下：{bad!r}")


def test_API_有認證_有操作紀錄():
    for path in ('"/api/asset-tags/batch"', '"/api/asset-tags/by-serial"'):
        seg = ROUTER.split(path)[1][:600]
        assert "Depends(require_auth)" in seg, path
    assert "批次加標籤" in ROUTER.partition('"/api/asset-tags/batch"')[2][:3000]


# ===================== 密碼代號：密碼絕不進標籤 =====================

def test_自動標籤只送代號與IP_不送密碼():
    for src, fn in ((BATCH, "async function autoTag("), (ONB, "async function autoTag(")):
        body = src.split(fn)[1].split("\n}\n")[0]
        assert "authFailTag(label" in body and "/api/asset-tags/batch" in body
        assert "password" not in body, "自動貼標籤的程式不可以碰到密碼"
    lbl = (FE / "composables" / "passwordLabel.ts").read_text(encoding="utf-8")
    assert "tag: `試過${label.trim()}失敗`" in lbl
    assert "note: `${at} ${reason}`" in lbl, "備註只有時間＋短原因"
    # 代號擋掉密碼本身／看起來像密碼的
    assert "代號不可以是密碼或密碼的一部分" in lbl and "這個代號看起來像密碼" in lbl
    assert "只記代號，不存密碼" in BATCH and "只記代號，不存密碼" in ONB


def test_自動標籤的呼叫端只傳代號():
    """呼叫端也要釘：就算 autoTag 本身乾淨，呼叫時把密碼塞進參數一樣會外流（審查 #5）。"""
    assert re.findall(r"autoTag\(([^)]*)\)", BATCH.split("async function autoTag(")[0]) == ["label"]
    assert re.findall(r"await autoTag\(([^)]*)\)", ONB) == ["label, r.message"]
    # 只有「登入被拒」才貼試過<代號>失敗（不認「需要 sudo 密碼」這類）
    assert "AUTH_REJECT_RE.test" in BATCH and "AUTH_REJECT_RE.test" in ONB


# ===================== 畫面 =====================

def test_前端_標籤欄可排序_點標籤篩選_兩張表都有():
    assert re.search(r'<SortTh k="tags_sort" :active="dSortKey"', ADOPT)
    assert re.search(r'<SortTh k="tags_sort" :active="mKey"', ADOPT)
    assert "dTag = dTag === t ? '' : t" in ADOPT and "mTag = mTag === t ? '' : t" in ADOPT
    assert "'/api/asset-tags/by-serial'" in ADOPT


def test_前端_加標籤模式_每列都勾得起來_沒登記的照實回():
    assert "type PickMode = 'onboard' | 'move' | 'tag'" in ADOPT
    assert "🏷 加標籤</button>" in ADOPT and "🏷 幫勾選的加標籤" in ADOPT
    assert "tagSelected.value = new Set()" in ADOPT.split("function clearPicks()")[1][:200]
    for p in ("root 不可遠端登入", "密碼待 PAM 確認", "待開 SSH", "待開 WinRM", "非本處管理"):
        assert p in TAGM
    assert "未登記，無法加標籤" in TAGM
    assert re.search(r'<SortTh k="ok"', TAGM), "結果表可排序"


def test_前端_批次納管後_把失敗的加標籤():
    assert "🏷 把失敗的 {{ count.failed + count.stopped }} 台加標籤" in BATCH
    assert ":preset-tag=\"tagPreset\"" in BATCH


# ===================== 下方表研判跟上方表同一份判斷 =====================

def test_網段掃描研判_TTL64不判Linux_閘道位址同一條規則():
    assert fingerprint.guess_os(ttl=64) is None, "舊程式回「Linux/Unix（TTL≈64）」"
    assert "Linux" in fingerprint.guess_os(ttl=64, open_ports=[22])
    assert hk.display_guess("Linux/Unix（TTL≈64）", ip="10.99.5.1", registered=0, ttl=64,
                            open_ports=None).startswith("網路設備（閘道位址）")
    assert hk.display_guess("Linux/Unix（TTL≈64）", ip="10.99.5.31", registered=0, ttl=64,
                            open_ports=None) is None, "舊存的 TTL-only Linux 依現在的規則重算"
    assert hk.display_guess("Windows（SSH banner 自報）", ip="10.99.5.40", registered=0, ttl=127,
                            open_ports="22,3389") == "Windows（SSH banner 自報）", "banner 自報的照舊"
