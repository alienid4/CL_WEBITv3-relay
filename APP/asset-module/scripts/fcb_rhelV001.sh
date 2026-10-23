#!/bin/bash
# =====================================================================
# fcb_rhelV001.sh — Linux（RHEL 8/9/10）系統組態檢核，61 條，唯讀
#
# 來源：上游 RHEL 檢核驅動腳本（SV.202606290800，TWGCB-01-012 / FCB-01-002，共 61 條）。
#   上游是「驅動腳本＋每條一支子腳本（sudo sh 逐支跑）」，子腳本不在本專案；
#   本檔把 61 條改寫成**單一自足、唯讀**的版本，條號／類別／名稱／標準設定值沿用上游輸出，
#   判定邏輯依標準設定值重寫（逐條差異見檔尾「與上游的差異」）。
#
# 保證：
#   1. **不改任何系統設定**：只讀檔、只跑查詢指令（rpm -q、systemctl is-*、ls、awk…）。
#   2. 以收集帳號（非 root）執行即可。要 root 才看得到的項目，向唯讀輔助程式取固定事實：
#        sudo -n /usr/local/libexec/webit3/rebuild-facts config-audit
#      （sudoers 只放行這一個固定子指令，無萬用字元）。拿不到 → 該條標 Error「權限不足」，
#      **不會**標成 Non-Compliant。以 root 直接執行時直接讀。
#   3. 收尾一定有 "Check ended at:" 與 "Check summary:" 兩行。缺任何一個＝中途死掉
#      （匯入端看到本版號而沒有收尾，會判「截斷」拒收）。
#
# 輸出：${FCBRHEL_OUTDIR:-/tmp}/FCB_<主機名>_<IP>.txt（6 欄，分號分隔，與上游同格式）
# =====================================================================
export LC_ALL=C
PATH=/usr/sbin:/usr/bin:/sbin:/bin
SCRIPTV="WEBIT-RHEL-V001 (61 條；上游 SV.202606290800；自製判定，尚未與官方逐條比對)"
HELPER=/usr/local/libexec/webit3/rebuild-facts

HOSTNAME_S=$(hostname 2>/dev/null)
# 第一個非 loopback 的 IPv4（上游寫死特定網段前綴，這裡不綁網段）
myIP=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}$' | grep -v '^127\.' | head -n 1)
allIP=$(hostname -I 2>/dev/null | xargs)
prettyNM=$(grep '^PRETTY_NAME' /etc/os-release 2>/dev/null | cut -f2 -d'"')
KV=$(uname -r)
OUTDIR="${FCBRHEL_OUTDIR:-/tmp}"
OUT="$OUTDIR/FCB_${HOSTNAME_S}_${myIP}.txt"
N_T=0; N_C=0; N_N=0; N_A=0; N_E=0

# ---------------------------------------------------------------------
# root 才看得到的事實：root 直接讀；否則問唯讀輔助程式；都不行＝權限不足
# ---------------------------------------------------------------------
FACTSRC=none
HF=""
if [ "$(id -u)" -eq 0 ]; then
    FACTSRC=root
elif HF=$(sudo -n "$HELPER" config-audit 2>/dev/null) && printf '%s\n' "$HF" | grep -q '^@@C '; then
    FACTSRC=helper
fi

_rd() { [ -L "$1" ] && return 97; [ -f "$1" ] || return 98; head -c 65536 "$1"; }
_shadow_empty() { [ -L /etc/shadow ] && return 97; awk -F: '$2==""{print $1}' /etc/shadow; }
_rules_d() {
    [ -d /etc/audit/rules.d ] || return 98
    for f in /etc/audit/rules.d/*.rules; do
        [ -f "$f" ] && [ ! -L "$f" ] && head -c 65536 "$f"
    done
    return 0
}

# fact <key>：印出內容；回傳 0＝取得、97/98＝符號連結／不存在、99＝權限不足、其他＝指令離開碼
fact() {
    case "$FACTSRC" in
    root)
        case "$1" in
            shadow_empty)   _shadow_empty ;;
            ls_auditd_conf) ls -ld /etc/audit/auditd.conf ;;
            ls_audit_log)   ls -ld /var/log/audit/audit.log ;;
            ls_audit_dir)   ls -ld /var/log/audit ;;
            ls_audit_rules) ls -ld /etc/audit/rules.d/audit.rules ;;
            auditd_conf)    _rd /etc/audit/auditd.conf ;;
            auditctl_l)     auditctl -l ;;
            rules_d)        _rules_d ;;
            sudoers_stig)   _rd /etc/sudoers.d/99-stig-password-policy ;;
            sshd_t)         sshd -T ;;
            sshd_config)    _rd /etc/ssh/sshd_config ;;
            *) return 96 ;;
        esac 2>/dev/null ;;
    helper)
        # 輔助程式輸出：@@C <key> <rc> ／ 每行前綴「| 」的內容 ／ @@E <key>（純文字，不用 base64）
        _rc=$(printf '%s\n' "$HF" | awk -v k="$1" '$1=="@@C" && $2==k {print $3; exit}')
        [ -n "$_rc" ] || return 96
        printf '%s\n' "$HF" | awk -v k="$1" '
            $1=="@@C" && $2==k {on=1; next}
            on && $1=="@@E" && $2==k {exit}
            on {sub(/^\| ?/, ""); print}'
        return "$_rc" ;;
    *) return 99 ;;
    esac
}

NOPERM="權限不足：需 root（這台尚未重新納管、沒有唯讀輔助程式 config-audit），本條未檢查"

# ---------------------------------------------------------------------
# 共用小工具
# ---------------------------------------------------------------------
emit() {  # id result 類別 名稱 標準設定值 目前值
    _cur=$(printf '%s' "$6" | tr '\r\n\t' '   ' | sed 's/  */ /g; s/ *$//')
    printf '%s;%s;%s;%s;%s;%s\n' "$1" "$2" "$3" "$4" "$5" "$_cur" >> "$OUT"
    N_T=$((N_T+1))
    case "$2" in
        Compliant) N_C=$((N_C+1)) ;;
        Non-Compliant) N_N=$((N_N+1)) ;;
        Not-Applicable) N_A=$((N_A+1)) ;;
        *) N_E=$((N_E+1)) ;;
    esac
}

is_int() { case "$1" in ''|-|*[!0-9-]*) return 1 ;; esac; return 0; }

# cfgval <key> <檔案...>：非註解行 `key = v` 或 `key v` 的最後一個值（後面的檔覆蓋前面的）
cfgval() {
    _k=$1; shift
    awk -v k="$_k" '
        /^[[:space:]]*#/ {next}
        { line=$0; sub(/^[[:space:]]+/, "", line); n=split(line, a, /[[:space:]=]+/)
          if (n>=2 && tolower(a[1])==tolower(k)) v=a[2] }
        END { if (v!="") print v }' "$@" 2>/dev/null
}

# sym2oct <-rwxr-x---> ：權限字串轉成數值（不含 setuid/sticky 位）
sym2oct() {
    _p=$1; _o=0; _i=1
    for _w in 256 128 64 32 16 8 4 2 1; do
        case "${_p:$_i:1}" in r|w|x|s|t) _o=$((_o+_w)) ;; esac
        _i=$((_i+1))
    done
    echo "$_o"
}

# perm_le <ls -ld 輸出> <八進位上限>：權限沒有超出上限
perm_le() {
    _m=$(sym2oct "$(printf '%s' "$1" | awk '{print $1}')")
    [ $(( _m & ~(8#$2) )) -eq 0 ]
}
owner_is_root() { [ "$(printf '%s' "$1" | awk '{print $3":"$4}')" = "root:root" ]; }

PWQ_FILES="/etc/security/pwquality.conf"
for _f in /etc/security/pwquality.conf.d/*.conf; do [ -f "$_f" ] && PWQ_FILES="$PWQ_FILES $_f"; done

# ---------------------------------------------------------------------
# 表頭
# ---------------------------------------------------------------------
mkdir -p "$OUTDIR" 2>/dev/null
: > "$OUT" || { echo "無法寫入 $OUT" >&2; exit 1; }
{
echo "系統組態檢查"
echo "平台: Linux"
printf '%s\n' "主機名: $HOSTNAME_S"
printf '%s\n' "IP地址: $allIP"
printf '%s\n' "作業系統版本: $prettyNM"
printf '%s\n' "核心版本: $KV"
printf '%s\n' "腳本版本: $SCRIPTV"
echo "檢核基準: TWGCB-01-012（RHEL 9）＋FCB-01-002（金融機構 RHEL 組態基準）共 61 條"
printf '%s\n' "Check stated at: $(date '+%Y-%m-%d %H:%M:%S')"
printf '%s\n' "Check runas: $(id -un)(uid=$(id -u)) 特權事實來源=$FACTSRC"
case "$prettyNM" in
    "Red Hat Enterprise Linux 9."*|"Red Hat Enterprise Linux 10."*) : ;;
    "Red Hat Enterprise Linux 8."*)
        echo "Check warning: RHEL 8 主機以 TWGCB-01-012（RHEL 9）條目檢核；上游對 RHEL 8 使用 TWGCB-01-008 條目，條號可能不同" ;;
    *)
        echo "Check warning: 本機不是 RHEL 8/9/10（$prettyNM），上游腳本在此會直接結束；本版照常檢查，結果僅供參考" ;;
esac
echo "TWGCB-ID;檢查結果;類別;原則設定名稱;標準設定值;目前值"
} >> "$OUT"

C1="系統設定與維護"; C2="日誌與稽核"; C3="帳號與存取控制"; C4="SSH設定"; C5="系統服務"

# =====================================================================
# TWGCB-01-012
# =====================================================================

# 0066 系統命令檔案權限
id=TWGCB-01-012-0066; std="檔案之權限為755或更低權限於如右目錄 (/bin /sbin /usr/bin /usr/sbin /usr/local/bin /usr/local/sbin )"
bad=$(find -H /bin /sbin /usr/bin /usr/sbin /usr/local/bin /usr/local/sbin -xdev -type f -perm /022 2>/dev/null | head -n 5 | xargs)
if [ -z "$bad" ]; then emit $id Compliant "$C1" "系統命令檔案權限" "$std" "無異常"
else emit $id Non-Compliant "$C1" "系統命令檔案權限" "$std" "群組或其他人可寫: $bad"; fi

# 0072 帳號不使用空白密碼（只拿「空白密碼的帳號名單」，不碰密碼雜湊）
id=TWGCB-01-012-0072; std="檢查有無使用空白密碼之帳號於 /etc/shadow 檔案"
v=$(fact shadow_empty); rc=$?
if [ $rc -eq 99 ]; then emit $id Error "$C1" "帳號不使用空白密碼" "$std" "$NOPERM"
elif [ $rc -ne 0 ]; then emit $id Error "$C1" "帳號不使用空白密碼" "$std" "讀不到 /etc/shadow（rc=$rc）"
elif [ -z "$v" ]; then emit $id Compliant "$C1" "帳號不使用空白密碼" "$std" "無異常"
else emit $id Non-Compliant "$C1" "帳號不使用空白密碼" "$std" "空白密碼帳號: $(printf '%s' "$v" | xargs)"; fi

# 0086 /etc/passwd 的群組都存在於 /etc/group
id=TWGCB-01-012-0086; std="這項原則設定決定是否檢查 /etc/passwd 檔案帳號的群組，是否都存在於/etc/group檔案中，若不存在於/etc/group檔案中，代表群組權限管理不恰當，將可能對系統安全構成威脅"
bad=$(awk -F: 'NR==FNR {g[$3]=1; next} !($4 in g) {print $1"(gid="$4")"}' /etc/group /etc/passwd 2>/dev/null | xargs)
if [ ! -r /etc/passwd ] || [ ! -r /etc/group ]; then emit $id Error "$C1" "檢查/etc/passwd檔案設定的群組" "$std" "讀不到 /etc/passwd 或 /etc/group"
elif [ -z "$bad" ]; then emit $id Compliant "$C1" "檢查/etc/passwd檔案設定的群組" "$std" "無異常"
else emit $id Non-Compliant "$C1" "檢查/etc/passwd檔案設定的群組" "$std" "群組不存在: $bad"; fi

# 0087 唯一的 UID
id=TWGCB-01-012-0087; std="為每個使用者帳號設定唯一的UID，以提供適當的存取防護"
dup=$(cut -d: -f3 /etc/passwd 2>/dev/null | sort | uniq -d | xargs)
if [ ! -r /etc/passwd ]; then emit $id Error "$C1" "唯一的UID" "$std" "讀不到 /etc/passwd"
elif [ -z "$dup" ]; then emit $id Compliant "$C1" "唯一的UID" "$std" "無異常"
else emit $id Non-Compliant "$C1" "唯一的UID" "$std" "重複 UID: $dup"; fi

# 0088 唯一的 GID
id=TWGCB-01-012-0088; std="為每個群組設定唯一的GID，以提供適當的存取防護"
dup=$(cut -d: -f3 /etc/group 2>/dev/null | sort | uniq -d | xargs)
if [ ! -r /etc/group ]; then emit $id Error "$C1" "唯一的GID" "$std" "讀不到 /etc/group"
elif [ -z "$dup" ]; then emit $id Compliant "$C1" "唯一的GID" "$std" "無異常"
else emit $id Non-Compliant "$C1" "唯一的GID" "$std" "重複 GID: $dup"; fi

# 0089 唯一的使用者帳號名稱
id=TWGCB-01-012-0089; std="檢查 /etc/passwd 確認每個使用者帳號名稱皆不相同"
dup=$(cut -d: -f1 /etc/passwd 2>/dev/null | sort | uniq -d | xargs)
if [ ! -r /etc/passwd ]; then emit $id Error "$C1" "唯一的使用者帳號名稱" "$std" "讀不到 /etc/passwd"
elif [ -z "$dup" ]; then emit $id Compliant "$C1" "唯一的使用者帳號名稱" "$std" "無重覆帳號"
else emit $id Non-Compliant "$C1" "唯一的使用者帳號名稱" "$std" "重複帳號: $dup"; fi

# 0132 auditd 套件
id=TWGCB-01-012-0132; std="檢查是否安裝auditd套件, ( #dnf install audit audit-libs ) "
if ! command -v rpm >/dev/null 2>&1; then emit $id Error "$C2" "auditd套件" "$std" "沒有 rpm 指令，無法判斷"
elif rpm -q audit >/dev/null 2>&1; then emit $id Compliant "$C2" "auditd套件" "$std" "已安裝 auditd套件"
else emit $id Non-Compliant "$C2" "auditd套件" "$std" "未安裝 audit 套件"; fi

# 0133 auditd 服務
id=TWGCB-01-012-0133; std="檢查是否啟用auditd服務, ( #systemctl --now enable auditd ) "
en=$(systemctl is-enabled auditd 2>/dev/null); ac=$(systemctl is-active auditd 2>/dev/null)
if [ "$en" = "enabled" ] && [ "$ac" = "active" ]; then emit $id Compliant "$C2" "auditd服務" "$std" "auditd.service is enabled and running."
else emit $id Non-Compliant "$C2" "auditd服務" "$std" "auditd.service is-enabled=${en:-unknown} is-active=${ac:-unknown}"; fi

# 0136 稽核處理失敗時通知系統管理者
id=TWGCB-01-012-0136; std="檢查稽核處理失敗時是否通知系統管理者, ( /etc/aliases 是否有 [postmaster: root] )"
if grep -Eq '^[[:space:]]*postmaster:[[:space:]]*root[[:space:]]*$' /etc/aliases 2>/dev/null; then
    emit $id Compliant "$C2" "稽核處理失敗時通知系統管理者" "$std" "The setup is correct."
elif [ -r /etc/aliases ]; then emit $id Non-Compliant "$C2" "稽核處理失敗時通知系統管理者" "$std" "/etc/aliases 沒有 postmaster: root"
else emit $id Error "$C2" "稽核處理失敗時通知系統管理者" "$std" "讀不到 /etc/aliases"; fi

# 0137~0142 稽核檔案／目錄的擁有者與權限（root 才看得到）
# chk_ls <id> <類別> <名稱> <標準> <fact key> <owner|perm> [上限]
chk_ls() {
    _v=$(fact "$5"); _rc=$?
    if [ $_rc -eq 99 ]; then emit "$1" Error "$2" "$3" "$4" "$NOPERM"; return; fi
    if [ $_rc -ne 0 ] || [ -z "$_v" ]; then emit "$1" Error "$2" "$3" "$4" "查不到（不存在或無法讀取，rc=$_rc）"; return; fi
    if [ "$6" = owner ]; then
        if owner_is_root "$_v"; then emit "$1" Compliant "$2" "$3" "$4" "$_v"
        else emit "$1" Non-Compliant "$2" "$3" "$4" "$_v"; fi
    else
        if perm_le "$_v" "$7"; then emit "$1" Compliant "$2" "$3" "$4" "無異常: ($(printf '%s' "$_v" | awk '{print $1}') $7 以內)"
        else emit "$1" Non-Compliant "$2" "$3" "$4" "超出 $7: $_v"; fi
    fi
}
chk_ls TWGCB-01-012-0137 "$C2" "稽核日誌檔案所有權" "避免因未經授權存取而洩露稽核記錄，讓攻擊者取得系統與組態設定等機敏資訊，( /etc/audit/auditd.conf 稽核日誌檔案擁有者與群組 root:root )" ls_auditd_conf owner
chk_ls TWGCB-01-012-0138 "$C2" "稽核日誌檔案權限" "避免因未經授權存取而洩露稽核記錄，讓攻擊者取得系統與組態設定等機敏資訊，( /var/log/audit/audit.log 稽核日誌檔案權限為 600 或更低 )" ls_audit_log perm 600
chk_ls TWGCB-01-012-0139 "$C2" "稽核日誌目錄所有權" "設定稽核日誌目錄所有權，避免因未經授權存取而洩露稽核紀錄，遭攻擊者取得系統與組態設定等機敏資訊，( /var/log/audit 目錄所有權擁有者與群組 root:root ) " ls_audit_dir owner
# 條號照上游輸出原樣保留（上游此條印的是 01-008），避免與既有報告對不上
chk_ls TWGCB-01-008-0140 "$C2" "稽核日誌目錄權限" "確保 /var/log/audit 目錄權限安全，建議 700 或更低權限" ls_audit_dir perm 700
chk_ls TWGCB-01-012-0141 "$C2" "稽核規則檔案權限" "避免未經授權人員修改稽核規則，導致遺失重要稽核紀錄，( /etc/audit/rules.d/audit.rules 稽核規則檔案權限 600 或更低權限 ) " ls_audit_rules perm 600
chk_ls TWGCB-01-012-0142 "$C2" "稽核設定檔案權限" "避免未經授權人員修改稽核設定，導致遺失重要稽核紀錄，( /etc/audit/auditd.conf 稽核設定檔案權限 640 或更低權限 )" ls_auditd_conf perm 640

# 0144 稽核工具所有權（不存在的工具略過並註明）
id=TWGCB-01-012-0144; std="設定稽核工具所有權，避免稽核紀錄遭未經授權修改共9個檔案，( /sbin/auditctl, /sbin/aureport, /sbin/ausearch, /sbin/autrace, /sbin/auditd, /sbin/audisp-remote, /sbin/audisp-syslog, /sbin/augenrules, /sbin/rsyslogd 稽核工具所有權 root:root )"
bad=""; miss=""
for t in auditctl aureport ausearch autrace auditd audisp-remote audisp-syslog augenrules rsyslogd; do
    if [ -e "/sbin/$t" ]; then
        o=$(stat -c '%U:%G' "/sbin/$t" 2>/dev/null)
        [ "$o" = "root:root" ] || bad="$bad /sbin/$t($o)"
    else miss="$miss $t"; fi
done
if [ -n "$bad" ]; then emit $id Non-Compliant "$C2" "稽核工具所有權" "$std" "擁有者不是 root:root:$bad"
else emit $id Compliant "$C2" "稽核工具所有權" "$std" "無異常, 所有文件的擁有者與群組均為 (root:root)。${miss:+（不存在:$miss）}"; fi

# 0149／0156／0171 稽核規則（執行中規則＋rules.d）
AR=$(fact auditctl_l); ar_rc=$?
RD=$(fact rules_d); rd_rc=$?
RULES=$(printf '%s\n%s\n' "$AR" "$RD")
# has_watch <路徑>：有 `-w <路徑>[/] -p <含 w 與 a>` 的規則
has_watch() {
    printf '%s\n' "$RULES" | grep -v '^[[:space:]]*#' | \
        grep -E -- "-w[[:space:]]+$1/?[[:space:]].*-p[[:space:]]+[rwxa]*w[rwxa]*" | grep -Eq -- "-p[[:space:]]+[rwx]*a"
}
chk_watch() {  # id 名稱 標準 路徑...
    _id=$1; _nm=$2; _st=$3; shift 3
    if [ $ar_rc -eq 99 ] && [ $rd_rc -eq 99 ]; then emit "$_id" Error "$C2" "$_nm" "$_st" "$NOPERM"; return; fi
    if [ -z "$(printf '%s' "$RULES" | tr -d '[:space:]')" ]; then emit "$_id" Error "$C2" "$_nm" "$_st" "查不到任何稽核規則（auditctl -l rc=$ar_rc、rules.d rc=$rd_rc）"; return; fi
    _miss=""
    for _p in "$@"; do has_watch "$_p" || _miss="$_miss $_p"; done
    if [ -z "$_miss" ]; then emit "$_id" Compliant "$C2" "$_nm" "$_st" "無異常: (-w$(printf ' %s' "$@") -p wa)"
    else emit "$_id" Non-Compliant "$C2" "$_nm" "$_st" "缺少監看:$_miss"; fi
}
chk_watch TWGCB-01-012-0149 "記錄變更登入與登出資訊事件" "這項原則設定決定是否記錄變更登入與登出資訊之事件，請查看政府組態基準 Red Hat Enterprise Linux 9 TWGCB-01-012-0149 的設定和說明方法" /var/log/lastlog /var/run/faillock
chk_watch TWGCB-01-012-0156 "記錄變更使用者或群組資訊事件" "這項原則設定決定是否記錄變更使用者或群組資訊之事件，請查看政府組態基準 Red Hat Enterprise Linux 9 TWGCB-01-012-0156 的設定和說明方法" /etc/group /etc/passwd /etc/gshadow /etc/shadow /etc/security/opasswd
chk_watch TWGCB-01-012-0171 "記錄Pam_Faillock日誌檔案" "這項原則設定決定是否記錄Pam_Faillock日誌檔案" /var/log/faillock

# 0174 rsyslog 套件
id=TWGCB-01-012-0174; std="這項原則設定決定是否安裝rsyslog套件"
v=$(rpm -q rsyslog 2>/dev/null)
if [ $? -eq 0 ]; then emit $id Compliant "$C2" "rsyslog套件" "$std" "無異常: ($v)"
elif command -v rpm >/dev/null 2>&1; then emit $id Non-Compliant "$C2" "rsyslog套件" "$std" "未安裝 rsyslog"
else emit $id Error "$C2" "rsyslog套件" "$std" "沒有 rpm 指令，無法判斷"; fi

# 0175 rsyslog 服務
id=TWGCB-01-012-0175; std="這項原則設定決定是否啟用rsyslog服務"
en=$(systemctl is-enabled rsyslog 2>/dev/null)
if [ "$en" = "enabled" ]; then emit $id Compliant "$C2" "rsyslog服務" "$std" "無異常: (rsyslog.service $en)"
else emit $id Non-Compliant "$C2" "rsyslog服務" "$std" "rsyslog.service is-enabled=${en:-unknown}"; fi

# 0177 rsyslog 記錄認證、授權及系統服務資訊（auth／authpriv／daemon 三類都要有規則）
id=TWGCB-01-012-0177; std="記錄認證、授權及系統服務資訊"
RS=$(cat /etc/rsyslog.conf /etc/rsyslog.d/*.conf 2>/dev/null | grep -v '^[[:space:]]*#')
miss=""
for fac in auth authpriv daemon; do
    printf '%s\n' "$RS" | grep -Eq "(^|[;,[:space:]])$fac\.[^[:space:]]*[[:space:]]+[-/]" || miss="$miss $fac"
done
if [ -z "$RS" ]; then emit $id Error "$C2" "設定rsyslog日誌記錄規則" "$std" "讀不到 rsyslog 設定"
elif [ -z "$miss" ]; then emit $id Compliant "$C2" "設定rsyslog日誌記錄規則" "$std" "設定正確"
else emit $id Non-Compliant "$C2" "設定rsyslog日誌記錄規則" "$std" "缺少記錄規則:$miss"; fi

# 0178／0179 /var/log/messages 與 /var/log 所有權
for pair in "TWGCB-01-012-0178|/var/log/messages|/var/log/messages檔案所有權" "TWGCB-01-012-0179|/var/log|/var/log目錄所有權"; do
    id=${pair%%|*}; rest=${pair#*|}; p=${rest%%|*}; nm=${rest#*|}
    std="這項原則設定決定是否設定${p}$( [ "$p" = /var/log ] && echo 目錄 || echo 檔案)所有權"
    v=$(ls -ld "$p" 2>/dev/null)
    if [ -z "$v" ]; then emit $id Error "$C2" "$nm" "$std" "查不到 $p"
    elif owner_is_root "$v"; then emit $id Compliant "$C2" "$nm" "$std" "無異常: ($v)"
    else emit $id Non-Compliant "$C2" "$nm" "$std" "$v"; fi
done

# 0206~0217 密碼品質（/etc/security/pwquality.conf 與 pwquality.conf.d）
# chk_pwq <id> <名稱> <標準> <key> <最小> <最大>（空字串＝不設該邊界）
chk_pwq() {
    # shellcheck disable=SC2086
    _v=$(cfgval "$4" $PWQ_FILES)
    if [ -z "$_v" ]; then emit "$1" Non-Compliant "$C3" "$2" "$3" "未設定 $4"; return; fi
    if ! is_int "$_v"; then emit "$1" Non-Compliant "$C3" "$2" "$3" "值不是整數: ( $4 = $_v)"; return; fi
    if { [ -z "$5" ] || [ "$_v" -ge "$5" ]; } && { [ -z "$6" ] || [ "$_v" -le "$6" ]; }; then
        emit "$1" Compliant "$C3" "$2" "$3" "無異常: ( $4 = $_v)"
    else emit "$1" Non-Compliant "$C3" "$2" "$3" "不符: ( $4 = $_v)"; fi
}
chk_pwq TWGCB-01-012-0206 "可設定密碼次數" "這項原則設定決定重新設定密碼時，若密碼強度未達到系統要求，可以連續輸入密碼之次數，( 3以下，但須大於0 )" retry 1 3
# 上游標準文字寫 12，但上游實際輸出 minlen = 8 判 Compliant——門檻沿用上游實際行為（8）
chk_pwq TWGCB-01-012-0208 "密碼最小長度" "這項原則設定決定使用者帳號之密碼可包含的最少字元數，( 8個字元以上；政府基準建議 12 )" minlen 8 ""
chk_pwq TWGCB-01-012-0210 "密碼必須至少包含數字個數" "這項原則設定決定使用者帳號之密碼至少包含幾個數字，( 1個以上 )" dcredit "" -1
chk_pwq TWGCB-01-012-0211 "密碼必須至少包含大寫字母個數" "這項原則設定決定使用者帳號之密碼至少包含幾個大寫字母，( 1個以上 )" ucredit "" -1
chk_pwq TWGCB-01-012-0212 "密碼必須至少包含小寫字母個數" "這項原則設定決定使用者帳號之密碼至少包含幾個小寫字母，( 1個以上 )" lcredit "" -1
chk_pwq TWGCB-01-012-0213 "密碼必須至少包含特殊字元個數" "這項原則設定決定使用者帳號之密碼至少包含幾個特殊字元，( 1個以上 )" ocredit "" -1
chk_pwq TWGCB-01-012-0214 "新密碼與舊密碼最少相異字元數" "這項原則設定決定使用者帳號的新密碼內必須有幾個字元與舊密碼不同，( 3個以上 )" difok 3 ""
chk_pwq TWGCB-01-012-0215 "同一類別字元可連續使用個數" "這項原則設定決定使用者帳號之密碼最多允許同一類別字元可連續使用個數，( 4以下，但須大於0 )" maxclassrepeat 1 4
chk_pwq TWGCB-01-012-0216 "相同字元可連續使用個數" "這項原則設定決定使用者帳號之密碼最多允許可連續使用幾個相同字元，( 3以下，但須大於0 )" maxrepeat 1 3
chk_pwq TWGCB-01-012-0217 "必須禁止使用字典檔單字做為密碼" "這項原則設定決定是否禁止使用字典檔單字做為使用者帳號的密碼，( 等於 1 不可為字典檔單字 )" dictcheck 1 1

# 0218 登入失敗鎖定次數（faillock.conf deny，1~5）
id=TWGCB-01-012-0218; std="這項原則設定決定使用者帳號被鎖定的嘗試登入失敗次數，( /etc/security/faillock.conf ) 應設定為 0 以上，5 次以下 (包含 5)"
v=$(cfgval deny /etc/security/faillock.conf)
if [ -z "$v" ]; then emit $id Non-Compliant "$C3" "登入失敗鎖定次數" "$std" "未設定 deny"
elif is_int "$v" && [ "$v" -ge 1 ] && [ "$v" -le 5 ]; then emit $id Compliant "$C3" "登入失敗鎖定次數" "$std" "無異常: ( deny = $v)"
else emit $id Non-Compliant "$C3" "登入失敗鎖定次數" "$std" "不符: ( deny = $v)"; fi

# 0223 PASS_MIN_DAYS ≥ 1
id=TWGCB-01-012-0223; std="設定密碼最短使用期限為1天，以避免使用者重複使用相同密碼"
v=$(cfgval PASS_MIN_DAYS /etc/login.defs)
if is_int "$v" && [ "$v" -ge 1 ]; then emit $id Compliant "$C3" "密碼最短使用期限" "$std" "無異常: (PASS_MIN_DAYS $v)"
else emit $id Non-Compliant "$C3" "密碼最短使用期限" "$std" "不符: (PASS_MIN_DAYS ${v:-未設定})"; fi

# 0228 CREATE_HOME yes
id=TWGCB-01-012-0228; std="為新增使用者帳號預設建立使用者家目錄，供使用者存放環境設定與個人檔案"
v=$(cfgval CREATE_HOME /etc/login.defs)
if [ "$(printf '%s' "$v" | tr 'A-Z' 'a-z')" = "yes" ]; then emit $id Compliant "$C3" "新使用者帳號預設建立使用者家目錄" "$std" "無異常: (CREATE_HOME $v)"
else emit $id Non-Compliant "$C3" "新使用者帳號預設建立使用者家目錄" "$std" "不符: (CREATE_HOME ${v:-未設定})"; fi

# 0266~0272 SSH：優先用 sshd -T（生效值），其次 sshd_config 原文；都拿不到＝權限不足
SSHT=$(fact sshd_t); st_rc=$?
SSHSRC=""
if [ $st_rc -eq 0 ] && [ -n "$SSHT" ]; then SSHSRC=sshd-T
else
    SSHC=$(fact sshd_config); sc_rc=$?
    if [ $sc_rc -ne 0 ] || [ -z "$SSHC" ]; then
        if [ -r /etc/ssh/sshd_config ]; then SSHC=$(head -c 65536 /etc/ssh/sshd_config); sc_rc=0; fi
    fi
    [ $sc_rc -eq 0 ] && [ -n "$SSHC" ] && SSHSRC=sshd_config
fi
# sshv <key>：sshd -T 取該鍵；sshd_config 取第一個出現的值（OpenSSH 以第一個為準）
sshv() {
    if [ "$SSHSRC" = sshd-T ]; then
        printf '%s\n' "$SSHT" | awk -v k="$1" 'tolower($1)==tolower(k) {print $2; exit}'
    else
        printf '%s\n' "$SSHC" | awk -v k="$1" '/^[[:space:]]*#/ {next} /^[[:space:]]*Match[[:space:]]/ {exit}
            { line=$0; sub(/^[[:space:]]+/, "", line); n=split(line, a, /[[:space:]=]+/)
              if (n>=2 && tolower(a[1])==tolower(k)) {print a[2]; exit} }'
    fi
}
ssh_err() {  # 拿不到設定時：權限不足 vs 其他
    if [ $st_rc -eq 99 ]; then echo "$NOPERM"; else echo "讀不到 sshd 設定（sshd -T rc=$st_rc）"; fi
}
id=TWGCB-01-012-0266; nm="SSH MaxAuthTries參數"; std="這項原則設定決定SSH是否設定MaxAuthTries參數；MaxAuthTries用以指定每個連線允許的驗證失敗次數，4以下，但須大於0，搭配 '/etc/security/faillock.conf'的 'deny = 3' 建議設定為2，以降低暴力攻擊風險"
if [ -z "$SSHSRC" ]; then emit $id Error "$C4" "$nm" "$std" "$(ssh_err)"
else v=$(sshv MaxAuthTries)
    if is_int "$v" && [ "$v" -ge 1 ] && [ "$v" -le 4 ]; then emit $id Compliant "$C4" "$nm" "$std" "無異常: (MaxAuthTries $v)"
    else emit $id Non-Compliant "$C4" "$nm" "$std" "不符: (MaxAuthTries ${v:-未設定，預設 6})"; fi
fi
id=TWGCB-01-012-0267; nm="SSH IgnoreRhosts參數"; std="此項原則設定決定SSH是否設定IgnoreRhosts參數，應設定為yes，避免使用.rhosts與.shosts檔案。"
if [ -z "$SSHSRC" ]; then emit $id Error "$C4" "$nm" "$std" "$(ssh_err)"
else v=$(sshv IgnoreRhosts)
    # sshd_config 沒寫時 OpenSSH 預設 yes；sshd -T 一定會列出生效值
    [ -z "$v" ] && [ "$SSHSRC" = sshd_config ] && v="yes(預設)"
    case "$(printf '%s' "$v" | tr 'A-Z' 'a-z')" in yes*) emit $id Compliant "$C4" "$nm" "$std" "無異常: (IgnoreRhosts $v)" ;;
        *) emit $id Non-Compliant "$C4" "$nm" "$std" "不符: (IgnoreRhosts ${v:-未設定})" ;; esac
fi
id=TWGCB-01-012-0270; nm="SSH PermitEmptyPasswords參數"; std="此項原則設定決定SSH是否設定PermitEmptyPasswords參數，禁止空通行碼帳號使用SSH進行遠端存取(應當設定 PermitEmptyPasswords no)，和系統層級的 PAM 'without-nullok' 模組互補。"
if [ -z "$SSHSRC" ]; then emit $id Error "$C4" "$nm" "$std" "$(ssh_err)"
else v=$(sshv PermitEmptyPasswords)
    [ -z "$v" ] && [ "$SSHSRC" = sshd_config ] && v="no(預設)"
    case "$(printf '%s' "$v" | tr 'A-Z' 'a-z')" in no*) emit $id Compliant "$C4" "$nm" "$std" "無異常: (PermitEmptyPasswords $v)" ;;
        *) emit $id Non-Compliant "$C4" "$nm" "$std" "不符: (PermitEmptyPasswords ${v:-未設定})" ;; esac
fi
id=TWGCB-01-012-0272; nm="SSH逾時時間"; std="這項原則設定決定是否設定SSH逾時時間；ClientAliveInterval與ClientAliveCountMax可控制SSH連線逾時；建議 ClientAliveInterval ≤600、ClientAliveCountMax ≥0"
if [ -z "$SSHSRC" ]; then emit $id Error "$C4" "$nm" "$std" "$(ssh_err)"
else iv=$(sshv ClientAliveInterval); cm=$(sshv ClientAliveCountMax)
    [ -z "$cm" ] && cm=3
    if is_int "$iv" && [ "$iv" -ge 1 ] && [ "$iv" -le 600 ] && is_int "$cm" && [ "$cm" -ge 0 ]; then
        emit $id Compliant "$C4" "$nm" "$std" "無異常: (ClientAliveInterval=$iv ClientAliveCountMax=$cm)"
    else emit $id Non-Compliant "$C4" "$nm" "$std" "不符: (ClientAliveInterval=${iv:-未設定} ClientAliveCountMax=$cm)"; fi
fi

# =====================================================================
# FCB-01-002
# =====================================================================

# 0001 一般帳號（UID>=1000、shell 非 nologin/false）的家目錄必須存在
id=FCB-01-002-0001; std="此原則要求 /etc/passwd 中所有一般使用者帳號 (UID>=1000, 且 shell 非 nologin) 的家目錄必須存在。"
bad=""
[ -r /etc/passwd ] && while IFS=: read -r u _ uid _ _ home shell; do
    is_int "$uid" || continue
    [ "$uid" -ge 1000 ] || continue
    case "$shell" in */nologin|*/false) continue ;; esac
    [ -d "$home" ] || bad="$bad $u($home)"
done < /etc/passwd
if [ ! -r /etc/passwd ]; then emit $id Error "$C3" "使用者於/etc/passwd檔案指定的家目錄必須存在" "$std" "讀不到 /etc/passwd"
elif [ -z "$bad" ]; then emit $id Compliant "$C3" "使用者於/etc/passwd檔案指定的家目錄必須存在" "$std" "無異常"
else emit $id Non-Compliant "$C3" "使用者於/etc/passwd檔案指定的家目錄必須存在" "$std" "家目錄不存在:$bad"; fi

# 0002／0003 auditd.conf（root 才看得到）
AC=$(fact auditd_conf); ac_rc=$?
id=FCB-01-002-0002; nm="所有稽核日誌在被消除前需先送到中央管控的日誌伺服器"; std="這項原則設定決定是否將正確的 hostname，定義在 /etc/audit/auditd.conf 裡。"
if [ $ac_rc -eq 99 ]; then emit $id Error "$C2" "$nm" "$std" "$NOPERM"
elif [ $ac_rc -ne 0 ]; then emit $id Error "$C2" "$nm" "$std" "讀不到 /etc/audit/auditd.conf（rc=$ac_rc）"
else v=$(printf '%s\n' "$AC" | awk -F'=' '/^[[:space:]]*#/ {next} {k=$1; gsub(/[[:space:]]/,"",k); if (tolower(k)=="name_format") {v=$2; gsub(/^[[:space:]]+|[[:space:]]+$/,"",v)}} END{print v}')
    if [ "$(printf '%s' "$v" | tr 'A-Z' 'a-z')" = "hostname" ]; then emit $id Compliant "$C2" "$nm" "$std" "無異常: (name_format = $v)"
    else emit $id Non-Compliant "$C2" "$nm" "$std" "不符: (name_format = ${v:-未設定})"; fi
fi
id=FCB-01-002-0003; nm="儲存稽核日誌的空間使用量達75%時，必需有相對應的動作處理"; std="這項原則設定讓稽核日誌空間儲存使用量達到75%時，可產生syslog。應包含這兩行 [space_left = 25%] 及 [space_left_action = SYSLOG]"
if [ $ac_rc -eq 99 ]; then emit $id Error "$C2" "$nm" "$std" "$NOPERM"
elif [ $ac_rc -ne 0 ]; then emit $id Error "$C2" "$nm" "$std" "讀不到 /etc/audit/auditd.conf（rc=$ac_rc）"
else
    sl=$(printf '%s\n' "$AC" | awk -F'=' '/^[[:space:]]*#/ {next} {k=$1; gsub(/[[:space:]]/,"",k); if (k=="space_left") {v=$2; gsub(/[[:space:]]/,"",v)}} END{print v}')
    sa=$(printf '%s\n' "$AC" | awk -F'=' '/^[[:space:]]*#/ {next} {k=$1; gsub(/[[:space:]]/,"",k); if (k=="space_left_action") {v=$2; gsub(/[[:space:]]/,"",v)}} END{print v}')
    if [ "$sl" = "25%" ] && [ "$(printf '%s' "$sa" | tr 'a-z' 'A-Z')" = "SYSLOG" ]; then
        emit $id Compliant "$C2" "$nm" "$std" "無異常: (space_left = $sl, space_left_action = $sa)"
    else emit $id Non-Compliant "$C2" "$nm" "$std" "不符: (space_left = ${sl:-未設定}, space_left_action = ${sa:-未設定})"; fi
fi

# 0004／0005 chrony：port 0、cmdport 0
for pair in "FCB-01-002-0004|port|關閉chrony服務避免變成chrony主機|這項原則設定在避免主機變成chrony主機(TimeSerever)，需設定為 'port 0'" \
            "FCB-01-002-0005|cmdport|關閉chrony網路管理服務|禁用遠端 'chronyc' 管理，僅允許本地，防止未授權的管理指令，需設定為 'cmdport 0'"; do
    IFS='|' read -r id key nm std <<EOF_PAIR
$pair
EOF_PAIR
    if [ ! -f /etc/chrony.conf ]; then emit $id Not-Applicable "$C5" "$nm" "$std" "沒有 /etc/chrony.conf（未使用 chrony）"; continue; fi
    v=$(cfgval "$key" /etc/chrony.conf)
    if [ "$v" = "0" ]; then emit $id Compliant "$C5" "$nm" "$std" "無異常: ($key 0)"
    else emit $id Non-Compliant "$C5" "$nm" "$std" "不符: ($key ${v:-未設定})"; fi
done

# 0006 不能安裝 sendmail
id=FCB-01-002-0006; std="這項原則在設定是否安裝sendmail套件。"
if ! command -v rpm >/dev/null 2>&1; then emit $id Error "$C5" "不能安裝sendmail套件" "$std" "沒有 rpm 指令，無法判斷"
elif rpm -q sendmail >/dev/null 2>&1; then emit $id Non-Compliant "$C5" "不能安裝sendmail套件" "$std" "已安裝 sendmail"
else emit $id Compliant "$C5" "不能安裝sendmail套件" "$std" "無異常: 未安裝 sendmail"; fi

# 0007~0009 modprobe.d 黑名單
MP=$(cat /etc/modprobe.d/*.conf 2>/dev/null | grep -v '^[[:space:]]*#')
mp_has() { printf '%s\n' "$MP" | grep -Eq "^[[:space:]]*$1[[:space:]]*$"; }
id=FCB-01-002-0007; std="禁用 UVC 驅動，將 'uvcvideo' 加入模組黑名單"
if mp_has 'blacklist[[:space:]]+uvcvideo'; then emit $id Compliant "$C1" "內建或外接的攝影機沒有在使用時需覆蓋或停用" "$std" "已設定: (blacklist uvcvideo)"
else emit $id Non-Compliant "$C1" "內建或外接的攝影機沒有在使用時需覆蓋或停用" "$std" "未設定 blacklist uvcvideo"; fi
id=FCB-01-002-0008; std="於 /etc/modprobe.d/blacklist.conf 設定：install firewire-core /bin/false 與 blacklist firewire-core"
a=0; b=0
mp_has 'install[[:space:]]+firewire-core[[:space:]]+/bin/false' && a=1
mp_has 'blacklist[[:space:]]+firewire-core' && b=1
if [ $a -eq 1 ] && [ $b -eq 1 ]; then emit $id Compliant "$C1" "停用 IEEE 1394(FireWire)" "$std" "已設定: (install firewire-core /bin/false) 與 (blacklist firewire-core)"
else emit $id Non-Compliant "$C1" "停用 IEEE 1394(FireWire)" "$std" "未設定完整: install=$a blacklist=$b"; fi
id=FCB-01-002-0009; std="在 /etc/modprobe.d/bluetooth.conf 設定 'install bluetooth /bin/false'"
if mp_has 'install[[:space:]]+bluetooth[[:space:]]+/bin/false'; then emit $id Compliant "$C1" "停用藍牙" "$std" "已設定: (install bluetooth /bin/false)"
else emit $id Non-Compliant "$C1" "停用藍牙" "$std" "未設定 install bluetooth /bin/false"; fi

# 0015 ctrl-alt-del.target masked
id=FCB-01-002-0015; std="組合鍵的功能應關閉，避免誤按而造成系統重開機。SYSTEMD status 應是 Loaded: masked (ReasonS: Unit ctrl-alt-del.target is masked.)"
v=$(systemctl is-enabled ctrl-alt-del.target 2>/dev/null)
if [ "$v" = "masked" ]; then emit $id Compliant "$C1" "關閉Ctrl-Alt-Delete組合鍵功能" "$std" "無異常: (mask_status=masked)"
else emit $id Non-Compliant "$C1" "關閉Ctrl-Alt-Delete組合鍵功能" "$std" "不符: (is-enabled=${v:-unknown})"; fi

# 0016 GNOME Ctrl+Alt+Delete
id=FCB-01-002-0016; std="檢查 /etc/dconf/db/local.d/00-disable-CAD 是否設定 logout=''"
if grep -Eq "^[[:space:]]*logout[[:space:]]*=[[:space:]]*''[[:space:]]*$" /etc/dconf/db/local.d/00-disable-CAD 2>/dev/null; then
    emit $id Compliant "$C1" "停用 Ctrl+Alt+Delete（GUI）" "$std" "已設定: (logout='')"
else emit $id Non-Compliant "$C1" "停用 Ctrl+Alt+Delete（GUI）" "$std" "未設定（檔案不存在或沒有 logout=''）"; fi

# 0017 CtrlAltDelBurstAction=none
id=FCB-01-002-0017; std="Ctrl-Alt-Delete組合鍵的功能應關閉，避免誤按而造成系統重開機。檔案 ( /etc/systemd/system.conf ) 應該有這行 ( CtrlAltDelBurstAction=none ) "
if grep -Eq '^[[:space:]]*CtrlAltDelBurstAction[[:space:]]*=[[:space:]]*none[[:space:]]*$' /etc/systemd/system.conf 2>/dev/null; then
    emit $id Compliant "$C1" "關閉 systemd的 Ctrl-Alt-Delete組合鍵功能" "$std" "無異常: (CtrlAltDelBurstAction=none)"
else emit $id Non-Compliant "$C1" "關閉 systemd的 Ctrl-Alt-Delete組合鍵功能" "$std" "未設定 CtrlAltDelBurstAction=none"; fi

# 0018 Postfix 不應安裝或存在 /etc/postfix
id=FCB-01-002-0018; std="Postfix 不應安裝或存在 /etc/postfix"
inst=0; command -v rpm >/dev/null 2>&1 && rpm -q postfix >/dev/null 2>&1 && inst=1
if ! command -v rpm >/dev/null 2>&1; then emit $id Error "$C1" "檢查 Postfix 是否存在" "$std" "沒有 rpm 指令，無法判斷"
elif [ $inst -eq 0 ] && [ ! -e /etc/postfix ]; then emit $id Compliant "$C1" "檢查 Postfix 是否存在" "$std" "檢查結果: Postfix 未安裝且 /etc/postfix 不存在"
else emit $id Non-Compliant "$C1" "檢查 Postfix 是否存在" "$std" "postfix 已安裝=$inst、/etc/postfix 存在=$([ -e /etc/postfix ] && echo 1 || echo 0)"; fi

# 0019 sudo 使用呼叫者密碼：/etc/sudoers.d/99-stig-password-policy 只有三行 Defaults（root 才看得到）
id=FCB-01-002-0019; nm="sudo 使用呼叫者密碼（禁用 rootpw/targetpw/runaspw）"; std="檢查 /etc/sudoers.d/99-stig-password-policy 僅含三行 Defaults"
v=$(fact sudoers_stig); rc=$?
if [ $rc -eq 99 ]; then emit $id Error "$C1" "$nm" "$std" "$NOPERM"
elif [ $rc -eq 98 ]; then emit $id Non-Compliant "$C1" "$nm" "$std" "檔案不存在"
elif [ $rc -ne 0 ]; then emit $id Error "$C1" "$nm" "$std" "讀不到檔案（rc=$rc）"
else
    body=$(printf '%s\n' "$v" | grep -v '^[[:space:]]*#' | grep -v '^[[:space:]]*$')
    n=$(printf '%s\n' "$body" | grep -c .)
    ok=1
    for d in targetpw rootpw runaspw; do printf '%s\n' "$body" | grep -Eq "^[[:space:]]*Defaults[[:space:]]+!$d[[:space:]]*$" || ok=0; done
    if [ $ok -eq 1 ] && [ "$n" -eq 3 ]; then emit $id Compliant "$C1" "$nm" "$std" "已設定三行 Defaults"
    else emit $id Non-Compliant "$C1" "$nm" "$std" "不符: 非註解行 $n 行、三項 Defaults 齊全=$ok"; fi
fi

# 0020 GNOME Login Banner
id=FCB-01-002-0020; std="檢查 /etc/dconf/db/local.d/01-banner-message 的 [org/gnome/login-screen] 與 banner-message-enable=true"
f=/etc/dconf/db/local.d/01-banner-message
if grep -q '^\[org/gnome/login-screen\]' "$f" 2>/dev/null && grep -Eq '^[[:space:]]*banner-message-enable[[:space:]]*=[[:space:]]*true' "$f" 2>/dev/null; then
    emit $id Compliant "$C1" "啟用 GNOME Login Banner" "$std" "已設定：banner 啟用（banner-message-enable=true）"
else emit $id Non-Compliant "$C1" "啟用 GNOME Login Banner" "$std" "未設定（檔案不存在或缺少設定）"; fi

# 0021 authselect without-nullok
id=FCB-01-002-0021; std="執行 authselect current 應含 - without-nullok"
if ! command -v authselect >/dev/null 2>&1; then emit $id Error "$C3" "authselect 必須啟用 without-nullok" "$std" "沒有 authselect 指令"
else v=$(authselect current 2>/dev/null)
    if printf '%s\n' "$v" | grep -q 'without-nullok'; then emit $id Compliant "$C3" "authselect 必須啟用 without-nullok" "$std" "無異常: (- without-nullok)"
    elif [ -z "$v" ]; then emit $id Error "$C3" "authselect 必須啟用 without-nullok" "$std" "authselect current 沒有輸出"
    else emit $id Non-Compliant "$C3" "authselect 必須啟用 without-nullok" "$std" "未啟用 without-nullok"; fi
fi

# 0022 chronyd 以 chrony 帳號執行
id=FCB-01-002-0022; std="chronyd 應以 chrony 帳號運行，而非 root 或其他帳號"
v=$(ps -C chronyd -o user= 2>/dev/null | head -n 1 | xargs)
if [ -z "$v" ]; then emit $id Not-Applicable "$C2" "確認chronyd沒有額外的權限" "$std" "chronyd 未執行"
elif [ "$v" = "chrony" ]; then emit $id Compliant "$C2" "確認chronyd沒有額外的權限" "$std" "無異常: chronyd 正以 ($v) 運行"
else emit $id Non-Compliant "$C2" "確認chronyd沒有額外的權限" "$std" "chronyd 以 ($v) 運行"; fi

# 0024 faillock unlock_time = 0
id=FCB-01-002-0024; std="這項原則設定決定使用者帳號被鎖定後的帳號解鎖時間，以降低密碼暴力攻擊之影響，( unlock_time=0 ) 帳號鎖定後永久有效，不會自動解除鎖定。"
v=$(cfgval unlock_time /etc/security/faillock.conf)
if [ "$v" = "0" ]; then emit $id Compliant "$C3" "帳戶鎖定時間" "$std" "無異常: /etc/security/faillock.conf:( unlock_time = $v)"
else emit $id Non-Compliant "$C3" "帳戶鎖定時間" "$std" "不符: ( unlock_time = ${v:-未設定})"; fi

# 0025 密碼歷程記錄 remember ≥ 3（pwhistory.conf 或 PAM 兩處擇一）
id=FCB-01-002-0025; std="藉由限制使用者不能持續重複使用舊密碼，令攻擊者難以猜測密碼以提升安全性，(remember=8 以上)，但依本機構資訊設備管理規定，密碼歷史紀錄至少3次即符合，參考兩個地方2擇1都可以"
r1=$(cfgval remember /etc/security/pwhistory.conf)
r2=$(grep -hE '^[[:space:]]*password.*(pam_pwhistory|pam_unix)\.so' /etc/pam.d/system-auth /etc/pam.d/password-auth 2>/dev/null \
     | grep -oE 'remember=[0-9]+' | cut -d= -f2 | sort -n | tail -n 1)
best=""
for x in $r1 $r2; do is_int "$x" && { [ -z "$best" ] || [ "$x" -gt "$best" ]; } && best=$x; done
if [ -n "$best" ] && [ "$best" -ge 3 ]; then emit $id Compliant "$C3" "強制執行密碼歷程記錄" "$std" "無異常: ( remember = $best)"
else emit $id Non-Compliant "$C3" "強制執行密碼歷程記錄" "$std" "不符: ( remember = ${best:-未設定})"; fi

# 0026 PASS_MAX_DAYS ≤ 90
id=FCB-01-002-0026; std="設定密碼最長使用期限為90天，提高密碼更換頻率，提高攻擊者破解密碼難度"
v=$(cfgval PASS_MAX_DAYS /etc/login.defs)
if is_int "$v" && [ "$v" -ge 1 ] && [ "$v" -le 90 ]; then emit $id Compliant "$C3" "密碼最長使用期限" "$std" "無異常: (PASS_MAX_DAYS $v)"
else emit $id Non-Compliant "$C3" "密碼最長使用期限" "$std" "不符: (PASS_MAX_DAYS ${v:-未設定})"; fi

# ---------------------------------------------------------------------
# 收尾（一定要有，匯入端靠它判斷檔案完整）
# ---------------------------------------------------------------------
printf '%s\n' "Check ended at: $(date '+%Y-%m-%d %H:%M:%S')" >> "$OUT"
printf '%s\n' "Check summary: 合計 $N_T 項；Compliant ${N_C}；Non-Compliant ${N_N}；Not-Applicable ${N_A}；Error $N_E" >> "$OUT"
chmod 644 "$OUT" 2>/dev/null
echo "$OUT"
exit 0

# =====================================================================
# 與上游的差異（逐條判定邏輯是依標準設定值重寫，上游子腳本不在本專案）
#   * 上游是驅動腳本 + 每條一支子腳本（sudo sh 逐支跑）；本版單一檔、非 root 執行，root 項目走唯讀輔助程式。
#   * 上游另產 HTML，本版只產 .txt（匯入用）。
#   * 上游 IP 取特定網段前綴；本版取第一個非 loopback IPv4。
#   * 上游遇到非 RHEL 8/9/10 直接結束；本版照常檢查並寫 Check warning。
#   * 0208 標準文字寫 12 字元，上游實際輸出 minlen=8 判合規 → 門檻用 8，文字註明。
#   * 0218 deny 取 1~5（deny=0 等於不鎖定，不算合規）。
#   * 0025 文字中的機構內部辦法名稱改為中性說法。
#   * 0144 不存在的稽核工具略過並在目前值註明（上游行為未知）。
#   * 0177 要求 auth／authpriv／daemon 三類都有記錄規則（上游判定細節未知）。
#   * 0140 條號照上游輸出原樣保留 TWGCB-01-008-0140。
# =====================================================================
