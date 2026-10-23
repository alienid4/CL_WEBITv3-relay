"""全測試共用：每個測試開始前清掉全機隊計算的跨請求快取（fleet_cache）。

快取是行程層級的；測試之間如果在「資料庫檔案沒變」的情況下換掉內部函式（monkeypatch），
可能拿到前一個測試算好的結果——那是假綠燈（2026-10-21 獨立審查 #4）。
"""
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "APP" / "asset-module" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


@pytest.fixture(autouse=True)
def _clear_fleet_cache():
    try:
        import fleet_cache
    except Exception:  # noqa: BLE001 - 不是資產模組的測試環境：沒有快取可清
        yield
        return
    fleet_cache.clear()
    yield
    fleet_cache.clear()
