"""月報匯出：不准把「不歸我們」寫成「我們沒有」，也不准讓 GPT 腦補。

規格 2026-09-07、動工 2026-09-08。系統只匯出 raw data ＋提示詞成一個檔，
人拿去給核准的 GPT 打「請分析」——**這裡沒有任何 AI 呼叫**。

守三條線：

1. **三種狀態要分得開**：有資料／保留（外部來源）／無資料。
   使用者 2026-09-08：「這三個我們可以先不做，以後可以去撈 What'sUp API，
   欄位先空出來。」把「不歸我們」寫成「我們沒有」，GPT 會產出
   「建議補強監控」——那是把別組的工作寫成我們的缺失。

2. **沒有的東西不准推測**。效能與容量沒資料時 GPT 會自己編一個看起來合理的趨勢，
   而部主管會拿那個去做決策。提示詞裡要有死命令。

3. **「連不上 4335 台」不可以被讀成「可用率 0.06%」**。那 4335 台不是掛掉，
   是還沒佈收集帳號。同一個數字兩種讀法差到天上地下，而錯的那種比較醒目。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import monthly_report as mr  # noqa: E402


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


# ---------------------------------------------------------------------------
# 系統不做分析
# ---------------------------------------------------------------------------

def test_這支不呼叫任何AI():
    """決策 2026-09-07：系統只匯出，分析交給外部 GPT。
    哪天有人為了「順便」把 LLM 接進來，這裡就紅——那會多一條對外連線
    與一個資安關卡，而這案子對外送碼是有關卡的。"""
    src = (ROOT / "APP" / "asset-module" / "backend" / "monthly_report.py").read_text(
        encoding="utf-8")
    for bad in ("openai", "anthropic", "requests.post", "httpx", "urllib.request"):
        assert bad not in src.lower(), f"月報模組出現對外呼叫：{bad}"


# ---------------------------------------------------------------------------
# 三種狀態要分得開
# ---------------------------------------------------------------------------

def test_效能容量可用率標成外部來源不是無資料():
    """把「不歸我們」寫成「我們沒有」，GPT 會產出「監控不足、建議補強監控」——
    那是把監控組的工作寫成我們的缺失，而且會直接送到部主管桌上。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            payload = mr.collect(conn)
            by_name = {s["name"]: s for s in payload["sections"]}
            for name in ("效能", "容量", "可用率 / 事故"):
                s = by_name[name]
                assert s["external"] is True, f"{name} 沒標成外部來源"
                assert s["has_data"] is False
                assert "What'sUp" in s["source"], f"{name} 沒指出資料在哪"
        finally:
            conn.close()


def test_外部來源的欄位要先空出來():
    """使用者：「欄位先空出來」。之後接 What'sUp API 只要把值填進去，
    提示詞與畫面都不用改。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            by_name = {s["name"]: s for s in mr.collect(conn)["sections"]}
            assert by_name["可用率 / 事故"]["data"]["uptime%"] is None
            assert by_name["效能"]["data"]["CPU 使用率"] is None
            assert by_name["容量"]["data"]["儲存容量"] is None
            for name in ("可用率 / 事故", "效能", "容量"):
                assert by_name[name]["data"]["狀態"] == "尚未介接"
        finally:
            conn.close()


def test_涵蓋表三種狀態用不同標記():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            md = mr.render_markdown(conn, "2026-09", "tester")
            assert "🔌 保留（外部來源，未介接）" in md
            assert "✅ 有" in md
            # 三種狀態的差別要寫在表格前面，讀的人第一眼就看到
            assert "三種狀態意思完全不同" in md
        finally:
            conn.close()


def test_不可以叫GPT把外部來源寫成監控不足():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            md = mr.render_markdown(conn, "2026-09", "tester")
            assert "不是我們的缺口，是分工" in md
            assert "建議補強監控" in md, "沒有明文禁止這個寫法"
            assert "由監控組提供" in md
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 不准腦補
# ---------------------------------------------------------------------------

def test_提示詞要禁止推測():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            md = mr.render_markdown(conn, "2026-09", "tester")
            for must in ("不准推測", "不准估算", "只能用檔案裡的資料",
                         "「沒查」跟「查了沒問題」要分開講"):
                assert must in md, f"提示詞少了：{must}"
        finally:
            conn.close()


def test_沒有上月快照要明講答不出來():
    """第一次產月報時「跟上個月比」根本沒有比較基準。
    不明講的話 GPT 會用「感覺」補一個趨勢出來。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            md = mr.render_markdown(conn, "2026-09", "tester")
            assert "沒有上月快照" in md
            assert "這次答不出來" in md
        finally:
            conn.close()


def test_納管涵蓋率不可以被讀成可用率():
    """「連不上 4335 台」在 221 實測是「還沒佈收集帳號」，不是主機掛掉。
    那個數字擺在 Availability 底下，任何人都會讀成「可用率 0.06%」。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            by_name = {s["name"]: s for s in mr.collect(conn)["sections"]}
            # 段名本身就不能叫「可用率」
            assert "納管涵蓋率（不是可用率）" in by_name
            note = by_name["納管涵蓋率（不是可用率）"]["data"][
                "⚠️ 這一段最容易被誤讀，請照這樣理解"]
            assert "不是主機故障" in note
            assert "❌ 不可以寫成" in note
            # 真正的 Availability 要自己佔一行，不能被涵蓋率頂掉
            assert by_name["可用率 / 事故"]["has_data"] is False
        finally:
            conn.close()


def test_機密標示要在最前面():
    """檔案含主機名、位址與帳號明細。決策走 C：只給核准的企業版／地端 GPT。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            md = mr.render_markdown(conn, "2026-09", "tester")
            head = md[:600]
            assert "機密" in head
            assert "不要貼到公開的 ChatGPT" in head
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 快照：同月覆蓋（決策 A）
# ---------------------------------------------------------------------------

def test_同月重跑覆蓋但記得重產過幾次():
    """使用者 2026-09-08 選 A：月報是當月結算，中途重產是正常的。
    但「這份數字什麼時候、誰產的、重產過幾次」要查得到。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            mr.freeze(conn, "2026-09", "alice")
            s2 = mr.freeze(conn, "2026-09", "bob")
            assert s2["regenerated_count"] == 2
            assert s2["generated_by"] == "bob"
            n = conn.execute(
                "SELECT COUNT(*) FROM monthly_report_snapshot").fetchone()[0]
            assert n == 1, "同月應該覆蓋，不是長出第二筆"
        finally:
            conn.close()


def test_快照只存彙總不存明細():
    """明細是當下的事實，下個月再看已經不同；而且會讓快照表無限長大。
    跨月要比的是數字。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            snap = mr.freeze(conn, "2026-09", "tester")
            blob = str(snap["summary"])
            assert "明細" not in blob
            assert len(blob) < 4000, f"快照太大（{len(blob)}），可能存進了明細"
        finally:
            conn.close()


def test_上個月怎麼算():
    assert mr.previous_month("2026-09") == "2026-08"
    assert mr.previous_month("2026-01") == "2025-12"


def test_帳號稽核只算最新一輪盤點():
    """account_finding 每輪都新增一批、舊的保留對照。月報不限 run 的話，
    每多收一輪數字就膨脹一次——2026-09-11 221 實測 93 條被報成 186。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            for run_id, when in ((1, "2026-09-10 01:00:00"), (2, "2026-09-11 01:00:00")):
                conn.execute(
                    "INSERT INTO account_collect_runs (id, trigger, status, started_at) "
                    "VALUES (?, 'schedule', 'ok', ?)", (run_id, when))
                conn.execute(
                    "INSERT INTO account_finding (run_id, ip, username, rule_id, verdict, "
                    "created_at) VALUES (?, '192.0.2.1', 'u1', 'R5', 'fail', ?)",
                    (run_id, when))
            conn.commit()
            by_name = {s["name"]: s for s in mr.collect(conn)["sections"]}
            data = by_name["帳號稽核 / 合規"]["data"]
            assert data["最新盤點時間"] == "2026-09-11 01:00:00"
            assert data["稽核結果分佈"] == {"fail": 1}, "舊輪次的發現被算進來了"
            assert len(data["未通過的發現（最多 300 筆）"]) == 1
        finally:
            conn.close()


def test_一段炸掉不該讓整份月報產不出來():
    """但也不能安靜跳過——涵蓋表要寫「取得失敗」，人看得出缺了哪一塊。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            conn.execute("DROP TABLE host_account")
            md = mr.render_markdown(conn, "2026-09", "tester")
            assert "取得失敗" in md
            assert "不是沒問題，是沒撈到" in md
        finally:
            conn.close()
