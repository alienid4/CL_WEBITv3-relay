#!/usr/bin/ksh
#=============================================================================
# run_fcb_aix_monthly.ksh —— FCB AIX 組態檢核的排程包裝程式（cron 呼叫這支）
#
# 由 install_fcb_aix_cron.ksh 裝進 root 的 crontab：
#     0 9 22-28 * * /usr/bin/ksh <本檔絕對路徑> >/dev/null 2>&1 # webit3-fcb-aix-monthly
#
# 【為什麼「第四個禮拜日」的判斷寫在這裡、不寫在 crontab】
#   cron 的「日期」與「星期」兩欄同時有值時是 OR，不是 AND：
#   `0 9 22-28 * 0` 會變成「22~28 號每天」＋「每個禮拜日」都跑。
#   所以 crontab 只寫 22-28 號，這裡再判斷今天是不是禮拜日；不是就安靜離開。
#   22~28 號之間的禮拜日剛好就是當月第四個禮拜日。
#
# 【稽核腳本怎麼挑】自動挑同目錄流水號最大的 fcb_aixV###.ksh。
#   取捨：換新版腳本時只要把新檔放進來，crontab 與本檔都不用改；
#   代價是同目錄若殘留別支更大號的檔就會跑到它——所以每次都把挑到哪一支、
#   同目錄共有幾支寫進 log，事後查得到。
#
# 【產出放哪】（使用者 2026-10-13 定）
#   1. 取件點 ${FCBAIX_PICKUP_DIR:-/tmp}：給其他系統來抓分析。
#      檔名照稽核腳本的預設（FCB_<主機>_<IP>_<YYYYMMDD_HHMM>.txt／.html／
#      _audit.html／.debug.log），不壓縮、不另開子目錄。
#      repo 裡找不到讀 /tmp 的收集端，所以沿用腳本預設檔名。
#      權限：有設 FCBAIX_PICKUP_GROUP 就 chgrp 該群組＋640；沒設就 644
#      （收集系統用哪個帳號不知道，只給 root 會讓它讀不到；代價是本機其他帳號也讀得到）。
#      /tmp 是所有人可寫的目錄：目標檔已存在（含 symlink）一律不寫、記 log，
#      寫入用 noclobber，不跟著別人預先放好的連結去覆寫系統檔。
#   2. log 目錄（＝歷史備份目錄；2026-10-14 起由安裝程式寫在同目錄 fcb_aix.conf 的
#      LOG_DIR=，沒有才退回 /var/tmp/fcbaudit）：目錄 700、檔案 600，只有 root。
#      這次產出的所有檔＋本次執行 log 打包成一個
#      fcbaudit_<主機>_<YYYYMMDD_HHMM>.tar.Z（AIX 一定有 compress；
#      找不到 compress 才改用 gzip 產 .tar.gz）。解開後逐一數過檔案數才算完成。
#      另外把這次的稽核版報告（_audit.html）在這裡也放一份（取件點 /tmp 那份照舊）。
#   3. Housekeeping：每次最後刪掉 log 目錄「本層」超過三年（1095 天）、且檔名符合
#      fcbaudit_*.tar.Z／fcbaudit_*.tar.gz／FCB_*_audit.html 的檔。其他檔一律不碰。
#   執行紀錄累積在 <log 目錄>/fcbaudit_monthly.log。
#
# 【ksh88 相容】沿用 fcb_aixV### 的守則：變數一律 ${VAR}、輸出一律 printf、
#   不用 bash 語法（[[ ]]、陣列、$'..'、<<<、local …），不用 echo。
#
# 本檔不寄信、不往外送任何東西；crontab 那行把輸出丟到 /dev/null，cron 也不會寄信。
#
# 環境變數（平常都不用設；測試或特殊情況才用）：
#   FCBAIX_FORCE_RUN=1     不是禮拜日也跑（手動補跑用）
#   FCBAIX_PICKUP_DIR      取件點，預設 /tmp
#   FCBAIX_PICKUP_GROUP    取件檔要給哪個群組讀（設了就 640，沒設 644）
#   FCBAIX_HIST_DIR        log／歷史備份目錄（優先於 fcb_aix.conf；兩者都沒有才用 /var/tmp/fcbaudit）
#   FCBAIX_KSH             用哪個 ksh 跑稽核腳本，預設 /usr/bin/ksh
#=============================================================================

PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin
export PATH
umask 077

# --- 第四個禮拜日判斷（cron 已限定 22-28 號；這裡再判一次，手動執行時也一樣準）---
# 略過時一律印一行：cron 那行把輸出導到 /dev/null 不會吵，手動跑的人才看得出「有跑、只是今天不是」。
_dow=`date +%w`
_dom=`date +%d`
if [ "${_dow}" != "0" ] || [ "${_dom}" -lt 22 ] || [ "${_dom}" -gt 28 ]; then
    if [ -z "${FCBAIX_FORCE_RUN}" ]; then
        printf '%s\n' "今天不是當月第四個禮拜日，略過；要強制跑請加 FCBAIX_FORCE_RUN=1"
        exit 0
    fi
fi

PICKUP_DIR="${FCBAIX_PICKUP_DIR:-/tmp}"
PICKUP_GROUP="${FCBAIX_PICKUP_GROUP:-}"
KSH_BIN="${FCBAIX_KSH:-/usr/bin/ksh}"

HOST=`hostname`
RUNSTAMP=`date +%Y%m%d_%H%M`
SDIR=`dirname "$0"`
SDIR=`cd "${SDIR}" 2>/dev/null && pwd`

# --- log／歷史目錄：環境變數 > 同目錄 fcb_aix.conf 的 LOG_DIR= > /var/tmp/fcbaudit ---
# fcb_aix.conf 由 install_fcb_aix_cron.ksh 寫（root、600，放在 root 的固定目錄）。
# 讀法是挑出 LOG_DIR= 那一行的值，不 source——設定檔只能給資料，不能給程式。
# 值不是絕對路徑或含空白就不採用，退回預設（cron 的輸出丟 /dev/null，停在這裡沒人看得到）。
CONF="${SDIR}/fcb_aix.conf"
CONF_DIR=""
if [ -f "${CONF}" ] && [ ! -h "${CONF}" ]; then
    CONF_DIR=`sed -n 's/^LOG_DIR=//p' "${CONF}" | head -n 1`
fi
case "${CONF_DIR}" in
    *" "*) CONF_DIR="" ;;
    /*)    : ;;
    *)     CONF_DIR="" ;;
esac
HIST_DIR="${FCBAIX_HIST_DIR:-${CONF_DIR:-/var/tmp/fcbaudit}}"

die() {
    printf '%s\n' "錯誤：$*" >&2
    exit 1
}

# 在 PATH 裡找指令。不用 command -v／whence：ksh88 與 POSIX sh 各有各的，這樣兩邊都一樣。
have() {
    _ifs0="${IFS}"
    IFS=:
    for _pd in ${PATH}; do
        if [ -x "${_pd}/$1" ]; then
            IFS="${_ifs0}"
            return 0
        fi
    done
    IFS="${_ifs0}"
    return 1
}

# --- 歷史目錄：700，只有 root。是 symlink 就停（root 不跟著別人的連結走）---
[ -h "${HIST_DIR}" ] && die "${HIST_DIR} 是符號連結，拒絕使用。"
if [ ! -d "${HIST_DIR}" ]; then
    mkdir -p "${HIST_DIR}" || die "建不出 ${HIST_DIR}"
fi
chmod 700 "${HIST_DIR}" || die "改不了 ${HIST_DIR} 的權限"

ROLLLOG="${HIST_DIR}/fcbaudit_monthly.log"
[ -h "${ROLLLOG}" ] && die "${ROLLLOG} 是符號連結，拒絕寫入。"
: >> "${ROLLLOG}" || die "寫不進 ${ROLLLOG}"
chmod 600 "${ROLLLOG}"

# 這次執行的暫存工作區。不用 mkdir -p：已經存在就是異常，要停。
WORK="${HIST_DIR}/.run_${RUNSTAMP}_$$"
mkdir "${WORK}" || die "建不出工作目錄 ${WORK}"
chmod 700 "${WORK}"
RUNLOG_NAME="fcbaudit_run_${HOST}_${RUNSTAMP}.log"
RUNLOG="${WORK}/${RUNLOG_NAME}"

log() {
    _ts=`date '+%Y-%m-%d %H:%M:%S'`
    printf '%s %s\n' "${_ts}" "$*" >> "${RUNLOG}"
    printf '%s %s\n' "${_ts}" "$*" >> "${ROLLLOG}"
}

cleanup_work() {
    # 只刪自己建的工作目錄，路徑不在歷史目錄底下就不動。
    case "${WORK}" in
        "${HIST_DIR}"/.run_*) rm -rf "${WORK}" ;;
    esac
}

FAIL=0

# --- 挑稽核腳本：同目錄流水號最大的那支 ---
SCRIPT=`ls "${SDIR}"/fcb_aixV[0-9][0-9][0-9].ksh 2>/dev/null | sort | tail -1`
NSCRIPT=`ls "${SDIR}"/fcb_aixV[0-9][0-9][0-9].ksh 2>/dev/null | wc -l | tr -d ' '`
if [ -z "${SCRIPT}" ]; then
    log "錯誤：${SDIR} 底下找不到 fcb_aixV###.ksh，這次沒有執行。"
    exit 1
fi
log "開始：主機 ${HOST}；稽核腳本 ${SCRIPT}（同目錄共 ${NSCRIPT} 支，取流水號最大）"

# --- 執行稽核腳本，產出先放在只有 root 讀得到的工作區 ---
FCBAIX_OUTDIR="${WORK}" "${KSH_BIN}" "${SCRIPT}" >> "${RUNLOG}" 2>&1
_rc=$?
log "稽核腳本結束，離開碼 ${_rc}"

FILES=`cd "${WORK}" && ls FCB_* 2>/dev/null`
TXT=`cd "${WORK}" && ls FCB_*.txt 2>/dev/null | head -n 1`

# 稽核腳本保證收尾一定印 Check summary；沒有就是中途死掉，那份不能給別的系統拿去分析。
COMPLETE=0
if [ -n "${TXT}" ] && grep "^Check summary:" "${WORK}/${TXT}" >/dev/null 2>&1; then
    COMPLETE=1
fi

# --- 1. 取件點（/tmp）：不壓縮，檔名照腳本預設 ---
if [ "${COMPLETE}" = 1 ]; then
    for _f in ${FILES}; do
        _dst="${PICKUP_DIR}/${_f}"
        if [ -h "${_dst}" ] || [ -e "${_dst}" ]; then
            log "取件點：${_dst} 已經存在（或是符號連結），不覆寫，這個檔沒有放上去。"
            FAIL=1
            continue
        fi
        if ( set -C; cat "${WORK}/${_f}" > "${_dst}" ) 2>> "${RUNLOG}"; then
            if [ -n "${PICKUP_GROUP}" ]; then
                if chgrp "${PICKUP_GROUP}" "${_dst}" 2>> "${RUNLOG}"; then
                    chmod 640 "${_dst}"
                    log "取件點：已放 ${_dst}（640，群組 ${PICKUP_GROUP}）"
                else
                    log "取件點：${_dst} chgrp ${PICKUP_GROUP} 失敗，維持 600（收集端可能讀不到）"
                    FAIL=1
                fi
            else
                chmod 644 "${_dst}"
                log "取件點：已放 ${_dst}（644）"
            fi
        else
            log "取件點：寫入 ${_dst} 失敗。"
            FAIL=1
        fi
    done
else
    log "錯誤：報告不完整（找不到 txt 或沒有 Check summary），不放到取件點 ${PICKUP_DIR}。"
    FAIL=1
fi

# --- 1b. 最新稽核版報告在 log 目錄也放一份（使用者 2026-10-14：/tmp 一份取件、log 目錄一份留存）---
AUDIT_HTML=`cd "${WORK}" && ls FCB_*_audit.html 2>/dev/null | head -n 1`
if [ "${COMPLETE}" = 1 ] && [ -n "${AUDIT_HTML}" ]; then
    _dst="${HIST_DIR}/${AUDIT_HTML}"
    if [ -h "${_dst}" ] || [ -e "${_dst}" ]; then
        log "log 目錄：${_dst} 已經存在（或是符號連結），不覆寫。"
        FAIL=1
    elif ( set -C; cat "${WORK}/${AUDIT_HTML}" > "${_dst}" ) 2>> "${RUNLOG}"; then
        chmod 600 "${_dst}"
        log "log 目錄：已放稽核版報告 ${_dst}（600）"
    else
        log "log 目錄：寫入 ${_dst} 失敗。"
        FAIL=1
    fi
fi

# --- 2. 歷史備份：這次的所有產出＋本次 log 打成一包 ---
if have compress; then
    ZEXT=Z
elif have gzip; then
    ZEXT=gz
else
    ZEXT=""
fi

ARC=""
if [ -z "${ZEXT}" ]; then
    log "錯誤：找不到 compress 也找不到 gzip，無法產生歷史備份；工作目錄 ${WORK} 保留未刪。"
    FAIL=1
else
    ARC="${HIST_DIR}/fcbaudit_${HOST}_${RUNSTAMP}.tar.${ZEXT}"
    if [ -h "${ARC}" ] || [ -e "${ARC}" ]; then
        ARC="${HIST_DIR}/fcbaudit_${HOST}_${RUNSTAMP}_$$.tar.${ZEXT}"
    fi
    NEXPECT=`(cd "${WORK}" && ls FCB_* "${RUNLOG_NAME}" 2>/dev/null) | wc -l | tr -d ' '`
    log "歷史備份：打包 ${NEXPECT} 個檔（這次的報告＋本次 log）-> ${ARC}"
    ( cd "${WORK}" && tar -cf .arc.tar ${FILES} "${RUNLOG_NAME}" ) 2>> "${RUNLOG}"
    if [ "${ZEXT}" = Z ]; then
        ( set -C; compress -f -c "${WORK}/.arc.tar" > "${ARC}" ) 2>> "${RUNLOG}"
        NGOT=`uncompress -c "${ARC}" 2>/dev/null | tar -tf - 2>/dev/null | wc -l | tr -d ' '`
    else
        ( set -C; gzip -c "${WORK}/.arc.tar" > "${ARC}" ) 2>> "${RUNLOG}"
        NGOT=`gzip -dc "${ARC}" 2>/dev/null | tar -tf - 2>/dev/null | wc -l | tr -d ' '`
    fi
    if [ -s "${ARC}" ] && [ "${NGOT}" = "${NEXPECT}" ] && [ "${NEXPECT}" -gt 0 ]; then
        chmod 600 "${ARC}"
        log "歷史備份：完成 ${ARC}（解開核對 ${NGOT} 個檔，600）"
        cleanup_work
    else
        log "錯誤：歷史備份核對失敗（預期 ${NEXPECT} 個檔，解開數到 ${NGOT} 個）；工作目錄 ${WORK} 保留未刪。"
        FAIL=1
    fi
fi

# --- 3. Housekeeping：只刪歷史目錄本層、本工具檔名、超過三年的備份與稽核版報告 ---
# AIX find 沒有 -maxdepth：用「子目錄一律 -prune」限定只看本層。
find "${HIST_DIR}/." \( -type d ! -name . -prune \) -o \
     \( -type f \( -name 'fcbaudit_*.tar.*' -o -name 'FCB_*_audit.html' \) -mtime +1095 -print \) 2>> "${ROLLLOG}" |
while read _old; do
    _b=`basename "${_old}"`
    case "${_b}" in
        fcbaudit_*.tar.Z|fcbaudit_*.tar.gz|FCB_*_audit.html) ;;
        *) continue ;;
    esac
    if rm -f "${HIST_DIR}/${_b}"; then
        _ts=`date '+%Y-%m-%d %H:%M:%S'`
        printf '%s %s\n' "${_ts}" "housekeeping：刪除超過三年的備份 ${HIST_DIR}/${_b}" >> "${ROLLLOG}"
    fi
done

if [ "${FAIL}" = 0 ]; then
    _ts=`date '+%Y-%m-%d %H:%M:%S'`
    printf '%s %s\n' "${_ts}" "結束：成功" >> "${ROLLLOG}"
    exit 0
fi
_ts=`date '+%Y-%m-%d %H:%M:%S'`
printf '%s %s\n' "${_ts}" "結束：有錯誤，詳見上面幾行" >> "${ROLLLOG}"
exit 1
