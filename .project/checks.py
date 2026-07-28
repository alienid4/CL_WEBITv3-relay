#!/usr/bin/env python3
# 強制關卡（可攜版）— pre-commit 會呼叫；也可手動 `python .project/checks.py`。
# 規則：任一項 FAIL -> exit 1 -> commit 被擋。輸出本身就是證據。
# 可攜設計：沒有測試時「警告放行」（不硬擋），讓任何專案丟進去就能跑；
#           想更嚴格 -> 設環境變數 REQUIRE_TESTS=1，沒測試就 FAIL。
#
# 風險分級（5.0.0.36 新增，取自 NEXT_BACKLOG N3）：loop.py 依切片的 "risk" 欄位
# 傳入 SLICE_RISK／SLICE_REVIEW 環境變數，分級跑不同深度的檢查——不是每次都跑同一組。
# 手動單獨跑（沒有 SLICE_RISK）視同 small，行為跟 5.0.0.35 以前完全一樣，向後相容。
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
REQUIRE_TESTS = os.environ.get("REQUIRE_TESTS", "0") == "1"
RISK_LEVELS = ("small", "medium", "large", "high-risk")
SLICE_RISK = os.environ.get("SLICE_RISK", "small")
if SLICE_RISK not in RISK_LEVELS:
    SLICE_RISK = "small"  # 沒帶/帶壞值一律當 small，不因為髒輸入而誤擋或誤放行
SLICE_REVIEW = os.environ.get("SLICE_REVIEW", "0") == "1"
# medium 以上：測試不能只是警告放行，要真的有跑；large 以上：多跑一輪更廣的敏感字串掃描；
# high-risk：backlog 裡這個切片一定要標 review:true，不能漏標
REQUIRE_TESTS_EFFECTIVE = REQUIRE_TESTS or SLICE_RISK in ("medium", "large", "high-risk")
RUN_EXTENDED_SCAN = SLICE_RISK in ("large", "high-risk")

BIN_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".xlsx", ".xls",
           ".zip", ".7z", ".rar", ".pyc", ".ico", ".woff", ".woff2"}
# 「程式碼」副檔名：本次 commit 有碰到才需要跑測試；純文件/資料檔（如 backlog.json、*.md）
# 不會改變測試結果，跳過那 30 秒白等（跟 stop-sanity hook 與 CLAUDE.md 的 .py/.html/.js 一致）
CODE_EXT = {".py", ".html", ".js", ".css", ".ts", ".jsx", ".tsx", ".sql",
            ".vue", ".sh", ".ps1", ".bash"}
SECRET_PATTERNS = [
    (r'(?i)password\s*[:=]\s*["\'][^"\']{3,}["\']', "疑似寫死密碼"),
    (r'(?i)(api[_-]?key|secret|access[_-]?token|token)\s*[:=]\s*["\'][^"\']{6,}["\']', "疑似寫死金鑰/Token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私鑰內容"),
]
# large/high-risk 才加開的較廣敏感字串掃描（比對照 SECURITY_COMMANDS.md 舊版 rg 清單精簡版）：
# 小修不用背這個成本，改動範圍大/風險高才值得多花時間全部掃一次
EXTENDED_SECRET_PATTERNS = [
    (r"(?i)connection[_ ]?string", "疑似連線字串"),
    (r"(?i)Trusted_Connection|UID\s*=|PWD\s*=", "疑似資料庫連線憑證"),
    (r"-----BEGIN OPENSSH PRIVATE KEY-----|-----BEGIN RSA PRIVATE KEY-----", "私鑰內容"),
    (r"(?i)\bBEGIN\s+CERTIFICATE\b", "憑證內容"),
]
SECRET_ALLOW = {".env.example", "checks.py"}  # checks.py 自己定義了這些正則字串，會自我命中，排除掉
# 引擎狀態檔：backlog/decisions/run_report 會記錄「失敗原因」，內容可能引用被擋下的密鑰字樣當證據，
# 不能因為留了證據就永遠過不了關（5.0 修正：自測抓到的真實 bug）
SECRET_ALLOW_PATHS = {".project/backlog.json", ".project/decisions.json", ".project/run_report.md"}


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace")


def tracked() -> list[Path]:
    out = run(["git", "ls-files"])
    return [ROOT / line for line in out.stdout.splitlines() if line.strip()]


def staged_files() -> list[str]:
    # 本次 commit 已 staged 的檔案（pre-commit 情境）。手動單獨跑 checks.py、沒 stage 任何
    # 東西時回傳空 list，呼叫端據此保守處理（照跑測試，不亂跳過）。
    out = run(["git", "diff", "--cached", "--name-only"])
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def only_non_code_staged() -> bool:
    # True 代表：這次 commit 確實有 stage 東西，但沒有一個是程式碼 -> 測試結果不會變 -> 可跳過。
    # 空 staged（手動跑）回 False，維持舊行為照跑測試。
    files = staged_files()
    if not files:
        return False
    return not any(Path(f).suffix.lower() in CODE_EXT for f in files)


results: list[tuple[str, bool, str]] = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


# 1. GIT 脊椎
git_ok = run(["git", "rev-parse", "--is-inside-work-tree"]).returncode == 0
rec("GIT 版控", git_ok, "" if git_ok else "尚未 git init")

# 2. 密鑰掃描（只掃 git 追蹤的檔）
leaks: list[str] = []
for f in tracked():
    if f.name in SECRET_ALLOW or f.suffix.lower() in BIN_EXT or not f.is_file():
        continue
    if f.relative_to(ROOT).as_posix() in SECRET_ALLOW_PATHS:
        continue
    try:
        txt = f.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    for pat, why in SECRET_PATTERNS:
        if re.search(pat, txt):
            leaks.append(f"{f.relative_to(ROOT).as_posix()} ({why})")
            break
rec("無寫死密鑰", not leaks, "; ".join(leaks[:6]))

# 3. 驗收測試（可攜：沒測試時警告放行，除非 REQUIRE_TESTS=1 或切片風險 >= medium）
# 本次 commit 只碰文件/資料檔（沒動程式碼）時跳過測試，省下 30 秒白等——測試結果不會變。
# 保守閘門：只有低風險（small，含手動 commit）才跳；medium 以上或 REQUIRE_TESTS=1 一律照跑。
test_dir = ROOT / "tests"
test_files = list(test_dir.rglob("test_*.py")) if test_dir.exists() else []
skip_tests = (
    test_files
    and SLICE_RISK == "small"
    and not REQUIRE_TESTS
    and only_non_code_staged()
)
if skip_tests:
    rec("驗收測試（本次無程式異動，跳過）", True,
        "只 staged 文件/資料檔，未碰 " + "/".join(sorted(CODE_EXT)) + "；測試結果不受影響")
elif test_files:
    r = run([sys.executable, "-m", "pytest", "-q"])
    out = (r.stdout + r.stderr).strip().splitlines()
    summary = next((ln.strip() for ln in reversed(out)
                    if "passed" in ln or "failed" in ln or "error" in ln), out[-1].strip() if out else "")
    rec("驗收測試通過", r.returncode == 0, summary)
elif REQUIRE_TESTS_EFFECTIVE:
    why = "REQUIRE_TESTS=1" if REQUIRE_TESTS else f"切片風險等級 {SLICE_RISK}（medium 以上強制要求）"
    rec("驗收測試", False, f"{why} 但 tests/ 沒有 test_*.py")
else:
    rec("驗收測試（警告放行）", True, "此專案尚無 pytest 測試；建議補上。設 REQUIRE_TESTS=1 或切片風險 >= medium 可改為強制")

# 3b. 進階敏感字串掃描（只有 large/high-risk 切片才跑，small/medium 不用背這個成本）
if RUN_EXTENDED_SCAN:
    ext_leaks: list[str] = []
    for f in tracked():
        if f.name in SECRET_ALLOW or f.suffix.lower() in BIN_EXT or not f.is_file():
            continue
        if f.relative_to(ROOT).as_posix() in SECRET_ALLOW_PATHS:
            continue
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat, why in EXTENDED_SECRET_PATTERNS:
            if re.search(pat, txt):
                ext_leaks.append(f"{f.relative_to(ROOT).as_posix()} ({why})")
                break
    rec(f"進階敏感字串掃描（{SLICE_RISK} 切片才跑）", not ext_leaks, "; ".join(ext_leaks[:6]))

# 3c. high-risk 切片一定要標 review:true，不能漏標（review.py 實際跑不跑是 loop.py 的事，這裡只管有沒有標）
if SLICE_RISK == "high-risk":
    rec("high-risk 切片已標 review:true", SLICE_REVIEW,
        "" if SLICE_REVIEW else "backlog.json 這個切片沒標 review:true，high-risk 一定要過對抗式審查")

# 4. 決策 <-> 文件一致性
conflicts: list[str] = []
dfile = ROOT / ".project" / "decisions.json"
try:
    data = json.loads(dfile.read_text(encoding="utf-8"))
except Exception:
    data = {"decisions": [], "doc_scan_ignore": []}

ignore = data.get("doc_scan_ignore", [])


def ignored(p: Path) -> bool:
    s = p.as_posix()
    return any(tok in s for tok in ignore)


mds = [p for p in ROOT.rglob("*.md") if not ignored(p)]
for d in data.get("decisions", []):
    if d.get("status") != "confirmed":
        continue
    for term in d.get("contradicts", []):
        for p in mds:
            try:
                if term.lower() in p.read_text(encoding="utf-8", errors="ignore").lower():
                    conflicts.append(f'{p.relative_to(ROOT).as_posix()} 出現「{term}」，'
                                     f'違反已確認決策 {d.get("id")}={d.get("choice")}')
            except Exception:
                pass
rec("決策與文件一致", not conflicts, "; ".join(conflicts[:6]))

# 輸出
print("=" * 56)
print("強制層檢查結果")
print("=" * 56)
all_ok = True
for name, ok, detail in results:
    tag = "PASS" if ok else "FAIL"
    all_ok = all_ok and ok
    line = f"[{tag}] {name}"
    if detail:
        line += f" -- {detail}"
    print(line)
print("=" * 56)
print("結論:", "全部 PASS，可以 commit / 宣稱完成" if all_ok else "有 FAIL，commit 會被擋，不得宣稱完成")
sys.exit(0 if all_ok else 1)
