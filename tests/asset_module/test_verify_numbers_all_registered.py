"""verify_numbers 每一支 check_* 都要登記進 main() 的執行清單。

起因（2.066.3）：check_daily_digest_never 寫了、單元測試也呼叫了，但沒登記進 main()，
部署關卡 [2d/7] 根本沒跑它——「寫了不等於有在守門」。這支把所有 check_* 跟 main() 釘在一起。
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend" / "verify_numbers.py"


def test_每支_check_都有登記進_main():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    checks = {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("check_")}
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    used = {n.id for n in ast.walk(main) if isinstance(n, ast.Name)}
    # 也接受「被另一支已登記的 check 呼叫」（例如 check_opening_peak 由日報段落內呼叫）
    for f in tree.body:
        if isinstance(f, ast.FunctionDef) and f.name.startswith("check_") and f.name in used:
            used |= {n.func.id for n in ast.walk(f) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    # check() 本身是記錄用的輔助函式，不是一段核對。
    # 刻意不登記的要寫理由（審查會看）：
    # （check_v2_sections 2.066.3 改成分組只算一次＋抽 4 台後已登記，從這裡移除）
    NOT_YET: set[str] = set()
    missing = sorted(c for c in checks - used - NOT_YET if c != "check")
    assert not missing, f"這些 check_* 沒登記進 verify_numbers.main()，部署關卡不會跑：{missing}"
