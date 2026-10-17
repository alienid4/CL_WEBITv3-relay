"""資產明細頁：每一類收集（帳號／軟體／服務／規格）在「這台」的現況。

為什麼單獨一支：api.py 已經是 ~500KB 的單檔（origin/main 就 499.8KB），
再往裡面塞邏輯會撞大檔閘門。這支把「筆數＋最後收集時間＋是不是收集對象」
的計算抽出來，api.py 只呼叫一行。

給前端分辨「真的沒有」「收集失敗」「從沒收過」——不要只給一個乾淨的 0
（2026-10-16 使用者：「服務 8、軟體 0、帳號 0，我無從判斷是沒有還是收集失敗」）。
失敗原因與最後嘗試時間另走 collect_trace（collect_ssh_trace：連不連得上、指令成敗的
事實，跨收集器共用的最後一跳）與 hardware.collect_ok/collect_error，前端一起判讀。
"""
from __future__ import annotations


def _max_seen(conn, table: str, where: str, params: list) -> str | None:
    """這台最後一次被這類收集「收到東西」的時間。沒這張表（舊 DB）回 None，不要敲整頁。"""
    try:
        r = conn.execute(
            f"SELECT MAX(last_seen) AS m FROM {table} WHERE gone_at IS NULL AND {where}",
            params).fetchone()
        return r["m"] if r else None
    except Exception:  # noqa: BLE001
        return None


def _row_count(conn, table: str, where: str, params: list) -> int:
    try:
        r = conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE gone_at IS NULL AND {where}",
            params).fetchone()
        return r["n"] if r else 0
    except Exception:  # noqa: BLE001
        return 0


def compute(conn, asset_serial: str, hardware, package_count: int) -> dict:
    """回 collect_status。

    targeted＝這台是不是「收集器會去收」的對象。帳號／服務／軟體三類都只收 collect_ok=1
    的主機（見 *_inventory._resolve_targets / account_inventory._targets），所以 collect_ok≠1
    的機器 0 是「從沒收過」，不是「真的沒有」。

    數字口徑（2026-10-16）：這裡的 count 是「目前在庫」，範圍是**這台機器**
    （machine_scope：同一台的全部登記＋這個 IP 收到的），跟下方分頁同一套——
    同一台的每一筆登記看到的數字都一樣。「本次收到幾筆」是收集按鈕當下的回應，另一回事。
    """
    import machine_scope

    sc = machine_scope.scope(conn, asset_serial, hardware)
    w, wp = machine_scope.scope_where(sc)
    try:
        collect_ok = hardware["collect_ok"]
    except (IndexError, KeyError):
        collect_ok = None
    try:
        _ph = ",".join("?" for _ in sc["serials"])
        _spec = conn.execute(
            f"SELECT MAX(collected_at) AS collected_at FROM host_spec WHERE asset_serial IN ({_ph})",
            sc["serials"]).fetchone()
        spec_at = _spec["collected_at"] if _spec else None
    except Exception:  # noqa: BLE001
        spec_at = None
    return {
        "targeted": collect_ok == 1,
        "collect_ok": collect_ok,
        "account": {"count": _row_count(conn, "host_account", w, wp),
                    "last_collected_at": _max_seen(conn, "host_account", w, wp)},
        "software": {"count": package_count,
                     "last_collected_at": _max_seen(conn, "host_package", w, wp)},
        "service": {"count": _row_count(conn, "host_service", w, wp),
                    "last_collected_at": _max_seen(conn, "host_service", w, wp)},
        "spec": {"last_collected_at": spec_at},
        # 同一台／同一個 IP 的其他登記——明細頁要看得到資料分散在哪幾筆（不合併）
        "scope_serials": sc["serials"],
        "same_ip": machine_scope.same_ip_records(conn, asset_serial, hardware),
        "self": machine_scope.self_flags(hardware),
        # 實測（存活掃描）vs CIA 登記：實測欄位以掃描為準，對不上要標（dyn_shell）
        "observed": _observed(conn, sc["serials"], hardware),
    }


def _observed(conn, serials, hardware):
    try:
        import dyn_shell

        return dyn_shell.observed_facts(conn, serials, hardware)
    except Exception:  # noqa: BLE001 - 輔助資訊，壞了不擋整頁
        return None
