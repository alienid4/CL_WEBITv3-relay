"""1.5 VMware 資料存放區：第一級／第二級（vm_ds_first_pct，預設 95，維持現況；NB 建議改 90 但使用者尚未拍板）。
判不判「低於門檻」仍是 vm_lun_* 那把尺，這裡只驗等級標示。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import aria_dailycheck as ad  # noqa: E402
import daily_check as dc  # noqa: E402
from test_daily_check_vmware import VCID, env, gen, ms, send, snap, tnow  # noqa: E402,F401


def _dss(t):
    mk = lambda i, name, free: {"id": i, "name": name, "vcid": VCID, "type": "VMFS", "cap_gb": 4000, "free_gb": free, "hosts": 4, "at": t}  # noqa: E731
    return [mk("d1", "SAN-96", 150.0), mk("d2", "SAN-90", 380.0), mk("d3", "SAN-87", 500.0), mk("d4", "SAN-OK", 3000.0)]


def _levels(c, drop, first=None):
    if first is not None:
        dc.save_settings(c, {"vm_ds_first_pct": first}, today=tnow().date())
    r, _v, _ = gen(c)
    return {d["name"]: (d["state"], d.get("level")) for d in r["VC-1"]["detail"] if d["kind"] == "資料存放區"}


def test_資料存放區等級_預設90_96與90_5是第一級_87是第二級_正常的沒有等級(env):
    c, drop = env
    from datetime import timedelta
    t = ms(tnow() - timedelta(minutes=5))
    send(drop, "ariadc_a", snap(dss=_dss(t)))
    ad.import_pending(c)
    lv = _levels(c, drop)
    assert lv["SAN-96"] == ("低於門檻", "第一級")            # 用量 96.25%
    assert lv["SAN-90"] == ("低於門檻", "第一級")            # 用量 90.5% ≥ 90（2026-11-22 預設改 90，與硬碟、Storage 一致）
    assert lv["SAN-87"] == ("低於門檻", "第二級")            # 剩 500 < 600 GB 才列出，用量 87.5%
    assert lv["SAN-OK"] == ("正常", None)


def test_第一級用量改回95_90_5回到第二級(env):
    c, drop = env
    from datetime import timedelta
    t = ms(tnow() - timedelta(minutes=5))
    send(drop, "ariadc_a", snap(dss=_dss(t)))
    ad.import_pending(c)
    lv = _levels(c, drop, first=95)
    assert lv["SAN-96"] == ("低於門檻", "第一級") and lv["SAN-90"] == ("低於門檻", "第二級")


def test_第一級用量改90_90_5變第一級_87_5仍第二級(env):
    c, drop = env
    from datetime import timedelta
    t = ms(tnow() - timedelta(minutes=5))
    send(drop, "ariadc_a", snap(dss=_dss(t)))
    ad.import_pending(c)
    lv = _levels(c, drop, first=90)
    assert lv["SAN-90"] == ("低於門檻", "第一級") and lv["SAN-87"] == ("低於門檻", "第二級")
