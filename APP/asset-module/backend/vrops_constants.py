"""Aria Operations（vROps）Suite API 的路徑與屬性鍵——集中在這一個檔，實機測試後要調整只改這裡。

⚠️ 全部依官方文件的回應格式推定，**尚未在公司的 Aria Operations 上實機驗證**（開發機連不到內網）。
屬性鍵（property key）在不同版本、不同 vCenter adapter 設定下會有差異，所以每個欄位給「候選清單」，
依序取第一個有值的；實機看過真實回應後，把不需要的候選刪掉、補上實際的鍵。
"""
from __future__ import annotations

API_PREFIX = "/suite-api/api"
TOKEN_ACQUIRE = f"{API_PREFIX}/auth/token/acquire"
TOKEN_RELEASE = f"{API_PREFIX}/auth/token/release"
RESOURCES = f"{API_PREFIX}/resources"
RESOURCE_RELATIONSHIPS = f"{API_PREFIX}/resources/{{id}}/relationships"
PROPERTIES_LATEST_QUERY = f"{API_PREFIX}/resources/properties/latest/query"
AUTH_HEADER = "Authorization"
AUTH_SCHEME = "vRealizeOpsToken"        # 標頭值：vRealizeOpsToken <token>

PAGE_SIZE = 1000
MAX_PAGES = 200                          # 保險絲：不讓壞掉的分頁欄位造成無窮迴圈
PROPERTY_CHUNK = 100                     # 一次查幾個資源的屬性

KIND_VM = "VirtualMachine"
KIND_HOST = "HostSystem"
KIND_CLUSTER = "ClusterComputeResource"
KIND_DATASTORE = "Datastore"
KIND_DATACENTER = "Datacenter"

# ---- 屬性鍵候選（依序取第一個有值的）----
P_BIOS_UUID = ("config|uuid", "summary|config|uuid", "sys|vm|uuid")           # RVTools 的「VM UUID」是 BIOS UUID，不可用 instance UUID 代替
P_GUEST_HOSTNAME = ("guest|hostName", "summary|guest|hostName", "config|guestHostName")
P_GUEST_IP = ("guest|ipAddress", "summary|guest|ipAddress", "net:default|ip_address")
P_GUEST_OS = ("config|guestFullName", "summary|guest|guestFullName", "summary|config|guestFullName")
P_POWER = ("summary|runtime|powerState", "runtime|powerState")
P_PARENT_HOST = ("summary|parentHost",)
P_PARENT_CLUSTER = ("summary|parentCluster",)
P_PARENT_DATASTORE = ("summary|parentDatastore",)
P_PARENT_DATACENTER = ("summary|parentDatacenter",)
P_CPU_COUNT = ("config|hardware|num_Cpu", "summary|config|numCpu")
P_MEM_KB = ("config|hardware|memoryKB",)
P_MEM_MB = ("summary|config|memorySize",)
P_DISK_GB = ("config|hardware|disk_Space", "summary|config|diskSpace")      # 單位 GB（推定）
P_ESX_VERSION = ("summary|version",)
P_ESX_BUILD = ("summary|build",)
P_DS_CAPACITY_GB = ("capacity|total_capacity", "summary|capacity")           # 單位 GB（推定）
P_DS_FREE_GB = ("capacity|available_space", "summary|freeSpace")
P_DS_TYPE = ("summary|type", "config|type")
P_DS_URL = ("summary|url",)

ALL_VM_KEYS = (P_BIOS_UUID + P_GUEST_HOSTNAME + P_GUEST_IP + P_GUEST_OS + P_POWER + P_PARENT_HOST
               + P_PARENT_CLUSTER + P_PARENT_DATASTORE + P_PARENT_DATACENTER + P_CPU_COUNT + P_MEM_KB + P_MEM_MB + P_DISK_GB)
ALL_HOST_KEYS = P_ESX_VERSION + P_ESX_BUILD + P_PARENT_CLUSTER + P_PARENT_DATACENTER
ALL_DS_KEYS = P_DS_CAPACITY_GB + P_DS_FREE_GB + P_DS_TYPE + P_DS_URL

GB_TO_MIB = 1024
KB_TO_MIB = 1 / 1024
