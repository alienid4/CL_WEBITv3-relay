"""2.059.1（V36）：⚙ 資料匯入整頁改 V2 原生——釘住「純搬移」（⚠D4）：
每種來源打的是舊 pages/import.vue 同一支 API、兩段式／確認流程不變，設定頁不再嵌入舊頁，/import 已列 PORTED。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"


def _src(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def test_設定頁用V2原生_不再嵌入舊匯入頁():
    s = _src("pages/v2/settings.vue")
    assert "pages/import.vue')" not in s and "<V2Imports v-else-if=\"tab === 'import'\"" in s
    assert "  '/import'," in _src("utils/v2LegacyMap.ts").split("export function isPorted")[0]


def test_每種來源用舊頁同一支API():
    old = _src("pages/import.vue")
    new = "".join(_src(f"components/v2/{n}.vue") for n in ("Imports", "ImportCia", "ImportRv", "ImportOne", "ImportExports")) + _src("utils/importV2.ts")
    for api in ("/api/import/sources", "/api/import/excel/preview", "/api/import/excel/confirm", "/api/import/field-mapping",
                "/api/import/rvtools", "/api/merge-reviews/auto-matchable", "/api/merge-reviews/batch-merge", "/api/import/log",
                "/api/import/dynassets", "/api/business-systems/import", "/api/business-systems/import-class",
                "/api/reports/system-category/import", "/api/reports/system-category/template", "/api/segments/import",
                "/api/segments/export", "/api/ad/import", "/api/ad/unmatched-managers", "/api/ad/unmatched-accounts",
                "/api/ad/disabled-with-account", "/api/account-audit/import", "/api/account-audit/export?mode=standard",
                "/api/v2/alert-contacts/import", "/api/relocation/export", "/api/relocation/import", "/api/export"):
        assert api in old, api           # 舊頁真的有這支（防止新版多打一支舊頁沒有的）
        assert api in new, f"V2 少了 {api}"
    # 兩段式：CIA 不回頭用一步式；確認只送 token＋檔案雜湊
    cia = _src("components/v2/ImportCia.vue")
    assert "'/api/import/excel'," not in cia and "'/api/import/excel')" not in cia
    assert "body: { token: pv.token, file_sha256: pv.file_sha256 }" in cia and "<V2CiaPreview" in cia
    # RVTools 較舊檔要先問（stale_import）、同意一次整批套用、每次匯入歸零
    rv = _src("components/v2/ImportRv.vue")
    assert "d.code === 'stale_import'" in rv and "forceAll.value = false" in rv and "fd.append('data_at', fileDataAt(file))" in rv
    # 告警聯絡：確認綁預覽（plan_digest）、走 guardedFetch；憑證沿用 CertImportPanel
    one = _src("components/v2/ImportOne.vue")
    assert "fd.append('plan_digest'" in one and "guardedFetch<any>('/api/v2/alert-contacts/import'" in one
    assert "<CertImportPanel" in one and "<AccountAuditImport" in one
