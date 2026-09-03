"""沒裝 sudo 的 Linux 要能納管，而且要講清楚少收了什麼。

2026-08-28 公司實測 Debian 11：帳號建好、公鑰佈好，然後倒在
`/etc/sudoers.d/webit3scan: No such file or directory`——那個目錄不存在，
因為那台**根本沒裝 sudo**（Debian 最小安裝的預設，不是特例）。

AIX 版早就寫過同一段推理（`build_aix_script` 的 docstring），Linux 版沒套用。

這裡守兩件事：
1. 沒有 sudo 不該讓整個納管失敗——帳號與公鑰都佈好了，九成的收集項目能動
2. **成功不一定是完整的成功**。序號與機型收不到，這件事要在納管當下就講，
   不然以後有人看到序號空白會去查收集是不是壞了，實際上納管那一刻就決定了
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import onboard_engine as eng  # noqa: E402

PUBKEY = "ssh-ed25519 AAAATESTKEY webit3-collector"
# ⚠️ 不要用 YOUR_SERVER_IP：去識別化替換表會把它換成佔位字串 YOUR_SERVER_IP，
# 而 validate_collector_ip() 的職責就是擋掉佔位值（8/16 踩過：佔位值寫進
# authorized_keys 的 from= 之後金鑰永遠被拒，但納管畫面顯示成功）。
# 結果就是「原始碼測試綠、去識別化產出物測試紅」，relay CI 擋下來但本機看不到。
# 10.0.0.221 不在替換表裡，跟 test_onboard_aix.py 用的是同一個。
IP = "10.0.0.221"


def _script() -> str:
    return eng.build_linux_script(PUBKEY, IP)


def test_腳本不會無條件寫_sudoers():
    """無條件 `> /etc/sudoers.d/...` 在沒有那個目錄的機器上必定失敗，
    而且是失敗在腳本中段——帳號已經建了，狀態變成半套。"""
    s = _script()
    assert "command -v sudo" in s, "沒有先確認 sudo 存在就寫 sudoers"
    assert "[ -d /etc/sudoers.d ]" in s, "沒有確認 sudoers.d 目錄存在"
    # 寫入那行必須在條件式底下（縮排），不是頂格無條件執行
    for line in s.splitlines():
        if line.strip().startswith('SUDOERS="/etc/sudoers.d/'):
            assert line.startswith("  "), f"寫 sudoers 的那行沒有在條件式裡：{line!r}"
            break
    else:
        raise AssertionError("找不到寫 sudoers 的那行")


def test_跳過時要留下機器可讀的標記():
    s = _script()
    assert eng.NO_SUDO_MARK in s, "跳過白名單時沒有留標記，上層無從得知這是降級的成功"


def test_跳過時要講後果與怎麼補():
    """後果要講**準**，不是講大。

    第一版寫「序號與機型都收不到」，2026-08-28 在真機查證才發現講錯了：
        -r--r--r--  product_name     ← 一般帳號讀得到
        -r--------  product_serial   ← 只有 root
    把損失講大會害人為了根本沒少的東西跑去裝 sudo——而在這個環境裝 sudo
    是要走變更的。訊息不準，代價是別人的時間。
    """
    s = _script()
    i = s.index(eng.NO_SUDO_MARK)
    tail = s[i:i + 700]
    assert "序號" in tail, "沒講少收了什麼"
    assert "product_serial" in tail, "沒指出是哪個檔案，人無從自己查證"
    assert "照常收" in tail or "照收" in tail, "沒講剩下的還在，會被讀成整台收不到"
    assert "裝 sudo" in tail, "沒講怎麼補"
    assert "重複執行" in tail, "沒講重跑安全——不然沒人敢再跑一次"


def test_不可以把損失講大成連機型都收不到():
    """守住上面那次修正。機型是 0444，沒有 sudo 一樣收得到。"""
    s = _script()
    i = s.index(eng.NO_SUDO_MARK)
    tail = s[i:i + 700]
    assert "序號與機型收不到" not in tail
    assert "機型收不到" not in tail


def test_跳過之後仍然要回報完成():
    """上層是用「輸出裡有沒有『完成。』」判定成功的。跳過白名單之後如果不印那行，
    一台其實已經納管好的機器會被記成失敗，然後有人跑去重納管、再失敗一次。"""
    s = _script()
    assert s.index(eng.NO_SUDO_MARK) < s.rindex("完成。")


# ---------------------------------------------------------------------------
# 成功訊息
# ---------------------------------------------------------------------------

def test_沒sudo的成功訊息要說出少收了什麼():
    msg = eng.success_message(
        f"[+] 已建立帳號 webit3scan\n[!] {eng.NO_SUDO_MARK} 這台沒有 sudo\n完成。")
    assert "序號" in msg, f"降級的成功卻沒講少了什麼：{msg}"
    assert "機型收不到" not in msg, "把損失講大了：機型是 0444，沒 sudo 一樣收得到"
    assert msg != "納管腳本執行完成"


def test_正常成功訊息不要多嘴():
    """有 sudo 的機器是完整成功。硬塞一句「可能收不到序號」會讓真正降級的那些
    被淹掉——狼來了喊多了就沒人看。"""
    msg = eng.success_message("[+] 已建立帳號 webit3scan\n完成。")
    assert msg == "納管腳本執行完成"


def test_AIX_本來就不佈_sudoers_不受影響():
    """AIX 沒有 dmi，序號機型走 uname 一般帳號就讀得到，本來就不需要 sudo。
    不要因為改 Linux 而讓 AIX 也開始噴「沒有 sudo」的警告。"""
    s = eng.build_aix_script(PUBKEY, IP)
    assert "sudoers" not in s
    assert eng.NO_SUDO_MARK not in s


def test_playbook_也要擋():
    pb = eng.build_linux_playbook(PUBKEY, IP)
    assert "/etc/sudoers.d" in pb
    assert "webit3_sudoers_dir" in pb, "playbook 沒有先確認 sudoers.d 存在"
    assert "when: webit3_sudoers_dir" in pb, "確認了卻沒有拿來當條件"
