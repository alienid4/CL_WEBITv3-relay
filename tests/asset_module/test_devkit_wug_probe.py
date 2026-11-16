"""D1c：WhatsUp REST 探測腳本（PowerShell）逐項守門。腳本是靜態內容、不含任何帳密；只有登入一次 POST、其餘 GET；
只寫 OutDir 以內；沒有 Invoke-Expression／Bypass／-EncodedCommand／登錄檔；開盤時間要輸入 YES；呼叫有上限與節流。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
CONTENT = FE / "content" / "devkit.ts"
PAGE = FE / "pages" / "v2" / "devkit.vue"
SCRIPT_MARK = "# WUG-PROBE-SCRIPT"
# 禁用字樣用字串相接，免得這支測試檔自己出現公司識別字（公開 relay 的去識別替換會改寫字面值）
BANNED = [r"10\.9\d\.", "cat" + "hay", "國" + "泰", "secan" + "sible"]


def _chunks():
    body = CONTENT.read_text(encoding="utf-8").split("// ===== 以下是純函式", 1)[0].split("export const DEVKIT_TASKS", 1)[1]
    return body.split("\n  {\n    id: '")[1:]


def _d1c_script():
    d1c = next(c for c in _chunks() if c.startswith("D1c'"))
    scripts = [t for t in re.findall(r"text:\s*`([^`]*)`", d1c) if SCRIPT_MARK in t]
    assert len(scripts) == 1, "D1c 應該只有一支腳本"
    return d1c, scripts[0]


FORBIDDEN_PS = [
    "Invoke-Expression", r"\biex\b", "Bypass", "-EncodedCommand", r"-enc\b", "FromBase64String", "ToBase64String", "Set-ItemProperty", "New-ItemProperty",
    "Remove-Item", "Remove-ItemProperty", "HKLM:", "HKCU:", "Registry::", r"\breg\s+add", "Start-Process", "Invoke-Command", "Invoke-WebRequest", "Invoke-RestMethod",
    "Set-ExecutionPolicy", "ExecutionPolicy", "Out-File", "Set-Content", "Add-Content", "New-Item", "Move-Item", "Copy-Item", "Export-Csv", "Export-Clixml", "Rename-Item",
    "Clear-Content", "DownloadFile", "UploadFile", "WebClient", "schtasks", "New-ScheduledTask", "Register-", "Send-MailMessage", "Net.Mail",
]


def test_只有登入一次是POST_其餘全是GET():
    _, sc = _d1c_script()
    assert len(re.findall(r"HttpMethod\]::Post", sc)) == 2           # 一次是挑方法、一次是比較；都在 Invoke-WugRequest 裡
    assert not re.search(r"HttpMethod\]::(Put|Delete|Patch|Head|Options|Trace)", sc, re.I)
    assert not re.search(r"-Method\s+(Post|Put|Delete|Patch)", sc, re.I)
    assert "if ($Path -ne '/api/v1/token') { throw 'POST is restricted to token login.' }" in sc      # POST 只准登入路徑
    assert len(re.findall(r"-Form \$form", sc)) == 1 and "'/api/v1/token' -Form $form" in sc         # 全腳本只有一處帶表單（登入）
    assert len(re.findall(r"Invoke-WugRequest -Path", sc)) >= 2


def test_禁用危險寫法():
    _, sc = _d1c_script()
    for pat in FORBIDDEN_PS:
        assert not re.search(pat, sc, re.I), f"腳本含禁用寫法：{pat}"
    assert "ServicePointManager]::ServerCertificateValidationCallback" not in sc          # 不改全域憑證驗證


def test_只寫OutDir以內_沒有別的寫檔路徑():
    _, sc = _d1c_script()
    assert len(re.findall(r"WriteAllText", sc)) == 1 and len(re.findall(r"CreateDirectory", sc)) == 1
    assert "$target = Join-Path $script:RunDir ([IO.Path]::GetFileName($Name))" in sc            # 檔名只取最後一段，擋路徑跳脫
    assert "$script:RunDir = Join-Path $OutDir ('WUG_Probe_' + $stamp)" in sc
    for line in sc.split("\n"):
        if "CreateDirectory" in line:
            assert "$script:RunDir" in line
        if "WriteAllText" in line:
            assert "$target" in line
    for pat in (r"File\]::(?!WriteAllText)(Write|Append|Create|Copy|Move|Delete|Replace)", r"Directory\]::(Delete|Move)", r"StreamWriter", r"\.Save\("):
        assert not re.search(pat, sc), pat


def test_帳密只用Read_Host_不含字面值_不記錄token():
    _, sc = _d1c_script()
    assert "Read-Host 'WhatsUp Gold username'" in sc and "Read-Host 'WhatsUp Gold password' -AsSecureString" in sc
    assert "ConvertTo-SecureString" not in sc and "-AsPlainText" not in sc and "PSCredential" not in sc
    assert not re.search(r"\$\w*(password|passwd|pwd|secret|token)\w*\s*=\s*['\"][^'\"\$]+['\"]", sc, re.I)          # 沒有帳密字面值
    assert "ZeroFreeBSTR" in sc and "$plain = $null; $form = $null" in sc                             # 用完清除
    for line in sc.split("\n"):
        if re.search(r"Add-Log|Write-Host|Add-Verdict|AppendLine", line):
            assert not re.search(r"access_token|\$plain|\$form\b|\$secure|\$userName|Authorization", line), f"記錄行不得碰帳密或 token：{line.strip()}"
    assert sc.count("access_token") == 2                         # 只有「取出」與「判斷有沒有」兩處，都不寫出


def test_節流_逾時_不重導向_呼叫上限():
    _, sc = _d1c_script()
    assert re.search(r"\$script:MaxCalls = 40\n", sc) and "throw 'call limit (40) reached'" in sc          # 精確等於 40（不是 400）
    assert "if ($script:Calls -gt 0) { Start-Sleep -Seconds 1 }" in sc
    assert "FromSeconds(30)" in sc and "AllowAutoRedirect = $false" in sc and "Tls12" in sc
    assert "[switch]$TestOnlyIgnoreCertificate" in sc
    assert "WugProbeCertPolicy" in sc and "handler.ServerCertificateCustomValidationCallback = Accept" in sc       # 只套在這次建立的 HttpClientHandler


def test_開盤時間警告要輸入YES_且在任何呼叫之前():
    _, sc = _d1c_script()
    assert "FromMinutes(510)" in sc and "FromHours(15)" in sc and "AddHours(8)" in sc
    assert "Type YES (capital letters)" in sc and "-cne 'YES'" in sc and "Nothing was called" in sc
    assert sc.index("-cne 'YES'") < sc.index("Invoke-WugRequest -Path '/api/v1/token'")
    assert "請盡量在非開盤時間執行" in sc


def test_涵蓋需求的每一步_判準正確():
    _, sc = _d1c_script()
    for must in ("/api/v1/product/version", "/api/v1/product/timezone", "/api/v1/product/api", "/api/v1/device-groups/-", "cpu-utilization", "memory-utilization",
                 "disk-utilization", "state-change", "lastNHours", "rangeN=48", "range=custom", "nextPageId", "@(7, 30, 60, 90)"):
        assert must in sc, must
    assert "$d0.AddHours(1), $d0.AddHours(1.5)" in sc and "$d0.AddHours(1.5), $d0.AddHours(2)" in sc      # UTC 01:00–01:30 與對照 01:30–02:00
    assert "$m -le 300" in sc and "$m -le 600" in sc and "'PASS'" in sc and "'LIMITED'" in sc and "'FAIL'" in sc
    assert "transport/TLS" in sc and "(400)" in sc and "(401)" in sc and "(403)" in sc            # 登入失敗要分辨
    assert "DEV-01" in sc and "x.x.x.x" in sc and "host.masked" in sc                               # 遮罩
    assert "Sort-Object { $_.Length } -Descending" in sc                                            # 先換長名稱，免得短名稱殘留網域尾巴
    assert "寄回前請自行檢查這些檔案是否含不該外流的內容；密碼與 token 不會被寫入" in sc


def test_失敗不中止_一定產生log():
    _, sc = _d1c_script()
    assert sc.count("function Step") == 1 and "catch { Add-Verdict $Name 'FAIL'" in sc
    fin = sc[sc.rindex("finally {"):]
    assert "probe_log.txt" in fin and "Write-RunFile" in fin


def test_無真實IP或公司識別字_且可安全放進TS樣板字串():
    _, sc = _d1c_script()
    for b in BANNED:
        assert not re.search(b, sc), b
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", sc)                              # 腳本內不放任何 IP 字面值
    assert "`" not in sc and "${" not in sc and "\\" not in sc                                       # 樣板字串裡反引號、${、反斜線都會被改寫
    assert sc.startswith("# WUG-PROBE-SCRIPT")


def test_D1c任務欄位_醒目非開盤提醒_可下載_D1d是備用():
    d1c, _ = _d1c_script()
    assert re.search(r"\n    warn: '[^']*非開盤時間[^']*',\n", d1c)
    assert "file: 'wug_probe.ps1'" in d1c and "只有登入是 POST" in d1c
    d1d = next(c for c in _chunks() if c.startswith("D1d'"))
    assert re.search(r"\n    optional: '[^']*不需要[^']*',\n", d1d)      # 11-10 使用者：D1d 不做（原「備用、不用先跑」）
    ids = [c.split("'", 1)[0] for c in _chunks()]
    assert ids.index("D1c") < ids.index("D1d")
    page = PAGE.read_text(encoding="utf-8")
    assert "data-warn" in page and "data-dl-script" in page and "data-optional" in page


# ===================== 第二輪審查：遮罩白名單、呼叫預算、回應大小、limit、TLS 還原 =====================
def test_呼叫預算_最壞路徑不超過40_每一步至少1次_預算不足時優先保留開盤窗與保存期():
    _, sc = _d1c_script()
    m = re.search(r"\$script:Budget = \[ordered\]@\{([^}]*)\}", sc)
    assert m, "要有固定的呼叫預算表"
    budget = {k: int(v) for k, v in re.findall(r"(\w+) = (\d+)", m.group(1))}
    assert set(budget) == {"login", "product", "groups", "search", "daily", "peak", "retention", "state"}
    assert all(v >= 1 for v in budget.values()), "每一步至少保有 1 次預算"
    assert sum(budget.values()) <= 40, f"最壞路徑 {sum(budget.values())} 次超過 40"
    assert "$worstPath" in sc and "if ($worstPath -gt $script:MaxCalls) { throw" in sc          # 腳本開頭自己算最壞值，超過就不跑
    assert "$script:Reserve = $script:Budget.peak + $script:Budget.retention" in sc            # 保留給開盤窗粒度與保存期
    assert "Test-Budget 1 -Priority:$Priority" in sc and sc.count("-Priority") >= 3             # 這兩步用優先通道
    assert "$script:Budget.groups" in sc and "$script:Budget.search" in sc                       # 迴圈上限來自預算表，不是另寫一個數字


def test_不使用limit_0_全部明確非零_日報表拆成受控窗():
    _, sc = _d1c_script()
    assert not re.search(r"limit=0\b", sc)
    limits = re.findall(r"limit=(\d+)", sc)
    assert limits and all(int(x) > 0 and int(x) <= 100 for x in limits)
    assert "$utcYesterday.AddHours($h) $utcYesterday.AddHours($h + 6)" in sc and "@(0, 6, 12, 18)" in sc      # 一天拆成四個 6 小時窗
    assert "-MaxPages 1" in sc


def test_回應大小上限_過大與格式錯誤分開_不硬ConvertFrom_Json():
    _, sc = _d1c_script()
    assert "$script:MaxBytes = 5242880" in sc and "ResponseHeadersRead" in sc and "ContentLength" in sc
    assert "FAIL_TOO_LARGE" in sc and "malformed response: not valid JSON" in sc
    assert sc.index("FAIL_TOO_LARGE: the response is bigger") < sc.index("ConvertFrom-Json")          # 先擋大小，才解析
    assert "Get-FailVerdict" in sc and "'FAIL_TOO_LARGE'" in sc
    assert "ReadAsStringAsync" not in sc                                                              # 不整包讀成字串
    assert "if ($mem.Length -gt $script:MaxBytes) {" in sc and "$declared -gt $script:MaxBytes" in sc          # 串流讀取時邊讀邊擋，宣告長度也先擋


def test_TLS_還原_憑證回呼先確認屬性存在():
    _, sc = _d1c_script()
    assert "$oldTls = [Net.ServicePointManager]::SecurityProtocol" in sc
    fin = sc[sc.rindex("finally {"):]
    assert "[Net.ServicePointManager]::SecurityProtocol = $oldTls" in fin
    assert "GetProperty('ServerCertificateCustomValidationCallback')" in sc and "needs 4.7.1 or newer" in sc


def test_遮罩_白名單欄位輸出_裝置名一律DEV01_寫檔前最後掃描_IPv6與單段主機名():
    _, sc = _d1c_script()
    for name in ("AllowedNumeric", "AllowedTimes", "AllowedNames", "AllowedText"):
        assert f"$script:{name} = @(" in sc
    assert "function ConvertTo-SafeRow" in sc and "if ($n -eq 'deviceName') { $o[$n] = 'DEV-01'; continue }" in sc
    assert "ConvertTo-Json -InputObject $copy.ToArray()" in sc and "ConvertTo-Json -Depth" not in sc.replace("ConvertTo-Json -InputObject $copy.ToArray() -Depth 8", "")
    assert "function Finalize-Mask" in sc and "(Finalize-Mask $Content)" in sc                       # 每個要寫出的檔案最後再掃一次
    assert "'[0-9A-Fa-f:]*::[0-9A-Fa-f:]*'" in sc and "([0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}" in sc    # IPv6
    assert "-Strict" in sc and "host-masked" in sc                                                      # 單段主機名（無點）
    assert "Get-SafeProduct" in sc and "(not exported)" in sc
    # 任意回應欄位不會被整包輸出：不得對整列／整份回應直接 ConvertTo-Json 後寫檔或記錄
    assert not re.search(r"ConvertTo-Json -Depth \d+ -Compress", sc)
    assert "values of fields outside the export whitelist are never written" in sc


def test_說明文字不保證很輕_寫明已設上限_以及密碼在記憶體短暫明文():
    d1c, _ = _d1c_script()
    assert "很輕" not in d1c
    assert "已設呼叫與大小上限" in d1c and "FAIL_TOO_LARGE" in d1c
    assert "記憶體中會短暫以明文存在" in d1c and "EDR" in d1c
