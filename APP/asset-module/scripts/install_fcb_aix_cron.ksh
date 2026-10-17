#!/usr/bin/ksh
#=============================================================================
# install_fcb_aix_cron.ksh —— 把「每月第四個禮拜日 09:00 跑 FCB AIX 檢核」裝進 root 的 crontab
#
# 用法（要用 root：FCB 檢核有很多項只有 root 讀得到）：
#     FCBAIX_INSTALL_DIR=<固定目錄> FCBAIX_LOG_DIR=<log 目錄> ksh ./install_fcb_aix_cron.ksh   第一次安裝
#     ksh ./install_fcb_aix_cron.ksh                       之後重裝／更新（沿用排程與設定檔裡的目錄）
#     ksh ./install_fcb_aix_cron.ksh -r                    移除（只刪本工具那一行）
#
# 安裝做這幾件事：
#   1. 把本目錄的 run_fcb_aix_monthly.ksh、最新一支 fcb_aixV###.ksh、本檔
#      複製到固定目錄（不存在就 mkdir -p；目錄 755、檔案 700、owner root）。
#   2. 建 log 目錄（700、root），並在固定目錄寫 fcb_aix.conf（LOG_DIR=…，600）；
#      包裝程式從那裡讀，cron 跑與手動跑都寫到同一個地方。
#   3. 舊的歷史目錄 /var/tmp/fcbaudit 若存在，逐檔搬到 log 目錄（複製、cksum 一致才刪原檔）。
#   4. crontab 一律指向「固定目錄/run_fcb_aix_monthly.ksh」，不指向本檔所在目錄——
#      所以從哪裡執行（例如剛傳上來的 /tmp/lab）結果都一樣。
#   log 目錄同固定目錄：FCBAIX_LOG_DIR，沒設就沿用固定目錄裡 fcb_aix.conf 的值，都沒有就停。
#
# 固定目錄從哪來（不寫死在程式裡：各環境的目錄不同，而且這支會進公開中繼）：
#   1. 環境變數 FCBAIX_INSTALL_DIR
#   2. 沒設就沿用 crontab 裡本工具那一行的目錄（重裝／換新版時不用再打一次）
#   3. 兩者都沒有就停下來要你指定——不自己挑一個預設目錄（挑錯了會裝到沒人知道的地方）
#   固定目錄不可以在 /tmp、/var/tmp 底下：重開機或清理後腳本不見，cron 照樣每月叫，
#   但叫不到東西、沒有 log、也沒人知道——排程靜默失效。
#
# 裝進去的那一行：
#     0 9 22-28 * * /usr/bin/ksh <固定目錄>/run_fcb_aix_monthly.ksh >/dev/null 2>&1 # webit3-fcb-aix-monthly
#   cron 只限定 22~28 號；「是不是禮拜日」由 run_fcb_aix_monthly.ksh 判斷。
#   不能寫成 `0 9 22-28 * 0`：日期與星期兩欄同時有值時 cron 取 OR，會變成每天跑。
#
# 為什麼另寫一支、不寫進稽核腳本：稽核腳本必須維持唯讀、不改系統設定。
#
# 安全做法：
#   * 冪等：標記 `# webit3-fcb-aix-monthly` 那一行已經是同一行就不動；指向別的目錄
#     （例如舊的 /tmp/lab）就換掉——永遠只留一行。
#   * 不覆蓋別人的排程：crontab -l 取出 -> 換掉／附加本工具那一行 -> crontab <檔案>，
#     裝完再讀回來核對「標記剛好一行、其他行不變」。
#   * crontab -l 失敗時分兩種：原本就沒有 crontab（正常，視為空白），
#     或讀不到但 spool 檔其實存在（停下來，絕不拿空白蓋掉既有排程）。
#   * 固定目錄若是符號連結就停（root 照著連結寫檔，等於讓連結的主人決定寫到哪）。
#   * 複製先寫暫存檔再 mv，不會留下寫到一半的腳本給 cron 叫。
#
# ksh88 相容：變數一律 ${VAR}、輸出一律 printf、不用 bash 語法、不用 echo。
#
# 測試用環境變數（平常不要設）：
#   FCBAIX_CRONTAB_CMD   取代 crontab 指令（測試時換成用檔案模擬的假 crontab）
#   FCBAIX_TEST_NOROOT=1 略過 root 檢查與 chown（只給模擬測試用）
#   FCBAIX_CRON_SPOOL    取代 /var/spool/cron（測試用，以 root 跑測試時不可碰真機 spool）
#   FCBAIX_TEST_TMP_OK=1 允許固定目錄／log 目錄在 /tmp 底下（測試的暫存目錄就在 /tmp）
#   FCBAIX_OLD_HIST_DIR  取代要搬移的舊歷史目錄 /var/tmp/fcbaudit
#=============================================================================

PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin
export PATH
umask 077

MARK_ID="webit3-fcb-aix-monthly"
MARK="# ${MARK_ID}"
CRON_SPEC="0 9 22-28 * *"
KSH_PATH="/usr/bin/ksh"
WRAPPER_NAME="run_fcb_aix_monthly.ksh"
SELF_NAME="install_fcb_aix_cron.ksh"
CONF_NAME="fcb_aix.conf"
CRONTAB_CMD="${FCBAIX_CRONTAB_CMD:-crontab}"
CRON_SPOOL="${FCBAIX_CRON_SPOOL:-/var/spool/cron}"

die() {
    printf '%s\n' "錯誤：$*" >&2
    exit 1
}

ACTION=install
case "$1" in
    "")  ACTION=install ;;
    -r)  ACTION=remove ;;
    *)   die "不認得的參數 $1。用法：[FCBAIX_INSTALL_DIR=<固定目錄>] ksh install_fcb_aix_cron.ksh [-r]" ;;
esac

# --- root 檢查 ---
if [ -z "${FCBAIX_TEST_NOROOT}" ]; then
    _uid=`id -u`
    [ "${_uid}" = "0" ] || die "請用 root 執行（FCB 檢核有很多項只有 root 讀得到，排程要裝在 root 的 crontab）。"
fi
ME=`id -un 2>/dev/null`

# --- 本檔所在目錄（複製的來源）---
SDIR=`dirname "$0"`
SDIR=`cd "${SDIR}" 2>/dev/null && pwd`

# --- 私人暫存目錄（不用 -p：已存在就是異常）---
TMPD="/tmp/.fcbaix_cron_$$"
mkdir "${TMPD}" || die "建不出暫存目錄 ${TMPD}"
chmod 700 "${TMPD}"
trap 'rm -rf "${TMPD}"' 0
trap 'exit 1' 1 2 15

# --- 取出目前的 crontab ---
"${CRONTAB_CMD}" -l > "${TMPD}/cur" 2> "${TMPD}/err"
_rc=$?
if [ "${_rc}" -ne 0 ]; then
    # 讀不到：是「原本沒有 crontab」還是「有但讀不到」？後者絕不能拿空白去蓋。
    for _sp in "${CRON_SPOOL}/crontabs/${ME}" "${CRON_SPOOL}/${ME}"; do
        if [ -n "${ME}" ] && [ -s "${_sp}" ]; then
            die "crontab -l 失敗，但 ${_sp} 存在且有內容。為了不蓋掉既有排程，停止。（crontab -l 訊息：`cat "${TMPD}/err"`）"
        fi
    done
    _msg=`cat "${TMPD}/err"`
    printf '%s\n' "提醒：目前沒有 crontab（${_msg}），視為空白。"
    : > "${TMPD}/cur"
fi

# 統一補上結尾換行（最後一行沒有換行的話，附加的那行會黏在它後面）。
awk '{ print }' "${TMPD}/cur" > "${TMPD}/base"
grep -v "${MARK_ID}" "${TMPD}/base" > "${TMPD}/keep"
NKEEP=`wc -l < "${TMPD}/keep" | tr -d ' '`
# 目前本工具那一行（可能沒有、可能指向舊目錄）
OLD_LINE=`grep "${MARK_ID}" "${TMPD}/base" | head -1`

if [ "${ACTION}" = install ]; then
    # --- 決定固定目錄 ---
    _olddir=`printf '%s\n' "${OLD_LINE}" | awk '{ for (i = 1; i <= NF; i++) if ($i ~ /\/run_fcb_aix_monthly\.ksh$/) { print $i; exit } }' | sed 's,/run_fcb_aix_monthly\.ksh$,,'`
    if [ -z "${FCBAIX_TEST_TMP_OK}" ]; then
        case "${_olddir}" in
            /tmp|/tmp/*|/var/tmp|/var/tmp/*) _olddir="" ;;   # 舊的裝在暫存區，不沿用
        esac
    fi
    if [ -n "${FCBAIX_INSTALL_DIR}" ]; then
        INSTALL_DIR="${FCBAIX_INSTALL_DIR}"
    elif [ -n "${_olddir}" ]; then
        INSTALL_DIR="${_olddir}"
        printf '%s\n' "沿用排程裡的固定目錄：${INSTALL_DIR}"
    else
        die "第一次安裝請指定固定目錄：FCBAIX_INSTALL_DIR=<固定目錄> ksh ./${SELF_NAME}（不可以在 /tmp、/var/tmp 底下）。"
    fi
    case "${INSTALL_DIR}" in
        /*) : ;;
        *)  die "固定目錄要寫絕對路徑（/ 開頭）：${INSTALL_DIR}" ;;
    esac
    case "${INSTALL_DIR}" in
        *" "*) die "固定目錄 ${INSTALL_DIR} 含空白，crontab 那一行會被拆開。" ;;
    esac
    INSTALL_DIR=`printf '%s\n' "${INSTALL_DIR}" | sed 's,/*$,,'`
    if [ -z "${FCBAIX_TEST_TMP_OK}" ]; then
        case "${INSTALL_DIR}" in
            /tmp|/tmp/*|/var/tmp|/var/tmp/*)
                die "固定目錄 ${INSTALL_DIR} 在 /tmp 或 /var/tmp 底下，重開機或清理後排程會靜默失效。請換一個固定目錄。" ;;
        esac
    fi
    [ -h "${INSTALL_DIR}" ] && die "固定目錄 ${INSTALL_DIR} 是符號連結，停止（請直接指定實際目錄）。"

    # --- 決定 log 目錄：FCBAIX_LOG_DIR > 固定目錄裡既有的 fcb_aix.conf > 停下來要你指定 ---
    CONF="${INSTALL_DIR}/${CONF_NAME}"
    _oldlog=""
    if [ -f "${CONF}" ] && [ ! -h "${CONF}" ]; then
        _oldlog=`sed -n 's/^LOG_DIR=//p' "${CONF}" | head -n 1`
    fi
    if [ -n "${FCBAIX_LOG_DIR}" ]; then
        LOG_DIR="${FCBAIX_LOG_DIR}"
    elif [ -n "${_oldlog}" ]; then
        LOG_DIR="${_oldlog}"
        printf '%s\n' "沿用 ${CONF} 的 log 目錄：${LOG_DIR}"
    else
        die "第一次安裝請指定 log 目錄：FCBAIX_INSTALL_DIR=<固定目錄> FCBAIX_LOG_DIR=<log 目錄> ksh ./${SELF_NAME}（不可以在 /tmp、/var/tmp 底下）。"
    fi
    case "${LOG_DIR}" in
        *" "*) die "log 目錄 ${LOG_DIR} 含空白。" ;;
        /*)    : ;;
        *)     die "log 目錄要寫絕對路徑（/ 開頭）：${LOG_DIR}" ;;
    esac
    LOG_DIR=`printf '%s\n' "${LOG_DIR}" | sed 's,/*$,,'`
    if [ -z "${FCBAIX_TEST_TMP_OK}" ]; then
        case "${LOG_DIR}" in
            /tmp|/tmp/*|/var/tmp|/var/tmp/*)
                die "log 目錄 ${LOG_DIR} 在 /tmp 或 /var/tmp 底下，會被清掉。請換一個固定目錄。" ;;
        esac
    fi
    [ -h "${LOG_DIR}" ] && die "log 目錄 ${LOG_DIR} 是符號連結，停止（請直接指定實際目錄）。"

    # --- 來源檔 ---
    SRC_AUDIT=`ls "${SDIR}"/fcb_aixV[0-9][0-9][0-9].ksh 2>/dev/null | sort | tail -1`
    [ -n "${SRC_AUDIT}" ] || die "${SDIR} 底下沒有 fcb_aixV###.ksh，裝了排程也沒東西可跑。"
    [ -f "${SDIR}/${WRAPPER_NAME}" ] || die "找不到 ${SDIR}/${WRAPPER_NAME}（要跟本檔放在同一個目錄）。"
    AUDIT_NAME=`basename "${SRC_AUDIT}"`

    # --- 複製到固定目錄 ---
    mkdir -p "${INSTALL_DIR}" || die "建不出固定目錄 ${INSTALL_DIR}"
    chmod 755 "${INSTALL_DIR}" || die "改不了 ${INSTALL_DIR} 的權限"
    [ -n "${FCBAIX_TEST_NOROOT}" ] || chown root "${INSTALL_DIR}" || die "改不了 ${INSTALL_DIR} 的 owner"
    if [ "${SDIR}" = "${INSTALL_DIR}" ]; then
        printf '%s\n' "本檔就在固定目錄 ${INSTALL_DIR}，不用複製。"
    else
        for _f in "${WRAPPER_NAME}" "${AUDIT_NAME}" "${SELF_NAME}"; do
            _tmpf="${INSTALL_DIR}/.${_f}.$$"
            rm -f "${_tmpf}"
            cp "${SDIR}/${_f}" "${_tmpf}" || die "複製 ${_f} 到 ${INSTALL_DIR} 失敗"
            chmod 700 "${_tmpf}"
            [ -n "${FCBAIX_TEST_NOROOT}" ] || chown root "${_tmpf}"
            mv -f "${_tmpf}" "${INSTALL_DIR}/${_f}" || die "放不進 ${INSTALL_DIR}/${_f}"
        done
        printf '%s\n' "已複製到 ${INSTALL_DIR}：${WRAPPER_NAME}、${AUDIT_NAME}、${SELF_NAME}"
    fi
    # 包裝程式會挑「流水號最大」那支跑；舊版留著只會讓人搞混，提醒但不替你刪。
    for _o in `ls "${INSTALL_DIR}"/fcb_aixV[0-9][0-9][0-9].ksh 2>/dev/null`; do
        [ "`basename "${_o}"`" = "${AUDIT_NAME}" ] || printf '%s\n' "提醒：${INSTALL_DIR} 還有舊版 `basename "${_o}"`，確認不需要後請自行刪除。"
    done

    # --- log 目錄＋設定檔（包裝程式從同目錄的 fcb_aix.conf 讀 LOG_DIR=，手動跑與 cron 跑同一個地方）---
    mkdir -p "${LOG_DIR}" || die "建不出 log 目錄 ${LOG_DIR}"
    chmod 700 "${LOG_DIR}" || die "改不了 ${LOG_DIR} 的權限"
    [ -n "${FCBAIX_TEST_NOROOT}" ] || chown root "${LOG_DIR}" || die "改不了 ${LOG_DIR} 的 owner"
    _tmpc="${INSTALL_DIR}/.${CONF_NAME}.$$"
    rm -f "${_tmpc}"
    {
        printf '%s\n' "# 由 ${SELF_NAME} 產生；包裝程式只讀 LOG_DIR= 這一行（不 source）。重跑安裝程式會覆寫。"
        printf '%s\n' "LOG_DIR=${LOG_DIR}"
    } > "${_tmpc}" || die "寫不出 ${CONF}"
    chmod 600 "${_tmpc}"
    [ -n "${FCBAIX_TEST_NOROOT}" ] || chown root "${_tmpc}"
    mv -f "${_tmpc}" "${CONF}" || die "放不進 ${CONF}"
    printf '%s\n' "log 目錄：${LOG_DIR}（設定寫在 ${CONF}）"

    # --- 舊歷史目錄 /var/tmp/fcbaudit 搬到 log 目錄（使用者 2026-10-14 要求自動搬）---
    # 逐檔：複製 -> cksum 一致才刪原檔；不一致、目的地已有不同內容、子目錄、符號連結都保留並印出。
    # 冪等：目的地已有同內容的檔就只刪原檔；舊目錄空了才 rmdir。
    OLD_HIST="${FCBAIX_OLD_HIST_DIR:-/var/tmp/fcbaudit}"
    if [ "${OLD_HIST}" != "${LOG_DIR}" ] && [ -d "${OLD_HIST}" ] && [ ! -h "${OLD_HIST}" ]; then
        _nmv=0
        _nkeep=0
        for _b in `ls -A "${OLD_HIST}" 2>/dev/null`; do
            _s="${OLD_HIST}/${_b}"
            _d="${LOG_DIR}/${_b}"
            if [ -h "${_s}" ] || [ ! -f "${_s}" ]; then
                printf '%s\n' "搬移：${_s} 不是一般檔案（子目錄或符號連結），保留未搬。"
                _nkeep=`expr ${_nkeep} + 1`
                continue
            fi
            _cs=`cksum < "${_s}"`
            if [ -h "${_d}" ] || [ -e "${_d}" ]; then
                _cd=`cksum < "${_d}" 2>/dev/null`
                if [ ! -h "${_d}" ] && [ "${_cs}" = "${_cd}" ]; then
                    rm -f "${_s}" && _nmv=`expr ${_nmv} + 1`
                else
                    printf '%s\n' "搬移：${_d} 已存在且內容不同，保留原檔 ${_s}。"
                    _nkeep=`expr ${_nkeep} + 1`
                fi
                continue
            fi
            _t="${LOG_DIR}/.mv_${_b}.$$"
            rm -f "${_t}"
            if cp -p "${_s}" "${_t}" 2>/dev/null && [ "`cksum < "${_t}"`" = "${_cs}" ]; then
                chmod 600 "${_t}"
                mv -f "${_t}" "${_d}" && rm -f "${_s}" && _nmv=`expr ${_nmv} + 1`
            else
                rm -f "${_t}"
                printf '%s\n' "搬移：${_s} 複製後核對不一致（或複製失敗），保留原檔。"
                _nkeep=`expr ${_nkeep} + 1`
            fi
        done
        if rmdir "${OLD_HIST}" 2>/dev/null; then
            printf '%s\n' "搬移：${OLD_HIST} -> ${LOG_DIR} 完成（${_nmv} 個檔），舊目錄已移除。"
        else
            printf '%s\n' "搬移：${OLD_HIST} -> ${LOG_DIR} 搬了 ${_nmv} 個檔，保留 ${_nkeep} 項在舊目錄（見上面），請自行確認。"
        fi
    fi

    WRAPPER="${INSTALL_DIR}/${WRAPPER_NAME}"
    CRON_LINE="${CRON_SPEC} ${KSH_PATH} ${WRAPPER} >/dev/null 2>&1 ${MARK}"

    # --- 排程：已經是同一行就不動；沒有就加；指向別處就換 ---
    _nmark0=`grep -c "${MARK_ID}" "${TMPD}/base"`
    if [ "${_nmark0}" = "1" ] && [ "${OLD_LINE}" = "${CRON_LINE}" ]; then
        printf '%s\n' "排程已安裝，略過。目前那一行："
        printf '%s\n' "${OLD_LINE}"
        exit 0
    fi
    cat "${TMPD}/keep" > "${TMPD}/new"
    printf '%s\n' "${CRON_LINE}" >> "${TMPD}/new"
    "${CRONTAB_CMD}" "${TMPD}/new" || die "crontab 寫入失敗，原本的排程沒有被更動。"
    "${CRONTAB_CMD}" -l > "${TMPD}/after" 2> /dev/null || die "裝完讀不回 crontab，請手動 crontab -l 檢查。"
    _nmark=`grep -c "${MARK_ID}" "${TMPD}/after"`
    _nnew=`grep -v "${MARK_ID}" "${TMPD}/after" | wc -l | tr -d ' '`
    if [ "${_nmark}" != "1" ] || [ "${_nnew}" != "${NKEEP}" ]; then
        die "裝完核對不符（標記 ${_nmark} 行、其他 ${_nnew} 行，原本 ${NKEEP} 行）。請手動 crontab -l 檢查。"
    fi
    if [ -n "${OLD_LINE}" ]; then
        printf '%s\n' "已換掉舊的排程（其他 ${NKEEP} 行保留不動）："
        printf '%s\n' "  舊：${OLD_LINE}"
        printf '%s\n' "  新：${CRON_LINE}"
    else
        printf '%s\n' "已安裝（原有 ${NKEEP} 行保留不動，新增 1 行）："
        printf '%s\n' "${CRON_LINE}"
    fi
    printf '%s\n' "確認：crontab -l | grep ${MARK_ID}"
    exit 0
fi

# --- 移除 ---
# 不寫 `if ! grep`：ksh88 不支援 `!` 反轉。
if [ -n "${OLD_LINE}" ]; then
    :
else
    printf '%s\n' "沒有安裝，略過。"
    exit 0
fi
"${CRONTAB_CMD}" "${TMPD}/keep" || die "crontab 寫入失敗，原本的排程沒有被更動。"
"${CRONTAB_CMD}" -l > "${TMPD}/after" 2> /dev/null
if grep "${MARK_ID}" "${TMPD}/after" > /dev/null 2>&1; then
    die "移除後仍看得到 ${MARK_ID}，請手動 crontab -l 檢查。"
fi
printf '%s\n' "已移除本工具的排程（其他行保留不動；固定目錄裡的腳本沒有刪）。"
exit 0
