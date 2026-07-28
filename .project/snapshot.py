#!/usr/bin/env python3
# C1 強制層 — 現狀快照產生器。產出 CURRENT_STATE.md。
# 規則：CURRENT_STATE.md 一律由本腳本生成，禁止手改。
#       新 session 第一件事就是跑本腳本，並以它為現狀真相，不信任其他手寫進度文件。
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 主控台/管線預設非 utf-8，強制統一
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace")


head = run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip() or "(尚無 commit)"
log = run(["git", "log", "--oneline", "-10"]).stdout.strip() or "(尚無 commit)"
checks = run([sys.executable, str(ROOT / ".project" / "checks.py")])

try:
    data = json.loads((ROOT / ".project" / "decisions.json").read_text(encoding="utf-8"))
except Exception:
    data = {"decisions": []}

lines: list[str] = []
lines.append("# CURRENT_STATE（自動生成，禁止手改）")
lines.append("")
lines.append("> 本檔由 `python .project/snapshot.py` 生成。任何手寫進度、WBS、交付紀錄都可能過期；")
lines.append("> 以本檔與 `python .project/checks.py` 的即時輸出為準。")
lines.append("")
lines.append(f"- 目前 HEAD: `{head}`")
lines.append("")
lines.append("## 強制層檢查即時結果")
lines.append("")
lines.append("```")
lines.append(checks.stdout.strip())
lines.append("```")
lines.append("")
lines.append("## 已登錄決策（decisions.json）")
lines.append("")
lines.append("| id | choice | status | note |")
lines.append("|---|---|---|---|")
for d in data.get("decisions", []):
    lines.append(f"| {d.get('id', '')} | {d.get('choice', '')} | {d.get('status', '')} | {d.get('note', '')} |")
lines.append("")
lines.append("## 最近 10 筆 commit")
lines.append("")
lines.append("```")
lines.append(log)
lines.append("```")

(ROOT / "CURRENT_STATE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"已更新 CURRENT_STATE.md（HEAD {head}）")
