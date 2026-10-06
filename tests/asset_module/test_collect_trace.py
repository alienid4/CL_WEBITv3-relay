"""[B-25] 收集診斷：遠端這一跳到底發生什麼事。

2026-09-22 真機第一次驗證：AIX `test1T` 四類全空，而且 `lslpp -Lc`、
`/etc/passwd`、`lsdev -Cc adapter` **三個同時空**。這三件事性質完全不同
（套件查詢／讀一個 644 的純文字檔／ODM 查詢），不可能各自因為各自的原因失敗。

但當時**證明不了**是哪一層，因為 runner 只回 `r.stdout`——離開碼與 stderr
被丟掉了。收集器看到空字串，只能推論「指令沒跑起來或被擋」，那是推論不是證據。

這組測試守的就是「事實與推論要分得開」：

1. SSH 沒連上（ssh 自己回 255）→ 要講「根本沒連上」，**不可以**說指令有問題
2. 連得上但指令回非 0 → 要講「指令跑不起來」，而且 stderr 原文要留著
3. 連得上、離開碼 0、輸出是空的 → 要講「真的沒回東西」，那才輪到查權限／格式
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import manage_state as ms  # noqa: E402


def _runner(monkeypatch, replies):
    """replies: [(rc, stdout, stderr), ...] 依序回。"""
    calls = iter(replies)

    def fake_run(argv, **kw):
        rc, out, err = next(calls)
        # 用 CompletedProcess 本尊，不手捏假物件——手捏的會在介面變動時漂移
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr=err)

    monkeypatch.setattr(ms.subprocess, "run", fake_run)
    return ms.SSHRunner("/k/collector", account="webit3sc", timeout=5)


def test_ssh沒連上要講沒連上_不可以賴到指令頭上(monkeypatch):
    """ssh(1) 用離開碼 255 表示**它自己**失敗（遠端指令的離開碼會原樣帶回來）。"""
    run = _runner(monkeypatch, [(255, "", "Permission denied (publickey).")] * 3)
    for cmd in ("lslpp -Lc", "cat /etc/passwd", "lsdev -Cc adapter"):
        assert run("h", cmd) == ""

    rec = run.trace["h"]
    assert rec["connected"] is False
    assert "Permission denied" in rec["transport_error"]
    v = ms.trace_verdict(_row(rec))
    assert v["verdict"] == "no_ssh"
    assert "沒連上" in v["text"]
    assert "指令" not in v["text"].split("原始訊息")[0] or "不是指令的問題" in v["text"]


def test_連得上但指令跑不起來_stderr原文要留著(monkeypatch):
    run = _runner(monkeypatch, [
        (0, "AIX\n", ""),                       # 先有一條成功 -> 證明 SSH 通
        (127, "", "ksh: lslpp: not found"),
    ])
    run("h", "uname -s")
    run("h", "lslpp -Lc")

    rec = run.trace["h"]
    assert rec["connected"] is True             # 通了就是通了，不因後面失敗而翻案
    assert rec["failed"] == 1
    # stderr 原文要能在畫面上看到——這是「該改指令」的直接依據
    assert any("not found" in s["stderr"] for s in rec["samples"])


def test_離開碼0但輸出空_是真的沒回東西(monkeypatch):
    """這一態最關鍵：**有了離開碼，「空」才能確定是遠端真的沒回，不是沒跑到。**"""
    run = _runner(monkeypatch, [(0, "", ""), (0, "", "")])
    run("h", "lsdev -Cc adapter")
    run("h", "cat /etc/passwd")

    rec = run.trace["h"]
    assert rec["connected"] is True
    assert rec["empty"] == 2 and rec["ok"] == 0 and rec["failed"] == 0
    v = ms.trace_verdict(_row(rec))
    assert v["verdict"] == "all_empty"
    assert "不是沒跑到" in v["text"]


def test_逾時要記成沒連上而不是空輸出(monkeypatch):
    import subprocess

    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 15)

    monkeypatch.setattr(ms.subprocess, "run", boom)
    run = ms.SSHRunner("/k/collector", account="webit3sc", timeout=5)
    assert run("h", "uname -s") == ""
    assert run.trace["h"]["connected"] is False
    assert "逾時" in run.trace["h"]["transport_error"]


def test_樣本數有上限_不可以把db灌爆(monkeypatch):
    run = _runner(monkeypatch, [(0, "x", "")] * 40)
    for i in range(40):
        run("h", f"cmd{i}")
    assert len(run.trace["h"]["samples"]) == ms.TRACE_SAMPLES
    assert run.trace["h"]["total"] == 40          # 計數還是要對，只有樣本被截斷


def test_不可以把金鑰內容寫進診斷(monkeypatch, tmp_path):
    """只存路徑。診斷是要給人看的，看的人不一定該看到金鑰。"""
    run = _runner(monkeypatch, [(0, "ok", "")])
    run("h", "uname -s")
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        run.save(conn, "A-1", "h")
        row = conn.execute("SELECT * FROM collect_ssh_trace").fetchone()
        assert row["key_path"] == "/k/collector"
        assert row["account"] == "webit3sc"
        assert "BEGIN" not in (row["samples"] or "")
    finally:
        conn.close()


def test_收集失敗也要寫得進去(monkeypatch, tmp_path):
    """**失敗的時候才是最需要看診斷的時候。**

    收集全失敗時 host_spec 根本不會有列，所以診斷不能掛在那張表上。
    """
    run = _runner(monkeypatch, [(255, "", "Connection timed out")])
    run("10.0.0.9", "uname -s")
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        run.save(conn, "A-9", "10.0.0.9")
        row = conn.execute("SELECT * FROM collect_ssh_trace WHERE asset_serial='A-9'").fetchone()
        assert row["connected"] == 0
        assert "timed out" in row["transport_error"]
        assert ms.trace_verdict(row)["verdict"] == "no_ssh"
    finally:
        conn.close()


def test_沒有紀錄要說沒有紀錄_不可以裝成沒問題():
    v = ms.trace_verdict(None)
    assert v["verdict"] == "no_data"
    assert "還沒有" in v["text"]


def _row(rec):
    """把記憶體裡的 rec 包成 trace_verdict 吃的那種 row（欄位名要一致）。"""
    return {
        "connected": None if rec["connected"] is None else int(rec["connected"]),
        "transport_error": rec["transport_error"],
        "cmd_ok": rec["ok"], "cmd_empty": rec["empty"], "cmd_failed": rec["failed"],
    }


@pytest.fixture(autouse=True)
def _no_real_ssh(monkeypatch):
    """保險：這組測試絕對不可以真的連出去。"""
    monkeypatch.setattr(ms, "_ssh_hostkey_opts", lambda: [])
