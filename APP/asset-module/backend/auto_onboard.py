"""B：排程自動納管。把授權網段內「已登記但未納管」的主機，無需人在場帶到已納管。

與 A（UI 一鍵）共用 onboard_engine 這顆引擎，差別只在「誰觸發、憑證哪來」（引擎 docstring 定案）：
    A：人當下輸入帳密、用完即丟
    B：憑證從加密憑證庫取（credential_store），只碰操作者「明確授權」的網段

## 兩道寫死的安全設計

1. **網段授權閘門**：納管腳本會在目標機建帳號、改 sshd_config——是有副作用的動作。
   讓它「無人在場自動對主機跑」風險很高（打錯機器、碰到不該碰的網段都是事故）。
   所以 B 只碰 `auto_onboard_segment` 裡「明確加入且啟用」的網段前綴，其餘一律不碰。
   整體另有一個總開關 `auto_onboard_enabled`（app_settings，預設 0）——排程要跑得先打開。

2. **只碰「已登記未納管」，不自動把未登記主機建成資產**：未登記＝掃到但還不是資產，
   要不要收它是人的決定（自動把來路不明的 IP 建成資產，正是「假資料混進真實盤點」
   要防的事）。B 只處理已登記卻連不進去的那批——那正是 bootstrap 能修好的集合。

## 憑證不落地仍然成立

B 用的憑證來自加密憑證庫（使用者明確授權、要給排程反覆使用），取出的明文只在記憶體、
用完即丟，稽核只記帳號名/成敗，永不含密碼——跟 A 的三條底線一致（見 onboard_engine）。

## Windows 不走 bootstrap

Windows 收集走 WinRM/CIM，完全不動目標機、不需在目標機建帳號佈金鑰（使用者定案）。
所以 Windows 主機的「自動納管」＝排程用 WinRM 憑證直接收集（由 collect_facts_into_assets
負責），這裡不對它跑 bootstrap，只在明細裡註明。
"""
from __future__ import annotations

import os

from db import get_connection

# ---- 總開關（app_settings）----
ENABLED_KEY = "auto_onboard_enabled"


def is_enabled(conn) -> bool:
    from db import get_setting

    return get_setting(conn, ENABLED_KEY, "0") == "1"


def set_enabled(conn, on: bool) -> None:
    from db import set_setting

    set_setting(conn, ENABLED_KEY, "1" if on else "0")


# ---- 授權網段 CRUD ----
def list_segments(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, prefix, enabled, note, created_at, updated_at "
        "FROM auto_onboard_segment ORDER BY prefix")]


def save_segment(conn, prefix: str, enabled: bool = True, note: str | None = None) -> int:
    """新增／更新一個授權網段（以 prefix 為唯一鍵，重複就更新）。"""
    prefix = (prefix or "").strip()
    if not prefix:
        raise ValueError("網段前綴不可為空")
    cur = conn.execute(
        "INSERT INTO auto_onboard_segment (prefix, enabled, note) VALUES (?,?,?) "
        "ON CONFLICT(prefix) DO UPDATE SET enabled=excluded.enabled, note=excluded.note, "
        "updated_at=datetime('now','localtime')",
        (prefix, 1 if enabled else 0, note),
    )
    conn.commit()
    return cur.lastrowid


def set_segment_enabled(conn, seg_id: int, enabled: bool) -> int:
    cur = conn.execute(
        "UPDATE auto_onboard_segment SET enabled = ?, updated_at = datetime('now','localtime') "
        "WHERE id = ?", (1 if enabled else 0, seg_id))
    conn.commit()
    return cur.rowcount


def delete_segment(conn, seg_id: int) -> int:
    cur = conn.execute("DELETE FROM auto_onboard_segment WHERE id = ?", (seg_id,))
    conn.commit()
    return cur.rowcount


# ---- 授權判定（純函式，好測）----
def authorized_prefixes(conn) -> list[str]:
    """目前「啟用中」的授權網段前綴。停用的不算——排程不會碰。"""
    return [r["prefix"] for r in conn.execute(
        "SELECT prefix FROM auto_onboard_segment WHERE enabled = 1 ORDER BY prefix")]


def matches(prefixes: list[str], ip: str) -> bool:
    """ip 是否落在任一授權前綴內。刻意用單純的前綴比對，與憑證庫 scope 同一套規則。"""
    if not ip:
        return False
    return any(ip.startswith(p.rstrip("*")) for p in prefixes if p)


# ---- 候選探查 ----
def _platform_of(conn, ip: str) -> str:
    """從最近一次掃描的開放埠猜平台（3389→windows，否則 linux）。與收集鏈同一套判斷。"""
    import facts_collector

    row = conn.execute(
        "SELECT open_ports FROM scan_history WHERE ip = ? AND scan_ok = 1 "
        "ORDER BY scan_time DESC LIMIT 1", (ip,)).fetchone()
    ports = [int(p) for p in (row["open_ports"] or "").split(",") if p.strip().isdigit()] \
        if row and row["open_ports"] else []
    return facts_collector.detect_platform(ports)


def find_candidates(conn) -> list[dict]:
    """授權網段內「已登記但未納管」的主機。這就是排程會去處理的集合。

    來源用 manage_state.summarize 的四態判定（同一份真相），只挑 state=未納管、
    且 ip 落在啟用中授權網段內的。平台從掃描指紋猜，決定用哪種手法。
    """
    import manage_state

    prefixes = authorized_prefixes(conn)
    if not prefixes:
        return []
    out = []
    for item in manage_state.summarize(conn)["items"]:
        ip = item.get("ip")
        if item.get("state") != manage_state.NOT_ONBOARDED:
            continue
        if not matches(prefixes, ip):
            continue
        out.append({
            "ip": ip,
            "asset_serial": item.get("asset_serial"),
            "platform": _platform_of(conn, ip),
        })
    return out


# ---- 核心：跑一輪自動納管 ----
def _default_cred_lookup(conn, cred_key_path):
    """預設憑證解析：從加密憑證庫依網段挑一組 ssh 憑證。回 (name, user, pw)。

    - 找不到適用憑證 → None
    - 找到但解不開（金鑰換過）→ (name, None, None)
    明文只在記憶體、交給引擎後即丟，絕不寫回任何地方。
    """
    import credential_store

    def lookup(ip: str):
        name = credential_store.pick_for_host(conn, ip, kind="ssh")
        if not name:
            return None
        got = credential_store.get_for_use(conn, name, key_path=cred_key_path)
        if got is None:
            return (name, None, None)
        return (name, got[0], got[1])

    return lookup


def _audit(conn, ip, platform, login_user, ok, stage, message, output=""):
    """寫一筆 trigger='auto' 的納管稽核。**永不含密碼**（login_user 只是帳號名）。"""
    conn.execute(
        "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, "
        "triggered_by, ok, stage, message, output) VALUES (?,?,?,?,?,?,?,?,?)",
        (ip, platform, login_user, "auto", "(排程)", 1 if ok else 0, stage, message, output),
    )
    conn.commit()


def run_auto_onboard(conn, collector_ip: str, *, executor=None, pubkey=None,
                     cred_lookup=None, cred_key_path: str | None = None) -> dict:
    """對授權網段內的未納管主機跑一輪 bootstrap（僅 Linux）。回統計＋逐台明細。

    ⚠️ 這裡**不檢查總開關**——總開關（auto_onboard_enabled）只擋「無人在場的排程」，
    由呼叫端（scan_service）判斷；真正的安全閘門是授權網段（沒授權就沒候選）。
    手動「立即執行」時操作者在場，照樣受授權網段約束即可。

    executor / cred_lookup 皆可注入，測試不碰真網路、不碰真憑證。
    密碼只在本函式與引擎之間傳遞、用完即丟——絕不寫回傳值、DB、log。
    """
    import credential_store
    import onboard_engine

    collector_ip = collector_ip or os.environ.get("ASSET_COLLECTOR_IP", "YOUR_SERVER_IP")
    if cred_key_path is None:
        cred_key_path = credential_store.KEY_PATH_DEFAULT
    lookup = cred_lookup or _default_cred_lookup(conn, cred_key_path)

    candidates = find_candidates(conn)
    onboarded = failed = skipped = 0
    details = []

    for c in candidates:
        ip, platform = c["ip"], c["platform"]
        if platform == "windows":
            skipped += 1
            details.append({**c, "action": "skipped",
                            "message": "Windows 走 WinRM 收集，不需 bootstrap；"
                                       "未納管請確認已設定 WinRM 憑證且目標 5985 可連"})
            continue
        if platform != "linux":
            skipped += 1
            details.append({**c, "action": "skipped",
                            "message": f"未支援自動納管的平台：{platform}"})
            continue

        cred = lookup(ip)
        if cred is None:
            failed += 1
            msg = "找不到適用的 SSH 憑證（請在收集憑證新增 kind=ssh 並設定適用網段）"
            _audit(conn, ip, platform, None, False, "connect", msg)
            details.append({**c, "action": "failed", "message": msg})
            continue
        cred_name, username, password = cred
        if username is None:
            failed += 1
            msg = f"SSH 憑證「{cred_name}」解不開（加密金鑰可能已更換），請重設"
            _audit(conn, ip, platform, None, False, "connect", msg)
            details.append({**c, "action": "failed", "message": msg})
            continue

        # 憑證取出後只在這裡與引擎之間存在，用完即丟
        result = onboard_engine.onboard(
            host=ip, platform=platform, username=username, password=password,
            collector_ip=collector_ip, pubkey=pubkey, executor=executor,
        )
        password = None  # 明確結束密碼生命週期
        _audit(conn, ip, platform, username, result.ok, result.stage,
               result.message, result.output)
        if result.ok:
            onboarded += 1
            details.append({**c, "action": "onboarded", "message": result.message})
        else:
            failed += 1
            details.append({**c, "action": "failed", "message": result.message})

    return {
        "enabled": is_enabled(conn),
        "authorized_prefixes": authorized_prefixes(conn),
        "candidates": len(candidates),
        "onboarded": onboarded,
        "failed": failed,
        "skipped": skipped,
        "details": details,
    }


def scheduled_cycle(collector_ip: str | None = None, conn=None,
                    refresh_runner=None, collect_runner=None, **kw) -> dict:
    """完整一輪：試連 → 自動納管 Linux 未納管 → 再試連 → 收 facts。

    掃描剛跑完時呼叫最有意義（有新鮮的存活與指紋資料）。這是排程／手動立即執行／
    命令列共用的入口。refresh_runner/collect_runner 可注入給測試，正式環境走真網路。
    """
    import manage_state

    own = conn is None
    conn = conn or get_connection()
    try:
        collector_ip = collector_ip or os.environ.get("ASSET_COLLECTOR_IP", "YOUR_SERVER_IP")
        manage_state.refresh_collect_status(conn, runner=refresh_runner)   # 先知道誰未納管
        result = run_auto_onboard(conn, collector_ip, **kw)
        manage_state.refresh_collect_status(conn, runner=refresh_runner)   # 納管後重試連
        result["collected"] = manage_state.collect_facts_into_assets(conn, runner=collect_runner)
        return result
    finally:
        if own:
            conn.close()


def recent_auto_audit(conn, limit: int = 50) -> list[dict]:
    """最近的排程納管稽核（trigger='auto'）。不含任何憑證。"""
    return [dict(r) for r in conn.execute(
        "SELECT target_ip, platform, login_user, ok, stage, message, created_at "
        "FROM onboard_audit WHERE trigger = 'auto' ORDER BY id DESC LIMIT ?", (limit,))]


# ---- 診斷外掛：只給設定與稽核，永不給密碼 ----
try:
    import diagnostics

    @diagnostics.register("auto_onboard")
    def _diag(conn) -> dict:
        try:
            segs = list_segments(conn)
            recent = recent_auto_audit(conn, 30)
        except Exception:  # noqa: BLE001
            segs, recent = [], []
        return {"enabled": is_enabled(conn), "segments": segs, "recent_auto_onboards": recent}
except ImportError:
    pass


if __name__ == "__main__":
    # 排程觸發（部署環境）：systemd timer／工作排程器每天呼叫一次 `python auto_onboard.py`。
    # 站內排程器（ASSET_SCHEDULER=1）已在掃描後自動接這一輪，這條命令列是給沒跑常駐服務、
    # 或想手動補一輪的環境用。總開關關閉時不動作（跟排程一致）。
    from db import init_db

    init_db()
    connection = get_connection()
    try:
        if not is_enabled(connection):
            print("自動納管總開關未啟用（auto_onboard_enabled=0），不動作。")
            raise SystemExit(0)
        summary = scheduled_cycle(conn=connection)
        print(f"授權網段 {summary['authorized_prefixes']}｜候選 {summary['candidates']}｜"
              f"納管成功 {summary['onboarded']}｜失敗 {summary['failed']}｜跳過 {summary['skipped']}")
        for d in summary["details"]:
            print(f"  [{d['action']}] {d['ip']} ({d['platform']}) — {d['message']}")
    finally:
        connection.close()
