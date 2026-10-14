"""AIX 一鍵安裝＋驗證 `fcb_aix_onekey.ksh` 的守門。

使用者 2026-10-14（老賈 Q51）：`ksh ./fcb_aix_onekey.ksh <基底目錄>` 一次做完
轉 LF → cksum 核對 → 安裝 → 檢查排程／設定檔 → 強制實跑 → 檢查結果，
任何一步失敗就停並講第幾步；公司路徑由參數帶，程式裡不寫。

cksum 預期值寫在一鍵程式裡（發版時寫入）。**這裡核對它跟 repo 裡那三支檔一致**——
改了稽核腳本／安裝程式／包裝程式卻忘了更新，這條就紅，失敗訊息會印新值。
"""
import io
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "APP" / "asset-module" / "scripts"
ONEKEY = SCRIPTS / "fcb_aix_onekey.ksh"
INSTALLER = SCRIPTS / "install_fcb_aix_cron.ksh"
WRAPPER = SCRIPTS / "run_fcb_aix_monthly.ksh"
MARK_ID = "webit3-fcb-aix-monthly"


def _read(p: Path) -> str:
    return io.open(p, encoding="utf-8", newline="").read()


def posix_cksum(data: bytes) -> str:
    """POSIX `cksum` 的 CRC 與位元組數（跟 AIX 上 `cksum < 檔` 的前兩欄一樣）。"""
    crc = 0
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else (crc << 1)
            crc &= 0xFFFFFFFF
    n = len(data)
    while n:
        crc ^= (n & 0xFF) << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else (crc << 1)
            crc &= 0xFFFFFFFF
        n >>= 8
    return f"{(~crc) & 0xFFFFFFFF} {len(data)}"


def _expect(text: str, key: str) -> str:
    m = re.search(rf'^{key}="([^"]*)"$', text, re.M)
    assert m, f"一鍵程式裡找不到 {key}"
    return m.group(1)


def test_posix_cksum_實作正確():
    # `printf 'abc' | cksum` 的已知值
    assert posix_cksum(b"abc") == "1219131554 3"
    assert posix_cksum(b"") == "4294967295 0"


def test_內建預期值跟_repo_裡的檔一致():
    """⚠️ 這條紅燈的意思是：改了 AIX 腳本，但一鍵程式裡的預期 cksum 沒更新。

    照訊息把新值貼進 fcb_aix_onekey.ksh 的 EXPECT_* 那幾行。
    """
    t = _read(ONEKEY)
    audits = sorted(SCRIPTS.glob("fcb_aixV???.ksh"))
    assert len(audits) == 1
    got = {
        "EXPECT_AUDIT_NAME": audits[0].name,
        "EXPECT_AUDIT_CKSUM": posix_cksum(audits[0].read_bytes()),
        "EXPECT_INSTALL_CKSUM": posix_cksum(INSTALLER.read_bytes()),
        "EXPECT_RUN_CKSUM": posix_cksum(WRAPPER.read_bytes()),
    }
    bad = {k: v for k, v in got.items() if _expect(t, k) != v}
    assert not bad, "一鍵程式的預期值過期，改成：\n" + "\n".join(f'{k}="{v}"' for k, v in bad.items())


def test_LF_與_ksh_shebang_沒有寫死公司路徑():
    raw = ONEKEY.read_bytes()
    assert b"\r" not in raw and raw.startswith(b"#!/usr/bin/ksh\n")
    code = "\n".join(l for l in _read(ONEKEY).split("\n") if not l.lstrip().startswith("#"))
    assert 'INSTALL_DIR="${BASE}/script"' in code and 'LOG_DIR="${BASE}/log/fcb"' in code
    assert not re.search(r'BASE="/', code), "基底目錄由參數帶，不可以寫死"


def test_七步都有標題_而且依序():
    t = _read(ONEKEY)
    nums = [int(n) for n in re.findall(r'^step (\d) "', t, re.M)]
    assert nums == [1, 2, 3, 4, 5, 6, 7]


# ---------------------------------------------------------------------------
# 行為測試：POSIX sh 跑；crontab 換成檔案模擬；稽核腳本換成假的
# ---------------------------------------------------------------------------

SH = shutil.which("sh")
needs_sh = pytest.mark.skipif(SH is None or os.name == "nt", reason="需要 POSIX 環境的 sh")

MOCK = """#!/bin/sh
F="${MOCK_CRONTAB_FILE:?}"
if [ "$1" = "-l" ]; then
  [ -f "$F" ] || { printf 'no crontab for tester\\n' >&2; exit 1; }
  cat "$F"
else
  cat "$1" > "$F"
fi
"""

# 檔名帶到秒：同一分鐘內重跑（冪等測試）取件點才不會撞名
FAKE_AUDIT = """d="${FCBAIX_OUTDIR:?}"; s=`date +%Y%m%d_%H%M%S`
printf 'x\\nCheck summary: ok\\n' > "$d/FCB_h_10.99.0.1_$s.txt"
printf '<html>\\n' > "$d/FCB_h_10.99.0.1_$s.html"
printf '<html>\\n' > "$d/FCB_h_10.99.0.1_${s}_audit.html"
printf 'dbg\\n' > "$d/FCB_h_10.99.0.1_$s.debug.log"
"""


def _kit(tmp_path, crlf=False, audit_name="fcb_aixV999.ksh", expect_override=None):
    """擺一份「剛傳上來」的目錄：假稽核腳本＋真安裝程式＋真包裝程式＋改過預期值的一鍵程式。"""
    kit = tmp_path / "kit"
    kit.mkdir()
    files = {
        audit_name: FAKE_AUDIT.encode(),
        INSTALLER.name: INSTALLER.read_bytes(),
        WRAPPER.name: WRAPPER.read_bytes(),
    }
    vals = {
        "EXPECT_AUDIT_NAME": "fcb_aixV999.ksh",
        "EXPECT_AUDIT_CKSUM": posix_cksum(files.get("fcb_aixV999.ksh", b"")),
        "EXPECT_INSTALL_CKSUM": posix_cksum(files[INSTALLER.name]),
        "EXPECT_RUN_CKSUM": posix_cksum(files[WRAPPER.name]),
    }
    vals.update(expect_override or {})
    for name, data in files.items():
        (kit / name).write_bytes(data.replace(b"\n", b"\r\n") if crlf else data)
    t = _read(ONEKEY)
    for k, v in vals.items():
        t = re.sub(rf'^{k}="[^"]*"$', f'{k}="{v}"', t, flags=re.M)
    (kit / ONEKEY.name).write_text(t)
    return kit


def _env(tmp_path):
    mock = tmp_path / "mockcrontab"
    mock.write_text(MOCK)
    mock.chmod(0o755)
    pick = tmp_path / "pick"
    pick.mkdir(exist_ok=True)
    env = dict(os.environ, MOCK_CRONTAB_FILE=str(tmp_path / "tab"),
               FCBAIX_CRONTAB_CMD=str(mock), FCBAIX_TEST_NOROOT="1", FCBAIX_TEST_TMP_OK="1",
               FCBAIX_CRON_SPOOL=str(tmp_path / "spool"),
               FCBAIX_OLD_HIST_DIR=str(tmp_path / "oldhist"),
               FCBAIX_PICKUP_DIR=str(pick), FCBAIX_KSH=SH)
    for k in ("FCBAIX_INSTALL_DIR", "FCBAIX_LOG_DIR", "FCBAIX_HIST_DIR", "FCBAIX_FORCE_RUN"):
        env.pop(k, None)
    return env


def _run(kit, env, *args):
    return subprocess.run([SH, str(kit / ONEKEY.name), *args], env=env,
                          capture_output=True, text=True, timeout=120)


@needs_sh
def test_從頭裝到底_全部通過_重跑也通過(tmp_path):
    kit = _kit(tmp_path, crlf=True)
    env = _env(tmp_path)
    old = tmp_path / "oldhist"
    old.mkdir()
    (old / "fcbaudit_h_20261013_0900.tar.Z").write_bytes(b"arc")
    base = tmp_path / "base"
    r = _run(kit, env, str(base))
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert [int(n) for n in re.findall(r"^===== (\d)\. ", out, re.M)] == [1, 2, 3, 4, 5, 6, 7]
    assert out.rstrip().splitlines()[-1] == "全部通過"
    assert b"\r" not in (kit / WRAPPER.name).read_bytes(), "第 1 步要把 CR 拿掉"
    tab = [l for l in (tmp_path / "tab").read_text().splitlines() if MARK_ID in l]
    assert len(tab) == 1 and f" {base}/script/run_fcb_aix_monthly.ksh " in tab[0]
    assert (base / "log" / "fcb" / "fcbaudit_h_20261013_0900.tar.Z").read_bytes() == b"arc"
    assert not old.exists()
    assert len(list((base / "log" / "fcb").glob("FCB_*_audit.html"))) == 1
    assert len(list((tmp_path / "pick").glob("FCB_*_audit.html"))) == 1

    import time
    time.sleep(1.1)   # 假稽核腳本檔名帶到秒
    r = _run(kit, env, str(base))
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.rstrip().splitlines()[-1] == "全部通過"
    tab = [l for l in (tmp_path / "tab").read_text().splitlines() if MARK_ID in l]
    assert len(tab) == 1


@needs_sh
def test_cksum_對不上_停在第2步_什麼都沒裝(tmp_path):
    kit = _kit(tmp_path, expect_override={"EXPECT_RUN_CKSUM": "1 1"})
    r = _run(kit, _env(tmp_path), str(tmp_path / "base"))
    assert r.returncode != 0
    assert r.stdout.rstrip().splitlines()[-1] == "第 2 步失敗"
    assert not (tmp_path / "tab").exists() and not (tmp_path / "base").exists()


@needs_sh
def test_稽核腳本版號不對_停在第1步(tmp_path):
    kit = _kit(tmp_path, audit_name="fcb_aixV998.ksh")
    r = _run(kit, _env(tmp_path), str(tmp_path / "base"))
    assert r.returncode != 0
    assert r.stdout.rstrip().splitlines()[-1] == "第 1 步失敗"


@needs_sh
@pytest.mark.parametrize("args", [[], ["relative/dir"], ["/tmp/x"], ["/var/tmp/x"], ["/a", "/b"]],
                         ids=["沒參數", "相對路徑", "tmp", "var_tmp", "兩個參數"])
def test_參數不對_印用法後停(tmp_path, args):
    kit = _kit(tmp_path)
    env = _env(tmp_path)
    env.pop("FCBAIX_TEST_TMP_OK")
    r = _run(kit, env, *args)
    assert r.returncode == 2
    assert "用法" in r.stderr
    assert "=====" not in r.stdout
    assert not (tmp_path / "tab").exists()
