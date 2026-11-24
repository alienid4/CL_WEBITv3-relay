"""Codex 995／939：補明示單號的前端守門（1390 列為缺測試）。

995：主機系統總覽「去補對照表」要連到 V2 業務系統排行（/v2/systems?tab=rank），不可回舊頁。
939：聯絡資訊「異動紀錄」要把名冊匯入顯示成「名冊匯入」，摘要鍵翻成人看得懂的字（新增／更新…）。
"""
from __future__ import annotations

import re
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"


def _src(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def test_995_去補對照表連到V2業務系統排行():
    s = _src("components/v2/SystemOverview.vue")
    m = re.search(r'<NuxtLink to="([^"]+)">去補對照表</NuxtLink>', s)
    assert m, "找不到「去補對照表」連結"
    assert m.group(1) == "/v2/systems?tab=rank"


def test_939_名冊匯入顯示類別與摘要筆數():
    s = _src("components/v2/Contacts.vue")
    assert "oncall_roster_import: '名冊匯入'" in s
    for k, label in (("new", "新增"), ("update", "更新"), ("conflict", "衝突"), ("error", "格式錯誤")):
        assert f"{k}: '{label}'" in s, (k, label)
    # 摘要要逐鍵翻成「標籤=數字」，不可只剩原始 JSON 或破折號
    assert "BRIEF_LABEL[k]}=${o[k]}" in s
    assert "kind_label: KIND[r.kind] ?? r.kind" in s
