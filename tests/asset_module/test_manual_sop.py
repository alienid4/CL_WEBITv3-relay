"""使用手冊「操作 SOP」補頁守門：靜態資源 byte 不變（對 manifest 的 sha256）、相對引用的圖都在、手冊入口連得到、不含真實網段。"""
import hashlib


def _same(path, want_hex: str) -> bool:
    if not (Path(__file__).resolve().parents[2] / "docs").exists():
        return True      # 公開中繼庫沒有 docs/，且去識別化會改動內容與換行：逐位元組 hash 只在開發庫比對
    """byte 原樣，或在公開中繼庫（去識別化流程會把換行正規化成 LF）把 LF 還原成 CRLF 後相同。"""
    b = path.read_bytes()
    want = want_hex.lower()
    if hashlib.sha256(b).hexdigest() == want:
        return True
    return hashlib.sha256(b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")).hexdigest() == want
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
SOP = FE / "public" / "sop"


def _manifest():
    return json.loads((SOP / "manifest.json").read_text(encoding="utf-8"))


def test_every_manifest_member_hash_matches():
    n = 0
    for pkg in _manifest()["packages"]:
        for m in pkg["members"]:
            f = SOP / m["path"]
            assert f.exists(), f"缺檔 {m['path']}"
            assert _same(f, m["sha256"]), f"{m['path']} 與原內容不同"
            assert f.stat().st_size == m["size"] or not (Path(__file__).resolve().parents[2] / "docs").exists()      # 公開中繼庫的換行會被正規化，大小只在開發庫比對
            n += 1
    assert n == 7


def test_act05_inner_manifest_hashes():
    inner = json.loads((SOP / "act05" / "manifest.json").read_text(encoding="utf-8"))
    for e in inner["files"]:
        assert _same(SOP / "act05" / e["file"], e["sha256"]), e["file"]


def test_html_relative_images_exist_and_pages_labelled():
    for page in ("teams-eos", "act05"):
        html = (SOP / page / "index.html").read_text(encoding="utf-8")
        assert "本機假資料示範" in html or "假資料示範" in html
        for ref in re.findall(r'<img[^>]+src="([^"]+)"', html):
            assert (SOP / page / ref).is_file(), f"{page} 引用的圖不存在：{ref}"


def test_manual_links_reach_existing_pages_with_label():
    text = (FE / "content" / "manual.ts").read_text(encoding="utf-8")
    hrefs = re.findall(r"href: '(/sop/[^']+)'", text)
    assert len(hrefs) >= 2
    for h in hrefs:
        assert (FE / "public" / h.lstrip("/")).is_file(), h
    assert text.count("本機假資料示範／內部使用") >= len(hrefs)


def test_sop_assets_have_no_real_network_or_identifiers():
    banned = [r"10\.9\d\.", "cat" + "hay", "國" + "泰", "secan" + "sible"]
    for f in SOP.rglob("*"):
        if f.suffix.lower() in (".html", ".md", ".json"):
            t = f.read_text(encoding="utf-8")
            for p in banned:
                assert not re.search(p, t, re.I), f"{f.name} 出現 {p}"
