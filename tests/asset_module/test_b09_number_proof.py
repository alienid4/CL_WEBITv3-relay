"""[B-09] 數字可舉證：漏斗 5 個數字的說明、下鑽、獨立重算（2026-09-18）。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import number_proof as np_  # noqa: E402
import pipeline  # noqa: E402
import scan_scope  # noqa: E402

T = "2026-09-18 01:00:00"


def _conn(tmp_path, coverage=True):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    scan_scope._ensure(c)
    rows = [
        ("A-1", "web1", "192.0.2.1", "Red Hat Enterprise Linux 8", "使用中", 1),   # 掃到＋收得到＝已納管
        ("A-2", "web1", "192.0.2.1", "Red Hat Enterprise Linux 8", "使用中", 1),   # 同一台第二筆
        ("B-1", "aix1", "192.0.2.2", "AIX 7.2", "使用中", None),                    # 掃不到、在網段內＝失聯
        ("C-1", "far1", "198.51.100.9", "Windows 2019", "使用中", 1),               # 不在網段＝未涵蓋（收得到過也算）
        ("D-1", "old1", "192.0.2.3", "AIX 6.1", "報廢", None),                      # 退役
    ]
    for sn, h, ip, os_, st, ok in rows:
        db.insert_hardware(c, asset_serial=sn, hostname=h, ip=ip, os=os_, asset_status=st)
        c.execute("UPDATE hardware SET collect_ok = ? WHERE asset_serial = ?", (ok, sn))
    c.execute("INSERT INTO scan_history (ip, scan_ok, open_ports, scan_time) VALUES ('192.0.2.1', 1, '22', ?)", (T,))
    c.execute("INSERT INTO scan_history (ip, scan_ok, open_ports, scan_time) VALUES ('192.0.2.50', 1, '22', ?)", (T,))
    if coverage:
        c.execute("INSERT INTO scan_coverage (scan_time, cidr, ok, created_at) VALUES (?, '192.0.2.0/24', 1, ?)", (T, T))
    c.commit()
    return c


def test_五個數字_畫面值與獨立重算逐台一致(tmp_path):
    h = np_.health(_conn(tmp_path))
    got = {n["key"]: (n["screen"], n["recalc"], n["mismatch"]) for n in h["numbers"]}
    assert got == {
        "total": (5, 5, 0),          # web1、aix1、far1、old1＋掃到未登記 192.0.2.50
        "onboarded": (1, 1, 0),
        "lost": (1, 1, 0),
        "not_covered": (1, 1, 0),
        "aix": (2, 2, 0),            # aix1＋已退役的 old1（AIX 看 OS，不看狀態）
    }


def test_差異會逐台歸因_人工改OS(tmp_path):
    c = _conn(tmp_path)
    import os_override
    os_override.set_override(c, "web1", "192.0.2.1", "A-1", "AIX", "Linux", "測試", "t")
    n = {x["key"]: x for x in np_.health(c)["numbers"]}["aix"]
    assert n["screen"] == 3 and n["recalc"] == 2 and n["unexplained"] == 0
    assert "人工改過 OS 類型" in n["explanations"][0]["why"]


def test_漏斗頂端數字_跟下鑽條件算出來一樣(tmp_path):
    c = _conn(tmp_path)
    s = pipeline.summarize(c)
    kn = {x["key"]: x for x in np_.key_numbers(c, s)["items"]}
    for key, n in kn.items():
        rows = s["items"]
        if n["drill"].get("stages"):
            rows = [r for r in rows if r["stage"] in n["drill"]["stages"]]
        if n["drill"].get("os_type"):
            rows = [r for r in rows if r["os_type"] in n["drill"]["os_type"]]
        assert len(rows) == n["value"], f"{key}：下鑽 {len(rows)} 台 ≠ 數字 {n['value']}"


def test_AIX說明_要講未登記的分不出來():
    assert "未登記的 AIX 從網路上分不出來" in np_.EXPLAIN["aix"]["limits"]
    for k in np_.KEYS:
        assert all(np_.EXPLAIN[k].get(f) for f in ("how", "source", "limits", "zero")), k


def test_零要分查過還是沒查(tmp_path):
    c = _conn(tmp_path, coverage=False)
    kn = {x["key"]: x for x in np_.key_numbers(c, pipeline.summarize(c))["items"]}
    assert kn["not_covered"]["value"] == 0
    assert kn["not_covered"]["zero"]["text"].startswith("沒查"), "沒有涵蓋紀錄的 0 不是「真的沒有」"
    ev = {"scan_time": None, "covered_segments": 0, "hardware_rows": 0, "collect_tried_rows": 0}
    assert np_.zero_status("lost", 0, ev)["text"].startswith("沒查")
    ev = {"scan_time": T, "covered_segments": 3, "hardware_rows": 9, "collect_tried_rows": 9}
    assert np_.zero_status("lost", 0, ev)["text"].startswith("查過真的沒有")


def test_獨立重算不准借畫面那套判定():
    src = (BACKEND / "number_proof.py").read_text(encoding="utf-8")
    body = re.search(r"^def independent\b[\s\S]*?(?=^def )", src, re.M).group(0)
    for bad in ("manage_state", "pipeline", "classify(", "scan_scope", "_row_in_keys"):
        assert bad not in body, f"independent() 不可以用 {bad}——同一套寫兩次只會一致地錯"


def test_沒有IP_不算失聯_另列沒有IP(tmp_path):
    """221 查證：失聯 316 台＝沒 IP 305＋IP 欄填兩個 11，沒有一台是真的失聯。"""
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="E-1", hostname="tmpl1", ip="", asset_status="使用中")
    db.insert_hardware(c, asset_serial="F-1", hostname="dual1", ip="192.0.2.60,192.0.2.1", asset_status="使用中")
    db.insert_hardware(c, asset_serial="G-1", hostname="dual2", ip="192.0.2.61,192.0.2.62", asset_status="使用中")
    c.commit()
    it = {i["asset_serial"]: i for i in pipeline.summarize(c)["items"]}
    assert it["E-1"]["stage"] == "no_ip", "沒有 IP＝沒查，不是失聯"
    assert it["F-1"]["stage"] != "lost", "兩個 IP 其中一個掃得到＝掃得到"
    assert it["G-1"]["stage"] == "lost", "兩個 IP 都在涵蓋網段、都沒掃到＝真的失聯"
    h = {n["key"]: n for n in np_.health(c)["numbers"]}
    assert all(n["mismatch"] == 0 for n in h.values()), {k: n["explanations"] for k, n in h.items() if n["mismatch"]}


def test_同一台只有一筆收得到_整台就算已納管(tmp_path):
    """2026-09-20 公司 198.14 數字健檢抓到：畫面 已納管 1、獨立重算 4，差 3 台未解釋。

    那 3 台（SECSVR198-011T/014T/015T）同一台有多筆登記，收集成功只寫在其中一筆
    （B-05 之前留下的資料）；代表筆取「要處理的優先」就挑到沒收到那筆，
    一台收得到的機器被顯示成「納管過，還沒重新收集」。
    """
    c = _conn(tmp_path)
    # 同一台兩筆登記：一筆收得到、一筆沒收到；這次掃描掃得到
    db.insert_hardware(c, asset_serial="M-1", hostname="dual3", ip="192.0.2.70", os="RHEL 9", asset_status="使用中")
    db.insert_hardware(c, asset_serial="M-2", hostname="dual3", ip="192.0.2.70", os="RHEL 9", asset_status="使用中")
    c.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = 'M-1'")
    c.execute("UPDATE hardware SET collect_ok = 0 WHERE asset_serial = 'M-2'")
    c.execute("INSERT INTO scan_history (ip, scan_ok, open_ports, scan_time) VALUES ('192.0.2.70', 1, '22', ?)", (T,))
    c.commit()

    import manage_state as ms
    assert ms.summarize(c)["counts"][ms.ONBOARDED] == 2, "原本 1 台（web1）＋這台＝2"
    it = {i["ip"]: i for i in pipeline.summarize(c)["items"]}
    assert it["192.0.2.70"]["stage"] in np_.ONBOARDED_STAGES, "收得到的那筆說了算，不可以顯示成還沒納管"
    h = {n["key"]: n for n in np_.health(c)["numbers"]}
    assert h["onboarded"]["mismatch"] == 0, h["onboarded"]["explanations"]


def test_AIX判定要認得oslevel格式():
    """AIX 納管成功後，os 會被覆寫成 `oslevel -s` 的輸出（7200-05-09-2446），
    **那串裡沒有 aix 三個字**。只比對 "aix" 的寫法會在「收集成功之後」反而數不到——
    有資料比沒資料更糟的典型。

    **守的是行為，不是寫法。** 我第一版把這條寫成「不准出現 `"aix" in`」，
    那是錯的不變量：`independent()` 刻意不共用 manage_state（見該函式上方說明，
    共用了就會一致地錯、對帳形同虛設），所以它本來就該自己寫一份字串比對。
    該擋的是「漏掉 oslevel 格式」，不是「用了某個字串」。
    """
    import number_proof as npf

    # AIX 收集成功後真正會寫進去的值
    for real_os in ("7200-05-09-2446", "7100-05-06-1806", "7200-05-09"):
        assert "aix" not in real_os.lower(), "前提：這串裡本來就沒有 aix"
        assert npf._is_aix(real_os), f"{real_os} 判不出 AIX——AIX 台數會漏算"

    # 舊寫法仍要能用（納管前 os 是 "AIX 7.2"）
    assert npf._is_aix("AIX 7.2") and npf._is_aix("IBM AIX 7100")
    # 不可以誤判別的平台
    for other in ("Red Hat Enterprise Linux 9.8", "Microsoft Windows Server 2022",
                  "", None, "VMware ESXi 7.0"):
        assert not npf._is_aix(other), f"{other!r} 不該被判成 AIX"


def test_independent仍然不可以共用主流程的程式碼():
    """這條跟上面那條一起看才完整：**獨立重算的價值就在於不共用**。

    2026-09-22：我差點照指示把 _is_aix 改成呼叫 manage_state.platform_from_os，
    是本檔既有的守門攔下來的。共用之後主流程判錯時這裡會一致地錯，對帳就白做了。
    """
    src = (BACKEND / "number_proof.py").read_text(encoding="utf-8")
    body = re.search(r"^def independent\b[\s\S]*?(?=^def )", src, re.M).group(0)
    assert "manage_state" not in body
