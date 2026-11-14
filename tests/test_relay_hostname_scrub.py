"""relay 去識別化：真實主機名命名樣式、MAC、PM 資料排除、更新包只留 1 包、更新包內容殘留掃描。

背景（2026-11-01）：GPT 複查公開 relay 抓到真實主機名（SEC…-NNN 之類）仍在註解與測試資料裡，
而逐字替換表列不完。這裡釘住「認命名規則」的替換與掃描。

⚠️ 本檔不可出現真實主機名：一律用字串相接組出命名樣式，避免被 relay 的替換改寫字面值。
"""
import io
import re
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".project"))

try:
    import desensitize_rules as dr  # noqa: E402
    import make_relay as mr  # noqa: E402
except ImportError:  # pragma: no cover - relay 快照沒有這兩支
    pytest.skip("去識別化規則不在 relay 快照裡（EXCLUDE），本測試只在主 repo 執行",
                allow_module_level=True)

S = "SE" + "C" + "SVR"          # 命名樣式前綴（相接，避免字面值被替換）
V = "SV" + "R"
MAC = "a4:bb:6d:" + "12:34:56"


def _sub(text, rel=""):
    return dr.regex_sanitize(text, rel)[0]


def _residual(text, rel="x.py"):
    return mr._scan_text(rel, text)


def test_主機名換成示範名_保留長相與尾綴():
    out = _sub(f"{S}194-068T 與 {S}018-145TT 與 {V}019-203")
    assert S not in out and f"{V}019" not in out
    assert re.search(r"EXSVR\d{3}-\d{3}T\b", out)
    assert re.search(r"EXSVR\d{3}-\d{3}TT\b", out)
    assert re.search(r"DSVR\d{3}-\d{3}\b", out)


def test_同字串同結果_大小寫形態保留():
    a = _sub(f"{S}194-068T")
    assert _sub(f"x {S}194-068T y") == "x " + a + " y"
    assert _sub(f"{S}194-068T".lower()) == a.lower()
    assert _sub(f"{S}194-068T".lower()).islower()


def test_不同字串不撞名_含少打一個0的錯名():
    names = [f"{S}161-22", f"{S}161-022", f"{S}162-022", f"{S}194-068T", f"{S}194-068",
             f"{S}195-059", f"{S}018-145TT", f"{S}018-145T", f"{V}019-203", f"{V}019-204",
             f"{S}001-001", f"{S}001-002"]
    outs = [_sub(n) for n in names]
    assert len(set(outs)) == len(names)


def test_全範圍一對一_第一段與第二段各自不撞():
    from desensitize_rules import _map_digits
    for length in (1, 2, 3):
        for a in (7, 11):
            vals = {_map_digits(str(i).zfill(length), a, 137) for i in range(10 ** length)}
            assert len(vals) == 10 ** length


def test_流水號原樣保留_執行時組出的字串對得上字面值():
    """測試常用 f"{前綴}-{n}T" 在執行時組名；字面值被替換後兩者必須一致。"""
    lit = _sub(f"{S}018-161T")
    built = _sub(f"{S}018") + "-" + "161" + "T"
    assert lit == built


def test_單獨的svr泛稱不被誤傷():
    assert _sub("svr01 與 APPSVR003") == "svr01 與 APPSVR003"


def test_替換後殘留掃描乾淨_原文則命中():
    raw = f"主機 {S}194-068T 與 {V}019-203 與 {S}19"
    assert _residual(raw)
    assert _residual(_sub(raw)) == []


def test_相鄰英數字不算命名():
    assert _sub(f"x{S}194-068") == f"x{S}194-068"


def test_mac只在project底下替換_且落在文件用範圍():
    out, n = dr.regex_sanitize(f"mac={MAC} 又 {MAC.upper()}", ".project/backlog_like.json")
    assert n == 2 and MAC not in out.lower()
    found = re.findall(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", out)
    assert found and all(f.startswith("00:00:5e:00:53:") for f in found)
    assert found[0] == found[1]                       # 同一個 MAC（大小寫不同）→ 同一個結果
    # APP／tests 的假 MAC 維持原樣（測試資料要原長相）
    assert dr.regex_sanitize(f"mac={MAC}", "tests/t.py")[0] == f"mac={MAC}"


def test_mac殘留掃描只在project_且排除文件範圍():
    assert mr._scan_text(".project/a.json", MAC)
    assert not mr._scan_text(".project/a.json", "00:00:5e:00:53:0a")
    assert not mr._scan_text("tests/t.py", MAC)


def test_sanitize整合_逐字加正規式都套用():
    text, n = mr.sanitize(f"{S}194-068T 在 YOUR_SERVER_IP", "APP/x.py")
    assert "YOUR_SERVER_IP" in text and S not in text and n == 2


def test_pm資料不進relay():
    assert ".project/backlog.json" in mr.EXCLUDE
    assert ".project/decisions.json" in mr.EXCLUDE


# ---------- patches ----------

def _pkg(root: Path, day: str, stamp: str, content: dict | None = None) -> Path:
    d = root / day
    d.mkdir(parents=True, exist_ok=True)
    tgz = d / f"patch_webit3_{stamp}_v1.0.0.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        for name, text in (content or {"files/APP/a.py": "x = 1\n"}).items():
            data = text.encode("utf-8")
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    (d / f"patch_webit3_{stamp}_v1.0.0.sha256").write_text("deadbeef", encoding="utf-8")
    return tgz


def test_只留最新一包():
    assert mr.KEEP_PATCHES == 1


def test_放入新包時承接的舊包全部清掉(tmp_path):
    out = tmp_path / "out"
    _pkg(out / "patches", "20261030", "20261030_0900")
    _pkg(out / "patches", "20261031", "20261031_0900")
    src = tmp_path / "src"
    _pkg(src, "20261101", "20261101_0900")

    added, pruned = mr.add_patch_dir(out, src)

    left = sorted(p.name for p in (out / "patches").rglob("*") if p.is_file())
    assert left == ["patch_webit3_20261101_0900_v1.0.0.sha256",
                    "patch_webit3_20261101_0900_v1.0.0.tar.gz"]
    assert added == 2 and pruned == 2
    assert not (out / "patches" / "20261030").exists()


def test_更新包內容含真實主機名_殘留掃描要擋(tmp_path):
    out = tmp_path / "out"
    _pkg(out / "patches", "20261101", "20261101_0900", {"files/APP/a.py": f"# {S}194-068T\n"})
    hits = mr.residual_scan(out)
    assert hits and "!files/APP/a.py" in hits[0]


def test_更新包內容乾淨則通過(tmp_path):
    out = tmp_path / "out"
    _pkg(out / "patches", "20261101", "20261101_0900")
    assert mr.residual_scan(out) == []


def test_更新包內project檔的真實mac要擋(tmp_path):
    out = tmp_path / "out"
    _pkg(out / "patches", "20261101", "20261101_0900", {"files/.project/n.json": MAC})
    assert mr.residual_scan(out)


def test_讀不開的更新包視為有問題(tmp_path):
    out = tmp_path / "out"
    d = out / "patches" / "20261101"
    d.mkdir(parents=True)
    (d / "patch_webit3_20261101_0900_v1.0.0.tar.gz").write_bytes(b"not a tar")
    assert mr.residual_scan(out)
