"""AUD-02 入口完整性守門：日後有人新增背景執行緒／排程 tick／啟動工作而沒登記到 quiet_window.ENTRIES，這裡就紅。

為什麼要有：盤中靜默窗要靠「每個自動入口自己呼叫守門」才成立，最容易出的事是日後新增一個排程忘了守。
掃描方式是 AST（不是文字搜尋）：
  · 所有 threading.Thread／Timer 的建立點（檔案:函式）
  · 所有叫 tick／*_tick 的函式
  · scan_service._scheduler_loop 裡呼叫的每一個內部函式
  · api.py 的 @app.on_event 啟動事件
每一個都必須登記在 quiet_window.THREAD_SITES／TICK_SITES／SCHEDULER_CALLS／STARTUP_SITES，並對應到 ENTRIES 的一個分類
（A 需守門、B 純本機、C 對外通知、H 使用者自己排入的傳送、M 人工入口的執行緒）。
新增入口時的做法：在 quiet_window.py 登記＋寫進 docs/codex-review/2026-10-30/115 清單；A 類另加守門與 test_quiet_window.py 的 driver。
"""
import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import quiet_window as qw  # noqa: E402


def _files():
    return sorted([*BACKEND.glob("*.py"), *BACKEND.glob("routers/*.py")])


def _rel(p: Path) -> str:
    r = p.relative_to(BACKEND).as_posix()
    return r.replace("routers/", "routers/")


class _Scan(ast.NodeVisitor):
    def __init__(self, rel):
        self.rel, self.stack = rel, []
        self.threads, self.ticks, self.startups = set(), set(), set()

    def visit_FunctionDef(self, n):
        if n.name == "tick" or n.name.endswith("_tick"):
            self.ticks.add(f"{self.rel}:{n.name}")
        for d in n.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "on_event":
                self.startups.add(f"{self.rel}:{n.name}")
        self.stack.append(n.name)
        self.generic_visit(n)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, n):
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        if name in ("Thread", "Timer"):
            self.threads.add(f"{self.rel}:{self.stack[-1] if self.stack else '<module>'}")
        self.generic_visit(n)


def _scan():
    threads, ticks, startups = set(), set(), set()
    for p in _files():
        sc = _Scan(_rel(p))
        sc.visit(ast.parse(p.read_text(encoding="utf-8")))
        threads |= sc.threads
        ticks |= sc.ticks
        startups |= sc.startups
    return threads, ticks, startups


def _hint(kind, missing):
    return (f"\n發現沒登記的{kind}：{sorted(missing)}\n"
            f"請到 backend/quiet_window.py 登記（ENTRIES 加分類＋對應表）。分類：A＝會對線上主機／平台連線→必須在最前面呼叫 "
            f"defer_if_quiet 並在 test_quiet_window.py 補 driver；B＝純本機；C＝對外通知；H／M 見檔頭。判不出就當 A。"
            f"也要更新 docs/codex-review/2026-10-30/115。")


def test_所有背景執行緒建立點都已登記():
    threads, _, _ = _scan()
    assert not (threads - set(qw.THREAD_SITES)), _hint("執行緒建立點", threads - set(qw.THREAD_SITES))
    assert not (set(qw.THREAD_SITES) - threads), f"登記了但程式裡已經沒有的執行緒建立點（請從 THREAD_SITES 移除）：{sorted(set(qw.THREAD_SITES) - threads)}"


def test_所有_tick_函式都已登記():
    _, ticks, _ = _scan()
    assert not (ticks - set(qw.TICK_SITES)), _hint("tick 函式", ticks - set(qw.TICK_SITES))
    assert not (set(qw.TICK_SITES) - ticks), f"登記了但已不存在的 tick：{sorted(set(qw.TICK_SITES) - ticks)}"


def test_啟動事件都已登記():
    _, _, startups = _scan()
    assert startups == set(qw.STARTUP_SITES), _hint("啟動事件", startups ^ set(qw.STARTUP_SITES))


def test_排程迴圈呼叫的每個函式都已登記():
    src = (BACKEND / "scan_service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    module_funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    loop = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_scheduler_once")
    called = {c.func.id for c in ast.walk(loop) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    called = {c for c in called if c in module_funcs and c not in ("is_running", "get_connection")}
    assert called == set(qw.SCHEDULER_CALLS), _hint("排程迴圈呼叫", called ^ set(qw.SCHEDULER_CALLS))


def test_登記表內的代號都存在_且分類合法():
    for table in (qw.THREAD_SITES, qw.TICK_SITES, qw.SCHEDULER_CALLS, qw.STARTUP_SITES):
        for site, entry in table.items():
            assert entry in qw.ENTRIES, f"{site} → {entry} 不在 ENTRIES"
    for k, v in qw.ENTRIES.items():
        assert v["cls"] in ("A", "B", "C", "H", "M"), k
        if v["cls"] in ("B", "C", "H"):
            assert v["why"], f"{k}：非 A 類必須寫明為什麼不受靜默窗限制"
        if v["cls"] == "A":
            assert v["manual"], f"{k}：A 類要寫明對應的人工路徑（或說明沒有）"


def _func_source(where: str) -> str:
    f, fn = where.split(":")
    src = (BACKEND / f).read_text(encoding="utf-8")
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == fn:
            return ast.get_source_segment(src, n) or ""
    raise AssertionError(f"{where} 找不到函式")


@pytest.mark.parametrize("entry", sorted(k for k, v in qw.ENTRIES.items() if v["cls"] == "A" and not v["where"].startswith("routers/")))
def test_A類入口的守門所在函式_確實呼叫守門(entry):
    body = _func_source(qw.ENTRIES[entry]["where"])
    assert re.search(r'(defer_if_quiet|checkpoint|begin|guarded)\(\s*(?:\w+\s*,\s*)?"%s"' % re.escape(entry), body), \
        f"{entry} 的守門函式 {qw.ENTRIES[entry]['where']} 裡找不到 defer_if_quiet／checkpoint／begin(conn, \"{entry}\"…)"


def test_guarded裝飾器的使用清單():
    """長任務入口用 @guarded 吃票證、拒絕窗內無授權呼叫：清單釘死，拿掉任一個就紅。"""
    found = {}
    for p in _files():
        src = p.read_text(encoding="utf-8")
        for m in re.finditer(r'guarded\("(\w+)"\)\s*\n\s*def (\w+)', src):
            found[m.group(2)] = m.group(1)
    expect = {"collect_services": "scan_post_services", "collect_accounts": "scan_post_accounts", "collect_software": "scan_post_software",
              "scheduled_cycle": "scan_post_auto_onboard", "run_import": "vrops_import_tick", "poll": "whatsup_poll_tick",
              "run_weekly_baseline": "health_baseline_weekly", "run_dispatch": "dispatch_weekly_tick", "check": "paste_watch_tick"}
    assert found == expect, found


def test_A類入口的人工路徑都有二次確認():
    routers_src = "\n".join(p.read_text(encoding="utf-8") for p in (BACKEND / "routers").glob("*.py"))
    # 每個有 HTTP 人工路徑的 A 類入口，路由裡都要有 confirm_from_request(conn, "<入口>", …)
    for entry, meta in qw.ENTRIES.items():
        if meta["cls"] == "A" and meta["manual"].startswith("POST "):
            assert re.search(r'confirm_from_request\(\s*conn\s*,\s*"%s"' % re.escape(entry), routers_src), \
                f"{entry} 的人工路徑 {meta['manual']} 沒有窗內二次確認"


def test_115清單文件涵蓋每一個登記的入口():
    if not (ROOT / "docs").exists():
        pytest.skip("relay 產出物沒有 docs/")
    docs = list((ROOT / "docs" / "codex-review" / "2026-10-30").glob("115_全站*.md"))
    assert docs, "找不到 docs/codex-review/2026-10-30/115_全站*.md"
    text = docs[0].read_text(encoding="utf-8")
    missing = [k for k in qw.ENTRIES if f"`{k}`" not in text]
    assert not missing, f"115 清單沒有列到這些入口（請補）：{missing}"


# ---------------------------------------------------------------- 審查必修 4：每個會外連的 router 端點都要有確認守門或明確豁免
_CONNECT = re.compile(
    r"host_ping\.|ssh_probe\.|health_probe\.|winrm_collector\.|cmdb_gateway\.|onboard_engine\.\w*(run|exec|bootstrap|probe|onboard|revoke|connect|collect|refresh|rotate|deploy|ssh)"
    r"|post_onboard\.(start|enqueue|run_)|batch_onboard_service\.start|dir_housekeeping\.queue_scan|collect_dispatch\.(run_dispatch|start_background)"
    r"|start_scan\(|socket\.create_connection|(account|service|software|schedule|iis)_inventory\.collect|\bvi\.(run_import|check_connection)"
    r"|\bwu\.(poll|check_connection)|\bsu\.check_connection|\bsc\.collect_device|san_collector|hmc_collector|net_collector|facts_collector|manage_state\.(collect|probe)|auto_onboard\.(scheduled|run)"
    r"|win_onboard\.\w*(onboard|probe|revoke|grant|collect)|os_rebuild_collector|win_rebuild_collector|host_spec_collector|fast_probe|net_scan\.|fingerprint\."
    r"|onboard_hostfix\.|root_key\.|collect_probe\.|svc\.(create|start|retry)")

#: 看起來會碰到上面關鍵字、但實際不對外連線（只讀庫、產生文字、本機解析、設定讀寫）的端點，附理由。新增端點被掃到又不在這裡＝紅。
EXEMPT = {
    "get_collect_config": "讀設定", "set_collect_config": "寫設定（驗證格式，不連線）",
    "san_switches_endpoint": "讀庫", "san_offline_guide_endpoint": "產生說明文字", "san_import_endpoint": "解析人工貼上的輸出（不連線）",
    "san_switch_archive_endpoint": "讀庫", "san_archive_text_endpoint": "讀庫", "host_spec_reparse_endpoint": "重新解析已存的輸出（不連線）",
    "san_reparse_endpoint": "重新解析已存的輸出（不連線）", "collect_agent_package": "產生文字／設定", "drift_recheck": "B 類：只比對資料庫",
    "agent_stage": "本機佈檔（不連線）", "onboard_script": "產生腳本文字", "onboard_paste_issue": "產生腳本文字與等待紀錄（不連線）",
    "onboard_post_collect_status": "讀庫狀態", "host_rebuild_sudo_whitelist_endpoint": "產生說明文字",
    "alive_check_runs": "讀歷史", "alive_check_run_detail": "讀歷史", "alive_check_run_export": "讀歷史", "hmc_consoles_endpoint": "讀設定",
}


def test_每個會外連的router端點_必須有確認守門或明確豁免():
    bad = []
    seen_gated = 0
    for p in sorted((BACKEND / "routers").glob("*.py")):
        src = p.read_text(encoding="utf-8")
        for n in ast.parse(src).body:
            if not isinstance(n, ast.FunctionDef):
                continue
            deco = [d for d in n.decorator_list if isinstance(d, ast.Call) and getattr(d.func, "attr", "") in ("get", "post", "put", "delete", "patch")]
            if not deco:
                continue
            seg = ast.get_source_segment(src, n) or ""
            deco_src = "".join(ast.get_source_segment(src, d) or "" for d in deco)
            if not _CONNECT.search(seg):
                continue
            gated = "confirm_from_request" in seg or "quiet_deps" in deco_src
            if gated:
                seen_gated += 1
                assert n.name not in EXEMPT, f"{n.name} 已有守門，請從 EXEMPT 移除"
            elif n.name not in EXEMPT:
                bad.append(f"{p.name}:{n.name}")
    assert not bad, f"這些端點可能對外連線，卻沒有 confirm 守門也不在 EXEMPT（請加 dependencies=[Depends(quiet_deps.gate(...))] 或登記豁免理由）：{bad}"
    assert seen_gated >= 30


# ---------------------------------------------------------------- 審查補充 I：__main__ 命令列入口與外部排程（cron／systemd timer）也要登記
def test_所有__main__命令列入口都已登記():
    mains = set()
    for p in sorted(BACKEND.glob("*.py")):
        for n in ast.parse(p.read_text(encoding="utf-8")).body:
            if (isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name)
                    and n.test.left.id == "__name__"):
                mains.add(p.name)
    assert mains == set(qw.MAIN_SITES), _hint("__main__ 命令列入口", mains ^ set(qw.MAIN_SITES))
    for f, entry in qw.MAIN_SITES.items():
        assert entry in qw.ENTRIES
        if qw.ENTRIES[entry]["cls"] == "A" and entry != "vcenter_autoimport_tick":
            assert "cli_gate" in (BACKEND / f).read_text(encoding="utf-8"), f"{f} 的 __main__ 是 A 類入口，必須呼叫 quiet_window.cli_gate"


def test_外部排程_cron與systemd_timer都已登記():
    app = BACKEND.parent
    for fname, pattern, count, entry in qw.EXTERNAL_SCHEDULES:
        text = (app / fname).read_text(encoding="utf-8")
        assert len(re.findall(pattern, text)) == count, f"{fname}：{pattern} 的數量變了（新增排程請登記到 quiet_window.EXTERNAL_SCHEDULES 與 115）"
        assert entry in qw.ENTRIES
    # 所有 shell 檔案裡的 /etc/cron.d 檔名、OnCalendar 數量，沒有登記的新來源就紅
    seen_cron = set()
    seen_timer = 0
    for sh in sorted(app.glob("*.sh")):
        t = sh.read_text(encoding="utf-8")
        seen_cron |= set(re.findall(r"/etc/cron\.d/([\w\-]+)", t))
        seen_timer += len(re.findall(r"OnCalendar=", t))
    assert seen_cron == {"webit3-scan"}, f"出現未登記的 cron 檔：{seen_cron}"
    assert seen_timer == 2, "出現未登記的 systemd timer"


# ---------------------------------------------------------------- 第三輪 13：最底層外連函式的直接呼叫守門
#: 最底層（沒有票證、不知道誰是人為）的外連函式／模組。自動路徑模組不得直接引用，必須經『任務層』（@guarded／checkpoint）。
LOWLEVEL = {"_ssh_exec", "fast_probe", "net_scan", "winrm_collector", "ssh_mux", "host_ping"}
#: 自動路徑（背景排程啟動）模組：只能經任務層碰到目標機。新增自動模組時加進來。
AUTO_PATH_MODULES = ["scan_service.py", "dispatch_weekly.py", "vrops_import.py", "whatsup_service.py", "health_baseline.py",
                     "paste_watch.py", "auto_onboard.py"]


def test_自動路徑模組_不得直接引用最底層外連函式():
    bad = []
    for f in AUTO_PATH_MODULES:
        tree = ast.parse((BACKEND / f).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            names = []
            if isinstance(n, ast.Import):
                names = [a.name.split(".")[0] for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                names = [(n.module or "").split(".")[0], *[a.name for a in n.names]]
            elif isinstance(n, ast.Attribute):
                names = [n.attr]
            elif isinstance(n, ast.Name):
                names = [n.id]
            for nm in names:
                if nm in LOWLEVEL:
                    bad.append(f"{f}:{getattr(n, 'lineno', '?')} 引用 {nm}")
    # auto_onboard 的 onboard_engine 是任務層（scheduled_cycle 有 @guarded＋逐台 checkpoint），其餘模組不得碰最底層
    assert not bad, "自動路徑模組直接引用了最底層外連函式（請改經 @guarded 的任務層）：" + "；".join(bad)


def test_只靠路由dependency的人工路徑_都在manual_diag清單():
    """人為診斷／測試類端點沒有 Run／票證，只靠路由 dependency 做窗內二次確認；115 的殘餘風險段列出它們。這裡釘住數量不縮水。"""
    n = 0
    for p in sorted((BACKEND / "routers").glob("*.py")):
        n += len(re.findall(r'quiet_deps"\)\.gate\("manual_diag"\)', p.read_text(encoding="utf-8")))
    assert n >= 26, n
