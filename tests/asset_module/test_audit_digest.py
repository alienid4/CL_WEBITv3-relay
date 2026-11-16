"""稽核表外部摘要（audit_digest，Codex 第三次審查第 7 點）的守門測試。

資料庫觸發器擋得住一般 SQL，擋不住能寫檔案的人先 DROP TRIGGER 再改；外部摘要檔才抓得到。
"""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import access_control  # noqa: E402
import api  # noqa: E402
import audit_digest as ad  # noqa: E402
import transfer_store as st  # noqa: E402
from test_transfer_q94 import env  # noqa: E402,F401

ROLE_AWARE_TESTS = True          # bob 要是真正的一般帳號（conftest 預設會把未指定 role 的帳號建成 admin）

T0 = dt.datetime(2026, 10, 30, 9, 0, 0)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_chattr(monkeypatch):
    # 221 以 root 跑時 chattr +a 會真的成功，之後測試要「竄改 log」就被 EPERM 擋住（2026-10-30 部署紅燈）。
    # 測試環境不設 append-only；chattr 行為本身是「盡量而為」，不在這裡驗。
    monkeypatch.setattr(ad, "_try_append_only", lambda path: None)


def _seed(c, n=3, start=1):
    for i in range(start, start + n):
        st.audit(c, "admin", "test", f"第{i}筆")


def _log(env):
    return Path(env.path).parent / ad.LOG_NAME


def _tamper(c, trigger_sql, sql, *args):
    c.execute(trigger_sql)                    # 繞過觸發器——這正是 Codex 擔心的情境
    c.execute(sql, args)
    c.commit()


def test_正常_留存後驗證一致(env):
    c = env.conn()
    _seed(c)
    assert ad.retain(c, now=T0)["done"]
    v = ad.verify(c)
    assert v["status"] == "consistent" and v["ok"] and v["issues"] == []
    _seed(c, 2, 4)                                         # 留存後正常新增紀錄，不可以變成不一致
    assert ad.verify(c)["status"] == "consistent"


def test_改一筆_驗證不一致且指出表和範圍(env):
    c = env.conn()
    _seed(c)
    ad.retain(c, now=T0)
    _seed(c, 2, 4)
    ad.retain(c, now=T0 + dt.timedelta(hours=2))
    _tamper(c, "DROP TRIGGER ft_audit_no_update", "UPDATE ft_audit SET detail = ? WHERE id = ?", "被改了", 4)
    v = ad.verify(c)
    assert v["status"] == "tampered" and not v["ok"]
    iss = [i for i in v["issues"] if i["table"] == "ft_audit"]
    assert len(iss) == 1 and iss[0]["kind"] == "modified"
    assert iss[0]["after_id"] == 3 and iss[0]["upto_id"] == 5         # 第 4 筆在第一次留存(3)之後、第二次(5)之前
    assert "ft_audit" in v["message"] and "4～5" in v["message"]
    assert all(i["table"] == "ft_audit" for i in v["issues"])         # 別的表不受牽連


def test_刪一筆_驗證不一致(env):
    c = env.conn()
    _seed(c)
    ad.retain(c, now=T0)
    _tamper(c, "DROP TRIGGER ft_audit_no_delete", "DELETE FROM ft_audit WHERE id = ?", 3)    # 連最後一筆都能抓
    v = ad.verify(c)
    assert v["status"] == "tampered"
    assert v["issues"][0]["table"] == "ft_audit" and v["issues"][0]["kind"] == "deleted"


def test_其他表也驗_export_log被改(env):
    c = env.conn()
    access_control.log_export(c, username="u", level="admin", ip="1.1.1.1", method="GET", path="/x", name="n",
                              filename="a.xlsx", rows=3, summary="s", status=200)
    ad.retain(c, now=T0)
    _tamper(c, "DROP TRIGGER export_log_no_update", "UPDATE export_log SET row_count = 9999 WHERE id = 1")
    v = ad.verify(c)
    assert v["status"] == "tampered" and [i["table"] for i in v["issues"]] == ["export_log"]


def test_log被改_回報摘要檔斷鏈(env):
    c = env.conn()
    _seed(c)
    ad.retain(c, now=T0)
    ad.retain(c, now=T0 + dt.timedelta(hours=2))
    p = _log(env)
    lines = p.read_text(encoding="utf-8").splitlines()
    e = json.loads(lines[1])
    e["count"] = 999                                       # 改中間一行（不重算雜湊）
    lines[1] = json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = ad.verify(c)
    assert not v["ok"] and v["log_ok"] is False
    assert v["log_problem"]["line"] == 2
    assert v["status"] in ("log_broken", "tampered")


def test_log刪掉中間一行_也斷(env):
    c = env.conn()
    ad.retain(c, now=T0)
    ad.retain(c, now=T0 + dt.timedelta(hours=2))
    p = _log(env)
    lines = p.read_text(encoding="utf-8").splitlines()
    del lines[1]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = ad.verify(c)
    assert v["log_ok"] is False and not v["ok"]


def test_無log_是尚無留存_不是通過(env):
    c = env.conn()
    assert not _log(env).exists()
    v = ad.verify(c)
    assert v["status"] == "none" and v["ok"] is False


def test_每小時最多留存一次(env):
    c = env.conn()
    assert ad.retain(c, now=T0)["done"]
    n1 = len(_log(env).read_text(encoding="utf-8").splitlines())
    r = ad.retain(c, now=T0 + dt.timedelta(minutes=59))
    assert not r["done"] and r["reason"] == "hourly"
    assert len(_log(env).read_text(encoding="utf-8").splitlines()) == n1
    assert ad.retain(c, now=T0 + dt.timedelta(minutes=61))["done"]
    assert ad.retain(c, force=True, now=T0 + dt.timedelta(minutes=62))["done"]       # 啟動時強制留存


def test_GET驗證是純讀_不寫檔(env):
    _seed(env.conn())
    r = env.admin.get("/api/transfer/audit-verify")
    assert r.status_code == 200, r.text
    assert r.json()["verify"]["status"] in ("none", "consistent") and r.json()["retained"] is None
    assert not _log(env).exists()                          # GET 絕不寫 log


def test_POST才驗證加留存_第二次被每小時限制擋(env):
    _seed(env.conn())
    r1 = env.admin.post("/api/transfer/audit-digest")
    assert r1.status_code == 200, r1.text
    j1 = r1.json()
    assert j1["verify"]["status"] == "none" and j1["retained"]["done"] is True
    assert _log(env).exists()
    j2 = env.admin.post("/api/transfer/audit-digest").json()
    assert j2["verify"]["status"] == "consistent" and j2["retained"]["done"] is False
    assert j2["retained"]["reason"] == "hourly"
    c = env.conn()
    _tamper(c, "DROP TRIGGER ft_audit_no_update", "UPDATE ft_audit SET action = 'x' WHERE id = 1")
    j3 = env.admin.get("/api/transfer/audit-verify").json()
    assert j3["verify"]["status"] == "tampered" and j3["verify"]["issues"][0]["table"] == "ft_audit"


def test_端點_非admin擋_GET與POST都是403(env):
    bob = env.client("bob")
    assert bob.get("/api/transfer/audit-verify").status_code == 403
    assert bob.post("/api/transfer/audit-digest").status_code == 403
    assert not _log(env).exists()                          # 被擋就不能觸發留存


def test_每輪固定六表_順序固定(env):
    c = env.conn()
    ad.retain(c, now=T0)
    es = [json.loads(x) for x in _log(env).read_text(encoding="utf-8").splitlines()]
    assert [e["table"] for e in es] == list(ad.TABLES) and len({e["round"] for e in es}) == 1


def test_截斷最後一輪部分表_偵測(env):
    c = env.conn()
    _seed(c)
    ad.retain(c, now=T0)
    ad.retain(c, now=T0 + dt.timedelta(hours=2))
    p = _log(env)
    lines = p.read_text(encoding="utf-8").splitlines()
    p.write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")     # 合法前綴：鏈沒斷，但最後一輪少兩張表
    v = ad.verify(c)
    assert v["log_ok"] is True
    assert v["status"] == "tampered" and not v["ok"]
    assert any(i["kind"] == "incomplete_round" for i in v["issues"])


def test_有資料的表在log完全沒有摘要_是未留存_不是通過(env):
    c = env.conn()
    _seed(c)
    ad.retain(c, now=T0)
    p = _log(env)
    keep = [x for x in p.read_text(encoding="utf-8").splitlines() if '"table":"ft_audit"' not in x]
    prev, out = ad.GENESIS, []                              # 重建一條合法的鏈（模擬 log 從來沒寫過 ft_audit）
    for x in keep:
        e = json.loads(x)
        e["prev"] = prev
        e["h"] = ad._line_hash(e)
        prev = e["h"]
        out.append(json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    v = ad.verify(c)
    assert not v["ok"] and "ft_audit" in v["unretained"]
    assert v["status"] in ("none", "tampered")                 # 缺表同時也讓最後一輪不齊→tampered；兩者都不是通過


def test_增量留存結果與全量重算一致(env):
    c = env.conn()
    _seed(c)
    ad.retain(c, now=T0)
    _seed(c, 4, 4)
    ad.retain(c, now=T0 + dt.timedelta(hours=2))
    es = [json.loads(x) for x in _log(env).read_text(encoding="utf-8").splitlines()]
    last = {e["table"]: e for e in es[-6:]}
    for t in ad.TABLES:
        _o, (n, h, mx) = ad._walk(c, t, [])
        assert (last[t]["count"], last[t]["tail"], last[t]["max_id"]) == (n, h, mx), t
    assert ad.verify(c)["status"] == "consistent"


def test_舊鎖不被誤奪_pid死掉的舊鎖可接手(env):
    import os
    import time
    import pytest
    lp = Path(str(_log(env)) + ".lock")
    lp.write_text("2147480000 0 abc", encoding="utf-8")           # 剛建立（鎖齡 < 10 分鐘）：即使 pid 不存在也不強奪
    try:
        with pytest.raises(TimeoutError):
            with ad._FileLock(_log(env)):
                pass
        assert lp.exists()
        t = time.time() - 700                                       # 超過 10 分鐘且 pid 不存在：可接手
        os.utime(lp, (t, t))
        with ad._FileLock(_log(env)) as lk:
            assert lk.owned
        assert not lp.exists()                                      # 退出時釋放
        lp.write_text(f"{os.getpid()} 0 abc", encoding="utf-8")   # 持有者還活著：鎖齡再舊也不強奪
        os.utime(lp, (t, t))
        with pytest.raises(TimeoutError):
            with ad._FileLock(_log(env)):
                pass
        assert lp.exists()
    finally:
        if lp.exists():
            lp.unlink()


def test_鎖退出只刪自己建的(env):
    lk = ad._FileLock(_log(env))
    lp = lk.p
    with lk:
        lp.write_text("1 0 someone-else", encoding="utf-8")        # 被別人換成他的鎖
    assert lp.exists()
    lp.unlink()


def test_symlink拒寫(env, tmp_path):
    import os
    import pytest
    c = env.conn()
    target = tmp_path / "elsewhere.txt"
    target.write_text("", encoding="utf-8")
    try:
        os.symlink(target, _log(env))
    except (OSError, NotImplementedError):
        pytest.skip("這個環境不能建 symlink")
    with pytest.raises(OSError):
        ad.retain(c, now=T0)
    assert target.read_text(encoding="utf-8") == ""             # 沒有寫到被導向的地方
    assert not ad.verify(c)["ok"]


def test_排程每小時才動_且失敗不丟例外(env, monkeypatch):
    c = env.conn()
    r = ad.scheduled(c, now=T0)
    assert r and r["retained"]["done"]
    assert ad.scheduled(c, now=T0 + dt.timedelta(minutes=5)) is None      # 沒到一小時：什麼都不做
    monkeypatch.setattr(ad, "verify_then_retain", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ad.scheduled(c, now=T0 + dt.timedelta(hours=3)) is None        # 失敗只記 log


def test_摘要與log裡沒有密碼類欄位(env):
    c = env.conn()
    _seed(c)
    ad.retain(c, now=T0)
    txt = _log(env).read_text(encoding="utf-8").lower()
    assert "password" not in txt and "passwd" not in txt


def test_登入停用時稽核摘要欄位帶標記(env, monkeypatch):
    c = env.conn()
    st.audit(c, "x", "act", "正常登入下")
    monkeypatch.setenv(api.AUTH_DISABLED_ENV, "1")
    st.audit(c, "(未登入·登入已停用)", "act", "匿名操作")
    access_control.log_export(c, username="(未登入·登入已停用)", level="admin", ip=None, method="GET", path="/p",
                              name="n", filename=None, rows=1, summary="條件：a=1", status=200)
    access_control.log_export(c, username="u", level="admin", ip=None, method="GET", path="/p",
                              name="n", filename=None, rows=1, summary=None, status=200)
    details = [r["detail"] for r in c.execute("SELECT detail FROM ft_audit ORDER BY id")]
    assert details[0] == "正常登入下" and details[1] == "[登入停用]匿名操作"
    sums = [r[0] for r in c.execute("SELECT filter_summary FROM export_log ORDER BY id")]
    assert sums[-2] == "[登入停用]條件：a=1" and sums[-1] == "[登入停用]"
    monkeypatch.delenv(api.AUTH_DISABLED_ENV)
    st.audit(c, "x", "act", "又恢復登入")
    assert c.execute("SELECT detail FROM ft_audit ORDER BY id DESC").fetchone()[0] == "又恢復登入"
