#!/usr/bin/ksh
#=============================================================================
# fcb_aix_onekey.ksh —— FCB AIX 每月檢核：一鍵安裝＋驗證
#
# 用法（root）：
#     ksh ./fcb_aix_onekey.ksh <基底目錄>
#   腳本目錄 = <基底目錄>/script、log 目錄 = <基底目錄>/log/fcb。
#   基底目錄由執行的人帶進來，程式裡不寫（各環境不同，而且這支會進公開中繼）。
#
# 本檔要跟 fcb_aixV###.ksh、install_fcb_aix_cron.ksh、run_fcb_aix_monthly.ksh
# 放在同一個目錄（例如剛傳上來的 /tmp/lab）。
#
# 七步，每步印「===== N. xxx =====」，任何一步失敗就停並講是哪一步：
#   1. 三支轉 LF（從 Windows 傳上來一定帶 CR）
#   2. 印 cksum，跟本檔內建的預期值比對（發版時寫入；對不上＝檔案不是這一版，停）
#   3. 呼叫 install_fcb_aix_cron.ksh（帶 FCBAIX_INSTALL_DIR／FCBAIX_LOG_DIR）
#   4. crontab 本工具那一行：剛好一行、指向腳本目錄
#   5. 印 fcb_aix.conf
#   6. FCBAIX_FORCE_RUN=1 實跑一次，印離開碼
#   7. monthly.log 最後幾行、腳本目錄／log 目錄最新版（FCB_latest*）／取件點的稽核版報告、
#      舊目錄 /var/tmp/fcbaudit 是否搬空
#   最後一行：「全部通過」或「第 N 步失敗」。
#
# 冪等：已裝過的機器重跑一樣安全（安裝程式本身冪等；第 6 步每跑一次多一份報告）。
# 同一分鐘內重跑第 6 步，取件點會因同名檔已存在而記錯誤——隔一分鐘再跑。
#
# ksh88 相容：變數一律 ${VAR}、輸出一律 printf、不用 bash 語法、不用 echo。
#
# 測試用環境變數（平常不要設）：沿用安裝程式與包裝程式的那幾個
#   FCBAIX_TEST_NOROOT、FCBAIX_TEST_TMP_OK、FCBAIX_CRONTAB_CMD、FCBAIX_CRON_SPOOL、
#   FCBAIX_OLD_HIST_DIR、FCBAIX_PICKUP_DIR、FCBAIX_KSH
#=============================================================================

PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin
export PATH
umask 077

# --- 發版時寫入的預期值（tests/asset_module/test_fcb_aix_onekey.py 會核對跟 repo 裡的檔一致）---
EXPECT_AUDIT_NAME="fcb_aixV013.ksh"
EXPECT_AUDIT_CKSUM="1435837933 153260"
EXPECT_INSTALL_CKSUM="227939643 16098"
EXPECT_RUN_CKSUM="119386896 16639"

MARK_ID="webit3-fcb-aix-monthly"
INSTALL_NAME="install_fcb_aix_cron.ksh"
RUN_NAME="run_fcb_aix_monthly.ksh"
KSH_BIN="${FCBAIX_KSH:-/usr/bin/ksh}"
CRONTAB_CMD="${FCBAIX_CRONTAB_CMD:-crontab}"
PICKUP_DIR="${FCBAIX_PICKUP_DIR:-/tmp}"
OLD_HIST="${FCBAIX_OLD_HIST_DIR:-/var/tmp/fcbaudit}"

STEP=0
NWARN=0

usage() {
    printf '%s\n' "用法：ksh ./fcb_aix_onekey.ksh <基底目錄>" >&2
    printf '%s\n' "  腳本裝到 <基底目錄>/script，log 放 <基底目錄>/log/fcb。" >&2
    printf '%s\n' "  基底目錄要寫絕對路徑，不可以在 /tmp、/var/tmp 底下。" >&2
    exit 2
}

step() {
    STEP=$1
    printf '\n%s\n' "===== $1. $2 ====="
}

fail() {
    printf '%s\n' "錯誤：$*"
    printf '\n%s\n' "第 ${STEP} 步失敗"
    exit 1
}

warn() {
    printf '%s\n' "提醒：$*"
    NWARN=`expr ${NWARN} + 1`
}

# --- 參數 ---
[ $# -eq 1 ] || usage
BASE=`printf '%s\n' "$1" | sed 's,/*$,,'`
case "${BASE}" in
    "")    usage ;;
    *" "*) usage ;;
    /*)    : ;;
    *)     usage ;;
esac
if [ -z "${FCBAIX_TEST_TMP_OK}" ]; then
    case "${BASE}" in
        /tmp|/tmp/*|/var/tmp|/var/tmp/*) usage ;;
    esac
fi
INSTALL_DIR="${BASE}/script"
LOG_DIR="${BASE}/log/fcb"

if [ -z "${FCBAIX_TEST_NOROOT}" ]; then
    _uid=`id -u`
    [ "${_uid}" = "0" ] || { printf '%s\n' "請用 root 執行。" >&2; exit 2; }
fi

SDIR=`dirname "$0"`
SDIR=`cd "${SDIR}" 2>/dev/null && pwd`
printf '%s\n' "來源目錄：${SDIR}"
printf '%s\n' "腳本目錄：${INSTALL_DIR}"
printf '%s\n' "log 目錄：${LOG_DIR}"
printf '%s\n' "取件點　：${PICKUP_DIR}（每次的報告也放一份在這裡）"

# ---------------------------------------------------------------------------
step 1 "三支腳本轉 LF"
AUDIT=`ls "${SDIR}"/fcb_aixV[0-9][0-9][0-9].ksh 2>/dev/null | sort | tail -1`
[ -n "${AUDIT}" ] || fail "${SDIR} 底下找不到 fcb_aixV###.ksh"
AUDIT_NAME=`basename "${AUDIT}"`
[ "${AUDIT_NAME}" = "${EXPECT_AUDIT_NAME}" ] || fail "本目錄最新的稽核腳本是 ${AUDIT_NAME}，這支一鍵程式對應的是 ${EXPECT_AUDIT_NAME}（請用同一批發版的檔）"
for _f in "${AUDIT_NAME}" "${INSTALL_NAME}" "${RUN_NAME}"; do
    [ -f "${SDIR}/${_f}" ] || fail "找不到 ${SDIR}/${_f}"
    tr -d '\015' < "${SDIR}/${_f}" > "${SDIR}/.${_f}.lf.$$" || fail "轉 ${_f} 失敗"
    mv -f "${SDIR}/.${_f}.lf.$$" "${SDIR}/${_f}" || fail "轉 ${_f} 失敗"
    printf '%s\n' "  ${_f} OK"
done

# ---------------------------------------------------------------------------
step 2 "cksum 核對"
_bad=0
for _pair in "${AUDIT_NAME}:${EXPECT_AUDIT_CKSUM}" "${INSTALL_NAME}:${EXPECT_INSTALL_CKSUM}" "${RUN_NAME}:${EXPECT_RUN_CKSUM}"; do
    _f=`printf '%s\n' "${_pair}" | sed 's/:.*//'`
    _want=`printf '%s\n' "${_pair}" | sed 's/^[^:]*://'`
    _got=`cksum < "${SDIR}/${_f}" | awk '{ print $1 " " $2 }'`
    if [ "${_got}" = "${_want}" ]; then
        printf '%s\n' "  ${_f}  ${_got}  一致"
    else
        printf '%s\n' "  ${_f}  ${_got}  預期 ${_want}  不一致"
        _bad=1
    fi
done
[ "${_bad}" = 0 ] || fail "cksum 對不上：檔案不是這一版，或傳輸過程壞掉。請重新取得同一批檔案。"

# ---------------------------------------------------------------------------
step 3 "安裝（複製腳本、建 log 目錄、搬舊目錄、裝排程）"
FCBAIX_INSTALL_DIR="${INSTALL_DIR}" FCBAIX_LOG_DIR="${LOG_DIR}" \
    "${KSH_BIN}" "${SDIR}/${INSTALL_NAME}" || fail "${INSTALL_NAME} 失敗（訊息見上面）"

# ---------------------------------------------------------------------------
step 4 "排程"
_cron=`"${CRONTAB_CMD}" -l 2>/dev/null | grep "${MARK_ID}"`
_n=`printf '%s\n' "${_cron}" | grep -c "${MARK_ID}"`
printf '%s\n' "${_cron}"
[ "${_n}" = "1" ] || fail "crontab 裡本工具的行有 ${_n} 行，應該剛好 1 行"
case "${_cron}" in
    *" ${INSTALL_DIR}/${RUN_NAME} "*) printf '%s\n' "  剛好一行，指向 ${INSTALL_DIR}/${RUN_NAME}" ;;
    *) fail "排程沒有指向 ${INSTALL_DIR}/${RUN_NAME}" ;;
esac

# ---------------------------------------------------------------------------
step 5 "設定檔"
cat "${INSTALL_DIR}/fcb_aix.conf" || fail "讀不到 ${INSTALL_DIR}/fcb_aix.conf"
grep "^LOG_DIR=${LOG_DIR}\$" "${INSTALL_DIR}/fcb_aix.conf" > /dev/null || fail "fcb_aix.conf 的 LOG_DIR 不是 ${LOG_DIR}"

# ---------------------------------------------------------------------------
step 6 "強制實跑一次（FCBAIX_FORCE_RUN=1）"
# log 目錄（V012 起）只留固定檔名的最新版 FCB_latest*，每跑一次整組覆蓋，檔數不會變多；
# 所以不數檔，改看 FCB_latest.info 記的「稽核版報告來源檔名」有沒有換成這一次的。
_lat0=`sed -n 's/^FCB_latest_audit.html=//p' "${LOG_DIR}/FCB_latest.info" 2>/dev/null | head -n 1`
_npick0=`ls "${PICKUP_DIR}"/FCB_*_audit.html 2>/dev/null | wc -l | tr -d ' '`
FCBAIX_FORCE_RUN=1 "${KSH_BIN}" "${INSTALL_DIR}/${RUN_NAME}"
_rc=$?
printf '%s\n' "exit=${_rc}"
[ "${_rc}" = 0 ] || fail "包裝程式離開碼 ${_rc}（原因看第 7 步的 log：${LOG_DIR}/fcbaudit_monthly.log）"

# ---------------------------------------------------------------------------
step 7 "結果檢查"
printf '%s\n' "--- ${LOG_DIR}/fcbaudit_monthly.log（最後 15 行）"
tail -n 15 "${LOG_DIR}/fcbaudit_monthly.log" || fail "讀不到 monthly.log"
printf '%s\n' "--- ${INSTALL_DIR}"
ls -l "${INSTALL_DIR}"
printf '%s\n' "--- ${LOG_DIR}（最新版，每次整組覆蓋）"
ls -l "${LOG_DIR}"/FCB_latest* 2>/dev/null
printf '%s\n' "--- ${PICKUP_DIR}（取件點的稽核版報告）"
ls -l "${PICKUP_DIR}"/FCB_*_audit.html 2>/dev/null
_lat1=`sed -n 's/^FCB_latest_audit.html=//p' "${LOG_DIR}/FCB_latest.info" 2>/dev/null | head -n 1`
_npick1=`ls "${PICKUP_DIR}"/FCB_*_audit.html 2>/dev/null | wc -l | tr -d ' '`
[ -f "${LOG_DIR}/FCB_latest_audit.html" ] || fail "log 目錄沒有 FCB_latest_audit.html"
[ -n "${_lat1}" ] && [ "${_lat1}" != "${_lat0}" ] || fail "log 目錄的最新版沒有換成這一次的（FCB_latest.info 記的來源：${_lat1:-空}）"
[ "${_npick1}" -gt "${_npick0}" ] || fail "取件點 ${PICKUP_DIR} 沒有多出這次的稽核版報告"
printf '%s\n' "  取件點多一份稽核版報告；log 目錄最新版已換成 ${_lat1}"
printf '%s\n' "--- 舊目錄 ${OLD_HIST}"
if [ -d "${OLD_HIST}" ]; then
    ls -la "${OLD_HIST}"
    warn "${OLD_HIST} 還在，上面是保留下來的項目（內容不同或子目錄），請自行確認後搬走或刪除"
else
    printf '%s\n' "  已搬空、已移除"
fi

if [ "${NWARN}" = 0 ]; then
    printf '\n%s\n' "全部通過"
else
    printf '\n%s\n' "全部通過（另有 ${NWARN} 項提醒，見上面）"
fi
exit 0
