#!/usr/bin/ksh
#=============================================================================
# FCB AIX 組態檢核（產出格式與 fcbrhelsh 相容，供 DYN 分析）
#
# 檔名：fcb_aixV006.ksh
#
#   改名沿革：`fcbaixsh` -> `fcb_aix.ksh` -> `fcb_aixV001.ksh`（2026-09-30）。
#
#   ⚠️ **檔名帶流水號，腳本內容一改就要遞增**：V001 -> V002 -> V003…
#      使用者要的是「看檔名就知道手上是哪一版」，所以這是第三套編號，
#      跟 App 版號（version.json）與下面的 SCRIPTV 都**無關**，各記各的。
#
#   ⚠️ 三套編號一漂掉，使用者看到的就是互相矛盾的版本資訊，而他要版號
#      正是為了搞清楚手上是哪一版。所以有守門：
#      `tests/asset_module/test_fcb_aix_version.py` 會比對
#      「檔名流水號 / SCRIPTV / 內容雜湊」三者，其中一個沒跟上就紅燈。
#      改了內容要做三件事：檔名 V+1、SCRIPTV 換成現在時間、貼新的 BODY_SHA256。
#
# 來源：DATA/AIX檢核表.XLS「AIX組態調整項目」98 條，編號 1:1 對應 FCB-AIX-0001..0098
# 【會進報告的字串怎麼寫——每次加字串都要遵守】
#   1. **不要用裝飾符號**（警告號、菱形、打勾打叉、箭頭之類）。它們在輸出路徑上
#      會被打壞：2026-10-01 真機報告 FCB-AIX-0053 開頭那個菱形亂碼，其實是一個
#      表意空格（U+3000，E3 80 80）被打壞的結果，不是我們寫了什麼符號。
#      要強調就寫「注意：」。程式碼**註解**裡愛用什麼都行，那不會進輸出。
#   2. **不要用奇怪的空白字元**（U+3000 表意空格、NBSP）。一般空格就好。
#   3. **報告的讀者是老闆與稽核，不是寫這支腳本的人。**
#      不可以出現「本表」「客戶」「項次」「我們」這種開發者視角的話，
#      也不可以把實作理由寫進欄位——那屬於程式碼註解。
#      驗收標準：一個沒看過這支腳本、不熟 AIX 的稽核，單看那一列，
#      能不能說出「這條在要求什麼、這台現在什麼狀況、算不算合規」。
#
# 【兩份產出的分工——這條比任何一次改動都重要，動欄位之前先看】
#   .txt  = **給系統吃的**（DYN、我們的 config_audit.py）。
#           它的**欄位順序是對外契約**：只能往後加，不能插中間、不能改順序、
#           不能改名。要重排就得同時協調所有下游（DYN、config_audit.py、
#           以及任何拿它對帳的人）——那不是改腳本的人單方面能決定的事。
#   .html = **給人看的**。欄位怎麼排、要不要加篩選、要不要合併欄位，
#           以好用為準，不受上面那條契約約束。它的讀者是人，不是解析器。
#
#   2026-09-30 踩過：新欄一度被插在第 5~7 與 9~11。照標題解析的下游沒事，
#   照位置解析的會整排錯位。已改成全部接在原六欄之後。
#
# 對照：fcbrhelsh（RHEL，61 條）——表頭 key、分號分隔、檔名規則相同。
#       ⚠️ 欄位自 2026-09-30 起**不再完全相同**：原本那六欄位置不動，
#       後面接了六個新欄（第 7~12）：
#         7  CIS基準條號   CIS AIX 7 Benchmark v1.2.0 的 §x.y.z
#         8  CIS控制項     CIS Critical Security Controls v8 的族群（#4 / #5）
#         9  本行對應條號  FCB-01-002-xxxx / TWGCB-01-012-xxxx
#         10 查核指令      單獨確認這一條目前狀態的指令
#         11 修正指令      改成合規的指令（只是文字，腳本不執行）
#         12 生效方式      受控詞彙
#       7~9 原本全塞在「標準設定值」那一串文字裡。7 與 8 都是 CIS 出的，
#       但是**兩份不同的出版品**，所以分兩欄，不是一欄。
#       下游 `config_audit.py` 是**照欄位標題取值、不照位置**（Windows 本來就 7 欄），
#       所以兩邊欄數不同不會壞。RHEL 那支要不要跟進由指揮官決定；在它跟進之前，
#       「AIX 7 欄、RHEL 6 欄」是刻意的，不是漏改。
#
# 這支腳本是**唯讀**的：只用 grep / ls / lssec / lssrc / no -o / ps 等查詢指令，
# 不寫入任何系統檔、不啟停任何服務、不需要改權限。可重複執行。
#
# 重要設計（跟 RHEL 版的差別，都是刻意的）：
#   1. 檢查結果有四種，不是兩種：Compliant / Non-Compliant / Not-Applicable / Error
#      「查不到」不等於「合規」。指令失敗、檔案讀不到、權限不足 → 一律 Error，
#      不可以靜默算成通過，否則報表會把「沒查」講成「沒問題」。
#   2. 「目前值」一定放**實際觀測到的內容**（ls -l 原文、no -o 輸出、設定行原文），
#      不是「符合設定」四個字。人要能從這一欄自己複核。
#   3. 收尾一定有 "Check ended at:" 與 "Check summary:" 兩行。
#      沒有這兩行 = 腳本中途死掉，那份檔案**不可以當成檢核結果**（防截斷誤判）。
#   4. 先寫暫存檔、跑完才改名，避免半成品被別人撿走。
#
# 【資安說明】被問到時一句話講得清楚的三件事：
#   不變更：全程唯讀。只用 grep / ls / awk / ps / lssec / lssrc / lsitab / no -o /
#           showmount / oslevel 等**查詢**指令。不改設定、不啟停服務、不裝任何東西。
#           唯一寫入的是自己的輸出檔（預設當下目錄，檔案 0640）。
#   不越線：沒有 base64 混淆、沒有下載、沒有 ExecutionPolicy Bypass 之類的手法，
#           純文字可讀可稽核；不寫世界可寫目錄的固定檔名（避免 symlink 提權）；
#           不輸出密碼雜湊（只列帳號名），ps 只留三欄避免命令列帶出機敏參數。
#   不影響效能：無 find / 全碟掃描、無網路掃描；ps -ef 全程只取一次快照；
#           showmount 只在 /etc/exports 有條目時才呼叫。整支約數秒完成。
#
# 用法：  ksh fcb_aixV005.ksh    （建議用 root 執行；非 root 會有多條 Error）
# 產出：  **當下目錄**（跑完就在手邊）：
#         ./FCB_<HOSTNAME>_<IP>_<YYYYMMDD_HHMM>.txt
#         ./FCB_<HOSTNAME>_<IP>_<YYYYMMDD_HHMM>.html
#         ./FCB_<HOSTNAME>_<IP>_<YYYYMMDD_HHMM>.debug.log
#         檔名帶執行時間 -> 跑第二次不會蓋掉第一次，事後也分得出哪份是什麼時候跑的。
#         真的需要固定檔名就設 FCBAIX_NOSTAMP=1。
#         要改地方就設 FCBAIX_OUTDIR=/var/tmp/fcbaudit（環境變數優先於當下目錄）。
#=============================================================================

SCRIPTV="SV.AIX.202610011600"

HOSTNAME=`hostname`
# AIX 沒有 hostname -I；用 ifconfig 取第一個非 loopback 位址，優先 10.9 網段（比照 RHEL 版）
ALLIP=`ifconfig -a 2>/dev/null | grep 'inet ' | awk '{print $2}' | grep -v '^127\.' | tr '\n' ' '`
myIP=`echo "$ALLIP" | tr ' ' '\n' | grep '^10\.9' | head -n 1`
[ -z "$myIP" ] && myIP=`echo "$ALLIP" | tr ' ' '\n' | grep -v '^$' | head -n 1`
[ -z "$myIP" ] && myIP="0.0.0.0"

# 這一輪執行的時間戳。**產出檔名會帶它**，理由：
#   同一台跑第二次不會蓋掉第一次（改了設定要能跟改之前對照）、
#   事後拿到一疊報告從檔名就分得出先後。
# 查證過檔名不是對外契約：config_audit.py 的檔頭白紙黑字寫「平台看表頭的
#   `平台:`，**不看檔名**——檔名可以被改」，匯入端點只把檔名當標籤存進
#   config_audit_run.file_name，沒有任何樣式比對；測試也是隨便給
#   "FCB_sec01.txt" 就過。RHEL 版本來就叫 FCB_<HOST>_<IP>_RHEL.txt，
#   本來就不是同一個固定樣式。
# ⚠️ DYN 端怎麼認檔**未驗證**（同 AI/待答問題.md 那條）。真的有固定檔名需求時，
#   設 FCBAIX_NOSTAMP=1 就會退回舊的無時間戳檔名。
RUNSTAMP=`date '+%Y%m%d_%H%M'`
STAMPSFX="_$RUNSTAMP"
[ -n "$FCBAIX_NOSTAMP" ] && STAMPSFX=""

OSLEVEL=`oslevel -s 2>/dev/null`
[ -z "$OSLEVEL" ] && OSLEVEL=`oslevel 2>/dev/null`
OSVER="AIX `uname -v`.`uname -r` (oslevel -s: $OSLEVEL)"
KERNEL="`uname -v`.`uname -r` `bootinfo -K 2>/dev/null`bit"

#-----------------------------------------------------------------------------
# 輸出目錄
#
# 2026-09-30 改：預設從 /var/tmp/fcbaudit 改成**當下目錄**（使用者要「跑完就在手邊」）。
#
# ⚠️ 下一個接手的人請先看完這段再動，這裡的檢查**不是多餘的**：
#
#   原本為什麼不用 /tmp：/tmp 是世界可寫（0777+t）。用固定檔名寫進世界可寫目錄，
#   任何本機帳號都可以先放一個 symlink FCB_<host>_<ip>.txt -> /etc/passwd，
#   root 跑這支腳本時就幫他覆寫了系統檔。這是實際的本機提權路徑，不是理論風險。
#
#   改成「當下目錄」之後靠什麼守住（三道，一條都不能拿掉）：
#     (1) 目錄若是**其他人可寫**（含 /tmp、/var/tmp 這種 o+w 目錄）→ 明確警告，
#         把風險講白，讓執行者自己決定要不要換地方。不靜默照跑。
#     (2) 目標檔若是 symlink → 直接拒寫（原本就有，保留）。
#     (3) 目標檔若已存在但**不是一般檔**（FIFO／裝置檔）或**硬連結數 > 1**
#         （有人把它 hard-link 到別的檔）→ 拒寫。symlink 檢查擋不住 hard link。
#   再加上 umask 077 + 產出 chmod 640，內容不會外洩給同機其他帳號。
#
#   「當下目錄」在 cron 底下是 $HOME（cron 不給你 cd），不是你放腳本的地方。
#   排程用途請明確設 FCBAIX_OUTDIR，不要賭 cwd。
#
# 環境變數 FCBAIX_OUTDIR 優先於當下目錄（保留，排程/集中收檔都靠它）。
#-----------------------------------------------------------------------------
OUTDIR="${FCBAIX_OUTDIR:-.}"
if [ ! -d "$OUTDIR" ]; then
    mkdir -p "$OUTDIR" 2>/dev/null
    # 只有「我們自己建的」目錄才動權限。使用者的當下目錄不是我們的，不去改它。
    chmod 750 "$OUTDIR" 2>/dev/null
fi
if [ ! -d "$OUTDIR" ] || [ ! -w "$OUTDIR" ]; then
    echo "錯誤：輸出目錄 $OUTDIR 不存在或不可寫。"
    echo "      請 cd 到一個你寫得進去的目錄再跑，"
    echo "      或設 FCBAIX_OUTDIR 指到可寫的專屬目錄，例如："
    echo "      mkdir -p /var/tmp/fcbaudit && chmod 750 /var/tmp/fcbaudit"
    echo "      FCBAIX_OUTDIR=/var/tmp/fcbaudit ksh fcb_aixV005.ksh"
    exit 1
fi
OUTABS=`cd "$OUTDIR" 2>/dev/null && pwd`
[ -z "$OUTABS" ] && OUTABS="$OUTDIR"

# (1) 世界可寫警告。不擋——使用者可能真的知道自己在做什麼——但一定要出聲。
OUTDIR_WARN=""
_perm=`ls -ld "$OUTDIR" 2>/dev/null | head -n 1 | cut -c1-10`
case "$_perm" in
    ????????w?)
        # ls -ld 第一欄：d rwx rwx rwx —— 第 9 碼就是 others 的 w。
        # /tmp 是 drwxrwxrwt，第 9 碼同樣是 w，所以這條會抓到。
        # sticky（第 10 碼 t）只擋「刪除別人的檔」，擋不住「先建立一個還不存在的
        # 檔名」這種佈置，所以有 sticky 也照警告。
        OUTDIR_WARN="輸出目錄 $OUTABS 權限為 $_perm（其他本機帳號可寫）。固定檔名放在這種目錄有被預先佈置 symlink／hard link 的風險；本腳本已逐一檢查並會拒寫，但建議改到自己的目錄再跑，或設 FCBAIX_OUTDIR。"
        echo "警告：$OUTDIR_WARN"
        ;;
esac

# (2)(3) 目標檔的預先佈置檢查。symlink、非一般檔、硬連結數 > 1 一律拒寫。
for _t in "FCB_${HOSTNAME}_${myIP}${STAMPSFX}.txt" "FCB_${HOSTNAME}_${myIP}${STAMPSFX}.html" "FCB_${HOSTNAME}_${myIP}${STAMPSFX}.debug.log"; do
    _p="$OUTDIR/$_t"
    if [ -h "$_p" ]; then
        echo "錯誤：$_p 是符號連結，拒絕寫入（疑似有人預先佈置）。"
        exit 1
    fi
    if [ -e "$_p" ] && [ ! -f "$_p" ]; then
        echo "錯誤：$_p 已存在但不是一般檔案（FIFO／裝置檔？），拒絕寫入。"
        exit 1
    fi
    if [ -f "$_p" ]; then
        _nl=`ls -ld "$_p" 2>/dev/null | awk '{print $2}'`
        if [ -n "$_nl" ] && [ "$_nl" -gt 1 ]; then
            echo "錯誤：$_p 的硬連結數為 $_nl（不只一個名字指向它），拒絕寫入。"
            echo "      symlink 檢查擋不住 hard link，這裡一併擋。請先確認來源再刪除它。"
            exit 1
        fi
        # 重跑會覆蓋：這是刻意的（同一台的檢核結果就是要看最新那份），但要講出來。
        echo "提醒：$_p 已存在，這次會覆蓋它（舊的一份請先自行另存）。"
    fi
done
umask 077
tmp_file="$OUTDIR/.fcb_aix_$$.part"
pend_file="$OUTDIR/.fcb_aix_$$.pending"
output_file="$OUTDIR/FCB_${HOSTNAME}_${myIP}${STAMPSFX}.html"
flat_txt="$OUTDIR/FCB_${HOSTNAME}_${myIP}${STAMPSFX}.txt"

if [ "`id -u`" -eq 0 ]; then AM_ROOT=1; RUNAS="root"; else AM_ROOT=0; RUNAS="`id -un` (非 root，部分項目無法讀取，會標 Error)"; fi

CAT_SVC="系統服務"
CAT_SYS="系統設定與維護"
CAT_SSH="SSH設定"
CAT_LOG="日誌與稽核"
CAT_ACC="帳號與存取控制"

N_C=0; N_N=0; N_A=0; N_E=0
# 未完成項目按「生效方式」分類計數——「這次停機要一起做完的有幾條」
# 是使用者拿去排維護窗口的數字，不能只給一個總數叫人自己數。
N_RB=0      # 未完成 且 需重開機（含需 bosboot + 重開機）
N_SVC=0     # 未完成 且 需重啟服務
N_NOW=0     # 未完成 且 立即生效
N_LOGIN=0   # 未完成 且 下次登入生效
N_PWD=0     # 未完成 且 需重設密碼時生效
N_UNK=0     # 未完成 且 生效方式未確認
N_SEQ=0

# 行程快照：整支腳本只取一次，避免重複 fork ps（效能），也讓 98 條看到的是同一瞬間
PS_SNAP=`ps -ef 2>/dev/null`

# 沒有 AIX 測試機（2026-09-21 使用者：公司才有），第一次是盲跑。
# 所以：console 逐條印進度（截圖就看得出死在哪一條），debug log 留原始輸出。
dbg_file="$OUTDIR/FCB_${HOSTNAME}_${myIP}${STAMPSFX}.debug.log"
: > "$dbg_file" 2>/dev/null
dbg() { echo "$*" >> "$dbg_file" 2>/dev/null; }

# 指令在不在。「指令不存在」必須是 Error，不可以被當成「項目不存在 → 合規」——
# 2026-09-21 在非 AIX 機器試跑時就是踩到這個：lsitab 不存在，前 4 條全被判成 Compliant。
# AIX /usr/bin/ksh 是 ksh88：沒有 command -v（用 whence），也不支援 "! cmd" 否定語法。
# 這兩點只要踩到就是整支 syntax error，在沒有測試機的情況下第一次跑就全滅。
# whence 是 ksh 內建、command -v 是 POSIX：兩個都試，哪台機器都認得。
# （只寫 whence 的話在 bash 下會全部報「缺少」——2026-09-21 自己踩到。）
have() {
    whence "$1" >/dev/null 2>&1 && return 0
    command -v "$1" >/dev/null 2>&1 && return 0
    return 1
}

# 帶逾時執行一個查詢，回 RT_RC（離開碼；124 = 逾時）與 RT_OUT（標準輸出）。
#
# 為什麼需要這個：showmount 走 RPC，對端 nfsd 沒起來或防火牆擋 portmapper 時
# **會等很久**，而這支腳本要一次掃 8 台正式機。AIX 沒有 GNU 的 timeout 指令，
# 所以自己看門。
#
# ⚠️ 這裡的 kill 砍的是**我們自己 fork 出來的子行程**，不是系統服務——
# 對目標主機沒有任何變更。暫存檔寫在 $OUTDIR，檔名帶 PID（.rt.$$），
# 用完立刻刪，不寫死在 /tmp 的固定路徑。
# ⚠️ 2026-09-30 起 $OUTDIR 預設是**當下目錄**，不再保證是 0750 的專屬目錄——
# 目錄若其他人可寫，檔頭那段已經出聲警告過了。這裡不重複擋，
# 因為 .rt.$$ 是隨機（PID）檔名、不是可預測的固定名稱，佈置不了。
#
# 用「子行程把離開碼寫進 .rc 檔」來偵測結束，不用 kill -0：
# ksh 的背景工作結束後會停在 zombie，kill -0 仍然成功，會害看門狗每次都等滿。
run_timeout() {   # $1=秒數 $2...=要跑的指令
    _to=$1; shift
    _tf="$OUTDIR/.rt.$$"
    rm -f "$_tf" "$_tf.rc" 2>/dev/null
    ( "$@" > "$_tf" 2>/dev/null; echo $? > "$_tf.rc" ) &
    _rpid=$!
    _i=0
    while [ "$_i" -lt "$_to" ]; do
        [ -f "$_tf.rc" ] && break
        sleep 1
        _i=`expr $_i + 1`
    done
    if [ -f "$_tf.rc" ]; then
        RT_RC=`cat "$_tf.rc" 2>/dev/null`
        RT_OUT=`cat "$_tf" 2>/dev/null`
    else
        kill "$_rpid" 2>/dev/null
        RT_RC=124
        RT_OUT=""
    fi
    rm -f "$_tf" "$_tf.rc" 2>/dev/null
}

#-----------------------------------------------------------------------------
# TWGCB 對應表
#
# 【重要】TWGCB（政府組態基準）**沒有發布 AIX 版**（已發布的是 Windows 10/11、
# Windows Server、RHEL 8/9、Ubuntu、macOS）。所以本檔的 ID 一律是本行自訂的
# FCB-AIX-xxxx，**絕不可以自己編一個 TWGCB 號碼**——那是造假，稽核一問就破。
#
# 能做的是「語意對應」：AIX 這一條，等同於 RHEL TWGCB 的哪一條。
# 只列**語意確實相同**的，在「標準設定值」欄末加註 (對應 TWGCB-xxx)。
# 語意只是接近的（例如 AIX minother 涵蓋數字＋符號，RHEL 是分開兩條）標 (近似 ...)，
# 沒有對應的就不加註——不硬湊。
#-----------------------------------------------------------------------------
# CIS Controls v8 控制項。直接照 AIX檢核表.XLS 的「類別」欄搬過來，不是我猜的：
#   1~85、97 = Secure Configuration of Enterprise Assets and Software（控制項 4）共 86 條
#   86~96、98 = Account Management（控制項 5）共 12 條
# 註：檢核表**沒有 CIS Benchmark 的條號欄**（例如 2.3.1），所以這裡只標到控制項，
#     標不到條號。要補條號需要 CIS IBM AIX 7.2 Benchmark 原文件對照。
# CIS Benchmark 條號。來源：Tenable 公開的 CIS 認證稽核檔
# CIS_AIX_7.2_Benchmark_v1.0.0_Level_1（https://www.tenable.com/audits/CIS_AIX_7.2_Benchmark_v1.0.0_Level_1）
# 對應方式：條目**標題逐字相同**才對，不靠推測。
# 10 條（mrouted、NFS restrict/secure、NIS markers、hosts.equiv、CDE dtlogin、
# sshd Match、SNMP 三條 community）未出現在公開的 L1 清單，標「待查」——
# 那些可能屬 Level 2 或 Manual 項目。依使用者 2026-09-21 指示：
# 「有編號就先放進去，沒有得先用自訂，之後再補」——所以先給本行自訂序號
# A-01~A-10（依項次排序，固定不動），拿到 CIS 原文件再換成真條號。
# **不填猜的 CIS 號碼**，自訂號一眼看得出是自訂的。
# CIS Benchmark 條號。基準：**CIS IBM AIX 7 Benchmark v1.2.0**（官方 PDF，567 頁）。
#
# 2026-09-22 使用者拍板「以 1.2 為主」，所以 v1.2.0 是唯一基準，
# 畫面上不保留 v1.0.0 舊號——兩個版本的號同時出現，看的人不知道該引用哪個。
# 舊號只留在 AI/CIS條號對照_v1.0.0_to_v1.2.0.md 當變更紀錄。
#
# 對照方法：**用指令名／參數名／檔案路徑當錨點**，不用標題字串比。
# v1.2.0 把標題全改成 `Ensure ...` 句型（v1.0.0 是 `Disable writesrv`、`pop3`），
# 直接字串比對會大量落空。每一條都對照過 PDF 目錄的實際標題。
#
# 對不到的標本行自訂 A-xx，**不填猜的**。
cis_id_map() {
    _n=`echo "$1" | sed 's/FCB-AIX-0*//'`
    case "$_n" in
        73) echo " §4.1.1.2" ;;
        74) echo " §4.1.1.3" ;;
        75) echo " §4.1.1.4" ;;
        76) echo " §4.1.1.5" ;;
        77) echo " §4.1.1.8" ;;
        78) echo " §4.1.1.9" ;;
        79) echo " §4.1.1.12" ;;
        80) echo " §4.1.1.14" ;;
        81) echo " §4.1.1.15" ;;
        82) echo " §4.1.1.16" ;;
        70|72) echo " §4.1.2.4" ;;
        71) echo " §4.1.2.8" ;;
        51) echo " §4.2.3" ;;
        52) echo " §4.2.4" ;;
        56) echo " §4.2.5" ;;
        64|65|66) echo " §4.2.7" ;;
        1) echo " §4.3.1.1" ;;
        2) echo " §4.3.1.2" ;;
        3) echo " §4.3.1.5" ;;
        5) echo " §4.3.2.2" ;;
        6) echo " §4.3.2.3" ;;
        7) echo " §4.3.2.4" ;;
        8) echo " §4.3.2.5" ;;
        9) echo " §4.3.2.7" ;;
        10) echo " §4.3.2.8" ;;
        11) echo " §4.3.2.10" ;;
        12) echo " §4.3.2.11" ;;
        13|67) echo " §4.3.2.12" ;;
        14) echo " §4.3.2.13" ;;
        15) echo " §4.3.3.1" ;;
        16) echo " §4.3.3.2" ;;
        17) echo " §4.3.3.3" ;;
        23) echo " §4.3.4.2" ;;
        24) echo " §4.3.4.3" ;;
        25) echo " §4.3.4.5" ;;
        26) echo " §4.3.4.6" ;;
        27) echo " §4.3.4.7" ;;
        28) echo " §4.3.4.8" ;;
        29) echo " §4.3.4.10" ;;
        30) echo " §4.3.4.11" ;;
        31) echo " §4.3.4.12" ;;
        32) echo " §4.3.4.13" ;;
        34) echo " §4.3.4.14" ;;
        35) echo " §4.3.4.16" ;;
        36) echo " §4.3.4.17" ;;
        37) echo " §4.3.4.18" ;;
        33) echo " §4.3.4.19" ;;
        38) echo " §4.3.4.20" ;;
        39) echo " §4.3.4.21" ;;
        40) echo " §4.3.4.23" ;;
        41) echo " §4.3.4.24" ;;
        42) echo " §4.3.4.26" ;;
        43) echo " §4.3.4.31" ;;
        18) echo " §4.4.1.3" ;;
        19) echo " §4.4.1.4" ;;
        20) echo " §4.4.1.5" ;;
        21) echo " §4.4.1.6" ;;
        22) echo " §4.4.1.7" ;;
        44) echo " §4.5.2" ;;
        45) echo " §4.5.4" ;;
        46) echo " §4.5.5" ;;
        47) echo " §4.5.6" ;;
        50) echo " §4.5.7" ;;
        48) echo " §4.5.11" ;;
        49) echo " §4.5.15" ;;
        53) echo " §4.6.1.3" ;;
        54) echo " §4.6.3.1" ;;
        55) echo " §4.6.3.2" ;;
        60) echo " §4.6.3.4" ;;
        62) echo " §4.6.3.6" ;;
        97) echo " §4.6.3.8" ;;
        61) echo " §4.6.3.9" ;;
        58) echo " §4.6.3.10" ;;
        63) echo " §4.6.3.11" ;;
        59) echo " §4.6.3.12" ;;
        57) echo " §4.6.3.13" ;;
        68) echo " §4.7.1" ;;
        69) echo " §4.7.3" ;;
        83) echo " §4.8.4" ;;
        84) echo " §4.8.5" ;;
        85) echo " §4.8.6" ;;
        87) echo " §5.1.1" ;;
        88) echo " §5.1.3" ;;
        90) echo " §5.1.6" ;;
        86) echo " §5.2.2" ;;
        89) echo " §5.2.4" ;;
        96) echo " §5.2.5" ;;
        95) echo " §5.2.6" ;;
        98) echo " §5.2.7" ;;
        91) echo " §5.2.9" ;;
        92) echo " §5.2.11" ;;
        93) echo " §5.2.12" ;;
        94) echo " §5.2.13" ;;
        4) echo " §本行自訂 A-01（v1.2.0 查無對應）" ;;
        *) echo "" ;;
    esac
}

cis_map() {
    _n=`echo "$1" | sed 's/FCB-AIX-0*//'`
    case "$_n" in
        86|87|88|89|90|91|92|93|94|95|96|98) echo " [Controls v8 #5 帳號管理]" ;;
        *) echo " [Controls v8 #4 安全組態]" ;;
    esac
}

twgcb_map() {
    case "$1" in
        FCB-AIX-0013) echo " (對應 FCB-01-002-0006 不能安裝sendmail套件)" ;;
        FCB-AIX-0057) echo " (對應 TWGCB-01-012-0270 SSH PermitEmptyPasswords參數)" ;;
        FCB-AIX-0059) echo " (對應 TWGCB-01-012-0266 SSH MaxAuthTries參數)" ;;
        FCB-AIX-0067) echo " (對應 FCB-01-002-0006 不能安裝sendmail套件)" ;;
        FCB-AIX-0068) echo " (近似 FCB-01-002-0020 登入警語；RHEL 該條為 GNOME GUI)" ;;
        FCB-AIX-0069) echo " (對應 TWGCB-01-012-0218 登入失敗鎖定次數)" ;;
        FCB-AIX-0070) echo " (對應 TWGCB-01-012-0139 稽核日誌目錄所有權)" ;;
        FCB-AIX-0072) echo " (對應 TWGCB-01-008-0140 稽核日誌目錄權限)" ;;
        FCB-AIX-0079) echo " (近似 TWGCB-01-012-0138 稽核日誌檔案權限)" ;;
        FCB-AIX-0080|FCB-AIX-0081|FCB-AIX-0082) echo " (近似 TWGCB-01-012-0138 稽核日誌檔案權限)" ;;
        FCB-AIX-0085) echo " (近似 FCB-01-002-0020 登入警語；RHEL 該條為 GNOME GUI)" ;;
        FCB-AIX-0086) echo " (對應 TWGCB-01-012-0223 密碼最短使用期限)" ;;
        FCB-AIX-0087) echo " (對應 TWGCB-01-012-0072 帳號不使用空白密碼)" ;;
        FCB-AIX-0088) echo " (對應 TWGCB-01-012-0088 唯一的GID)" ;;
        FCB-AIX-0091) echo " (對應 TWGCB-01-012-0208 密碼最小長度)" ;;
        FCB-AIX-0092) echo " (近似 TWGCB-01-012-0211/0212 大小寫字母個數；AIX 不分大小寫)" ;;
        FCB-AIX-0093) echo " (近似 TWGCB-01-012-0210/0213 數字與特殊字元個數；AIX minother 兩者合計)" ;;
        FCB-AIX-0094) echo " (對應 TWGCB-01-012-0216 相同字元可連續使用個數)" ;;
        FCB-AIX-0095) echo " (對應 FCB-01-002-0026 密碼最長使用期限)" ;;
        FCB-AIX-0097) echo " (對應 TWGCB-01-012-0267 SSH IgnoreRhosts參數)" ;;
        *) echo "" ;;
    esac
}

#-----------------------------------------------------------------------------
# 「目前值」的輸出淨化
#
# 為什麼需要：2026-09-30 真機報告的 FCB-AIX-0068 herald 那一格整片亂碼——
# AIX 的 /etc/security/login.cfg 裡 herald 的值帶了**非 UTF-8 的位元組**
# （Big5 中文、控制字元、填充字元都有），腳本直接把原始位元組倒進報告，
# 結果 HTML 那一格是問號方塊、還把欄寬撐爆到整張表看不了。
#
# 三件事，**只改呈現、不碰系統**（這支是唯讀腳本，不去動 herald）：
#   1. 控制字元（含換行、Tab）一律壓掉——控制字元不可能是合法設定值。
#   2. **只把不合法的那幾個位元組**換成 `.`，合法的一律原樣保留。
#      為什麼不是「整段判斷、整段替換」：腳本自己產的 Error 訊息是中文，
#      而同一格裡常常是「一段好中文 + 幾個壞位元組」。整段替換會把好的一起毀掉
#      ——2026-10-01 真機上就是這樣毀了 21 條（詳見下面那段）。
#   3. 太長就截斷，保留頭尾、中間標明原長度。
#      保留尾巴是因為 herald 這種值「結尾」常常才是重點（版本、簽名行）。
#-----------------------------------------------------------------------------
SAN_MAX=400       # 目前值超過幾個位元組就截斷
SAN_HEAD=240
SAN_TAIL=80

#-----------------------------------------------------------------------------
# UTF-8 合法性檢查：**自己逐位元組做，不呼叫 iconv**
#
# ⚠️⚠️ 2026-10-01 真機事故，這段註解不要刪：
#
# 原本用 `iconv -f UTF-8 -t UTF-8` 的離開碼判斷「這段字串是不是合法 UTF-8」，
# 不合法才把非 ASCII 換成點。在 Windows/MSYS 上測都是對的。
#
# 上到 AIX 7.2 真機之後，FCB-AIX-0023~0043 共 **21 條**的「目前值」整片變成點：
#
#   .........: ..........................inetd .......... （原值含非 UTF-8 位元組，已以 . 取代）
#
# 決定性的證據是**同一行後半段那句註記的中文是好的**——那是這支腳本自己加的字。
# 所以檔案編碼沒問題、傳輸也沒壞，是 **AIX 的 iconv 對合法輸入也回非 0**，
# 於是走了替換分支，把一整段合法中文毀掉。
#
# 教訓有兩層：
#   1. **不要拿外部指令的離開碼當語意判斷**。同一支指令在不同平台行為不一致，
#      而我們沒有那些平台可以驗——那等於拿正式機當測試環境。
#   2. **不要整段一刀切**。就算判斷對了，「整段不合法就整段替換」也會把同一段裡
#      合法的部分一起毀掉。只該換真正壞掉的那幾個位元組。
#
# 現在的做法：LC_ALL=C 下用 awk 逐位元組走過，自己認 UTF-8 的前導／後續位元組範圍，
# **只替換不合法的那幾個位元組**，合法的一律原樣保留。沒有任何外部相依，
# 行為在哪個平台都一樣（awk 只用到 substr/length/index/sprintf 這些 POSIX 基本功能）。
#
# ⚠️ 不用 gsub：gsub 替換字串裡的 & 語意在 gawk／mawk／AIX awk 之間不一致
#    （上一輪改 HTML 那段時踩過）。這裡連 gsub 都不用，只用 substr 拼字串。
# ⚠️ 不用 split(s, a, "")：用空字串當分隔符切成單字元**不是 POSIX 保證的行為**，
#    AIX 的 awk 不一定支援。改用 substr(s, i, 1) 逐一取，那是 POSIX 基本功能。
#
# 已知限制（誠實講）：只檢查「前導位元組 + 後續位元組範圍」這層結構，
# 不擋 overlong encoding 與 surrogate 範圍。目的是「不要毀掉好資料、把壞位元組標出來」，
# 不是做完整的 UTF-8 驗證器。
#-----------------------------------------------------------------------------

san_value() {
    _s="$1"
    # (1) 控制字元：換行/Tab/CR 壓成空白，其餘刪掉。
    #     控制字元不可能是合法設定值，而且會把 HTML 與終端機排版弄壞。
    _s=`printf '%s' "$_s" | LC_ALL=C tr '\011\012\013\014\015' '     ' | LC_ALL=C tr -d '\000-\010\016-\037\177'`
    [ -z "$_s" ] && return

    # (2)(3) 位元組層級的 UTF-8 修正 + 過長截斷，一次 awk 做完（也少 fork 幾次）。
    printf '%s\n' "$_s" | LC_ALL=C awk -v MAX="$SAN_MAX" -v HEAD="$SAN_HEAD" -v TAIL="$SAN_TAIL" '
BEGIN {
    # 位元組值查表：ord(c) = 這個位元組的數值。
    # 1..255（0 進不了 awk 字串，前面 tr 也已經刪掉了）。
    TBL = ""
    for (i = 1; i < 256; i++) TBL = TBL sprintf("%c", i)
    BAD = 0
}
function ord(c) { return index(TBL, c) }
function fix(s,    n, i, j, b, bb, len, ok, c, out) {
    n = length(s)
    i = 1
    out = ""
    while (i <= n) {
        c = substr(s, i, 1)
        b = ord(c)
        if (b < 128) { out = out c; i++; continue }          # ASCII，直接留
        if (b >= 194 && b <= 223)      len = 2               # 2 位元組序列
        else if (b >= 224 && b <= 239) len = 3               # 3 位元組（中文在這）
        else if (b >= 240 && b <= 244) len = 4               # 4 位元組
        else { out = out "."; BAD = 1; i++; continue }       # 不可能是前導位元組
        if (i + len - 1 > n) { out = out "."; BAD = 1; i++; continue }
        ok = 1
        for (j = 1; j < len; j++) {
            bb = ord(substr(s, i + j, 1))
            if (bb < 128 || bb > 191) { ok = 0; break }       # 後續位元組必須是 10xxxxxx
        }
        if (ok) { out = out substr(s, i, len); i += len }     # 整個序列合法 -> 原樣保留
        else    { out = out "."; BAD = 1; i++ }               # 只換掉這一個壞位元組
    }
    return out
}
{
    t = fix($0)
    if (BAD) t = t " （原值含非 UTF-8 位元組，已以 . 取代）"
    n = length(t)
    if (n > MAX) {
        # 切點可能落在多位元組字中間；再跑一次 fix，落單的位元組會變成點，
        # 不會像以前那樣把整段好的中文一起毀掉。
        h  = fix(substr(t, 1, HEAD))
        tl = fix(substr(t, n - TAIL + 1))
        t = h " …（已截斷，原長 " n " 位元組）… " tl
    }
    print t
}
'
}

#-----------------------------------------------------------------------------
# 給操作者的三欄：查核指令 / 修正指令 / 生效方式
#
# 使用者要的是「看到一條 Non-Compliant，不用查手冊就能動手」：
# 我現在是什麼狀態（查核指令）、怎麼改（修正指令）、什麼時候生效（生效方式）。
#
# ⚠️⚠️ 這三欄的內容會被人**貼到正式金融主機上執行**，寫錯的代價跟其他欄位
#      完全不同等級。所以這裡的鐵律是：
#
#   (a) **只從這支腳本自己已經在用的東西推導，不另外發明。**
#       每一條的查核指令都是「這個檢查實際去讀的那個東西」的單條版本
#       （例如 ck_sec 讀 lssec -f X -s Y -a Z，查核指令就是那一行）；
#       修正指令是同一組管理指令的寫入版本（lssec->chsec、no -o->no -p -o）。
#       這樣就不會出現「報告叫你去改一個腳本根本沒在看的東西」。
#   (b) **推不出來的一律填「未確認」，不填看起來很像的指令。**
#       未確認有幾條就幾條——填錯的指令比空白危險得多。
#   (c) **腳本自己絕對不執行這些指令。** 這支是唯讀的，修正指令只是報告裡的文字。
#
# 生效方式是**受控詞彙**，不是自由文字——因為「改完要重開機的有哪幾條」
# 是排維護窗口要的數字，自由文字問不出這個數字。只能是這幾個值：
#   立即生效 / 下次登入生效 / 需重啟服務：<服務名> / 需重開機 /
#   需 bosboot + 重開機 / 未確認
#-----------------------------------------------------------------------------
FIX_CHK=""; FIX_CMD=""; FIX_EFF=""; FIX_KIND=""

# set_fix <查核指令> <修正指令> <生效方式>   —— 下一個 emit 會取用並清空
#
# ⚠️⚠️ 這三個值裡**絕對不可以出現分號**。分號是報告的欄位分隔字元，emit 會把它
#      換成逗號當安全網——換完那行指令就**不再是可以貼去跑的指令了**
#      （`a ; b` 變成 `a , b`，貼上去直接語法錯誤）。
#      2026-09-30 自己踩過：rc.tcpip 的 `grep … ; ps …` 與 chmod/chown 都中招。
#      要串接指令一律用 `&&` 或 `||`，或者拆成「1) … 2) …」的步驟文字。
# set_fix <查核指令> <修正指令> <生效方式> <修正指令類型>
#
# 第 4 個參數（2026-10-01 加）只有兩個值：
#   可執行 = 這一格**貼到 shell 就能跑**，而且重跑是安全的（冪等）
#   步驟   = 給人看的步驟說明，貼上去不會有任何效果
#
# 為什麼要分：HTML 的「複製」鈕等於一個承諾——按下去貼上就能跑。
# 使用者看到 timed 那條的複製鈕時問「這個複製沒意義? 夾帶說明太多」，
# 就是因為那格是步驟說明卻掛了複製鈕。**按鈕做不到它承諾的事，比沒有按鈕更糟。**
#
# 「可執行」的三條規矩（全是為了過稽核，一條都不能省）：
#   1. 開頭先 grep/test 判斷，全程用 && 串
#      -> 條件不成立整串不動；已經改過再跑一次是 no-op，不會重複加 #
#   2. 改系統檔之前一定 cp -p 備份，備份檔名帶時間戳
#      -> 出事回得去；稽核問「改之前留底了嗎」有答案
#   3. 暫存檔放**跟目標檔同一個目錄**，絕對不放 /tmp
#      -> /tmp 世界可寫，root 從那裡讀內容寫進系統檔 = 任何本機帳號都能插隊 = 本機提權
#      用 `cat 新檔 > 原檔` 不用 mv：mv 會換 inode，權限與擁有者可能跟著跑掉
#
# ⚠️ AIX 沒有 sed -i（GNU 擴充），一定要走「導到新檔再倒回去」。
# ⚠️ 指令裡不可以有分號（欄位分隔字元），一律用 &&。
set_fix() { FIX_CHK="$1"; FIX_CMD="$2"; FIX_EFF="$3"; FIX_KIND="$4"; }

# cmp_text <屬性名> <期望值> <比較方式> <單位(可省略)>
#
# 使用者看到 minage 的標準設定值寫「1 週以上」，問「**一週以上是數字多少?**」——
# 他是這個系統的負責人，連他都要問，老闆與稽核更不可能知道。
# 報告要同時給出三件事：參數名、具體數字、單位。
#
# ⚠️ 數字與比較符號**一律從判定用的同一組參數生出來**（$2 期望值、$3 比較方式），
#    絕對不另外手寫一份。手寫的那份跟判定邏輯一漂掉，報告就會說謊——
#    那是 09-24「版本戳停在過去」的同型問題。
#    單位（週／次／字元）是原本不存在的新資訊，沒有第二份可以漂，所以由呼叫端提供。
cmp_text() {     # $1=屬性 $2=期望值 $3=eq/le/ge $4=單位
    case "$3" in
        ge) _cop=">=" ; _cwd="至少" ;;
        le) _cop="<=" ; _cwd="最多" ;;
        *)  _cop="="  ; _cwd="要等於" ;;
    esac
    if [ -n "$4" ]; then
        echo "$1 $_cop $2，單位為$4，即$_cwd $2 $4"
    else
        echo "$1 $_cop $2"
    fi
}

# ls -l 的權限字串（例如 -rw-r-----）轉八進位（640），給 chmod 用。
# 只處理 rwx 九碼；setuid/setgid/sticky 不處理——本檔 12 條 ck_perm 的期望值
# 都沒有那些位元，真的出現時會轉錯，所以這裡只給 ck_perm 用，不要拿去別的地方。
sym2oct() {
    _sp=`echo "$1" | cut -c2-10`
    _res=""
    for _g in 1 4 7; do
        _g2=`expr $_g + 1`; _g3=`expr $_g + 2`
        _v=0
        _c=`echo "$_sp" | cut -c$_g`
        [ "$_c" = "r" ] && _v=`expr $_v + 4`
        _c=`echo "$_sp" | cut -c$_g2`
        [ "$_c" = "w" ] && _v=`expr $_v + 2`
        _c=`echo "$_sp" | cut -c$_g3`
        [ "$_c" = "x" ] && _v=`expr $_v + 1`
        _res="$_res$_v"
    done
    echo "$_res"
}

#-----------------------------------------------------------------------------
# emit <id> <result> <category> <name> <standard> <current>
#   分號是欄位分隔字元，值裡面的分號一律換成逗號，換行壓成空白
#
#   2026-09-30：參考來源從「標準設定值」裡抽出來，變成**三個**獨立欄。
#
#   原本整串塞在一起：
#     `…，( 建議值 ) [CIS AIX 7 v1.2.0 §4.7.1] [Controls v8 #4 安全組態] (近似 FCB-01-002-0020 …)`
#
#   ⚠️ 這三段是**三條不同軸線**，混在一欄裡不只難讀，而且沒辦法拿來篩選：
#     (1) CIS基準條號  = CIS AIX 7 Benchmark v1.2.0，平台專屬的逐條設定值。
#                        98 條幾乎條條不同 -> 這是**查對**用的鍵，不是篩選維度
#                        （拿來當下拉選單會有 98 個選項，等於沒有篩選）。
#     (2) CIS控制項    = CIS Critical Security Controls v8，高階控制族群。
#                        本檔只用到 #4 安全組態 與 #5 帳號管理，很多條共用
#                        -> 這才是好用的**篩選維度**（「安全組態這族還有幾條沒過」）。
#     (3) 本行對應條號 = 本行自己的控制編號（FCB-01-002-xxxx / TWGCB-01-012-xxxx），
#                        對內報告用的第三條軸。沒有對應就留空——**不硬湊**。
#   (1) 與 (2) 都是 CIS 出的，但是**兩份不同的出版品**，不可以混為一談。
#
#   抽完之後「標準設定值」只剩說明句與建議值，人看得完。
#-----------------------------------------------------------------------------
emit() {
    # ⚠️ 這些 tr/sed 一律加 LC_ALL=C，讓它們**按位元組**處理。
    #    2026-10-01 真機報告裡有「三位元組的中文少掉第一個位元組」的痕跡
    #    （例如「，」EF BC 8C 只剩 BC 8C）。那幾欄**不經過 san_value**，
    #    所以不是淨化造成的；經手的只有這裡的 tr/sed。
    #    AIX 的 tr/sed 在多位元組 locale 下處理多位元組字元是有名的會出事，
    #    而這裡要換的字元集（分號、空白）全是 ASCII，用 C locale 沒有副作用。
    #    ⚠️ 這是**推論**：沒有 AIX 可以重現，但成本低、風險低，先擋起來。
    _cur=`san_value "$6" | LC_ALL=C tr ';' ',' | LC_ALL=C sed 's/  */ /g' | LC_ALL=C sed 's/^ *//;s/ *$//'`
    [ -z "$_cur" ] && _cur="(無輸出)"
    # (1) CIS Benchmark 條號
    _sec=`cis_id_map "$1"`
    if [ -n "$_sec" ]; then
        _ref="CIS AIX 7 v1.2.0`echo "$_sec" | LC_ALL=C tr ';' ','`"
    else
        _ref="（v1.2.0 查無對應條號）"
    fi
    # (2) CIS Controls v8 控制族群。cis_map 回 " [Controls v8 #4 安全組態]"，剝掉中括號
    _ctl=`cis_map "$1" | LC_ALL=C sed 's/^ *\[//' | LC_ALL=C sed 's/\]$//' | LC_ALL=C tr ';' ','`
    # (3) 本行對應條號。twgcb_map 回 " (對應 FCB-…)"，剝掉小括號；沒有就留空
    # 「對應」與「近似」這兩個修飾詞**留在欄位值裡**，不丟掉也不搬到說明欄：
    #   對應 = 語意確實相同，可以直接引用；近似 = 只是接近（例如 AIX minother
    #   把數字與符號合計，RHEL 是分開兩條）。這是「這個對照能不能拿去交差」的
    #   強度資訊，不是贅字。把它留在值裡，排序時「對應…」會自己聚在一起。
    _org=`twgcb_map "$1" | LC_ALL=C sed 's/^ *(//' | LC_ALL=C sed 's/)$//' | LC_ALL=C tr ';' ','`
    [ -z "$_org" ] && _org="（本行查無對應條號）"
    _std=`echo "$5" | LC_ALL=C tr ';' ','`
    _nam=`echo "$4" | LC_ALL=C tr ';' ','`
    # 三個操作欄。沒有人 set_fix 過就是「未確認」——預設值刻意是「不知道」，
    # 不是空白，更不是猜一個。
    # 分號換成逗號是最後的安全網（分號是欄位分隔字元）。但被換過的指令
    # **已經不能直接貼去跑了**，所以不靜默換——加一句話講明白，
    # 不然操作者會拿一行壞掉的指令去試（2026-09-30 自己寫出過這種 bug）。
    # ⚠️ `echo x | LC_ALL=C tr '\012' ' '` 會把 echo 自己那個結尾換行也換成空白，
    #    所以空值出來是「一個空白」不是空字串，下面的 [ -z ] 就永遠不成立，
    #    「未確認」這個預設值等於失效（2026-09-30 實測到 28 條變成空白格）。
    #    一定要再去掉頭尾空白。
    _fchk=`echo "$FIX_CHK" | LC_ALL=C tr '\012' ' ' | LC_ALL=C sed 's/  */ /g' | LC_ALL=C sed 's/^ *//;s/ *$//'`
    _fcmd=`echo "$FIX_CMD" | LC_ALL=C tr '\012' ' ' | LC_ALL=C sed 's/  */ /g' | LC_ALL=C sed 's/^ *//;s/ *$//'`
    _feff=`echo "$FIX_EFF" | LC_ALL=C tr '\012' ' ' | LC_ALL=C sed 's/  */ /g' | LC_ALL=C sed 's/^ *//;s/ *$//'`
    case "$_fchk$_fcmd" in
        *';'*)
            _fchk=`echo "$_fchk" | LC_ALL=C tr ';' ','`
            _fcmd=`echo "$_fcmd" | LC_ALL=C tr ';' ','`
            _fcmd="$_fcmd 注意：原值含分號已改為逗號，這行不是可直接執行的指令"
            ;;
    esac
    _feff=`echo "$_feff" | LC_ALL=C tr ';' ','`
    [ -z "$_fchk" ] && _fchk="未確認"
    [ -z "$_fcmd" ] && _fcmd="未確認"
    [ -z "$_feff" ] && _feff="未確認"
    _fkind=`echo "$FIX_KIND" | LC_ALL=C tr ';' ',' | LC_ALL=C sed 's/^ *//;s/ *$//'`
    [ -z "$_fkind" ] && _fkind="未確認"
    # ⚠️ Not-Applicable 的三欄**不可以顯示「未確認」**。
    #   「未確認」的意思是「我不知道該下什麼指令」，跟「這條這台不適用」是兩件事；
    #   混用會讓「未確認」這個詞失去意義，而稽核就是靠這個詞分辨我們知道什麼、不知道什麼。
    #   為什麼給一句話而不是留白：留白在 .txt 裡跟「欄位漏了」分不出來。
    if [ "$2" = "Not-Applicable" ]; then
        _fchk="本台不適用"; _fcmd="本台不適用"; _feff="本台不適用"; _fkind="本台不適用"
    fi
    FIX_CHK=""; FIX_CMD=""; FIX_EFF=""; FIX_KIND=""
    #-------------------------------------------------------------------------
    # 欄位順序（2026-09-30 指揮官定案）
    #
    #   1~6  原本就有的六欄，**位置一個都沒動**：
    #        TWGCB-ID / 檢查結果 / 類別 / 原則設定名稱 / 標準設定值 / 目前值
    #   7~12 這次新增的六欄，**全部接在後面**：
    #        CIS基準條號 / CIS控制項 / 本行對應條號 / 查核指令 / 修正指令 / 生效方式
    #
    # 為什麼不是插在中間（我原本插在 5~7 與 9~11，改掉了）：
    #   .txt 是給系統吃的。照欄位標題解析的下游沒事，照位置解析的會整排錯位。
    #   DYN 走哪一條我們還不知道 -> 就不要讓這個問題有機會發生。
    #
    # 「目前值要放最後一欄才能吸收殘留分隔字元」怎麼辦？
    #   那個需求的前提是「某個欄位的值裡可能還有分號」。**本檔不可能**：
    #   上面每一個欄位都過了 tr ';' ','，id 是 FCB-AIX-nnnn、檢查結果是四個固定字串、
    #   類別是 CAT_* 常數，三者本來就不含分號。所以分號數一定剛好 11 個，
    #   「最後一欄吸收」這件事永遠不會被觸發，放哪一欄都一樣。
    #   吸收位置因此落到「生效方式」——那是受控詞彙，更不可能有分號。
    #   ⚠️ 這是**建立在「每欄都無分號」這個保證上**的，所以下面加一道守門：
    #      欄數不是 12 就代表保證破了，明確記進 debug log 並在該列留下記號，
    #      不要靜默產生一份錯位的報告。
    #-------------------------------------------------------------------------
    _line="$1;$2;$3;$_nam;$_std;$_cur;$_ref;$_ctl;$_org;$_fchk;$_fcmd;$_feff;$_fkind"
    _nf=`echo "$_line" | awk -F';' '{print NF}'`
    if [ "$_nf" -ne 13 ]; then
        dbg "!! 欄數守門：$1 切出 $_nf 欄（應為 13）——有欄位夾帶分號，該列已標記"
        _line="$_line 注意：欄數異常($_nf)：有欄位夾帶分號，這一列的欄位對應不可信"
    fi
    echo "$_line" >> "$tmp_file"
    # 未完成清單要當工作單用，所以把三個操作欄一起帶下去
    PEND_CHK="$_fchk"; PEND_CMD="$_fcmd"; PEND_EFF="$_feff"
    # 「沒完成」＝ Non-Compliant 與 Error。另存一份，收尾時附在純文字報告最後，
    # 讓人不用自己 grep 98 行去挑。⚠️ 這份清單**不可以有分號**——
    # 下游解析是「含分號的行就是一筆項目」，帶分號會被多算成項目、
    # 害「宣稱 98 實際 N」的截斷判定誤報。
    case "$2" in
        Non-Compliant|Error)
            case "$_feff" in
                *重開機*)   N_RB=`expr $N_RB + 1` ;;
                需重啟服務*) N_SVC=`expr $N_SVC + 1` ;;
                立即生效)    N_NOW=`expr $N_NOW + 1` ;;
                下次登入生效) N_LOGIN=`expr $N_LOGIN + 1` ;;
                需重設密碼時生效) N_PWD=`expr $N_PWD + 1` ;;
                *)          N_UNK=`expr $N_UNK + 1` ;;
            esac
            {
            echo "  $1  $2  $_nam"
            echo "      查核: $PEND_CHK"
            echo "      修正: $PEND_CMD"
            echo "      生效: $PEND_EFF"
            } >> "$pend_file"
            ;;
    esac
    N_SEQ=`expr $N_SEQ + 1`
    echo "[$N_SEQ/98] $1 $2 - $_nam"
    dbg "[$N_SEQ] $1 result=$2 name=$_nam"
    dbg "        std=$_std"
    dbg "        cur=$_cur"
    case "$2" in
        Compliant)      N_C=`expr $N_C + 1` ;;
        Non-Compliant)  N_N=`expr $N_N + 1` ;;
        Not-Applicable) N_A=`expr $N_A + 1` ;;
        *)              N_E=`expr $N_E + 1` ;;
    esac
}

#-----------------------------------------------------------------------------
# 共用檢查函式
#-----------------------------------------------------------------------------

# /etc/inittab 項目應為 off 或不存在
ck_inittab() {   # $1=id $2=顯示名稱（中文為主） $3=inittab ident $4=備註(可省略)
    _std="開機自動啟動的項目 $3，( inittab 無 $3 項目，或啟動欄位是 off )"
    # 不要用表意空格 U+3000：真機報告 FCB-AIX-0053 開頭那個菱形亂碼就是它
    #   （三位元組 E3 80 80 在輸出路徑被打壞）。一般空格就沒這個問題。
    [ -n "$4" ] && _std="$_std $4"
    # 查核／修正都用 AIX 的 inittab 指令家族（lsitab 讀、chitab 改、rmitab 刪），
    # 跟這個檢查實際讀的東西是同一個。rmitab 是整條移除，chitab 是改成 off；
    # 兩個都給，因為「這台還要不要留這個項目」是管理者的判斷，不是我的。
    # 生效方式：inittab 只管開機時要不要起，改了之後現在正在跑的那個行程不會停
    # ——所以標「需重開機」；要現在就停另外用 stopsrc，那是不同的事。
    # 可執行：lsitab 先判斷有沒有這個項目，有才刪 -> 已經刪過再跑一次是 no-op。
    set_fix "lsitab $3"             "lsitab $3 && rmitab $3"             "需重開機" "可執行"
    if have lsitab; then :; else
        emit "$1" "Error" "$CAT_SVC" "$2" "$_std" "無 lsitab 指令（非 AIX？）——未查"
        return
    fi
    _l=`lsitab "$3" 2>/dev/null`
    if [ -z "$_l" ]; then
        emit "$1" "Compliant" "$CAT_SVC" "$2" "$_std" "inittab 無 $3 項目"
    else
        _act=`echo "$_l" | cut -f3 -d:`
        if [ "$_act" = "off" ]; then
            emit "$1" "Compliant" "$CAT_SVC" "$2" "$_std" "已停用: $_l"
        else
            emit "$1" "Non-Compliant" "$CAT_SVC" "$2" "$_std" "$_l"
        fi
    fi
}

# /etc/rc.tcpip 的 start 行需註解，且行程不得在跑
# ——「設定關了」跟「現在沒在跑」是兩件事，兩個都要查（只查一個會誤判）
ck_rctcpip() {   # $1=id $2=顯示名稱 $3=daemon 檔名 $4=ps 比對字串(可省略)
    _pat="$4"; [ -z "$_pat" ] && _pat="$3"
    _std="網路服務 $3 的開機自啟與執行狀態，( rc.tcpip 的 $3 那行已註解，且 ps 查不到 $3 )"
    # 這個檢查看兩件事（設定檔 + 行程），所以查核指令也給兩段——只看一段會誤判。
    # 修正也是兩步：註解掉開機啟動、再把現在正在跑的停掉。
    # ⚠️ 不給 sed 一鍵改 /etc/rc.tcpip：那是系統檔，盲改不留備份在金融環境不可接受，
    #    而且這支腳本是唯讀的，不該示範「拿去直接改系統檔」的一行指令。
    # 查核指令用 `||` 串（**不可以用分號**，見 set_fix 的警告）：
    #   先看 rc.tcpip 還會不會啟動它；沒有那行才去看行程現在在不在。
    #   **兩段都沒有輸出＝這一條合規**，有輸出就是那一段的原文。
    # 生效方式：stopsrc 下去就停了，rc.tcpip 那行管的是下次開機 -> 兩步做完即刻生效。
    # 可執行。四個設計點都在：先 grep 判斷（已註解過就整串不動）、cp -p 帶時間戳備份、
    # 暫存檔放同目錄不放 /tmp（那是世界可寫，root 從那裡讀內容寫進系統檔就是本機提權）、
    # 用 cat 倒回去不用 mv（mv 會換 inode，權限與擁有者可能跑掉）。
    # AIX 沒有 sed -i，所以走「導到新檔再倒回去」。
    set_fix "grep -nE '^[[:space:]]*start[[:space:]].*/$3' /etc/rc.tcpip || ps -ef | grep -v grep | grep -w $3"             "grep -nE '^[[:space:]]*start[[:space:]].*/$3' /etc/rc.tcpip && cp -p /etc/rc.tcpip /etc/rc.tcpip.bak.\$(date +%Y%m%d%H%M) && sed 's|^\([[:space:]]*start[[:space:]].*/$3\)|#|' /etc/rc.tcpip > /etc/rc.tcpip.new.\$\$ && cat /etc/rc.tcpip.new.\$\$ > /etc/rc.tcpip && rm -f /etc/rc.tcpip.new.\$\$ && stopsrc -s $3"             "立即生效" "可執行"
    if [ ! -f /etc/rc.tcpip ]; then
        emit "$1" "Error" "$CAT_SVC" "$2" "$_std" "讀不到 /etc/rc.tcpip"
        return
    fi
    _live=`grep -E "^[ 	]*start[ 	]+[^ 	]*/$3([ 	]|$)" /etc/rc.tcpip 2>/dev/null | head -n 1`
    _cmt=`grep -E "^[ 	]*#[ 	]*start[ 	]+[^ 	]*/$3([ 	]|$)" /etc/rc.tcpip 2>/dev/null | head -n 1`
    # ps -ef 全機只跑一次（下面 PS_SNAP 快取）。AIX LPAR 上行程數可能上萬，
    # 13 個項目各 fork 一次 ps 是沒必要的負擔。
    _run=`echo "$PS_SNAP" | grep -v grep | grep -w "$_pat" | head -n 1`
    # 只留「使用者 PID 指令名」三欄——ps 的完整命令列可能夾帶密碼參數，不可落進報表
    [ -n "$_run" ] && _run=`echo "$_run" | awk '{print $1, $2, $8}'`
    if [ -n "$_live" ]; then
        emit "$1" "Non-Compliant" "$CAT_SVC" "$2" "$_std" "rc.tcpip 仍會啟動: $_live"
    elif [ -n "$_run" ]; then
        emit "$1" "Non-Compliant" "$CAT_SVC" "$2" "$_std" "rc.tcpip 已註解但行程仍在執行: $_run"
    elif [ -n "$_cmt" ]; then
        emit "$1" "Compliant" "$CAT_SVC" "$2" "$_std" "已註解: $_cmt"
    else
        emit "$1" "Compliant" "$CAT_SVC" "$2" "$_std" "rc.tcpip 無 $3 項目，行程亦未執行"
    fi
}

# /etc/inetd.conf 服務需註解或移除
# ⚠️ 2026-09-23 自我稽核 A4：這支原本跟 ck_rctcpip **兩套標準**——
#   設定檔不存在：ck_rctcpip 判 Error，這裡判 Compliant
#   行程檢查：    ck_rctcpip 有查，這裡完全沒查
# 而 ck_rctcpip 的註解白紙黑字寫著「只關設定沒關行程等於沒關」。
# 同一份腳本兩套標準，改成一套（三態分清楚）：
#   設定檔不存在 -> Not-Applicable（**這是觀察到的事實，不是觀察失敗**，所以不是 Error）
#   存在但讀不到 -> Error（這才叫沒查到）
#   其餘        -> 比對設定，**而且一定要查行程**
ck_inetd() {     # $1=id $2=顯示名稱 $3=服務名
    _std="inetd 隨連線啟動的服務 $3，( inetd.conf 的 $3 那行已註解或移除 )"
    # chsubserver 是 AIX 管 inetd 子服務的指令，但它要帶協定（tcp/udp）與 socket 型別，
    # 我這裡**沒有那些資訊**（檢查只比對服務名），亂帶會改錯條目。
    # 所以修正給的是「註解掉再 refresh inetd」——那是 inetd.conf 的通用做法，
    # 而且跟這個檢查實際看的東西（inetd.conf 那一行）完全對得起來。
    set_fix "grep -nE '^[[:space:]]*$3[[:space:]]' /etc/inetd.conf"             "grep -nE '^[[:space:]]*$3[[:space:]]' /etc/inetd.conf && cp -p /etc/inetd.conf /etc/inetd.conf.bak.\$(date +%Y%m%d%H%M) && sed 's|^\([[:space:]]*$3[[:space:]]\)|#|' /etc/inetd.conf > /etc/inetd.conf.new.\$\$ && cat /etc/inetd.conf.new.\$\$ > /etc/inetd.conf && rm -f /etc/inetd.conf.new.\$\$ && refresh -s inetd"             "需重啟服務：inetd" "可執行"
    if [ ! -f /etc/inetd.conf ]; then
        # 這裡原本判 Not-Applicable，2026-10-01 改成 Compliant。
        # 規範要求的是「這個服務不要被啟用」。inetd 的設定檔根本不存在，
        # 就代表這個服務確實沒有被啟用——**要求做到了，那是合規，不是不適用**。
        # 標成不適用會把使用者做對的事從成績單上拿掉，合規率被低估。
        # （「檔案不存在」是查證過的事實，跟「檔案在但讀不到」不同，後者判 Error。）
        emit "$1" "Compliant" "$CAT_SVC" "$2" "$_std" "無 /etc/inetd.conf，inetd 未設定任何服務"
        return
    fi
    if [ ! -r /etc/inetd.conf ]; then
        emit "$1" "Error" "$CAT_SVC" "$2" "$_std" "/etc/inetd.conf 讀不到（權限不足）——未查"
        return
    fi
    # 行程：inetd 起的服務多半是**隨連線產生的短命行程**，所以
    #   掃到  -> 確定在跑（強證據）-> Non-Compliant，不管設定檔怎麼寫
    #   掃不到 -> 只代表這一瞬間沒有連線（弱證據）-> 不能拿來當合規依據
    # 比對的是指令名那一欄的開頭（telnet -> telnetd、ftp -> ftpd），不比整條命令列：
    # 完整命令列可能夾帶密碼參數，不可落進報表（同 ck_rctcpip）。
    # 用 awk -v 把樣式傳進去，不要在 awk 程式裡插 shell 變數：
    # 巢狀引號（反引號裡再套單引號雙引號）在 ksh88 很容易解析成別的東西，
    # 而這支腳本沒有測試機，第一次跑就是正式機。
    # 取第 8 欄（ps -ef 的指令欄）不是 $NF——$NF 是**最後一個參數**，不是指令名。
    _ipat="^$3"'d?$'
    _rp=`echo "$PS_SNAP" | grep -v grep | awk -v pat="$_ipat" '{n=split($8,p,"/"); c=p[n]; if (c ~ pat) print $1, $2, c}' | head -n 1`
    _l=`grep -E "^[ 	]*$3[ 	]" /etc/inetd.conf 2>/dev/null | head -n 1`
    if [ -n "$_l" ]; then
        emit "$1" "Non-Compliant" "$CAT_SVC" "$2" "$_std" "inetd.conf 仍啟用: $_l"
    elif [ -n "$_rp" ]; then
        emit "$1" "Non-Compliant" "$CAT_SVC" "$2" "$_std" "inetd.conf 已關但行程仍在執行: $_rp"
    else
        _c=`grep -E "^[ 	]*#[ 	]*$3[ 	]" /etc/inetd.conf 2>/dev/null | head -n 1`
        if [ -n "$_c" ]; then
            emit "$1" "Compliant" "$CAT_SVC" "$2" "$_std" "已註解: $_c（行程未執行）"
        else
            emit "$1" "Compliant" "$CAT_SVC" "$2" "$_std" "inetd.conf 無 $3 項目（行程未執行）"
        fi
    fi
}

# 網路參數 no -o <tunable> 需等於期望值
ck_no() {        # $1=id $2=顯示名稱（中文為主） $3=tunable $4=期望值
    # restricted tunable 需改用 no -r -o 並 bosboot -a 後重開機。
    # 這句原本在「標準設定值」欄，2026-10-01 使用者說「備註太多」，
    # 改放進 fcb_aix使用說明.md 的技術名詞表，報告裡不再重複 7 次。
    _std="核心網路參數 $3，( $3 = $4 )"
    # ⚠️ no 的「改執行期」跟「改永久」是不同指令，只寫一半等於沒改：
    #     no -o x=v        只改現在，重開機就回去了
    #     no -p -o x=v     同時寫進 /etc/tunables/nextboot，重開機還在  <- 要的是這個
    #     no -r -o x=v     restricted tunable 專用，改完還要 bosboot -a 再重開機
    #   哪些是 restricted 我**沒有把握**（要 AIX 原機的 no -F -o 才看得出來），
    #   所以修正指令給 -p 這版並明講：如果 no 回「restricted」，那條要改走 -r + bosboot，
    #   生效方式也要跟著變成「需 bosboot + 重開機」。這句提醒不是廢話，是我不知道的部分。
    # 指令保持純粹（能直接貼），restricted tunable 的提醒移到標準設定值那一欄——
    # 夾在指令裡的說明文字會讓「複製」變成沒意義，那正是使用者抱怨的事。
    set_fix "no -o $3"             "no -p -o $3=$4"             "立即生效" "可執行"
    if have no; then :; else
        emit "$1" "Error" "$CAT_SYS" "$2" "$_std" "無 no 指令（非 AIX？）——未查"
        return
    fi
    _o=`no -o "$3" 2>/dev/null`
    if [ -z "$_o" ]; then
        emit "$1" "Error" "$CAT_SYS" "$2" "$_std" "no -o $3 無輸出（受限參數需 root，或此版本無此參數）"
        return
    fi
    _v=`echo "$_o" | sed 's/.*= *//' | sed 's/ *$//'`
    if [ "$_v" = "$4" ]; then
        emit "$1" "Compliant" "$CAT_SYS" "$2" "$_std" "$_o"
    else
        emit "$1" "Non-Compliant" "$CAT_SYS" "$2" "$_std" "$_o"
    fi
}

# 檔案/目錄 權限 + 擁有者 + 群組
# 說明欄的寫法照行內既有慣例（使用者 2026-09-21 貼的 RHEL 實例）：
#   「<完整句子>，( <建議值> )」，目前值則是「無異常: ( <實際值> )」。
# 不要自己發明分隔符——三個平台共用同一個慣例，解析器才能一套通吃。
ck_perm() {      # $1=id $2=類別 $3=顯示名稱（中文為主） $4=路徑 $5=期望權限 $6=owner $7=group $8=檔案不存在時判什麼（error/compliant/na，見上面的判準）
    # ⚠️ _oct 要先算，因為下面的標準設定值要把八進位印出來給人看。
    # chmod/chown 的參數直接從這個檢查的**期望值**轉出來（sym2oct 只是把
    # -rw-r----- 換算成 640），不是另外查的，所以不會跟判定標準不一致。
    _oct=`sym2oct "$5"`
    _std="$4 的權限與擁有者，( 權限 $5（八進位 $_oct），擁有者 $6，群組 $7 )"
    set_fix "ls -ld $4"             "chmod $_oct $4 && chown $6:$7 $4"             "立即生效" "可執行"
    if [ ! -e "$4" ]; then
        # 逐條看這個檔在 AIX 上是不是本來就該存在，判準見上面那段註解。
        case "$8" in
            error)
                emit "$1" "Error" "$2" "$3" "$_std" "$4 不存在（AIX 標準組態，應該存在）——未查到權限" ;;
            compliant)
                emit "$1" "Compliant" "$2" "$3" "$_std" "$4 不存在（此檔非必要），沒有被讀走或竄改的風險" ;;
            *)
                emit "$1" "Not-Applicable" "$2" "$3" "$_std" "$4 不存在——對應服務啟用才會產生，本台未啟用，不適用" ;;
        esac
        return
    fi
    _l=`ls -ld "$4" 2>/dev/null`
    if [ -z "$_l" ]; then
        emit "$1" "Error" "$2" "$3" "$_std" "ls -ld 讀不到 $4"
        return
    fi
    _p=`echo "$_l" | awk '{print $1}' | cut -c1-10`
    _o=`echo "$_l" | awk '{print $3}'`
    _g=`echo "$_l" | awk '{print $4}'`
    if [ "$_p" = "$5" ] && [ "$_o" = "$6" ] && [ "$_g" = "$7" ]; then
        emit "$1" "Compliant" "$2" "$3" "$_std" "$_l"
    else
        emit "$1" "Non-Compliant" "$2" "$3" "$_std" "$_l"
    fi
}

# sshd_config / ssh_config 關鍵字
ck_sshd() {      # $1=id $2=顯示名稱 $3=關鍵字 $4=期望值(egrep pattern) $5=標準描述
    _f=/etc/ssh/sshd_config
    # $4 是 egrep 樣式（可能是 yes|shosts-only 這種多選一），不能整串當設定值寫進去。
    # 取第一個選項當建議值——它是可接受值之一，不是我另外挑的。
    _sv=`echo "$4" | cut -d'|' -f1`
    # 可執行：先把同名設定整批刪掉、再補一行正確的 -> 重跑兩次結果一樣（冪等）。
    set_fix "grep -nEi '^[[:space:]]*$3[[:space:]]' $_f"             "cp -p $_f $_f.bak.\$(date +%Y%m%d%H%M) && grep -vEi '^[[:space:]]*$3[[:space:]]' $_f > $_f.new.\$\$ && echo '$3 $_sv' >> $_f.new.\$\$ && cat $_f.new.\$\$ > $_f && rm -f $_f.new.\$\$ && stopsrc -s sshd && startsrc -s sshd"             "需重啟服務：sshd" "可執行"
    if [ ! -f "$_f" ]; then
        emit "$1" "Error" "$CAT_SSH" "$2" "$5" "讀不到 $_f"
        return
    fi
    _l=`grep -Ei "^[ 	]*$3[ 	]" "$_f" 2>/dev/null | tail -n 1`
    if [ -z "$_l" ]; then
        emit "$1" "Non-Compliant" "$CAT_SSH" "$2" "$5" "未明示 $3（吃預設值）"
        return
    fi
    _v=`echo "$_l" | awk '{print $2}'`
    if echo "$_v" | grep -Eiq "$4"; then
        emit "$1" "Compliant" "$CAT_SSH" "$2" "$5" "$_l"
    else
        emit "$1" "Non-Compliant" "$CAT_SSH" "$2" "$5" "$_l"
    fi
}

# lssec 取值並與期望比較（$5: eq / le / ge）
ck_sec() {       # $1=id $2=顯示名稱 $3="檔案 節 屬性" $4=期望值 $5=比較方式 $6=標準描述 $7=單位(可省略)
    set -- "$1" "$2" "$3" "$4" "$5" "$6" "$7"
    _id=$1; _nm=$2; _spec=$3; _exp=$4; _op=$5; _std=$6; _unit=$7
    _f=`echo "$_spec" | awk '{print $1}'`
    _s=`echo "$_spec" | awk '{print $2}'`
    _a=`echo "$_spec" | awk '{print $3}'`
    # lssec 讀、chsec 寫，是同一組指令的兩面，參數一模一樣——這是這三欄裡
    # 我最有把握的一類（使用者舉的 maxrepeats 例子就是這一類）。
    # 生效方式**逐條判斷**，不照檔案一刀切。
    # 2026-09-30 指揮官核准新增受控詞彙「需重設密碼時生效」，理由：
    #   原本這 8 條全標「下次登入生效」，但那會讓人以為使用者下次登入就合規，
    #   實際上既有密碼不會回頭被檢查——排時程的人照那個排，稽核時才發現沒生效。
    #   用一個最接近但不精確的值填掉，等於把不知道藏起來。
    #
    # 判斷依據（逐條，不是整批套）：
    #   minlen / minalpha / minother / maxrepeats / mindiff
    #       = 密碼**組成**規則，passwd 在「設定一個新密碼」那一刻才檢查
    #       -> 需重設密碼時生效
    #   minage
    #       = 「距上次改密碼多久之內不准再改」，同樣在**改密碼那一刻**被檢查
    #       -> 需重設密碼時生效
    #   loginretries
    #       = 連續登入失敗幾次就鎖，在**登入時**比對 unsuccessful_login_count
    #       -> 下次登入生效
    #   maxage / maxexpired / histsize / histexpire
    #       = 密碼到期與歷史。我**分不清**它到底是「下次登入判斷到期時」生效、
    #         還是別的時機，而且它對既有密碼是用 lastupdate 回推的
    #       -> 未確認。**不硬塞進最接近的格子**——這一欄存在的理由就是不要猜。
    case "$_a" in
        minlen|minalpha|minother|maxrepeats|mindiff)  _eff="需重設密碼時生效" ;;
        minage)                                        _eff="需重設密碼時生效" ;;
        loginretries|account_locked)                   _eff="下次登入生效" ;;
        *)                                             _eff="未確認" ;;
    esac
    # 標準設定值的「合規條件」由 cmp_text 從判定用的同一組參數生出來，
    # 所以報告上寫的數字永遠等於實際拿來判定的數字。
    _std="$_std，( `cmp_text "$_a" "$_exp" "$_op" "$_unit"` )"
    set_fix "lssec -f $_f -s $_s -a $_a" "chsec -f $_f -s $_s -a $_a=$_exp" "$_eff" "可執行"
    if have lssec; then :; else
        emit "$_id" "Error" "$CAT_ACC" "$_nm" "$_std" "無 lssec 指令（非 AIX？）——未查"
        return
    fi
    _o=`lssec -f "$_f" -s "$_s" -a "$_a" 2>/dev/null | tail -n 1`
    if [ -z "$_o" ]; then
        emit "$_id" "Error" "$CAT_ACC" "$_nm" "$_std" "lssec -f $_f -s $_s -a $_a 無輸出（需 root 或屬性不存在）"
        return
    fi
    _v=`echo "$_o" | sed 's/.*=//' | tr -d '"' | sed 's/ *//g'`
    if [ -z "$_v" ]; then
        emit "$_id" "Non-Compliant" "$CAT_ACC" "$_nm" "$_std" "$_o （值為空，未設定）"
        return
    fi
    _ok=0
    case "$_op" in
        eq) [ "$_v" = "$_exp" ] && _ok=1 ;;
        le) echo "$_v" | grep -Eq '^[0-9]+$' && [ "$_v" -le "$_exp" ] && _ok=1 ;;
        ge) echo "$_v" | grep -Eq '^[0-9]+$' && [ "$_v" -ge "$_exp" ] && _ok=1 ;;
    esac
    if [ "$_ok" -eq 1 ]; then
        emit "$_id" "Compliant" "$CAT_ACC" "$_nm" "$_std" "$_o"
    else
        emit "$_id" "Non-Compliant" "$CAT_ACC" "$_nm" "$_std" "$_o"
    fi
}

#=============================================================================
# 表頭（"Check stated at" 的拼字沿用 RHEL 版，
#      不要「順手改成 started」——DYN 那邊是照字串比對的）
#
# 欄位順序自 2026-09-30 起比 fcbrhelsh 多六欄，**全部接在原六欄之後**（第 7~12）。
# 原本的 TWGCB-ID／檢查結果／類別／原則設定名稱／標準設定值／目前值 位置一個都沒動。
# 下游解析（config_audit.py）照欄位標題取值、不照位置；照位置的下游也不會錯位，
# 因為前六欄還在原本的地方。
#=============================================================================
: > "$tmp_file"
: > "$pend_file"
echo "系統組態檢查"                                     >> "$tmp_file"
echo "平台: AIX"                                        >> "$tmp_file"
echo "主機名: $HOSTNAME"                                >> "$tmp_file"
echo "IP地址: $myIP"                                    >> "$tmp_file"
echo "作業系統版本: $OSVER"                             >> "$tmp_file"
echo "核心版本: $KERNEL"                                >> "$tmp_file"
echo "腳本版本: $SCRIPTV"                               >> "$tmp_file"
echo "檢核基準: CIS IBM AIX 7 Benchmark v1.2.0（本行 AIX檢核表 98 條；條號已對照 97 條，1 條 v1.2.0 查無對應、暫用本行自訂）。TWGCB 未發布 AIX 版，故 ID 為本行自訂 FCB-AIX-xxxx" >> "$tmp_file"
T_START=`date '+%Y-%m-%d %H:%M:%S'`
echo "Check stated at: $T_START"                        >> "$tmp_file"
echo "Check runas: $RUNAS"                              >> "$tmp_file"
if [ "`uname -s`" != "AIX" ]; then
    echo "Check warning: 本機 uname -s = `uname -s`，不是 AIX。AIX 專屬指令無法執行的項目一律標 Error，這份結果不可當成正式檢核。" >> "$tmp_file"
fi
echo "TWGCB-ID;檢查結果;類別;原則設定名稱;標準設定值;目前值;CIS基準條號;CIS控制項;本行對應條號;查核指令;修正指令;生效方式;修正指令類型" >> "$tmp_file"

#=============================================================================
# 環境自檢——第一次在公司 AIX 盲跑時，這一段的畫面／LOG 就是診斷依據
#=============================================================================
echo "=============================================================="
echo " FCB AIX 組態檢核  $SCRIPTV"
echo " 主機: $HOSTNAME   IP: $myIP"
echo " uname -s: `uname -s`   oslevel -s: $OSLEVEL"
echo " 執行身分: $RUNAS"
echo " shell: $0   \$KSH_VERSION=${KSH_VERSION:-(未設定，可能是 ksh88)}"
echo "-------------------------------------------------------------- "
echo " 指令自檢（缺哪個，對應項目就會是 Error）:"
for _c in lsitab lssrc lssec no showmount lsfs bootinfo oslevel ifconfig ssh awk sed grep; do
    if have "$_c"; then _st="OK  "; else _st="缺少"; fi
    echo "   [$_st] $_c"
    dbg "HAVE $_c = $_st"
done
echo "-------------------------------------------------------------- "
echo " 關鍵檔案:"
for _f in /etc/inittab /etc/rc.tcpip /etc/inetd.conf /etc/exports /etc/ssh/sshd_config /etc/security/user /etc/security/login.cfg /etc/security/passwd; do
    if [ -r "$_f" ]; then _st="可讀"; elif [ -e "$_f" ]; then _st="不可讀"; else _st="不存在"; fi
    echo "   [$_st] $_f"
    dbg "FILE $_f = $_st"
done
echo "=============================================================="
echo ""

#=============================================================================
# 1-4　inittab 啟動項
#=============================================================================
ck_inittab "FCB-AIX-0001" "關閉開機自動啟動：writesrv"   "writesrv"
ck_inittab "FCB-AIX-0002" "關閉開機自動啟動：dt"          "dt"
ck_inittab "FCB-AIX-0003" "關閉開機自動啟動：rc.nfs"      "rcnfs"
ck_inittab "FCB-AIX-0004" "關閉開機自動啟動：cas_agent"   "cas_agent"

#=============================================================================
# 5-17　rc.tcpip 常駐服務
#=============================================================================
ck_rctcpip "FCB-AIX-0005" "關閉網路服務：dhcpcd" "dhcpcd"
ck_rctcpip "FCB-AIX-0006" "關閉網路服務：dhcprd" "dhcprd"
ck_rctcpip "FCB-AIX-0007" "關閉網路服務：dhcpsd" "dhcpsd"
ck_rctcpip "FCB-AIX-0008" "關閉網路服務：gated" "gated"
ck_rctcpip "FCB-AIX-0009" "關閉網路服務：mrouted" "mrouted"
ck_rctcpip "FCB-AIX-0010" "關閉網路服務：named" "named"
ck_rctcpip "FCB-AIX-0011" "關閉網路服務：routed" "routed"
ck_rctcpip "FCB-AIX-0012" "關閉網路服務：rwhod" "rwhod"
ck_rctcpip "FCB-AIX-0013" "關閉網路服務：sendmail" "sendmail"
ck_rctcpip "FCB-AIX-0014" "關閉網路服務：timed" "timed"
ck_rctcpip "FCB-AIX-0015" "關閉網路服務：autoconf6" "autoconf6"
ck_rctcpip "FCB-AIX-0016" "關閉網路服務：ndpd-host" "ndpd-host"
ck_rctcpip "FCB-AIX-0017" "關閉網路服務：ndpd-router" "ndpd-router"

#=============================================================================
# 18-22　NFS
#   先判「這台到底有沒有在用 NFS」——沒在用就是 Not-Applicable，
#   不可以因為「沒設定」就算合規（那是兩件事）
#=============================================================================
NFS_EXPORTS_N=0
[ -f /etc/exports ] && NFS_EXPORTS_N=`grep -vE '^[ 	]*(#|$)' /etc/exports 2>/dev/null | wc -l | tr -d ' '`
if have lsfs; then
    NFS_MOUNTS=`lsfs -v nfs 2>/dev/null | grep -v '^Name' | grep -v '^$'`
    NFS_PROBE_OK=1
else
    NFS_MOUNTS=""
    NFS_PROBE_OK=0
fi

# 查核指令＝這個檢查實際 grep 的那一行；修正給步驟不給一行指令（改系統設定檔）。
# ⚠️ 生效方式標「未確認」是刻意的：改完 /etc/filesystems 之後，要 umount/mount
#    該掛載點才套用；掛載正在使用中時卸不掉就得重開機。**到底要不要停機取決於
#    現場狀況**，填任何一個現有詞彙都會誤導排維護窗口的人。
set_fix "grep -p nfs /etc/filesystems"         "1) 編輯 /etc/filesystems（先 cp -p 備份），該 nfs 段的 options 加上 nosuid 與 nodev  2) umount 再 mount 該掛載點"         "未確認" "步驟"
_std18="NFS 掛載選項，( 掛載帶 nosuid 與 nodev )"
if [ "$NFS_PROBE_OK" -eq 0 ]; then
    emit "FCB-AIX-0018" "Error" "$CAT_SVC" "NFS 掛載選項 nosuid 與 nodev" "$_std18" "無 lsfs 指令（非 AIX？）——未查"
elif [ -z "$NFS_MOUNTS" ]; then
    emit "FCB-AIX-0018" "Not-Applicable" "$CAT_SVC" "NFS 掛載選項 nosuid 與 nodev" "$_std18" "未掛載任何 NFS 檔案系統，不適用"
else
    # ⚠️ 2026-09-23 自我稽核 B1：原本只 grep -v 'nosuid'，**完全沒有查 nodev**——
    # 而條目標題寫的是 "enable both nosuid and nodev"。
    # 掛載帶了 nosuid、沒有 nodev 的機器會被判合規，報表上看起來查過了，實際只查一半。
    # 這是金融業，稽核問起來解釋不了就是缺失。
    _opts=`grep -p nfs /etc/filesystems 2>/dev/null | grep -i 'options'`
    _bad_su=`echo "$_opts" | grep -v '^$' | grep -v 'nosuid' | head -n 3`
    _bad_nd=`echo "$_opts" | grep -v '^$' | grep -v 'nodev' | head -n 3`
    if [ -z "$_opts" ]; then
        # 有 NFS 掛載（前面判過了）卻讀不到 options，那是沒查到不是沒問題
        emit "FCB-AIX-0018" "Error" "$CAT_SVC" "NFS 掛載選項 nosuid 與 nodev" "$_std18" "/etc/filesystems 讀不到 nfs 段的 options 行——未查"
    elif [ -n "$_bad_su" ] || [ -n "$_bad_nd" ]; then
        _miss=""
        [ -n "$_bad_su" ] && _miss="未帶 nosuid: $_bad_su"
        [ -n "$_bad_nd" ] && _miss="$_miss 未帶 nodev: $_bad_nd"
        emit "FCB-AIX-0018" "Non-Compliant" "$CAT_SVC" "NFS 掛載選項 nosuid 與 nodev" "$_std18" "$_miss"
    else
        emit "FCB-AIX-0018" "Compliant" "$CAT_SVC" "NFS 掛載選項 nosuid 與 nodev" "$_std18" "所有 NFS 掛載皆帶 nosuid 與 nodev"
    fi
fi

set_fix "grep -inE 'localhost|127\.0\.0\.1' /etc/exports"         "1) 編輯 /etc/exports（先 cp -p 備份）移除 localhost／127.0.0.1 的輸出條目  2) exportfs -u <該目錄> 再 exportfs -a 重新讀入"         "立即生效" "步驟"
_std19="NFS 輸出對象，( 無 localhost 條目 )"
if [ "$NFS_EXPORTS_N" -eq 0 ]; then
    emit "FCB-AIX-0019" "Not-Applicable" "$CAT_SVC" "NFS 輸出對象排除 localhost" "$_std19" "未開啟 NFS 輸出（/etc/exports 無有效條目），不適用"
else
    _l=`grep -iE 'localhost|127\.0\.0\.1' /etc/exports 2>/dev/null | grep -vE '^[ 	]*#' | head -n 2`
    if [ -n "$_l" ]; then
        emit "FCB-AIX-0019" "Non-Compliant" "$CAT_SVC" "NFS 輸出對象排除 localhost" "$_std19" "$_l"
    else
        emit "FCB-AIX-0019" "Compliant" "$CAT_SVC" "NFS 輸出對象排除 localhost" "$_std19" "/etc/exports 無 localhost 條目（有效條目 $NFS_EXPORTS_N 筆）"
    fi
fi

set_fix "showmount -e"         "1) 編輯 /etc/exports（先 cp -p 備份），輸出對象改成明確的主機或網段清單  2) exportfs -u <該目錄> 再 exportfs -a"         "立即生效" "步驟"
_std20='NFS 輸出範圍，( showmount -e 無 everyone )'
if [ "$NFS_EXPORTS_N" -eq 0 ]; then
    emit "FCB-AIX-0020" "Not-Applicable" "$CAT_SVC" "NFS 輸出範圍限定（不可 everyone）" "$_std20" "未開啟 NFS 輸出（無輸出目錄），不適用"
else
    # ⚠️ 2026-09-23 自我稽核 A1：這是全檔**唯一靠外部服務回應**的檢查。
    # 原本只擋「指令不存在」，擋不住「指令執行失敗」——showmount 在 nfsd 沒起來、
    # RPC 不通、防火牆擋 portmapper 時會吐空並回非 0，於是「問不到」被判成 Compliant。
    # 那跟解析端「跑到一半死掉顯示 100% 合規」是同一個形狀，而且更糟：
    # 它同時是整支腳本唯一可能卡死的地方。
    _l=""
    if have showmount; then :; else
        emit "FCB-AIX-0020" "Error" "$CAT_SVC" "NFS 輸出範圍限定（不可 everyone）" "$_std20" "無 showmount 指令——未查"
        _l="__SKIP__"
    fi
    if [ "$_l" != "__SKIP__" ]; then
        run_timeout 15 showmount -e
        if [ "$RT_RC" = "124" ]; then
            emit "FCB-AIX-0020" "Error" "$CAT_SVC" "NFS 輸出範圍限定（不可 everyone）" "$_std20" "showmount -e 15 秒無回應（RPC 不通或 nfsd 未啟動）——未查"
            _l="__SKIP__"
        elif [ "$RT_RC" != "0" ]; then
            emit "FCB-AIX-0020" "Error" "$CAT_SVC" "NFS 輸出範圍限定（不可 everyone）" "$_std20" "showmount -e 查詢失敗（離開碼 $RT_RC）——未查"
            _l="__SKIP__"
        else
            _l=`echo "$RT_OUT" | grep '(everyone)' | head -n 2`
        fi
    fi
    if [ "$_l" = "__SKIP__" ]; then
        :
    elif [ -n "$_l" ]; then
        emit "FCB-AIX-0020" "Non-Compliant" "$CAT_SVC" "NFS 輸出範圍限定（不可 everyone）" "$_std20" "$_l"
    else
        emit "FCB-AIX-0020" "Compliant" "$CAT_SVC" "NFS 輸出範圍限定（不可 everyone）" "$_std20" "showmount -e 無 (everyone)"
    fi
fi

set_fix "grep -nE 'no_root_squash|root=' /etc/exports"         "1) 編輯 /etc/exports（先 cp -p 備份）移除 no_root_squash 與 root= 授權  2) exportfs -u <該目錄> 再 exportfs -a"         "立即生效" "步驟"
_std21="NFS root 權限，( 無 no_root_squash 與 root= )"
if [ "$NFS_EXPORTS_N" -eq 0 ]; then
    emit "FCB-AIX-0021" "Not-Applicable" "$CAT_SVC" "NFS 停用 no_root_squash" "$_std21" "未開啟 NFS 輸出（/etc/exports 無有效條目），不適用"
else
    _l=`grep -E 'no_root_squash|root=' /etc/exports 2>/dev/null | grep -vE '^[ 	]*#' | head -n 2`
    if [ -n "$_l" ]; then
        emit "FCB-AIX-0021" "Non-Compliant" "$CAT_SVC" "NFS 停用 no_root_squash" "$_std21" "$_l"
    else
        emit "FCB-AIX-0021" "Compliant" "$CAT_SVC" "NFS 停用 no_root_squash" "$_std21" "無 no_root_squash / root= 授權"
    fi
fi

# 不指定 sec= 要用哪個值：那是本行政策決定的（sys／krb5／krb5i／krb5p 強度不同），
# 腳本推不出來，硬填一個等於替政策做決定。
set_fix "grep -n 'sec=' /etc/exports"         "1) 編輯 /etc/exports（先 cp -p 備份），每個輸出條目加上 sec=（值依本行政策，例如 sec=krb5）  2) exportfs -u <該目錄> 再 exportfs -a"         "立即生效" "步驟"
_std22="NFS 認證方式，( 每個輸出都有 sec= )"
if [ "$NFS_EXPORTS_N" -eq 0 ]; then
    emit "FCB-AIX-0022" "Not-Applicable" "$CAT_SVC" "NFS 輸出指定認證方式 sec=" "$_std22" "未開啟 NFS 輸出（/etc/exports 無有效條目），不適用"
else
    _l=`grep -vE '^[ 	]*(#|$)' /etc/exports 2>/dev/null | grep -v 'sec=' | head -n 2`
    if [ -n "$_l" ]; then
        emit "FCB-AIX-0022" "Non-Compliant" "$CAT_SVC" "NFS 輸出指定認證方式 sec=" "$_std22" "未指定 sec=: $_l"
    else
        emit "FCB-AIX-0022" "Compliant" "$CAT_SVC" "NFS 輸出指定認證方式 sec=" "$_std22" "所有輸出皆指定 sec="
    fi
fi

#=============================================================================
# 23-43　inetd 服務
#=============================================================================
ck_inetd "FCB-AIX-0023" "關閉 inetd 服務：chargen" "chargen"
ck_inetd "FCB-AIX-0024" "關閉 inetd 服務：comsat" "comsat"
ck_inetd "FCB-AIX-0025" "關閉 inetd 服務：discard" "discard"
ck_inetd "FCB-AIX-0026" "關閉 inetd 服務：echo" "echo"
ck_inetd "FCB-AIX-0027" "關閉 inetd 服務：exec" "exec"
ck_inetd "FCB-AIX-0028" "關閉 inetd 服務：finger" "finger"
ck_inetd "FCB-AIX-0029" "關閉 inetd 服務：imap2" "imap2"
ck_inetd "FCB-AIX-0030" "關閉 inetd 服務：instsrv" "instsrv"
ck_inetd "FCB-AIX-0031" "關閉 inetd 服務：klogin" "klogin"
ck_inetd "FCB-AIX-0032" "關閉 inetd 服務：kshell" "kshell"
ck_inetd "FCB-AIX-0033" "關閉 inetd 服務：login" "login"
ck_inetd "FCB-AIX-0034" "關閉 inetd 服務：netstat" "netstat"
ck_inetd "FCB-AIX-0035" "關閉 inetd 服務：pcnfsd" "pcnfsd"
ck_inetd "FCB-AIX-0036" "關閉 inetd 服務：pop3" "pop3"
ck_inetd "FCB-AIX-0037" "關閉 inetd 服務：rexd" "rexd"
ck_inetd "FCB-AIX-0038" "關閉 inetd 服務：rquotad" "rquotad"
ck_inetd "FCB-AIX-0039" "關閉 inetd 服務：rstatd" "rstatd"
ck_inetd "FCB-AIX-0040" "關閉 inetd 服務：rusersd" "rusersd"
ck_inetd "FCB-AIX-0041" "關閉 inetd 服務：rwalld" "rwalld"
ck_inetd "FCB-AIX-0042" "關閉 inetd 服務：sprayd" "sprayd"
ck_inetd "FCB-AIX-0043" "關閉 inetd 服務：uucp" "uucp"

#=============================================================================
# 44-50　網路參數（no）
#=============================================================================
ck_no "FCB-AIX-0044" "網路核心參數：bcastping"          "bcastping"          "0"   # 回應廣播 ping，可被拿去放大流量攻擊
ck_no "FCB-AIX-0045" "網路核心參數：directed_broadcast" "directed_broadcast" "0"   # Smurf 攻擊跳板
ck_no "FCB-AIX-0046" "網路核心參數：icmpaddressmask"    "icmpaddressmask"    "0"   # 洩漏內網網段切法
ck_no "FCB-AIX-0047" "網路核心參數：ipforwarding"       "ipforwarding"       "0"   # 這台會變路由器
ck_no "FCB-AIX-0048" "網路核心參數：ipsrcrouterecv"     "ipsrcrouterecv"     "0"   # 來源路由可繞過防火牆
ck_no "FCB-AIX-0049" "網路核心參數：nonlocsrcroute"     "nonlocsrcroute"     "0"   # 同上
ck_no "FCB-AIX-0050" "網路核心參數：ip6forwarding"      "ip6forwarding"      "0"   # IPv6 上會變路由器

#=============================================================================
# 51-53　NIS / hosts.equiv / CDE
#=============================================================================
# 改 /etc/passwd、/etc/group 之後用 AIX 自己的 usrck／grpck 複核，比肉眼看可靠。
set_fix "grep -n '^+' /etc/passwd /etc/group"         "1) 編輯 /etc/passwd 與 /etc/group（先 cp -p 備份）移除開頭是 + 的行  2) usrck -n ALL 與 grpck -n ALL 複核"         "立即生效" "步驟"
_std51='/etc/passwd 與 /etc/group 的 NIS 標記，( 皆無 + 開頭項目 )'
_l=`grep -l '^+' /etc/passwd /etc/group 2>/dev/null | tr '\n' ' '`
if [ -n "$_l" ]; then
    emit "FCB-AIX-0051" "Non-Compliant" "$CAT_SYS" "移除 passwd／group 的 NIS 標記" "$_std51" "有 NIS 標記: $_l"
else
    emit "FCB-AIX-0051" "Compliant" "$CAT_SYS" "移除 passwd／group 的 NIS 標記" "$_std51" 'passwd 與 group 皆無 "+" 標記'
fi

set_fix "grep -vE '^[ 	]*(#|$)' /etc/hosts.equiv"         "grep -qvE '^[[:space:]]*(#|\$)' /etc/hosts.equiv && cp -p /etc/hosts.equiv /etc/hosts.equiv.bak.\$(date +%Y%m%d%H%M) && cat /dev/null > /etc/hosts.equiv"         "立即生效" "可執行"
_std52="/etc/hosts.equiv 的信任主機，( 不存在或無有效條目 )"
if [ ! -f /etc/hosts.equiv ]; then
    emit "FCB-AIX-0052" "Compliant" "$CAT_SYS" "清除 /etc/hosts.equiv 的信任主機" "$_std52" "/etc/hosts.equiv 不存在"
else
    _l=`grep -vE '^[ 	]*(#|$)' /etc/hosts.equiv 2>/dev/null | head -n 3`
    if [ -n "$_l" ]; then
        emit "FCB-AIX-0052" "Non-Compliant" "$CAT_SYS" "清除 /etc/hosts.equiv 的信任主機" "$_std52" "$_l"
    else
        emit "FCB-AIX-0052" "Compliant" "$CAT_SYS" "清除 /etc/hosts.equiv 的信任主機" "$_std52" "檔案存在但無有效條目"
    fi
fi

# ⚠️ 項次 2 與項次 53 查的是**同一個 inittab ident（dt）**，結果必然一致。
# 已對照 DATA/AIX檢核表.XLS「AIX組態調整項目」：客戶的正式檢核表本來就列兩條
# （項次 2「dt」、項次 53「CDE - disabling dtlogin」），所以照列，不自行合併。
# 但要在報表上講明——否則合規率的分母裡有一條在重複計算同一個事實，
# 而看報表的人會以為那是兩個獨立的控制項。
# **分母要不要調整是客戶的決定，我們的責任是讓他看得出來。**
# 2026-10-01：原本第 4 個參數塞的是「註：與項次 2（dt）查同一個 inittab ident，
# 結果必然一致；本表依客戶檢核表列為兩項」。那段話有三個問題：
#   1) 它講的是我們怎麼實作、怎麼排表，不是「這條規範要求什麼」——
#      那屬於程式碼註解，不該出現在給老闆與稽核看的欄位
#   2) 出現「客戶」「本表」「項次」這種開發者視角的用詞，而讀者就是那個「客戶」
#   3) 開頭的表意空格 U+3000 在真機上被打壞成菱形亂碼
#
# 事實本身有價值，所以留在這裡（不進報告）：
#   FCB-AIX-0053 與 FCB-AIX-0002 查的是同一個 inittab ident（dt），
#   所以兩條的判定結果必然一致。檢核表列成兩項，我們照列以維持 1:1 對應。
#   兩條同時不合規時，動一次 dt 就會一起變成合規，不用做兩次。
ck_inittab "FCB-AIX-0053" "關閉 CDE 圖形登入（dtlogin，inittab 的 dt）" "dt" \
    ""

#=============================================================================
# 54-63　OpenSSH
#=============================================================================
# ⚠️ 修正這條是「升級軟體」，不是改一個設定值：升級管道與版本來源依本行軟體政策，
#    腳本推不出來，所以給的是步驟說明不是一行指令。**沒有憑空生一個 installp 指令。**
set_fix "ssh -V"         "升級 OpenSSH 到 8.1 以上（來源與版本依本行軟體政策）再重啟 sshd"         "需重啟服務：sshd" "步驟"
_std54="OpenSSH 版本，( 8.1 以上 )"
_sv=`ssh -V 2>&1 | head -n 1`
if [ -z "$_sv" ]; then
    emit "FCB-AIX-0054" "Error" "$CAT_SSH" "OpenSSH 最低版本 8.1" "$_std54" "ssh -V 無輸出"
else
    _maj=`echo "$_sv" | sed 's/^OpenSSH_//' | cut -f1 -d'.'`
    _min=`echo "$_sv" | sed 's/^OpenSSH_//' | cut -f2 -d'.' | sed 's/[^0-9].*//'`
    echo "$_maj" | grep -Eq '^[0-9]+$' || _maj=0
    echo "$_min" | grep -Eq '^[0-9]+$' || _min=0
    if [ "$_maj" -gt 8 ] || { [ "$_maj" -eq 8 ] && [ "$_min" -ge 1 ]; }; then
        emit "FCB-AIX-0054" "Compliant" "$CAT_SSH" "OpenSSH 最低版本 8.1" "$_std54" "$_sv"
    else
        emit "FCB-AIX-0054" "Non-Compliant" "$CAT_SSH" "OpenSSH 最低版本 8.1" "$_std54" "$_sv"
    fi
fi

set_fix "ls -l /etc/shosts.equiv /etc/rhosts.equiv"         "先 cp -p 備份，再移除存在的那個（或那兩個）檔"         "立即生效" "步驟"
_std55="/etc/shosts.equiv 與 /etc/rhosts.equiv，( 兩個檔案皆不存在 )"
_l=""
[ -e /etc/shosts.equiv ] && _l="$_l /etc/shosts.equiv"
[ -e /etc/rhosts.equiv ] && _l="$_l /etc/rhosts.equiv"
if [ -n "$_l" ]; then
    emit "FCB-AIX-0055" "Non-Compliant" "$CAT_SSH" "移除主機信任檔 shosts.equiv／rhosts.equiv" "$_std55" "仍存在:$_l"
else
    emit "FCB-AIX-0055" "Compliant" "$CAT_SSH" "移除主機信任檔 shosts.equiv／rhosts.equiv" "$_std55" "兩個檔案皆不存在"
fi

# 查核指令跟檢查一樣是「從 /etc/passwd 取家目錄再看有沒有 .shosts」，
# 只是改成一行可貼的形式；ls 的錯誤訊息丟掉，所以有輸出就代表真的有檔。
# 不用 find -maxdepth：AIX 的 find 沒有這個選項（踩過的話會整行失敗）。
set_fix "awk -F: '\$6 ~ /^\// {print \$6\"/.shosts\"}' /etc/passwd | sort -u | xargs ls -ld 2>/dev/null"         "先 cp -p 備份，再把列出來的每個 .shosts 移除"         "立即生效" "步驟"
_std56="各使用者家目錄的 .shosts，( 所有家目錄皆無 .shosts )"
if [ "$AM_ROOT" -eq 1 ]; then
    _homes=`awk -F: '$6 ~ /^\// {print $6}' /etc/passwd 2>/dev/null | sort -u`
    _hit=""
    for _h in $_homes; do
        [ -e "$_h/.shosts" ] && _hit="$_hit $_h/.shosts"
    done
    if [ -n "$_hit" ]; then
        emit "FCB-AIX-0056" "Non-Compliant" "$CAT_SSH" "移除家目錄的 .shosts" "$_std56" "仍存在:$_hit"
    else
        emit "FCB-AIX-0056" "Compliant" "$CAT_SSH" "移除家目錄的 .shosts" "$_std56" "已檢查 /etc/passwd 所列家目錄，皆無 .shosts"
    fi
else
    emit "FCB-AIX-0056" "Error" "$CAT_SSH" "移除家目錄的 .shosts" "$_std56" "非 root，無法讀取其他使用者家目錄"
fi

ck_sshd "FCB-AIX-0057" "SSH 禁止空密碼登入（PermitEmptyPasswords）" "PermitEmptyPasswords" "^no$"            "SSH PermitEmptyPasswords 參數，( no )"
ck_sshd "FCB-AIX-0058" "SSH 記錄層級（LogLevel）"                   "LogLevel"             "^(INFO|VERBOSE)$" "SSH LogLevel 參數，( INFO 或 VERBOSE )"
ck_sshd "FCB-AIX-0059" "SSH 認證嘗試次數上限（MaxAuthTries）"       "MaxAuthTries"         "^[1-4]$"          "SSH MaxAuthTries 參數，( 4 以下 )"

# ⚠️ 這條的判定只會是 Compliant 或 Error——腳本只記 Match 區塊數，不判不合規。
#    所以「修正」實際上只對 Error 那條路有意義：讀不到 sshd_config。
set_fix "grep -nE '^[ 	]*Match ' /etc/ssh/sshd_config"         "僅 Error 時需處理：確認 /etc/ssh/sshd_config 存在且讀得到"         "立即生效" "步驟"
_std60='SSH 條件式例外，( 例外寫在 Match 區塊 )'
if [ -f /etc/ssh/sshd_config ]; then
    _n=`grep -cE '^[ 	]*Match ' /etc/ssh/sshd_config 2>/dev/null | tr -d ' '`
    emit "FCB-AIX-0060" "Compliant" "$CAT_SSH" "SSH 條件式例外（Match 區塊）" "$_std60（人工判讀：例外是否經核准）" "Match 區塊數 = $_n"
else
    emit "FCB-AIX-0060" "Error" "$CAT_SSH" "SSH 條件式例外（Match 區塊）" "$_std60" "讀不到 /etc/ssh/sshd_config"
fi

# 61-63：弱演算法一律判不合規；沒設定就是吃編譯預設，同樣標不合規並附實際值
ck_alg() {      # $1=id $2=名稱 $3=關鍵字 $4=弱演算法 egrep pattern
    _std="SSH $3 演算法，( 不含 $4 )"
    # 指令用的是**這個檢查自己在讀的兩個檔與同一個關鍵字**，所以不會跟判定標準走岔。
    # ssh_config 是用戶端設定、下次連線就生效；要重啟的是 sshd 那一側，
    # 生效方式取比較嚴的那個（需重啟服務）當代表值。
    set_fix "grep -Ei '^[ 	]*$3[ 	]' /etc/ssh/sshd_config /etc/ssh/ssh_config"             "1) 編輯 /etc/ssh/sshd_config 與 /etc/ssh/ssh_config（先 cp -p 備份），把 $3 明示成只保留強演算法（移除符合 $4 的那些）  2) stopsrc -s sshd && startsrc -s sshd"             "需重啟服務：sshd" "步驟"
    _out=""
    for _f in /etc/ssh/sshd_config /etc/ssh/ssh_config; do
        [ -f "$_f" ] || continue
        _v=`grep -Ei "^[ 	]*$3[ 	]" "$_f" 2>/dev/null | tail -n 1`
        [ -n "$_v" ] && _out="$_out [$_f] $_v"
    done
    if [ -z "$_out" ]; then
        emit "$1" "Non-Compliant" "$CAT_SSH" "$2" "$_std" "兩個設定檔皆未明示 $3（吃預設值）"
    elif echo "$_out" | grep -Eiq "$4"; then
        emit "$1" "Non-Compliant" "$CAT_SSH" "$2" "$_std" "含弱演算法:$_out"
    else
        emit "$1" "Compliant" "$CAT_SSH" "$2" "$_std" "$_out"
    fi
}
ck_alg "FCB-AIX-0061" "SSH 金鑰交換演算法（KexAlgorithms）"   "KexAlgorithms" "group1-sha1|group-exchange-sha1|gss-"
ck_alg "FCB-AIX-0062" "SSH 加密演算法（Ciphers）"               "Ciphers"       "arcfour|-cbc|3des|blowfish|cast128"
ck_alg "FCB-AIX-0063" "SSH 訊息驗證碼演算法（MACs）"            "MACs"          "hmac-md5|hmac-sha1[^-]|hmac-sha1$|96|umac-64[^-]"

#=============================================================================
# 64-67　SNMP / sendmail
#=============================================================================
ck_snmp() {     # $1=id $2=community 名稱
    _f=/etc/snmpdv3.conf
    [ -f "$_f" ] || _f=/etc/snmpd.conf
    _nm="停用 SNMP 預設社群字串（$2）"
    _std="SNMP 預設社群字串 $2，( $_f 無啟用中的 $2 社群字串 )"
    # $_f 就是這個檢查實際在讀的那個檔（snmpdv3.conf 優先、退回 snmpd.conf），
    # 指令直接沿用，不另外寫死路徑。
    # ⚠️ 給步驟不給一行指令：這是系統設定檔，盲改不留備份在金融環境不可接受
    #    （同 rc.tcpip／inetd.conf 的處理原則）。
    set_fix "grep -inE '^[[:space:]]*COMMUNITY[[:space:]]+$2([[:space:]]|\$)' $_f"             "grep -inE '^[[:space:]]*COMMUNITY[[:space:]]+$2([[:space:]]|\$)' $_f && cp -p $_f $_f.bak.\$(date +%Y%m%d%H%M) && sed 's|^\([[:space:]]*[Cc][Oo][Mm][Mm][Uu][Nn][Ii][Tt][Yy][[:space:]][[:space:]]*$2\)|#|' $_f > $_f.new.\$\$ && cat $_f.new.\$\$ > $_f && rm -f $_f.new.\$\$ && stopsrc -s snmpd && startsrc -s snmpd"             "需重啟服務：snmpd" "可執行"
    if [ ! -f "$_f" ]; then
        # 同上：規範要求「預設社群字串不要啟用」，連 SNMP 設定檔都沒有，
        # 就代表沒有任何社群字串被啟用 = 要求做到了 = Compliant，不是不適用。
        emit "$1" "Compliant" "$CAT_SVC" "$_nm" "$_std" "無 SNMP 設定檔，未啟用任何社群字串"
        return
    fi
    if [ "$AM_ROOT" -ne 1 ] && [ ! -r "$_f" ]; then
        emit "$1" "Error" "$CAT_SVC" "$_nm" "$_std" "非 root，讀不到 $_f"
        return
    fi
    _l=`grep -iE "^[ 	]*(COMMUNITY|community)[ 	]+$2([ 	]|$)" "$_f" 2>/dev/null | head -n 1`
    if [ -n "$_l" ]; then
        emit "$1" "Non-Compliant" "$CAT_SVC" "$_nm" "$_std" "$_f: $_l"
    else
        emit "$1" "Compliant" "$CAT_SVC" "$_nm" "$_std" "$_f 無啟用中的 $2 community"
    fi
}
ck_snmp "FCB-AIX-0064" "private"
ck_snmp "FCB-AIX-0065" "system"
ck_snmp "FCB-AIX-0066" "public"

# lssrc 讀、stopsrc 停，是同一組 SRC 指令；開機自啟那一半跟 FCB-AIX-0013 是同一件事。
set_fix "lssrc -s sendmail"         "stopsrc -s sendmail && grep -nE '^[[:space:]]*start[[:space:]].*/sendmail' /etc/rc.tcpip && cp -p /etc/rc.tcpip /etc/rc.tcpip.bak.\$(date +%Y%m%d%H%M) && sed 's|^\([[:space:]]*start[[:space:]].*/sendmail\)|#|' /etc/rc.tcpip > /etc/rc.tcpip.new.\$\$ && cat /etc/rc.tcpip.new.\$\$ > /etc/rc.tcpip && rm -f /etc/rc.tcpip.new.\$\$"         "立即生效" "可執行"
_std67="sendmail 服務狀態，( 已停止且不自動啟動 )"
if have lssrc; then _ls=`lssrc -s sendmail 2>/dev/null | grep -v Subsystem | head -n 1`; else _ls="__NOCMD__"; fi
if [ "$_ls" = "__NOCMD__" ]; then
    emit "FCB-AIX-0067" "Error" "$CAT_SVC" "停用 sendmail 服務" "$_std67" "無 lssrc 指令（非 AIX？）——未查"
elif [ -z "$_ls" ]; then
    emit "FCB-AIX-0067" "Compliant" "$CAT_SVC" "停用 sendmail 服務" "$_std67" "SRC 無 sendmail 子系統"
else
    _st=`echo "$_ls" | awk '{print $NF}'`
    if [ "$_st" = "active" ]; then
        emit "FCB-AIX-0067" "Non-Compliant" "$CAT_SVC" "停用 sendmail 服務" "$_std67" "$_ls"
    else
        emit "FCB-AIX-0067" "Compliant" "$CAT_SVC" "停用 sendmail 服務" "$_std67" "$_ls"
    fi
fi

#=============================================================================
# 68-69　登入警語 / 登入重試
#=============================================================================
# lssec 讀、chsec 寫，同一組指令的兩面，參數一模一樣。
# ⚠️ herald 的內容是法遵決定的警語文字，腳本不該替它決定，所以給的是指令形狀＋佔位。
#    值裡若要換行用 \n，整串要用單引號包住，不然 shell 會先吃掉特殊字元。
set_fix "lssec -f /etc/security/login.cfg -s default -a herald"         "chsec -f /etc/security/login.cfg -s default -a herald='<本行核可的登入警語>'"         "下次登入生效" "步驟"
_std68="登入警語 herald，( login.cfg default 節有設 herald )"
if have lssec; then _o=`lssec -f /etc/security/login.cfg -s default -a herald 2>/dev/null | tail -n 1`; else _o=""; fi
_v=`echo "$_o" | sed 's/.*herald=//' | tr -d '"'`
if [ -z "$_o" ]; then
    emit "FCB-AIX-0068" "Error" "$CAT_ACC" "登入前警語（herald）" "$_std68" "lssec 無輸出或無此指令（需 root／非 AIX）"
elif [ -z "$_v" ]; then
    emit "FCB-AIX-0068" "Non-Compliant" "$CAT_ACC" "登入前警語（herald）" "$_std68" "herald 未設定: $_o"
else
    emit "FCB-AIX-0068" "Compliant" "$CAT_ACC" "登入前警語（herald）" "$_std68" "$_o"
fi

ck_sec "FCB-AIX-0069" "登入失敗鎖定次數（loginretries）" "/etc/security/user default loginretries" "5" "le" \
       "連續登入失敗幾次就鎖定帳號" "次"

#=============================================================================
# 70　稽核子系統目錄所有權
#=============================================================================
# ls -ld 讀、chown 寫，跟 ck_perm 那 12 條是同一個套路。
set_fix "ls -ld /audit /etc/security/audit"         "chown root:audit /audit /etc/security/audit"         "立即生效" "可執行"
_std70="/audit 與 /etc/security/audit 所有權，( root:audit )"
_r70=""
_bad70=0
for _d in /audit /etc/security/audit; do
    if [ ! -e "$_d" ]; then
        _r70="$_r70 [$_d 不存在]"
        continue
    fi
    _l=`ls -ld "$_d" 2>/dev/null`
    _o=`echo "$_l" | awk '{print $3}'`; _g=`echo "$_l" | awk '{print $4}'`
    _r70="$_r70 $_l"
    { [ "$_o" = "root" ] && [ "$_g" = "audit" ]; } || _bad70=1
done
if [ -z "$_r70" ]; then
    emit "FCB-AIX-0070" "Error" "$CAT_LOG" "稽核目錄所有權（/audit、/etc/security/audit）" "$_std70" "兩個路徑都讀不到"
elif [ "$_bad70" -eq 1 ]; then
    emit "FCB-AIX-0070" "Non-Compliant" "$CAT_LOG" "稽核目錄所有權（/audit、/etc/security/audit）" "$_std70" "$_r70"
else
    emit "FCB-AIX-0070" "Compliant" "$CAT_LOG" "稽核目錄所有權（/audit、/etc/security/audit）" "$_std70" "$_r70"
fi

#=============================================================================
# 71-82　檔案與目錄權限
#=============================================================================
ck_perm "FCB-AIX-0071" "$CAT_SYS" "檔案權限與擁有者：/var/spool/cron/crontabs" "/var/spool/cron/crontabs" "drwxrwx---" "bin"  "cron"     "error"      # AIX 的 cron 標準目錄，一定存在；可寫就能以別人身分排程執行指令
ck_perm "FCB-AIX-0072" "$CAT_LOG" "檔案權限與擁有者：/etc/security/audit"      "/etc/security/audit"      "drwxr-x---" "root" "audit"    "error"      # AIX 稽核子系統標準目錄；稽核設定被改就查不出誰動過什麼
ck_perm "FCB-AIX-0073" "$CAT_SYS" "檔案權限與擁有者：/etc/group"               "/etc/group"               "-rw-r--r--" "root" "security" "error"      # 系統核心檔，不可能不存在；可寫就能把自己加進特權群組
ck_perm "FCB-AIX-0074" "$CAT_SYS" "檔案權限與擁有者：/etc/inetd.conf"          "/etc/inetd.conf"          "-rw-r--r--" "root" "system"   "compliant"  # inetd.conf 可能被移除（等同沒有設定任何 inetd 服務），與 ck_inetd 的判法一致
ck_perm "FCB-AIX-0075" "$CAT_SYS" "檔案權限與擁有者：/etc/motd"                "/etc/motd"                "-rw-r--r--" "bin"  "bin"      "compliant"  # motd 可以不存在；沒有檔案就沒有權限風險（內容要求由 FCB-AIX-0085 判）
ck_perm "FCB-AIX-0076" "$CAT_SYS" "檔案權限與擁有者：/etc/passwd"              "/etc/passwd"              "-rw-r--r--" "root" "security" "error"      # 系統核心檔，不可能不存在；可寫就能改 UID 冒充他人
ck_perm "FCB-AIX-0077" "$CAT_SYS" "檔案權限與擁有者：/etc/ssh/ssh_config"      "/etc/ssh/ssh_config"      "-rw-r--r--" "root" "system"   "compliant"  # 沒裝 OpenSSH 就不會有這個檔，沒有檔案就沒有權限風險
ck_perm "FCB-AIX-0078" "$CAT_SYS" "檔案權限與擁有者：/etc/ssh/sshd_config"     "/etc/ssh/sshd_config"     "-rw-r--r--" "root" "system"   "compliant"  # 同上
ck_perm "FCB-AIX-0079" "$CAT_LOG" "檔案權限與擁有者：/var/adm/cron/log"        "/var/adm/cron/log"        "-rw-rw----" "bin"  "cron"     "na"         # cron 的日誌，cron 沒跑過就不會產生
ck_perm "FCB-AIX-0080" "$CAT_LOG" "檔案權限與擁有者：/var/tmp/dpid2.log"       "/var/tmp/dpid2.log"       "-rw-r-----" "root" "system"   "na"         # dpid2 的日誌，該服務沒啟用就不會產生
ck_perm "FCB-AIX-0081" "$CAT_LOG" "檔案權限與擁有者：/var/tmp/hostmibd.log"    "/var/tmp/hostmibd.log"    "-rw-r-----" "root" "system"   "na"         # hostmibd 的日誌，該服務沒啟用就不會產生
ck_perm "FCB-AIX-0082" "$CAT_LOG" "檔案權限與擁有者：/var/tmp/snmpd.log"       "/var/tmp/snmpd.log"       "-rw-r-----" "root" "system"   "na"         # snmpd 的日誌，該服務沒啟用就不會產生

#=============================================================================
# 83-85　PATH 與登入警示語
#=============================================================================
#-----------------------------------------------------------------------------
# 「檔案不存在」要判什麼？——判準寫在這裡，不要藏在某個人的記憶裡
#
# 2026-10-01 指揮官裁示。**不可以因為「都是檔案不存在」就一視同仁**，
# 判準是「這個檔不在，在這個作業系統上正不正常」：
#
#   檔案缺席是**正常**狀態  -> Compliant
#       風險確定不存在＝要求達成。例如 /.profile 本來就常常沒有，
#       它不存在就代表 root 的 PATH 不可能從那裡被塞進 "."。
#       （跟 ck_inetd 對 /etc/inetd.conf 的推理是同一條規則）
#
#   檔案缺席是**異常**狀態  -> Error
#       那是「我們沒讀到」或「這台有異狀」，屬於**不知道**，不是沒問題。
#       例如 /etc/environment 在 AIX 上本來就該存在，不見了就是查不出結果。
#
#   檔案只有在**該功能啟用時才會產生** -> Not-Applicable
#       例如 snmpd 的日誌檔。沒啟用那個服務就不會有那個檔，
#       那是前提不成立，不是我們做到了什麼，也不是沒查到。
#
# ⚠️ 三者絕對不可以混用。把「沒查到」寫成「沒問題」是最糟的一種說謊。
#-----------------------------------------------------------------------------
ck_path() {     # $1=id $2=名稱 $3=檔案 $4=檔案不存在時判什麼（compliant/error）
    _std="$3 的 PATH 設定，( 不含 . 與 :: )"
    # $3 就是這個檢查在讀的檔（/etc/environment 或 /.profile），指令沿用同一個變數。
    set_fix "grep -E '^[ 	]*(export[ 	]+)?PATH=' $3"             "編輯 $3（先 cp -p 備份），移除 PATH 裡的 . 、頭尾多餘的 : 、連續的 ::"             "下次登入生效" "步驟"
    if [ ! -f "$3" ]; then
        # 兩個呼叫端的檔性質不同，所以這裡**不共用同一個判定**（見上面的判準）。
        if [ "$4" = "error" ]; then
            emit "$1" "Error" "$CAT_SYS" "$2" "$_std" "$3 不存在（AIX 標準組態檔，應該存在）——未查到 PATH 設定"
        else
            emit "$1" "Compliant" "$CAT_SYS" "$2" "$_std" "$3 不存在（此檔非必要），PATH 不會從這裡被加進目前目錄"
        fi
        return
    fi
    if [ ! -r "$3" ]; then
        emit "$1" "Error" "$CAT_SYS" "$2" "$_std" "讀不到 $3（權限不足）"
        return
    fi
    _l=`grep -E '^[ 	]*(export[ 	]+)?PATH=' "$3" 2>/dev/null | grep -E '(^|=|:)\.(:|$)|::' | head -n 2`
    if [ -n "$_l" ]; then
        emit "$1" "Non-Compliant" "$CAT_SYS" "$2" "$_std" "$_l"
    else
        _cur=`grep -E '^[ 	]*(export[ 	]+)?PATH=' "$3" 2>/dev/null | head -n 1`
        [ -z "$_cur" ] && _cur="$3 未設定 PATH"
        emit "$1" "Compliant" "$CAT_SYS" "$2" "$_std" "$_cur"
    fi
}
ck_path "FCB-AIX-0083" "系統 PATH 不含目前目錄（/etc/environment）" "/etc/environment" "error"
ck_path "FCB-AIX-0084" "root 的 PATH 不含目前目錄（/.profile）"                   "/.profile" "compliant"

set_fix "cat -v /etc/motd"         "編輯 /etc/motd（先 cp -p 備份）放入本行核可的登入警語（內容依法遵規定）"         "立即生效" "步驟"
_std85="/etc/motd 登入警示語，( 檔案有內容 )"
if [ ! -f /etc/motd ]; then
    emit "FCB-AIX-0085" "Non-Compliant" "$CAT_SYS" "登入警示語內容（/etc/motd）" "$_std85" "/etc/motd 不存在"
else
    _n=`grep -vE '^[ 	]*$' /etc/motd 2>/dev/null | wc -l | tr -d ' '`
    if [ "$_n" -gt 0 ]; then
        emit "FCB-AIX-0085" "Compliant" "$CAT_SYS" "登入警示語內容（/etc/motd）" "$_std85" "已設定，共 $_n 行；首行: `head -n 1 /etc/motd`"
    else
        emit "FCB-AIX-0085" "Non-Compliant" "$CAT_SYS" "登入警示語內容（/etc/motd）" "$_std85" "/etc/motd 內容為空"
    fi
fi

#=============================================================================
# 86-96、98　帳號與密碼原則
#   minalpha / minother：CIS 建議 3，本行依管理辦法保留 1——
#   這是**經核准的例外**，所以標準值欄位要把「建議值」跟「本行值」都寫出來，
#   不可以只寫一個數字讓人以為系統不合規
#=============================================================================
ck_sec "FCB-AIX-0086" "密碼最短使用期限（minage）"      "/etc/security/user default minage"      "1" "ge" \
       "密碼改過後至少要隔多久才能再改" "週"

# ⚠️⚠️ 這條的修正**最危險**，指令要帶警告一起給。
#   這裡抓到的多半是 AIX 內建系統帳號（daemon bin sys adm uucp guest nobody lpd…），
#   它們本來就沒有密碼雜湊——那是設計如此，不是被人弄壞的。
#   **直接 chuser account_locked=true 鎖掉可能讓服務或排程失敗**，那是把一個
#   稽核項目換成一場故障。正確做法是確認它們「不能登入」而不是「鎖起來」。
#
# 查核指令不用檢查裡那段 awk：那段含分號，而分號是報告的欄位分隔字元
# （emit 會換成逗號，指令就壞了）。改成等價的 grep，一行可貼。
set_fix "grep -n -E '^[ 	]*password *= *\**[ 	]*$' /etc/security/passwd"         "注意：直接鎖系統內建帳號（daemon bin sys adm uucp guest nobody lpd 等）可能讓服務或排程失敗。內建帳號改用 chuser login=false rlogin=false <帳號>；非系統帳號才用 chuser account_locked=true <帳號>"         "下次登入生效" "步驟"
_std87="帳號密碼雜湊，( 每個未鎖定帳號都有雜湊 )"
if [ "$AM_ROOT" -eq 1 ]; then
    # ⚠️ 2026-09-23 自我稽核 A3：這裡原本還有一行 _nopw=...>/dev/null; echo ""，
    # 輸出被丟掉、變數永遠是空字串、而且沒有任何地方用到它——
    # 它看起來像第二重檢查，其實什麼都沒做，只會誤導讀程式的人。已刪。
    #
    # 另外加一道前置：先確認**檔案真的讀到了**。
    # 沒有這道的話，檔案不存在或 awk 失敗都會讓 _bad 是空的 -> 判成 Compliant，
    # 也就是「沒查到」冒充「沒問題」。
    _pwn=`grep -c ':' /etc/security/passwd 2>/dev/null | tr -d ' '`
    [ -z "$_pwn" ] && _pwn=0
    _bad=`awk -F: '/^[a-zA-Z0-9_]+:$/{u=$1} /password *=/{gsub(/ /,"");split($0,a,"=");if(a[2]==""||a[2]=="*")print u}' /etc/security/passwd 2>/dev/null | tr '\n' ' '`
    if [ "$_pwn" -eq 0 ]; then
        emit "FCB-AIX-0087" "Error" "$CAT_ACC" "所有帳號都要有密碼雜湊" "$_std87" "/etc/security/passwd 讀不到內容——未查"
    elif [ -n "$_bad" ]; then
        emit "FCB-AIX-0087" "Non-Compliant" "$CAT_ACC" "所有帳號都要有密碼雜湊" "$_std87" "無密碼帳號: $_bad"
    else
        emit "FCB-AIX-0087" "Compliant" "$CAT_ACC" "所有帳號都要有密碼雜湊" "$_std87" "所有帳號皆有密碼雜湊"
    fi
else
    emit "FCB-AIX-0087" "Error" "$CAT_ACC" "所有帳號都要有密碼雜湊" "$_std87" "非 root，讀不到 /etc/security/passwd"
fi

# 查核就是檢查裡那三段管線，原樣搬過來（不含分號，可以直接貼）。
# ⚠️ 改 GID 不是改一個數字就好：原本屬於舊 GID 的檔案會變成「沒有對應群組」，
#    所以修正一定要連檔案擁有權一起處理，這句不可以省。
set_fix "awk -F: '{print \$3}' /etc/group | sort | uniq -d"         "1) 確認哪一個群組該換號  2) chgroup id=<新GID> <群組名>  3) 注意：一定要把原屬舊 GID 的檔案改成新 GID，否則那些檔會變成無對應群組"         "立即生效" "步驟"
_std88="群組 GID 唯一性，( GID 無重複 )"
# ⚠️ 2026-09-23 自我稽核 A2：原本沒有任何「檔案讀到了沒」的判斷。
# /etc/group 讀不到 -> _dup 空 -> 判 Compliant，證據字串還會印成
# 「/etc/group 共 　個群組」（數字是空的）。實務風險低（644），但形狀是錯的：
# 「沒有重複」的依據是一個空結果，而沒有任何證據顯示檔案被讀過。
_n=`wc -l < /etc/group 2>/dev/null | tr -d ' '`
[ -z "$_n" ] && _n=0
_dup=`awk -F: '{print $3}' /etc/group 2>/dev/null | sort | uniq -d | tr '\n' ' '`
if [ "$_n" -eq 0 ]; then
    emit "FCB-AIX-0088" "Error" "$CAT_ACC" "群組名稱與 GID 不可重複" "$_std88" "/etc/group 讀不到內容——未查"
elif [ -n "$_dup" ]; then
    emit "FCB-AIX-0088" "Non-Compliant" "$CAT_ACC" "群組名稱與 GID 不可重複" "$_std88" "重複 GID: $_dup"
else
    emit "FCB-AIX-0088" "Compliant" "$CAT_ACC" "群組名稱與 GID 不可重複" "$_std88" "/etc/group 共 $_n 個群組，GID 無重複"
fi

# 0089 與 0090 是同一個檢查（都看 NOCHECK），所以修正也是同一組。
# 定義一次、兩個 emit 前各呼叫一次——emit 用完會清空，不呼叫第二次的話第二條會變空白。
_f89c="grep -n 'NOCHECK' /etc/security/passwd"
_f89m="1) 找出帶 NOCHECK 的帳號（查核指令會印行號）  2) chsec -f /etc/security/passwd -s <帳號> -a flags=   3) 重跑本腳本確認 NOCHECK 消失"
_f89e="需重設密碼時生效"
_std89="/etc/security/passwd 的 NOCHECK 旗標，( 無 NOCHECK )"
if [ "$AM_ROOT" -eq 1 ]; then
    _l=`grep -n 'NOCHECK' /etc/security/passwd 2>/dev/null | head -n 3 | tr '\n' ' '`
    if [ -n "$_l" ]; then
        set_fix "$_f89c" "$_f89m" "$_f89e" "步驟"
        emit "FCB-AIX-0089" "Non-Compliant" "$CAT_ACC" "新密碼受密碼原則控管（停用 NOCHECK）" "$_std89" "$_l"
        set_fix "$_f89c" "$_f89m" "$_f89e" "步驟"
        emit "FCB-AIX-0090" "Non-Compliant" "$CAT_ACC" "密碼原則適用所有使用者（NOCHECK）" "$_std89" "$_l"
    else
        set_fix "$_f89c" "$_f89m" "$_f89e" "步驟"
        emit "FCB-AIX-0089" "Compliant" "$CAT_ACC" "新密碼受密碼原則控管（停用 NOCHECK）" "$_std89" "/etc/security/passwd 無 NOCHECK"
        set_fix "$_f89c" "$_f89m" "$_f89e" "步驟"
        emit "FCB-AIX-0090" "Compliant" "$CAT_ACC" "密碼原則適用所有使用者（NOCHECK）" "$_std89" "/etc/security/passwd 無 NOCHECK"
    fi
else
    set_fix "$_f89c" "$_f89m" "$_f89e" "步驟"
    emit "FCB-AIX-0089" "Error" "$CAT_ACC" "新密碼受密碼原則控管（停用 NOCHECK）" "$_std89" "非 root，讀不到 /etc/security/passwd"
    set_fix "$_f89c" "$_f89m" "$_f89e" "步驟"
    emit "FCB-AIX-0090" "Error" "$CAT_ACC" "密碼原則適用所有使用者（NOCHECK）" "$_std89" "非 root，讀不到 /etc/security/passwd"
fi

ck_sec "FCB-AIX-0091" "密碼最短長度（minlen）"      "/etc/security/user default minlen"      "8" "ge" \
       "密碼最少要幾個字元" "字元"
ck_sec "FCB-AIX-0092" "密碼最少英文字母數（minalpha）"    "/etc/security/user default minalpha"    "1" "ge" \
       "密碼裡最少要有幾個英文字母" "個"
ck_sec "FCB-AIX-0093" "密碼最少非英文字母數（minother）"    "/etc/security/user default minother"    "1" "ge" \
       "密碼最少要幾個非英文字母字元（數字與符號合計）" "個"
ck_sec "FCB-AIX-0094" "同字元最多連續重複次數（maxrepeats）"  "/etc/security/user default maxrepeats"  "4" "le" \
       "同一個字元最多連續重複幾次" "個"
ck_sec "FCB-AIX-0095" "密碼最長使用期限（maxage）"      "/etc/security/user default maxage"      "13" "le" \
       "密碼最長可以用多久" "週"
ck_sec "FCB-AIX-0096" "密碼過期寬限期（maxexpired）"  "/etc/security/user default maxexpired"  "4" "le" \
       "密碼過期之後的寬限期" "週"

#=============================================================================
# 97　IgnoreRhosts
#=============================================================================
ck_sshd "FCB-AIX-0097" "SSH 忽略 rhosts 信任（IgnoreRhosts）" "IgnoreRhosts" "^(yes|shosts-only)$" \
        "SSH IgnoreRhosts 參數，( yes 或 shosts-only )"

#=============================================================================
# 98　密碼加密演算法
#=============================================================================
# lssec 讀、chsec 寫，參數一模一樣（跟 ck_sec 那 8 條同一個套路）。
# 生效方式是「需重設密碼時生效」：改了演算法，**既有密碼的雜湊不會自己換**，
# 要等使用者下次改密碼才會用新演算法重算。
set_fix "lssec -f /etc/security/login.cfg -s usw -a pwd_algorithm"         "chsec -f /etc/security/login.cfg -s usw -a pwd_algorithm=ssha512"         "需重設密碼時生效" "可執行"
_std98="密碼雜湊演算法，( pwd_algorithm = ssha512 )"
if have lssec; then _o=`lssec -f /etc/security/login.cfg -s usw -a pwd_algorithm 2>/dev/null | tail -n 1`; else _o=""; fi
_v=`echo "$_o" | sed 's/.*=//' | tr -d '" '`
if [ -z "$_o" ]; then
    emit "FCB-AIX-0098" "Error" "$CAT_ACC" "密碼雜湊演算法（pwd_algorithm）" "$_std98" "lssec 無輸出或無此指令（需 root／非 AIX）"
elif [ "$_v" = "ssha512" ]; then
    emit "FCB-AIX-0098" "Compliant" "$CAT_ACC" "密碼雜湊演算法（pwd_algorithm）" "$_std98" "$_o"
else
    emit "FCB-AIX-0098" "Non-Compliant" "$CAT_ACC" "密碼雜湊演算法（pwd_algorithm）" "$_std98" "$_o"
fi

#=============================================================================
# 收尾：這兩行是「這份檔案跑完了」的唯一憑證
#=============================================================================
N_T=`expr $N_C + $N_N + $N_A + $N_E`
N_TODO=`expr $N_N + $N_E`
T_END=`date '+%Y-%m-%d %H:%M:%S'`
echo "Check ended at: $T_END" >> "$tmp_file"
echo "Check summary: 合計 $N_T 項；Compliant $N_C；Non-Compliant $N_N；Not-Applicable $N_A；Error $N_E" >> "$tmp_file"

#-----------------------------------------------------------------------------
# 未完成清單（附在純文字報告最後）
#
# 「未完成」＝ Non-Compliant（查到了、不符合）＋ Error（沒查到，不可以當成合規）。
# Not-Applicable 不算未完成——那是觀察到的事實，不是觀察失敗。
#
# ⚠️ 這一段**刻意不用分號**：下游解析把「含分號的行」當成一筆檢核項目，
#    帶分號會被多算，害「宣稱 98 / 實際 N」的截斷判定誤報。
#
# ⚠️ 「0 項未完成」要能跟「根本沒檢查到」分得開，所以這裡同時印出
#    「已判定 N_T 項 / 應有 98 項」。兩者不等 = 這份結果不完整，
#    不管未完成數字是多少都不可信。
#-----------------------------------------------------------------------------
{
echo ""
echo "===== 未完成清單（Non-Compliant 與 Error，共 $N_TODO 項）====="
echo "已判定 $N_T 項 / 應有 98 項"
if [ "$N_T" -ne 98 ]; then
    echo "!! 已判定項數不等於 98 —— 這份結果不完整，下面的清單也不完整，不可採信。"
fi
if [ "$N_TODO" -eq 0 ]; then
    echo "（沒有 Non-Compliant，也沒有 Error。前提是上面那行「已判定 / 應有」相等，"
    echo "  而且檔尾有 Check ended at 與 Check summary 兩行——缺任何一個都代表中途死掉。）"
else
    echo "Non-Compliant $N_N 項（查到了、不符合基準）、Error $N_E 項（沒查到，不等於合規）"
    echo ""
    echo "-- 按生效方式分類（排維護窗口看這裡）--"
    echo "  需重開機（含需 bosboot）: $N_RB   <-- 這些要一起排停機"
    echo "  需重啟服務            : $N_SVC"
    echo "  立即生效              : $N_NOW"
    echo "  下次登入生效          : $N_LOGIN"
    echo "  需重設密碼時生效      : $N_PWD   <-- 既有密碼不會回頭被檢查，要等使用者改密碼"
    echo "  生效方式未確認        : $N_UNK   <-- 查核與修正指令都有，只是「什麼時候生效」要看現場（例如卸不卸得掉掛載、密碼到期判定時機）"
    echo ""
    echo "-- 逐條工作單 --"
    echo "每條四行：編號／狀態／項目，然後是查核指令、修正指令、生效方式。"
    echo "「未確認」代表這支腳本推不出可靠的指令，不是沒有事情要做——那幾條要自己查。"
    echo "修正指令只是文字，這支腳本不會替你執行；貼上去跑之前請自己看過。"
    cat "$pend_file" 2>/dev/null
fi
echo "===== 未完成清單結束 ====="
} >> "$tmp_file"

#-----------------------------------------------------------------------------
# 抬頭區塊（2026-09-30 加）
#
# 使用者實際拿去用之後回報：「我看不出來 aix.ksh 的版本是多少」。
# 版本戳其實一直都有印，但它是 11 行等寬灰字表頭裡的第 7 行，
# 夾在核心版本與一句 130 字的檢核基準中間——**有印不等於看得到**。
# 而且結束時間只出現在檔案最底下（98 列之後），開頭只有開始時間。
#
# 所以在最前面加一塊一眼能讀完的抬頭：這份是哪一版腳本、什麼時候跑的、
# 跑哪台、判定了幾項。四件事回答「這份報告是什麼、可不可信」。
#
# 為什麼要等到這裡才寫：結束時間與判定總數都是跑完才知道的。
# 所以先把抬頭寫成獨立暫存檔，再跟本體接起來、最後才改名
# ——維持「先寫暫存檔、跑完才改名」那條原則，不要為了加抬頭就讓半成品落地。
#
# ⚠️ 這一塊**不可以有分號**，也不可以出現「檢查結果」四個字：
#    下游解析把「含分號又含檢查結果」的行當成欄位標題列。
#    標籤也刻意避開既有的表頭 key（用「腳本版本戳」不是「腳本版本」）。
#-----------------------------------------------------------------------------
hdr_file="$OUTDIR/.fcb_aix_$$.hdr"
{
echo "================================================================================"
echo " FCB AIX 組態檢核報告"
echo "--------------------------------------------------------------------------------"
echo " 腳本版本戳: $SCRIPTV"
echo " 受檢主機  : $HOSTNAME  ($myIP)"
echo " 執行時間  : $T_START  ~  $T_END"
echo " 判定總數  : 已判定 $N_T 項 / 應有 98 項    未完成 $N_TODO 項（Non-Compliant $N_N、Error $N_E）"
echo " 執行身分  : $RUNAS"
if [ "$N_T" -ne 98 ]; then
    echo " !! 已判定項數不等於 98 —— 這份結果不完整，不可採信"
fi
if [ "`uname -s`" != "AIX" ]; then
    echo " !! 本機不是 AIX（uname -s = `uname -s`）—— 這份結果不可當成正式檢核"
fi
echo "================================================================================"
} > "$hdr_file"

cat "$hdr_file" "$tmp_file" > "$tmp_file.full"
mv "$tmp_file.full" "$flat_txt"
rm -f "$pend_file" "$hdr_file" "$tmp_file" 2>/dev/null
chmod 640 "$flat_txt" "$dbg_file" 2>/dev/null

#=============================================================================
# 轉 HTML
#
# 2026-09-30 改版理由（使用者原話：「目前無法篩選，我想快速找出那些沒有完成」）：
#   * 上方四張統計卡，Non-Compliant 與 Error 合稱「未完成」，點卡片就只剩那些列
#   * 每一欄的表頭可點擊排序（升冪／降冪切換）
#   * 一個關鍵字搜尋框，跨所有欄位比對
#   * 「參考來源」獨立成欄，可以直接照 CIS 條號排序
#
# 全部做在單一 HTML 檔裡（原生 JS，零外部資源）——這份檔案會被 scp 來 scp 去、
# 也會在沒有網路的正式機上直接用瀏覽器開，不可以依賴 CDN。
#
# 值一律 HTML 逸出：herald 這種欄位真的會帶 < > &，不逸出會把版面整個吃掉。
#=============================================================================
htmlesc() {
    printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
}

{
cat <<'FCBHTMLA'
<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
FCBHTMLA
# 標題要自己說明是哪一台、什麼時候——存檔或列印出來才分得出是哪一份。
printf '<title>AIX 組態檢核 %s %s</title><style>
' "$HOSTNAME" "$RUNSTAMP"
cat <<'FCBHTMLB'
body { font-family: "Microsoft JhengHei", "Noto Sans CJK TC", sans-serif; margin: 16px; color:#222; }
h1 { font-size: 20px; }
.meta { background:#f7f7f7; border:1px solid #ddd; padding:8px 12px; font-size:13px; line-height:1.7; }
/* 抬頭區塊：使用者說「我看不出來版本是多少」——版本戳本來就印在下面那塊灰字裡，
   但它是 11 行等寬小字的第 7 行，等於沒印。這一塊把「這份報告是什麼」講在最上面。 */
.top { border:2px solid #333; border-radius:6px; padding:12px 16px; margin:10px 0 6px; }
.top h2 { margin:0 0 8px; font-size:17px; }
.top dl { display:grid; grid-template-columns:auto 1fr; gap:4px 14px; margin:0; font-size:14px; }
.top dt { color:#555; white-space:nowrap; }
.top dd { margin:0; font-weight:600; }
.top .stamp { font-family:monospace; background:#fffbe6; border:1px solid #e8d48b;
              padding:1px 6px; border-radius:3px; }
.top .bad { color:#c00; }
.cards { display:flex; flex-wrap:wrap; gap:10px; margin:14px 0; }
.card { cursor:pointer; border:2px solid #ccc; border-radius:6px; padding:8px 14px; min-width:120px;
        background:#fff; user-select:none; }
.card:hover { background:#f0f0f0; }
.card.on { border-color:#333; background:#eee; }
.card .n { font-size:24px; font-weight:bold; display:block; }
.card.todo { border-color:#c00; }
.card.todo .n { color:#c00; }
.c-Compliant .n { color:#0a7d2b; }
.c-NonCompliant .n { color:#c00; }
.c-NotApplicable .n { color:#777; }
.c-Error .n { color:#d97706; }
#q { padding:6px 8px; width:320px; font-size:14px; }
/* 12 欄之下，讓瀏覽器自己分配寬度會把指令那兩欄擠成一個字一行。
   改成 table-layout:fixed + colgroup 明確指定百分比，並給整張表一個
   min-width，視窗不夠寬就整張橫向捲動，不要壓縮欄位。 */
.tblwrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; min-width: 1680px;
        table-layout: fixed; font-size:13px; }
td { overflow-wrap: break-word; }
th, td { border: 1px solid #bbb; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background-color: #f2f2f2; cursor:pointer; user-select:none; position:sticky; top:0; }
th .ar { color:#888; font-size:11px; }
td.std { font-size: 12.5px; }
td.cur { word-break: break-all; font-family: monospace; font-size:12px; }
td.ref { font-family: monospace; font-size: 12px; }
td.ctl { font-size: 12px; }
td.org { font-size: 12px; color:#555; }
td.cmd { font-family: monospace; font-size: 11.5px; overflow-wrap: break-word;
         position: relative; }
td.cmd.unk { font-family: inherit; color:#b45309; font-weight:600; }
td.cmd.na { font-family: inherit; color:#777; }
td.cmd.steps { font-family: inherit; font-size: 12px; }
td.cmd.steps ol { margin:4px 0 0; padding-left:1.3em; }
td.cmd.steps li { margin-bottom:3px; }
.kindtag { display:inline-block; font-size:10.5px; padding:1px 5px; border-radius:3px;
           background:#eef2ff; border:1px solid #c7d2fe; color:#3730a3; }
td.eff { white-space: nowrap; font-size: 12px; }
td.eff.reboot { color:#c00; font-weight:600; }
.cp { float:right; margin-left:6px; font-size:10.5px; padding:1px 5px; cursor:pointer;
      border:1px solid #bbb; border-radius:3px; background:#fafafa; }
.cp:hover { background:#eee; }
.filters { display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin:10px 0 4px; font-size:13px; }
.filters select { padding:5px 8px; font-size:13px; }
.filters label { display:flex; gap:6px; align-items:center; }
tr.r-Compliant td.res { color:#0a7d2b; }
tr.r-NonCompliant td.res, tr.r-NonCompliant td.cur { color:#c00; }
tr.r-NotApplicable td.res { color:#777; }
tr.r-Error td.res, tr.r-Error td.cur { color:#d97706; }
.hint { font-size:12px; color:#666; margin:6px 0 12px; }
.tail { background:#fffbe6; border:1px solid #e8d48b; padding:8px 12px; font-size:13px;
        white-space:pre-wrap; font-family:monospace; margin-top:18px; }
</style></head><body>
FCBHTMLB
} > "$output_file"

# --- 抬頭區塊：這份報告是什麼（版本／時間／主機／判定數）---
{
_e_host=`htmlesc "$HOSTNAME"`
_e_runas=`htmlesc "$RUNAS"`
echo '<div class="top">'
echo '<h2>FCB AIX 組態檢核報告</h2>'
echo '<dl>'
printf '<dt>腳本版本</dt><dd><span class="stamp">%s</span></dd>
' "$SCRIPTV"
printf '<dt>受檢主機</dt><dd>%s（%s）</dd>
' "$_e_host" "$myIP"
printf '<dt>執行時間</dt><dd>%s ~ %s</dd>
' "$T_START" "$T_END"
printf '<dt>判定總數</dt><dd>已判定 %s 項 / 應有 98 項 ｜ 未完成 <span class="bad">%s</span> 項（Non-Compliant %s、Error %s）</dd>
' "$N_T" "$N_TODO" "$N_N" "$N_E"
printf '<dt>執行身分</dt><dd>%s</dd>
' "$_e_runas"
echo '</dl>'
if [ "$N_T" -ne 98 ]; then
    printf '<p class="bad"><b>!! 已判定項數不等於 98 —— 這份結果不完整，不可採信</b></p>
'
fi
if [ "`uname -s`" != "AIX" ]; then
    printf '<p class="bad"><b>!! 本機不是 AIX（uname -s = %s）—— 這份結果不可當成正式檢核</b></p>
' "`uname -s`"
fi
echo '</div>'
} >> "$output_file"

# --- 表頭區（欄位標題列之前的 key: value 行）---
#
# ⚠️ .txt 最前面那塊 ==== 抬頭區塊要**跳過**：它的內容已經做成上面那張 .top 卡片，
#    照抄一次會在灰框裡重複顯示一整塊（2026-09-30 實際開畫面看到才抓到）。
#    用兩條 ==== 當界線，中間整段不輸出。
echo '<div class="meta">' >> "$output_file"
_inhdr=0
_first=1
while IFS= read -r line
do
    case "$line" in
        TWGCB-ID*) break ;;
    esac
    if [ "$_first" -eq 1 ]; then
        _first=0
        case "$line" in
            ====*) _inhdr=1; continue ;;
        esac
    fi
    if [ "$_inhdr" -eq 1 ]; then
        case "$line" in
            ====*) _inhdr=0 ;;
        esac
        continue
    fi
    case "$line" in
        "") ;;
        *)
            _e=`htmlesc "$line"`
            printf '%s<br>
' "$_e" >> "$output_file"
            ;;
    esac
done < "$flat_txt"
if [ -n "$OUTDIR_WARN" ]; then
    _e=`htmlesc "$OUTDIR_WARN"`
    printf '<br><b style="color:#c00">注意：%s</b>\n' "$_e" >> "$output_file"
fi
echo '</div>' >> "$output_file"

# --- 統計卡（點了就篩選）---
{
echo '<div class="cards">'
echo "<div class=\"card todo\" data-f=\"todo\"><span class=\"n\">$N_TODO</span>未完成<br><small>Non-Compliant + Error</small></div>"
echo "<div class=\"card c-NonCompliant\" data-f=\"Non-Compliant\"><span class=\"n\">$N_N</span>Non-Compliant</div>"
echo "<div class=\"card c-Error\" data-f=\"Error\"><span class=\"n\">$N_E</span>Error<br><small>沒查到，不等於合規</small></div>"
echo "<div class=\"card c-Compliant\" data-f=\"Compliant\"><span class=\"n\">$N_C</span>Compliant</div>"
echo "<div class=\"card c-NotApplicable\" data-f=\"Not-Applicable\"><span class=\"n\">$N_A</span>Not-Applicable</div>"
echo "<div class=\"card on\" data-f=\"all\"><span class=\"n\">$N_T</span>全部</div>"
echo '</div>'
echo '<div class="filters">'
echo '<label>CIS 控制項 <select id="ctl"><option value="">（全部）</option></select></label>'
echo '<label>生效方式 <select id="eff"><option value="">（全部）</option></select></label>'
echo '<input id="q" type="text" placeholder="關鍵字搜尋（編號／項目／條號／目前值）">'
echo '<span id="cnt"></span>'
echo '</div>'
echo '<div class="hint">狀態卡片、CIS 控制項、生效方式、關鍵字四個篩選會<b>疊加</b>（例如「未完成」＋「需重開機」＝這次停機要一起做完的清單；「未完成」＋「#5 帳號管理」＝帳號那族還沒完成的）。點欄位標題可排序，再點一次反向。已判定 '"$N_T"' 項／應有 98 項。</div>'
echo '<div class="hint">注意：「修正指令」只是<b>報告裡的文字</b>，這支腳本全程唯讀、不會替你執行。貼上去跑之前請自己看過一遍，「未確認」的那些沒有指令可貼，要自己查。</div>'
} >> "$output_file"

# --- 明細表 ---
{
echo '<div class="tblwrap"><table id="t">'
# 百分比合計 100。指令兩欄各給 12%，比原本擠成一團好讀很多。
echo '<colgroup>'
echo '<col style="width:5%"><col style="width:5%"><col style="width:5%"><col style="width:9%">'
echo '<col style="width:7%"><col style="width:7%"><col style="width:8%">'
echo '<col style="width:13%"><col style="width:11%">'
echo '<col style="width:12%"><col style="width:12%"><col style="width:6%">'
echo '</colgroup>'
echo '<thead><tr>'
echo '<th data-c="0">編號 <span class="ar"></span></th>'
echo '<th data-c="1">檢查結果 <span class="ar"></span></th>'
echo '<th data-c="2">類別 <span class="ar"></span></th>'
echo '<th data-c="3">原則設定名稱 <span class="ar"></span></th>'
echo '<th data-c="4">CIS基準條號 <span class="ar"></span></th>'
echo '<th data-c="5">CIS控制項 <span class="ar"></span></th>'
echo '<th data-c="6">本行對應條號 <span class="ar"></span></th>'
echo '<th data-c="7">標準設定值 <span class="ar"></span></th>'
echo '<th data-c="8">目前值 <span class="ar"></span></th>'
echo '<th data-c="9">查核指令 <span class="ar"></span></th>'
echo '<th data-c="10">修正指令 <span class="ar"></span></th>'
echo '<th data-c="11">生效方式 <span class="ar"></span></th>'
echo '</tr></thead><tbody>'
} >> "$output_file"

#-----------------------------------------------------------------------------
# 逐列產生 <tr>：**整批交給一次 awk**，不要在 shell 迴圈裡每列 fork 十幾次。
#
# 2026-09-30 改：原本每一列要跑 12 次 cut ＋ 12 次 htmlesc（各自 fork
# printf/sed），98 列就是一千多次 fork。實測這一段在 Windows/MSYS 上要
# 3 分 11 秒；改成單次 awk 之後 0.15 秒，而且產出**逐位元組相同**（已比對 md5）。
# AIX 的 fork 比較便宜，但一千多次仍然沒有意義。
#
# ⚠️ 逸出用 split+join，**不用 gsub**：gsub 的替換字串裡 & 代表「整個比對到的
#    文字」，要輸出字面 & 得靠 \& 跳脫，而那個跳脫在 gawk / mawk / AIX awk
#    之間行為不一致（gawk 還會噴 warning）。這支腳本只在 AIX 上跑、沒有測試機，
#    不能賭 awk 實作，所以改用完全不碰 & 語意的寫法。
#
# 同一次掃描順便把「欄位標題之後、不是項目列」的行（Check ended / summary /
# 未完成清單）逸出後寫進 $tail_file，給下面的 .tail 區塊用。
#-----------------------------------------------------------------------------
tail_file="$OUTDIR/.fcb_aix_$$.tail"
: > "$tail_file"
awk -F';' -v TAILF="$tail_file" '
function jrep(s, sep, rep,   n, a, i, out) {
    n = split(s, a, sep)
    out = a[1]
    for (i = 2; i <= n; i++) out = out rep a[i]
    return out
}
function esc(s) {
    s = jrep(s, "&", "&amp;")
    s = jrep(s, "<", "&lt;")
    s = jrep(s, ">", "&gt;")
    s = jrep(s, "\"", "&quot;")
    return s
}
BEGIN { seen = 0 }
seen == 0 { if ($0 ~ /^TWGCB-ID/) seen = 1; next }
/^FCB-/ || /^TWGCB-/ {
    id=$1; res=$2; cat=$3; nam=$4; std=$5; cur=$6; ref=$7; ctl=$8; org=$9; chk=$10; cmd=$11
    eff=$12; kind=$13
    for (i = 14; i <= NF; i++) kind = kind ";" $i
    if (org == "") org = "—"
    rcls = res; gsub(/-/, "", rcls)
    printf "<tr class=\"r-%s\" data-r=\"%s\" data-ctl=\"%s\" data-eff=\"%s\" data-kind=\"%s\"><td>%s</td><td class=\"res\">%s</td><td>%s</td><td>%s</td><td class=\"ref\">%s</td><td class=\"ctl\">%s</td><td class=\"org\">%s</td><td class=\"std\">%s</td><td class=\"cur\">%s</td><td class=\"cmd\">%s</td><td class=\"cmd\">%s</td><td class=\"eff\">%s</td></tr>\n", \
        rcls, esc(res), esc(ctl), esc(eff), esc(kind), esc(id), esc(res), esc(cat), esc(nam), \
        esc(ref), esc(ctl), esc(org), esc(std), esc(cur), esc(chk), esc(cmd), esc(eff)
    next
}
{ print esc($0) > TAILF }
' "$flat_txt" >> "$output_file"

echo '</tbody></table></div>' >> "$output_file"
echo '<div class="tail">' >> "$output_file"
# 上面那次 awk 已經把這些行逸出好寫進 $tail_file 了，直接接上去。
cat "$tail_file" >> "$output_file"
rm -f "$tail_file" 2>/dev/null
echo '</div>' >> "$output_file"

cat >> "$output_file" <<'FCBHTMLJS'
<script>
(function () {
  var tb = document.querySelector('#t tbody');
  var rows = Array.prototype.slice.call(tb.rows);
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  var q = document.getElementById('q');
  var ctl = document.getElementById('ctl');
  var eff = document.getElementById('eff');
  var cnt = document.getElementById('cnt');
  var filter = 'all';

  // CIS 控制項的下拉選項從資料本身長出來，不寫死——
  // 哪天檢核表多一個控制族群，這裡自己會多一個選項。
  // 刻意不用 CIS 基準條號做下拉：98 條幾乎條條不同，98 個選項等於沒有篩選。
  function fillSelect(sel, attr) {
    var seen = {};
    rows.forEach(function (tr) {
      var v = tr.getAttribute(attr) || '';
      if (!v) return;
      seen[v] = (seen[v] || 0) + 1;
    });
    Object.keys(seen).sort().forEach(function (v) {
      var o = document.createElement('option');
      o.value = v;
      o.textContent = v + '（' + seen[v] + ' 條）';
      sel.appendChild(o);
    });
  }
  fillSelect(ctl, 'data-ctl');
  // 生效方式是受控詞彙，所以下拉的選項數是可控的——「需重開機的有幾條」
  // 這個排維護窗口要用的數字，在選項上直接看得到。
  fillSelect(eff, 'data-eff');

  // 指令欄的複製鈕 = 一個承諾：按下去貼上就能跑。
  // 使用者看到 timed 那條的複製鈕時問「這個複製沒意義? 夾帶說明太多」——
  // 因為那格是步驟說明卻掛了複製鈕。**按鈕做不到它承諾的事，比沒有按鈕更糟。**
  // 所以只有 data-kind="可執行" 的列，修正指令那一格才給複製鈕；
  // 「步驟」那些改排成分行清單（.txt 維持一行，那是對外契約，不能動）。
  rows.forEach(function (tr) {
    var kind = tr.getAttribute('data-kind') || '';
    var fixTd = tr.cells[10];
    if (fixTd && kind === '步驟') {
      var raw = (fixTd.textContent || '').trim();
      // 把「1) … 2) …」拆成分行清單，讀起來才不會擠成一坨
      var parts = raw.split(/(?=\s\d\))/).map(function (x) { return x.trim(); })
                     .filter(function (x) { return x; });
      fixTd.classList.add('steps');
      fixTd.innerHTML = '';
      var tag = document.createElement('span');
      tag.className = 'kindtag';
      tag.textContent = '步驟（不能直接貼）';
      fixTd.appendChild(tag);
      if (parts.length > 1) {
        var ol = document.createElement('ol');
        parts.forEach(function (x) {
          var li = document.createElement('li');
          li.textContent = x.replace(/^\d\)\s*/, '');
          ol.appendChild(li);
        });
        fixTd.appendChild(ol);
      } else {
        var d = document.createElement('div');
        d.textContent = raw;
        fixTd.appendChild(d);
      }
    }
    [9, 10].forEach(function (c) {
      var td = tr.cells[c];
      if (!td) return;
      var txt = (td.textContent || '').trim();
      if (txt === '未確認') { td.classList.add('unk'); return; }
      if (txt === '本台不適用') { td.classList.add('na'); return; }
      // 修正指令那一格只有「可執行」才給複製鈕；查核指令（第 9 欄）一律可複製
      if (c === 10 && kind !== '可執行') return;
      var b = document.createElement('button');
      b.className = 'cp';
      b.type = 'button';
      b.textContent = '複製';
      b.addEventListener('click', function () {
        var t = td.getAttribute('data-raw');
        if (navigator.clipboard) {
          navigator.clipboard.writeText(t).then(function () {
            b.textContent = '已複製';
            setTimeout(function () { b.textContent = '複製'; }, 1200);
          });
        }
      });
      td.setAttribute('data-raw', txt);
      td.insertBefore(b, td.firstChild);
    });
    var e = tr.cells[11];
    if (e && (e.textContent || '').indexOf('重開機') >= 0) { e.classList.add('reboot'); }
  });

  // 三個條件是 AND：狀態卡片 × CIS 控制項 × 關鍵字。
  // 疊加才答得出「安全組態這一族裡還沒完成的有哪幾條」。
  function match(tr) {
    var r = tr.getAttribute('data-r');
    if (filter === 'todo') { if (r !== 'Non-Compliant' && r !== 'Error') return false; }
    else if (filter !== 'all') { if (r !== filter) return false; }
    if (ctl.value && (tr.getAttribute('data-ctl') || '') !== ctl.value) return false;
    if (eff.value && (tr.getAttribute('data-eff') || '') !== eff.value) return false;
    var kw = q.value.trim().toLowerCase();
    if (!kw) return true;
    return tr.textContent.toLowerCase().indexOf(kw) >= 0;
  }
  function apply() {
    var n = 0;
    rows.forEach(function (tr) {
      var ok = match(tr);
      tr.style.display = ok ? '' : 'none';
      if (ok) n++;
    });
    var todo = rows.filter(function (tr) {
      if (tr.style.display === 'none') return false;
      var r = tr.getAttribute('data-r');
      return r === 'Non-Compliant' || r === 'Error';
    }).length;
    cnt.textContent = '顯示 ' + n + ' / ' + rows.length + ' 項（其中未完成 ' + todo + ' 項）';
  }
  cards.forEach(function (c) {
    c.addEventListener('click', function () {
      cards.forEach(function (x) { x.classList.remove('on'); });
      c.classList.add('on');
      filter = c.getAttribute('data-f');
      apply();
    });
  });
  q.addEventListener('input', apply);
  ctl.addEventListener('change', apply);
  eff.addEventListener('change', apply);

  // 排序：同一欄再點一次就反向。數字型（編號尾碼、條號）用自然排序，
  // 不然 §4.10 會排在 §4.2 前面。
  var dir = {};
  function keyOf(tr, c) { return (tr.cells[c].textContent || '').trim(); }
  function natCmp(a, b) {
    var ra = a.match(/\d+|\D+/g) || [], rb = b.match(/\d+|\D+/g) || [];
    for (var i = 0; i < Math.max(ra.length, rb.length); i++) {
      var x = ra[i], y = rb[i];
      if (x === undefined) return -1;
      if (y === undefined) return 1;
      var nx = parseInt(x, 10), ny = parseInt(y, 10);
      if (!isNaN(nx) && !isNaN(ny)) { if (nx !== ny) return nx - ny; }
      else if (x !== y) return x < y ? -1 : 1;
    }
    return 0;
  }
  document.querySelectorAll('#t thead th').forEach(function (th) {
    th.addEventListener('click', function () {
      var c = parseInt(th.getAttribute('data-c'), 10);
      dir[c] = dir[c] === 1 ? -1 : 1;
      var d = dir[c];
      rows.sort(function (a, b) { return natCmp(keyOf(a, c), keyOf(b, c)) * d; });
      rows.forEach(function (tr) { tb.appendChild(tr); });
      document.querySelectorAll('#t thead th .ar').forEach(function (s) { s.textContent = ''; });
      th.querySelector('.ar').textContent = d === 1 ? '▲' : '▼';
    });
  });
  apply();
})();
</script>
</body></html>
FCBHTMLJS
chmod 640 "$output_file" 2>/dev/null

echo ""
echo "產出（都在 $OUTABS）："
echo "  $flat_txt"
echo "  $output_file"
echo "  $dbg_file   <-- 有問題請把這個檔（或畫面）回傳"
echo "合計 $N_T 項；Compliant $N_C；Non-Compliant $N_N；Not-Applicable $N_A；Error $N_E"
echo "未完成（Non-Compliant + Error）：$N_TODO 項——純文字報告最後有清單，HTML 點左上角那張紅卡片。"
[ -n "$OUTDIR_WARN" ] && echo "提醒：$OUTDIR_WARN"

# ⚠️ 一定要明確 exit 0：上面最後一行是 `[ -n … ] && echo`，沒有警告時它回 1，
# 整支腳本就會以離開碼 1 結束——排程／收集端會把「跑成功」當成「跑失敗」。
exit 0
