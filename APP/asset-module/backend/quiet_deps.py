"""AUD-02：人工端點的盤中靜默窗確認，做成 FastAPI dependency（`dependencies=[Depends(quiet_deps.gate("<入口>"))]`）。

刻意不用 `from __future__ import annotations`：FastAPI 要從函式簽章解析型別（Request／Depends），
字串註解在閉包裡會解析不到。窗內沒帶 confirm_in_window=true → 409（code=quiet_window_confirm）；帶了 → 稽核同一交易後放行。
回傳票證（窗外為 None）；需要把票證往下傳的端點改在函式內直接呼叫 quiet_window.confirm_from_request。
"""
import sqlite3

from fastapi import Depends, Request

import quiet_window


def gate(entry: str):
    from _app_shared import get_db, require_auth

    def _dep(request: Request, confirm_in_window: bool = False,
             session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
        return quiet_window.confirm_from_request(conn, entry, confirm_in_window, session["username"], request)

    return _dep
