"""收集端環境自我檢查／自我修復：paramiko 到底載到哪一個、壞了怎麼修（2026-09-15）。

## 為什麼要在後端行程內做

SAN 收集與納管都靠 paramiko。公司機 .14 出現過「`import paramiko` 成功，但沒有
`SSHClient`」——那是 **paramiko 被同名的空目錄蓋掉**（Python 把沒有 `__init__.py` 的
目錄當 namespace package，import 得到但裡面什麼都沒有）。

從 shell 跑 `python -c "import paramiko"` 驗不到這件事：shell 的工作目錄、`sys.path`
跟 uvicorn 行程不一樣。所以檢查一定要在**後端行程內**做，看到的才是收集實際會用到的那個。

## 為什麼要有「自我修復」按鈕

patch 的 `[4.6/5]` 只掃 `$APP/backend` 與 `$APP` 兩個固定位置。2026-09-15 公司機套了
v1.174.0 還是壞的 → 那個空目錄不在那兩處。每查一次就要重出一包 patch、使用者再套一次，
來回太慢（使用者 9/14：「不想再手動貼指令來回」）。

所以這裡做兩件事：
1. `selfcheck()` 把 **sys.path 上所有叫 paramiko 的東西**列出來，標出哪個是壞的——
   位置直接顯示在畫面上，不用叫人去跑指令。
2. `selfheal()` 只把「**目錄但沒有 `__init__.py`**」這種明確壞掉的改名備份（不刪除）。
   有 `__init__.py` 的是真套件，**一律不碰**——那種要重裝，不是改名能解決的。
"""
from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

#: 改名時加的後綴（保留原檔，隨時可以改回去）
BACKUP_SUFFIX = ".broken-"


def _candidates() -> list[dict]:
    """sys.path 上所有叫 paramiko 的檔案／目錄，依 import 的搜尋順序。"""
    out: list[dict] = []
    seen: set[str] = set()
    for entry in sys.path:
        try:
            base = Path(entry or ".").resolve()
        except (OSError, ValueError):
            continue
        for name in ("paramiko", "paramiko.py"):
            p = base / name
            try:
                if not p.exists():
                    continue
            except OSError:
                continue
            key = str(p)
            if key in seen:
                continue
            seen.add(key)
            is_dir = p.is_dir()
            # ⚠️ 2026-09-15 公司機真因：套件是 root 用嚴格 umask 裝的，服務帳號**讀不到**目錄內容。
            # 那時 __init__.py 其實在，只是 stat 被拒——Python 把它當 namespace 空殼，
            # 才會一直「沒有 SSHClient」。這種**絕對不能**當成空目錄去改名（那是真套件），
            # 要回報成權限問題。
            permission_denied = False
            has_init = None
            if is_dir:
                try:
                    has_init = (p / "__init__.py").exists()
                    if has_init and not os.access(p / "__init__.py", os.R_OK):
                        permission_denied = True
                except PermissionError:
                    permission_denied = True
                if not os.access(p, os.R_OK | os.X_OK):
                    permission_denied = True
            in_site = "site-packages" in key.replace("\\", "/")
            out.append({
                "path": key,
                "is_dir": is_dir,
                "has_init": has_init,
                "in_site_packages": in_site,
                "permission_denied": permission_denied,
                # 壞的定義：**讀得到**、而且確定沒有 __init__.py（namespace 空殼）
                "broken": bool(is_dir and not permission_denied and has_init is False),
            })
    return out


def _cwd() -> str:
    try:
        return os.getcwd()
    except OSError as exc:
        return f"(取不到：{type(exc).__name__}: {exc}——多半是 patch 換掉了目錄、服務還沒重啟)"


def selfcheck() -> dict:
    """在**這個行程內**驗 paramiko 能不能真的用，並附上它到底載自哪裡。"""
    out: dict = {
        "ok": False,
        "python": sys.executable,
        # patch 會換掉程式目錄，uvicorn 原本的工作目錄可能已經不存在——取不到要講，不能整支炸掉
        "cwd": _cwd(),
        "paramiko_file": None,
        "paramiko_version": None,
        "reason": None,
        "candidates": _candidates(),
        "fix": ("按「一鍵修復」會把蓋住 paramiko 的空目錄改名備份（不刪除）；"
                "若真套件本身殘缺則要重裝，畫面會告訴你。也可以改用『離線匯入』（不需 paramiko）"),
    }
    out["broken_paths"] = [c["path"] for c in out["candidates"] if c["broken"]]
    out["permission_denied_paths"] = [c["path"] for c in out["candidates"] if c["permission_denied"]]
    if out["permission_denied_paths"]:
        # 權限問題優先講：這種不是改名能修，要 root 放寬讀取權限（見 patch.sh [4.6/5]）
        site = str(Path(out["permission_denied_paths"][0]).parent)
        out["reason"] = (
            f"paramiko 檔案權限錯誤：服務帳號讀不到 {out['permission_denied_paths'][0]}"
            f"（多半是用 root 安裝時 umask 太嚴，檔案只有 root 讀得到）")
        out["fix"] = (f"請 root 執行：chmod -R a+rX {site}　然後重啟後端服務。"
                      "（新版 patch 會自動處理並改用服務帳號驗證）")
        out["fix_command"] = f"chmod -R a+rX {site}"
        return out
    try:
        import paramiko

        out["paramiko_file"] = getattr(paramiko, "__file__", None)
        out["paramiko_version"] = getattr(paramiko, "__version__", "?")
        # namespace 空目錄沒有 __file__，但 __path__ 會寫出它是哪個目錄——這是找到它的關鍵
        out["paramiko_path"] = [str(x) for x in (getattr(paramiko, "__path__", None) or [])]
        if not hasattr(paramiko, "SSHClient"):
            where = out["paramiko_file"] or (
                "(namespace 空目錄：" + ("、".join(out.get("paramiko_path") or []) or "位置不明") + ")")
            extra = ("　蓋住它的是：" + "、".join(out["broken_paths"])) if out["broken_paths"] else ""
            out["reason"] = f"paramiko 不完整：載自 {where}，缺 SSHClient{extra}"
            return out
        import paramiko.ssh_exception  # noqa: F401  子模組也要在
        paramiko.SSHClient()           # 真的建一個（SAN／納管都要）
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001 - 什麼錯都要如實回報，不是只有 ImportError
        out["reason"] = f"{type(exc).__name__}: {exc}"
    return out


def selfheal() -> dict:
    """把「目錄但沒有 __init__.py」的 paramiko 空殼改名備份，然後重新 import 驗一次。

    **不刪除**（改名成 paramiko.broken-<timestamp>，要還原改回即可）。
    **不碰有 __init__.py 的真套件**——那種是安裝殘缺，要重裝，改名只會讓它連 import 都不成。
    """
    before = selfcheck()
    if before.get("permission_denied_paths"):
        # 權限問題不是改名能解決的——服務帳號本來就沒有 site-packages 的寫入權，而且那是真套件
        return {"ok": False, "renamed": [], "failed": [],
                "message": "這不是「空目錄蓋掉」的問題，是檔案權限：" + str(before.get("reason"))
                           + "。" + str(before.get("fix")),
                "before": before, "after": before}
    renamed: list[dict] = []
    failed: list[dict] = []
    for c in before["candidates"]:
        if not c["broken"]:
            continue
        src = Path(c["path"])
        dst = src.with_name(src.name + BACKUP_SUFFIX + str(int(time.time())))
        try:
            src.rename(dst)
            renamed.append({"from": str(src), "to": str(dst)})
        except OSError as exc:
            failed.append({"path": str(src), "error": f"{type(exc).__name__}: {exc}"})

    # 讓 import 重新找一次：清掉 paramiko 相關模組與 finder 快取
    for name in [m for m in list(sys.modules) if m == "paramiko" or m.startswith("paramiko.")]:
        sys.modules.pop(name, None)
    importlib.invalidate_caches()

    after = selfcheck()
    if after["ok"] and renamed:
        message = (f"已修復：移開 {len(renamed)} 個蓋住 paramiko 的空目錄，現在 paramiko "
                   f"{after.get('paramiko_version')} 可以正常使用")
    elif after["ok"]:
        message = "本來就是好的，沒有東西需要修"
    elif renamed:
        message = ("空目錄已移開，但 paramiko 還是不能用——代表真套件本身殘缺，要重裝："
                   "套最新 patch（內含離線套件）或請開發者出一包。原因：" + str(after.get("reason")))
    elif failed:
        message = "找到壞掉的目錄但改名失敗（權限？）：" + "；".join(
            f"{f['path']}（{f['error']}）" for f in failed)
    elif before["ok"]:
        message = "本來就是好的，沒有東西需要修"
    else:
        message = ("沒有找到「空目錄蓋掉」這種問題，所以不是改名能解決的。"
                   "原因：" + str(after.get("reason")) + "——這種要重裝 paramiko（套最新 patch）")
    return {"ok": after["ok"], "renamed": renamed, "failed": failed,
            "message": message, "before": before, "after": after}
