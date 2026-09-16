"""匯出檔加密：只有持私鑰的那台解得開（決策 SEC5）。

## 這片在解什麼問題

使用者 2026-09-08：「我目前匯出 100M，要寄 MAIL 不實際，這可以壓縮嗎」，
接著：「B（加密），我們是不是可以做到內建加密器，只有我們的系統才能解」。

檔案裡有**全公司主機清單、IP、主機名、帳號名、業務系統歸屬**。寄錯人、
信箱被入侵、或被轉寄出去，那就是整份資產清冊外流。

## 為什麼是公鑰，不是共用一把密碼

共用一把對稱金鑰的話，那把金鑰要從公司機送到 221——**用 email 送就跟直接
寄密碼一樣**，等於沒加密。

公鑰**不需要保密**（它只能加密、不能解密），可以直接貼在信裡、寫進設定、
甚至進版控都無所謂。所以整個流程**不需要傳任何秘密**。

附帶好處：公司機不持有私鑰，所以就算公司機被入侵，攻擊者也解不開任何
過去寄出去的備份。

## 為什麼金鑰不寫在程式裡

`APP/` 底下的原始碼會被打包進**公開的 relay repo**。寫死等於金鑰公開；
就算不公開，任何拿到程式的人都能解。稽核會判定「等同未加密」——
那是自己騙自己（天條：解釋不了就換做法）。

私鑰存**檔案**（0600、不進 DB、不進版控），跟收集私鑰同一套規矩。

## 不自己發明演算法

全部用 `cryptography` 的標準元件（該套件已經是相依，`credential_store` 在用，
公司離線機也已經裝好 49.0.0，不需要臨時裝東西）：

    X25519 金鑰交換  →  HKDF-SHA256 導出金鑰  →  AES-256-GCM 加密

這是業界標準的 hybrid 做法：公鑰加密不適合直接處理大檔，所以用臨時金鑰對做
交換得到一把對稱金鑰，資料本體走 AES-GCM。GCM 自帶完整性驗證——檔案被改過
會解密失敗，而不是安靜地解出垃圾。

## 每次匯出的密文都不一樣，那是正確的

每次都產生新的臨時金鑰對。**不要為了「看起來一致」去固定它**——
固定等於所有匯出共用同一把金鑰，破一次就全破。

## 先壓再加密

順序不能反：加密後的資料是亂數，壓不動。實測 221 的 45MB 資料庫 gzip 後
4.1MB（9%）。

（壓縮＋加密在某些情境會透過長度洩漏資訊（CRIME/BREACH），但那需要攻擊者
能反覆注入內容並觀察長度變化；這裡是一次性的整庫匯出，不適用。）

## 檔案格式：自己說得出自己是什麼

    b"WEBIT3ENC\\x01"   10 bytes  魔術字＋版本
    收件人指紋           8 bytes   sha256(公鑰) 前 8 bytes
    臨時公鑰            32 bytes
    nonce               12 bytes
    密文＋GCM tag        其餘

指紋是為了讓還原端能明確講「**這個檔不是加密給這台的**」，而不是丟一個
看不懂的解密失敗——那兩件事要做的處理完全不同。
"""
from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path

MAGIC = b"WEBIT3ENC\x01"
FP_LEN = 8
EPK_LEN = 32
NONCE_LEN = 12
HEADER_LEN = len(MAGIC) + FP_LEN + EPK_LEN + NONCE_LEN

#: 私鑰檔預設位置。跟收集私鑰放同一個目錄、同一套權限規矩。
#: **只有收件端（221）需要這個檔**；公司機不該有。
#:
#: ⚠️ 用 `private_key_path()` 取值，不要把這個常數直接當預設參數——
#: 預設參數是**定義時**綁死的，之後改這個變數不會生效，等於路徑不可設定。
PRIVATE_KEY_DEFAULT = "/opt/webit3/data/.export_private_key"

#: 環境變數覆寫（跟 onboard_engine 的 WEBIT3_KNOWN_HOSTS 同一套慣例）
PRIVATE_KEY_ENV = "WEBIT3_EXPORT_KEY"


def private_key_path(explicit: str | None = None) -> str:
    """呼叫當下才決定私鑰在哪：明確指定 > 環境變數 > 預設值。"""
    return explicit or os.environ.get(PRIVATE_KEY_ENV) or PRIVATE_KEY_DEFAULT

#: 公鑰存在 app_settings 的哪個 key（來源端用）。公鑰不需要保密。
PUBLIC_KEY_SETTING = "export_public_key"

_HKDF_INFO = b"webit3 export v1"


def _cry():
    """延後 import：這支模組被 api.py 匯入時，沒有加密需求的路徑不用付這個成本。"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey, X25519PublicKey)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return hashes, serialization, X25519PrivateKey, X25519PublicKey, AESGCM, HKDF


def fingerprint(public_key_raw: bytes) -> bytes:
    """公鑰指紋（前 8 bytes）。用來回答「這個檔是加密給誰的」。"""
    return hashlib.sha256(public_key_raw).digest()[:FP_LEN]


def fingerprint_hex(public_key_raw: bytes) -> str:
    return fingerprint(public_key_raw).hex()


# ---------------------------------------------------------------------------
# 金鑰
# ---------------------------------------------------------------------------

def generate_keypair(private_path: str | None = None) -> dict:
    """在收件端（221）產生金鑰對。私鑰落地 0600，公鑰回傳給人複製。

    **已經有私鑰就不覆蓋**——覆蓋等於把過去所有加密備份變成永遠打不開的垃圾，
    而且是無聲的：畫面會顯示「產生成功」，直到某天要還原才發現。
    要換金鑰必須是人明確刪掉舊檔，那一步逼他想清楚舊備份怎麼辦。
    """
    _, serialization, X25519PrivateKey, _, _, _ = _cry()
    private_path = private_key_path(private_path)
    p = Path(private_path)
    if p.exists():
        raise ValueError(
            f"私鑰已經存在（{private_path}）。**不覆蓋**——覆蓋之後過去所有加密匯出"
            f"就永遠打不開了。真的要換金鑰，請先確認舊的加密備份都已經還原或不再需要，"
            f"再手動刪除該檔案。")
    priv = X25519PrivateKey.generate()
    raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())
    p.parent.mkdir(parents=True, exist_ok=True)
    # 先建檔再寫：用 0o600 開檔，避免「先用預設權限建立、再 chmod」中間那一瞬間
    # 檔案是可讀的（那個空窗期在多人主機上是真的會被撈走）
    # ⚠️ 一定要 O_BINARY：Windows 的 os.open 預設是**文字模式**，會把私鑰裡的
    # 0x0A 位元組轉成 CRLF——32 bytes 的金鑰約有 12% 機率含 0x0A，寫出去就是壞的，
    # 而且是隨機發生（時好時壞最難查）。Linux 上不會，但測試在 Windows 跑，當場抓到。
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(str(p), flags, 0o600)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    pub_raw = public_key_of(private_path)
    return {"public_key": pub_raw.hex(), "fingerprint": fingerprint_hex(pub_raw),
            "private_key_path": str(p)}


def public_key_of(private_path: str | None = None) -> bytes:
    """從私鑰算出公鑰（原始 32 bytes）。"""
    _, serialization, X25519PrivateKey, _, _, _ = _cry()
    raw = Path(private_key_path(private_path)).read_bytes()
    priv = X25519PrivateKey.from_private_bytes(raw)
    return priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)


def parse_public_key(value: str) -> bytes:
    """把人貼進來的公鑰字串轉回 32 bytes，並且**擋掉貼錯東西**。

    人會貼到的錯誤：整段連換行貼、貼到指紋、貼到私鑰、貼到 SSH 公鑰。
    長度不對就直接拒絕並講清楚——比事後解不開好查。
    """
    cleaned = "".join((value or "").split())
    if not cleaned:
        raise ValueError("公鑰是空的")
    try:
        raw = bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"公鑰格式不對（應該是 64 個十六進位字元）。"
            f"如果貼的是 SSH 公鑰或整段說明文字，那不是這裡要的東西。") from exc
    if len(raw) != EPK_LEN:
        raise ValueError(
            f"公鑰長度不對：{len(raw)} bytes，應該是 {EPK_LEN}。"
            f"確認貼的是「公鑰」不是指紋（指紋只有 {FP_LEN} bytes）。")
    return raw


# ---------------------------------------------------------------------------
# 加解密
# ---------------------------------------------------------------------------

def encrypt(plaintext: bytes, recipient_public_raw: bytes, *, compress: bool = True) -> bytes:
    """壓縮（可選）後加密給指定公鑰的持有者。

    順序是「先壓再加密」——反過來壓不動，加密後的資料是亂數。
    """
    hashes, serialization, X25519PrivateKey, X25519PublicKey, AESGCM, HKDF = _cry()

    body = gzip.compress(plaintext, compresslevel=6) if compress else plaintext

    recipient = X25519PublicKey.from_public_bytes(recipient_public_raw)
    eph = X25519PrivateKey.generate()          # 每次都新的：固定它等於全部共用一把金鑰
    eph_pub = eph.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    shared = eph.exchange(recipient)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=_HKDF_INFO).derive(shared)

    fp = fingerprint(recipient_public_raw)
    nonce = os.urandom(NONCE_LEN)
    # header 進 AAD：有人改掉指紋或臨時公鑰，解密會失敗而不是解出垃圾
    aad = MAGIC + fp + eph_pub
    ct = AESGCM(key).encrypt(nonce, body, aad)
    return MAGIC + fp + eph_pub + nonce + ct


def looks_encrypted(data: bytes) -> bool:
    """這包是不是我們加密過的？還原端靠它決定要不要解。"""
    return data[:len(MAGIC)] == MAGIC


def describe(data: bytes) -> dict:
    """不解密就能回答「這是什麼、加密給誰」。"""
    if not looks_encrypted(data):
        return {"encrypted": False}
    fp = data[len(MAGIC):len(MAGIC) + FP_LEN]
    return {"encrypted": True, "recipient_fingerprint": fp.hex()}


def decrypt(data: bytes, private_path: str | None = None,
            *, decompress: bool = True) -> bytes:
    """用本機私鑰解開。錯誤訊息要**分得出是哪一種錯**——三種原因處理方式完全不同。"""
    hashes, _, X25519PrivateKey, X25519PublicKey, AESGCM, HKDF = _cry()
    private_path = private_key_path(private_path)

    if not looks_encrypted(data):
        raise ValueError("這不是加密匯出檔（開頭沒有 WEBIT3ENC 標記）")
    if len(data) < HEADER_LEN + 16:
        raise ValueError("檔案不完整——長度不足以容納標頭與內容，可能是分割檔沒併回")

    p = Path(private_path)
    if not p.exists():
        raise ValueError(
            f"這台沒有私鑰（{private_path}），解不開。加密匯出只有收件端解得開——"
            f"請在收件端（221）操作，或確認私鑰檔是不是被移走了。")

    off = len(MAGIC)
    fp_in = data[off:off + FP_LEN]; off += FP_LEN
    eph_pub = data[off:off + EPK_LEN]; off += EPK_LEN
    nonce = data[off:off + NONCE_LEN]; off += NONCE_LEN
    ct = data[off:]

    priv = X25519PrivateKey.from_private_bytes(p.read_bytes())
    my_fp = fingerprint(public_key_of(private_path))
    if fp_in != my_fp:
        # 這是「拿錯檔案」不是「檔案壞了」——講清楚才不會有人去找不存在的毀損問題
        raise ValueError(
            f"這個檔不是加密給這台的。檔案指紋 {fp_in.hex()}，"
            f"這台的公鑰指紋是 {my_fp.hex()}。"
            f"可能是來源端貼了別台的公鑰，或這個檔本來就要給另一台還原。")

    shared = priv.exchange(X25519PublicKey.from_public_bytes(eph_pub))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=_HKDF_INFO).derive(shared)
    aad = MAGIC + fp_in + eph_pub
    try:
        body = AESGCM(key).decrypt(nonce, ct, aad)
    except Exception as exc:  # noqa: BLE001 - cryptography 丟的是 InvalidTag
        raise ValueError(
            "解密失敗：內容驗證不過。檔案在傳輸過程中被改動或損毀"
            "（GCM 會驗完整性，所以壞掉會在這裡擋下來，不會解出垃圾資料）。") from exc

    if decompress and body[:2] == b"\x1f\x8b":
        return gzip.decompress(body)
    return body


# ---------------------------------------------------------------------------
# 設定（來源端存公鑰）
# ---------------------------------------------------------------------------

def get_recipient_public(conn) -> bytes | None:
    """來源端設定的收件人公鑰；沒設回 None。"""
    from db import get_setting

    val = get_setting(conn, PUBLIC_KEY_SETTING, None)
    return parse_public_key(val) if val else None


def set_recipient_public(conn, value: str) -> dict:
    """存收件人公鑰。**存之前先驗**——貼錯到匯出當下才爆，那時候人已經在等檔案了。"""
    from db import set_setting

    raw = parse_public_key(value)
    set_setting(conn, PUBLIC_KEY_SETTING, raw.hex())
    return {"fingerprint": fingerprint_hex(raw)}
