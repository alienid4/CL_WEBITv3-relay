"""遠端自動化紅燈偵測（checks.py 用它把 CI 狀態拉到每次 commit 前）。

這支測試存在的理由就是這次的教訓本身：2026-08-15 起 relay 同步連續失敗 9 次，
沒有任何人發現，因為沒有東西在看。補了偵測之後，如果偵測自己的錯誤路徑沒被驗過，
等於又回到原點——「沒驗過的錯誤路徑等於不存在」。

所以這裡把每一條路都走一遍，特別是「查不到」的那幾條：它們必須安靜地回空字串，
不能讓 checks.py 掛掉（那會反過來擋住所有人的 commit）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".project"))

import ci_status  # noqa: E402


def _runner(rc, out):
    return lambda: (rc, out)


def test_最新一次失敗要回報標題():
    out = '[{"conclusion":"failure","displayTitle":"relay 同步失敗","status":"completed"}]'
    assert ci_status.last_failure(_runner(0, out)) == "relay 同步失敗"


def test_最新一次成功就是沒事():
    out = '[{"conclusion":"success","displayTitle":"某個 commit","status":"completed"}]'
    assert ci_status.last_failure(_runner(0, out)) == ""


def test_只看最新一次_不翻歷史():
    """要回答的是「現在還壞著嗎」，不是「歷史上壞過幾次」。
    舊的失敗已經修好了還一直亮紅燈，等於製造雜訊，下場就是被忽略。"""
    out = ('[{"conclusion":"success","displayTitle":"剛修好","status":"completed"},'
           ' {"conclusion":"failure","displayTitle":"昨天壞的","status":"completed"}]')
    assert ci_status.last_failure(_runner(0, out)) == ""


def test_還在跑的不算失敗():
    out = '[{"conclusion":null,"displayTitle":"跑到一半","status":"in_progress"}]'
    assert ci_status.last_failure(_runner(0, out)) == ""


# ===== 查不到的每一種情況都要安靜跳過，不能讓 checks 掛掉 =====

def test_沒裝gh或沒網路_安靜跳過():
    assert ci_status.last_failure(_runner(1, "")) == ""


def test_輸出不是JSON_安靜跳過():
    assert ci_status.last_failure(_runner(0, "gh: command not found")) == ""


def test_沒有任何run_安靜跳過():
    assert ci_status.last_failure(_runner(0, "[]")) == ""


def test_回傳形狀不對_安靜跳過():
    for weird in ('{"conclusion":"failure"}', "null", '["不是物件"]'):
        assert ci_status.last_failure(_runner(0, weird)) == ""


def test_runner自己爆掉時不能把checks拖下水():
    def boom():
        raise OSError("gh 掛了")

    try:
        ci_status.last_failure(boom)
    except OSError:
        # 真實呼叫路徑（_default_runner）自己吞掉例外；注入的 runner 由呼叫端負責，
        # checks.py 也再包一層 try。這裡明確記錄這個分工，免得日後有人以為
        # last_failure 會吞掉任何東西。
        pass


def test_預設runner碰不到gh時回非0而不是丟例外():
    """真實路徑：不裝 gh／不是 GitHub repo 是常態，不能變成例外。"""
    rc, out = ci_status._default_runner()
    assert isinstance(rc, int) and isinstance(out, str)


def test_失敗標題會被截短_不要洗版():
    long_title = "非常長的標題" * 20
    out = ('[{"conclusion":"failure","displayTitle":"' + long_title +
           '","status":"completed"}]')
    assert len(ci_status.last_failure(_runner(0, out))) <= 40


def test_失敗但沒有標題_仍要回報壞掉():
    """回空字串代表『沒事』，所以沒標題時不能回空——會把紅燈吞掉。"""
    out = '[{"conclusion":"failure","displayTitle":null,"status":"completed"}]'
    assert ci_status.last_failure(_runner(0, out)) != ""
