#!/usr/bin/env python3
"""一鍵產出「交給協作 AI（公司 Codex）的工作包」。

    python .project/make_codex_pack.py [輸出目錄]

產出一個 zip，裡面是：
    AGENTS.md                       規則（Codex 會自動讀這個檔名）
    開場指令.md                      使用者直接複製貼給 Codex 的第一則訊息
    CODEX_HANDOFF_TEMPLATE.md       他交件用的格式
    APP/ tests/                     **已逐字去識別化**的程式碼

## 為什麼要有這支

交接要成立得同時滿足三件事，缺一件就會出事：
  1. 他知道規則（AGENTS.md）
  2. 他知道怎麼交（範本）
  3. **送出去的東西不含真實 IP／主機名／公司識別字**

第 3 件靠人記得是不可靠的——2026-08-20 已經證明過一次（`git add -A` 把資產庫傾印
推上 GitHub）。所以這支**強制跑殘留掃描，掃到任何一處就中止不產出**，
跟 make_relay／make_patch 同一套規則、同一個立場：寧可不送，也不要送出半成品。

## 跟 make_relay 的差別

make_relay 是「推上 public GitHub 的完整快照」，會帶 .project/ 那些開發工具。
這支只給協作 AI 需要的：程式碼 + 測試 + 三份文件。**不帶 .project/**——
他不需要打包工具、部署腳本、決策紀錄，那些只會讓他以為自己也該碰。

⚠️ 一律**不帶 `AI/` 與 `docs/`**（真實 IP、拓撲、資產庫傾印都在那）。
唯二例外是明確要給他的那兩份文件，而且它們產出前也會被掃過。
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
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".project"))

# ⚠️ relay clone 上沒有這支（它含真實值，刻意不進 relay），所以這裡不能硬 import：
# 硬 import 會讓 toAI.bat 在公司端直接 ImportError 掛掉，而使用者只會看到一串
# traceback，不知道「其實你不需要跑這支」。改成缺了就給 None，由 main() 判斷。
try:
    from desensitize_rules import (  # noqa: E402
        REPLACEMENTS,
        RESIDUAL_ALLOW,
        RESIDUAL_PATTERNS,
        TEXT_EXT,
    )
except ImportError:
    REPLACEMENTS, RESIDUAL_ALLOW, RESIDUAL_PATTERNS, TEXT_EXT = [], set(), [], set()

#: 只給他這些。刻意不含 .project/（開發工具）、AI/、docs/（含真實資料）。
INCLUDE_CODE = ["APP", "tests"]

#: 三份文件：來源路徑 → 包裡的檔名
DOCS = {
    "AGENTS.md": "AGENTS.md",
    "AI/CODEX_HANDOFF_TEMPLATE.md": "CODEX_HANDOFF_TEMPLATE.md",
    "AI/CODEX_開場指令.md": "開場指令.md",
}

SKIP_DIRS = {"node_modules", ".nuxt", ".output", "__pycache__", "data", ".git",
             ".pytest_cache", ".venv", "dist"}


def sanitize(text: str) -> tuple[str, int]:
    n = 0
    for old, new in REPLACEMENTS:
        c = text.count(old)
        if c:
            text = text.replace(old, new)
            n += c
    return text, n


def copy_one(src: Path, dst: Path) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() not in TEXT_EXT:
        shutil.copy2(src, dst)
        return 0
    try:
        raw = src.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        shutil.copy2(src, dst)
        return 0
    clean, n = sanitize(raw)
    dst.write_text(clean, encoding="utf-8")
    return n


def export(out: Path) -> tuple[int, int]:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    files = replaced = 0
    for item in INCLUDE_CODE:
        src_root = REPO / item
        if not src_root.exists():
            continue
        for src in src_root.rglob("*"):
            if not src.is_file():
                continue
            if any(p in SKIP_DIRS for p in src.relative_to(REPO).parts):
                continue
            rel = src.relative_to(REPO)
            replaced += copy_one(src, out / rel)
            files += 1

    for src_rel, dst_name in DOCS.items():
        src = REPO / src_rel
        if not src.exists():
            sys.exit(f"缺少必要文件：{src_rel}（交接三件套缺一不可，中止）")
        replaced += copy_one(src, out / dst_name)
        files += 1
    return files, replaced


def residual_scan(out: Path) -> list[str]:
    hits: list[str] = []
    for f in out.rglob("*"):
        if not f.is_file() or any(p in SKIP_DIRS for p in f.parts):
            continue
        if f.suffix.lower() in {".gz", ".zip", ".tar", ".sha256", ".whl"}:
            continue
        rel = f.relative_to(out).as_posix()
        if rel in RESIDUAL_ALLOW:
            continue
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat, why in RESIDUAL_PATTERNS:
            for m in re.finditer(pat, txt):
                line = txt[: m.start()].count("\n") + 1
                hits.append(f"{rel}:{line} [{why}] {m.group()[:40]}")
    return hits


def is_relay_checkout() -> bool:
    """這份 checkout 是不是 relay（去識別化後的快照）。

    判別方式：relay 刻意**不帶** desensitize_rules.py（那支裡面直接寫著真實 IP／
    主機名／公司識別字，送出去等於附上「真實值長什麼樣」）。所以規則檔不在，
    就代表這裡是 relay。

    這件事要判出來，是因為在 relay 上「再打一次包」是沒有意義的：
    那裡的程式碼**已經是去識別化過的產物**，直接給 Codex 就好。
    而且沒有規則檔也根本無法重跑掃描——硬跑只會得到「掃過了、乾淨」這種
    **看起來有驗證其實沒驗證**的結論，那比不掃更危險。
    """
    return not (REPO / ".project" / "desensitize_rules.py").exists()


def print_relay_instructions() -> None:
    print("偵測到：relay clone（去識別化後的快照）")
    print()
    print("這份 checkout 的程式碼**已經是去識別化過的產物**，不需要再打包一次。")
    print()
    print("直接這樣做：")
    print()
    print("  1. 把這整個資料夾給 Codex（排除 node_modules）")
    print("  2. 打開 AI/CODEX_開場指令.md，把「──────」框起來那段整段複製，")
    print("     貼給 Codex 當第一則訊息")
    print("  3. 它交回 Markdown 之後，把那份 .md email 出來，")
    print("     回到**有完整 repo 的那台**跑：")
    print("       python .project/apply_codex_md.py 那份.md")
    print("       python .project/apply_codex_md.py 那份.md --write")
    print()
    print("⚠️ 第 3 步不能在這裡跑——收成果要回到有完整 repo 的機器，")
    print("   因為那邊才有測試環境可以實跑與抽驗。")


def main() -> None:
    if is_relay_checkout():
        print_relay_instructions()
        return

    out_root = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "dist" / "codex"
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    stage = out_root / f"codex_pack_{stamp}"

    print(f"主 repo：{REPO}")
    files, replaced = export(stage)
    print(f"複製 {files} 個檔案，去識別化替換 {replaced} 處")

    print("\n=== 殘留掃描 ===")
    hits = residual_scan(stage)
    if hits:
        for h in hits[:40]:
            print(f"  {h}")
        if len(hits) > 40:
            print(f"  …共 {len(hits)} 處")
        shutil.rmtree(stage, ignore_errors=True)
        sys.exit("\n掃到殘留，**已刪除產出、不出包**。寧可不送也不要送出半成品。")
    print("乾淨，無殘留。")

    zip_path = out_root / f"codex_pack_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(stage).as_posix())
    shutil.rmtree(stage, ignore_errors=True)

    size_kb = zip_path.stat().st_size / 1024
    print(f"\n完成：{zip_path}")
    print(f"  大小：{size_kb:.1f} KB")
    print("\n給使用者的三步：")
    print("  1. 把這個 zip 給 Codex")
    print("  2. 打開包裡的「開場指令.md」，把框起來那段整段複製貼給它當第一則訊息")
    print("  3. 它交回 Markdown 之後：python .project/apply_codex_md.py <那份.md>")


if __name__ == "__main__":
    main()
