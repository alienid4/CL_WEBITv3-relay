"""V2 移植清單第 9 批起（2.048.16 起）：V49 系統組報告、V46 主機分類……改 V2 原生（純搬移、行為不變）。
說明見 docs/ui-v2/V2移植_B區_批9_2026-11-05.md。"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
BE = ROOT / "APP" / "asset-module" / "backend"
POL = (BE / "access_policy.py").read_text(encoding="utf-8")


def _src(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def _code(rel: str) -> str:
    return "\n".join(l for l in _src(rel).splitlines() if not l.lstrip().startswith("//"))


def _ported() -> str:
    m = _src("utils/v2LegacyMap.ts")
    return m[m.index("export const PORTED"):m.index("export function isPorted")]


def _set(name: str) -> str:
    s = POL[POL.index(f"{name}: frozenset"):]
    return s[:s.index("\n)\n")]


def test_V49_V46_改V2原生_不再嵌入舊頁():
    r = _src("pages/v2/report.vue")
    assert "<ClientOnly v-else><V2SystemGroup /></ClientOnly>" in r and "pages/reports/system-group.vue" not in r
    d = _src("pages/v2/datafix.vue")
    assert "<V2Classify @changed=\"load('classify', true)\" />" in d and "pages/reports/classify.vue" not in d
    assert 'data-testid="classify-scope-note"' in d
    for p in ["'/reports/system-group'", "'/reports/classify'"]:
        assert p in _ported(), p
    for comp, apis in (("components/v2/SystemGroup.vue", ["/api/reports/system-group'", "/api/reports/system-group/drill",
                                                          "/api/reports/system-group/note", "/api/reports/system-group/snapshots"]),
                       ("components/v2/Classify.vue", ["/api/classify/summary", "/api/classify${qs}", "'/api/classify', { method: 'PUT'",
                                                       "/api/classify/seed"])):
        src = _src(comp)
        assert "<V2Table" in src and "<table" not in src and "useLatest" in src and "~/pages/" not in src, comp
        for x in apis:
            assert x in src, (comp, x)


def test_V49_V46_寫入權限跟後端一致():
    usr, adm = _set("USER_WRITE"), _set("ADMIN_WRITE")
    assert '"/api/reports/system-group/note"' in usr and '"/api/reports/system-group/snapshots"' in usr
    assert '"/api/classify"' in usr and '"/api/classify/seed"' in adm
    g = _code("components/v2/SystemGroup.vue")
    assert '<button v-if="canWrite" type="button" class="btn2" :disabled="saving" @click="saveSnapshot">' in g
    assert '<button v-else-if="canWrite" type="button" class="note"' in g
    c = _code("components/v2/Classify.vue")
    assert '<div v-if="canWrite" class="batch"' in c and ':selectable="canWrite"' in c
    assert '<div v-if="isAdmin" class="bar">' in c                    # 帶入分類限管理員（舊頁一般帳號按得到、被後端擋）


def test_V46_帶入預設試算_確認才寫():
    c = _code("components/v2/Classify.vue")
    assert "fd.append('commit', commit ? 'true' : 'false')" in c
    assert '@click="runSeed(false)">試算（不寫入）' in c
    assert ':disabled="seeding || !seedResult || !seedResult.dry_run" @click="runSeed(true)">確認寫入' in c


def test_V49_下鑽條件在網址_null送空字串():
    g = _code("components/v2/SystemGroup.vue")
    assert "query[DK[k]] = v === null || v === undefined ? '' : String(v)" in g
    assert "dl('cluster', { vcenter: c.vcenter, cluster: c.cluster, version: c.version })" in g
    assert "dl('platform', { status: c.s, registered_only: 'true' })" in g       # 摘要卡帶 registered_only，口徑對齊卡片


def _mini_db():
    sys.path.insert(0, str(BE))
    import system_report  # noqa: PLC0415
    return system_report


def test_drill_cluster_空字串對到沒有值的列(monkeypatch):
    """表3「（未歸叢集）」「版本未知」那幾列：前端送空字串，下鑽筆數要＝格子數字（2.048.16 補）。"""
    sr = _mini_db()
    hosts = [{"host": "esx1", "vcenter": "vc.example.test", "cluster": None, "version": None},
             {"host": "esx2", "vcenter": "vc.example.test", "cluster": "C1", "version": "VMware ESXi 7.0.3 build-1"},
             {"host": "esx3", "vcenter": "vc.example.test", "cluster": "C1", "version": None}]
    monkeypatch.setattr(sr, "_vhosts", lambda conn: hosts)
    monkeypatch.setattr(sr, "vcenter_location", lambda vc, conn, cl=None: "A 機房")
    conn = sqlite3.connect(":memory:")
    assert [h["host"] for h in sr.drill_cluster(conn, vcenter="vc.example.test", cluster="", version="")] == ["esx1"]
    assert [h["host"] for h in sr.drill_cluster(conn, vcenter="vc.example.test", cluster="C1", version="")] == ["esx3"]
    assert [h["host"] for h in sr.drill_cluster(conn, vcenter="vc.example.test", cluster="C1", version="VMware ESXi 7.0.3")] == ["esx2"]
    assert len(sr.drill_cluster(conn, cluster="C1")) == 2                         # 不帶 version＝整個叢集（行為不變）


def test_虛擬化環境_None與空字串同一列_每列下鑽等於臺數(monkeypatch):
    """審查 P2：None 與 "" 分組要跟 drill_cluster 同一條規則，否則兩列各自下鑽都拿到兩列合計；vCenter 空的那列也要對得上。"""
    sr = _mini_db()
    hosts = [{"host": "e1", "vcenter": None, "cluster": None, "version": None},
             {"host": "e2", "vcenter": "", "cluster": "", "version": None},
             {"host": "e3", "vcenter": "vc.example.test", "cluster": "C1", "version": "VMware ESXi 8.0.1 build-2"}]
    monkeypatch.setattr(sr, "_vhosts", lambda conn: hosts)
    monkeypatch.setattr(sr, "vcenter_location", lambda vc, conn, cl=None: "A 機房")
    monkeypatch.setattr(sr, "physical_hosts_report", lambda conn: [])
    conn = sqlite3.connect(":memory:")
    cl = sr.virtualization_env(conn)["clusters"]
    assert sorted(c["count"] for c in cl) == [1, 2]
    for c in cl:
        assert len(sr.drill_cluster(conn, vcenter=c["vcenter"] or "", cluster=c["cluster"] or "", version=c["version"] or "")) == c["count"], c


def test_verify_numbers_有系統組報告加總等於下鑽():
    v = (BE / "verify_numbers.py").read_text(encoding="utf-8")
    assert '@section("系統組報告（10.1）加總＝下鑽")' in v
    for x in ["drill_platform(conn, status=s, registered_only=True)", "drill_platform(conn, retired=True)",
              "drill_cluster(conn, location=loc[\"location\"], service=r[\"service\"])", 'version=c["version"] or ""']:
        assert x in v, x


# ===== 第 10 批（2.048.18）：V27 資料品質、V42 照片／機櫃圖對帳 =====

def test_V27_V42_改V2原生_不再嵌入舊頁():
    d = _src("pages/v2/datafix.vue")
    assert "<ClientOnly v-else-if=\"tab === 'quality'\"><V2DataQuality /></ClientOnly>" in d and "pages/data-quality.vue" not in d
    r = _src("pages/v2/inventory/rack.vue")
    assert "<ClientOnly v-else-if=\"tab === 'photo'\"><V2RackDiagram /></ClientOnly>" in r and "pages/rack-diagram.vue" not in r
    for p in ["'/data-quality'", "'/rack-diagram'"]:
        assert p in _ported(), p
    for comp, apis in (("components/v2/DataQuality.vue", ["'/api/data-quality'", "/api/data-quality/${", "/api/measured-mismatch", "/api/trust/distribution",
                                                          "/api/trust/hosts", "/api/data-quality/field-fill", "/api/data-quality/single-person-names",
                                                          "/api/data-quality/person-merge/preview", "'/api/data-quality/person-merge'", "<SshProbe />"]),
                       ("components/v2/RackDiagram.vue", ["/api/rack-diagram/imports'", "/reconcile`", "/diff`", "/reviews`", "/api/rack-diagram/import'",
                                                          "/api/rack-diagram/rows/${x.row_id}/review", "/cia-fix.xlsx", "/api/rack-diagram/export.xlsx"])):
        src = _src(comp)
        assert "<V2Table" in src and "<table" not in src and "useLatest" in src and "~/pages/" not in src, comp
        for x in apis:
            assert x in src, (comp, x)


def test_V27_V42_權限跟後端一致():
    usr, adm = _set("USER_WRITE"), _set("ADMIN_WRITE")
    assert '"/api/data-quality/person-merge"' in usr and '"/api/rack-diagram/rows/{row_id}/review"' in usr
    assert '"/api/rack-diagram/import"' in adm
    exp = POL[POL.index("_ADMIN_EXPORTS"):POL.index("_USER_EXPORTS")]
    assert '"/api/rack-diagram/export.xlsx"' in exp and '"/api/rack-diagram/imports/{iid}/cia-fix.xlsx"' in exp
    dq = _code("components/v2/DataQuality.vue")
    assert '<button v-if="canWrite" type="button" class="btn2 danger" :disabled="mergeBusy" @click="doMergeApply">' in dq
    rd = _code("components/v2/RackDiagram.vue")
    assert '<div v-if="isAdmin" class="bar">' in rd                                   # 匯入
    assert '<a v-if="isAdmin" class="lnk" :href="`${apiBase}/api/rack-diagram/imports/${rc.import.id}/cia-fix.xlsx`">' in rd
    assert '<div v-if="isAdmin" class="chips">' in rd                                  # 匯出現場格式
    assert '<template v-if="canWrite">' in rd                                          # 確認／判讀錯了


def test_V27_人名合併_先預覽再確認():
    dq = _code("components/v2/DataQuality.vue")
    assert "if (!window.confirm(`確定把「${p.from_name}」全部改成「${p.to_name}」？共 ${p.total} 筆，這個動作會直接改資料。`)) return" in dq
    # 送的是預覽過的那一組＋預覽代號（Codex 1145／1149：候選換了後端回 409）
    assert "body: { from_name: p.from_name, to_name: p.to_name, preview_token: p.preview_token }" in dq
    assert "if (!p || !p.total) return" in dq


def test_V42_確認走guardedFetch_只出報告():
    rd = _code("components/v2/RackDiagram.vue")
    assert "await guardedFetch<any>(`/api/rack-diagram/rows/${x.row_id}/review`, { method: 'POST', body: { action, note: notes[x.row_id] || null } })" in rd
    assert "/api/cia" not in rd.replace("cia-fix", "")                                 # 不寫 CIA


def test_verify_numbers_可信度分布加總等於下鑽():
    v = (BE / "verify_numbers.py").read_text(encoding="utf-8")
    assert 'trust_score.hosts_with(conn, b["score"], None)' in v and 'trust_score.hosts_with(conn, None, c["combo"])' in v


def _vn():
    sys.path.insert(0, str(BE))
    import verify_numbers  # noqa: PLC0415
    return verify_numbers


def test_verify_numbers_主流程有跑系統組報告():
    """審查發現：2.048.16 加了 check_system_group 卻沒排進 main() 的清單，部署關卡 [2d/7] 根本沒跑。"""
    v = (BE / "verify_numbers.py").read_text(encoding="utf-8")
    main = v[v.index("for check in ("):]
    main = main[:main.index(")")]
    assert "check_system_group," in main and "check_data_quality," in main


def _fake_trust(monkeypatch, rows, dist):
    import trust_score  # noqa: PLC0415
    calls = {"n": 0}

    def compute_all(conn):
        calls["n"] += 1
        return {r["asset_serial"]: r for r in rows}
    monkeypatch.setattr(trust_score, "compute_all", compute_all)
    if dist is not None:
        monkeypatch.setattr(trust_score, "distribution", lambda conn: dist)
    return calls


def test_可信度下鑽檢查_對得上就綠_只算一次(monkeypatch):
    vn = _vn()
    rows = [{"asset_serial": "A", "score": 100, "sources": {"cia": True, "rvtools": True}},
            {"asset_serial": "B", "score": 50, "sources": {"cia": True, "rvtools": False}}]
    dist = {"total": 2, "by_score": [{"score": 100, "count": 1}, {"score": 50, "count": 1}],
            "by_combo": [{"combo": "cia+rvtools", "count": 1}, {"combo": "cia", "count": 1}]}
    calls = _fake_trust(monkeypatch, rows, dist)
    before = vn.FAILS
    vn.check_trust_drill(None)
    assert vn.FAILS == before
    assert calls["n"] == 1                                                    # 正式庫只算一次


def test_可信度下鑽檢查_格子跟下鑽對不上會紅(monkeypatch):
    vn = _vn()
    rows = [{"asset_serial": "A", "score": 100, "sources": {"cia": True}}]
    dist = {"total": 2, "by_score": [{"score": 100, "count": 2}], "by_combo": [{"combo": "cia", "count": 1}]}
    _fake_trust(monkeypatch, rows, dist)
    before = vn.FAILS
    vn.check_trust_drill(None)
    assert vn.FAILS == before + 2                                             # 下鑽對不上＋加總對不上
