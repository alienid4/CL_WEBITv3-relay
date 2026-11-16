"""一支、跑一次的納管腳本 webit3_onboard.sh（Linux／AIX 通用；決策 ONBOARD-FLOW1）。

使用者 2026-10-18：「我沒有密碼」那條路要的是**一支腳本、跑一次**，不是一串指令一行一行貼。
「我有密碼：網頁一鍵納管」也跑**同一份檔案**（同版本、同 sha256）——兩條路不能各有一套會分岔的程式。

結構（全部純文字，可審閱）：
  外層（POSIX sh）：ESXi／vCenter 天條 → 平台白名單（2026-10-19）→ 身分判斷（A1–A4、A8）→ 依 `uname -s` 選 Linux（bash）或
  AIX（ksh）那一段 → 跑完印**一行**結果：「完成」／「請改用 root 再跑一次」／「停止：<原因>」。
  內層：onboard_engine.build_script("linux"/"aix") 產的那兩份（帳號、金鑰、sudoers 或 root 收集金鑰、
  主機端坑 B1–B5），以 heredoc 原文嵌入、從 stdin 交給 bash／ksh——不 base64、不落暫存檔。
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time

FILENAME = "webit3_onboard.sh"
RESULT_DONE = "完成"
RESULT_NEED_ROOT = "請改用 root 再跑一次"
RESULT_STOP = "停止"


def build_bundle(pubkey: str, collector_ip: str, account: str | None = None,
                 comment: str | None = None, root_pubkey: str | None = None,
                 extra_ips: list[str] | None = None, aix_account: str | None = None) -> str:
    """組出 webit3_onboard.sh。內容只跟收集器（公鑰、位址、帳號、備註）有關，**跟目標主機無關**——
    所以同一個收集器產出的檔案每台都一樣，sha256 也一樣，資安審一次就好。"""
    import onboard_engine as eng
    import onboard_hostfix as hf

    lin = eng.build_script("linux", pubkey, collector_ip, account, comment, root_pubkey, extra_ips)
    if aix_account is None:
        # AIX 帳號名受 max_logname 限制（8 字元）；Linux 那邊若還是舊的長名字，AIX 段用預設短名
        aix_account = account if account and len(account) <= eng.AIX_MAX_LOGNAME else eng.DEFAULT_ACCOUNT_AIX
    aix = eng.build_script("aix", pubkey, collector_ip, aix_account, comment, root_pubkey, extra_ips)
    ver = eng._script_version()
    for body, end in ((lin, "WEBIT3_LINUX_BODY"), (aix, "WEBIT3_AIX_BODY")):
        if f"\n{end}\n" in f"\n{body}\n":
            raise ValueError(f"內層腳本含有外層 heredoc 結束字 {end}，不能嵌入")
    return f"""#!/bin/sh
# webit3_onboard.sh v{ver} — webit3 納管腳本（Linux／AIX 通用，一支、跑一次）
# 用法：sh {FILENAME}   （root 執行；不是 root 會自己用 sudo，sudo 不完整或沒有就請你改用 root）
# 最後一行是結果：「{RESULT_DONE}」／「{RESULT_NEED_ROOT}」／「{RESULT_STOP}：<原因>」，細節在上面。
# 主機端紀錄：{hf.HOST_LOG}
{eng.ESXI_SCRIPT_GUARD_SH.replace("exit 3; fi", 'echo "' + RESULT_STOP + '：VMware 設備（ESXi／vCenter）不可納管"; exit 3; fi')}
{eng.platform_guard_sh(result_prefix=RESULT_STOP + "：")}
WEBIT3_SHA=""
if [ -f "$0" ]; then
  WEBIT3_SHA="$( {{ sha256sum "$0" 2>/dev/null || openssl dgst -sha256 "$0" 2>/dev/null || csum -h SHA256 "$0" 2>/dev/null || true; }} | awk '{{for(i=1;i<=NF;i++) if(length($i)==64){{print $i; exit}}}}')"
fi
{hf.header_sh("sh", RESULT_NEED_ROOT)}webit3_os="$(uname -s 2>/dev/null)"
case "$webit3_os" in
  Linux)
    bash -s "$WEBIT3_SHA" <<'WEBIT3_LINUX_BODY'
{lin}
WEBIT3_LINUX_BODY
    rc=$? ;;
  AIX)
    ksh -s "$WEBIT3_SHA" <<'WEBIT3_AIX_BODY'
{aix}
WEBIT3_AIX_BODY
    rc=$? ;;
  *)
    echo "{RESULT_STOP}：這支只支援 Linux 與 AIX（這台是 ${{webit3_os:-不明}}；Windows 由系統走 WinRM，不用這支）"
    exit 2 ;;
esac
case "$rc" in
  0) echo "{RESULT_DONE}" ;;
  {hf.NEED_ROOT_EXIT}) echo "{RESULT_NEED_ROOT}" ;;
  3) echo "{RESULT_STOP}：VMware 設備（ESXi／vCenter）不可納管" ;;
  {eng.PLATFORM_REFUSED_EXIT}) echo "{RESULT_STOP}：不是允許的平台（原因見上方）" ;;
  *) echo "{RESULT_STOP}：腳本中途失敗（離開碼 $rc；原因見上方 [!] 行與 {hf.HOST_LOG}）" ;;
esac
exit "$rc"
"""


def bundle_for(conn) -> str:
    """用這台收集器的現行設定組出 webit3_onboard.sh（網頁一鍵納管與下載共用這一支）。"""
    import manage_state
    import onboard_engine as eng

    return build_bundle(eng.collector_pubkey(), eng.resolve_collector_ip(conn),
                        manage_state.onboard_account(conn, "linux"),   # 舊名一律建新名
                        eng.resolve_account_comment(conn),
                        extra_ips=eng.resolve_extra_collector_ips(conn),
                        aix_account=manage_state.get_collect_account(conn, "aix"))


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def one_liner(url: str, sha: str) -> str:
    """一行：下載 → 驗 sha256 → 跑。放在自己的家目錄（不用 /tmp 固定檔名，避免被別人預先放檔）。
    Linux／AIX 都能用：curl 不在就用 wget；sha256sum 不在就用 openssl／csum。"""
    f = f"$HOME/{FILENAME}"
    # if／else：腳本本身跑失敗時，不可以被講成「下載或 sha256 驗證失敗」（2026-10-18 審查）
    return (f"F={f}; if (curl -fsS -o $F '{url}' || wget -q -O $F '{url}') && "
            f"S=$( (sha256sum $F || openssl dgst -sha256 $F || csum -h SHA256 $F) 2>/dev/null "
            f"| awk '{{for(i=1;i<=NF;i++) if(length($i)==64){{print $i; exit}}}}') && "
            f"[ \"$S\" = \"{sha}\" ]; then sh $F; else echo '{RESULT_STOP}：下載或 sha256 驗證失敗'; fi")


# ===== 下載權杖：綁這一批目標主機、30 分鐘內有效 =====
#
# 不是「用一次就失效」：同一行指令要貼到這一批的每一台。改成**綁定這批主機的 IP**——
# 只有從這批主機（request 的來源位址）來的下載才給（2026-10-18 審查）。
# 腳本裡沒有密碼／私鑰，但**有**收集帳號備註（設定值，預設中性 WEBIT3 collector）與收集器（含備援）IP，
# 所以不是「沒有任何敏感資訊」，才要綁來源。來源被 NAT 成別的位址時下載會被拒——改用下載檔。
TOKEN_TTL = 30 * 60
_TOKENS: dict[str, tuple[float, frozenset]] = {}
_LOCK = threading.Lock()


def issue_token(ips=(), now: float | None = None) -> str:
    now = now or time.time()
    tok = secrets.token_urlsafe(24)
    with _LOCK:
        for k in [k for k, (exp, _) in _TOKENS.items() if exp < now]:
            _TOKENS.pop(k, None)
        _TOKENS[tok] = (now + TOKEN_TTL, frozenset(i.strip() for i in ips if i and i.strip()))
    return tok


def token_valid(tok: str, client_ip: str | None = None, now: float | None = None) -> bool:
    """權杖沒過期、而且來源位址是這批主機之一，才給下載。"""
    now = now or time.time()
    with _LOCK:
        got = _TOKENS.get(tok or "")
    if not got:
        return False
    exp, ips = got
    return exp >= now and (client_ip or "") in ips
