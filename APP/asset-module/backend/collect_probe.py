"""收集健檢：對一台主機即時跑收集指令，逐步回報「為什麼收不到」。

給公司多 OS 上線 debug 權限/相容問題用——把「查不到（需 root）」這種粗結論，
變成「這條指令、退出碼、stderr、判定」，一眼分得出是權限、指令缺、語系還是防火牆。

## 安全底線（去識別化的一部分）

- **永不回傳指令的原始輸出**：passwd/group/shadow/sudoers/authorized_keys 的內容
  絕不外流，只回 rc、stdout 行數、stderr（會再過去識別化）、是否含非 ASCII、判定。
  這樣既足以判斷問題，又不會把真實帳號/金鑰帶出機器。
- **非 ASCII 旗標**是刻意的：中文語系害 lastlog 解析爆掉那類 bug，用「輸出含 CJK」
  就抓得到，不用看內容。
- 端點層會再用 diagnostics.Desensitizer 遮 IP/MAC 並跑殘留掃描閘門。
"""
from __future__ import annotations

import subprocess

import manage_state

# 要探測的收集指令：沿用 account_collector 真正在用的那組，健檢才反映實況。
_HAS_CJK = None


def _has_non_ascii(s: str) -> bool:
    return any(ord(c) > 127 for c in (s or ""))


def _classify(rc: int, out: str, err: str) -> str:
    """把一次指令執行結果歸成一個判定，方便一眼看出問題類別。"""
    e = (err or "").lower()
    if rc == 255 or "connection" in e or "timed out" in e or "refused" in e or \
            "no route" in e or "permission denied (publickey" in e:
        return "unreachable"          # SSH 連不上/認證失敗
    if "command not found" in e or "not found" in e or "no such file" in e and "cmd" in e:
        return "command_missing"      # 指令不存在（如 Debian 13 無 lastlog）
    if "a password is required" in e or "sudo:" in e or "not allowed" in e or \
            ("permission denied" in e):
        return "permission"           # 權限不足（多半 sudo -n 不可用）
    if rc == 0 and (out or "").strip():
        return "ok"
    if rc == 0:
        return "empty"                # 跑成功但沒輸出（可能沒資料，也可能靜默被擋）
    return "error"


def _runner(ip: str, account: str, key_path: str, local_ips: set, timeout: int = 12):
    """回一個 run(cmd)->(rc,stdout,stderr) 的探測器，路徑與收集器一致。"""
    use_local = account == manage_state.DEFAULT_COLLECT_ACCOUNT and ip in local_ips

    def run(cmd: str):
        try:
            if use_local:
                p = subprocess.run(["bash", "-lc", cmd], capture_output=True,
                                   text=True, timeout=timeout + 10)
            else:
                p = subprocess.run(
                    ["ssh", "-i", key_path, "-o", "BatchMode=yes",
                     "-o", "StrictHostKeyChecking=no", "-o", f"ConnectTimeout={timeout}",
                     f"{account}@{ip}", cmd],
                    capture_output=True, text=True, timeout=timeout + 15)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return 124, "", "timed out"
        except Exception as exc:  # noqa: BLE001
            return 1, "", str(exc)
    return run


def probe(ip: str, account: str, key_path: str, local_ips: set | None = None) -> dict:
    """對一台主機跑健檢。回結構化診斷（不含任何原始輸出內容）。"""
    import account_collector

    local_ips = local_ips or set()
    run = _runner(ip, account, key_path, local_ips)

    # 1) 連得到嗎 + 登入身分
    rc, out, err = run("id -un 2>/dev/null")
    reachable = rc == 0 and out.strip() != ""
    result = {
        "ip": ip, "account": account, "reachable": reachable,
        "identity": out.strip() if reachable else None,
        "commands": [], "hints": [],
    }
    if not reachable:
        result["error"] = _classify(rc, out, err)
        result["stderr"] = (err or "")[:300]
        result["hints"].append(
            f"以 {account} 身分連不上——確認收集公鑰已授權進該主機的 "
            f"{account}/.ssh/authorized_keys，且該帳號存在、可登入。")
        return result

    # 2) sudo -n 可用嗎（需 root 欄位的關鍵）。2>&1 把遠端 sudo 訊息併進 stdout，
    #    所以看 stdout 的 __rc=0。run() 回 (rc, stdout, stderr)——別把 rc 當 stdout。
    _, sout, _ = run("sudo -n true 2>&1; echo __rc=$?")
    result["sudo_n"] = "__rc=0" in (sout or "")

    # 3) 語系（抓 lastlog/chage 解析爆掉那類）
    _, lout, _ = run("echo \"LANG=$LANG LC_ALL=$LC_ALL\"")
    result["locale"] = (lout or "").strip()[:80]

    # 4) OS 探測
    _, oout, _ = run(account_collector.OS_PROBE)
    os_info = account_collector.parse_os(oout)
    result["os"] = {"id": os_info["id"], "version": os_info["version"],
                    "family": os_info["family"], "uid_min": os_info["uid_min"],
                    "bins": os_info["bins"]}

    # 5) 每條收集指令：跑、判定、記 rc/行數/stderr/是否含 CJK（都不含原始內容）
    for name, cmd in (
        ("passwd", account_collector.LINUX_CMDS["passwd"]),
        ("group", account_collector.LINUX_CMDS["group"]),
        ("lastlog", account_collector.LINUX_CMDS["lastlog"]),
        ("shadow", account_collector.LINUX_CMDS["shadow_status"]),
        ("sudoers", account_collector.LINUX_CMDS["sudoers"]),
        ("authkeys", account_collector.LINUX_CMDS["authkeys"]),
    ):
        rc, out, err = run(cmd)
        result["commands"].append({
            "name": name,
            "verdict": _classify(rc, out, err),
            "rc": rc,
            "stdout_lines": len((out or "").splitlines()),
            "has_non_ascii": _has_non_ascii(out),   # True＝可能語系問題
            "stderr": (err or "").strip()[:200],    # 端點層會再去識別化
        })

    _add_hints(result)
    return result


def _add_hints(result: dict) -> None:
    """把判定翻成可行動的白話建議——這是健檢真正有用的地方。"""
    by = {c["name"]: c for c in result["commands"]}
    hints = result["hints"]

    if not result.get("sudo_n"):
        hints.append("sudo -n 不可用 → 密碼效期/空密碼/sudo 明細/authorized_keys 這些需 root "
                     "的欄位收不到。改用有 NOPASSWD 的管理身分（如 sysinfra）收，或佈唯讀 sudo 白名單。")
    if by.get("lastlog", {}).get("verdict") == "command_missing":
        hints.append("此系統無 lastlog（Debian 13+ 已移除）→ 已自動退回 last；last 只列登入過的人，"
                     "『從未登入』無法斷定，閒置判定會改保守（unknown 而非誤報）。")
    for name in ("passwd", "lastlog"):
        if by.get(name, {}).get("has_non_ascii"):
            hints.append(f"{name} 輸出含非 ASCII → 可能是語系（如中文）害解析。收集指令已掛 LC_ALL=C，"
                         "若仍出現代表 sshd/PAM 覆蓋了語系，需在該主機層處理。")
    for name in ("shadow", "sudoers"):
        if by.get(name, {}).get("verdict") == "permission":
            hints.append(f"{name} 因權限被擋 → 同上，需要能 sudo 的收集身分。")
    if not hints:
        hints.append("各步驟看起來正常；若仍有欄位空白，把此健檢匯出貼給開發者對照解析邏輯。")
