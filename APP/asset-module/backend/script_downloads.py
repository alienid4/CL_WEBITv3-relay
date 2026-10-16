"""可以單獨下載的檢核／維運腳本清單。

**為什麼要有這個**：這些腳本的實際動線是「網頁下載 → scp 到 AIX／Linux 主機 →
ksh/sh 跑」。在這之前，要拿其中一支得下載整包 3.3MB 的更新包再自己翻出來——
為了一支 60KB 的檔案。

**為什麼不寫死檔名**：檔名會變（`fcbaixsh` 正在改名成 `fcb_aix.ksh`）。寫死等於
改名那天下載就壞掉，而且壞在「按鈕還在、點了 404」這種最難聯想的地方。這裡改成
**列出 scripts/ 底下符合條件的檔**：條件是「副檔名是 shell 腳本」或「第一行是
shebang」——改名、新增一支 Windows 版，清單都會自己跟上。
說明文件（.md）不符合條件，所以不會混進來。

**安全上刻意的幾件事**（金融業，會被稽核）：
  · 下載只認 `list_scripts()` 列出來的**檔名**，不接受呼叫端給路徑。想穿越目錄的
    輸入根本對不到清單裡任何一項，在「查表」這一步就落空了，不是靠字串過濾。
  · 再加一層 `resolve()` 後必須仍在 scripts/ 底下的檢查（擋 symlink 指到外面）。
  · 只回傳一般檔案，不跟隨符號連結、不列目錄。
  · 內容一律以 **LF** 送出。這些檔案要在 AIX/Linux 跑，CRLF 會讓 `#!/usr/bin/ksh`
    帶著 `\\r` 直接執行失敗（2026-07-28、2026-09-22 各踩過一次）。
  · 清單附上腳本自己的版本戳（`SCRIPTV="SV.AIX.YYYYMMDDHHMM"`）、大小與 sha256，
    讓人知道手上這份是哪一版——沒有這些資訊的下載鈕等於要人猜。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

#: 部署後的版面：backend/ 與 scripts/ 是同一層（$APP/backend、$APP/scripts），
#: repo 裡也是（APP/asset-module/backend、APP/asset-module/scripts）。兩邊一致。
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

#: 副檔名白名單。沒有副檔名的（fcbaixsh、fcbrhelsh）改看第一行 shebang。
SHELL_SUFFIX = {".sh", ".ksh", ".ksh93", ".bash", ".bat", ".ps1"}

_SCRIPTV_RE = re.compile(r'^\s*SCRIPTV\s*=\s*["\']?([A-Za-z0-9._\-]+)', re.MULTILINE)


def _is_downloadable(p: Path) -> bool:
    """這個檔是不是「給人下載去別台跑」的腳本。"""
    if p.is_symlink() or not p.is_file():
        return False           # symlink 不跟著走，目錄不列
    if p.name.startswith("."):
        return False
    if p.suffix.lower() in SHELL_SUFFIX:
        return True
    if p.suffix:
        return False           # 有副檔名但不是腳本（.md 說明文件就落在這裡）
    try:
        with p.open("rb") as fh:
            return fh.read(2) == b"#!"
    except OSError:
        return False


def _describe(text: str, name: str) -> tuple[str, str]:
    """回傳（一句話說明, 平台）。兩個都只是給人看的標示，取不到就給空字串。"""
    title = ""
    for line in text.splitlines()[1:12]:      # 跳過 shebang，只看開頭那幾行註解
        s = line.strip()
        if not s.startswith("#"):
            continue
        s = s.lstrip("#").strip()
        if not s or set(s) <= set("=-_ *"):   # 分隔線
            continue
        title = s
        break

    platform = ""
    m = _SCRIPTV_RE.search(text)
    if m:
        parts = m.group(1).split(".")
        if len(parts) >= 3:
            platform = parts[1].upper()       # SV.AIX.202609241500 → AIX
    if not platform:
        upper = (name + " " + title).upper()
        for cand in ("AIX", "RHEL", "ROCKY", "WINDOWS", "LINUX"):
            if cand in upper:
                platform = cand
                break
    return title, platform


def _meta(p: Path) -> dict:
    raw = p.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")          # 送出去的一定是 LF，統計也照 LF 算
    text = lf.decode("utf-8", errors="replace")
    m = _SCRIPTV_RE.search(text)
    title, platform = _describe(text, p.name)
    return {
        "name": p.name,
        "title": title,
        "platform": platform,
        # 沒有版本戳就明講「腳本裡沒寫」，不要拿檔案時間冒充成版本——
        # 那會讓人以為自己確認過版本，其實只是看到一個複製檔案的時間。
        "script_version": m.group(1) if m else None,
        "size": len(lf),
        "sha256": hashlib.sha256(lf).hexdigest(),
        "modified_at": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        "crlf_in_repo": b"\r\n" in raw,       # 真的發生就要看得到，不要安靜地修掉
    }


def list_scripts() -> list[dict]:
    """scripts/ 底下所有「可以單獨下載」的腳本，依檔名排序。"""
    if not SCRIPTS_DIR.is_dir():
        return []
    return [_meta(p) for p in sorted(SCRIPTS_DIR.iterdir(), key=lambda x: x.name)
            if _is_downloadable(p)]


def read_script(name: str) -> tuple[str, bytes]:
    """取一支腳本的內容（LF）。取不到就丟 KeyError，由路由層轉成 404。

    只認 `list_scripts()` 列得出來的檔名：帶 `..`、帶斜線、絕對路徑的輸入
    在這一步就對不到任何一項，等於在查表時就被擋掉，不是靠字串過濾去猜。
    """
    if not name or name != Path(name).name:   # 任何含路徑分隔的輸入直接出局
        raise KeyError(name)
    p = SCRIPTS_DIR / name
    # 再驗一次解析後的實際位置：擋「scripts/ 底下某層是 symlink 指到外面」
    try:
        resolved = p.resolve(strict=True)
    except OSError as exc:
        raise KeyError(name) from exc
    if resolved.parent != SCRIPTS_DIR.resolve():
        raise KeyError(name)
    if not _is_downloadable(p):
        raise KeyError(name)
    return name, p.read_bytes().replace(b"\r\n", b"\n")
