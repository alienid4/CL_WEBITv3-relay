#!/usr/bin/env python3
# C1 對抗式審查 (5.0) — 用「另一個乾淨 context 的 AI」試著推翻這次改動。
# 定位：checks.py 是機器關卡（git/密鑰/測試），review.py 是獨立審查者（找 bug、安全、done_when 未達成）。
# 建議 review_cmd 填「跟 exec_cmd 不同家」的引擎（Claude 寫 → Codex 審），跨引擎互驗最能抓盲點。
#
# 用法：
#   python .project/review.py --staged        --desc "..." --done-when "..."   # 審 staged 改動（loop 自動用這個）
#   python .project/review.py --last-commit   --desc "..." --done-when "..."   # 審最後一筆 commit
#   python .project/review.py --worktree      --desc "..." --done-when "..."   # 審所有未 commit 改動
# 判定：AI 輸出最後的「VERDICT: PASS」或「VERDICT: FAIL: <理由>」→ exit 0 / 1。
# 沒填 review_cmd（也沒有舊欄位 agent_cmd）→ exit 3（skipped，不假裝 PASS）。
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
PROJ = ROOT / ".project"
CONFIG = PROJ / "loop_config.json"
LOGS = PROJ / "logs"

MAX_DIFF_CHARS = 60000  # 太大的 diff 截尾，避免塞爆 context


def load_cfg() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                       encoding="utf-8", errors="replace")
    return r.stdout


def get_diff(mode: str) -> str:
    if mode == "staged":
        return git("diff", "--cached", "HEAD")
    if mode == "last-commit":
        return git("show", "--format=commit %h %s", "HEAD")
    return git("diff", "HEAD")  # worktree：含 staged + unstaged（不含未追蹤新檔）


def build_prompt(diff: str, desc: str, done_when: str) -> str:
    truncated = ""
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS]
        truncated = "\n（diff 過長已截斷，以上為前 60000 字元）"
    return "\n".join([
        "你是獨立的對抗式審查者。你唯一的任務是**試著推翻**下面這次程式改動——",
        "不是幫它講好話。請專注找四類問題：",
        "1. 正確性 bug（邏輯錯、邊界漏、會炸的路徑）",
        "2. 安全問題（密鑰、注入、權限、危險操作）",
        "3. 「完成的定義」其實沒達成（宣稱做到但 diff 裡看不到證據）",
        "4. User 角度（假裝你是實際要用這個功能的人，不是寫這段程式的人）：",
        "   規格都達成、測試都過，不代表好用——找「技術上對、但真人用起來會卡」的地方，",
        "   例如：操作要點幾次才到、錯誤訊息看不懂、資訊藏太深要點好幾層才找到、",
        "   跟這次切片『怎樣算對』的初衷（不只是字面條件）對不上。",
        "",
        f"這次切片要做的事：{desc or '（未提供）'}",
        f"完成的定義（怎樣算對）：{done_when or '（未提供）'}",
        "",
        "=== DIFF 開始 ===",
        diff or "（沒有任何改動——若切片宣稱有做事，這本身就是 FAIL）",
        "=== DIFF 結束 ===" + truncated,
        "",
        "規則：",
        "- 只讀不寫：不要修改任何檔案，只做審查。",
        "- 有疑慮傾向 FAIL（寧可誤殺讓人再看一次，不可放過）。",
        "- 最後一行必須是以下兩種格式之一（一定要是輸出的最後一行）：",
        "  VERDICT: PASS",
        "  VERDICT: FAIL: <一句話理由>",
    ])


def parse_verdict(output: str) -> tuple[str, str]:
    """從輸出找最後一個 VERDICT 行。回傳 (pass|fail|unknown, 理由)"""
    for line in reversed(output.strip().splitlines()):
        s = line.strip().lstrip("#*-• ").strip()
        if s.upper().startswith("VERDICT:"):
            rest = s[len("VERDICT:"):].strip()
            if rest.upper().startswith("PASS"):
                return "pass", ""
            if rest.upper().startswith("FAIL"):
                return "fail", rest[4:].lstrip(": ").strip()
    return "unknown", "AI 輸出裡找不到 VERDICT 行"


def main() -> int:
    ap = argparse.ArgumentParser(description="對抗式審查：獨立 AI 試著推翻這次改動")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--staged", action="store_true", help="審 staged 改動（預設）")
    g.add_argument("--last-commit", action="store_true", help="審最後一筆 commit")
    g.add_argument("--worktree", action="store_true", help="審所有未 commit 改動")
    ap.add_argument("--desc", default="", help="切片描述")
    ap.add_argument("--done-when", default="", help="完成的定義")
    args = ap.parse_args()

    mode = "last-commit" if args.last_commit else ("worktree" if args.worktree else "staged")
    cfg = load_cfg()
    cmd = (cfg.get("review_cmd") or cfg.get("agent_cmd") or "").strip()
    if not cmd:
        print("⚠ loop_config.json 沒填 review_cmd（也沒有舊欄位 agent_cmd），無法審查。")
        print("VERDICT: SKIPPED")
        return 3

    diff = get_diff(mode)
    prompt = build_prompt(diff, args.desc, args.done_when)
    timeout_min = cfg.get("agent_timeout_minutes", 15)

    print(f"審查模式：{mode}｜引擎：{cmd.split()[0]}｜上限 {timeout_min} 分")
    try:
        r = subprocess.run(shlex.split(cmd), cwd=ROOT, input=prompt,
                           capture_output=True, encoding="utf-8", errors="replace",
                           timeout=timeout_min * 60)
        output = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr and r.stderr.strip() else "")
        agent_failed = r.returncode != 0
    except subprocess.TimeoutExpired:
        output = "（審查引擎逾時）"
        agent_failed = True

    # 留檔
    LOGS.mkdir(parents=True, exist_ok=True)
    gi = LOGS / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    (LOGS / f"review_{stamp}.md").write_text(
        f"# review（{mode}）\n\n## Prompt\n```\n{prompt}\n```\n\n## 輸出\n```\n{output.strip()[-12000:]}\n```\n",
        encoding="utf-8")

    verdict, reason = parse_verdict(output)
    if agent_failed and verdict == "unknown":
        print("VERDICT: FAIL: 審查引擎執行失敗/逾時，無法取得判定")
        return 1
    if verdict == "pass":
        print("VERDICT: PASS")
        return 0
    if verdict == "fail":
        print(f"VERDICT: FAIL: {reason}")
        return 1
    # unknown：審查者沒照格式回，視同沒過（寧可誤殺）
    print(f"VERDICT: FAIL: {reason}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
