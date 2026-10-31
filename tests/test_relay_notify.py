"""relay-sync 失敗通知：一次事件一封信、成功自動結案（2026-10-13）。

為什麼要有測試：舊版 issue #4 被重複用了 33 次，每次失敗都在上面留言（留言＝寄信），
一個早上 09:58～11:18 塞了 10 封；恢復之後也不會自己關。這兩條路徑平常不會觸發
（要 relay 壞掉才走得到），等真的壞掉才發現通知邏輯錯，又是一早上的信。

做法：把 workflow 裡兩個通知 job 的 `run:` 原文抽出來，用假的 `gh`（記錄被怎麼呼叫）
在 bash 底下實跑四種情境——不是看 YAML 推論。jq 篩選式另外用真的 jq 驗（CI 上有）。

.github/ 不進 relay 快照，所以在快照裡這支整檔跳過。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "relay-sync.yml"
TITLE = "⚠️ relay 同步失敗——公司主機拿不到更新"

if not WF.is_file():  # pragma: no cover - relay 快照裡沒有 .github/
    pytest.skip(".github/ 不在 relay 快照裡，本測試只在主 repo 執行", allow_module_level=True)

BASH = shutil.which("bash")
# Windows 上 System32 的 bash.exe 是 WSL 轉接器，PATH／路徑語意不同，不拿它跑
if BASH and "system32" in BASH.lower():
    BASH = None


def _run_block(job: str) -> str:
    """抽出某個 job 第一個 `run: |` 區塊的原文（不靠 yaml 套件，CI 沒裝）。"""
    lines = WF.read_text(encoding="utf-8").splitlines()
    i = lines.index(f"  {job}:")
    while not lines[i].strip().startswith("run: |"):
        i += 1
    indent = len(lines[i]) - len(lines[i].lstrip()) + 2
    out = []
    for ln in lines[i + 1:]:
        if ln.strip() and len(ln) - len(ln.lstrip()) < indent:
            break
        out.append(ln[indent:])
    return "\n".join(out) + "\n"


FAKE_GH = r'''#!/usr/bin/env bash
# 假 gh：記下每次呼叫；issue list 依 FAKE_OPEN 回開著的 issue 號碼（已套過 jq 的樣子）
printf '%s\n' "$1 $2" >> "$GH_LOG"
case "$1 $2" in
  "issue list")
    if printf '%s' "$*" | grep -q '\.\[0\]'; then
      printf '%s\n' $FAKE_OPEN | head -1
    else
      printf '%s\n' $FAKE_OPEN
    fi ;;
  "issue view") printf '%s\n' "$FAKE_BODY" ;;
  "issue edit"|"issue create"|"issue comment"|"issue close")
    while [ $# -gt 0 ]; do
      case "$1" in --body|--comment) printf '%s' "$2" > "$GH_LOG.$1"; shift ;; esac
      shift
    done ;;
esac
'''


@pytest.fixture
def run_job(tmp_path):
    if not BASH:
        pytest.skip("沒有可用的 bash")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_bytes(FAKE_GH.encode("utf-8"))
    gh.chmod(0o755)
    log = tmp_path / "gh.log"

    def _run(job: str, open_issues: str = "", body: str = "") -> list[str]:
        script = tmp_path / f"{job}.sh"
        # 用 shell 函式蓋掉 gh，而不是改 PATH：Windows 路徑的磁碟冒號會把 PATH 切壞，
        # 一不小心就呼叫到真的 gh（第一次寫就踩到，真的 gh 拿假 token 回 401）。
        # 函式優先於任何 PATH 上的執行檔，真的 gh 不可能被叫到。
        shim = f'gh() {{ bash "{gh.as_posix()}" "$@"; }}\n'
        script.write_bytes((shim + _run_block(job)).encode("utf-8"))
        env = dict(os.environ,
                   GH_LOG=log.as_posix(), FAKE_OPEN=open_issues, FAKE_BODY=body,
                   GITHUB_REPOSITORY="o/r", GITHUB_SHA="abc1234",
                   RUN_URL="https://example.invalid/run/1", GH_TOKEN="")
        # 跟 GitHub Actions 的預設 shell 一樣：bash -eo pipefail
        r = subprocess.run([BASH, "-eo", "pipefail", script.as_posix()], env=env,
                           capture_output=True)
        assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    _run.log = log
    return _run


def test_失敗_沒有開著的_開新的一張(run_job):
    calls = run_job("notify-on-failure", open_issues="")
    assert calls == ["issue list", "issue create"]
    body = Path(str(run_job.log) + ".--body").read_text(encoding="utf-8")
    assert "自動留言「已恢復」並關閉" in body
    assert body.rstrip().splitlines()[-1].startswith("最近一次失敗：")


def test_失敗_已有開著的_不留言只靜默改內文(run_job):
    old = "第一行\n第一次失敗：x\n最近一次失敗：2026-10-13 09:58　run：舊　commit：舊"
    calls = run_job("notify-on-failure", open_issues="7", body=old)
    # 重點：沒有 comment、沒有 create —— 這兩個才會寄信
    assert calls == ["issue list", "issue view", "issue edit"]
    new = Path(str(run_job.log) + ".--body").read_text(encoding="utf-8")
    lines = new.splitlines()
    assert lines[:2] == ["第一行", "第一次失敗：x"]
    assert [ln for ln in lines if ln.startswith("最近一次失敗：")] == [lines[-1]]
    assert "abc1234" in lines[-1] and "舊" not in lines[-1]


def test_成功_有開著的_留言已恢復並關閉(run_job):
    calls = run_job("close-on-success", open_issues="4 9")
    assert calls == ["issue list", "issue close", "issue close"]
    msg = Path(str(run_job.log) + ".--comment").read_text(encoding="utf-8")
    assert msg.startswith("已恢復") and "abc1234" in msg and "example.invalid/run/1" in msg


def test_成功_沒有開著的_什麼都不做(run_job):
    assert run_job("close-on-success", open_issues="") == ["issue list"]


def test_兩個通知job的觸發條件():
    """成功／失敗兩條路徑互斥，而且都等 relay 跑完。"""
    text = WF.read_text(encoding="utf-8")
    for job, cond in (("notify-on-failure", "failure()"), ("close-on-success", "success()")):
        seg = text[text.index(f"  {job}:"):]
        head = seg[:seg.index("steps:")]
        assert "needs: relay" in head and f"if: {cond}" in head, job
        assert "issues: write" in head, job
    # 舊版的「每次失敗都 comment」不可以回來
    assert "gh issue comment" not in text


@pytest.mark.skipif(not shutil.which("jq"), reason="沒有 jq（CI 的 ubuntu runner 有）")
def test_jq篩選式_只認完整標題():
    """不用 --search（索引延遲，剛開的會搜不到又重開一張），改成拿全部再比完整標題。"""
    issues = [{"number": 3, "title": "別的 issue"},
              {"number": 5, "title": TITLE},
              {"number": 8, "title": TITLE + "（舊）"},
              {"number": 9, "title": TITLE}]
    text = WF.read_text(encoding="utf-8")
    for job, expect in (("notify-on-failure", "5"), ("close-on-success", "5\n9")):
        block = _run_block(job)
        # 照 bash 的規則取出雙引號字串：遇到 \" 不算結束（舊寫法 split('")') 會在 \"$TITLE\")) 那裡
        # 提早截斷，留下結尾的反斜線——jq 1.6 直接編譯失敗；這跟 emoji／中文無關）。
        m = re.search(r'--jq "((?:[^"\\]|\\.)*)"', block)
        assert m, job
        expr = re.sub(r'\\(["\\$`])', r"\1", m.group(1)).replace("$TITLE", TITLE)
        r = subprocess.run(["jq", "-r", expr], input=json.dumps(issues).encode("utf-8"),
                           capture_output=True)
        assert r.returncode == 0, r.stderr
        assert r.stdout.decode("utf-8").strip() == expect, job
    assert text  # 讀得到檔
