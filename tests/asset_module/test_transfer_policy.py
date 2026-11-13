"""檔案傳送（Q94）的規則：時段／大小矩陣、白名單資料夾、檔名、估算與分波。純函式，用注入的時鐘測每個邊界。

使用者拍板的規則（伺服器時間 Asia/Taipei，平日＝週一到週五，不查國定假日）：
  ≤10MB 任何時間；10～100MB 平日 08:30～15:00 不可；>100MB 平日只能 22:00～06:00；週末不限。
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import transfer_policy as pol  # noqa: E402

MB = pol.MB
# 2026-10-29 是週四；10-31 週六、11-01 週日、11-02 週一
THU, FRI, SAT, SUN, MON = (pol.at(2026, 10, 29), pol.at(2026, 10, 30), pol.at(2026, 10, 31),
                           pol.at(2026, 11, 1), pol.at(2026, 11, 2))


def _t(day, h, m=0):
    return day.replace(hour=h, minute=m)


def test_測試用的星期沒有算錯():
    assert [d.weekday() for d in (THU, FRI, SAT, SUN, MON)] == [3, 4, 5, 6, 0]


# ---------------------------------------------------------------- 時段與大小矩陣
SMALL, MID, BIG = 10 * MB, 100 * MB, 100 * MB + 1


@pytest.mark.parametrize("size", [1, SMALL])
@pytest.mark.parametrize("h,m", [(0, 0), (8, 29), (8, 30), (10, 0), (14, 59), (15, 0), (22, 0), (23, 59)])
def test_十MB以下任何時間都可以(size, h, m):
    for day in (THU, SAT):
        assert pol.allowed_at(size, _t(day, h, m))


@pytest.mark.parametrize("size", [SMALL + 1, MID])
@pytest.mark.parametrize("h,m,ok", [
    (0, 0, True), (8, 29, True), (8, 30, False), (10, 0, False), (14, 59, False),
    (15, 0, True), (22, 0, True), (23, 59, True)])
def test_十到一百MB平日開盤時間不可(size, h, m, ok):
    assert pol.allowed_at(size, _t(THU, h, m)) is ok
    assert pol.allowed_at(size, _t(MON, h, m)) is ok                 # 週一、週五同規則
    assert pol.allowed_at(size, _t(FRI, h, m)) is ok
    assert pol.allowed_at(size, _t(SAT, h, m)) is True               # 週末不限
    assert pol.allowed_at(size, _t(SUN, h, m)) is True


@pytest.mark.parametrize("h,m,ok", [
    (0, 0, True), (5, 59, True), (6, 0, False), (8, 30, False), (10, 0, False), (15, 0, False),
    (21, 59, False), (22, 0, True), (23, 59, True)])
def test_超過一百MB平日只有夜間窗(h, m, ok):
    assert pol.allowed_at(BIG, _t(THU, h, m)) is ok
    assert pol.allowed_at(BIG, _t(SAT, h, m)) is True
    assert pol.allowed_at(BIG, _t(SUN, h, m)) is True


def test_大小的邊界剛好在哪一邊():
    assert pol.size_band(10 * MB) == "small" and pol.size_band(10 * MB + 1) == "mid"
    assert pol.size_band(100 * MB) == "mid" and pol.size_band(100 * MB + 1) == "big"


def test_下一個允許時間():
    assert pol.next_allowed(MID, _t(THU, 10)) == _t(THU, 15)
    assert pol.next_allowed(BIG, _t(THU, 10)) == _t(THU, 22)
    assert pol.next_allowed(BIG, _t(THU, 6, 0)) == _t(THU, 22)
    assert pol.next_allowed(BIG, _t(FRI, 6, 30)) == _t(FRI, 22)
    assert pol.next_allowed(MID, _t(THU, 20)) == _t(THU, 20)               # 本來就允許
    assert pol.next_allowed(BIG, _t(THU, 23, 10)) == _t(THU, 23, 10)
    v = pol.verdict(MID, _t(THU, 10))
    assert not v.ok and v.next_at == _t(THU, 15) and "08:30" in v.why
    assert pol.verdict(SMALL, _t(THU, 10)).ok


def test_平日_08_15之後不開新的一波_十MB以下不受限():
    assert pol.may_start_new_wave(MID, _t(THU, 8, 14))
    assert not pol.may_start_new_wave(MID, _t(THU, 8, 15))
    assert not pol.may_start_new_wave(MID, _t(THU, 14, 59))
    assert pol.may_start_new_wave(MID, _t(THU, 15, 0))
    assert pol.may_start_new_wave(MID, _t(SAT, 9, 0))                      # 週末沒這條
    assert pol.may_start_new_wave(SMALL, _t(THU, 9, 0))                    # 10MB 以下本來就不限時間
    # 08:20 的中型檔：大小規則允許（還沒到 08:30），但不開新的一波 → 下一個可開始的時間是 15:00
    assert pol.allowed_at(MID, _t(THU, 8, 20))
    assert pol.next_start(MID, _t(THU, 8, 20)) == _t(THU, 15)
    assert pol.next_start(MID, _t(THU, 8, 0)) == _t(THU, 8, 0)


def test_伺服器時鐘固定是台北時間():
    assert pol.TZ.utcoffset(None) == dt.timedelta(hours=8)
    pol.set_clock(lambda: pol.at(2026, 10, 29, 9, 0))
    try:
        assert pol.now().hour == 9
    finally:
        pol.set_clock(None)


# ---------------------------------------------------------------- 白名單資料夾
@pytest.mark.parametrize("raw,expect", [
    ("D:\\Temp", "D:\\Temp"), ("d:\\temp", "D:\\Temp"), ("D:/Temp", "D:\\Temp"), ("D:\\Temp\\", "D:\\Temp"),
    ("C:\\Windows\\Temp", "C:\\Windows\\Temp"), ("c:\\windows\\temp\\", "C:\\Windows\\Temp")])
def test_Windows白名單接受的寫法(raw, expect):
    assert pol.check_folder("windows", raw) == expect


@pytest.mark.parametrize("raw", [
    "", "D:\\", "D:\\Temp\\..\\Windows", "D:\\Temp\\sub", "E:\\Temp", "C:\\Temp", "C:\\Windows", "C:\\Windows\\System32",
    "\\\\server\\share", "\\\\?\\D:\\Temp", "D:Temp", "Temp", "..\\Temp", "D:\\Temp\\..", "D:\\Temp:stream",
    "D:\\Temp\x00", "D:\\Tem p", "C:\\Windows\\Temp\\..\\..\\Users", "/tmp"])
def test_Windows白名單拒絕繞路(raw):
    with pytest.raises(pol.PolicyError):
        pol.check_folder("windows", raw)


@pytest.mark.parametrize("raw", ["/tmp", "/tmp/"])
def test_Linux白名單接受(raw):
    assert pol.check_folder("linux", raw) == "/tmp"
    assert pol.check_folder("aix", raw) == "/tmp"


@pytest.mark.parametrize("raw", [
    "", "/", "/etc", "/tmp/sub", "/tmp/..", "/tmp/../etc", "tmp", "./tmp", "//tmp", "/tmp//x", "/var/tmp", "/tmp\x00",
    "D:\\Temp", "/tmp\\x", "/TMP", "/tmp/.", "~/tmp", "/proc/self/root/tmp"])
def test_Linux白名單拒絕繞路(raw):
    with pytest.raises(pol.PolicyError):
        pol.check_folder("linux", raw)


def test_檔名規則():
    for ok in ["setup.msi", "agent_2.1-win.zip", "報表 (1).xlsx", "a+b=c,d@e#f%g[1].bin"]:
        assert pol.check_name("linux", ok) == ok
    for bad in ["", " x", "x ", "a/b", "a\\b", "..", ".", "a:b", "a'b", 'a"b', "a;b", "a|b", "a&b", "a`b", "a$b", "a<b",
                "a>b", "x.", "a\nb", "a\x00b", "x" * 200, "x.webit3part", "X.WEBIT3PART", "-rf", "~x", ".hidden"]:
        with pytest.raises(pol.PolicyError):
            pol.check_name("linux", bad)
    with pytest.raises(pol.PolicyError):
        pol.check_name("windows", "con.txt")
    with pytest.raises(pol.PolicyError):
        pol.check_name("windows", "LPT1")
    assert pol.check_name("linux", "con.txt") == "con.txt"             # 保留字只管 Windows


def test_遠端完整路徑只會落在白名單裡():
    assert pol.remote_path("windows", "d:\\temp", "a.zip") == "D:\\Temp\\a.zip"
    assert pol.remote_path("linux", "/tmp/", "a.zip") == "/tmp/a.zip"
    with pytest.raises(pol.PolicyError):
        pol.remote_path("linux", "/etc", "passwd")
    with pytest.raises(pol.PolicyError):
        pol.remote_path("linux", "/tmp", "../etc/passwd")


# ---------------------------------------------------------------- 估算與分波
def test_分波與估算():
    assert [len(w) for w in pol.waves(list(range(25)))] == [10, 10, 5]
    e = pol.estimate(480 * MB, 40, 3, 2, 5, 120)
    assert e["total_bytes"] == 480 * MB * 40 and e["waves"] == 4 and e["throughput_mbps"] == 10
    assert e["minutes"] == int(-(-(480 * 40 / 10 + 3 * 120) // 60))      # 傳輸秒數 + 三次波間暫停
    assert not e["needs_second_confirm"]
    assert pol.estimate(600 * MB, 40, 3, 2, 5, 0)["needs_second_confirm"]    # 24 GB > 20 GB
    assert pol.estimate(10 * MB, 0, 0, 2, 5, 0)["minutes"] == 0
    assert pol.clamp_concurrency(99) == pol.MAX_CONCURRENCY and pol.clamp_concurrency("x") == 2 and pol.clamp_concurrency(0) == 1
    assert pol.clamp_rate(7) == 5 and pol.clamp_rate(10) == 10
