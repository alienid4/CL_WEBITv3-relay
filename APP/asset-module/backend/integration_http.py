"""外部系統整合（Aria Operations、WhatsUp Gold）共用的 HTTP 層。

## 為什麼獨立一層

兩個整合都要對公司內網的管理系統發 HTTPS 請求。把「怎麼發」收在一個小介面後面有三個好處：
  1. 測試用假的 transport，開發機連不到公司內網也能把整條路測完（真實回應格式仍須在公司網路驗證）。
  2. TLS 規則只有一份：預設一定驗證憑證；公司自簽 CA 只能「指定 CA 憑證檔路徑」，
     **沒有**「關閉驗證」這個選項（要關就是安全缺口，不提供）。
  3. 錯誤訊息一律先清掉密碼／權杖再往上丟，不會有帳密跑進 log、稽核或畫面。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

DEFAULT_TIMEOUT = 30.0


class TransportError(Exception):
    """連線層錯誤（連不上、逾時、憑證驗證失敗…）。訊息已清掉機密，可以直接給人看。"""


class ConfigError(ValueError):
    """設定本身不合法（網址、CA 路徑…）。"""


@dataclass
class HttpResult:
    status: int
    body: Any = None            # 解析成功的 JSON（失敗為 None）
    text: str = ""              # 原始文字（只用於產生錯誤訊息，已截短）
    headers: dict = field(default_factory=dict)


class Transport(Protocol):
    def request(self, method: str, url: str, *, headers: dict | None = None, json_body: Any = None,
                form: dict | None = None, params: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> HttpResult: ...

    def close(self) -> None: ...


def normalize_base_url(url: str) -> str:
    """只收 https://主機[:埠]，去掉尾端斜線。不收帳密嵌在網址裡、不收路徑與查詢字串。"""
    u = (url or "").strip()
    if not u:
        raise ConfigError("尚未設定 API 網址")
    parts = urlsplit(u)
    if parts.scheme != "https":
        raise ConfigError("API 網址必須以 https:// 開頭（帳密與權杖不能走未加密連線）")
    if not parts.hostname:
        raise ConfigError("API 網址格式不正確")
    if parts.username or parts.password:
        raise ConfigError("API 網址不能夾帶帳號密碼")
    if parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ConfigError("API 網址只填到主機與埠（例：https://aria.example.local），路徑由程式補")
    return f"https://{parts.netloc}"


def check_ca_bundle(path: str | None) -> str | None:
    """CA 憑證檔路徑：空＝用系統預設的信任清單；有填就必須是存在的檔案。"""
    p = (path or "").strip()
    if not p:
        return None
    if not os.path.isfile(p):
        raise ConfigError("找不到 CA 憑證檔，請確認伺服器上的路徑")
    return p


def scrub(text: Any, secrets: list[str] | tuple[str, ...] = (), limit: int = 200) -> str:
    """把機密從文字裡抹掉並截短。權杖與長 base64 樣式的字串也一併遮蔽。"""
    s = "" if text is None else str(text)
    for sec in secrets:
        if sec and len(sec) >= 3:
            s = s.replace(sec, "●●●")
    s = re.sub(r"(?i)(token|authorization|password|secret)(\"?\s*[:=]\s*\"?)[^\s\",}]+", r"\1\2●●●", s)
    s = re.sub(r"[A-Za-z0-9+/=_\-]{40,}", "●●●", s)
    return s[:limit]


class HttpxTransport:
    """真正連線用（httpx）。測試不用這個。"""

    def __init__(self, ca_bundle: str | None = None):
        self._verify: Any = check_ca_bundle(ca_bundle) or True      # True＝驗證且用系統信任清單
        self._client = None

    def _c(self):
        if self._client is None:
            import httpx
            self._client = httpx.Client(verify=self._verify, follow_redirects=False)
        return self._client

    def request(self, method, url, *, headers=None, json_body=None, form=None, params=None, timeout=DEFAULT_TIMEOUT):
        import httpx
        try:
            r = self._c().request(method, url, headers=headers, json=json_body, data=form, params=params, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise TransportError("連線逾時，請確認網路與防火牆") from exc
        except httpx.ConnectError as exc:
            msg = str(exc).lower()
            if "certificate" in msg or "ssl" in msg or "tls" in msg:
                raise TransportError("TLS 憑證驗證失敗，請確認 CA 憑證檔路徑（本系統不提供關閉驗證）") from exc
            raise TransportError("連不上目標主機，請確認網址、埠與防火牆") from exc
        except httpx.HTTPError as exc:
            raise TransportError(f"連線失敗（{type(exc).__name__}）") from exc
        body = None
        try:
            body = r.json()
        except ValueError:
            body = None
        return HttpResult(status=r.status_code, body=body, text=(r.text or "")[:500],
                          headers={k.lower(): v for k, v in r.headers.items()})

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


#: 測試可以換成回傳假 transport 的函式：factory(ca_bundle) -> Transport
TRANSPORT_FACTORY = HttpxTransport


def make_transport(ca_bundle: str | None) -> Transport:
    return TRANSPORT_FACTORY(ca_bundle)
