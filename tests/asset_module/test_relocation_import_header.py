"""搬遷回填端點的 HTTP 標頭（Codex 42 號，2026-10-31）：標頭只能 ASCII；基準狀態是中文時原本會
UnicodeEncodeError。標頭改送穩定 ASCII 代碼，前端映射成中文。這裡打到實際端點（真 StreamingResponse）。"""
import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import relocation_import as ri  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(api.AUTH_DISABLED_ENV, "1")             # 登入停用＝假 admin，免登入即可打端點
    p = tmp_path / "t.db"
    db.init_db(p)

    def _o():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _o
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


@pytest.mark.parametrize("status,code", [(ri.ST_OK, "ok"), (ri.ST_MISSING, "missing"),
                                          (ri.ST_SUSPECT, "suspect"), (ri.ST_REF, "reference")])
def test_四種基準狀態端點都能回應_標頭是ASCII代碼(client, monkeypatch, status, code):
    monkeypatch.setattr(ri, "read_upload", lambda f: b"x")
    monkeypatch.setattr(ri, "reconcile", lambda conn, raw: (io.BytesIO(b"PK-fake"), {
        "same": 0, "diff": 0, "filled": 0, "unknown_serial": 0,
        "three_way": {"baseline_status": status, "baseline_reasons": [], "counts": {"unchanged": 1, "untrusted": 2}}}))
    r = client.post("/api/relocation/import", files={"file": ("a.xlsx", b"x", "application/octet-stream")})
    assert r.status_code == 200, r.text
    assert r.headers["X-Three-Baseline"] == code
    assert r.headers["X-Three-unchanged"] == "1" and r.headers["X-Three-untrusted"] == "2"
    assert r.content == b"PK-fake"
    for k, v in r.headers.items():                              # 所有標頭都必須是 ASCII
        k.encode("ascii")
        v.encode("latin-1")


def test_認不得的狀態一律不可信_不降為ok():
    assert ri.baseline_code("某個新狀態") == "suspect"
    assert ri.baseline_code("") == "suspect"
    assert ri.baseline_code(ri.ST_OK) == "ok"


def test_前端用代碼映射中文狀態():
    s = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "relocation.vue").read_text(encoding="utf-8")
    assert "BASELINE_FROM_CODE" in s and "suspect: '疑似被改'" in s and "reference: '僅供參考'" in s
    assert "h.get('X-Three-Baseline')" in s
