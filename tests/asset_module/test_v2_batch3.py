"""2.x 第三批（2.005.0）：6.1 納管作業（新設備上線逐台 5 步、納管進度對目標）、6.2／9.3 嵌入既有畫面、
9.1 待補資料、選單與舊頁橫幅。全部假資料（IP 一律 10.99.x）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import v2_hosts  # noqa: E402
import v2_onboard  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn):
    db.insert_hardware(conn, asset_serial="A-1", hostname="lnx01", ip="10.99.1.1", physical_location="01_甲機房",
                       rack_no="B2/12-13U", device_model="Dell R750", os="Red Hat Enterprise Linux 9")
    db.insert_hardware(conn, asset_serial="A-2", hostname="win01", ip="10.99.1.2", rack_no="VM", device_model="(VM)",
                       os="Windows Server 2019", is_vm="Y")
    db.insert_hardware(conn, asset_serial="A-3", hostname="sw01", ip="10.99.1.3", device_model="Cisco 9300", os="網路設備")
    db.insert_hardware(conn, asset_serial="A-4", hostname="old01", ip="10.99.1.4", os="Red Hat Enterprise Linux 7", asset_status="報廢")
    conn.commit()


def test_新設備上線_五步_退出不列_上線檢查看上線前檢查表(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    conn.execute("INSERT INTO golive_check (asset_serial, status) VALUES ('A-1', 'passed')")
    conn.commit()
    nd = v2_onboard.new_devices(conn)
    assert [s["label"] for s in nd["steps"]] == ["CIA 登記", "上架", "網路偵測", "納管", "上線檢查"]
    serials = {r["serial"] for r in nd["rows"]}
    assert "A-4" not in serials                       # 報廢是出口，不列
    r1 = next(r for r in nd["rows"] if r["serial"] == "A-1")
    assert r1["steps"]["golive"] is True and r1["steps"]["registered"] is True
    assert sum(nd["counts"].values()) == nd["total"] == len(nd["rows"])
    for r in nd["rows"]:                              # 下一步＝第一個沒完成的步驟
        assert r["next"] == next((k for k, _ in v2_onboard.STEPS if not r["steps"][k]), None)


def test_納管進度_已納管與2_1同一個數字_目標可設定_下鑽等於格子(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    pg = v2_onboard.progress(conn)
    assert pg["target_pct"] == 80.0
    tabs = {t["key"]: t["count"] for t in v2_hosts.listing(conn, "onboarded")["tabs"]}
    assert pg["onboarded_total"] == tabs["onboarded"]
    fams = {r["family"]: r for r in pg["rows"]}
    assert fams["Linux"]["server"] and not fams.get("網路設備", {"server": False})["server"]
    for r in pg["rows"]:
        assert r["n_online_onboarded"] <= r["n_online"] <= r["n_all"]
        for k in ("all", "online", "onboarded", "online_not_onboarded"):
            assert len(v2_onboard.drill(conn, r["family"], k)["rows"]) == len(r[k])
    db.set_setting(conn, "onboard_target_pct", "65")
    conn.commit()
    assert v2_onboard.progress(conn)["target_pct"] == 65.0
    db.set_setting(conn, "onboard_target_pct", "abc")
    conn.commit()
    assert v2_onboard.progress(conn)["target_pct"] == 80.0


def test_納管進度_下鑽_不認得的清單或大類回錯(tmp_path):
    import pytest
    conn = _conn(tmp_path)
    _seed(conn)
    with pytest.raises(ValueError):
        v2_onboard.drill(conn, "Linux", "bogus")
    with pytest.raises(LookupError):
        v2_onboard.drill(conn, "不存在的大類", "all")


def test_第三批頁面_版型_頁籤_舊頁橫幅_嵌入頁不重複橫幅():
    def page(p):
        return (FE / "pages" / p).read_text(encoding="utf-8")
    ob = page("v2/onboard.vue")
    for lab in ["待納管", "納管失敗", "已納管", "新設備上線", "納管進度", "掃描缺口"]:
        assert f"label: `{lab}" in ob or f"label: '{lab}" in ob, lab
    # 納管只呼叫既有元件，不自己打納管 API
    for comp in ["<OnboardModal", "<BatchOnboardModal", "<WinOnboardModal", "<RevokeModal", "<WinRevokeModal"]:
        assert comp in ob, comp
    assert "'/api/onboard'" not in ob and "/api/collect/dispatch'," not in ob
    df = page("v2/datafix.vue")
    for lab in ["依單位", "CIA 待更正", "實測不符", "聯絡資訊待補", "重複登記合併", "下線與報廢", "主機分類"]:   # 2026-10-28 改名／9.3 併入
        assert f"label: `{lab}" in df or f"label: '{lab}" in df, lab
    assert "/api/scan-shells/apply" not in df          # 併回改寫資料，留在舊版頁
    col = page("v2/collect.vue")
    assert "label: `SAN 交換器" in col and "label: `HMC" in col
    for p in ["v2/onboard.vue", "v2/collect.vue", "v2/datafix.vue", "v2/classify.vue"]:   # classify.vue＝轉址到 9.1 頁籤
        assert "layout: 'v2'" in page(p), p
    for old, to, emb in [("adopt.vue", "/v2/onboard", False), ("batch-onboard.vue", "/v2/onboard", True), ("pipeline.vue", "/v2/onboard?tab=progress", False),
                         ("lifecycle.vue", "/v2/onboard?tab=newdev", False), ("reports/scan-gaps.vue", "/v2/onboard?tab=gaps", False),
                         ("san.vue", "/v2/collect", True), ("hmc.vue", "/v2/collect?tab=hmc", True), ("data-fix.vue", "/v2/datafix", False),
                         ("corrections.vue", "/v2/datafix?tab=mismatch", False), ("scan-shells.vue", "/v2/datafix?tab=shells", False),
                         ("reports/classify.vue", "/v2/datafix?tab=classify", True)]:
        s = page(old)
        cond = ' v-if="!props.embedded"' if emb else ""
        want = f'<V2Banner{cond} to="{to}"'
        assert want in s, old
        if emb:
            assert "defineProps<{ embedded?: boolean }>()" in s, old


def test_9_2_數量對帳已有新版():
    # 2.007.12：9.2 改成 v2 頁（對帳總表｜帳實比對｜計算規則｜偵測證據），舊 7-8／7-7 頁上方有新版提示
    src = (FE / "composables" / "useNavV2.ts").read_text(encoding="utf-8")
    assert "{ no: '9.2', label: '數量對帳', to: '/v2/reconcile' }" in src
    assert (FE / "pages" / "v2" / "reconcile.vue").is_file()
    old = lambda p: (FE / "pages" / p).read_text(encoding="utf-8")
    assert '<V2Banner to="/v2/reconcile" label="9.2 數量對帳" />' in old("count-bridge.vue")
    assert '<V2Banner to="/v2/reconcile?tab=rule"' in old("machine-key.vue")
