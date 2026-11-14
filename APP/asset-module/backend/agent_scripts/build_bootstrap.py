"""從三支正本腳本產生 `bootstrap_watcher.sh`。

## 為什麼要有這支產生器

`bootstrap_watcher.sh` 是自帶內容的一次性佈署腳本——它把 `run_auto_web3.sh`、
`install.sh`、`push_agent.sh` 三份**內嵌**在自己裡面，這樣要納管一台主機只要傳
這一個檔案上去就完整，不用帶四個檔案還要交代放哪。

但內嵌帶來一個很容易出事的地方：**同一段程式碼存在兩份**。原本靠註解寫著
「改了正本記得也改這裡」，那是靠人記得——而 2026-08-28 修本機提權漏洞時就發現，
如果只改了正本沒改內嵌版，實際佈到主機上的還是**有漏洞的舊版**，而且從
git diff 上看起來已經修好了。這種「看起來修好、實際沒修」比沒修更危險。

所以改成產生：正本只有一份，`bootstrap_watcher.sh` 是產物。
`tests/asset_module/test_agent_scripts_security.py` 會驗它跟正本同步，
不同步就紅——不再依賴任何人記得。

## 用法

    python build_bootstrap.py            # 重新產生
    python build_bootstrap.py --check    # 只檢查是否同步（測試用），不寫檔
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "bootstrap_watcher.sh"

#: 內嵌腳本 → heredoc 結束標記。標記必須是腳本內容裡不可能出現的字串，
#: 產生時會逐一驗證；撞到就直接失敗，不要產出一份會在中途斷掉的佈署腳本。
EMBEDS = [
    ("run_auto_web3.sh", "RUN_AUTO_WEB3_EOF"),
    ("install.sh", "INSTALL_SH_EOF"),
    ("push_agent.sh", "PUSH_AGENT_EOF"),
]

HEADER = """#!/bin/sh
# ============================================================================
# ⚠️ 這個檔案是**產生出來的**，不要直接改它。
#     改 run_auto_web3.sh / install.sh / push_agent.sh，然後跑：
#         python backend/agent_scripts/build_bootstrap.py
#     測試會驗這份跟三支正本同步（test_agent_scripts_security.py）。
# ============================================================================
#
# Push agent watcher 一次性佈署腳本——root 登入後只要這一行：
#
#     sudo sh bootstrap_watcher.sh
#
# 這是唯一需要 root/PAM 代登的步驟，執行完可以立刻結束那次 root session、
# 刪掉這支腳本。之後 watcher 自己每天用 root crontab 跑，日常 agent 資料回報
# 走 sysinfra 的 crontab，兩邊都不需要人再登入。
#
# 全程純文字可讀：沒有 base64、沒有下載、沒有 ExecutionPolicy 之類的旁路。
# 貼上去執行的人看得懂自己在跑什麼，稽核也查得到。
# ============================================================================

set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "bootstrap_watcher.sh 需要 root 權限執行（sudo sh bootstrap_watcher.sh）" >&2
    exit 1
fi

AGENT_USER="sysinfra"
BIN_DIR="/opt/webit3-agent/bin"
INCOMING_DIR="/var/lib/webit3-agent/incoming"
LOG_DIR="/caslog/webit3_agent"

mkdir -p "$BIN_DIR" "$LOG_DIR/done" "$LOG_DIR/failed" "$LOG_DIR/archive"

# ---- 投放目錄：0730 root:sysinfra ----
# 這是這次佈署最重要的一行。舊版讓 root 排程去撿 /tmp/webit3_agent_* 執行，
# 而 /tmp 全域可寫 → 任何本機帳號都能讓 root 幫他安裝任意程式（真的提權，
# 不是外觀問題，說明見 install.sh 開頭）。
#   owner root  ：只有 root 能讀取內容、決定要不要處理
#   group 上傳帳號：wx——能寫入、能進入自己知道名字的目錄，但不能列出目錄
#   other 0     ：其他本機帳號完全碰不到
if ! id "$AGENT_USER" >/dev/null 2>&1; then
    echo "找不到 $AGENT_USER 帳號——請先建立收集帳號再跑這支腳本" >&2
    exit 1
fi
mkdir -p "$INCOMING_DIR"
chown "root:$AGENT_USER" "$INCOMING_DIR"
chmod 730 "$INCOMING_DIR"
"""

FOOTER = """
chmod 755 "$BIN_DIR/run_auto_web3.sh" "$BIN_DIR/install.sh" "$BIN_DIR/push_agent.sh"
chown root:root "$BIN_DIR/run_auto_web3.sh" "$BIN_DIR/install.sh" "$BIN_DIR/push_agent.sh"

# ---- 註冊 root 的 daily crontab（不是 sysinfra 的——這支要寫系統路徑，需要 root）----
CRON_LINE="7 2 * * * $BIN_DIR/run_auto_web3.sh"
{ crontab -l 2>/dev/null | grep -v "$BIN_DIR/run_auto_web3.sh" || true; echo "$CRON_LINE"; } | crontab -

echo "佈署完成："
echo "  · $BIN_DIR/run_auto_web3.sh 已註冊到 root crontab（$CRON_LINE）"
echo "  · 投放目錄 $INCOMING_DIR（0730 root:$AGENT_USER），只放 agent_key／collector_url"
echo "  · agent 程式本體固定在 $BIN_DIR/push_agent.sh，不從投放目錄取得"
echo "可以安全結束這次 root session、刪掉這支 bootstrap_watcher.sh 了。"
"""


def render() -> str:
    parts = [HEADER]
    for name, marker in EMBEDS:
        body = (HERE / name).read_text(encoding="utf-8")
        if marker in body:
            raise SystemExit(
                f"{name} 內容撞到 heredoc 結束標記 {marker}，"
                f"產出的佈署腳本會在那裡斷掉。請換一個標記。")
        parts.append(f"\n# ---- {name} ----\ncat > \"$BIN_DIR/{name}\" <<'{marker}'\n"
                     f"{body.rstrip(chr(10))}\n{marker}\n")
    parts.append(FOOTER)
    return "".join(parts)


def main() -> int:
    want = render()
    if "--check" in sys.argv:
        have = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if have != want:
            print("bootstrap_watcher.sh 與正本不同步，請跑 python build_bootstrap.py")
            return 1
        print("bootstrap_watcher.sh 與正本同步")
        return 0
    # 換行一律 LF：這份要在 Linux/AIX 上跑，CRLF 會讓 `#!/bin/sh` 直接壞掉
    OUT.write_bytes(want.encode("utf-8"))
    print(f"已產生 {OUT}（{len(want.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
