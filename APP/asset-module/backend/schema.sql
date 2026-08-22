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
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    -- 「人」最後一次修改的時間。跟 updated_at 分開：後者每次自動匯入都會刷新，
    -- 拿它當「有沒有人在維護」的新鮮度指標，匯一次 Excel 就全部變新鮮，那數字是假的。
    manual_updated_at TEXT
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

-- 重複登記人工判定：使用者手動標記「這組（同主機名+IP）其實不是重複登記」（例如
-- DB 主機本來就會掛多個 VIP/IP），標記後 /api/assets/duplicates 不再列出這組。
-- 這是純粹的內部管理工具，不需要保留變更稽核。
CREATE TABLE IF NOT EXISTS duplicate_dismiss (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT NOT NULL,                 -- 存 lower(trim(...))，跟 duplicates 查詢比對口徑一致
    ip TEXT NOT NULL,                       -- 存 trim(...)
    note TEXT,
    dismissed_by TEXT,
    dismissed_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(hostname, ip)
);

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

-- EOS 頁分類人工覆寫（使用者 2026-08-12）：有些設備技術上偵測到的 OS 是對的
-- （例：Palo Alto Panorama／Finika 底層真的跑 CentOS/Linux），但維護權責歸網路設備組，
-- 不是一般主機 OS 組——這不是 normalize_alias 能救的「名稱認錯」問題，是「歸類權責」
-- 跟「技術OS」對不上，需要獨立一張表讓人直接把某個 canonical 名稱强制指定分類。
CREATE TABLE IF NOT EXISTS eos_category_override (
    canonical TEXT PRIMARY KEY,
    category TEXT NOT NULL,                 -- host_os / firmware / other
    overridden_by TEXT,
    overridden_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 使用者 2026-08-13 要求：不管系統目前顯示的名稱是規則確認還是靠 hint 猜的，
-- 都要能直接改成正確名稱，改完之後系統永遠照這個為準——這跟 normalize_alias
-- 不一樣：alias 是「原始字串 → 標準名」，只對還沒被規則收斂掉的原始值有用；
-- 這裡是「系統目前算出來的 canonical → 使用者確認的正確 canonical」，對任何
-- 已經顯示出來的名稱都能用（不管是 rule/hint/hint-vendor 哪個方法算出來的）。
CREATE TABLE IF NOT EXISTS normalize_canonical_override (
    kind TEXT NOT NULL,                     -- os / device_model
    old_canonical TEXT NOT NULL,            -- 系統原本算出來的名稱
    new_canonical TEXT NOT NULL,            -- 使用者鍵入的正確名稱
    overridden_by TEXT,
    overridden_at TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (kind, old_canonical)
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

-- Push agent 用的機器對機器 key（不是給人登入的，跟 users/sessions 分開一張表）。
-- 只存 hash，明文只在種 agent 當下（issue_host_key()）出現一次，之後查不到。
CREATE TABLE IF NOT EXISTS host_api_key (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_serial TEXT UNIQUE NOT NULL REFERENCES hardware(asset_serial),
    key_hash TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    last_seen_at TEXT,
    revoked_at TEXT
);

-- Push agent 回報的每日狀態指標，只存「最新一筆」（upsert），不是時序表——
-- 這是給運維看現況用，不是監控趨勢；之後真的要畫趨勢圖再另開歷史表，不動這張。
CREATE TABLE IF NOT EXISTS host_metric_latest (
    asset_serial TEXT NOT NULL REFERENCES hardware(asset_serial),
    metric_key TEXT NOT NULL,
    value REAL,
    unit TEXT,
    collected_at TEXT NOT NULL,
    received_at TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (asset_serial, metric_key)
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
    ('accounts', '帳號盤點', 1),
    -- 2026-08-15 補：新做的模組原本沒進開關清單，等於「系統設定裡關不掉」。
    -- 使用者的情境很實際——還沒開始收帳號、還沒做服務盤點的時候，
    -- 選單上掛著一堆空功能只會讓人以為系統壞了。
    ('golive', '上線前檢查與基線', 1),
    ('documents', '單據檔案室', 1),
    ('segments', '網段配置表', 1),
    ('data_quality', '資料品質', 1),
    ('eos', 'EOS 生命週期', 1),
    ('adopt', '納入管理', 1),
    ('pipeline', '納管漏斗', 1),
    -- 2026-08-21 系統組月報。照新規則**預設關閉**（enabled=0）：新模組由使用者
    -- 驗過內容再自己打開，不要一部署就出現在同事的選單上。
    ('report_system', '系統組報告', 0),
    -- 2026-08-18 拍板：MICS切片2做完先關（enabled=0），測試驗證過再由使用者手動開。
    -- 這個開關是路由層真攔截（middleware/features.global.ts 的 ROUTE_MODULE_MAP，
    -- 2026-08-19 查證修正——先前這裡誤寫成「只隱藏選單連結」，實際上關掉時連網址直接打
    -- 都會被導回 /settings，不只是選單上看不到）。
    ('blast', '影響範圍查詢', 0),
    -- 2026-08-19 拍板：資料庫還原（方案B：整檔覆蓋+單一確認）。這是唯一可能直接砸掉
    -- 正式資料的功能，跟其他模組一樣預設關閉，但這裡的「關」意義不同——不是「還沒做完」，
    -- 是「平時就該關著，要用的時候才手動開，用完關回去」。這個 key 不在
    -- ROUTE_MODULE_MAP（不是獨立頁面，是系統設定頁裡的一個區塊），前端用 isEnabled()
    -- 直接判斷要不要顯示還原表單。
    ('restore', '資料庫還原', 0);

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

-- ===== CI 圖譜（影響範圍查詢的地基，2026-08-18）=====
-- 為什麼不沿用上面的 systems/system_deps：那組是「業務系統」單一節點型別的扁平圖，
-- 而且主機掛系統靠 hardware.api_id 單值字串（一台主機只能屬一個系統）。要回答
-- 「這台 ESXi 死了影響哪些 VM／哪些業務／要通知誰」需要異質節點（VM/ESXi/Cluster/
-- Datastore/機櫃/網段/業務系統）與具型別的邊，撐不起來，所以另開一組。
--
-- systems/system_deps 不動、不搬、不退役：rebuild 時**單向投影**進來（永不回寫），
-- topology.vue 與既有 5 支 API 完全不受影響。那組承載的是別處沒有的「業務層人工真相」。
--
-- 方向約定跟 system_deps 完全一致：**src 依賴 dst**。
-- 所以「誰依賴我」＝反向找 src ＝ 爆炸半徑；「我依賴誰」＝正向找 dst ＝ 找肇因。
CREATE TABLE IF NOT EXISTS ci_node (
    node_id TEXT PRIMARY KEY,               -- 具名前綴，見 ci_graph.py 的 NODE_ID_PREFIX
                                            -- hw:<asset_serial> / esxi:<name> / cluster:<name>
                                            -- ds:<name> / bizsys:<api_id> / sys:<systems.id>
                                            -- seg:<cidr> / rack:<location>#<rack_no>
    node_type TEXT NOT NULL,                -- vm/host/esxi/cluster/datastore/business_service
                                            -- system/app/db/storage/san/switch/segment/rack/other
    label TEXT NOT NULL,
    asset_serial TEXT REFERENCES hardware(asset_serial),  -- 對得到資產才填，對不到留 NULL
    source TEXT NOT NULL,                   -- derive:hardware / derive:rvtools / derive:systems
                                            -- derive:segments / manual
    source_key TEXT,
    attrs TEXT,                             -- JSON 快照（ip/os/environment/rack_no/…）
    health TEXT DEFAULT 'unknown',
    is_spof INTEGER DEFAULT 0,
    -- 1 = 人工維護，rebuild 絕不覆蓋也絕不軟刪。這是日後人工補 switch/storage 關聯的入口，
    -- 一開始就要留：沒有它，人補完的資料第一次 rebuild 就會消失。
    manual_locked INTEGER DEFAULT 0,
    first_seen_at TEXT,
    last_seen_at TEXT,
    gone_at TEXT,                           -- 軟刪（沿用 host_service 的做法，不 DELETE）
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_ci_node_type ON ci_node(node_type);
CREATE INDEX IF NOT EXISTS idx_ci_node_asset ON ci_node(asset_serial);

CREATE TABLE IF NOT EXISTS ci_edge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_node_id TEXT NOT NULL REFERENCES ci_node(node_id),
    dst_node_id TEXT NOT NULL REFERENCES ci_node(node_id),
    edge_type TEXT NOT NULL,                -- runs_on/member_of/stores_on/serves/
                                            -- depends_on/located_in
    -- 「幾分證據說幾分話」在資料層的落地：這欄不是備註，是一等公民。
    -- 血緣遍歷時取整條路徑上**最弱**的一環當該筆結果的信心度。
    confidence TEXT NOT NULL,               -- 證據 / 推論 / 未驗證
    evidence TEXT,                          -- 「RVTools vInfo Host 欄，匯入於 2026-08-18 09:12」
    source TEXT NOT NULL,
    source_key TEXT,
    manual_locked INTEGER DEFAULT 0,
    first_seen_at TEXT,
    last_seen_at TEXT,
    gone_at TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(src_node_id, dst_node_id, edge_type)
);
-- 血緣查詢的熱路徑：找「誰依賴我」是按 dst 反查，跑得最頻繁
CREATE INDEX IF NOT EXISTS idx_ci_edge_dst ON ci_edge(dst_node_id, gone_at);
CREATE INDEX IF NOT EXISTS idx_ci_edge_src ON ci_edge(src_node_id, gone_at);

-- 重建批次紀錄（形狀照抄 scan_runs，同一套「已在跑就回 409」的慣例）
CREATE TABLE IF NOT EXISTS ci_graph_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL,                  -- manual / auto
    triggered_by TEXT,
    status TEXT NOT NULL,                   -- running / done / failed
    node_count INTEGER DEFAULT 0,
    edge_count INTEGER DEFAULT 0,
    gone_count INTEGER DEFAULT 0,           -- 本輪被軟刪（來源已消失）的筆數
    error TEXT,
    started_at TEXT DEFAULT (datetime('now','localtime')),
    finished_at TEXT
);

-- MICS 切片3：計畫性停機事前評估的存證快照。圖會變（新設備/新依賴陸續補上），
-- 三週後有人問「當初評估說不影響」時要拿得出「當時」問過什麼、算出什麼——
-- 所以整包 impact() 的回傳原封存成 JSON，不是只存幾個欄位事後重算（重算會跟事後的圖對不起來）。
CREATE TABLE IF NOT EXISTS change_impact_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    reason TEXT,
    asked_by TEXT NOT NULL,
    asked_at TEXT NOT NULL,
    result_json TEXT NOT NULL
);

-- 2026-08-20 拍板方案A：事故當下要的是派工清單，不是查詢結果——攤平成「一列一個
-- （主機,聯絡人）配對」，給一個連結，全隊登入都看同一份，改狀態/寫備註就地存，
-- 不用各自抓Excel走（那樣多人同時編輯容易互相蓋掉，事後也對不回誰查過什麼）。
-- 掛在 change_impact_snapshot 底下：快照本來就是「當下凍結一份事實」，清單天生
-- 該長在快照上面，不然圖一直變、清單也跟著變，沒人知道自己是不是漏查了新主機。
-- 2026-08-20 使用者：現有的姓名/角色/電話/代理人/email/部門/部門主管是「通訊錄」
-- 欄位組合，回答的是「這個人是誰」；事故當下要回答的是「這台機器多重要、我現在
-- 該不該優先處理它、聯絡不到人怎麼辦」——所以單位從「人」改成「機器」，嚴重度/
-- 環境/位置這些機器身分欄位補進來，且 severity/sort_depth 在建立當下就算好存住，
-- 不用每次查詢重算，也不會因為底層資產資料之後變動而讓已凍結的清單跟著漂移。
CREATE TABLE IF NOT EXISTS checklist_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES change_impact_snapshot(id),
    asset_serial TEXT,
    hostname TEXT,
    ip TEXT,
    environment TEXT,
    physical_location TEXT,
    biz_system TEXT,
    severity TEXT,                  -- '重大'/'一般'：業務系統可用性(A)>=3 即為重大
    sort_depth INTEGER,             -- 跟查詢主機隔幾層，只用來排序，不對人顯示
    contact_name TEXT,
    contact_role TEXT,
    contact_department TEXT,
    contact_phone TEXT,
    status TEXT NOT NULL DEFAULT '未聯絡',
    note TEXT,
    updated_by TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_checklist_item_snapshot ON checklist_item(snapshot_id);

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
    -- 遠端 SSH 收集身分。定案用專用唯讀帳號 webit3scan，不共用 sysinfra——
    -- 共用會讓管理帳號的登入紀錄被機器流量塞滿，出事時人跟系統分不開
    -- （完整理由見 manage_state.DEFAULT_COLLECT_ACCOUNT）。
    -- 這個帳號的三道鎖：authorized_keys 帶 from= 限來源、sudo 只給唯讀白名單
    -- （不含 /etc/shadow）、撤銷只要刪一行不影響任何人。
    ('collect_ssh_account', 'webit3scan'),
    -- AIX 上同一個收集身分的名字。為什麼不同名（2026-08-16 定案）：AIX 的 sys0
    -- max_logname 預設 9（＝可用 8 個字元），webit3scan 是 10 個字元，mkuser 直接
    -- 拒絕；放寬要 chdev 並**重新開機**，為了帳號名重開 8 台正式 AIX 不划算。
    -- 該環境若已放寬過 max_logname，把這個值改成 webit3scan 就完全同名。
    ('collect_ssh_account_aix', 'webit3sc');

-- ===== 網段配置表（來源：（示範企業）期貨總分公司網段配置表 Excel）=====
-- 2026-08-15 使用者提供。用途有三個，缺一個這張表就只是靜態文件：
--   1. 新增資產時「機房 → 環境 → 網段」三層選 IP，不用背 10.99.x 是哪裡
--   2. 掃描範圍的依據（弱掃說明裡「建議排除掃描」的段不該被主動掃）
--   3. 資料品質的涵蓋率分母（哪些網段該掃卻還沒掃）
-- 匯入是**整批取代**不是 upsert：Excel 是這份清單的唯一真相，段被刪掉就該從系統消失；
-- upsert 會讓已作廢的網段永遠留在系統裡，越用越髒。
CREATE TABLE IF NOT EXISTS network_segment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cidr TEXT,                              -- 正規化 CIDR；原檔有「兩段寫在一格」「IP範圍」
                                            -- 這種寫法解析不出來，留 NULL 但不丟掉該列
    raw_cidr TEXT NOT NULL,                 -- 原始字串，解析不出來時畫面照樣看得到
    net_start INTEGER,                      -- 網段起訖（整數），用來反查「這個 IP 屬於哪段」
    net_end INTEGER,
    usage_status TEXT,                      -- 使用狀況（已使用／保留…）
    location TEXT,                          -- 使用位置＝機房（01_內湖機房…）
    purpose_desc TEXT,                      -- 用途說明
    category TEXT,                          -- 使用類別（SERVER／OA／NETWORK／UAT-*）
    usage TEXT,                             -- 使用目的（DMZ／x86-AP／PC…）
    environment TEXT,                       -- 由 category 的 UAT- 前綴推導：測試／正式
    scan_excluded INTEGER DEFAULT 0,        -- 弱掃說明寫「建議排除掃描」
    scan_note TEXT,                         -- 弱掃說明原文（含伺服器/IOT 區段的細節）
    row_no INTEGER,                         -- 原檔列號，匯入警告要指得出是哪一列
    imported_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_segment_cidr ON network_segment(cidr);
CREATE INDEX IF NOT EXISTS idx_segment_loc ON network_segment(location);
CREATE INDEX IF NOT EXISTS idx_segment_range ON network_segment(net_start, net_end);

-- ===== 單據檔案室（既有 Word 表單的歸檔與索引）=====
-- 2026-08-15：公司已經累積很多「主機及網路異動需求單」與「伺服器上線前檢查表」的
-- Word 檔（.doc 與 .docx 都有）。這張表的定位是**檔案室的索引卡，不是資料來源**：
--   抽：單據編號、日期、主機名、IP、申請人（識別用，格式固定、錯了一眼看得出）
--   不抽：CPU/記憶體/勾選框那些表格內容（□☑ 巢狀表格抓歪不會報錯，是安靜寫錯資料）
-- 歷史檢查表**不轉成 golive_check 基線**：那些勾選是當年的狀態，變成基線會立刻
-- 產生一堆過期的假 drift。基線只從今天以後在系統裡跑完的檢查表產生。
CREATE TABLE IF NOT EXISTS doc_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type TEXT NOT NULL,                 -- provision_form 異動需求單 / golive_form 上線前檢查表
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,                -- 原始檔落地位置（DATA/doc_archive/）
    file_ext TEXT,
    request_no TEXT,                        -- 這份單自己的單據編號
    ref_request_no TEXT,                    -- 檢查表回指的「伺服器申請單據表單編號」＝兩種單的 join key
    form_date TEXT,
    applicant_unit TEXT,
    applicant TEXT,
    unit_manager TEXT,
    system_name TEXT,
    hostname TEXT,                          -- 抽到的主機名（SECSVR195-059 這種）
    ip TEXT,
    asset_serial TEXT,                      -- 綁定到的資產
    -- auto  ＝檔名IP／內文IP／主機名編碼三方一致且對得到資產，可以自動綁
    -- review＝有任一不一致或對不到資產，要人看過才算
    -- manual＝人工指定的綁定
    bind_confidence TEXT,
    warnings TEXT,                          -- JSON 陣列：抽取時發現的矛盾（如檔名年份與填表日期不符）
    extracted TEXT,                         -- JSON：所有抽到的欄位原樣，供日後回溯
    -- 全文檢索用。使用者的真實痛點是「以前找資料要把所有 Word 一份份打開」，
    -- 只索引單號解決不了；存的是清理過的可讀文字（濾掉舊 .doc 雙解碼的亂碼並去重）。
    full_text TEXT,
    -- 單據上勾選的欄位（主機型態／機房／環境／服務類型…）。存 JSON。
    -- 只當「單據上寫什麼」，不直接覆蓋資產欄位——畫面上跟現況並列給人比對。
    checkboxes TEXT,
    -- 自由填寫的規格值（CPU/記憶體/硬碟/OS版本）。JSON。
    values_json TEXT,
    -- 需求單第二頁 13 個大區塊的適用狀態（DNS/F5/備份/WAF/資料庫…）。JSON。
    sections_json TEXT,
    -- 上線檢查表的每一列檢核項目與判定（完成／不需／未填），約 90 列。JSON。
    checklist_json TEXT,
    -- 人工審核：pending 待審 / confirmed 已確認。使用者 2026-08-15 要求加這關——
    -- 自由填寫欄抓歪不會報錯，唯一的解法就是「有人看過」。沒審過的值不參與比對。
    -- 這張單是不是該 IP 目前有效的那張。IP 會被回收再分配：三年前的單描述的是
    -- 「當時佔用這個 IP 的另一台機器」，拿它跟現在的資產比對只會得到一堆假不一致。
    -- 同一個 IP 只有最新一張 is_current=1，舊的仍然保留（歷史查得到）但不參與比對。
    is_current INTEGER DEFAULT 1,
    -- 這份單裡有帳密（進 DB 的全文已遮罩，但**原始檔沒有**，下載看得到）
    has_secrets INTEGER DEFAULT 0,
    -- 最新一張是下線單 ⇒ 這台已退役。資產卻還標「使用中」就是清單過期，
    -- 這是單據免費送的稽核訊號，不用另外去掃。
    is_decommission INTEGER DEFAULT 0,
    review_status TEXT DEFAULT 'pending',
    reviewed_by TEXT,
    reviewed_at TEXT,
    imported_by TEXT,
    imported_at TEXT,
    UNIQUE(file_name)
);
CREATE INDEX IF NOT EXISTS idx_doc_serial ON doc_archive(asset_serial);

-- 單據原檔下載稽核。全文檢索那份已經遮掉帳密，但原始 Word 沒有——
-- 既然一定要讓人下載得到（那是稽核證據），至少要留「誰在什麼時候看了哪一份」。
CREATE TABLE IF NOT EXISTS doc_download_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    file_name TEXT,
    username TEXT,
    downloaded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_doc_dl_doc ON doc_download_audit(doc_id);
CREATE INDEX IF NOT EXISTS idx_doc_reqno ON doc_archive(request_no);
CREATE INDEX IF NOT EXISTS idx_doc_ref ON doc_archive(ref_request_no);

-- ===== 資產生命週期：申請單 → 上線檢查 → 基線回檢 =====
-- 規格來源：2026-08-15 與使用者的討論（「主機及網路異動需求單」＋「伺服器上線前檢查表」
-- 目前是 Word 填寫、再人工抄進 Excel）。三張表對應三個階段：
--   provision_request  資產的出生證明（當初「申請」的內容）
--   golive_check(_result) 上線閘門，同時是「刻意設定成這樣」的基線宣告
--   baseline_drift     基線失效告警（本來是刻意的，現在被拿掉了）
-- 為什麼申請單不併進 hardware：申請單是文件（有簽核、會被駁回、有時效），hardware 是
-- 現況。分開存才比得出「當初申請 500G、現在 1TB」這種稽核價值高的差異。

CREATE TABLE IF NOT EXISTS provision_request (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_serial TEXT NOT NULL,             -- 這張單產生的資產
    source TEXT NOT NULL,                   -- form=依申請單轉錄 / direct=直接新增（IT 自建，無單）
    request_no TEXT,                        -- 單據編號（如 ES800011504075）
    applicant_unit TEXT,                    -- 申請單位
    applicant TEXT,                         -- 申請人員
    unit_manager TEXT,                      -- 單位主管
    form_date TEXT,                         -- 填表日期
    change_kind TEXT,                       -- 變更類別：緊急／一般／標準
    -- 申請單填寫內容全文（CPU/記憶體/硬碟/機房/服務類型…這些 hardware 沒有的欄位）。
    -- 存 JSON 而不是攤成欄位：申請單版面會改，攤成欄位每改一次就要 migration。
    raw_fields TEXT,
    attachment_name TEXT,                   -- 附件原始檔名（簽核用的 Word/PDF）
    attachment_path TEXT,                   -- 落地路徑（DATA/provision_attachments/）
    created_by TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_provision_serial ON provision_request(asset_serial);
CREATE INDEX IF NOT EXISTS idx_provision_no ON provision_request(request_no);

-- 一台資產一份上線檢查表。status=passed 才准把資產轉「使用中」。
CREATE TABLE IF NOT EXISTS golive_check (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_serial TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'open',    -- open進行中 / passed已通過
    started_at TEXT,
    passed_at TEXT,
    passed_by TEXT
);

-- 逐項結果。verdict=pass 的 auto 項就是「基線」——之後每天回檢的對照組。
CREATE TABLE IF NOT EXISTS golive_check_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id INTEGER NOT NULL,
    item_key TEXT NOT NULL,                 -- 對應 golive_checklist.json 的項目
    verdict TEXT,                           -- pass完成 / na不需 / fail未過 / unknown測不到
    evidence TEXT,                          -- auto 項測到的實際值（人工項留 NULL）
    checked_by TEXT,                        -- auto 項填 'auto'
    checked_at TEXT,
    UNIQUE(check_id, item_key)
);
CREATE INDEX IF NOT EXISTS idx_golive_result_check ON golive_check_result(check_id);

-- 基線失效。以 (asset_serial,item_key) 為鍵持久存在，狀態沿用 finding_disposition 的
-- 精神：標過「已確認/已修復」下次回檢還記得，不要每天重新亮一次同一條紅燈。
CREATE TABLE IF NOT EXISTS baseline_drift (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_serial TEXT NOT NULL,
    item_key TEXT NOT NULL,
    baseline TEXT,                          -- 上線時宣告的狀態（應然）
    current TEXT,                           -- 這次回檢測到的（實然）
    status TEXT NOT NULL DEFAULT 'open',    -- open待處理 / ack已確認 / fixed已恢復
    note TEXT,
    decided_by TEXT,
    decided_at TEXT,
    first_detected_at TEXT,
    last_detected_at TEXT,
    resolved_at TEXT,                       -- 回檢發現又符合基線了，自動填
    UNIQUE(asset_serial, item_key)
);
CREATE INDEX IF NOT EXISTS idx_drift_status ON baseline_drift(status);
CREATE INDEX IF NOT EXISTS idx_drift_serial ON baseline_drift(asset_serial);

-- ===== 收集入口收斂（決策 C4，2026-08-16）=====
-- 一次「按一下收集」的執行紀錄＋逐台結果。為什麼要存而不是只回給前端：
-- 這動作要探測數十到數百台、可能跑數十秒，做完只活在瀏覽器記憶體裡，
-- 使用者一重新整理就沒了，等於白等一次。存下來畫面才還原得回來。
CREATE TABLE IF NOT EXISTS collect_dispatch_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT,                           -- manual（畫面按的）
    triggered_by TEXT,                      -- 操作者帳號
    targets_raw TEXT,                       -- 使用者輸入的原文（截斷 2000 字）
    target_count INTEGER,
    status TEXT DEFAULT 'running',          -- running / ok / failed
    error TEXT,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS collect_dispatch_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    ip TEXT NOT NULL,
    alive INTEGER,
    open_ports TEXT,
    route TEXT,        -- ssh / winrm / agent / import（見 collect_dispatch.py 的四條路）
    status TEXT,       -- collected / needs_credential / needs_agent / import_only / failed
    asset_serial TEXT, -- 有登記才有；未登記留 NULL，這裡刻意不自動建資產
    hostname TEXT,
    registered INTEGER,
    message TEXT,      -- 給人看的「下一步要做什麼」，不是給機器解析的代碼
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_dispatch_result_run ON collect_dispatch_result(run_id);

CREATE INDEX IF NOT EXISTS idx_hardware_hostname ON hardware(hostname);
CREATE INDEX IF NOT EXISTS idx_hardware_ip ON hardware(ip);
CREATE INDEX IF NOT EXISTS idx_hardware_environment ON hardware(environment);
CREATE INDEX IF NOT EXISTS idx_scan_history_time ON scan_history(scan_time);
CREATE INDEX IF NOT EXISTS idx_comparison_read ON comparison_result(is_read);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_system_deps_source ON system_deps(source);
CREATE INDEX IF NOT EXISTS idx_system_deps_target ON system_deps(target);

-- ===== 系統組月報（2026-08-21）=====
-- 使用者每月要交部門報告，裡面固定三張表原本是人工統計的。他的原話：
-- 「以後我就 COPY 畫面，不用再自己統計」。

-- 表格列的備註。像「7/10 為颱風假、無開單」這種話是人寫的，寫一次要留著，
-- 不必每個月重打。period 為 NULL＝不分月份的通用備註（大多數屬於這種）；
-- 指定 period 才是「這個月特有」的說明。
CREATE TABLE IF NOT EXISTS report_note (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    period     TEXT,               -- 'YYYY-MM'，NULL = 每個月都帶出來
    row_key    TEXT NOT NULL,      -- 對應哪一列，例如 'platform:Windows Server'
    note       TEXT,
    updated_by TEXT,
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(period, row_key)
);

-- 月份快照。報告是每月出的，數字會隨資料變動；三個月後有人問「7 月那份怎麼算的」，
-- 重跑一次只會得到今天的數字。存快照才有稽核軌跡，也才做得出月與月的對比。
-- 同一個月重存＝覆蓋（同月本來就只該有一份定稿）。
CREATE TABLE IF NOT EXISTS report_snapshot (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    period     TEXT NOT NULL UNIQUE,   -- 'YYYY-MM'
    payload    TEXT NOT NULL,          -- 整份報告的 JSON，原封存證
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
