"""suggest-segments 的 fmt=txt：產出直接餵給外部掃描機的 segments.txt。

review:true——這條路徑斷掉的方式很安靜：檔案下載得下來、mmap.sh 也裝得起來，
但掃描器一個網段都讀不到，要等到隔天早上看 log 才會發現整晚白跑。
所以這裡不只驗 HTTP 回應，還直接拿 scan_segments.load_segments 去解析產出物。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(ROOT / "APP" / "asset-module"))

import auth  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402
from scan_segments import load_segments  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_STUB_CREDENTIAL = "test-password-123"


def _client(tmp):
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)

    def _override_get_db():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    return TestClient(api.app), db_path


def _login(client, db_path):
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password(_STUB_CREDENTIAL))
    finally:
        conn.close()
    assert client.post(
        "/api/auth/login", json={"username": "tester", "password": _STUB_CREDENTIAL}
    ).status_code == 200


def _seed(db_path, rows):
    conn = db.get_connection(db_path)
    try:
        for i, (ip, loc) in enumerate(rows):
            db.insert_hardware(
                conn, asset_serial=f"SN{i:04d}", hostname=f"host{i}", ip=ip,
                physical_location=loc,
            )
    finally:
        conn.close()


def test_txt_requires_login():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            assert client.get(
                "/api/connections/suggest-segments?fmt=txt"
            ).status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_txt_is_parsable_by_the_scanner():
    """產出物要能被掃描器實際解析——這才是這支端點唯一的目的。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            _seed(db_path, [
                ("10.99.1.5", "板橋"), ("10.99.1.6", "板橋"),   # 同網段兩台 → 只該出現一次
                ("10.91.168.20", "內湖"),
            ])
            resp = client.get("/api/connections/suggest-segments?fmt=txt")
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/plain")
            # 檔名固定 segments.txt：mmap.sh 只認這個名字，帶時間戳就得人工改名
            assert 'filename="segments.txt"' in resp.headers["content-disposition"]

            out = Path(tmp) / "segments.txt"
            out.write_text(resp.text, encoding="utf-8")
            parsed = load_segments(out)

            assert parsed == ["10.99.1.0/24", "10.91.168.0/24"]  # 台數多的排前面
        finally:
            api.app.dependency_overrides.clear()


def test_txt_header_is_comments_only():
    """說明文字一律走整行註解，網段行保持乾淨。

    新版 load_segments 已經吃得下行尾註解，但掃描機上那份可能還是舊版——
    舊版會把 `10.99.1.0/24  # 板橋` 整行判成無法解析而略過。產出物要對舊版也安全，
    否則就是「清單看起來 129 段、實際掃 0 段」，而且要隔天早上才看得出來。
    """
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            _seed(db_path, [("10.99.1.5", "板橋")])
            text = client.get("/api/connections/suggest-segments?fmt=txt").text

            for line in text.splitlines():
                if line.strip() and not line.startswith("#"):
                    assert "#" not in line, f"網段行不可帶行尾註解：{line}"
            assert "產生時間" in text          # 這份清單是哪天的，要看得出來
            assert "10.99.1.0/24" in text
        finally:
            api.app.dependency_overrides.clear()


def test_bad_ip_is_warned_not_silently_dropped():
    """IP 格式壞掉的資產不屬於任何網段，掃描永遠看不到它們。

    不講的話，掃完發現「還有機器沒出現」會回頭懷疑掃描器，其實是資料本身有問題。
    """
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            _seed(db_path, [("10.99.1.5", "板橋"), ("不是IP", "板橋"), ("10.99.999.1", "板橋")])
            text = client.get("/api/connections/suggest-segments?fmt=txt").text

            assert "2 筆" in text and "IP 格式不正確" in text
            # 警告本身也必須是註解，不能污染網段清單
            assert all(
                "IP 格式不正確" not in ln or ln.startswith("#")
                for ln in text.splitlines()
            )
        finally:
            api.app.dependency_overrides.clear()


def test_json_is_still_the_default():
    """既有前端呼叫的是不帶 fmt 的版本，預設不能被改掉。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            _seed(db_path, [("10.99.1.5", "板橋")])
            resp = client.get("/api/connections/suggest-segments")
            assert resp.status_code == 200
            assert resp.json()["segments"][0]["cidr"] == "10.99.1.0/24"
        finally:
            api.app.dependency_overrides.clear()
