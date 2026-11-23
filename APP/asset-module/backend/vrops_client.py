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
    """Aria Operations 回報的錯誤或不認得的回應。訊息已清掉機密。
    category（2.069.0，給每日快照分類用）：auth＝登入被拒（401／403 於登入）、timeout、tls、connect、server（5xx）、client（其他非 200）、format（200 但不是 JSON 物件）、other。
    status＝HTTP 狀態碼（沒有就 None）；what＝固定的動作名稱（不含 vROps 原始訊息）。"""

    def __init__(self, msg="", category: str = "other", status: int | None = None, what: str = ""):
        super().__init__(msg)
        self.category, self.status, self.what = category, status, what


def _transport_category(exc) -> str:
    t = str(exc)
    if "逾時" in t:
        return "timeout"
    if "TLS" in t or "憑證" in t:
        return "tls"
    return "connect"


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
        cat = "format" if res.status == 200 else "server" if res.status >= 500 else "client"
        return VropsError(f"{what}失敗（HTTP {res.status}）{('：' + ih.scrub(msg, self._secrets())) if msg else ''}", category=cat, status=res.status, what=what)

    def _call(self, method: str, path: str, *, params: dict | None = None, json_body=None, retry: bool = True) -> ih.HttpResult:
        if self._token is None:
            self.acquire_token()
        headers = {C.AUTH_HEADER: f"{C.AUTH_SCHEME} {self._token}", "Accept": "application/json"}
        try:
            res = self._t.request(method, self.base + path, headers=headers, params=params, json_body=json_body, timeout=self._timeout)
        except ih.TransportError as exc:
            raise VropsError(ih.scrub(exc, self._secrets()), category=_transport_category(exc)) from exc
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
            raise VropsError(ih.scrub(exc, self._secrets()), category=_transport_category(exc), what="登入") from exc
        if res.status != 200 or not isinstance(res.body, dict) or not res.body.get("token"):
            if res.status in (401, 403):
                raise VropsError("登入失敗：帳號、密碼或驗證來源不正確", category="auth", status=res.status, what="登入")
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

    def close(self) -> None:
        """關掉底層 HTTP 連線（用完一定呼叫；盡力而為）。"""
        try:
            self._t.close()
        except Exception:  # noqa: BLE001 - 關連線失敗不影響結果
            pass

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

    # ---- 每日 VMware 快照用的單次 GET（2.069.0）。每個方法只送一個請求、回原始 JSON；翻頁、分批、間隔、重試、上限都在呼叫端
    #      （aria_dailycheck_direct）。非 200 或不是 JSON 物件一律丟 VropsError（訊息已清機密）。只有 GET，不寫入 Aria。
    def _get_body(self, what: str, path: str, params) -> dict:
        res = self._call("GET", path, params=params)
        if res.status != 200 or not isinstance(res.body, dict):
            raise self._fail(what, res)
        return res.body

    def adapters(self, adapter_kind_key: str = "VMWARE") -> dict:
        """GET /adapters?adapterKindKey=…：每個 vCenter 一個收集器實例（含最後收集時間）。"""
        return self._get_body("讀取收集器清單", C.ADAPTERS, {"adapterKindKey": adapter_kind_key})

    def resources_page(self, adapter_kind: str, resource_kind: str, page: int, page_size: int) -> dict:
        """GET /resources?adapterKind=…&resourceKind=…（一頁）。"""
        return self._get_body(f"讀取 {resource_kind} 清單",
                              C.RESOURCES, {"adapterKind": adapter_kind, "resourceKind": resource_kind, "page": page, "pageSize": page_size})

    def resources_by_ids(self, ids: list[str], page_size: int) -> dict:
        """GET /resources?resourceId=…&resourceId=…（一批）。"""
        return self._get_body("讀取資源", C.RESOURCES, {"resourceId": list(ids), "pageSize": page_size})

    def properties_get(self, ids: list[str]) -> dict:
        """GET /resources/properties?resourceId=…（一批資源的最新屬性）。"""
        return self._get_body("讀取屬性", C.RESOURCES_PROPERTIES, {"resourceId": list(ids)})

    def stats_latest(self, ids: list[str], keys: list[str]) -> dict:
        """GET /resources/stats/latest?resourceId=…&statKey=…（一批資源的最新指標值＋時間）。"""
        return self._get_body("讀取最新指標", C.RESOURCES_STATS_LATEST, {"resourceId": list(ids), "statKey": list(keys)})

    def alerts_page(self, page: int, page_size: int, active_only: bool = True) -> dict:
        """GET /alerts?activeOnly=true（一頁）。"""
        return self._get_body("讀取警示", C.ALERTS, {"activeOnly": "true" if active_only else "false", "page": page, "pageSize": page_size})

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
