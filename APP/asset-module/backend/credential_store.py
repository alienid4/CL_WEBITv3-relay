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

#: 憑證類型白名單。winrm／ssh＝主機收集與納管；whatsup／vrops＝外部系統 API 串接；
#: storage＝儲存設備（SAN Brocade／HMC IBM Power）唯讀收集。2026-10-30 使用者：這些帳密都在「收集憑證」設定。
KINDS = ("winrm", "ssh", "whatsup", "vrops", "storage")
KIND_LABELS = {"winrm": "Windows 收集（WinRM）", "ssh": "Linux 自動納管（SSH）", "whatsup": "WhatsUp Gold API",
               "vrops": "Aria Operations（vROps）API", "storage": "儲存設備（SAN／HMC）"}


def valid_kind(kind: str) -> bool:
    return kind in KINDS


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

    scope：這組憑證適用範圍（如網段 10.99.1.0/24 或網域名），用來決定哪台用哪組。
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


def update(conn, name: str, username: str, scope: str | None, note: str | None,
           password: str | None = None, key_path: str = KEY_PATH_DEFAULT) -> bool:
    """改一組已存在的憑證（2026-10-28：憑證要能編輯）。名稱與類型不改（改名＝新增一組再刪舊的，避免各處指定的名稱斷掉）。
    password 留空＝沿用原本的密碼（不用重打）；有給才重新加密。回 False＝查無這組。"""
    row = conn.execute("SELECT id FROM collect_credential WHERE name = ?", (name,)).fetchone()
    if row is None:
        return False
    if password:
        token = _fernet(key_path).encrypt(password.encode()).decode()
        conn.execute("UPDATE collect_credential SET username=?, scope=?, note=?, secret_enc=?, "
                     "updated_at=datetime('now','localtime') WHERE name=?", (username, scope, note, token, name))
    else:
        conn.execute("UPDATE collect_credential SET username=?, scope=?, note=?, "
                     "updated_at=datetime('now','localtime') WHERE name=?", (username, scope, note, name))
    conn.commit()
    return True


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


AUTO = "__auto__"      # 納管時選「改回依網段自動」＝清掉這台指定的憑證


def _pref_table_exists(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='host_credential_pref'").fetchone() is not None


def get_pref(conn, ip: str, kind: str) -> str | None:
    """這台指定要用的憑證名稱（納管時人選的）；憑證已被刪、或類型不對就當沒指定。
    只讀（不建表）：收集端有些連線是唯讀的。"""
    if not _pref_table_exists(conn):
        return None
    row = conn.execute("SELECT name FROM host_credential_pref WHERE ip = ? AND kind = ?", (ip, kind)).fetchone()
    if row is None:
        return None
    ok = conn.execute("SELECT 1 FROM collect_credential WHERE name = ? AND kind = ?", (row["name"], kind)).fetchone()
    return row["name"] if ok else None


def set_pref(conn, ip: str, kind: str, name: str, by: str | None) -> None:
    """記下「這台用哪一組憑證」（納管時從已設定的憑證裡挑的）。之後這台所有收集都走同一組，不會納管用甲、收集又用乙。"""
    conn.execute("CREATE TABLE IF NOT EXISTS host_credential_pref ("
                 " ip TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, set_by TEXT,"
                 " set_at TEXT NOT NULL DEFAULT (datetime('now','localtime')), PRIMARY KEY (ip, kind))")
    conn.execute("INSERT INTO host_credential_pref (ip, kind, name, set_by) VALUES (?,?,?,?) "
                 "ON CONFLICT(ip, kind) DO UPDATE SET name = excluded.name, set_by = excluded.set_by, "
                 "set_at = datetime('now','localtime')", (ip, kind, name, by))


def clear_pref(conn, ip: str, kind: str) -> None:
    if _pref_table_exists(conn):
        conn.execute("DELETE FROM host_credential_pref WHERE ip = ? AND kind = ?", (ip, kind))


def valid_choice(conn, name: str, kind: str) -> bool:
    """人從畫面挑的憑證名稱：必須是已設定的、而且類型對（Windows 納管只能挑 winrm）。"""
    return conn.execute("SELECT 1 FROM collect_credential WHERE name = ? AND kind = ?", (name, kind)).fetchone() is not None


def pick_for_host(conn, ip: str, kind: str | None = None, use_pref: bool = True) -> str | None:
    """依 scope 決定這台該用哪組憑證。回憑證名稱。

    比對規則刻意簡單：scope 是網段前綴（如 "10.99.1."）或留空代表通用。
    先找有 scope 且前綴相符的，再退回通用的——明確的優先於萬用的。

    kind：限定憑證類型（'winrm'／'ssh'…）。Windows 收集要 winrm、Linux 自動納管 bootstrap
    要 ssh——同一台不能拿錯類型的憑證去登。留 None 代表不限類型（維持舊行為）。
    """
    if use_pref and kind is not None:
        pref = get_pref(conn, ip, kind)       # 納管時人指定的優先（之後所有收集都走同一組）
        if pref:
            return pref
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


def resolve_for_kind(conn, kind: str, chosen: str | None = None, legacy: str | None = None) -> str | None:
    """整合服務（WhatsUp／vROps）取用哪一筆憑證。優先序：
    1. 設定裡人挑的名稱（必須存在且類型對）
    2. 舊版固定名稱（legacy，例 integration:whatsup）——舊資料已在憑證庫，不搬也能用
    3. 同類型只有唯一一筆就用它；多筆又沒挑＝None（不猜，避免拿錯組）
    """
    def _ok(n):
        return bool(n) and conn.execute("SELECT 1 FROM collect_credential WHERE name = ? AND kind = ?", (n, kind)).fetchone() is not None
    if _ok(chosen):
        return chosen
    if _ok(legacy):
        return legacy
    rows = conn.execute("SELECT name FROM collect_credential WHERE kind = ? ORDER BY name", (kind,)).fetchall()
    return rows[0]["name"] if len(rows) == 1 else None


def resolve_storage(conn, name: str, ip: str, key_path: str | None = None) -> tuple[str, str]:
    """SAN／HMC 收集挑了一筆已設定的 storage 憑證：回 (username, password)，只在伺服器記憶體裡。
    只能挑 kind='storage'，且目標 IP 要在適用範圍內，否則 CredentialRefused。"""
    row = conn.execute("SELECT kind, scope FROM collect_credential WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise CredentialRefused(f"查無憑證「{name}」")
    if row["kind"] != "storage":
        raise CredentialRefused(f"憑證「{name}」不是儲存設備類型，不能用在 SAN／HMC 收集")
    if not (row["scope"] or "").strip():
        raise CredentialRefused(f"儲存設備憑證「{name}」沒有設定適用範圍（留空＝通用），為避免誤送到別台，不使用；請先填適用的 IP 或網段")
    if not scope_allows(row["scope"], ip):
        raise CredentialRefused(f"憑證「{name}」的適用範圍（{row['scope']}）不含 {ip}，不使用")
    got = get_for_use(conn, name, key_path=key_path or KEY_PATH_DEFAULT)
    if got is None:
        raise CredentialRefused(f"憑證「{name}」解不開（加密金鑰可能已更換），請到系統設定重新輸入密碼")
    return got


class CredentialRefused(Exception):
    """憑證不能用在這台（類型不對／不在適用範圍／解不開）。訊息可直接給使用者看，不含密碼。"""


def scope_allows(scope: str | None, ip: str) -> bool:
    """這組憑證的適用範圍含不含這個 IP。留空＝通用；含「/」當 CIDR；否則當前綴（同 pick_for_host）。"""
    import ipaddress

    sc = (scope or "").strip()
    if not sc:
        return True
    if "/" in sc:
        try:
            return ipaddress.ip_address(ip) in ipaddress.ip_network(sc, strict=False)
        except ValueError:
            return False
    return ip.startswith(sc.rstrip("*"))


def resolve_ssh_for_onboard(conn, name: str, ip: str, key_path: str | None = None) -> tuple[str, str]:
    """手動／批次納管挑了一組已設定的 SSH 憑證：回 (username, password)，只在伺服器記憶體裡。

    只能挑 kind='ssh'，而且目標 IP 要在這組的適用範圍內，否則 CredentialRefused。
    這只是 bootstrap 登入用；人當下打的密碼仍然用完即丟，不會進這裡。
    """
    row = conn.execute("SELECT kind, scope FROM collect_credential WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise CredentialRefused(f"查無憑證「{name}」")
    if row["kind"] != "ssh":
        raise CredentialRefused(f"憑證「{name}」不是 SSH 類型，不能用在 Linux／AIX 納管")
    if not scope_allows(row["scope"], ip):
        raise CredentialRefused(f"憑證「{name}」的適用範圍（{row['scope']}）不含 {ip}，不使用")
    got = get_for_use(conn, name, key_path=key_path or KEY_PATH_DEFAULT)
    if got is None:
        raise CredentialRefused(f"憑證「{name}」解不開（加密金鑰可能已更換），請到系統設定重新輸入密碼")
    return got


def delete(conn, name: str) -> int:
    cur = conn.execute("DELETE FROM collect_credential WHERE name = ?", (name,))
    if _pref_table_exists(conn):          # 指定過這組的台也一併清掉，避免日後同名新憑證悄悄復活舊綁定
        conn.execute("DELETE FROM host_credential_pref WHERE name = ?", (name,))
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
