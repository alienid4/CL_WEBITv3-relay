"""檔案傳送（Q94）送到目標主機執行的小腳本，與解析它們輸出的函式。

## 規則（跟收集腳本同一套 SOC 精神）

  · 純文字，不 base64、不編碼指令、不改執行原則；Windows 是 PowerShell 的 cmdlet，Linux／AIX 是 POSIX sh／ksh。
  · 路徑與檔名一律先過 transfer_policy（白名單資料夾＋嚴格檔名），所以這裡可以放心放進單引號。
    即使如此，組腳本時仍再檢查一次沒有單引號（縱深防禦：檔名規則日後被放寬，這裡也不會變成注入點）。
  · Linux／AIX 腳本最前面一律放 VMware 守門句（跟收集共用 onboard_engine.vmware_marker_guard_sh）：
    連上後發現是 ESXi／vCenter 就印標記、什麼都不做。這是天條的最後一道防線。
  · 輸出一律是「KEY=value」行，用 parse_kv() 解析；沒看到 __WEBIT_END__ 就當腳本沒跑完。

## 為什麼 AIX 沒有 stat／sha256sum 也行

雜湊：sha256sum（Linux）→ csum -h SHA256（AIX）→ openssl dgst -sha256，三者擇一。
大小：wc -c。修改時間：ls -ld 的月日時三欄原樣顯示（AIX 沒有 stat，且 ls 的日期格式因環境而異，不假裝解析成時間戳）。
"""
from __future__ import annotations

import re

import transfer_policy as pol

END_MARK = "__WEBIT_END__"
VMWARE_MARK = "__WEBIT_VMWARE_REFUSED__"

_HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")


class ScriptError(ValueError):
    pass


def _q(s: str) -> str:
    """放進單引號。有單引號或控制字元就拒絕（不嘗試跳脫）。"""
    if "'" in s or any(ord(c) < 32 for c in s):
        raise ScriptError("路徑或檔名含不允許的字元")
    return "'" + s + "'"


def _vmware_guard() -> str:
    import onboard_engine
    return onboard_engine.vmware_marker_guard_sh(VMWARE_MARK)


# ---------------------------------------------------------------- Linux／AIX（sh／ksh）
_SH_HASH_FN = (
    'h() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk \'{print $1}\'; '
    'elif command -v csum >/dev/null 2>&1; then csum -h SHA256 "$1" | awk \'{print $1}\'; '
    'elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 "$1" | awk \'{print $NF}\'; '
    'else echo NOHASH; fi; }\n'
)


def sh_wrap(body: str) -> str:
    """守門句＋本文（包成函式，才能用 return 提早離開而仍印結束標記）＋結束標記。
    守門句命中會直接 exit 0（印 VMWARE_MARK），本文不會執行，也不會有結束標記。"""
    return _vmware_guard() + "\nw3main() {\n" + body.rstrip("\n") + f"\n}}\nw3main\necho {END_MARK}\n"


def sh_probe(folder: str, name: str | None) -> str:
    f = _q(folder)
    body = (f'if [ -d {f} ]; then echo DIR=1; else echo DIR=0; fi\n'
            f'echo FREE_KB=$(df -Pk {f} 2>/dev/null | awk \'NR==2{{print $4}}\')\n')
    if name:
        p = _q(f"{folder}/{name}")
        body += (f'if [ -L {p} ]; then echo EXIST=1; echo SYMLINK=1;\n'
                 f'elif [ -f {p} ]; then echo EXIST=1; echo SYMLINK=0; echo SIZE=$(wc -c < {p} | awk \'{{print $1}}\');\n'
                 f'  echo MTIME=$(ls -ld {p} | awk \'{{print $6" "$7" "$8}}\');\n'
                 f'elif [ -e {p} ]; then echo EXIST=1; echo SYMLINK=0; echo NOTFILE=1;\n'
                 f'else echo EXIST=0; fi\n')
    return sh_wrap(body)


def sh_part_free(folder: str, part: str) -> str:
    """開始傳之前確認暫存檔名沒有被佔用（含符號連結——/tmp 是人人可寫的目錄，不跟著連結寫出去）。"""
    p = _q(f"{folder}/{part}")
    return sh_wrap(f'if [ -e {p} ] || [ -L {p} ]; then echo PARTBUSY=1; else echo PARTBUSY=0; fi\n')


def sh_sha256(folder: str, name: str) -> str:
    p = _q(f"{folder}/{name}")
    return sh_wrap(_SH_HASH_FN + f'if [ -L {p} ]; then echo SYMLINK=1; elif [ -f {p} ]; then echo SYMLINK=0; echo SHA256=$(h {p}); '
                   f'else echo MISSING=1; fi\n')


def sh_finalize(folder: str, part: str, final: str, expected: str, overwrite: bool) -> str:
    """驗雜湊→相符才改名成正式檔名；不符就刪掉暫存檔（不留下壞檔）。已有同名檔且沒要覆蓋 → 刪暫存、回 exists。"""
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ScriptError("期望雜湊格式不對")
    pp, fp = _q(f"{folder}/{part}"), _q(f"{folder}/{final}")
    ow = "1" if overwrite else "0"
    return sh_wrap(_SH_HASH_FN +
                   f'if [ -L {pp} ]; then echo RESULT=symlink; return 0; fi\n'
                   f'if [ ! -f {pp} ]; then echo RESULT=missing; return 0; fi\n'
                   f'H=$(h {pp}); echo SHA256=$H\n'
                   f'if [ "$H" != "{expected}" ]; then rm -f {pp}; echo RESULT=mismatch; return 0; fi\n'
                   f'if [ -d {fp} ]; then rm -f {pp}; echo RESULT=isdir; return 0; fi\n'
                   f'if [ -e {fp} ] && [ "{ow}" != "1" ]; then rm -f {pp}; echo RESULT=exists; return 0; fi\n'
                   f'if [ -L {fp} ]; then rm -f {pp}; echo RESULT=symlink; return 0; fi\n'
                   f'mv -f {pp} {fp} && echo RESULT=ok || echo RESULT=mvfail\n')


def sh_remove_part(folder: str, part: str) -> str:
    if not part.endswith(pol.PART_SUFFIX):
        raise ScriptError("只能清掉我們自己的暫存檔")
    return sh_wrap(f'rm -f {_q(f"{folder}/{part}")}; echo REMOVED=1\n')


def sh_list(folder: str) -> str:
    f = _q(folder)
    return sh_wrap(
        f'cd {f} 2>/dev/null || {{ echo DIR=0; return 0; }}\necho DIR=1\n'
        'for f in *; do\n'
        '  [ -f "$f" ] || continue\n'
        '  [ -L "$f" ] && continue\n'
        '  echo "F|$f|$(wc -c < "$f" | awk \'{print $1}\')|$(ls -ld "$f" | awk \'{print $6" "$7" "$8}\')"\n'
        'done\n')


def sh_command(folder: str, command: str) -> str:
    """使用者自己打的指令：從白名單資料夾開始跑。守門句在最前面；指令原樣放在後面，輸出與離開碼原樣回。"""
    if "\x00" in command:
        raise ScriptError("指令含不合法字元")
    return _vmware_guard() + f"\ncd {_q(folder)} || exit 97\n" + command.rstrip("\n") + "\n"


# ---------------------------------------------------------------- Windows（PowerShell cmdlet）
def _wq(s: str) -> str:
    return _q(s)


def ps_probe(folder: str, name: str | None) -> str:
    f = _wq(folder)
    drive = folder[0]
    body = (f"$f = {f}\n"
            "$d = Test-Path -LiteralPath $f -PathType Container\n"
            '"DIR=" + [int]$d\n'
            "if ($d) {\n"
            f'  try {{ "FREE=" + (New-Object System.IO.DriveInfo("{drive}")).AvailableFreeSpace }} catch {{ "FREE=" }}\n')
    if name:
        p = _wq(f"{folder}\\{name}")
        body += (f"  $p = {p}\n"
                 "  if (Test-Path -LiteralPath $p -PathType Leaf) {\n"
                 "    $i = Get-Item -LiteralPath $p\n"
                 '    "EXIST=1"\n'
                 '    if ($i.Attributes -band [IO.FileAttributes]::ReparsePoint) { "SYMLINK=1" } else { "SYMLINK=0" }\n'
                 '    "SIZE=" + $i.Length\n'
                 '    "MTIME=" + $i.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")\n'
                 '  } elseif (Test-Path -LiteralPath $p) { "EXIST=1"; "SYMLINK=0"; "NOTFILE=1" } else { "EXIST=0" }\n')
    body += "}\n"
    return body + f'"{END_MARK}"\n'


def ps_part_free(folder: str, part: str) -> str:
    p = _wq(f"{folder}\\{part}")
    return f'if (Test-Path -LiteralPath {p}) {{ "PARTBUSY=1" }} else {{ "PARTBUSY=0" }}\n"{END_MARK}"\n'


def ps_sha256(folder: str, name: str) -> str:
    p = _wq(f"{folder}\\{name}")
    return (f"$p = {p}\n"
            'if (Test-Path -LiteralPath $p -PathType Leaf) {\n'
            '  $i = Get-Item -LiteralPath $p\n'
            '  if ($i.Attributes -band [IO.FileAttributes]::ReparsePoint) { "SYMLINK=1" } else {\n'
            '    "SYMLINK=0"\n'
            '    "SHA256=" + (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower()\n'
            '  }\n'
            '} else { "MISSING=1" }\n'
            f'"{END_MARK}"\n')


def ps_finalize(folder: str, part: str, final: str, expected: str, overwrite: bool) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ScriptError("期望雜湊格式不對")
    pp, fp = _wq(f"{folder}\\{part}"), _wq(f"{folder}\\{final}")
    ow = "$true" if overwrite else "$false"
    return (f"$pp = {pp}\n$fp = {fp}\n$ow = {ow}\n"
            "if (-not (Test-Path -LiteralPath $pp -PathType Leaf)) { \"RESULT=missing\" }\n"
            "elseif ((Get-Item -LiteralPath $pp).Attributes -band [IO.FileAttributes]::ReparsePoint) { \"RESULT=symlink\" }\n"
            "else {\n"
            "  $h = (Get-FileHash -LiteralPath $pp -Algorithm SHA256).Hash.ToLower()\n"
            '  "SHA256=" + $h\n'
            f'  if ($h -ne "{expected}") {{ Remove-Item -LiteralPath $pp -Force; "RESULT=mismatch" }}\n'
            "  elseif (Test-Path -LiteralPath $fp -PathType Container) { Remove-Item -LiteralPath $pp -Force; \"RESULT=isdir\" }\n"
            "  elseif ((Test-Path -LiteralPath $fp) -and ((Get-Item -LiteralPath $fp).Attributes -band [IO.FileAttributes]::ReparsePoint)) { Remove-Item -LiteralPath $pp -Force; \"RESULT=symlink\" }\n"
            "  elseif ((Test-Path -LiteralPath $fp) -and (-not $ow)) { Remove-Item -LiteralPath $pp -Force; \"RESULT=exists\" }\n"
            "  else {\n"
            "    try { Move-Item -LiteralPath $pp -Destination $fp -Force:$ow -ErrorAction Stop; \"RESULT=ok\" } catch { \"RESULT=mvfail\" }\n"
            "  }\n"
            "}\n"
            f'"{END_MARK}"\n')


def ps_remove_part(folder: str, part: str) -> str:
    if not part.endswith(pol.PART_SUFFIX):
        raise ScriptError("只能清掉我們自己的暫存檔")
    p = _wq(f"{folder}\\{part}")
    return f'Remove-Item -LiteralPath {p} -Force -ErrorAction SilentlyContinue\n"REMOVED=1"\n"{END_MARK}"\n'


def ps_list(folder: str) -> str:
    f = _wq(folder)
    return (f"$f = {f}\n"
            'if (-not (Test-Path -LiteralPath $f -PathType Container)) { "DIR=0" } else {\n'
            '  "DIR=1"\n'
            '  Get-ChildItem -LiteralPath $f -ErrorAction SilentlyContinue | Where-Object { (-not $_.PSIsContainer) -and (-not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)) } | ForEach-Object {\n'
            '    "F|" + $_.Name + "|" + $_.Length + "|" + $_.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")\n'
            '  }\n'
            '}\n'
            f'"{END_MARK}"\n')


# ---------------------------------------------------------------- 解析
def parse_kv(text: str) -> dict:
    """KEY=value 行 → dict（同 key 取最後一個）。F| 開頭的檔案列放進 files。"""
    out: dict = {"files": []}
    for line in (text or "").splitlines():
        line = line.strip("\r")
        if line.startswith("F|"):
            parts = line.split("|")
            if len(parts) >= 4:
                try:
                    out["files"].append({"name": "|".join(parts[1:-2]), "size": int(parts[-2]), "mtime": parts[-1]})
                except ValueError:
                    continue
            continue
        m = re.fullmatch(r"([A-Z0-9_]+)=(.*)", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    out["complete"] = END_MARK in (text or "")
    out["vmware"] = VMWARE_MARK in (text or "")
    return out


def hash_of(v: str | None) -> str | None:
    """從 SHA256= 的值取 64 位十六進位（AIX csum／openssl 輸出可能有前綴）。"""
    m = _HEX64.search(v or "")
    return m.group(0).lower() if m else None
