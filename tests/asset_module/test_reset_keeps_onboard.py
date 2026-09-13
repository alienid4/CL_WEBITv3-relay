"""清空盤點資料不可以清掉納管紀錄（2026-09-09 使用者問出來的）。

他的問題：「我清空完再匯入最新資料，那些本來沒納管、現在變成納管的，
新的系統會知道納管狀態嗎？」——查證後答案是**不會**，`onboard_audit`
原本在清空名單裡。

為什麼這比「畫面變空」嚴重：

1. 清空之後目標主機上的收集帳號與金鑰**還在**，是系統自己忘了做過
2. 「取消納管」按鈕靠這張表才會出現——紀錄沒了就**再也沒辦法從畫面收回**
   那些帳號，它們變成沒人認領的孤兒帳號
3. 「誰在什麼時候、在哪台建了帳號」是稽核軌跡，金融業要留

判準是那段程式自己的註解：清的是「盤點／採集資料」，不清「帳號／憑證／
連線／設定」。納管紀錄記的是**我們對外做過的動作**，屬於後者。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402


def test_納管紀錄不可以被清空():
    assert "onboard_audit" not in api._RESET_TABLES, (
        "onboard_audit 被加回清空名單了——清掉之後目標主機上的帳號還在，"
        "但系統忘了它做過，「取消納管」按鈕不會出現，帳號收不回來")


def test_絕不清空的表要真的都不在名單裡():
    """把規則寫成清單，讓它被測試盯著，而不是靠人記得。"""
    for t in api._RESET_NEVER:
        assert t not in api._RESET_TABLES, f"{t} 不該被清空"


def test_盤點資料還是要清掉():
    """反向守著：別為了保納管紀錄，把清空功能弄成不清東西。"""
    for t in ("hardware", "scan_history", "host_service", "host_account", "source_record"):
        assert t in api._RESET_TABLES, f"{t} 是盤點資料，清空就該清掉"
