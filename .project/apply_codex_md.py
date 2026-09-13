#!/usr/bin/env python3
"""把協作 AI 交回來的 Markdown 還原成檔案，並立刻實跑。

    python .project/apply_codex_md.py <他交回來的.md> [--write]

**預設乾跑**（只列出會寫哪些檔、有什麼問題），加 `--write` 才真的落地。
理由跟分類匯入那支一樣：先看過再寫入，不要「執行了才知道做了什麼」。

## 為什麼需要這支

公司網路不能推 GitHub，所以交接靠一份 email 出來的 Markdown——**沒有 git diff**。
手動從 MD 複製貼上檔案，是那種「一定會有一次貼漏」的工作，而貼漏的症狀是
測試安靜地少一條，沒有人會發現。

所以格式訂得嚴（見 AI/CODEX_HANDOFF_TEMPLATE.md）：一節一個檔、完整路徑、
完整內容。嚴格的格式換來的就是這支腳本——機械式還原，不靠人眼。

## 它認得的格式

    ### 檔案 N：`相對路徑`
    ...（中間的說明不管）
    ```lang
    整份檔案內容
    ```

## 它會擋下來的事

  1. 路徑不在允許範圍（只准 tests/ 底下）——協作 AI 不改程式，這是硬邊界
  2. 內容裡有真實 IP／主機名／公司識別字——那份 MD 是 email 出來的，
     夾帶真實資料代表**已經外洩了**，這裡擋只是止血，仍要回報
  3. 用了 `...` 或「其餘不變」等省略寫法——那代表內容不完整，還原出來是壞的
"""
from __future__ import annotations

import io as _io
import sys as _sys

# Windows 主控台預設 cp950，印不出 ✓／⚠ 之類的字元會直接拋 UnicodeEncodeError，
# 整支腳本掛掉。2026-08-27 第一次跑就踩到。這支是給人直接跑的工具，
# **不能因為輸出字元掛掉**——所以在最前面就把 stdout 換成 UTF-8。
if hasattr(_sys.stdout, "buffer"):
    _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace")
    _sys.stderr = _io.TextIOWrapper(_sys.stderr.buffer, encoding="utf-8", errors="replace")

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".project"))

from desensitize_rules import RESIDUAL_PATTERNS  # noqa: E402

#: 只准寫這些目錄底下。協作 AI 只寫測試，程式碼不歸他改（見 AGENTS.md 第 1 節）。
ALLOW_PREFIX = ("tests/", "APP/asset-module/frontend/tests/")

#: 省略寫法的特徵。出現在**程式碼區塊**裡就代表內容不完整。
ELLIPSIS_HINTS = [
    (r"^\s*(#|//)\s*\.\.\.\s*$", "註解只寫 ... 表示省略"),
    (r"其餘不變", "「其餘不變」"),
    (r"（?以下省略）?", "「以下省略」"),
    (r"^\s*\.\.\.\s*$", "整行只有 ..."),
]

SECTION_RE = re.compile(
    r"^###\s*檔案\s*\d*\s*[：:]\s*[`\"']?([^\s`\"']+)[`\"']?\s*$", re.M)
FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```\s*$", re.M | re.S)


def parse(md: str) -> list[tuple[str, str]]:
    """回傳 [(相對路徑, 內容), ...]。"""
    out: list[tuple[str, str]] = []
    marks = list(SECTION_RE.finditer(md))
    for i, m in enumerate(marks):
        path = m.group(1).strip()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(md)
        body = md[m.end():end]
        fences = FENCE_RE.findall(body)
        if not fences:
            out.append((path, ""))       # 標成沒內容，後面會擋
            continue
        # 一節可能有多個區塊（預期表格旁邊也可能貼片段），取**最長**那個當檔案內容
        out.append((path, max(fences, key=len)))
    return out


def check(path: str, content: str) -> list[str]:
    problems: list[str] = []
    norm = path.replace("\\", "/").lstrip("./")

    if not content.strip():
        problems.append("這一節找不到程式碼區塊（```），沒有內容可以還原")

    if not norm.startswith(ALLOW_PREFIX):
        problems.append(
            f"路徑不在允許範圍。協作 AI 只寫測試，只准 {' 或 '.join(ALLOW_PREFIX)} 底下")

    for pat, why in ELLIPSIS_HINTS:
        if re.search(pat, content, re.M):
            problems.append(f"內容裡有省略寫法（{why}）——還原出來會是壞的，要請他重貼整份")

    for pat, why in RESIDUAL_PATTERNS:
        for m in re.finditer(pat, content):
            line = content[: m.start()].count("\n") + 1
            problems.append(
                f"第 {line} 行有真實識別字 [{why}] {m.group()[:40]} "
                f"—— ⚠️ 這份 MD 是 email 出來的，代表**已經外洩**，止血之外還要回報")
    return problems


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    md_path = Path(sys.argv[1])
    write = "--write" in sys.argv
    if not md_path.exists():
        sys.exit(f"找不到檔案：{md_path}")

    md = md_path.read_text(encoding="utf-8")
    items = parse(md)
    if not items:
        sys.exit("這份 MD 裡找不到任何「### 檔案 N：`路徑`」的區段。"
                 "請他照 AI/CODEX_HANDOFF_TEMPLATE.md 的格式重寫。")

    print(f"來源：{md_path}")
    print(f"找到 {len(items)} 個檔案區段\n")

    blocked = 0
    for path, content in items:
        problems = check(path, content)
        lines = content.count("\n") + 1 if content else 0
        head = "✗" if problems else "✓"
        print(f"{head} {path}　（{lines} 行）")
        for p in problems:
            print(f"    ⚠ {p}")
        if problems:
            blocked += 1

    if blocked:
        print(f"\n{blocked} 個區段有問題，**全部不寫入**——"
              f"部分套用會留下一半新一半舊，比沒套用更難查。")
        sys.exit(1)

    if not write:
        print("\n乾跑結束，沒有寫入任何檔案。確認無誤後加 --write 再跑一次。")
        return

    for path, content in items:
        dst = REPO / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        existed = dst.exists()
        dst.write_text(content if content.endswith("\n") else content + "\n",
                       encoding="utf-8")
        print(f"{'覆蓋' if existed else '新增'} {path}")

    print("\n=== 接下來一定要做的三件事 ===")
    print("1. 實跑（他交的一律當未驗證）：")
    print("     cd APP/asset-module/frontend && npm test")
    print("     python -m pytest tests/asset_module -q")
    print("2. 對照他標的「預期：綠」——標綠卻紅的，那是要查的訊號，不是雜訊")
    print("3. **抽驗**：故意把對應的程式改壞，看那條測試會不會紅。")
    print("   紅不了的退回去——沒看過它紅過的測試不算測試（AGENTS.md 3.5）")


if __name__ == "__main__":
    main()
