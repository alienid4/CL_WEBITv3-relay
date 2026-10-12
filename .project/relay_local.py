"""本機產出中繼快照並推到 relay repo——GitHub Actions 停掉時的正規替代路徑。

2026-09-24：個人帳號的 Actions 免費額度用完（2000/2000 分鐘，7 天後才重置），
job 根本沒排進去就失敗，公司端拿不到任何更新。**這支不是臨時繞道**，
它跑的是跟 CI 完全一樣的那幾步，差別只在「誰按下去」。

跟 .github/workflows/relay-sync.yml 一步一步對齊：
  1. **乾淨 clone origin/main**（＝CI 的 actions/checkout）
  2. build 前端（更新包要帶 build 好的 bundle，目標主機不能自己 build）
  3. 產累積更新包（make_patch baseline）
  4. 產去識別化快照（make_relay，內含**殘留掃描**與**在產出物上跑測試**）
  5. force push 到 relay repo

## 刻意的四件

**一定從乾淨的 clone 產，不用任何人的工作區。**
2026-09-24 第一版寫成「用本機 main 的工作區」，實際跑下去才發現那個工作區裡有
別人未提交的 `AI/實測紀錄_*.md`——**那種檔案會被一起複製進產出物**。
殘留掃描確實會擋，但那是最後一道防線，不該拿來當第一道。
CI 之所以沒這個問題，就是因為它每次都是全新 checkout。

**只從 main 產。** feature 分支的東西送到公司端，他們分不出來——
他們只知道「更新來了」。

**推送走 SSH，不在這支裡放 token。** CI 用 secret 是因為它沒有你的金鑰；
本機有，就不該把長期權杖寫進檔案或留在 shell 歷史裡。

**不碰 make_relay.py／desensitize_rules.py 的判準。** 它們的替換表直接寫著
真實 IP／主機名／公司識別字，本來就不該進產出物。殘留掃描或產出物測試沒過
就直接中止，**不提供任何略過的旗標**。

用法：
    py -3 .project/relay_local.py            # 產出＋推送
    py -3 .project/relay_local.py --dry-run  # 只產出，不推送（產物路徑會印出來）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

MAIN_SSH = "git@github.com:alienid4/CL_WEBITv3.git"
RELAY_SSH = "git@github.com:alienid4/CL_WEBITv3-relay.git"

#: 換行，避免在字串裡寫跳脫序列時被工具鏈吃掉。
LF = chr(10)

#: CI 當初釘的版本（relay-sync.yml 的 setup-python）。產出物測試要在同一版上跑，
#: 不然驗到的不是公司會遇到的情況。
WANT_PY = (3, 11)


def _reexec_on_venv() -> None:
    """不是 3.11 就用專案 .venv 重新起一次自己。

    ⚠️ make_relay 會用 `sys.executable` 在產出物上跑約 100 項測試
    （make_relay.py:441），而這支又用 `sys.executable` 去叫 make_relay——
    所以「誰起這支」決定了「用哪支 Python 驗公司要拿的東西」。

    2026-09-29 踩到：本機 `py -3` 是 3.14，CI 釘的是 3.11。
    同一天才剛因為 3.11 吃不下的語法卡了五天（f-string 反斜線、
    Path.read_text(newline=)），3.14 全綠、3.11 全炸。用 3.14 驗出來的綠燈
    證明不了公司端跑得動，比沒驗更危險——它讓人以為驗過了。
    """
    if sys.version_info[:2] == WANT_PY:
        return
    here = Path(__file__).resolve().parents[1]
    for rel in ("Scripts/python.exe", "bin/python"):
        cand = here / ".venv" / rel
        if cand.exists():
            print(f"目前是 Python {sys.version.split()[0]}，改用 .venv 重新起"
                  f"（CI 釘 {WANT_PY[0]}.{WANT_PY[1]}）：\n  {cand}", flush=True)
            # ⚠️ 不可以用 os.execv：**Windows 上它不會取代行程**。
            # 它 spawn 一個新行程之後父行程就自己結束，而且以 0 離開——
            # 於是呼叫端（背景工作、CI、任何看離開碼的東西）會看到「成功」，
            # 實際上真正的工作才剛開始跑，成敗完全沒人知道。
            # 2026-09-29 踩到：relay 推送回報 exit=0，relay HEAD 卻沒變，
            # 查下去才發現子行程還在跑 npm install。
            # 用 subprocess 等它跑完，把離開碼原樣傳回去。
            r = subprocess.run([str(cand), str(Path(__file__).resolve()), *sys.argv[1:]])
            raise SystemExit(r.returncode)
    raise SystemExit(
        f"!! 目前是 Python {sys.version.split()[0]}，需要 "
        f"{WANT_PY[0]}.{WANT_PY[1]}（CI 釘的版本），而且找不到專案 .venv。\n"
        "   產出物測試跑錯版本＝驗了等於沒驗，不自動放行。")


def _resolve(exe: str) -> str:
    """把指令名解析成完整路徑。

    Windows 上 npm 實際是 `npm.cmd`；Python 不透過 shell 執行時**不會**幫你補
    副檔名，會直接 FileNotFoundError，而訊息只說「系統找不到指定的檔案」——
    看不出是哪一個指令不見了。先解析，找不到就講出名字。
    不用 shell=True：那會讓參數多走一次 shell 解析，路徑帶空白時更脆。
    """
    got = shutil.which(exe)
    if not got:
        raise SystemExit(f"!! 找不到指令 `{exe}`，請確認它在 PATH 上。")
    return got


def run(cmd: list[str], cwd: Path, why: str) -> None:
    """跑一步。**失敗就中止**——中途失敗還繼續推，等於把半成品送出去。"""
    print(f"\n=== {why} ===\n$ {' '.join(cmd)}", flush=True)
    r = subprocess.run([_resolve(cmd[0])] + cmd[1:], cwd=str(cwd))
    if r.returncode != 0:
        print(f"\n!! 這一步失敗（離開碼 {r.returncode}）：{why}")
        print("   沒有推送任何東西。修好再跑一次。")
        raise SystemExit(r.returncode)


def out(cmd: list[str], cwd: Path) -> str:
    return subprocess.run([_resolve(cmd[0])] + cmd[1:], cwd=str(cwd),
                          capture_output=True, text=True).stdout.strip()


def main() -> None:
    _reexec_on_venv()
    dry = "--dry-run" in sys.argv[1:]
    tmp = Path(tempfile.mkdtemp(prefix="relay_"))
    src = tmp / "src"
    clone, patch_out, relay_out = tmp / "relay-clone", tmp / "patch-out", tmp / "relay-out"
    try:
        # ⚠️ 全歷史：make_patch 的累積包要 `git diff <baseline>..HEAD`，
        # baseline 是好幾十個 commit 以前的，淺層 clone 裡沒有那個 commit。
        # （CI 那邊是 fetch-depth: 0，同一個理由。）
        run(["git", "clone", "-b", "main", MAIN_SSH, str(src)], tmp,
            "乾淨 clone origin/main（不碰任何人的工作區）")
        sha = out(["git", "rev-parse", "HEAD"], src)
        print(f"main = {sha[:8]}")

        run(["git", "clone", "--depth", "1", RELAY_SSH, str(clone)], tmp,
            "Clone 既有 relay（承接 patches/ 用）")
        # 開工時 relay 的樣子。推之前要再看一次——見下面「有人插隊」那段。
        relay_before = out(["git", "rev-parse", "HEAD"], clone)

        frontend = src / "APP" / "asset-module" / "frontend"
        # npm install 不是 npm ci：lock 在 Windows 產的，缺 Linux 才解得出來的
        # optional 相依。理由見 workflow 裡那段長註解，這裡跟它保持一致。
        run(["npm", "install", "--no-audit", "--no-fund"], frontend, "安裝前端相依")
        run(["npm", "run", "build"], frontend, "Build 前端")

        run([sys.executable, str(src / ".project" / "make_patch.py"),
             "baseline", str(patch_out)], src, "產生累積更新包")

        # make_relay 內含殘留掃描＋在產出物上跑測試，沒過會自己 exit 非 0
        run([sys.executable, str(src / ".project" / "make_relay.py"), str(relay_out),
             "--relay", str(clone), "--add-patch", str(patch_out)], src,
            "產生去識別化快照（含殘留掃描＋在產出物上跑測試）")

        if dry:
            kept = Path(tempfile.mkdtemp(prefix="relay_out_")) / "relay-out"
            shutil.copytree(relay_out, kept)
            print(f"\n--dry-run：沒有推送。產出物留在\n  {kept}")
            return

        run(["git", "init", "-b", "main"], relay_out, "relay 不留歷史，每次全新單一 commit")
        run(["git", "add", "-A"], relay_out, "加入全部檔案")
        run(["git", "-c", "user.email=relay-bot@users.noreply.github.com",
             "-c", "user.name=relay-bot", "commit", "-m",
             f"中繼快照（本機產出，來自 main@{sha}）"], relay_out, "commit")
        run(["git", "remote", "add", "origin", RELAY_SSH], relay_out, "設定 relay 遠端")

        # ⚠️ 有人插隊就停手。
        # relay 是 force push 而且**不留歷史**（每次 git init 重來），所以另一台
        # 機器在這十幾分鐘內推過的東西會被這一推無聲蓋掉，而且救不回來——
        # 沒有 reflog、沒有前一個 commit。CI 時代不會有這問題：只有一個 runner。
        # 改成本機跑之後，公司 NB 與家裡 PC 都有金鑰，這就是真的會發生的事。
        now = out(["git", "ls-remote", RELAY_SSH, "refs/heads/main"], tmp).split()
        relay_now = now[0] if now else ""
        if relay_now != relay_before:
            raise SystemExit(
                "!! 這段時間有人推過 relay，停手，什麼都沒送出去。" + LF
                + f"   開工時：{relay_before[:8]}   現在：{relay_now[:8]}" + LF
                + "   force push 會把那一推蓋掉且救不回（relay 不留歷史）。" + LF
                + "   先確認那是誰推的、要不要保留，再重跑這支。")

        run(["git", "push", "-f", "-u", "origin", "main"], relay_out,
            "Force push 到 relay repo")

        # 留下「送出去的是哪一版」，給閘門判斷 relay 有沒有落後（見 checks.py）。
        # CI 時代不需要：push 到 main 就自動同步，落後不可能發生。
        # 現在要靠人按，就必須有東西看得出「按過了沒」。
        state = Path(__file__).resolve().parents[1] / ".project" / "relay_state.json"
        state.write_text(json.dumps({
            "_comment": "relay_local.py 每次成功推送後自己寫的。閘門拿它跟 origin/main 比，"
                        "看公司端落後幾個 commit。這是本機記錄，不是跟 relay 對帳的結果。",
            "main_sha": sha,
            "pushed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=2) + LF, encoding="utf-8", newline=LF)
        print(LF + "完成：relay 已更新，公司端可以拉了。")
        print(f"已記錄到 {state}（記得跟著 commit，閘門靠它判斷落後）")
    except SystemExit:
        # **失敗時不要刪。** make_relay 自己就說「輸出目錄留著供檢查」——
        # 第一版在 finally 裡無條件 rmtree，等於把它特地留下來的證據丟掉，
        # 於是只看得到「2 failed」，查不出是哪兩行 assert 掛了。
        print("\n產出物留在（供查證，查完請自行刪除）：")
        print(f"  {tmp}")
        raise
    else:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
