#!/bin/sh
#=============================================================================
# 收集帳號遷移（單機版）—— 把舊的收集帳號複製成新的統一名稱
#
#   舊：webit3scan（10 字元，只在 Linux 上）
#   新：webit3sc  （8 字元，AIX 的 max_logname 只給 8，兩邊能共用的只有短的那個）
#
# 使用者 2026-09-24 拍板：「還是統一都叫 webit3sc」。
#
#-----------------------------------------------------------------------------
# 【這支為什麼是「複製」而不是「重新納管」】
#
# 舊帳號上的 authorized_keys 與 sudo 白名單**本來就是對的**（納管時佈的）。
# 重寫一套等於第二份定義，兩邊遲早漂走——這個專案已經被這件事咬過很多次。
# 所以這支只做一件事：**把舊帳號已經有的東西，原樣複製給新帳號**。
#
# 附帶好處：腳本裡**不需要嵌任何公鑰或收集器 IP**，因為那些值都從這台機器
# 現有的設定讀出來。這份檔案可以直接進版控、可以寄給任何人看。
#
#-----------------------------------------------------------------------------
# 【安全性：被資安問到時一句話講得清楚】
#
#   預設唯讀：不加 --apply 什麼都不改，只印出現況與「將會做什麼」。
#   純文字  ：沒有 base64、沒有 curl|bash、沒有落地暫存執行檔。
#             你看到的就是會跑的，可以逐行審。
#   有備份  ：動到的檔案先複製一份到 /var/backups/webit3（0700）。
#   可還原  ：新帳號是**加**上去的，舊帳號原封不動；驗到新的收得到，
#             才用 --remove-old 另外一步移除舊的。
#   不刪不問：--remove-old 會先檢查新帳號真的可用，不可用就拒絕執行。
#
#-----------------------------------------------------------------------------
# 【用法】
#
#   sh fcb_collect_account_migrate.sh                 # 只看現況（預設，不改任何東西）
#   sh fcb_collect_account_migrate.sh --apply         # 建立新帳號並複製設定
#   sh fcb_collect_account_migrate.sh --remove-old    # 確認新的可用後，移除舊帳號
#
#   --old NAME / --new NAME 可改帳號名（預設 webit3scan -> webit3sc）
#
# 要 root。AIX 與 Linux（RHEL/Rocky 系列）都可以跑；
# 語法限 POSIX sh，因為 AIX 的 /bin/sh 是 ksh88
# （沒有 [[ ]]、沒有 $(( )) 以外的算術、沒有陣列、沒有 local）。
#=============================================================================

OLD_ACCT="webit3scan"
NEW_ACCT="webit3sc"
MODE="check"
BK="/var/backups/webit3"
STAMP=`date '+%Y%m%d%H%M%S'`

while [ $# -gt 0 ]; do
    case "$1" in
        --apply)      MODE="apply" ;;
        --remove-old) MODE="remove" ;;
        --old)        shift; OLD_ACCT="$1" ;;
        --new)        shift; NEW_ACCT="$1" ;;
        -h|--help)    sed -n '2,45p' "$0"; exit 0 ;;
        *) echo "不認得的參數：$1（--help 看用法）" >&2; exit 2 ;;
    esac
    shift
done

if [ "`id -u`" -ne 0 ]; then
    echo "需要 root（要建帳號、讀 authorized_keys）" >&2
    exit 1
fi

OS=`uname -s`
case "$OS" in
    AIX)   PLAT="aix" ;;
    Linux) PLAT="linux" ;;
    *)     echo "這支只支援 AIX 與 Linux，這台是 $OS——沒跑，不要當成做過了" >&2; exit 1 ;;
esac

echo "================================================================"
echo " 收集帳號遷移（單機版）   $OS   `hostname`   `date '+%Y-%m-%d %H:%M:%S'`"
echo " 舊帳號 $OLD_ACCT  ->  新帳號 $NEW_ACCT"
echo " 模式：$MODE"
[ "$MODE" = "check" ] && echo " ** 現況檢查模式：不會改任何東西。要動手請加 --apply **"
echo "================================================================"

#-----------------------------------------------------------------------------
# 現況：帳號在不在、家目錄在哪、金鑰有沒有
#
# ⚠️「帳號不存在」跟「帳號在但沒有金鑰」是兩件事，要分開講——
#    兩者在畫面上都是「連不上」，但要做的事完全不同。
#-----------------------------------------------------------------------------
acct_exists() {
    if [ "$PLAT" = "aix" ]; then
        lsuser "$1" >/dev/null 2>&1
    else
        id "$1" >/dev/null 2>&1
    fi
}

home_of() {
    if [ "$PLAT" = "aix" ]; then
        lsuser -a home "$1" 2>/dev/null | sed 's/.*home=//' | awk '{print $1}'
    else
        getent passwd "$1" 2>/dev/null | cut -d: -f6
    fi
}

report_one() {
    _a="$1"
    if acct_exists "$_a"; then
        _h=`home_of "$_a"`
        [ -z "$_h" ] && _h="/home/$_a"
        _k="$_h/.ssh/authorized_keys"
        if [ -f "$_k" ]; then
            # ⚠️ 不可以用 ^ssh- 比對：納管佈上去的那一行開頭是
            #    from="...",no-agent-forwarding,... 這些選項，金鑰型別在中間。
            #    用 ^ssh- 會數出 0，而畫面上「0 把金鑰」跟「沒有金鑰」長得一樣——
            #    2026-09-24 在 221 實跑這支時就是這樣被誤導的。
            _n=`grep -c 'ssh-rsa\|ssh-ed25519\|ecdsa-sha2\|sk-ssh\|sk-ecdsa' "$_k" 2>/dev/null`
            echo "  $_a：帳號存在，家目錄 $_h，authorized_keys 有 $_n 把金鑰"
        else
            echo "  $_a：帳號存在，家目錄 $_h，**沒有 authorized_keys**（存在但連不進來）"
        fi
        if [ "$PLAT" = "aix" ]; then
            _lk=`lsuser -a account_locked "$_a" 2>/dev/null | sed 's/.*account_locked=//'`
            if [ "$_lk" = "true" ]; then
                echo "      ⚠ account_locked=true —— AIX 這個旗標會連公鑰一起擋，"
                echo "        症狀是 Permission denied (publickey,password)，看起來像金鑰壞掉。"
            fi
        fi
    else
        echo "  $_a：帳號不存在"
    fi
}

echo ""
echo "[現況]"
report_one "$OLD_ACCT"
report_one "$NEW_ACCT"

OLD_HOME=`home_of "$OLD_ACCT"`
[ -z "$OLD_HOME" ] && OLD_HOME="/home/$OLD_ACCT"
OLD_KEYS="$OLD_HOME/.ssh/authorized_keys"

#-----------------------------------------------------------------------------
# check：只說「會做什麼」，不做
#-----------------------------------------------------------------------------
if [ "$MODE" = "check" ]; then
    echo ""
    echo "[如果加上 --apply，會做這些]"
    if acct_exists "$NEW_ACCT"; then
        echo "  · 新帳號已存在，不會重建"
    else
        if [ "$PLAT" = "aix" ]; then
            echo "  · mkuser shell=/usr/bin/ksh gecos=\"webit3 readonly collector\" $NEW_ACCT"
        else
            echo "  · useradd -m -s /bin/bash -c \"webit3 readonly collector\" $NEW_ACCT"
        fi
    fi
    if [ "$PLAT" = "aix" ]; then
        echo "  · chuser account_locked=false rlogin=true $NEW_ACCT"
        echo "    （AIX 的 account_locked 會連公鑰一起擋，一定要確認是 false）"
    fi
    if [ -f "$OLD_KEYS" ]; then
        echo "  · 把 $OLD_KEYS 複製給新帳號（同一把收集金鑰，同樣的 from= 限制）"
    else
        echo "  · ⚠ 舊帳號沒有 authorized_keys，**沒有東西可以複製**——"
        echo "      這台要走重新納管，不是遷移。"
    fi
    if [ "$PLAT" = "linux" ] && [ -f "/etc/sudoers.d/$OLD_ACCT" ]; then
        echo "  · 複製 /etc/sudoers.d/$OLD_ACCT -> /etc/sudoers.d/$NEW_ACCT（帳號名換掉）"
        echo "    並用 visudo -c 驗過語法才留下"
    fi
    echo ""
    echo "什麼都沒有改。確認上面沒問題後，重跑一次並加 --apply。"
    exit 0
fi

#-----------------------------------------------------------------------------
# apply：建立新帳號並複製設定
#-----------------------------------------------------------------------------
if [ "$MODE" = "apply" ]; then
    if [ ! -f "$OLD_KEYS" ]; then
        echo ""
        echo "[停止] 舊帳號 $OLD_ACCT 沒有 authorized_keys，沒有東西可以複製。" >&2
        echo "       這台請走「重新納管」產生新的收集身分，不要用這支遷移。" >&2
        exit 1
    fi

    mkdir -p "$BK" 2>/dev/null
    chmod 700 "$BK" 2>/dev/null

    echo ""
    echo "[執行]"
    if acct_exists "$NEW_ACCT"; then
        echo "  [=] 帳號 $NEW_ACCT 已存在，不重建"
    else
        if [ "$PLAT" = "aix" ]; then
            # AIX 的帳號名上限：先問清楚，不要等 mkuser 吐一句看不懂的錯
            MAXLOG=`lsattr -El sys0 -a max_logname 2>/dev/null | awk '{print $2}'`
            if [ -n "$MAXLOG" ]; then
                NLEN=`echo "$NEW_ACCT" | awk '{print length($0)}'`
                if [ "$MAXLOG" -le "$NLEN" ]; then
                    echo "  [停止] 此主機 max_logname=$MAXLOG，容不下 $NEW_ACCT（$NLEN 字元）。" >&2
                    echo "         請改用較短的名字，或 chdev -l sys0 -a max_logname=32 後重開機。" >&2
                    exit 1
                fi
            fi
            mkuser shell=/usr/bin/ksh gecos="webit3 readonly collector" "$NEW_ACCT" || exit 1
        else
            useradd -m -s /bin/bash -c "webit3 readonly collector" "$NEW_ACCT" || exit 1
        fi
        echo "  [+] 已建立帳號 $NEW_ACCT"
    fi

    # AIX：account_locked 會連公鑰一起擋（2026-09-22 八台 AIX 全部卡在這裡）。
    # 已存在的帳號也要做——可能是前一版腳本鎖起來的。
    if [ "$PLAT" = "aix" ]; then
        chuser account_locked=false rlogin=true "$NEW_ACCT"
        echo "  [+] 已確認 $NEW_ACCT 未被鎖定、允許遠端登入"
    fi

    NEW_HOME=`home_of "$NEW_ACCT"`
    [ -z "$NEW_HOME" ] && NEW_HOME="/home/$NEW_ACCT"
    NEW_KEYS="$NEW_HOME/.ssh/authorized_keys"

    if [ -f "$NEW_KEYS" ]; then
        cp -p "$NEW_KEYS" "$BK/authorized_keys.$NEW_ACCT.$STAMP" 2>/dev/null
        echo "  [=] 新帳號已有 authorized_keys（已備份到 $BK）"
    fi
    mkdir -p "$NEW_HOME/.ssh"
    # 原樣複製：同一把收集金鑰、同樣的 from= 與 no-*-forwarding 限制。
    # 不重新組一行——重組就是第二份定義，而且會漏掉原本的限制。
    cp "$OLD_KEYS" "$NEW_KEYS"
    chmod 700 "$NEW_HOME/.ssh"
    chmod 600 "$NEW_KEYS"
    if [ "$PLAT" = "aix" ]; then
        NEW_GRP=`lsuser -a pgrp "$NEW_ACCT" 2>/dev/null | sed 's/.*pgrp=//' | awk '{print $1}'`
        [ -z "$NEW_GRP" ] && NEW_GRP="staff"
    else
        NEW_GRP=`id -gn "$NEW_ACCT"`
    fi
    chown -R "$NEW_ACCT:$NEW_GRP" "$NEW_HOME/.ssh"
    echo "  [+] 已複製收集公鑰給 $NEW_ACCT（來源：$OLD_KEYS）"

    # Linux 的唯讀 sudo 白名單：AIX 沒有（也不需要，序號機型走 uname）
    if [ "$PLAT" = "linux" ] && [ -f "/etc/sudoers.d/$OLD_ACCT" ]; then
        cp -p "/etc/sudoers.d/$OLD_ACCT" "$BK/sudoers.$OLD_ACCT.$STAMP"
        sed "s/\\b$OLD_ACCT\\b/$NEW_ACCT/g" "/etc/sudoers.d/$OLD_ACCT" \
            > "/etc/sudoers.d/.$NEW_ACCT.tmp"
        chmod 440 "/etc/sudoers.d/.$NEW_ACCT.tmp"
        # ⚠️ 一定要 visudo -c 驗過才放上去。語法錯的 sudoers 會讓**整台機器**
        # 的 sudo 全部失效，包括管理員自己——那比收不到資料嚴重得多。
        if visudo -c -f "/etc/sudoers.d/.$NEW_ACCT.tmp" >/dev/null 2>&1; then
            mv "/etc/sudoers.d/.$NEW_ACCT.tmp" "/etc/sudoers.d/$NEW_ACCT"
            echo "  [+] 已複製唯讀 sudo 白名單（visudo 驗過）"
        else
            rm -f "/etc/sudoers.d/.$NEW_ACCT.tmp"
            echo "  [!] sudo 白名單複製後 visudo 驗不過，**已丟棄、沒有放上去**。"
            echo "      原檔備份在 $BK/sudoers.$OLD_ACCT.$STAMP，請人工比對。"
        fi
    fi

    echo ""
    echo "[完成] 新帳號已備妥。**舊帳號原封不動。**"
    echo "       下一步：回系統對這台按一次收集，確認四類都收得到；"
    echo "       確認沒問題之後，才用 --remove-old 移除舊帳號。"
    exit 0
fi

#-----------------------------------------------------------------------------
# remove-old：移除舊帳號。**先檢查新的真的可用**
#
# 不檢查就刪的話，萬一新帳號其實沒佈好，這台會完全連不進來，
# 只能派人去機房。不可逆的動作要先確認前提。
#-----------------------------------------------------------------------------
if [ "$MODE" = "remove" ]; then
    echo ""
    echo "[移除前檢查]"
    FAIL=0
    if acct_exists "$NEW_ACCT"; then
        echo "  [ok] 新帳號 $NEW_ACCT 存在"
    else
        echo "  [NG] 新帳號 $NEW_ACCT 不存在" >&2; FAIL=1
    fi
    NEW_HOME=`home_of "$NEW_ACCT"`
    [ -z "$NEW_HOME" ] && NEW_HOME="/home/$NEW_ACCT"
    if [ -f "$NEW_HOME/.ssh/authorized_keys" ]; then
        echo "  [ok] 新帳號有 authorized_keys"
    else
        echo "  [NG] 新帳號沒有 authorized_keys——刪掉舊的這台就連不進來了" >&2; FAIL=1
    fi
    if [ "$PLAT" = "aix" ]; then
        LK=`lsuser -a account_locked "$NEW_ACCT" 2>/dev/null | sed 's/.*account_locked=//'`
        if [ "$LK" = "true" ]; then
            echo "  [NG] 新帳號 account_locked=true —— AIX 這個旗標會連公鑰一起擋" >&2
            FAIL=1
        else
            echo "  [ok] 新帳號未被鎖定"
        fi
    fi
    if [ "$FAIL" -ne 0 ]; then
        echo ""
        echo "[停止] 前提沒過，**沒有移除任何東西**。先把上面的 NG 修好。" >&2
        exit 1
    fi

    # ⚠️ 這裡只檢查得到「本機看起來沒問題」。**真正能不能收，要從收集器那端連過來才算數。**
    echo ""
    echo "  註：以上是在這台機器上看到的狀態。**真正能不能收，要從收集器那端連過來才算數**——"
    echo "      請確認系統對這台按過收集而且收得到，再繼續。"
    echo ""
    printf "確定要移除舊帳號 %s 嗎？(輸入 yes 才會執行) " "$OLD_ACCT"
    read ANS
    if [ "$ANS" != "yes" ]; then
        echo "已取消，沒有移除任何東西。"
        exit 0
    fi

    mkdir -p "$BK" 2>/dev/null
    chmod 700 "$BK" 2>/dev/null
    [ -f "$OLD_KEYS" ] && cp -p "$OLD_KEYS" "$BK/authorized_keys.$OLD_ACCT.$STAMP"
    if [ "$PLAT" = "linux" ] && [ -f "/etc/sudoers.d/$OLD_ACCT" ]; then
        cp -p "/etc/sudoers.d/$OLD_ACCT" "$BK/sudoers.$OLD_ACCT.$STAMP"
        rm -f "/etc/sudoers.d/$OLD_ACCT"
        echo "  [+] 已移除 /etc/sudoers.d/$OLD_ACCT（備份在 $BK）"
    fi
    if [ "$PLAT" = "aix" ]; then
        rmuser -p "$OLD_ACCT"
    else
        userdel -r "$OLD_ACCT"
    fi
    echo "  [+] 已移除帳號 $OLD_ACCT（家目錄一併清掉，否則舊的 authorized_keys 會留著）"
    echo ""
    echo "[完成] 回系統按「重新探測」，這台的狀態會從「可切換」變成「已完成」。"
    exit 0
fi
