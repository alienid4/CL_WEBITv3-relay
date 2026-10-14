"""納管失敗清單：哪些機器試過但進不去、為什麼（2026-09-17）。

## 為什麼要有

使用者批次納管 7 台，5 台回「登入被拒——帳號或密碼不對」，接著問：

> 「這些帳號納管失敗，我以後要怎麼查那些納管失敗過，他的 root 密碼可能不是預設密碼」

這是真實的維運問題：那批機器的密碼跟大部分機器不一樣，要單獨處理（找管理員問、
或走 PAM 取密碼）。但納管紀錄（`onboard_audit`）是**一次動作一列**的流水帳——
同一台試過五次就有五列，要從裡面看出「現在還有哪幾台進不去」得自己用眼睛歸戶。

所以這裡做的是**現況**：每台**最後一次**的結果，而且只列現在仍然失敗的。

## 為什麼只看最後一次

一台機器上週失敗、今天成功了，就不該再出現在「待處理」裡——
把歷史失敗混進現況，清單會越積越長，最後沒人看。歷史仍然查得到（納管紀錄那張表沒動）。

## 失敗原因分類

分類決定**下一步做什麼**，所以不能混成一句「失敗」：

| 分類 | 訊息特徵 | 下一步 |
|---|---|---|
| 帳密不對 | Permission denied／登入被拒 | 這台密碼不是預設值——找管理員要或走 PAM |
| 連不上 | timed out／refused／unreachable | 防火牆或機器不在——先確認網路 |
| 不納管 | 不納管（ESXi／設備） | 本來就不該納管，不是問題 |
| 腳本失敗 | stage=execute/verify | 進得去但腳本沒跑完——看輸出 |
| 其他 | 以上皆非 | 看原始訊息 |

⚠️ 分類只看訊息文字，認不出來就歸「其他」並**原樣附上訊息**——
猜錯分類比不分類更糟，那會把人帶去錯的方向。
"""
from __future__ import annotations

REASON_CREDENTIAL = "帳密不對"
REASON_UNREACHABLE = "連不上"
REASON_NOT_ONBOARDABLE = "不納管"
REASON_SCRIPT = "腳本失敗"
REASON_OTHER = "其他"

#: 下一步該做什麼。分類存在的唯一理由就是這個。
NEXT_STEP = {
    REASON_CREDENTIAL: "這台的密碼不是預設值——跟管理員要正確密碼，或走 PAM 取得後再納管",
    REASON_UNREACHABLE: "先確認機器在不在、防火牆有沒有開通（可用「偵測存活」）",
    REASON_NOT_ONBOARDABLE: "本來就不納管（ESXi／設備），不是待辦",
    REASON_SCRIPT: "登入成功但腳本沒跑完——看納管紀錄裡的輸出找原因",
    REASON_OTHER: "看原始訊息判斷",
}

_CREDENTIAL_HINTS = ("permission denied", "登入被拒", "authentication", "帳號或密碼",
                     "password", "auth failed")
_UNREACHABLE_HINTS = ("timed out", "timeout", "refused", "unreachable", "no route",
                      "連不上", "無法連線", "connection reset")
_NOT_ONBOARDABLE_HINTS = ("不納管",)


def classify_reason(stage: str | None, message: str | None) -> str:
    """把失敗訊息歸成「下一步不同」的幾類。認不出來就回「其他」，不硬猜。"""
    msg = (message or "").lower()
    raw = message or ""
    if any(h in raw for h in _NOT_ONBOARDABLE_HINTS):
        return REASON_NOT_ONBOARDABLE
    if any(h in msg or h in raw for h in _CREDENTIAL_HINTS):
        return REASON_CREDENTIAL
    if any(h in msg or h in raw for h in _UNREACHABLE_HINTS):
        return REASON_UNREACHABLE
    if (stage or "") in ("execute", "verify"):
        return REASON_SCRIPT
    return REASON_OTHER


def current_failures(conn, include_resolved: bool = False) -> list[dict]:
    """每台**最後一次**納管結果；預設只回現在仍然失敗的。

    `include_resolved=True` 時連「後來成功了」的也回（附 `resolved` 旗標），
    給「我想看看以前卡過哪幾台」用。
    """
    rows = conn.execute(
        """
        SELECT a.target_ip, a.platform, a.login_user, a.ok, a.stage, a.message,
               a.trigger, a.triggered_by, a.created_at,
               (SELECT COUNT(*) FROM onboard_audit x
                 WHERE x.target_ip = a.target_ip AND x.ok = 0) AS fail_count,
               (SELECT MIN(created_at) FROM onboard_audit x
                 WHERE x.target_ip = a.target_ip AND x.ok = 0) AS first_failed_at
          FROM onboard_audit a
          JOIN (SELECT target_ip, MAX(id) AS last_id
                  FROM onboard_audit GROUP BY target_ip) last
            ON last.last_id = a.id
         ORDER BY a.target_ip
        """
    ).fetchall()

    hw = {r["ip"]: r for r in conn.execute(
        "SELECT ip, asset_serial, hostname, os, environment, physical_location, "
        "usage_unit, user_name, custodian, collect_ok FROM hardware "
        "WHERE ip IS NOT NULL AND ip != ''")}

    out = []
    for r in rows:
        resolved = bool(r["ok"])
        if resolved and not include_resolved:
            continue
        h = hw.get(r["target_ip"])
        reason = "" if resolved else classify_reason(r["stage"], r["message"])
        out.append({
            "ip": r["target_ip"],
            "asset_serial": h["asset_serial"] if h else None,
            "hostname": h["hostname"] if h else None,
            "os": h["os"] if h else None,
            "environment": h["environment"] if h else None,
            "physical_location": h["physical_location"] if h else None,
            # 要找誰問密碼——這才是這張清單的用途
            "department": h["usage_unit"] if h else None,
            "contact": h["user_name"] if h else None,
            "custodian": h["custodian"] if h else None,
            "platform": r["platform"],
            "login_user": r["login_user"],
            "stage": r["stage"],
            "message": r["message"],
            "reason": reason,
            "next_step": NEXT_STEP.get(reason, ""),
            "fail_count": r["fail_count"] or 0,
            "first_failed_at": r["first_failed_at"],
            "last_tried_at": r["created_at"],
            "last_trigger": r["trigger"],
            "last_by": r["triggered_by"],
            "resolved": resolved,
            # 納管失敗但收集帳號其實通得了（以前佈過）——這種不是真的待辦
            "collect_ok": (h["collect_ok"] if h else None),
            "registered": h is not None,
        })
    return out


def summary(conn) -> dict:
    """依原因分組的台數。畫面上要一眼看出「幾台是密碼問題」。"""
    items = current_failures(conn)
    by_reason: dict[str, int] = {}
    for i in items:
        by_reason[i["reason"]] = by_reason.get(i["reason"], 0) + 1
    return {
        "total": len(items),
        "by_reason": by_reason,
        # 密碼不是預設值的那批——使用者真正要的就是這個數字
        "credential": by_reason.get(REASON_CREDENTIAL, 0),
    }


EXPORT_HEADERS = ["IP", "主機名稱", "資產序號", "機房", "環境", "作業系統",
                  "使用單位（部門）", "使用者（窗口）", "保管者",
                  "失敗原因", "下一步", "最後訊息", "登入帳號", "階段",
                  "失敗次數", "第一次失敗", "最後一次嘗試"]


def export_rows(conn) -> list[list]:
    return [[i["ip"], i["hostname"], i["asset_serial"], i["physical_location"],
             i["environment"], i["os"], i["department"], i["contact"], i["custodian"],
             i["reason"], i["next_step"], i["message"], i["login_user"], i["stage"],
             i["fail_count"], i["first_failed_at"], i["last_tried_at"]]
            for i in current_failures(conn)]
