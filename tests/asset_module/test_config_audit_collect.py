"""組態檢核一鍵收集（2026-10-01）：平台閘門、ship 指令、空輸出擋、輸出餵 import。

真機 AIX 的 SSH 跑那段這裡驗不了（221 沒 ksh/AIX）——那靠真機「一鍵」按鈕驗。
這裡釘的是收集器的接線：只收 AIX、確實把腳本 ship 上去、收不到要報錯、收到要進 import。
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import config_audit  # noqa: E402
import db  # noqa: E402
import manage_state  # noqa: E402


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="A-1", hostname="aix01", ip="10.99.0.1")
    c.commit()
    return c


def test_只收aix_其他平台擋(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            monkeypatch.setattr(manage_state, "collect_platform_of", lambda conn, ip, *a, **k: "linux")
            with pytest.raises(ValueError, match="只支援 AIX"):
                config_audit.collect_from_host(c, "A-1", runner=lambda ip, cmd: "x")
        finally:
            c.close()


def test_沒ip擋(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            db.insert_hardware(c, asset_serial="A-2", hostname="noip", ip="")
            c.commit()
            with pytest.raises(ValueError, match="IP"):
                config_audit.collect_from_host(c, "A-2", runner=lambda ip, cmd: "x")
        finally:
            c.close()


def test_ship指令含腳本與outdir_空輸出報錯(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            monkeypatch.setattr(manage_state, "collect_platform_of", lambda conn, ip, *a, **k: "aix")
            seen = {}

            def runner(ip, cmd):
                seen["ip"] = ip
                seen["cmd"] = cmd
                return ""        # 模擬 SSH 連不上／沒輸出
            with pytest.raises(ValueError, match="收不到"):
                config_audit.collect_from_host(c, "A-1", runner=runner)
            # 確實把 ksh ship 上去、設了 FCBAIX_OUTDIR、對的 IP
            assert seen["ip"] == "10.99.0.1"
            assert "FCBAIX_OUTDIR" in seen["cmd"]
            assert "__FCBAIX_EOF__" in seen["cmd"]
            assert "FCB AIX 組態檢核" in seen["cmd"]   # 腳本內容真的被塞進去
        finally:
            c.close()


def test_有輸出就餵進import(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c = _conn(tmp)
        try:
            monkeypatch.setattr(manage_state, "collect_platform_of", lambda conn, ip, *a, **k: "aix")
            captured = {}

            def fake_import(conn, blob, file_name=None, by=None):
                captured["blob"] = blob
                captured["file"] = file_name
                captured["by"] = by
                return {"total": 98, "counts": {}}
            monkeypatch.setattr(config_audit, "import_report", fake_import)
            r = config_audit.collect_from_host(
                c, "A-1", runner=lambda ip, cmd: "FAKE_TXT_OUTPUT", by="tester")
            assert r["total"] == 98
            assert captured["blob"] == b"FAKE_TXT_OUTPUT"
            assert captured["by"] == "tester"
            assert "aix01" in captured["file"]
        finally:
            c.close()
