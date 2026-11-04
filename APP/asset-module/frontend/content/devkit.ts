// 開發協作頁（pages/v2/devkit.vue）的「收集任務」內容。頁面只負責顯示，要新增任務只改這個檔。
// 規則（tests/asset_module/test_devkit_content.py 會檢查）：
//   * 查詢本身一律唯讀：SQL 只能 SELECT；uemcli 只能 show（可重導向到 /tmp 的新檔）；不得含寫入類關鍵字。
//   * 指令文字要逐字原樣（用反引號樣板字串，裡面不可再出現反引號）。
//   * 範例位址只用 192.0.2.x、example.test；不放真實 IP 或公司識別字。
//   * 沒有指令的任務必須標 placeholder: true（等前一步的結果再補）。
//   * 改了指令文字或增減指令，task.ver 加 1（使用者本機的舊進度會顯示「指令已更新」）。

export type DevkitTag = 'WhatsUp' | 'Unity' | '其他'

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

export const DEVKIT_TAGS: DevkitTag[] = ['WhatsUp', 'Unity', '其他']

export const DEVKIT_TASKS: DevkitTask[] = [
  {
    id: 'D1',
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
    optional: '備用（不用先跑）：已改走 REST 探測（D1c）；只有 REST 走不通、改試唯讀 SQL 時才用',
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
        text: `# WUG-RELAY-COLLECT-SCRIPT v2 : WhatsUp Gold read-only collector for the relay folder (GET only, plus one token login)
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
  [switch]$AllowMarketHours
)
$ErrorActionPreference = 'Stop'
$ScriptVersion = 'wug_collect 2'
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
function Get-Wug([string]$Path) {
  $script:Calls++
  if ($script:Calls -gt $MaxCalls) { throw 'Call limit reached; stopping.' }
  Start-Sleep -Seconds 1
  return Invoke-RestMethod -Uri ($root + $Path) -Headers $headers -TimeoutSec 30
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
function Close-Batch {
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
  }
  Write-Atomic (Join-Path $script:batchDir 'manifest.json') ($manifest | ConvertTo-Json -Depth 8 -Compress)
  $script:total += $files.Count
  Write-Host ('BATCH ' + $script:batchId + ' : ' + $files.Count + ' devices')
}

$ips = @(Get-Content -LiteralPath $IpFile | ForEach-Object { $_.Trim() } | Where-Object { $_ -and (-not $_.StartsWith('#')) })
Open-Batch
foreach ($ip in $ips) {
  if ($files.Count -ge $BatchSize) { Close-Batch; Open-Batch }
  $addr = $null
  if ((-not [Net.IPAddress]::TryParse($ip, [ref]$addr)) -or ($addr.AddressFamily -ne 'InterNetwork')) { Write-Host 'SKIP: a line in the IP file is not an IPv4 address'; continue }
  try {
    $r = Get-Wug ('/api/v1/device-groups/-1/devices?search=' + $ip + '&view=overview&limit=10')
    $dv = @($r.data.devices | Where-Object { $_.networkAddress -eq $ip })[0]
    if (-not $dv) { Write-Host ('NOT FOUND ' + $ip); continue }
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
    $dev = [ordered]@{ ip = $ip; id = $dv.id; name = [string]$dv.name; reports = $reports }
    $fname = $ip + '.json'
    $final = Join-Path $script:batchDir $fname
    Write-Atomic $final ($dev | ConvertTo-Json -Depth 8 -Compress)
    $item = [ordered]@{ name = $fname; sha256 = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $final).Length }
    [void]$files.Add($item)
    Write-Host ('OK ' + $ip)
  } catch {
    Write-Host ('FAILED ' + $ip + ' : ' + $_.Exception.Message)
  }
}
$headers = $null
Close-Batch
if ($script:total -eq 0) {
  Write-Host 'No device was collected, so no manifest was written (the system will ignore this empty folder).'
  exit 1
}
Write-Host ('DONE ' + $script:total + ' devices in ' + $script:part + ' batch(es), ' + $script:Calls + ' calls')
`,
        ok: '每台顯示 OK，最後一行 DONE n devices；輸出資料夾裡有一個 whatsup_日期_時間 的批次資料夾，內含 manifest.json 與每台一個 IP.json',
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
    ver: 3,
    commands: [
      {
        label: '腳本 wug_alert_collect.ps1（按「下載腳本」存成檔案，放到中繼資料夾，和 wug_collect.ps1、wug_account.txt 同一個資料夾）。平常由排程每分鐘自動執行，手動試跑：powershell -File wug_alert_collect.ps1 -Base https://<WhatsUp主機名稱>:9644 -OutDir wug_alert_out。設備清單翻頁很慢（第 2 頁起每頁約 25 秒），一次最多 420 秒，超過就把這次標成「不完整」（系統只開新告警、不會因此把舊告警當成已恢復）。',
        file: 'wug_alert_collect.ps1',
        text: `# WUG-ALERT-COLLECT-SCRIPT v3 : WhatsUp Gold read-only alert snapshot for the relay folder (GET only, plus one token login)
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
  [int]$MaxRecheckDevices = 150
)
$ErrorActionPreference = 'Stop'
$ScriptVersion = 'wug_alert_collect 3'
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
$state = [ordered]@{ sig = ''; complete = $false; last_write_utc = ''; last_scan_utc = ''; fp = ''; ids = @() }
if (Test-Path -LiteralPath $StateFile) {
  try {
    $s = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
    $state.sig = [string]$s.sig; $state.complete = [bool]$s.complete; $state.last_write_utc = [string]$s.last_write_utc
    $state.last_scan_utc = [string]$s.last_scan_utc; $state.fp = [string]$s.fp; $state.ids = @($s.ids | Where-Object { $_ } | ForEach-Object { [string]$_ })
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
    [void]$ms.Add([ordered]@{ name = $nm; type = [string]$r.type; status = [string]$r.status; enabled = [string]$r.enabled })
  }
  return ,$ms.ToArray()
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
$scan = [ordered]@{ complete = $false; reason = $why; pages_read = 0; devices_seen = 0; group_total = $null; elapsed_s = 0; stopped_reason = ''; monitor_calls = 0; monitor_failed = 0 }
$newFp = ''
$newIds = @()
if ($kind -eq 'scan') {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $pg = ''
  $lastPage = $false
  try {
    do {
      $q = '/api/v1/device-groups/-1/devices?view=overview&limit=' + $PageLimit
      if ($pg) { $q += '&pageId=' + $pg }
      $x = Get-Wug $q 90
      if ((-not $x.data) -or (-not $x.data.PSObject.Properties['devices'])) { $scan.stopped_reason = 'response has no data.devices'; break }
      foreach ($d in @($x.data.devices)) {
        if (-not $d) { continue }
        $scan.devices_seen++
        $ws = [string]$d.worstState
        $dn = 0
        if ($d.PSObject.Properties['totalActiveMonitorsDown']) { $dn = [int]$d.totalActiveMonitorsDown }
        if ((($ws -ne 'Up') -and ($ws -ne '')) -or ($dn -gt 0)) {
          $o = [ordered]@{ id = [string]$d.id; name = [string]$d.name; host_name = [string]$d.hostName; ip = [string]$d.networkAddress
                           worst_state = $ws; best_state = [string]$d.bestState; monitors_down = $dn; monitors_ok = $false; monitors = @() }
          if ($d.PSObject.Properties['totalActiveMonitors']) { $o.monitors_total = [int]$d.totalActiveMonitors }
          [void]$devices.Add($o)
        }
      }
      $scan.pages_read++
      if (-not $x.PSObject.Properties['paging']) { $scan.stopped_reason = 'response has no paging block'; break }
      $pg = [string]$x.paging.nextPageId
      if (-not $pg) { $lastPage = $true; break }
      if ($sw.Elapsed.TotalSeconds -gt $TimeBoxSeconds) { $scan.stopped_reason = 'time box ' + $TimeBoxSeconds + ' s reached'; break }
    } while ($scan.pages_read -lt $MaxPages)
    if ((-not $lastPage) -and (-not $scan.stopped_reason)) { $scan.stopped_reason = 'max pages ' + $MaxPages + ' reached' }
  } catch {
    $scan.stopped_reason = 'page read failed: ' + $_.Exception.Message
  }
  # complete only if the list was read to the end AND the count is close to the group total (98 %); an empty list is never complete.
  if ($lastPage) {
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
    if ($scan.monitor_calls -ge $MaxMonitorCalls) { $newFp = 'too many'; continue }
    $scan.monitor_calls++
    try {
      $o.monitors = Read-Monitors $o.id
      $o.monitors_ok = $true
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
}
Write-Atomic $StateFile ($state | ConvertTo-Json -Compress)
Write-Host ('BATCH ' + $batchId + ' : ' + $kind + ', problem devices ' + $devices.Count + ', complete ' + $state.complete + ', calls ' + $script:Calls)
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
