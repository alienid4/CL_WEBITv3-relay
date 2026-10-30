"""2.x 左側選單「像 Excel 的群組」（2026-10-27）：預設只有目前頁所在的群組打開、每人的展開狀態存得住、
全部展開／全部收合、鍵盤與 aria-expanded。前端純函式用 node 直接跑（node 22.6+ 才能直接載入 .ts；沒有就跳過）。"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import nav_prefs  # noqa: E402

ALL = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "⚙"]


def _node(expr: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("沒有 node")
    v = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v").split(".")
    if (int(v[0]), int(v[1])) < (22, 6):
        pytest.skip(f"node {'.'.join(v)} 不能直接載入 .ts")
    src = (FE / "utils" / "navGroups.ts").as_uri()
    code = f"import * as n from '{src}'; const ALL = {json.dumps(ALL)}; console.log(JSON.stringify({expr}))"
    r = subprocess.run([node, "--input-type=module", "-e", code], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_預設只有目前頁的群組打開():
    assert _node("n.openGroups(null, '2', ALL)") == ["2"]                 # 沒存過：只開目前頁的群組
    assert _node("n.openGroups([], null, ALL)") == []                    # 不在任何群組的頁：全收
    assert _node("n.openGroups(['7', '1'], '4', ALL)") == ["1", "4", "7"]  # 存的＋目前頁，照選單順序
    assert _node("n.openGroups(['x', '3'], '3', ALL)") == ["3"]           # 不認得的群組丟掉


def test_點標題切換_目前頁群組收起只在這次():
    assert _node("n.toggleGroup([], '7', '2', false, ALL)") == [["7"], False]
    assert _node("n.toggleGroup(['7'], '7', '2', false, ALL)") == [[], False]
    s, closed = _node("n.toggleGroup(['2', '7'], '2', '2', false, ALL)")       # 收起目前頁的群組
    assert (s, closed) == (["7"], True)
    assert _node(f"n.openGroups({json.dumps(s)}, '2', ALL, true)") == ["7"]
    assert _node("n.toggleGroup(['7'], '2', '2', true, ALL)") == [["7"], False]  # 再點一次打開
    assert _node("n.setAllGroups(true, ALL)") == [ALL, False]
    assert _node("n.setAllGroups(false, ALL)") == [[], True]
    assert _node("n.openGroups([], '2', ALL, true)") == []                # 全部收合連目前頁也收


def test_每人的展開狀態存得住(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        assert nav_prefs.get_v2_open(conn, "alice") == {"username": "alice", "open": [], "is_default": True}
        nav_prefs.set_v2_open(conn, "alice", ["7", "1", "nope", "⚙"])
        assert nav_prefs.get_v2_open(conn, "alice")["open"] == ["1", "7", "⚙"]       # 照選單順序、不認得的丟掉
        assert nav_prefs.get_v2_open(conn, "bob")["open"] == []                       # 每人一份
        nav_prefs.set_v2_open(conn, None, ["3"])                                       # 登入停用：共用一份
        assert nav_prefs.get_v2_open(conn, None)["open"] == ["3"]
        from routers import system as rs
        r = rs.nav_v2_groups_put(rs.NavV2GroupsBody(open=["5"]), None, conn)
        assert r["open"] == ["5"] and r["storage"] in ("server", "local")
        assert rs.nav_v2_groups_get(None, conn)["open"] == ["5"]
    finally:
        conn.close()


def test_外框_鍵盤與aria():
    src = (FE / "layouts" / "v2.vue").read_text(encoding="utf-8")
    assert ':aria-expanded="openSet.has(g.no)"' in src and '<button type="button" class="gh"' in src   # button＝Enter/Space 會觸發
    assert "全部展開" in src and "全部收合" in src
    assert "（{{ g.items.length }}）" not in src      # 2026-10-28 找碴 C30：括號數字像待辦數，拿掉（項數留在 title 提示）
    assert ">資訊戰情室</NuxtLink>" in src
    assert "collapsed = !collapsed" in src                                     # 整個側欄收合鈕還在
    assert "localStorage" in src and "/api/nav/v2-groups" in src
