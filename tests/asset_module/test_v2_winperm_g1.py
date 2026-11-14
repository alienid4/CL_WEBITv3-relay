"""17 主項 G1：舊 2-1 WinRM 頁簽的「Windows 收集權限」一行，V2 納管作業的 WinRM 路線要看得到（同一元件、同一支唯讀 API）。"""
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"


def test_V2_待納管_WinRM路線有收集權限面板():
    s = (FE / "pages" / "v2" / "onboard.vue").read_text(encoding="utf-8")
    i = s.index("<WinPermPanel")
    line = s[s.rfind("\n", 0, i):s.find("\n", i)]
    assert "pendTab === 'winrm'" in line, "只在 WinRM 路線顯示"
    assert "pick-ip" in line and "ip" in line, "點台數明細的 IP 要能篩到該台"


def test_面板只呼叫唯讀權限API():
    s = (FE / "components" / "WinPermPanel.vue").read_text(encoding="utf-8")
    assert "/api/onboard/windows/permissions" in s
    assert "method:" not in s, "面板不寫入"
