#!/usr/bin/env python3
"""舊版 SSH 測連線小工具——**只談判加密、不登入**（2026-09-15）。

## 為什麼要有

公司機（RHEL 9）的系統 ssh 連 Brocade FOS 6.4 SAN switch（測試區那批舊 switch）失敗：
`no matching host key type found. Their offer: ssh-dss`，加 `-oHostKeyAlgorithms=+ssh-dss` 也一樣
→ 系統 ssh 根本不支援 DSA。收集程式用的 paramiko 5.0 也已經移除 DSA。
paramiko 3.5.1 還支援（本機自架 ssh-dss 測試伺服器驗證過），但**真的 switch 還沒試過**。
這支就是在公司機上試「談不談得成」，結果決定要做舊版 SSH 收集，還是退到 telnet。

## 做什麼、不做什麼

- 做：TCP 連到目標 → SSH 握手（金鑰交換＋主機金鑰驗證）→ 印出雙方談成的演算法 → 斷線
- **不做**：不送帳號密碼、不登入、不開 shell、不跑任何指令、不寫任何檔案、不改系統設定
- 主機金鑰只拿來印出類型與指紋，不寫進 known_hosts

用法：  python ssh_legacy_probe.py <IP> [port]
結束碼：0 談成／2 SSH 談判失敗／3 TCP 連不上／4 參數錯誤
"""
from __future__ import annotations

import io
import logging
import re
import socket
import sys

#: 舊設備常見、新版預設關掉的演算法。只「加進」可選清單，不移除任何新演算法。
LEGACY = {
    "keys": ["ssh-dss", "ssh-rsa"],
    "kex": ["diffie-hellman-group14-sha1", "diffie-hellman-group1-sha1",
            "diffie-hellman-group-exchange-sha1"],
    "ciphers": ["aes128-cbc", "aes192-cbc", "aes256-cbc", "3des-cbc"],
    "digests": ["hmac-sha1", "hmac-sha1-96", "hmac-md5", "hmac-md5-96"],
}

#: paramiko DEBUG log 裡「談成／對方提供」的那幾行。
#: paramiko 3.5.1 的寫法是 "=== Key exchange agreements ==="、"Kex: ..."、"HostKey: ..."、
#: "Cipher: ..."、"MAC: ..."（不是 "agreed"），對方提供的清單是 "kex algos:"、"server key:" 等。
_PREFIX = re.compile(r"^[A-Z]+:paramiko[\w.]*:")
_AGREED = re.compile(r"^(Kex|HostKey|Cipher|MAC|Compression)\b|agreed|agreements|kex algos|"
                     r"server key|client encrypt|server encrypt|client mac|server mac|"
                     r"Connected \(version", re.I)


def summarize_log(text: str) -> list[str]:
    """從 paramiko 的 DEBUG log 挑出跟談判有關的行（去掉「DEBUG:paramiko.transport:」這種前綴）。"""
    out = []
    for line in text.splitlines():
        s = _PREFIX.sub("", line).strip()
        if s and _AGREED.search(s):
            out.append(s)
    return out


def _extend(current, extra, supported):
    return tuple(list(current) + [x for x in extra if x in supported and x not in current])


def probe(host: str, port: int = 22, timeout: float = 15.0) -> tuple[int, list[str]]:
    import paramiko

    lines = [f"paramiko 版本：{paramiko.__version__}（ssh-dss 支援：{'有' if hasattr(paramiko, 'DSSKey') else '沒有'}）",
             f"目標：{host}:{port}"]

    log_buf = io.StringIO()
    handler = logging.StreamHandler(log_buf)
    handler.setLevel(logging.DEBUG)
    plog = logging.getLogger("paramiko")
    plog.setLevel(logging.DEBUG)
    plog.addHandler(handler)

    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        lines.append(f"✗ TCP 連不上：{type(exc).__name__}: {exc}（網路／防火牆問題，還沒到 SSH 這一步）")
        plog.removeHandler(handler)
        return 3, lines

    t = paramiko.Transport(sock)
    try:
        opts = t.get_security_options()
        opts.key_types = _extend(opts.key_types, LEGACY["keys"], t._key_info)
        opts.kex = _extend(opts.kex, LEGACY["kex"], t._kex_info)
        opts.ciphers = _extend(opts.ciphers, LEGACY["ciphers"], t._cipher_info)
        opts.digests = _extend(opts.digests, LEGACY["digests"], t._mac_info)
        # start_client 只做握手（金鑰交換＋主機金鑰），**不做任何認證**
        t.start_client(timeout=timeout)
        key = t.get_remote_server_key()
        lines += [
            "✓ SSH 談判成功（沒有登入、沒有送帳密）",
            f"  對方版本：{t.remote_version}",
            f"  主機金鑰：{key.get_name()}（{key.get_bits()} bits）指紋 {key.get_fingerprint().hex()}",
            f"  加密：{t.remote_cipher}　MAC：{t.remote_mac}",
        ]
        code = 0
    except Exception as exc:  # noqa: BLE001 - 談判失敗的原因要原樣印出
        lines.append(f"✗ SSH 談判失敗：{type(exc).__name__}: {exc}")
        code = 2
    finally:
        t.close()
        plog.removeHandler(handler)

    detail = summarize_log(log_buf.getvalue())
    if detail:
        lines.append("  ── 談判細節（paramiko log）──")
        lines += [f"  {d}" for d in detail]
    return code, lines


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.split("用法：")[1].split("\n")[0].strip() if "用法：" in __doc__ else "用法錯誤")
        print("用法：python ssh_legacy_probe.py <IP> [port]")
        return 4
    host = argv[1]
    try:
        port = int(argv[2]) if len(argv) > 2 else 22
    except ValueError:
        print("port 要是數字")
        return 4
    code, lines = probe(host, port)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
