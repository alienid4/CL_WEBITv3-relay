"""勾選多台一次納管：自動停止之後不卡在「等待中」（v1.468.4）。

2026-10-19 使用者（公司機）：「失敗會卡住」。7 台，前 4 台登入被拒、系統自動停下（避免拿錯的密碼
一直打把帳號鎖住），剩下 3 台一直寫「等待中」、計數「等待 3」——看起來像當掉。

排程邏輯在 frontend/composables/batchQueue.ts，行為測試在 frontend/tests/batchQueue.spec.ts（vitest）。
這支釘住畫面接線：視窗用那支排程、有「未執行（已停止）」、停止原因一行、兩個按鈕、密碼不外流。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
VUE = (FE / "components" / "BatchOnboardModal.vue").read_text(encoding="utf-8")
QUEUE = (FE / "composables" / "batchQueue.ts").read_text(encoding="utf-8")


def test_視窗用共用排程_停下來的標未執行():
    assert "from '~/composables/batchQueue'" in VUE
    assert "runQueue(queue" in VUE
    assert "stopped: '未執行（已停止）'" in VUE
    # 排程跑完一定把還在 waiting／running 的改掉（不會卡在等待中）
    assert "r.state === 'waiting' || r.state === 'running'" in QUEUE and "r.state = 'stopped'" in QUEUE
    # 後端的「登入被拒」字樣要算帳密錯（舊的判斷只認「密碼／認證／auth」）
    assert "登入被拒" in QUEUE


def test_停止原因一行_計數有已停止_兩個按鈕():
    assert "已自動停止：{{ stopInfo.authFailed }} 台帳密被拒，剩下 {{ stopInfo.notRun }} 台沒試，避免帳號被鎖" in VUE
    assert "已停止 <b" in VUE
    assert re.search(r"換密碼重跑失敗＋未執行的 \{\{ count\.failed \+ count\.stopped \}\} 台", VUE)
    assert "用同一組密碼繼續剩下的 {{ count.stopped }} 台" in VUE
    # 用同一組密碼繼續要明確確認
    assert "confirm(`用同一組密碼繼續剩下的" in VUE


def test_每台有逾時_視窗不會永遠停在納管中():
    assert "timeout: ONE_TIMEOUT_MS" in VUE
    assert "running.value = false" in VUE.split("async function start(")[1].split("finally")[1][:80]


def test_密碼只在記憶體_關閉就清():
    assert "function close() { lastPassword = ''; cred.password = ''" in VUE
    assert "onBeforeUnmount(() => { cred.password = ''; lastPassword = '' })" in VUE
    assert "localStorage" not in VUE and "sessionStorage" not in VUE
