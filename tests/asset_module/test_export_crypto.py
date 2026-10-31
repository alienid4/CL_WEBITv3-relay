"""匯出檔加密（決策 SEC5）：只有持私鑰的那台解得開。

使用者 2026-09-08：「B（加密），我們是不是可以做到內建加密器，只有我們的系統才能解」。

守的幾條，每一條都對應一個真的會出事的情境：
1. **金鑰不在程式裡**——`APP/` 會被打包進公開 relay repo
2. **每次密文不同**——固定臨時金鑰等於所有匯出共用一把，破一次全破
3. **拿錯檔案要講「不是給這台的」**，不是丟一個看不懂的解密失敗
4. **產金鑰不覆蓋既有私鑰**——覆蓋等於把過去所有加密備份變成永遠打不開的垃圾
5. **內容被改過要擋下來**，不能安靜地解出垃圾
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import export_crypto as ec  # noqa: E402

PLAIN = b"CREATE TABLE hardware (...);\n" * 500     # 壓得動的內容


def _keys(tmp):
    """產一對金鑰，回 (私鑰路徑, 公鑰 raw)。"""
    priv = str(Path(tmp) / "priv.key")
    info = ec.generate_keypair(priv)
    return priv, ec.parse_public_key(info["public_key"])


# ---------------------------------------------------------------------------
# 1. 金鑰不可以在程式裡
# ---------------------------------------------------------------------------

def test_原始碼裡不可以有寫死的金鑰():
    """`APP/` 底下會被打包進公開的 relay repo。寫死等於金鑰公開；
    就算不公開，任何拿到程式的人都能解——稽核會判定等同未加密。"""
    src = (ROOT / "APP" / "asset-module" / "backend" / "export_crypto.py").read_text(
        encoding="utf-8")
    # 32 bytes 的十六進位金鑰長這樣：連續 64 個 hex 字元
    import re
    hits = [m for m in re.findall(r"\b[0-9a-fA-F]{64}\b", src)]
    assert not hits, f"原始碼裡出現看起來像金鑰的字串：{hits}"
    assert "PRIVATE_KEY_DEFAULT" in src, "私鑰應該是檔案路徑，不是內嵌值"


# ---------------------------------------------------------------------------
# 2. 基本往返
# ---------------------------------------------------------------------------

def test_加密再解密拿回原文():
    with tempfile.TemporaryDirectory() as tmp:
        priv, pub = _keys(tmp)
        blob = ec.encrypt(PLAIN, pub)
        assert ec.decrypt(blob, priv) == PLAIN


def test_密文裡不可以出現原文片段():
    with tempfile.TemporaryDirectory() as tmp:
        _, pub = _keys(tmp)
        blob = ec.encrypt(PLAIN, pub)
        assert b"CREATE TABLE" not in blob, "原文直接出現在密文裡——那沒有加密"


def test_有壓縮_而且是先壓再加密():
    """順序反了就壓不動：加密後的資料是亂數。
    這裡用「密文明顯小於原文」來證明壓縮確實發生在加密之前。"""
    with tempfile.TemporaryDirectory() as tmp:
        _, pub = _keys(tmp)
        blob = ec.encrypt(PLAIN, pub)
        assert len(blob) < len(PLAIN) / 3, (
            f"密文 {len(blob)} vs 原文 {len(PLAIN)}——沒有壓到，"
            f"檢查是不是先加密才壓縮（那樣壓不動）")


# ---------------------------------------------------------------------------
# 3. 每次都要不一樣
# ---------------------------------------------------------------------------

def test_每次加密結果都不同():
    """固定臨時金鑰等於所有匯出共用同一把，破一次全破。
    密文每次不同是**正確的**，不要為了「看起來一致」去固定它。"""
    with tempfile.TemporaryDirectory() as tmp:
        priv, pub = _keys(tmp)
        a, b = ec.encrypt(PLAIN, pub), ec.encrypt(PLAIN, pub)
        assert a != b
        assert ec.decrypt(a, priv) == ec.decrypt(b, priv) == PLAIN


# ---------------------------------------------------------------------------
# 4. 拿錯檔案 vs 檔案壞掉：要分得出來
# ---------------------------------------------------------------------------

def test_別台的檔案要講清楚不是給這台的():
    """「拿錯檔案」跟「檔案壞了」要做的事完全不同——
    講成解密失敗，人會跑去找不存在的毀損問題。"""
    with tempfile.TemporaryDirectory() as tmp:
        priv_a, pub_a = _keys(tmp)
        priv_b = str(Path(tmp) / "b.key")
        ec.generate_keypair(priv_b)

        blob = ec.encrypt(PLAIN, pub_a)          # 加密給 A
        with pytest.raises(ValueError) as exc:
            ec.decrypt(blob, priv_b)             # 拿去 B 解
        msg = str(exc.value)
        assert "不是加密給這台" in msg
        assert "指紋" in msg, "沒給指紋，人無從確認是哪兩把搞混"


def test_內容被改過要擋下來():
    """AES-GCM 自帶完整性驗證。壞掉要在這裡擋，不能安靜地解出垃圾資料。"""
    with tempfile.TemporaryDirectory() as tmp:
        priv, pub = _keys(tmp)
        blob = bytearray(ec.encrypt(PLAIN, pub))
        blob[-5] ^= 0xFF                          # 動密文一個 byte
        with pytest.raises(ValueError) as exc:
            ec.decrypt(bytes(blob), priv)
        assert "驗證" in str(exc.value) or "損毀" in str(exc.value)


def test_沒有私鑰要講在哪一台解():
    with tempfile.TemporaryDirectory() as tmp:
        _, pub = _keys(tmp)
        blob = ec.encrypt(PLAIN, pub)
        with pytest.raises(ValueError) as exc:
            ec.decrypt(blob, str(Path(tmp) / "不存在.key"))
        assert "沒有私鑰" in str(exc.value)


def test_不是加密檔要直說():
    with tempfile.TemporaryDirectory() as tmp:
        priv, _ = _keys(tmp)
        with pytest.raises(ValueError) as exc:
            ec.decrypt(b"SQLite format 3\x00just a plain db", priv)
        assert "不是加密匯出檔" in str(exc.value)


def test_分割檔沒併回要講出來():
    with tempfile.TemporaryDirectory() as tmp:
        priv, pub = _keys(tmp)
        blob = ec.encrypt(PLAIN, pub)
        with pytest.raises(ValueError) as exc:
            ec.decrypt(blob[:30], priv)           # 只有半個標頭
        assert "不完整" in str(exc.value)


# ---------------------------------------------------------------------------
# 5. 產金鑰不可以覆蓋
# ---------------------------------------------------------------------------

def test_已經有私鑰就不覆蓋():
    """覆蓋等於把過去所有加密備份變成永遠打不開的垃圾，而且是**無聲的**：
    畫面顯示「產生成功」，直到某天要還原才發現。"""
    with tempfile.TemporaryDirectory() as tmp:
        priv = str(Path(tmp) / "p.key")
        first = ec.generate_keypair(priv)
        with pytest.raises(ValueError) as exc:
            ec.generate_keypair(priv)
        assert "不覆蓋" in str(exc.value)
        # 原本那把要原封不動
        assert ec.public_key_of(priv).hex() == first["public_key"]


def test_私鑰檔權限是_600():
    """跟收集私鑰同一套規矩。Windows 上 os.chmod 語意不同，只在 POSIX 驗。"""
    import os
    if os.name != "posix":
        pytest.skip("權限位元只在 POSIX 有意義")
    with tempfile.TemporaryDirectory() as tmp:
        priv, _ = _keys(tmp)
        assert oct(Path(priv).stat().st_mode & 0o777) == "0o600"


# ---------------------------------------------------------------------------
# 6. 貼錯公鑰要當場擋
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad,why", [
    ("", "空的"),
    ("這不是金鑰", "不是十六進位"),
    ("aabbcc", "太短（像是貼到指紋）"),
    ("ssh-ed25519 AAAAC3Nz... user@host", "貼到 SSH 公鑰"),
])
def test_公鑰貼錯要當場報錯(bad, why):
    """貼錯到匯出當下才爆，那時候人已經在等檔案了。"""
    with pytest.raises(ValueError):
        ec.parse_public_key(bad)


def test_公鑰可以帶空白換行():
    """人從畫面複製常常會夾到換行。這種不該當成錯誤。"""
    with tempfile.TemporaryDirectory() as tmp:
        _, pub = _keys(tmp)
        messy = "  " + pub.hex()[:32] + "\n" + pub.hex()[32:] + "  "
        assert ec.parse_public_key(messy) == pub


# ---------------------------------------------------------------------------
# 7. 設定
# ---------------------------------------------------------------------------

def test_公鑰存得進設定也讀得回來():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            assert ec.get_recipient_public(conn) is None, "沒設定時應該回 None"
            _, pub = _keys(tmp)
            info = ec.set_recipient_public(conn, pub.hex())
            assert info["fingerprint"] == ec.fingerprint_hex(pub)
            assert ec.get_recipient_public(conn) == pub
        finally:
            conn.close()


def test_不解密也要看得出是加密檔與收件人():
    """還原端要能在解之前就說「這是加密檔、加密給某某指紋」。"""
    with tempfile.TemporaryDirectory() as tmp:
        _, pub = _keys(tmp)
        blob = ec.encrypt(PLAIN, pub)
        info = ec.describe(blob)
        assert info["encrypted"] is True
        assert info["recipient_fingerprint"] == ec.fingerprint_hex(pub)
        assert ec.describe(b"SQLite format 3\x00")["encrypted"] is False
