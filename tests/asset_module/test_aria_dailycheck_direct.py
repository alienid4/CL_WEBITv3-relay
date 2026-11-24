"""VMware 每日檢查快照：系統直抓 vROps（2.069.0，aria_dailycheck_direct）。

用假 transport 接真的 VropsClient，把整條路走一遍：分頁讀到底、total 對不上就 complete=0、某一類失敗其他類照跑、
警示等級過濾、50 個一組、重試／呼叫上限／時間箱、不洩漏密碼與權杖、只有 GET（外加登入／釋放權杖）、
窗口與「已有完整批次就跳過」、功能開關預設關、盤中靜默延後、走跟跳板機中繼同一條 parse→apply。

⚠️ 回應是依 aria_daily_snap.ps1 v3 實際讀的欄位手造的假資料，**不是**真實 vROps 的回應；沒有對真實 vROps 跑過。
"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import aria_dailycheck as ad  # noqa: E402
import aria_dailycheck_direct as add  # noqa: E402
import auth  # noqa: E402
import credential_store  # noqa: E402
import db  # noqa: E402
import integration_http as ih  # noqa: E402
import quiet_window as qw  # noqa: E402
import vrops_constants as C  # noqa: E402
import vrops_import as vi  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PW = "pw-for-direct-test-4321"
FAKE_SESSION = "tok-abcdef-0123456789-abcdef-0123456789-abcdef-0123456789"
URL = "https://aria.example.local"
SAT = lambda h, m=0: dt.datetime(2026, 11, 2, h, m)    # noqa: E731  週一早上（08:30 前不在盤中靜默窗、是工作日）
MON = lambda h, m=0: dt.datetime(2026, 11, 2, h, m)    # noqa: E731  週一
VCID = "vc-uuid-1"


def _ident(vc=VCID):
    return [{"identifierType": {"name": "VMEntityVCID"}, "value": vc}]


def build_world(n_hosts=7, n_ds=3, alerts=None):
    hosts = [{"identifier": f"h{i}", "resourceKey": {"name": f"esxi{i}.example.test", "resourceKindKey": "HostSystem", "resourceIdentifiers": _ident()}}
             for i in range(n_hosts)]
    dss = [{"identifier": f"d{i}", "resourceKey": {"name": f"DS_{i}", "resourceKindKey": "Datastore", "resourceIdentifiers": _ident()}} for i in range(n_ds)]
    props = {h["identifier"]: {"runtime|connectionState": "connected", "runtime|maintenanceState": "notInMaintenance", "summary|other": "x"} for h in hosts}
    props.update({d["identifier"]: {"summary|type": "VMFS", "summary|isLocal": "false"} for d in dss})
    stats = {h["identifier"]: {"cpu|usage_average": (12.5, 1762470000000)} for h in hosts}
    stats.update({d["identifier"]: {"capacity|total_capacity": (1000, 1762470000000), "capacity|available_space": (400, 1762470000000),
                                   "summary|total_number_hosts": (3, 1762470000000)} for d in dss})
    alerts = alerts if alerts is not None else [
        {"alertId": "a1", "resourceId": "h0", "alertLevel": "CRITICAL", "alertDefinitionName": "Host down", "status": "ACTIVE",
         "controlState": "OPEN", "ownerName": "", "startTimeUTC": 1762460000000, "updateTimeUTC": 1762470000000},
        {"alertId": "a2", "resourceId": "d1", "alertLevel": "info", "alertDefinitionName": "noise", "status": "ACTIVE", "controlState": "OPEN",
         "startTimeUTC": 1762460000000, "updateTimeUTC": 1762470000000},
        {"alertId": "a3", "resourceId": "d2", "alertLevel": "warning", "alertDefinitionName": "Datastore low", "status": "ACTIVE",
         "controlState": "ASSIGNED", "ownerName": "ops", "startTimeUTC": 1762460000000, "updateTimeUTC": 1762470000000},
    ]
    adapters = [{"id": "ad1", "resourceKey": {"name": "vCenter-A", "resourceIdentifiers": _ident()}, "lastCollected": 1762470000000,
                 "lastHeartbeat": 1762470000000, "messageFromAdapterInstance": "OK"}]
    return {"hosts": hosts, "dss": dss, "props": props, "stats": stats, "alerts": alerts, "adapters": adapters}


class FakeAria:
    """假 transport（接真的 VropsClient）。fail_plan：路徑 → 還要失敗幾次（99＝一直失敗）。lie_total：路徑 → 假的 totalCount。"""

    def __init__(self, world, bad_login=False, fail_plan=None, lie_total=None, login_status=None, expire_first_get=False, bad_relogin=False,
                 login_raises=None, on_request=None, dup_ids=(), extra_id=False, omit_res=()):
        self.w, self.bad_login = world, bad_login
        self.fail = dict(fail_plan or {})
        self.lie = dict(lie_total or {})
        self.calls: list[dict] = []
        self.login_status, self.expire_first_get, self.bad_relogin, self.login_raises, self.on_request = login_status, expire_first_get, bad_relogin, login_raises, on_request
        self.acquire_n, self.closed = 0, False
        self.dup_ids, self.extra_id, self.omit_res = set(dup_ids), extra_id, set(omit_res)

    def _page(self, items, params, key, path):
        size, page = params["pageSize"], params["page"]
        total = self.lie.get(path + key, len(items))
        return {"pageInfo": {"totalCount": total, "page": page, "pageSize": size}, key: items[page * size:(page + 1) * size]}

    def request(self, method, url, *, headers=None, json_body=None, form=None, params=None, timeout=30.0):
        path = urlsplit(url).path
        self.calls.append({"method": method, "path": path, "params": params, "host": urlsplit(url).netloc, "timeout": timeout})
        R = ih.HttpResult
        if self.on_request:
            self.on_request(self, method, path)
        if path == C.TOKEN_ACQUIRE:
            self.acquire_n += 1
            if self.login_raises:
                raise self.login_raises
            if self.login_status:
                return R(self.login_status, {"message": "boom SECRET-INTERNAL-HOST"}, "")
            if self.bad_relogin and self.acquire_n >= 2:
                return R(401, {"message": "bad"}, "")
            if self.bad_login:
                return R(401, {"message": f"bad credentials for {json_body['password']}"}, "")
            return R(200, {"token": FAKE_SESSION, "validity": 1}, "")
        if path == C.TOKEN_RELEASE:
            return R(204, None, "")
        if (headers or {}).get(C.AUTH_HEADER) != f"{C.AUTH_SCHEME} {FAKE_SESSION}":
            return R(401, {"message": "no token"}, "")
        if self.expire_first_get:
            self.expire_first_get = False
            return R(401, {"message": "expired"}, "")
        if self.fail.get(path, 0) > 0:
            self.fail[path] -= 1
            return R(500, {"message": f"boom password={PW} token {FAKE_SESSION}"}, "")
        w = self.w
        if path == C.ADAPTERS:
            return R(200, {"adapterInstancesInfoDto": w["adapters"]}, "")
        if path == C.RESOURCES:
            if "resourceId" in params:
                allr = {r["identifier"]: r for r in w["hosts"] + w["dss"]}
                return R(200, {"resourceList": [allr[i] for i in params["resourceId"] if i in allr and i not in self.omit_res]}, "")
            items = {"HostSystem": w["hosts"], "Datastore": w["dss"]}[params["resourceKind"]]
            return R(200, self._page(items, params, "resourceList", path + params["resourceKind"]), "")
        if path == C.RESOURCES_PROPERTIES:
            ids = [i for i in params["resourceId"] if i in w["props"]]
            ids += [i for i in ids if i in self.dup_ids] + (["zz-extra"] if self.extra_id else [])
            return R(200, {"resourcePropertiesList": [{"resourceId": i, "property": [{"name": k, "value": v} for k, v in w["props"].get(i, {}).items()]}
                                                      for i in ids]}, "")
        if path == C.RESOURCES_STATS_LATEST:
            vals = []
            ids = list(params["resourceId"])
            ids += [i for i in ids if i in self.dup_ids] + (["zz-extra"] if self.extra_id else [])
            for i in ids:
                st = [{"statKey": {"key": k}, "timestamps": [t], "data": [v]} for k, (v, t) in w["stats"].get(i, {}).items() if k in params["statKey"]]
                vals.append({"resourceId": i, "stat-list": {"stat": st}})
            return R(200, {"values": vals}, "")
        if path == C.ALERTS:
            return R(200, self._page(w["alerts"], params, "alerts", path), "")
        return R(404, None, "")

    def close(self):
        self.closed = True

    def gets(self, path):
        return [c for c in self.calls if c["path"] == path]


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    monkeypatch.setattr(vi, "KEY_PATH", str(tmp_path / "cred.key"))
    conn.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='vrops_api'")
    conn.commit()
    credential_store.save(conn, vi.CRED_NAME, "vrops", "svc_aria", PW, key_path=vi.KEY_PATH)
    vi.save_config(conn, url=URL, auth_source="", ca_bundle="", enabled=False, interval_hours=24)
    db.set_setting(conn, add.K_ENABLED, "1")          # 預設開著（直接呼叫 run_snapshot 的測試用）；要測「預設關」的測試自己關
    monkeypatch.setattr(add, "SLEEP", lambda s: None)
    monkeypatch.setattr(add, "RUN_IN_BACKGROUND", False)
    monkeypatch.setattr(add, "PAGE_SIZE", 3)
    monkeypatch.setattr(add, "_state", {"running": False})
    qw.set_clock(lambda: SAT(6, 0))

    class E:
        pass
    e = E()
    e.conn, e.db_path, e.mp, e.tmp = conn, db_path, monkeypatch, tmp_path

    def fake(world=None, **kw):
        f = FakeAria(world or build_world(), **kw)
        monkeypatch.setattr(ih, "TRANSPORT_FACTORY", lambda ca: f)
        return f
    e.fake = fake
    yield e
    conn.close()
    qw.set_clock(None)


def _batch(conn):
    if not ad._has(conn, "aria_dc_batch"):
        return None
    return conn.execute("SELECT * FROM aria_dc_batch ORDER BY collected_at DESC, batch_id DESC LIMIT 1").fetchone()


def _turn_on(conn):
    db.set_setting(conn, add.K_ENABLED, "1")


# ============================================================ 抓、parse、apply
def test_完整批次_分頁讀到底_走同一條_parse_apply_存進_aria_dc表(env):
    f = env.fake()
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "ok" and out["complete"] is True, out
    b = _batch(env.conn)
    assert b["complete"] == 1 and b["collector_version"] == add.COLLECTOR_VERSION and b["batch_id"].startswith("ariadc_direct_")
    assert b["collected_at"] == "2026-11-02 06:00:00" and b["rule_version"] == ad.RULE_VERSION
    assert env.conn.execute("SELECT COUNT(*) FROM aria_dc_host WHERE batch_id=?", (b["batch_id"],)).fetchone()[0] == 7       # 7 台、頁大小 3 → 3 頁讀完
    assert len(f.gets(C.RESOURCES)) == 3 + 1 + 1                                                                              # 主機 3 頁＋資料存放區 1 頁＋警示的資源查詢 1 次
    h = env.conn.execute("SELECT * FROM aria_dc_host WHERE res_id='h0' AND batch_id=?", (b["batch_id"],)).fetchone()
    assert (h["vcid"], h["conn"], h["maint"], h["collected_at"]) == (VCID, "connected", "notInMaintenance", "2025-11-07 07:00:00")   # 毫秒 → 台北時間
    d = env.conn.execute("SELECT * FROM aria_dc_ds WHERE res_id='d0' AND batch_id=?", (b["batch_id"],)).fetchone()
    assert (d["ds_type"], d["cap_gb"], d["free_gb"], d["hosts"], d["local"], d["vcid"]) == ("VMFS", 1000, 400, 3, 0, VCID)
    a = env.conn.execute("SELECT * FROM aria_dc_adapter WHERE batch_id=?", (b["batch_id"],)).fetchone()
    assert (a["name"], a["vcid"], a["message"]) == ("vCenter-A", VCID, "OK")


def test_警示只留_CRITICAL_IMMEDIATE_WARNING_大小寫不拘_total是讀到的全部(env):
    env.fake()
    add.run_snapshot(env.conn, "manual", "admin")
    b = _batch(env.conn)
    rows = env.conn.execute("SELECT alert_id, level, control, owner, res_kind, res_name, vcid FROM aria_dc_alert WHERE batch_id=? ORDER BY alert_id", (b["batch_id"],)).fetchall()
    assert [(r["alert_id"], r["level"]) for r in rows] == [("a1", "CRITICAL"), ("a3", "WARNING")]                           # info 被濾掉，warning 轉大寫
    assert rows[0]["res_kind"] == "HostSystem" and rows[0]["res_name"] == "esxi0.example.test" and rows[0]["vcid"] == VCID
    assert rows[1]["control"] == "ASSIGNED" and rows[1]["owner"] == "ops"
    summary = json.loads(b["summary_json"])
    assert summary["alerts"]["total"] == 3 and summary["alerts"]["fetched"] == 3 and summary["alerts"]["rows"] == 2


def test_總數對不上_存起來但_complete_是0_不當正常(env):
    env.fake(lie_total={C.RESOURCES + "HostSystem" + "resourceList": 9})        # vROps 說 9 台，只讀到 7 台
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "incomplete" and out["complete"] is False
    assert any("分頁不完整" in p and "9" in p and "7" in p for p in out["problems"]), out["problems"]
    assert _batch(env.conn)["complete"] == 0
    assert ad.latest_complete(env.conn) is None


def test_某一類失敗_其他類照跑_該類_ok_false_並寫原因(env):
    f = env.fake(fail_plan={C.RESOURCES_STATS_LATEST: 99})
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "incomplete"
    b = _batch(env.conn)
    s = json.loads(b["summary_json"])
    assert s["hosts"]["ok"] is False and s["datastores"]["ok"] is False                       # 兩類都要最新指標
    assert s["adapters"]["ok"] is True and s["alerts"]["ok"] is True                           # 警示與收集器照跑
    assert env.conn.execute("SELECT COUNT(*) FROM aria_dc_alert WHERE batch_id=?", (b["batch_id"],)).fetchone()[0] == 2
    assert env.conn.execute("SELECT COUNT(*) FROM aria_dc_host WHERE batch_id=?", (b["batch_id"],)).fetchone()[0] == 0
    assert any("ESXi 主機讀取失敗" in p for p in out["problems"])
    assert len(f.gets(C.RESOURCES_STATS_LATEST)) >= 4                                          # 失敗最多重試 3 次（共 4 次）才放棄


def test_每個請求最多重試3次_前兩次失敗第三次成功就是完整(env):
    f = env.fake(fail_plan={C.RESOURCES_PROPERTIES: 2})
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "ok", out
    assert len(f.gets(C.RESOURCES_PROPERTIES)) == 4                                       # 主機那組失敗 2 次＋成功 1 次，資料存放區 1 次


def test_properties_50個一組(env):
    f = env.fake(build_world(n_hosts=120, n_ds=2))
    env.mp.setattr(add, "PAGE_SIZE", 500)
    add.run_snapshot(env.conn, "manual", "admin")
    # 120 台主機 → 3 組（50＋50＋20），2 個資料存放區 → 1 組；統計同
    ps = [len(c["params"]["resourceId"]) for c in f.gets(C.RESOURCES_PROPERTIES)]
    assert ps == [50, 50, 20, 2]
    assert [len(c["params"]["resourceId"]) for c in f.gets(C.RESOURCES_STATS_LATEST)] == [50, 50, 20, 2]


def test_掛載主機數_預設不用關聯_開了才用且失敗只留空(env):
    f = env.fake()
    add.run_snapshot(env.conn, "manual", "admin")
    assert not [c for c in f.calls if c["path"].endswith("/relationships")]
    db.set_setting(env.conn, add.K_MOUNT, "1")
    f2 = env.fake()
    out = add.run_snapshot(env.conn, "manual", "admin")
    rel = [c["path"] for c in f2.calls if c["path"].endswith("/relationships")]
    assert len(set(rel)) == 3 and len(rel) == 12                                           # 3 個資料存放區各查一次；失敗各重試到第 4 次（同腳本的 Call）
    assert out["status"] == "ok"                                                            # 假 transport 沒有 relationships 路徑 → 404 → 只讓掛載數留空，不影響完整性
    s = json.loads(_batch(env.conn)["summary_json"])
    assert s["datastores"]["ok"] is True                                                    # 失敗只讓掛載數留空，不讓整類失敗


def test_登入失敗_存成不完整批次_密碼與權杖不外洩(env):
    f = env.fake(bad_login=True)
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "incomplete" and any("登入失敗" in p for p in out["problems"])
    blob = json.dumps(out, ensure_ascii=False) + json.dumps(add._last_compat(env.conn), ensure_ascii=False)
    blob += json.dumps([dict(r) for r in env.conn.execute("SELECT * FROM aria_dc_batch")], ensure_ascii=False, default=str)
    blob += json.dumps([dict(r) for r in env.conn.execute("SELECT * FROM credential_use_audit")], ensure_ascii=False, default=str)
    assert PW not in blob and FAKE_SESSION not in blob
    assert add._last_compat(env.conn)["error_class"] == "auth" and out["error_class"] == "auth"
    assert not [c for c in f.calls if c["path"] not in (C.TOKEN_ACQUIRE, C.TOKEN_RELEASE)]       # 登入不成功就不再打其他 API


def test_錯誤訊息不含密碼與權杖_即使_vROps_把它們回在錯誤本文(env):
    env.fake(fail_plan={C.ALERTS: 99})
    out = add.run_snapshot(env.conn, "manual", "admin")
    blob = json.dumps(out, ensure_ascii=False) + json.dumps([dict(r) for r in env.conn.execute("SELECT * FROM aria_dc_batch")], ensure_ascii=False, default=str)
    assert "警示讀取失敗" in blob and PW not in blob and FAKE_SESSION not in blob


def test_只有_GET_外加登入與釋放權杖_只連設定的那一台(env):
    f = env.fake()
    add.run_snapshot(env.conn, "manual", "admin")
    posts = {c["path"] for c in f.calls if c["method"] != "GET"}
    assert posts == {C.TOKEN_ACQUIRE, C.TOKEN_RELEASE}
    assert {c["host"] for c in f.calls} == {"aria.example.local"}
    assert f.gets(C.TOKEN_RELEASE)                                                                       # 用完有釋放權杖


def test_總呼叫上限與時間箱_超過就停_該類寫失敗原因(env):
    env.fake()
    env.mp.setattr(add, "MAX_CALLS", 3)
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "incomplete" and any("超過呼叫預算" in p for p in out["problems"])
    t = iter(range(0, 10_000, 1000))
    env.fake()
    env.mp.setattr(add, "MAX_CALLS", 3000)
    env.mp.setattr(add, "CLOCK", lambda: next(t))              # 每次讀時鐘就過 1000 秒：第 3 次檢查起超過 2700 秒
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "failed" and out["error_class"] == "budget"     # 時間箱用完：收集到一半的資料不解析、不寫批次


def test_沒設定網址或憑證_不連線_不存批次_寫失敗原因(env):
    env.conn.execute("DELETE FROM app_settings WHERE key=?", (vi.K_URL,))
    env.conn.commit()
    f = env.fake()
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "failed" and "網址" in out["message"] and f.calls == []
    assert _batch(env.conn) is None
    assert add._last_compat(env.conn)["status"] == "failed" and add._last_compat(env.conn)["error_class"] == "config"


# ============================================================ 1.5 看得到來源與今天有沒有收到
def test_status_view_看得出系統直抓與今天有沒有完整批次_沒收到不是0(env):
    now = qw.local_now()
    v = ad.status_view(env.conn, now)
    assert v["today"] == {"day": "2026-11-02", "complete": False, "collected_at": None, "source": None, "batch_id": None}
    assert v["direct"]["enabled"] is True and v["direct"]["last_ok"] is None and v["latest"] is None
    env.fake()
    add.run_snapshot(env.conn, "manual", "admin")
    v = ad.status_view(env.conn, now)
    assert v["today"]["complete"] is True and v["today"]["source"] == "direct" and v["latest_complete"]["source"] == "direct"
    assert v["direct"]["last_ok"]["collected_at"] == "2026-11-02 06:00:00"


def test_手動中繼批次與直抓同一天都有_取最新且完整的(env):
    env.fake()
    qw.set_clock(lambda: SAT(6, 0))
    add.run_snapshot(env.conn, "manual", "admin")
    # 07:30 另有一批跳板機中繼來的完整批次
    parsed = {"aria": {"ok": True, "error": ""}, "dups": {}, "complete": True, "problems": [],
              **{k: {"ok": True, "error": "", "total": 0, "fetched": 0, "rows": []} for k in ad.KINDS}}
    ad.apply(env.conn, "ariadc_20261102T073000", "2026-11-02T07:30:00+08:00", "aria_daily_snap 3", parsed, "2026-11-02 07:31:00")
    env.conn.commit()
    lc = ad.latest_complete(env.conn)
    assert lc["batch_id"] == "ariadc_20261102T073000" and ad.source_of(lc) == "relay"
    v = ad.status_view(env.conn, SAT(8, 0))
    assert v["today"]["source"] == "relay" and v["direct"]["last_ok"]["collected_at"] == "2026-11-02 06:00:00"


# ============================================================ 排程：開關、窗口、重試、跳過
def test_功能開關預設關閉_tick_什麼都不做_不連線(env):
    f = env.fake()
    db.set_setting(env.conn, add.K_ENABLED, "0")
    assert add.enabled(env.conn) is False
    assert add.tick(env.conn) == {"status": "disabled"} and f.calls == []
    assert add.start_manual(env.conn, "admin")["status"] == "failed" and f.calls == []         # 關著時「立即抓一次」也拒絕
    _turn_on(env.conn)
    env.conn.execute("UPDATE feature_flags SET enabled=0 WHERE module_key='vrops_api'")       # 模組關著＋子開關開著＝還是不跑
    env.conn.commit()
    assert add.tick(env.conn) == {"status": "disabled"} and f.calls == []


@pytest.mark.parametrize("h,m,due", [(5, 59, False), (6, 0, True), (7, 40, True), (7, 41, False), (8, 0, False), (8, 29, False), (12, 0, False)])
def test_窗口_0600到0740開始_0800前收尾(env, h, m, due):
    f = env.fake()
    _turn_on(env.conn)
    qw.set_clock(lambda: SAT(h, m))
    out = add.tick(env.conn)
    assert bool(f.calls) is due, out


def test_今天已有完整批次就不再抓_沒完整就在窗口內每20分鐘重試(env):
    f = env.fake(lie_total={C.RESOURCES + "HostSystem" + "resourceList": 9})           # 第一次不完整
    _turn_on(env.conn)
    out = add.tick(env.conn)
    assert out["status"] == "incomplete"
    n = len(f.calls)
    qw.set_clock(lambda: SAT(6, 19))
    assert add.tick(env.conn) == {"status": "retry_later"} and len(f.calls) == n        # 不到 20 分鐘不重試
    f.lie.clear()                                                                       # vROps 恢復
    qw.set_clock(lambda: SAT(6, 20))
    assert add.tick(env.conn)["status"] == "ok" and len(f.calls) > n                    # 20 分鐘後重試，這次完整
    n2 = len(f.calls)
    qw.set_clock(lambda: SAT(7, 0))
    assert add.tick(env.conn) == {"status": "done_today"} and len(f.calls) == n2          # 今天已完整 → 不再抓


def test_今天已有跳板機中繼的完整批次_直抓不重複抓(env):
    f = env.fake()
    _turn_on(env.conn)
    parsed = {"aria": {"ok": True, "error": ""}, "dups": {}, "complete": True, "problems": [],
              **{k: {"ok": True, "error": "", "total": 0, "fetched": 0, "rows": []} for k in ad.KINDS}}
    ad.apply(env.conn, "ariadc_20261102T060000", "2026-11-02T06:00:00+08:00", "aria_daily_snap 3", parsed, "2026-11-02 06:01:00")
    env.conn.commit()
    qw.set_clock(lambda: SAT(6, 30))
    assert add.tick(env.conn) == {"status": "done_today"} and f.calls == []


def test_昨天的完整批次不算今天(env):
    env.fake()
    _turn_on(env.conn)
    parsed = {"aria": {"ok": True, "error": ""}, "dups": {}, "complete": True, "problems": [],
              **{k: {"ok": True, "error": "", "total": 0, "fetched": 0, "rows": []} for k in ad.KINDS}}
    ad.apply(env.conn, "ariadc_20261101T060000", "2026-11-01T06:00:00+08:00", "aria_daily_snap 3", parsed, "2026-11-01 06:01:00")
    env.conn.commit()
    assert add.tick(env.conn)["status"] == "ok"


def test_登入被拒_60分鐘內不再試_一天最多3次_達上限寫放棄紀錄(env):
    f = env.fake(bad_login=True)
    _turn_on(env.conn)
    assert add.tick(env.conn)["status"] == "incomplete"
    assert len(f.gets(C.TOKEN_ACQUIRE)) == 1
    qw.set_clock(lambda: SAT(6, 30))
    assert add.tick(env.conn) == {"status": "auth_backoff"}                              # 登入被拒：60 分鐘內不再試（一般失敗只要 20 分鐘）
    qw.set_clock(lambda: SAT(7, 1))
    assert add.tick(env.conn)["status"] == "incomplete"
    ps = add.public_state(env.conn, SAT(7, 2))
    assert ps["auth_rejects_today"] == 2 and ps["next_try"] is None                       # 下次可試是 08:01，已超過最晚開始時間
    add.run_snapshot(env.conn, "manual", "admin")                                         # 手動也算進同一個額度（登入鎖帳保護共用）：第 3 次
    assert add.public_state(env.conn, SAT(7, 3))["auth_rejects_today"] == 3
    qw.set_clock(lambda: SAT(7, 40))
    assert add.tick(env.conn) == {"status": "gave_up_today"}
    assert add.public_state(env.conn, SAT(7, 41))["gave_up_today"]["reason"] == "login_limit"
    assert add.start_manual(env.conn, "admin")["status"] == "failed"                      # 達上限：手動也不再試
    add.tick(env.conn)
    assert env.conn.execute("SELECT COUNT(*) FROM aria_dc_direct_runs WHERE gave_up=1").fetchone()[0] == 1   # 放棄紀錄一天只寫一筆


def test_背景執行緒_tick_立刻返回_抓取在另一條執行緒_同時只有一個(env):
    import threading
    env.mp.setattr(add, "RUN_IN_BACKGROUND", True)
    gate, started = threading.Event(), threading.Event()
    done = threading.Event()

    def slow(c, trigger="schedule", user=None, lock_held=False):
        started.set()
        gate.wait(5)
        done.set()
        return {"status": "ok"}
    env.mp.setattr(add, "run_snapshot", slow)
    _turn_on(env.conn)
    # 背景執行緒自己開連線（同一個 DB 檔）
    out = add.tick(env.conn)
    assert out["status"] == "started"
    assert started.wait(5)
    assert add._state["running"] is True
    assert add.tick(env.conn) == {"status": "busy"}                                       # 跑到一半，下一分鐘的 tick 不會再開一個
    assert add.start_manual(env.conn, "admin")["status"] == "busy"
    gate.set()
    assert done.wait(5)
    for _ in range(100):
        if not add._state["running"]:
            break
        threading.Event().wait(0.05)
    assert add._state["running"] is False


# ============================================================ 盤中靜默窗
def test_盤中靜默窗_tick_延後_不連線_記延後與待補跑(env):
    f = env.fake()
    _turn_on(env.conn)
    env.mp.setattr(add, "_window", lambda cn: ("00:00", "23:59", False))
    qw.set_clock(lambda: MON(10, 0))
    assert add.tick(env.conn) == {"status": "deferred_quiet_window"} and f.calls == []
    assert env.conn.execute("SELECT count FROM quiet_window_log WHERE entry=? AND kind='deferred'", (add.ENTRY,)).fetchone()["count"] >= 1
    assert qw.pending_is(env.conn, add.ENTRY) is True


def test_抓到一半進盤中靜默窗_不再派送新連線_這次不存批次(env):
    f = env.fake()
    state = {"n": 0}
    real = qw.in_quiet_window

    def flip(conn, now=None):
        state["n"] += 1
        return state["n"] > 4                                  # 前 4 次檢查在窗外，之後進窗
    env.mp.setattr(qw, "in_quiet_window", flip)
    env.mp.setattr(qw, "may_connect", lambda entry, run=None, mark_pending=True: True)   # 只量 checkpoint 這一道
    out = add.run_snapshot(env.conn, "schedule", None)
    assert out["status"] == "deferred", out
    assert _batch(env.conn) is None
    assert len(f.calls) <= 6
    assert add._last_compat(env.conn)["status"] == "deferred"
    env.mp.setattr(qw, "in_quiet_window", real)


def test_窗內沒有授權直接呼叫_被_guarded_拒絕(env):
    f = env.fake()
    qw.set_clock(lambda: MON(10, 0))
    out = add.run_snapshot(env.conn, "schedule", None)
    assert out.get("refused") is True and f.calls == []


# ============================================================ API（設定頁用）
def _client(tmp, db_path):
    def _dep():
        c = db.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _dep
    return TestClient(api.app)


def _login(client, db_path, username):
    c = db.get_connection(db_path)
    try:
        if not c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            db.create_user(c, username, auth.hash_password("test-password-123"))
    finally:
        c.close()
    assert client.post("/api/auth/login", json={"username": username, "password": "test-password-123"}).status_code == 200


ROLE_AWARE_TESTS = True


def test_API_開關與立即抓一次_只有admin_模組關著403(env):
    client = _client(env.tmp, env.db_path)
    try:
        assert client.get("/api/integrations/vrops/dailycheck").status_code == 401
        env.conn.execute("UPDATE feature_flags SET enabled=0 WHERE module_key='vrops_api'")
        env.conn.commit()
        _login(client, env.db_path, "admin")
        assert client.get("/api/integrations/vrops/dailycheck").status_code == 403
        env.conn.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='vrops_api'")
        env.conn.commit()
        s = client.get("/api/integrations/vrops/dailycheck").json()
        assert s["enabled"] is True and s["configured"] is True and s["today"]["complete"] is False and s["last"] == {}
        client.post("/api/auth/logout")
        _login(client, env.db_path, "someone")
        assert client.get("/api/integrations/vrops/dailycheck").status_code == 200            # 一般登入者可看
        assert client.put("/api/integrations/vrops/dailycheck", json={"enabled": True}).status_code == 403
        assert client.post("/api/integrations/vrops/dailycheck/run").status_code == 403
        client.post("/api/auth/logout")
        _login(client, env.db_path, "admin")
        env.fake()
        r = client.put("/api/integrations/vrops/dailycheck", json={"enabled": True})
        assert r.status_code == 200 and r.json()["enabled"] is True
        run = client.post("/api/integrations/vrops/dailycheck/run").json()
        assert run["status"] == "ok" and PW not in json.dumps(run) and FAKE_SESSION not in json.dumps(run)
        s = client.get("/api/integrations/vrops/dailycheck").json()
        assert s["last"]["status"] == "ok" and s["today"]["complete"] is True and s["today"]["source"] == "direct" and s["last_ok"]["batch_id"].startswith("ariadc_direct_")
        vm = client.get("/api/daily-check/vmware").json()
        assert vm["today"]["complete"] is True and vm["latest_complete"]["source"] == "direct" and vm["direct"]["enabled"] is True
    finally:
        api.app.dependency_overrides.clear()


def test_API_沒設定網址憑證就不能開自動抓取(env):
    env.conn.execute("DELETE FROM app_settings WHERE key=?", (vi.K_URL,))
    db.set_setting(env.conn, add.K_ENABLED, "0")
    env.conn.commit()
    client = _client(env.tmp, env.db_path)
    try:
        _login(client, env.db_path, "admin")
        r = client.put("/api/integrations/vrops/dailycheck", json={"enabled": True})
        assert r.status_code == 400 and add.enabled(env.conn) is False
        r = client.post("/api/integrations/vrops/dailycheck/run")
        assert r.json()["status"] == "failed"
    finally:
        api.app.dependency_overrides.clear()


# ============================================================ 2.069.0 GPT 審查後補強（時間契約、警示等級、資料嚴謹、預算、分類、紀錄、排程、背景）
def _put_batch(conn, batch_id, at, version="aria_daily_snap 3"):
    parsed = {"aria": {"ok": True, "error": ""}, "dups": {}, "complete": True, "problems": [],
              **{k: {"ok": True, "error": "", "total": 0, "fetched": 0, "rows": []} for k in ad.KINDS}}
    ad.apply(conn, batch_id, at, version, parsed, "2026-11-02 09:00:00")
    conn.commit()


def test_常數一致_呼叫上限乘間隔等於時間箱_最晚啟動加時間箱等於採用截止():
    assert add.MAX_CALLS == 800 and add.MAX_CALLS * add.DELAY_S == add.TIME_BOX_S == 1200
    assert add.last_start_hms("08:00") == "07:40:00" and add.last_start_hms("09:30") == "09:10:00"


def test_窗外手動抓的批次不讓排程停手_1_5也不採用(env):
    env.fake()
    _turn_on(env.conn)
    _put_batch(env.conn, "ariadc_20261102T055500", "2026-11-02T05:55:00+08:00")        # 05:55 手動／中繼：在 1.5 採用窗（06:00～08:00）外
    qw.set_clock(lambda: SAT(6, 0))
    assert ad.status_view(env.conn, SAT(6, 0))["today"]["complete"] is False            # 資料來源頁也寫沒收到
    assert add.tick(env.conn)["status"] == "ok"                                         # 排程照抓
    assert ad.status_view(env.conn, SAT(6, 1))["today"]["source"] == "direct"


def test_資料來源頁的收集器清單跟_1_5_採用的批次一致_同時列最新收到(env):
    env.fake()
    qw.set_clock(lambda: SAT(6, 0))
    add.run_snapshot(env.conn, "manual", "admin")                                       # 06:00 直抓（採用）
    _put_batch(env.conn, "ariadc_20261102T081000", "2026-11-02T08:10:00+08:00")         # 08:10 較晚的完整批次（窗外）
    v = ad.status_view(env.conn, SAT(8, 20))
    assert v["adopted"]["batch_id"].startswith("ariadc_direct_") and v["latest_complete"]["batch_id"] == "ariadc_20261102T081000"
    assert [a["name"] for a in v["adapters"]] == ["vCenter-A"]                          # 清單取自採用批次（08:10 那批沒有收集器）


def test_重試時槽以開始時間算_不以完成時間算(env):
    env.fake()
    _turn_on(env.conn)
    qw.set_clock(lambda: SAT(6, 0))
    rid = add._run_start(env.conn, "schedule", None, SAT(6, 0))
    qw.set_clock(lambda: SAT(6, 18))
    add._run_end(env.conn, rid, "incomplete", "server", "x", 10)                        # 06:00 開始、06:18 才結束
    qw.set_clock(lambda: SAT(6, 19))
    assert add._due(env.conn)[1] == "retry_later"
    qw.set_clock(lambda: SAT(6, 20))
    assert add._due(env.conn) == (True, "due")                                          # 下一個時槽是 06:20，不是 06:38


def test_人工失敗不延後排程_一般錯誤不共用節流(env):
    f = env.fake(login_status=503)
    _turn_on(env.conn)
    qw.set_clock(lambda: SAT(5, 59))
    assert add.run_snapshot(env.conn, "manual", "admin")["status"] == "incomplete"
    qw.set_clock(lambda: SAT(6, 0))
    f.login_status = None
    assert add.tick(env.conn)["status"] == "ok"


def test_過了0740不再啟動_寫放棄紀錄_今天不會再自動重試(env):
    f = env.fake()
    _turn_on(env.conn)
    qw.set_clock(lambda: SAT(7, 41))
    assert add.tick(env.conn) == {"status": "past_last_start"} and f.calls == []
    g = add.public_state(env.conn, SAT(7, 42))["gave_up_today"]
    assert g["reason"] == "past_last_start" and "請走備援" in g["message"]
    add.tick(env.conn)
    assert env.conn.execute("SELECT COUNT(*) FROM aria_dc_direct_runs WHERE gave_up=1").fetchone()[0] == 1
    assert ad.status_view(env.conn, SAT(7, 43))["direct"]["gave_up_today"]["reason"] == "past_last_start"


def test_沒有補跑假承諾_catchup過了時槽就是不需要(env):
    f = env.fake()
    _turn_on(env.conn)
    qw.set_clock(lambda: SAT(9, 0))
    assert add.catchup(env.conn)["status"] == "not_needed" and f.calls == []


def test_非工作日不抓(env):
    f = env.fake()
    _turn_on(env.conn)
    qw.set_clock(lambda: dt.datetime(2026, 11, 7, 6, 30))      # 週六
    assert add.tick(env.conn) == {"status": "not_working_day"} and f.calls == []


def test_直抓排在整合任務最前面():
    src = (ROOT / "APP" / "asset-module" / "backend" / "scan_service.py").read_text(encoding="utf-8")
    assert '("aria_dailycheck_direct", "vrops_import", "whatsup_service", "ad_ldap_sync")' in src


def test_跟_vROps_資產匯入共用同一把鎖_後到者回_busy(env):
    assert add._run_lock is vi._run_lock
    env.fake()
    assert vi._run_lock.acquire(blocking=False)
    try:
        assert add.run_snapshot(env.conn, "manual", "admin")["status"] == "busy"
    finally:
        vi._run_lock.release()
    seen = {}

    def hook(f, method, path):
        if path == C.ADAPTERS and "r" not in seen:
            seen["r"] = vi.run_import(env.conn, "schedule", None)
    env.fake(on_request=hook)
    assert add.run_snapshot(env.conn, "manual", "admin")["status"] == "ok"
    assert seen["r"]["status"] == "busy"                                                # 直抓進行中，資產匯入拿不到鎖


@pytest.mark.parametrize("lvl", ["CRITICAL", "IMMEDIATE", "WARNING", "INFORMATION"])
def test_警示等級跟每日檢查設定一致_設定選中的每個等級都會留下(env, lvl):
    import daily_check as dc
    assert set(dc.VM_LEVELS) == {"CRITICAL", "IMMEDIATE", "WARNING", "INFORMATION"}
    alerts = [{"alertId": f"x-{l}", "resourceId": "h0", "alertLevel": l, "alertDefinitionName": l, "status": "ACTIVE", "controlState": "OPEN",
               "startTimeUTC": 1762460000000, "updateTimeUTC": 1762470000000} for l in dc.VM_LEVELS]
    env.fake(build_world(alerts=alerts))
    dc.save_settings(env.conn, {"vm_alert_levels": [lvl]}, by="t")
    out = add.run_snapshot(env.conn, "manual", "admin")
    b = _batch(env.conn)
    got = {r["level"] for r in env.conn.execute("SELECT level FROM aria_dc_alert WHERE batch_id=?", (b["batch_id"],))}
    assert out["status"] == "ok" and lvl in got
    assert got == ({"CRITICAL", "IMMEDIATE", "WARNING"} | {lvl})                         # 預設三級 ∪ 設定


def test_ID不能是None_空白_或怪型別_該類失敗不出現字串None(env):
    w = build_world()
    w["hosts"][2]["identifier"] = None
    env.fake(w)
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "incomplete" and any("ID 格式不對" in p for p in out["problems"])
    assert env.conn.execute("SELECT COUNT(*) FROM aria_dc_host WHERE res_id IN ('None','')").fetchone()[0] == 0
    w2 = build_world()
    w2["dss"][0]["identifier"] = "   "
    env.fake(w2)
    assert add.run_snapshot(env.conn, "manual", "admin")["status"] == "incomplete"


@pytest.mark.parametrize("bad", ["Infinity", "-Infinity", "NaN", "oops"])
def test_非有限數值或無法轉換_該類不完整_不悄悄變空值(env, bad):
    w = build_world()
    w["stats"]["d0"]["capacity|available_space"] = (bad, 1762470000000)
    env.fake(w)
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "incomplete" and any("資料存放區" in p for p in out["problems"])
    assert env.conn.execute("SELECT COUNT(*) FROM aria_dc_ds").fetchone()[0] == 0


def test_properties漏回一個資源_stats漏必要鍵_都標不完整並寫缺幾個(env):
    w = build_world()
    w["props"].pop("h3")
    env.fake(w)
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "incomplete" and any("缺 1、" in p for p in out["problems"])
    w2 = build_world()
    w2["stats"]["d1"].pop("capacity|available_space")
    env.fake(w2)
    out2 = add.run_snapshot(env.conn, "manual", "admin")
    assert any("剩餘缺 1 個" in p for p in out2["problems"])


def test_實際使用的鍵與等級寫進批次params(env):
    env.fake()
    add.run_snapshot(env.conn, "manual", "admin")
    raw = json.loads(_batch(env.conn)["raw_sha_json"])
    p = raw[0]["params"]
    assert p["host_conn"] == add.HOST_CONN_KEY and p["ds_cap"] == add.DS_CAP_KEY and p["criticality"] == ["CRITICAL", "IMMEDIATE", "WARNING"]


def test_單頁筆數上限_伺服器不理pageSize就失敗不吃光記憶體(env):
    env.fake()
    env.mp.setattr(add, "MAX_PAGE_ITEMS", 2)
    env.mp.setattr(add, "PAGE_SIZE", 500)             # 單頁一次回 7 筆 > 2
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "incomplete" and any("單頁筆數超過上限" in p for p in out["problems"])


def test_每個實際送出的請求含重試都受呼叫上限約束(env):
    f = env.fake(fail_plan={C.ADAPTERS: 99, C.RESOURCES: 99, C.ALERTS: 99, C.RESOURCES_PROPERTIES: 99, C.RESOURCES_STATS_LATEST: 99})
    env.mp.setattr(add, "MAX_CALLS", 5)
    add.run_snapshot(env.conn, "manual", "admin")
    api_calls = [c for c in f.calls if c["path"] not in (C.TOKEN_ACQUIRE, C.TOKEN_RELEASE)]
    assert len(api_calls) <= 5


def test_登入錯誤分類_連線_5xx_不計入登入保護_只有帳密被拒才算(env):
    _turn_on(env.conn)
    for kw, cls in (({"login_status": 503}, "server"), ({"login_raises": ih.TransportError("連不上目標主機，請確認網址、埠與防火牆")}, "connect"),
                    ({"login_raises": ih.TransportError("連線逾時，請確認網路與防火牆")}, "timeout"),
                    ({"login_raises": ih.TransportError("TLS 憑證驗證失敗，請確認 CA 憑證檔路徑")}, "tls"), ({"bad_login": True}, "auth")):
        env.fake(**kw)
        out = add.run_snapshot(env.conn, "manual", "admin")
        assert out["error_class"] == cls, (kw, out)
    ps = add.public_state(env.conn, SAT(6, 0))
    assert ps["auth_rejects_today"] == 1                                                   # 只有最後那次帳密被拒


def test_權杖過期後重新登入被拒_立刻停止其餘類別_記為登入失敗(env):
    f = env.fake(expire_first_get=True, bad_relogin=True)
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["error_class"] == "auth" and out["status"] == "incomplete"
    assert f.acquire_n == 2                                                                # 一次登入＋一次重新登入，沒有再重跑 GET 四次
    assert len([c for c in f.calls if c["path"] not in (C.TOKEN_ACQUIRE, C.TOKEN_RELEASE)]) == 1
    assert add.public_state(env.conn, SAT(6, 0))["auth_rejects_today"] == 1


def test_使用者只看到固定文字_不回vROps原始訊息本文(env):
    env.fake(login_status=500, fail_plan={})
    out = add.run_snapshot(env.conn, "manual", "admin")
    blob = json.dumps(out, ensure_ascii=False) + json.dumps(add.status(env.conn), ensure_ascii=False, default=str)
    blob += json.dumps([dict(r) for r in env.conn.execute("SELECT * FROM aria_dc_batch")], ensure_ascii=False, default=str)
    assert "SECRET-INTERNAL-HOST" not in blob and "HTTP 500" in blob


def test_每次執行寫執行紀錄_開始前先寫running_重啟後孤兒標interrupted(env):
    f = env.fake()
    seen = {}

    def hook(fk, method, path):
        if "run" not in seen and path == C.ADAPTERS:
            seen["run"] = [dict(r) for r in env.conn.execute("SELECT status FROM aria_dc_direct_runs")]
    f.on_request = hook
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert seen["run"] == [{"status": "running"}]                                           # 抓之前就有 running 紀錄
    r = env.conn.execute("SELECT * FROM aria_dc_direct_runs").fetchone()
    assert (r["status"], r["trigger"], r["started_by"], r["batch_id"], r["complete"]) == ("ok", "manual", "admin", out["batch_id"], 1) and r["calls"] > 0
    env.conn.execute("INSERT INTO aria_dc_direct_runs (day, trigger, started_at, status) VALUES ('2026-11-02','schedule','2026-11-02 06:07:00','running')")
    env.conn.commit()
    assert add.mark_interrupted(env.conn) == 1
    assert env.conn.execute("SELECT status FROM aria_dc_direct_runs ORDER BY id DESC LIMIT 1").fetchone()[0] == "interrupted"


def test_執行紀錄保留400天_不受批次30天清理影響(env):
    env.fake()
    add.ensure(env.conn)
    env.conn.execute("INSERT INTO aria_dc_direct_runs (day, trigger, started_at, status) VALUES ('2025-10-01','schedule','2025-10-01 06:00:00','ok')")
    env.conn.execute("INSERT INTO aria_dc_direct_runs (day, trigger, started_at, status) VALUES ('2024-01-01','schedule','2024-01-01 06:00:00','ok')")
    env.conn.commit()
    add.run_snapshot(env.conn, "manual", "admin")
    days = {r[0] for r in env.conn.execute("SELECT day FROM aria_dc_direct_runs")}
    assert "2025-10-01" in days and "2024-01-01" not in days                               # 400 天內的留著（批次表只留 30 天）


def test_背景執行緒建立失敗_復原狀態_留紀錄_不卡死(env):
    env.fake()
    env.mp.setattr(add, "RUN_IN_BACKGROUND", True)

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("can't start new thread")
    env.mp.setattr(add.threading, "Thread", Boom)
    out = add.start_manual(env.conn, "admin")
    assert out["status"] == "failed" and add._state["running"] is False
    assert env.conn.execute("SELECT error_class FROM aria_dc_direct_runs").fetchone()[0] == "thread"
    assert "aria_dc_direct_thread" in qw._HEALTH


def test_抓到一半把開關關掉_下一個請求前就中止_記人為中止(env):
    def hook(f, method, path):
        if path == C.ADAPTERS:
            db.set_setting(env.conn, add.K_ENABLED, "0")
    f = env.fake(on_request=hook)
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "aborted" and _batch(env.conn) is None
    assert len([c for c in f.calls if c["path"] not in (C.TOKEN_ACQUIRE, C.TOKEN_RELEASE)]) == 1


def test_釋放權杖遇到靜默窗只記錄_不覆蓋已成功的結果_且一定關連線(env):
    f = env.fake()
    import vrops_client as vc

    def boom(self):
        raise qw.QuietWindowBlocked("x")
    env.mp.setattr(vc.VropsClient, "release_token", boom)
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "ok" and f.closed is True


def test_開關變更留稽核_誰何時舊值到新值(env):
    db.set_setting(env.conn, add.K_ENABLED, "0")
    add.save_enabled(env.conn, True, "alice")
    add.save_enabled(env.conn, True, "alice")                       # 沒變不重複記
    add.save_enabled(env.conn, False, "bob")
    rows = add.audit_list(env.conn)
    assert [(r["by"], r["before"], r["after"]) for r in rows] == [("bob", "開", "關"), ("alice", "關", "開")] and rows[0]["at"]


def test_背景模式整合_不改RUN_IN_BACKGROUND_背景完成後用第二條連線驗證(env):
    import threading as th
    env.fake()
    env.mp.setattr(add, "RUN_IN_BACKGROUND", True)
    out = add.start_manual(env.conn, "admin")
    assert out["status"] == "started"
    for _ in range(300):
        if not add._state["running"]:
            break
        th.Event().wait(0.1)
    assert add._state["running"] is False
    c2 = db.get_connection(env.db_path)                                  # 第二條連線：背景執行緒有 commit
    try:
        b = c2.execute("SELECT complete, collector_version FROM aria_dc_batch").fetchone()
        r = c2.execute("SELECT status, started_by FROM aria_dc_direct_runs").fetchone()
    finally:
        c2.close()
    assert b["complete"] == 1 and b["collector_version"] == add.COLLECTOR_VERSION and (r["status"], r["started_by"]) == ("ok", "admin")
    assert add.status(env.conn)["last"]["status"] == "ok"


def test_所有層級的日誌都不含密碼與權杖(env, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    env.fake(fail_plan={C.RESOURCES_STATS_LATEST: 99, C.ALERTS: 99})
    add.run_snapshot(env.conn, "manual", "admin")
    env.fake(bad_login=True)
    add.run_snapshot(env.conn, "manual", "admin")
    assert PW not in caplog.text and FAKE_SESSION not in caplog.text


def test_關聯失敗只讓掛載主機數留空_hosts是None(env):
    w = build_world()
    for d in w["dss"]:
        w["stats"][d["identifier"]].pop("summary|total_number_hosts")      # stats 沒有掛載數，只能靠關聯；關聯又失敗
    env.fake(w)
    db.set_setting(env.conn, add.K_MOUNT, "1")
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "ok"
    assert [r["hosts"] for r in env.conn.execute("SELECT hosts FROM aria_dc_ds")] == [None, None, None]


# ---- 真實 TLS 握手（HttpxTransport）：不受信任憑證握手失敗＝tls；指定正確 CA 才登入成功
def _tls_server(tmp_path):
    import http.server
    import ipaddress
    import ssl
    import threading as th

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    def mk(tag):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subj).issuer_name(subj).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=2))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
        (tmp_path / f"{tag}.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (tmp_path / f"{tag}.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        return str(tmp_path / f"{tag}.pem"), str(tmp_path / f"{tag}.key")
    good, gkey = mk("good")
    other, _ = mk("other")
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            hits.append(self.path)
            body = json.dumps({"token": "tls-test-session-token"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            hits.append(self.path)
            self.send_response(404)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(good, gkey)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    th.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, good, other, hits


def test_真實TLS握手_不受信任憑證失敗_指定正確CA才成功(env):
    srv, good, other, hits = _tls_server(env.tmp)
    try:
        env.mp.setattr(ih, "TRANSPORT_FACTORY", ih.HttpxTransport)
        url = f"https://127.0.0.1:{srv.server_address[1]}"
        vi.save_config(env.conn, url=url, auth_source="", ca_bundle=other, enabled=False, interval_hours=24)
        out = add.run_snapshot(env.conn, "manual", "admin")
        assert out["error_class"] == "tls" and hits == []                                  # 握手就失敗，請求根本沒送到伺服器
        vi.save_config(env.conn, url=url, auth_source="", ca_bundle=good, enabled=False, interval_hours=24)
        out = add.run_snapshot(env.conn, "manual", "admin")
        assert any(h.endswith("/auth/token/acquire") for h in hits) and out["error_class"] != "tls"   # 登入成功（之後的 GET 是 404，這裡只驗握手與登入）
    finally:
        srv.shutdown()


# ============================================================ 第二輪 GPT 複查（共用鎖、transport 預算、硬截止、時間契約單一來源、ID 集合、警示 ID、開關、孤兒、紀錄落盤…）
def _no_token(f):
    return [c for c in f.calls if c["path"] not in (C.TOKEN_ACQUIRE, C.TOKEN_RELEASE)]


def test_共用鎖由主執行緒先取得_實際跑_integrations_tick_資產匯入搶不到(env):
    import scan_service
    import threading as th
    env.fake()
    _turn_on(env.conn)
    db.set_setting(env.conn, vi.K_ENABLED, "1")                      # vROps 資產匯入也到期
    called = []
    env.mp.setattr(vi, "fetch_snapshot", lambda c: called.append(1) or (_ for _ in ()).throw(RuntimeError("不該被呼叫")))
    env.mp.setattr(add, "RUN_IN_BACKGROUND", True)
    gate, started = th.Event(), th.Event()

    def slow(c, trigger="schedule", user=None, lock_held=False):
        started.set()
        gate.wait(5)
        return {"status": "ok"}
    env.mp.setattr(add, "run_snapshot", slow)
    scan_service._integrations_tick(env.conn)                        # 直抓排最前面：先開背景工作，接著同一輪輪到資產匯入
    assert vi._run_lock.locked() is True                             # 鎖在主執行緒就取好了（不是等背景執行緒）
    assert called == [] and vi.list_runs(env.conn) == []             # 資產匯入拿到 busy，沒有開始、沒有留紀錄
    assert started.wait(5)
    gate.set()
    for _ in range(100):
        if not add._state["running"]:
            break
        th.Event().wait(0.05)
    assert vi._run_lock.locked() is False                            # worker 結束才釋放


def test_呼叫預算在transport_登入_重新登入_401後重送_釋放權杖都算(env):
    slept = []
    env.mp.setattr(add, "SLEEP", slept.append)
    f = env.fake(expire_first_get=True)
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "ok" and f.acquire_n == 2                # 初次登入＋401 後重新登入
    row = env.conn.execute("SELECT calls FROM aria_dc_direct_runs").fetchone()
    assert row["calls"] == len(f.calls) - 1                           # 結束紀錄在釋放權杖之前寫：其餘每一個請求（含登入、重新登入、重送）都算
    assert slept.count(add.DELAY_S) == len(f.calls)                   # 每個實際請求（含釋放權杖）前都睡過 1.5 秒
    f2 = env.fake(expire_first_get=True)
    env.mp.setattr(add, "MAX_CALLS", 2)
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert len(f2.calls) <= 2 and out["status"] == "incomplete"       # 上限 2：登入、第一個 GET，重新登入就被擋（釋放權杖也被擋，只記錄）


def test_絕對截止_秒級邊界_timeout取剩餘秒數_釋放權杖也受約束(env):
    env.mp.setattr(add, "last_start_hms", lambda cut: "23:59:00")          # 這個測試只量截止，不量最晚啟動
    f = env.fake()
    qw.set_clock(lambda: dt.datetime(2026, 11, 2, 7, 59, 50))
    out = add.run_snapshot(env.conn, "schedule", None)
    assert f.calls and max(c["timeout"] for c in f.calls) <= 10.0 + 1e-6 and out["status"] in ("ok", "incomplete")
    f = env.fake()
    qw.set_clock(lambda: dt.datetime(2026, 11, 2, 8, 0, 0))
    out = add.run_snapshot(env.conn, "schedule", None)
    assert f.calls == [] and out["status"] == "failed" and out["error_class"] == "budget"      # 08:00:00 整：一個請求都不送，也不寫批次
    f = env.fake()
    qw.set_clock(lambda: dt.datetime(2026, 11, 2, 8, 0, 1))
    assert add.run_snapshot(env.conn, "manual", "admin")["status"] == "ok"       # 人工抓不受採用截止限制（只受 20 分鐘時間箱）


def test_最晚啟動_0740整_可以_0740零1秒不行(env):
    f = env.fake()
    _turn_on(env.conn)
    qw.set_clock(lambda: dt.datetime(2026, 11, 2, 7, 40, 1))
    assert add.tick(env.conn) == {"status": "past_last_start"} and f.calls == []
    qw.set_clock(lambda: dt.datetime(2026, 11, 2, 7, 40, 0))
    env.conn.execute("DELETE FROM aria_dc_direct_runs")
    env.conn.commit()
    assert add.tick(env.conn)["status"] == "ok"


def test_時間窗只有一個來源_自訂窗_空cutoff用預設且標示_0800半分不採用(env):
    import daily_check as dc
    f = env.fake()
    _turn_on(env.conn)
    dc.save_settings(env.conn, {"vm_batch_from": "07:00", "vm_batch_cutoff": "09:00"}, by="t")
    assert add._window(env.conn) == ("07:00", "09:00", False) and add.last_start_hms("09:00") == "08:40:00"
    qw.set_clock(lambda: SAT(6, 30))
    assert add.tick(env.conn) == {"status": "out_of_window"}
    qw.set_clock(lambda: SAT(8, 29))
    assert add.tick(env.conn)["status"] == "ok"                                      # 自訂窗內、在最晚啟動 08:40 之前
    # 空 cutoff：1.5 回到「最新完整批次」舊規則，但直抓視為預設窗並標示
    dc.save_settings(env.conn, {"vm_batch_from": "06:00", "vm_batch_cutoff": ""}, by="t")
    assert add._window(env.conn) == ("06:00", "08:00", True)
    assert add.public_state(env.conn, SAT(6, 0))["window"]["used_default"] is True and "用預設" in add.status(env.conn)["window_text"]
    env.conn.execute("DELETE FROM aria_dc_batch")
    env.conn.execute("DELETE FROM aria_dc_direct_runs")
    env.conn.commit()
    _put_batch(env.conn, "ariadc_20261102T055500", "2026-11-02T05:55:00+08:00")      # 05:55（窗外）的完整批次
    qw.set_clock(lambda: SAT(6, 0))
    assert add.tick(env.conn)["status"] == "ok"                                      # 不會被 vm_pick 的舊規則誤判成「今天已完成」
    # 08:00:30 不採用、08:00:00 採用
    dc.save_settings(env.conn, {"vm_batch_from": "06:00", "vm_batch_cutoff": "08:00"}, by="t")
    env.conn.execute("DELETE FROM aria_dc_batch")
    env.conn.commit()
    _put_batch(env.conn, "ariadc_20261102T080030", "2026-11-02T08:00:30+08:00")
    assert dc.vm_pick(env.conn, dc.settings(env.conn), SAT(8, 5)) is None and add._adopted_today(env.conn, SAT(8, 5)) is None
    _put_batch(env.conn, "ariadc_20261102T080000", "2026-11-02T08:00:00+08:00")
    assert dc.vm_pick(env.conn, dc.settings(env.conn), SAT(8, 5))["batch_id"] == "ariadc_20261102T080000"
    assert add._adopted_today(env.conn, SAT(8, 5))["batch_id"] == "ariadc_20261102T080000"


def test_回傳ID集合必須完全相等_重複_額外都不行(env):
    env.fake(dup_ids={"h0"})
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "incomplete" and any("重複 1" in p for p in out["problems"])
    env.fake(extra_id=True)
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "incomplete" and any("額外 1" in p for p in out["problems"])
    assert env.conn.execute("SELECT COUNT(*) FROM aria_dc_host").fetchone()[0] == 0


def test_警示資源ID走嚴格正規化_前後空白對得上_沒有資源存NULL(env):
    alerts = [{"alertId": "a1", "resourceId": " h0 ", "alertLevel": "CRITICAL", "alertDefinitionName": "x", "status": "ACTIVE", "controlState": "OPEN",
               "startTimeUTC": 1762460000000, "updateTimeUTC": 1762470000000},
              {"alertId": "a2", "resourceId": None, "alertLevel": "WARNING", "alertDefinitionName": "sys", "status": "ACTIVE", "controlState": "OPEN",
               "startTimeUTC": 1762460000000, "updateTimeUTC": 1762470000000}]
    env.fake(build_world(alerts=alerts))
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "ok"
    rows = {r["alert_id"]: r for r in env.conn.execute("SELECT * FROM aria_dc_alert")}
    assert (rows["a1"]["res_id"], rows["a1"]["res_name"], rows["a1"]["vcid"]) == ("h0", "esxi0.example.test", VCID)
    assert rows["a2"]["res_id"] is None
    bad = [dict(alerts[0], alertId="a3", resourceId=1.5)]
    env.fake(build_world(alerts=bad))
    assert add.run_snapshot(env.conn, "manual", "admin")["status"] == "incomplete"      # 不是字串／整數的 ID：警示這一類失敗


def test_開關在開client與登入之前就檢查_關閉後背景worker啟動也不送任何請求(env):
    f = env.fake()
    db.set_setting(env.conn, add.K_ENABLED, "0")                    # 排程判斷到期之後、worker 開跑之前被關掉
    out = add.run_snapshot(env.conn, "schedule", None)
    assert out["status"] == "aborted" and f.calls == []             # 連登入 POST 都沒送
    assert env.conn.execute("SELECT status FROM aria_dc_direct_runs").fetchone()[0] == "aborted"


def test_孤兒running_開關關著重啟後也回收_走tick路徑(env):
    add.ensure(env.conn)
    env.conn.execute("INSERT INTO aria_dc_direct_runs (day, trigger, started_at, status) VALUES ('2026-11-02','schedule','2026-11-02 06:00:00','running')")
    env.conn.commit()
    db.set_setting(env.conn, add.K_ENABLED, "0")                     # 執行中被關掉、然後服務崩潰重啟
    env.mp.setattr(add, "_state", {"running": False})
    assert add.tick(env.conn) == {"status": "disabled"}
    assert env.conn.execute("SELECT status FROM aria_dc_direct_runs").fetchone()[0] == "interrupted"


def test_結束紀錄寫入失敗_回傳註明未落盤_留health_下一輪補寫_不誤標interrupted(env):
    import sqlite3
    env.fake()
    real = add._run_end_write
    env.mp.setattr(add, "_run_end_write", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")))
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "ok" and out["audit_note"] and "稽核紀錄未落盤" in out["message"]
    assert "aria_dc_direct_runlog" in qw._HEALTH and add._PENDING_END
    env.mp.setattr(add, "_run_end_write", real)
    assert add.mark_interrupted(env.conn) == 0                       # 已成功的批次不會被誤標成 interrupted
    assert env.conn.execute("SELECT status FROM aria_dc_direct_runs").fetchone()[0] == "running"
    add.tick(env.conn)                                               # 補寫
    r = env.conn.execute("SELECT status, batch_id FROM aria_dc_direct_runs").fetchone()
    assert (r["status"], bool(r["batch_id"])) == ("ok", True) and not add._PENDING_END


def test_執行紀錄有耗時_設定頁最近一次回真實後端形狀(env):
    env.fake()
    add.run_snapshot(env.conn, "manual", "admin")
    last = add.status(env.conn)["last"]
    assert isinstance(last["elapsed_s"], int) and isinstance(last["calls"], int) and last["status"] == "ok" and last["batch_id"]


def test_1_5頁面三欄接線契約_刪掉就紅():
    vue = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "v2" / "dailycheck.vue").read_text(encoding="utf-8")
    assert "vmwareThree" in vue and 'data-testid="dc-vmware-three"' in vue and "今日收件" in vue and "最近有效批次" in vue and "最近一次執行" in vue and "看什麼" in vue


def test_工作日_自訂休市日的星期一不執行_一般星期一會執行(env):
    import daily_check as dc
    f = env.fake()
    _turn_on(env.conn)
    dc.save_settings(env.conn, {"holidays": ["2026-11-02"]}, by="t")
    qw.set_clock(lambda: SAT(6, 30))
    assert add.tick(env.conn) == {"status": "not_working_day"} and f.calls == []
    dc.save_settings(env.conn, {"holidays": []}, by="t")
    assert add.tick(env.conn)["status"] == "ok" and f.calls


@pytest.mark.parametrize("raw", ["not json {", '{"vm_alert_levels": ["BOGUS"]}', '{"vm_alert_levels": "CRITICAL"}'])
def test_警示等級設定讀不到或壞掉_不退回預設_警示這一類不完整並寫原因(env, raw):
    import daily_check as dc
    env.conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (dc.SETTING, raw))
    env.conn.commit()
    env.fake()
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "incomplete" and any("警示" in p and "不完整" in p for p in out["problems"]), out["problems"]
    s = json.loads(_batch(env.conn)["summary_json"])
    assert s["alerts"]["ok"] is False and s["hosts"]["ok"] is True       # 只有警示這一類失敗，其他照跑


# ============================================================ 第三輪 GPT 複查（只看高嚴重度）：每一點都有「改回舊寫法就紅」的測試
def test_vm_pick只有一套時間窗規則_空cutoff視為預設窗(env):
    import daily_check as dc
    dc.save_settings(env.conn, {"vm_batch_from": "06:00", "vm_batch_cutoff": ""}, by="t")
    _put_batch(env.conn, "ariadc_20261102T055500", "2026-11-02T05:55:00+08:00")
    cfg = dc.settings(env.conn)
    assert dc.vm_pick(env.conn, cfg, SAT(9, 0)) is None and add._adopted_today(env.conn, SAT(9, 0)) is None     # 1.5 與直抓都不採用窗外的 05:55
    assert dc.vm_source(env.conn, cfg, SAT(9, 0))["ok"] is False
    _put_batch(env.conn, "ariadc_20261102T063000", "2026-11-02T06:30:00+08:00")
    assert dc.vm_pick(env.conn, cfg, SAT(9, 0))["batch_id"] == "ariadc_20261102T063000" == add._adopted_today(env.conn, SAT(9, 0))["batch_id"]


def test_孤兒回收_沒有running也不留寫入交易(env):
    add.ensure(env.conn)
    env.conn.commit()
    assert add.mark_interrupted(env.conn) == 0 and env.conn.in_transaction is False
    env.conn.execute("INSERT INTO aria_dc_direct_runs (day, trigger, started_at, status) VALUES ('2026-11-02','schedule','2026-11-02 06:07:00','running')")
    env.conn.commit()
    rid = env.conn.execute("SELECT id FROM aria_dc_direct_runs").fetchone()[0]
    env.mp.setitem(add._PENDING_END, rid, ("ok", None, "", 0, None, None, 0))       # 等補寫的列：不動它，也不能留交易
    assert add.mark_interrupted(env.conn) == 0 and env.conn.in_transaction is False


def test_結束紀錄寫失敗後重啟_由已提交的批次補回_不丟批次ID(env):
    import sqlite3
    env.fake()
    real = add._run_end_write
    env.mp.setattr(add, "_run_end_write", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")))
    out = add.run_snapshot(env.conn, "manual", "admin")
    bid = out["batch_id"]
    env.mp.setattr(add, "_run_end_write", real)
    add._PENDING_END.clear()                                          # 模擬服務重啟：記憶體暫存沒了
    env.mp.setattr(add, "_state", {"running": False})
    assert add.mark_interrupted(env.conn) == 1
    r = env.conn.execute("SELECT status, batch_id, complete FROM aria_dc_direct_runs").fetchone()
    assert (r["status"], r["batch_id"], r["complete"]) == ("ok", bid, 1)  # 從批次反查補回，不是 interrupted
    env.conn.execute("INSERT INTO aria_dc_direct_runs (day, trigger, started_at, status) VALUES ('2026-11-02','schedule','2026-11-02 06:07:00','running')")
    env.conn.commit()
    add.mark_interrupted(env.conn)
    assert env.conn.execute("SELECT status FROM aria_dc_direct_runs ORDER BY id DESC LIMIT 1").fetchone()[0] == "interrupted"   # 沒有批次才是 interrupted


def test_警示資源ID_鍵不存在不合法_明確null才是NULL_資源查詢漏回讓該類不完整(env):
    base = {"alertId": "a1", "alertLevel": "CRITICAL", "alertDefinitionName": "x", "status": "ACTIVE", "controlState": "OPEN",
            "startTimeUTC": 1762460000000, "updateTimeUTC": 1762470000000}
    env.fake(build_world(alerts=[dict(base)]))                                       # 沒有 resourceId 這個鍵
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "incomplete" and any("缺少 resourceId" in p for p in out["problems"])
    env.fake(build_world(alerts=[dict(base, resourceId="h0")]), omit_res={"h0"})     # 要求查 h0，resources 漏回 h0
    out = add.run_snapshot(env.conn, "manual", "admin")
    assert out["status"] == "incomplete" and any("警示資源" in p and "缺 1" in p for p in out["problems"])
    assert env.conn.execute("SELECT COUNT(*) FROM aria_dc_alert WHERE res_id='h0' AND res_name=''").fetchone()[0] == 0
    env.fake(build_world(alerts=[dict(base, resourceId=None)]))                       # 明確 null＝沒有資源
    assert add.run_snapshot(env.conn, "manual", "admin")["status"] == "ok"
    assert env.conn.execute("SELECT res_id FROM aria_dc_alert").fetchone()[0] is None


def test_硬截止_剩餘小於最低timeout就停_不放大成0點5秒(env):
    env.mp.setattr(add, "last_start_hms", lambda cut: "23:59:00")
    f = env.fake()
    qw.set_clock(lambda: dt.datetime(2026, 11, 2, 7, 59, 59, 800000))              # 剩 0.2 秒
    out = add.run_snapshot(env.conn, "schedule", None)
    assert f.calls == [] and out["status"] == "incomplete"


def test_硬截止_收集完超過截止_不解析不寫批次(env):
    env.mp.setattr(add, "last_start_hms", lambda cut: "23:59:00")
    state = {"t": dt.datetime(2026, 11, 2, 7, 59, 0)}
    qw.set_clock(lambda: state["t"])

    def hook(f, method, path):
        if path == C.ALERTS:
            state["t"] = dt.datetime(2026, 11, 2, 8, 0, 1)                            # 最後一批請求回來時已過 08:00
    env.fake(on_request=hook)
    out = add.run_snapshot(env.conn, "schedule", None)
    assert out["status"] == "failed" and out["error_class"] == "budget" and _batch(env.conn) is None


def test_硬截止_寫批次途中超過截止_不提交_批次不落地(env):
    env.mp.setattr(add, "last_start_hms", lambda cut: "23:59:00")
    state = {"t": dt.datetime(2026, 11, 2, 7, 59, 0)}
    qw.set_clock(lambda: state["t"])
    env.fake()
    real = ad.apply

    def slow_apply(*a, **k):
        r = real(*a, **k)
        state["t"] = dt.datetime(2026, 11, 2, 8, 0, 2)                                # 寫入（SQLite 鎖等待）花到過了 08:00
        return r
    env.mp.setattr(ad, "apply", slow_apply)
    out = add.run_snapshot(env.conn, "schedule", None)
    assert out["status"] == "failed" and out["error_class"] == "budget"
    c2 = db.get_connection(env.db_path)
    try:
        assert c2.execute("SELECT COUNT(*) FROM aria_dc_batch").fetchone()[0] == 0     # 第二條連線看不到：已 rollback
    finally:
        c2.close()


def test_排程worker開始前重新核對最晚啟動時間_主執行緒通過後才過0740就拒絕(env):
    f = env.fake()
    _turn_on(env.conn)
    qw.set_clock(lambda: dt.datetime(2026, 11, 2, 7, 40, 0))
    real = qw.capture

    def slow_capture(*a, **k):
        qw.set_clock(lambda: dt.datetime(2026, 11, 2, 7, 40, 1))                       # 主執行緒通過 _due 之後、worker 開跑之前，時間走過 07:40:00
        return real(*a, **k)
    env.mp.setattr(qw, "capture", slow_capture)
    out = add.tick(env.conn)
    assert out["status"] == "failed" and out["error_class"] == "past_last_start" and f.calls == []
    assert add.public_state(env.conn, dt.datetime(2026, 11, 2, 7, 41))["gave_up_today"]["reason"] == "past_last_start"


def test_worker只用自己的連線_不碰主執行緒的連線_連線失敗要寫紀錄與health(env):
    import threading as th
    env.fake()
    env.mp.setattr(add, "RUN_IN_BACKGROUND", True)
    seen = []
    real_path = qw._path_of
    env.mp.setattr(qw, "_path_of", lambda c: (seen.append(th.current_thread().name), real_path(c))[1])
    assert add.start_manual(env.conn, "admin")["status"] == "started"
    for _ in range(300):
        if not add._state["running"]:
            break
        th.Event().wait(0.1)
    assert set(seen) <= {th.current_thread().name}                                      # 路徑只在主執行緒解析，worker 沒碰主連線
    # worker 連線第一次就失敗：API 已回 started，也要留 failed 紀錄與 health
    env.conn.execute("DELETE FROM aria_dc_direct_runs")
    env.conn.commit()
    qw._HEALTH.pop("aria_dc_direct_thread", None)
    n = {"c": 0}
    real_connect = add._connect

    def flaky(path):
        n["c"] += 1
        if n["c"] == 1:
            raise sqlite3_error()
        return real_connect(path)
    import sqlite3

    def sqlite3_error():
        return sqlite3.OperationalError("unable to open database file")
    env.mp.setattr(add, "_connect", flaky)
    assert add.start_manual(env.conn, "admin")["status"] == "started"
    for _ in range(300):
        if not add._state["running"]:
            break
        th.Event().wait(0.1)
    c2 = db.get_connection(env.db_path)
    try:
        r = c2.execute("SELECT status, error_class FROM aria_dc_direct_runs").fetchone()
    finally:
        c2.close()
    assert (r["status"], r["error_class"]) == ("failed", "worker_start") and "aria_dc_direct_thread" in qw._HEALTH


def test_開關與稽核同一個序列化交易_兩連線交錯不漏記(env):
    db.set_setting(env.conn, add.K_ENABLED, "0")
    other = db.get_connection(env.db_path)
    try:
        env.mp.setattr(add, "enabled", lambda c: False)                                  # A 先前讀到的是「關」（過期的讀值）
        add.save_enabled(other, True, "B")                                               # B 先開並提交
        add.save_enabled(env.conn, False, "A")                                           # A 之後才關：必須在寫鎖內重讀舊值（開），不是用先前讀到的「關」
    finally:
        other.close()
    rows = [(r["by"], r["before"], r["after"]) for r in add.audit_list(env.conn)]
    assert rows == [("A", "開", "關"), ("B", "關", "開")]
    assert get_setting_raw(env.conn) == "0" and env.conn.in_transaction is False


def get_setting_raw(conn):
    return db.get_setting(conn, add.K_ENABLED, "0")


def test_vm_window拒絕太短的窗口_不跨日回繞():
    import daily_check as dc
    assert dc.vm_window({"vm_batch_from": "00:00", "vm_batch_cutoff": "00:10"}) == ("06:00", "08:00", True)
    assert dc.vm_window({"vm_batch_from": "07:50", "vm_batch_cutoff": "08:00"}) == ("06:00", "08:00", True)
    assert dc.vm_window({"vm_batch_from": "07:00", "vm_batch_cutoff": "07:21"}) == ("07:00", "07:21", False)
    assert add.last_start_hms("00:21") == "00:01:00" and add.last_start_hms("00:10") == "00:00:00"


def test_短窗口設定_直抓用預設窗_半夜不會一直建立過期的不完整紀錄(env):
    import daily_check as dc
    f = env.fake()
    _turn_on(env.conn)
    dc.save_settings(env.conn, {"vm_batch_from": "00:00", "vm_batch_cutoff": "00:10"}, by="t")
    assert add._window(env.conn) == ("06:00", "08:00", True)
    qw.set_clock(lambda: dt.datetime(2026, 11, 2, 0, 20))
    assert add.tick(env.conn)["status"] == "out_of_window" and f.calls == []
    add.ensure(env.conn)
    assert env.conn.execute("SELECT COUNT(*) FROM aria_dc_direct_runs WHERE status != 'gave_up'").fetchone()[0] == 0
