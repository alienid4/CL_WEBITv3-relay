"""1.5 每日檢查三層分離（2.066.x，使用者 11-14 核可示意 AI/樣板_1.5每日檢查_示意_2026-11-14.html）。

① 1.5 頁籤＝今天／資料來源／紀錄，「設定」頁籤拿掉（舊網址 ?tab=settings 轉到 ⚙ 系統設定 › 每日檢查）；
   今天頁籤最上面「要你處理」（沒事也要寫「沒有要你處理的事」），主表有搜尋；不顯示「試辦第 N/5 個工作天」。
② 設定搬到 ⚙ 系統設定 › 每日檢查，四個子頁籤，同一支 PUT /api/daily-check/settings、同一組欄位。
③ 資料來源狀態固定六種（純函式 utils/dailycheckSources.ts，用 node 直接載入測）。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
PAGE = (FE / "pages" / "v2" / "dailycheck.vue").read_text(encoding="utf-8")
SETTINGS = (FE / "pages" / "v2" / "settings.vue").read_text(encoding="utf-8")
COMP = (FE / "components" / "v2" / "DailyCheckSettings.vue").read_text(encoding="utf-8")
SIX = ["正常", "過期", "失敗", "只收到一半", "沒收到", "待設定"]


def _template(src: str) -> str:
    return src[src.index("<template>"):src.rindex("</template>")]


# ---------------------------------------------------------------- 1.5 頁面結構
def test_頁籤是今天_資料來源_紀錄_沒有設定():
    m = re.search(r"const TABS = \[(.*?)\]\n", PAGE)
    assert m, "找不到 TABS"
    keys = re.findall(r"k: '(\w+)'", m.group(1))
    assert keys == ["today", "sources", "history"], keys
    assert "label: '今天'" in m.group(1) and "label: '資料來源'" in m.group(1)
    assert "tab === 'settings'" not in PAGE


def test_舊網址_tab_settings_轉到系統設定():
    assert re.search(r"q\('tab'\) === 'settings'\) await navigateTo\(\{ path: '/v2/settings', query: \{ st: 'dailycheck'", PAGE)


def test_不顯示試辦標記():
    t = _template(PAGE)
    assert "試辦" not in t and "trial_day" not in t


def test_今天頁籤有要你處理_沒事也要寫():
    t = _template(PAGE)
    assert "要你處理（{{ todos.length }} 件）" in t
    assert "沒有要你處理的事" in t
    # 每件都有一顆按鈕
    assert re.search(r'v-for="\(t, i\) in todos".*?<NuxtLink v-if="t.to" class="btn".*?<button v-else', t, re.S)
    # 要你處理至少有：核准群組、VMware 去設定、資料來源看原因
    for s in ("label: '去核准'", "label: '去設定', to: setLink('vm')", "label: '看原因'"):
        assert s in PAGE, s


def test_舊的紅黃段落併進要你處理():
    t = _template(PAGE)
    assert 'data-testid="dc-lp-alert"' not in t and 'data-testid="dc-vm-newer"' not in t
    assert "d.value?.vm_newer" in PAGE and "run.value?.superseded" in PAGE and "warnings_now" in PAGE


def test_主表有搜尋_卡片改叫要人工看():
    assert ':search="true"' in PAGE
    assert "label: '要人工看'" in PAGE
    assert "系統判定" in PAGE


def test_列明細_依據有改門檻_資料來源連結_批次收在更多():
    t = _template(PAGE)
    i = t.index('data-testid="dc-detail"')
    j = t.index("<!-- 資料來源 -->")
    detail = t[i:j]
    assert "判斷依據" in detail and 'data-testid="dc-edit-threshold"' in detail and "isAdmin && detailRow.rule" in detail
    assert 'data-testid="dc-row-source"' in detail
    more = detail[detail.index("<details"):detail.index("</details>")]
    assert "來源批次" in more and "收集器" in more
    assert "來源批次" not in detail[:detail.index("<details")]


def test_設定欄位不在1_5頁():
    assert "PUT" not in PAGE and "saveCfg" not in PAGE and "/api/daily-check/settings" not in PAGE
    assert "⚙ 設定" in PAGE and "/v2/settings?st=dailycheck" in PAGE


def test_核准成員流程搬到資料來源():
    t = _template(PAGE)
    src = t[t.index("<!-- 資料來源 -->"):t.index("<!-- 紀錄 -->")]
    assert 'data-testid="dc-lastpoll"' in src and 'data-testid="dc-member-diff"' in src and "核准目前成員" in src
    assert "isAdmin && row.ok" in src
    # 批次編號、收集器、設定改動紀錄收在「更多」
    for k in ("batch_id", "收集器 ID", "VMware 設定改動時間", "核准時間"):
        i = src.index(k)
        assert src.rfind("<details", 0, i) > src.rfind("</details>", 0, i), k


# ---------------------------------------------------------------- 系統設定 › 每日檢查
def test_系統設定有每日檢查頁籤與四個子頁籤():
    assert "{ k: 'dailycheck', label: '每日檢查' }" in SETTINGS
    m = re.search(r"dailycheck: \[(.*?)\],", SETTINGS)
    assert m and re.findall(r"k: '(\w+)'", m.group(1)) == ["thr", "wug", "vm", "list"]
    assert '<V2DailyCheckSettings v-else-if="tab === \'dailycheck\'"' in SETTINGS
    admin_only = re.search(r"const ADMIN_ONLY = new Set\(\[(.*?)\]\)", SETTINGS).group(1)
    assert "'dailycheck'" not in admin_only      # 非管理員照舊看得到精簡、唯讀的那幾欄
    assert "q('tab') === 'dailycheck'" in SETTINGS


def test_設定元件同一支API_同一組欄位():
    assert "'/api/daily-check/settings', { method: 'PUT', body: { patch } }" in COMP
    for k in ("enabled", "signoff", "snapshot_time", "remind_time", "escalate_time", "notify_stop_time", "cpu", "mem", "disk", "swap",
              "poll_min", "alert_fresh_min", "perf_fresh_h", "storage_fresh_h", "xserver_ips", "banqiao", "storage_devices", "vcenters",
              "holidays", "escalate_to", "disk_exceptions", "daily_check_perf_source", "lp_fresh_min", "disk_list", "disk_alert",
              "wug_member_jump_pct", "dc_group_cpu_mem", "dc_group_swap", "dc_group_disk", "dc_group_poll", "vm_lun_threshold", "vm_lun_unit",
              "vm_lun_op", "vm_lun_used_pct_max", "vm_alert_levels", "vm_fresh_min", "vm_adapter_stale_min", "vm_record_stale_min",
              "vm_local_patterns", "vm_mute_max_days", "vm_vcenters", "vm_alert_mutes"):
        assert k in COMP, k
    assert "cfg.value?.limited" in COMP and ':disabled="!isAdmin"' in COMP
    assert "‹ 回 1.5 每日檢查" in COMP and "/v2/dailycheck?tab=today&row=" in COMP
    assert "加入清單" in COMP and "useVcid" in COMP


def test_設定元件沒有狀態表_沒有開發筆記():
    t = _template(COMP)
    assert "最新一批 <b" not in t and "核准目前成員" not in t and "last_collected" not in t and "batch_id" not in t
    for bad in ("使用者 11-", "正本", "第二期", "2.06", "跳板機 每天"):
        assert bad not in t, bad


# ---------------------------------------------------------------- 資料來源狀態（純函式）
_HOOK = ("data:text/javascript,export async function resolve(s,c,n){try{return await n(s,c)}catch(e){"
         "if((s.startsWith('.')||s.startsWith('file:'))&&!/\\.[cm]?[jt]s$/.test(s))return n(s+'.ts',c);throw e}}")


def _node(expr: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("沒有 node")
    v = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v").split(".")
    if (int(v[0]), int(v[1])) < (22, 6):
        pytest.skip(f"node {'.'.join(v)} 不能直接載入 .ts")
    src = (FE / "utils" / "dailycheckSources.ts").as_uri()
    code = (f"import {{ register }} from 'node:module'; register({json.dumps(_HOOK)});"
            f"const m = await import('{src}'); console.log(JSON.stringify({expr}))")
    r = subprocess.run([node, "--no-warnings", "--input-type=module", "-e", code], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_來源對應():
    got = _node("['WhatsUp 告警快照','WhatsUp 上次輪循','vROps（VMware）','Unity 中繼（unity_collect）','AD 對時中繼','WhatsUp 效能中繼',"
                "'WhatsUp 磁碟中繼／納管健康檢查', null, ''].map(m.srcKeyOf)")
    assert got == ["alert", "lastpoll", "vmware", "storage", "adtime", "perf", "perf", "", ""]


def test_逐列推狀態_六種():
    rows = {
        "ok": [{"code": "W-a", "verdict": "ok", "data_at": "2026-11-14 06:20:00"}],
        "half": [{"code": "S-1", "verdict": "ok", "data_at": "2026-11-14 07:00:00"}, {"code": "S-2", "verdict": "nodata", "reason": "對不到中繼資料"}],
        "stale": [{"code": "S-1", "verdict": "nodata", "reason": "Unity 資料太舊（最後 11-12）", "data_at": "2026-11-12 07:00:00"}],
        "fail": [{"code": "A-1", "verdict": "nodata", "reason": "收集失敗：逾時", "data_at": "2026-11-14 06:00:00"}],
        "none": [{"code": "A-1", "verdict": "nodata", "reason": "AD 對時資料來源還沒上線（ad_time）"}],
        "unset": [{"code": "W-c", "verdict": "nodata", "reason": "1-xServer 範圍（IP 清單）未設定"}],
        "manual": [{"code": "O-1", "verdict": "manual"}],
    }
    got = _node(f"Object.fromEntries(Object.entries({json.dumps(rows)}).map(([k, v]) => [k, m.rowsStatus(v)]))")
    assert got["ok"]["status"] == "正常" and got["ok"]["at"] == "2026-11-14 06:20:00"
    assert got["half"]["status"] == "只收到一半" and "S-2" in got["half"]["next"] and "缺" in got["half"]["next"]
    assert got["stale"]["status"] == "過期"
    assert got["fail"]["status"] == "失敗"
    assert got["none"]["status"] == "沒收到"
    assert got["unset"]["status"] == "待設定"
    assert got["manual"] is None      # 人工列不是資料來源


def test_上次輪循狀態():
    base = {"latest": {"collected_at": "2026-11-14 10:00:00", "complete": True, "problems": [], "age_min": 5},
            "latest_complete": {"collected_at": "2026-11-14 10:00:00", "age_min": 5}, "fresh_min": 35,
            "groups": [{"path": "A", "ok": True, "scope_ok": True, "partial": []}]}
    cases = {
        "none": ({"latest": None}, "10:00"),
        "ok": (base, "10:00"),
        "unscoped": ({**base, "groups": [{"path": "A", "ok": True, "scope_ok": False, "partial": []}]}, "10:00"),
        "failed": ({**base, "groups": [{"path": "A", "ok": False, "error": "403"}]}, "10:00"),
        "stale": ({**base, "latest_complete": {"collected_at": "2026-11-14 08:00:00", "age_min": 120}}, "10:00"),
        "night": ({**base, "latest_complete": {"collected_at": "2026-11-13 17:30:00", "age_min": 300}}, "22:30"),
        "half": ({**base, "latest": {**base["latest"], "complete": False, "problems": ["CPU 分頁沒讀完"]}}, "10:00"),
        "part": ({**base, "groups": [{"path": "A", "ok": True, "scope_ok": True, "partial": ["disk"]}]}, "10:00"),
    }
    got = _node("Object.fromEntries(Object.entries(" + json.dumps(cases) + ").map(([k, [s, hm]]) => [k, m.lastpollStatus(s, 35, hm, null)]))")
    assert got["none"]["status"] == "沒收到"
    assert got["ok"]["status"] == "正常"
    assert got["unscoped"]["status"] == "待設定" and got["unscoped"]["act"] == "approve" and "核准" in got["unscoped"]["next"]
    assert got["failed"]["status"] == "失敗" and "403" in got["failed"]["next"]
    assert got["stale"]["status"] == "過期"
    assert got["night"]["status"] == "正常"            # 收集時段（05:30～18:00）外不算過期
    assert got["half"]["status"] == "只收到一半" and "CPU 分頁沒讀完" in got["half"]["next"]
    assert got["part"]["status"] == "只收到一半" and "硬碟" in got["part"]["next"]   # 11-15 原因改白話（disk→硬碟）
    alert = _node("m.lastpollStatus(" + json.dumps(base) + ", 35, '10:00', '上次輪循 60 分鐘沒有新的完整批次')")
    assert alert["status"] == "過期" and "60 分鐘" in alert["next"]


def test_VMware狀態():
    ok = {"latest": {"collected_at": "2026-11-14 06:20:00", "complete": True, "problems": []}, "latest_complete": {"collected_at": "2026-11-14 06:20:00", "age_min": 60}}
    got = _node("[m.vmwareStatus(null, true), m.vmwareStatus({latest: null}, true), m.vmwareStatus(" + json.dumps(ok) + ", false), m.vmwareStatus(" + json.dumps(ok) + ", true),"
                "m.vmwareStatus(" + json.dumps({**ok, "latest_complete": None}) + ", true), m.vmwareStatus(" + json.dumps({**ok, "latest_complete": {"collected_at": "x", "age_min": 3000}}) + ", true)]")
    assert got[0] is None
    assert got[1]["status"] == "沒收到"
    assert got[2]["status"] == "待設定" and got[2]["act"] == "setvm" and "vCenter" in got[2]["next"]
    assert got[3]["status"] == "正常"
    assert got[4]["status"] == "只收到一半"
    assert got[5]["status"] == "過期"
    # 11-14 獨立審查 P2：早上兩輪都失敗，昨天 07:30 那批還不到 26 小時——過了 07:45 沒有今天的完整批次也要算過期
    yday = {"latest": {"collected_at": "2026-11-13 07:30:00", "complete": True, "problems": []}, "latest_complete": {"collected_at": "2026-11-13 07:30:00", "age_min": 1410}}
    late = _node("[m.vmwareStatus(" + json.dumps(yday) + ", true, '2026-11-14', '07:50'), m.vmwareStatus(" + json.dumps(yday) + ", true, '2026-11-14', '05:00'),"
                 "m.vmwareStatus(" + json.dumps(ok) + ", true, '2026-11-14', '09:00')]")
    assert late[0]["status"] == "過期" and "07:45" in late[0]["next"]
    assert late[1]["status"] == "正常"      # 今天的批次還沒到收集時間：昨天的算數
    assert late[2]["status"] == "正常"      # 今天已有完整批次


def test_全部來源_狀態只有六種_項次固定():
    inp = {"rows": [{"code": "W-a", "verdict": "ok", "source": "WhatsUp 告警快照", "data_at": "2026-11-14 06:20:00"},
                    {"code": "S-1", "verdict": "nodata", "source": "Unity 中繼（unity_collect）", "reason": "資料太舊"},
                    {"code": "O-1", "verdict": "manual", "source": None}],
           "lpst": {"latest": None}, "vmst": {"latest": None}, "perfSource": "lastpoll", "vmConfigured": False, "nowHM": "10:00"}
    got = _node(f"m.buildSources({json.dumps(inp)})")
    assert [(s["key"], s["__no"], s["status"]) for s in got] == [("alert", 1, "正常"), ("lastpoll", 2, "沒收到"), ("vmware", 4, "沒收到"), ("storage", 5, "過期")]
    assert all(s["status"] in SIX for s in got)
    assert _node("m.SRC_STATUSES") == SIX
    w3 = _node(f"m.buildSources({json.dumps({**inp, 'perfSource': 'w3'})}).map(s => s.key)")
    assert "lastpoll" not in w3
