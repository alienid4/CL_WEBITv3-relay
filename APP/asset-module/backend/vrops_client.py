"""Aria Operations Suite API 用戶端（唯讀）。

流程：登入換權杖（POST auth/token/acquire）→ 之後每個請求帶 `Authorization: vRealizeOpsToken <權杖>`
→ 資源清單（resources，分頁）、關聯（resources/{id}/relationships）、屬性（批次查詢）。
只做 GET／查詢型 POST，完全不寫入 Aria，也不碰任何 vCenter／ESXi（VMware 天條：資訊只走 Aria 的 API）。

權杖遇到 401 只重新登入一次，再失敗就丟錯，不無限重試（避免帳號被鎖）。
"""
from __future__ import annotations

import integration_http as ih
import vrops_constants as C


class VropsError(Exception):
    """Aria Operations 回報的錯誤或不認得的回應。訊息已清掉機密。"""


class VropsClient:
    def __init__(self, base_url: str, username: str, password: str, auth_source: str | None,
                 transport: ih.Transport, timeout: float = ih.DEFAULT_TIMEOUT):
        self.base = ih.normalize_base_url(base_url)
        self._user, self._pw = username, password
        self._source = (auth_source or "").strip() or None
        self._t = transport
        self._timeout = timeout
        self._token: str | None = None

    # ---- 內部 ----
    def _secrets(self) -> list[str]:
        return [self._pw, self._token or ""]

    def _fail(self, what: str, res: ih.HttpResult) -> VropsError:
        msg = ""
        if isinstance(res.body, dict):
            msg = str(res.body.get("message") or res.body.get("error") or res.body.get("apiErrorCode") or "")
        return VropsError(f"{what}失敗（HTTP {res.status}）{('：' + ih.scrub(msg, self._secrets())) if msg else ''}")

    def _call(self, method: str, path: str, *, params: dict | None = None, json_body=None, retry: bool = True) -> ih.HttpResult:
        if self._token is None:
            self.acquire_token()
        headers = {C.AUTH_HEADER: f"{C.AUTH_SCHEME} {self._token}", "Accept": "application/json"}
        try:
            res = self._t.request(method, self.base + path, headers=headers, params=params, json_body=json_body, timeout=self._timeout)
        except ih.TransportError as exc:
            raise VropsError(ih.scrub(exc, self._secrets())) from exc
        if res.status == 401 and retry:
            self._token = None
            self.acquire_token()
            return self._call(method, path, params=params, json_body=json_body, retry=False)
        return res

    # ---- 登入 ----
    def acquire_token(self) -> None:
        body = {"username": self._user, "password": self._pw}
        if self._source:
            body["authSource"] = self._source
        try:
            res = self._t.request("POST", self.base + C.TOKEN_ACQUIRE, headers={"Accept": "application/json", "Content-Type": "application/json"},
                                  json_body=body, timeout=self._timeout)
        except ih.TransportError as exc:
            raise VropsError(ih.scrub(exc, self._secrets())) from exc
        if res.status != 200 or not isinstance(res.body, dict) or not res.body.get("token"):
            if res.status in (401, 403):
                raise VropsError("登入失敗：帳號、密碼或驗證來源不正確")
            raise self._fail("登入", res)
        self._token = str(res.body["token"])

    def release_token(self) -> None:
        """用完就釋放權杖（盡力而為，失敗不影響結果）。"""
        if not self._token:
            return
        try:
            self._t.request("POST", self.base + C.TOKEN_RELEASE,
                            headers={C.AUTH_HEADER: f"{C.AUTH_SCHEME} {self._token}", "Accept": "application/json"}, timeout=10.0)
        except ih.TransportError:
            pass  # 釋放失敗只是權杖多活到逾時，不影響這次匯入結果
        self._token = None

    # ---- 查詢 ----
    def list_resources(self, kind: str) -> list[dict]:
        """某種資源的完整清單（自動翻頁）。第一頁失敗就丟錯；翻到一半失敗也丟錯（不回半份清單假裝完整）。"""
        out: list[dict] = []
        page = 0
        while page < C.MAX_PAGES:
            res = self._call("GET", C.RESOURCES, params={"resourceKind": kind, "page": page, "pageSize": C.PAGE_SIZE})
            if res.status != 200 or not isinstance(res.body, dict):
                raise self._fail(f"讀取 {kind} 清單", res)
            chunk = res.body.get("resourceList") or []
            out.extend(r for r in chunk if isinstance(r, dict))
            info = res.body.get("pageInfo") or {}
            total = info.get("totalCount")
            if not chunk or len(chunk) < C.PAGE_SIZE or (isinstance(total, int) and len(out) >= total):
                return out
            page += 1
        raise VropsError(f"讀取 {kind} 清單：分頁超過 {C.MAX_PAGES} 頁，疑似分頁資訊異常，已中止")

    def relationships(self, resource_id: str, rel_type: str) -> list[dict]:
        """某個資源的 PARENT 或 CHILD 關聯資源。"""
        res = self._call("GET", C.RESOURCE_RELATIONSHIPS.format(id=resource_id), params={"relationshipType": rel_type, "pageSize": C.PAGE_SIZE})
        if res.status != 200 or not isinstance(res.body, dict):
            raise self._fail("讀取關聯", res)
        return [r for r in (res.body.get("resourceList") or []) if isinstance(r, dict)]

    def latest_properties(self, ids: list[str], keys: tuple[str, ...]) -> dict[str, dict[str, str]]:
        """{資源 id: {屬性鍵: 值}}。分批查；某一批失敗會丟錯，讓呼叫端記成部分失敗。"""
        out: dict[str, dict[str, str]] = {}
        for i in range(0, len(ids), C.PROPERTY_CHUNK):
            chunk = ids[i:i + C.PROPERTY_CHUNK]
            res = self._call("POST", C.PROPERTIES_LATEST_QUERY, json_body={"resourceIds": chunk, "propertyKeys": list(keys)})
            if res.status != 200 or not isinstance(res.body, dict):
                raise self._fail("讀取屬性", res)
            for item in res.body.get("values") or []:
                rid = item.get("resourceId")
                contents = ((item.get("property-contents") or {}).get("property-content")) or []
                props: dict[str, str] = {}
                for pc in contents:
                    vals = pc.get("values") or []
                    if pc.get("statKey") and vals and vals[-1] not in (None, ""):
                        props[pc["statKey"]] = str(vals[-1])
                if rid:
                    out[rid] = props
        return out
