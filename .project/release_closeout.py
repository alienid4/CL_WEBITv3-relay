"""發版收尾四件檢查（2026-10-30 使用者要求：中繼庫又被我忘了）。

用法：py -3 .project/release_closeout.py
每次 push main 之後、對使用者說「完成」之前跑一次；有 ❌ 就不准說完成。
只讀，不改任何東西；查不到（離線）會標「未驗證」，不當作通過。

四件：
  1 私有 Git 已推、且有對應 tag
  2 221 正在跑的版號＝這一版（且工作區乾淨）
  3 公司中繼庫 relay 已含最新程式（落後只算 APP/ tests/ .project/ 的變動）
  4 交接摘要已寫到這一版
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELAY_SSH = "git@github.com:alienid4/CL_WEBITv3-relay.git"
HOST_221 = "root@demo-host"


def sh(*args: str, timeout: int = 40) -> str:
    try:
        r = subprocess.run(args, cwd=ROOT, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return r.stdout.strip()
    except Exception:  # noqa: BLE001 - 查不到回空，呼叫端標未驗證
        return ""


def version() -> str:
    p = ROOT / "APP" / "asset-module" / "backend" / "version.json"
    return json.loads(p.read_text(encoding="utf-8-sig"))["version"]


def relay_source_sha() -> str:
    d = tempfile.mkdtemp(prefix="closeout_relay_")
    try:
        sh("git", "-C", d, "init", "-q")
        r = subprocess.run(
            ["git", "-C", d, "fetch", "-q", "--depth=1", "--filter=tree:0", RELAY_SSH, "main"],
            capture_output=True, timeout=40)
        if r.returncode != 0:
            return ""
        msg = sh("git", "-C", d, "log", "-1", "--format=%s%n%b", "FETCH_HEAD")
        m = re.search(r"main@([0-9a-f]{7,40})", msg)
        return m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        return ""
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows cp950 印不出 ✅
    ver = version()
    bad = 0
    rows: list[tuple[str, str, str]] = []

    # 1 私有 Git ＋ tag
    sh("git", "fetch", "origin", "-q", "--tags")
    head = sh("git", "rev-parse", "HEAD")
    ahead = sh("git", "rev-list", "--count", "origin/main..HEAD")
    tag = sh("git", "tag", "--list", f"*-v{ver}")
    tag_remote = sh("git", "ls-remote", "--tags", "origin", f"*-v{ver}")
    ok1 = ahead == "0" and bool(tag) and bool(tag_remote)
    rows.append(("1 私有 Git 已推＋tag", "✅" if ok1 else "❌",
                 f"v{ver}　未推 commit={ahead or '?'}　tag={tag or '無'}"
                 f"{'（遠端已有）' if tag_remote else '（遠端沒有）'}"))

    # 2 221 版號
    out = sh("ssh", "-o", "ConnectTimeout=20", HOST_221,
             "curl -s localhost:8000/api/version", timeout=50)
    try:
        j = json.loads(out[out.index("{"):])
        ok2 = j.get("version") == ver and j.get("dirty") is False
        rows.append(("2 221 在跑這一版", "✅" if ok2 else "❌",
                     f"221={j.get('version')}　更版={j.get('started_at')}　dirty={j.get('dirty')}"))
    except Exception:  # noqa: BLE001
        ok2 = False
        rows.append(("2 221 在跑這一版", "❓", "連不上 221，未驗證（不算通過）"))

    # 3 公司中繼庫
    sha = relay_source_sha()
    if sha:
        n = sh("git", "rev-list", "--count", f"{sha}..origin/main", "--",
               "APP", "tests", ".project", ":(exclude).project/relay_state.json")
        ok3 = n == "0"
        rows.append(("3 公司中繼庫已更新", "✅" if ok3 else "❌",
                     f"relay 來源={sha[:8]}　落後程式變動={n}個 commit"
                     f"{'' if ok3 else '　→ py -3 .project/relay_local.py'}"))
    else:
        ok3 = False
        rows.append(("3 公司中繼庫已更新", "❓", "查不到 relay，未驗證（不算通過）"))

    # 4 交接摘要
    notes = sorted((ROOT / "AI").glob("交接摘要_*.md"))
    ok4 = bool(notes) and ver in notes[-1].read_text(encoding="utf-8", errors="replace")
    rows.append(("4 交接摘要寫到這一版", "✅" if ok4 else "❌",
                 f"{notes[-1].name if notes else '無檔'}　{'有' if ok4 else '沒有'} v{ver}"))

    for name, mark, detail in rows:
        print(f"{mark} {name}　{detail}")
        if mark != "✅":
            bad += 1
    print("\n全部到位，可以說「完成」。" if not bad else
          f"\n還有 {bad} 件沒到位——不准對使用者說「完成」，先補齊。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
