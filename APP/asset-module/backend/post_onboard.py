"""納管成功後自動收一輪（2026-09-16 使用者：「以後納管後 要自動跑一次，收集全部 是手動狀態」）。

## 為什麼要有

使用者納管完一台、也按了「收集規格」，卻看到「軟體 0 服務 0 帳號 0」，
問「為什麼軟體跟帳號都沒有收集到」。原因是：納管只建帳號佈金鑰、收集規格只收硬體，
軟體／服務／帳號是三個各自獨立的收集，沒有人去觸發。

人剛把一台機器納管起來，他要的就是「這台的資料現在都進來了」。
所以納管成功後自動收一輪；「收集全部」按鈕保留給之後想手動重收的時候用。

## 為什麼在背景跑

納管端點本身已經要花數十秒（在目標機建帳號、佈金鑰）。再同步收四樣會讓它變成兩三分鐘，
瀏覽器多半先逾時——使用者會以為納管失敗，然後再按一次。

所以開一條背景執行緒，納管端點立刻回。收集結果寫進 `collect_log`（kind=`post_onboard`），
畫面上看得到成敗與原因。

## 失敗不影響納管

某一樣收不到（例如沒 root 收不到帳號）不影響其他樣，也**不會**把納管標成失敗——
納管成功是已經發生的事實。每一樣的結果分開記，人才知道是哪一樣沒收到。
"""
from __future__ import annotations

import threading

#: 依序收這幾樣。順序有意義：規格最快也最常成功，先讓畫面有東西；
#: 帳號最容易因為權限不足收不到，放最後才不會擋住前面。
JOBS = ("spec", "service", "software", "account")

JOB_LABEL = {"spec": "硬體規格", "service": "服務", "software": "軟體", "account": "帳號"}

_lock = threading.Lock()
#: 同一台不要同時跑兩輪（連按兩次納管、或納管＋手動收集撞在一起）
_running: set[str] = set()


def running_for(asset_serial: str) -> bool:
    return asset_serial in _running


def _collect_one(conn, job: str, asset_serial: str) -> tuple[bool, str]:
    """跑一樣。回 (有沒有收到, 說明)。例外在這裡吃掉並轉成說明，不往外丟。"""
    try:
        if job == "spec":
            import host_spec_collector

            r = host_spec_collector.run_collection(conn, trigger="post_onboard", only_serial=asset_serial)
        elif job == "service":
            import service_inventory

            r = service_inventory.collect_services(conn, only_serial=asset_serial, trigger="post_onboard")
        elif job == "software":
            import software_inventory

            r = software_inventory.collect_software(conn, only_serial=asset_serial, trigger="post_onboard")
        else:
            import account_inventory

            r = account_inventory.collect_accounts(conn, only_serial=asset_serial, trigger="post_onboard")
    except Exception as exc:  # noqa: BLE001 - 每一樣各自失敗，不可以拖垮其他樣
        return False, f"{type(exc).__name__}: {exc}"

    r = r or {}
    ok = bool(r.get("ok") or r.get("hosts") or r.get("collected") or r.get("count"))
    if ok:
        return True, "收到了"
    if r.get("unsupported"):
        return False, "這台平台暫不支援"
    detail = (r.get("fail_detail") or [{}])[0].get("error") if r.get("fail_detail") else None
    return False, detail or "沒有收到資料"


def run_now(conn, asset_serial: str, *, by: str | None = None) -> list[dict]:
    """同步收一輪（測試與手動重收用）。回每一樣的結果。"""
    import collect_log

    results = []
    for job in JOBS:
        ok, msg = _collect_one(conn, job, asset_serial)
        results.append({"job": job, "label": JOB_LABEL[job], "ok": ok, "message": msg})
    got = [r["label"] for r in results if r["ok"]]
    missed = [f"{r['label']}（{r['message']}）" for r in results if not r["ok"]]
    collect_log.record(
        conn, "post_onboard", asset_serial, not missed,
        f"納管後自動收集：收到 {'、'.join(got) or '無'}"
        + (f"；沒收到 {'、'.join(missed)}" if missed else ""),
        actor=by, extra={"results": results})

    # 納管時先存一份健康基準（2026-09-22 使用者：「納管的時候就先一份」）——
    # 事發時的即時健檢馬上有基準可比，不必等週六排程鋪底。失敗不影響上面的收集。
    try:
        import health_probe
        import health_baseline
        import manage_state

        row = conn.execute(
            "SELECT ip FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
        ip = row["ip"] if row else None
        if ip:
            res = health_probe.check_one(ip, platform=manage_state.collect_platform_of(conn, ip))
            health_baseline.save_snapshot(conn, res, source="onboard")
    except Exception:  # noqa: BLE001 - 基準沒存到不影響納管收集本體
        pass
    return results


def start(db_path, asset_serial: str, *, by: str | None = None) -> bool:
    """在背景收一輪。已經在跑同一台就不重複啟動，回 False。"""
    from db import get_connection

    if not asset_serial:
        return False
    with _lock:
        if asset_serial in _running:
            return False
        _running.add(asset_serial)

    def worker():
        conn = get_connection(db_path)
        try:
            run_now(conn, asset_serial, by=by)
        except Exception:  # noqa: BLE001 - 背景執行緒不可以把例外丟到沒人接的地方
            pass
        finally:
            conn.close()
            with _lock:
                _running.discard(asset_serial)

    threading.Thread(target=worker, daemon=True).start()
    return True
