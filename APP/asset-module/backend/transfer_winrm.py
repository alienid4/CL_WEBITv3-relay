"""檔案傳送（Q94）的 Windows 傳輸層：跟 winrm_collector._psrp_run 同一條路（pypsrp，PSRP，NTLM／5985／訊息加密）。

## 為什麼不用 Ansible 傳 Windows（審查 BLOCKER 1）

Ansible 的 Windows 模組（跑指令、傳檔、抓檔那幾個）會把 payload 包成編碼過的文字、傳檔也是文字編碼分段，
跟專案的 SOC 硬規則（不送編碼指令、不用文字編碼混淆）衝突。這裡改成跟收集器一樣：

  · 腳本是**純文字**，用 PSRP 的 add_script 當資料送（不做動態求值、不編碼指令、不改執行原則）；
  · 檔案本體是**位元組陣列當 PSRP 參數**（[byte[]]）一段一段送：腳本文字裡沒有任何檔案內容，
    內容怎麼在 WS-Man 線上序列化是 PSRP 協定自己的事，不是我們組進腳本的字串；
  · 來源碼守門：tests/asset_module/test_transfer_winrm.py 掃這個檔案，出現編碼相關字樣就紅。

Windows 這條**沒有限速**（PSRP 不能節流），所以預設同時 1 台；大檔很慢（每段 64 KB 一次往返）。
"""
from __future__ import annotations

import contextlib
from pathlib import Path

CHUNK_BYTES = 64 * 1024                  # 每次往返的檔案位元組數（單一 WS-Man 訊息要收得下，見 winrm_collector.SAFE_ENVELOPE_BYTES）

#: 第一段：CreateNew——暫存檔已存在（含符號連結）就失敗，不跟著寫出去。
WRITE_FIRST = ("param([string]$Path, [byte[]]$Bytes)\n"
               "$fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)\n"
               "try { $fs.Write($Bytes, 0, $Bytes.Length) } finally { $fs.Dispose() }\n"
               '"OK"\n')
WRITE_NEXT = ("param([string]$Path, [byte[]]$Bytes)\n"
              "$fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)\n"
              "try { $fs.Write($Bytes, 0, $Bytes.Length) } finally { $fs.Dispose() }\n"
              '"OK"\n')
#: 讀一段：先輸出實際讀到幾個位元組，再輸出整個緩衝區（呼叫端只取前 n 個）
READ_CHUNK = ("param([string]$Path, [long]$Offset, [int]$Count)\n"
              "$fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)\n"
              "try { [void]$fs.Seek($Offset, [System.IO.SeekOrigin]::Begin); $buf = New-Object byte[] $Count; $n = $fs.Read($buf, 0, $Count); $n; , $buf } finally { $fs.Dispose() }\n")


class WinTransportError(Exception):
    """Windows 傳輸失敗（訊息直接給人看，不含密碼）。"""


class GateStopped(WinTransportError):
    """傳到一半被閘門叫停（模組停用、登入停用…）：暫存檔已刪，呼叫端把這台標「已停止」。"""


#: 每傳幾段問一次閘門（64 KB × 16 ＝ 約 1 MB 一次）
CHECK_EVERY_CHUNKS = 16
REMOVE_PART = ("param([string]$Path)\n"
               "Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue\n"
               '"OK"\n')


class PsrpSession:
    """一個 RunspacePool 的工作階段。invoke() 回輸出文字行；invoke_raw() 回原始輸出物件（讀檔用）。"""

    def __init__(self, pool, host: str, timeout: int):
        self._pool, self._host, self._timeout = pool, host, timeout

    def _run(self, script: str, params: dict | None, as_text: bool, deadline_s: int | None):
        import winrm_collector as wc
        from pypsrp.powershell import PowerShell

        ps = PowerShell(self._pool)
        ps.add_script(script)
        for k, v in (params or {}).items():
            ps.add_parameter(k, v)
        if as_text:
            ps.add_cmdlet("Out-String").add_parameter("Stream")
        wc._invoke_with_deadline(ps, self._host, max(wc.SCRIPT_DEADLINE_S, self._timeout, deadline_s or 0))
        errors = [str(e) for e in (getattr(ps.streams, "error", None) or [])]
        return ps, errors

    def invoke(self, script: str, params: dict | None = None, deadline_s: int | None = None) -> tuple[list[str], list[str]]:
        ps, errors = self._run(script, params, True, deadline_s)
        return [str(o) for o in (ps.output or []) if o is not None], errors

    def invoke_raw(self, script: str, params: dict | None = None, deadline_s: int | None = None) -> list:
        ps, errors = self._run(script, params, False, deadline_s)
        if errors and not ps.output:
            raise WinTransportError("；".join(errors)[:300])
        return list(ps.output or [])


class PsrpTransport:
    """正式用：連線方式跟 winrm_collector 一模一樣（NTLM、5985、訊息加密一律開）。"""

    @contextlib.contextmanager
    def open(self, t, timeout: int = 120):
        import winrm_collector as wc
        from pypsrp.powershell import RunspacePool
        from pypsrp.wsman import WSMan

        try:
            with WSMan(t.ip, port=wc.WINRM_HTTP_PORT, ssl=False, auth="ntlm", encryption="always",
                       username=t.username, password=t.password, no_proxy=True,
                       connection_timeout=min(timeout, 30), operation_timeout=timeout, read_timeout=timeout + 10,
                       max_envelope_size=wc.SAFE_ENVELOPE_BYTES) as wsman:
                with RunspacePool(wsman, configuration_name=wc.PSRP_CONFIGURATION) as pool:
                    yield PsrpSession(pool, t.ip, timeout)
        except WinTransportError:
            raise
        except Exception as exc:  # noqa: BLE001 - 分類成「下一步不同」的錯誤；密碼不進訊息
            mapped = wc._classify(t.ip, exc)
            msg = str(mapped)
            if t.password:
                msg = msg.replace(t.password, "●●●●●●")
            raise WinTransportError(msg[:400]) from exc


# ---------------------------------------------------------------- 檔案傳輸（只用 session，測試換成假的 session）
def run_text(transport, t, script: str, timeout: int = 120, deadline_s: int | None = None) -> tuple[str, list[str]]:
    with transport.open(t, timeout) as s:
        lines, errors = s.invoke(script, None, deadline_s)
    return "\n".join(lines), errors


def upload_file(transport, t, local_path: Path, remote_path: str, max_bytes: int | None = None, check=None) -> int:
    """check()：回 None 表示可以繼續，回字串＝要停的原因。每 CHECK_EVERY_CHUNKS 段問一次；要停就刪掉暫存檔並丟 GateStopped。"""
    if not remote_path.endswith(".webit3part"):
        raise WinTransportError("只能寫我們自己的暫存檔（.webit3part）")
    size = Path(local_path).stat().st_size
    if max_bytes is not None and size > max_bytes:
        raise WinTransportError("檔案超過允許的大小")
    sent, idx = 0, 0
    with transport.open(t, 120) as s, open(local_path, "rb") as f:
        while True:
            if check is not None and idx % CHECK_EVERY_CHUNKS == 0:
                why = check()
                if why:
                    s.invoke(REMOVE_PART, {"Path": remote_path}, 60)
                    raise GateStopped(f"已停止：{why}（暫存檔已刪）")
            chunk = f.read(CHUNK_BYTES)
            if not chunk and idx > 0:
                break
            lines, errors = s.invoke(WRITE_FIRST if idx == 0 else WRITE_NEXT, {"Path": remote_path, "Bytes": chunk}, 300)
            if "OK" not in lines:                          # 空檔的第一段也要看結果
                raise WinTransportError("寫入目標檔失敗：" + ("；".join(errors) or "沒有回應")[:200])
            idx += 1
            sent += len(chunk)
            if not chunk:
                break
    return sent


def download_file(transport, t, remote_path: str, local_path: Path, max_bytes: int) -> int:
    """逐段讀回；累計超過 max_bytes 立刻停止（呼叫端負責刪掉不完整的本機檔）。"""
    got = 0
    with transport.open(t, 120) as s, open(local_path, "wb") as out:
        while True:
            raw = s.invoke_raw(READ_CHUNK, {"Path": remote_path, "Offset": got, "Count": CHUNK_BYTES}, 300)
            if len(raw) < 2:
                raise WinTransportError("讀取目標檔失敗")
            n = int(raw[0])
            if n <= 0:
                break
            data = bytes(raw[1])[:n]
            got += len(data)
            if got > max_bytes:
                raise WinTransportError("檔案超過允許的大小，已停止接收")
            out.write(data)
            if n < CHUNK_BYTES:
                break
    return got
