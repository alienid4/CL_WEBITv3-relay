"""收集用憑證庫：加密儲存 Windows 服務帳號等「排程收集必須反覆使用」的憑證。

## 為什麼需要（使用者 2026-07-19 同意）

Linux 走 SSH 金鑰——而 221 上本來就存著收集私鑰。Windows 走 WinRM 需要帳密，
每次排程收集都要用，不可能每次問人。**「存一組服務帳密」與「存一把私鑰」是同一類的事**，
業界 CMDB（ServiceNow／Lansweeper）都有憑證庫。

## 三條寫死的保護

1. **加密儲存**：密碼用 Fernet（AES128-CBC + HMAC）加密後才進 DB。
   金鑰檔獨立存放（0600、不在 DB 裡、不進版控），DB 被複製走也解不開。
2. **只解密給收集用**：`get_for_use()` 是唯一會回明文的入口，且明文只在記憶體。
   列表／API／診斷包一律只給「有沒有設定」與 metadata，**永不回傳密碼**。
3. **每次使用留稽核**（誰用了哪組、對哪台、成敗），但稽核不含密碼。

## 與「憑證不落地」的界線

一鍵納管（人當下輸入的帳密）＝**用完即丟，絕不進這裡**。
這個庫只放「使用者明確授權、要給排程反覆使用」的服務帳號——兩者不可混。
"""
from __future__ import annotations

import os
import stat

KEY_PATH_DEFAULT = "/opt/webit3/.credential_key"


def _load_or_create_key(path: str = KEY_PATH_DEFAULT) -> bytes:
    """讀加密金鑰；不存在就產生一把（0600）。

    金鑰**刻意不放 DB**：DB 會被備份、被複製到別處，金鑰跟著走就等於沒加密。
    分開存才有意義——備份外流時密碼仍是密文。
    """
    from cryptography.fernet import Fernet

    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read().strip()
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def _fernet(key_path: str = KEY_PATH_DEFAULT):
    from cryptography.fernet import Fernet

    return Fernet(_load_or_create_key(key_path))


def save(conn, name: str, kind: str, username: str, password: str,
         scope: str | None = None, note: str | None = None,
         key_path: str = KEY_PATH_DEFAULT) -> int:
    """存一組憑證。password 進 DB 前先加密。

    scope：這組憑證適用範圍（如網段 192.168.1.0/24 或網域名），用來決定哪台用哪組。
    """
    token = _fernet(key_path).encrypt(password.encode()).decode()
    cur = conn.execute(
        "INSERT INTO collect_credential (name, kind, username, secret_enc, scope, note) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, username=excluded.username, "
        "secret_enc=excluded.secret_enc, scope=excluded.scope, note=excluded.note, "
        "updated_at=datetime('now','localtime')",
        (name, kind, username, token, scope, note),
    )
    conn.commit()
    return cur.lastrowid


def list_public(conn) -> list[dict]:
    """列出憑證——**永不含密碼**，連密文都不給。

    畫面只需要知道「有哪幾組、給誰用、什麼時候更新的」。把密文丟到前端沒有任何用途，
    只是多一個外洩面。
    """
    return [
        {"id": r["id"], "name": r["name"], "kind": r["kind"], "username": r["username"],
         "scope": r["scope"], "note": r["note"], "updated_at": r["updated_at"],
         "has_secret": bool(r["secret_enc"])}
        for r in conn.execute(
            "SELECT id, name, kind, username, secret_enc, scope, note, updated_at "
            "FROM collect_credential ORDER BY name")
    ]


def get_for_use(conn, name: str, key_path: str = KEY_PATH_DEFAULT) -> tuple[str, str] | None:
    """取出可用的 (username, password)。**這是唯一會回明文的入口。**

    呼叫端拿到後只能拿去連線，不可寫入任何地方（DB／log／回傳給前端）。
    解不開時回 None 而不是丟例外——換過金鑰檔是可預期的情況，
    要讓呼叫端能好好報「這組憑證解不開，請重設」。
    """
    from cryptography.fernet import InvalidToken

    row = conn.execute(
        "SELECT username, secret_enc FROM collect_credential WHERE name = ?", (name,)
    ).fetchone()
    if row is None or not row["secret_enc"]:
        return None
    try:
        pw = _fernet(key_path).decrypt(row["secret_enc"].encode()).decode()
    except (InvalidToken, ValueError):
        return None
    return row["username"], pw


def pick_for_host(conn, ip: str, kind: str | None = None) -> str | None:
    """依 scope 決定這台該用哪組憑證。回憑證名稱。

    比對規則刻意簡單：scope 是網段前綴（如 "192.168.1."）或留空代表通用。
    先找有 scope 且前綴相符的，再退回通用的——明確的優先於萬用的。

    kind：限定憑證類型（'winrm'／'ssh'…）。Windows 收集要 winrm、Linux 自動納管 bootstrap
    要 ssh——同一台不能拿錯類型的憑證去登。留 None 代表不限類型（維持舊行為）。
    """
    rows = conn.execute(
        "SELECT name, scope, kind FROM collect_credential ORDER BY name").fetchall()
    generic = None
    for r in rows:
        if kind is not None and r["kind"] != kind:
            continue
        scope = (r["scope"] or "").strip()
        if not scope:
            generic = generic or r["name"]
            continue
        if ip.startswith(scope.rstrip("*")):
            return r["name"]
    return generic


def delete(conn, name: str) -> int:
    cur = conn.execute("DELETE FROM collect_credential WHERE name = ?", (name,))
    conn.commit()
    return cur.rowcount


def audit_use(conn, name: str, target_ip: str, ok: bool, message: str = "") -> None:
    """記錄一次使用。稽核**不含密碼**——只記用了哪組、對哪台、成不成功。"""
    conn.execute(
        "INSERT INTO credential_use_audit (credential_name, target_ip, ok, message) "
        "VALUES (?,?,?,?)",
        (name, target_ip, 1 if ok else 0, message[:300]),
    )
    conn.commit()


# ---- 診斷外掛：只給 metadata，永不給密碼或密文 ----
try:
    import diagnostics

    @diagnostics.register("credentials")
    def _diag(conn) -> dict:
        try:
            creds = list_public(conn)
            recent = [dict(r) for r in conn.execute(
                "SELECT credential_name, target_ip, ok, message, created_at "
                "FROM credential_use_audit ORDER BY id DESC LIMIT 30")]
        except Exception:  # noqa: BLE001
            creds, recent = [], []
        return {"credentials": creds, "recent_use": recent,
                "key_file_present": os.path.exists(KEY_PATH_DEFAULT)}
except ImportError:
    pass
