"""帳號稽核規則引擎：把法規條文變成可判定的紅綠燈。

規格依據：AI/資訊戰情室重建案/帳號盤點_稽核需求與競品分析.md
法規來源：金管會證期局查核重點、證券商公會資通系統安全防護基準自律規範。

## 設計原則

1. **門檻可設定，不寫死**。稽核要求會隨年度變（90 天可能改 60 天），
   寫死在程式等於每次改規定都要發版。門檻存 app_settings。
2. **分類決定適用範圍**。服務帳號本來就不該有密碼到期，
   拿真人的規則去判它會製造大量誤報——誤報是稽核工具最大的死因。
3. **拿不到資料 ≠ 通過**。權限不足導致欄位是 None 時，判定為 `unknown` 而不是 `pass`。
   把「沒查到」講成「查過沒問題」是最危險的假綠燈。
4. **每條 finding 都帶得走**：規則代號、法規出處、白話說明、涉及帳號、證據欄位。
   稽核當天要的是可交付的證據，不是叫稽核員坐旁邊看畫面。
"""
from __future__ import annotations

from datetime import datetime

# 預設門檻。存 app_settings 後以 DB 值優先（UI 可改、改了馬上生效）。
DEFAULT_THRESHOLDS = {
    "acct_pw_max_days": 90,        # 密碼最長效期（法規：至少每 3 個月變更）
    "acct_idle_days": 180,         # 閒置多久算閒置帳號（法規：至少每半年審查並停用閒置帳號）
    "acct_review_days": 180,       # 權限覆核週期（法規：至少每半年）
}

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def get_thresholds(conn) -> dict:
    from db import get_setting

    out = {}
    for k, v in DEFAULT_THRESHOLDS.items():
        raw = get_setting(conn, k, str(v))
        try:
            out[k] = int(raw)
        except (TypeError, ValueError):
            out[k] = v
    return out


import re

# lastlog 在 C 語系下的樣子：`pts/0 192.0.2.5 Mon Jul 20 09:12:03 +0800 2026`
# 只抓「月 日 …… 年」，不要求整串完全吻合——欄位數會因為有沒有 From 欄而變動。
# ⚠️ 時區 `+0800` 要先剝掉再抓年份，否則會把 0800 當成西元 800 年，
# 算出來「1224 年沒登入」——荒謬到不會有人相信，但它會安靜地變成一條假紅燈。
_TZ_RE = re.compile(r"[+\-]\d{4}\b")
_LASTLOG_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\b.*?\b(\d{4})\b"
)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def _parse_date(s: str | None):
    """日期字串 → datetime。解不出來回 None（→ unknown，不當成通過）。

    吃兩種來源：chage 的 `Jul 01, 2026`，以及 lastlog 那種夾在一整行裡的
    `Mon Jul 20 09:12:03 +0800 2026`。採集端已強制 LC_ALL=C，所以只需認英文。
    """
    if not s:
        return None
    s = s.strip()
    if s.lower() in ("never", "從不", "永不") or "從未登入過" in s:
        return "never"
    for fmt in ("%b %d, %Y", "%Y-%m-%d", "%d/%m/%Y", "%Y年%m月%d日"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    m = _LASTLOG_RE.search(_TZ_RE.sub(" ", s))
    if m:
        try:
            return datetime(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            return None
    return None


def _days_since(dt) -> int | None:
    if not isinstance(dt, datetime):
        return None
    return (datetime.now() - dt).days


# ---- 規則本體。每條回 (verdict, detail)；verdict: pass / fail / unknown / n_a ----

def _r2_password_age(acc, th):
    """R2 密碼至少每 3 個月變更。真人與標準管理帳號都要管——
    mgmt 是特權帳號（常帶 NOPASSWD:ALL），密碼不輪替是更嚴重的稽核缺失。"""
    if acc["kind"] not in ("human", "mgmt"):
        return "n_a", "非真人/管理帳號不適用密碼效期"
    if acc.get("pw_status") == "locked":
        return "n_a", "帳號已鎖定"
    d = _parse_date(acc.get("pw_last_change"))
    if d == "never":
        return "fail", "從未設定過密碼"
    days = _days_since(d)
    if days is None:
        return "unknown", "取不到上次改密碼日期（需 root）"
    if days > th["acct_pw_max_days"]:
        return "fail", f"已 {days} 天（門檻 {th['acct_pw_max_days']} 天）"
    return "pass", f"{days} 天前換過"


def _r2b_password_never_expires(acc, th):
    """密碼永不過期——繞過 R2 的常見手法。真人與標準管理帳號都要管。"""
    if acc["kind"] not in ("human", "mgmt"):
        return "n_a", "非真人/管理帳號不適用"
    mx = acc.get("pw_max_days")
    if mx is None:
        return "unknown", "取不到密碼效期設定（需 root）"
    try:
        n = int(str(mx).strip())
    except (TypeError, ValueError):
        return "unknown", f"效期設定無法判讀：{mx}"
    if n >= 99999 or n <= 0:
        return "fail", f"maxdays={n}"          # 項目欄已寫「密碼永不過期」，判定只給證據值
    return "pass", f"maxdays={n}"


def _r5_idle(acc, th):
    """R5 閒置帳號應停用。

    ⚠️ 只判真人帳號。實測 221 第一輪把 halt／shutdown／sync 也判成「從未登入」——
    那些是系統帳號，shell 是 /sbin/halt 之類（不是 nologin，所以 can_login 判 True），
    但它們本來就不是拿來登入的，從未登入是正常狀態。
    這種誤報一多，真正該看的那幾條就會被淹掉。
    """
    if acc["kind"] == "mgmt":
        # 標準管理帳號全機隊佈署、只在需要時登入——大部分主機上「從未登入」是正常的。
        # 套閒置規則會在上百台上狂噴誤報，把真正該清的閒置真人帳號淹掉。
        return "n_a", "標準管理帳號（全機隊佈署，未使用不等於閒置）"
    if acc["kind"] != "human":
        return "n_a", "非真人帳號不適用閒置判定"
    if not acc.get("can_login"):
        return "n_a", "無登入 shell"
    if acc.get("pw_status") == "locked":
        return "pass", "已鎖定（等同停用）"
    if acc.get("never_logged_in"):
        return "fail", "從未登入過"
    # Debian 13 等沒有 lastlog 的系統只能用 last，它**只列登入過的人**——
    # 帳號不在裡面不代表從未登入，只代表 wtmp 保存期（通常一個月）內沒紀錄。
    # 在這裡編造確定性，會讓一堆正常帳號被誤報成該清掉的殘留帳號。
    if acc.get("login_source") == "last" and not acc.get("login_known"):
        return "unknown", ("此系統無 lastlog（只能查 wtmp），保存期內查無登入紀錄，"
                           "無法確認是否從未登入")
    d = _parse_date(acc.get("last_login"))
    days = _days_since(d)
    if days is None:
        return "unknown", "取不到最後登入時間"
    if days > th["acct_idle_days"]:
        return "fail", f"{days} 天未登入（門檻 {th['acct_idle_days']} 天）"
    return "pass", f"{days} 天前登入過"


def _r9_default_account(acc, th):
    """R9 系統預設帳號應改密碼或停用。"""
    if acc["kind"] != "default":
        return "n_a", "非預設帳號"
    if acc.get("pw_status") == "locked":
        return "pass", "預設帳號已鎖定"
    if not acc.get("can_login"):
        return "pass", "無登入 shell"
    if acc.get("pw_status") is None:
        return "unknown", "取不到密碼狀態（需 root）"
    return "fail", "仍可登入（未鎖定）"


def _empty_password(acc, th):
    if acc.get("pw_status") is None:
        return "unknown", "取不到密碼狀態（需 root）"
    if acc.get("pw_status") == "empty":
        return "fail", "空密碼"
    return "pass", "已設密碼"


def _uid_zero(acc, th):
    """UID 0 的非 root 帳號是經典後門，稽核必查。"""
    if acc["uid"] != 0:
        return "n_a", "非 UID 0"
    if acc["username"] == "root":
        return "pass", "root 本身"
    return "fail", "UID=0（等同 root）"


def _sudo_nopasswd(acc, th):
    """NOPASSWD 等於無痕提權——特權帳號認定與每日覆核（R6）的重點。"""
    if not acc.get("is_sudoer"):
        return "n_a", "非 sudoer"
    if acc.get("sudo_nopasswd"):
        return "fail", "NOPASSWD"
    return "pass", "sudo 需驗證密碼"


def _authorized_keys(acc, th):
    """免密碼就能進來的人。密碼政策完全管不到它，稽核最常漏的一塊。"""
    n = acc.get("authorized_keys")
    if n is None:
        return "unknown", "取不到 authorized_keys（需 root）"
    if n > 0 and acc["kind"] == "default":
        return "fail", f"{n} 把免密碼金鑰"
    if n > 0:
        return "pass", f"{n} 把授權金鑰（正常運維，應納入覆核）"
    return "pass", "無授權金鑰"


RULES = [
    {"id": "R2", "label": "密碼逾期未更換", "severity": "medium",
     "law": "證券商公會自律規範：密碼至少每 3 個月變更", "fn": _r2_password_age},
    {"id": "R2b", "label": "密碼永不過期", "severity": "high",
     "law": "同 R2（設定永不過期即規避該要求）", "fn": _r2b_password_never_expires},
    {"id": "R5", "label": "閒置帳號未停用", "severity": "medium",
     "law": "至少每半年審查帳號適切性並停用閒置帳號", "fn": _r5_idle},
    {"id": "R9", "label": "預設帳號仍可登入", "severity": "high",
     "law": "系統預設帳號應更改密碼或停用", "fn": _r9_default_account},
    {"id": "A1", "label": "空密碼帳號", "severity": "high",
     "law": "身分驗證應強制最低密碼複雜度", "fn": _empty_password},
    {"id": "A2", "label": "UID 0 非 root 帳號", "severity": "high",
     "law": "最小權限原則；特權帳號應定期檢視", "fn": _uid_zero},
    {"id": "A3", "label": "sudo NOPASSWD", "severity": "medium",
     "law": "特權帳號使用應可追溯並覆核", "fn": _sudo_nopasswd},
    {"id": "A4", "label": "免密碼登入金鑰", "severity": "medium",
     "law": "存取控制應涵蓋所有登入途徑", "fn": _authorized_keys},
]


def password_expiry_status(acc: dict) -> str:
    """密碼到期狀態（給畫面明講「過期/未過期」用，不只寫項目名稱）。

    回：never（永不過期）/ expired（已過期，該換沒換）/ valid（未過期）/
        na（非真人/管理帳號、不適用）/ unknown（需 root 查不到）。
    「已過期」＝距上次改密碼已超過最長效期。
    """
    if acc.get("kind") not in ("human", "mgmt"):
        return "na"
    mx = acc.get("pw_max_days")
    if mx is None:
        return "unknown"
    try:
        n = int(str(mx).strip())
    except (TypeError, ValueError):
        return "unknown"
    if n >= 99999 or n <= 0:
        return "never"
    d = _parse_date(acc.get("pw_last_change"))
    if d == "never":
        return "expired"
    days = _days_since(d)
    if days is None:
        return "unknown"
    return "expired" if days > n else "valid"


def evaluate(accounts: list[dict], thresholds: dict) -> list[dict]:
    """對一批帳號跑全部規則，回傳 finding 清單（只回 fail 與 unknown）。

    pass 不回傳——稽核要看的是例外。但 unknown 一定要回，
    因為「沒查到」需要有人去補權限，不能被當成沒事。
    """
    findings = []
    for acc in accounts:
        for rule in RULES:
            verdict, detail = rule["fn"](acc, thresholds)
            if verdict in ("pass", "n_a"):
                continue
            findings.append({
                "rule_id": rule["id"],
                "label": rule["label"],
                "severity": rule["severity"] if verdict == "fail" else "low",
                "verdict": verdict,
                "law": rule["law"],
                "username": acc["username"],
                "kind": acc["kind"],
                "detail": detail,
            })
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9),
                                 f["rule_id"], f["username"]))
    return findings
