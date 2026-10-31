"""Q91：端點權限分級登記簿（誰能呼叫哪一支）。

三級：viewer 只能看 < user 一般 < admin 管理員（auth.LEVELS）。
**所有**判定集中在這一份，api.py 掛路由時依這裡替每支端點加一道 `require_level(...)` 依賴。
這樣新增端點時只要在這裡登記一次；忘了登記的非 GET 端點：
  · 執行期一律當 admin 才能呼叫（fail-closed），
  · tests/asset_module/test_auth_coverage.py 會直接紅燈，逼你做一次明確的決定。

規則：
  · GET：預設 viewer 就能看。例外 → ADMIN_READ（設定、金鑰、操作紀錄、備份、檔案傳送…）。
  · 匯出／下載（回應是檔案的端點，含 POST 版）→ EXPORT_LEVEL：**viewer 一律不能匯出**；
    大量匯出或含帳號資料 → admin，其餘 → user。
  · 非 GET：必須明確列在 ADMIN_WRITE／USER_WRITE／VIEWER_WRITE 其中一份。
    VIEWER_WRITE ＝「用 POST 但其實唯讀」的查詢，與「只動自己偏好」的寫入。
  · 登入停用（auth_disabled）時假 session 的 role 是 admin，所以**既有功能照舊全開**；
    唯一的例外是各模組自己 fail-closed 的閘門（檔案傳送、Aria、WhatsUp：登入停用就拒絕）。

跟舊判定的對應：原本零散的 `_can_edit_nav`（帳號名＝admin）、傳送／整合模組的 is_admin，
現在都改成讀同一個 users.role，所以「帶 admin 閘門的端點」在這裡一律登記為 ADMIN。
"""
from __future__ import annotations

VIEWER, USER, ADMIN = "viewer", "user", "admin"

# ---- 不掛等級依賴的端點：自己有別的驗證，或刻意公開（跟 test_auth_coverage.PUBLIC 同步）----
PUBLIC_ROUTES = frozenset({
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/version"),
    ("GET", "/api/onboard/fetch/{token}"),     # 憑一次性權杖下載納管腳本
})
#: push agent 用 X-Agent-Key（機器對機器）驗證，不是人的 session。
AGENT_ROUTES = frozenset({
    ("POST", "/api/agent/facts"),
})

# ---- GET 中需要 admin 才能看的（設定、金鑰、操作紀錄、備份、對外整合…）----
ADMIN_READ_EXACT = frozenset({
    "/api/activity", "/api/activity/summary", "/api/credentials", "/api/collector-key", "/api/export-key",
    "/api/accounts/collect-config", "/api/diagnostics", "/api/cmdb/pull",
    "/api/access/users", "/api/access/export-log", "/api/access/level-audit",
    "/api/machine-state/compare",   # 整台狀態聚合上線前評估（逐台變動清單），admin 才能看
})
# 對外整合（/api/integrations/*）的 GET 同理維持「登入即可看」（密碼一律遮罩）；設定與執行才限 admin。
# 通知／排程類設定（digest／eos-notify／vuln-notify／collect dispatch／Windows 設定）的 GET 維持「登入即可看」
# （既有行為：一般帳號的設定頁要顯示目前值；寫入才限 admin）。
# 整份帳號清冊的 JSON 讀取：只能看的帳號不能讀（Q91 M4）。要看數量請用摘要端點（/api/accounts/coverage、/api/account-audit/status…）。
# 超過門檻列數會寫 export_log（api.py _bulk_json_middleware）。
USER_READ_EXACT = frozenset({
    "/api/accounts", "/api/accounts/findings", "/api/accounts/hosts", "/api/accounts/sudo-rules",
    "/api/accounts/by-username", "/api/accounts/inventoried-hosts",
    "/api/number-proof/diff",     # W221-007 完整差異清單（逐台主機名／IP），一般帳號以上才能讀
})
ADMIN_READ_PREFIX = (
    "/api/admin/", "/api/backup/", "/api/transfer/", "/api/patch/blob",
)

# ---- 匯出／下載：(方法, 路徑) → 最低等級（viewer 一律不可）----
_ADMIN_EXPORTS = [
    ("GET", "/api/contacts/roster/template"),     # 值班電話名冊範本（只有管理員）
    # 帳號資料
    ("GET", "/api/accounts/by-username/export"), ("GET", "/api/accounts/export"), ("GET", "/api/accounts/findings/export"),
    ("GET", "/api/accounts/inventoried-hosts/export"), ("GET", "/api/accounts/matrix/export"),
    ("GET", "/api/inventory/{kind}/hosts/export"),
    # 整庫／大量
    ("GET", "/api/backup/dump"), ("GET", "/api/export"), ("GET", "/api/import/{source}/export/dump"),
    ("GET", "/api/import/{source}/export/excel"), ("GET", "/api/import/{source}/export/original"),
    ("GET", "/api/reports/hardware-master/export"), ("GET", "/api/reports/host-sources/export"),
    ("GET", "/api/monthly-report/export"), ("GET", "/api/v2/machine/export.xlsx"), ("GET", "/api/relocation/export"),
    ("GET", "/api/onboard/managed/export"), ("GET", "/api/onboard/batch-auto/export"),
    ("GET", "/api/onboard/failures/export"), ("GET", "/api/stats/onboard-matrix/export"),
    ("GET", "/api/measured-mismatch/export"), ("GET", "/api/measured-mismatch/cia-report"),
    ("GET", "/api/existence/cia-pending/export"), ("GET", "/api/cia-pending/export"),
    ("GET", "/api/config-audit/export"), ("GET", "/api/system/collect-log/export"),
    ("GET", "/api/segments/export"), ("GET", "/api/connections/suggest-segments"),
    ("GET", "/api/v2/nmon/raw"), ("GET", "/api/san/archive/{archive_id}/text"),
    # 納管腳本／部署工具、選單設定
    ("GET", "/api/onboard/bundle/download"), ("GET", "/api/scripts/{name}/download"),
    # 經 _xlsx_response／rack_inventory 這類 helper 回檔案的（靜態掃 Content-Disposition 看不到，2.014.3 獨立審查補登）
    ("GET", "/api/account-audit/export"), ("GET", "/api/account-audit/template"),
    ("GET", "/api/rack-diagram/export.xlsx"), ("GET", "/api/rack-diagram/imports/{iid}/cia-fix.xlsx"),
    ("GET", "/api/rack-audits/{audit_id}/report.xlsx"),
    ("GET", "/api/nav-layout/export"),
]
_USER_EXPORTS = [
    ("GET", "/api/blast/impact/csv"), ("GET", "/api/blast/snapshot/{snapshot_id}/csv"),
    ("GET", "/api/racks/position/pending-cia.xlsx"), ("GET", "/api/racks/field-edits.xlsx"),
    ("GET", "/api/segments/export-template"), ("GET", "/api/import/dynassets/export-template"),
    ("GET", "/api/import/{source}/export-template"), ("GET", "/api/reports/system-category/template"),
    ("GET", "/api/assets/{asset_serial}/provision-attachment-file"), ("GET", "/api/documents/{doc_id}/download"),
    ("GET", "/api/normalize/pending/export"), ("GET", "/api/reports/scan-gaps/export"),
    ("GET", "/api/incidents/{eid}/export.csv"), ("GET", "/api/v2/incident/{eid}/contacts.csv"),
    ("GET", "/api/v2/inv/{kind}/tasks/{tid}/report.xlsx"), ("GET", "/api/tools/alive-check/runs/{run_id}/export"),
    ("GET", "/api/eos/export"), ("POST", "/api/nav-layout/mapping.xlsx"),
    # 畫面表格匯出（前端把「目前畫面那幾列」丟來轉 xlsx）：端點內另有「大量／含帳號欄位 → admin」的判斷
    ("POST", "/api/export/table-xlsx"),
]
EXPORT_LEVEL: dict[tuple[str, str], str] = {
    **{k: ADMIN for k in _ADMIN_EXPORTS},
    **{k: USER for k in _USER_EXPORTS},
}

# ---- 非 GET（會改東西／觸發動作）----
def _routes(method: str, *paths: str) -> set[tuple[str, str]]:
    return {(method, p) for p in paths}


ADMIN_WRITE: frozenset[tuple[str, str]] = frozenset(
    # 帳號資料／收集身分／金鑰
    _routes("POST", "/api/account-audit/import", "/api/accounts/collect", "/api/ad/import",
            "/api/collect-account/migration/probe", "/api/collect-account/migration/revert",
            "/api/collect-account/migration/switch", "/api/collector-key/import", "/api/export-key/generate")
    | _routes("PUT", "/api/accounts/collect-config", "/api/accounts/exclude", "/api/export-key/encryption",
              "/api/export-key/recipient")
    # 匯入
    | _routes("POST", "/api/business-systems/import", "/api/business-systems/import-class", "/api/segments/import",
              "/api/documents/import", "/api/import/dump/preview", "/api/import/dynassets", "/api/import/excel",
              "/api/import/rvtools", "/api/normalize/pending/import", "/api/reports/system-category/import",
              "/api/contracts/import", "/api/contracts/preview", "/api/rack-diagram/import",
              "/api/contacts/roster/preview", "/api/contacts/roster/import",
              "/api/racks/excel-import/preview", "/api/racks/excel-import/apply", "/api/relocation/import",
              "/api/san/import", "/api/assets/scan/import", "/api/classify/seed")
    | _routes("PUT", "/api/import/field-mapping", "/api/night/settings")
    # 手動建資產、探索、收集／掃描觸發
    | _routes("POST", "/api/assets/manual", "/api/assets/scan/discover", "/api/host-spec/collect",
              "/api/host-spec/reparse", "/api/host-spec/rebuild-collect", "/api/host-schedule/collect",
              "/api/san/collect", "/api/san/reparse", "/api/services/collect", "/api/software/collect",
              "/api/hmc/collect", "/api/config-audit/collect", "/api/config-audit/import", "/api/drift/recheck",
              "/api/manage-state/collect", "/api/manage-state/refresh", "/api/scan/run", "/api/scan/scope/apply",
              "/api/collect/agent-package", "/api/collect/dispatch", "/api/collect/dispatch/{run_id}/mark-interrupted",
              "/api/collect/dispatch/{run_id}/stop", "/api/connections", "/api/connections/batch-segments",
              "/api/connections/{connection_id}/test", "/api/vcenter-autoimport/run",
              "/api/health-check/baseline/run", "/api/health-check/run", "/api/system/collector-selfheal",
              "/api/tools/ssh-probe", "/api/housekeeping/scan", "/api/ci/rebuild",
              "/api/scan-shells/apply", "/api/scan-shells/restore/{audit_id}")
    | _routes("PUT", "/api/blast/observed-collect", "/api/ci/schedule", "/api/collect/dispatch/gap",
              "/api/collect/dispatch/settings", "/api/collect/dispatch/weekly", "/api/connections/{connection_id}",
              "/api/scan/schedule", "/api/scan/scope", "/api/scan/scope/policy", "/api/vcenter-autoimport",
              "/api/config-audit/windows-settings")
    | _routes("DELETE", "/api/connections/{connection_id}")
    | _routes("PATCH", "/api/connections/{connection_id}/enabled", "/api/connections/{connection_id}/scan-time")
    # 納管、憑證、自動納管
    | _routes("POST", "/api/onboard", "/api/onboard/windows", "/api/onboard/windows/revoke", "/api/onboard/revoke",
              "/api/onboard/verify", "/api/onboard/precheck", "/api/onboard/batch-auto",
              "/api/onboard/batch-auto/preview", "/api/onboard/batch-probe", "/api/onboard/paste/issue",
              "/api/onboard/post-collect-batch", "/api/onboard/post-collect-queue/cancel",
              "/api/onboard/post-collect-queue/requeue-lost", "/api/onboard/post-collect/{asset_serial}",
              "/api/onboard-exempt", "/api/onboard-exempt/remove", "/api/agent/stage",
              "/api/auto-onboard/run", "/api/auto-onboard/segments",
              "/api/credentials", "/api/credentials/{name}/test")
    | _routes("PUT", "/api/credentials/{name}")
    | _routes("DELETE", "/api/credentials/{name}", "/api/auto-onboard/segments/{seg_id}")
    | _routes("PATCH", "/api/auto-onboard/enabled", "/api/auto-onboard/segments/{seg_id}/enabled")
    # 設定、重設、備份還原、功能開關、選單編輯器
    | _routes("PUT", "/api/admin/auth-disabled", "/api/admin/write-guard", "/api/backup/offsite", "/api/digest/settings",
              "/api/eos-notify/settings", "/api/vuln-notify/settings", "/api/risk/settings", "/api/data-fix/required",
              "/api/feature-flags/{module_key}", "/api/nav-layout", "/api/model-specs", "/api/model-vendors",
              "/api/patch/blob/{key}", "/api/transfer/settings", "/api/integrations/vrops",
              "/api/integrations/whatsup", "/api/count-bridge/override", "/api/access/users/{username}/level")
    | _routes("POST", "/api/access/users", "/api/admin/reset-inventory", "/api/backup/restore", "/api/backup/run",
              "/api/nav-layout/apply-default", "/api/nav-layout/import", "/api/nav-layout/restore",
              "/api/digest/test-mail", "/api/eos-notify/run", "/api/vuln-notify/preview", "/api/vuln-notify/run",
              "/api/vulns/import", "/api/integrations/vrops/run", "/api/integrations/vrops/test",
              "/api/integrations/whatsup/poll", "/api/integrations/whatsup/test")
    | _routes("DELETE", "/api/patch/blob/{key}")
    # 檔案傳送模組（本身另有 fail-closed 閘門：登入停用時一律拒絕）
    | _routes("POST", "/api/transfer/commands", "/api/transfer/jobs", "/api/transfer/jobs/{job_id}/retry-failed",
              "/api/transfer/jobs/{job_id}/reverify", "/api/transfer/jobs/{job_id}/stop", "/api/transfer/library",
              "/api/transfer/preflight", "/api/transfer/pull", "/api/transfer/audit-digest")
    | _routes("DELETE", "/api/transfer/library/{file_id}")
    # 機櫃：原本就是「限管理員」（_can_edit_nav）的寫入，維持
    | _routes("PUT", "/api/racks/position", "/api/racks/position/bulk", "/api/racks/state/bulk",
              "/api/racks/reservations/{rid}/cancel", "/api/racks/field-edit", "/api/racks/position/move",
              "/api/rack-audits/{audit_id}/rack")
    | _routes("POST", "/api/racks/reservations", "/api/racks/add-device", "/api/racks/overlaps/decide",
              "/api/racks/overlaps/{decision_id}/undo", "/api/racks/position/moves/{move_id}/undo",
              "/api/rack-audits", "/api/rack-audits/{audit_id}/close", "/api/rack-audits/{audit_id}/diffs/act",
              "/api/rack-audits/{audit_id}/diffs/status", "/api/rack-audits/{audit_id}/upload",
              "/api/v2/inv/{kind}/tasks", "/api/v2/inv/{kind}/tasks/{tid}/diffs/status")
    | _routes("DELETE", "/api/racks/field-edit")
)

USER_WRITE: frozenset[tuple[str, str]] = frozenset(
    # 帳號盤點的處置與標註
    _routes("PUT", "/api/accounts/findings/disposition", "/api/accounts/kind", "/api/accounts/note")
    # 資產資料維護與標註
    | _routes("PUT", "/api/assets/{asset_serial}", "/api/classify", "/api/os-type-overrides")
    | _routes("DELETE", "/api/os-type-overrides", "/api/assets/{asset_serial}/tags/{tag}",
              "/api/normalize/alias/{alias_id}",
              "/api/normalize/canonical-override/{kind}/{old_canonical}", "/api/eos/category-override/{canonical}")
    | _routes("POST", "/api/asset-tags/batch", "/api/asset-tags/by-serial", "/api/assets/adopt",
              "/api/assets/batch-status", "/api/assets/duplicates/dismiss", "/api/assets/duplicates/release",
              "/api/assets/{asset_serial}/provision-attachment", "/api/assets/{asset_serial}/tags",
              "/api/cia-pending/synced", "/api/adopt/move-tab", "/api/normalize/alias",
              "/api/normalize/canonical-override", "/api/eos/category-override",
              "/api/merge-reviews/batch-merge", "/api/changes/snapshot")
    | _routes("POST", "/api/issues/mark-read") | _routes("PATCH", "/api/issues/{issue_id}")
    # 架構／依賴／系統
    | _routes("PUT", "/api/arch/ocp/cluster", "/api/arch/ocp/role", "/api/blast/checklist/item/{item_id}",
              "/api/systems/class", "/api/systems/{system_id}", "/api/racks/state")
    | _routes("POST", "/api/blast/checklist", "/api/blast/snapshot", "/api/deps", "/api/systems")
    | _routes("DELETE", "/api/deps/{dep_id}", "/api/systems/{system_id}")
    # 文件、掃描缺口、報表備註、聯絡人、事件
    | _routes("POST", "/api/documents/{doc_id}/bind", "/api/documents/{doc_id}/review",
              "/api/reports/system-group/snapshots", "/api/rack-diagram/rows/{row_id}/review",
              "/api/data-quality/person-merge", "/api/drift/{drift_id}/disposition", "/api/golive/{asset_serial}/item",
              "/api/golive/{asset_serial}/pass", "/api/tools/alive-check", "/api/tools/alive-check/batch",
              "/api/incidents", "/api/incidents/{eid}/alerts", "/api/incidents/{eid}/check",
              "/api/incidents/{eid}/close", "/api/incidents/{eid}/note", "/api/incidents/{eid}/report",
              "/api/v2/host-monthly/nmon")
    | _routes("PUT", "/api/reports/scan-gaps/note", "/api/reports/scan-gaps/visibility",
              "/api/reports/system-group/note", "/api/contacts/entry", "/api/contacts/oncall",
              "/api/incidents/tasks/{tid}", "/api/incidents/{eid}/scope", "/api/v2/host-monthly/note")
)

VIEWER_WRITE: frozenset[tuple[str, str]] = frozenset(
    # 用 POST 但其實是查詢（不改資料）
    _routes("POST", "/api/assets/batch-lookup", "/api/assets/scope-preview", "/api/onboard/audit/latest-by-ip",
            "/api/data-quality/person-merge/preview")
    # 只動自己的畫面偏好
    | _routes("PUT", "/api/nav/favorites", "/api/nav/v2-groups")
)


def classification(method: str, path: str) -> str | None:
    """非 GET 端點的明確分級；沒登記回 None。"""
    key = (method, path)
    if key in ADMIN_WRITE:
        return ADMIN
    if key in USER_WRITE:
        return USER
    if key in VIEWER_WRITE:
        return VIEWER
    return None


def is_exempt(method: str, path: str) -> bool:
    return (method, path) in PUBLIC_ROUTES or (method, path) in AGENT_ROUTES


def level_for(method: str, path: str) -> str | None:
    """這支端點要的最低等級；None ＝不掛等級依賴（公開／agent 自己驗證）。

    沒登記的非 GET → ADMIN（fail-closed）。"""
    method = method.upper()
    if is_exempt(method, path):
        return None
    if (method, path) in EXPORT_LEVEL:
        return EXPORT_LEVEL[(method, path)]
    if method in ("GET", "HEAD"):
        if path in USER_READ_EXACT:
            return USER
        if path in ADMIN_READ_EXACT or path.startswith(ADMIN_READ_PREFIX):
            return ADMIN
        return VIEWER
    return classification(method, path) or ADMIN


def all_registered_writes() -> set[tuple[str, str]]:
    return set(ADMIN_WRITE) | set(USER_WRITE) | set(VIEWER_WRITE)
