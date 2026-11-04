#!/usr/bin/env python3
"""部署後端到端驗收（決策 ACCEPT-E2E1，2026-10-18 使用者方向）。

## 為什麼要有

使用者在公司機「重新納管」一台 RHEL 後，資產頁 OS 重建分頁仍是「尚未收集」。
之前的「驗證」是直接呼叫內部函式（post_onboard.run_now）——**沒有走瀏覽器實際走的那條路**，
所以驗了等於沒驗。這支只打瀏覽器會打的 HTTP 端點、帶跟瀏覽器一樣的參數：

  1. 重新納管收集器自己（不需要密碼的兩條路，跟 2-1 畫面同一組端點）：
     · ``script``：OnboardModal「本機執行」→ GET /api/onboard/script → 以 root 在本機跑
       → POST /api/onboard/verify（畫面上的「驗證」按鈕）
     · ``bundle``：PasteOnboardModal「取得納管指令」→ POST /api/onboard/paste/issue
       → GET /api/onboard/bundle/download → 以 root 跑 → 等系統自動偵測
       （GET /api/onboard/paste/status，畫面每 20 秒輪詢的那支）
  2. 等納管後六樣跑完：GET /api/onboard/post-collect/<序號>（資產頁那一列六樣結果）
  3. 用資產頁分頁讀的端點確認資料是**這次之後**才寫進去的：
     · OS 重建：GET /api/host-spec/rebuild?asset_serial=
     · 組態檢核：GET /api/config-audit/by-asset?asset_serial=
  4. 存證：每一次呼叫的狀態碼與回應存進 --evidence 目錄，另存資產頁 SSR HTML。

六樣任一樣「沒收到」（ok=false）、或沒有正當略過原因 → 結束碼 1（deploy.sh 會中止）。

只用標準函式庫（部署時 venv 的 python 跑，不多裝東西）。這支會進公開 relay：**不寫死任何 IP**。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

SIX = ("spec", "service", "software", "account", "rebuild", "config_audit")


class Api:
    def __init__(self, base: str, token: str, evidence: str):
        self.base = base.rstrip("/")
        self.cookie = f"session_token={token}" if token and "=" not in token else (token or "")
        self.evidence = evidence
        self.n = 0
        self.log: list[dict] = []

    def call(self, method: str, path: str, *, params: dict | None = None,
             body: dict | None = None, text: bool = False, quiet: bool = False):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        except Exception as e:  # noqa: BLE001 - 連不上也要記下來、往上報
            status, raw = 0, str(e).encode()
        self.n += 1
        payload = raw.decode("utf-8", "replace")
        rec = {"n": self.n, "at": _now(), "method": method, "path": path, "params": params,
               "status": status, "ms": int((time.time() - t) * 1000)}
        self.log.append(rec)
        if not quiet:
            print(f"  [{self.n:03d}] {method} {path}{'?' + urllib.parse.urlencode(params) if params else ''}"
                  f" → {status}")
        if self.evidence:
            fn = os.path.join(self.evidence, f"{self.n:03d}_{method}_{path.strip('/').replace('/', '_')[:80]}.txt")
            with open(fn, "w", encoding="utf-8") as f:
                f.write(f"{method} {url}\nstatus {status}\n\n{payload[:200000]}")
        if text:
            return status, payload
        try:
            return status, json.loads(payload)
        except ValueError:
            return status, payload


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _run_root(cmd: list[str], *, stdin: str | None = None, label: str) -> None:
    print(f"  以 root 在本機執行：{label}")
    p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=600)
    tail = (p.stdout + p.stderr).strip().splitlines()[-6:]
    for ln in tail:
        print(f"     | {ln}")
    if p.returncode != 0:
        raise SystemExit(f"✗ 納管腳本結束碼 {p.returncode}（{label}）")


def onboard_script(api: Api, ip: str) -> str:
    """OnboardModal「本機執行」＋「驗證」——同兩支端點、同參數。回資產序號。"""
    st, r = api.call("GET", "/api/onboard/script", params={"platform": "linux"})
    if st != 200 or not isinstance(r, dict) or not r.get("command"):
        raise SystemExit(f"✗ 取不到本機執行指令（{st}）：{str(r)[:300]}")
    cmd = r["command"]
    if cmd.startswith("sudo "):          # 已經是 root：畫面說明也是「去掉開頭的 sudo」
        cmd = cmd[len("sudo "):]
    _run_root(["bash", "-c", cmd], label="GET /api/onboard/script 的指令")
    st, v = api.call("POST", "/api/onboard/verify", body={"ip": ip, "platform": "linux"})
    if st != 200 or not isinstance(v, dict) or not v.get("ok"):
        raise SystemExit(f"✗ 驗證失敗（{st}）：{str(v)[:400]}")
    print(f"  ✓ 驗證成功：{v.get('hostname')}／{v.get('os')}　序號 {v.get('asset_serial')}"
          + (f"　註：{v.get('post_note')}" if v.get("post_note") else ""))
    return v["asset_serial"]


def onboard_bundle(api: Api, ip: str, wait: int) -> str:
    """PasteOnboardModal「取得納管指令」→ 下載 → 跑 → 系統自己偵測。回資產序號。"""
    st, r = api.call("POST", "/api/onboard/paste/issue", body={"ips": [ip]})
    if st != 200:
        raise SystemExit(f"✗ 取得納管指令失敗（{st}）：{str(r)[:300]}")
    st, text = api.call("GET", "/api/onboard/bundle/download", text=True)
    if st != 200 or not text.startswith("#!"):
        raise SystemExit(f"✗ 下載 webit3_onboard.sh 失敗（{st}）")
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8", newline="\n") as f:
        f.write(text)
        path = f.name
    try:
        _run_root(["sh", path], label="webit3_onboard.sh（GET /api/onboard/bundle/download）")
    finally:
        os.unlink(path)
    end = time.time() + wait
    while time.time() < end:
        st, s = api.call("GET", "/api/onboard/paste/status", params={"ips": ip}, quiet=True)
        row = next((x for x in (s or {}).get("rows", []) if x.get("ip") == ip), None) if isinstance(s, dict) else None
        if row and row.get("state") == "ok" and row.get("asset_serial") and (row.get("detected_at") or "") >= T0:
            api.call("GET", "/api/onboard/paste/status", params={"ips": ip})
            print(f"  ✓ 系統已自動偵測：{row.get('detected_at')}　序號 {row['asset_serial']}")
            return row["asset_serial"]
        if row and row.get("state") not in (None, "waiting", "ok"):
            raise SystemExit(f"✗ 自動偵測回報 {row.get('state')}：{row.get('reason')}")
        time.sleep(10)
    raise SystemExit(f"✗ {wait} 秒內系統沒有自動偵測到（paste/status 仍是等待中）")


def wait_post_collect(api: Api, serial: str, wait: int) -> dict:
    """等資產頁那一列六樣出現**這次之後**的結果。"""
    q = urllib.parse.quote(serial, safe="")
    end = time.time() + wait
    last_print = ""
    while time.time() < end:
        st, r = api.call("GET", f"/api/onboard/post-collect/{q}", quiet=True)
        if st == 200 and isinstance(r, dict):
            last = r.get("last") or {}
            msg = f"running={r.get('running')} progress={r.get('progress')} last.at={last.get('at')}"
            if msg != last_print:
                print(f"     … {_now()} {msg}")
                last_print = msg
            if not r.get("running") and (last.get("at") or "") >= T0 and r.get("results"):
                api.call("GET", f"/api/onboard/post-collect/{q}")
                return r
        time.sleep(10)
    raise SystemExit(f"✗ {wait} 秒內資產頁沒有出現這次的六樣結果（post-collect）")


#: 收集帳號換新（2026-10-18）：重新納管後這台只能剩新帳號（值同 manage_state.READONLY_ACCOUNT／LEGACY_LINUX_ACCOUNT）
NEW_ACCOUNT, OLD_ACCOUNT = "webit3sc", "webit3scan"


def account_check() -> list[str]:
    """收集器自己這台：重新納管後要有新收集帳號、舊的已移除（換帳號那段真的跑了）。"""
    def has(u: str) -> bool:
        return subprocess.run(["getent", "passwd", u], capture_output=True).returncode == 0

    if not shutil.which("getent"):
        return [f"這台沒有 getent，查不了收集帳號（{NEW_ACCOUNT}／{OLD_ACCOUNT}）"]

    new_ok, old_left = has(NEW_ACCOUNT), has(OLD_ACCOUNT)
    print(f"  收集帳號：{NEW_ACCOUNT} {'有' if new_ok else '沒有'}；舊帳號 {OLD_ACCOUNT} {'還在' if old_left else '已移除'}")
    bad = []
    if not new_ok:
        bad.append(f"重新納管後沒有新收集帳號 {NEW_ACCOUNT}")
    if old_left:
        bad.append(f"重新納管後舊收集帳號 {OLD_ACCOUNT} 還在（換帳號那段沒跑或驗證沒過，看 /var/log/webit3_onboard.log）")
    return bad


def check(api: Api, serial: str, pc: dict, web: str | None) -> list[str]:
    bad = []
    res = {x["job"]: x for x in pc.get("results", [])}
    print("  六樣結果（資產頁那一列）：")
    for j in SIX:
        x = res.get(j)
        if not x:
            bad.append(f"{j}：沒有結果")
            print(f"     ✗ {j}：沒有結果")
            continue
        mark = "✓" if x["ok"] else ("略過" if x["ok"] is None else "✕")
        print(f"     {mark} {x['label']}：{x['message']}")
        if x["ok"] is False:
            bad.append(f"{x['label']}：{x['message']}")
        elif x["ok"] is None and not (x.get("message") or "").strip():
            bad.append(f"{x['label']}：略過但沒有原因")
    q = {"asset_serial": serial}
    if (res.get("rebuild") or {}).get("ok"):
        st, r = api.call("GET", "/api/host-spec/rebuild", params=q)
        hr = (r or {}).get("host_rebuild") if isinstance(r, dict) else None
        at = (hr or {}).get("collected_at") or ""
        summ = ((hr or {}).get("data") or {}).get("summary") or {}
        print(f"  OS 重建分頁：collected_at={at or '—'}　必要項 {summ.get('a_collected')}/{summ.get('total_a')}")
        if st != 200 or not hr or at < T0:
            bad.append(f"OS 重建分頁沒有這次的資料（{st}，collected_at={at or '無'}，開始於 {T0}）")
    if (res.get("config_audit") or {}).get("ok"):
        st, r = api.call("GET", "/api/config-audit/by-asset", params=q)
        run = (r or {}).get("run") if isinstance(r, dict) else None
        at = max(((run or {}).get("imported_at") or ""), ((run or {}).get("checked_at") or ""))
        print(f"  組態檢核分頁：state={(r or {}).get('state') if isinstance(r, dict) else r}　"
              f"run#{(run or {}).get('id')} 匯入 {(run or {}).get('imported_at')}　{len((r or {}).get('items') or [])} 條")
        if st != 200 or not run or at < T0:
            bad.append(f"組態檢核分頁沒有這次的結果（{st}，時間={at or '無'}，開始於 {T0}）")
    if web:
        for tab in ("rebuild", "audit"):
            st, html = api_web(api, web, serial, tab)
            print(f"  資產頁 SSR（tab={tab}）：HTTP {st}，{len(html)} bytes")
            if st != 200:
                bad.append(f"資產頁打不開（tab={tab}，HTTP {st}）")
    return bad


def api_web(api: Api, web: str, serial: str, tab: str):
    w = Api(web, "", api.evidence)
    w.cookie = api.cookie
    w.n = api.n
    st, html = w.call("GET", f"/assets/{urllib.parse.quote(serial, safe='')}", params={"tab": tab}, text=True)
    api.n = w.n
    api.log.extend(w.log)
    return st, html


T0 = ""


def main() -> int:
    global T0
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--api", required=True, help="後端，例 http://10.99.0.1:8000")
    ap.add_argument("--web", help="前端，例 http://10.99.0.1:3000（存資產頁 SSR 當證據）")
    ap.add_argument("--ip", required=True, help="要重新納管的主機（收集器自己）")
    ap.add_argument("--mode", choices=("script", "bundle", "both"), default="script")
    ap.add_argument("--token", default="", help="session_token（登入停用時可省略）")
    ap.add_argument("--evidence", default="", help="證據目錄（每次呼叫的狀態碼與回應）")
    ap.add_argument("--wait", type=int, default=600, help="等六樣跑完的秒數上限")
    a = ap.parse_args()
    if os.geteuid() != 0:
        print("✗ 要以 root 跑（納管腳本要在本機以 root 執行，跟使用者在主機上貼指令一樣）")
        return 1
    if a.evidence:
        os.makedirs(a.evidence, exist_ok=True)
    api = Api(a.api, a.token, a.evidence)
    bad_all: list[str] = []
    modes = ("script", "bundle") if a.mode == "both" else (a.mode,)
    for m in modes:
        T0 = (datetime.now() - timedelta(seconds=2)).strftime("%Y-%m-%d %H:%M:%S")
        print(f"== 端到端驗收（{m}）開始 {T0}：重新納管 {a.ip} → 等六樣 → 讀資產頁分頁 ==")
        try:
            serial = onboard_script(api, a.ip) if m == "script" else onboard_bundle(api, a.ip, a.wait)
            pc = wait_post_collect(api, serial, a.wait)
            bad = check(api, serial, pc, a.web) + account_check()
        except SystemExit as e:
            bad = [str(e)]
        if bad:
            print(f"✗ 端到端驗收（{m}）不通過：")
            for b in bad:
                print(f"   - {b}")
        else:
            print(f"✓ 端到端驗收（{m}）通過：六樣都有這次的結果，OS 重建／組態檢核分頁讀得到 {T0} 之後的資料")
        bad_all += [f"[{m}] {b}" for b in bad]
    if a.evidence:
        with open(os.path.join(a.evidence, "calls.json"), "w", encoding="utf-8") as f:
            json.dump(api.log, f, ensure_ascii=False, indent=1)
        print(f"  證據：{a.evidence}（{api.n} 次呼叫，calls.json 是總表）")
    return 1 if bad_all else 0


if __name__ == "__main__":
    sys.exit(main())
