"""WhatsUp Gold REST API 的路徑、欄位名與告警狀態判定——集中在這一個檔，實機測試後要調整只改這裡。

⚠️ 依官方文件推定；2026-11-03 已在公司 WhatsUp 實測其中三條（標「已實測」），其餘仍未實機驗證（開發機連不到內網）。
"""
from __future__ import annotations

TOKEN_PATH = "/api/v1/token"                         # OAuth2 password grant（表單：grant_type／username／password）
#: 2026-11-03 21:40 公司 WhatsUp 實測：/api/v1/devices/-?view=overview 回 400；改用群組 -1（全部裝置）的設備清單，
#: 回 data.devices[]（id、name、hostName、networkAddress、worstState、bestState、totalActiveMonitors、totalActiveMonitorsDown），limit 上限 200。
DEVICES_OVERVIEW_PATH = "/api/v1/device-groups/-1/devices"   # 搭配 view=overview：全部裝置與最佳／最差狀態（已實測）
GROUP_STATUS_PATH = "/api/v1/device-groups/-1/status"        # 群組狀態總數＋lastStateChangeUtc（已實測，0.76 秒）
DEVICE_MONITORS_STATUS_PATH = "/api/v1/devices/{id}/monitors/-"   # 搭配 view=status：每個監控項目的狀態文字（已實測；view=details／summary 回 400）
DEVICES_OVERVIEW_PARAMS = {"view": "overview"}
GROUP_DEVICES_PATH = "/api/v1/device-groups/-/devices/-"   # 搭配 view=card：每台裝置所屬群組等資訊
GROUP_DEVICES_PARAMS = {"view": "card"}
DEVICE_STATUS_PATH = "/api/v1/devices/{id}/status"   # 單台裝置目前的狀態與有問題的監視項目

PAGE_LIMIT = 200
MAX_PAGES = 500                                       # 保險絲

#: 算「目前告警」的狀態（小寫比對）。Up／Maintenance／Unknown 不算告警。
ALERT_STATES = frozenset({"down", "critical", "warning", "unresponsive", "error", "major", "minor"})
#: 這些狀態顯示給人看的中文
STATE_LABEL = {"down": "當機", "critical": "嚴重", "major": "嚴重", "warning": "警告", "minor": "輕微",
               "unresponsive": "無回應", "error": "錯誤"}

# 回應欄位名候選（依序取第一個有值的）
F_ID = ("id", "deviceId")
F_NAME = ("name", "displayName", "hostName")
F_IP = ("networkAddress", "ipAddress", "address")
F_STATE = ("worstState", "bestState", "state")
F_GROUP = ("groupName", "deviceGroupName", "group")
F_SINCE = ("lastStateChange", "stateChangeTime", "since")
F_PROBLEM_LIST = ("downActiveMonitors", "activeProblems", "problems", "monitors")
F_PROBLEM_NAME = ("name", "monitorName", "description")
