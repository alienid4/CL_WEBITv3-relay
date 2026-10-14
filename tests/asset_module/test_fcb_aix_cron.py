"""AIX 稽核排程（每月第四個禮拜日 09:00）的安裝程式與包裝程式守門。

使用者 2026-10-13 要的：
* 另寫安裝程式，稽核腳本本身維持唯讀、不改系統設定
* 冪等：跑兩次只加一行，不動 crontab 既有的其他行
* 第四個禮拜日：cron 寫 `0 9 22-28 * *`，禮拜日判斷放在包裝程式
  （cron 的日期與星期兩欄同時有值是 OR，寫成 `22-28 * 0` 會每天跑）
* 取件點 /tmp 照稽核腳本預設檔名放（給其他系統抓），不壓縮
* /var/tmp/fcbaudit 留歷史：打包壓縮成一個檔、700/600、三年自動刪

兩支都要在 AIX ksh88 上跑，沿用 fcb_aixV### 的守則（test_fcb_aix_shell_safety、
test_fcb_aix_echo_guard）：`${VAR}`、printf 不用 echo、沒有 bash 語法。
本機與 CI 沒有 ksh88，所以行為測試用 POSIX sh 跑——兩支刻意只用 sh 與 ksh88
的交集，sh 跑得過是必要條件（不是充分條件，ksh88 真機另驗）。
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
INSTALLER = SCRIPTS / "install_fcb_aix_cron.ksh"
WRAPPER = SCRIPTS / "run_fcb_aix_monthly.ksh"
ONEKEY = SCRIPTS / "fcb_aix_onekey.ksh"
MARK_ID = "webit3-fcb-aix-monthly"

GREEDY_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)(?=[^\x00-\x7f])")

#: ksh88 不支援、或 bash 專屬的寫法。
BASHISMS = [
    (re.compile(r"\[\["), "[[ ]]"),
    (re.compile(r"(^|\s)local\s"), "local"),
    (re.compile(r"(^|\s)declare\s"), "declare"),
    (re.compile(r"(^|\s)source\s"), "source"),
    (re.compile(r"\$'"), "$'...'"),
    (re.compile(r"<<<"), "here-string <<<"),
    (re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*//"), "${var//}"),
    (re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:[0-9]"), "${var:offset}"),
    (re.compile(r"\$\{![A-Za-z_]"), "${!var}"),
    (re.compile(r"=\("), "array=( )"),
    (re.compile(r"\[\s[^]]*\s==\s"), "[ a == b ]"),
    (re.compile(r"&>"), "&>"),
    (re.compile(r"\|&"), "|&"),
    (re.compile(r"(^|[;\s])if\s+!\s"), "if ! cmd（ksh88 沒有 ! 反轉）"),
    (re.compile(r"\bfunction\s"), "function 關鍵字（統一用 name() {）"),
    (re.compile(r"\becho\b"), "echo（AIX ksh 的 echo 會展開反斜線，一律 printf）"),
    (re.compile(r"\bcommand -v\b"), "command -v（ksh88 不保證有）"),
]


def _read(p: Path) -> str:
    return io.open(p, encoding="utf-8", newline="").read()


def _code_lines(text: str):
    for n, line in enumerate(text.split("\n"), 1):
        if not line.lstrip().startswith("#"):
            yield n, line


@pytest.mark.parametrize("path", [INSTALLER, WRAPPER, ONEKEY], ids=lambda p: p.name)
def test_兩支都是_LF_與_ksh_shebang(path):
    raw = path.read_bytes()
    assert b"\r" not in raw, f"{path.name} 有 CR，AIX 上會整支跑不動"
    assert raw.startswith(b"#!/usr/bin/ksh\n")


@pytest.mark.parametrize("path", [INSTALLER, WRAPPER, ONEKEY], ids=lambda p: p.name)
def test_沒有_bash_語法(path):
    bad = [
        (n, why, line.strip()[:70])
        for n, line in _code_lines(_read(path))
        for rx, why in BASHISMS
        if rx.search(line)
    ]
    assert not bad, "\n".join(f"  第 {n} 行 {why}｜ {s}" for n, why, s in bad)


@pytest.mark.parametrize("path", [INSTALLER, WRAPPER, ONEKEY], ids=lambda p: p.name)
def test_變數後面不可以直接接全形字(path):
    bad = [(n, m.group(1)) for n, line in _code_lines(_read(path))
           for m in GREEDY_RE.finditer(line)]
    assert not bad, f"AIX ksh 會把全形字吃進變數名：{bad}"


def test_crontab_行是_22到28號_不寫星期():
    t = _read(INSTALLER)
    assert 'CRON_SPEC="0 9 22-28 * *"' in t
    # 寫成 `22-28 * 0` 會變成「22~28 號每天＋每個禮拜日」
    code = "\n".join(l for _, l in _code_lines(t))
    assert not re.search(r"22-28 \* [0-7]", code)


def test_包裝程式有禮拜日判斷_而且在執行稽核腳本之前():
    t = _read(WRAPPER)
    m = re.search(r'_dow=`date \+%w`\s*\n_dom=`date \+%d`\s*\nif \[ "\$\{_dow\}" != "0" \]', t)
    assert m, "包裝程式找不到「不是禮拜日就離開」的判斷"
    assert m.start() < t.index('"${KSH_BIN}" "${SCRIPT}"'), "禮拜日判斷要在跑稽核腳本之前"


def test_安裝程式先去掉本工具舊行才附加():
    t = _read(INSTALLER)
    assert f'MARK_ID="{MARK_ID}"' in t
    keep = t.index('grep -v "${MARK_ID}" "${TMPD}/base" > "${TMPD}/keep"')
    new = t.index('cat "${TMPD}/keep" > "${TMPD}/new"')
    add = t.index('printf \'%s\\n\' "${CRON_LINE}" >> "${TMPD}/new"')
    assert keep < new < add, "附加 crontab 之前要先去掉本工具的舊行（只留一行）"


def test_歷史備份有壓縮步驟而且有核對():
    t = _read(WRAPPER)
    assert "compress -f -c" in t and ".tar.${ZEXT}" in t
    assert "uncompress -c" in t and "tar -tf -" in t, "壓縮後要解開核對檔案數"
    assert 'HIST_DIR="${FCBAIX_HIST_DIR:-${CONF_DIR:-/var/tmp/fcbaudit}}"' in t
    assert "sed -n 's/^LOG_DIR=//p'" in t and ". \"${CONF}\"" not in t, "設定檔只讀值、不 source"
    assert 'chmod 700 "${HIST_DIR}"' in t and 'chmod 600 "${ARC}"' in t


def test_housekeeping_限定目錄_檔名_三年():
    t = _read(WRAPPER)
    m = re.search(r'find "\$\{HIST_DIR\}/\."(.*?)\n(.*?)\|\n', t, re.S)
    assert m, "找不到 housekeeping 的 find"
    expr = m.group(0)
    assert "-prune" in expr, "要用 -prune 限定只看本層（AIX find 沒有 -maxdepth）"
    assert "-name 'fcbaudit_*.tar.*'" in expr and "-name 'FCB_*_audit.html'" in expr
    assert "-mtime +1095" in expr
    # rm 只對歷史目錄底下、本工具檔名的檔
    rms = [l.strip() for _, l in _code_lines(t) if re.search(r"\brm\b", l)]
    for l in rms:
        assert l in ('"${HIST_DIR}"/.run_*) rm -rf "${WORK}" ;;',
                     'if rm -f "${HIST_DIR}/${_b}"; then'), f"多出一個 rm：{l}"


def test_取件點預設_tmp_權限有群組選項():
    t = _read(WRAPPER)
    assert 'PICKUP_DIR="${FCBAIX_PICKUP_DIR:-/tmp}"' in t
    assert 'chgrp "${PICKUP_GROUP}"' in t and "chmod 640" in t and "chmod 644" in t
    assert "set -C" in t, "寫進 /tmp 要用 noclobber，不跟著預先放好的連結覆寫"


def test_稽核腳本本身沒有改_crontab():
    """稽核腳本必須維持唯讀；排程只能由另一支安裝程式處理。"""
    for p in SCRIPTS.glob("fcb_aixV???.ksh"):
        code = "\n".join(l for _, l in _code_lines(_read(p)) if not l.lstrip().startswith("set_fix"))
        assert not re.search(r"\bcrontab\s+(-[a-z]\s+)?[\"$/]", code), f"{p.name} 不可以改 crontab"


# ---------------------------------------------------------------------------
# 行為測試：用 POSIX sh 跑，crontab 換成檔案模擬
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


def _env(tmp_path, **kw):
    mock = tmp_path / "mockcrontab"
    mock.write_text(MOCK)
    mock.chmod(0o755)
    # spool 一律指到測試目錄：221 以 root 跑測試時，真機 /var/spool/cron/root 存在，
    # 不隔開的話「原本沒有 crontab」這條會被安裝程式的保護擋下（而且不該讀到真機排程）。
    # 固定目錄指到測試目錄；測試的暫存目錄本身就在 /tmp，所以放行 /tmp 檢查。
    env = dict(os.environ, MOCK_CRONTAB_FILE=str(tmp_path / "tab"),
               FCBAIX_CRONTAB_CMD=str(mock), FCBAIX_TEST_NOROOT="1",
               FCBAIX_CRON_SPOOL=str(tmp_path / "spool"),
               FCBAIX_INSTALL_DIR=str(tmp_path / "inst"), FCBAIX_TEST_TMP_OK="1",
               FCBAIX_LOG_DIR=str(tmp_path / "log"),
               FCBAIX_OLD_HIST_DIR=str(tmp_path / "oldhist"))
    env.update(kw)
    return env


def _install(env, *args, installer=INSTALLER):
    return subprocess.run([SH, str(installer), *args], env=env,
                          capture_output=True, text=True)


def _marked(tmp_path):
    return [l for l in (tmp_path / "tab").read_text().splitlines() if MARK_ID in l]


@needs_sh
def test_原本沒有_crontab_跑兩次只加一行(tmp_path):
    env = _env(tmp_path)
    r1 = _install(env)
    r2 = _install(env)
    assert r1.returncode == 0 and r2.returncode == 0, r1.stderr + r2.stderr
    assert "已安裝，略過" in r2.stdout
    tab = (tmp_path / "tab").read_text()
    assert tab.count(MARK_ID) == 1
    assert tab.startswith(f"0 9 22-28 * * /usr/bin/ksh {tmp_path}/inst/run_fcb_aix_monthly.ksh ")


@needs_sh
def test_既有排程保留不動_移除只刪本工具那行(tmp_path):
    env = _env(tmp_path)
    (tmp_path / "tab").write_text("MAILTO=\"\"\n5 1 * * * /opt/x/backup.sh\n")
    _install(env)
    _install(env)
    tab = (tmp_path / "tab").read_text().splitlines()
    assert tab[:2] == ['MAILTO=""', "5 1 * * * /opt/x/backup.sh"]
    assert len(tab) == 3 and MARK_ID in tab[2]
    r = _install(env, "-r")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "tab").read_text().splitlines() == ['MAILTO=""', "5 1 * * * /opt/x/backup.sh"]


@needs_sh
def test_crontab_讀不到但_spool_有內容_就停下來不蓋(tmp_path):
    env = _env(tmp_path)
    me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    spool = tmp_path / "spool"
    spool.mkdir()
    (spool / me).write_text("5 1 * * * /opt/x/backup.sh\n")
    r = _install(env)
    assert r.returncode != 0
    assert "不蓋掉既有排程" in r.stderr
    assert not (tmp_path / "tab").exists(), "讀不到 crontab 時不可以寫入"


def test_固定目錄不寫死_不可以在暫存區():
    """固定目錄各環境不同、而且這支會進公開中繼，所以不寫死在程式裡；
    /tmp、/var/tmp 會被清掉（排程靜默失效），一律拒絕。"""
    t = _read(INSTALLER)
    code = "\n".join(l for _, l in _code_lines(t))
    assert 'INSTALL_DIR="${FCBAIX_INSTALL_DIR}"' in code
    assert not re.search(r'INSTALL_DIR="/', code), "固定目錄不可以寫死預設值"
    assert "/tmp|/tmp/*|/var/tmp|/var/tmp/*)" in code
    assert "ALLOW_TMP" not in code, "固定目錄放暫存區沒有放行開關（只有測試用的 FCBAIX_TEST_TMP_OK）"


@pytest.fixture
def tmp_kit():
    """在真的 /tmp 底下擺一份安裝程式＋包裝程式＋稽核腳本（模擬使用者傳到 /tmp/lab）。"""
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="fcbaix_lab_", dir="/tmp"))
    try:
        for f in (INSTALLER, WRAPPER):
            shutil.copy(f, d / f.name)
        (d / "fcb_aixV998.ksh").write_text("exit 0\n")
        (d / "fcb_aixV999.ksh").write_text("exit 0\n")
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@needs_sh
def test_從_tmp_執行_檔案複製到固定目錄_排程指向固定目錄(tmp_path, tmp_kit):
    env = _env(tmp_path)
    r = _install(env, installer=tmp_kit / INSTALLER.name)
    assert r.returncode == 0, r.stderr
    inst = tmp_path / "inst"
    got = sorted(p.name for p in inst.iterdir() if p.suffix == ".ksh")
    assert got == sorted([WRAPPER.name, INSTALLER.name, "fcb_aixV999.ksh"]), "只複製最新一支稽核腳本"
    assert sorted(p.name for p in inst.iterdir()) == sorted(got + ["fcb_aix.conf"])
    assert inst.stat().st_mode & 0o777 == 0o755
    assert all((inst / n).stat().st_mode & 0o777 == 0o700 for n in got)
    assert _marked(tmp_path) == [
        f"0 9 22-28 * * /usr/bin/ksh {inst}/run_fcb_aix_monthly.ksh >/dev/null 2>&1 # {MARK_ID}"]
    assert str(tmp_kit) not in (tmp_path / "tab").read_text()


@needs_sh
def test_固定目錄在_tmp_底下_拒裝(tmp_path, tmp_kit):
    env = _env(tmp_path, FCBAIX_INSTALL_DIR=str(tmp_kit / "fixed"))
    env.pop("FCBAIX_TEST_TMP_OK")
    r = _install(env, installer=tmp_kit / INSTALLER.name)
    assert r.returncode != 0 and "靜默失效" in r.stderr
    assert not (tmp_path / "tab").exists(), "拒裝時不可以動 crontab"
    assert not (tmp_kit / "fixed").exists(), "拒裝時不可以建目錄"


@needs_sh
def test_第一次安裝沒指定固定目錄_就停下來(tmp_path):
    env = _env(tmp_path)
    env.pop("FCBAIX_INSTALL_DIR")
    r = _install(env)
    assert r.returncode != 0 and "FCBAIX_INSTALL_DIR=" in r.stderr
    assert not (tmp_path / "tab").exists()


@needs_sh
def test_舊的_tmp_排程換成固定目錄_只留一行_其他行不動(tmp_path, tmp_kit):
    """使用者 2026-10-14 在 test1T 從 /tmp/lab 裝過；重裝要把那行換掉，不是再加一行。"""
    env = _env(tmp_path)
    old = f"0 9 22-28 * * /usr/bin/ksh {tmp_kit}/run_fcb_aix_monthly.ksh >/dev/null 2>&1 # {MARK_ID}"
    (tmp_path / "tab").write_text(f"5 1 * * * /opt/x/backup.sh\n{old}\n")
    r = _install(env, installer=tmp_kit / INSTALLER.name)
    assert r.returncode == 0, r.stderr
    assert "已換掉舊的排程" in r.stdout
    lines = (tmp_path / "tab").read_text().splitlines()
    assert lines[0] == "5 1 * * * /opt/x/backup.sh"
    assert _marked(tmp_path) == [
        f"0 9 22-28 * * /usr/bin/ksh {tmp_path}/inst/run_fcb_aix_monthly.ksh >/dev/null 2>&1 # {MARK_ID}"]
    assert len(lines) == 2


@needs_sh
def test_重裝沒設環境變數_沿用排程裡的固定目錄(tmp_path, tmp_kit):
    env = _env(tmp_path)
    assert _install(env, installer=tmp_kit / INSTALLER.name).returncode == 0
    env.pop("FCBAIX_INSTALL_DIR")
    (tmp_kit / "fcb_aixV999.ksh").write_text("exit 0 # new\n")
    r = _install(env, installer=tmp_kit / INSTALLER.name)
    assert r.returncode == 0, r.stderr
    assert "沿用排程裡的固定目錄" in r.stdout and "已安裝，略過" in r.stdout
    assert "new" in (tmp_path / "inst" / "fcb_aixV999.ksh").read_text(), "重裝要更新固定目錄裡的腳本"


@needs_sh
def test_舊排程在_tmp_時_不沿用那個目錄(tmp_path, tmp_kit):
    env = _env(tmp_path)
    env.pop("FCBAIX_INSTALL_DIR")
    env.pop("FCBAIX_TEST_TMP_OK")
    (tmp_path / "tab").write_text(
        f"0 9 22-28 * * /usr/bin/ksh {tmp_kit}/run_fcb_aix_monthly.ksh >/dev/null 2>&1 # {MARK_ID}\n")
    r = _install(env, installer=tmp_kit / INSTALLER.name)
    assert r.returncode != 0 and "FCBAIX_INSTALL_DIR=" in r.stderr


@needs_sh
def test_固定目錄是符號連結就停(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "inst").symlink_to(real)
    r = _install(_env(tmp_path))
    assert r.returncode != 0 and "符號連結" in r.stderr
    assert not list(real.iterdir())


@needs_sh
def test_寫設定檔_log_目錄_重裝沿用(tmp_path):
    env = _env(tmp_path)
    r = _install(env)
    assert r.returncode == 0, r.stderr
    conf = tmp_path / "inst" / "fcb_aix.conf"
    assert f"LOG_DIR={tmp_path}/log" in conf.read_text().splitlines()
    assert conf.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "log").stat().st_mode & 0o777 == 0o700
    env.pop("FCBAIX_LOG_DIR")
    env.pop("FCBAIX_INSTALL_DIR")
    r = _install(env)
    assert r.returncode == 0, r.stderr
    assert "沿用" in r.stdout and f"LOG_DIR={tmp_path}/log" in conf.read_text()


@needs_sh
def test_第一次安裝沒指定_log_目錄_就停下來(tmp_path):
    env = _env(tmp_path)
    env.pop("FCBAIX_LOG_DIR")
    r = _install(env)
    assert r.returncode != 0 and "FCBAIX_LOG_DIR=" in r.stderr
    assert not (tmp_path / "tab").exists() and not (tmp_path / "inst").exists()


@needs_sh
def test_log_目錄在_var_tmp_拒裝(tmp_path):
    env = _env(tmp_path, FCBAIX_LOG_DIR="/var/tmp/fcbaudit_x")
    env.pop("FCBAIX_TEST_TMP_OK")
    env["FCBAIX_INSTALL_DIR"] = "/nonexistent-fixed-dir"
    r = _install(env)
    assert r.returncode != 0
    assert not (tmp_path / "tab").exists()


@needs_sh
def test_舊歷史目錄_逐檔搬移_核對後才刪_衝突保留(tmp_path):
    old = tmp_path / "oldhist"
    old.mkdir()
    (old / "fcbaudit_h_20261013_0900.tar.Z").write_bytes(b"arc1")
    (old / "fcbaudit_monthly.log").write_text("line1\n")
    (old / "same.txt").write_text("same")
    (old / "clash.txt").write_text("old version")
    (old / ".run_20261013_0900_1").mkdir()
    log = tmp_path / "log"
    log.mkdir()
    (log / "same.txt").write_text("same")
    (log / "clash.txt").write_text("new version")
    env = _env(tmp_path)
    r = _install(env)
    assert r.returncode == 0, r.stderr
    assert (log / "fcbaudit_h_20261013_0900.tar.Z").read_bytes() == b"arc1"
    assert (log / "fcbaudit_monthly.log").read_text() == "line1\n"
    assert sorted(p.name for p in old.iterdir()) == [".run_20261013_0900_1", "clash.txt"]
    assert (old / "clash.txt").read_text() == "old version", "內容不同的不可以蓋、不可以刪"
    assert (log / "clash.txt").read_text() == "new version"
    assert "保留" in r.stdout
    # 冪等：再跑一次不出錯、結果不變
    r = _install(env)
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in old.iterdir()) == [".run_20261013_0900_1", "clash.txt"]
    assert not [p for p in log.iterdir() if p.name.startswith(".mv_")], "不可以留下搬一半的暫存檔"


@needs_sh
def test_舊歷史目錄_全部搬完就移除(tmp_path):
    old = tmp_path / "oldhist"
    old.mkdir()
    (old / "fcbaudit_h_20261013_0900.tar.Z").write_bytes(b"arc1")
    r = _install(_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert not old.exists()
    assert (tmp_path / "log" / "fcbaudit_h_20261013_0900.tar.Z").read_bytes() == b"arc1"
    r = _install(_env(tmp_path))
    assert r.returncode == 0, r.stderr


@needs_sh
def test_從_tmp_執行移除仍然可以(tmp_path, tmp_kit):
    """使用者要從 /tmp/lab 把舊排程移掉，-r 不能被擋。"""
    env = _env(tmp_path)
    env.pop("FCBAIX_INSTALL_DIR")
    env.pop("FCBAIX_TEST_TMP_OK")
    (tmp_path / "tab").write_text(
        f"0 9 22-28 * * /usr/bin/ksh {tmp_kit}/run_fcb_aix_monthly.ksh >/dev/null 2>&1 # {MARK_ID}\n")
    r = _install(env, "-r", installer=tmp_kit / INSTALLER.name)
    assert r.returncode == 0, r.stderr
    assert MARK_ID not in (tmp_path / "tab").read_text()


FAKE_AUDIT = """d="${FCBAIX_OUTDIR:?}"; s=`date +%Y%m%d_%H%M`
printf 'x\\nCheck summary: ok\\n' > "$d/FCB_h_10.99.0.1_$s.txt"
printf '<html>\\n' > "$d/FCB_h_10.99.0.1_$s.html"
printf '<html>\\n' > "$d/FCB_h_10.99.0.1_${s}_audit.html"
printf 'dbg\\n' > "$d/FCB_h_10.99.0.1_$s.debug.log"
"""


@needs_sh
def test_包裝程式_取件點_歷史備份_housekeeping(tmp_path):
    if shutil.which("compress") is None and shutil.which("gzip") is None:
        pytest.skip("沒有 compress 也沒有 gzip")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shutil.copy(WRAPPER, bindir / WRAPPER.name)
    (bindir / "fcb_aixV999.ksh").write_text(FAKE_AUDIT)
    pick, hist = tmp_path / "pick", tmp_path / "hist"
    pick.mkdir()
    hist.mkdir()
    old = hist / "fcbaudit_h_20200101_0900.tar.Z"
    other = hist / "keep_20200101.tar.Z"
    for f in (old, other):
        f.write_text("x")
        os.utime(f, (1577836800, 1577836800))   # 2020-01-01
    env = dict(os.environ, FCBAIX_FORCE_RUN="1", FCBAIX_KSH=SH,
               FCBAIX_PICKUP_DIR=str(pick), FCBAIX_HIST_DIR=str(hist))
    r = subprocess.run([SH, str(bindir / WRAPPER.name)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + (hist / "fcbaudit_monthly.log").read_text()

    picked = sorted(p.name for p in pick.iterdir())
    assert len(picked) == 4 and all(n.startswith("FCB_h_10.99.0.1_") for n in picked)
    assert all((pick / n).stat().st_mode & 0o777 == 0o644 for n in picked)

    arcs = [p for p in hist.iterdir() if re.match(r"fcbaudit_.+_\d{8}_\d{4}\.tar\.(Z|gz)$", p.name)]
    assert len(arcs) == 1 and arcs[0].stat().st_size > 0
    assert arcs[0].stat().st_mode & 0o777 == 0o600
    assert hist.stat().st_mode & 0o777 == 0o700
    assert not old.exists(), "超過三年的本工具備份要刪"
    assert other.exists(), "不是本工具檔名的不可以刪"
    assert not [p for p in hist.iterdir() if p.name.startswith(".run_")], "工作目錄要清掉"
    audit = [p for p in hist.iterdir() if p.name.endswith("_audit.html")]
    assert len(audit) == 1 and audit[0].stat().st_mode & 0o777 == 0o600, "log 目錄也要放一份稽核版報告"
    assert any(n.endswith("_audit.html") for n in picked), "取件點 /tmp 那份照舊"


@needs_sh
def test_包裝程式從同目錄設定檔讀_log_目錄(tmp_path):
    if shutil.which("compress") is None and shutil.which("gzip") is None:
        pytest.skip("沒有 compress 也沒有 gzip")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shutil.copy(WRAPPER, bindir / WRAPPER.name)
    (bindir / "fcb_aixV999.ksh").write_text(FAKE_AUDIT)
    logdir = tmp_path / "logdir"
    (bindir / "fcb_aix.conf").write_text(f"# x\nLOG_DIR={logdir}\n")
    pick = tmp_path / "pick"
    pick.mkdir()
    env = dict(os.environ, FCBAIX_FORCE_RUN="1", FCBAIX_KSH=SH, FCBAIX_PICKUP_DIR=str(pick))
    env.pop("FCBAIX_HIST_DIR", None)
    r = subprocess.run([SH, str(bindir / WRAPPER.name)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (logdir / "fcbaudit_monthly.log").exists()
    assert [p for p in logdir.iterdir() if re.match(r"fcbaudit_.+\.tar\.(Z|gz)$", p.name)]


@needs_sh
def test_不是禮拜日就什麼都不做(tmp_path):
    hist = tmp_path / "hist"
    env = dict(os.environ, FCBAIX_HIST_DIR=str(hist), FCBAIX_PICKUP_DIR=str(tmp_path))
    env.pop("FCBAIX_FORCE_RUN", None)
    fake_date = tmp_path / "fakebin"
    fake_date.mkdir()
    # 包裝程式自己設 PATH，所以改寫一份把 date 換成固定回 3（禮拜三）
    src = _read(WRAPPER).replace("_dow=`date +%w`", "_dow=3")
    w = tmp_path / "w.ksh"
    w.write_text(src)
    r = subprocess.run([SH, str(w)], env=env, capture_output=True, text=True)
    assert r.returncode == 0
    assert not hist.exists()
    # 手動跑的人要看得出「有跑、只是今天不是」（2026-10-14 使用者週三手動跑，什麼都沒印）
    assert "今天不是當月第四個禮拜日" in r.stdout and "FCBAIX_FORCE_RUN=1" in r.stdout


@needs_sh
def test_禮拜日但不在22到28號_也略過(tmp_path):
    """手動在第一個禮拜日跑，不能當成第四個禮拜日去跑。"""
    hist = tmp_path / "hist"
    env = dict(os.environ, FCBAIX_HIST_DIR=str(hist), FCBAIX_PICKUP_DIR=str(tmp_path))
    env.pop("FCBAIX_FORCE_RUN", None)
    src = (_read(WRAPPER).replace("_dow=`date +%w`", "_dow=0")
           .replace("_dom=`date +%d`", "_dom=05"))
    w = tmp_path / "w.ksh"
    w.write_text(src)
    r = subprocess.run([SH, str(w)], env=env, capture_output=True, text=True)
    assert r.returncode == 0
    assert not hist.exists()
    assert "今天不是當月第四個禮拜日" in r.stdout
