"""S3：掃描來源介面 ＋ 家裡開發用的 Mock 實作。

契約架構（MVP契約「架構」一節）：
  Server／虛擬主機 → VMware vCenter API（首選）
  SAN／Core／一般 Switch → SNMP 查詢 + LLDP/CDP

家裡沒有真實 vCenter/SNMP 可連（D30/契約開發流程已定案：家裡用假資料 mock 寫邏輯），
所以這裡先實作 Mock 版本；正式串接時只要新增一個實作同樣介面的 class（例如
RealVCenterSource 用 pyVmomi），run_scan() 的邏輯完全不用改。
"""
from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ScanResult:
    hostname: str
    ip: str
    device_model: str
    is_vm: bool
    segment: str
    # S16 指紋（被動、不必登入該機器）。舊來源不填＝維持 None，不影響既有行為。
    mac: str | None = None
    mac_vendor: str | None = None
    open_ports: str | None = None      # 逗號分隔字串，如 "22,3389"
    ttl: int | None = None
    os_guess: str | None = None


class ScanSource(ABC):
    """掃描來源的共同介面：vCenter、SNMP、之後其他來源都實作這個。"""

    name: str

    @abstractmethod
    def scan(self) -> list[ScanResult]:
        """回傳這個來源掃到的結果。連不到/逾時時要 raise，不要吞掉假裝成功。"""
        raise NotImplementedError


class MockVCenterSource(ScanSource):
    """家裡開發用：模擬 vCenter API 回應，固定回傳一批虛擬機清單。"""

    name = "vCenter(mock)"

    def __init__(self, fail: bool = False):
        self._fail = fail

    def scan(self) -> list[ScanResult]:
        if self._fail:
            raise ConnectionError("mock vCenter 連線逾時")
        return [
            ScanResult("mock-db-app-07", "10.20.30.41", "Dell PowerEdge R740", True, "機房A"),
            ScanResult("mock-vm-web-14", "10.20.30.55", "VM", True, "機房A"),
            ScanResult("mock-vm-app-22", "10.20.30.63", "VM", True, "機房B"),
        ]


class MockSNMPSource(ScanSource):
    """家裡開發用：模擬 SNMP+LLDP/CDP 拓樸查詢，回傳一批交換器清單。"""

    name = "SNMP(mock)"

    def __init__(self, fail: bool = False):
        self._fail = fail

    def scan(self) -> list[ScanResult]:
        if self._fail:
            raise ConnectionError("mock SNMP 網段連不到")
        return [
            ScanResult("mock-sw-core-01", "10.20.11.1", "Core Switch", False, "機房A"),
            ScanResult("mock-sw-access-02", "10.20.11.2", "Access Switch", False, "機房B"),
        ]


def random_flaky_sources() -> list[ScanSource]:
    """測試/展示用：隨機讓其中一個來源模擬連線失敗，驗證「掃描健康度」邏輯不會誤判。"""
    fail_vcenter = random.random() < 0.15
    fail_snmp = random.random() < 0.15
    return [MockVCenterSource(fail=fail_vcenter), MockSNMPSource(fail=fail_snmp)]
