// 開發協作頁（pages/v2/devkit.vue）的「收集任務」內容。頁面只負責顯示，要新增任務只改這個檔。
// 規則（tests/asset_module/test_devkit_content.py 會檢查）：
//   * 查詢本身一律唯讀：SQL 只能 SELECT；uemcli 只能 show（可重導向到 /tmp 的新檔）；不得含寫入類關鍵字。
//   * 指令文字要逐字原樣（用反引號樣板字串，裡面不可再出現反引號）。
//   * 範例位址只用 192.0.2.x、example.test；不放真實 IP 或公司識別字。
//   * 沒有指令的任務必須標 placeholder: true（等前一步的結果再補）。
//   * 改了指令文字或增減指令，task.ver 加 1（使用者本機的舊進度會顯示「指令已更新」）。

export type DevkitTag = 'WhatsUp' | 'Unity' | 'vCOps' | '其他'

export interface DevkitCommand {
  label: string
  text: string
  /** 成功判準：怎樣算這條跑成功 */
  ok?: string
  /** 失敗時：停止還是繼續，以及怎麼處理 */
  onFail?: string
  /** 要帶回什麼 */
  bring?: string
  /** 這條是一支檔案（腳本）：頁面多一個「下載」按鈕，檔名用這個（含 BOM 的 UTF-8） */
  file?: string
}
export interface DevkitTask {
  id: string
  /** 醒目的提醒（例如：請在非開盤時間執行） */
  warn?: string
  /** 備用任務：標在清單上，說明不用先跑 */
  optional?: string
  tag: DevkitTag
  title: string
  purpose: string
  where: string
  perm: string
  /** 指令內容的版本；改了指令文字或增減指令就加 1 */
  ver: number
  /** 連線前檢查（單條試跑，確認能執行、權限夠；不算進度） */
  precheck?: DevkitCommand[]
  commands: DevkitCommand[]
  /** 失敗訊息怎麼收集帶回 */
  failHow?: string
  expect: string
  returnHow: string
  notes?: string[]
  /** 還沒有指令、等前一步結果再補的任務；沒有進度可勾 */
  placeholder?: boolean
  /** placeholder 任務顯示的狀態字 */
  waiting?: string
}

export const DEVKIT_TAGS: DevkitTag[] = ['WhatsUp', 'Unity', 'vCOps', '其他']

export const DEVKIT_TASKS: DevkitTask[] = [
  {
    id: 'D1',
    optional: '不需要（11-10 盤點）：WhatsUp 已改走 REST（D1c 取樣、D4 每天收集），資料庫這條路不用再跑',
    tag: 'WhatsUp',
    title: 'WhatsUp Gold 資料庫探索（第一步）',
    purpose: '夜間速查要顯示「目前告警中」，需要先知道 WhatsUp 資料庫裡哪些表放著裝置、監控項、告警狀態；這一步只是看有哪些表和欄位，不讀任何監控內容。',
    where: 'WhatsUp Gold 的 SQL Server（SSMS 或 sqlcmd），資料庫通常叫 WhatsUp',
    perm: '唯讀帳號（只做 SELECT）',
    ver: 1,
    commands: [
      {
        label: '列出跟監控、告警、狀態有關的表',
        text: `SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_TYPE='BASE TABLE' AND (TABLE_NAME LIKE '%Monitor%' OR TABLE_NAME LIKE '%Alert%' OR TABLE_NAME LIKE '%State%' OR TABLE_NAME LIKE '%Device%' OR TABLE_NAME LIKE '%Disk%' OR TABLE_NAME LIKE '%Performance%')
ORDER BY TABLE_NAME;`,
        ok: '回傳一張表名清單（幾十列）',
        onFail: '停止：找不到資料庫、沒有權限或沒有任何列時，不要做第 2、3 條，把這條的結果（或錯誤訊息）帶回',
        bring: '第 1 條完整結果（表名清單）',
      },
      {
        label: '看這些表有哪些欄位',
        text: `SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME IN ('Device','NetworkInterface','ActiveMonitorType','PivotActiveMonitorTypeToDevice','ActiveMonitorStateChangeLog')
ORDER BY TABLE_NAME, ORDINAL_POSITION;`,
        ok: '回傳「表名、欄位名、資料型別」三欄多列',
        onFail: '繼續：若報找不到表，在這條備註寫「找不到表」，繼續做第 3 條',
        bring: '結果 CSV；失敗則帶錯誤訊息',
      },
      {
        label: '看監控類型裡跟磁碟有關的',
        text: `SELECT * FROM ActiveMonitorType WHERE sMonitorTypeName LIKE '%Disk%' OR sMonitorTypeName LIKE '%Partition%' OR sMonitorTypeName LIKE '%Storage%' OR sMonitorTypeName LIKE '%磁碟%';`,
        ok: '回傳幾列監控類型（沒有符合的列也算成功，請在備註寫「0 列」）',
        onFail: '繼續：若報找不到表，在這條備註寫「找不到表」，這條就做完了',
        bring: '結果 CSV；失敗則帶錯誤訊息',
      },
    ],
    expect: '三個查詢各自一張結果表：第 1 條是表名清單（幾十列）；第 2 條是「表名、欄位名、資料型別」三欄；第 3 條是幾列監控類型。',
    returnHow: '每個查詢的結果在 SSMS 結果區按右鍵「另存結果為」存成 CSV，放進 lowData；或用「整理結果」頁籤貼上並去識別後帶回。',
    notes: [
      '表名可能因 WhatsUp 版本不同；第 2、3 條若報找不到表，只要把第 1 條的結果帶回即可。',
    ],
  },
  {
    id: 'D1b',
    optional: '不需要（11-10 盤點）：WhatsUp 已改走 REST（D1c 取樣、D4 每天收集），資料庫這條路不用再跑',
    tag: 'WhatsUp',
    title: 'WhatsUp Gold 第二步（待開發）',
    purpose: '第二步：裝置清單、目前告警、告警歷史、磁碟監控——待開發者依第一步結果補上精準查詢。',
    where: 'WhatsUp Gold 的 SQL Server（同第一步）',
    perm: '唯讀帳號（只做 SELECT）',
    ver: 1,
    commands: [],
    expect: '（等第一步結果後補上）',
    returnHow: '（等第一步結果後補上）',
    placeholder: true,
    waiting: '等第一步結果',
  },
  {
    id: 'D2',
    tag: 'Unity',
    title: 'Dell Unity 設定匯出',
    purpose: '把 Unity 上的主機、連接埠、LUN、對應關係、儲存池、設備資訊匯出來，用來對照機房儲存配置；全部都是查詢，不改任何設定。',
    where: 'SSH 登入 Unity 管理介面後，在裡面執行 uemcli',
    perm: '查詢本身唯讀（只用 uemcli 的 show 指令）；會在 /tmp 建立輸出檔（檔名帶日期時間、不覆寫舊檔），需要該目錄的寫入權限',
    ver: 2,
    precheck: [
      { label: '確認 uemcli 能執行（看版本）', text: `uemcli -version` },
      { label: '單條試跑，確認帳號權限夠（只顯示在畫面，不產生檔案）', text: `uemcli /sys/general show` },
    ],
    commands: [
      { label: '主機清單（名稱、IP、OS）', text: `uemcli -output csv /remote/host show -detail > /tmp/unity_hosts_$(date +%Y%m%d_%H%M).csv` },
      { label: '主機連接埠（WWPN／IQN）', text: `uemcli -output csv /remote/host/initiator show -detail > /tmp/unity_initiators_$(date +%Y%m%d_%H%M).csv` },
      { label: 'LUN（名稱、大小、儲存池、WWN）', text: `uemcli -output csv /stor/prov/luns/lun show -detail > /tmp/unity_luns_$(date +%Y%m%d_%H%M).csv` },
      { label: '主機與 LUN 對應', text: `uemcli -output csv /remote/host/hlu show -detail > /tmp/unity_hlu_$(date +%Y%m%d_%H%M).csv` },
      { label: '儲存池容量', text: `uemcli -output csv /stor/config/pool show -detail > /tmp/unity_pools_$(date +%Y%m%d_%H%M).csv` },
      { label: '設備型號、序號、版本', text: `uemcli -output csv /sys/general show -detail > /tmp/unity_system_$(date +%Y%m%d_%H%M).csv` },
    ],
    failHow: '任何一條出現錯誤訊息：不用自己修，把整段錯誤訊息（含你輸入的那一行指令）複製下來，貼到「整理結果」頁籤去識別後帶回；並在那條指令的備註寫「失敗」。',
    expect: '每條指令會在 /tmp 產生一個檔名帶日期時間的 CSV（畫面上不會印出內容）；第一列是欄位名稱，之後每列一筆。',
    returnHow: '把 6 個 CSV 檔取出，放進 lowData；也可以 cat 檔案後貼到「整理結果」頁籤去識別再帶回。',
    notes: [
      '語法依 Unity 5.5 文件；第一次請先單條試跑（先做上面的連線前檢查）；任何錯誤訊息貼回來即可。',
      '檔案存在哪：Unity 管理主機的 /tmp，檔名 unity_<項目>_<日期>_<時間>.csv。/tmp 不能寫就改成有權限的目錄（把指令裡的路徑換掉即可）。',
      '怎麼取回：在你自己的電腦用 scp 或 sftp，例如 scp <帳號>@<Unity管理位址>:/tmp/unity_*_<日期>_*.csv ./lowData/（尖括號換成實際值）；或 sftp 登入後 get /tmp/unity_hosts_<日期>_<時間>.csv。',
      '用完如何清理：取回並確認檔案無誤後，登入 Unity 管理主機刪除自己建立的 unity_*.csv（只刪這幾個檔，不要刪整個 /tmp）。',
      '輸出含主機名與 IP，只存在 lowData，不要貼到公開處。',
    ],
  },
  {
    id: 'D1c',
    warn: '請在非開盤時間執行（平日 08:30–15:00 以外）。如果在開盤時間啟動，腳本會要求輸入 YES 才繼續，而且不建議。',
    tag: 'WhatsUp',
    title: 'WhatsUp REST 取樣探測（PowerShell，只讀）',
    purpose: '容量分析的取數方式定為 WhatsUp REST（只用 GET，不走 SQL）。這支腳本在公司端對「一台」主機做一輪探測，回答：API 通不通、伺服器時區、昨天的 CPU／記憶體／磁碟報表的刻度有多細、開盤 09:00–09:30 看不看得到分鐘級尖峰、7／30／60／90 天前還有沒有資料、告警文字長什麼樣。',
    where: '公司端、連得到 WhatsUp API 的 Windows 電腦（Windows PowerShell 5.1）；不是 221',
    perm: '唯讀：用一個 WhatsUp 唯讀帳號。腳本只有登入是 POST，其餘全是 GET；寫檔只寫在你指定的輸出資料夾；不改執行原則、不寫登錄檔；最壞路徑 29 次呼叫、硬上限 40 次',
    ver: 2,
    commands: [
      {
        label: '腳本 wug_probe.ps1（按「下載腳本」存成檔案，或整段複製貼到 .ps1 檔）。執行：powershell -File wug_probe.ps1 -BaseUrl https://WhatsUp主機:9644 -DeviceName 要探測的主機名 -OutDir 輸出資料夾（憑證名稱對不上、且是可信任網路時才加 -TestOnlyIgnoreCertificate）',
        text: `# WUG-PROBE-SCRIPT v3 : WhatsUp Gold REST read-only probe (login is the only POST; everything else is GET)
# Run on Windows PowerShell 5.1:  powershell -File wug_probe.ps1 -BaseUrl https://wug-host:9644 -DeviceName SOMEHOST
# Please run it OUTSIDE market hours (weekday 08:30-15:00 Asia/Taipei). The script asks you to type YES if you start inside that window.
# No password or token is ever written to disk or printed. Files are written only under -OutDir.
# Call budget is fixed (see $script:Budget): worst path stays under 40 calls, 1 second between calls, every response is capped at 5 MB.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BaseUrl,
    [string]$DeviceName,
    [string]$DeviceId,
    [switch]$TestOnlyIgnoreCertificate,
    [string]$OutDir = (Join-Path ([IO.Path]::GetTempPath()) 'wug_probe')
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
$script:MaxCalls = 40
$script:MaxBytes = 5242880
$script:Calls = 0
$script:Client = $null
$script:Base = $BaseUrl.TrimEnd('/')
$script:RunDir = $null
$script:LogLines = New-Object 'System.Collections.Generic.List[string]'
$script:Verdicts = New-Object 'System.Collections.Generic.List[string]'
$script:NameMask = @{}

# ---------- fixed call budget: worst path is computed here and must stay under the hard limit ----------
$script:Budget = [ordered]@{ login = 1; product = 3; groups = 2; search = 4; daily = 12; peak = 2; retention = 4; state = 1 }
$worstPath = 0
foreach ($b in $script:Budget.Values) {
    if ($b -lt 1) { throw 'budget error: every step needs at least 1 call' }
    $worstPath += $b
}
if ($worstPath -gt $script:MaxCalls) { throw ('budget error: worst path ' + $worstPath + ' calls is over the limit of ' + $script:MaxCalls) }
# If the budget ever runs short, the opening-window and retention steps (the two questions that matter most) keep their calls first.
$script:Reserve = $script:Budget.peak + $script:Budget.retention

# ---------- exported-field whitelist: nothing outside these names is ever written to a log or sample ----------
$script:AllowedNumeric = @('minPercent', 'maxPercent', 'avgPercent', 'size', 'minUsed', 'maxUsed', 'avgUsed', 'avgFree', 'timeFromLastPollSeconds', 'totalSeconds', 'count', 'samples', 'sampleCount', 'numberOfSamples')
$script:AllowedTimes = @('pollTimeUtc', 'timeUtc', 'time', 'timestamp', 'dateTimeUtc', 'startTimeUtc', 'endTimeUtc')
$script:AllowedNames = @('cpu', 'memory', 'disk', 'diskName')
$script:AllowedText = @('monitorTypeName', 'stateName', 'result', 'internalMonitorState')

# ---------- market-hours guard (Asia/Taipei is UTC+8, no daylight saving) ----------
$twNow = [DateTime]::UtcNow.AddHours(8)
$isWeekday = ($twNow.DayOfWeek -ne [DayOfWeek]::Saturday -and $twNow.DayOfWeek -ne [DayOfWeek]::Sunday)
if ($isWeekday -and $twNow.TimeOfDay -ge [TimeSpan]::FromMinutes(510) -and $twNow.TimeOfDay -lt [TimeSpan]::FromHours(15)) {
    Write-Warning 'It is weekday market hours in Taipei (08:30-15:00). 請盡量在非開盤時間執行 / please run outside market hours.'
    $answer = Read-Host 'Type YES (capital letters) to continue anyway, anything else to stop'
    if ($answer -cne 'YES') { Write-Host 'Stopped. Nothing was called.'; return }
}

# ---------- masking ----------
function Mask-Text {
    # Strict is for free text (alert messages): it also masks single-label host names such as ABCD001-001.
    param([string]$Text, [switch]$Strict)
    if ($null -eq $Text) { return '' }
    if ($Text -match '^[0-9]+([.][0-9]+){1,4}$') { return $Text }
    $t = $Text
    foreach ($k in @($script:NameMask.Keys | Sort-Object { $_.Length } -Descending)) {
        if ($k) { $t = [regex]::Replace($t, [regex]::Escape($k), $script:NameMask[$k], 'IgnoreCase') }
    }
    $t = [regex]::Replace($t, '[A-Za-z][A-Za-z0-9-]+([.][A-Za-z0-9-]+)+', 'host.masked')
    $t = [regex]::Replace($t, '([0-9]{1,3}[.]){3}[0-9]{1,3}', 'x.x.x.x')
    $t = [regex]::Replace($t, '[0-9A-Fa-f:]*::[0-9A-Fa-f:]*', 'x:x:x:x')
    $t = [regex]::Replace($t, '([0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}', 'x:x:x:x')
    if ($Strict) {
        $t = [regex]::Replace($t, '(?<![A-Za-z0-9-])[A-Za-z][A-Za-z0-9]*[0-9][A-Za-z0-9]*(-[A-Za-z0-9]+)*(?![A-Za-z0-9-])', 'host-masked')
    }
    return $t
}

function Add-MaskName {
    param($Name)
    $n = [string]$Name
    if (-not $n) { return }
    $script:NameMask[$n] = 'DEV-01'
    $short = ($n -split '[.]')[0]
    if ($short.Length -ge 3) { $script:NameMask[$short] = 'DEV-01' }
}

function Finalize-Mask {
    # last pass over everything that is about to be written: names learned late, IPv4, IPv6 and dotted names
    param([string]$Text)
    $t = Mask-Text $Text
    # token-shaped values (JWT, long opaque strings) never reach a file, whatever echoed them
    $t = [regex]::Replace($t, 'eyJ[A-Za-z0-9_-]{8,}[.][A-Za-z0-9_-]{8,}[.]?[A-Za-z0-9_-]*', '<token>')
    $t = [regex]::Replace($t, '[A-Za-z0-9+/=_-]{40,}', '<long-value>')
    foreach ($k in @($script:NameMask.Keys)) {
        if ($k -and $t.IndexOf($k, [StringComparison]::OrdinalIgnoreCase) -ge 0) { $t = [regex]::Replace($t, [regex]::Escape($k), 'DEV-01', 'IgnoreCase') }
    }
    return $t
}

function Add-Log {
    param([string]$Msg)
    $line = (Get-Date -Format 'HH:mm:ss') + ' ' + $Msg
    $script:LogLines.Add($line) | Out-Null
    Write-Host $line
}

function Add-Verdict {
    param([string]$Step, [string]$Verdict, [string]$Note)
    $script:Verdicts.Add(('{0,-14} {1} : {2}' -f $Verdict, $Step, $Note)) | Out-Null
    Add-Log ('[' + $Verdict + '] ' + $Step + ' - ' + $Note)
}

function Get-FailVerdict {
    param([string]$Err)
    if ($Err -like 'FAIL_TOO_LARGE*') { return 'FAIL_TOO_LARGE' }
    return 'FAIL'
}

function Write-RunFile {
    param([string]$Name, [string]$Content)
    if (-not $script:RunDir) { return }
    $target = Join-Path $script:RunDir ([IO.Path]::GetFileName($Name))
    [IO.File]::WriteAllText($target, (Finalize-Mask $Content), (New-Object System.Text.UTF8Encoding($true)))
}

function Test-Budget {
    param([int]$Need, [switch]$Priority)
    $left = $script:MaxCalls - $script:Calls
    if ($Priority) { return ($left -ge $Need) }
    return (($left - $script:Reserve) -ge $Need)
}

# ---------- HTTP ----------
function Invoke-WugRequest {
    param([string]$Path, [string]$Form)
    if ($script:Calls -ge $script:MaxCalls) { throw 'call limit (40) reached' }
    if ($script:Calls -gt 0) { Start-Sleep -Seconds 1 }
    $script:Calls++
    $method = [System.Net.Http.HttpMethod]::Get
    if ($PSBoundParameters.ContainsKey('Form')) {
        if ($Path -ne '/api/v1/token') { throw 'POST is restricted to token login.' }
        $method = [System.Net.Http.HttpMethod]::Post
    }
    $req = New-Object System.Net.Http.HttpRequestMessage -ArgumentList $method, ([uri]($script:Base + $Path))
    $res = $null
    $out = [pscustomobject]@{ Ok = $false; Status = 0; Ms = 0; Json = $null; Error = ''; TooLarge = $false }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
        if ($method -eq [System.Net.Http.HttpMethod]::Post) {
            $req.Content = New-Object System.Net.Http.StringContent -ArgumentList $Form, ([Text.Encoding]::UTF8), 'application/x-www-form-urlencoded'
            # WhatsUp 的 token 端點不吃帶 charset 的 Content-Type（實測 400）：改成純 application/x-www-form-urlencoded
            $req.Content.Headers.ContentType = New-Object System.Net.Http.Headers.MediaTypeHeaderValue -ArgumentList 'application/x-www-form-urlencoded'
        }
        try { $res = $script:Client.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult() }
        catch { $out.Error = 'transport/TLS failure (certificate trust? hostname? port? firewall?)'; return $out }
        $out.Status = [int]$res.StatusCode
        if (-not $res.IsSuccessStatusCode) {
            $out.Error = 'HTTP ' + $out.Status
            if ($Path -eq '/api/v1/token') {
                try {
                    $es = $res.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
                    $eb = New-Object byte[] 2000
                    $en = $es.Read($eb, 0, $eb.Length)
                    $errText = [Text.Encoding]::UTF8.GetString($eb, 0, $en)
                    $parts = @()
                    # only a standard OAuth error code (fixed list): the server may echo the submitted form (password) or a token in other fields
                    if (($errText -match '"error"[ ]*:[ ]*"([^"]{1,80})"') -and (@('invalid_request','invalid_client','invalid_grant','unauthorized_client','unsupported_grant_type','invalid_scope','server_error','temporarily_unavailable') -ccontains $Matches[1])) { $parts += ('error=' + $Matches[1]) }
                    if ($errText -match '"error_description"') { $parts += 'error_description=(not logged)' }
                    if ($parts.Count -gt 0) { $out.Error += ' (' + (($parts -join '; ')) + ')' }
                } catch { }
            }
            return $out
        }
        $declared = $res.Content.Headers.ContentLength
        if ($null -ne $declared -and $declared -gt $script:MaxBytes) {
            $out.TooLarge = $true
            $out.Error = 'FAIL_TOO_LARGE: the response says it is ' + $declared + ' bytes (limit ' + $script:MaxBytes + '); it was not downloaded or parsed'
            return $out
        }
        $stream = $res.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $mem = New-Object System.IO.MemoryStream
        $buf = New-Object byte[] 65536
        while (($n = $stream.Read($buf, 0, $buf.Length)) -gt 0) {
            $mem.Write($buf, 0, $n)
            if ($mem.Length -gt $script:MaxBytes) {
                $out.TooLarge = $true
                $out.Error = 'FAIL_TOO_LARGE: the response is bigger than ' + $script:MaxBytes + ' bytes; reading was stopped and nothing was parsed'
                return $out
            }
        }
        $text = [Text.Encoding]::UTF8.GetString($mem.ToArray())
        $out.Ok = $true
        try { $out.Json = $text | ConvertFrom-Json } catch { $out.Ok = $false; $out.Error = 'malformed response: not valid JSON (the size was within the limit)' }
    }
    finally {
        $sw.Stop()
        $out.Ms = [int]$sw.ElapsedMilliseconds
        if ($null -ne $res) { $res.Dispose() }
        $req.Dispose()
        $Form = $null
        $text = $null
    }
    return $out
}

function Explain-Failure {
    param($R)
    if ($R.TooLarge) { return 'The server returned far more data than a probe should read; send this log back and the query will be narrowed.' }
    if ($R.Status -eq 0) { return 'Cannot reach the API: check BaseUrl/port 9644, certificate trust (use a name that matches the certificate, or -TestOnlyIgnoreCertificate for a trusted network only), firewall.' }
    if ($R.Status -eq 400) { return 'Request rejected (400). Does NOT prove the password is wrong; send this log back so the request format can be checked.' }
    if ($R.Status -eq 401) { return 'Authentication rejected (401): check account/password, or the token expired.' }
    if ($R.Status -eq 403) { return 'Account has no permission for this resource (403): ask the WhatsUp admin for a read-only role that can see devices and reports.' }
    if ($R.Status -eq 404) { return 'Endpoint not found (404): API version/path differs; send this log back.' }
    return 'Unexpected response; send this log back.'
}

function Get-Rows {
    param($Json)
    if ($null -eq $Json) { return @() }
    $d = $Json
    if ($Json.PSObject.Properties['data']) { $d = $Json.data }
    if ($d -is [array]) { return $d }
    foreach ($p in $d.PSObject.Properties) { if ($p.Value -is [array]) { return $p.Value } }
    return @($d)
}

function Get-Median {
    param([double[]]$Values)
    if (-not $Values -or $Values.Count -eq 0) { return $null }
    $s = @($Values | Sort-Object)
    return $s[[int][math]::Floor(($s.Count - 1) / 2)]
}

function Get-PagedRows {
    # follows paging.nextPageId; capped by $MaxPages and by the call budget; always an explicit non-zero limit in $Path
    param([string]$Path, [int]$MaxPages, [switch]$Priority)
    $rows = New-Object 'System.Collections.Generic.List[object]'
    $pages = 0; $ms = 0; $status = 0; $err = ''; $next = ''
    $seen = @{}
    do {
        if (-not (Test-Budget 1 -Priority:$Priority)) { $err = 'skipped: call budget'; break }
        $url = $Path
        if ($next) {
            if ($seen.ContainsKey($next)) { $err = 'repeated paging cursor'; break }
            $seen[$next] = $true
            $sep = '?'
            if ($url.Contains('?')) { $sep = '&' }
            $url = $url + $sep + 'pageId=' + [uri]::EscapeDataString($next)
        }
        $r = Invoke-WugRequest -Path $url
        $pages++; $ms += $r.Ms; $status = $r.Status
        if (-not $r.Ok) { $err = $r.Error + ' ' + (Explain-Failure $r); break }
        foreach ($row in @(Get-Rows $r.Json)) { $rows.Add($row) | Out-Null }
        $next = ''
        if ($r.Json.PSObject.Properties['paging'] -and $r.Json.paging) { $next = [string]$r.Json.paging.nextPageId }
    } while ($next -and $pages -lt $MaxPages)
    if ($next -and $pages -ge $MaxPages -and -not $err) { $err = 'stopped at page cap (' + $MaxPages + '), more pages exist' }
    return [pscustomobject]@{ Rows = $rows.ToArray(); Pages = $pages; Ms = $ms; Status = $status; Error = $err }
}

function Merge-Fetch {
    param($Parts)
    $rows = New-Object 'System.Collections.Generic.List[object]'
    $pages = 0; $ms = 0; $err = ''
    foreach ($p in $Parts) {
        foreach ($r in @($p.Rows)) { $rows.Add($r) | Out-Null }
        $pages += $p.Pages; $ms += $p.Ms
        if ($p.Error -and -not $err) { $err = $p.Error }
    }
    return [pscustomobject]@{ Rows = $rows.ToArray(); Pages = $pages; Ms = $ms; Status = 200; Error = $err }
}

function Get-SeriesInfo {
    param($Row)
    $seriesProp = $null
    if ($Row.PSObject.Properties['series'] -and $Row.series -is [array]) { $seriesProp = 'series' }
    else { foreach ($p in $Row.PSObject.Properties) { if ($p.Value -is [array] -and $p.Value.Count -gt 0 -and $p.Value[0] -is [pscustomobject]) { $seriesProp = $p.Name; break } } }
    if (-not $seriesProp) { return $null }
    $pts = @($Row.$seriesProp)
    $timeProp = $null
    if ($pts.Count -gt 0) {
        foreach ($p in $pts[0].PSObject.Properties) { if ($p.Name -match '^(pollTimeUtc|timeUtc|time|timestamp|dateTimeUtc)$') { $timeProp = $p.Name; break } }
        if (-not $timeProp) { foreach ($p in $pts[0].PSObject.Properties) { if ($p.Name -match 'time|date|utc') { $timeProp = $p.Name; break } } }
    }
    return [pscustomobject]@{ Name = $seriesProp; Points = $pts; TimeProp = $timeProp }
}

function Summarize-Report {
    param($Fetch, [string]$Label)
    $rows = @($Fetch.Rows)
    $counts = New-Object 'System.Collections.Generic.List[double]'
    $gaps = New-Object 'System.Collections.Generic.List[double]'
    $times = New-Object 'System.Collections.Generic.List[DateTimeOffset]'
    $rowFields = @{}; $pointFields = @{}; $nulls = @{ minPercent = 0; maxPercent = 0; avgPercent = 0 }; $disks = @{}
    foreach ($row in $rows) {
        if ($row.PSObject.Properties['deviceName']) { Add-MaskName $row.deviceName }
        foreach ($p in $row.PSObject.Properties) { $rowFields[$p.Name] = $true }
        foreach ($f in @('minPercent', 'maxPercent', 'avgPercent')) { if ($row.PSObject.Properties[$f] -and $null -eq $row.$f) { $nulls[$f]++ } }
        foreach ($dn in @('disk', 'diskName')) { if ($row.PSObject.Properties[$dn] -and $row.$dn) { $disks[(Mask-Text ([string]$row.$dn))] = $true; break } }
        $si = Get-SeriesInfo $row
        if ($si) {
            $counts.Add([double]$si.Points.Count) | Out-Null
            $prev = $null
            foreach ($pt in $si.Points) {
                foreach ($p in $pt.PSObject.Properties) { $pointFields[$p.Name] = $true }
                foreach ($f in @('minPercent', 'maxPercent', 'avgPercent')) { if ($pt.PSObject.Properties[$f] -and $null -eq $pt.$f) { $nulls[$f]++ } }
                if ($si.TimeProp) {
                    $t = [DateTimeOffset]::MinValue
                    if ([DateTimeOffset]::TryParse([string]$pt.($si.TimeProp), [ref]$t)) {
                        $times.Add($t) | Out-Null
                        if ($null -ne $prev) { $gaps.Add([math]::Abs(($t - $prev).TotalSeconds)) | Out-Null }
                        $prev = $t
                    }
                }
            }
        }
    }
    $allNames = @($rowFields.Keys) + @($pointFields.Keys)
    $sampleLike = @($allNames | Where-Object { $_ -match 'count|samples|numberOf|polls' })
    $gapMin = $null; $gapMax = $null; $gapMed = Get-Median ($gaps.ToArray())
    if ($gaps.Count -gt 0) { $gapMin = ($gaps | Measure-Object -Minimum).Minimum; $gapMax = ($gaps | Measure-Object -Maximum).Maximum }
    $range = 'n/a'
    if ($times.Count -gt 0) { $range = (($times | Sort-Object | Select-Object -First 1).UtcDateTime.ToString('o')) + ' .. ' + (($times | Sort-Object | Select-Object -Last 1).UtcDateTime.ToString('o')) }
    Add-Log ($Label + ': rows=' + $rows.Count + ' pages=' + $Fetch.Pages + ' ms=' + $Fetch.Ms + ' series-points(min/median/max)=' + $(if ($counts.Count) { ('{0}/{1}/{2}' -f ($counts | Measure-Object -Minimum).Minimum, (Get-Median ($counts.ToArray())), ($counts | Measure-Object -Maximum).Maximum) } else { 'n/a' }))
    Add-Log ($Label + ': adjacent-gap seconds min/median/max=' + $gapMin + '/' + $gapMed + '/' + $gapMax + ' time-range=' + $range)
    Add-Log ($Label + ': field NAMES present (values of fields outside the export whitelist are never written)  row=' + ((@($rowFields.Keys) | Sort-Object) -join ',') + ' | point=' + ((@($pointFields.Keys) | Sort-Object) -join ','))
    Add-Log ($Label + ': null counts min/max/avgPercent=' + $nulls.minPercent + '/' + $nulls.maxPercent + '/' + $nulls.avgPercent + ' sample-count-like fields=' + $(if ($sampleLike.Count) { ($sampleLike -join ',') } else { 'none' }))
    if ($disks.Count -gt 0) { Add-Log ($Label + ': component names (first 12)=' + ((@($disks.Keys) | Select-Object -First 12) -join ' ; ')) }
    return [pscustomobject]@{ Rows = $rows.Count; GapMedian = $gapMed; GapMax = $gapMax; PointCount = $(if ($counts.Count) { ($counts | Measure-Object -Sum).Sum } else { 0 }); Fields = $allNames }
}

function ConvertTo-SafeRow {
    # whitelist export: only explicitly listed fields leave this function; device names are always DEV-01
    param($Row, [int]$MaxPoints = 5)
    $o = [ordered]@{}
    foreach ($p in $Row.PSObject.Properties) {
        $n = $p.Name; $v = $p.Value
        if ($n -eq 'deviceName') { $o[$n] = 'DEV-01'; continue }
        if ($v -is [array]) {
            $pts = @()
            foreach ($pt in @($v | Select-Object -First $MaxPoints)) { if ($pt -is [pscustomobject]) { $pts += ConvertTo-SafeRow $pt 0 } }
            if ($pts.Count -gt 0) { $o[$n] = $pts }
            continue
        }
        if ($script:AllowedNames -contains $n) { $o[$n] = Mask-Text ([string]$v); continue }
        if ($script:AllowedText -contains $n) { $o[$n] = Mask-Text ([string]$v) -Strict; continue }
        if ($script:AllowedTimes -contains $n) {
            if ([string]$v -match '^[0-9TZ:. +-]+$') { $o[$n] = [string]$v } else { $o[$n] = '(not exported)' }
            continue
        }
        if ($script:AllowedNumeric -contains $n) { if ($null -eq $v -or $v -is [ValueType]) { $o[$n] = $v }; continue }
    }
    return [pscustomobject]$o
}

function Save-Sample {
    param([string]$Name, $Fetch)
    $copy = New-Object 'System.Collections.Generic.List[object]'
    foreach ($row in @($Fetch.Rows | Select-Object -First 3)) { $copy.Add((ConvertTo-SafeRow $row)) | Out-Null }
    Write-RunFile ($Name + '.json') (ConvertTo-Json -InputObject $copy.ToArray() -Depth 8)
}

function Get-ReportPath {
    param([string]$Id, [string]$Kind, [DateTime]$StartUtc, [DateTime]$EndUtc)
    $q = '?range=custom&rangeStartUtc=' + [uri]::EscapeDataString($StartUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')) + '&rangeEndUtc=' + [uri]::EscapeDataString($EndUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')) + '&limit=100'
    return '/api/v1/devices/' + [uri]::EscapeDataString($Id) + '/reports/' + $Kind + $q
}

function Get-SafeProduct {
    # product endpoints: only a few short, well-known keys are printed
    param($Json)
    $d = $Json
    if ($Json.PSObject.Properties['data']) { $d = $Json.data }
    if ($d -is [string]) { return (Mask-Text $d) }
    $parts = @()
    foreach ($p in $d.PSObject.Properties) {
        if (@('name', 'version', 'build', 'release', 'api', 'apiVersion', 'value', 'timezone', 'edition') -contains $p.Name) { $parts += ($p.Name + '=' + (Mask-Text ([string]$p.Value))) }
        else { $parts += ($p.Name + '=(not exported)') }
    }
    return ($parts -join '; ')
}

function Step {
    param([string]$Name, [scriptblock]$Body)
    Add-Log ('===== ' + $Name)
    try { & $Body }
    catch { Add-Verdict $Name 'FAIL' ('step aborted, continuing with the next step (script line ' + $_.InvocationInfo.ScriptLineNumber + '): ' + (Mask-Text $_.Exception.Message)) }
}

# ---------- main ----------
$stamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
$script:RunDir = Join-Path $OutDir ('WUG_Probe_' + $stamp)
$handler = New-Object System.Net.Http.HttpClientHandler
$handler.AllowAutoRedirect = $false
$oldTls = [Net.ServicePointManager]::SecurityProtocol
try {
    [IO.Directory]::CreateDirectory($script:RunDir) | Out-Null
    Add-Log ('WUG probe started (Taipei time ' + $twNow.ToString('yyyy-MM-dd HH:mm') + '), hard limit ' + $script:MaxCalls + ' calls, worst planned path ' + $worstPath + ', 1 second between calls, responses capped at 5 MB')
    $baseUri = [uri]$script:Base
    if (-not $baseUri.IsAbsoluteUri -or $baseUri.Scheme -ne 'https' -or $baseUri.UserInfo -or $baseUri.Query -or $baseUri.Fragment -or $baseUri.AbsolutePath -ne '/') {
        throw 'BaseUrl must be an HTTPS origin such as https://host:9644 (no credentials, path or query).'
    }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    if ($TestOnlyIgnoreCertificate) {
        if ($null -eq [System.Net.Http.HttpClientHandler].GetProperty('ServerCertificateCustomValidationCallback')) {
            throw 'TestOnlyIgnoreCertificate is not supported on this .NET Framework (it needs 4.7.1 or newer). Use the server name that matches its certificate instead.'
        }
        Write-Warning 'TEST ONLY: certificate verification is disabled for this run only.'
        if (-not ('WugProbeCertPolicy' -as [type])) {
            Add-Type -ReferencedAssemblies ([System.Net.Http.HttpClient].Assembly.Location) -TypeDefinition @'
using System.Net.Http;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
public static class WugProbeCertPolicy {
    public static bool Accept(HttpRequestMessage r, X509Certificate2 c, X509Chain h, SslPolicyErrors e) { return true; }
    public static void Configure(HttpClientHandler handler) { handler.ServerCertificateCustomValidationCallback = Accept; }
}
'@
        }
        [WugProbeCertPolicy]::Configure($handler)
    }
    $script:Client = New-Object System.Net.Http.HttpClient -ArgumentList $handler
    $script:Client.Timeout = [TimeSpan]::FromSeconds(30)
    if ($DeviceName) { Add-MaskName $DeviceName }

    # ----- login (the only POST) -----
    $script:LoggedIn = $false
    Step '0 login' {
        $userName = Read-Host 'WhatsUp Gold username'
        $secure = Read-Host 'WhatsUp Gold password' -AsSecureString
        $bstr = [IntPtr]::Zero
        try {
            $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
            $fields = [ordered]@{ grant_type = 'password'; username = $userName; password = $plain }
            $form = (@($fields.Keys | ForEach-Object { $_ + '=' + [uri]::EscapeDataString([string]$fields[$_]) }) -join '&')
            $fields = $null
            $r = Invoke-WugRequest -Path '/api/v1/token' -Form $form
        }
        finally {
            if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
            $plain = $null; $form = $null; $secure = $null; $userName = $null
        }
        if ($r.Ok -and $r.Json -and $r.Json.access_token) {
            $script:Client.DefaultRequestHeaders.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue -ArgumentList 'Bearer', ([string]$r.Json.access_token)
            $script:LoggedIn = $true
            Add-Verdict '0 login' 'PASS' ('token obtained (' + $r.Ms + ' ms); token not logged')
        } else {
            Add-Verdict '0 login' (Get-FailVerdict $r.Error) ($r.Error + ' ' + (Explain-Failure $r))
        }
    }

    if ($script:LoggedIn) {
        # ----- 1 environment -----
        Step '1 environment (version / timezone / api)' {
            foreach ($p in @('/api/v1/product/version', '/api/v1/product/timezone', '/api/v1/product/api')) {
                if (-not (Test-Budget 1)) { Add-Log ($p + ' skipped: call budget'); continue }
                $r = Invoke-WugRequest -Path $p
                if ($r.Ok) {
                    $txt = Get-SafeProduct $r.Json
                    if ($txt.Length -gt 300) { $txt = $txt.Substring(0, 300) }
                    Add-Log ($p + ' -> HTTP ' + $r.Status + ' ' + $r.Ms + ' ms : ' + $txt)
                } else { Add-Log ($p + ' -> ' + $r.Error + ' ' + (Explain-Failure $r)) }
            }
            Add-Verdict '1 environment' 'UNKNOWN' 'read the server timezone above: it decides how WhatsUp times map to Taipei trading slots'
        }

        # ----- 2 devices -----
        $script:DevId = $DeviceId
        $script:FoundId = $null
        Step '2 device groups and target device' {
            $g = Get-PagedRows -Path '/api/v1/device-groups/-?view=summary&limit=100' -MaxPages $script:Budget.groups
            $groups = @($g.Rows)
            Add-Log ('device groups visible: ' + $groups.Count + ' (pages ' + $g.Pages + ', ' + $g.Ms + ' ms) ' + $g.Error)
            if (-not $script:DevId -and $DeviceName) {
                $tried = 0
                foreach ($grp in $groups) {
                    if ($script:DevId -or $tried -ge $script:Budget.search) { break }
                    $tried++
                    $d = Get-PagedRows -Path ('/api/v1/device-groups/' + [uri]::EscapeDataString([string]$grp.id) + '/devices?view=overview&limit=100') -MaxPages 1
                    foreach ($dev in @($d.Rows)) {
                        if ([string]$dev.name -ieq $DeviceName -or ([string]$dev.name).StartsWith($DeviceName + '.', 'OrdinalIgnoreCase')) {
                            $script:FoundId = [string]$dev.id
                            Add-MaskName ([string]$dev.name)
                            Add-Log ('device found: id=' + $dev.id + ' networkAddress present=' + [bool]$dev.networkAddress)
                            break
                        }
                    }
                    if ($script:FoundId) { $script:DevId = $script:FoundId }
                }
            }
            if ($script:DevId) { Add-Verdict '2 device' 'PASS' ('target device id ' + $script:DevId) }
            else { Add-Verdict '2 device' 'FAIL' 'target device not found (use -DeviceId, or a name visible to this account); steps 3-6 skipped' }
        }

        if ($script:DevId) {
            $yTw = $twNow.Date.AddDays(-1)            # yesterday in Taipei
            $utcYesterday = [DateTime]::SpecifyKind([DateTime]::UtcNow.Date.AddDays(-1), 'Utc')

            # ----- 3 one UTC day of cpu / memory / disk, in four 6-hour windows (each window: one page) -----
            Step '3 reports for yesterday (UTC 00:00-24:00, four 6-hour windows)' {
                foreach ($kind in @('cpu-utilization', 'memory-utilization', 'disk-utilization')) {
                    $parts = @()
                    foreach ($h in @(0, 6, 12, 18)) {
                        $parts += Get-PagedRows -Path (Get-ReportPath $script:DevId $kind $utcYesterday.AddHours($h) $utcYesterday.AddHours($h + 6)) -MaxPages 1
                    }
                    $f = Merge-Fetch $parts
                    if ($f.Error -and $f.Rows.Count -eq 0) { Add-Verdict ('3 ' + $kind) (Get-FailVerdict $f.Error) ($f.Error); continue }
                    $s = Summarize-Report $f $kind
                    Save-Sample ('sample_' + $kind) $f
                    if ($kind -eq 'memory-utilization') {
                        $hasAvail = @($s.Fields | Where-Object { $_ -match 'avail|free|swap|page' })
                        Add-Log ('memory: fields about available/swap/free = ' + $(if ($hasAvail.Count) { ($hasAvail -join ',') } else { 'none (used percent only?)' }))
                    }
                    if ($f.Error) { Add-Log ($kind + ': note - ' + $f.Error) }
                    if ($s.Rows -gt 0) { Add-Verdict ('3 ' + $kind) 'PASS' ('rows=' + $s.Rows + ' median gap seconds=' + $s.GapMedian) }
                    else { Add-Verdict ('3 ' + $kind) 'UNKNOWN' 'no rows: no data for that day, or the account cannot see this device' }
                }
            }

            # ----- 4 peak granularity: Taipei 09:00-09:30 and 09:30-10:00 yesterday (UTC 01:00-01:30 / 01:30-02:00 of the same calendar date) -----
            Step '4 opening-peak granularity (Taipei 09:00-09:30 vs 09:30-10:00)' {
                try {
                    $d0 = [DateTime]::SpecifyKind($yTw, 'Utc')
                    $wins = @(@('A 09:00-09:30', $d0.AddHours(1), $d0.AddHours(1.5)), @('B 09:30-10:00 (control)', $d0.AddHours(1.5), $d0.AddHours(2)))
                    foreach ($w in $wins) {
                        $f = Get-PagedRows -Path (Get-ReportPath $script:DevId 'cpu-utilization' $w[1] $w[2]) -MaxPages 1 -Priority
                        if ($f.Error -and $f.Rows.Count -eq 0) { Add-Verdict ('4 window ' + $w[0]) (Get-FailVerdict $f.Error) $f.Error; continue }
                        $s = Summarize-Report $f ('window ' + $w[0])
                        Save-Sample ('sample_peak_' + ($w[0].Substring(0, 1))) $f
                        $m = $s.GapMedian
                        if ($null -eq $m) { Add-Verdict ('4 window ' + $w[0]) 'UNKNOWN' 'no adjacent points, cannot judge granularity' }
                        elseif ($m -le 300) { Add-Verdict ('4 window ' + $w[0]) 'PASS' ('median gap ' + $m + ' s (<= 5 min): opening peak can be evaluated') }
                        elseif ($m -le 600) { Add-Verdict ('4 window ' + $w[0]) 'LIMITED' ('median gap ' + $m + ' s (5-10 min): only a rough view of the opening peak') }
                        else { Add-Verdict ('4 window ' + $w[0]) 'FAIL' ('median gap ' + $m + ' s (> 10 min): too coarse to evaluate the opening peak') }
                    }
                }
                finally { $script:Reserve = [math]::Max(0, $script:Reserve - $script:Budget.peak) }
            }

            # ----- 5 retention: one 1-hour window, 7 / 30 / 60 / 90 days ago -----
            Step '5 retention (7/30/60/90 days ago, 1-hour window)' {
                try {
                    foreach ($n in @(7, 30, 60, 90)) {
                        $a = [DateTime]::SpecifyKind([DateTime]::UtcNow.Date.AddDays(-$n).AddHours(1), 'Utc')
                        $f = Get-PagedRows -Path (Get-ReportPath $script:DevId 'cpu-utilization' $a $a.AddHours(1)) -MaxPages 1 -Priority
                        if ($f.Error -and $f.Rows.Count -eq 0) { Add-Verdict ('5 ' + $n + ' days ago') (Get-FailVerdict $f.Error) $f.Error; continue }
                        $s = Summarize-Report $f ($n.ToString() + ' days ago')
                        if ($s.PointCount -gt 0) { Add-Verdict ('5 ' + $n + ' days ago') 'PASS' ('has data, points=' + $s.PointCount + ', median gap seconds=' + $s.GapMedian + ' (compare with step 4 to see whether older data gets coarser)') }
                        else { Add-Verdict ('5 ' + $n + ' days ago') 'UNKNOWN' 'no data at that age (retention shorter, or the device was not monitored then)' }
                    }
                }
                finally { $script:Reserve = [math]::Max(0, $script:Reserve - $script:Budget.retention) }
            }

            # ----- 6 alert text samples -----
            Step '6 state-change text samples (last 48 hours)' {
                $f = Get-PagedRows -Path ('/api/v1/devices/' + [uri]::EscapeDataString($script:DevId) + '/reports/state-change?range=lastNHours&rangeN=48&limit=20&sortBy=startTimeUtc&sortByDir=desc') -MaxPages 1
                if ($f.Error -and $f.Rows.Count -eq 0) { Add-Verdict '6 state-change' (Get-FailVerdict $f.Error) $f.Error; return }
                $i = 0
                foreach ($e in @($f.Rows | Select-Object -First 20)) {
                    $i++
                    if ($e.PSObject.Properties['deviceName']) { Add-MaskName $e.deviceName }
                    $safe = ConvertTo-SafeRow $e 0
                    $res = [string]$safe.result
                    if ($res.Length -gt 200) { $res = $res.Substring(0, 200) }
                    Add-Log ('  #' + $i + ' monitor=' + [string]$safe.monitorTypeName + ' state=' + [string]$safe.stateName + ' result=' + $res)
                }
                Save-Sample 'sample_state_change' $f
                if ($f.Rows.Count -gt 0) { Add-Verdict '6 state-change' 'PASS' ('rows returned=' + $f.Rows.Count + '; check whether disk/memory alert text names the partition') }
                else { Add-Verdict '6 state-change' 'UNKNOWN' 'no state changes in the last 48 hours for this device' }
            }
        }
    }
}
catch {
    Add-Log ('FATAL (log still written): ' + (Mask-Text $_.Exception.Message))
}
finally {
    $body = New-Object System.Text.StringBuilder
    [void]$body.AppendLine('WUG probe log - masked. No password/token/real host name/IP is included.')
    [void]$body.AppendLine('calls made: ' + $script:Calls + ' of ' + $script:MaxCalls + ' (planned worst path ' + $worstPath + ')')
    [void]$body.AppendLine('')
    [void]$body.AppendLine('=== VERDICTS ===')
    foreach ($v in $script:Verdicts) { [void]$body.AppendLine($v) }
    [void]$body.AppendLine('')
    [void]$body.AppendLine('=== DETAIL ===')
    foreach ($l in $script:LogLines) { [void]$body.AppendLine($l) }
    Write-RunFile 'probe_log.txt' $body.ToString()
    if ($null -ne $script:Client) { $script:Client.DefaultRequestHeaders.Clear(); $script:Client.Dispose() }
    $handler.Dispose()
    $script:Client = $null
    [Net.ServicePointManager]::SecurityProtocol = $oldTls
    Write-Host ''
    Write-Host ('Output folder: ' + $script:RunDir)
    Write-Host '寄回前請自行檢查這些檔案是否含不該外流的內容；密碼與 token 不會被寫入。 Please review these files before sending; the password and token are never written.'
}
`,
        file: 'wug_probe.ps1',
        ok: '最後印出輸出資料夾，裡面有 probe_log.txt 與各步驟的精簡樣本 json；probe_log.txt 開頭的 VERDICTS 每一步都有 PASS／FAIL／LIMITED／UNKNOWN',
        onFail: '任何一步失敗腳本會繼續其他步驟並寫進 log，不用重跑；登入失敗時 log 會寫 400／401／403／憑證問題的下一步建議。整支都跑不起來也請把畫面上的錯誤文字一起帶回',
        bring: '輸出資料夾裡的 probe_log.txt 與所有 sample_*.json（已遮罩主機名、IP；寄回前請自己再看一次）',
      },
    ],
    failHow: '腳本失敗不需要自己修：把輸出資料夾（或畫面上的錯誤文字）原樣帶回即可。帳號密碼只在執行時輸入，不會寫進任何檔案或 log。',
    expect: '一份 probe_log.txt（每步的耗時、PASS／FAIL／LIMITED／UNKNOWN 與下一步建議）加上最多 3 列、series 最多前 5 點的精簡 json 樣本。判準：開盤窗相鄰時間差 ≤5 分鐘＝PASS，5–10 分鐘＝限制，>10 分鐘＝FAIL。',
    returnHow: '把輸出資料夾整個壓縮或逐檔帶回（放進 lowData）；寄回前請自行檢查這些檔案是否含不該外流的內容。',
    notes: [
      '已設呼叫與大小上限：最壞路徑 29 次、硬上限 40 次，每次間隔至少 1 秒、單次逾時 30 秒、單次回應超過約 5MB 就中止該步（記為 FAIL_TOO_LARGE）；所有請求都用明確的非零 limit。仍請避開開盤時間。',
      '會輸入兩次：先帳號、按 Enter 後再輸入密碼（密碼不顯示）。',
      '密碼不會寫進任何檔案，但在 PowerShell 程序記憶體中會短暫以明文存在（登入那一瞬間），受公司 EDR／側錄政策約束；用完立即清除。',
      'log 與樣本只輸出白名單欄位（時間、數值、series 點、元件名稱字串），裝置名一律 DEV-01、IP／IPv6／網域名一律遮罩、警示文字另外遮罩無點的主機名；寄回前仍請自己看一次。',
      '總呼叫次數與每一步結果都寫在 probe_log.txt，可以用來確認它沒有做多餘的事。',
    ],
  },
  {
    id: 'D1d',
    optional: '不需要（使用者 11-10：不做）：效能歷史改走 REST（D4）',
    tag: 'WhatsUp',
    title: 'WhatsUp Gold 效能歷史 SQL 探測（備用）',
    purpose: '10.3 容量分析要用 WhatsUp 的 CPU／記憶體／磁碟歷史做分時段分析；「保存 90 天」「刻度夠細」目前只是口述、尚未驗證。這幾條只做 SELECT，確認版本、效能表、最早最晚時間、相鄰取樣間隔、是否有 avg／max／筆數。',
    where: 'WhatsUp Gold 的 SQL Server（同第一步）',
    perm: '唯讀帳號（只做 SELECT）；每條都只查少量裝置與短日期範圍',
    ver: 2,
    commands: [
      {
        label: '資料庫與 SQL Server 版本',
        text: `SELECT DB_NAME() AS db, @@VERSION AS sql_version;`,
        ok: '回傳 1 列',
        onFail: '停止：把錯誤訊息帶回',
        bring: '結果 1 列',
      },
      {
        label: '列出效能相關的表與筆數（找 CPU／記憶體／磁碟歷史放哪）',
        text: `SELECT t.TABLE_NAME, SUM(p.row_count) AS row_count FROM INFORMATION_SCHEMA.TABLES t LEFT JOIN sys.dm_db_partition_stats p ON p.object_id = OBJECT_ID(t.TABLE_SCHEMA + '.' + t.TABLE_NAME) AND p.index_id IN (0,1) WHERE t.TABLE_TYPE = 'BASE TABLE' AND (t.TABLE_NAME LIKE '%Perf%' OR t.TABLE_NAME LIKE '%CPU%' OR t.TABLE_NAME LIKE '%Memory%' OR t.TABLE_NAME LIKE '%Disk%' OR t.TABLE_NAME LIKE '%Utiliz%' OR t.TABLE_NAME LIKE '%Statistic%') GROUP BY t.TABLE_NAME ORDER BY t.TABLE_NAME;`,
        ok: '回傳表名與筆數（可能是 0 列，請在備註寫「0 列」）',
        onFail: '繼續：若報權限不足，備註寫「權限不足」，改帶第一步的表名清單',
        bring: '結果 CSV',
      },
      {
        label: '某張效能表的最早與最晚時間（把 PerfTableName、TimeColumn 換成上一條找到的表與時間欄；筆數第 2 條已有，這裡不再 COUNT）',
        text: `SET LOCK_TIMEOUT 5000; SELECT MIN(TimeColumn) AS earliest, MAX(TimeColumn) AS latest FROM PerfTableName WITH (NOLOCK);`,
        ok: '回傳 1 列：最早、最晚',
        onFail: '繼續：表名或欄名換不對就在備註寫「對不到」，帶回上一條結果由開發者補精準查詢',
        bring: '結果 1 列、你換成的表名與欄名',
      },
      {
        label: '同一張表任選 1 台裝置、最近 3 天內最多 20 筆（看相鄰取樣間隔；把 DeviceIdColumn、AvgColumn、MaxColumn、CountColumn 換成實際欄名，沒有的欄刪掉；123 換成一台測試裝置）',
        text: `SET LOCK_TIMEOUT 5000; SELECT TOP 20 TimeColumn, DeviceIdColumn, AvgColumn, MaxColumn, CountColumn FROM PerfTableName WITH (NOLOCK) WHERE DeviceIdColumn = 123 AND TimeColumn BETWEEN DATEADD(day, -3, GETDATE()) AND GETDATE() ORDER BY TimeColumn DESC;`,
        ok: '回傳 20 列；請看時間欄相鄰差距是 1、5 還是 60 分鐘，欄位有沒有平均、最大值、筆數',
        onFail: '繼續：換一台裝置再試一次；仍失敗就帶錯誤訊息',
        bring: '結果 CSV（去識別後）；另在備註寫相鄰時間差約幾分鐘、較舊資料是否變粗',
      },
    ],
    failHow: '任何一條出現錯誤訊息：不用自己修，把整段錯誤訊息（含指令）貼到「整理結果」頁籤去識別後帶回，並在備註寫「失敗」。',
    expect: '四個結果：版本、效能表與筆數、某表最早最晚時間、單機 20 筆樣本。另請口頭補充：WhatsUp 版本、時間欄是 UTC 還是本地、缺值（離線）如何記錄。',
    returnHow: '每個查詢結果另存 CSV 放進 lowData，或用「整理結果」頁籤貼上並去識別後帶回。',
    notes: [
      '全部只是 SELECT；請不要在正式尖峰時段對整張大表做 COUNT，若很慢就加時間條件或改在離峰跑。',
    ],
  },
  {
    id: 'D1e',
    tag: '其他',
    title: 'AD 人員欄位探測（PowerShell，只讀）',
    purpose: '確認公司 AD 裡有沒有 Email、分機（電話／IP 電話）、部門、職稱等欄位，以及有沒有值；決定「用姓名自動帶出 Email 與分機」這個功能做不做、欄位用哪一個。這支腳本只做 LDAP 查詢，只查你指定的帳號，輸出的值都已遮罩（只留前 2 碼與最後 1 碼）。',
    where: '公司網域內、已加入網域的 Windows 電腦（Windows PowerShell 5.1），用你目前登入的帳號；不是 221',
    perm: '唯讀：只用目前登入帳號的身分做 LDAP 搜尋，不提示輸入密碼、不寫 AD、不寫檔（除非你加 -OutFile 指定輸出檔）',
    ver: 1,
    commands: [
      {
        label: '腳本 ad_probe.ps1（按「下載腳本」存成檔案，或整段複製貼到 .ps1 檔）。執行：powershell -File ad_probe.ps1 -User 你的帳號,另一個帳號 -OutFile ad_probe_out.txt（-User 可放你自己與 webit3sc 等帳號，最多幾個都可以）',
        file: 'ad_probe.ps1',
        text: `# AD-PROBE-SCRIPT v1 : AD read-only attribute probe (LDAP search only; no write, no password prompt)
# Run on a domain-joined Windows PC as the logged-in user:  powershell -File ad_probe.ps1 -User yourAccount[,otherAccount]
param(
  [Parameter(Mandatory=$true)][string[]]$User,
  [string]$OutFile = ''
)
$ErrorActionPreference = 'Stop'
$Attrs = @('displayName','mail','telephoneNumber','ipPhone','mobile','department','title','employeeID','physicalDeliveryOfficeName','manager','sAMAccountName','userAccountControl')
function Mask([string]$v) {
  if ([string]::IsNullOrEmpty($v)) { return '(empty)' }
  if ($v.Length -le 3) { return ('*' * $v.Length) }
  return ($v.Substring(0,2) + ('*' * ($v.Length - 3)) + $v.Substring($v.Length - 1))
}
$lines = New-Object System.Collections.Generic.List[string]
function Say([string]$t) { Write-Host $t; $lines.Add($t) | Out-Null }
Say ('AD probe  ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  (read-only; values are masked)')
try {
  $root = New-Object System.DirectoryServices.DirectoryEntry('LDAP://RootDSE')
  $domain = $root.Properties['defaultNamingContext'][0]
  Say ('Naming context (domain) found: yes')
} catch {
  Say ('Cannot reach AD from this PC: ' + $_.Exception.Message)
  Say 'Run this on a domain-joined PC inside the company network.'
  if ($OutFile) { $lines | Out-File -FilePath $OutFile -Encoding UTF8 }
  exit 1
}
$base = New-Object System.DirectoryServices.DirectoryEntry('LDAP://' + $domain)
foreach ($u in $User) {
  Say ''
  Say ('--- account: ' + (Mask $u))
  if ($u -notmatch '^[A-Za-z0-9._-]{1,40}$') { Say 'skipped: account name has unexpected characters'; continue }
  $ds = New-Object System.DirectoryServices.DirectorySearcher($base)
  $ds.Filter = '(&(objectCategory=person)(objectClass=user)(sAMAccountName=' + $u + '))'
  $ds.SizeLimit = 2
  $ds.PageSize = 2
  foreach ($a in $Attrs) { [void]$ds.PropertiesToLoad.Add($a) }
  $res = $ds.FindAll()
  if ($res.Count -eq 0) { Say 'not found (or not visible to this account)'; continue }
  if ($res.Count -gt 1) { Say 'more than one match: only the first is shown' }
  $r = $res[0]
  foreach ($a in $Attrs) {
    $vals = $r.Properties[$a.ToLower()]
    if ($vals -and $vals.Count -gt 0) {
      $v = [string]$vals[0]
      if ($a -eq 'manager') { $v = 'present' }
      if ($a -eq 'userAccountControl') { Say ('{0,-28} {1}' -f $a, $v); continue }
      Say ('{0,-28} {1}' -f $a, (Mask $v))
    } else {
      Say ('{0,-28} (empty)' -f $a)
    }
  }
}
Say ''
Say 'Done. Only LDAP search was used. Nothing was written to AD.'
if ($OutFile) { $lines | Out-File -FilePath $OutFile -Encoding UTF8; Write-Host ('Saved to ' + $OutFile) }
`,
        ok: '看到「Naming context (domain) found: yes」，每個帳號列出各欄位是 (empty) 還是已遮罩的值',
        onFail: '若顯示 Cannot reach AD：確認這台電腦在公司網路內且已加入網域；仍不行就停止，把訊息帶回',
        bring: '輸出檔 ad_probe_out.txt 的內容（值已遮罩，可直接貼回）；請另外口頭補充：分機通常填在哪一欄、公司 Email 網域是否都一致',
      },
    ],
    expect: '每個帳號 12 個欄位各一行：有值（遮罩）或 (empty)。重點看 mail、telephoneNumber、ipPhone、mobile 哪幾個有值。',
    returnHow: '貼回輸出內容（已遮罩）。不要貼任何密碼；腳本本來就不會要密碼。',
    notes: [
      '只查你指定的帳號，不會列舉整個 AD。',
      '查不到不代表帳號不存在：可能是這個登入身分沒有讀取權限，請把「not found」帶回。',
    ],
  },
  {
    id: 'D3',
    tag: 'Unity',
    title: 'Dell Unity 容量與主機對應收集（PowerShell，只讀，給中繼機）',
    purpose: '儲存設備資料改由「中繼機收集 → 檔案 → 系統匯入」：這支腳本在能連到 Unity 管理位址的 Windows 中繼機上，一台一台、每頁小量、每次呼叫間隔 1 秒地讀取儲存池（名稱、總量、已用、剩餘）、LUN（名稱、大小、掛載的主機）、主機（名稱、型態），每台輸出一個 JSON，再附一份 manifest.json（來源、版本、收集時間、筆數、sha256）。腳本只發 GET，不改 Unity 任何設定。',
    where: '公司端的 Windows 中繼機（Windows PowerShell 5.1），要連得到 Unity 管理位址的 443；不是 221',
    perm: '唯讀：建議申請 Unity 唯讀角色（Operator）帳號；腳本只發 GET，帳密存在 Windows DPAPI 保護的憑證檔，檔案內沒有明文密碼或 token；只寫在你指定的輸出資料夾',
    ver: 1,
    commands: [
      {
        label: '腳本 unity_collect.ps1（按「下載腳本」存成檔案，或整段複製貼到 .ps1 檔）。執行：powershell -File unity_collect.ps1 -ManagementHost Unity管理位址 -OutDir 輸出資料夾（可一次列多台，用逗號分隔；自簽憑證請加 -TrustThumbprint 憑證指紋，不要關閉驗證）',
        file: 'unity_collect.ps1',
        text: `# UNITY-COLLECT-SCRIPT v1 : Dell Unity read-only collector (HTTP GET only; no write to the array; no password prompt)
# One-time credential file (run once, as the same Windows user that will run this script; the file is DPAPI-protected):
#   see the devkit page note: it saves a DPAPI-protected PSCredential file at the path given by -CredentialFile.
# Run:  powershell -File unity_collect.ps1 -ManagementHost unity-a.example.test -OutDir C:/unity_out
param(
  [Parameter(Mandatory=$true)][string[]]$ManagementHost,
  [Parameter(Mandatory=$true)][string]$OutDir,
  [string]$CredentialFile = '',
  [int]$Port = 443,
  [string]$TrustThumbprint = '',
  [int]$PerPage = 50,
  [int]$MaxPagesPerList = 40,
  [int]$MaxCalls = 150
)
$ErrorActionPreference = 'Stop'
$CollectorVersion = 'unity_collect v1'
$SchemaVersion = 1
$script:Calls = 0
$script:BasicAuth = $null
$script:Established = $false
$script:Client = $null
$script:Cookies = $null
$script:Secret = ''

function Mask([string]$v) {
  if ([string]::IsNullOrEmpty($v)) { return '(empty)' }
  if ($v.Length -le 3) { return ('*' * $v.Length) }
  return ($v.Substring(0,2) + ('*' * ($v.Length - 3)) + $v.Substring($v.Length - 1))
}
function Scrub([string]$t) {
  if ($null -eq $t) { return '' }
  foreach ($h in $ManagementHost) { if ($h) { $t = $t.Replace($h, (Mask $h)) } }
  if ($script:Secret) { $t = $t.Replace($script:Secret, '***') }
  return $t
}
function Say([string]$t) { Write-Host (Scrub $t) }

if ($PerPage -lt 1 -or $PerPage -gt 100) { throw 'PerPage must be between 1 and 100.' }
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Port must be between 1 and 65535.' }
foreach ($h in $ManagementHost) {
  if ($h -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]{0,251}$') { throw 'ManagementHost must be a plain host name or IPv4 address (no scheme, path, port or account).' }
  if (($h -eq 'localhost') -or ($h -match '^(127[.]|0[.]0[.]0[.]0|169[.]254[.]|22[4-9][.]|23[0-9][.])')) { throw 'ManagementHost must be the Storage management address (loopback, link-local and multicast addresses are refused).' }
}
if (-not $CredentialFile) { $CredentialFile = Join-Path $env:USERPROFILE 'unity_cred.xml' }
if (-not (Test-Path -LiteralPath $CredentialFile)) {
  throw 'Credential file not found. Create it once (see the devkit page note), then run this script again.'
}
$cred = Import-Clixml -LiteralPath $CredentialFile
if ($null -eq $cred -or -not ($cred -is [System.Management.Automation.PSCredential])) { throw 'Credential file is not a saved PSCredential; create it again as described on the devkit page.' }
$script:Secret = $cred.GetNetworkCredential().Password
$pair = $cred.UserName + ':' + $script:Secret
$script:BasicAuth = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
$pair = $null
$pin = ($TrustThumbprint -replace '[^0-9A-Fa-f]', '').ToUpper()
if ($TrustThumbprint -and $pin.Length -ne 40) { throw 'TrustThumbprint must be a 40-hex-digit SHA-1 certificate thumbprint.' }

Add-Type -AssemblyName System.Net.Http
$oldTls = [Net.ServicePointManager]::SecurityProtocol

function New-UnityClient([string]$Base) {
  $handler = New-Object System.Net.Http.HttpClientHandler
  $handler.AllowAutoRedirect = $false
  $handler.UseCookies = $true
  $handler.UseProxy = $false
  $script:Cookies = New-Object System.Net.CookieContainer
  $handler.CookieContainer = $script:Cookies
  if ($pin) {
    if ($null -eq [System.Net.Http.HttpClientHandler].GetProperty('ServerCertificateCustomValidationCallback')) {
      throw 'TrustThumbprint needs .NET Framework 4.7.1 or newer on this PC.'
    }
    if (-not ('UnityPin' -as [type])) {
      Add-Type -ReferencedAssemblies ([System.Net.Http.HttpClient].Assembly.Location) -TypeDefinition @'
using System;
using System.Net.Http;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
public static class UnityPin {
    public static string Pin = "";
    public static bool Accept(HttpRequestMessage r, X509Certificate2 c, X509Chain h, SslPolicyErrors e) {
        if (e == SslPolicyErrors.None) { return true; }
        return c != null && Pin.Length == 40 && string.Equals(c.Thumbprint, Pin, StringComparison.OrdinalIgnoreCase);
    }
    public static void Configure(HttpClientHandler handler) { handler.ServerCertificateCustomValidationCallback = Accept; }
}
'@
    }
    [UnityPin]::Pin = $pin
    [UnityPin]::Configure($handler)
  }
  $c = New-Object System.Net.Http.HttpClient -ArgumentList $handler
  $c.Timeout = [TimeSpan]::FromSeconds(30)
  $c.MaxResponseContentBufferSize = 5242880
  return $c
}

function Test-SameOrigin([Uri]$Target, [Uri]$BaseUri) {
  if ($Target.UserInfo) { return $false }
  if ($Target.Scheme -ne $BaseUri.Scheme) { return $false }
  if ($Target.DnsSafeHost -ne $BaseUri.DnsSafeHost) { return $false }
  if ($Target.Port -ne $BaseUri.Port) { return $false }
  $p = $Target.AbsolutePath
  if (-not ($p.StartsWith('/api/') -or $p.StartsWith('/cas/'))) { return $false }
  if ($p.Contains('..')) { return $false }
  return $true
}

# Single GET with same-origin redirect following (max 3). Basic auth is sent only until a session cookie exists,
# and only to the same origin. Returns the parsed JSON body.
function Get-Unity([string]$PathAndQuery) {
  $baseUri = New-Object Uri($script:Base)
  $uri = New-Object Uri($baseUri, $PathAndQuery)
  if (-not (Test-SameOrigin $uri $baseUri)) { throw 'Refused: request target is not under /api/ of the Storage.' }
  $hops = 0
  while ($true) {
    if ($script:Calls -ge $MaxCalls) { throw 'Call limit reached (MaxCalls); stopping to stay light on the Storage.' }
    if ($script:Calls -gt 0) { Start-Sleep -Seconds 1 }
    $script:Calls++
    $req = New-Object System.Net.Http.HttpRequestMessage ([System.Net.Http.HttpMethod]::Get), $uri
    [void]$req.Headers.TryAddWithoutValidation('Accept', 'application/json')
    [void]$req.Headers.TryAddWithoutValidation('X-EMC-REST-CLIENT', 'true')
    # Basic 只送給第一個請求（尚無 cookie）與 /cas/ 登入跳；轉回 /api/ 一律只用 cookie
    if (((-not $script:Established) -and ($hops -eq 0)) -or (($hops -gt 0) -and $uri.AbsolutePath.StartsWith('/cas/'))) { [void]$req.Headers.TryAddWithoutValidation('Authorization', $script:BasicAuth) }
    try {
      $resp = $script:Client.SendAsync($req).GetAwaiter().GetResult()
    } catch {
      throw ('Cannot reach the Storage or TLS failed: check host, port, firewall and certificate (use -TrustThumbprint to pin a self-signed certificate). ' + $_.Exception.Message)
    }
    try {
      $code = [int]$resp.StatusCode
      if ($code -ge 300 -and $code -lt 400) {
        if ($hops -ge 3) { throw 'Too many redirects (limit 3).' }
        $loc = ''
        if ($resp.Headers.Location) { $loc = $resp.Headers.Location.OriginalString }
        if ((-not $loc) -or $loc.StartsWith('//') -or $loc.Contains([string][char]92)) { throw 'Refused: redirect target is not acceptable.' }
        $next = New-Object Uri($uri, $loc)
        if (-not (Test-SameOrigin $next $baseUri)) { throw 'Refused: redirect leaves the Storage (different host, port or path).' }
        $uri = $next
        $hops++
        continue
      }
      if ($code -eq 401) { throw 'HTTP 401: account or password rejected. Not retrying, to avoid locking the account.' }
      if ($code -eq 403) { throw 'HTTP 403: the account is not allowed to read this (needs a read-only role such as Operator).' }
      if ($code -ge 400) { throw ('HTTP ' + $code + ' from the Storage.') }
      $ctype = ''
      if ($resp.Content.Headers.ContentType) { $ctype = $resp.Content.Headers.ContentType.MediaType }
      $text = $resp.Content.ReadAsStringAsync().GetAwaiter().GetResult()
      if (($ctype -like '*html*') -or $text.TrimStart().StartsWith('<')) { throw 'Got an HTML page (login page or proxy) instead of JSON.' }
      try { $body = $text | ConvertFrom-Json } catch { throw 'Response is not valid JSON.' }
      if ($script:Cookies.GetCookies($baseUri).Count -gt 0) { $script:Established = $true }
      return $body
    } finally {
      $resp.Dispose()
      $req.Dispose()
    }
  }
}

function Get-List([string]$Type, [string]$Fields) {
  $rows = New-Object System.Collections.Generic.List[object]
  $page = 1
  while ($true) {
    if ($page -gt $MaxPagesPerList) { throw ('More than ' + $MaxPagesPerList + ' pages for ' + $Type + '; raise -MaxPagesPerList and -MaxCalls only if you really need all of them.') }
    $q = '/api/types/' + $Type + '/instances?fields=' + $Fields + '&per_page=' + $PerPage + '&page=' + $page
    $body = Get-Unity $q
    if ($null -eq $body.entries) { throw ('Unexpected response shape for ' + $Type + ' (no entries).') }
    foreach ($e in @($body.entries)) {
      if ($null -ne $e.content) { $rows.Add($e.content) } else { $rows.Add($e) }
    }
    $nextLinks = @($body.links | Where-Object { $_.rel -eq 'next' })
    if ($nextLinks.Count -eq 0 -or @($body.entries).Count -eq 0) { break }
    $page++
  }
  return ,$rows.ToArray()
}

function Save-TextAtomic([string]$Dir, [string]$Name, [string]$Text) {
  $final = Join-Path $Dir ([IO.Path]::GetFileName($Name))
  $tmp = $final + '.tmp'
  try {
    [IO.File]::WriteAllText($tmp, $Text, (New-Object Text.UTF8Encoding $false))
    Move-Item -LiteralPath $tmp -Destination $final -Force
  } finally {
    if (Test-Path -LiteralPath $tmp) { try { [IO.File]::Delete($tmp) } catch { } }      # 失敗時不留含資料的暫存檔
  }
  return $final
}

$collectedAt = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ssK')
$day = (Get-Date).ToString('yyyy-MM-dd')
$runDir = Join-Path $OutDir $day
[void][IO.Directory]::CreateDirectory($runDir)
$files = New-Object System.Collections.Generic.List[object]
$failed = 0
try {
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  foreach ($h in $ManagementHost) {
    $script:Base = 'https://' + $h + ':' + $Port
    $script:Established = $false
    $script:Client = New-UnityClient $script:Base
    try {
      Say ('Collecting ' + (Mask $h))
      $sys = Get-List 'system' 'name,model,serialNumber'
      $pools = Get-List 'pool' 'name,sizeTotal,sizeUsed,sizeFree'
      $luns = Get-List 'lun' 'name,sizeTotal,hostAccess'
      $hosts = Get-List 'host' 'name,type'
      $doc = [ordered]@{
        source = 'unity'
        schema_version = $SchemaVersion
        collector_version = $CollectorVersion
        collected_at = $collectedAt
        management_host = $h
        complete = $true
        system = $sys
        pools = $pools
        luns = $luns
        hosts = $hosts
      }
      $json = ConvertTo-Json -InputObject $doc -Depth 8 -Compress
      $name = 'unity_' + ($h -replace '[^A-Za-z0-9._-]', '_') + '.json'
      $path = Save-TextAtomic $runDir $name $json
      $item = Get-Item -LiteralPath $path
      $files.Add([ordered]@{ name = $name; sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower(); size = $item.Length })
      Say ('OK ' + (Mask $h) + ': pools=' + @($pools).Count + ' luns=' + @($luns).Count + ' hosts=' + @($hosts).Count)
    } catch {
      $failed++
      Say ('FAILED ' + (Mask $h) + ': ' + $_.Exception.Message)
      if ($_.Exception.Message -like 'HTTP 401*') { Say 'Stopping the whole run after a 401 (no retry, no other devices).'; break }
    } finally {
      if ($null -ne $script:Client) { $script:Client.Dispose(); $script:Client = $null }
      $script:Established = $false
    }
  }
  if ($files.Count -gt 0) {
    $manifest = [ordered]@{
      source = 'unity'
      schema_version = $SchemaVersion
      collector_version = $CollectorVersion
      collected_at = $collectedAt
      count = $files.Count
      files = $files.ToArray()
    }
    [void](Save-TextAtomic $runDir 'manifest.json' (ConvertTo-Json -InputObject $manifest -Depth 6))
  }
} finally {
  [Net.ServicePointManager]::SecurityProtocol = $oldTls
  $script:BasicAuth = $null
  $script:Secret = ''
  $cred = $null
}
Say ('DONE: ' + $files.Count + ' device file(s), ' + $failed + ' failed, ' + $script:Calls + ' GET call(s). Output folder: ' + $runDir)
if ($failed -gt 0) { exit 1 }
`,
        ok: '看到「OK …: pools=N luns=N hosts=N」與「DONE: 1 device file(s), 0 failed」，輸出資料夾（以日期命名）裡有 unity_<位址>.json 與 manifest.json',
        onFail: '停止：看到 FAILED 時不要重跑超過一次（401 絕不重試，避免鎖帳號）；把畫面上的訊息（位址已自動遮罩）帶回',
        bring: '畫面輸出的文字，以及 manifest.json（不含任何密碼）；資料 JSON 先不用帶',
      },
    ],
    failHow: '失敗時整段畫面輸出貼到「整理結果」頁籤去識別後帶回；常見：連不上＝防火牆還沒開 443；TLS 失敗＝改用 -TrustThumbprint 指定憑證指紋；HTTP 401＝帳密不對；HTTP 403＝帳號角色不能讀。',
    expect: '每台一個 unity_<位址>.json（內含 system、pools、luns、hosts 四份清單）加一份 manifest.json；manifest 最後才寫入，可當作「這一批完整」的標記。畫面上位址只顯示前 2 碼與最後 1 碼。',
    returnHow: '先在中繼機看畫面與 manifest.json；確認正常後，之後由系統匯入該資料夾（匯入功能另行開發，目前不用把 JSON 傳回）。',
    notes: [
      '第一次要先建立憑證檔（只做一次，用要排程執行的同一個 Windows 使用者）：在 PowerShell 輸入 Get-Credential | Export-Clixml -Path $env:USERPROFILE/unity_cred.xml，視窗裡輸入 Unity 唯讀帳號與密碼。檔案受 Windows DPAPI 保護，只有同一使用者在同一台電腦解得開；腳本本身從不詢問密碼。',
      '真機 Unity 只帶帳密會被導到登入頁：腳本每個請求都帶 X-EMC-REST-CLIENT 與 Accept: application/json，並只跟隨同一台 Unity 的重新導向（最多 3 次、網址須在 /api/ 或 /cas/ 之下），第一次用帳密換 session cookie，之後只用 cookie；cookie 只在該台該次執行內使用，結束即丟棄。',
      '節流與上限：每頁 50 筆、每次呼叫間隔 1 秒、每份清單最多 40 頁、整次最多 150 次呼叫；任何一份清單讀不完就整台不輸出（不會產生不完整的檔案）。盤中請避開，先以一台測試。',
      'TLS 預設驗證；自簽憑證請用 -TrustThumbprint 帶 40 碼 SHA-1 指紋，只在憑證驗證失敗且指紋完全相符時才放行。',
      '輸出的 JSON 含 LUN 與主機名稱，屬內部資料，不要貼到公開處。',
    ],
  },
  {
    id: 'D4',
    tag: 'WhatsUp',
    warn: '請在非開盤時間執行（平日 08:30–15:00 腳本預設會拒絕，除非加 -AllowMarketHours）',
    title: 'WhatsUp 效能資料收集腳本（中繼檔案，PowerShell，只讀）',
    purpose: '正式環境的 WhatsUp 系統主機連不到。這支腳本放在能連到 WhatsUp 的 Windows 管理主機上，每天收集前一天每台設備的 CPU、記憶體、磁碟使用率（只留時間、平均、最大、最小與識別欄），輸出成一個批次資料夾放到共用資料夾；系統只讀這個資料夾，不直接連 WhatsUp。',
    where: '能連到 WhatsUp Gold 的 Windows 管理主機（Windows PowerShell 5.1），用固定的服務帳號執行；不是 221、也不是系統主機',
    perm: '唯讀：WhatsUp 只做 GET（外加一次登入取得權杖），建議申請唯讀帳號；本機只寫輸出資料夾。帳密放在腳本旁的 wug_account.txt（操作者自己用記事本填 username= 與 password=，權限只給執行帳號），輸出檔不含密碼或權杖',
    ver: 2,
    commands: [
      {
        label: '腳本 wug_collect.ps1（按「下載腳本」存成檔案）。同一個資料夾放 wug_ips.txt（一行一個 IPv4，# 開頭是註解）與 wug_account.txt（第一次執行會自動建立空白範本，填好帳密再跑）。執行：powershell -File wug_collect.ps1 -Base https://<WhatsUp主機名稱>:9644 -IpFile wug_ips.txt -OutDir <共用資料夾路徑>。每 100 台一批，各自一個資料夾與 manifest，中途斷掉只損失那一批。',
        file: 'wug_collect.ps1',
        text: `# WUG-RELAY-COLLECT-SCRIPT v3 : WhatsUp Gold read-only collector for the relay folder (GET only, plus token login / refresh)
# Run on the relay Windows host (PowerShell 5.1) as the account that owns the saved credential file.
# Output: one folder per batch = manifest.json + one JSON file per device. The system only reads these files; it never connects to WhatsUp.
# Example:  powershell -File wug_collect.ps1 -Base https://wug.example.test:9644 -IpFile wug_ips.txt -OutDir wug_out
param(
  [Parameter(Mandatory=$true)][string]$Base,
  [string]$IpFile = 'wug_ips.txt',
  [string]$OutDir = 'wug_out',
  [int]$DaysAgo = 1,
  [int]$PageLimit = 100,
  [int]$MaxPages = 20,
  [int]$MaxCalls = 20000,
  [int]$BatchSize = 100,
  [int]$ListMaxPages = 100,
  [int]$RefreshMinutes = 25,
  [switch]$AllowMarketHours
)
$ErrorActionPreference = 'Stop'
$ScriptVersion = 'wug_collect 3'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Do not run during trading hours (weekdays 08:30-15:00 local time) unless you explicitly allow it.
$now = Get-Date
$mins = $now.Hour * 60 + $now.Minute
if (($now.DayOfWeek -ne 'Saturday') -and ($now.DayOfWeek -ne 'Sunday') -and ($mins -ge 510) -and ($mins -lt 900) -and (-not $AllowMarketHours)) {
  Write-Host 'Stopped: this is weekday trading hours (08:30-15:00). Run it after 15:00, or add -AllowMarketHours if a person really needs it now.'
  exit 1
}

# Credentials: plain text file next to this script (username=... / password=...), edited by the operator; only the owner may read it. Nothing secret is ever written to the output.
$cfg = Join-Path $PSScriptRoot 'wug_account.txt'
if (-not (Test-Path -LiteralPath $cfg)) { Write-Host ('Missing ' + $cfg + ' - create it with Notepad: two lines username=... and password=..., readable only by this account; then run again.'); exit 1 }
$kv = @{}
foreach ($l in Get-Content -LiteralPath $cfg) { $i = $l.IndexOf('='); if ($i -gt 0) { $kv[$l.Substring(0, $i).Trim()] = $l.Substring($i + 1) } }
$user = $kv['username']; $pass = $kv['password']; $kv = $null
if (-not $user -or -not $pass) { Write-Host ('username or password is empty in ' + $cfg); exit 1 }
if ($Base -notmatch '^https://[A-Za-z0-9][A-Za-z0-9.-]{0,251}(:[0-9]{1,5})?/?$') { throw 'Base must be an https:// URL with a host name (plain http is refused because the password and token would travel unencrypted).' }
$root = $Base.TrimEnd('/')
# WhatsUp tokens expire after 30 minutes (expires_in=1799, measured 2026-11-09). v2 logged in once, so after ~73 devices every
# later call got 401 and only ~73 of 4684 devices arrived each day. v3 refreshes before 25 minutes, retries once on 401
# (refresh_token first, then the password again), and stops cleanly if it cannot log back in.
$script:refresh = $null
$script:tokAt = $null
$script:headers = $null
function Get-Token([bool]$UseRefresh) {
  if ($UseRefresh -and $script:refresh) { $b = @{ grant_type = 'refresh_token'; refresh_token = $script:refresh } }
  else { $b = @{ grant_type = 'password'; username = $user; password = $pass } }
  $t = Invoke-RestMethod -Method Post -Uri ($root + '/api/v1/token') -Body $b -TimeoutSec 30
  $b = $null
  if (-not $t.access_token) { throw 'token response has no access_token' }
  $script:headers = @{ Authorization = ('Bearer ' + $t.access_token) }
  $script:refresh = [string]$t.refresh_token
  $script:tokAt = Get-Date
  $t = $null
}
function Renew-Token {
  try { Get-Token $true; Write-Host 'TOKEN refreshed'; return } catch { Write-Host ('TOKEN refresh failed: ' + $_.Exception.Message) }
  Get-Token $false
  Write-Host 'TOKEN renewed with password'
}
try {
  Get-Token $false
} catch {
  Write-Host ('Login failed: ' + $_.Exception.Message)
  exit 1
}

$script:Calls = 0
$script:authLost = $false
function Test-Unauthorized($err) {
  try { return ([int]$err.Exception.Response.StatusCode -eq 401) } catch { return $false }
}
function Get-Wug([string]$Path, [int]$Timeout = 30) {
  if ($script:authLost) { throw 'login lost earlier in this run; not calling WhatsUp again' }
  if (((Get-Date) - $script:tokAt).TotalMinutes -ge $RefreshMinutes) {
    try { Renew-Token } catch { $script:authLost = $true; throw ('cannot log back in: ' + $_.Exception.Message) }
  }
  for ($try = 1; $try -le 2; $try++) {
    $script:Calls++
    if ($script:Calls -gt $MaxCalls) { throw 'Call limit reached; stopping.' }
    Start-Sleep -Seconds 1
    try {
      return Invoke-RestMethod -Uri ($root + $Path) -Headers $script:headers -TimeoutSec $Timeout
    } catch {
      if (($try -eq 1) -and (Test-Unauthorized $_)) {
        Write-Host 'TOKEN got 401, logging in again'
        try { Renew-Token } catch { $script:authLost = $true; throw ('cannot log back in: ' + $_.Exception.Message) }
        continue
      }
      throw
    }
  }
}

# WhatsUp returns one summary row per CPU core / memory / disk; the real time points (3 or 10 min apart) are in that row's 'series'.
# Emit one row per point (carrying the core / disk id), so the system sees every point; the system averages cores at the same time.
$ids = @('cpuId','memoryId','diskId','disk')
$vals = @('pollTimeUtc','avgPercent','maxPercent','minPercent')
function Convert-One($src, $idRow) {
  $o = [ordered]@{}
  foreach ($f in $ids) { if ($idRow.PSObject.Properties[$f]) { $o[$f] = $idRow.$f } }
  foreach ($f in $vals) {
    if ($src.PSObject.Properties[$f]) {
      $v = $src.$f
      if ($v -is [DateTime]) { $v = $v.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }
      $o[$f] = $v
    }
  }
  return $o
}
function Convert-Rows($rows) {
  $out = New-Object System.Collections.ArrayList
  foreach ($r in @($rows)) {
    $pts = @()
    if ($r.PSObject.Properties['series']) { $pts = @($r.series) }
    if ($pts.Count -gt 0) {
      foreach ($p in $pts) { $o = Convert-One $p $r; if ($o.Contains('pollTimeUtc')) { [void]$out.Add($o) } }
    } else {
      $o = Convert-One $r $r
      if ($o.Contains('pollTimeUtc')) { [void]$out.Add($o) }
    }
  }
  return ,$out.ToArray()
}
function Write-Atomic([string]$Path, [string]$Text) {
  $tmp = $Path + '.tmp'
  [IO.File]::WriteAllText($tmp, $Text, (New-Object System.Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $tmp -Destination $Path -Force
}

# The day to collect: whole local day (default yesterday), asked from WhatsUp in UTC.
$d0 = (Get-Date).Date.AddDays(-$DaysAgo)
$day = $d0.ToString('yyyy-MM-dd')
$startUtc = $d0.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$endUtc = $d0.AddDays(1).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$stamp = (Get-Date).ToString('yyyyMMddTHHmmss')
$script:part = 0
$script:batchDir = $null
$script:batchId = $null
$files = New-Object System.Collections.ArrayList
$script:total = 0
# Each batch (at most $BatchSize devices) gets its own folder + manifest, so a crash only loses the batch in progress.
function Open-Batch {
  $script:part++
  $script:batchId = 'whatsup_' + $day + '_' + $stamp + '_p' + $script:part.ToString('000')
  $script:batchDir = Join-Path $OutDir $script:batchId
  New-Item -ItemType Directory -Force -Path $script:batchDir | Out-Null
  $files.Clear()
}
# manifest.json is written last, so the system only ever sees a finished batch.
$script:ok = 0
$script:failed = 0
$script:notfound = 0
$script:nIps = 0
function Close-Batch([bool]$Final) {
  if ($files.Count -eq 0) { return }
  $manifest = [ordered]@{
    source = 'whatsup'
    batch_id = $script:batchId
    schema_version = 1
    collector_version = $ScriptVersion
    collected_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
    time_zone = 'UTC'
    range = [ordered]@{ from = $day; to = $day }
    count = $files.Count
    files = $files.ToArray()
    run = [ordered]@{ final = $Final; ips = $script:nIps; ok = $script:ok; failed = $script:failed; notfound = $script:notfound; calls = $script:Calls }
  }
  Write-Atomic (Join-Path $script:batchDir 'manifest.json') ($manifest | ConvertTo-Json -Depth 8 -Compress)
  $script:total += $files.Count
  Write-Host ('BATCH ' + $script:batchId + ' : ' + $files.Count + ' devices')
}

$ips = @(Get-Content -LiteralPath $IpFile | ForEach-Object { $_.Trim() } | Where-Object { $_ -and (-not $_.StartsWith('#')) })
$script:nIps = $ips.Count

# One pass over the whole device list (200 per page, ~30 s a page) builds IP -> device, instead of one search per IP.
# If the list cannot be read, fall back to searching each IP (slower, same result).
$byIp = @{}
$listOk = $false
try {
  $pg = '0'
  $n = 0
  do {
    $x = Get-Wug ('/api/v1/device-groups/-1/devices?view=overview&limit=200&pageId=' + $pg) 180
    foreach ($dv in @($x.data.devices)) { $a = [string]$dv.networkAddress; if ($a -and (-not $byIp.ContainsKey($a))) { $byIp[$a] = $dv } }
    $pg = [string]$x.paging.nextPageId
    $n++
  } while ($pg -and ($n -lt $ListMaxPages))
  $listOk = $true
  Write-Host ('LIST ' + $byIp.Count + ' devices in ' + $n + ' page(s)')
} catch {
  Write-Host ('LIST failed, searching each IP instead: ' + $_.Exception.Message)
}

Open-Batch
foreach ($ip in $ips) {
  $addr = $null
  if ((-not [Net.IPAddress]::TryParse($ip, [ref]$addr)) -or ($addr.AddressFamily -ne 'InterNetwork')) { Write-Host 'SKIP: a line in the IP file is not an IPv4 address'; $script:failed++; continue }
  if ($script:authLost) { $script:failed++; continue }
  try {
    if ($listOk) {
      $dv = $byIp[$ip]
    } else {
      $r = Get-Wug ('/api/v1/device-groups/-1/devices?search=' + $ip + '&view=overview&limit=10')
      $dv = @($r.data.devices | Where-Object { $_.networkAddress -eq $ip })[0]
    }
    if (-not $dv) { Write-Host ('NOT FOUND ' + $ip); $script:notfound++; continue }
    $reports = [ordered]@{}
    foreach ($k in 'cpu-utilization','memory-utilization','disk-utilization') {
      $rows = @()
      $pg = ''
      $n = 0
      do {
        $q = '/api/v1/devices/' + $dv.id + '/reports/' + $k + '?range=custom&rangeStartUtc=' + $startUtc + '&rangeEndUtc=' + $endUtc + '&limit=' + $PageLimit
        if ($pg) { $q += '&pageId=' + $pg }
        $x = Get-Wug $q
        $rows += @($x.data)
        $pg = [string]$x.paging.nextPageId
        $n++
      } while ($pg -and ($n -lt $MaxPages))
      $reports[$k] = Convert-Rows $rows
    }
    # A full batch is closed only when the next device is ready, so the last batch is never empty and always carries the final counts.
    if ($files.Count -ge $BatchSize) { Close-Batch $false; Open-Batch }
    $dev = [ordered]@{ ip = $ip; id = $dv.id; name = [string]$dv.name; reports = $reports }
    $fname = $ip + '.json'
    $final = Join-Path $script:batchDir $fname
    Write-Atomic $final ($dev | ConvertTo-Json -Depth 8 -Compress)
    $item = [ordered]@{ name = $fname; sha256 = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $final).Length }
    [void]$files.Add($item)
    $script:ok++
    Write-Host ('OK ' + $ip)
  } catch {
    $script:failed++
    Write-Host ('FAILED ' + $ip + ' : ' + $_.Exception.Message)
  }
}
$headers = $null
$script:headers = $null
$script:refresh = $null
$pass = $null
Close-Batch $true
$summary = 'ok=' + $script:ok + ' failed=' + $script:failed + ' notfound=' + $script:notfound + ' of ' + $script:nIps + ' IPs'
if ($script:total -eq 0) {
  Write-Host ('No device was collected (' + $summary + '), so no manifest was written (the system will show the source as stale).')
  exit 1
}
if ($script:authLost) { Write-Host 'WARN: lost the WhatsUp login part way; the rest were counted as failed.' }
if ((($script:ok + $script:failed) -gt 0) -and (($script:failed / ($script:ok + $script:failed)) -gt 0.1)) { Write-Host ('WARN: more than 10% failed (' + $summary + '); the system will show the source as incomplete.') }
Write-Host ('DONE ' + $summary + ', ' + $script:total + ' devices in ' + $script:part + ' batch(es), ' + $script:Calls + ' calls')
`,
        ok: '先一行 LIST n devices（整份設備清單），每台顯示 OK，最後一行 DONE ok=… failed=… notfound=…；failed 超過一成會多一行 WARN，系統的資料來源燈會亮「收集不完整」。輸出資料夾裡有 whatsup_日期_時間_pNNN 的批次資料夾，內含 manifest.json 與每台一個 IP.json',
        onFail: '登入失敗：確認網址（要用憑證上的主機名稱與埠）與帳密；NOT FOUND：WhatsUp 裡沒有這個 IP；FAILED：把該行訊息帶回。腳本不會自動重試',
        bring: '只需帶回終端機顯示的文字（不含密碼）；批次資料夾本身由共用資料夾交給系統匯入，不用手動搬',
      },
    ],
    failHow: '任何錯誤：把終端機顯示的整段文字（不含帳密）貼到「整理結果」頁籤去識別後帶回，並在備註寫「失敗」。',
    expect: '一個批次資料夾：manifest.json（來源、版本、收集時間、範圍、每個檔的 sha256）加上每台設備一個 JSON；系統匯入後在「系統設定 → 外部整合 → 中繼匯入」看得到批次與鮮度燈。',
    returnHow: '把輸出資料夾設成共用資料夾（系統主機讀得到），再到「中繼匯入」頁籤填路徑並啟用；不用把檔案傳回給開發者。',
    notes: [
      '每次呼叫間隔 1 秒、每頁最多 100 筆、每台每種報表最多 20 頁、全程最多 5000 次呼叫；超過就停止。',
      '建議用 Windows 工作排程器每天收盤後（例如 16:00）執行一次；WhatsUp 較舊的資料會變粗或消失，所以要每天收，不要隔很多天才收。',
      '憑證檔只有同一個 Windows 使用者在同一台電腦才解得開；換電腦或換執行帳號要重新輸入一次。',
      '時間欄位是 UTC（WhatsUp 的 pollTimeUtc），manifest 的 time_zone 會寫明；整個輸出不含設備名稱以外的識別資料，仍請寄出前確認。',
    ],
  },
  {
    id: 'D5',
    tag: '其他',
    warn: '請在非開盤時間執行（平日 08:30–15:00 腳本預設會拒絕，除非加 -AllowMarketHours）',
    title: '憑證到期探測腳本（中繼檔案，PowerShell，只做 TLS 握手）',
    purpose: '系統主機不對外連線。這支腳本放在公司中繼 Windows PC 上，讀「7.5 憑證盤點 → 設定 → 匯出探測清單」產生的清單，對每個端點做一次只讀 TLS 握手、只讀憑證公開資訊（到期時間、簽發者、序號、SHA-256 指紋、SAN），每 100 個端點輸出一個批次資料夾到共用資料夾；系統只讀檔匯入，探測到的到期日只拿來比對，不覆蓋人工登錄的值。',
    where: '能連到各憑證端點的公司中繼 Windows PC（Windows PowerShell 5.1），用固定帳號執行；不是 221、也不是系統主機',
    perm: '唯讀：只建立 TCP 連線並完成 TLS 握手，握手後立即關閉，不送任何請求、不登入、不需要任何帳號密碼；不讀也不寫任何私鑰；本機只寫輸出資料夾',
    ver: 1,
    commands: [
      {
        label: '腳本 cert_collect.ps1（按「下載腳本」存成檔案）。把憑證頁匯出的 cert_probe_list.txt 放在同一個資料夾，再執行：powershell -File cert_collect.ps1 -ListFile cert_probe_list.txt -OutDir 共用資料夾路徑',
        file: 'cert_collect.ps1',
        text: `# CERT-RELAY-COLLECT-SCRIPT v1 : read-only TLS certificate expiry collector for the relay folder (TLS handshake only, no HTTP, no login)
# Run on the relay Windows host (PowerShell 5.1). It only opens a TCP connection, does a TLS handshake and reads the PUBLIC certificate.
# It never sends a request, never logs in, never reads or writes any private key. The system only reads the output files.
# Endpoint list: one line per endpoint, host:port[,sni]  (IPv6 as [addr]:port). Lines starting with # are comments. Export it from the
# certificate page button (probe list). Example:  powershell -File cert_collect.ps1 -ListFile cert_probe_list.txt -OutDir cert_out
param(
  [string]$ListFile = 'cert_probe_list.txt',
  [string]$OutDir = 'cert_out',
  [int]$TimeoutMs = 5000,
  [int]$BatchSize = 100,
  [int]$MaxEndpoints = 2000,
  [switch]$AllowMarketHours
)
$ErrorActionPreference = 'Stop'
$ScriptVersion = 'cert_collect 1'
$SanOid = '2.5.29' + '.17'   # OID of the Subject Alternative Name extension (written in two parts on purpose)

# Do not run during trading hours (weekdays 08:30-15:00 local time) unless you explicitly allow it.
$now = Get-Date
$mins = $now.Hour * 60 + $now.Minute
if (($now.DayOfWeek -ne 'Saturday') -and ($now.DayOfWeek -ne 'Sunday') -and ($mins -ge 510) -and ($mins -lt 900) -and (-not $AllowMarketHours)) {
  Write-Host 'Stopped: this is weekday trading hours (08:30-15:00). Run it after 15:00, or add -AllowMarketHours if a person really needs it now.'
  exit 1
}
if ($AllowMarketHours) { Write-Host 'WARNING: -AllowMarketHours was given. This run is allowed during weekday trading hours (08:30-15:00) by a person on purpose; it is not for scheduled runs.' }
if (($BatchSize -lt 1) -or ($BatchSize -gt 100)) { $BatchSize = 100 }
if (($TimeoutMs -lt 1000) -or ($TimeoutMs -gt 5000)) { $TimeoutMs = 5000 }

function Write-Atomic([string]$Path, [string]$Text) {
  $tmp = $Path + '.tmp'
  [IO.File]::WriteAllText($tmp, $Text, (New-Object System.Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Cut([string]$s, [int]$n) {
  if ($null -eq $s) { return '' }
  if ($s.Length -gt $n) { return $s.Substring(0, $n) }
  return $s
}

# Parse the endpoint list: host:port[,sni]
$eps = New-Object System.Collections.ArrayList
$seen = @{}
foreach ($raw in @(Get-Content -LiteralPath $ListFile)) {
  $line = ([string]$raw).Trim()
  if ((-not $line) -or $line.StartsWith('#')) { continue }
  $sni = ''
  $c = $line.IndexOf(',')
  if ($c -ge 0) { $sni = $line.Substring($c + 1).Trim().ToLower(); $line = $line.Substring(0, $c).Trim() }
  $h = $line
  $port = 443
  try {
    if ($line.StartsWith('[')) {
      $e = $line.IndexOf(']')
      if ($e -lt 0) { throw 'bad IPv6 line' }
      $h = $line.Substring(1, $e - 1)
      $rest = $line.Substring($e + 1)
      if ($rest.StartsWith(':')) { $port = [int]$rest.Substring(1) }
    } elseif ($line.Split(':').Count -eq 2) {
      $h = $line.Split(':')[0]
      $port = [int]$line.Split(':')[1]
    }
  } catch { Write-Host 'SKIP: a line is not host:port'; continue }
  $h = $h.Trim().ToLower()
  if (([Uri]::CheckHostName($h) -eq 'Unknown') -or ($port -lt 1) -or ($port -gt 65535)) { Write-Host 'SKIP: a line is not host:port'; continue }
  if ($sni -and ([Uri]::CheckHostName($sni) -ne 'Dns')) { Write-Host 'SKIP: SNI is not a host name'; continue }
  $k = $h + '|' + $port + '|' + $sni
  if ($seen.ContainsKey($k)) { continue }
  $seen[$k] = 1
  [void]$eps.Add([ordered]@{ endpoint = $h; port = $port; sni = $sni })
}
if ($eps.Count -eq 0) { Write-Host 'The endpoint list is empty.'; exit 1 }
if ($eps.Count -gt $MaxEndpoints) { Write-Host ('Too many endpoints (' + $eps.Count + '); the limit is ' + $MaxEndpoints + '.'); exit 1 }

# One TLS handshake. The validation callback ACCEPTS any certificate only so we can read it (expired and self-signed ones too);
# the validation result is recorded as text. Nothing is sent after the handshake; the connection is closed right away.
function Probe($ep) {
  $r = [ordered]@{ endpoint = $ep.endpoint; port = $ep.port; sni = $ep.sni; ok = $false; error = ''; subject = ''; issuer = ''; serial = '';
                   sha256 = ''; not_before_utc = ''; not_after_utc = ''; san = @() }
  $tcp = New-Object System.Net.Sockets.TcpClient
  $ssl = $null
  try {
    $ar = $tcp.BeginConnect($ep.endpoint, $ep.port, $null, $null)
    if (-not $ar.AsyncWaitHandle.WaitOne($TimeoutMs)) { throw 'connect timeout' }
    $tcp.EndConnect($ar)
    $ns = $tcp.GetStream()
    $ns.ReadTimeout = $TimeoutMs
    $ns.WriteTimeout = $TimeoutMs
    $script:CertErr = ''
    $cb = [System.Net.Security.RemoteCertificateValidationCallback]{ param($snd, $crt, $chn, $errs) $script:CertErr = [string]$errs; return $true }
    $ssl = New-Object System.Net.Security.SslStream($ns, $false, $cb)
    $target = $ep.endpoint
    if ($ep.sni) { $target = $ep.sni }
    $protos = [Security.Authentication.SslProtocols]::Tls12 -bor [Security.Authentication.SslProtocols]::Tls11 -bor [Security.Authentication.SslProtocols]::Tls
    $ssl.AuthenticateAsClient($target, $null, $protos, $false)
    $x = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
    $sha = [Security.Cryptography.SHA256]::Create()
    $r.sha256 = ([BitConverter]::ToString($sha.ComputeHash($x.RawData))).Replace('-', '').ToLower()
    $r.subject = Cut $x.Subject 500
    $r.issuer = Cut $x.Issuer 500
    $r.serial = Cut $x.SerialNumber 128
    $r.not_before_utc = $x.NotBefore.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $r.not_after_utc = $x.NotAfter.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $names = New-Object System.Collections.ArrayList
    foreach ($ext in $x.Extensions) {
      if ($ext.Oid.Value -eq $SanOid) {
        foreach ($part in $ext.Format($false).Split(',')) {
          $p = $part.Trim()
          $eq = $p.IndexOf('=')
          if ($eq -ge 0) { $p = $p.Substring($eq + 1) }
          if ($p -and ($names.Count -lt 200)) { [void]$names.Add((Cut $p 253)) }
        }
      }
    }
    $r.san = $names.ToArray()
    $r.ok = $true
    if ($script:CertErr -and ($script:CertErr -ne 'None')) { $r.error = Cut ('validation: ' + $script:CertErr) 400 }
    $x = $null
  } catch {
    $r.ok = $false
    $ex = $_.Exception
    while ($ex.InnerException) { $ex = $ex.InnerException }
    $r.error = Cut ([string]$ex.Message) 400
    if (-not $r.error) { $r.error = 'handshake failed' }
  } finally {
    if ($ssl) { $ssl.Dispose() }
    $tcp.Close()
  }
  return $r
}

$stamp = (Get-Date).ToString('yyyyMMddTHHmmss')
$day = (Get-Date).ToString('yyyy-MM-dd')
$total = 0
$batchNo = 0
for ($i = 0; $i -lt $eps.Count; $i += $BatchSize) {
  $batchNo++
  $batchId = 'cert_' + $stamp + '_' + $batchNo
  $batchDir = Join-Path $OutDir $batchId
  New-Item -ItemType Directory -Force -Path $batchDir | Out-Null
  $files = New-Object System.Collections.ArrayList
  $last = [Math]::Min($i + $BatchSize, $eps.Count) - 1
  foreach ($j in $i..$last) {
    if ($j -gt $i) { Start-Sleep -Seconds 1 }
    $res = Probe $eps[$j]
    $fname = 'ep' + ($j + 1).ToString('0000') + '.json'
    $final = Join-Path $batchDir $fname
    Write-Atomic $final ($res | ConvertTo-Json -Depth 4 -Compress)
    $item = [ordered]@{ name = $fname; sha256 = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $final).Length }
    [void]$files.Add($item)
    if ($res.ok) { Write-Host ('OK ' + ($j + 1)) } else { Write-Host ('FAILED ' + ($j + 1) + ' : ' + $res.error) }
  }
  # manifest.json is written last, so the system only ever sees a finished batch.
  $manifest = [ordered]@{
    source = 'cert'
    batch_id = $batchId
    schema_version = 1
    collector_version = $ScriptVersion
    collected_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
    time_zone = 'UTC'
    range = [ordered]@{ from = $day; to = $day }
    count = $files.Count
    files = $files.ToArray()
  }
  Write-Atomic (Join-Path $batchDir 'manifest.json') ($manifest | ConvertTo-Json -Depth 4 -Compress)
  $total += $files.Count
  Write-Host ('BATCH ' + $batchId + ' : ' + $files.Count + ' endpoints')
}
Write-Host ('DONE ' + $total + ' endpoints in ' + $batchNo + ' batches -> ' + $OutDir)
`,
        ok: '每個端點顯示 OK n 或 FAILED n（連不上、逾時也會照樣輸出一筆失敗紀錄），每批一行 BATCH，最後一行 DONE；輸出資料夾裡有 cert_日期時間_序號 的批次資料夾，內含 manifest.json 與每個端點一個 epNNNN.json',
        onFail: '清單是空的或格式不對：重新從憑證頁匯出；大量 FAILED：確認中繼 PC 到該網段的防火牆；把終端機訊息帶回。腳本不會自動重試',
        bring: '只需帶回終端機顯示的文字；批次資料夾由共用資料夾交給系統匯入，不用手動搬',
      },
    ],
    failHow: '任何錯誤：把終端機顯示的整段文字貼到「整理結果」頁籤去識別後帶回，並在備註寫「失敗」。',
    expect: '每 100 個端點一個批次資料夾：manifest.json（source=cert、版本、收集時間、每個檔的 sha256）加上每個端點一個 JSON；系統匯入後在「系統設定 → 外部整合 → 中繼匯入」看得到批次與鮮度燈，憑證清單的「最後探測」「探測比對」欄會更新。',
    returnHow: '把輸出資料夾設成中繼匯入的共用資料夾（系統主機讀得到）；不用把檔案傳回給開發者。',
    notes: [
      '每個端點連線與握手各最多 5 秒、端點之間間隔 1 秒、單次最多 2000 個端點；建議用 Windows 工作排程器每週收盤後執行一次。',
      '憑證驗證失敗（過期、自簽、名稱不符）照樣讀出來並記下原因：這是盤點，不是信任判斷。驗證只在這一條連線內放寬，不改系統或 PowerShell 的全域設定。',
      '輸出不含憑證本體與任何私鑰，只有公開欄位；端點與憑證主體屬內部資料，請勿貼到公開處。',
    ],
  },
  {
    id: 'D6',
    tag: 'WhatsUp',
    title: '中繼機一鍵安裝（WhatsUp 收集＋上傳排程）',
    purpose: '讓任何人都能自己把一台中繼 Windows PC 設好，不用開發者在旁邊打指令：建資料夾、產生上傳用的金鑰、寫好每日執行檔、登記「不論使用者是否登入都執行」的每日排程（先收 WhatsUp 前一天的資料，再用 SFTP 傳到系統主機的中繼資料夾），並檢查電腦會不會睡著。換到別的系統也照這三支檔案做一次即可。',
    where: '能連到 WhatsUp Gold 的公司中繼 Windows PC（Windows PowerShell 5.1），用之後要跑每日排程的那個 Windows 帳號執行；不是 221、也不是系統主機',
    perm: '安裝本身不連 WhatsUp、也不連系統主機；只在本機建資料夾與檔案、在使用者自己的 .ssh 資料夾產生一把金鑰、登記一個排程。不改登錄檔、防火牆、服務、電源設定與執行原則。每日排程對 WhatsUp 唯讀（同 D4），上傳只用 SFTP 放檔',
    ver: 2,
    commands: [
      {
        label: '腳本 relay_push.ps1（按「下載腳本」存成檔案，放到中繼資料夾，預設 C:/webit3_relay）。把批次資料夾用 SFTP 傳到系統主機：每批先傳到系統主機上一個新的隱藏暫存資料夾（每個檔先傳成 .tmp 再改名，manifest.json 最後傳），最後整個資料夾一次改成批次名稱；同名批次已在系統主機上就失敗、不覆蓋。名稱只接受英數、點、減號、底線。上傳與本機搬移都成功才算 OK，傳完的批次搬到 wug_sent，push_log.txt 一批一行。由每日排程自動呼叫，平常不用自己跑。',
        file: 'relay_push.ps1',
        text: `# RELAY-PUSH-SCRIPT v2 : upload finished batches from the relay PC to the system host relay folder by SFTP (SSH key only).
# The key is restricted on the system host side: only from this PC, sftp only, start dir = the relay folder.
# Each batch goes into a fresh hidden staging folder (.up_<batch>_<random>, which the importer skips because it starts with a dot):
# every file goes up as .tmp and is renamed, manifest.json goes last, then the whole folder is renamed to the batch name in one step.
# If a batch of that name is already on the system host, the final rename fails and the batch is reported FAIL; nothing is overwritten.
# A batch folder without manifest.json is still being written and is skipped. Sent batches move to SentDir; push_log.txt keeps one line per batch.
# Example:  powershell -File relay_push.ps1 -Target relayuser@sysrelay.example.test
param(
  [string]$OutDir = (Join-Path $PSScriptRoot 'wug_out'),
  [string]$SentDir = (Join-Path $PSScriptRoot 'wug_sent'),
  [string]$Key = (Join-Path (Join-Path $env:USERPROFILE '.ssh') 'webit3_relay'),
  [Parameter(Mandatory=$true)][string]$Target
)
if ($Target -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$') { Write-Host 'Target must look like user@host'; exit 1 }
if (-not (Test-Path -LiteralPath $Key)) { Write-Host ('Missing key file ' + $Key + ' - run relay_setup.ps1 first'); exit 1 }
if (-not (Test-Path -LiteralPath $OutDir)) { Write-Host ('Nothing to send: ' + $OutDir + ' does not exist'); exit 0 }
New-Item -ItemType Directory -Force -Path $SentDir | Out-Null
$log = Join-Path $SentDir 'push_log.txt'
$enc = New-Object System.Text.UTF8Encoding($false)
$safe = '^[A-Za-z0-9][A-Za-z0-9._-]*$'
$bad = 0
foreach ($b in Get-ChildItem -LiteralPath $OutDir -Directory) {
  $m = Join-Path $b.FullName 'manifest.json'
  if (-not (Test-Path -LiteralPath $m)) { continue }
  $n = $b.Name
  $files = @(Get-ChildItem -LiteralPath $b.FullName -File | Where-Object { $_.Name -ne 'manifest.json' -and $_.Name -notlike '*.tmp' }) + @(Get-Item -LiteralPath $m)
  $r = 'FAIL'
  $why = ''
  $unsafe = @($files | Where-Object { $_.Name -notmatch $safe })
  if ($n -notmatch $safe) { $why = ' (batch name has characters other than letters, digits, dot, dash, underscore)' }
  elseif ($unsafe.Count -gt 0) { $why = ' (file name has characters other than letters, digits, dot, dash, underscore)' }
  else {
    $stage = '.up_' + $n + '_' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    $lines = @('mkdir "' + $stage + '"')
    foreach ($f in $files) {
      $p = $f.FullName.Replace([char]92, [char]47)
      $lines += ('put "' + $p + '" "' + $stage + '/' + $f.Name + '.tmp"')
      $lines += ('rename "' + $stage + '/' + $f.Name + '.tmp" "' + $stage + '/' + $f.Name + '"')
    }
    $lines += ('rename "' + $stage + '" "' + $n + '"')
    $bf = Join-Path $env:TEMP ('relay_push_' + $n + '.txt')
    [IO.File]::WriteAllLines($bf, [string[]]$lines, $enc)
    & sftp -i $Key -o BatchMode=yes -o StrictHostKeyChecking=accept-new -b $bf $Target 2>$null | Out-Null
    $sent = ($LASTEXITCODE -eq 0)
    Remove-Item -LiteralPath $bf
    if (-not $sent) { $why = ' (upload failed, or a batch of this name is already on the system host)' }
    else {
      try {
        Move-Item -LiteralPath $b.FullName -Destination (Join-Path $SentDir $n) -ErrorAction Stop
        $r = 'OK'
      } catch {
        $why = ' (uploaded, but could not move it to the sent folder: ' + $_.Exception.Message + ')'
      }
    }
  }
  if ($r -ne 'OK') { $bad++ }
  Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + ' ' + $n + ' ' + $r + ' files=' + $files.Count + $why)
  Write-Host ($n + ' ' + $r + $why)
}
if ($bad -gt 0) { exit 1 }
exit 0
`,
        ok: '每個批次顯示「批次名稱 OK」；wug_sent 資料夾裡的 push_log.txt 多一行 OK',
        onFail: 'FAIL：多半是系統主機還沒裝上金鑰那一行，或這台電腦的 IP 跟金鑰那行寫的不一樣；把 push_log.txt 最後幾行帶回。失敗的批次留在 wug_out，下次會再傳；若寫「already on the system host」，表示這批已傳過，確認後把它搬到 wug_sent 即可',
        bring: '只需帶回 push_log.txt 最後幾行',
      },
      {
        label: '腳本 relay_setup.ps1（按「下載腳本」存成檔案）。中繼資料夾要先有：wug_collect.ps1（D4）、relay_push.ps1（上一條）、wug_ips.txt（一行一個 IPv4）、wug_account.txt（自己用記事本填 username= 與 password=）。執行：powershell -File relay_setup.ps1 -WugBase https://<WhatsUp主機名稱>:9644 -Target <上傳帳號>@<系統主機名稱>。每一步印 OK／FAIL；中途會跳出 Windows 密碼視窗（排程要用，直接交給 Windows，腳本不存）。跑完把畫面上那一行金鑰交給系統主機管理員裝上，再加 -TestNow 重跑一次（平日請在 15:00 後）確認整段跑得通。',
        file: 'relay_setup.ps1',
        text: `# WUG-RELAY-SETUP-SCRIPT v2 : one-click setup of a relay Windows PC (WhatsUp daily collect + SFTP upload schedule; -Alerts adds a per-minute alert task)
# Run once on the relay PC (Windows PowerShell 5.1) as the Windows account that will run the daily job.
# It only prepares this PC: folders, an SSH key for upload, run_daily.ps1, and one scheduled task.
# With -Alerts it also writes run_alert.ps1 and a second task that runs wug_alert_collect.ps1 (devkit D7) and the upload every minute, 24 hours a day.
# It checks that wug_account.txt, the private key and their folders are open only to you (plus SYSTEM and Administrators);
# it offers to tighten the two files (asks y/N first) and prints the command for existing folders, which you run yourself.
# A relay folder that this script creates itself is locked down that way from the start.
# It never connects to WhatsUp or the system host, never stores your password, and does not change registry, firewall, services or power settings.
# Put wug_collect.ps1 (devkit D4), relay_push.ps1 and this file in the same folder first (and wug_alert_collect.ps1 for -Alerts). Example:
#   powershell -File relay_setup.ps1 -WugBase https://wug.example.test:9644 -Target relayuser@sysrelay.example.test -Alerts
param(
  [string]$Root = (Join-Path $env:SystemDrive 'webit3_relay'),
  [Parameter(Mandatory=$true)][string]$WugBase,
  [Parameter(Mandatory=$true)][string]$Target,
  [string]$Time = '15:30',
  [string]$IpFile = '',
  [string]$TaskName = 'webit3_wug_daily',
  [string]$RemoteDir = '/opt/webit3/relay_in',
  [switch]$TestNow,
  [switch]$Alerts,
  [string]$AlertTaskName = 'webit3_wug_alert'
)
$ErrorActionPreference = 'Stop'
$script:Fails = 0
$skipTask = $false
$skipAlert = $false
function Ok([string]$m) { Write-Host ('OK    ' + $m) -ForegroundColor Green }
function Fail([string]$m) { Write-Host ('FAIL  ' + $m) -ForegroundColor Red; $script:Fails++ }
function Warn([string]$m) { Write-Host ('WARN  ' + $m) -ForegroundColor Yellow }
function Info([string]$m) { Write-Host ('      ' + $m) }
function Q([string]$s) { return "'" + $s.Replace("'", "''") + "'" }
# Who may read a path: only you, SYSTEM, Administrators (and CREATOR OWNER) are fine; anyone else is reported.
$me = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
function Get-OtherAccess([string]$Path) {
  $okSids = @($me, 'S-1-5-18', 'S-1-5-32-544', 'S-1-3-0')
  $names = @()
  foreach ($r in (Get-Acl -LiteralPath $Path).Access) {
    if ([string]$r.AccessControlType -ne 'Allow') { continue }
    $sid = [string]$r.IdentityReference
    try { $sid = $r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } catch { $sid = [string]$r.IdentityReference }
    if ($okSids -notcontains $sid) { $names += [string]$r.IdentityReference }
  }
  return @($names | Select-Object -Unique)
}
# A secret file (WhatsUp account file, private key): offer to restrict it to you; never changed without your y.
function Protect-SecretFile([string]$Path, [string]$Label) {
  $o = @(Get-OtherAccess $Path)
  if ($o.Count -eq 0) { Ok ($Label + ' is readable only by you (plus SYSTEM and Administrators)'); return }
  Warn ($Label + ' can also be read by: ' + ($o -join ', '))
  $fix = 'icacls "' + $Path + '" /inheritance:r /grant:r "*' + $me + ':F"'
  $ans = Read-Host ('Restrict ' + $Label + ' to your account only now? This runs: ' + $fix + '  (y/N)')
  if ($ans -match '^[yY]') {
    & icacls $Path /inheritance:r /grant:r ('*' + $me + ':F') | Out-Null
    if (($LASTEXITCODE -eq 0) -and (@(Get-OtherAccess $Path).Count -eq 0)) { Ok ($Label + ' is now readable only by you'); return }
  }
  Fail ($Label + ' is readable by other accounts. Fix it yourself with: ' + $fix)
}
# A folder holding secrets or the scripts the job runs: only reported; the fix command is printed for you to run.
function Test-PrivateFolder([string]$Path, [string]$Label) {
  $o = @(Get-OtherAccess $Path)
  if ($o.Count -eq 0) { Ok ($Label + ' ' + $Path + ' is open only to you (plus SYSTEM and Administrators)'); return }
  Fail ($Label + ' ' + $Path + ' is also open to: ' + ($o -join ', ') + '. Other accounts could read the secrets or change the scripts in it. Run this yourself, then run setup again:')
  Info ('icacls "' + $Path + '" /inheritance:r /grant:r "*' + $me + ':(OI)(CI)F" /grant:r "*S-1-5-18:(OI)(CI)F" /grant:r "*S-1-5-32-544:(OI)(CI)F"')
}
# The one place this script writes a file: the job scripts run_daily.ps1 / run_alert.ps1 (UTF-8 with BOM for PowerShell 5.1).
function Write-RunFile([string]$Path, [string[]]$Lines) {
  [IO.File]::WriteAllText($Path, (($Lines -join [Environment]::NewLine) + [Environment]::NewLine), (New-Object System.Text.UTF8Encoding($true)))
  Ok ('Written ' + $Path)
}
function Stop-IfFailed { if ($script:Fails -gt 0) { Write-Host ''; Write-Host ('Stopped: ' + $script:Fails + ' problem(s) above. Fix them and run this script again (it is safe to re-run).') -ForegroundColor Red; exit 1 } }

Write-Host '== Relay PC setup =='
# 0. Check the inputs before touching anything.
if ($WugBase -match '^https://[A-Za-z0-9][A-Za-z0-9.-]{0,251}(:[0-9]{1,5})?/?$') { Ok ('WhatsUp address ' + $WugBase) } else { Fail 'WugBase must be https://<host name>[:port] (plain http is refused: the password would travel unencrypted)' }
if ($Target -match '^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$') { Ok ('Upload target ' + $Target) } else { Fail 'Target must look like user@host (the upload account on the system host)' }
if ($RemoteDir -match '^/[A-Za-z0-9._/-]+$') { Ok ('Remote relay folder ' + $RemoteDir) } else { Fail 'RemoteDir must be an absolute path like /opt/webit3/relay_in' }
$at = $null
if ($Time -match '^([01][0-9]|2[0-3]):[0-5][0-9]$') { $at = [datetime]::ParseExact($Time, 'HH:mm', $null); Ok ('Daily run time ' + $Time) } else { Fail 'Time must be HH:mm, for example 15:30' }
if ($at) { $mins = $at.Hour * 60 + $at.Minute; if (($mins -ge 510) -and ($mins -lt 900)) { Warn 'This time is inside weekday trading hours (08:30-15:00); the collector refuses to run then on weekdays. 15:30 or later is recommended.' } }
foreach ($c in 'ssh-keygen', 'sftp') { if (Get-Command $c -ErrorAction SilentlyContinue) { Ok ($c + ' found') } else { Fail ($c + ' not found: ask IT to enable the Windows optional feature OpenSSH Client') } }
$pol = [string](Get-ExecutionPolicy)
if (@('Restricted', 'AllSigned', 'Undefined') -contains $pol) { Fail ('PowerShell script policy on this PC is ' + $pol + ', so the daily job could not run scripts. Ask IT to allow local scripts (RemoteSigned) for this account; this setup does not change it.') } else { Ok ('PowerShell script policy ' + $pol) }
Stop-IfFailed

# a. Folders and files.
Write-Host ''
Write-Host '-- a. Folders and files'
$outDir = Join-Path $Root 'wug_out'
$sentDir = Join-Path $Root 'wug_sent'
$rootNew = -not (Test-Path -LiteralPath $Root)
New-Item -ItemType Directory -Force -Path $Root | Out-Null
if ($rootNew) {
  # A folder this script just created is locked down from the start (you, SYSTEM, Administrators); an existing folder is only checked.
  & icacls $Root /inheritance:r /grant:r ('*' + $me + ':(OI)(CI)F') /grant:r '*S-1-5-18:(OI)(CI)F' /grant:r '*S-1-5-32-544:(OI)(CI)F' | Out-Null
  if ($LASTEXITCODE -eq 0) { Ok ('Folder ' + $Root + ' created, open only to you (plus SYSTEM and Administrators)') } else { Fail ('Folder ' + $Root + ' created, but setting its permissions failed') }
} else { Ok ('Folder ' + $Root + ' already exists (permissions checked below)') }
$alertOut = Join-Path $Root 'wug_alert_out'
$alertSent = Join-Path $Root 'wug_alert_sent'
$dirs = @($outDir, $sentDir)
if ($Alerts) { $dirs += $alertOut; $dirs += $alertSent }
foreach ($d in $dirs) { New-Item -ItemType Directory -Force -Path $d | Out-Null; Ok ('Folder ' + $d) }
$need = @('wug_collect.ps1', 'relay_push.ps1')
if ($Alerts) { $need += 'wug_alert_collect.ps1' }
foreach ($f in $need) {
  $p = Join-Path $Root $f
  if (-not (Test-Path -LiteralPath $p)) { Fail ($f + ' is missing in ' + $Root + ' - download it from the devkit page (D4 = wug_collect.ps1, D6 = relay_push.ps1, D7 = wug_alert_collect.ps1) and save it there'); continue }
  if (Get-Item -LiteralPath $p -Stream 'Zone.Identifier' -ErrorAction SilentlyContinue) { Unblock-File -LiteralPath $p; Ok ($f + ' was marked as downloaded; unblocked this one file so the daily job may run it') }
  Ok ($f + ' present')
}
$pushFile = Join-Path $Root 'relay_push.ps1'
if ((Test-Path -LiteralPath $pushFile) -and -not (Select-String -LiteralPath $pushFile -SimpleMatch 'RELAY-PUSH-SCRIPT v2' -Quiet)) { Fail 'relay_push.ps1 is an old version - download it again from the devkit page D6' }
$acct = Join-Path $Root 'wug_account.txt'
if ((Test-Path -LiteralPath $acct) -and ((Get-Item -LiteralPath $acct).Length -gt 0)) { Ok 'wug_account.txt present (contents not read here)'; Protect-SecretFile $acct 'wug_account.txt' } else { Fail ('wug_account.txt is missing or empty. Open Notepad, write two lines username=<WhatsUp read-only account> and password=<its password>, save as ' + $acct + ', and make it readable only by this Windows account.') }
if (-not $IpFile) { $IpFile = Join-Path $Root 'wug_ips.txt' }
if (Test-Path -LiteralPath $IpFile) { $ipCount = @(Get-Content -LiteralPath $IpFile | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') }).Count; Ok ('IP list ' + $IpFile + ' (' + $ipCount + ' lines)') } else { Fail ('IP list ' + $IpFile + ' is missing: one IPv4 address per line, lines starting with # are comments') }
Test-PrivateFolder $Root 'Relay folder'
Stop-IfFailed

# b. SSH key for upload (no passphrase, so the job can run unattended; the system host limits what it can do).
Write-Host ''
Write-Host '-- b. Upload key'
$sshDir = Join-Path $env:USERPROFILE '.ssh'
$key = Join-Path $sshDir 'webit3_relay'
New-Item -ItemType Directory -Force -Path $sshDir | Out-Null
if (Test-Path -LiteralPath $key) { Ok ('Key already exists, kept: ' + $key) } else {
  $empty = '""'
  if ($PSVersionTable.PSVersion.Major -ge 7) { $empty = '' }
  & ssh-keygen -q -t ed25519 -N $empty -C 'webit3-relay-push' -f $key
  if (($LASTEXITCODE -eq 0) -and (Test-Path -LiteralPath $key)) { Ok ('Key created: ' + $key) } else { Fail 'ssh-keygen failed'; Stop-IfFailed }
}
Test-PrivateFolder $sshDir 'Key folder'
Protect-SecretFile $key 'Private key webit3_relay'
Stop-IfFailed
$pub = (Get-Content -LiteralPath ($key + '.pub') -TotalCount 1).Trim()
$myIp = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | ForEach-Object { $_.IPv4Address.IPAddress })
if ($myIp.Count -eq 0) { $myIp = @('<this PC IPv4>'); Warn 'Could not find this PC IPv4 address; replace <this PC IPv4> below by hand.' }
if ($myIp.Count -gt 1) { Warn ('This PC has more than one IPv4 address (' + ($myIp -join ', ') + '); the first one is used below. Use the one the system host sees.') }
$line = 'from="' + $myIp[0] + '",command="internal-sftp -d ' + $RemoteDir + '",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding ' + $pub
Info 'Give this ONE line to the system host administrator, to add to authorized_keys of the upload account (it is a public key, not a secret):'
Write-Host ''
Write-Host $line -ForegroundColor Cyan
Write-Host ''

# c. run_daily.ps1 (collect yesterday, then upload; everything goes to run_log.txt).
Write-Host '-- c. Daily job script'
$runPath = Join-Path $Root 'run_daily.ps1'
$run = @(
  '# RELAY-DAILY-RUN v2 : written by relay_setup.ps1 - run relay_setup.ps1 again to change it. Collect yesterday from WhatsUp, then upload.',
  '$root = $PSScriptRoot',
  '$log = Join-Path $root ''run_log.txt''',
  'function Log([string]$m) { Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + '' '' + $m) }',
  'Set-Location -LiteralPath $root',
  '$bad = 0',
  'Log ''start''',
  'try {',
  '  $global:LASTEXITCODE = 0',
  ('  & (Join-Path $root ''wug_collect.ps1'') -Base ' + (Q $WugBase) + ' -IpFile ' + (Q $IpFile) + ' -OutDir (Join-Path $root ''wug_out'') *>&1 | ForEach-Object { Log (''collect: '' + $_) }'),
  '  $rc = $global:LASTEXITCODE',
  '  Log (''collect exit '' + $rc)',
  '  if ($rc -ne 0) { $bad++ }',
  '} catch { Log (''collect error: '' + $_.Exception.Message); $bad++ }',
  'try {',
  '  $global:LASTEXITCODE = 0',
  ('  & (Join-Path $root ''relay_push.ps1'') -OutDir (Join-Path $root ''wug_out'') -SentDir (Join-Path $root ''wug_sent'') -Key ' + (Q $key) + ' -Target ' + (Q $Target) + ' *>&1 | ForEach-Object { Log (''push: '' + $_) }'),
  '  $rc = $global:LASTEXITCODE',
  '  Log (''push exit '' + $rc)',
  '  if ($rc -ne 0) { $bad++ }',
  '} catch { Log (''push error: '' + $_.Exception.Message); $bad++ }',
  'Log (''end, problems='' + $bad)',
  'if ($bad -gt 0) { exit 1 }',
  'exit 0'
)
Write-RunFile $runPath $run
if ($Alerts) {
  $alertRunPath = Join-Path $Root 'run_alert.ps1'
  $runA = @(
    '# RELAY-ALERT-RUN v1 : written by relay_setup.ps1 -Alerts - run relay_setup.ps1 again to change it. Read WhatsUp alert state, then upload. Runs every minute, also in trading hours (read-only, WhatsUp only).',
    '$root = $PSScriptRoot',
    '$log = Join-Path $root ''alert_log.txt''',
    'function Log([string]$m) { Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + '' '' + $m) }',
    'Set-Location -LiteralPath $root',
    '$bad = 0',
    'try {',
    '  $global:LASTEXITCODE = 0',
    ('  & (Join-Path $root ''wug_alert_collect.ps1'') -Base ' + (Q $WugBase) + ' -OutDir (Join-Path $root ''wug_alert_out'') *>&1 | ForEach-Object { Log (''alert: '' + $_) }'),
    '  $rc = $global:LASTEXITCODE',
    '  if ($rc -ne 0) { $bad++; Log (''alert exit '' + $rc) }',
    '} catch { Log (''alert error: '' + $_.Exception.Message); $bad++ }',
    'try {',
    '  $global:LASTEXITCODE = 0',
    ('  & (Join-Path $root ''relay_push.ps1'') -OutDir (Join-Path $root ''wug_alert_out'') -SentDir (Join-Path $root ''wug_alert_sent'') -Key ' + (Q $key) + ' -Target ' + (Q $Target) + ' *>&1 | ForEach-Object { Log (''push: '' + $_) }'),
    '  $rc = $global:LASTEXITCODE',
    '  if ($rc -ne 0) { $bad++; Log (''push exit '' + $rc) }',
    '} catch { Log (''push error: '' + $_.Exception.Message); $bad++ }',
    'if ($bad -gt 0) { exit 1 }',
    'exit 0'
  )
  Write-RunFile $alertRunPath $runA
}

# d. Scheduled task(s) that run whether or not the user is logged on.
Write-Host ''
Write-Host '-- d. Scheduled task'
$who = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$old = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($old) {
  $ans = Read-Host ('A task named ' + $TaskName + ' already exists. Replace it? (y/N)')
  if ($ans -notmatch '^[yY]') { Warn 'Kept the existing task unchanged.'; $skipTask = $true }
}
if ($Alerts) {
  $oldA = Get-ScheduledTask -TaskName $AlertTaskName -ErrorAction SilentlyContinue
  if ($oldA) {
    $ans = Read-Host ('A task named ' + $AlertTaskName + ' already exists. Replace it? (y/N)')
    if ($ans -notmatch '^[yY]') { Warn 'Kept the existing alert task unchanged.'; $skipAlert = $true }
  }
} else { $skipAlert = $true }
# The one place a task is registered (asked first above when a task of that name exists).
function Register-Job([string]$Name, [string]$RunFile, $Trigger, $Limit, [string]$What) {
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -File "' + $RunFile + '"') -WorkingDirectory $Root
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit $Limit -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  try {
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger -Settings $settings -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
    $lt = (Get-ScheduledTask -TaskName $Name).Principal.LogonType
    Ok ('Task ' + $Name + ' registered: ' + $What + ', runs whether or not you are logged on (logon type ' + $lt + ')')
  } catch {
    Fail ('Task ' + $Name + ' not registered: ' + $_.Exception.Message + ' - check the password; if it says access denied, run PowerShell as administrator, or ask IT to give this account the right to log on as a batch job.')
  }
}
if ((-not $skipTask) -or (-not $skipAlert)) {
  Info ('Windows needs the password of ' + $who + ' so the task can run while nobody is logged on. It goes straight to Windows Task Scheduler and is not saved or logged by this script.')
  $cred = Get-Credential -UserName $who -Message 'Password of this Windows account (for the scheduled task)'
  if (-not $cred) { Fail 'No password given; task not registered.' } else {
    if (-not $skipTask) { Register-Job $TaskName $runPath (New-ScheduledTaskTrigger -Daily -At $at) (New-TimeSpan -Hours 3) ('daily at ' + $Time) }
    if (-not $skipAlert) { Register-Job $AlertTaskName $alertRunPath (New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 1)) (New-TimeSpan -Minutes 10) 'every minute, 24 hours a day' }
    $cred = $null
  }
}

# e. Sleep check (read only).
Write-Host ''
Write-Host '-- e. Sleep check'
$q = @(& powercfg -query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE | Select-String -Pattern '0x[0-9a-fA-F]+' | ForEach-Object { $_.Matches[0].Value })
if ($q.Count -ge 2) {
  $acSec = [Convert]::ToInt32($q[$q.Count - 2], 16)
  if ($acSec -eq 0) { Ok 'This PC never sleeps on AC power' } else { Warn ('This PC goes to sleep after ' + [int]($acSec / 60) + ' minutes on AC power, so the daily job may be missed. If that is allowed here, run this yourself: powercfg -change -standby-timeout-ac 0') }
} else { Warn 'Could not read the sleep setting; make sure this PC does not sleep at the daily run time.' }

# f. Optional test run now.
if ($TestNow -and -not $skipTask -and $script:Fails -eq 0) {
  Write-Host ''
  Write-Host '-- f. Test run now'
  Start-ScheduledTask -TaskName $TaskName
  Info 'Started; waiting for it to finish (up to 30 minutes)...'
  Start-Sleep -Seconds 5
  $waited = 0
  while (((Get-ScheduledTask -TaskName $TaskName).State -eq 'Running') -and ($waited -lt 1800)) { Start-Sleep -Seconds 10; $waited += 10 }
  $res = (Get-ScheduledTaskInfo -TaskName $TaskName).LastTaskResult
  Info ('Last result code ' + $res + ' (0 = finished)')
  $rl = Join-Path $Root 'run_log.txt'
  if (Test-Path -LiteralPath $rl) { Info 'Last lines of run_log.txt:'; Get-Content -LiteralPath $rl -Tail 15 | ForEach-Object { Info $_ } }
  $pl = Join-Path $sentDir 'push_log.txt'
  if (Test-Path -LiteralPath $pl) { Info 'Last lines of push_log.txt:'; Get-Content -LiteralPath $pl -Tail 5 | ForEach-Object { Info $_ } } else { Warn 'No push_log.txt yet: nothing was uploaded (normal on weekdays 08:30-15:00, or if the key line is not installed yet).' }
}

Write-Host ''
if ($script:Fails -gt 0) { Write-Host ('Finished with ' + $script:Fails + ' problem(s); fix them and run again.') -ForegroundColor Red; exit 1 }
Write-Host 'Done. Next: give the key line above to the system host administrator; after it is installed, run this again with -TestNow (after 15:00) to check one full run.' -ForegroundColor Green
if ($Alerts) { Write-Host 'Alert task: after the key is installed, alert_log.txt in the relay folder gets a line only when something goes wrong; wug_alert_sent fills with small batches.' }
`,
        ok: '每一步都是 OK，最後一行 Done；工作排程器裡有 webit3_wug_daily，「不論使用者登入與否均執行」；加 -TestNow 時 push_log.txt 有 OK',
        onFail: '照 FAIL 那行的說明補好（缺檔、帳密檔、檔案或資料夾權限太開放、執行原則、密碼錯）再重跑，重跑是安全的：已有的金鑰與資料夾會保留，同名排程會先問要不要取代；出現 WARN 睡眠設定時，由你自己決定要不要執行畫面上那行 powercfg',
        bring: '只需帶回畫面文字（不含密碼）與金鑰那一行（公開金鑰，不是秘密）',
      },
    ],
    failHow: '任何錯誤：把畫面上的整段文字（不含密碼）貼到「整理結果」頁籤去識別後帶回，並在備註寫「失敗」。',
    expect: '中繼資料夾裡多出 wug_out、wug_sent、run_daily.ps1；工作排程器有一個每天固定時間執行的排程；系統主機管理員裝好金鑰後，每天收盤後自動收集並上傳，系統「中繼匯入」頁看得到新批次。',
    returnHow: '不用把檔案傳回給開發者；金鑰那一行交給系統主機管理員，裝好後到「系統設定 → 外部整合 → 中繼匯入」看鮮度燈。',
    notes: [
      '排程預設每天 15:30（可用 -Time 改），選「不論使用者是否登入都執行」，電腦錯過時間開機後會補跑一次，單次最多 3 小時、不會重疊執行。',
      '密碼只在登記排程時交給 Windows 工作排程器；Windows 密碼改了，要重跑一次本腳本更新排程。',
      '金鑰沒有密碼，是為了讓排程能自動上傳；系統主機那邊用金鑰那一行限制它只能從這台電腦連、只能用 SFTP 放檔到中繼資料夾。',
      '腳本不連 WhatsUp、不改電源設定；電腦會睡著時只提醒，要不要改由你決定。',
      '加 -Alerts（v2）會多登記一個每分鐘執行的告警排程 webit3_wug_alert（先放好 D7 的 wug_alert_collect.ps1）：只讀 WhatsUp 的告警狀態再上傳，24 小時都跑（含開盤時間，使用者 2026-11-03 決定）；單次最多 10 分鐘、不會重疊。',
    ],
  },
  {
    id: 'D7',
    tag: 'WhatsUp',
    title: 'WhatsUp 告警狀態收集腳本（中繼檔案，每分鐘，PowerShell，只讀）',
    purpose: '讓系統在 WhatsUp 狀態一變就知道是哪台、哪個監控項目、壞了多久，並推到 Teams。每分鐘只問 WhatsUp 一次「群組狀態」（不到 1 秒）；沒變就什麼都不寫（每 5 分鐘寫一份很小的心跳批次，讓系統分得出「沒事」和「中繼 PC 停了」）；有變才翻設備清單找出不是 Up 的設備，再問那幾台的監控項目狀態，輸出一個批次資料夾。',
    where: '能連到 WhatsUp Gold 的公司中繼 Windows PC（Windows PowerShell 5.1），由 D6 relay_setup.ps1 -Alerts 登記的每分鐘排程執行；不是 221、也不是系統主機',
    perm: '唯讀：WhatsUp 只做 GET（外加一次登入取得權杖），帳密沿用 D4 的 wug_account.txt；本機只寫輸出資料夾與一個狀態檔（不含密碼或權杖）。不連任何主機，所以開盤時間也照跑（使用者 2026-11-03 決定監控 24 小時）',
    ver: 5,
    commands: [
      {
        label: '腳本 wug_alert_collect.ps1（按「下載腳本」存成檔案，放到中繼資料夾，和 wug_collect.ps1、wug_account.txt 同一個資料夾）。平常由排程每分鐘自動執行，手動試跑：powershell -File wug_alert_collect.ps1 -Base https://<WhatsUp主機名稱>:9644 -OutDir wug_alert_out。設備清單翻頁很慢（第 2 頁起每頁約 25 秒），一次最多 420 秒，超過就把這次標成「不完整」（系統只開新告警、不會因此把舊告警當成已恢復）。',
        file: 'wug_alert_collect.ps1',
        text: `# WUG-ALERT-COLLECT-SCRIPT v6 : WhatsUp Gold read-only alert snapshot for the relay folder (GET only, plus one token login)
# Run every minute on the relay Windows host (PowerShell 5.1) by the scheduled task that relay_setup.ps1 -Alerts registers.
# Each run asks WhatsUp one fast question (group status). Nothing changed and the last full scan was complete: it writes nothing,
# except every HeartbeatMinutes it re-checks the devices that had a problem last time (device status + monitor status).
# If they look the same it writes one small heartbeat batch (so the system can tell "no change" from "the relay PC stopped");
# if they changed (one host recovered while another failed, so the group totals stayed the same) it does a full scan.
# A full scan is also forced every ForceScanMinutes even when nothing seems to have changed.
# Full scan: pages the device list, keeps devices that are not Up (Down, Maintenance, Unknown, or with monitors down),
# asks each down device for its monitor status (which monitor, "Down at least 20 min"), and writes one batch folder.
# complete = true only when every page had a device list, the last page had a paging block without a next page, the group
# status has totalDevices, and the number of devices seen is close to that total. Anything else (time box, max pages, missing fields, empty list) is complete = false:
# the system then opens new alerts but never closes old ones.
# v4 (11-10): a full scan took about 7 minutes (72 calls), mostly re-reading the monitor list of devices that were already
# down last time. Now a device whose worst state and number of down monitors are the same as in the last scan reuses the
# monitor list saved from that scan (no call); only new or changed problem devices are read. The forced scan every
# ForceScanMinutes reads every device again, so a reused list is never older than that. scan.page_s / monitor_s / monitor_reused
# show where the time went.
# v6 (11-14): -StateFilter asks WhatsUp only for problem devices (state=Down, Maintenance, Unknown, UpWithDownMonitors, a
# few pages) instead of paging all ~2000 devices (about 6 minutes, so the snapshot was 6-13 minutes old). The forced scan every
# ForceScanMinutes still pages everything, as a reconciliation. -ProbeStateFilter runs both ways once, prints the difference and
# writes nothing: run it before turning -StateFilter on.
# Runs 24 hours a day including trading hours (user decision 2026-11-03): it only reads WhatsUp; it never connects to any host.
# Example:  powershell -File wug_alert_collect.ps1 -Base https://wug.example.test:9644 -OutDir wug_alert_out
param(
  [Parameter(Mandatory=$true)][string]$Base,
  [string]$OutDir = 'wug_alert_out',
  [string]$StateFile = '',
  [int]$HeartbeatMinutes = 5,
  [int]$ForceScanMinutes = 30,
  [int]$TimeBoxSeconds = 420,
  [int]$PageLimit = 200,
  [int]$MaxPages = 30,
  [int]$MaxMonitorCalls = 60,
  [int]$MaxRecheckDevices = 150,
  [switch]$StateFilter,
  [switch]$ProbeStateFilter
)
$FilterStates = @('Down', 'Maintenance', 'Unknown', 'UpWithDownMonitors')
$ErrorActionPreference = 'Stop'
$ScriptVersion = 'wug_alert_collect 6'
# v5 (11-11): every monitor also carries the name of the action policy it uses (policy), when WhatsUp gives it.
# The server then pushes only monitors whose policy matches the chosen names (for example the host team LINE policy).
# No policy field in the reply = policy left out = the server treats it as not known and still pushes.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if ($PageLimit -gt 200) { $PageLimit = 200 }
if (-not $StateFile) { $StateFile = Join-Path $PSScriptRoot 'wug_alert_state.json' }

# Credentials: the same wug_account.txt as wug_collect.ps1 (username=... / password=...), readable only by this account. Nothing secret is written out.
$cfg = Join-Path $PSScriptRoot 'wug_account.txt'
if (-not (Test-Path -LiteralPath $cfg)) { Write-Host ('Missing ' + $cfg + ' - create it with Notepad: two lines username=... and password=..., readable only by this account; then run again.'); exit 1 }
$kv = @{}
foreach ($l in Get-Content -LiteralPath $cfg) { $i = $l.IndexOf('='); if ($i -gt 0) { $kv[$l.Substring(0, $i).Trim()] = $l.Substring($i + 1) } }
$user = $kv['username']; $pass = $kv['password']; $kv = $null
if (-not $user -or -not $pass) { Write-Host ('username or password is empty in ' + $cfg); exit 1 }
if ($Base -notmatch '^https://[A-Za-z0-9][A-Za-z0-9.-]{0,251}(:[0-9]{1,5})?/?$') { throw 'Base must be an https:// URL with a host name (plain http is refused because the password and token would travel unencrypted).' }
$root = $Base.TrimEnd('/')

function Write-Atomic([string]$Path, [string]$Text) {
  $tmp = $Path + '.tmp'
  [IO.File]::WriteAllText($tmp, $Text, (New-Object System.Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Minutes-Since([string]$Iso, $Now) {
  if (-not $Iso) { return 1000000 }
  try { return ($Now - [datetime]::Parse($Iso).ToUniversalTime()).TotalMinutes } catch { return 1000000 }
}

# State from the last run (no secrets in it): group status signature, whether the last scan was complete, when a batch was
# last written, when the last full scan ran, and the fingerprint + ids of the devices that were down in that scan.
$state = [ordered]@{ sig = ''; complete = $false; last_write_utc = ''; last_scan_utc = ''; fp = ''; ids = @(); mon = @{} }
if (Test-Path -LiteralPath $StateFile) {
  try {
    # v5 (11-14): the state file is UTF-8 with Chinese policy names; PS 5.1 reads it as ANSI without -Encoding and the JSON breaks,
    # so every run fell back to a 6-7 minute full scan and the snapshot kept going stale on the 10-minute check.
    $s = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $state.sig = [string]$s.sig; $state.complete = [bool]$s.complete; $state.last_write_utc = [string]$s.last_write_utc
    $state.last_scan_utc = [string]$s.last_scan_utc; $state.fp = [string]$s.fp; $state.ids = @($s.ids | Where-Object { $_ } | ForEach-Object { [string]$_ })
    if ($s.PSObject.Properties['mon'] -and $s.mon) { foreach ($p in $s.mon.PSObject.Properties) { $state.mon[[string]$p.Name] = $p.Value } }
  } catch { Write-Host 'State file unreadable; doing a full scan this time.' }
}

$body = @{ grant_type = 'password'; username = $user; password = $pass }
try {
  $tok = Invoke-RestMethod -Method Post -Uri ($root + '/api/v1/token') -Body $body -TimeoutSec 30
} catch {
  Write-Host ('Login failed: ' + $_.Exception.Message)
  exit 1
}
$body = $null
$pass = $null
$headers = @{ Authorization = ('Bearer ' + $tok.access_token) }
$tok = $null

$script:Calls = 0
function Get-Wug([string]$Path, [int]$Timeout = 30) {
  $script:Calls++
  return Invoke-RestMethod -Uri ($root + $Path) -Headers $headers -TimeoutSec $Timeout
}
# Monitor status of one device; throws if it cannot be read.
function Read-Monitors([string]$Id) {
  $m = Get-Wug ('/api/v1/devices/' + $Id + '/monitors/-?view=status&limit=50')
  $list = @()
  if ($m.data -is [array]) { $list = @($m.data) }
  else {
    $found = $false
    foreach ($p in 'monitors', 'activeMonitors', 'items') { if ($m.data.PSObject.Properties[$p]) { $list = @($m.data.$p); $found = $true; break } }
    if (-not $found) { throw 'monitor list missing in the response' }
  }
  $ms = New-Object System.Collections.ArrayList
  foreach ($r in $list) {
    if (-not $r) { continue }
    $nm = [string]$r.monitorTypeName
    if ((-not $nm) -and $r.PSObject.Properties['description']) { $nm = [string]$r.description }
    $h = [ordered]@{ name = $nm; type = [string]$r.type; status = [string]$r.status; enabled = [string]$r.enabled }
    if ($r.PSObject.Properties['active'] -and $r.active -and $r.active.PSObject.Properties['actionPolicyName']) { $h.policy = [string]$r.active.actionPolicyName }
    elseif ($r.PSObject.Properties['actionPolicyName']) { $h.policy = [string]$r.actionPolicyName }
    [void]$ms.Add($h)
  }
  return ,$ms.ToArray()
}
# Add one problem device (overview view) to $devices unless its id is already in $Seen.
function Add-Problem($d, [string]$ws, [int]$dn, $Seen) {
  $id = [string]$d.id
  if ($Seen.ContainsKey($id)) { return }
  $Seen[$id] = $true
  $o = [ordered]@{ id = $id; name = [string]$d.name; host_name = [string]$d.hostName; ip = [string]$d.networkAddress
                   worst_state = $ws; best_state = [string]$d.bestState; monitors_down = $dn; monitors_ok = $false; monitors = @() }
  if ($d.PSObject.Properties['totalActiveMonitors']) { $o.monitors_total = [int]$d.totalActiveMonitors }
  [void]$devices.Add($o)
}
# One paged device-list query (Extra = '' for every device, or '&state=X'). Adds the problem devices it sees to $devices,
# skipping ids already in $Seen. Returns $true only when the last page was reached; otherwise sets $scan.stopped_reason.
function Read-DevicePages([string]$Extra, $Seen, $Sw) {
  $pg = ''
  do {
    $q = '/api/v1/device-groups/-1/devices?view=overview&limit=' + $PageLimit + $Extra
    if ($pg) { $q += '&pageId=' + $pg }
    $x = Get-Wug $q 90
    if ((-not $x.data) -or (-not $x.data.PSObject.Properties['devices'])) { $scan.stopped_reason = 'response has no data.devices' + $Extra; return $false }
    foreach ($d in @($x.data.devices)) {
      if (-not $d) { continue }
      $scan.devices_seen++
      $ws = [string]$d.worstState
      $dn = 0
      if ($d.PSObject.Properties['totalActiveMonitorsDown']) { $dn = [int]$d.totalActiveMonitorsDown }
      if ((($ws -ne 'Up') -and ($ws -ne '')) -or ($dn -gt 0)) { Add-Problem $d $ws $dn $Seen }
    }
    $scan.pages_read++
    if (-not $x.PSObject.Properties['paging']) { $scan.stopped_reason = 'response has no paging block' + $Extra; return $false }
    $pg = [string]$x.paging.nextPageId
    if (-not $pg) { return $true }
    if ($Sw.Elapsed.TotalSeconds -gt $TimeBoxSeconds) { $scan.stopped_reason = 'time box ' + $TimeBoxSeconds + ' s reached'; return $false }
  } while ($scan.pages_read -lt $MaxPages)
  $scan.stopped_reason = 'max pages ' + $MaxPages + ' reached'
  return $false
}
# Fingerprint piece of one down device: id, worst state, and the sorted names of its monitors that are Down.
function Fp-Piece([string]$Id, [string]$Worst, $Monitors) {
  $downs = @($Monitors | Where-Object { ([string]$_.status).StartsWith('Down') -and ([string]$_.enabled -ne 'False') } | ForEach-Object { [string]$_.name } | Sort-Object)
  return ($Id + ':' + $Worst + ':' + ($downs -join ','))
}

$startedAt = Get-Date
$nowUtc = $startedAt.ToUniversalTime()
try {
  $g = Get-Wug '/api/v1/device-groups/-1/status'
} catch {
  Write-Host ('Group status failed: ' + $_.Exception.Message)
  exit 1
}
$sig = [string]($g.data | ConvertTo-Json -Depth 6 -Compress)

if ($ProbeStateFilter) {
  # Read-only comparison, writes nothing: problem devices by state filter vs by paging every device.
  $res = @{}
  foreach ($mode in 'filter', 'full') {
    $scan = [ordered]@{ pages_read = 0; devices_seen = 0; stopped_reason = '' }
    $devices = New-Object System.Collections.ArrayList
    $seen = @{}
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $ok = $true
    try {
      if ($mode -eq 'filter') { foreach ($stq in $FilterStates) { if (-not (Read-DevicePages ('&state=' + $stq) $seen $sw)) { $ok = $false; break } } }
      else { $ok = Read-DevicePages '' $seen $sw }
    } catch { $ok = $false; $scan.stopped_reason = 'page read failed: ' + $_.Exception.Message }
    $m = @{}
    foreach ($o in $devices) { $m[$o.id] = $o }
    $res[$mode] = $m
    $tail = ''
    if ($scan.stopped_reason) { $tail = ', ' + $scan.stopped_reason }
    Write-Host ($mode.ToUpper().PadRight(7) + ': complete ' + $ok + ', problem devices ' + $m.Count + ', pages ' + $scan.pages_read + ', devices seen ' + $scan.devices_seen + ', seconds ' + [int]$sw.Elapsed.TotalSeconds + $tail)
  }
  foreach ($k in $res['full'].Keys) { if (-not $res['filter'].ContainsKey($k)) { $o = $res['full'][$k]; Write-Host ('ONLY IN FULL  : ' + $o.id + ' ' + $o.name + ' ' + $o.worst_state + ' monitors down ' + $o.monitors_down) } }
  foreach ($k in $res['filter'].Keys) { if (-not $res['full'].ContainsKey($k)) { $o = $res['filter'][$k]; Write-Host ('ONLY IN FILTER: ' + $o.id + ' ' + $o.name + ' ' + $o.worst_state + ' monitors down ' + $o.monitors_down) } }
  $sumTxt = ''
  if ($g.data.PSObject.Properties['stateSummaries']) { $sumTxt = (@($g.data.stateSummaries) | ForEach-Object { [string]$_.state + ' ' + [string]$_.deviceCount }) -join ', ' }
  $byState = (@($res['filter'].Values) | Group-Object { [string]$_.worst_state } | ForEach-Object { $_.Name + ' ' + $_.Count }) -join ', '
  Write-Host ('GROUP STATUS  : ' + $sumTxt)
  Write-Host ('FILTER BY STATE: ' + $byState)
  Write-Host 'PROBE DONE (nothing written)'
  exit 0
}

$kind = 'scan'
$why = 'group status changed or last scan was not complete'
if (($sig -eq $state.sig) -and $state.complete) {
  if ((Minutes-Since $state.last_scan_utc $nowUtc) -ge $ForceScanMinutes) { $why = 'forced full scan every ' + $ForceScanMinutes + ' min' }
  elseif ((Minutes-Since $state.last_write_utc $nowUtc) -lt $HeartbeatMinutes) { $headers = $null; Write-Host 'No change.'; exit 0 }
  else {
    # Re-check last time's problem devices (Down, Maintenance and Unknown): group totals can stay the same while one host
    # recovers and another fails. When the last scan could not take a usable fingerprint (too many devices, a monitor list
    # failed) or this re-check itself fails, do NOT full-scan every 5 minutes: send a heartbeat and wait for the forced scan.
    $fp = 'skip'
    if (($state.fp -ne 'too many') -and ($state.fp -ne 'monitor read failed') -and (@($state.ids).Count -le $MaxRecheckDevices)) {
      $pieces = @()
      $calls = 0
      try {
        foreach ($id in $state.ids) {
          $st = Get-Wug ('/api/v1/devices/' + $id + '/status')
          $w = [string]$st.data.worstState
          if (($w -eq 'Up') -or ($w -eq 'Maintenance') -or ($w -eq 'Unknown') -or ($w -eq '')) { $pieces += ($id + ':' + $w + ':'); continue }
          $calls++
          if ($calls -gt $MaxMonitorCalls) { throw 'too many down devices to re-check' }
          $pieces += (Fp-Piece $id $w (Read-Monitors $id))
        }
        $fp = ($pieces | Sort-Object) -join ';'
        if (-not $fp) { $fp = 'none' }
      } catch { $fp = 'skip' }
    }
    if (($fp -eq 'skip') -or ($state.fp -and ($fp -eq $state.fp))) { $kind = 'heartbeat' } else { $why = 'problem devices changed while the group status did not' }
  }
}

$devices = New-Object System.Collections.ArrayList
$scan = [ordered]@{ complete = $false; reason = $why; pages_read = 0; devices_seen = 0; group_total = $null; elapsed_s = 0; stopped_reason = ''; monitor_calls = 0; monitor_failed = 0; monitor_reused = 0; page_s = 0; monitor_s = 0 }
$forced = $why.StartsWith('forced')
$newMon = @{}
$newFp = ''
$newIds = @()
if ($kind -eq 'scan') {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $lastPage = $false
  $seen = @{}
  # Filter only when the last scan was complete: its problem ids are then the full list of devices that can have an open alert,
  # and every one of them that the filtered queries do not return is asked about directly below (review 11-14 P1).
  $useFilter = $StateFilter -and (-not $forced) -and $state.complete
  if ($useFilter) { $scan.mode = 'state filter' } else { $scan.mode = 'all devices' }
  try {
    if ($useFilter) {
      $lastPage = $true
      foreach ($stq in $FilterStates) { if (-not (Read-DevicePages ('&state=' + $stq) $seen $sw)) { $lastPage = $false; break } }
      # 1) Devices that had a problem last time but were not returned now: ask each one directly. Only a clear Up with no monitor
      #    down counts as recovered; still a problem = added back; no clear answer = not complete (the system then closes nothing).
      $scan.recheck_calls = 0
      $scan.recheck_still = 0
      if ($lastPage) {
        foreach ($id in @($state.ids)) {
          if ((-not $id) -or $seen.ContainsKey([string]$id)) { continue }
          $scan.recheck_calls++
          if ($scan.recheck_calls -gt $MaxRecheckDevices) { $lastPage = $false; $scan.stopped_reason = 'too many devices to re-check'; break }
          $r = Get-Wug ('/api/v1/devices/' + $id + '?view=overview')
          $d = $r.data
          $ws = [string]$d.worstState
          if ((-not $d) -or (-not $ws)) { $lastPage = $false; $scan.stopped_reason = 're-check of device ' + $id + ' gave no state'; break }
          # This answer decides whether an alert closes: a missing down-monitor count is not taken as zero (review 11-14 P3).
          if (-not $d.PSObject.Properties['totalActiveMonitorsDown']) { $lastPage = $false; $scan.stopped_reason = 're-check of device ' + $id + ' has no monitor down count'; break }
          $dn = [int]$d.totalActiveMonitorsDown
          if (($ws -ne 'Up') -or ($dn -gt 0)) { Add-Problem $d $ws $dn $seen; $scan.recheck_still++ }
          if ($sw.Elapsed.TotalSeconds -gt $TimeBoxSeconds) { $lastPage = $false; $scan.stopped_reason = 'time box ' + $TimeBoxSeconds + ' s reached during re-check'; break }
        }
      }
      # 2) Per state, the devices we hold must cover the group status count (Down, Maintenance, Unknown each on its own, so a
      #    shortage in one state cannot be hidden by a surplus in another). Short in any state = not complete.
      if ($lastPage) {
        if (-not $g.data.PSObject.Properties['stateSummaries']) { $lastPage = $false; $scan.stopped_reason = 'group status has no stateSummaries' }
        else {
          $want = @{}
          foreach ($s in @($g.data.stateSummaries)) { if ($s) { $k = [string]$s.state; if (-not $want.ContainsKey($k)) { $want[$k] = 0 }; $want[$k] += [int]$s.deviceCount } }
          foreach ($stq in 'Down', 'Maintenance', 'Unknown') {
            $got = @($devices | Where-Object { $_.worst_state -eq $stq }).Count
            $need = 0
            if ($want.ContainsKey($stq)) { $need = $want[$stq] }
            if ($got -lt $need) { $lastPage = $false; $scan.stopped_reason = 'state ' + $stq + ': group status says ' + $need + ', got ' + $got; break }
          }
        }
      }
    } else {
      $lastPage = Read-DevicePages '' $seen $sw
    }
  } catch {
    $lastPage = $false
    $scan.stopped_reason = 'page read failed: ' + $_.Exception.Message
  }
  $scan.page_s = [int]$sw.Elapsed.TotalSeconds
  # complete only if the list was read to the end AND the count is close to the group total (98 %); an empty list is never complete.
  if ($lastPage -and $useFilter) {
    # Filtered: every state query reached its last page, every problem device from the last scan that was not returned was
    # re-checked directly, and Down / Maintenance / Unknown each cover the group status count. The total-count check below
    # does not apply (only problem devices come back).
    $scan.complete = $true
  } elseif ($lastPage) {
    if ($g.data.PSObject.Properties['totalDevices']) { $scan.group_total = [int]$g.data.totalDevices }
    if ($scan.devices_seen -le 0) { $scan.stopped_reason = 'device list was empty' }
    elseif ($scan.group_total -eq $null) { $scan.stopped_reason = 'group status has no totalDevices, cannot tell if the list is whole' }
    elseif ($scan.devices_seen -lt [int]([double]$scan.group_total * 0.98)) { $scan.stopped_reason = 'saw ' + $scan.devices_seen + ' devices but the group total is ' + $scan.group_total }
    else { $scan.complete = $true }
  }
  # Monitor detail only for devices that are down (not Maintenance, not Unknown), at most MaxMonitorCalls per run.
  # Every problem device (Maintenance and Unknown too) goes into the fingerprint, so a swap with Up is seen within 5 minutes.
  $pieces = @()
  foreach ($o in $devices) {
    $newIds += $o.id
    if (($o.worst_state -eq 'Maintenance') -or ($o.worst_state -eq 'Unknown')) { $o.monitors_ok = $true; $pieces += ($o.id + ':' + $o.worst_state + ':'); continue }
    $c = $null
    if ((-not $forced) -and $state.mon.ContainsKey($o.id)) { $c = $state.mon[$o.id] }
    if ($c -and ([string]$c.w -eq $o.worst_state) -and ([int]$c.dn -eq [int]$o.monitors_down)) {
      $o.monitors = @($c.m); $o.monitors_ok = $true; $scan.monitor_reused++
      $newMon[$o.id] = $c
      $pieces += (Fp-Piece $o.id $o.worst_state $o.monitors)
      continue
    }
    if ($scan.monitor_calls -ge $MaxMonitorCalls) { $newFp = 'too many'; continue }
    $scan.monitor_calls++
    try {
      $o.monitors = Read-Monitors $o.id
      $o.monitors_ok = $true
      $newMon[$o.id] = [ordered]@{ w = $o.worst_state; dn = [int]$o.monitors_down; m = @($o.monitors) }
      $pieces += (Fp-Piece $o.id $o.worst_state $o.monitors)
    } catch {
      $scan.monitor_failed++
      $newFp = 'monitor read failed'
    }
  }
  if (-not $newFp) { $newFp = ($pieces | Sort-Object) -join ';' }
  if (-not $newFp) { $newFp = 'none' }
  if (@($newIds).Count -gt $MaxRecheckDevices) { $newFp = 'too many' }
  if (-not $scan.complete) { $newFp = '' }
  $scan.elapsed_s = [int]$sw.Elapsed.TotalSeconds
  $scan.monitor_s = $scan.elapsed_s - $scan.page_s
}
$headers = $null

$stamp = $startedAt.ToString('yyyyMMddTHHmmss')
$batchId = 'wugalert_' + $stamp + '_' + $kind
$dir = Join-Path $OutDir $batchId
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$snap = [ordered]@{ kind = $kind; group = $g.data; scan = $scan; devices = $devices.ToArray() }
$fname = 'snapshot.json'
$final = Join-Path $dir $fname
Write-Atomic $final ($snap | ConvertTo-Json -Depth 8 -Compress)
$item = [ordered]@{ name = $fname; sha256 = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $final).Length }
$day = $startedAt.ToString('yyyy-MM-dd')
$manifest = [ordered]@{
  source = 'whatsup_alert'
  batch_id = $batchId
  schema_version = 1
  collector_version = $ScriptVersion
  collected_at = $startedAt.ToString('yyyy-MM-ddTHH:mm:sszzz')
  time_zone = 'UTC'
  range = [ordered]@{ from = $day; to = $day }
  count = 1
  files = @($item)
}
# manifest.json is written last, so the system only ever sees a finished batch.
Write-Atomic (Join-Path $dir 'manifest.json') ($manifest | ConvertTo-Json -Depth 8 -Compress)
$state.last_write_utc = $nowUtc.ToString('o')
if ($kind -eq 'scan') {
  $state.sig = $sig; $state.complete = [bool]$scan.complete; $state.last_scan_utc = $nowUtc.ToString('o'); $state.fp = $newFp; $state.ids = @($newIds)
  $state.mon = $newMon
}
Write-Atomic $StateFile ($state | ConvertTo-Json -Depth 8 -Compress)
Write-Host ('BATCH ' + $batchId + ' : ' + $kind + ' (' + $scan.mode + '), problem devices ' + $devices.Count + ', complete ' + $state.complete + ', calls ' + $script:Calls + ', monitor lists reused ' + $scan.monitor_reused + ', seconds ' + $scan.elapsed_s)
exit 0
`,
        ok: '有變化時印一行 BATCH wugalert_… : scan, problem devices N, complete True；沒變化印 No change.；wug_alert_out 底下多一個批次資料夾',
        onFail: '登入失敗：確認網址與 wug_account.txt；Group status failed：把該行訊息帶回；complete False 且 stopped_reason 是 time box：WhatsUp 翻頁太慢，正常，下一分鐘會再掃一次',
        bring: '只需帶回終端機顯示的文字（不含密碼）；批次由排程上傳給系統，不用手動搬',
      },
    ],
    failHow: '任何錯誤：把終端機顯示的整段文字（不含帳密）貼到「整理結果」頁籤去識別後帶回，並在備註寫「失敗」。',
    expect: '有變化時一個批次資料夾：manifest.json（source=whatsup_alert）加上 snapshot.json（群組狀態、這次掃描完整與否、有問題的設備與它們的監控項目狀態）。系統匯入後在「4.1 事件應變 → 告警事件」看得到。',
    returnHow: '用 D6 relay_setup.ps1 -Alerts 登記排程後自動上傳；不用把檔案傳回給開發者。',
    notes: [
      '收集條件：worstState 不是 Up（Down、Maintenance、Unknown 都收）或 totalActiveMonitorsDown 大於 0。Maintenance（WhatsUp 上設的維護）會記錄但系統不推通知；Unknown 標「狀態未知」照樣通知。',
      '只有「每一頁都有設備清單、讀到最後一頁、看到的台數接近群組總數（98%）」才標完整；空清單、缺欄位、逾時一律不完整（系統不會因此把告警當成恢復）。群組狀態沒變時，每 5 分鐘重查上次有問題的那幾台（含維護中與狀態未知；一台恢復、另一台同時故障時總數不變也抓得到），每 30 分鐘再強制完整掃一次。有問題的設備太多（超過 150 台）或重查失敗時，不會每 5 分鐘全掃，只等 30 分鐘那次。群組狀態沒有 totalDevices、或最後一頁沒有分頁資訊，都算不完整。',
      '每次執行只做：1 次登入、1 次群組狀態；有變化才加上設備清單每頁 1 次（每頁 200 台、最多 30 頁）與每台有問題的設備 1 次監控項目（一次最多 60 台）。',
      '狀態檔 wug_alert_state.json 只記上次群組狀態的指紋、那次是否完整、最後寫出時間；刪掉它只會讓下一次重新完整掃一次。',
      '尚未在公司的 WhatsUp 上以排程實跑驗證（群組狀態與設備清單的路徑已在 11-03 實測過）。',
    ],
  },
  {
    id: 'D8',
    tag: 'vCOps',
    title: 'vCOps（Aria Operations）VM 清單收集腳本（中繼檔案，每週一次，PowerShell，只讀）',
    purpose: '取代日常手動匯入 RVTools：每週從 vCOps 讀一次全部 VM、ESXi、叢集（含所屬 vCenter），輸出成一個批次資料夾，經中繼上傳後系統自動匯入 VM 清單與位置。快照、逐顆磁碟等明細仍看每月的 RVTools。系統第一次收到時只預覽、不寫資產。',
    where: '連得到 vCOps 的跳板 Windows 主機（Windows PowerShell 5.1）；輸出資料夾再複製到中繼 PC 上傳。不是系統主機',
    perm: '唯讀：vCOps 只做 GET，外加一次登入取得權杖、一個唯讀的「最新屬性查詢」（POST 查詢、不改 vCOps）。帳密放在腳本旁的 aria_account.txt（第 1 行帳號、第 2 行密碼、第 3 行驗證來源可省略），輸出檔不含密碼或權杖',
    ver: 1,
    commands: [
      {
        label: '腳本 aria_inventory.ps1（按「下載腳本」存成檔案，放在 aria_account.txt 同一個資料夾）。執行：powershell -NoProfile -File aria_inventory.ps1 -AriaHost <vCOps 位址>。看到 BATCH ariainv_… : VM=… 才算成功；有 Stopped: 就把那一行帶回。',
        file: 'aria_inventory.ps1',
        text: `# ARIA-INVENTORY-SCRIPT v1 : weekly VM list from Aria Operations (vCOps) for the IT war room, read only.
# Runs on the jump host that reaches vCOps, PowerShell 5.1, any time (weekly is enough: the list hardly changes).
# What it reads: every VirtualMachine / HostSystem / ClusterComputeResource, the latest properties of the VMs and hosts,
# and the CHILD relationships cluster->host and host->VM. That is exactly what RVTools vInfo gives: VM, IP, OS, power,
# which ESXi, which cluster, which vCenter.
# Calls: GET only, except the token login and the read-only "latest properties" query (POST /resources/properties/latest/query,
# a query body, nothing is changed in vCOps).
# Output: one relay batch folder aria_relay/ariainv_<day>_<stamp>/ (next to this script) with vms.json hosts.json clusters.json props_NNN.json rels.json,
# manifest.json written last (the system never sees a half batch). Copy the folder to the relay PC's aria_out like the opening data,
# then run run2 (relay_push) there.
# If the list or the properties fail = no manifest; just run it again. Relationships are optional (-WithRels, v4).
# Credentials: aria_account.txt next to this script (line 1 user, line 2 password, line 3 auth source optional). Not in git.
# Run:  powershell -NoProfile -File aria_inventory.ps1 -AriaHost <vCOps address>
param(
  [Parameter(Mandatory=$true)][string]$AriaHost,
  [string]$OutDir = '',
  [int]$PageSize = 500,
  [int]$PropChunk = 50,
  [int]$FileChunk = 800,
  [switch]$WithRels,
  [int]$DelayMs = 1500
)
$ScriptVersion = 'aria_inventory 5'
# v9 (11-10, user: "big collections can take 24h, do not pull too much at once"): every vCOps call waits -DelayMs first
# (default 1500 ms) and is retried up to 3 times (3 / 6 / 9 s). The log prints the call number of the first failure, so we
# can tell "blocked after N calls" (rate limit / IPS) from "failed from the start" (connection).
$ts = Get-Date -f yyyyMMdd_HHmmss
# Everything stays next to this script (user 11-09: all WEBIT3 tools live in one folder, CL_DevTools).
$dl = $PSScriptRoot
$L = Join-Path $dl ('ariainv_' + $ts)
Start-Transcript "$L.log" | Out-Null
if (-not $OutDir) { $OutDir = Join-Path $dl 'aria_relay' }
$H = $AriaHost
[Net.ServicePointManager]::SecurityProtocol = 'Tls12'
[Net.ServicePointManager]::Expect100Continue = $false
[Net.ServicePointManager]::ServerCertificateValidationCallback = { param($a, $b, $c, $e) $e -eq 'None' -or $a.RequestUri.Host -eq $H }
$B = "https://$H/suite-api/api"

# ---- login (token) ----
$acc = Get-Content (Join-Path $PSScriptRoot 'aria_account.txt')
$u = "$($acc[0])".Trim(); $pw = "$($acc[1])"; $src = if ($acc.Count -gt 2) { "$($acc[2])".Trim() } else { '' }
# The transcript never records the account name, the auth source, the password, the token or the server's error body.
$t = $null
foreach ($s2 in @($src, 'LOCAL')) {
  $bd = @{ username = $u; password = $pw }; if ($s2) { $bd.authSource = $s2 }
  try {
    $t = Invoke-RestMethod -Method Post -Uri "$B/auth/token/acquire" -ContentType 'application/json' -Headers @{ Accept = 'application/json' } -Body ($bd | ConvertTo-Json) -ErrorAction Stop
    'login ok'; break
  } catch {
    $code = $null; $r = $_.Exception.Response; if ($r) { try { $code = [int]$r.StatusCode } catch { $code = $null } }
    'login failed, HTTP status=' + $(if ($code) { $code } else { 'none' })
  }
}
$pw = $null; $bd = $null; $acc = $null
if (-not $t) { Stop-Transcript; return }
$hd = @{ Accept = 'application/json'; Authorization = "vRealizeOpsToken $($t.token)" }
$t = $null

# v3 (11-10, NB measured on the jump host): vCOps answers without a charset, so PowerShell 5.1 reads UTF-8 as ISO-8859-1
# and Chinese folder names come out garbled. Every text value goes through Fix-Text: when all its characters fit in one byte
# and the bytes are valid UTF-8, it is decoded again as UTF-8; anything else is left as it is.
$script:strict = New-Object System.Text.UTF8Encoding($false, $true)
$script:latin1 = [Text.Encoding]::GetEncoding(28591)
function Fix-Text($v) {
  if ($null -eq $v) { return $null }
  $t = [string]$v
  $high = $false
  foreach ($c in $t.ToCharArray()) { $n = [int]$c; if ($n -gt 255) { return $t }; if ($n -ge 128) { $high = $true } }
  if (-not $high) { return $t }
  try { return $script:strict.GetString($script:latin1.GetBytes($t)) } catch { return $t }
}

# Keep only what the system needs from a resource (name, kind, identifiers); vCOps adds badges etc. we do not need.
function Slim($r) {
  $ids = @()
  foreach ($x in @($r.resourceKey.resourceIdentifiers)) {
    if ($x -and $x.identifierType) { $ids += [ordered]@{ identifierType = [ordered]@{ name = [string]$x.identifierType.name }; value = (Fix-Text $x.value) } }
  }
  [ordered]@{ identifier = [string]$r.identifier
              resourceKey = [ordered]@{ name = (Fix-Text $r.resourceKey.name); adapterKindKey = [string]$r.resourceKey.adapterKindKey
                                        resourceKindKey = [string]$r.resourceKey.resourceKindKey; resourceIdentifiers = $ids } }
}

# v2 (11-10, measured on the jump host): Invoke-WebRequest failed on every NEW TLS connection to vCOps
# ("the underlying connection was closed: an unexpected error occurred on a send"); calls only worked while they reused the
# login connection. Invoke-RestMethod (what aria_probe.ps1 has used on the same host for weeks) does not have this problem,
# so all calls go through Invoke-RestMethod, and a failed call is retried once after 3 seconds.
function Get-Json([string]$Uri, [string]$Method = 'Get', [string]$Body = $null) {
  $p = @{ Uri = $Uri; Headers = $hd; Method = $Method }
  if ($Body) { $p.ContentType = 'application/json; charset=utf-8'; $p.Body = [Text.Encoding]::UTF8.GetBytes($Body) }
  # v4 (11-10 NB on the jump host): one retry was not enough; up to 3 retries, waiting 3 / 6 / 9 seconds.
  for ($t = 0; $t -le 3; $t++) {
    Start-Sleep -Milliseconds $DelayMs
    $script:calls++
    try { return Invoke-RestMethod @p }
    catch {
      if ($null -eq $script:firstFail) { $script:firstFail = $script:calls; Write-Host ("first failure at call #" + $script:calls + ": " + $_.Exception.Message) }
      if ($t -eq 3) { throw }
      Write-Host ("retry " + ($t + 1) + " of 3 (call #" + $script:calls + "): " + $_.Exception.Message); Start-Sleep -Seconds (3 * ($t + 1))
    }
  }
}
$script:calls = 0; $script:firstFail = $null

$ErrorActionPreference = 'Stop'
$ok = $true
try {
  # ---- full lists (paged, checked against totalCount) ----
  $lists = @{}
  foreach ($kind in @('VirtualMachine', 'HostSystem', 'ClusterComputeResource')) {
    $all = @(); $pg = 0; $total = $null
    do {
      $rr = Get-Json "$B/resources?resourceKind=$kind&page=$pg&pageSize=$PageSize"
      $chunk = @($rr.resourceList)
      $all += $chunk
      if ($rr.pageInfo -and ($null -ne $rr.pageInfo.totalCount)) { $total = [int]$rr.pageInfo.totalCount }
      $pg++
    } while (($chunk.Count -ge $PageSize) -and ($pg -lt 200) -and (($null -eq $total) -or ($all.Count -lt $total)))
    if (($null -ne $total) -and ($all.Count -lt $total)) { throw "$kind : vCOps says $total but only $($all.Count) came back" }
    "$kind listed=$($all.Count) total=$total pages=$pg"
    $lists[$kind] = @($all | Where-Object { $_.identifier })
  }
  if ($lists['VirtualMachine'].Count -eq 0) { throw 'no VirtualMachine came back' }

  # ---- ALL properties of VMs, hosts and clusters (v3, user 11-10: take the whole set, the system picks what it needs) ----
  # GET /resources/properties?resourceId=..&resourceId=.. (several resources per call). If that answer has no
  # resourcePropertiesList, fall back to GET /resources/{id}/properties one by one (the call NB measured: 73 properties per VM).
  $props = [ordered]@{}
  $bulk = $true
  function Add-Props($item) {
    $p = [ordered]@{}
    foreach ($x in @($item.property)) { if ($x -and $x.name -and ("$($x.value)" -ne '')) { $p[[string]$x.name] = (Fix-Text $x.value) } }
    $props[[string]$item.resourceId] = $p
  }
  $all = @($lists['VirtualMachine']) + @($lists['HostSystem']) + @($lists['ClusterComputeResource'])
  $ids = @($all | ForEach-Object { [string]$_.identifier })
  for ($i = 0; $i -lt $ids.Count; $i += $PropChunk) {
    $chunk = $ids[$i..([Math]::Min($i + $PropChunk, $ids.Count) - 1)]
    if ($bulk) {
      $q = ($chunk | ForEach-Object { 'resourceId=' + $_ }) -join '&'
      $res = Get-Json "$B/resources/properties?$q"
      if ($res -and $res.resourcePropertiesList) { foreach ($item in @($res.resourcePropertiesList)) { Add-Props $item } }
      else { $bulk = $false; 'bulk properties call has no resourcePropertiesList; reading one resource at a time' }
    }
    if (-not $bulk) {
      foreach ($id in $chunk) { if (-not $props.Contains($id)) { Add-Props (Get-Json "$B/resources/$id/properties"); Start-Sleep -Milliseconds 100 } }
    }
    Start-Sleep -Milliseconds 300
  }
  $nKeys = 0; foreach ($k in $props.Keys) { $nKeys += $props[$k].Count }
  "properties for $($props.Count) resources ($nKeys values, bulk=$bulk)"

  # ---- relationships: cluster -> hosts, host -> VMs (v4: OPTIONAL) ----
  # v4 (11-10 NB): this part dropped the connection on the jump host and the whole batch was lost. Every VM's properties
  # already carry summary|parentHost / parentCluster / parentVcenter (the system falls back to them), so relationships
  # are only read with -WithRels, and a failure there no longer stops the batch: rels.json is written empty.
  $rels = [ordered]@{ cluster_hosts = [ordered]@{}; host_vms = [ordered]@{} }
  if ($WithRels) {
    try {
      foreach ($pair in @(@{ list = $lists['ClusterComputeResource']; key = 'cluster_hosts'; kind = 'HostSystem' },
                          @{ list = $lists['HostSystem']; key = 'host_vms'; kind = 'VirtualMachine' })) {
        foreach ($r in $pair.list) {
          $rr = Get-Json "$B/resources/$($r.identifier)/relationships?relationshipType=CHILD&pageSize=$PageSize"
          # One page only: if vCOps says there are more, stop (a cut list would drop VMs from their ESXi).
          if ($rr.pageInfo -and ($null -ne $rr.pageInfo.totalCount) -and ([int]$rr.pageInfo.totalCount -gt @($rr.resourceList).Count)) {
            throw "relationships of $($r.resourceKey.name): $($rr.pageInfo.totalCount) listed but only $(@($rr.resourceList).Count) came back" }
          $rels[$pair.key][[string]$r.identifier] = @(@($rr.resourceList) | Where-Object { $_.resourceKey.resourceKindKey -eq $pair.kind } | ForEach-Object { [string]$_.identifier })
          Start-Sleep -Milliseconds 200
        }
      }
      "relationships: clusters=$($rels.cluster_hosts.Count) hosts=$($rels.host_vms.Count)"
    } catch {
      $rels = [ordered]@{ cluster_hosts = [ordered]@{}; host_vms = [ordered]@{} }
      'relationships skipped (' + $_.Exception.Message + '); host / cluster come from the VM properties'
    }
  } else {
    'relationships not read (host / cluster come from the VM properties; add -WithRels to read them)'
  }
} catch {
  $ok = $false
  'Stopped: ' + $_.Exception.Message + ' (no batch written; run it again).'
}
"vCOps calls=$($script:calls) first failure at call #$(if ($null -eq $script:firstFail) { 'none' } else { $script:firstFail }) (delay $DelayMs ms)"
$hd = $null

if ($ok) {
  function Write-Atomic([string]$Path, [string]$Text) {
    $tmp = $Path + '.tmp'
    [IO.File]::WriteAllText($tmp, $Text, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
  }
  $tz8 = New-TimeSpan -Hours 8
  $day = [DateTimeOffset]::UtcNow.ToOffset($tz8).ToString('yyyy-MM-dd')
  $batchId = 'ariainv_' + $day + '_' + (Get-Date -f yyyyMMddTHHmmss)
  $bdir = Join-Path $OutDir $batchId
  try {
    New-Item -ItemType Directory -Force -Path $bdir | Out-Null
    $files = New-Object System.Collections.ArrayList
    $parts = [ordered]@{
      'vms.json' = @($lists['VirtualMachine'] | ForEach-Object { Slim $_ })
      'hosts.json' = @($lists['HostSystem'] | ForEach-Object { Slim $_ })
      'clusters.json' = @($lists['ClusterComputeResource'] | ForEach-Object { Slim $_ })
      'rels.json' = $rels
    }
    # properties go in props_001.json, props_002.json, ... ($FileChunk resources each) so no file gets near the relay size limit
    $pk = @($props.Keys); $n = 0
    for ($i = 0; $i -lt $pk.Count; $i += $FileChunk) {
      $n++; $part = [ordered]@{}
      foreach ($k in $pk[$i..([Math]::Min($i + $FileChunk, $pk.Count) - 1)]) { $part[$k] = $props[$k] }
      $parts[('props_' + $n.ToString('000') + '.json')] = $part
    }
    foreach ($name in $parts.Keys) {
      $final = Join-Path $bdir $name
      Write-Atomic $final (ConvertTo-Json -InputObject $parts[$name] -Depth 8 -Compress)
      [void]$files.Add([ordered]@{ name = $name; sha256 = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $final).Length })
    }
    $manifest = [ordered]@{
      source = 'aria_inventory'
      batch_id = $batchId
      schema_version = 1
      collector_version = $ScriptVersion
      collected_at = [DateTimeOffset]::UtcNow.ToOffset($tz8).ToString('yyyy-MM-ddTHH:mm:sszzz')
      time_zone = 'Asia/Taipei'
      range = [ordered]@{ from = $day; to = $day }
      count = $files.Count
      files = $files.ToArray()
    }
    Write-Atomic (Join-Path $bdir 'manifest.json') (ConvertTo-Json -InputObject $manifest -Depth 8 -Compress)
    "BATCH $batchId : VM=$($lists['VirtualMachine'].Count) ESXi=$($lists['HostSystem'].Count) cluster=$($lists['ClusterComputeResource'].Count) -> $bdir"
    'Next: copy this folder to the relay PC aria_out folder, then run run2 (relay_push) there.'
  } catch { 'Stopped writing the batch: ' + $_.Exception.Message + ' (no manifest; run it again).' }
}
$ErrorActionPreference = 'Continue'
Stop-Transcript
`,
      },
    ],
    failHow: '畫面上 Stopped: 開頭那一行（不含帳密）原文帶回；沒有寫出 manifest 的資料夾系統不會匯入，重跑即可。',
    expect: '腳本所在資料夾（建議 C:\\CL_DevTools）底下 aria_relay 資料夾裡一個 ariainv_ 開頭的批次資料夾：vms.json、hosts.json、clusters.json、props_001.json…（全部屬性，每檔 800 台）、rels.json，最後是 manifest.json（source=aria_inventory）。',
    returnHow: '把批次資料夾複製到中繼 PC 的上傳資料夾，用 relay_push 上傳；系統匯入後在「資料匯入 → 整合中繼」看得到那一批的預覽數字。',
    notes: [
      '每週一次就好（VM 清單不太會變）；系統超過 9 天沒收到新的一批才亮過期。',
      '系統第一次收到時只預覽、不寫資產：會顯示「對得上既有幾台、新的幾台、各欄位有值比例」，確認數字合理才打開寫入。',
      '只更新 VM 清單與位置（VM、IP、作業系統、ESXi、叢集、vCenter），不碰 RVTools 的主機、datastore、磁碟、快照分頁。',
      '屬性鍵（例如 vCenter 名稱用的 summary|parentVcenter）尚未在公司的 vCOps 實測。',
    ],
  },
  {
    id: 'D9',
    tag: '其他',
    title: 'AD 人員名單匯出（雙擊 BAT，只讀，預設只留資訊管理處）',
    purpose: '給「資料匯入 → AD 名單」用：EOS／弱點通知要用 AD 查收件人的信箱、主管、部門。用登入者自己的身分讀公司 AD（ADSI，不用額外模組），預設只輸出資訊管理處的人，不把全公司個資寫到磁碟。',
    where: '加入網域、登入公司帳號的 Windows 電腦（Windows PowerShell 5.1）；兩個檔案放同一個資料夾（建議 CL_DevTools）',
    perm: '唯讀：只查 AD，不改任何東西，不問密碼；只寫一個 CSV 到腳本旁邊（含個資，匯入後請刪除）',
    ver: 2,
    commands: [
      {
        label: '腳本 ad_export.ps1（按「下載腳本」存成檔案）。要別的部門：powershell -NoProfile -File ad_export.ps1 -Dept <部門名稱開頭>；全公司：加 -All（會寫出全公司個資，非必要不要用）。',
        file: 'ad_export.ps1',
        text: `# AD-EXPORT-SCRIPT v3 : read-only AD user list for WEBIT3 (import: 6 data import -> AD list)
# Uses ADSI (no RSAT / no Graph module). Runs as the logged-in user; no password asked; nothing in AD is changed.
# Columns match ad_import.py aliases: SamAccountName, employeeID, DisplayName,
# UserPrincipalName, mail, department, manager (DN), Enabled; extra Phone/Ext (ignored by import).
# v2 (11-09 user): keep only one department tree (default = the IT management division) instead of writing the whole company
# (3,900+ people) to disk; output goes next to this script (all WEBIT3 tools live in one folder, CL_DevTools).
# The default department is built from code points so the file stays plain ASCII (PowerShell 5.1 reads BOM-less files as ANSI).
# v3 (11-10 user: the WEBIT3 host cannot reach AD, only this PC can): -RelayDir writes a relay batch instead of a CSV
#   (RelayDir/adusers_<time>/users.json + manifest.json, manifest last) for relay_push.ps1 to upload; WEBIT3 imports it as
#   source ad_users (whole list replaced; a list more than 20 % smaller than the current one is refused there).
#   -PurgeDir <sent folder> deletes uploaded adusers_* batches older than 2 days (they hold personal data).
#   Daily example:  powershell -NoProfile -File ad_export.ps1 -All -RelayDir ad_out -PurgeDir ad_sent
# Run:  double-click run_ad_export.bat, or  powershell -NoProfile -File ad_export.ps1 [-Dept <department prefix>] [-All]
param(
  [string]$Dept = '',
  [switch]$All,
  [string]$Out = '',
  [string]$RelayDir = '',
  [string]$PurgeDir = ''
)
if (-not $Dept) { $Dept = -join ([char[]](0x8CC7, 0x8A0A, 0x7BA1, 0x7406, 0x8655)) }
if (-not $Out) { $Out = Join-Path $PSScriptRoot ('ad_users_' + (Get-Date -f yyyyMMdd) + '.csv') }
$s = New-Object DirectoryServices.DirectorySearcher
$s.Filter = '(&(objectCategory=person)(objectClass=user))'
$s.PageSize = 1000
$p = 'samaccountname','employeeid','displayname','userprincipalname','mail','department','manager','useraccountcontrol','telephonenumber'
$p | ForEach-Object { [void]$s.PropertiesToLoad.Add($_) }
function V($r, $k) { if ($r.Properties[$k].Count) { [string]$r.Properties[$k][0] } else { '' } }
$total = 0
$rows = foreach ($r in $s.FindAll()) {
  $total++
  $dep = V $r 'department'
  if ((-not $All) -and (-not $dep.StartsWith($Dept))) { continue }
  [pscustomobject]@{
    SamAccountName    = V $r 'samaccountname'
    employeeID        = V $r 'employeeid'
    DisplayName       = V $r 'displayname'
    UserPrincipalName = V $r 'userprincipalname'
    mail              = V $r 'mail'
    department        = $dep
    manager           = V $r 'manager'
    Enabled           = -not ([int]('0' + (V $r 'useraccountcontrol')) -band 2)
    Phone             = V $r 'telephonenumber'
    Ext               = ((V $r 'telephonenumber') -split '#')[1]
  }
}
if (@($rows).Count -eq 0) { "No user matched (read $total from AD); nothing written. Check -Dept."; exit 1 }
if ($PurgeDir -and (Test-Path -LiteralPath $PurgeDir)) {
  Get-ChildItem -LiteralPath $PurgeDir -Directory -Filter 'adusers_*' | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-2) } |
    ForEach-Object { [IO.Directory]::Delete($_.FullName, $true) }
}
if ($RelayDir) {
  $bs = [string][char]92     # backslash built from its code: this text is also embedded in a JS template string, where a literal backslash would be an escape
  function J([string]$v) {
    $b = New-Object Text.StringBuilder
    foreach ($ch in $v.ToCharArray()) {
      $c = [int]$ch
      if ($c -eq 34) { [void]$b.Append($bs + '"') } elseif ($c -eq 92) { [void]$b.Append($bs + $bs) }
      elseif ($c -lt 32) { [void]$b.Append($bs + 'u' + $c.ToString('x4')) } else { [void]$b.Append($ch) }
    }
    return '"' + $b.ToString() + '"'
  }
  $cols = @('SamAccountName', 'employeeID', 'DisplayName', 'UserPrincipalName', 'mail', 'department', 'manager', 'Enabled', 'Phone', 'Ext')
  $sb = New-Object Text.StringBuilder
  [void]$sb.Append('{"columns":[' + (($cols | ForEach-Object { J $_ }) -join ',') + '],"rows":[')
  $first = $true
  foreach ($r in $rows) {
    if (-not $first) { [void]$sb.Append(',') }
    $first = $false
    [void]$sb.Append('[' + (($cols | ForEach-Object { J ([string]$r.$_) }) -join ',') + ']')
  }
  [void]$sb.Append(']}')
  $now = Get-Date
  $bid = 'adusers_' + $now.ToString('yyyyMMddTHHmmss')
  $dir = Join-Path $RelayDir $bid
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $f = Join-Path $dir 'users.json'
  [IO.File]::WriteAllText($f + '.tmp', $sb.ToString(), (New-Object Text.UTF8Encoding($false)))
  Move-Item -LiteralPath ($f + '.tmp') -Destination $f -Force
  $item = [ordered]@{ name = 'users.json'; sha256 = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $f).Length }
  $day = $now.ToString('yyyy-MM-dd')
  $m = [ordered]@{ source = 'ad_users'; batch_id = $bid; schema_version = 1; collector_version = 'ad_export 3'
                   collected_at = $now.ToString('yyyy-MM-ddTHH:mm:sszzz'); time_zone = 'UTC'; range = [ordered]@{ from = $day; to = $day }; count = 1; files = @($item) }
  $mf = Join-Path $dir 'manifest.json'
  [IO.File]::WriteAllText($mf + '.tmp', ($m | ConvertTo-Json -Depth 5 -Compress), (New-Object Text.UTF8Encoding($false)))
  Move-Item -LiteralPath ($mf + '.tmp') -Destination $mf -Force
  "$(@($rows).Count) users (of $total in AD) -> relay batch $dir"
  exit 0
}
$rows | Export-Csv $Out -NoTypeInformation -Encoding UTF8
"$(@($rows).Count) users (of $total in AD) -> $Out"
'Next: WEBIT3 6 data import -> AD list, upload this CSV. Delete the CSV afterwards (it holds personal data).'
`,
      },
      {
        label: '雙擊用的 run_ad_export.bat（按「下載腳本」存成檔案，跟 ad_export.ps1 放同一個資料夾）。雙擊後看到「N users (of M in AD) -> …csv」就完成。',
        file: 'run_ad_export.bat',
        text: `@echo off
rem AD-EXPORT-BAT v1
rem Export the AD user list (IT management division only) for WEBIT3: double-click this file.
rem Put it in the same folder as ad_export.ps1 (the CL_DevTools folder). Read only; runs as you; asks no password.
cd /d "%~dp0"
powershell -NoProfile -File ad_export.ps1
pause
`,
      },
      {
        label: 'AD 對時 ad_time.ps1（按「下載腳本」存成檔案，跟 relay_push.ps1 放同一個資料夾）。每天每日檢查快照前跑一次：powershell -NoProfile -File ad_time.ps1 -RelayDir ad_out，再用 relay_push.ps1 上傳。只讀：w32tm /monitor 問每台 DC 的時間，不改任何東西。',
        file: 'ad_time.ps1',
        text: `# AD-TIME-SCRIPT v1 : read-only time offset of every domain controller, for the WEBIT3 daily check (AD time sync row)
# Runs the built-in Windows tool  w32tm /monitor  (asks each DC for its NTP time; changes nothing anywhere, needs no password,
# no admin rights). Writes one relay batch (RelayDir/adtime_<time>/dcs.json + manifest.json, manifest last) for relay_push.ps1;
# WEBIT3 imports it as source ad_time. If a DC cannot be asked (firewall, UDP 123) that DC is written with ok=false and the
# reason, so the daily check shows it as no data, never as normal.
# Daily example (before the 06:30 daily-check snapshot):  powershell -NoProfile -File ad_time.ps1 -RelayDir ad_out
param(
  [Parameter(Mandatory=$true)][string]$RelayDir,
  [string]$Domain = ''
)
$ErrorActionPreference = 'Stop'
$args2 = @('/monitor', '/nowarn')
if ($Domain) { $args2 += ('/domain:' + $Domain) }
$out = @(& w32tm @args2 2>&1 | ForEach-Object { [string]$_ })
$list = New-Object System.Collections.ArrayList
$cur = $null
foreach ($line in $out) {
  $t = $line.Trim()
  if (-not $t) { continue }
  $open = $t.IndexOf('[')
  $close = $t.IndexOf(']')
  if ((-not $line.StartsWith(' ')) -and ($open -gt 0) -and ($close -gt $open) -and $t.EndsWith(':')) {
    $ipport = $t.Substring($open + 1, $close - $open - 1)
    $ip = $ipport
    $c = $ipport.LastIndexOf(':')
    if (($c -gt 0) -and ($ipport.IndexOf('.') -gt 0)) { $ip = $ipport.Substring(0, $c) }
    $cur = [ordered]@{ name = ($t.Substring(0, $open).Trim() -split ' ')[0]; ip = $ip; ok = $false; offset_s = $null; note = 'no NTP line' }
    [void]$list.Add($cur)
    continue
  }
  if ($cur -and $t.StartsWith('NTP:')) {
    $rest = $t.Substring(4).Trim()
    $tok = ($rest -split ' ')[0]
    if ($tok -match '^[+-]?[0-9]+([.][0-9]+)?s$') {
      $cur.offset_s = [double]::Parse($tok.TrimEnd('s'), [Globalization.CultureInfo]::InvariantCulture)
      $cur.ok = $true
      $cur.note = ''
    } else {
      $cur.note = $rest
      if ($cur.note.Length -gt 200) { $cur.note = $cur.note.Substring(0, 200) }
    }
  }
}
if ($list.Count -eq 0) { 'w32tm /monitor listed no domain controller; nothing written. Output was:'; $out | Select-Object -First 15; exit 1 }
$now = Get-Date
$bid = 'adtime_' + $now.ToString('yyyyMMddTHHmmss')
$dir = Join-Path $RelayDir $bid
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$f = Join-Path $dir 'dcs.json'
$json = '{"dcs":' + (ConvertTo-Json -InputObject @($list.ToArray()) -Depth 3 -Compress) + '}'
[IO.File]::WriteAllText($f + '.tmp', $json, (New-Object Text.UTF8Encoding($false)))
Move-Item -LiteralPath ($f + '.tmp') -Destination $f -Force
$item = [ordered]@{ name = 'dcs.json'; sha256 = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $f).Length }
$day = $now.ToString('s').Substring(0, 10)
$m = [ordered]@{ source = 'ad_time'; batch_id = $bid; schema_version = 1; collector_version = 'ad_time 1'
                 collected_at = $now.ToString('yyyy-MM-ddTHH:mm:sszzz'); time_zone = 'UTC'; range = [ordered]@{ from = $day; to = $day }; count = 1; files = @($item) }
$mf = Join-Path $dir 'manifest.json'
[IO.File]::WriteAllText($mf + '.tmp', ($m | ConvertTo-Json -Depth 5 -Compress), (New-Object Text.UTF8Encoding($false)))
Move-Item -LiteralPath ($mf + '.tmp') -Destination $mf -Force
$okn = @($list | Where-Object { $_.ok }).Count
"$($list.Count) domain controllers ($okn answered) -> relay batch $dir"
foreach ($d in $list) { '  ' + $d.name + '  ' + $(if ($d.ok) { [string]$d.offset_s + ' s' } else { 'no answer: ' + $d.note }) }
exit 0
`,
      },
    ],
    failHow: '畫面最後幾行（不含個資）原文帶回；No user matched 表示部門名稱對不上，換 -Dept 再跑。',
    expect: '腳本旁一個 ad_users_<日期>.csv（UTF-8），欄位：SamAccountName、employeeID、DisplayName、UserPrincipalName、mail、department、manager、Enabled、Phone、Ext。',
    returnHow: '到系統「資料匯入 → AD 名單」上傳這個 CSV；上傳後刪掉 CSV（內含個資）。',
    notes: [
      '預設部門比對：department 欄以「資訊管理處」開頭的人（含底下各部）。',
      'DisplayName 帶英文名（例如「林宏書 KOBE」）不用另外拆欄：通知比對收件人時會自動只比中文名，同名的仍不亂猜。',
    ],
  },
  {
    id: 'D10',
    tag: 'vCOps',
    warn: '請在收盤後（15:05 以後）執行；資料是當天 09:00–09:30 的開盤尖峰',
    title: 'vCOps（Aria Operations）開盤尖峰收集腳本（中繼檔案，每天收盤後，PowerShell，只讀）',
    purpose: '首頁「今日開盤尖峰」與 Teams 開盤摘要的資料來源：讀每台 ESXi 09:00–09:30 的 CPU／記憶體（含 vCOps 最高值，跟主機組人工報告同一個數字），一台一檔寫成一個批次資料夾，經中繼上傳後系統自動匯入。',
    where: '連得到 vCOps 的跳板 Windows 主機（Windows PowerShell 5.1）；輸出資料夾再複製到中繼 PC 上傳。不是系統主機',
    perm: '唯讀：vCOps 只做 GET，外加一次登入取得權杖。帳密放在腳本旁的 aria_account.txt（第 1 行帳號、第 2 行密碼、第 3 行驗證來源可省略），輸出檔不含密碼或權杖',
    ver: 7,
    commands: [
      {
        label: '腳本 aria_probe.ps1（按「下載腳本」存成檔案，放在 aria_account.txt 同一個資料夾）。執行：powershell -NoProfile -File aria_probe.ps1 -AriaHost <vCOps 位址> -NameLike <ESXi 名稱樣式>。看到 BATCH ariahost_… 才算成功。',
        file: 'aria_probe.ps1',
        text: `# ARIA-HOST-PROBE-SCRIPT v7 : Aria Operations (vCOps) read-only probe for ESXi hosts (GET only, plus one token login).
# Runs on the jump host (the only one that reaches vCOps), PowerShell 5.1, after the market closes (15:05 or later).
# 2026-11-04 synced with the version running on the jump host: resourceKind=HostSystem&pageSize=300, start = today 09:00 (+08),
# also fetches the swap statKey, file prefix ariahost_, and dot-sources aria_host_report.ps1 at the end (console Top10 + CSV).
# New in v2: also writes one relay batch folder = manifest.json + one JSON per host (same contract as wug_collect.ps1;
# manifest.json is written last, so the system only ever sees a finished batch). The system (IT war room) only reads these files.
# v3 (review fixes): missing values stay null (never 0); the host list is paged (page=0,1,...) and checked against totalCount;
# the transcript does not record the account, auth source or server error body (login failure = HTTP status only);
# the batch-writing part runs with ErrorActionPreference Stop and any failure (or any host that could not be read) means no manifest.
# vCOps keeps 5-minute points for 28 days, so a run after the close (or a later day with -Day) gets the full 09:00-09:30 window.
# Credentials: aria_account.txt next to this script (line 1 user, line 2 password, line 3 auth source optional). Not in git.
# PS 5.1 trap: strings from Get-Content carry PSPath etc.; always cast to plain string ("$($x)") or vCOps answers 400 Invalid input format.
# Run:  powershell -NoProfile -File aria_probe.ps1 -AriaHost <vCOps address>            (no execution-policy override, no encoded command)
# Self-signed certificate is accepted only for $AriaHost, only inside this PowerShell process.
param(
  [Parameter(Mandatory=$true)][string]$AriaHost,
  [string]$NameLike = 'esxi169-*',
  [string]$Day = '',
  [string]$OutDir = '',
  [int]$PageSize = 300,
  [ValidateSet('Open', '24h')][string]$Mode = 'Open',
  [int]$DelayMs = 1500
)
$ScriptVersion = 'aria_probe 11'
# v11 (11-11, NB measured on the jump host: v10 always broke at call #101, then every retry failed too): vCOps closes a
# keep-alive connection after 100 requests and PS 5.1 keeps reusing the dead one. Every call now opens its own connection
# (-DisableKeepAlive), and before a retry the pooled connections to vCOps are closed, so a retry never reuses a broken one.
# v10 (11-10, NB measured on the jump host): GET /resources/{host}/relationships drops the connection on the FIRST call,
# also when slowed down (not a rate limit); v7 never used it and always worked. So v10 does not call relationships at all:
# it lists the VMs (paged) and reads their properties in bulk (GET /resources/properties?resourceId=..), then puts each VM
# under the host named in its summary|parentHost. Host stats are read exactly as in v7. If the VM part fails, the host
# numbers still go out and every host gets vms_error (the system shows the VM section as unknown).
# v9 (11-10, user: "big collections can take 24h, do not pull too much at once"): every vCOps call waits -DelayMs first
# (default 1500 ms) and is retried up to 3 times (3 / 6 / 9 s). The log prints the call number of the first failure, so we
# can tell "blocked after N calls" (rate limit / IPS) from "failed from the start" (connection).
# v8 (11-10): second part of the opening report = "VMs using more than 10% of the host's physical CPU" (team's manual report).
# Per host: GET its CHILD relationships (VirtualMachine), then GET /resources/stats for those VMs in groups of 40
# (cpu|usagemhz_average, rollUpType=MAX, 5 min). Written into the host file as "vms": [{name, resource_id, t, v}].
# The system divides by the host's cpu|totalCapacity_average (already collected). A VM read failure does NOT stop the
# batch (the host numbers still go out); that host gets "vms_error" instead of "vms", and the system shows it as unknown.
# v7 (11-09): -AriaHost is required (no address in the file: it is published in the devkit); the log, the raw JSON and
# the relay batches are written next to this script (user 11-09: all WEBIT3 tools live in one folder, WEBIT3_DevTools).
# v6 (11-09): the team's manual opening report uses the vCOps "CPU Usage (Max)" column = rollUpType=MAX.
# 5-minute AVG flattens short peaks (esxi169-214 on 11-09: AVG 80.66%, MAX 87.48% = manual report), so each host is also
# read once with rollUpType=MAX for cpu|usage_average and mem|usage_average, written as extra keys "<key>:max".
# Older systems ignore keys they do not know, so the batch still imports there (just without the max).
# v5 (11-04): -Mode Open (default) = only $Day 09:00-09:30; -Mode 24h = last 24 hours (see v4 below).
# v4 (11-04 evening): range = last 24 hours up to now (never later than $Day 09:00), so one run after the close feeds every
# report in aria_host_report.ps1: opening peak, hosts >=90% in 24h, per-host 24h resources, cluster-wide CPU, Top10.
# Extra keys cpu|usagemhz_average and cpu|totalCapacity_average (MHz) give the cluster-wide CPU total; missing stays null.
$ts = Get-Date -f yyyyMMdd_HHmmss
$L = Join-Path $PSScriptRoot ('ariahost_' + $ts)
Start-Transcript "$L.log" | Out-Null
if (-not $OutDir) { $OutDir = Join-Path $PSScriptRoot 'aria_relay' }
$H = $AriaHost
[Net.ServicePointManager]::SecurityProtocol = 'Tls12'
[Net.ServicePointManager]::Expect100Continue = $false
[Net.ServicePointManager]::ServerCertificateValidationCallback = { param($a, $b, $c, $e) $e -eq 'None' -or $a.RequestUri.Host -eq $H }
$B = "https://$H/suite-api/api"

# ---- login (the only POST) ----
$acc = Get-Content (Join-Path $PSScriptRoot 'aria_account.txt')
$u = "$($acc[0])".Trim(); $pw = "$($acc[1])"; $src = if ($acc.Count -gt 2) { "$($acc[2])".Trim() } else { '' }
# The transcript never records the account name, the auth source, the password, the token or the server's error body.
$t = $null
foreach ($s2 in @($src, 'LOCAL')) {
  $bd = @{ username = $u; password = $pw }; if ($s2) { $bd.authSource = $s2 }
  try {
    $t = Invoke-RestMethod -Method Post -Uri "$B/auth/token/acquire" -ContentType 'application/json' -Headers @{ Accept = 'application/json' } -Body ($bd | ConvertTo-Json) -DisableKeepAlive -ErrorAction Stop
    'login ok'; break
  } catch {
    $code = $null; $r = $_.Exception.Response; if ($r) { try { $code = [int]$r.StatusCode } catch { $code = $null } }
    'login failed, HTTP status=' + $(if ($code) { $code } else { 'none' })
  }
}
$pw = $null; $bd = $null; $acc = $null
if (-not $t) { Stop-Transcript; return }
$hd = @{ Accept = 'application/json'; Authorization = "vRealizeOpsToken $($t.token)" }
$t = $null

# ---- time range: $Day 09:00 (+08) to now (or end of that day for a past day) ----
$tz8 = New-TimeSpan -Hours 8
if (-not $Day) { $Day = [DateTimeOffset]::UtcNow.ToOffset($tz8).ToString('yyyy-MM-dd') }
$dd = [DateTime]::ParseExact($Day, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
$d0 = New-Object DateTimeOffset($dd.Year, $dd.Month, $dd.Day, 9, 0, 0, $tz8)
$open0 = $d0.ToUnixTimeMilliseconds()                    # 09:00 of $Day (opening window start, used by the report)
$dayEnd = $d0.AddHours(15).ToUnixTimeMilliseconds()     # 24:00 of $Day
$e2 = [Math]::Min([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(), $dayEnd)
$b2 = [Math]::Min($open0, $e2 - 86400000)                # last 24h, never later than 09:00 so the opening window is always inside
if ($Mode -eq 'Open') {   # opening only: 09:00-09:30
  if ($e2 -le $open0) { "$Day 09:00 (+08) has not come yet; nothing to fetch in -Mode Open."; Stop-Transcript; return }
  $b2 = $open0; $e2 = [Math]::Min($e2, $open0 + 1800000)
}
if ($e2 -le $open0) { "Note: $Day 09:00 (+08) has not come yet; the opening report will be empty, the 24h reports still run." }

# ---- hosts + stats (GET only) ----
$script:calls = 0; $script:firstFail = $null
function Call([string]$Uri) {
  for ($t = 0; $t -le 3; $t++) {
    Start-Sleep -Milliseconds $DelayMs
    $script:calls++
    try { return Invoke-RestMethod -Uri $Uri -Headers $hd -DisableKeepAlive }
    catch {
      if ($null -eq $script:firstFail) { $script:firstFail = $script:calls; Write-Host ("first failure at call #" + $script:calls + ": " + $_.Exception.Message) }
      if ($t -eq 3) { throw }
      try { [void][Net.ServicePointManager]::FindServicePoint([Uri]$Uri).CloseConnectionGroup('') } catch { Write-Host ('could not reset the connection: ' + $_.Exception.Message) }
      Write-Host ("retry " + ($t + 1) + " of 3 (call #" + $script:calls + "): " + $_.Exception.Message); Start-Sleep -Seconds (3 * ($t + 1))
    }
  }
}
$keys = @('cpu|usage_average', 'mem|usage_average', 'mem|swapped_average', 'cpu|usagemhz_average', 'cpu|totalCapacity_average')
# Page through the whole list (page=0,1,...) so a large inventory is never cut at one page.
$hosts = @(); $pg = 0; $listedTotal = $null
do {
  $rr = Call "$B/resources?resourceKind=HostSystem&page=$pg&pageSize=$PageSize"
  $chunk = @($rr.resourceList)
  $hosts += $chunk
  if ($rr.pageInfo -and ($null -ne $rr.pageInfo.totalCount)) { $listedTotal = [int]$rr.pageInfo.totalCount }
  $pg++
} while (($chunk.Count -ge $PageSize) -and ($pg -lt 100) -and (($null -eq $listedTotal) -or ($hosts.Count -lt $listedTotal)))
"HostSystem listed=$($hosts.Count) total=$listedTotal pages=$pg"
if (($null -ne $listedTotal) -and ($hosts.Count -lt $listedTotal)) { "Stopped: vCOps says $listedTotal hosts but only $($hosts.Count) came back."; Stop-Transcript; return }
$hosts = @($hosts | Where-Object { $_.resourceKey.name -like $NameLike })
"in scope ($NameLike)=$($hosts.Count)"
$sk = ($keys | ForEach-Object { 'statKey=' + [Uri]::EscapeDataString($_) }) -join '&'
$maxKeys = @('cpu|usage_average', 'mem|usage_average')
$skMax = ($maxKeys | ForEach-Object { 'statKey=' + [Uri]::EscapeDataString($_) }) -join '&'
$skVm = 'statKey=' + [Uri]::EscapeDataString('cpu|usagemhz_average')
$script:fetchFailed = 0

# ---- VMs per host from the VM properties (v10: no relationships call) ----
$short = { param($n) ("$n".Trim().ToLower() -split '[.]')[0] }
$inScope = @{}; foreach ($v in $hosts) { $inScope[(& $short $v.resourceKey.name)] = @() }
$vmMapErr = $null
try {
  $vmAll = @(); $pg = 0; $vmTotal = $null
  do {
    $rr = Call "$B/resources?resourceKind=VirtualMachine&page=$pg&pageSize=500"
    $chunk = @($rr.resourceList); $vmAll += $chunk
    if ($rr.pageInfo -and ($null -ne $rr.pageInfo.totalCount)) { $vmTotal = [int]$rr.pageInfo.totalCount }
    $pg++
  # keep paging while vCOps says there are more (a server that caps pageSize lower must not stop us at page 1)
  } while (($chunk.Count -gt 0) -and ($pg -lt 100) -and ((($null -eq $vmTotal) -and ($chunk.Count -ge 500)) -or (($null -ne $vmTotal) -and ($vmAll.Count -lt $vmTotal))))
  if (($null -ne $vmTotal) -and ($vmAll.Count -lt $vmTotal)) { throw "vCOps says $vmTotal VMs but only $($vmAll.Count) came back" }
  $vmName = @{}; foreach ($x in $vmAll) { $vmName[[string]$x.identifier] = [string]$x.resourceKey.name }
  $ids = @($vmAll | ForEach-Object { [string]$_.identifier })
  $placed = 0
  for ($k = 0; $k -lt $ids.Count; $k += 50) {
    $q = ($ids[$k..([Math]::Min($k + 50, $ids.Count) - 1)] | ForEach-Object { 'resourceId=' + $_ }) -join '&'
    $res = Call "$B/resources/properties?$q"
    if (-not ($res -and $res.resourcePropertiesList)) { throw 'bulk properties call has no resourcePropertiesList' }
    foreach ($item in @($res.resourcePropertiesList)) {
      $ph = $null
      foreach ($pp in @($item.property)) { if ($pp -and ([string]$pp.name -eq 'summary|parentHost')) { $ph = [string]$pp.value; break } }
      $hk = & $short $ph
      if ($ph -and $inScope.ContainsKey($hk)) { $inScope[$hk] += [string]$item.resourceId; $placed++ }
    }
  }
  "VMs listed=$($vmAll.Count) placed under in-scope hosts=$placed"
} catch { $vmMapErr = 'VM list failed'; "FAILED (vm list) : $($_.Exception.Message)" }

$out = foreach ($v in $hosts) {
  Start-Sleep 1
  $q = "resourceId=$($v.identifier)&$sk&begin=$b2&end=$e2&rollUpType=AVG&intervalType=MINUTES&intervalQuantifier=5"
  $st = $null
  try { $st = Call "$B/resources/stats?$q" } catch { $script:fetchFailed++; "FAILED $($v.resourceKey.name) : $($_.Exception.Message)" }
  $qm = "resourceId=$($v.identifier)&$skMax&begin=$b2&end=$e2&rollUpType=MAX&intervalType=MINUTES&intervalQuantifier=5"
  $stm = $null
  try { $stm = Call "$B/resources/stats?$qm" } catch { $script:fetchFailed++; "FAILED (max) $($v.resourceKey.name) : $($_.Exception.Message)" }
  $vmStats = $null; $vmErr = $null
  if ($vmMapErr) { $vmErr = $vmMapErr }
  else {
    try {
      $vmIds = @($inScope[(& $short $v.resourceKey.name)])
      $vmStats = New-Object System.Collections.ArrayList
      for ($k = 0; $k -lt $vmIds.Count; $k += 40) {
        $part = @($vmIds[$k..([Math]::Min($k + 39, $vmIds.Count - 1))])
        $idq = ($part | ForEach-Object { 'resourceId=' + $_ }) -join '&'
        $sv = Call "$B/resources/stats?$idq&$skVm&begin=$b2&end=$e2&rollUpType=MAX&intervalType=MINUTES&intervalQuantifier=5"
        foreach ($val in @($sv.values)) {
          $rid = [string]$val.resourceId
          [void]$vmStats.Add(@{ name = $vmName[$rid]; id = $rid; stat = @($val.'stat-list'.stat)[0] })
        }
      }
      "VMs $($v.resourceKey.name) : $($vmIds.Count) placed, $($vmStats.Count) with stats"
    } catch { $vmStats = $null; $vmErr = 'VM read failed'; "FAILED (vms) $($v.resourceKey.name) : $($_.Exception.Message)" }
  }
  @{ name = [string]$v.resourceKey.name; id = [string]$v.identifier; stats = $st; statsMax = $stm; vms = $vmStats; vmErr = $vmErr }
}
"vCOps calls=$($script:calls) first failure at call #$(if ($null -eq $script:firstFail) { 'none' } else { $script:firstFail }) (delay $DelayMs ms)"
$hd = $null
$f = $L + '.json'
$out | ConvertTo-Json -Depth 12 | Set-Content $f -Encoding UTF8
"saved $f hosts=$($hosts.Count)"

# ---- relay batch: one JSON per host + manifest.json last ----
# Any failure in this section (or any host whose stats could not be read) means no manifest: the system never sees a half batch.
# vCOps keeps 28 days, so just run it again later (or with -Day for a past day).
$ErrorActionPreference = 'Stop'
$batchOk = $true
function Write-Atomic([string]$Path, [string]$Text) {
  $tmp = $Path + '.tmp'
  [IO.File]::WriteAllText($tmp, $Text, (New-Object System.Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $tmp -Destination $Path -Force
}
$batchId = 'ariahost_' + $Day + '_' + (Get-Date -f yyyyMMddTHHmmss)
$bdir = Join-Path $OutDir $batchId
$files = New-Object System.Collections.ArrayList
$used = @{}
try {
New-Item -ItemType Directory -Force -Path $bdir | Out-Null
foreach ($o in @($out)) {
  $metrics = [ordered]@{}
  $list = @()
  if ($o.stats -and $o.stats.values) { $list = @($o.stats.values[0].'stat-list'.stat) }
  if ($o.statsMax -and $o.statsMax.values) {
    # MAX series get the suffix ':max' so they never overwrite the AVG series of the same key.
    foreach ($s in @($o.statsMax.values[0].'stat-list'.stat)) { if ($maxKeys -contains [string]$s.statKey.key) { $list += [pscustomobject]@{ statKey = [pscustomobject]@{ key = ([string]$s.statKey.key + ':max') }; timestamps = $s.timestamps; data = $s.data } } }
  }
  foreach ($s in $list) {
    $k = [string]$s.statKey.key
    if (($keys -notcontains $k) -and (($maxKeys | ForEach-Object { $_ + ':max' }) -notcontains $k)) { continue }
    # Missing values stay null (never 0): casting $null to a number would turn a gap into a real 0 point.
    $tt = New-Object System.Collections.ArrayList; $vv = New-Object System.Collections.ArrayList
    $ta = @($s.timestamps); $da = @($s.data)
    if ($ta.Count -ne $da.Count) { throw ('timestamps and data differ in length for ' + $o.name) }
    for ($j = 0; $j -lt $ta.Count; $j++) {
      [void]$tt.Add([int64]$ta[$j])
      $x = $da[$j]
      if (($null -eq $x) -or ("$x" -eq '') -or ("$x" -eq 'NaN')) { [void]$vv.Add($null) } else { [void]$vv.Add([double]$x) }
    }
    $metrics[$k] = [ordered]@{ t = $tt.ToArray(); v = $vv.ToArray() }
  }
  $dev = [ordered]@{ name = $o.name; resource_id = $o.id; metrics = $metrics }
  if ($null -ne $o.vms) {
    $vl = New-Object System.Collections.ArrayList
    foreach ($w in @($o.vms)) {
      if (-not $w.name) { continue }
      $tt = New-Object System.Collections.ArrayList; $vv = New-Object System.Collections.ArrayList
      $ta = @(); $da = @(); if ($w.stat) { $ta = @($w.stat.timestamps); $da = @($w.stat.data) }
      if ($ta.Count -ne $da.Count) { throw ('VM timestamps and data differ in length for ' + $w.name) }
      for ($j = 0; $j -lt $ta.Count; $j++) {
        [void]$tt.Add([int64]$ta[$j])
        $x = $da[$j]
        if (($null -eq $x) -or ("$x" -eq '') -or ("$x" -eq 'NaN')) { [void]$vv.Add($null) } else { [void]$vv.Add([double]$x) }
      }
      [void]$vl.Add([ordered]@{ name = $w.name; resource_id = $w.id; t = $tt.ToArray(); v = $vv.ToArray() })
    }
    $dev.vms = $vl.ToArray()
  } elseif ($o.vmErr) {
    $dev.vms_error = $o.vmErr
  }
  $base = ($o.name -replace '[^A-Za-z0-9._-]', '_').TrimStart('.', '_', '-')
  if (-not $base) { $base = 'host' }
  if ($base.Length -gt 100) { $base = $base.Substring(0, 100) }
  $fname = $base + '.json'; $i = 1
  while ($used.ContainsKey($fname.ToLower())) { $i++; $fname = $base + '_' + $i + '.json' }
  $used[$fname.ToLower()] = 1
  $final = Join-Path $bdir $fname
  Write-Atomic $final (ConvertTo-Json -InputObject $dev -Depth 8 -Compress)
  [void]$files.Add([ordered]@{ name = $fname; sha256 = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $final).Length })
}
} catch {
  $batchOk = $false
  'Stopped writing the batch: ' + $_.Exception.Message + ' (no manifest was written; run it again).'
}
$ErrorActionPreference = 'Continue'
if (-not $batchOk) {
  'Batch not finished, so no manifest.'
} elseif ($script:fetchFailed -gt 0) {
  "No manifest: $($script:fetchFailed) host(s) failed to read; run it again later (vCOps keeps 28 days)."
} elseif ($files.Count -eq 0) {
  'No host in scope, so no manifest was written (the system will ignore this empty folder).'
} else {
  $manifest = [ordered]@{
    source = 'aria_host'
    batch_id = $batchId
    schema_version = 1
    collector_version = $ScriptVersion
    collected_at = [DateTimeOffset]::UtcNow.ToOffset($tz8).ToString('yyyy-MM-ddTHH:mm:sszzz')
    time_zone = 'Asia/Taipei'
    range = [ordered]@{ from = $Day; to = $Day }
    scope = $NameLike
    listed_total = $listedTotal
    data_until = [DateTimeOffset]::FromUnixTimeMilliseconds($e2).ToOffset($tz8).ToString('yyyy-MM-ddTHH:mm:sszzz')
    count = $files.Count
    files = $files.ToArray()
  }
  try {
    $ErrorActionPreference = 'Stop'
    Write-Atomic (Join-Path $bdir 'manifest.json') (ConvertTo-Json -InputObject $manifest -Depth 8 -Compress)
    "BATCH $batchId : $($files.Count) hosts -> $bdir"
  } catch { 'Stopped: manifest could not be written: ' + $_.Exception.Message }
  $ErrorActionPreference = 'Continue'
}

# ---- console report (Top10 + all + CSV), same as before ----
$rep = Join-Path $PSScriptRoot 'aria_host_report.ps1'
if (Test-Path -LiteralPath $rep) { . $rep }
Stop-Transcript
`,
      },
    ],
    failHow: '畫面上 Stopped:／No manifest 開頭那一行（不含帳密）原文帶回；vCOps 保留 28 天，晚一點重跑或加 -Day 補跑前幾天。',
    expect: '腳本旁 aria_relay 資料夾裡一個 ariahost_ 開頭的批次資料夾：每台 ESXi 一個 JSON，最後是 manifest.json（source=aria_host）。',
    returnHow: '把批次資料夾複製到中繼 PC 的上傳資料夾，用 relay_push 上傳；系統收盤後自動匯入（盤中暫停）。',
    notes: [
      'v7 起輸出寫在腳本旁（不再寫 Downloads），-AriaHost 必填。',
      '舊版（沒有最高值的批次）系統照樣收，只是開盤尖峰退回 5 分鐘平均的最高，會比主機組報告低。',
    ],
  },
  {
    id: 'D11',
    tag: 'WhatsUp',
    title: 'WhatsUp 上次輪循收集腳本（中繼檔案，每 30 分鐘，PowerShell，只讀）',
    purpose: '1.5 每日檢查的 CPU／記憶體／硬碟／輪詢四列改看 WhatsUp「上次輪循」當下值（跟人工檢查表一樣），範圍改用 WhatsUp 群組。每次對幾個群組各問一次成員與 CPU、記憶體、磁碟的上次輪循值（約 20 次呼叫），輸出一個批次資料夾。',
    where: '能連到 WhatsUp Gold 的公司中繼 Windows PC（Windows PowerShell 5.1），和 D7 告警收集同一台、同一個資料夾；不是系統主機',
    perm: '唯讀：WhatsUp 只做 GET（外加一次登入取得權杖），帳密沿用 D4 的 wug_account.txt；本機只寫輸出資料夾（-SaveRaw／-ProbeSwap 才多寫原始回應資料夾）。不連任何主機',
    ver: 4,
    commands: [
      {
        label: '腳本 wug_lastpoll_collect.ps1（按「下載腳本」存成檔案，放到中繼資料夾，和 wug_alert_collect.ps1、wug_account.txt 同一個資料夾）。第一次手動試跑並存原始回應：powershell -NoProfile -File wug_lastpoll_collect.ps1 -Base https://<WhatsUp主機名稱>:9644 -OutDir wug_lp_out -SaveRaw -ProbeSwap（-Base 一定用憑證上的主機名稱，用 IP 會 SSL 信任失敗；也可以在 wug_account.txt 加一行 base=https://<主機名稱>:9644 之後就不用帶 -Base）。之後排程每 30 分鐘（05:30～18:00）跑一次（不加 -SaveRaw），批次用 relay_push 上傳。',
        file: 'wug_lastpoll_collect.ps1',
        text: `# WUG-LASTPOLL-COLLECT-SCRIPT v4 : WhatsUp Gold read-only "last polled" values per group, for the WEBIT3 daily check (GET only, plus one token login)
# What it does: asks WhatsUp the same thing the morning checker reads on screen with time = "last polled":
#   CPU, memory and disk utilization of every device in a few WhatsUp groups (sub groups included), plus the member list of each group.
# One run = 1 login + 1 group list + per group (members + cpu + memory + disk), every list read page by page until there is no next page.
# Writes one batch folder OutDir/wuglp_<time>/ with snap.json, then manifest.json last (the system only ever sees a finished batch).
# v2 (first real run): every group has its own call budget, sized from its member count (a big group can no longer use up the calls of the
#   others); a report that hits its page cap keeps the pages it read and is marked partial (pages read, rows kept), not failed;
#   reports are sorted by percent, highest first (WhatsUp default), so a partial report still has the highest values.
#   Members: when the one-shot member list fails (HTTP 500 on very big groups), it pages with a smaller page size, then reads every sub group
#   one by one (no hierarchy) and joins them. Group names are compared after removing spaces and turning full-width brackets into
#   half-width ones; configured names that match nothing are listed with similar WhatsUp names.
# v3 (second real run): the group CPU report stops at about 100 devices (150 rows) with no next page, whatever the group size.
#   When one CPU query returns -CpuCapDevices devices or more, it is split: the group's own devices (no hierarchy) plus every
#   child group (again split when needed), joined by device; a leaf that still hits the cap is PARTIAL "WhatsUp report cap", never
#   complete. Any report covering fewer than half of the members (no next page) gets a low coverage note.
#   Swap probe: the old monitor list call used view=details, which WhatsUp refuses (HTTP 400); now view=basic, plus the device
#   memory report (lastPolled) to see whether swap shows up there.
# Anything that fails (group not found, a name that matches two groups, a report call that errors before the first page) is written
# into that group / metric as ok = false with the reason; the other groups still run. The system marks that batch not complete
# and the daily check shows "no data" for the rows that need it, never "normal".
# Group names: -Groups, or -GroupsFile (one name per line, UTF-8, lines starting with # are skipped), or wug_lp_groups.txt next to this
#   script, or the built-in default. A name is the group's own name (must match exactly one group) or its full path with ' > ' between levels.
# -Base: the WhatsUp address with the host name that is in its certificate (an IP gives an SSL trust error). When -Base is not given,
#   the line base=https://<host>:<port> in wug_account.txt is used.
# -SaveRaw     also writes every raw reply to RawDir (for the first run: compare with the WhatsUp screen). Off by default.
# -ProbeSwap   also reads the performance monitor list and the memory report of one device (-ProbeDeviceId, default: a device with a
#              swap memory row, else the first member) into RawDir, to see whether Linux_SWAP has a value over REST. Not parsed. Off by default.
# v4: optional device status check for a short list of devices (the 1.5 Banqiao rows): wug_lp_devices.txt next to this script
#   (or -DevicesFile), one IP or WhatsUp device id per line, optional ',name'. Per device: find it (IP: device search, exact address;
#   id: device overview), its status (worst / best state, down monitors) and its last poll time (device ping report, lastPolled).
#   Written as devices.json in the same batch; a device that cannot be found or read is marked ok = false with the reason.
# Runs every 30 minutes on the relay Windows host (PowerShell 5.1). It only reads WhatsUp; it never connects to any host.
# Example:  powershell -NoProfile -File wug_lastpoll_collect.ps1 -Base https://wug-host.example.test:9644 -OutDir wug_lp_out
param(
  [string]$Base = '',
  [string]$OutDir = 'wug_lp_out',
  [string[]]$Groups = @(),
  [string]$GroupsFile = '',
  [string]$RawDir = 'wug_lp_raw',
  [switch]$SaveRaw,
  [switch]$ProbeSwap,
  [string]$ProbeDeviceId = '',
  [int]$TimeBoxSeconds = 1200,
  [int]$MaxCalls = 3000,
  [int]$GroupMaxCalls = 0,
  [int]$MaxPagesPerReport = 400,
  [int]$MemberPageSize = 200,
  [int]$CpuCapDevices = 100,
  [string]$DevicesFile = ''
)
$ErrorActionPreference = 'Stop'
$ScriptVersion = 'wug_lastpoll_collect 4'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Sep = ' > '
$RowsPerPage = 50

# Default group names, built from code points so this file stays plain ASCII (PowerShell 5.1 reads BOM-less files as ANSI).
function U([int[]]$c) { return (-join ([char[]]$c)) }
if ((@($Groups).Count -eq 0) -and (-not $GroupsFile)) {
  $gf = Join-Path $PSScriptRoot 'wug_lp_groups.txt'
  if (Test-Path -LiteralPath $gf) { $GroupsFile = $gf }
}
if ($GroupsFile) {
  $Groups = @([IO.File]::ReadAllLines((Resolve-Path -LiteralPath $GroupsFile).Path, (New-Object System.Text.UTF8Encoding($false))) | ForEach-Object { $_.Trim() } | Where-Object { $_ -and (-not $_.StartsWith('#')) })
}
if (@($Groups).Count -eq 0) {
  $Groups = @(
    ('1-xServer (' + (U 0x91CD,0x8981,0x7CFB,0x7D71) + '/' + (U 0x4EA4,0x6613) + ')'),
    'Server',
    ('5-Infra(' + (U 0x4E3B,0x6A5F,0x985E) + '_Windows/Linux)')
  )
}

# Credentials: the same wug_account.txt as wug_collect.ps1 (username=... / password=..., optional base=...), readable only by this account.
# Nothing secret is written out.
$cfg = Join-Path $PSScriptRoot 'wug_account.txt'
if (-not (Test-Path -LiteralPath $cfg)) { Write-Host ('Missing ' + $cfg + ' - create it with Notepad: two lines username=... and password=..., readable only by this account; then run again.'); exit 1 }
$kv = @{}
foreach ($l in Get-Content -LiteralPath $cfg) { $i = $l.IndexOf('='); if ($i -gt 0) { $kv[$l.Substring(0, $i).Trim()] = $l.Substring($i + 1) } }
$user = $kv['username']; $pass = $kv['password']
if ((-not $Base) -and $kv['base']) { $Base = $kv['base'].Trim() }
$kv = $null
if (-not $user -or -not $pass) { Write-Host ('username or password is empty in ' + $cfg); exit 1 }
if (-not $Base) { Write-Host 'No WhatsUp address: pass -Base https://<WhatsUp host name>:<port> or add a line base=https://<host name>:<port> to wug_account.txt.'; exit 1 }
if ($Base -notmatch '^https://[A-Za-z0-9][A-Za-z0-9.-]{0,251}(:[0-9]{1,5})?/?$') { throw 'Base must be an https:// URL with a host name (plain http is refused because the password and token would travel unencrypted).' }
$root = $Base.TrimEnd('/')
if ($root -match '^https://[0-9.]+(:[0-9]+)?$') { Write-Host 'WARNING: -Base is an IP address; the WhatsUp certificate is issued to its host name, so use the host name if the login fails with an SSL / trust error.' }

function Write-Atomic([string]$Path, [string]$Text) {
  $tmp = $Path + '.tmp'
  [IO.File]::WriteAllText($tmp, $Text, (New-Object System.Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Short([string]$s) { if ($s.Length -gt 300) { return $s.Substring(0, 300) } return $s }
# Group name compare key: no spaces (half-width, full-width, no-break, tab), full-width brackets / slash / underscore as half-width, lower case.
function Norm([string]$s) {
  if ($null -eq $s) { return '' }
  $t = $s.Replace([string][char]0x3000, ' ').Replace([string][char]0x00A0, ' ').Replace([string][char]9, ' ')
  $t = $t.Replace([string][char]0xFF08, '(').Replace([string][char]0xFF09, ')').Replace([string][char]0xFF0F, '/').Replace([string][char]0xFF3F, '_')
  return ($t -replace '[ ]+', '').ToLowerInvariant()
}

$body = @{ grant_type = 'password'; username = $user; password = $pass }
try {
  $tok = Invoke-RestMethod -Method Post -Uri ($root + '/api/v1/token') -Body $body -TimeoutSec 30
} catch {
  Write-Host ('Login failed: ' + $_.Exception.Message)
  exit 1
}
$body = $null
$pass = $null
$headers = @{ Authorization = ('Bearer ' + $tok.access_token) }
$tok = $null

$startedAt = Get-Date
$sw = [Diagnostics.Stopwatch]::StartNew()
$stamp = $startedAt.ToString('yyyyMMddTHHmmss')
$script:Calls = 0
$script:GCalls = 0
$script:GBudget = 0
$script:RawN = 0
if ($SaveRaw -or $ProbeSwap) { New-Item -ItemType Directory -Force -Path $RawDir | Out-Null }
function Save-Raw([string]$Label, $Obj) {
  $script:RawN++
  $f = Join-Path $RawDir ('raw_' + $stamp + '_' + $script:RawN.ToString('0000') + '_' + $Label + '.json')
  Write-Atomic $f ($Obj | ConvertTo-Json -Depth 12 -Compress)
}
# One GET. Three limits: the whole run (MaxCalls), the current group (its budget), and the time box.
function Get-Wug([string]$Path, [string]$Label) {
  if ($script:Calls -ge $MaxCalls) { throw ('run call cap ' + $MaxCalls + ' reached') }
  if (($script:GBudget -gt 0) -and ($script:GCalls -ge $script:GBudget)) { throw ('group call budget ' + $script:GBudget + ' reached') }
  if ($sw.Elapsed.TotalSeconds -gt $TimeBoxSeconds) { throw ('time box ' + $TimeBoxSeconds + ' s reached') }
  $script:Calls++
  $script:GCalls++
  $x = Invoke-RestMethod -Uri ($root + $Path) -Headers $headers -TimeoutSec 120
  if ($SaveRaw) { Save-Raw $Label $x }
  return $x
}
function Http-Code($e) {
  # The HTTP status of a failed call, 0 when there is none (time out, name error); only used for the message.
  $code = 0
  try { if ($e.Exception.Response) { $code = [int]$e.Exception.Response.StatusCode } } catch { $code = 0 }
  return $code
}
# Pages of one list until there is no next page, at most MaxP pages. Never throws: returns what it read.
#   complete = every page read; partial = some pages read, then a cap / error stopped it (rows kept); pages = 0 with error = nothing read.
# Inner = '' when data itself is the list (reports), else the property under data (devices, groups).
function Get-Pages([string]$Path, [string]$Inner, [string]$Label, [int]$MaxP) {
  $res = @{ rows = (New-Object System.Collections.ArrayList); pages = 0; complete = $false; error = ''; code = 0 }
  $pg = ''
  $seen = @{}
  while ($true) {
    if ($res.pages -ge $MaxP) { $res.error = 'page cap ' + $MaxP + ' reached, more pages left'; break }
    $q = $Path
    if ($pg) { $q += '&pageId=' + [Uri]::EscapeDataString($pg) }
    try { $x = Get-Wug $q $Label } catch { $res.error = Short $_.Exception.Message; $res.code = Http-Code $_; break }
    $res.pages++
    if (-not $x.PSObject.Properties['data']) { $res.error = 'response has no data'; break }
    if ($Inner) {
      if ((-not $x.data) -or (-not $x.data.PSObject.Properties[$Inner])) { $res.error = 'response has no data.' + $Inner; break }
      $list = $x.data.$Inner
    } else { $list = $x.data }
    foreach ($r in @($list)) { if ($r) { [void]$res.rows.Add($r) } }
    $nx = ''
    if ($x.PSObject.Properties['paging'] -and $x.paging) { $nx = [string]$x.paging.nextPageId }
    if (-not $nx) { $res.complete = $true; break }
    if ($seen.ContainsKey($nx)) { $res.error = 'next page id repeated: ' + $nx; break }
    $seen[$nx] = 1
    $pg = $nx
  }
  return $res
}

# 1) all groups -> full path of each (parent chain), to turn a name into exactly one id.
$groupsOk = $true
$groupsErr = ''
$byId = @{}
$kids = @{}
$gl = Get-Pages '/api/v1/device-groups/-?limit=0' 'groups' 'groups' $MaxPagesPerReport
if (-not $gl.complete) { $groupsOk = $false; $groupsErr = $gl.error }
else {
  foreach ($g in $gl.rows) {
    $byId[[string]$g.id] = @{ name = [string]$g.name; parent = [string]$g.parentGroupId }
    $p = [string]$g.parentGroupId
    if (-not $kids.ContainsKey($p)) { $kids[$p] = New-Object System.Collections.ArrayList }
    [void]$kids[$p].Add([string]$g.id)
  }
  if ($byId.Count -eq 0) { $groupsOk = $false; $groupsErr = 'group list was empty' }
}
function Path-Of([string]$Id) {
  $parts = @()
  $cur = $Id
  $guard = 0
  while ($cur -and $byId.ContainsKey($cur) -and ($guard -lt 50)) {
    $parts = @($byId[$cur].name) + $parts
    $cur = $byId[$cur].parent
    $guard++
  }
  return ($parts -join $Sep)
}
function Tree-Of([string]$Id) {
  $ids = New-Object System.Collections.ArrayList
  $todo = New-Object System.Collections.Queue
  $todo.Enqueue($Id)
  while (($todo.Count -gt 0) -and ($ids.Count -lt 5000)) {
    $c = [string]$todo.Dequeue()
    if ($ids.Contains($c)) { continue }
    [void]$ids.Add($c)
    if ($kids.ContainsKey($c)) { foreach ($k in $kids[$c]) { $todo.Enqueue($k) } }
  }
  return ,$ids.ToArray()
}
$paths = @{}
foreach ($k in $byId.Keys) { $paths[$k] = Path-Of $k }

# Every id-like field the reply has is kept (id, deviceId when present, the cpu/memory/disk item id, monitorId when present).
# avg / max are written exactly as WhatsUp sends them (avgPercent may be a 0-1 fraction); used / size too when present, so the
# system can tell fraction from percent.
function Row([object]$r, [string]$ItemProp) {
  $item = ''
  if ($ItemProp -and $r.PSObject.Properties[$ItemProp]) { $item = [string]$r.$ItemProp }
  $o = [ordered]@{ id = [string]$r.id; name = [string]$r.deviceName; item = $item; poll = [string]$r.pollTimeUtc
                   age = $r.timeFromLastPollSeconds; avg = $r.avgPercent; max = $r.maxPercent }
  if ($r.PSObject.Properties['deviceId']) { $o.device_id = [string]$r.deviceId }
  if ($ItemProp -and $r.PSObject.Properties[$ItemProp + 'Id']) { $o.item_id = [string]$r.($ItemProp + 'Id') }
  if ($r.PSObject.Properties['monitorId']) { $o.monitor_id = [string]$r.monitorId }
  if ($r.PSObject.Properties['avgUsed']) { $o.used = $r.avgUsed }
  if ($r.PSObject.Properties['size']) { $o.size = $r.size }
  return $o
}
# Page caps per report from the member count: cpu one row per device, memory about 2, disk about 6 (first run: memory 1974 rows
# for 1379 members, disk more than 50 pages of 50).
$Reports = @(
  @{ key = 'cpu'; path = 'cpu-utilization'; extra = '&rollupByDevice=true'; item = 'cpu'; per = 1; split = $true },
  @{ key = 'mem'; path = 'memory-utilization'; extra = ''; item = 'memory'; per = 2 },
  @{ key = 'disk'; path = 'disk-utilization'; extra = ''; item = 'disk'; per = 6 }
)
function Page-Cap([int]$Members, [int]$Per) {
  if ($Members -le 0) { return $MaxPagesPerReport }
  $n = [int][Math]::Ceiling(($Members * $Per) / $RowsPerPage) + 5
  if ($n -gt $MaxPagesPerReport) { $n = $MaxPagesPerReport }
  return $n
}
function Dev-Count($Rows) { return @($Rows | ForEach-Object { [string]$_.id } | Sort-Object -Unique).Count }
function Add-Rows($Acc, $Rows, [string]$ItemProp) {
  foreach ($r in $Rows) {
    # Only an exact repeat (same device, item, poll time and value: the same row seen through a parent and a child group) is dropped.
    $k = [string]$r.id + '|' + [string]$r.$ItemProp + '|' + [string]$r.($ItemProp + 'Id') + '|' + [string]$r.pollTimeUtc + '|' + [string]$r.avgPercent
    if (-not $Acc.ContainsKey($k)) { $Acc[$k] = $r }
  }
}
# One report for one group, split when WhatsUp stops at its device cap (CPU): the group's own devices plus each child group.
# St: pages, capped (group paths still at the cap), error (first error), broken (a call stopped early).
function Read-Split([string]$Gid, $Rp, [string]$Label, [int]$MaxP, $Acc, $St, [int]$Depth) {
  $base = '/api/v1/device-groups/' + $Gid + '/devices/reports/' + $Rp.path + '?range=lastPolled&returnHierarchy='
  $r = Get-Pages ($base + 'true' + $Rp.extra + '&limit=0') '' $Label $MaxP
  $St.pages += $r.pages
  Add-Rows $Acc $r.rows $Rp.item
  if (-not $r.complete) {
    $St.broken = $true
    if (-not $St.error) { $St.error = $r.error; $St.code = $r.code }
    return
  }
  if ((-not $Rp.split) -or ((Dev-Count $r.rows) -lt $CpuCapDevices)) { return }
  $ch = @()
  if ($kids.ContainsKey($Gid)) { $ch = @($kids[$Gid]) }
  if (($ch.Count -eq 0) -or ($Depth -ge 12)) { [void]$St.capped.Add($paths[$Gid]); return }
  $d = Get-Pages ($base + 'false' + $Rp.extra + '&limit=0') '' ($Label + '_own') $MaxP
  $St.pages += $d.pages
  Add-Rows $Acc $d.rows $Rp.item
  if (-not $d.complete) { $St.broken = $true; if (-not $St.error) { $St.error = $d.error; $St.code = $d.code } }
  elseif ((Dev-Count $d.rows) -ge $CpuCapDevices) { [void]$St.capped.Add($paths[$Gid] + ' (own devices)') }
  foreach ($c in $ch) { Read-Split $c $Rp ($Label + '_' + $c) $MaxP $Acc $St ($Depth + 1) }
}
function Add-Members($Acc, $Rows) {
  foreach ($d in $Rows) {
    $did = [string]$d.id
    if ((-not $did) -or $Acc.ContainsKey($did)) { continue }
    $mo = [ordered]@{ id = $did; name = [string]$d.name; ip = [string]$d.networkAddress }
    if ($d.PSObject.Properties['hostName']) { $mo.host = [string]$d.hostName }
    $Acc[$did] = $mo
  }
}

$out = New-Object System.Collections.ArrayList
$counts = New-Object System.Collections.ArrayList
$unmatched = New-Object System.Collections.ArrayList
$gi = 0
foreach ($want in $Groups) {
  $gi++
  $script:GCalls = 0
  $script:GBudget = 0
  $grp = [ordered]@{ query = $want; path = $want; id = $null; ok = $false; error = ''; members_via = ''; members = @()
                     metrics = [ordered]@{ cpu = [ordered]@{ ok = $false; partial = $false; pages = 0; error = 'not read'; rows = @() }
                                           mem = [ordered]@{ ok = $false; partial = $false; pages = 0; error = 'not read'; rows = @() }
                                           disk = [ordered]@{ ok = $false; partial = $false; pages = 0; error = 'not read'; rows = @() } } }
  $cnt = [ordered]@{ path = $want; members = 0; cpu = 0; mem = 0; disk = 0; calls = 0; budget = 0 }
  if (-not $groupsOk) { $grp.error = 'group list failed: ' + $groupsErr }
  else {
    $nw = Norm $want
    if ($want.Contains($Sep.Trim())) {
      $hits = @($paths.Keys | Where-Object { $paths[$_] -eq $want })
      if ($hits.Count -eq 0) { $hits = @($paths.Keys | Where-Object { (Norm $paths[$_]) -eq $nw }) }
    } else {
      $hits = @($byId.Keys | Where-Object { $byId[$_].name -eq $want })
      if ($hits.Count -eq 0) { $hits = @($byId.Keys | Where-Object { (Norm $byId[$_].name) -eq $nw }) }
    }
    if ($hits.Count -eq 0) {
      $key = $nw
      $cut = $key.IndexOf('(')
      if ($cut -gt 2) { $key = $key.Substring(0, $cut) }
      $near = @($byId.Keys | Where-Object { (Norm $byId[$_].name).Contains($key) } | ForEach-Object { $byId[$_].name } | Sort-Object -Unique | Select-Object -First 8)
      $grp.error = 'group not found'
      if ($near.Count -gt 0) { $grp.error += ' (similar WhatsUp names: ' + (Short ($near -join ' | ')) + ')' }
      [void]$unmatched.Add($want + ' -> ' + $grp.error)
    }
    elseif ($hits.Count -gt 1) { $grp.error = 'name matches ' + $hits.Count + ' groups, use the full path: ' + (Short ((@($hits | ForEach-Object { $paths[$_] }) | Sort-Object) -join ' | ')) }
    else {
      $id = [string]$hits[0]
      $grp.id = $id
      $grp.path = $paths[$id]
      $cnt.path = $grp.path
      # Members: one shot; if that fails, smaller pages; if that fails too, every sub group on its own (no hierarchy), joined by device id.
      $acc = @{}
      $mr = Get-Pages ('/api/v1/device-groups/' + $id + '/devices?returnHierarchy=true&view=overview&limit=0') 'devices' ('members' + $gi) $MaxPagesPerReport
      $via = 'one list'
      if (-not $mr.complete) {
        $first = $mr.error
        $mr = Get-Pages ('/api/v1/device-groups/' + $id + '/devices?returnHierarchy=true&view=overview&limit=' + $MemberPageSize) 'devices' ('members' + $gi) $MaxPagesPerReport
        $via = 'pages of ' + $MemberPageSize + ' (one list failed: ' + (Short $first) + ')'
      }
      if ($mr.complete) { Add-Members $acc $mr.rows }
      else {
        $tree = Tree-Of $id
        $via = 'sub groups one by one (' + $tree.Count + ' groups; the list with sub groups failed: ' + (Short $mr.error) + ')'
        $subErr = ''
        foreach ($sg in $tree) {
          $sr = Get-Pages ('/api/v1/device-groups/' + $sg + '/devices?returnHierarchy=false&view=overview&limit=' + $MemberPageSize) 'devices' ('members' + $gi + '_' + $sg) $MaxPagesPerReport
          if (-not $sr.complete) { $subErr = 'sub group ' + $paths[$sg] + ': ' + $sr.error; break }
          Add-Members $acc $sr.rows
        }
        if ($subErr) { $grp.error = 'members: ' + (Short $subErr) }
      }
      $grp.members_via = $via
      if (-not $grp.error) {
        $grp.members = @($acc.Values)
        $grp.ok = $true
        $cnt.members = $acc.Count
      }
      # Members failed: the reports are not read (the system cannot use a group without its members); the calls stay for the other groups.
      if (-not $grp.ok) { foreach ($k in @('cpu', 'mem', 'disk')) { $grp.metrics[$k].error = 'not read: members failed' } }
      else {
        # Report budget for this group: the page caps from the member count (or GroupMaxCalls when given).
        $caps = @{}
        $sum = 0
        foreach ($rp in $Reports) { $caps[$rp.key] = Page-Cap $acc.Count $rp.per; $sum += $caps[$rp.key] }
        $sum += 3 * (Tree-Of $id).Count                                   # CPU split: own devices + each child group
        if ($GroupMaxCalls -gt 0) { $script:GBudget = $GroupMaxCalls } else { $script:GBudget = $script:GCalls + $sum }
        $cnt.budget = $script:GBudget
        foreach ($rp in $Reports) {
          $m = $grp.metrics[$rp.key]
          $racc = @{}
          $st = @{ pages = 0; capped = (New-Object System.Collections.ArrayList); error = ''; code = 0; broken = $false }
          Read-Split $id $rp ($rp.key + $gi) $caps[$rp.key] $racc $st 0
          $rows = New-Object System.Collections.ArrayList
          foreach ($r in $racc.Values) { [void]$rows.Add((Row $r $rp.item)) }
          $m.pages = $st.pages
          $m.rows = @($rows.ToArray())
          $cnt[$rp.key] = $rows.Count
          $devs = Dev-Count $m.rows
          if ((-not $st.broken) -and ($st.capped.Count -eq 0)) {
            $m.ok = $true
            $m.error = ''
            if (($acc.Count -gt 0) -and ($devs -lt ($acc.Count * 0.5))) { $m.note = 'low coverage: ' + $devs + ' devices of ' + $acc.Count + ' members (no next page)' }
          }
          elseif ($rows.Count -gt 0) {
            $m.ok = $false
            $m.partial = $true
            if ($st.capped.Count -gt 0) { $m.error = 'partial: WhatsUp report cap (' + $CpuCapDevices + ' devices per query) in ' + (Short ((@($st.capped) | Sort-Object) -join ' | ')) }
            else { $m.error = 'partial: ' + $st.pages + ' pages read; ' + $st.error }
          }
          else {
            $m.ok = $false
            $m.error = $st.error
            if ($st.code) { $m.error = 'HTTP ' + $st.code + ': ' + $st.error }
          }
        }
      }
    }
  }
  $cnt.calls = $script:GCalls
  [void]$out.Add($grp)
  [void]$counts.Add($cnt)
}
$script:GBudget = 0

# Optional: the monitor list of one device, to see if Linux_SWAP (a custom performance monitor) has a value over REST. Saved raw, not parsed.
$probe = ''
if ($ProbeSwap) {
  $pid2 = $ProbeDeviceId
  if (-not $pid2) {
    foreach ($g in $out) { foreach ($r in @($g.metrics.mem.rows)) { if ((-not $pid2) -and ([string]$r.item -like '*swap*')) { $pid2 = [string]$r.id } } }
  }
  if (-not $pid2) {
    foreach ($g in $out) { if ((-not $pid2) -and (@($g.members).Count -gt 0)) { $pid2 = [string]@($g.members)[0].id } }
  }
  if ($pid2) {
    # view must be id / minimum / basic / status (view=details gave HTTP 400 on the second real run).
    $probe = 'device ' + $pid2 + ':'
    foreach ($pp in @(@{ n = 'monitors'; q = ('/api/v1/devices/' + $pid2 + '/monitors/-?type=performance&view=basic&limit=0') },
                      @{ n = 'memory'; q = ('/api/v1/devices/' + $pid2 + '/reports/memory-utilization?range=lastPolled&limit=0') })) {
      try {
        $x = Get-Wug $pp.q ('probe_' + $pp.n)
        $f = Join-Path $RawDir ('probe_swap_' + $stamp + '_' + $pid2 + '_' + $pp.n + '.json')
        Write-Atomic $f ($x | ConvertTo-Json -Depth 12 -Compress)
        $probe += ' ' + $pp.n + ' saved ' + $f + ';'
      } catch {
        $code = Http-Code $_
        $probe += ' ' + $pp.n + ' failed (' + $(if ($code) { 'HTTP ' + $code } else { 'no HTTP status' }) + ', ' + $pp.q + '): ' + (Short $_.Exception.Message) + ';'
      }
    }
  } else { $probe = 'probe skipped: no device id (no group has members)' }
}

# Optional: status of a few listed devices (1.5 Banqiao rows). Own budget: 4 calls per device + 5.
$devOut = New-Object System.Collections.ArrayList
$devList = @()
if (-not $DevicesFile) {
  $df = Join-Path $PSScriptRoot 'wug_lp_devices.txt'
  if (Test-Path -LiteralPath $df) { $DevicesFile = $df }
}
if ($DevicesFile) {
  $devList = @([IO.File]::ReadAllLines((Resolve-Path -LiteralPath $DevicesFile).Path, (New-Object System.Text.UTF8Encoding($false))) | ForEach-Object { $_.Trim() } | Where-Object { $_ -and (-not $_.StartsWith('#')) })
}
if ($devList.Count -gt 0) {
  $script:GCalls = 0
  $script:GBudget = 4 * $devList.Count + 5
  foreach ($line in $devList) {
    $parts = $line.Split(',')
    $key = $parts[0].Trim()
    $label = ''
    if ($parts.Count -gt 1) { $label = $parts[1].Trim() }
    $o = [ordered]@{ query = $key; label = $label; ip = ''; id = ''; name = ''; ok = $false; error = ''; worst_state = ''; best_state = ''
                     monitors_down = $null; down_monitors = @(); last_change = ''; poll = ''; age = $null; collected_at = '' }
    try {
      $addr = $null
      if ($key.Contains('.') -and [Net.IPAddress]::TryParse($key, [ref]$addr)) {
        $o.ip = $key
        $r = Get-Wug ('/api/v1/device-groups/-1/devices?search=' + $key + '&view=overview&limit=50') 'dev_search'
        $hits = @(@($r.data.devices) | Where-Object { $_ -and ([string]$_.networkAddress -eq $key) })
        if ($hits.Count -eq 0) { throw 'device not found by IP (device search, exact network address)' }
        if ($hits.Count -gt 1) { throw ('IP matches ' + $hits.Count + ' devices: ' + ((@($hits | ForEach-Object { [string]$_.id }) | Sort-Object) -join ' ')) }
        $d = $hits[0]
      } else {
        $x = Get-Wug ('/api/v1/devices/' + [Uri]::EscapeDataString($key) + '?view=overview') 'dev_get'
        $d = $x.data
        if (-not $d) { throw 'device not found by id' }
        $o.ip = [string]$d.networkAddress
      }
      $o.id = [string]$d.id
      $o.name = [string]$d.name
      if ($d.PSObject.Properties['totalActiveMonitorsDown']) { $o.monitors_down = [int]$d.totalActiveMonitorsDown }
      if ($d.PSObject.Properties['downActiveMonitors']) { $o.down_monitors = @(@($d.downActiveMonitors) | Where-Object { $_ } | ForEach-Object { [string]$_.monitorTypeName } | Select-Object -First 10) }
      $st = Get-Wug ('/api/v1/devices/' + $o.id + '/status') 'dev_status'
      $o.worst_state = [string]$st.data.worstState
      $o.best_state = [string]$st.data.bestState
      $o.last_change = [string]$st.data.lastChangeUtc
      $o.collected_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
      $o.ok = $true
      # Last poll: the device ping response time report at lastPolled (pollTimeUtc, timeFromLastPollSeconds); the newest row wins.
      try {
        $pr = Get-Wug ('/api/v1/devices/' + $o.id + '/reports/ping-response-time?range=lastPolled&limit=0') 'dev_ping'
        $best = $null
        foreach ($row in @($pr.data)) {
          if ($row -and $row.PSObject.Properties['timeFromLastPollSeconds'] -and ($null -ne $row.timeFromLastPollSeconds)) {
            if (($null -eq $best) -or ($row.timeFromLastPollSeconds -lt $best.timeFromLastPollSeconds)) { $best = $row }
          }
        }
        if ($best) { $o.poll = [string]$best.pollTimeUtc; $o.age = $best.timeFromLastPollSeconds }
        else { $o.error = 'no last poll time in the ping report' }
      } catch { $o.error = 'last poll: ' + (Short $_.Exception.Message) }
    } catch { $o.ok = $false; $o.error = Short $_.Exception.Message }
    [void]$devOut.Add($o)
  }
  $script:GBudget = 0
}
$headers = $null

$batchId = 'wuglp_' + $stamp
$dir = Join-Path $OutDir $batchId
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$snap = [ordered]@{ groups = @($out.ToArray()) }
$fname = 'snap.json'
$final = Join-Path $dir $fname
Write-Atomic $final ($snap | ConvertTo-Json -Depth 8 -Compress)
$item = [ordered]@{ name = $fname; sha256 = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $final).Length }
$items = @($item)
if ($devOut.Count -gt 0) {
  $dfin = Join-Path $dir 'devices.json'
  Write-Atomic $dfin ([ordered]@{ devices = @($devOut.ToArray()) } | ConvertTo-Json -Depth 6 -Compress)
  $items += [ordered]@{ name = 'devices.json'; sha256 = (Get-FileHash -LiteralPath $dfin -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $dfin).Length }
}
$day = $startedAt.ToString('yyyy-MM-dd')
$manifest = [ordered]@{
  source = 'whatsup_lastpoll'
  batch_id = $batchId
  schema_version = 1
  collector_version = $ScriptVersion
  collected_at = $startedAt.ToString('yyyy-MM-ddTHH:mm:sszzz')
  time_zone = 'UTC'
  range = [ordered]@{ from = $day; to = $day }
  count = $items.Count
  files = $items
  counts = @($counts.ToArray())
  devices = $devOut.Count
  calls = $script:Calls
  elapsed_s = [int]$sw.Elapsed.TotalSeconds
}
# manifest.json is written last, so the system only ever sees a finished batch.
Write-Atomic (Join-Path $dir 'manifest.json') ($manifest | ConvertTo-Json -Depth 6 -Compress)

# Summary: per group members, rows / pages per report (complete, PARTIAL or FAILED), calls used of its budget.
function State($m) { if ($m.ok) { return 'complete' } if ($m.partial) { return 'PARTIAL' } return 'FAILED' }
$bad = @($out | Where-Object { (-not $_.ok) -or (-not $_.metrics.cpu.ok) -or (-not $_.metrics.mem.ok) -or (-not $_.metrics.disk.ok) }).Count
Write-Host ('BATCH ' + $batchId + ' : groups ' + $out.Count + ', with problems ' + $bad + ', calls ' + $script:Calls + ', seconds ' + [int]$sw.Elapsed.TotalSeconds)
for ($n = 0; $n -lt $out.Count; $n++) {
  $g = $out[$n]
  $c = $counts[$n]
  Write-Host ('  [' + ($n + 1) + '] ' + $g.query + '  =>  ' + $g.path)
  if (-not $g.id) { Write-Host ('      PROBLEM : ' + $g.error); continue }
  $ml = '      members ' + $c.members
  if ($g.members_via) { $ml += ' (via ' + $g.members_via + ')' }
  if (-not $g.ok) { $ml += '  PROBLEM ' + $g.error }
  Write-Host $ml
  foreach ($k in @('cpu', 'mem', 'disk')) {
    $m = $g.metrics[$k]
    $l = '      ' + $k + ' : rows ' + @($m.rows).Count + ', pages ' + $m.pages + ', ' + (State $m)
    if ($m.error) { $l += ' - ' + $m.error }
    if ($m.Contains('note')) { $l += ' - ' + $m.note }
    if ($c.members -gt 0) {
      $devs = @($m.rows | ForEach-Object { $_.id } | Sort-Object -Unique).Count
      $l += ' (devices ' + $devs + ' of ' + $c.members + ' members)'
    }
    Write-Host $l
  }
  Write-Host ('      calls ' + $c.calls + ' of budget ' + $c.budget)
}
if ($unmatched.Count -gt 0) {
  Write-Host ('  Configured groups that match no WhatsUp group (' + $unmatched.Count + '):')
  foreach ($u in $unmatched) { Write-Host ('    - ' + $u) }
}
if ($probe) { Write-Host ('  swap probe: ' + $probe) }
if ($devOut.Count -gt 0) {
  Write-Host ('  Device status (' + $devOut.Count + '):')
  foreach ($o in $devOut) {
    $l = '    - ' + $o.query + ' : '
    if ($o.ok) { $l += 'id ' + $o.id + ' ' + $o.name + ', worst ' + $o.worst_state + ', monitors down ' + $o.monitors_down + ', last poll ' + $o.age + ' s' }
    else { $l += 'FAILED' }
    if ($o.error) { $l += ' - ' + $o.error }
    Write-Host $l
  }
}
exit 0
`,
        ok: '印一行 BATCH wuglp_… : groups 3, with problems 0，下面每個群組：成員數（怎麼讀到的）、cpu／mem／disk 各幾筆幾頁（complete／PARTIAL／FAILED）、用了幾次呼叫／預算；wug_lp_out 底下多一個批次資料夾',
        onFail: 'Login failed：確認網址與 wug_account.txt；PROBLEM … group not found（後面會列相近的 WhatsUp 群組名）／name matches N groups：群組名稱對不上，改 wug_lp_groups.txt 或用 -Groups 傳 WhatsUp 上的完整路徑（層級之間用 > 分隔）；PARTIAL：報表讀到頁數上限，已讀的照收，系統那列標「只讀到部分」；其他錯誤把整段文字帶回',
        bring: '終端機顯示的文字（不含密碼）；第一次加 -SaveRaw 的 wug_lp_raw 資料夾（原始回應，含設備名稱與 IP，只給 PC 端開發者看，不貼到公開地方）',
      },
    ],
    failHow: '任何錯誤：把終端機顯示的整段文字（不含帳密）貼到「整理結果」頁籤去識別後帶回，並在備註寫「失敗」。',
    expect: '一個批次資料夾 wuglp_<時間>：snap.json（每個群組的完整路徑、ID、成員、CPU／記憶體／磁碟的上次輪循值）加上 manifest.json（source=whatsup_lastpoll，帶每個群組各指標筆數，最後寫）。系統匯入後在「1.5 每日檢查 → 設定」看得到最新批次與各群組台數。',
    returnHow: '用 relay_push 上傳（排程）；不用把檔案傳回給開發者。第一次的 -SaveRaw 原始回應由 PC 端開發者對 WhatsUp 畫面核對。',
    notes: [
      '預設群組＝人工檢查表 1-3～1-7，用 WhatsUp 實名：1-xServer (重要系統/交易)、Server、5-Infra(主機類_Windows/Linux)（1-xServer(正式-電券/交易) 已併進 1-xServer (重要系統/交易)）。要改：腳本旁放 wug_lp_groups.txt（一行一個，UTF-8，# 開頭略過），或 -Groups／-GroupsFile。名稱比對時忽略空白、全形括號視同半形。',
      '名稱只對得到一個群組才用；找不到或同名有好幾個，那個群組記失敗（系統那幾列顯示無資料），不會猜。',
      'Linux_SWAP 是 WhatsUp 的自訂效能工具，REST 沒有報表；-ProbeSwap 只把一台設備的監控清單原樣存下來，看有沒有值，系統不解析。',
      'v2（11-13 第一次實跑後）：每個群組依成員數自己算呼叫預算（不再一個群組用光全部 150 次）；報表一頁 50 筆、讀到上限＝PARTIAL 照收；群組成員一次讀回 HTTP 500（Server 這種大群組）就改小頁、再改逐個子群組讀；avgPercent 是 0～1 小數，系統換成百分比。',
      'v3（11-14 第二次實跑後）：WhatsUp 群組 CPU 報表一次最多約 100 台、沒有下一頁 → 自動拆成「群組自己的設備＋每個子群組」再合併；拆到底還是滿 100 台＝PARTIAL（WhatsUp report cap），不會標完整。報表涵蓋設備不到成員一半會加註 low coverage。SWAP 探測改 view=basic（舊的 view=details 被 WhatsUp 回 400），另存該台記憶體報表。',
      'v4：板橋交易所主機的正面證據。腳本旁放 wug_lp_devices.txt（UTF-8，一行一個 IP 或 WhatsUp 設備 ID，可加「,名稱」；內容＝1.5 設定頁「板橋交易所主機」那幾個 IP），每台查 WhatsUp 狀態（worst／best、停止的監控）與最後輪循時間（設備 ping 報表 lastPolled），寫進同一批的 devices.json。查不到的那台標失敗、寫原因，其他照跑。'
    ],
  },
  {
    id: 'D12',
    tag: 'vCOps',
    warn: '排程 06:00、06:20、07:30（一律帶 -CertThumbprint <vROps 憑證指紋> -SkipIfOkToday；前一次已完整就不再跑）；只讀 vROps，不連 ESXi／vCenter',
    title: 'vROps 每日檢查 VMware 段快照腳本（中繼檔案，每天早上，PowerShell，只讀）',
    purpose: '1.5 每日檢查 VMware 四座 vCenter（正本 2-1～2-4）改成系統預填、人確認：一次讀 vROps 的 vCenter 收集器狀態、每台 ESXi 連線／維護、每個資料存放區剩餘空間與掛載台數、目前未解警示，寫成一個批次資料夾，經中繼上傳後系統每分鐘自動匯入。',
    where: '連得到 vROps 的跳板 Windows 主機（Windows PowerShell 5.1），和 D8／D10 同一個資料夾；輸出資料夾再複製到中繼 PC 上傳。不是系統主機',
    perm: '唯讀：vROps 只做 GET，外加一次登入取得權杖；不連任何 ESXi 或 vCenter、不在任何地方建帳號。帳密沿用腳本旁的 aria_account.txt，輸出檔不含密碼或權杖',
    ver: 3,
    commands: [
      {
        label: '腳本 aria_daily_snap.ps1（按「下載腳本」存成檔案，放在 aria_account.txt 同一個資料夾）。第一次手動試跑並存原始回應：powershell -NoProfile -File aria_daily_snap.ps1 -AriaHost <vROps 位址> -CertThumbprint <vROps 憑證指紋> -SaveRaw（指紋在瀏覽器開 vROps 看憑證詳細資料的 SHA-1）。看到 BATCH ariadc_… 而且 failed parts 0 才算成功。之後排程 06:00、06:20、07:30 各跑一次：powershell -NoProfile -File aria_daily_snap.ps1 -AriaHost <vROps 位址> -CertThumbprint <vROps 憑證指紋> -SkipIfOkToday（不加 -SaveRaw）。沒帶 -CertThumbprint 腳本會印警告。',
        file: 'aria_daily_snap.ps1',
        text: `# ARIA-DAILYCHECK-SCRIPT v1 : Aria Operations (vROps) read-only snapshot for the WEBIT3 daily check, VMware part (GET only, plus one token login)
# What it reads (the same things the morning checker looks at in each vCenter, but from vROps):
#   adapters   : every VMWARE adapter instance (one per vCenter) with its last collection time, so a stopped collector is never read as "all fine"
#   hosts      : every HostSystem: connection state and maintenance state (properties) and the time of its latest sample
#   datastores : every Datastore: type (property), total / free capacity and number of hosts (latest stats, metric keys are parameters)
#   alerts     : every ACTIVE alert, kept when its criticality is in -Criticality; control state (OPEN / ASSIGNED / SUPPRESSED) and owner kept,
#                the alert's resource is looked up so the system can put it under the right vCenter
# Every row carries the vROps resource id, the vCenter id (identifier VMEntityVCID) and its own sample time (ms).
# It never connects to ESXi or vCenter and never creates an account anywhere: only vROps suite-api, only GET (login is the one POST).
# Every list is read page by page to the end and checked against pageInfo.totalCount. A category that fails is written with ok = false and
# the reason; the other categories still run. The system then marks the batch not complete and the daily check shows "no data", never "normal".
# Output: OutDir/ariadc_<time>/ with snap.json, then manifest.json last (the system only ever sees a finished batch). Copy it to the relay PC
# like the other aria batches and let relay_push upload it.
# Schedule: 06:00, 06:20 and 07:30 with -SkipIfOkToday -CertThumbprint <thumbprint> (the later runs only retry when the earlier one did not finish complete).
# -SaveRaw also writes every raw reply to RawDir and lists their sha256 in the manifest (first run: compare with the vCenter screens).
# Certificate: -CertThumbprint <SHA-1 hex> accepts only that certificate (recommended). Without it, only a self-signed / unknown-issuer
# certificate (RemoteCertificateChainErrors alone) for exactly AriaHost is accepted; a name mismatch is always refused.
# -SkipIfOkToday: the marker ariadc_last_ok.txt is written only when the batch passes the same completeness checks the system does
# (no category failed or empty, every page total = rows read), so 06:20 / 07:30 retry when 06:00 was not complete.
# Credentials: aria_account.txt next to this script (line 1 user, line 2 password, line 3 auth source optional), same as aria_inventory.ps1.
# Run:  powershell -NoProfile -File aria_daily_snap.ps1 -AriaHost <vROps address>
param(
  [Parameter(Mandatory=$true)][string]$AriaHost,
  [string]$OutDir = '',
  [string]$RawDir = '',
  [switch]$SaveRaw,
  [switch]$SkipIfOkToday,
  [switch]$MountFromRels,
  [string]$CertThumbprint = '',
  [string[]]$Criticality = @('CRITICAL', 'IMMEDIATE', 'WARNING'),
  [string]$VcIdName = 'VMEntityVCID',
  [string]$HostConnKey = 'runtime|connectionState',
  [string]$HostMaintKey = 'runtime|maintenanceState',
  [string]$HostTimeKey = 'cpu|usage_average',
  [string]$DsTypeKey = 'summary|type',
  [string]$DsCapKey = 'capacity|total_capacity',
  [string]$DsFreeKey = 'capacity|available_space',
  [string]$DsHostsKey = 'summary|total_number_hosts',
  [string]$DsLocalKey = 'summary|isLocal',
  [int]$PageSize = 500,
  [int]$Chunk = 50,
  [int]$DelayMs = 1500,
  [int]$MaxCalls = 3000,
  [int]$TimeBoxSeconds = 2700
)
$ScriptVersion = 'aria_daily_snap 1'
if (-not $OutDir) { $OutDir = Join-Path $PSScriptRoot 'aria_relay' }
if (-not $RawDir) { $RawDir = Join-Path $PSScriptRoot 'ariadc_raw' }
$tz8 = New-TimeSpan -Hours 8
$startedAt = [DateTimeOffset]::UtcNow.ToOffset($tz8)
$today = $startedAt.ToString('yyyy-MM-dd')
$stamp = $startedAt.ToString('yyyyMMddTHHmmss')
$marker = Join-Path $PSScriptRoot 'ariadc_last_ok.txt'

function Write-Atomic([string]$Path, [string]$Text) {
  $tmp = $Path + '.tmp'
  [IO.File]::WriteAllText($tmp, $Text, (New-Object System.Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Short([string]$s) { if ($s.Length -gt 300) { return $s.Substring(0, 300) } return $s }

$skip = $false
if ($SkipIfOkToday -and (Test-Path -LiteralPath $marker)) {
  if (([IO.File]::ReadAllText($marker)).Trim() -eq $today) { $skip = $true; Write-Host ('A complete batch was already written today (' + $today + '); nothing to do.') }
}

if (-not $skip) {
$H = $AriaHost
if ($H -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]{0,251}(:[0-9]{1,5})?$') { Write-Host 'AriaHost must be a host name (optionally :port), no scheme or path.'; exit 1 }
[Net.ServicePointManager]::SecurityProtocol = 'Tls12'
[Net.ServicePointManager]::Expect100Continue = $false
# Certificate check, only inside this PowerShell process: pinned thumbprint when given; otherwise a valid chain, or a chain error alone
# (self-signed) for exactly AriaHost. Any name mismatch is refused.
$script:pin = ($CertThumbprint -replace '[^0-9A-Fa-f]', '').ToUpper()
if (-not $script:pin) { Write-Host 'WARNING: no -CertThumbprint given; any self-signed certificate named AriaHost is accepted. Pass -CertThumbprint (SHA-1 of the vROps certificate) in the schedule.' }
$script:hostOnly = ($H -split ':')[0]
[Net.ServicePointManager]::ServerCertificateValidationCallback = {
  param($a, $b, $c, $e)
  if ($script:pin) { return ($null -ne $b) -and ($b.GetCertHashString().ToUpper() -eq $script:pin) }
  if ([string]$e -eq 'None') { return $true }
  return ([string]$e -eq 'RemoteCertificateChainErrors') -and ($a.RequestUri.Host -eq $script:hostOnly)
}
$B = "https://$H/suite-api/api"
$sw = [Diagnostics.Stopwatch]::StartNew()
$script:calls = 0; $script:firstFail = $null; $script:RawN = 0
$raw = New-Object System.Collections.ArrayList
if ($SaveRaw) { New-Item -ItemType Directory -Force -Path $RawDir | Out-Null }

# vROps answers without a charset, so PowerShell 5.1 reads UTF-8 as ISO-8859-1; Fix-Text decodes such text again (from aria_inventory.ps1 v3).
$script:strict = New-Object System.Text.UTF8Encoding($false, $true)
$script:latin1 = [Text.Encoding]::GetEncoding(28591)
function Fix-Text($v) {
  if ($null -eq $v) { return $null }
  $t = [string]$v
  $high = $false
  foreach ($c in $t.ToCharArray()) { $n = [int]$c; if ($n -gt 255) { return $t }; if ($n -ge 128) { $high = $true } }
  if (-not $high) { return $t }
  try { return $script:strict.GetString($script:latin1.GetBytes($t)) } catch { return $t }
}

# ---- login (the only POST) ----
$aria = [ordered]@{ ok = $false; error = '' }
$hd = $null
try {
  $acc = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'aria_account.txt')
  $u = "$($acc[0])".Trim(); $pw = "$($acc[1])"; $src = if ($acc.Count -gt 2) { "$($acc[2])".Trim() } else { '' }
  $tok = $null
  foreach ($s2 in @($src, 'LOCAL')) {
    $bd = @{ username = $u; password = $pw }; if ($s2) { $bd.authSource = $s2 }
    try {
      $tok = Invoke-RestMethod -Method Post -Uri "$B/auth/token/acquire" -ContentType 'application/json' -Headers @{ Accept = 'application/json' } -Body ($bd | ConvertTo-Json) -DisableKeepAlive -ErrorAction Stop
      break
    } catch {
      $code = $null; $r = $_.Exception.Response; if ($r) { try { $code = [int]$r.StatusCode } catch { $code = $null } }
      $aria.error = 'login failed, HTTP status=' + $(if ($code) { $code } else { 'none' })
    }
  }
  $pw = $null; $bd = $null; $acc = $null
  if ($tok) { $hd = @{ Accept = 'application/json'; Authorization = "vRealizeOpsToken $($tok.token)" }; $aria.ok = $true; $aria.error = '' }
  $tok = $null
} catch { $aria.error = 'aria_account.txt: ' + (Short $_.Exception.Message) }
# The output never records the account name, the auth source, the password, the token or the server's error body.

# Every call: wait DelayMs, its own connection (-DisableKeepAlive: vROps drops a keep-alive connection after 100 requests), up to 3 retries.
function Call([string]$Uri, [string]$Label) {
  if ($script:calls -ge $MaxCalls) { throw ('call cap ' + $MaxCalls + ' reached') }
  if ($sw.Elapsed.TotalSeconds -gt $TimeBoxSeconds) { throw ('time box ' + $TimeBoxSeconds + ' s reached') }
  for ($t = 0; $t -le 3; $t++) {
    Start-Sleep -Milliseconds $DelayMs
    $script:calls++
    try {
      $x = Invoke-RestMethod -Uri $Uri -Headers $hd -DisableKeepAlive
      if ($SaveRaw) {
        $script:RawN++
        $f = Join-Path $RawDir ('raw_' + $stamp + '_' + $script:RawN.ToString('0000') + '_' + $Label + '.json')
        Write-Atomic $f ($x | ConvertTo-Json -Depth 12 -Compress)
        [void]$raw.Add([ordered]@{ name = (Split-Path -Leaf $f); sha256 = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLower() })
      }
      return $x
    } catch {
      if ($null -eq $script:firstFail) { $script:firstFail = $script:calls; Write-Host ('first failure at call #' + $script:calls + ': ' + $_.Exception.Message) }
      if ($t -eq 3) { throw }
      try { [void][Net.ServicePointManager]::FindServicePoint([Uri]$Uri).CloseConnectionGroup('') } catch { Write-Host ('could not reset the connection: ' + $_.Exception.Message) }
      Start-Sleep -Seconds (3 * ($t + 1))
    }
  }
}
# A whole list, page by page; returns the items and vROps' own total (pageInfo.totalCount) so the system can check nothing was cut.
function Get-Paged([string]$Path, [string]$Prop, [string]$Label) {
  $all = New-Object System.Collections.ArrayList
  $pg = 0; $total = $null
  while ($true) {
    $sep = '?'; if ($Path.Contains('?')) { $sep = '&' }
    $rr = Call ($B + $Path + $sep + 'page=' + $pg + '&pageSize=' + $PageSize) ($Label + $pg)
    $chunk = @($rr.$Prop)
    foreach ($x in $chunk) { if ($x) { [void]$all.Add($x) } }
    if ($rr.pageInfo -and ($null -ne $rr.pageInfo.totalCount)) { $total = [int]$rr.pageInfo.totalCount }
    $pg++
    if ($chunk.Count -lt $PageSize) { break }
    if (($null -ne $total) -and ($all.Count -ge $total)) { break }
    if ($pg -ge 200) { throw ('more than 200 pages: ' + $Label) }
  }
  return @{ list = $all.ToArray(); total = $total }
}
function VcId($r) {
  foreach ($x in @($r.resourceKey.resourceIdentifiers)) { if ($x -and $x.identifierType -and ([string]$x.identifierType.name -eq $VcIdName)) { return [string]$x.value } }
  return ''
}
function Chunks($ids) {
  $out = New-Object System.Collections.ArrayList
  for ($i = 0; $i -lt $ids.Count; $i += $Chunk) { [void]$out.Add(@($ids[$i..([Math]::Min($i + $Chunk, $ids.Count) - 1)])) }
  return ,$out.ToArray()
}
# Latest properties of many resources: {id: {name: value}} (GET /resources/properties?resourceId=..).
function Get-Props($ids, [string]$Label) {
  $p = @{}
  foreach ($c in (Chunks $ids)) {
    $q = ($c | ForEach-Object { 'resourceId=' + $_ }) -join '&'
    $res = Call ($B + '/resources/properties?' + $q) $Label
    if (-not $res.resourcePropertiesList) { throw 'properties answer has no resourcePropertiesList' }
    foreach ($item in @($res.resourcePropertiesList)) {
      $m = @{}
      foreach ($x in @($item.property)) { if ($x -and $x.name) { $m[[string]$x.name] = (Fix-Text $x.value) } }
      $p[[string]$item.resourceId] = $m
    }
  }
  return $p
}
# Latest value and its time for some metric keys: {id: {key: @{v; t}}} (GET /resources/stats/latest).
function Get-Latest($ids, [string[]]$Keys, [string]$Label) {
  $p = @{}
  $kq = ($Keys | ForEach-Object { 'statKey=' + [Uri]::EscapeDataString($_) }) -join '&'
  foreach ($c in (Chunks $ids)) {
    $q = ($c | ForEach-Object { 'resourceId=' + $_ }) -join '&'
    $res = Call ($B + '/resources/stats/latest?' + $q + '&' + $kq) $Label
    foreach ($v in @($res.values)) {
      $m = @{}
      foreach ($s in @($v.'stat-list'.stat)) {
        if ($s -and $s.statKey) {
          $d = @($s.data); $ts = @($s.timestamps)
          if ($d.Count -gt 0) { $m[[string]$s.statKey.key] = @{ v = $d[-1]; t = $ts[-1] } }
        }
      }
      $p[[string]$v.resourceId] = $m
    }
  }
  return $p
}
function Num($x) { if ($null -eq $x -or "$x" -eq '' -or "$x" -eq 'NaN') { return $null } return [double]$x }
function Block() { return [ordered]@{ ok = $false; error = 'not read'; total = $null; rows = @() } }

$adapters = Block; $hosts = Block; $dss = Block; $alerts = Block; $alerts.fetched = $null
if (-not $aria.ok) { foreach ($b in @($adapters, $hosts, $dss, $alerts)) { $b.error = 'not read: ' + $aria.error } }
else {
  # ---- adapters (one per vCenter) ----
  try {
    $res = Call ($B + '/adapters?adapterKindKey=VMWARE') 'adapters'
    $rows = New-Object System.Collections.ArrayList
    foreach ($a in @($res.adapterInstancesInfoDto)) {
      if (-not $a) { continue }
      [void]$rows.Add([ordered]@{ id = [string]$a.id; name = (Fix-Text $a.resourceKey.name); vcid = (VcId $a)
                                  last_collected = $a.lastCollected; last_heartbeat = $a.lastHeartbeat; message = (Short ([string]$a.messageFromAdapterInstance)) })
    }
    # total = vROps' own pageInfo.totalCount when the answer has one; otherwise the count read (total_from says which)
    $adapters.total = $rows.Count; $adapters.total_from = 'count'
    if ($res.pageInfo -and ($null -ne $res.pageInfo.totalCount)) { $adapters.total = [int]$res.pageInfo.totalCount; $adapters.total_from = 'pageInfo' }
    $adapters.rows = @($rows.ToArray()); $adapters.ok = $true; $adapters.error = ''
  } catch { $adapters.error = Short $_.Exception.Message }

  # ---- hosts ----
  try {
    $l = Get-Paged '/resources?adapterKind=VMWARE&resourceKind=HostSystem' 'resourceList' 'hosts'
    $ids = @($l.list | ForEach-Object { [string]$_.identifier })
    $pp = Get-Props $ids 'hostprops'
    $st = Get-Latest $ids @($HostTimeKey) 'hoststats'
    $rows = New-Object System.Collections.ArrayList
    foreach ($r in $l.list) {
      $id = [string]$r.identifier; $p = $pp[$id]; $s = $st[$id]
      $at = $null; if ($s -and $s[$HostTimeKey]) { $at = $s[$HostTimeKey].t }
      [void]$rows.Add([ordered]@{ id = $id; name = (Fix-Text $r.resourceKey.name); vcid = (VcId $r)
                                  conn = $(if ($p) { $p[$HostConnKey] } else { $null }); maint = $(if ($p) { $p[$HostMaintKey] } else { $null }); at = $at })
    }
    $hosts.rows = @($rows.ToArray()); $hosts.total = $l.total; $hosts.ok = $true; $hosts.error = ''
  } catch { $hosts.error = Short $_.Exception.Message }

  # ---- datastores ----
  try {
    $l = Get-Paged '/resources?adapterKind=VMWARE&resourceKind=Datastore' 'resourceList' 'datastores'
    $ids = @($l.list | ForEach-Object { [string]$_.identifier })
    $pp = Get-Props $ids 'dsprops'
    $st = Get-Latest $ids @($DsCapKey, $DsFreeKey, $DsHostsKey) 'dsstats'
    $rows = New-Object System.Collections.ArrayList
    $mountFail = 0
    foreach ($r in $l.list) {
      $id = [string]$r.identifier; $p = $pp[$id]; $s = $st[$id]
      $cap = $null; $free = $null; $nh = $null; $at = $null
      if ($s) {
        if ($s[$DsCapKey]) { $cap = Num $s[$DsCapKey].v }
        if ($s[$DsFreeKey]) { $free = Num $s[$DsFreeKey].v; $at = $s[$DsFreeKey].t }
        if ($s[$DsHostsKey]) { $x = Num $s[$DsHostsKey].v; if ($null -ne $x -and $x -ge 1) { $nh = [int]$x } }
      }
      if ($MountFromRels) {
        # Optional: count the HostSystem resources related to this datastore. A failure here only leaves hosts unknown (the system lists it).
        try {
          $rr = Call ($B + '/resources/' + $id + '/relationships?relationshipType=PARENT&pageSize=' + $PageSize) 'dsrel'
          $n = @(@($rr.resourceList) | Where-Object { $_.resourceKey.resourceKindKey -eq 'HostSystem' }).Count
          if ($n -ge 1) { $nh = $n }
        } catch { $mountFail++ }
      }
      if ($null -ne $free -and $free -lt 0) { $free = $null }
      if ($null -ne $cap -and $cap -lt 0) { $cap = $null }
      [void]$rows.Add([ordered]@{ id = $id; name = (Fix-Text $r.resourceKey.name); vcid = (VcId $r); type = $(if ($p) { $p[$DsTypeKey] } else { $null })
                                  cap_gb = $cap; free_gb = $free; hosts = $nh; local = $(if ($p) { $p[$DsLocalKey] } else { $null }); at = $at })
    }
    if ($mountFail) { Write-Host ('mount count failed for ' + $mountFail + ' datastores (left unknown)') }
    $dss.rows = @($rows.ToArray()); $dss.total = $l.total; $dss.ok = $true; $dss.error = ''
  } catch { $dss.error = Short $_.Exception.Message }

  # ---- active alerts ----
  try {
    $l = Get-Paged '/alerts?activeOnly=true' 'alerts' 'alerts'
    $alerts.total = $l.total; $alerts.fetched = @($l.list).Count
    $want = @($Criticality | ForEach-Object { ([string]$_).ToUpper() })
    $keep = @($l.list | Where-Object { $want -contains ([string]$_.alertLevel).ToUpper() })
    $rids = @($keep | ForEach-Object { [string]$_.resourceId } | Where-Object { $_ } | Sort-Object -Unique)
    $res = @{}
    foreach ($c in (Chunks $rids)) {
      $q = ($c | ForEach-Object { 'resourceId=' + $_ }) -join '&'
      $rr = Call ($B + '/resources?' + $q + '&pageSize=' + $PageSize) 'alertres'
      foreach ($r in @($rr.resourceList)) { if ($r) { $res[[string]$r.identifier] = $r } }
    }
    $rows = New-Object System.Collections.ArrayList
    foreach ($a in $keep) {
      $r = $res[[string]$a.resourceId]
      [void]$rows.Add([ordered]@{ id = [string]$a.alertId; res_id = [string]$a.resourceId
                                  res_kind = $(if ($r) { [string]$r.resourceKey.resourceKindKey } else { '' }); res_name = $(if ($r) { (Fix-Text $r.resourceKey.name) } else { '' })
                                  vcid = $(if ($r) { (VcId $r) } else { '' }); name = (Fix-Text $a.alertDefinitionName); level = [string]$a.alertLevel
                                  status = [string]$a.status; control = [string]$a.controlState; owner = (Fix-Text $a.ownerName)
                                  start = $a.startTimeUTC; updated = $a.updateTimeUTC })
    }
    $alerts.rows = @($rows.ToArray()); $alerts.ok = $true; $alerts.error = ''
  } catch { $alerts.error = Short $_.Exception.Message }
}
$hd = $null

# ---- write the batch (manifest.json last) ----
$batchId = 'ariadc_' + $stamp
$dir = Join-Path $OutDir $batchId
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$snap = [ordered]@{ aria = $aria; adapters = $adapters; hosts = $hosts; datastores = $dss; alerts = $alerts
                    params = [ordered]@{ criticality = @($Criticality); vcid = $VcIdName; host_conn = $HostConnKey; host_maint = $HostMaintKey; host_time = $HostTimeKey
                                         ds_type = $DsTypeKey; ds_cap = $DsCapKey; ds_free = $DsFreeKey; ds_hosts = $DsHostsKey; ds_local = $DsLocalKey; mount_from_rels = [bool]$MountFromRels } }
$final = Join-Path $dir 'snap.json'
Write-Atomic $final ($snap | ConvertTo-Json -Depth 8 -Compress)
$item = [ordered]@{ name = 'snap.json'; sha256 = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $final).Length }
$counts = [ordered]@{ adapters = @($adapters.rows).Count; hosts = @($hosts.rows).Count; datastores = @($dss.rows).Count; alerts = @($alerts.rows).Count }
$manifest = [ordered]@{
  source = 'aria_dailycheck'
  batch_id = $batchId
  schema_version = 1
  collector_version = $ScriptVersion
  collected_at = $startedAt.ToString('yyyy-MM-ddTHH:mm:sszzz')
  time_zone = 'Asia/Taipei'
  range = [ordered]@{ from = $today; to = $today }
  count = 1
  files = @($item)
  counts = $counts
  raw = @($raw.ToArray())
  calls = $script:calls
  elapsed_s = [int]$sw.Elapsed.TotalSeconds
}
Write-Atomic (Join-Path $dir 'manifest.json') ($manifest | ConvertTo-Json -Depth 6 -Compress)
$bad = @(@($adapters, $hosts, $dss, $alerts) | Where-Object { -not $_.ok }).Count
Write-Host ('BATCH ' + $batchId + ' : adapters ' + $counts.adapters + ', hosts ' + $counts.hosts + '/' + $hosts.total + ', datastores ' + $counts.datastores + '/' + $dss.total +
            ', alerts kept ' + $counts.alerts + ' of ' + $alerts.fetched + '/' + $alerts.total + ', failed parts ' + $bad + ', calls ' + $script:calls +
            ', first failure at call #' + $(if ($null -eq $script:firstFail) { 'none' } else { $script:firstFail }) + ', seconds ' + [int]$sw.Elapsed.TotalSeconds)
foreach ($b in @(@('aria', $aria), @('adapters', $adapters), @('hosts', $hosts), @('datastores', $dss), @('alerts', $alerts))) { if (-not $b[1].ok) { Write-Host ('  PROBLEM ' + $b[0] + ' : ' + $b[1].error) } }
# The same completeness checks the system does; only a complete batch writes the marker (so the later runs retry otherwise).
$why = @()
if (-not $aria.ok) { $why += 'login' }
foreach ($b in @(@('adapters', $adapters), @('hosts', $hosts), @('datastores', $dss))) {
  if (-not $b[1].ok) { $why += ($b[0] + ' failed') }
  elseif (@($b[1].rows).Count -eq 0) { $why += ($b[0] + ' empty') }
  elseif (($null -eq $b[1].total) -or ([int]$b[1].total -ne @($b[1].rows).Count)) { $why += ($b[0] + ' page total ' + $b[1].total + ' vs ' + @($b[1].rows).Count) }
}
if (-not $alerts.ok) { $why += 'alerts failed' }
elseif (($null -eq $alerts.total) -or ([int]$alerts.total -eq 0)) { $why += 'alerts total empty' }
elseif ([int]$alerts.total -ne [int]$alerts.fetched) { $why += ('alerts page total ' + $alerts.total + ' vs ' + $alerts.fetched) }
if ($why.Count -eq 0) { Write-Atomic $marker $today; Write-Host 'Complete: marker written (later runs today will skip).' }
else { Write-Host ('NOT complete (' + ($why -join '; ') + '): no marker, the next scheduled run will try again.') }
}
exit 0
`,
        bring: '終端機顯示的文字（不含密碼）；第一次 -SaveRaw 的 ariadc_raw 資料夾（原始回應，含主機與資料存放區名稱，只給 PC 端開發者看，不貼到公開地方）',
      },
    ],
    failHow: '任何錯誤：把終端機顯示的整段文字（不含帳密）貼到「整理結果」頁籤去識別後帶回，並在備註寫「失敗」。',
    expect: '一個批次資料夾 ariadc_<時間>：snap.json（收集器、ESXi、資料存放區、警示四類，各自 ok／筆數／總數）加上 manifest.json（source=aria_dailycheck，帶各類筆數與原始回應 sha256，最後寫）。系統匯入後在「1.5 每日檢查 → 設定 → VMware」看得到最新批次與看到的 vCenter。',
    returnHow: '複製到中繼 PC 用 relay_push 上傳（排程）；不用把檔案傳回給開發者。第一次的 -SaveRaw 原始回應由 PC 端開發者對 vCenter 畫面核對。',
    notes: [
      '尚未在公司 vROps 上實跑。第一次 -SaveRaw 要確認：vCenter 識別（VMEntityVCID）在 ESXi、資料存放區、收集器上都有；資料存放區類型（summary|type）、容量與剩餘（capacity|total_capacity、capacity|available_space，GB）、掛載台數（summary|total_number_hosts）這幾個鍵有值；鍵名不同就用參數改。',
      '掛載台數拿不到時可加 -MountFromRels（每個資料存放區多一次關係查詢；舊紀錄顯示這台跳板機查關係可能斷線，失敗只會讓那個資料存放區變成「判不出本機碟」，不會中斷）。',
      '任何一類讀不到（登入失敗、分頁沒讀完、0 筆）系統都顯示「無資料＋原因」，不會當成正常；-SkipIfOkToday 只在當天已經有一批四類都成功時才跳過。',
    ],
  },
]

// ===== 以下是純函式，給頁面與測試用（進度相關的在 utils/devkitProgress.ts） =====

export interface DevkitFilter { q?: string; tag?: string; status?: string }

/** 篩選任務：搜尋（標題、用途、位置、指令標籤與內容、編號）＋標籤＋狀態；順序（＝項次 D1、D2…）不變。statusOf 由進度推導。 */
export function filterTasks(tasks: DevkitTask[], f: DevkitFilter, statusOf: (t: DevkitTask) => string): DevkitTask[] {
  const q = (f.q || '').trim().toLowerCase()
  return tasks.filter((t) => {
    if (f.tag && f.tag !== '全部' && t.tag !== f.tag) return false
    if (f.status && f.status !== '全部' && statusOf(t) !== f.status) return false
    if (!q) return true
    const hay = [t.id, t.title, t.purpose, t.where, ...(t.precheck ?? []).flatMap(c => [c.label, c.text]), ...t.commands.flatMap(c => [c.label, c.text])].join('\n').toLowerCase()
    return hay.includes(q)
  })
}

/** 把一組任務的指令整理成一份純文字（給「下載全部指令」帶去公司內部環境用）。 */
export function commandsAsText(tasks: DevkitTask[]): string {
  const out: string[] = []
  for (const t of tasks) {
    if (!t.commands.length) continue
    out.push(`=== ${t.id} ${t.title} ===`, `在哪裡執行：${t.where}`, `所需權限：${t.perm}`, '')
    ;(t.precheck ?? []).forEach((c, i) => out.push(`【連線前檢查 ${i + 1}】${c.label}`, c.text, ''))
    t.commands.forEach((c, i) => out.push(`【指令 ${i + 1}】${c.label}`, c.text, ''))
  }
  return out.join('\n')
}
