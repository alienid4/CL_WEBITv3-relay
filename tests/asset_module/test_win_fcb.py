"""Windows 組態檢核（公司基準 286 條，2026-10-21）：腳本、包裝層、解析、管理員判斷、設定開關、
舊標準 W-V001 停用後不計入、卡片＝下鑽。

真機驗證：**還沒有**（221 與家中沒有 Windows Server）。包裝層已在開發機 Windows PowerShell 5.1
實跑（非管理員：只回身分、整段不跑；強制走管理員分支：286 條跑完、暫存目錄有刪掉）。
"""
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import config_audit as ca  # noqa: E402
import db  # noqa: E402
import win_fcb  # noqa: E402

FIXTURE = ROOT / "tests" / "asset_module" / "fixtures" / "config_audit" / "fcb_windows_sample_sanitized.txt"


def _txt() -> str:
    return FIXTURE.read_text(encoding="utf-8-sig")


def _out(admin=True, txt=None) -> str:
    """照包裝層格式造 PSRP 輸出。"""
    head = f"{win_fcb.MARK_RUNAS}\tWINAP01\\webit3adm\t{'1' if admin else '0'}\n"
    if not admin:
        return head
    return head + f"{win_fcb.MARK_BEGIN}\n{txt if txt is not None else _txt()}\n{win_fcb.MARK_END}\n"


def _db(tmp, serial="W-1", ip="10.99.0.21", os_="Microsoft Windows Server 2022 Standard"):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial=serial, hostname="winap01", ip=ip, os=os_)
    c.commit()
    return c


# ---------------- 腳本本體 ----------------

def test_腳本_檔名照慣例_有BOM_版本戳是本系統的_沒有同事版號與公司網段():
    p = win_fcb.latest_script()
    assert re.fullmatch(r"fcb_windowsV\d{3}\.ps1", p.name)
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                   # PowerShell 5.1 讀中文要 BOM
    text = raw.decode("utf-8-sig")
    assert re.search(r"^\$ScriptVersion = 'WEBIT-WINCFG-V\d{3}'$", text, re.M)
    # 上游的腳本名／版號（拆開寫，免得本檔自己出現那個字樣）
    assert ("FCB-" + "WIN") not in text and not re.search(r"(?i)\bv" + "5" + r"\.\d", text)
    assert not re.search(r"\b10\.9[0-9]\.", text) and "10\\.9" not in text
    assert len(re.findall(r"^\s*Index = \d+\s*$", text, re.M)) == 286
    # TXT 用 UTF-8 寫（任何語系都不會變問號）
    assert "WriteAllText($TxtPath, $txtSb.ToString(), (New-Object System.Text.UTF8Encoding($true)))" in text


def test_轉換工具_錨點對不到就失敗_不產半套(tmp_path):
    # 轉換工具在 repo 根的 scripts/，不進公開 relay（只有 APP/ tests/ .project/）——那邊沒有就略過
    if not (ROOT / "scripts" / "build_fcb_windows.py").is_file():
        pytest.skip("scripts/build_fcb_windows.py 不在這份產出物裡（relay 不帶 repo 根的 scripts/）")
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_fcb_windows as b

    src = "<#\n.SYNOPSIS\n x\n#>\n$ScriptVersion = 'X'\n"
    with pytest.raises(SystemExit):
        b.build(src, "V009")


# ---------------- 包裝層 ----------------

def test_包裝層_先看是不是管理員_暫存目錄finally刪_不寫檔頭說明():
    p = win_fcb.build_payload(win_fcb.latest_script().read_text(encoding="utf-8-sig"))
    i_admin = p.index("IsInRole")
    i_body = p.index("$ScriptVersion = 'WEBIT-WINCFG-")
    assert i_admin < i_body and "if ($__w3adm) {" in p[:i_body]
    assert "finally" in p and "[System.IO.Directory]::Delete($__w3dir, $true)" in p
    assert "-OutputDir $__w3dir" in p
    assert "手動執行" not in p and ".ps1" not in p                  # 檔頭說明沒送出去
    for bad in ("ExecutionPolicy", "EncodedCommand", "Invoke-Expression", "FromBase64"):
        assert bad not in p


def test_包裝層_中斷留下的暫存目錄_下次先清_只清本系統命名且夠舊的():
    """逾時 ps.stop()／連線斷掉時遠端 finally 不一定跑得到（2026-10-21 審查補）。"""
    p = win_fcb.build_payload(win_fcb.latest_script().read_text(encoding="utf-8-sig"))
    i_clean = p.index("'^w3cfg_[0-9a-f]{32}$'")
    i_new = p.index("$__w3dir = Join-Path")
    assert i_clean < i_new                                         # 先清舊的才建新的
    assert f"AddMinutes(-{win_fcb.STALE_MINUTES})" in p
    assert win_fcb.STALE_MINUTES * 60 >= 2 * win_fcb.DEADLINE_S    # 不會誤刪正在跑的那一份
    # 腳本自己的暫存檔（secedit 匯出）也導進同一個目錄，被中斷也會在下次一起清掉；finally 還原
    i_body = p.index("$ScriptVersion = 'WEBIT-WINCFG-")
    assert "$env:TMP = $__w3dir" in p[:i_body] and "$env:TEMP = $__w3dir" in p[:i_body]
    assert "$env:TMP = $__w3envtmp" in p[p.index("} finally {"):]
    st = win_fcb.parse_output(_out() + f"{win_fcb.MARK_STALE}\t2\n")
    assert st["state"] == "ok" and "暫存目錄 2 個" in st["error"]


def test_包裝層_不用5點0才有的語法_版本不夠照實講():
    """整段一次解析：包裝層用了 PS 2～4 不認得的語法（6>$null）就整段不跑、連 RUNAS 都回不來。"""
    p = win_fcb.build_payload(win_fcb.latest_script().read_text(encoding="utf-8-sig"))
    assert not re.search(r"\b[3-6]>", p)
    i_ver = p.index("PSVersion.Major -lt 5")
    assert i_ver < p.index("$ScriptVersion = 'WEBIT-WINCFG-")
    e = win_fcb.parse_output(f"{win_fcb.MARK_RUNAS}\tx\t1\n{win_fcb.MARK_ERR}\t這台 PowerShell 版本 4.0，需要 5.1 以上\n")
    assert e["state"] == "error" and "5.1" in e["error"]


def test_大腳本_每片WSMan訊息不超過150KB_任何主機都收得下(monkeypatch):
    pytest.importorskip("pypsrp")
    import winrm_collector as wc
    from pypsrp.wsman import WSMan

    payload = win_fcb.build_payload(win_fcb.latest_script().read_text(encoding="utf-8-sig"))
    assert len(payload.encode("utf-8")) > 150 * 1024               # 一次送一定超過舊預設
    extra = wc._wsman_extra(payload)
    assert extra == {"max_envelope_size": wc.SAFE_ENVELOPE_BYTES}
    assert wc._wsman_extra("Get-Date") == {}                        # 小腳本維持原行為
    # pypsrp 看到 153600 會自動拉高到 500 KB——不可以剛好等於它
    assert wc.SAFE_ENVELOPE_BYTES != 153600 and wc.SAFE_ENVELOPE_BYTES <= 150 * 1024
    w = WSMan("192.0.2.1", username="u", password="p", auth="ntlm", ssl=False, **extra)
    assert w.max_envelope_size == wc.SAFE_ENVELOPE_BYTES
    assert w.max_payload_size * 4 / 3 < 150 * 1024                  # 片段 base64 後仍在上限內

    seen = {}

    class _Stop(Exception):
        pass

    def fake_wsman(*a, **k):
        seen.update(k)
        raise _Stop()

    monkeypatch.setattr("pypsrp.wsman.WSMan", fake_wsman)
    with pytest.raises(_Stop):
        wc._psrp_run("192.0.2.1", "u", "p", payload, 120, deadline_s=win_fcb.DEADLINE_S)
    assert seen["max_envelope_size"] == wc.SAFE_ENVELOPE_BYTES
    seen.clear()
    with pytest.raises(_Stop):
        wc._psrp_run("192.0.2.1", "u", "p", "Get-Date", 120)
    assert "max_envelope_size" not in seen


def test_大腳本分段送_每次遠端呼叫都在上限內_組回來跟原本一字不差():
    """使用者 2026-10-21：「太大就分段」。對方 MaxEnvelopeSizekb 只有 150 也收得下。"""
    import html

    import winrm_collector as wc

    payload = win_fcb.build_payload(win_fcb.latest_script().read_text(encoding="utf-8-sig"))
    calls = wc._remote_calls(payload)
    assert len(calls) >= 4                                          # 初始化＋≥2 段＋執行
    for cmd, params in calls:
        size = len(cmd.encode("utf-8")) + sum(len(str(v).encode("utf-8")) for v in params.values())
        assert size <= wc.CHUNK_CALL_LIMIT, size
        # 參數序列化成 XML 會跳脫 < > & 等，最壞情況也要在上限內
        esc = len(cmd.encode("utf-8")) + sum(len(html.escape(str(v)).encode("utf-8")) for v in params.values())
        assert esc <= wc.CHUNK_CALL_LIMIT, esc
    parts = [p["c"] for c, p in calls if p]
    assert "".join(parts) == payload                                # 組回來一字不差
    assert all(c == wc._CHUNK_APPEND for c, p in calls if p)        # 資料只走參數
    assert calls[0] == (wc._CHUNK_INIT, {}) and calls[-1] == (wc._CHUNK_RUN, {})
    assert "WEBIT-WINCFG" not in calls[-1][0]                       # 最後一次只組回來執行，不再帶本體
    for bad in ("Invoke-Expression", "EncodedCommand", "FromBase64", "ExecutionPolicy"):
        assert all(bad not in c for c, _ in calls)
    # 中文字不會被切成兩半
    s = "國" * 40000
    segs = wc._split_utf8(s, wc.CHUNK_DATA_BYTES)
    assert "".join(segs) == s and all(len(x.encode("utf-8")) <= wc.CHUNK_DATA_BYTES for x in segs)
    assert wc._remote_calls("Get-Date") == [("Get-Date", {})]       # 小腳本照舊一次送


def test_分段送_同一個工作階段_先送資料最後才執行(monkeypatch):
    pytest.importorskip("pypsrp")
    import winrm_collector as wc

    payload = win_fcb.build_payload(win_fcb.latest_script().read_text(encoding="utf-8-sig"))
    log = []

    class FakeWSMan:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakePool(FakeWSMan):
        def __init__(self, wsman, **k):
            log.append(("pool", k.get("min_runspaces", 1), k.get("max_runspaces", 1)))

    class FakePS:
        def __init__(self, pool):
            self.cmds, self.params, self.output, self.had_errors = [], {}, [], False
            self.streams = type("S", (), {"error": []})()

        def add_script(self, s):
            self.cmds.append(s)
            return self

        def add_cmdlet(self, s):
            self.cmds.append(s)
            return self

        def add_parameter(self, k, v=None):
            self.params[k] = v
            return self

        def invoke(self):
            log.append(("call", self.cmds[0], dict(self.params)))
            if self.cmds[0] == wc._CHUNK_RUN:
                self.output = ["ok"]

    monkeypatch.setattr("pypsrp.wsman.WSMan", FakeWSMan)
    monkeypatch.setattr("pypsrp.powershell.RunspacePool", FakePool)
    monkeypatch.setattr("pypsrp.powershell.PowerShell", FakePS)
    assert wc._psrp_run("192.0.2.1", "u", "p", payload, 120, deadline_s=win_fcb.DEADLINE_S) == "ok"
    assert log[0] == ("pool", 1, 1)                                  # 單一 runspace，變數留得住
    calls = [x for x in log if x[0] == "call"]
    assert calls[0][1] == wc._CHUNK_INIT and calls[-1][1] == wc._CHUNK_RUN
    assert "".join(c[2]["c"] for c in calls if c[2].get("c") is not None) == payload


def test_解析_四種狀態():
    assert win_fcb.parse_output(_out(admin=False))["state"] == "not_admin"
    ok = win_fcb.parse_output(_out())
    assert ok["state"] == "ok" and ok["runas"] == "WINAP01\\webit3adm"
    assert ok["txt"].startswith("系統組態檢查")
    e = win_fcb.parse_output(f"{win_fcb.MARK_RUNAS}\tx\t1\n{win_fcb.MARK_ERR}\t腳本沒有產出結果檔（找到 0 份）\n")
    assert e["state"] == "error" and "沒有產出" in e["error"]
    assert win_fcb.parse_output("")["state"] == "error"          # 沒輸出不是「沒問題」
    # 只有 BEGIN 沒有 END＝中途斷掉，不收
    assert win_fcb.parse_output(f"{win_fcb.MARK_RUNAS}\tx\t1\n{win_fcb.MARK_BEGIN}\nabc\n")["state"] == "error"


# ---------------- 解析器（自動收集與 8-2 人工匯入同一個）----------------

@pytest.mark.parametrize("enc", ["utf-8-sig", "cp950"])
def test_去識別化樣本_四態都對_UTF8與CP950都吃(enc):
    d = ca.parse(_txt().encode(enc, errors="replace"))   # 上游舊版以系統字碼頁寫，≤ 這類字會變 ?
    assert d["header"]["platform"] == "Windows"
    assert d["completeness"] == "完整"                          # 有 Check summary 且條數對得上
    c = ca.tally(d["items"])
    assert c == {"Compliant": 281, "Non-Compliant": 2, "Not-Applicable": 1, "Error": 2}
    it = {x["item_id"]: x for x in d["items"]}
    assert it["TWGCB-01-007-0002"]["expected"] == "90天"          # Windows 標準設定值＝建議值
    assert it["TWGCB-01-007-0001"]["result"] == "Error"
    assert it["CSCB-01-2025-0001"]["result"] == "Not-Applicable"
    assert it["TWGCB-01-007-0001"]["role"] == "Common"


def test_截斷的檔_收尾條數對不上_不收():
    lines = _txt().splitlines()
    cut = "\n".join(lines[:50] + [lines[-2], lines[-1]]) + "\n"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        c = _db(tmp)
        try:
            with pytest.raises(ValueError, match="截斷"):
                ca.import_report(c, cut.encode("utf-8"), "x.txt")
        finally:
            c.close()


def test_本系統腳本_沒收尾也當截斷():
    body = "\n".join(l for l in _txt().splitlines() if not l.startswith("Check summary"))
    body = "\n".join(l for l in body.splitlines() if not l.startswith("Check ended"))
    assert ca.parse(body.encode("utf-8"))["completeness"] == "截斷"


# ---------------- 一鍵收集：管理員／非管理員／設定關／逾時 ----------------

def test_管理員_收成286條那套_標示公司基準_卡片等於下鑽():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        c = _db(tmp)
        try:
            sent = []
            r = ca.collect_from_host(c, "W-1", runner=lambda ip, s: sent.append(s) or _out())
            assert len(sent) == 1 and win_fcb.MARK_RUNAS in sent[0]
            assert r["collected_platform"] == "windows" and r["asset_serial"] == "W-1"
            assert r["baseline_label"] == "Windows 組態檢核（依組態安全配置檢核表 v4.0，286 條）"
            d = ca.by_asset(c, "W-1")
            assert d["run"]["baseline_label"] == win_fcb.BASELINE_LABEL
            assert d["run"]["self_made_label"] is None
            s = {b["platform"]: b for b in ca.summary(c)["by_platform"]}
            for res in ca.RESULTS:
                assert s["Windows"][res] == len(ca.items_across(c, "Windows", res)), res
            assert s["Windows"]["Error"] == 2 and s["Windows"]["Not-Applicable"] == 1
        finally:
            c.close()


def test_非管理員_不跑_不計數_資產頁講原因():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        c = _db(tmp)
        try:
            with pytest.raises(ValueError, match="需本機管理員才能做 Windows 組態檢核"):
                ca.collect_from_host(c, "W-1", runner=lambda ip, s: _out(admin=False))
            assert ca.summary(c)["hosts"] == 0 and ca.items_across(c) == []
            d = ca.by_asset(c, "W-1")
            assert d["run"] is None
            assert "需本機管理員" in d["last_failure"]["note"]
            assert ca.history(c)[0]["status"] == "failed"
        finally:
            c.close()


def test_失敗紀錄_之後成功過就不再當現況():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        c = _db(tmp)
        try:
            with pytest.raises(ValueError):
                ca.collect_from_host(c, "W-1", runner=lambda ip, s: _out(admin=False))
            ca.collect_from_host(c, "W-1", runner=lambda ip, s: _out())
            assert ca.by_asset(c, "W-1")["last_failure"] is None
        finally:
            c.close()


def test_連續失敗只留最新一筆_中間成功過就分開記():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        c = _db(tmp)
        try:
            for _ in range(3):
                with pytest.raises(ValueError):
                    ca.collect_from_host(c, "W-1", runner=lambda ip, s: _out(admin=False))
            assert [h["status"] for h in ca.history(c)] == ["failed"]
            ca.collect_from_host(c, "W-1", runner=lambda ip, s: _out())
            with pytest.raises(ValueError):
                ca.collect_from_host(c, "W-1", runner=lambda ip, s: "")
            with pytest.raises(ValueError):
                ca.collect_from_host(c, "W-1", runner=lambda ip, s: _out(admin=False))
            assert [h["status"] for h in ca.history(c)] == ["failed", "ready", "failed"]
            d = ca.by_asset(c, "W-1")
            assert d["run"] is not None and "需本機管理員" in d["last_failure"]["note"]   # 最新那次的原因
            assert ca.summary(c)["hosts"] == 1                       # 失敗不進計數
        finally:
            c.close()


def test_設定關掉_略過_什麼都不送():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        c = _db(tmp)
        try:
            assert win_fcb.enabled(c) is True                    # 預設開（使用者選自動跑）
            db.set_setting(c, win_fcb.SETTING_ENABLED, "0")
            c.commit()
            sent = []
            with pytest.raises(ca.ConfigAuditSkipped, match="系統設定關閉"):
                ca.collect_from_host(c, "W-1", runner=lambda ip, s: sent.append(s) or _out())
            assert sent == []
        finally:
            c.close()


def test_逾時_照實講_記失敗(monkeypatch):
    import credential_store
    import winrm_collector

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        c = _db(tmp)
        try:
            import win_onboard

            # 測的是逾時那條路：主機當成已納管（「沒納管就不登入」在 test_win_onboard.py 測）
            monkeypatch.setattr(win_onboard, "collect_block", lambda *a, **k: None)
            monkeypatch.setattr(credential_store, "pick_for_host", lambda *a, **k: "w")
            monkeypatch.setattr(credential_store, "get_for_use", lambda *a, **k: ("u", "p"))
            monkeypatch.setattr(credential_store, "audit_use", lambda *a, **k: None)
            seen = {}

            def boom(host, u, p, script, timeout, deadline_s=None):
                seen["deadline"] = deadline_s
                raise winrm_collector.WinRMScriptTimeout("x")

            monkeypatch.setattr(winrm_collector, "_psrp_run", boom)
            with pytest.raises(ValueError, match="逾時"):
                ca.collect_from_host(c, "W-1")
            assert seen["deadline"] == win_fcb.DEADLINE_S
            assert "逾時" in ca.by_asset(c, "W-1")["last_failure"]["note"]
        finally:
            c.close()


def test_人工匯入TXT_對回資產_同一份解析():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        c = _db(tmp)
        try:
            r = ca.import_report(c, FIXTURE.read_bytes(), "FCB_WINAP01_10.99.0.21.txt", "u")
            assert r["asset_serial"] == "W-1" and r["match_by"] == "ip" and r["total"] == 286
        finally:
            c.close()


# ---------------- 舊標準 W-V001 停用：不計入任何數字 ----------------

_OLD_WV001 = """系統組態檢查
平台: Windows
主機名: WINAP02
IP地址: 10.99.0.22
腳本版本: WEBIT-WIN-V001（自製判定 W-V001（非官方 FCB，有官方清單時替換））
檢核基準: 自製判定 W-V001
Check stated at: 2026-10-20 10:00:00
TWGCB-ID;檢查結果;類別;原則設定名稱;標準設定值;目前值
W-V001-01;Non-Compliant;帳號;密碼最小長度;x，( 8 );6
W-V001-02;Compliant;帳號;y;x，( 1 );1
Check ended at: 2026-10-20 10:00:01
Check summary: 合計 2 項；Compliant 1；Non-Compliant 1；Not-Applicable 0；Error 0
"""


def test_舊標準W_V001_不計入_卡片等於下鑽_資產頁標舊標準():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        c = _db(tmp)
        try:
            db.insert_hardware(c, asset_serial="W-2", hostname="winap02", ip="10.99.0.22",
                               os="Microsoft Windows Server 2019")
            c.commit()
            ca.import_report(c, _OLD_WV001.encode("utf-8"), "old.txt", asset_serial="W-2")
            ca.collect_from_host(c, "W-1", runner=lambda ip, s: _out())
            sm = ca.summary(c)
            assert sm["hosts"] == 1                                   # 只有 W-1 的公司基準
            win = {b["platform"]: b for b in sm["by_platform"]}["Windows"]
            assert win["hosts"] == 1 and win["Non-Compliant"] == 2
            for res in ca.RESULTS:
                assert win[res] == len(ca.items_across(c, "Windows", res)), res
            assert not any(it["item_id"].startswith("W-V001") for it in ca.items_across(c))
            assert all(not ca.is_superseded(r.get("script_version")) for r in ca.runs(c))
            d = ca.by_asset(c, "W-2")
            assert d["run"] is None and d["superseded_runs"] == 1
            assert d["superseded_label"] == "舊標準（已停用）"
            h = {x["file_name"]: x for x in ca.history(c)}
            assert h["old.txt"]["superseded"] is True
            assert h["old.txt"]["baseline_label"] == "舊標準（已停用）"
            assert "W-V001" not in ca.export_raw(c)
        finally:
            c.close()


def test_verify_numbers_組態檢核那段_舊標準在也全綠():
    import verify_numbers as vn

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        c = _db(tmp)
        try:
            ca.import_report(c, _OLD_WV001.encode("utf-8"), "old.txt")
            ca.collect_from_host(c, "W-1", runner=lambda ip, s: _out())
            before, n0 = vn.FAILS, len(vn.RESULTS)
            vn.check_config_audit_drill(c)
            new = vn.RESULTS[n0:]
            assert vn.FAILS == before, [r for r in new if not r.get("ok")]
            assert any("舊標準" in r["name"] for r in new)
        finally:
            c.close()
