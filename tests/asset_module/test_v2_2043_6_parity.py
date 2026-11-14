"""2.043.6：V2 移植清單 N 區「畫面欄位／條件對照缺口」（N1、N13、N14、N15、N22…）。全部假資料。

後端只有 N13（2.1 主機查詢的機房／環境複選、只看多筆登記）有新條件，其他是畫面接線；
這裡釘「條件真的篩、頁籤筆數不受影響」與「畫面接上了舊頁有的欄位／子清單」。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import v2_hosts  # noqa: E402
from test_v2_batch1 import _conn, _seed  # noqa: E402


def _read(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def test_N13_機房環境複選與只看多筆登記(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        base = v2_hosts.listing(conn, "all")
        assert "甲機房" in "".join(base["facets"]["sites"]) and base["facets"]["envs"]
        site = next(s for s in base["facets"]["sites"] if "甲機房" in s)
        only = v2_hosts.listing(conn, "all", sites=site)
        assert [r["name"] for r in only["rows"]] == ["aix01"]
        assert only["tabs"] == base["tabs"], "條件只篩列，頁籤筆數不變"
        assert [r["name"] for r in v2_hosts.listing(conn, "all", multi=True)["rows"]] == ["aix01"], "兩筆登記的那台"
        env = base["facets"]["envs"][0]
        n_env = len(v2_hosts.listing(conn, "all", envs=env)["rows"])
        assert 0 < n_env <= len(base["rows"])
        # 匯出（size=0）跟畫面走同一份條件
        assert len(v2_hosts.listing(conn, "all", sites=site, size=0)["rows"]) == len(only["rows"])
    finally:
        conn.close()


def test_N13_畫面帶條件給後端():
    v = _read("pages/v2/hosts.vue")
    assert "sites: String(route.query.sites" in v and "envs: String(route.query.envs" in v and "multi: route.query.multi === '1'" in v
    assert "togFacet('sites'" in v and "togFacet('envs'" in v and "togMulti" in v


def test_N1_實測不符_欄位與需人處理與對照欄():
    v = _read("pages/v2/datafix.vue")
    for s in ("q('mf')", "q('mn') === '1'", "label: '對照'", "label: '需誰處理'", "label: '信心'", "tone: (r: any) => (r.needs && r.cia"):
        assert s in v, s


def test_N14_合併規則_成員明細():
    v = _read("pages/v2/reconcile.vue")
    for s in ("mkMembers", "label: 'MAC'", "label: '硬體序號'", "label: '登記序號'", "g: r.name"):
        assert s in v, s


def test_N15_設備風險子清單():
    v = _read("pages/v2/risk.vue")
    for s in ("viewTo('unreadable')", "view: 'undetermined'", "viewTo('ok')", "viewTo('none')",
              "uptime?.unreadable", "spof?.undetermined", "contract?.ok_rows", "contract?.none_rows"):
        assert s in v, s
    m = _read("utils/v2LegacyMap.ts")
    assert "'/risk': q => to('/v2/risk', pick(q, ['tab', 'view']))" in m


def test_N22_服務清單有綁定位址():
    v = _read("pages/v2/inventory/service.vue")
    assert "bind: r.bind_addr" in v and "label: '綁定位址'" in v


def test_V70_軟體盤點依主機():
    v = _read("pages/v2/inventory/software.vue")
    # 2.043.7 起一台＝machine_key：/api/software/by-machine（清單與單台套件同一支）
    assert "{ k: 'hosts', label: '依主機' }" in v and "/api/software/by-machine" in v
    assert "/software?legacy=1" not in v, "依主機搬完，舊版入口拿掉"


def test_V71_儲存容量_實體主機磁碟_已排除_門檻():
    v = _read("pages/v2/storage.vue")
    for s in ("{ k: 'phys', label:", "{ k: 'excluded', label:", "datastores.excluded", "data.value?.alerts"):
        assert s in v, s
    assert "/storage?legacy=1" not in v
    # 2026-11-14：Datastore 改用 1.5 每日檢核同一把尺（剩餘 GB，lun_low），本頁不再有臨時 % 門檻
    assert "parseThreshold(route.query.th)" not in v
    be = (ROOT / "APP" / "asset-module" / "backend" / "storage_capacity.py").read_text(encoding="utf-8")
    assert 'p["by"] != "mount"' in be and 'disk_level.listed(used)' in be and 'if v["lun_low"]' in be
    ut = _read("utils/storageAlerts.ts")
    assert "p.by === 'mount'" in ut and "v.free_pct < th" in ut
