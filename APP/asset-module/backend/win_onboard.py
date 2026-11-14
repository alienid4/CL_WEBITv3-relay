"""Windows 納管：勾選才收（2026-10-22）。

## 使用者怎麼用

2-1 WinRM 收集頁簽：Windows 主機**預設不登入、不收集**。人勾選幾台按「納管」，才：
登入（WinRM／PSRP）→ 看收集帳號是不是本機管理員 → 收一輪全部（主機事實、硬體規格、服務＋IIS、
軟體、本機帳號、排程、OS 重建、組態檢核、健康快照）→ 記下結果。之後 2-1 開始收集（手動／排程）
才會把這台收進來。另有「重新納管」（再驗一次＋再收一輪全部）與「取消納管」（停止收集，資料保留）。

## 為什麼以前不是這樣

2-1 開始收集以前只要 445／5985 開著、又有一組適用的 winrm 憑證，就**自動登入**收 facts——
Linux 要人按「一鍵納管」才動，Windows 卻是掃到就登入。對公司上千台 Windows，那等於收集器
每一輪都拿服務帳號去登一大批沒人說要收的機器（SOC 看到的就是一個帳號掃一整段在登入）。

## 跟 Linux 的對照（狀態模型）

  Linux：onboard_audit（每次納管／取消納管一筆，trigger='revoke' 是取消）＋ hardware.collect_ok
  Windows：同一張 onboard_audit（platform='windows'，trigger='winrm' 是納管、'revoke' 是取消）
           ＋ 本模組的 win_onboard_state（這台**現在**要不要收：opted_in）＋ hardware.collect_ok

多一張表的原因：Linux「要不要收」看的是目標機上**有沒有收集帳號**（取消納管會刪帳號，收集自然失敗）；
Windows 我們**不在目標機建任何東西**（決策 WINRM-ADMIN1：權限由 AD 群組經 GPO 給），
取消納管後服務帳號照樣登得進去——所以「要不要登入」必須是我們這邊明確記著的一個開關，
不能靠「登不登得進去」來推。

## 底線

- 納管不改目標機任何東西：不建帳號、不改群組、不裝東西、不改設定、不改執行原則
  （腳本以 PSRP 的資料送出，不落地、不 Bypass、不 base64、不 Invoke-Expression；見 winrm_collector）。
- VMware 天條：判成 ESXi／vCenter 的一律拒絕（fail-closed），不登入。
- 判不出這台是否已納管（查表失敗）→ 當作沒納管，不登入（fail-closed）。
- 密碼只在 collect_dispatch._do_winrm 裡解出來、用完即丟；這裡不碰明文。
"""
from __future__ import annotations

import sqlite3

#: 一次性：把「已經 WinRM 收成功過」的主機視為已納管（2026-10-22 上線前的舊行為收進來的那幾台）
GRANDFATHER_KEY = "win_onboard_grandfathered_v1"
#: 收集帳號經哪個 AD 群組拿到本機 Administrators（組織內部名稱，不進版控；系統設定填）
ADMIN_GROUP_KEY = "winrm_admin_group"

#: 2-1 收集結果「待納管」那一列的說明（使用者要的字）
PENDING_MESSAGE = "未納管：勾選後按納管"
#: 稽核表的 trigger（取消納管沿用 Linux 的 'revoke'，讓既有「最後一個動作是取消」的判定一體適用）
TRIGGER_ONBOARD = "winrm"
TRIGGER_REVOKE = "revoke"
PLATFORM = "windows"


# ───────────── 認台 ─────────────
#
# 規則（獨立審查 2026-10-22，IP 會回收，所以「只有 IP 相同」永遠不算同一台）：
#   · 這個 IP 現在有**現役**登記 → 只認「同一台」（machine_key 相同，或同一台的某筆登記＋同 IP）的那列。
#     以前沒登記時用 'ip:<IP>' 記下的那列**不算**——那時候在那個 IP 的不一定是現在這台。
#   · 這個 IP 現在只剩**退役**登記 → 一律不算已納管（fail-closed），2-1 寫「登記已退役，請重新納管」。
#   · 這個 IP 現在完全沒有登記 → 才認 'ip:<IP>' 那列（上線前收過、還沒登記的那種）。

#: 2-1「待納管」那一列的另一種原因：納管過的那筆登記已退役（IP 可能已給了別台）
RETIRED_MESSAGE = "登記已退役，請重新納管"
_NO_ASSET = object()


def _is_retired(a: dict | None) -> bool:
    import manage_state

    return bool(a) and (a.get("asset_status") or "").strip() in manage_state.RETIRED_STATUS


def _asset_for_ip(conn, ip: str) -> dict | None:
    """這個 IP 的資產（同 IP 多筆登記挑現役那筆：manage_state.serial_for_ip，全入口同一把尺）。
    只剩退役登記時回的是退役那筆（呼叫端用 _is_retired 判）。"""
    import manage_state

    sn = manage_state.serial_for_ip(conn, ip)
    if not sn:
        return None
    row = conn.execute("SELECT asset_serial, hostname, ip, os, device_model, asset_purpose, asset_status "
                       "FROM hardware WHERE asset_serial = ?", (sn,)).fetchone()
    return dict(row) if row else None


def key_for(conn, ip: str, asset=_NO_ASSET) -> str:
    """這台的身分鍵：有登記就用全站唯一那把尺（system_stats.machine_key），沒登記用 'ip:<IP>'。"""
    import system_stats

    ip = (ip or "").strip()
    a = _asset_for_ip(conn, ip) if asset is _NO_ASSET else asset
    if not a:
        return f"ip:{ip}"
    return system_stats.machine_key(a.get("hostname"), ip, a.get("asset_serial"))


def lookup(conn, ip: str, asset=_NO_ASSET) -> dict | None:
    """這台**現在**對得上的納管狀態列；對不上（含退役、IP 換了別台）回 None。規則見上面。"""
    import manage_state

    ip = (ip or "").strip()
    if not ip:
        return None
    a = _asset_for_ip(conn, ip) if asset is _NO_ASSET else asset
    if _is_retired(a):
        return None
    if not a:
        row = conn.execute("SELECT * FROM win_onboard_state WHERE machine_key = ?",
                           (f"ip:{ip}",)).fetchone()
        return dict(row) if row else None
    row = conn.execute("SELECT * FROM win_onboard_state WHERE machine_key = ?",
                       (key_for(conn, ip, a),)).fetchone()
    if row is None:
        # 主機名在 CIA 被改正（身分鍵變了）：同一台的某筆登記、同一個 IP 記的那列還算
        serials = manage_state.same_machine_serials(conn, a["asset_serial"]) or [a["asset_serial"]]
        row = conn.execute(
            f"SELECT * FROM win_onboard_state WHERE ip = ? AND asset_serial IN "
            f"({','.join('?' * len(serials))}) ORDER BY rowid DESC LIMIT 1", (ip, *serials)).fetchone()
    return dict(row) if row else None


def is_opted_in(conn, ip: str, asset=_NO_ASSET) -> bool:
    """這台現在要不要登入收集。**查不出來就是不要**（fail-closed：不確定就不登入）。"""
    try:
        row = lookup(conn, ip, asset)
    except sqlite3.Error:
        # 舊庫沒有這張表、或查詢失敗：寧可這一輪不收（畫面寫「待納管」），也不要登入沒人說要收的機器
        return False
    return bool(row and row.get("opted_in") == 1)


def pending_reason(conn, ip: str) -> str:
    """沒納管的 WinRM 列給人看的那句：一般是「未納管：勾選後按納管」；
    納管過的那筆登記已退役 →「登記已退役，請重新納管」。"""
    try:
        a = _asset_for_ip(conn, ip)
        if _is_retired(a):
            hit = conn.execute(
                "SELECT 1 FROM win_onboard_state WHERE opted_in = 1 AND (machine_key = ? OR asset_serial = ?)",
                (key_for(conn, ip, a), a["asset_serial"])).fetchone()
            if hit:
                return RETIRED_MESSAGE
    except sqlite3.Error:
        # 查不到就用一般那句；狀態一樣是待納管（不登入）
        return PENDING_MESSAGE
    return PENDING_MESSAGE


def is_opted_in_serial(conn, asset_serial: str, ip: str | None = None) -> bool:
    """收集器（照資產序號跑的那幾支）問：這筆登記的 Windows 現在可以登入嗎。
    用這筆登記自己去認台（不是用 IP 去挑「現役那筆」）：退役的、跟現在 IP 對不上的一律不行。"""
    try:
        a = conn.execute("SELECT asset_serial, hostname, ip, os, device_model, asset_purpose, asset_status "
                         "FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
        if a is None:
            return False
        return is_opted_in(conn, (ip or a["ip"] or "").strip(), dict(a))
    except sqlite3.Error:
        # 查不到就不登入（fail-closed）
        return False


#: 收集器擋下沒納管的 Windows 時寫的原因（使用者看得懂下一步）
NOT_OPTED_COLLECT = "Windows 未納管（或已取消納管）：不登入。要收請到 2-1 WinRM 頁簽勾選後按「納管」"


def collect_block(conn, ip: str, asset_serial: str | None = None) -> str | None:
    """各收集器（軟體／服務／帳號／規格／OS 重建／排程／主機事實／健康）在**登入 Windows 之前**問一句：
    沒納管（從沒納管、已取消、登記已退役、IP 已換別台）就回原因、不登入；可以登入回 None。
    第二道防線：那幾支本來就只挑 collect_ok=1 的機器，取消納管也會把整台的 collect_ok 清掉。"""
    ok = is_opted_in_serial(conn, asset_serial, ip) if asset_serial else is_opted_in(conn, ip)
    return None if ok else NOT_OPTED_COLLECT


def public_state(row: dict | None) -> dict | None:
    """給畫面的那幾個欄位（沒有任何憑證相關的東西）。"""
    if not row:
        return None
    return {"opted_in": bool(row.get("opted_in") == 1), "is_admin": row.get("is_admin"),
            "last_ok": row.get("last_ok"), "last_message": row.get("last_message"),
            "last_at": row.get("last_at"), "onboarded_by": row.get("onboarded_by"),
            "onboarded_at": row.get("onboarded_at"), "revoked_by": row.get("revoked_by"),
            "revoked_at": row.get("revoked_at")}


def _assets_for_ips(conn, ips) -> dict[str, dict]:
    """_asset_for_ip 的批次版（同一把尺 manage_state.serials_for_ips）。"""
    import manage_state

    sn = manage_state.serials_for_ips(conn, ips)
    out: dict[str, dict] = {}
    serials = list(set(sn.values()))
    by_sn: dict[str, dict] = {}
    for i in range(0, len(serials), 400):
        chunk = serials[i:i + 400]
        for r in conn.execute("SELECT asset_serial, hostname, ip, os, device_model, asset_purpose, asset_status "
                              f"FROM hardware WHERE asset_serial IN ({','.join('?' * len(chunk))})", chunk):
            by_sn[r["asset_serial"]] = dict(r)
    for ip, s in sn.items():
        if s in by_sn:
            out[ip] = by_sn[s]
    return out


def resolve_batch(conn, ips) -> dict[str, tuple[dict | None, dict | None, str]]:
    """一批 IP → (資產, 對得上的狀態列, 沒納管時給人看的那句)。同 lookup／pending_reason 的規則；
    2-1 一次讀上千列用這支：資產一次撈、表裡有哪些鍵／IP 一次撈，對不上的 IP 不做完整認台。"""
    want = list(dict.fromkeys(i.strip() for i in (ips or []) if i and i.strip()))
    if not want:
        return {}
    try:
        assets = _assets_for_ips(conn, want)
        st = [dict(r) for r in conn.execute(
            "SELECT machine_key, ip, asset_serial, opted_in FROM win_onboard_state")]
    except sqlite3.Error:
        # 沒有這張表／查詢失敗：全部當沒納管（fail-closed：不登入、畫面待納管）
        return {ip: (None, None, PENDING_MESSAGE) for ip in want}
    keys = {r["machine_key"] for r in st}
    have_ips = {r["ip"] for r in st}
    opted_keys_serials = {r["machine_key"] for r in st if r["opted_in"] == 1} | {
        r["asset_serial"] for r in st if r["opted_in"] == 1 and r["asset_serial"]}
    out: dict[str, tuple[dict | None, dict | None, str]] = {}
    for ip in want:
        a = assets.get(ip)
        row = None
        reason = PENDING_MESSAGE
        if keys:
            if _is_retired(a):
                if key_for(conn, ip, a) in opted_keys_serials or a["asset_serial"] in opted_keys_serials:
                    reason = RETIRED_MESSAGE
            elif not a:
                row = lookup(conn, ip, None) if f"ip:{ip}" in keys else None
            elif key_for(conn, ip, a) in keys or ip in have_ips:
                row = lookup(conn, ip, a)
        out[ip] = (a, row, reason)
    return out


def rows_for_ips(conn, ips) -> dict[str, dict | None]:
    """一批 IP → 各自對得上的狀態列（同 lookup 的規則，批次版）。"""
    return {ip: v[1] for ip, v in resolve_batch(conn, ips).items()}


def state_map(conn, ips) -> dict[str, dict]:
    """一批 IP → 畫面用的狀態（沒納管過的不列）。"""
    return {ip: st for ip, row in rows_for_ips(conn, ips).items() if (st := public_state(row))}


def _now() -> str:
    from db import _now_local

    return _now_local()


def _upsert(conn, ip: str, asset=_NO_ASSET, **fields) -> None:
    """寫這台的狀態列。對得上的那列沿用；那列的鍵跟現在的身分鍵不同（主機名改過、先用 IP 記的）
    就順手搬到現在的鍵（之後直接用身分鍵就認得到）。"""
    a = _asset_for_ip(conn, ip) if asset is _NO_ASSET else asset
    if _is_retired(a):
        raise ValueError("退役登記不記納管狀態")
    row = lookup(conn, ip, a)
    key = key_for(conn, ip, a)
    serial = (a or {}).get("asset_serial") or (row or {}).get("asset_serial")
    base = {"ip": ip, "asset_serial": serial, **fields}
    if row is None:
        cols = ["machine_key", *base]
        conn.execute(f"INSERT INTO win_onboard_state ({', '.join(cols)}) "
                     f"VALUES ({', '.join('?' * len(cols))})", (key, *base.values()))
    else:
        sets = ", ".join(f"{c} = ?" for c in ("machine_key", *base))
        conn.execute(f"UPDATE win_onboard_state SET {sets} WHERE machine_key = ?",
                     (key, *base.values(), row["machine_key"]))


# ───────────── 舊資料收進來 ─────────────

def grandfather(conn) -> int:
    """一次性：上線前已經 WinRM 收成功過的主機 → 視為已納管（不然升版後它們會突然停收）。

    證據兩處都認：2-1 收集結果有過 route=winrm 且 collected 的列；或 win_collect_session 有紀錄
    （那張表只有 WinRM 真的登入成功才會寫）。跑過就記在 app_settings，不重跑——
    之後人取消納管的，不可以因為重啟又被收回來。回收進來幾台。
    """
    import db

    if db.get_setting(conn, GRANDFATHER_KEY):
        return 0
    # 身分鍵要用這個庫的口徑算（主機名口徑／舊口徑）；init_db 時連線可能還沒套過這個庫的設定
    import machine_identity

    machine_identity.refresh(conn)
    ips: set[str] = set()
    for sql in ("SELECT DISTINCT ip FROM collect_dispatch_result "
                "WHERE route = 'winrm' AND status = 'collected'",
                "SELECT DISTINCT ip FROM win_collect_session"):
        try:
            ips |= {(r[0] or "").strip() for r in conn.execute(sql) if r[0]}
        except sqlite3.Error:
            # 舊庫沒有那張表＝那條證據不存在，另一條照算
            continue
    n = 0
    now = _now()
    for ip in sorted(ips):
        if not ip:
            continue
        a = _asset_for_ip(conn, ip)
        # 只剩退役登記的不收進來（那個 IP 現在不一定是當時那台；要收請重新納管）
        if _is_retired(a) or lookup(conn, ip, a) is not None:
            continue
        _upsert(conn, ip, a, opted_in=1, last_ok=1,
                last_message="上線前已收成功過，視為已納管", last_at=now,
                onboarded_by="(上線前已收過)", onboarded_at=now)
        n += 1
    db.set_setting(conn, GRANDFATHER_KEY, now)
    conn.commit()
    return n


# ───────────── 不登入的機器（VMware 天條／另有納管方式）─────────────

def refusal(conn, ip: str, asset: dict | None) -> str | None:
    """這台不可以走 Windows 納管就回原因（訊息含「另有納管方式」，納管失敗清單據此排除）。

    跟 2-1 同一套判定、同一張人工指定表（os_override）：
      · VMware（ESXi／vCenter）：**一律**拒絕，人工改分類也不放行（天條，同 latest_run 的做法）。
      · 人工指定成另有納管方式（儲存／網路／ESXi／OCP／其他）：拒絕。
      · 資產登記或 2-1 最近一次判成另有納管方式（儲存、網路設備…）：拒絕——除非人工指定成 WinRM 收集。
    直接打 API 也一樣擋（畫面那道只是第一道）。
    """
    import collect_dispatch as cd
    import onboard_eligibility as oe
    import os_override

    full = None
    if asset:
        r = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (asset["asset_serial"],)).fetchone()
        full = dict(r) if r else dict(asset)
    ov = cd.override_of(os_override.active_map(conn), {
        "ip": ip, "hostname": (full or {}).get("hostname"), "asset_serial": (full or {}).get("asset_serial")})
    ov_tab = (ov or {}).get("tab")
    if full and oe.is_vmware_platform(full):
        return f"VMware 平台（ESXi／vCenter）——{cd.NOT_ONBOARDABLE_TEXT}；天條：不登入、不納管"
    blk = oe.block_for_asset(conn, full, ip=ip) if full else None
    if blk and blk.get("kind") == "esxi":
        return f"VMware（{blk.get('reason') or ''}）——{cd.NOT_ONBOARDABLE_TEXT}；天條：不登入、不納管"
    last = conn.execute("SELECT status, not_onboardable_kind FROM collect_dispatch_result "
                        "WHERE ip = ? ORDER BY id DESC LIMIT 1", (ip,)).fetchone()
    if last and last["status"] == cd.STATUS_NOT_ONBOARDABLE and last["not_onboardable_kind"] == "esxi":
        return f"2-1 收集判定這台是 VMware——{cd.NOT_ONBOARDABLE_TEXT}；天條：不登入、不納管"
    if ov_tab in cd._TAB_NOK_KIND:
        return (f"人工指定為「{cd.TAB_LABEL.get(ov_tab, ov_tab)}」——{cd.NOT_ONBOARDABLE_TEXT}，"
                f"不走 Windows 納管（{ov.get('by') or '不詳'} 於 {ov.get('at') or '不詳'}）")
    if ov_tab == cd.TAB_WINRM:
        return None       # 人看過、指定成 WinRM 收集：以人為準（VMware 已在上面擋掉）
    if blk:
        return f"{blk.get('label') or '設備'}——{cd.NOT_ONBOARDABLE_TEXT}，不走 Windows 納管。{blk.get('reason') or ''}"
    if last and last["status"] == cd.STATUS_NOT_ONBOARDABLE:
        return f"2-1 收集判定為另有納管方式（{last['not_onboardable_kind'] or '設備'}）——不走 Windows 納管"
    return None


# ───────────── 納管／重新納管 ─────────────

def _cred_user(conn, ip: str, credential: str | None = None) -> str | None:
    """這台會用哪組 winrm 憑證的**帳號名**（給稽核；不碰密碼）。"""
    import credential_store

    if credential == credential_store.AUTO:
        name = credential_store.pick_for_host(conn, ip, kind="winrm", use_pref=False)
    else:
        name = credential or credential_store.pick_for_host(conn, ip, kind="winrm")
    if not name:
        return None
    for c in credential_store.list_public(conn):
        if c["name"] == name:
            return c.get("username") or name
    return name


def _audit(conn, ip: str, *, trigger: str, by: str | None, ok: bool, stage: str,
           message: str, login_user: str | None = None) -> None:
    """跟 Linux 同一張納管紀錄（2-1「上次納管時間／上次失敗」、納管失敗頁簽、取消後不疊回都讀它）。"""
    conn.execute(
        "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, triggered_by, ok, "
        "stage, message, output) VALUES (?,?,?,?,?,?,?,?,?)",
        (ip, PLATFORM, login_user, trigger, by, 1 if ok else 0, stage, (message or "")[:1000], None))


def _ensure_asset(conn, ip: str) -> tuple[dict | None, str | None]:
    """沒有現役登記的主機補一筆最小資產（同 Linux 一鍵納管）：收到的事實才有地方落。
    只剩退役登記的也補一筆新的（退役那筆是以前那台，不能把這台的事實寫到它身上）。
    回 (資產, 這次新建的序號或 None)。"""
    a = _asset_for_ip(conn, ip)
    if a and not _is_retired(a):
        return a, None
    import db

    scan_hn = None
    try:
        r = conn.execute("SELECT hostname FROM scan_history WHERE ip = ? AND scan_ok = 1 "
                         "ORDER BY scan_time DESC LIMIT 1", (ip,)).fetchone()
        scan_hn = r["hostname"] if r and r["hostname"] else None
    except sqlite3.Error:
        # 沒有掃描紀錄表：主機名留空（收到的 facts 會補）
        scan_hn = None
    serial = f"AUTO-{ip}"
    if conn.execute("SELECT 1 FROM hardware WHERE asset_serial = ?", (serial,)).fetchone():
        serial = f"AUTO-{ip}-{_now().replace('-', '').replace(':', '').replace(' ', '')}"
    db.insert_hardware(conn, asset_serial=serial, ip=ip, hostname=scan_hn)
    return _asset_for_ip(conn, ip), serial


def _health(conn, ip: str, health_runner=None, cred_key_path=None) -> str:
    """健康快照一份（source='onboard'），回一句結果。失敗不影響納管本身，但要講出來。"""
    import credential_store
    import health_baseline
    import health_probe

    def _creds(_ip):
        name = credential_store.pick_for_host(conn, _ip, kind="winrm")
        if not name:
            return None
        return (credential_store.get_for_use(conn, name, key_path=cred_key_path)
                if cred_key_path else credential_store.get_for_use(conn, name))

    try:
        r = health_probe.check_one(ip, platform="windows", creds=_creds, _winrm=health_runner)
        health_baseline.save_snapshot(conn, r, source="onboard")
        return f"健康快照：{r.get('overall') or '不明'}"
    except Exception as exc:  # noqa: BLE001 - 健康快照失敗不可以把整次納管判成失敗，但要講出來
        return f"健康快照沒收到：{str(exc)[:120]}"


def onboard_one(conn, ip: str, *, by: str | None, winrm_runner=None, cred_key_path=None,
                enqueue=None, health_runner=None, credential: str | None = None) -> dict:
    """納管／重新納管一台 Windows：登入 → 本機管理員 → 收一輪全部。回逐台結果。

    重新納管＝同一支（已納管的再跑一次：重驗登入與管理員、再收一輪全部）。
    成功才記成已納管；重新納管失敗**不**把已納管的取消（同 Linux：舊的那套還在），只記這次失敗。
    `winrm_runner`／`health_runner`／`enqueue` 只給測試注入，正式呼叫端不給。
    """
    import collect_dispatch as cd
    import post_onboard

    ip = (ip or "").strip()
    before = lookup(conn, ip)
    was_in = bool(before and before.get("opted_in") == 1)
    action = "重新納管" if was_in else "納管"
    asset = _asset_for_ip(conn, ip)

    why = refusal(conn, ip, asset)
    if why:
        _audit(conn, ip, trigger=TRIGGER_ONBOARD, by=by, ok=False, stage="refused", message=why)
        conn.commit()
        return {"ip": ip, "ok": False, "stage": "refused", "message": why,
                "opted_in": was_in, "is_admin": None, "action": action}

    user = _cred_user(conn, ip, credential)
    asset, created = _ensure_asset(conn, ip)
    status, message = cd._do_winrm(conn, ip, asset, winrm_runner, cred_key_path, credential=credential)
    ok = status == cd.STATUS_COLLECTED
    if not ok and created:
        # 登入失敗：剛剛為它補的那筆最小資產收回（同 Linux：納管成功才補登），只刪自己剛建的那一筆
        conn.execute("DELETE FROM hardware WHERE asset_serial = ?", (created,))
        asset = _asset_for_ip(conn, ip)
        if _is_retired(asset):
            asset = None
    now = _now()
    sess = None
    if ok:
        import winrm_collector

        sess = winrm_collector.session_for(conn, ip)
    is_admin = (sess or {}).get("is_admin") if ok else (before or {}).get("is_admin")
    stage = "collect" if ok else ("credential" if status == cd.STATUS_NEEDS_CREDENTIAL else "connect")
    fields = {"last_ok": 1 if ok else 0, "last_message": message[:500], "last_at": now}
    if ok:
        fields.update(opted_in=1, is_admin=is_admin)
        if not was_in:
            fields.update(onboarded_by=by, onboarded_at=now, revoked_by=None, revoked_at=None)
    elif before is None:
        fields.update(opted_in=0)
    if ok or asset is None or not _is_retired(asset):
        _upsert(conn, ip, asset, **fields)
    picked = ""
    if credential:
        import credential_store
        if credential == credential_store.AUTO:
            picked = "（指定：依網段自動）"
            if ok:
                credential_store.clear_pref(conn, ip, "winrm")
        else:
            picked = f"（指定憑證：{credential}）"
            if ok:        # 登入成功才記：之後這台所有收集都走這一組；失敗不改原本的設定
                credential_store.set_pref(conn, ip, "winrm", credential, by)
    _audit(conn, ip, trigger=TRIGGER_ONBOARD, by=by, ok=ok, stage=stage,
           message=f"Windows {action}{picked}：{message}", login_user=user)
    conn.commit()

    notes: list[str] = []
    queued = False
    if ok and asset:
        # 收一輪全部：同 Linux 納管後那一套（規格／服務＋IIS／軟體／帳號／排程／OS 重建／組態檢核），
        # 背景隊伍逐台跑（一次勾幾百台也不會同時轟）；結果寫進採集紀錄，2-1 與資產頁看得到每一樣
        try:
            (enqueue or post_onboard.enqueue)(cd._db_path_of(conn), [asset["asset_serial"]],
                                              by=by, source="onboard")
            queued = True
        except Exception as exc:  # noqa: BLE001 - 排不進去要講出來，不可以靜默
            notes.append(f"全部收集沒有排進去：{exc}")
        notes.append(_health(conn, ip, health_runner, cred_key_path))
    return {"ip": ip, "ok": ok, "stage": stage, "action": action,
            "message": message + (f"（{'；'.join(notes)}）" if notes else ""),
            "opted_in": ok or was_in, "is_admin": is_admin, "queued": queued,
            "asset_serial": (asset or {}).get("asset_serial")}


# ───────────── 取消納管 ─────────────

def revoke(conn, ip: str, *, by: str | None) -> dict:
    """取消納管：之後的收集不再登入這台。**目標機上什麼都不動**（本來就沒建任何東西），收到的資料保留。"""
    import post_onboard

    ip = (ip or "").strip()
    row = lookup(conn, ip)
    if not row or row.get("opted_in") != 1:
        return {"ip": ip, "ok": False, "message": "這台沒有納管，不用取消"}
    import manage_state

    asset = _asset_for_ip(conn, ip)
    now = _now()
    _upsert(conn, ip, asset, opted_in=0, revoked_by=by, revoked_at=now,
            last_message=f"已由 {by or '不詳'} 取消納管", last_at=now)
    # 同 Linux 取消納管：這台現在收不到，而且是**刻意**收不到（不是連線失敗）——
    # 後面那幾樣收集只挑 collect_ok=1 的機器，所以排程不會再去登入它。
    # 寫**整台**（同一台的每一筆登記，含別的 IP 那幾筆），不然兄弟登記還是 collect_ok=1 照收
    serials = manage_state.expand_to_machines(conn, [asset["asset_serial"]]) if asset else []
    err = f"已由 {by or '不詳'} 取消納管（不是連線失敗）"
    conn.execute("UPDATE hardware SET collect_ok = 0, collect_checked_at = ?, collect_error = ? "
                 "WHERE ip = ?", (now, err, ip))
    if serials:
        conn.execute(f"UPDATE hardware SET collect_ok = 0, collect_checked_at = ?, collect_error = ? "
                     f"WHERE asset_serial IN ({','.join('?' * len(serials))})", (now, err, *serials))
    _audit(conn, ip, trigger=TRIGGER_REVOKE, by=by, ok=True, stage="revoke",
           message="Windows 取消納管（目標機未做任何變更；已收的資料保留）")
    conn.commit()
    if asset:
        try:
            post_onboard.cancel(conn, serials or [asset["asset_serial"]], by=by)
        except Exception as exc:  # noqa: BLE001 - 隊伍撤不掉要講出來；排到時收集器也會因 collect_ok=0 略過
            return {"ip": ip, "ok": True, "message": f"已取消納管（排隊中的收集沒撤掉：{exc}）"}
    return {"ip": ip, "ok": True, "message": "已取消納管：之後不再登入這台，已收的資料保留"}


# ───────────── 權限說明 ─────────────

#: 我們絕不做的事（畫面「不做」那一欄；跟 winrm_collector 檔頭的合規硬規則同一份承諾）
NEVER_DO = (
    "不在目標機建立或修改任何帳號、群組",
    "不寫檔、不安裝任何東西、不改任何設定或服務",
    "不改執行原則、不帶略過執行原則的參數",
    "腳本以 PSRP 資料送出：不落地成 .ps1、不 base64、不 -EncodedCommand、不 Invoke-Expression",
    "只走 WinRM 5985＋NTLM 訊息加密，不用 Basic、不允許未加密",
    "不收任何密碼或秘密",
)


def permissions(conn) -> dict:
    """權限面板：用哪個帳號、權限怎麼來、讀哪些東西、絕不做什麼、各台是不是本機管理員。"""
    import credential_store
    import db
    import winrm_collector

    accounts = [{"name": c["name"], "username": c.get("username"), "scope": c.get("scope") or ""}
                for c in credential_store.list_public(conn) if c.get("kind") == "winrm"]
    group = (db.get_setting(conn, ADMIN_GROUP_KEY, "") or "").strip()
    reads = [{"item": k, "label": winrm_collector.ITEM_LABEL.get(k, k), "needs": v}
             for k, v in winrm_collector.PERM_NEED.items()]
    hosts = []
    for r in conn.execute("SELECT * FROM win_onboard_state WHERE opted_in = 1 ORDER BY ip"):
        d = dict(r)
        hosts.append({"ip": d["ip"], "asset_serial": d.get("asset_serial"),
                      "is_admin": d.get("is_admin"), "last_at": d.get("last_at"),
                      "last_ok": d.get("last_ok")})
    return {
        "accounts": accounts,
        "admin_group": group,
        "rights_via": "AD 群組經 GPO 加入各台本機 Administrators（我們不建帳號）",
        "reads": reads,
        "never": list(NEVER_DO),
        "hosts": hosts,
        "admin_yes": sum(1 for h in hosts if h["is_admin"] == 1),
        "admin_no": sum(1 for h in hosts if h["is_admin"] == 0),
        "admin_unknown": sum(1 for h in hosts if h["is_admin"] is None),
    }
