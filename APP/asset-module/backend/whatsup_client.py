"""WhatsUp Gold REST API 用戶端（唯讀）。

登入：POST /api/v1/token（OAuth2 password grant）→ `Authorization: Bearer <access_token>`。
讀取：裝置清單（overview）、群組裝置（card，補群組名稱）、單台狀態（補有問題的監視項目與起始時間）。
全程只讀，不改 WhatsUp 的任何設定。硬體告警一律以 WhatsUp 為準，本系統不另外掃硬體。
"""
from __future__ import annotations

import integration_http as ih
import whatsup_constants as C


class WhatsupError(Exception):
    """WhatsUp Gold 回報的錯誤或不認得的回應。訊息已清掉機密。"""


def pick(d: dict, keys: tuple[str, ...]):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


class WhatsupClient:
    def __init__(self, base_url: str, username: str, password: str, transport: ih.Transport,
                 timeout: float = ih.DEFAULT_TIMEOUT):
        self.base = ih.normalize_base_url(base_url)
        self._user, self._pw = username, password
        self._t = transport
        self._timeout = timeout
        self._token: str | None = None

    def _secrets(self) -> list[str]:
        return [self._pw, self._token or ""]

    def _fail(self, what: str, res: ih.HttpResult) -> WhatsupError:
        msg = ""
        if isinstance(res.body, dict):
            msg = str(res.body.get("error_description") or res.body.get("error") or res.body.get("message") or "")
        return WhatsupError(f"{what}失敗（HTTP {res.status}）{('：' + ih.scrub(msg, self._secrets())) if msg else ''}")

    def acquire_token(self) -> None:
        try:
            res = self._t.request("POST", self.base + C.TOKEN_PATH, headers={"Accept": "application/json"},
                                  form={"grant_type": "password", "username": self._user, "password": self._pw},
                                  timeout=self._timeout)
        except ih.TransportError as exc:
            raise WhatsupError(ih.scrub(exc, self._secrets())) from exc
        if res.status in (400, 401, 403):
            raise WhatsupError("登入失敗：帳號或密碼不正確")
        if res.status != 200 or not isinstance(res.body, dict) or not res.body.get("access_token"):
            raise self._fail("登入", res)
        self._token = str(res.body["access_token"])

    def _get(self, path: str, params: dict | None = None, retry: bool = True) -> ih.HttpResult:
        if self._token is None:
            self.acquire_token()
        try:
            res = self._t.request("GET", self.base + path, headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                                  params=params, timeout=self._timeout)
        except ih.TransportError as exc:
            raise WhatsupError(ih.scrub(exc, self._secrets())) from exc
        if res.status == 401 and retry:
            self._token = None
            self.acquire_token()
            return self._get(path, params, retry=False)
        return res

    def _pages(self, path: str, base_params: dict, what: str) -> list[dict]:
        """依 paging.nextPageId 翻頁；任何一頁失敗就丟錯（不回半份清單假裝完整）。"""
        out: list[dict] = []
        page_id = None
        for _ in range(C.MAX_PAGES):
            params = {**base_params, "limit": C.PAGE_LIMIT}
            if page_id:
                params["pageId"] = page_id
            res = self._get(path, params)
            if res.status != 200 or not isinstance(res.body, dict):
                raise self._fail(f"讀取{what}", res)
            data = res.body.get("data") or {}
            items = data.get("devices") if isinstance(data, dict) else None
            out.extend(d for d in (items or []) if isinstance(d, dict))
            page_id = (res.body.get("paging") or {}).get("nextPageId")
            if not page_id:
                return out
        raise WhatsupError(f"讀取{what}：分頁超過 {C.MAX_PAGES} 頁，疑似分頁資訊異常，已中止")

    def devices_overview(self) -> list[dict]:
        return self._pages(C.DEVICES_OVERVIEW_PATH, dict(C.DEVICES_OVERVIEW_PARAMS), "裝置清單")

    def group_devices(self) -> list[dict]:
        return self._pages(C.GROUP_DEVICES_PATH, dict(C.GROUP_DEVICES_PARAMS), "群組裝置")

    def device_status(self, device_id: str) -> dict:
        res = self._get(C.DEVICE_STATUS_PATH.format(id=device_id))
        if res.status != 200 or not isinstance(res.body, dict):
            raise self._fail("讀取裝置狀態", res)
        data = res.body.get("data")
        return data if isinstance(data, dict) else res.body
