"""開盤尖峰 → Teams 摘要（opening_notify.py，2.047.5）。

不連網路；Teams 只看佇列（teams_notify_delivery），不真的送。主機名用 esxi99-*.example.test。
釘住：匯入成功才排一則、同一天只一則、Teams 未啟用只記錄、≥90% 列出（CPU 或記憶體）、Top10 數字＝首頁同一份、
被較新批次蓋過的舊批次不送、摘要失敗不影響匯入。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import credential_store  # noqa: E402
import opening_notify as on  # noqa: E402
import opening_peak as op  # noqa: E402
import relay_import as ri  # noqa: E402
import teams_notify as tn  # noqa: E402
from test_opening_peak import DAY, env as _env, host, imp, write_batch  # noqa: E402,F401

REAL = "https://abc123def.4a.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/0000/triggers/manual/paths/invoke?api-version=1&sv=1.0&sig=FAKEFAKE"


@pytest.fixture
def env(_env, tmp_path, monkeypatch):
    monkeypatch.setattr(credential_store, "KEY_PATH_DEFAULT", str(tmp_path / "k.key"))
    return _env


def teams_on(c):
    cfg = tn.load_settings(c)
    cfg["enabled"] = True
    tn.save_settings(c, cfg)
    tn.set_webhook(c, REAL)
    c.commit()


def queued(c):
    if not tn._has(c, "teams_notify_delivery"):
        return []
    return c.execute("SELECT event_key, title, payload_json FROM teams_notify_delivery WHERE kind = 'opening_peak'").fetchall()


def hosts12():
    hs = [host(f"esxi99-{i:03d}.example.test", cpu=[10 + i, 20 + i, 30 + i, 40 + i, 50 + i, 60 + i], mem=[50] * 6) for i in range(12)]
    hs.append(host("esxi99-900.example.test", cpu=[95, 20, 20, 20, 20, 20], mem=[40] * 6))      # CPU ≥90
    hs.append(host("esxi99-901.example.test", cpu=[5] * 6, mem=[91, 91, 91, 91, 91, 91]))       # 記憶體 ≥90
    return hs


def test_匯入成功排一則_內容與首頁同一份(env):
    c, drop = env
    teams_on(c)
    write_batch(drop, "b1", hosts12())
    imp(c)
    q = queued(c)
    assert len(q) == 1 and q[0][0] == f"opening:{DAY}"
    assert "≥90% 2 台" in q[0][1]
    text = json.dumps(json.loads(q[0][2]), ensure_ascii=False)
    assert "esxi99-900.example.test" in text and "esxi99-901.example.test" in text
    rs = op.rows(c, DAY, with_vms=False)
    top = [r for r in rs if r["has_data"]][:10]
    s = c.execute("SELECT summary FROM opening_notify_seen WHERE day = ?", (DAY,)).fetchone()[0]   # 卡片同內容的純文字（卡片會跳脫標點）
    assert f"1. {top[0]['host'].split('.')[0]}｜{top[0]['max']:g}%" in s      # Top10 第一名＝首頁第一名
    assert f"10. {top[9]['host'].split('.')[0]}" in s and "11. " not in s


def test_同一天只送一次_重匯較新批次不重送(env):
    c, drop = env
    teams_on(c)
    write_batch(drop, "b1", hosts12())
    imp(c)
    write_batch(drop, "b2", hosts12(), collected=f"{DAY}T16:00:00+08:00")
    imp(c)
    assert len(queued(c)) == 1


def test_Teams未啟用只記錄不排佇列(env):
    c, drop = env
    write_batch(drop, "b1", hosts12())
    imp(c)
    assert queued(c) == []
    r = c.execute("SELECT status, summary FROM opening_notify_seen WHERE day = ?", (DAY,)).fetchone()
    assert r["status"] == "recorded" and "esxi99-900" in r["summary"]


def test_關閉就不送也不記(env):
    c, drop = env
    teams_on(c)
    c.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (on.SETTING_KEY, json.dumps({"enabled": False})))
    c.commit()
    write_batch(drop, "b1", hosts12())
    imp(c)
    assert queued(c) == []


def test_摘要失敗不影響匯入(env, monkeypatch):
    c, drop = env
    teams_on(c)
    monkeypatch.setattr(on, "build", lambda *a, **k: 1 / 0)
    write_batch(drop, "b1", hosts12())
    out = imp(c)
    assert c.execute("SELECT COUNT(*) FROM opening_peak WHERE day = ? AND metric = 'cpu'", (DAY,)).fetchone()[0] == 14
    assert queued(c) == []
    assert "失敗 0" in json.dumps(out, ensure_ascii=False) or out
