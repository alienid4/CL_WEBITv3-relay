"""檔案傳送（Q94）：指令文字與輸出在寫進紀錄、回給畫面之前，先遮掉明顯的機密（審查 MEDIUM 6）。

指令是使用者自己打的、輸出是主機吐的——兩邊都可能帶密碼、金鑰、shadow 檔內容。紀錄是「不可刪」的，
所以**進紀錄之前**就要遮（寫進去就刪不掉了）。實際執行的仍是原文（遮的只是留存與顯示的那一份）。
這是「明顯樣式」的盡力遮蔽，不是保證：樣式認不得的機密還是會留下，所以畫面與紀錄都標「已遮蔽 N 處」。
"""
from __future__ import annotations

import re

MASK = "●●●"

_KEY_BLOCK = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S)
_AUTH = re.compile(r"(?i)\b(authorization\s*:\s*)(?:bearer|basic|token)?\s*[^\s\"']+(?:\s+[^\s\"']+)?")
_SHADOW = re.compile(r"(?m)^([A-Za-z_][\w.\-]*):\$[0-9a-zA-Z]+\$[^:\n]*:[^\n]*$")
# 變數名稱只要「含」這些字就遮（DB_PASS、PGPASSWORD、SECRET_KEY、AWS_SECRET_ACCESS_KEY…）：前後各最多 40 個字元，
# 避免對很長的一串文字做二次方回溯（ReDoS）；底線是 \w，所以不能用 \b 開頭。
_KV = re.compile(r"(?i)(?<![A-Za-z0-9])([\w.\-]{0,40}?(?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|access[_-]?key|credential)[\w.\-]{0,40}\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s\"';&|]+)")
# 盡力而為（樣式有限）：echo <密碼> | sudo -S …；net use … /user:帳號 <密碼> 或 net use \\主機\分享 <密碼> /user:帳號
_SUDOS = re.compile(r"(?i)(\becho\s+(?:-n\s+)?)(?:\"[^\"]*\"|'[^']*'|[^\s|]+)(\s*\|\s*sudo\s+(?:-\w+\s+)*-S\b)")
_NETUSE_A = re.compile(r"(?i)(\bnet\s+use\b[^\n]{0,200}?/user:\S+\s+)(?!/)(?:\"[^\"]*\"|'[^']*'|\S+)")
_NETUSE_B = re.compile(r"(?i)(\bnet\s+use\s+(?:\S+\s+)?\\\\\S+\s+)(?!/)(?:\"[^\"]*\"|'[^']*'|\S+)(?=\s+/user:)")
_LONGFLAG = re.compile(r"(?i)(--?(?:password|passwd|pass|passphrase|token|secret)(?:=|\s+))(?:\"[^\"]*\"|'[^']*'|[^\s\"';&|]+)")
_PWTOOL = re.compile(r"(?i)\b(ssh" r"pass\s+-p\s*)(?:\"[^\"]*\"|'[^']*'|\S+)")
_MYSQLP = re.compile(r"(?i)\b((?:mysql|mysqldump|mysqladmin|mariadb|psql|sqlplus|mongo|redis-cli)\b[^\n|;&]{0,200}?\s-p)(?!\s*-)(?:\"[^\"]*\"|'[^']*'|\S+)")
_CURLU = re.compile(r"(?i)(\s(?:-u|--user)\s+[^\s:]+:)(?:\"[^\"]*\"|'[^']*'|\S+)")
_SECURESTR = re.compile(r"(?i)(ConvertTo-SecureString\s+)(?:\"[^\"]*\"|'[^']*'|\S+)")


def redact(text: str | None) -> tuple[str, int]:
    """回 (遮蔽後的文字, 遮了幾處)。"""
    s = text or ""
    n = 0

    def sub(rx, repl):
        nonlocal s, n

        def one(m):
            global_new = repl(m) if callable(repl) else repl
            nonlocal n
            if global_new != m.group(0):          # 已經是遮蔽後的樣子就不重算
                n += 1
            return global_new
        s = rx.sub(one, s)

    sub(_KEY_BLOCK, "[私鑰區塊已遮蔽]")
    sub(_SHADOW, lambda m: f"{m.group(1)}:{MASK}")
    sub(_AUTH, lambda m: f"{m.group(1)}{MASK}")
    sub(_PWTOOL, lambda m: f"{m.group(1)}{MASK}")
    sub(_MYSQLP, lambda m: f"{m.group(1)}{MASK}")
    sub(_CURLU, lambda m: f"{m.group(1)}{MASK}")
    sub(_SECURESTR, lambda m: f"{m.group(1)}{MASK}")
    sub(_SUDOS, lambda m: f"{m.group(1)}{MASK}{m.group(2)}")
    sub(_NETUSE_B, lambda m: f"{m.group(1)}{MASK}")
    sub(_NETUSE_A, lambda m: f"{m.group(1)}{MASK}")
    sub(_LONGFLAG, lambda m: f"{m.group(1)}{MASK}")
    sub(_KV, lambda m: f"{m.group(1)}{MASK}")
    return s, n
