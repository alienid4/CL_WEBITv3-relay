"""V2 移植完成度：從 docs/V2移植清單.md 的打勾框重算百分比。

只數 `- [x] **V<n>**` / `- [ ] **V<n>**` 這種項目行（舊版頁面），收尾項目（X1…）不算。
用法：
  python .project/v2_progress.py          # 印一行：V2 移植完成度：x/y（z%）
  python .project/v2_progress.py --write  # 同上，並把清單開頭的「完成度：」那行改成最新數字
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIST = ROOT / "docs" / "V2移植清單.md"
ITEM = re.compile(r"^\s*- \[( |x|X)\] \*\*V(\d+)\*\*", re.M)
HEAD = re.compile(r"^完成度：.*$", re.M)


def compute(text: str) -> tuple[int, int]:
    marks = ITEM.findall(text)
    nums = [int(n) for _, n in marks]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if dup:
        raise SystemExit(f"清單裡項次重複：{', '.join('V%d' % n for n in dup)}")
    done = sum(1 for m, _ in marks if m.lower() == "x")
    return done, len(marks)


def main(argv: list[str]) -> int:
    text = LIST.read_text(encoding="utf-8")
    done, total = compute(text)
    pct = round(done * 100 / total) if total else 0
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(f"V2 移植完成度：{done}/{total}（{pct}%）")
    if "--write" in argv:
        new = HEAD.sub(f"完成度：{done}/{total}（{pct}%）", text, count=1)
        if new != text:
            LIST.write_text(new, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
