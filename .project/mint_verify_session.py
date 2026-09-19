"""在 221 上開一個驗證用 session（不碰使用者密碼）。

用途：AI 要逐頁檢查登入後的畫面（S15 語意稽核），但不應該接觸使用者的密碼。
既然已經有該機 root 與資料庫存取權，直接建立一筆可稽核、可撤銷的 session
比讓密碼出現在對話紀錄裡安全得多。

## 為什麼是兩把鑰匙

| | 常駐（預設） | 瀏覽器用（--browser） |
|---|---|---|
| 效期 | 一年 | 24 小時 |
| token 值 | **永不列印**，寫進 TOKEN_FILE（0600） | 直接印出來 |
| 給誰用 | 221 上的 curl，`$(cat 檔案)` 取值 | 貼進瀏覽器 cookie |

分成兩把的理由：瀏覽器工具沒辦法「從檔案讀 cookie」，token 值一定會出現在工具
呼叫裡＝進對話紀錄，而對話紀錄是長期保存的。所以長效那把絕不進瀏覽器；要用
瀏覽器就另開一把短命的，印出來也無所謂，隔天自己死。

    curl -s -b "session_token=$(cat /opt/webit3/.verify_token)" localhost:3000/

⚠️ cookie 名稱是 `session_token`（auth.py 的 SESSION_COOKIE_NAME），不是 `session`。
寫錯的話後端一律當未登入：API 回「未登入或登入已過期」、頁面 302 導登入，
看起來跟 token 失效一模一樣，很容易誤判成 session 沒建成功。

用法：
    python mint_verify_session.py             # 建立/覆寫常駐 session（一年）
    python mint_verify_session.py --hours 2   # 常駐那把改用別的效期
    python mint_verify_session.py --show      # 連常駐 token 的值一起印（少用）
    python mint_verify_session.py --browser   # 另開 2 小時瀏覽器用 token 並印出
    python mint_verify_session.py --revoke    # 兩把一起撤銷
"""
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "/opt/webit3/src/APP/asset-module/backend")

import db  # noqa: E402

MARK = "aiverify"                              # 常駐 token 前綴，方便事後辨識與整批撤銷
BROWSE_MARK = "aibrowse"                       # 瀏覽器用短期 token 前綴
TOKEN_FILE = "/opt/webit3/.verify_token"       # 不在 src/ 底下＝不會被 git 掃到
DEFAULT_HOURS = 24 * 365                       # 常駐＝一年
BROWSE_HOURS = 24
PROD_DB = "/opt/webit3/data/asset.db"          # systemd 用 ASSET_DB_PATH 指定的那顆

# ⚠️ db.py 的預設路徑是 backend/data/asset.db（本機開發用），服務實際跑的是 PROD_DB
# ——靠 systemd 的 Environment=ASSET_DB_PATH 覆蓋。這支腳本是手動執行、沒有那個環境
# 變數，若不補就會連到不存在的預設路徑，而 SQLite 會**無聲建一顆空 DB**，然後在
# 「no such table: users」才炸——訊息完全不指向真正的原因（2026-07-18 實際踩過）。
os.environ.setdefault("ASSET_DB_PATH", PROD_DB)

db_path = db.get_db_path()
if not os.path.exists(db_path):
    print(f"!! 找不到資料庫 {db_path}——拒絕執行，以免 SQLite 無聲建一顆空的。")
    print("   若不是在 221 上跑，請自行設定 ASSET_DB_PATH。")
    raise SystemExit(1)

conn = db.get_connection()


def _expires_after(hours: int) -> str:
    """⚠️ 必須回傳 UTC：auth.py 用 datetime.now(timezone.utc) 寫入也用 UTC 比對。
    寫成本地時間會讓 session 立刻被判定過期（本專案時間欄位一律本地，唯獨
    sessions 例外，見 decisions.json T6）。"""
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def _issue(prefix: str, hours: int, user_id: int) -> str:
    """清掉同前綴的舊 token 再發一把新的，避免 sessions 表長出孤兒。"""
    conn.execute("DELETE FROM sessions WHERE token LIKE ?", (prefix + "%",))
    conn.commit()
    token = prefix + secrets.token_urlsafe(24)
    db.create_session(conn, token, user_id, _expires_after(hours))
    return token


if "--revoke" in sys.argv:
    n = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE token LIKE ? OR token LIKE ?",
        (MARK + "%", BROWSE_MARK + "%"),
    ).fetchone()[0]
    conn.execute(
        "DELETE FROM sessions WHERE token LIKE ? OR token LIKE ?",
        (MARK + "%", BROWSE_MARK + "%"),
    )
    conn.commit()
    conn.close()
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    print(f"已撤銷 {n} 筆驗證用 session，並刪除 {TOKEN_FILE}")
    raise SystemExit(0)

user = conn.execute("SELECT id, username FROM users ORDER BY id LIMIT 1").fetchone()
if user is None:
    print("!! 沒有任何使用者帳號")
    raise SystemExit(1)

if "--browser" in sys.argv:
    # 短命、會被印出來、不碰常駐那把
    token = _issue(BROWSE_MARK, BROWSE_HOURS, user["id"])
    conn.close()
    print(f"瀏覽器用 session：帳號={user['username']}，{BROWSE_HOURS} 小時後自動失效")
    print(f"cookie 名稱：session_token")
    print("TOKEN_BEGIN")
    print(token)
    print("TOKEN_END")
    raise SystemExit(0)

hours = DEFAULT_HOURS
if "--hours" in sys.argv:
    hours = int(sys.argv[sys.argv.index("--hours") + 1])

token = _issue(MARK, hours, user["id"])
conn.close()

# 先以 0600 建立再寫入，避免有一瞬間是 world-readable
fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(token)

print(f"已建立常駐 session：帳號={user['username']}，效期 {hours} 小時（約 {hours // 24} 天）")
print(f"token 已寫入 {TOKEN_FILE}（0600），指紋 …{token[-6:]}")
print(f'用法：curl -s -b "session_token=$(cat {TOKEN_FILE})" localhost:3000/')
if "--show" in sys.argv:
    print("TOKEN_BEGIN")
    print(token)
    print("TOKEN_END")
