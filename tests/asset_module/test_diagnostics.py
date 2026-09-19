"""診斷包：萬用框架（核心＋外掛），不綁任何單一功能。

第一版被寫成「正規化專用」是錯的——任何功能都會出問題、都需要 debug。
這組測試守的就是「萬用」這件事：新功能註冊一下就進診斷包，不用改核心。
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import diagnostics as dg  # noqa: E402


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def test_任何功能都能註冊進診斷包_不用改核心():
    """萬用的判準：新功能只要 register 一下就會出現在包裡。"""
    @dg.register("我的新功能")
    def _f(conn):
        return {"hello": "world"}

    assert "我的新功能" in dg.registered()
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            b = dg.collect(conn, note="測試", include_errors=False)
            assert b["sections"]["我的新功能"] == {"hello": "world"}
        finally:
            conn.close()
            dg._COLLECTORS.pop("我的新功能", None)


def test_一個外掛壞掉不可拖垮整包():
    """診斷工具自己在出問題時掛掉，是最糟的情況。"""
    @dg.register("會爆的功能")
    def _bad(conn):
        raise RuntimeError("我壞了")

    @dg.register("正常功能")
    def _good(conn):
        return {"ok": True}

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            b = dg.collect(conn, include_errors=False)
            assert b["sections"]["正常功能"] == {"ok": True}   # 其他區段照樣產出
            assert "會爆的功能" in b["collector_failures"]
            assert "我壞了" in b["collector_failures"]["會爆的功能"]["error"]
        finally:
            conn.close()
            dg._COLLECTORS.pop("會爆的功能", None)
            dg._COLLECTORS.pop("正常功能", None)


def test_去識別化要一致_同一個值永遠對到同一個代號():
    """全遮成星號就看不出「這兩筆是不是同一台」了。假名要保留關聯。

    ⚠️ 這裡刻意用 TEST-NET（203.0.113.x，RFC 5737）與假主機名，不用真實內網值——
    relay 匯出會逐字替換真實 IP／主機名，若測試依賴那些值，去識別化後的產出物測試就會紅，
    甚至把真主機名帶進 relay。用測試網段既能驗去識別化邏輯，又不隨替換表變動。
    """
    d = dg.Desensitizer()
    a1 = d.walk({"ip": "203.0.113.10", "hostname": "host-alpha"})
    a2 = d.walk({"ip": "203.0.113.10", "hostname": "host-alpha"})
    assert a1 == a2                                  # 一致
    assert a1["ip"] != "203.0.113.10"                # 有遮
    assert a1["hostname"] != "host-alpha"
    # 不同值要對到不同代號，否則會把兩台機器看成同一台
    b = d.walk({"ip": "203.0.113.11"})
    assert b["ip"] != a1["ip"]


def test_巢狀結構也要遮_只遮頂層等於沒遮():
    d = dg.Desensitizer()
    out = d.walk({"items": [{"ip": "10.99.1.5", "detail": {"note": "連到 10.99.1.5 失敗"}}]})
    dumped = json.dumps(out, ensure_ascii=False)
    assert "10.99.1.5" not in dumped


def test_敏感欄位不管內容都要遮():
    d = dg.Desensitizer()
    out = d.walk({"person_name": "王小明", "phone": "0912345678", "password": "hunter2"})
    for v in out.values():
        assert "王小明" not in str(v) and "0912" not in str(v) and "hunter2" not in str(v)


def test_殘留掃描要抓得到漏遮的位址():
    """輸出前的最後一道關卡——公司資料不出這台機器是硬規則。"""
    assert dg.residual_scan('{"x":"192.168.5.20"}'), "沒抓到未遮蔽 IP"
    assert not dg.residual_scan('{"x":"10.0.0.1"}'), "假名位址不該被誤報"


def test_關閉去識別化時原值保留_但要明確標示():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            b = dg.collect(conn, desensitize=False, include_errors=False)
            assert b["_desensitized"] is False
        finally:
            conn.close()


def test_核心區段一定有_不依賴任何功能():
    """就算一個外掛都沒有，環境快照與資料量也要在——那是所有問題的共同起點。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            b = dg.collect(conn, note="沒填也要能跑", include_errors=False)
            assert "meta" in b and "schema" in b
            assert "hardware" in b["schema"]
            assert b["note"]
        finally:
            conn.close()
