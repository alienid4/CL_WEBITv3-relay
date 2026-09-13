"""③ facts 收集器：指令輸出解析（注入假 runner，不打真 SSH，確定性）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import facts_collector  # noqa: E402


def _linux_runner(host, cmd):
    if cmd == "hostname":
        return "demo-host\n"
    if "os-release" in cmd:
        return "Rocky Linux 9.7 (Blue Onyx)\n"
    if "product_name" in cmd:
        return "VMware Virtual Platform\n"
    if "product_serial" in cmd:
        return ""  # sysctl 拿不到（需 root）
    if "detect-virt" in cmd:
        return "vmware\n"
    if "class/net" in cmd:
        return "00:0c:29:56:4e:98\n"
    return ""


def test_linux收集解析():
    f = facts_collector.collect(_linux_runner, "10.0.0.1", "linux")
    assert f["hostname"] == "demo-host"
    assert f["os"] == "Rocky Linux 9.7 (Blue Onyx)"
    assert f["mac"] == "00:0c:29:56:4e:98"
    assert f["device_model"] == "VMware Virtual Platform"
    assert f["is_vm"] == 1                # vmware → 虛擬
    assert f["hw_serial"] is None         # 拿不到就留 None，不假裝


def test_實體機is_vm為0():
    f = facts_collector.collect(lambda h, c: "none" if "detect-virt" in c else "x", "h", "linux")
    assert f["is_vm"] == 0


def test_單一指令失敗不整組掛掉():
    def flaky(host, cmd):
        if "product_serial" in cmd:
            raise OSError("boom")
        return "x"
    f = facts_collector.collect(flaky, "h", "linux")
    assert f["hw_serial"] is None
    assert f["hostname"] == "x"           # 其他欄位照收


def test_未支援平台要raise():
    with pytest.raises(ValueError):
        facts_collector.collect(lambda h, c: "", "h", "solaris")


def test_偵測平台():
    assert facts_collector.detect_platform([3389, 445]) == "windows"
    assert facts_collector.detect_platform([22]) == "linux"


def test_序號要透過sudo讀_否則永遠是空的():
    """實測踩到：3 台已納管機器收得到 OS，但 hw_serial 全空。

    原因：/sys/class/dmi/id/product_serial 權限是 0400（只有 root 讀得到），
    直接 cat 一定拿到空字串。納管腳本已經替收集帳號開了「只能讀該目錄」的 sudo 規則，
    收集端必須真的用它，否則等於白開。
    """
    import facts_collector as fc

    assert "sudo -n" in fc.LINUX_CMDS["hw_serial"], "序號沒走 sudo，一定收不到"
    # 沒有 sudo 權限的機器要能退回直接讀，不是整個失敗
    assert "||" in fc.LINUX_CMDS["hw_serial"]

    # 用假 runner 驗整條路徑：指令有送出、值有被收進來
    sent = []

    def runner(host, cmd):
        sent.append(cmd)
        return "VMware-56 4d 04 ff" if "product_serial" in cmd else "x"

    facts = fc.collect(runner, "1.2.3.4", "linux")
    assert facts["hw_serial"] == "VMware-56 4d 04 ff"
    assert any("sudo -n" in c and "product_serial" in c for c in sent)
