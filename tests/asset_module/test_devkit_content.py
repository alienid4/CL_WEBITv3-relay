"""開發協作頁的內容守門：收集任務的指令一律唯讀、欄位完整、編號不重複、沒有真實 IP／公司識別字。

devkit.ts 是 TypeScript，這裡不跑 TS，用文字比對（檔案格式刻意寫得很死，見檔頭說明）。
前端行為（去識別、複製鈕、篩選）在 frontend/tests/devkit*.spec.ts。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
CONTENT = FE / "content" / "devkit.ts"
PAGE = FE / "pages" / "v2" / "devkit.vue"
SANITIZE = FE / "utils" / "devkitSanitize.ts"

# 禁用字樣用字串相接，免得這支測試檔自己出現公司識別字（公開 relay 的去識別替換會改寫字面值）
BANNED = [r"10\.9\d\.", "cat" + "hay", "國" + "泰", "secan" + "sible"]

# 寫入類關鍵字（單字邊界、不分大小寫）；xp_ 是 SQL Server 擴充預存程序前綴，沒有邊界問題
LOCK_PREFIX = re.compile(r"^\s*SET LOCK_TIMEOUT \d+;\s*", re.I)
WRITE_WORDS = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "EXEC", "EXECUTE", "MERGE", "CREATE", "GRANT",
               "REVOKE", "SHUTDOWN", "REBOOT", "RESTART", "RM", "MKFS", "DD", "CHMOD", "CHOWN", "KILL", "SET", "APPLY", "SUDO"]


def _body():
    return CONTENT.read_text(encoding="utf-8").split("// ===== 以下是純函式", 1)[0]


def _task_chunks():
    body = _body().split("export const DEVKIT_TASKS", 1)[1]
    return body.split("\n  {\n    id: '")[1:]


SCRIPT_MARK = "# WUG-PROBE-SCRIPT"
AD_MARK = "# AD-PROBE-SCRIPT"
RELAY_MARK = "# WUG-RELAY-COLLECT-SCRIPT"
UNITY_MARK = "# UNITY-COLLECT-SCRIPT"
CERT_MARK = "# CERT-RELAY-COLLECT-SCRIPT"
PUSH_MARK = "# RELAY-PUSH-SCRIPT"
SETUP_MARK = "# WUG-RELAY-SETUP-SCRIPT"
ALERT_MARK = "# WUG-ALERT-COLLECT-SCRIPT"
AD_TIME_MARK = "# AD-TIME-SCRIPT"
LASTPOLL_MARK = "# WUG-LASTPOLL-COLLECT-SCRIPT"
ARIA_INV_MARK = "# ARIA-INVENTORY-SCRIPT"
AD_EXPORT_MARK = "# AD-EXPORT-SCRIPT"
AD_BAT_MARK = "rem AD-EXPORT-BAT"
ARIA_HOST_MARK = "# ARIA-HOST-PROBE-SCRIPT"
ARIA_DC_MARK = "# ARIA-DAILYCHECK-SCRIPT"
SCRIPT_MARKS = (SCRIPT_MARK, AD_MARK, RELAY_MARK, UNITY_MARK, CERT_MARK, PUSH_MARK, SETUP_MARK, ALERT_MARK, ARIA_INV_MARK, AD_EXPORT_MARK, AD_BAT_MARK, ARIA_HOST_MARK, AD_TIME_MARK, LASTPOLL_MARK,
                ARIA_DC_MARK)


def _all_commands(chunk):
    return re.findall(r"text:\s*`([^`]*)`", chunk)


def _commands(chunk):
    """SQL／uemcli 類的指令（腳本型的指令另有 test_WUG探測腳本_* 逐項守門）。"""
    return [t for t in _all_commands(chunk) if not any(m in t for m in SCRIPT_MARKS)]


def _scripts(chunk):
    return [t for t in _all_commands(chunk) if SCRIPT_MARK in t]


def test_files_exist():
    assert CONTENT.exists() and PAGE.exists() and SANITIZE.exists()


def test_至少兩組任務_編號不重複():
    ids = [c.split("'", 1)[0] for c in _task_chunks()]
    assert len(ids) >= 3 and len(set(ids)) == len(ids), ids
    assert {"D1", "D1b", "D2", "D3"} <= set(ids)


def test_每個任務欄位完整_有指令或明確標placeholder():
    for c in _task_chunks():
        tid = c.split("'", 1)[0]
        for field in ("tag", "title", "purpose", "where", "perm", "expect", "returnHow"):
            m = re.search(rf"\n    {field}: '(.+)',\n", c)
            assert m and m.group(1).strip(), f"{tid} 缺 {field}"
        cmds = _all_commands(c)
        if "placeholder: true" in c:
            assert not cmds, f"{tid} 是 placeholder 就不該有指令"
            assert re.search(r"\n    waiting: '.+',\n", c), f"{tid} 是 placeholder 要寫 waiting 狀態字"
        else:
            assert cmds, f"{tid} 沒有任何指令"
            assert len(re.findall(r"label: '", c)) == len(cmds), f"{tid} 每條指令都要有 label"
        m = re.search(r"\n    perm: '(.+)',\n", c)
        assert "唯讀" in m.group(1), f"{tid} 所需權限一律要寫唯讀"


def test_指令一律唯讀_不含寫入類關鍵字():
    n = 0
    for c in _task_chunks():
        tid = c.split("'", 1)[0]
        for text in _commands(c):
            text = LOCK_PREFIX.sub("", text)      # 唯一允許的 SET：SET LOCK_TIMEOUT n;（只限制等鎖時間，讓查詢不卡住正式庫）
            n += 1
            for w in WRITE_WORDS:
                assert not re.search(rf"\b{w}\b", text, re.I), f"{tid} 指令含寫入類字樣 {w}：{text[:60]}"
            assert "xp_" not in text.lower(), f"{tid} 指令含 xp_"
            assert not re.search(r"[;|]\s*(rm|dd|mkfs)\b", text, re.I)
    assert n >= 9


UEMCLI_QUERY = re.compile(r"uemcli -output csv (/[\w-]+)+ show -detail > /tmp/unity_[a-z]+_\$\(date \+%Y%m%d_%H%M\)\.csv")
UEMCLI_PRECHECK = {"uemcli -version", "uemcli /sys/general show"}


def test_SQL只能SELECT_uemcli只能show():
    for c in _task_chunks():
        tid = c.split("'", 1)[0]
        for text in _commands(c):
            text = LOCK_PREFIX.sub("", text)
            if text.lstrip().upper().startswith(("SELECT", "WITH")):
                assert text.lstrip().upper().startswith("SELECT"), f"{tid} SQL 要以 SELECT 開頭"
            elif text.startswith("uemcli"):
                # 只允許：連線前檢查兩條，或 show 並重導向到 /tmp 底下「帶日期時間」的新檔名（不覆寫舊檔）
                assert text in UEMCLI_PRECHECK or UEMCLI_QUERY.fullmatch(text), f"{tid} uemcli 只允許 show 並輸出到帶時間的 /tmp 新檔：{text}"
            else:
                pytest.fail(f"{tid} 出現未知類型的指令（只允許 SELECT 或 uemcli show）：{text[:60]}")


def test_重導向只准_tmp_帶時間檔名():
    n = 0
    for c in _task_chunks():
        for text in _commands(c):
            for m in re.findall(r">\s*(\S+(?: \+%Y%m%d_%H%M\)\S*)?)", text):
                n += 1
                assert m.startswith("/tmp/") and "$(date +%Y%m%d_%H%M)" in m, f"重導向目標不安全：{m}"
    assert n == 6


def test_Unity權限措辭不再只說唯讀():
    d2 = next(c for c in _task_chunks() if c.startswith("D2'"))
    perm = re.search(r"\n    perm: '(.+)',\n", d2).group(1)
    assert "查詢本身唯讀" in perm and "建立輸出檔" in perm and "不覆寫舊檔" in perm
    notes = d2.split("notes: [", 1)[1]
    for must in ("檔案存在哪", "scp", "sftp", "清理", "語法依 Unity 5.5 文件", "第一次請先單條試跑", "錯誤訊息貼回來即可"):
        assert must in notes, must
    assert "未實測" not in d2
    assert "precheck: [" in d2 and "failHow:" in d2


def test_D1每條SQL有成功判準_失敗處理_帶回內容():
    d1 = next(c for c in _task_chunks() if c.startswith("D1'"))
    assert len(re.findall(r"\n        ok: '", d1)) == 3
    assert len(re.findall(r"\n        onFail: '", d1)) == 3
    assert len(re.findall(r"\n        bring: '", d1)) == 3
    assert "停止" in d1 and "繼續" in d1


def test_任務都有指令版本():
    for c in _task_chunks():
        assert re.search(r"\n    ver: \d+,\n", c), c.split("'", 1)[0]


def test_指令文字逐字原樣():
    """D1 三條照使用者原文；D2 六條依 11-01 審查改為帶時間戳記的檔名。"""
    body = _body()
    must = [
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES\nWHERE TABLE_TYPE='BASE TABLE' AND (TABLE_NAME LIKE '%Monitor%' OR TABLE_NAME LIKE '%Alert%' OR TABLE_NAME LIKE '%State%' OR TABLE_NAME LIKE '%Device%' OR TABLE_NAME LIKE '%Disk%' OR TABLE_NAME LIKE '%Performance%')\nORDER BY TABLE_NAME;",
        "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS\nWHERE TABLE_NAME IN ('Device','NetworkInterface','ActiveMonitorType','PivotActiveMonitorTypeToDevice','ActiveMonitorStateChangeLog')\nORDER BY TABLE_NAME, ORDINAL_POSITION;",
        "SELECT * FROM ActiveMonitorType WHERE sMonitorTypeName LIKE '%Disk%' OR sMonitorTypeName LIKE '%Partition%' OR sMonitorTypeName LIKE '%Storage%' OR sMonitorTypeName LIKE '%磁碟%';",
        "uemcli -output csv /remote/host show -detail > /tmp/unity_hosts_$(date +%Y%m%d_%H%M).csv",
        "uemcli -output csv /remote/host/initiator show -detail > /tmp/unity_initiators_$(date +%Y%m%d_%H%M).csv",
        "uemcli -output csv /stor/prov/luns/lun show -detail > /tmp/unity_luns_$(date +%Y%m%d_%H%M).csv",
        "uemcli -output csv /remote/host/hlu show -detail > /tmp/unity_hlu_$(date +%Y%m%d_%H%M).csv",
        "uemcli -output csv /stor/config/pool show -detail > /tmp/unity_pools_$(date +%Y%m%d_%H%M).csv",
        "uemcli -output csv /sys/general show -detail > /tmp/unity_system_$(date +%Y%m%d_%H%M).csv",
    ]
    for s in must:
        assert s in body, s[:50]


def test_內容檔沒有真實IP或公司識別字():
    for f in (CONTENT, PAGE, SANITIZE):
        text = f.read_text(encoding="utf-8")
        for pat in BANNED:
            assert not re.search(pat, text, re.I), f"{f.name} 出現禁用字樣 {pat}"
        for ip in re.findall(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])", text):
            ok = ip.startswith(("192.0.2.", "198.51.100.", "203.0.113.", "255.255.255.", "0.0.0.0", "5.5.0.0", "5.5.1.2", "1.2.3.4"))
            assert ok, f"{f.name} 出現非示範 IP：{ip}"


def test_選單與頁面接線():
    nav = (FE / "composables" / "useNavV2.ts").read_text(encoding="utf-8")
    assert "{ no: '🛠', label: '開發協作', to: '/v2/devkit' }" in nav
    page = PAGE.read_text(encoding="utf-8")
    assert "layout: 'v2'" in page
    assert "InfoNote" in page and "navigator.clipboard" in page
    # 不新增後端表：頁面不呼叫任何 API，進度只存 localStorage
    assert "apiFetch" not in page and "useApi" not in page and "localStorage" in page


def test_localStorage讀寫都有try_catch():
    page = PAGE.read_text(encoding="utf-8")
    for call in ("localStorage.getItem", "localStorage.setItem"):
        i = page.index(call)
        assert "try {" in page[max(0, i - 200):i], call


def test_整理結果_複製下載前強制確認_對照表不提供下載():
    page = PAGE.read_text(encoding="utf-8")
    san = SANITIZE.read_text(encoding="utf-8")
    for k in ("主機名", "網域", "IQN", "WWPN／WWN", "設備序號", "LUN 名稱", "共享／掛載路徑", "帳號", "IPv6"):
        assert k in san, k
    assert ':disabled="!cleaned || !checked"' in page                      # 結果複製／下載
    assert "watch(raw" in page and "checked.value = false" in page         # 每次貼上新內容就重置
    assert "下載對照表" not in page and "mapText" in page
    assert not re.search(r"download\([^)]*mapText", page), "對照表不可提供下載"
    assert "對照表」三個字" in page
    assert "匯出進度" in page and "匯入進度" in page and "最後更新" in page and "換電腦" in page


def test_AD探測腳本_只讀_不寫AD_不問密碼():
    """D1e：只做 LDAP 搜尋；不得有寫入 AD 的 .NET／cmdlet、不得提示密碼、不得改執行原則、值要遮罩。"""
    found = 0
    for c in _task_chunks():
        for text in _all_commands(c):
            if AD_MARK not in text:
                continue
            found += 1
            for bad in ("CommitChanges", "Set-AD", "New-AD", "Remove-AD", "Add-AD", "Invoke-WebRequest", "Invoke-RestMethod",
                        "Set-ExecutionPolicy", "Read-Host", "Get-Credential", "ConvertTo-SecureString", "-Password", "Set-ItemProperty"):
                assert bad.lower() not in text.lower(), f"AD 腳本不該出現 {bad}"
            assert "DirectorySearcher" in text and "FindAll" in text
            assert "Mask" in text
    assert found == 1


def _relay_scripts():
    out = []
    for c in _task_chunks():
        out += [(c.split("'", 1)[0], t) for t in _all_commands(c) if RELAY_MARK in t]
    return out


def test_中繼收集腳本_只有一支_放在D4_標頭有唯一標記():
    found = _relay_scripts()
    assert [tid for tid, _ in found] == ["D4"]
    assert found[0][1].startswith(RELAY_MARK + " v3")


def test_中繼收集腳本_只讀WhatsUp_不寫別處_不洩密():
    """D4：WhatsUp 只 GET（外加一次 token 登入）；只寫輸出資料夾；帳密由操作者自填 wug_account.txt（2026-11-02 使用者決定，取代 DPAPI）；沒有危險寫法；輸出不含密碼或權杖。"""
    (_, text), = _relay_scripts()
    for bad in ("Invoke-Expression", "iex ", "Bypass", "-EncodedCommand", "FromBase64String", "ToBase64String", "Set-ExecutionPolicy", "ExecutionPolicy",
                "Set-ItemProperty", "New-ItemProperty", "Remove-Item", "HKLM:", "HKCU:", "Registry::", "Start-Process", "Invoke-Command", "Invoke-WebRequest",
                "Send-MailMessage", "Net.Mail", "schtasks", "Register-ScheduledTask", "DownloadFile", "UploadFile", "WebClient", "ConvertTo-SecureString",
                "-AsPlainText", "Out-File", "Set-Content", "Add-Content", "Export-Csv", "ServerCertificateValidationCallback", "-SkipCertificateCheck"):
        assert bad.lower() not in text.lower(), f"腳本不該出現 {bad}"
    assert len(re.findall(r"-Method Post", text)) == 1 and "/api/v1/token" in text.split("-Method Post", 1)[1][:80]      # 只有登入是 POST
    assert not re.search(r"-Method\s+(Put|Delete|Patch)", text, re.I)
    assert len(re.findall(r"Invoke-RestMethod", text)) == 2                                                             # 登入一次＋Get-Wug 一處
    assert "'wug_account.txt'" in text and "Read-Host" not in text and "Export-Clixml" not in text and "$pass = $null" in text   # 帳密只從操作者自填的檔讀，用完清掉
    assert text.count("WriteAllText") == 1 and "UTF8Encoding($false)" in text and "$tmp = $Path + '.tmp'" in text and "Move-Item" in text   # 先寫 .tmp 再改名，單一寫檔出口
    _close = text[text.index("function Close-Batch"):text.index("$ips = @(")]
    assert "'manifest.json'" in _close and text.rindex("Close-Batch") > text.index("$final = Join-Path $script:batchDir $fname")   # 每批 manifest 最後才寫（資料檔寫完才收批）                           # manifest 最後才寫
    assert "$headers = $null" in text and "Start-Sleep -Seconds 1" in text and "$MaxCalls" in text and "$MaxPages" in text
    assert "$AllowMarketHours" in text and "-ge 510" in text and "-lt 900" in text                                      # 開盤時間預設拒絕
    assert "ConvertTo-Json -Depth 8 -Compress" in text and "foreach ($p in $pts)" in text          # 每個時間點一列（WhatsUp 的點在 series 裡；Depth 6 會把點壓成文字，11-03 實測只剩 2 點）
    for f in ("pollTimeUtc", "avgPercent", "maxPercent", "minPercent", "cpuId", "memoryId", "diskId"):
        assert f"'{f}'" in text
    assert "schema_version = 1" in text and "collector_version" in text and "sha256" in text and "time_zone = 'UTC'" in text
    assert "`" not in text and "\\" not in text and "${" not in text                                                  # 樣板字串裡不能有反引號／反斜線／${


def test_中繼收集腳本_v3_token會續_整份清單_回報計數():
    """2.058.22：v2 只登入一次，30 分鐘 token 過期後其餘全 401（每天只收 ~73／4684 台）。"""
    (_, text), = _relay_scripts()
    assert "grant_type = 'refresh_token'" in text and "$RefreshMinutes" in text and "[int]$RefreshMinutes = 25" in text
    assert "-eq 401" in text and "Renew-Token" in text and "$script:authLost" in text            # 401 重登一次，登不回去就停
    assert "devices?view=overview&limit=200&pageId=" in text and "$ListMaxPages" in text          # 整份清單建對照，不逐台 search
    assert "run = [ordered]@{ final = $Final" in text and "Close-Batch $true" in text             # 最後一批帶整輪計數
    for k in ("ok = $script:ok", "failed = $script:failed", "notfound = $script:notfound", "ips = $script:nIps"):
        assert k in text
    assert "$summary = 'ok='" in text and "('DONE ' + $summary" in text
    # 最後一批不可能是空的：滿批要等下一台資料好了才收
    assert text.index("if ($files.Count -ge $BatchSize) { Close-Batch $false; Open-Batch }") > text.index("$reports[$k] = Convert-Rows $rows")


def test_中繼收集腳本_和AI資料夾的正本一致():
    src = ROOT / "AI" / "tools_relay" / "wug_collect.ps1"
    if not src.exists():
        pytest.skip("relay 產出物沒有 AI/")
    (_, text), = _relay_scripts()
    assert text == src.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10)), "devkit.ts 的 D4 腳本和 AI/tools_relay/wug_collect.ps1 不一致，請同步"


def _unity_script():
    found = []
    for c in _task_chunks():
        for text in _all_commands(c):
            if UNITY_MARK in text:
                found.append((c, text))
    assert len(found) == 1, "Unity 收集腳本應該只有一支"
    return found[0]


def test_Unity收集腳本_只發GET_不寫Unity_不問密碼():
    """D3：中繼機上的唯讀收集腳本。只准 HttpMethod Get；不得有寫入類動詞、不得提示或儲存密碼；帳密只讀 DPAPI 憑證檔。"""
    c, sc = _unity_script()
    assert sc.startswith(UNITY_MARK)
    assert set(re.findall(r"HttpMethod\]::(\w+)", sc)) == {"Get"}
    assert not re.search(r"-Method\b", sc, re.I) and not re.search(r"Invoke-(WebRequest|RestMethod)", sc, re.I)
    for bad in ("Invoke-Expression", "iex ", "Bypass", "-EncodedCommand", "FromBase64String", "Set-ExecutionPolicy", "ExecutionPolicy",
                "Read-Host", "Get-Credential", "ConvertTo-SecureString", "-AsPlainText", "Export-Clixml", "Export-Csv", "Set-Content",
                "Add-Content", "Out-File", "New-Item", "Remove-Item", "Copy-Item", "Rename-Item", "Start-Process", "Invoke-Command",
                "Set-ItemProperty", "New-ItemProperty", "HKLM:", "HKCU:", "Registry::", "schtasks", "Register-", "Send-MailMessage",
                "WebClient", "DownloadFile", "UploadFile", "StreamWriter", "-Password",
                "ServicePointManager]::ServerCertificateValidationCallback", "SkipCertificateCheck", "TestOnlyIgnoreCertificate"):
        assert bad.lower() not in sc.lower(), f"Unity 腳本不該出現 {bad}"
    assert "Import-Clixml" in sc and "GetNetworkCredential().Password" in sc and sc.count("GetNetworkCredential()") == 1
    assert not re.search(r"\$\w*(password|passwd|pwd|secret|token)\w*\s*=\s*['\"][^'\"$]+['\"]", sc, re.I)       # 沒有帳密字面值
    # 寫檔只有一個出口（先寫 .tmp 再改名），且只寫在 OutDir 底下的當日資料夾
    assert sc.count("WriteAllText") == 1 and sc.count("Move-Item") == 1 and sc.count("CreateDirectory") == 1
    assert "$final + '.tmp'" in sc and "Join-Path $Dir ([IO.Path]::GetFileName($Name))" in sc
    assert "$runDir = Join-Path $OutDir $day" in sc
    # 值遮罩：位址與密碼在畫面輸出前都先處理
    assert "function Mask" in sc and "function Scrub" in sc and "'***'" in sc and "Say (" in sc


def test_Unity收集腳本_同源轉址_節流_上限_輸出格式():
    c, sc = _unity_script()
    assert "AllowAutoRedirect = $false" in sc and "X-EMC-REST-CLIENT" in sc and "application/json" in sc
    assert "StartsWith('/api/')" in sc and "StartsWith('/cas/')" in sc and "StartsWith('//')" in sc
    assert "$Target.DnsSafeHost -ne $BaseUri.DnsSafeHost" in sc and "$Target.Port -ne $BaseUri.Port" in sc and "$Target.Scheme -ne $BaseUri.Scheme" in sc
    assert "$hops -ge 3" in sc and "HTTP 401" in sc and "Not retrying" in sc
    assert "Start-Sleep -Seconds 1" in sc and "$script:Calls -ge $MaxCalls" in sc and "[int]$PerPage = 50" in sc
    assert "$uri.AbsolutePath.StartsWith('/cas/')" in sc            # 有 cookie 後只靠 cookie
    assert "-like '*html*'" in sc                                          # HTML 登入頁拒收
    for f in ("name,sizeTotal,sizeUsed,sizeFree", "name,sizeTotal,hostAccess", "name,type"):
        assert f"'{f}'" in sc, f
    for k in ("source = 'unity'", "schema_version = $SchemaVersion", "collector_version = $CollectorVersion", "collected_at = $collectedAt",
              "count = $files.Count", "files = $files.ToArray()", "sha256 =", "size ="):
        assert k in sc, k
    assert "[string]$TrustThumbprint" in sc and "$pin.Length -ne 40" in sc and "[Net.ServicePointManager]::SecurityProtocol = $oldTls" in sc
    assert sc.index("function Save-TextAtomic") < sc.index("'manifest.json'") and sc.count("'manifest.json'") == 1


def test_Unity收集腳本_無真實IP或公司識別字_可安全放進TS樣板字串_與AI目錄原稿一致():
    c, sc = _unity_script()
    for b in BANNED:
        assert not re.search(b, sc, re.I), b
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", sc)
    assert "`" not in sc and "${" not in sc and "\\" not in sc
    assert "\r" not in sc
    src = ROOT / "AI" / "tools_relay" / "unity_collect.ps1"
    if not src.exists():
        pytest.skip("relay 產出物沒有 AI/（正本比對只在開發機做）")
    original = src.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert sc == original, "devkit 內的腳本與 AI/tools_relay/unity_collect.ps1 不一致（改一邊要兩邊同步）"


def test_D3任務欄位_Unity標籤_可下載_權限寫唯讀():
    c, _ = _unity_script()
    assert c.startswith("D3'") and "tag: 'Unity'," in c
    assert "file: 'unity_collect.ps1'," in c
    perm = re.search(r"\n    perm: '(.+)',\n", c).group(1)
    assert "唯讀" in perm and "Operator" in perm
    notes = c.split("notes: [", 1)[1]
    assert "Get-Credential | Export-Clixml" in notes and "DPAPI" in notes and "-TrustThumbprint" in notes


# ---------------------------------------------------------------- D5 憑證到期探測腳本（2.042.02）
def _cert_script():
    found = [(c, t) for c in _task_chunks() for t in _all_commands(c) if CERT_MARK in t]
    assert len(found) == 1, "憑證探測腳本應該只有一支"
    return found[0]


def test_憑證探測腳本_在D5_只做TLS握手_不連HTTP_不登入_不碰私鑰():
    c, sc = _cert_script()
    assert c.startswith("D5'") and "file: 'cert_collect.ps1'," in c
    assert sc.startswith(CERT_MARK + " v1")
    for bad in ("Invoke-Expression", "iex ", "Bypass", "-EncodedCommand", "FromBase64String", "ToBase64String", "Set-ExecutionPolicy", "ExecutionPolicy",
                "Invoke-WebRequest", "Invoke-RestMethod", "WebClient", "HttpClient", "HttpWebRequest", "-Method", "Read-Host", "Get-Credential",
                "Import-Clixml", "Export-Clixml", "ConvertTo-SecureString", "-AsPlainText", "Out-File", "Set-Content", "Add-Content", "Export-Csv",
                "Remove-Item", "Start-Process", "Invoke-Command", "Set-ItemProperty", "New-ItemProperty", "HKLM:", "HKCU:", "Registry::",
                "schtasks", "Register-ScheduledTask", "Send-MailMessage", "ServicePointManager", "ServerCertificateValidationCallback",
                "SkipCertificateCheck", "PrivateKey", "Export(", "GetRawCertData", ".Write(", "StreamWriter"):
        assert bad.lower() not in sc.lower(), f"探測腳本不該出現 {bad}"
    # 驗證放寬只在這一條連線內：每條 SslStream 自己帶 callback，不動全域設定
    assert "[System.Net.Security.RemoteCertificateValidationCallback]" in sc and "New-Object System.Net.Security.SslStream($ns, $false, $cb)" in sc
    assert "return $true" in sc and "$script:CertErr = [string]$errs" in sc and "'validation: '" in sc
    assert "AuthenticateAsClient(" in sc and sc.count("$ssl.Dispose()") == 1 and "$tcp.Close()" in sc
    # 憑證本體不輸出：RawData 只拿來算 SHA-256
    assert sc.count("RawData") == 1 and "ComputeHash($x.RawData)" in sc


def test_憑證探測腳本_節流_逾時_盤中拒絕_原子寫檔_manifest最後():
    _c, sc = _cert_script()
    assert "$AllowMarketHours" in sc and "-ge 510" in sc and "-lt 900" in sc and "exit 1" in sc
    assert "[int]$TimeoutMs = 5000" in sc and "WaitOne($TimeoutMs)" in sc and "$ns.ReadTimeout = $TimeoutMs" in sc and "$TimeoutMs -gt 5000" in sc
    assert "Start-Sleep -Seconds 1" in sc and "[int]$BatchSize = 100" in sc and "$BatchSize -gt 100" in sc and "$MaxEndpoints" in sc
    assert sc.count("WriteAllText") == 1 and "$tmp = $Path + '.tmp'" in sc and "Move-Item -LiteralPath $tmp" in sc and "UTF8Encoding($false)" in sc
    assert sc.index("'manifest.json'") > sc.index("Write-Atomic $final")
    for k in ("source = 'cert'", "schema_version = 1", "collector_version = $ScriptVersion", "time_zone = 'UTC'", "count = $files.Count",
              "files = $files.ToArray()", "sha256 =", "size ="):
        assert k in sc, k
    for f in ("endpoint", "port", "sni", "ok", "error", "subject", "issuer", "serial", "sha256", "not_before_utc", "not_after_utc", "san"):
        assert f + " = " in sc, f


def test_憑證探測腳本_ASCII_可放進TS樣板字串_與AI目錄正本一致():
    _c, sc = _cert_script()
    assert all(ord(ch) < 128 for ch in sc), "腳本要全 ASCII（PowerShell 5.1 讀無 BOM 檔會亂碼）"
    assert "`" not in sc and "${" not in sc and "\\" not in sc and "\r" not in sc
    for b in BANNED:
        assert not re.search(b, sc, re.I), b
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", sc)
    src = ROOT / "AI" / "tools_relay" / "cert_collect.ps1"
    if not src.exists():
        pytest.skip("relay 產出物沒有 AI/")
    assert sc == src.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10)), "devkit.ts 的 D5 腳本和 AI/tools_relay/cert_collect.ps1 不一致，請同步"


# ---------------------------------------------------------------- D6 中繼機一鍵安裝（2.042.16）
def _d6():
    c = next(c for c in _task_chunks() if c.startswith("D6'"))
    cmds = _all_commands(c)
    push = [t for t in cmds if t.startswith(PUSH_MARK + " v2")]
    setup = [t for t in cmds if t.startswith(SETUP_MARK + " v2")]
    assert len(push) == 1 and len(setup) == 1 and len(cmds) == 2
    return c, push[0], setup[0]


def test_D6_兩支腳本只出現在D6_可下載():
    c, _, _ = _d6()
    assert "file: 'relay_push.ps1'," in c and "file: 'relay_setup.ps1'," in c
    for other in _task_chunks():
        if other.startswith("D6'"):
            continue
        for t in _all_commands(other):
            assert PUSH_MARK not in t and SETUP_MARK not in t


_ALWAYS_BANNED = ("Invoke-Expression", "iex ", "Bypass", "EncodedCommand", "FromBase64String", "ToBase64String", "base64",
                  "Set-ExecutionPolicy", "Set-ItemProperty", "New-ItemProperty", "Remove-ItemProperty", "HKLM:", "HKCU:", "Registry::", "reg add",
                  "reg.exe", "netsh", "NetFirewall", "Set-Service", "Stop-Service", "Start-Service", "New-Service", "sc.exe", "Start-Process",
                  "Invoke-Command", "Invoke-WebRequest", "Invoke-RestMethod", "WebClient", "DownloadFile", "ConvertTo-SecureString", "-AsPlainText",
                  "Export-Clixml", "Out-File", "Set-Content", "Unregister-ScheduledTask", "schtasks", "ServerCertificateValidationCallback")


def test_D6_上傳腳本_只用金鑰SFTP_manifest最後_只刪自己的暫存檔():
    _, push, _ = _d6()
    for bad in _ALWAYS_BANNED + ("Register-ScheduledTask", "Get-Credential", "ssh-keygen", "Read-Host", "password", "sshpass"):
        assert bad.lower() not in push.lower(), f"上傳腳本不該出現 {bad}"
    assert "[Parameter(Mandatory=$true)][string]$Target" in push                                    # 沒有預設目標主機
    assert "-o BatchMode=yes" in push and "StrictHostKeyChecking=accept-new" in push and "& sftp -i $Key" in push
    assert push.count("Remove-Item") == 1 and "Remove-Item -LiteralPath $bf" in push              # 只刪自己寫的 sftp 批次檔
    assert "$bf = Join-Path $env:TEMP ('relay_push_'" in push
    assert "@(Get-Item -LiteralPath $m)" in push.split("$files = ", 1)[1].split("\n", 1)[0]        # manifest 排在最後
    assert "if (-not (Test-Path -LiteralPath $m)) { continue }" in push                            # 沒有 manifest 的批次（還在寫）不傳
    # 11-03 安全審查：名稱白名單、遠端路徑加引號、上傳到新的隱藏暫存資料夾、最後整個改名（同名已存在就失敗，不覆蓋）
    assert "$safe = '^[A-Za-z0-9][A-Za-z0-9._-]*$'" in push and "$n -notmatch $safe" in push and "$_.Name -notmatch $safe" in push
    assert "$stage = '.up_' + $n + '_' + [guid]::NewGuid()" in push                                  # 匯入端略過 . 開頭的資料夾
    assert "@('mkdir \"' + $stage + '\"')" in push and "-mkdir" not in push and "-rm" not in push      # 不忽略錯誤、不刪遠端檔
    assert "('put \"' + $p + '\" \"' + $stage + '/' + $f.Name + '.tmp\"')" in push
    assert "('rename \"' + $stage + '\" \"' + $n + '\"')" in push
    loop = push.split("foreach ($f in $files) {", 1)[1]
    assert loop.index("$lines += ('rename \"' + $stage + '\" \"' + $n + '\"')") > loop.index("}")         # 整批改名在所有檔案之後
    # 本機搬移失敗也算 FAIL；只有上傳＋搬移都成功才記 OK；有失敗 exit 1
    assert "Move-Item -LiteralPath $b.FullName -Destination (Join-Path $SentDir $n) -ErrorAction Stop" in push
    mv = push.split("Move-Item -LiteralPath $b.FullName", 1)
    assert "try {" in mv[0][-40:] and "$r = 'OK'" in mv[1].split("catch", 1)[0] and push.count("$r = 'OK'") == 1
    assert "if ($bad -gt 0) { exit 1 }" in push and push.rstrip().endswith("exit 0")


def test_匯入端略過點開頭的暫存資料夾():
    src = (ROOT / "APP" / "asset-module" / "backend" / "relay_import.py").read_text(encoding="utf-8")
    assert 'if e.name.startswith(".") or e.name.endswith(".tmp"):' in src


def test_D6_安裝腳本_不連WhatsUp_不改系統設定_密碼不落地():
    _, _, setup = _d6()
    for bad in _ALWAYS_BANNED + ("Remove-Item", "Copy-Item", "Export-Csv", "sshpass", "Add-LocalGroupMember", "secedit", "ntrights"):
        assert bad.lower() not in setup.lower(), f"安裝腳本不該出現 {bad}"
    assert not re.search(r"&\s*powercfg\s+[-/](change|set|x|setactive|setacvalueindex|setdcvalueindex)\b", setup, re.I)   # 電源設定只讀
    assert "& powercfg -query" in setup and "run this yourself: powercfg -change -standby-timeout-ac 0" in setup
    # 只有這支能登記排程、問密碼、產生金鑰，各一處
    assert setup.count("Register-ScheduledTask ") == 1 and setup.count("Get-Credential ") == 1 and setup.count("& ssh-keygen ") == 1
    assert "-RunLevel Limited" in setup and "-Password $cred.GetNetworkCredential().Password" in setup and "$cred = $null" in setup
    assert not re.search(r"\$\w*pass\w*\s*=", setup, re.I)                                            # 密碼不另存變數
    assert "Add-Content" not in setup.split("$run = @(", 1)[0]                                       # 安裝過程不寫 log（只有每日執行檔寫 run_log）
    for k in ("-StartWhenAvailable", "-ExecutionTimeLimit $Limit", "(New-TimeSpan -Hours 3)", "-MultipleInstances IgnoreNew", "New-ScheduledTaskTrigger -Daily -At $at"):
        assert k in setup, k
    reg = setup.split("Register-ScheduledTask ", 1)
    assert "-Force" in reg[1].split("\n", 1)[0] and "Read-Host" in reg[0]                            # 同名排程先問再取代
    assert setup.count("WriteAllText") == 1 and "'run_daily.ps1'" in setup                           # 每日執行檔單一寫檔出口
    assert "-t ed25519" in setup and "-C 'webit3-relay-push'" in setup
    assert 'command="internal-sftp -d ' in setup and "no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding" in setup
    assert "'from=\"' + $myIp[0]" in setup
    assert "[string]$RemoteDir = '/opt/webit3/relay_in'" in setup and "[string]$Time = '15:30'" in setup and "[string]$TaskName = 'webit3_wug_daily'" in setup
    assert "(Join-Path $env:SystemDrive 'webit3_relay')" in setup
    assert "[Parameter(Mandatory=$true)][string]$WugBase" in setup and "[Parameter(Mandatory=$true)][string]$Target" in setup
    assert "'^https://[A-Za-z0-9][A-Za-z0-9.-]{0,251}(:[0-9]{1,5})?/?$'" in setup
    assert "[switch]$TestNow" in setup and "Start-ScheduledTask -TaskName $TaskName" in setup
    assert "wug_account.txt" in setup and "Get-Content -LiteralPath $acct" not in setup               # 帳密檔只看在不在，不讀內容
    # 11-03 安全審查：帳密檔與私鑰只限本人；資料夾太開放就 FAIL 並印指令；只在使用者按 y 後對那兩個檔跑 icacls
    # icacls 只有兩處：(1) 使用者按 y 後收緊那兩個秘密檔；(2) 本腳本「剛建立」的中繼資料夾一建好就上鎖（既有資料夾只檢查、印指令）
    assert len(re.findall(r"&\s*icacls\b", setup, re.I)) == 2 and "& icacls $Path /inheritance:r /grant:r ('*' + $me + ':F') | Out-Null" in setup
    new_root = "  & icacls $Root /inheritance:r /grant:r ('*' + $me + ':(OI)(CI)F') /grant:r '*S-1-5-18:(OI)(CI)F' /grant:r '*S-1-5-32-544:(OI)(CI)F' | Out-Null"
    assert new_root in setup
    assert "$rootNew = -not (Test-Path -LiteralPath $Root)" in setup
    before = setup.split(new_root, 1)[0]
    assert before.rstrip().endswith("an existing folder is only checked.") and before.rindex("if ($rootNew) {") > before.rindex("$rootNew = -not")
    assert setup.index("$rootNew = -not") < setup.index("New-Item -ItemType Directory -Force -Path $Root")   # 先判斷是不是新的，再建立
    assert setup.index(new_root) < setup.index("foreach ($d in $dirs)")                          # 子資料夾在上鎖後才建，跟著繼承
    prot = setup.split("function Protect-SecretFile", 1)[1].split("\nfunction ", 1)[0]
    assert "& icacls" in prot and prot.index("Read-Host") < prot.index("& icacls") and "if ($ans -match '^[yY]')" in prot
    assert "& icacls" not in setup.split("function Test-PrivateFolder", 1)[1].split("\nfunction ", 1)[0]   # 既有資料夾只印指令
    assert "Protect-SecretFile $acct 'wug_account.txt'" in setup and "Protect-SecretFile $key " in setup
    assert "Test-PrivateFolder $Root " in setup and "Test-PrivateFolder $sshDir " in setup
    assert "'S-1-5-18', 'S-1-5-32-544'" in setup
    # 每日執行檔：收集或上傳任一失敗就 exit 1，排程器看得到失敗
    for k in ("'$bad = 0',", "'  if ($rc -ne 0) { $bad++ }',", "'if ($bad -gt 0) { exit 1 }',", "'  $global:LASTEXITCODE = 0',", "$bad++ }',"):
        assert k in setup, k
    assert setup.count("'  if ($rc -ne 0) { $bad++ }',") == 2 and setup.count("'  $global:LASTEXITCODE = 0',") == 4
    # 2.043.0 -Alerts：第二個排程（每分鐘、單次最多 10 分、不重疊），同一個密碼、同一個登記出口；不加 -Alerts 就不登記
    assert "[switch]$Alerts" in setup and "[string]$AlertTaskName = 'webit3_wug_alert'" in setup
    assert "New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 1)" in setup and "(New-TimeSpan -Minutes 10)" in setup
    assert "} else { $skipAlert = $true }" in setup and "if (-not $skipAlert) { Register-Job $AlertTaskName $alertRunPath" in setup
    assert "if ($Alerts) { $need += 'wug_alert_collect.ps1' }" in setup and "'# RELAY-ALERT-RUN v1" in setup
    assert setup.count("Write-RunFile $") == 2 and setup.count("function Register-Job") == 1 and setup.count("Register-Job $") == 2


def test_D6_腳本ASCII_可放進TS樣板字串_無真實IP_與AI目錄正本一致():
    _, push, setup = _d6()
    for name, sc in (("relay_push.ps1", push), ("relay_setup.ps1", setup)):
        assert all(ord(ch) < 128 for ch in sc), f"{name} 要全 ASCII"
        assert "`" not in sc and "${" not in sc and "\\" not in sc and "\r" not in sc, name
        for b in BANNED:
            assert not re.search(b, sc, re.I), (name, b)
        assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", sc), name
    for name, sc in (("relay_push.ps1", push), ("relay_setup.ps1", setup)):
        src = ROOT / "AI" / "tools_relay" / name
        if not src.exists():
            pytest.skip("relay 產出物沒有 AI/")
        assert sc == src.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10)), f"devkit.ts 的 D6 {name} 和 AI/tools_relay/{name} 不一致，請同步"


# ---------------------------------------------------------------- D7 告警狀態收集（2.043.0）
def _d7():
    found = [(c, t) for c in _task_chunks() for t in _all_commands(c) if ALERT_MARK in t]
    assert len(found) == 1, "告警收集腳本應該只有一支"
    return found[0]


def test_D7_告警收集腳本_只在D7_可下載_只讀WhatsUp():
    c, sc = _d7()
    assert c.startswith("D7'") and "file: 'wug_alert_collect.ps1'," in c and sc.startswith(ALERT_MARK + " v4")
    banned = [b for b in _ALWAYS_BANNED if b != "Invoke-RestMethod"] + ["Read-Host", "Get-Credential", "Import-Clixml", "Register-ScheduledTask",
                                                                       "Remove-Item", "Add-Content", "Export-Csv", "sshpass", "SkipCertificateCheck"]
    for bad in banned:
        assert bad.lower() not in sc.lower(), f"告警收集腳本不該出現 {bad}"
    assert len(re.findall(r"-Method Post", sc)) == 1 and "/api/v1/token" in sc.split("-Method Post", 1)[1][:80]
    assert not re.search(r"-Method[ ]+(Put|Delete|Patch)", sc, re.I) and len(re.findall(r"Invoke-RestMethod", sc)) == 2
    for path in ("'/api/v1/device-groups/-1/status'", "'/api/v1/device-groups/-1/devices?view=overview&limit='", "'/monitors/-?view=status&limit=50'"):
        assert path in sc, path
    assert "/api/v1/devices/-?" not in sc                                                # 11-03 實測 400 的路徑不可用
    assert "'wug_account.txt'" in sc and "$pass = $null" in sc and "$headers = $null" in sc
    assert sc.count("WriteAllText") == 1 and "$tmp = $Path + '.tmp'" in sc and "UTF8Encoding($false)" in sc
    assert sc.index("(Join-Path $dir 'manifest.json')") > sc.index("Write-Atomic $final")           # manifest 最後寫
    assert sc.index("Write-Atomic $StateFile") > sc.index("(Join-Path $dir 'manifest.json')")        # 狀態檔在批次寫完之後才更新
    for k in ("source = 'whatsup_alert'", "schema_version = 1", "collector_version = $ScriptVersion", "time_zone = 'UTC'", "count = 1",
              "$batchId = 'wugalert_'", "[int]$TimeBoxSeconds = 420", "[int]$HeartbeatMinutes = 5", "[int]$MaxMonitorCalls = 60",
              "if ($PageLimit -gt 200) { $PageLimit = 200 }", "$scan.complete = $true", "stopped_reason"):
        assert k in sc, k
    assert "AllowMarketHours" not in sc and "-ge 510" not in sc                     # 24 小時監控（使用者 11-03 決定），不擋開盤
    # 11-03 GPT 審查 1：完整＝每頁都有 data.devices、讀到最後一頁、台數接近群組總數；空清單／缺欄位一律不完整
    assert "$scan.complete = $true" in sc and sc.count("$scan.complete = $true") == 1
    assert "-not $x.data.PSObject.Properties['devices']" in sc and "'device list was empty'" in sc
    assert "[double]$scan.group_total * 0.98" in sc and "if ($lastPage) {" in sc
    assert sc.index("if ($lastPage) {") < sc.index("$scan.complete = $true")
    # 審查 2：Unknown 也收（不再排除）
    assert "($ws -ne 'Unknown')" not in sc and "(($ws -ne 'Up') -and ($ws -ne ''))" in sc
    # 審查 4：群組狀態沒變也每 5 分鐘重查上次有問題的設備，指紋不同就完整掃；每 ForceScanMinutes 強制完整掃
    assert "[int]$ForceScanMinutes = 30" in sc and "'/api/v1/devices/' + $id + '/status'" in sc
    assert "if (-not $scan.complete) { $newFp = '' }" in sc
    # GPT 複核：缺分頁資訊或群組總數＝不完整；維護中／狀態未知也進指紋；指紋不可用或重查失敗時不每 5 分鐘全掃
    assert "-not $x.PSObject.Properties['paging']" in sc and "'response has no paging block'" in sc
    assert "elseif ($scan.group_total -eq $null) { $scan.stopped_reason = 'group status has no totalDevices" in sc
    assert "$pieces += ($o.id + ':' + $o.worst_state + ':'); continue }" in sc
    assert sc.index("    $newIds += $o.id") < sc.index("if (($o.worst_state -eq 'Maintenance') -or ($o.worst_state -eq 'Unknown')) { $o.monitors_ok = $true;")
    assert "if (($fp -eq 'skip') -or ($state.fp -and ($fp -eq $state.fp))) { $kind = 'heartbeat' }" in sc
    assert "} catch { $fp = 'skip' }" in sc and "'recheck failed'" not in sc
    assert "($state.fp -ne 'too many') -and ($state.fp -ne 'monitor read failed')" in sc and "[int]$MaxRecheckDevices = 150" in sc


def test_D7_腳本ASCII_可放進TS樣板字串_無真實IP_與AI目錄正本一致():
    _c, sc = _d7()
    assert all(ord(ch) < 128 for ch in sc)
    assert chr(96) not in sc and "${" not in sc and chr(92) not in sc and chr(13) not in sc
    for b in BANNED:
        assert not re.search(b, sc, re.I), b
    assert not re.search(r"[0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}", sc)
    src = ROOT / "AI" / "tools_relay" / "wug_alert_collect.ps1"
    if not src.exists():
        pytest.skip("relay 產出物沒有 AI/")
    assert sc == src.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10)), "devkit.ts 的 D7 腳本和 AI/tools_relay/wug_alert_collect.ps1 不一致，請同步"


def test_匯入端_告警批次由告警tick處理_一般排程不碰():
    src = (ROOT / "APP" / "asset-module" / "backend" / "relay_import.py").read_text(encoding="utf-8")
    assert 'FAST_PREFIX = "wugalert_"' in src and 'LP_PREFIX = "wuglp_"' in src and "not f.name.startswith(FAST_PREFIXES)" in src


def test_vCOps清單腳本_D8_和AI資料夾的正本一致_不寫死位址():
    src = ROOT / "AI" / "tools_relay" / "aria_inventory.ps1"
    found = [t for c in _task_chunks() for t in _all_commands(c) if ARIA_INV_MARK in t]
    assert len(found) == 1, "vCOps 清單腳本應該只有一支（D8）"
    assert "[Parameter(Mandatory=$true)][string]$AriaHost" in found[0]
    assert found[0].count("-Method Post") == 1   # 唯一的 -Method Post 是登入；屬性查詢走 Get-Json 'Post'（唯讀查詢）
    if src.exists():
        assert found[0] == src.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10)), "D8 和 AI/tools_relay/aria_inventory.ps1 不一致"


def test_AD匯出_D9_和AI正本一致_預設只留一個部門_不寫全公司():
    found = [t for c in _task_chunks() for t in _all_commands(c) if AD_EXPORT_MARK in t]
    assert len(found) == 1, "AD 匯出腳本應該只有一支（D9）"
    t = found[0]
    assert "StartsWith($Dept)" in t and "[switch]$All" in t and "Downloads" not in t
    for w in ("Set-ADUser", "Remove-", "New-ADUser", ".Put(", ".CommitChanges"):
        assert w not in t, f"AD 匯出腳本不可寫 AD：{w}"
    src = ROOT / "AI" / "tools_relay" / "ad_export.ps1"
    if src.exists():
        assert t == src.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10)), "D9 和 AI/tools_relay/ad_export.ps1 不一致"


def test_開盤尖峰腳本_D10_和AI正本一致_不寫死位址_不寫Downloads():
    found = [t for c in _task_chunks() for t in _all_commands(c) if ARIA_HOST_MARK in t]
    assert len(found) == 1, "開盤尖峰腳本應該只有一支（D10）"
    t = found[0]
    assert "[Parameter(Mandatory=$true)][string]$AriaHost" in t and "Downloads" not in t and "rollUpType=MAX" in t
    src = ROOT / "AI" / "tools_relay" / "aria_probe.ps1"
    if src.exists():
        assert t == src.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10)), "D10 和 AI/tools_relay/aria_probe.ps1 不一致"


# ---------------------------------------------------------------- D11 上次輪循收集（2.062.0，S13）
def test_D11_上次輪循腳本_只在D11_只讀WhatsUp_ASCII_與AI正本一致():
    found = [(c, t) for c in _task_chunks() for t in _all_commands(c) if LASTPOLL_MARK in t]
    assert len(found) == 1, "上次輪循收集腳本應該只有一支"
    c, sc = found[0]
    assert c.startswith("D11'") and "file: 'wug_lastpoll_collect.ps1'," in c and sc.startswith(LASTPOLL_MARK + " v3")
    banned = [b for b in _ALWAYS_BANNED if b != "Invoke-RestMethod"] + ["Read-Host", "Get-Credential", "Import-Clixml", "Register-ScheduledTask",
                                                                       "Remove-Item", "Add-Content", "Export-Csv", "sshpass", "SkipCertificateCheck"]
    for bad in banned:
        assert bad.lower() not in sc.lower(), f"上次輪循腳本不該出現 {bad}"
    assert len(re.findall(r"-Method Post", sc)) == 1 and "/api/v1/token" in sc.split("-Method Post", 1)[1][:80]
    assert not re.search(r"-Method[ ]+(Put|Delete|Patch)", sc, re.I) and len(re.findall(r"Invoke-RestMethod", sc)) == 2
    for path in ("'/api/v1/device-groups/-?limit=0'", "'/devices?returnHierarchy=true&view=overview&limit=0'", "'?range=lastPolled&returnHierarchy='",
                 "'cpu-utilization'", "'memory-utilization'", "'disk-utilization'", "'&rollupByDevice=true'", "'/monitors/-?type=performance&view=basic&limit=0'", "'/reports/memory-utilization?range=lastPolled&limit=0'"):
        assert path in sc, path
    for k in ("source = 'whatsup_lastpoll'", "schema_version = 1", "$batchId = 'wuglp_'", "counts = @($counts.ToArray())", "'wug_account.txt'",
              "$pass = $null", "$headers = $null", "[switch]$SaveRaw", "[switch]$ProbeSwap", "[int]$TimeBoxSeconds = 1200", "[int]$MaxCalls = 3000", "[int]$GroupMaxCalls = 0",
              "'group not found'", "'name matches '", "$Base -notmatch '^https://", "'wug_lp_groups.txt'", "$kv['base']",
              "function Norm(", "partial = $true", "returnHierarchy=false&view=overview&limit=", "group call budget", "next page id repeated",
              "Configured groups that match no WhatsUp group", "function Read-Split(", "[int]$CpuCapDevices = 100", "WhatsUp report cap", "low coverage"):
        assert k in sc, k
    # 實際主機名稱只寫在 AI/ 文件，腳本（會進 APP/devkit.ts）只有範例網域
    assert "example.test" in sc and "Mandatory=$true" not in sc
    assert "monitors/-?view=details" not in sc                     # WhatsUp 回 400（11-14 實跑）
    assert sc.index("(Join-Path $dir 'manifest.json')") > sc.index("Write-Atomic $final")       # manifest 最後寫
    assert all(ord(ch) < 128 for ch in sc) and chr(96) not in sc and "${" not in sc and chr(92) not in sc and chr(13) not in sc
    assert sc.count("\nexit 0") == 1 and sc.rstrip("\n").endswith("exit 0")                     # jsDelivr 取檔以「換行 exit 0」為結尾
    for b in BANNED:
        assert not re.search(b, sc, re.I), b
    assert not re.search(r"[0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}", sc)
    src = ROOT / "AI" / "tools_relay" / "wug_lastpoll_collect.ps1"
    if not src.exists():
        pytest.skip("relay 產出物沒有 AI/")
    assert sc == src.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10)), "devkit.ts 的 D11 和 AI/tools_relay/wug_lastpoll_collect.ps1 不一致，請同步"


# ---------------------------------------------------------------- D12 VMware 每日檢查快照（2.064.0）
def test_D12_VMware每日檢查腳本_只在D12_只對vROps做GET_不碰ESXi_ASCII_與AI正本一致():
    found = [(c, t) for c in _task_chunks() for t in _all_commands(c) if ARIA_DC_MARK in t]
    assert len(found) == 1, "VMware 每日檢查腳本應該只有一支"
    c, sc = found[0]
    assert c.startswith("D12'") and "file: 'aria_daily_snap.ps1'," in c and sc.startswith(ARIA_DC_MARK + " v1")
    # 自簽憑證只對 AriaHost 放行（同 aria_probe.ps1），所以 ServerCertificateValidationCallback 例外；其餘一律不准
    banned = [b for b in _ALWAYS_BANNED if b not in ("Invoke-RestMethod", "ServerCertificateValidationCallback")] + [
        "Read-Host", "Get-Credential", "Import-Clixml", "Register-ScheduledTask", "Remove-Item", "Add-Content", "Export-Csv", "sshpass",
        "SkipCertificateCheck", "ssh ", "plink", "New-LocalUser", "esxcli", "Connect-VIServer", "PowerCLI", "/sdk", "vim25", "/rest/vcenter"]
    for bad in banned:
        assert bad.lower() not in sc.lower(), f"VMware 每日檢查腳本不該出現 {bad}"
    assert len(re.findall(r"-Method Post", sc)) == 1 and "/auth/token/acquire" in sc.split("-Method Post", 1)[1][:80]
    assert not re.search(r"-Method[ ]+(Put|Delete|Patch)", sc, re.I) and len(re.findall(r"Invoke-RestMethod", sc)) == 2
    assert all("-DisableKeepAlive" in ln for ln in sc.splitlines() if "Invoke-RestMethod" in ln)   # 第 101 次斷線（aria_probe v11）
    for path in ("'/adapters?adapterKindKey=VMWARE'", "'/resources?adapterKind=VMWARE&resourceKind=HostSystem'",
                 "'/resources?adapterKind=VMWARE&resourceKind=Datastore'", "'/alerts?activeOnly=true'", "'/resources/properties?'", "'/resources/stats/latest?'"):
        assert path in sc, path
    for k in ("source = 'aria_dailycheck'", "schema_version = 1", "$batchId = 'ariadc_'", "counts = $counts", "raw = @($raw.ToArray())", "'aria_account.txt'",
              "$pw = $null", "$hd = $null", "[switch]$SaveRaw", "[switch]$SkipIfOkToday", "pageInfo.totalCount", "fetched", "[string]$DsFreeKey"):
        assert k in sc, k
    assert sc.index("(Join-Path $dir 'manifest.json')") > sc.index("Write-Atomic $final")       # manifest 最後寫
    assert all(ord(ch) < 128 for ch in sc) and chr(96) not in sc and "${" not in sc and chr(92) not in sc and chr(13) not in sc
    assert sc.count(chr(10) + "exit 0") == 1 and sc.rstrip(chr(10)).endswith("exit 0")
    for b in BANNED:
        assert not re.search(b, sc, re.I), b
    assert not re.search(r"[0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}", sc)
    src = ROOT / "AI" / "tools_relay" / "aria_daily_snap.ps1"
    if not src.exists():
        pytest.skip("relay 產出物沒有 AI/")
    assert sc == src.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10)), "devkit.ts 的 D12 和 AI/tools_relay/aria_daily_snap.ps1 不一致，請同步"
