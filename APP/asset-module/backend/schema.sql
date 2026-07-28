-- 資產盤點模組 — SQLite Schema
-- 欄位依 MVP契約_資產盤點系統.md「完整 ICA 欄位清單」(D23) 定案版本
-- D24：hardware 表另標示哪些欄位屬於「可自動掃描比對層」，其餘為「業務性/僅顯示層」

CREATE TABLE IF NOT EXISTS hardware (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_serial TEXT UNIQUE NOT NULL,      -- 資產序號
    inventory_division TEXT,                -- 盤點單位-處別
    inventory_department TEXT,              -- 盤點單位-部門
    asset_status TEXT,                      -- 資產狀態
    group_name TEXT,                        -- 群組名稱
    api_id TEXT,                            -- API ID
    asset_name TEXT,                        -- 資產名稱
    infra_type TEXT,                        -- 整體基礎架構
    device_model TEXT,                      -- 設備機型            [可比對層 D24]
    asset_purpose TEXT,                     -- 資產用途
    physical_location TEXT,                 -- 資產實體位置
    rack_no TEXT,                           -- 機櫃編號
    quantity INTEGER,                       -- 數量
    owner TEXT,                             -- 擁有者
    environment TEXT,                       -- 環境別（正式/測試/備援，D25 篩選依據）
    hostname TEXT,                          -- 主機名稱            [可比對層 D24]
    os TEXT,                                -- 作業系統
    big_ip_vip TEXT,                        -- BIG IP/VIP
    hardware_no TEXT,                       -- 硬體編號
    ip TEXT,                                -- IP                  [可比對層 D24]
    custodian TEXT,                         -- 保管者（＝SP保管者，D27）
    usage_unit TEXT,                        -- 使用單位
    user_name TEXT,                         -- 使用者（＝AP User，D27）
    remark TEXT,                            -- 附加說明
    owning_company TEXT,                    -- 所屬公司
    integrity INTEGER,                      -- 完整性 (I)
    confidentiality INTEGER,                -- 機密性 (C)
    availability INTEGER,                   -- 可用性 (A)
    request_no TEXT,                        -- 申請單編號
    is_vm INTEGER DEFAULT 0,                -- VM/實體機標記         [可比對層 D24]
    subnet TEXT,                            -- 網段（技術層，IP 推算）
    mac TEXT,                               -- MAC（技術層，SSH facts）
    hw_serial TEXT,                         -- 硬體序號（技術層，dmidecode/prtconf，需 root）
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS personnel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_serial TEXT NOT NULL REFERENCES hardware(asset_serial),
    inventory_division TEXT,
    inventory_department TEXT,
    asset_status TEXT,
    group_name TEXT,
    person_name TEXT,                       -- 人員姓名
    infra_type TEXT,
    belong_division TEXT,                   -- 隸屬單位-處別
    belong_department TEXT,                 -- 隸屬單位-部門
    phone TEXT,                             -- 聯絡電話
    job_desc TEXT,                          -- 職務概述
    proxy1 TEXT,                            -- 代理人1
    proxy1_phone TEXT,                      -- 代理人聯絡電話
    remark TEXT,
    integrity INTEGER,
    confidentiality INTEGER,
    availability INTEGER,
    request_no TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS software (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_serial TEXT NOT NULL REFERENCES hardware(asset_serial),
    inventory_division TEXT,
    inventory_department TEXT,
    asset_status TEXT,
    group_name TEXT,
    api_id TEXT,
    asset_name TEXT,
    infra_type TEXT,
    cloud_service_type TEXT,                -- 雲端服務類型
    project_zone TEXT,                      -- 專案/可用區
    cloud TEXT,                             -- Cloud
    hostname TEXT,
    ip TEXT,
    os TEXT,
    db_software TEXT,                       -- 資料庫/軟體
    backup_frequency TEXT,                  -- 備份頻率
    asset_purpose TEXT,
    handles_pii INTEGER,                    -- 處理個資
    owner TEXT,
    outsourced_maintenance TEXT,            -- 委外維護
    custodian TEXT,
    usage_unit TEXT,
    owning_company TEXT,
    remark TEXT,
    integrity INTEGER,
    confidentiality INTEGER,
    availability INTEGER,
    request_no TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    -- 納管狀態（跟 asset_status 是**兩條各自獨立的軸**，不可混用）：
    --   asset_status = 這台機器的業務生命週期（使用中/閒置/停用…），人維護
    --   collect_*    = 系統技術上收不收得到它，系統自己試連後記錄
    -- 一台機器可以同時是「使用中」而且「連不進去」——兩句都對、都有用，
    -- 混成一欄就會丟掉其中一個資訊。
    collect_ok INTEGER,                     -- NULL=沒試過；1=連得上收得到；0=試過但連不上
    collect_checked_at TEXT,                -- 最後一次試連時間
    collect_error TEXT                      -- 連不上的原因（原樣保留，不要吞成一句「失敗」）
);

-- ===== 多來源匯入的地基（階段一）=====
-- 目標：Excel／掃描／facts／之後的 vCenter 匯進同一份清單時，不會安靜地把兩台機器
-- 合併成一台，而且每個值都查得出「哪來的」。

-- 各來源的原始紀錄。**只增不改**——這是證據，正規化或合併判錯時要能重跑。
CREATE TABLE IF NOT EXISTS source_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,                   -- excel / net_scan / facts / vcenter …
    source_key TEXT,                        -- 該來源自己的主鍵（VC 的 moref、Excel 的列號…）
    payload TEXT NOT NULL,                  -- 原始內容（JSON），來源說什麼就存什麼
    collected_at TEXT DEFAULT (datetime('now','localtime')),
    -- 解析結果：對到哪台、用哪條規則、信心度。判錯時要能回溯是哪一步出問題。
    resolved_status TEXT,                   -- matched / new / ambiguous
    resolved_hardware_id INTEGER REFERENCES hardware(id) ON DELETE SET NULL,
    resolved_rule TEXT,
    resolved_confidence REAL,
    UNIQUE(source, source_key)
);
CREATE INDEX IF NOT EXISTS idx_source_record_status ON source_record(resolved_status);

-- 模糊案例的人工審核佇列。身分解析判不準時進這裡，**不自動合併**——
-- 合併錯的資料不會噴錯，只會安靜地變成一台，很難發現也很難還原。
CREATE TABLE IF NOT EXISTS merge_review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_record_id INTEGER NOT NULL REFERENCES source_record(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,                   -- 為什麼判不準（給人看的白話）
    candidates TEXT NOT NULL,               -- 可能的既有資產（JSON 陣列）
    status TEXT DEFAULT 'open',             -- open / merged / created / ignored
    decided_by TEXT,
    decided_at TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_merge_review_status ON merge_review(status);

-- 納管稽核：每次「系統去建帳號」的紀錄。3000 台自動化，要能事後查「上週三納管了哪些」。
-- ⚠️ 這張表**永遠不含憑證**——登入用的帳密用完即丟，不落地是寫死的安全底線。
CREATE TABLE IF NOT EXISTS onboard_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_ip TEXT NOT NULL,
    platform TEXT,                          -- linux / windows
    login_user TEXT,                        -- 用哪個帳號登入（帳號名可留，密碼絕不留）
    trigger TEXT NOT NULL,                  -- manual（UI 一鍵）/ auto（排程）
    triggered_by TEXT,                      -- 哪個登入者按的（manual 時）
    ok INTEGER NOT NULL,
    stage TEXT,                             -- connect / execute / verify
    message TEXT,
    output TEXT,                            -- 腳本輸出（已是無機密的納管腳本產物）
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_onboard_audit_ip ON onboard_audit(target_ip);

-- 收集用憑證庫：排程收集必須反覆使用的服務帳號（如 Windows WinRM）。
-- ⚠️ 密碼一律**加密後**才存進 secret_enc；加密金鑰放 /opt/webit3/.credential_key（0600），
-- 刻意不在 DB 裡——DB 會被備份、被複製，金鑰跟著走就等於沒加密。
-- ⚠️ 一鍵納管那種「人當下輸入、用完即丟」的帳密**絕不進這張表**，兩者不可混。
CREATE TABLE IF NOT EXISTS collect_credential (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,              -- 給人辨識的名稱（如「公司網域收集帳號」）
    kind TEXT NOT NULL,                     -- winrm / ssh / snmp …
    username TEXT NOT NULL,
    secret_enc TEXT NOT NULL,               -- Fernet 密文，永不以明文出現在任何回應
    scope TEXT,                             -- 適用範圍（網段前綴或網域），空=通用
    note TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 憑證使用稽核：一組能讀所有 Windows 的憑證放在服務裡，就必須查得到它被誰、對哪台用過。
-- 同樣**不含密碼**。
CREATE TABLE IF NOT EXISTS credential_use_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    credential_name TEXT NOT NULL,
    target_ip TEXT,
    ok INTEGER NOT NULL,
    message TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_cred_audit_name ON credential_use_audit(credential_name);

-- B（排程自動納管）的授權閘門：只有列在這裡且啟用的網段前綴，排程才會自動去納管。
-- ⚠️ 為什麼要這道閘門：納管腳本會在目標機建帳號、改 sshd_config，是有副作用的動作。
-- 讓它「無人在場自動對主機跑」風險高（打錯機器、碰到不該碰的網段都是事故），所以
-- 排程只碰操作者「明確加進來且啟用」的網段，其餘一律不碰。整體預設關閉
-- （app_settings.auto_onboard_enabled=0），要開必須明確打開。
CREATE TABLE IF NOT EXISTS auto_onboard_segment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prefix TEXT NOT NULL UNIQUE,            -- 網段前綴，如 "192.168.1."（比對用 startswith）
    enabled INTEGER NOT NULL DEFAULT 1,     -- 停用＝排程跳過但保留設定
    note TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 人工別名字典：規則解析橋不了的真實字差（Windows 11 → Microsoft Windows 11）。
-- 存 DB 而不是寫死在程式：這是**資料維護**不是改程式，人補完立刻生效、不用發版。
CREATE TABLE IF NOT EXISTS normalize_alias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                     -- os / device_model
    raw_value TEXT NOT NULL,                -- 來源實際出現的字串
    canonical TEXT NOT NULL,                -- 對應到的標準名
    note TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(kind, raw_value)
);

-- 掃描歷史：每次排程掃描的原始結果，供「本次 vs 上次」比對用（異常新增/異常消失）
CREATE TABLE IF NOT EXISTS scan_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_time TEXT DEFAULT (datetime('now','localtime')),
    hostname TEXT,
    ip TEXT,
    device_model TEXT,
    is_vm INTEGER DEFAULT 0,
    segment TEXT,                           -- 掃描的網段（掃描健康度用）
    scan_ok INTEGER DEFAULT 1,              -- 該筆掃描是否成功（0=失敗/逾時）
    -- S16 未知主機指紋：掃到一台沒登記的機器時，光禿禿一個 IP 沒人認得出來是什麼，
    -- 「納入管理」等於沒得管。以下欄位存的是「不必登入該機器就拿得到」的線索。
    mac TEXT,                               -- MAC 位址（前 3 段 OUI 可查廠商，00:0c:29=VMware）
    mac_vendor TEXT,                        -- OUI 查出的廠商名
    open_ports TEXT,                        -- 開放埠，逗號分隔（如 "22,3389"）；3389=Windows
    ttl INTEGER,                            -- 存活時間，64≈Linux、128≈Windows
    os_guess TEXT                           -- 綜合上述的作業系統猜測（是猜的，畫面要講清楚）
);

-- 比對結果：每次排程跑完 D4 比對引擎後的結論（異常新增/異常消失/漏登記）
CREATE TABLE IF NOT EXISTS comparison_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT DEFAULT (datetime('now','localtime')),
    hostname TEXT,
    ip TEXT,
    issue_type TEXT CHECK(issue_type IN ('異常新增', '異常消失', '漏登記')),
    is_read INTEGER DEFAULT 0,              -- 標記已處理（契約已定案功能）
    handled_at TEXT
);

-- 使用者（D8：儀表板登入驗證，本機帳密，不整合AD/SSO）。密碼一律雜湊儲存，不存明文。
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- Session：登入後發一組不可預測的token，伺服器端可查詢/可撤銷（登出即刪除該筆），
-- 比起JWT等自含式token更符合「本機帳密、MVP不搞複雜」的D8精神，且撤銷不用額外黑名單機制。
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);

-- 匯入紀錄（S9資料匯入頁「上次匯入」卡片用：時間/匯入人/各分頁筆數）
CREATE TABLE IF NOT EXISTS import_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imported_at TEXT DEFAULT (datetime('now','localtime')),
    imported_by TEXT,                       -- 匯入人（登入帳號username）
    hardware_count INTEGER DEFAULT 0,
    personnel_count INTEGER DEFAULT 0,
    software_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0
);

-- 連線設定（S10：D14/D15管理介面合併帳密+連線目標同一表格）。密碼欄位D11定案不加密，
-- 靠兩層保護：這個DB檔案本身在 data/ 目錄（.gitignore排除，不進git）＋正式環境部署時
-- 檔案權限只開放服務執行帳號可讀。API層另外把密碼欄位設計成write-only，不透過HTTP回顯。
CREATE TABLE IF NOT EXISTS connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                     -- 連線名稱（畫面顯示用，如"vCenter-機房A"）
    connection_type TEXT,                   -- 連線類型（vCenter/SNMP/其他）
    target TEXT NOT NULL,                   -- 目標位址／網段
    port INTEGER,
    username TEXT,
    password TEXT,
    enabled INTEGER DEFAULT 1,              -- 0=停用：排程掃描直接跳過，不會被算成「掃描失敗」
    last_status TEXT DEFAULT '灰',           -- 綠(已連線)/紅(未連線)/灰(未知，未測過或太久沒更新，D15)
    last_tested_at TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 功能模組開關（S13，D28：每個功能可獨立開關，測試時關閉/正式上線再打開，
-- 透過「功能模組管理」畫面切換，不改程式碼不重新部署）。
-- 「系統設定」本身刻意不列入這張表——它是切換開關的畫面本身，如果被自己鎖死，
-- 沒人能再進來重新打開，是設計上刻意排除的自鎖死防呆，不是漏掉。
CREATE TABLE IF NOT EXISTS feature_flags (
    module_key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

INSERT OR IGNORE INTO feature_flags (module_key, label, enabled) VALUES
    ('dashboard', '儀表板', 1),
    ('assets', '資產查詢', 1),
    ('import', '資料匯入', 1),
    ('topology', '系統聯通圖', 1),
    ('services', '服務盤點', 1),
    ('accounts', '帳號盤點', 1);

-- ===== M2 系統聯通圖 =====
-- 戰情室重寫模型：全新建，不搬舊資料。系統/依賴由人維護（業務層），健康度先手動標，
-- 之後「主機層」切片再把系統關聯到 hardware、由掃描/比對自動推導健康度。
CREATE TABLE IF NOT EXISTS systems (
    id TEXT PRIMARY KEY,                    -- 短代碼（如 'core'），前端節點 id
    label TEXT NOT NULL,                    -- 顯示名稱（如「核心交易系統」）
    category TEXT,                          -- 分類：對外/閘道/核心/基礎服務…（自由，前端只做視覺分群）
    domain TEXT,                            -- 領域：數位通路/核心系統…
    health TEXT DEFAULT 'ok' CHECK(health IN ('ok','warn','err')),  -- 運作中/維護中/異常
    is_spof INTEGER DEFAULT 0,             -- 單點故障（SPOF）標記
    note TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 依賴：A 依賴 B  =>  source=A, target=B（箭頭 A→B）。停 B 會波及 B 的所有 predecessors。
CREATE TABLE IF NOT EXISTS system_deps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
    target TEXT NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
    dep_type TEXT,                          -- API 呼叫/DB 連線/訊息/dependsOn…
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(source, target)
);

-- M2 第一片：主機上實際在聽的服務（service_collector 收，非人工維護）。
-- 這是「一台主機看得出什麼服務」的落地表，也是之後把主機接到 systems（業務系統）的橋。
--
-- ⚠️ 主鍵用 (ip, proto, port, bind_addr) 而不是 asset_serial：
-- 收集對象可能是「掃到但還沒登記」的主機（asset_serial 為 NULL），而 SQLite 的 UNIQUE
-- 不會把兩個 NULL 視為相同——用 asset_serial 當鍵會讓未登記主機每收一次就新增一筆。
-- 以 IP 為主、序號為輔，跟決策 T7 同一原則。
CREATE TABLE IF NOT EXISTS host_service (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    asset_serial TEXT,                      -- 對得到資產就填，未登記主機留 NULL
    proto TEXT NOT NULL DEFAULT 'tcp',
    port INTEGER NOT NULL,
    bind_addr TEXT,                         -- 0.0.0.0 / 127.0.0.1 / :: …
    exposure TEXT,                          -- all / localhost / specific（由 bind_addr 推導）
    process TEXT,                           -- 行程名。**機器講的才填**，收不到留 NULL
    service_guess TEXT,                     -- 顯示用推測（行程名或埠號猜的，看 guess_source）
    guess_source TEXT,                      -- process（確定）/ port（猜的）
    is_infra INTEGER DEFAULT 0,             -- 管理/基礎服務（SSH/DNS/NTP…），畫面可收合
    source TEXT NOT NULL,                   -- ssh_ss / local_ss / winrm …
    first_seen TEXT,
    last_seen TEXT,
    -- 服務消失不刪除，只標時間：「上週還在聽 3306、今天不見了」本身就是要看的事實，
    -- 刪掉就查不出來了（同 comparison_result 的「異常消失」精神）。
    gone_at TEXT,
    UNIQUE(ip, proto, port, bind_addr)
);
CREATE INDEX IF NOT EXISTS idx_host_service_ip ON host_service(ip);
CREATE INDEX IF NOT EXISTS idx_host_service_serial ON host_service(asset_serial);
CREATE INDEX IF NOT EXISTS idx_host_service_port ON host_service(port);

-- 每次服務採集的執行紀錄（誰觸發、收了幾台、成敗）。跟 scan_runs 分開：
-- 那個是網段掃描，這個是進主機收服務，兩件事的失敗原因完全不同。
CREATE TABLE IF NOT EXISTS service_collect_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT,                           -- manual / schedule
    status TEXT DEFAULT 'running',          -- running / ok / failed
    host_count INTEGER,                     -- 這次試著收幾台
    service_count INTEGER,                  -- 收到幾筆服務
    failed_count INTEGER,
    error TEXT,
    started_at TEXT,
    finished_at TEXT
);

-- ===== 帳號盤點（稽核導向）=====
-- 規格：AI/資訊戰情室重建案/帳號盤點_稽核需求與競品分析.md
-- 目標不是「有哪些帳號」的清單，是「哪幾條不合規、證據在這」。
CREATE TABLE IF NOT EXISTS host_account (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    asset_serial TEXT,
    username TEXT NOT NULL,
    uid INTEGER,
    gid INTEGER,
    gecos TEXT,                             -- /etc/passwd 第5欄（自動撈的帳號備註）
    note TEXT,                              -- 稽核人員手動輸入的備註（跟 gecos 不同，收集不覆蓋）
    home TEXT,
    shell TEXT,
    can_login INTEGER,                      -- shell 不是 nologin/false
    kind TEXT,                              -- human / service / default（決定規則適用範圍）
    last_login TEXT,                        -- 原始字串，不硬轉 datetime（多語系格式差很多）
    never_logged_in INTEGER,
    -- 以下需 root；收不到一律 NULL，畫面顯示「需 root」而非空白
    pw_status TEXT,                         -- set / empty / locked
    pw_last_change TEXT,
    pw_expires TEXT,
    pw_max_days TEXT,
    acct_expires TEXT,
    is_sudoer INTEGER,
    sudo_nopasswd INTEGER,
    priv_groups TEXT,                       -- wheel/sudo/docker… 逗號分隔
    authorized_keys INTEGER,                -- 免密碼登入的公鑰數
    -- 登入資料來源決定「從未登入」有多確定：lastlog/lastlog2 是確定的，
    -- last 只代表 wtmp 保存期內沒紀錄（Debian 13 起沒有 lastlog，只能走這條）
    login_source TEXT,
    os_family TEXT,                         -- rhel / debian / suse（指令與路徑慣例不同）
    os_id TEXT,                             -- rocky / debian / rhel / centos…
    os_version TEXT,
    source TEXT,
    first_seen TEXT,
    last_seen TEXT,
    -- 帳號消失（被刪或改名）同樣是要看的事實，標記不刪除
    gone_at TEXT,
    UNIQUE(ip, username)
);
CREATE INDEX IF NOT EXISTS idx_host_account_ip ON host_account(ip);
CREATE INDEX IF NOT EXISTS idx_host_account_user ON host_account(username);
CREATE INDEX IF NOT EXISTS idx_host_account_kind ON host_account(kind);

-- 稽核判定結果。每次採集後重算，保留歷史供「上次稽核 vs 這次」對照。
CREATE TABLE IF NOT EXISTS account_finding (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    ip TEXT NOT NULL,
    asset_serial TEXT,
    username TEXT NOT NULL,
    rule_id TEXT NOT NULL,                  -- R2 / R5 / R9 / A1…
    label TEXT,
    severity TEXT,                          -- high / medium / low
    verdict TEXT,                           -- fail / unknown（pass 不存，稽核看的是例外）
    law TEXT,                               -- 法規出處，佐證匯出要用
    detail TEXT,
    -- 例外管理：核准過的項目不該每次都亮紅燈，但核准要有期限，
    -- 否則「永久例外」會變成把問題永遠藏起來的工具。
    exempt_until TEXT,
    exempt_reason TEXT,
    exempt_by TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_account_finding_run ON account_finding(run_id);
CREATE INDEX IF NOT EXISTS idx_account_finding_rule ON account_finding(rule_id);

-- 發現的處置狀態（生命週期）。以 (ip,username,rule_id) 為鍵**跨盤點持久**——
-- findings 每次重算會換 id，狀態不能綁在會消失的 finding 上，否則每次盤點都重看一遍。
-- 這是把工具從「查詢介面」變成「稽核系統」的關鍵：稽核員標了「已確認/核准例外/已修復」，
-- 下次盤點還記得。
CREATE TABLE IF NOT EXISTS finding_disposition (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    username TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',    -- open待處理 / ack已確認 / exception核准例外 / fixed已修復
    note TEXT,
    exempt_until TEXT,                       -- 核准例外到期日（exception 用；過期自動回待處理亮燈）
    decided_by TEXT,
    decided_at TEXT,
    UNIQUE(ip, username, rule_id)
);
CREATE INDEX IF NOT EXISTS idx_finding_disposition_key
    ON finding_disposition(ip, username, rule_id);

CREATE TABLE IF NOT EXISTS account_collect_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT,
    status TEXT DEFAULT 'running',
    host_count INTEGER,
    account_count INTEGER,
    finding_count INTEGER,
    failed_count INTEGER,
    needs_root_count INTEGER,               -- 有幾台因權限不足只收到一半
    error TEXT,
    started_at TEXT,
    finished_at TEXT
);

-- 全站鍵值設定（掃描排程等 UI 可調設定放這，不寫死在系統層）。
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- 掃描執行紀錄（手動/排程觸發的每一次掃描狀態，供「重掃」四態回饋與排程判定用）。
CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT,                          -- manual（手動重掃）/ schedule（排程觸發）
    status TEXT DEFAULT 'running',         -- running / ok / failed
    found_count INTEGER,
    error TEXT,
    started_at TEXT,                       -- 本地時間（排程判定用，故不用 datetime('now','localtime') UTC）
    finished_at TEXT
);

-- 掃描排程預設值（DB 驅動，UI 可改，改了馬上生效，不用碰 systemd）。
INSERT OR IGNORE INTO app_settings (key, value) VALUES
    ('scan_enabled', '1'),
    ('scan_mode', 'daily'),          -- daily（每日某時）/ interval（每 N 小時）
    ('scan_time', '01:00'),
    ('scan_interval_hours', '6'),
    -- B 排程自動納管：預設關閉。開了之後也只碰 auto_onboard_segment 裡啟用的網段。
    ('auto_onboard_enabled', '0'),
    -- VC 自動匯入（方案 B 檔案中繼）：預設關閉；監看資料夾由 UI 設定。
    ('vcenter_autoimport_enabled', '0'),
    ('vcenter_autoimport_dir', ''),
    ('vcenter_autoimport_max_age_hours', '36'),
    -- M2 服務採集：預設開。收服務是唯讀查詢，不像自動納管會改目標主機，
    -- 所以不需要授權網段那道閘門。要停就設 0。
    ('service_collect_enabled', '1'),
    -- 帳號盤點門檻。稽核要求會隨年度變，所以做成可設定不寫死。
    ('acct_pw_max_days', '90'),      -- 法規：密碼至少每 3 個月變更
    ('acct_idle_days', '180'),       -- 法規：至少每半年審查並停用閒置帳號
    ('acct_review_days', '180'),
    ('account_collect_enabled', '1'),
    -- 遠端 SSH 收集身分。預設 webit3scan（唯讀最小權限）；可切成 sysinfra 等
    -- 標準管理帳號（NOPASSWD:ALL）以拿到需 root 的欄位。切之前要先把 webit3 收集
    -- 公鑰授權進該帳號的 authorized_keys，否則登入會被拒。
    ('collect_ssh_account', 'webit3scan');

CREATE INDEX IF NOT EXISTS idx_hardware_hostname ON hardware(hostname);
CREATE INDEX IF NOT EXISTS idx_hardware_ip ON hardware(ip);
CREATE INDEX IF NOT EXISTS idx_hardware_environment ON hardware(environment);
CREATE INDEX IF NOT EXISTS idx_scan_history_time ON scan_history(scan_time);
CREATE INDEX IF NOT EXISTS idx_comparison_read ON comparison_result(is_read);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_system_deps_source ON system_deps(source);
CREATE INDEX IF NOT EXISTS idx_system_deps_target ON system_deps(target);
