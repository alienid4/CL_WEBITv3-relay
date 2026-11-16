"""同一台機器，三個判定呼叫點必須給出**同一個**不納管類別。

── 這是在防什麼 ──────────────────────────────────────────────────────────
`onboard_eligibility.not_onboardable()` 的證據是**呼叫端自己湊**的，於是三處
湊出三組不同的證據：

    collect_dispatch（/adopt 收集分派）：os + hostname + ip + esxi_names + extra_text
    api._onboard_block（資產查詢／資產詳細）：os + hostname + ip        ← 少兩項
    pipeline._onboard_block_for（漏斗）    ：os + hostname + ip        ← 少兩項

後果：一台 EMC UnityXT480（**作業系統欄是 `N/A`**、身分只寫在資產用途裡）
在 /adopt 判成「儲存設備」，在資產查詢卻判成可以納管、還掛著「一鍵納管」按鈕。
使用者為了這個分類不一致抱怨過兩次。

修法：證據蒐集收進 `onboard_eligibility.judge_for_asset()`，三處都走它。
這支測試釘的是**結果一致**，不是「程式碼看起來有呼叫同一支」。

⚠️ collect_dispatch 這一側在這一版**沒有改**（有別人在改那個檔）。它本來就已經
傳齊證據，所以它是這裡的「標準答案」——另外兩處要跟它一樣。合併之後再把它也
換成 `judge_for_asset`，那時這支測試不需要改。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402
import onboard_eligibility as oe  # noqa: E402
import pipeline  # noqa: E402


# ip, hostname, os 欄, 資產名稱, 資產用途, 預期類別（None＝可以納管）
CASES = [
    # ★ 核心案例：os 欄空白／N/A，身分只寫在資產用途裡。
    #   只有拿得到 extra_text 的呼叫點才判得出來——以前三處只有 /adopt 判得出來。
    ("10.99.19.207", "NAS-055", "N/A", "NAS-055",
     "EMC UnityXT480 NAS (iSCSI target)", "storage"),
    ("10.99.19.208", "nas-208", "", "NetApp FAS2750", "檔案伺服器", "storage"),
    ("10.99.19.209", "esx-209", None, "", "ESXi 虛擬化主機", "esxi"),
    # os 欄本來就講得清楚的：三處一直都一致，這裡是回歸護欄
    ("10.99.19.210", "net-210", "網路設備", "", "", oe.KIND_NETWORK),
    ("10.99.19.211", "sto-211", "儲存設備", "", "", "storage"),
    # 真的可以納管的，不可以因為多給了證據就被誤擋（自由文字只採信「會擋下來」的結論）
    ("10.99.19.212", "app-212", "Rocky Linux 9", "app-212", "測試機", None),
    ("10.99.19.213", "app-213", "", "app-213", "測試機", None),
    ("10.99.19.214", "app-214", "", "app-214", "Linux 應用主機", None),
]


@pytest.fixture()
def conn(tmp_path):
    """空的測試 DB。一定要關掉——Windows 上沒關的 sqlite 會讓暫存目錄刪不掉。"""
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    try:
        yield c
    finally:
        c.close()


def _seed(conn):
    for ip, host, os_text, name, purpose, _ in CASES:
        db.insert_hardware(conn, asset_serial=f"S-{ip}", ip=ip, os=os_text,
                           hostname=host, asset_name=name, asset_purpose=purpose,
                           environment="測試", asset_status="使用中")


def _row(conn, ip):
    return conn.execute("SELECT * FROM hardware WHERE ip = ?", (ip,)).fetchone()


def _kind_of(blk):
    return blk["kind"] if blk else None


def test_三個呼叫點對同一筆資產給同一個類別(conn):
    _seed(conn)
    esxi = oe.esxi_host_names(conn)

    for ip, host, os_text, name, purpose, want in CASES:
        row = _row(conn, ip)

        # (1) /adopt 收集分派這一側：它本來就傳齊證據，是標準答案。
        #     走 collect_dispatch 自己的 `_rejudge_block`（跟 run_dispatch 裡那段
        #     用同一組證據），不要在測試裡抄一份參數——抄了，測試自己就成了
        #     第四個呼叫點，那正是這支測試在防的事。
        #     collect_dispatch.py 這一版**沒有改**（有別人在改那個檔）。
        adopt_row = {"ip": ip, "alive": 1, "status": "needs_credential"}
        cd._rejudge_block(conn, adopt_row, esxi)
        adopt_kind = adopt_row.get("not_onboardable_kind")

        # (2) 資產查詢／資產詳細
        api_kind = _kind_of(api._onboard_block(dict(row), conn, esxi))

        # (3) 漏斗
        pipe_kind = _kind_of(pipeline._onboard_block_for(dict(row), conn, esxi))

        where = f"{ip}（{host}／os={os_text!r}／用途={purpose!r}）"
        assert adopt_kind == want, f"{where}：/adopt 判成 {adopt_kind}，預期 {want}"
        assert api_kind == adopt_kind, (
            f"{where}：資產查詢判成 {api_kind}，/adopt 判成 {adopt_kind}——分類不一致")
        assert pipe_kind == adopt_kind, (
            f"{where}：漏斗判成 {pipe_kind}，/adopt 判成 {adopt_kind}——分類不一致")


def test_os欄空白但用途寫著NAS_三處都要判成儲存(conn):
    """把核心案例單獨釘一條：這一題紅了就是老 bug 回來了。

    （整組一致還不夠——三處「一起錯成可以納管」也會讓上面那題綠。）
    """
    _seed(conn)
    esxi = oe.esxi_host_names(conn)
    row = dict(_row(conn, "10.99.19.207"))

    assert _kind_of(api._onboard_block(row, conn, esxi)) == "storage", \
        "資產查詢頁又把 EMC UnityXT480 當成可以納管了（os 欄 N/A，用途寫著 NAS）"
    assert _kind_of(pipeline._onboard_block_for(row, conn, esxi)) == "storage", \
        "漏斗又把 EMC UnityXT480 當成可以納管了"


def test_vCenter列為ESXi的機器_三處都擋(conn):
    """esxi_names 這項證據以前只有 /adopt 拿得到：os 欄完全沒線索時只靠它。"""
    db.insert_hardware(conn, asset_serial="S-esxi", ip="10.99.19.56",
                       os="", hostname="Esxi019-56", asset_name="", asset_purpose="",
                       environment="測試", asset_status="使用中")
    conn.execute("INSERT INTO source_record (source, payload) VALUES ('vcenter', ?)",
                 ('{"esxi_host": "Esxi019-56"}',))
    conn.commit()

    row = dict(_row(conn, "10.99.19.56"))
    # 不傳 esxi_names：共用函式要自己去 conn 查，呼叫端不必記得
    assert _kind_of(api._onboard_block(row, conn)) == "esxi", \
        "資產查詢沒拿到 vCenter 的 ESXi 名單——跟 /adopt 又不一致了"
    assert _kind_of(pipeline._onboard_block_for(row, conn)) == "esxi", \
        "漏斗沒拿到 vCenter 的 ESXi 名單"


def test_共用函式吃得下少撈欄位的列():
    """漏斗的 SELECT 不是 `SELECT *`，少一欄不可以炸——不然這支就沒人敢用。"""
    assert oe.judge_for_asset(None, {"ip": "10.99.19.1"}) is None
    assert oe.judge_for_asset(None, None) is None
    assert oe.judge_for_asset(None, {"os": "網路設備"})[0] == oe.KIND_NETWORK
    assert oe.asset_free_text({"asset_name": "NAS-1"}) == "NAS-1"
    assert oe.asset_free_text({}) is None
    assert oe.asset_free_text(None) is None


def test_沒有呼叫點再自己湊參數():
    """防復發：API 層不可以再自己呼叫 not_onboardable 湊參數。

    行為一致由上面幾題證明；這題防的是有人為了「少傳一個 conn」又把它改回去。

    v1.436.0 起 api.py 依網域拆成 routers/、共用狀態移到 _app_shared.py，
    掃描範圍一併擴到 routers/*.py 與 _app_shared.py——不然守門會因為路由搬家而失效。
    """
    be = ROOT / "APP" / "asset-module" / "backend"
    targets = [be / "api.py", be / "pipeline.py", be / "_app_shared.py"]
    targets += sorted((be / "routers").glob("*.py"))
    for f in targets:
        code = "".join(l for l in f.read_text(encoding="utf-8").splitlines(True)
                       if not l.lstrip().startswith("#"))
        assert "not_onboardable(" not in code, \
            f"{f.name} 又自己呼叫 not_onboardable 湊參數了——請走 judge_for_asset／block_for_asset"
