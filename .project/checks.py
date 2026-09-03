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

# 2.5 去識別化殘留掃描（2026-08-19 新增，這是三次同樣故障的解法）
#
# 為什麼要在 commit 前擋：這個掃描本來只跑在 CI（push 之後）。公司識別字寫進
# APP/ 或 tests/ 時，要等 push → CI 跑完 → 失敗 → 有人注意到，才知道出事。
# 實際代價：2026-08-15 停一整天、2026-08-18 停了五天五個 commit（公司完全拿不到
# 更新，而且沒有人發現）。同樣一個檢查搬到 commit 前，代價是 5 秒。
#
# 只掃「會進 relay 的路徑」（INCLUDE 那幾個）——AI/ 與 docs/ 本來就是內網情報
# 集中處、不進 relay，在那裡寫真實 IP 是既有慣例，不該擋。
#
# 規則來自 desensitize_rules.py（跟 make_relay/make_patch 同一份）。那支檔案不進
# relay，所以 relay 副本裡 import 會失敗——那是正常的，副本本來就已經去識別化過，
# 不需要再掃，略過即可。用 SKIP 而不是靜默通過，免得哪天在主 repo 也悄悄不掃。
RELAY_SHIPPED_PREFIXES = ("APP/", "tests/", ".project/")
try:
    sys.path.insert(0, str(ROOT / ".project"))
    from desensitize_rules import (
        REPLACEMENTS,
        RESIDUAL_ALLOW,
        RESIDUAL_PATTERNS,
        TEXT_EXT,
    )

    residual: list[str] = []
    for f in tracked():
        rel = f.relative_to(ROOT).as_posix()
        if not rel.startswith(RELAY_SHIPPED_PREFIXES) or rel in RESIDUAL_ALLOW:
            continue
        if f.suffix.lower() not in TEXT_EXT or not f.is_file():
            continue
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        # ⚠️ 一定要先套替換表再掃，順序跟 make_relay 完全一致。
        # 直接掃原始碼會把「本來就會被替換掉」的正常內容（例如 deploy.sh 裡的
        # YOUR_SERVER_IP）通通判成失敗，變成一個天天誤報、最後被無視的檢查。
        # 這裡要回答的是：「送出去之後還會不會剩下識別字」。
        for old, new in REPLACEMENTS:
            txt = txt.replace(old, new)
        for pat, why in RESIDUAL_PATTERNS:
            m = re.search(pat, txt)
            if m:
                residual.append(f"{rel} [{why}] {m.group(0)[:40]}")
                break
    rec("無內網/公司識別字（會進 relay 的路徑）", not residual,
        "; ".join(residual[:6]) if residual
        else f"掃過 {'/'.join(RELAY_SHIPPED_PREFIXES)}，乾淨")
except ImportError:
    rec("無內網/公司識別字（SKIP）", True,
        "找不到 .project/desensitize_rules.py——這裡應該是 relay 副本，內容已去識別化，不需再掃")

# 2b. 這道關卡本身有沒有接上
#
# 2026-08-19 查證：這台開發機的 .git/hooks/ 是空的、core.hooksPath 也沒設，
# 也就是說 .project/pre-commit 寫好之後，從來沒有真的攔下過任何一次 commit。
# 上面那些檢查寫得再周全，沒接上就等於不存在——8/15、8/16、8/19 三次公司主機
# 拿不到更新，根因都在這裡，不是「有人忘了跑」。
#
# 所以 checks.py 要反過來檢查自己有沒有被掛上去。CI 也跑這支，因此哪台 clone
# 沒設定會在 CI 被指名講出來，不會再靜默失效。
# （relay 副本或非 git 環境不適用，略過。）
if (ROOT / ".git").exists():
    try:
        hooks_path = subprocess.run(
            ["git", "config", "core.hooksPath"],
            cwd=ROOT, capture_output=True, text=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        hooks_path = ""
    # 光是「檔案在」不夠，還要 git 記錄的模式是 100755。Windows 檔案系統沒有執行位元，
    # 在這邊 chmod +x 不會寫進版本；Linux clone 拿到的就是不可執行的 hook，而 git 對
    # 不可執行的 hook 是**安靜跳過**——不報錯、不警告，跟沒裝一模一樣。
    # 2026-08-19 部署到 221 時實際踩到（-rw-r-----）。修：git update-index --chmod=+x <path>
    hook_rel = ".project/hooks/pre-commit"
    try:
        mode = subprocess.run(
            ["git", "ls-files", "-s", hook_rel],
            cwd=ROOT, capture_output=True, text=True,
        ).stdout.split(" ", 1)[0]
    except Exception:  # noqa: BLE001
        mode = ""
    problems = []
    if hooks_path != ".project/hooks":
        problems.append(f"core.hooksPath 是 {hooks_path!r}，這台機器的 commit 沒有被攔檢"
                        f"（修：git config core.hooksPath .project/hooks）")
    if not (ROOT / ".project" / "hooks" / "pre-commit").exists():
        problems.append(f"{hook_rel} 不存在")
    elif mode != "100755":
        problems.append(f"{hook_rel} 版控模式是 {mode or '未知'} 而非 100755，"
                        f"Linux 上 git 會安靜跳過（修：git update-index --chmod=+x {hook_rel}）")
    rec("pre-commit 關卡已接上", not problems,
        "；".join(problems) if problems else "已生效（core.hooksPath=.project/hooks，模式 100755）")

# 2c. 不得把大檔（多半就是資料）放進版控
#
# 2026-08-20：`git add -A` 把 docs/asset_dump.7z（1.88MB 的資產庫傾印）掃進版控並
# push 上 GitHub，違反「真實資料別上 git」。事後用改寫歷史才清掉。
#
# 當時補了 .gitignore 擋副檔名，但那只擋得住「我想得到的格式」——下一次可能是 .bak、
# .tar.gz、.xlsx，或根本沒有副檔名。**大小是比副檔名更可靠的訊號**：程式碼不會大，
# 資料才會大。
#
# 閾值 500KB 是量出來的不是拍的：這個 repo 現有 298 個檔，最大的是 408KB 的
# package-lock.json（純文字），沒有任何檔案超過 500KB。所以現有 0 個誤報，
# 而那份 1880KB 的 dump 必被擋。誤報率為零很重要——會誤報的關卡，第一次就會有人
# 用 --no-verify 繞過去，從此形同虛設。
#
# 檢查的是 **index（暫存區）**不是 HEAD：pre-commit 執行時新檔還沒進 HEAD，
# 只看 HEAD 等於永遠抓不到當下這次要 commit 的東西。
MAX_TRACKED_KB = 500
# 已知且正當的大檔。加白名單要寫清楚為什麼，不能只是「它一直都在」。
BIG_FILE_ALLOW = {
    "APP/asset-module/frontend/package-lock.json",      # npm 鎖定檔，本來就大
    "APP/asset-module/frontend/public/vendor/cytoscape.min.js",  # 前端圖形函式庫
    "APP/asset-module/frontend/public/vendor/dagre.min.js",      # 同上，版面演算法
}
try:
    idx = subprocess.run(
        ["git", "ls-files", "-s", "-z"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    entries = []
    for ent in idx.split("\0"):
        if not ent.strip():
            continue
        meta, _, path = ent.partition("\t")
        parts = meta.split()
        if len(parts) >= 3:
            entries.append((parts[1], path))   # (blob sha, path)

    big: list[str] = []
    if entries:
        # 一次問完所有 blob 的大小，不要一個檔開一次 git（298 個檔會慢到有感）
        query = "\n".join(sha for sha, _ in entries) + "\n"
        sizes_out = subprocess.run(
            ["git", "cat-file", "--batch-check=%(objectsize)"],
            cwd=ROOT, input=query, capture_output=True, text=True,
        ).stdout.split()
        for (sha, path), raw in zip(entries, sizes_out):
            if path in BIG_FILE_ALLOW or not raw.isdigit():
                continue
            kb = int(raw) / 1024
            if kb > MAX_TRACKED_KB:
                big.append(f"{path}（{kb:.0f}KB）")

    rec("無大檔進版控", not big,
        "；".join(big[:5]) + f"　超過 {MAX_TRACKED_KB}KB。這通常是資料不是程式碼——"
        f"真實資料不進版控。確定要留就加進 .gitignore；真的是必要的大檔，"
        f"加進 checks.py 的 BIG_FILE_ALLOW 並寫明理由" if big
        else f"版控中無超過 {MAX_TRACKED_KB}KB 的檔案")
except Exception as exc:  # noqa: BLE001
    rec("無大檔進版控（無法檢查）", False, f"檢查本身出錯：{type(exc).__name__}: {exc}")

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

# N. 遠端自動化有沒有壞（2026-08-16 加）
#
# 為什麼要在這裡看：實際發生過——relay 同步從 2026-08-15 15:19 起連續失敗 9 次
# （去識別化殘留掃描擋下一個公司識別字），整整一天不只更新包沒出去，連程式碼都沒出去，
# 而**沒有任何人發現**。GitHub Actions 失敗是安靜的，要主動去點才看得到；最後是使用者
# 問「怎麼沒看到 patch」才偶然摸到。
#
# 這一項刻意**不是 FAIL**：紅燈的原因往往跟你手上這次改動無關，擋住 commit 會連
# 「要修它的那個 commit」都推不出去，結果就是大家學會繞過去——那比沒有還糟。
# 改成「一定看得到」：只有壞掉時才印，而且會寫進最後的結論行。
#
# 不裝 gh、沒網路、不是 GitHub repo 一律安靜跳過——這是額外的眼睛，不是新的相依。
ci_bad: str = ""
if os.environ.get("SKIP_CI_CHECK") != "1":
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import ci_status

        ci_bad = ci_status.last_failure()
    except Exception:  # noqa: BLE001 - 查不到就當沒這回事，不要因為它讓 checks 掛掉
        pass

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
if ci_bad:
    print(f"[WARN] 遠端自動化紅燈 -- 上一次 CI 失敗：{ci_bad}")
    print("       relay 同步壞著＝公司主機拿不到任何更新（程式碼與更新包都不會前進）。")
    print("       看原因： gh run view --log-failed")
print("=" * 56)
conclusion = "全部 PASS，可以 commit / 宣稱完成" if all_ok else "有 FAIL，commit 會被擋，不得宣稱完成"
if ci_bad:
    # 結論行也要講。只印在上面那幾行的話，會淹在一堆 PASS 裡被滑過去——
    # 而「被滑過去」正是這道檢查要防的事。
    conclusion += "；但遠端 CI 紅燈，公司拿不到更新，修完再交接"
print("結論:", conclusion)
sys.exit(0 if all_ok else 1)
