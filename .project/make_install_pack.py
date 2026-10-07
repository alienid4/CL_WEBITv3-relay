#!/usr/bin/env python3
"""組「正式區一鍵離線安裝包」——拆成 runtime 包 + app 包兩份。

    python .project/make_install_pack.py [輸出目錄] [--app-only] [--rebuild] [--refresh]

產出（<輸出目錄>/）：
    install.sh                                      ← 使用者只要跑這一支
    webit3-runtime-linux-x64-py311-node20.tar.gz    ← 約 93 MB，很少動
      └ 切成 .part00/.part01/.part02（每份 40 MB）+ .splits.sha256
    webit3-app-v<版本>.tar.gz                        ← 約 12 MB，每次升版都重出
    各包的 .sha256

## 為什麼拆兩包（2026-10-06 使用者定）
整包 100 MB 裡有 93 MB 是**零公司內容的第三方執行環境**（可攜 Python 46.7MB、
Node 25.0MB、離線 wheels 21.0MB），只有 12 MB 是應用程式。每次升版都重傳 100 MB
沒有意義。拆開之後：
  · 第一次安裝 → 兩包都傳（約 105 MB）
  · 之後升版　 → **只傳 app 包（12 MB）**，install.sh 偵測 runtime 已裝好就整段跳過

⚠️ wheels 放在 runtime 包裡（它也是第三方、很少變）。**改動 backend/requirements.txt
之後 runtime 包必須重出**，否則新相依沒有對應的 wheel，離線機裝不起來。
`--app-only` 不會重出 runtime 包，所以改了 requirements.txt 就不要用它。

## 安裝端（使用者只打一行）
    sudo bash /tmp/install.sh
install.sh 自己做完：搬到 /opt/webit3/install（0700）→ sha256 校驗 → 合併分割檔
→ 解 runtime（已裝過就跳過）→ 解 app → 跑 setup.sh → 印驗收結果。
程式安裝路徑沿用 setup.sh 的既有預設：/opt/webit3/{app,data,venv,runtime}。

## 前提
- 在**能上網的 PC／NB**跑（要抓 wheels／python／node）；離線正式機只消費產物、不跑這支。
- 目標平台鎖 Linux x86_64 / CPython 3.11。**若正式機平台或 Python 版本改變，改下面
  TARGET_* 常數，並且一定要重出 runtime 包。**
- 前端 .output：預設沿用現有（開發時 build 的）；沒有就自動 npm build（要有 node）。--rebuild 強制重建。
- python／node tarball 會快取在 <輸出目錄>/.cache，重跑不重抓；--refresh 強制重抓。

## 出包前的殘留掃描（2026-10-06 加）
app 包要放 **public relay 的 Releases**（公司正式機連不到私有庫），所以標準是
「能不能公開」。打包完成後會掃**包裡的實際內容**（含 `.output` 編譯產物）找公司識別字
與私有網段位址，命中就**刪掉剛產出的包並 exit 1**——不留壞包、不只印警告。
規則不自己抄，全部來自 `.project/desensitize_rules.py` 與
`tests/test_no_private_ip_in_shipped.py`。細節見 `scan_pack_or_die` 上面那段註解。

## 大檔提醒
**不要 commit 進版控**——這是產物不是原始碼（checks.py 的大檔關卡也會擋）。
放 dist/（已 gitignore）或直接交付。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "APP" / "asset-module"
REQ = APP / "backend" / "requirements.txt"

# ---- 目標平台（正式機）。改這裡就能出別的平台/版本的包。----
TARGET_PY = "3.11"          # 可攜 python 版本 → wheels 的 cp 版本
TARGET_ABI = "311"
TARGET_PLATFORMS = ["manylinux2014_x86_64", "manylinux_2_28_x86_64", "manylinux_2_17_x86_64"]
NODE_MAJOR = "20"
PBS_RELEASE_API = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
NODE_INDEX = "https://nodejs.org/dist/index.json"


def log(msg: str) -> None:
    print(f"[make_install_pack] {msg}", flush=True)


def version() -> str:
    return json.loads((APP / "backend" / "version.json").read_text(encoding="utf-8"))["version"]


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "webit3-installpack"})
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 - 固定的官方來源
        return r.read()


def _download_to(url: str, dst: Path) -> None:
    if dst.exists() and dst.stat().st_size > 0:
        log(f"快取沿用：{dst.name}（{dst.stat().st_size // 1048576} MB）")
        return
    log(f"下載：{url}")
    dst.write_bytes(_fetch(url))
    log(f"  → {dst.name}（{dst.stat().st_size // 1048576} MB）")


def build_wheels(dst: Path) -> int:
    """把 requirements.txt 全相依抓成 cp<ABI> manylinux 離線輪子。"""
    dst.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "download", "--only-binary=:all:",
           "--python-version", TARGET_ABI, "--implementation", "cp"]
    for p in TARGET_PLATFORMS:
        cmd += ["--platform", p]
    # sniffio 有時不會被多 --platform 的解析帶到，明確補；重跑會略過已存在的
    cmd += ["-r", str(REQ), "sniffio", "-d", str(dst)]
    subprocess.run(cmd, check=True)
    n = len(list(dst.glob("*.whl")))
    if n < 20:
        raise SystemExit(f"wheels 只有 {n} 個，疑似相依沒抓齊，中止。")
    log(f"wheels：{n} 個")
    return n


def ensure_frontend_output(rebuild: bool) -> None:
    out = APP / "frontend" / ".output"
    if out.is_dir() and not rebuild:
        log("前端 .output 已存在，沿用（要重建加 --rebuild）")
        return
    log("build 前端（.output 缺或 --rebuild）")
    fe = APP / "frontend"
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("找不到 npm，且沒有現成 .output——請先在有 node 的機器 npm run build")
    if not (fe / "node_modules").is_dir():
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=fe, check=True, shell=(sys.platform == "win32"))
    subprocess.run([npm, "run", "build"], cwd=fe, check=True, shell=(sys.platform == "win32"))
    if not out.is_dir():
        raise SystemExit("build 後仍找不到 .output，中止。")


def fetch_python(cache: Path) -> Path:
    data = json.loads(_fetch(PBS_RELEASE_API))
    pat = re.compile(rf"cpython-{re.escape(TARGET_PY)}\.\d+\+\d+-x86_64-unknown-linux-gnu-install_only\.tar\.gz$")
    urls = [a["browser_download_url"] for a in data.get("assets", []) if pat.search(a["name"])]
    if not urls:
        raise SystemExit("找不到 python-build-standalone 的 3.11 linux install_only 資產")
    dst = cache / "python311-standalone.tar.gz"
    _download_to(urls[0], dst)
    return dst


def fetch_node(cache: Path) -> Path:
    idx = json.loads(_fetch(NODE_INDEX))
    ver = next((e["version"] for e in idx if e["version"].startswith(f"v{NODE_MAJOR}.")), None)
    if not ver:
        raise SystemExit(f"nodejs 沒有 v{NODE_MAJOR}.x")
    name = f"node-{ver}-linux-x64.tar.xz"
    dst = cache / name
    _download_to(f"https://nodejs.org/dist/{ver}/{name}", dst)
    return dst


def _tar_gz(items: list[tuple[Path, str]], out: Path) -> None:
    """用 tarfile 建（跨平台、避開 Windows 磁碟機冒號被當遠端主機的雷）。"""
    with tarfile.open(out, "w:gz") as t:
        for path, arc in items:
            t.add(path, arcname=arc)


# ---- 出包前的最後一關：掃「包裡的實際內容」有沒有識別字 ----
#
# 為什麼要在這裡再掃一次（2026-10-06）：
# checks.py 的 commit 前殘留掃描**先套替換表再掃**，回答的是「送進 relay 之後還剩
# 不剩」。app 包走的是**另一條管道**——它從 APP/ 原封不動打包，不經過去識別化，
# 所以 checks.py 判「乾淨」對這一包完全不成立：凡是在替換表裡的位址（開發機那台就是）
# 都會先被換成 YOUR_SERVER_IP，那邊永遠看不到它，而 app 包裡它是原值。
#
# app 包要放 public relay 的 Releases（公司正式機連不到私有庫），標準是「能不能
# 公開」。所以這裡掃的是**原始內容、不套替換表**，而且掃的是 tar 裡真正的 member
# （含 .output 的編譯產物——改了 .vue 沒重 build 時舊字串還留在那裡）。
#
# 命中就刪掉剛產出的包並 exit 1。不留壞包、也不只印警告——下次有人塞東西進去，
# 包根本產不出來，不用靠人記得檢查。
#
# ⚠️ 樣式**一條都不在這裡自己抄**。這個 repo 已經被「兩份複製的規則表」咬過兩次
# （2026-08-19，見 desensitize_rules.py 的 docstring），所以兩類規則各自只有一個來源：
#   · 公司識別字／主機名／私鑰／寫死密碼 → .project/desensitize_rules.py RESIDUAL_PATTERNS
#   · 私有網段位址（含示範網段豁免、同行標記豁免）→ tests/test_no_private_ip_in_shipped.py
# 這裡只做一件這兩邊都做不到的事：**掃 tar 裡真正被交付出去的那份內容**。
#   （checks.py 掃 git 工作區且先套替換表；那支測試掃工作區檔案系統。
#     兩者都不是「包」——包可能用的是比原始碼舊的 .output，或被誰手動塞了東西。）

# 只寫到網段前綴、沒寫滿四段的提及（形如「<網段>.x」「<網段>.*」）。四段式的位址由
# tests 那支的 PRIVATE_IP 管；前綴式不是合法位址、它抓不到，但一樣指得出是哪個機房。
# （這裡刻意不舉真實網段當例子——那正是這道關卡要擋的東西。）
# 10.9[0-8] 的範圍跟 checks.py 的 _SEG 對齊（10.99 是示範網段，不含）。
SEGMENT_PREFIX_PATTERNS: list[tuple[str, str]] = [
    (r"\b10\.9[0-8]\.(?:x\b|\*)", "公司內網網段前綴（示範請用 10.99.x）"),
    (r"\b192\.168\.\d{1,3}\.(?:x\b|\*)", "私有網段前綴 192.168/16"),
    (r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.(?:x\b|\*)", "私有網段前綴 172.16/12"),
]

# 包裡會出現、但不是識別字的誤報。寫在這裡要附理由，不准當垃圾桶用。
PACK_SCAN_ALLOW_SUBSTR: tuple[str, ...] = (
    "frontend/node_modules/",       # 正常不會被打包（ignore_patterns 已擋），保險
)

# .output 的編譯產物／source map／內嵌 svg，副檔名不在 TEXT_EXT 裡但一定要掃。
_EXTRA_TEXT_EXT = {".mjs", ".cjs", ".map", ".svg"}


def _pack_scan_rules():
    """把兩個既有規則來源拉進來（**自己不抄一份**），回 (識別字樣式, 私有IP規則, 文字副檔名)。

    **fail closed**：任一來源匯入不到就中止，不准靜默跳過。這支是閘門，
    「規則找不到」必須等於「不出包」，否則閘門會在沒人注意時變成裝飾
    （relay 副本裡 desensitize_rules.py 不存在，那裡本來也不該出官方包）。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(REPO / "tests"))
    try:
        from desensitize_rules import RESIDUAL_PATTERNS, TEXT_EXT
    except ImportError as exc:
        raise SystemExit(
            "找不到 .project/desensitize_rules.py，無法做出包前的殘留掃描 → 中止，不產出包。"
            f"（{exc}）"
        ) from exc
    try:
        from test_no_private_ip_in_shipped import DEMO_SEGMENT, MARKER, PRIVATE_IP
    except ImportError as exc:
        raise SystemExit(
            "找不到 tests/test_no_private_ip_in_shipped.py 的私有網段規則 → 中止，不產出包。"
            f"（{exc}）"
        ) from exc
    ident = list(RESIDUAL_PATTERNS) + SEGMENT_PREFIX_PATTERNS
    return ident, (PRIVATE_IP, DEMO_SEGMENT, MARKER), set(TEXT_EXT) | _EXTRA_TEXT_EXT


def scan_pack_or_die(pack: Path, extra_files: list[Path] | None = None) -> None:
    """掃 tar 裡每個文字 member（＋隨包一起交付的檔）；命中就刪包 + exit 1。

    私有網段位址的豁免語意跟 tests 那支完全一樣：示範網段（10.20/10.30/10.99）免標記，
    其餘要同行 `webit3:allow-private-ip`。**`.output` 的 minify 產物帶不了註解標記**，
    所以它的允許清單從包裡的原始碼推導——bundle 沒有自己的豁免管道，
    而且「改完沒重 build」會因此紅燈（舊字串還留在產物裡）。
    """
    ident_patterns, (private_ip, demo_seg, marker), text_ext = _pack_scan_rules()
    compiled = [(re.compile(pat), why) for pat, why in ident_patterns]
    hits: list[str] = []            # 識別字：直接成立
    ip_hits: list[tuple[str, str]] = []   # (位址, 說明) 待用 approved 過濾
    approved: set[str] = set()
    scanned = 0

    def scan_text(label: str, txt: str, *, is_output: bool) -> None:
        nonlocal scanned
        scanned += 1
        reported = False
        for lineno, line in enumerate(txt.splitlines(), 1):
            if not reported:
                for rx, why in compiled:
                    m = rx.search(line)
                    if m:
                        hits.append(f"{label}:{lineno} [{why}] {m.group(0)[:60]}")
                        reported = True     # 同一檔的識別字報第一筆就夠，不要洗版
                        break
            for m in private_ip.finditer(line):
                ip = m.group(0)
                if demo_seg.match(ip):
                    continue
                if not is_output and marker in line:
                    approved.add(ip)        # 人簽過名的例外
                    continue
                ip_hits.append((ip, f"{label}:{lineno} [私有網段位址] {ip}"))

    with tarfile.open(pack, "r:gz") as t:
        for member in t.getmembers():
            if not member.isfile():
                continue
            name = member.name
            if any(s in name for s in PACK_SCAN_ALLOW_SUBSTR):
                continue
            suffix = Path(name).suffix.lower()
            # 沒有副檔名的（例如 LICENSE、.output 裡的雜檔）也當文字掃。
            if suffix and suffix not in text_ext:
                continue
            if member.size > 8 * 1024 * 1024:
                continue
            f = t.extractfile(member)
            if f is None:
                continue
            scan_text(name, f.read().decode("utf-8", errors="ignore"),
                      is_output="/.output/" in f"/{name}")

    for p in extra_files or []:
        if p.is_file():
            scan_text(p.name, p.read_text(encoding="utf-8", errors="ignore"),
                      is_output=False)

    hits += sorted({msg for ip, msg in ip_hits if ip not in approved})

    # 「0 命中」必須能跟「根本沒掃到檔案」分辨開——後者的綠燈是假的。
    if scanned < 100:
        pack.unlink(missing_ok=True)
        raise SystemExit(
            f"殘留掃描只掃到 {scanned} 個文字檔，不合理（app 包裡應該有幾百個）——"
            "這種『通過』是假的，已刪除剛產出的包。"
            "請檢查副檔名清單／PACK_SCAN_ALLOW_SUBSTR 是否被改壞。")

    if hits:
        log(f"殘留掃描命中 {len(hits)} 處（掃過 {scanned} 個文字檔）：")
        for h in hits:
            print(f"    {h}", flush=True)
        pack.unlink(missing_ok=True)
        (pack.parent / (pack.name + ".sha256")).unlink(missing_ok=True)
        raise SystemExit(
            "\napp 包要放 public relay，含識別字不可出包 → 已刪除剛產出的包。\n"
            "改掉上面列出的位置後重跑；若改的是 .vue／.ts，記得加 --rebuild 讓 "
            ".output 一起更新（舊字串會留在編譯產物裡）。"
        )
    # 刻意把掃描規模與豁免清單印出來，讓「乾淨」這句話有依據可看。
    log(f"殘留掃描：乾淨（掃過包裡 {scanned} 個文字檔，"
        f"同行標記核可的位址 {sorted(approved) or '無'}）")


RUNTIME_PACK = "webit3-runtime-linux-x64-py311-node20.tar.gz"
SPLIT_BYTES = 40 * 1024 * 1024      # 每份 40 MB（使用者 2026-10-06 定）


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_lf(path: Path, text: str) -> None:
    """寫文字檔，**強制 LF**。

    這支在 Windows 跑，而 Path.write_text 走文字模式會把 \\n 翻成 \\r\\n。
    產物是要在 Linux 上被 `sha256sum -c` 讀、被 bash 執行的，所以不能帶 CR：
      · .sha256 帶 CR → 自己寫的解析（awk 取檔名）會拿到 "檔名\\r"，判成檔案不存在
      · .sh 帶 CR 　　→ bash 直接噴 `$'\\r': command not found`
        （2026-07-28 公司 198-014 踩過，整支 setup.sh 跑不起來）
    2026-10-06 又踩一次：.sha256 是 CRLF，導致整包校驗那一步變成「全部略過」——
    看起來過了，其實沒驗。所以統一走這個函式，不用 write_text。
    """
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))


def _copy_sh_lf(src: Path, dst: Path) -> None:
    """複製 shell 腳本並保證 LF（不信任工作區的行尾狀態）。

    .gitattributes 有 `*.sh text eol=lf`，但那只管 git 的 checkout／commit；
    編輯器或工具直接寫出 CRLF 時工作區還是會變 CRLF，而這支是**從工作區打包**的。
    所以這裡自己再轉一次，讓包裡的腳本在任何情況下都是 LF。
    """
    dst.write_bytes(src.read_bytes().replace(b"\r\n", b"\n"))


def _write_sha256(target: Path) -> Path:
    """產 sha256sum 格式的清單檔（離線機可直接 sha256sum -c）。"""
    out = target.parent / (target.name + ".sha256")
    _write_lf(out, "{}  {}\n".format(_sha256(target), target.name))
    log(f"sha256：{out.name}")
    return out


def _split(target: Path, size: int = SPLIT_BYTES) -> list[Path]:
    """切成 <name>.part00、part01…（**後綴補零**）並產 .splits.sha256。

    後綴一定要補零：合併端是 `cat *.part*`，靠的是 shell 的字典序。沒補零的話
    part0…part10 會排成 0,1,10,2,…，合出來的檔案大小正常、解壓才爆，而且錯誤訊息
    長得像「包壞了」而不是「順序錯了」——所以合併後一定要再驗整包 sha256。

    清單檔刻意叫 .splits.sha256 而**不是** .parts.sha256：後者會被 `*.part*` 這個
    glob 命中，於是合併時被 cat 進結果裡，合出一個壞檔（2026-10-06 實際踩到）。
    """
    for old in target.parent.glob(target.name + ".part*"):
        old.unlink()
    parts: list[Path] = []
    with target.open("rb") as f:
        idx = 0
        while True:
            chunk = f.read(size)
            if not chunk:
                break
            part = target.parent / "{}.part{:02d}".format(target.name, idx)
            part.write_bytes(chunk)
            parts.append(part)
            idx += 1
    listing = target.parent / (target.name + ".splits.sha256")
    _write_lf(listing, "".join("{}  {}\n".format(_sha256(p), p.name) for p in parts))
    log(f"切割：{len(parts)} 份（每份 {size // 1048576} MB）→ {listing.name}")
    return parts


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    out_dir = Path(args[0]).resolve() if args else (REPO / "dist" / "install")
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = out_dir / ".cache"
    cache.mkdir(exist_ok=True)
    if "--refresh" in flags:
        for f in cache.glob("*.tar.*"):
            f.unlink()

    ver = version()
    log(f"版本 v{ver}　目標 Linux x86_64 / CPython {TARGET_PY}")

    rt_pack = out_dir / RUNTIME_PACK
    app_only = "--app-only" in flags

    # ========== runtime 包（第三方執行環境，零公司內容，很少動）==========
    if app_only and rt_pack.exists():
        log(f"--app-only：沿用現有 {rt_pack.name}"
            f"（{rt_pack.stat().st_size // 1048576} MB）")
        log("  [注意] 若這次改過 backend/requirements.txt，不可以用 --app-only："
            "新相依沒有對應的 wheel，離線機會裝不起來")
    else:
        stage_rt = out_dir / ".stage-rt"
        if stage_rt.exists():
            shutil.rmtree(stage_rt)
        stage_rt.mkdir()

        # wheels → wheels.tar.gz（.whl 放在壓縮檔根，setup.sh 會解進 $APP/wheels）
        wheeldir = out_dir / ".wheels"
        if wheeldir.exists():
            shutil.rmtree(wheeldir)
        build_wheels(wheeldir)
        _tar_gz([(w, w.name) for w in sorted(wheeldir.glob("*.whl"))],
                stage_rt / "wheels.tar.gz")
        shutil.rmtree(wheeldir)

        # 可攜 python / node（快取）
        shutil.copy2(fetch_python(cache), stage_rt / "python311-standalone.tar.gz")
        node_tar = fetch_node(cache)
        shutil.copy2(node_tar, stage_rt / node_tar.name)

        _write_lf(stage_rt / "RUNTIME.txt",
            "webit3 執行環境包（第三方內容，不含任何公司資料）\n"
            "========================================\n"
            f"可攜 CPython {TARGET_PY}（python-build-standalone，x86_64 linux-gnu，install_only）\n"
            f"可攜 Node {NODE_MAJOR}（nodejs.org 官方 linux-x64 建置）\n"
            "離線 wheels（backend/requirements.txt 的全部相依，cp311 manylinux）\n"
            "\n"
            "最低 glibc 需求：2.28\n"
            "  · 可攜 Python 的 ELF 版本化符號最高到 GLIBC_2.17\n"
            "  · wheels 裡有一個 manylinux_2_28 的（sspilib）→ 整包門檻由它決定\n"
            "  目標機確認方式： ldd --version | head -1\n"
            "\n"
            "這一包只有第一次安裝要傳。裝好之後它的內容在 /opt/webit3/runtime，\n"
            "之後升版只需要 app 包（install.sh 會自動偵測並跳過這一包）。\n"
            "\n"
            "改動 backend/requirements.txt 之後必須重出這一包。\n",
        )

        _tar_gz([(p, p.name) for p in sorted(stage_rt.iterdir())], rt_pack)
        shutil.rmtree(stage_rt)
        log(f"runtime 包：{rt_pack.name}（{rt_pack.stat().st_size // 1048576} MB）")

    _write_sha256(rt_pack)
    _split(rt_pack)

    # ========== app 包（應用程式，每次升版都重出）==========
    stage = out_dir / ".stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()

    # 腳本（install.sh 是使用者唯一要跑的那一支）
    for s in ("setup.sh", "deploy.sh", "install.sh"):
        _copy_sh_lf(APP / s, stage / s)
    # backend（去雜物與資料）
    shutil.copytree(APP / "backend", stage / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.db", "data"))
    # frontend（含 .output、去 node_modules）
    ensure_frontend_output("--rebuild" in flags)
    shutil.copytree(APP / "frontend", stage / "frontend",
                    ignore=shutil.ignore_patterns("node_modules", ".nuxt", ".data"))
    if not (stage / "frontend" / ".output" / "server" / "index.mjs").is_file():
        raise SystemExit("包裡的 frontend 缺 .output/server/index.mjs，中止。")

    # 安裝說明（包內那份；完整版見 DOC/06_正式區離線安裝說明.md）
    _write_lf(stage / "安裝說明.txt",
        "資產盤點模組 — 正式區 一鍵離線安裝（app 包）\n"
        f"版本：v{ver}\n"
        "========================================\n"
        "使用者只要打這一行，其餘全部由腳本自己處理：\n"
        "\n"
        "    sudo bash /tmp/install.sh\n"
        "\n"
        "把下載到的檔案跟 install.sh 放在同一個目錄即可（放 /tmp 沒問題，\n"
        "install.sh 會自己搬到 /opt/webit3/install 再執行）。\n"
        "不需要人工 mkdir / chmod / mv / sha256sum —— 腳本自己做。\n"
        "\n"
        "第一次安裝要兩包：\n"
        f"  {RUNTIME_PACK}（或它的 .part00/.part01/.part02）\n"
        f"  webit3-app-v{ver}.tar.gz\n"
        "之後升版只要 app 包（約 12 MB），runtime 不用再傳。\n"
        "\n"
        "安裝路徑： /opt/webit3/app  /opt/webit3/data  /opt/webit3/venv  /opt/webit3/runtime\n"
        "服務：     webit3-api(8000)  webit3-web(3000)\n"
        "           webit3-backup.timer(02:00)  webit3-cleanup.timer(03:00)\n"
        "安裝途中會問：對外服務 IP、跑服務的系統帳號、埠衝突處理，最後設 admin 密碼。\n"
        "\n"
        "入站 3000/8000 由安裝腳本開本機 firewalld（失敗只警告、不中止安裝）。\n"
        "出站（SSH 22、WinRM 5985、網段掃描埠）屬網段層級，須另向網路單位申請。\n"
        "\n"
        "出錯時腳本會印出是什麼失敗、該怎麼辦，以及完整 log 路徑\n"
        "（/opt/webit3/data/logs/setup_*.log）。修好後重跑安全（冪等）。\n"
        "\n"
        "-- 驗收／數字核對（不靠 AI，程式獨立重算每頁數字）--\n"
        "  sudo -u <服務帳號> ASSET_DB_PATH=/opt/webit3/data/asset.db \\\n"
        "    /opt/webit3/venv/bin/python /opt/webit3/app/backend/verify_numbers.py \\\n"
        "    --html /opt/webit3/data/logs/數字核對報告.html\n"
        "全過印「全部通過 ✅」並回離開碼 0；有對不上回非 0（可接 CI／排程）。\n"
        "--html 那份是自成一頁的 HTML 報告，可直接寄出／存檔給主管留存。\n"
        "（帳號／路徑若安裝時改過，照 /opt/webit3/data/install.conf 內的值換。）\n"
        "\n"
        "詳細的事前確認、連線需求表、常見失敗排查：見交付文件\n"
        "「正式區離線安裝說明」（DOC/06_正式區離線安裝說明.md）。\n",
    )

    app_pack = out_dir / f"webit3-app-v{ver}.tar.gz"
    _tar_gz([(p, p.name) for p in sorted(stage.iterdir())], app_pack)
    shutil.rmtree(stage)
    log(f"app 包：{app_pack.name}（{app_pack.stat().st_size // 1048576} MB）")

    # 出包前的最後一關：掃包裡的實際內容。沒過就刪包 + exit 1（不產出 .sha256）。
    scan_pack_or_die(app_pack)

    _write_sha256(app_pack)
    # app 包 12 MB，不切割（使用者 2026-10-06：40MB 切割只套用在 runtime 包）

    # install.sh 要能單獨拿到 —— 它比任何一包都先被執行。
    _copy_sh_lf(APP / "install.sh", out_dir / "install.sh")

    print("\n交付這些檔給正式機（放同一個目錄，然後 sudo bash install.sh）：")
    print(f"  {out_dir / 'install.sh'}")
    for f in sorted(out_dir.glob("webit3-*")):
        print(f"  {f}　（{f.stat().st_size:,} bytes）")
    print("\n升版時只要重出並重傳 app 包：")
    print("  py -3 .project/make_install_pack.py dist --app-only")


if __name__ == "__main__":
    main()
