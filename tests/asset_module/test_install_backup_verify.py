"""備份驗證不可被 pipefail 下的 SIGPIPE 誤判（2026-11-16 正式機踩到）。

install.sh 開頭 `set -uo pipefail`；舊寫法 `tar tzf x | grep -q pat` 在封存檔很大時，
grep 找到就提早結束，tar 收到 SIGPIPE，整條管線回非 0 → 誤判「備份驗證失敗」而中止安裝。
"""
import re
import subprocess
from pathlib import Path

INSTALL = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "install.sh"


def _bash(script: str, cwd=None):
    r = subprocess.run(["bash"], input=script.encode("utf-8"), capture_output=True, cwd=cwd)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def test_install_does_not_pipe_tar_into_grep_q():
    text = INSTALL.read_text(encoding="utf-8")
    assert not re.search(r"tar tzf[^\n]*\|\s*grep -q", text), "不可用 tar tzf | grep -q（pipefail 下 SIGPIPE 誤判）"
    lines = [ln for ln in text.splitlines() if "app.tar.gz" in ln and "grep -c" in ln and "version.json" in ln]
    assert lines, "找不到改過的備份驗證行（應用 grep -c 讀完全部輸出）"


def test_backup_verify_expression_survives_large_archive(tmp_path):
    src = tmp_path / "src" / "backend"
    src.mkdir(parents=True)
    (src / "a_version.json").write_text("{}")  # 排序在前，grep 提早命中
    (src / "version.json").write_text("{}")
    for i in range(20000):
        (src / f"f{i:05d}.py").write_text("x")
    script = (
        "set -uo pipefail\n"
        "tar czf app.tar.gz -C src backend\n"
        "BK_DIR=.\n"
        'if tar tzf "$BK_DIR/app.tar.gz" >/dev/null 2>&1 && '
        '[ "$(tar tzf "$BK_DIR/app.tar.gz" 2>/dev/null | grep -c \'^backend/version.json$\')" -ge 1 ]; '
        "then echo VERIFY_OK; else echo VERIFY_FAIL; fi\n"
    )
    rc, out, err = _bash(script, cwd=tmp_path)
    assert "VERIFY_OK" in out, (rc, out, err)


def test_deploy_migrates_verify_snapshot_before_verify_numbers():
    """正式庫舊結構時，核對複本要先遷移（init_db）再跑 verify_numbers，不然誤判 no such column。"""
    text = (INSTALL.parent / "deploy.sh").read_text(encoding="utf-8")
    i_snap = text.index('VERIFY_DB="$DATA/.verify_snapshot.db"')
    i_mig = text.index('db.init_db()', i_snap)
    i_ver = text.index('verify_numbers.py"; then', i_snap)
    assert i_snap < i_mig < i_ver, "遷移複本必須在建立複本之後、verify_numbers 之前"
    assert 'ASSET_DB_PATH="$VERIFY_DB"' in text[i_mig - 200:i_mig + 200], "遷移只能作用在複本"
