#!/usr/bin/env python3
"""組「正式區一鍵離線安裝包」——setup.sh 吃的那一整包。

    python .project/make_install_pack.py [輸出目錄]

產出：  <輸出目錄>/資產盤點_正式區安裝包_YYYYMMDD_v<版本>.tar.gz
內容：  setup.sh  deploy.sh  backend/  frontend/(含預建 .output、無 node_modules)
        wheels.tar.gz(全相依離線輪子)  python311-standalone.tar.gz  node-*-linux-x64.tar.xz
        安裝說明.txt

目標情境（2026-09-15 使用者定）：**全裸 Linux x86_64、離線、無 python/node**——所以整包自帶
可攜 python3.11＋node20（setup.sh 偵測系統沒有就用內附的、裝到 /opt/webit3/runtime，不動系統）。

## 為什麼要有這支
2026-09-15 第一包是手工組的。手工＝下次升版又要重走一次、容易漏（少一個 wheel、忘了去 node_modules…）。
腳本化後升版就一行重生同款包。

## 前提
- 在**能上網的 PC／NB**跑（要抓 wheels／python／node）；離線正式機只消費產物、不跑這支。
- 目標平台鎖 Linux x86_64 / CPython 3.11。**若正式機平台或 Python 版本改變，改下面 TARGET_* 常數。**
- 前端 .output：預設沿用現有（開發時 build 的）；沒有就自動 npm build（要有 node）。--rebuild 強制重建。
- python／node tarball 會快取在 <輸出目錄>/.cache，重跑不重抓；--refresh 強制重抓。

## 大檔提醒
產出約 100MB（可攜 python ~49MB、node ~26MB、wheels ~32MB）。**不要 commit 進版控**——
這是產物不是原始碼（checks.py 的大檔關卡也會擋）。放 dist/（已 gitignore）或直接交付。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from datetime import datetime
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
    stage = out_dir / ".stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()

    # 腳本
    for s in ("setup.sh", "deploy.sh"):
        shutil.copy2(APP / s, stage / s)
    # backend（去雜物與資料）
    shutil.copytree(APP / "backend", stage / "backend",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.db", "data"))
    # frontend（含 .output、去 node_modules）
    ensure_frontend_output("--rebuild" in flags)
    shutil.copytree(APP / "frontend", stage / "frontend",
                    ignore=shutil.ignore_patterns("node_modules", ".nuxt", ".data"))
    if not (stage / "frontend" / ".output" / "server" / "index.mjs").is_file():
        raise SystemExit("包裡的 frontend 缺 .output/server/index.mjs，中止。")

    # wheels → wheels.tar.gz（.whl 放在壓縮檔根，setup.sh 會解進 $APP/wheels）
    wheeldir = out_dir / ".wheels"
    if wheeldir.exists():
        shutil.rmtree(wheeldir)
    build_wheels(wheeldir)
    _tar_gz([(w, w.name) for w in sorted(wheeldir.glob("*.whl"))], stage / "wheels.tar.gz")

    # 可攜 python / node（快取）
    shutil.copy2(fetch_python(cache), stage / "python311-standalone.tar.gz")
    node_tar = fetch_node(cache)
    shutil.copy2(node_tar, stage / node_tar.name)

    # 安裝說明
    (stage / "安裝說明.txt").write_text(
        "資產盤點模組 — 正式區 一鍵離線安裝包\n"
        f"版本：v{ver}　對象：全新 Linux x86_64、離線、無 python/node 也 OK（本包自帶可攜版）\n\n"
        "安裝（目標主機 root）：\n"
        "  tar xzf 資產盤點_正式區安裝包_*.tar.gz\n"
        "  cd .stage 2>/dev/null || cd installpack 2>/dev/null || cd .\n"
        "  sudo bash setup.sh\n"
        "會逐步問 IP／服務帳號／埠，自動用內附可攜 python/node＋離線 wheels＋預建前端，建空庫＋設 admin。\n"
        "出錯畫面會印 log 路徑，貼回給開發者。重跑安全（冪等）。\n",
        encoding="utf-8")

    # 最終單一檔（內容放在壓縮檔根，解開就看到 setup.sh）
    stamp = datetime.now().strftime("%Y%m%d")
    final = out_dir / f"資產盤點_正式區安裝包_{stamp}_v{ver}.tar.gz"
    _tar_gz([(p, p.name) for p in sorted(stage.iterdir())], final)
    shutil.rmtree(stage)
    shutil.rmtree(wheeldir)
    mb = final.stat().st_size // 1048576
    log(f"完成：{final}（{mb} MB）")
    print(f"\n交付這個檔給正式機：\n  {final}")


if __name__ == "__main__":
    main()
