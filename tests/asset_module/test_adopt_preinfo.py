"""2-1「納管前資訊」（v1.468.7）：一台機器已知的全部線索，只讀、不連線、不登入。

2026-10-19 使用者（公司機）：怕把網路設備、或不是 RHEL／CentOS／Debian 的機器納管進來。
例子 IP 一律 10.99.x。
"""
import contextlib
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACK = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACK))

import adopt_preinfo as ap  # noqa: E402
import asset_tags  # noqa: E402
import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402

FE = ROOT / "APP" / "asset-module" / "frontend"
PANEL = (FE / "components" / "PreinfoPanel.vue").read_text(encoding="utf-8")
ADOPT = (FE / "pages" / "adopt.vue").read_text(encoding="utf-8")


@contextlib.contextmanager
def _db():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            yield conn
        finally:
            conn.close()


def test_banner_代表什麼_規則在設定檔():
    assert ap.banner_raw("22 通、但收集帳號進不去（SSH-2.0-Cisco-1.25 不接受）") == "SSH-2.0-Cisco-1.25"
    assert ap.banner_hint("SSH-2.0-Cisco-1.25")["kind"] == "device"
    assert ap.banner_hint("SSH-2.0-ROSSSH")["kind"] == "device"
    assert ap.banner_hint("SSH-2.0-dropbear_2019.78")["kind"] == "device"
    assert ap.banner_hint("SSH-2.0-OpenSSH_for_Windows_8.1")["kind"] == "windows"
    assert ap.banner_hint("SSH-2.0-OpenSSH_8.7")["kind"] == "linux_like"
    assert ap.banner_hint(None) is None


def test_收齊已知線索_查不到的回None不猜():
    with _db() as conn:
        db.insert_hardware(conn, asset_serial="P-1", ip="10.99.6.20", hostname="box20", os="RHEL 8",
                           asset_name="測試主機", api_id="N-001", owner="甲", environment="測試",
                           asset_status="使用中")
        conn.execute("INSERT INTO scan_history (ip, hostname, scan_ok, ttl, open_ports, os_guess, mac, mac_vendor) "
                     "VALUES ('10.99.6.20', 'box20.example', 1, 63, '22', 'Linux/Unix（SSH 22）', "
                     "'00:11:22:33:44:55', 'Dell')")
        conn.commit()
        cd.run_dispatch(conn, "10.99.6.20", prober=lambda ip: [22], ttl_prober=lambda ip: None,
                        ssh_prober=lambda ip: (False, "SSH-2.0-OpenSSH_8.7 Permission denied"))
        asset_tags.add_tag(conn, "P-1", "試過A失敗", "2026-10-19 帳密被拒", by="t")
        d = ap.gather(conn, "10.99.6.20")
        assert d["registry"]["asset_name"] == "測試主機" and d["registry"]["api_id"] == "N-001"
        assert d["scan"]["hostname"] == "box20.example" and d["scan"]["mac_vendor"] == "Dell"
        assert d["dispatch"]["status_label"] == cd.STATUS_LABEL[d["dispatch"]["status"]]
        assert d["banner"]["raw"] == "SSH-2.0-OpenSSH_8.7" and d["banner"]["hint"]["kind"] == "linux_like"
        assert d["judgement"]["kind"] == "linux" and d["judgement"]["confidence"] == "confirmed"
        assert [t["tag"] for t in d["tags"]] == ["試過A失敗"]
        empty = ap.gather(conn, "10.99.6.99")
        assert empty["registry"] is None and empty["scan"] is None and empty["dispatch"] is None
        assert empty["banner"] is None and empty["tags"] == [] and empty["last_attempt"] is None


def test_只讀_不碰網路不登入():
    src = (BACK / "adopt_preinfo.py").read_text(encoding="utf-8")
    for bad in ("socket", "paramiko", "_ssh_exec", "probe_target", "INSERT", "UPDATE", "DELETE", "commit("):
        assert bad not in src, f"納管前資訊不可以 {bad}"


def test_前端_每列有資訊鈕_面板寫明只讀_查不到寫沒有():
    assert 'preinfoIp = r.ip' in ADOPT and "<PreinfoPanel" in ADOPT
    assert "'/api/adopt/preinfo'" in PANEL
    assert "只讀資料，不連線、不登入、不改任何東西" in PANEL
    assert "const NONE = '沒有'" in PANEL
    assert re.search(r"沒有（資產庫沒登記這個 IP）", PANEL)
