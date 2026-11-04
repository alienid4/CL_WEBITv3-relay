"""預檢（只看不動）：登進去問這台是什麼、能不能納管——**不改這台任何東西**（v1.468.9）。

2026-10-19 使用者（公司機）：怕把網路設備、或不是 RHEL／CentOS／Debian 的機器納管進來。
「納管前資訊」只看資料庫；這裡用人給的帳密**唯讀登入**一次，跑的是一鍵納管前同一支探測
（onboard_engine.probe_target：uname、id -u、有沒有 sudo、vmware -v、VCSA 標記、os-release、
設備標記——全部是讀檔／存在測試），結論用同一份白名單（onboard_platform_rules.json，
platform_refusal）與 VMware 天條（vmware_refusal）。所以預檢說「可以納管」，一鍵納管就不會被平台擋。

2026-10-20：結論改成**直接呼叫一鍵納管自己的判斷**（onboard_engine.onboard_decision）——預檢＝預告
一鍵納管會做什麼（含 Windows 會走 Windows 那條），不再自己另寫一份。

不做的事：不建帳號、不佈金鑰、不改 sudoers、不跑任何納管腳本、不寫 onboard_audit（沒有納管）。
密碼只在這次呼叫的記憶體裡，不進 DB／log／回應。

自動停止（同一次納管的規矩）：還沒有任何一台登入成功、就出現「登入被拒」→ 不再開始新的一台，
沒試的標「未執行（已停止）」——拿同一組錯的密碼去打全部，容易被當成暴力破解、或把帳號鎖住。
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor

#: 一次最多幾台（同步請求；4 台並行 × 每台最壞約 50 秒）
MAX_TARGETS = 20
CONCURRENCY = 4
#: 「登入被拒」——觸發自動停止
_AUTH_REJECT_RE = re.compile(r"登入被拒|帳號或密碼不對|Permission denied|Authentication failed", re.I)

V_OK = "可以納管"
V_NO = "不會納管"
V_LOGIN = "登不進去"
V_STOPPED = "未執行（已停止）"
#: 平台可以，但這組帳號納管不了（一鍵納管會以「不是 root 又沒有 sudo」拒絕）——跟 onboard() 同一個判斷
V_NEED_ROOT = "需改用 root"
VERDICTS = (V_OK, V_NEED_ROOT, V_NO, V_LOGIN, V_STOPPED)


#: 一鍵納管的判斷代碼 → 預檢結論（判斷本身在 onboard_engine.onboard_decision，這裡只翻譯）
_CODE_VERDICT = {"ok": V_OK, "need_root": V_NEED_ROOT, "probe_failed": V_LOGIN,
                 "vmware": V_NO, "platform": V_NO, "unknown_os": V_NO, "args": V_NO}


def verdict(probe: dict, *, platform: str = "linux", username: str = "",
            collector_ip: str | None = None, accounts: dict | None = None) -> dict:
    """探測結果 → 結論（純函式）。回 {verdict, onboardable, reason, would, pretty_name, ...}。

    2026-10-20 起**不自己判斷**：呼叫 onboard_engine.onboard_decision——一鍵納管在任何副作用
    之前用的同一組函式、同樣順序。所以 verdict 就是「一鍵納管會做什麼」：
    Windows 認得出來 → 一鍵納管會走 Windows 那條，這裡就照實說「會用 Windows 方式納管」。

    platform：一鍵納管畫面選的平台（預設 linux；選 linux／aix 都會被探測結果改正）。
    collector_ip：None＝略過「連線前的參數檢查」（單元測試）；API 一律帶設定裡的值。
    accounts：{平台: 收集帳號名}，只用來把原因講具體。
    """
    import onboard_engine as oe

    osr = probe.get("os_release") or {}
    base = {
        "uname": probe.get("os") or None,
        "pretty_name": osr.get("PRETTY_NAME") or probe.get("legacy_release") or None,
        "os_id": osr.get("ID") or None,
        "version_id": osr.get("VERSION_ID") or None,
        "uid": probe.get("uid"),
        "is_root": probe.get("uid") == 0,
        "has_sudo": bool(probe.get("has_sudo")),
        "appliance": list(probe.get("appliance") or []),
    }
    accounts = accounts or {}
    if probe.get("error") and not (collector_ip is not None and oe.onboard_args_refusal(
            platform, collector_ip, accounts.get(platform))):
        # 登不進去：一鍵納管第一步（登入）就會失敗，什麼都不會做——畫面選 Windows 也一樣
        d = {"ok": False, "code": oe.DECIDE_PROBE_FAILED, "stage": "connect",
             "message": probe["error"], "platform": platform}
    elif collector_ip is None:
        d = (oe.decide_after_probe(platform, probe, username) if platform != "windows"
             else oe.decide_windows_selected(probe))
    else:
        d = oe.onboard_decision(platform, probe, username, collector_ip, accounts.get(platform))
    v = _CODE_VERDICT[d["code"]]
    plat = d["platform"]
    out = {**base, "verdict": v, "onboardable": d["ok"], "auth_rejected": False,
           # 一鍵納管會走哪條（ok 時）／在哪一關停下（拒絕時）——跟 onboard() 回的 stage 同一個值
           "would": {"onboard": d["ok"], "platform": plat, "code": d["code"], "stage": d["stage"]}}
    if d["code"] == oe.DECIDE_PROBE_FAILED:
        err = probe.get("error") or d["message"]
        return {**out, "reason": err, "auth_rejected": bool(_AUTH_REJECT_RE.search(err))}
    if d["code"] == oe.DECIDE_UNKNOWN_OS:
        return {**out, "reason": "登得進去，但認不出平台（不是 POSIX shell，多半是網路設備或設備）"
                                 "——一鍵納管會停在探測、不納管"}
    if d["code"] == oe.DECIDE_NEED_ROOT:
        return {**out, "reason": "平台可以納管，但" + d["message"]}
    if not d["ok"]:
        return {**out, "reason": d["message"]}
    if plat == "windows":
        acct = accounts.get("windows") or oe.DEFAULT_ACCOUNT
        return {**out, "reason": f"會用 Windows 方式納管：SSH 登入後以 PowerShell 建本機帳號 {acct}、"
                                 "佈收集金鑰（需要 OpenSSH Server，登入帳號要是系統管理員）"}
    return {**out, "reason": f"{base['pretty_name'] or plat}，平台白名單內"
                             + ("（登入身分是 root）" if base["is_root"] else "（有 sudo）")}


def _is_ip(s: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def run(ips: list[str], username: str, password: str, *, runner=None,
        concurrency: int = CONCURRENCY, platform: str = "linux",
        collector_ip: str | None = None, accounts: dict | None = None) -> dict:
    """對這批 IP 各唯讀登入一次。回 {results: [...], auto_stopped, auth_rejected}。runner 可注入（測試）。"""
    import onboard_engine as oe

    targets = [ip.strip() for ip in dict.fromkeys(ips or []) if ip and ip.strip()]
    bad = [ip for ip in targets if not _is_ip(ip)]
    if bad:
        raise ValueError(f"只接受 IP 位址：{'、'.join(bad[:5])}")
    results: dict[str, dict] = {}
    lock = threading.Lock()
    state = {"stop": False, "ok": 0}
    queue = list(targets)

    args_refused = (collector_ip is not None and
                    oe.onboard_args_refusal(platform, collector_ip, (accounts or {}).get(platform)))

    def worker():
        while True:
            with lock:
                if state["stop"] or not queue:
                    return
                ip = queue.pop(0)
            try:
                if args_refused:
                    # 一鍵納管在連線前就會停（收集器位址／帳號名／平台）——預檢也不登入
                    v = verdict({"os": "", "uid": None, "has_sudo": False}, platform=platform,
                                username=username, collector_ip=collector_ip, accounts=accounts)
                    with lock:
                        results[ip] = {"ip": ip, **v}
                    continue
                probe = oe.probe_target(ip, username, password, runner=runner)
                v = verdict(probe, platform=platform, username=username,
                            collector_ip=collector_ip, accounts=accounts)
            except Exception as exc:  # noqa: BLE001 - 一台出錯不擋整批；只寫例外型別（訊息可能帶連線參數）
                v = verdict({"os": "", "uid": None, "has_sudo": False,
                             "error": f"預檢出錯（{type(exc).__name__}）"}, platform=platform)
            with lock:
                results[ip] = {"ip": ip, **v}
                if v["verdict"] != V_LOGIN:
                    state["ok"] += 1
                elif v["auth_rejected"] and state["ok"] == 0:
                    state["stop"] = True

    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(targets) or 1))) as ex:
        for _ in range(max(1, min(concurrency, len(targets) or 1))):
            ex.submit(worker)
    out = []
    for ip in targets:
        out.append(results.get(ip) or {
            "ip": ip, "verdict": V_STOPPED, "onboardable": False, "auth_rejected": False,
            "reason": "前面幾台帳密被拒，已自動停止（避免帳號被鎖）", "uname": None, "pretty_name": None,
            "os_id": None, "version_id": None, "uid": None, "is_root": False, "has_sudo": False,
            "appliance": [], "would": None})
    rejected = sum(1 for r in out if r.get("auth_rejected"))
    return {"results": out, "auto_stopped": any(r["verdict"] == V_STOPPED for r in out),
            "auth_rejected": rejected,
            "counts": {k: sum(1 for r in out if r["verdict"] == k) for k in VERDICTS}}
