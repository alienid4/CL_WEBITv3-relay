#!/usr/bin/env python3
"""打包「舊版 SSH 測連線小工具」成一個獨立 tar.gz（不放進 patch）。

    python .project/make_ssh_probe.py

產出：dist/tools/ssh_legacy_probe_YYYYMMDD_HHMM.tar.gz
內容：ssh_legacy_probe/{ssh_legacy_probe.py, run.sh, README.txt, paramiko-3.5.1-py3-none-any.whl}

為什麼不進 patch：這是一次性的診斷，用完就決定方向；放進 patch 會讓每台公司機都多一份
不需要的舊版 paramiko。run.sh 一律轉成 LF（Windows 上編輯會變 CRLF，bash 會報錯）。
"""
from __future__ import annotations

import io
import tarfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / ".project" / "tools" / "ssh_legacy_probe"
#: 工具本體 2026-09-15 搬進 APP（品質分析頁也用它），這裡直接打包那一份，不另存副本
TOOL_PY = REPO / "APP" / "asset-module" / "backend" / "tools" / "ssh_legacy_probe.py"
FILES = ["ssh_legacy_probe.py", "run.sh", "README.txt", "paramiko-3.5.1-py3-none-any.whl"]


def main() -> None:
    def path_of(name):
        return TOOL_PY if name == "ssh_legacy_probe.py" else SRC / name

    missing = [f for f in FILES if not path_of(f).exists()]
    if missing:
        raise SystemExit(f"缺檔：{missing}")
    out_dir = REPO / "dist" / "tools"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"ssh_legacy_probe_{datetime.now().strftime('%Y%m%d_%H%M')}.tar.gz"
    with tarfile.open(out, "w:gz") as tf:
        for name in FILES:
            data = path_of(name).read_bytes()
            if name.endswith((".sh", ".py", ".txt")):
                data = data.replace(b"\r\n", b"\n")
            info = tarfile.TarInfo(f"ssh_legacy_probe/{name}")
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            info.mtime = int(datetime.now().timestamp())
            tf.addfile(info, io.BytesIO(data))
    print(f"{out}（{out.stat().st_size / 1024:.0f} KB）")
    print("公司機：tar xzf " + out.name + " && cd ssh_legacy_probe && bash run.sh <IP>")


if __name__ == "__main__":
    main()
