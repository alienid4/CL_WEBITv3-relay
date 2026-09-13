"""收集金鑰匯入：對不起來的一定要擋，換掉之前要講清楚代價。

使用者 2026-09-08：「做一個匯入頁」。公司若已有一把佈遍全機隊的金鑰
（例如巡檢用的），匯入它就省掉重新納管每一台。

守四件事：

1. **私鑰公鑰對不起來要擋**。佈下去之後目標主機認公鑰、收集端拿私鑰，
   對不起來的話**納管會顯示成功但永遠連不進去**——這正是 2026-08-16
   踩過的那類故障：所有紅綠燈都說成功，只有資料永遠不進來。
2. **有密語的私鑰要擋**。收集是排程無人值守跑的，不會有人在旁邊輸入密語。
3. **中途失敗不能留半套**。私鑰寫了公鑰沒寫的話，系統下次會自己再產一把，
   匯入的那把就對不上了——所以先全部驗完才動檔案。
4. **私鑰絕不回傳**。稽核只記指紋（公開資訊，足以追溯且洩漏不出東西）。
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collector_key as ck  # noqa: E402
import db  # noqa: E402

_HAS_KEYGEN = None


def _has_keygen() -> bool:
    global _HAS_KEYGEN
    if _HAS_KEYGEN is None:
        try:
            subprocess.run(["ssh-keygen", "-h"], capture_output=True, timeout=10)
            _HAS_KEYGEN = True
        except (OSError, subprocess.SubprocessError):
            _HAS_KEYGEN = False
    return _HAS_KEYGEN


needs_keygen = pytest.mark.skipif(
    not _has_keygen(), reason="這台沒有 ssh-keygen（金鑰驗證靠它）")


def _make_key(d: Path, name="k", passphrase="") -> tuple[str, str]:
    """產一對真金鑰。用真的而不是寫死字串——寫死的假金鑰驗不出
    「配對」與「有沒有密語」，那正是這支測試要守的東西。"""
    p = d / name
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", passphrase,
                    "-C", f"test-{name}", "-f", str(p)],
                   capture_output=True, check=True, timeout=30)
    return p.read_text(encoding="utf-8"), (d / f"{name}.pub").read_text(encoding="utf-8")


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _point_at(monkeypatch, d: Path):
    """把金鑰位置指到暫存目錄——不要碰這台機器真正的收集金鑰。"""
    import onboard_engine
    monkeypatch.setattr(onboard_engine, "COLLECTOR_KEY_PUB", str(d / "ck") + ".pub")


# ---------------------------------------------------------------------------
# 驗證
# ---------------------------------------------------------------------------

@needs_keygen
def test_私鑰公鑰對不起來一定要擋():
    """對不起來的話納管會顯示成功但永遠連不進去——所有紅綠燈都說成功，
    只有資料永遠不進來。那是這個專案反覆在防的那一類故障。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        priv_a, _ = _make_key(d, "a")
        _, pub_b = _make_key(d, "b")
        conn = _conn(tmp)
        try:
            with pytest.raises(ValueError) as exc:
                ck.import_key(conn, priv_a, pub_b, "tester")
            assert "不是一對" in str(exc.value)
            assert "永遠連不進去" in str(exc.value), "沒講後果，人不知道為什麼被擋"
        finally:
            conn.close()


@needs_keygen
def test_有密語的私鑰要擋():
    """收集是排程無人值守跑的，不會有人在旁邊輸入密語。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        priv, _ = _make_key(d, "p", passphrase="s3cret-pass")
        with pytest.raises(ValueError) as exc:
            ck.derive_public(priv)
        assert "密語" in str(exc.value)
        assert "無人值守" in str(exc.value), "沒講為什麼不行"


@needs_keygen
def test_公鑰留空就自己導出():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        priv, pub = _make_key(d, "k")
        derived = ck.derive_public(priv)
        assert ck._same_key(derived, pub)


@needs_keygen
def test_註解不同不算換金鑰():
    """公鑰結尾那段 user@host 改了不代表換了金鑰。拿它當條件會誤判成
    「你換了一把」然後嚇人說全機隊要重納管。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _, pub = _make_key(d, "k")
        assert ck._same_key(pub, pub.rsplit(" ", 1)[0] + " someone@elsewhere")


# ---------------------------------------------------------------------------
# 匯入
# ---------------------------------------------------------------------------

@needs_keygen
def test_匯入成功要備份舊的(monkeypatch):
    """換錯金鑰＝全機隊收集失效，一定要還原得回來。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _point_at(monkeypatch, d)
        old_priv, old_pub = _make_key(d, "old")
        new_priv, new_pub = _make_key(d, "new")
        conn = _conn(tmp)
        try:
            ck.import_key(conn, old_priv, old_pub, "tester")
            r = ck.import_key(conn, new_priv, new_pub, "tester")
            assert r["backed_up"], "沒有備份舊金鑰"
            assert r["changed"] is True
            assert r["old_fingerprint"] != r["new_fingerprint"]
        finally:
            conn.close()


@needs_keygen
def test_匯入同一把要說沒有變(monkeypatch):
    """重新匯入原本那把是常見操作（例如確認一下）。報成「換了金鑰」
    會讓人以為全機隊要重納管，白白緊張一場。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _point_at(monkeypatch, d)
        priv, pub = _make_key(d, "k")
        conn = _conn(tmp)
        try:
            ck.import_key(conn, priv, pub, "tester")
            r = ck.import_key(conn, priv, pub, "tester")
            assert r["changed"] is False
        finally:
            conn.close()


@needs_keygen
def test_回傳裡絕對不可以有私鑰(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _point_at(monkeypatch, d)
        priv, pub = _make_key(d, "k")
        conn = _conn(tmp)
        try:
            r = ck.import_key(conn, priv, pub, "tester")
            blob = str(r) + str(ck.status(conn))
            assert "PRIVATE KEY" not in blob, "私鑰內容跑進回傳值了"
            # 稽核只記指紋——公開資訊，足以追溯且洩漏不出東西
            note = conn.execute(
                "SELECT value FROM app_settings "
                "WHERE key = 'collector_key_last_import'").fetchone()[0]
            assert "PRIVATE KEY" not in note
            assert "tester" in note and "SHA256" in note
        finally:
            conn.close()


@needs_keygen
def test_驗證失敗不可以動到既有金鑰(monkeypatch):
    """中途失敗留半套是這裡最糟的結果：私鑰寫了公鑰沒寫，
    系統下次會自己再產一把，匯入的那把就對不上了。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _point_at(monkeypatch, d)
        good_priv, good_pub = _make_key(d, "good")
        other_priv, _ = _make_key(d, "other")
        _, other_pub = _make_key(d, "other2")
        conn = _conn(tmp)
        try:
            ck.import_key(conn, good_priv, good_pub, "tester")
            before = ck.fingerprint(ck.pub_path())
            with pytest.raises(ValueError):
                ck.import_key(conn, other_priv, other_pub, "tester")
            assert ck.fingerprint(ck.pub_path()) == before, "驗證失敗卻已經動了檔案"
        finally:
            conn.close()


def test_status_要講出會影響幾台(monkeypatch):
    """沒有這個數字，換金鑰看起來只是改個設定——實際上是讓那些主機的
    收集當場失效（它們 authorized_keys 裡是舊公鑰）。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _point_at(monkeypatch, d)
        conn = _conn(tmp)
        try:
            db.insert_hardware(conn, asset_serial="A1", ip="10.0.0.1", collect_ok=1)
            db.insert_hardware(conn, asset_serial="A2", ip="10.0.0.2", collect_ok=1)
            db.insert_hardware(conn, asset_serial="A3", ip="10.0.0.3", collect_ok=0)
            conn.commit()
            st = ck.status(conn)
            assert st["onboarded_hosts"] == 2
            assert "public_key" in st
            assert "private_key" not in st, "現況查詢竟然吐私鑰"
        finally:
            conn.close()


def test_接手路徑要擋掉不存在與過大的檔案():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            with pytest.raises(ValueError) as exc:
                ck.adopt_from_path(conn, str(Path(tmp) / "nope"), "tester")
            assert "找不到檔案" in str(exc.value)

            big = Path(tmp) / "big"
            big.write_text("x" * (70 * 1024), encoding="utf-8")
            with pytest.raises(ValueError) as exc:
                ck.adopt_from_path(conn, str(big), "tester")
            assert "不像是一把私鑰" in str(exc.value)
        finally:
            conn.close()
