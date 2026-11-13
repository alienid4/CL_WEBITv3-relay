"""2.060.21（S11）主機頁來源對照表：四種來源並列、依 field_trust.json 規則採用；對不上又分不出新舊＝不猜。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

import db  # noqa: E402
import field_sources as fs  # noqa: E402
import oncall  # noqa: E402
from test_srv_sheet import _imp, _seed  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    yield c
    c.close()


def _key(c, serial):
    return oncall._groups(c)[1][serial]


def test_來源並列_機器資訊信實掃_人的資訊信較新(conn):
    _imp(conn)
    conn.execute("UPDATE hardware SET hostname_observed = 'app02', updated_at = '2026-01-01 00:00:00' WHERE asset_serial = 'S2'")
    conn.commit()
    out = fs.compare(conn, _key(conn, "S2"))
    by = {r["key"]: r for r in out["rows"]}
    assert by["host"]["values"]["sheet"] == "app02x" and by["host"]["values"]["cia"] == "app02"
    assert by["host"]["pick"] == "onboard" and by["host"]["pick_value"] == "app02"            # 納管回報優先
    assert by["owner"]["disagree"] and by["owner"]["pick"] == "cia"                            # CIA 2026 比彙整表 2024 新
    assert by["env"]["status"] == "ok" and not by["env"]["disagree"]                           # PRD＝正式
    assert by["purpose"]["status"] == "ok"


def test_分不出新舊就不猜(conn):
    _imp(conn)
    conn.execute("UPDATE hardware SET updated_at = NULL, manual_updated_at = NULL WHERE asset_serial = 'S2'")
    conn.execute("UPDATE srv_sheet_row SET marked_at = NULL")
    conn.commit()
    by = {r["key"]: r for r in fs.compare(conn, _key(conn, "S2"))["rows"]}
    assert by["owner"]["status"] == "conflict" and by["owner"]["pick"] is None


def test_找不到機器(conn):
    with pytest.raises(LookupError):
        fs.compare(conn, "nope")
