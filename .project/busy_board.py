"""忙碌狀態看板——產生一頁 HTML，讓使用者一個網址看到「誰在做什麼、卡在哪」。

2026-09-24 使用者：「做一個儀表板，讓我知道你們忙碌狀態」。

git 只看得到「已 push 的」，所以這支同時吃兩種來源：
  * 自動：各分支最後 commit 與停多久（客觀，但看不到做完沒推）
  * 手動：SESSIONS 裡的「在做什麼／狀態／卡在誰」（wbs 每次問過一輪後更新）

**「閒置」與「等人拍板」是兩種狀態，不要混**——下一步完全不同。

用法：
    py -3 .project/busy_board.py <輸出的 html 路徑>
"""
from __future__ import annotations

import datetime
import os
import html
import subprocess
import sys

ROOT = r"C:\AiProject\CL_WEBITv3"

# 手動維護：wbs 每次向各 session 要過三行現況後更新這裡。
# branch 留空 = 這條線沒有自己的分支（例如 wbs 不寫程式）。
#: 公司機 wbs 連不到，只能人工填。seen ＝ 這個版號是什麼時候看到的。
COMPANY = dict(host="10.92.198.14", version="1.361.0", seen="09-23 15:50",
               note="1.366 patch 已交付未套用；1.371 打包中。套用後要更新這格")

SESSIONS = [
    dict(need_you="", running_on="派工與合併；等三條線回來驗", name="BOSS", t_done=0, t_wait=[], t_run=1, t_note='', t_donelist=[], role="合併・部署・驗收", branch="main",
         doing="沒事做，在等三個人回來：AIX 遷移第 4、5 步／SysCheck 搜尋慢／佈署 1.361→1.371",
         state="waiting", asked="09-24 12:20",
         idle_why="等三個 session 回來"),
    dict(need_you="fcbaixsh 在真 AIX 上跑第一輪（腳本已經交給你）", need_since="2026-09-22 13:30", running_on="重寫 AIX 真機驗證清單", name="AIX_Hardering", t_done=0, t_wait=['AIX 四類收集修正', 'AIX 健檢九維度', '收集帳號遷移 1~5', 'fcbaixsh 五條修正', '單機版遷移 .sh'], t_run=1, t_note='5 件全部未真機驗證——公司機還沒升到這版', t_donelist=[], role="AIX 強化與收集", branch="feature/aix-hardening",
         doing="重寫 AIX 真機驗證清單；剛交付單機版遷移 .sh 與 fcbaixsh 五條修正",
         state="working", asked="09-24 13:05"),
    dict(need_you="", running_on="重複資產診斷（先查為什麼一台會有兩筆）＋合併前補快照", name="SysCheck", t_done=2, t_wait=['大表略過在真的超過門檻時的行為（221 資料量不夠，要等公司機）'], t_run=1, t_note='', t_donelist=['搜尋精確優先（佈署在 221 實測過）', '搜尋大表略過（佈署在 221 實測過）'], role="主機健檢・值班診斷",
         branch="feature/mail",
         extra=["feature/health-windows", "feature/syscheck"],
         doing="出「重複資產合併」方案（先出方案，不動手）",
         state="working", asked="09-24 12:12",
         long_job="產 relay 包並推公司端", long_eta="約 25 分鐘"),
    dict(need_you="", running_on="AI 戰情室、進度報表", name="wbs", t_done=0, t_wait=[], t_run=1, t_note='', t_donelist=[], role="進度統計・對外報告", branch="",
         doing="AI 戰情室、進度報表",
         state="working", asked="09-24 10:55"),
    dict(need_you="公司機套 1.371.0 patch，照升版清單走", need_since="2026-09-24 12:15", running_on="", name="佈署", t_done=1, t_wait=[], t_run=1, t_note='', t_donelist=['v1.372.0 部署 221 並實測搜尋兩條'], role="部署與交付", branch="",
         doing="待命",
         state="waiting", asked="09-24 12:44",
         idle_why="待命，等 BOSS 合併才有東西可部署"),
    dict(need_you="", running_on="", name="討論", t_done=0, t_wait=[], t_run=0, t_note='', t_donelist=[], role="需求釐清・拍板", branch="",
         doing="待命，使用者開口才動",
         state="standby", asked="", standby=True,
         idle_why="待命（正常，不用派工）"),
]

# 卡住的事——這張表才是使用者要看的重點。
BLOCKERS = [
    dict(id="gh-billing", verify_by="CI 出現一次 success", since="2026-09-24 08:15",
         what="自動出貨斷線（GitHub Actions 帳單／額度用完）",
         detail="relay 那條完全沒啟動，job 只跑 1 秒。但本機打 patch 這條是通的——"
                "09-24 10:31 已交付 1.361→1.366，現在在打 1.361→1.371。"
                "所以現在是「可交付，但每次都要人工帶入」，不是完全送不出去。",
         who="使用者", how="GitHub → Settings → Billing & plans", level="證據"),
    dict(id="mail-ui", verify_by="寄信設定存檔成功且預覽出得來", since="2026-09-24 11:00",
         what="寄信設定沒有人在畫面上點過",
         detail="API 驗過會擋，但瀏覽器上沒人填過一次。填完才算驗收。",
         who="使用者", how="221 → 系統 → 寄信設定", level="未驗證"),
    dict(id="aix-collect", verify_by="公司機版號到 1.371 以上，且該台收到的筆數不是 0", since="2026-09-24 12:15",
         what="AIX 四類收集修完能不能真的收到",
         detail="修在 v1.364.0，公司機停在 1.361.0，還沒升到這版所以驗不到。",
         who="BOSS", how="公司機升版後請使用者按一次收集", level="未驗證"),
    dict(id="fcbaixsh", verify_by="組態檢核頁出現一台 AIX 的真機結果", since="2026-09-22 13:30",
         what="fcbaixsh 98 條組態檢核零真機執行",
         detail="從來沒在真 AIX 跑過。跑得起來與跑不起來都沒有證據。",
         who="使用者", how="任一台 AIX 跑一次，回傳 /var/tmp/fcbaudit/", level="未驗證"),
]

STATE_LABEL = {
    "waiting": ("在等", "wait"),
    "working": ("進行中", "go"),
    "waiting": ("在等別人", "wait"),
    "idle": ("閒置", "idle"),
    "blocked": ("卡住", "stop"),
}


def run(cmd: str) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT)
    return (r.stdout or "").strip()


def humanise(mins: int) -> str:
    if mins < 60:
        return f"{mins} 分鐘"
    h, m = divmod(mins, 60)
    if h < 24:
        return f"{h} 小時 {m} 分" if m else f"{h} 小時"
    d, h = divmod(h, 24)
    return f"{d} 天 {h} 小時" if h else f"{d} 天"


def positions(now):
    """三台在哪一版。Git 與 221 自動抓，公司機只能人工填。"""
    main_h = run("git log --pretty=%h -1 origin/main")
    main_t = run("git log --pretty=%cI -1 origin/main")
    main_when = ""
    if main_t:
        main_when = datetime.datetime.fromisoformat(main_t).replace(
            tzinfo=None).strftime("%m-%d %H:%M")
    import json
    main_ver = "讀不到"
    raw = run("git show origin/main:APP/asset-module/backend/version.json")
    if raw:
        try:
            main_ver = json.loads(raw).get("version") or "?"
        except Exception:
            pass

    api = run("curl -s -m 5 http://127.0.0.1:8000/api/version")
    ver = commit = started = "讀不到"
    if api:
        try:
            d = json.loads(api)
            ver = d.get("version") or "?"
            commit = (d.get("git_commit") or "")[:7]
            started = (d.get("started_at") or "")[5:16]
        except Exception:
            pass
    return [
        dict(name="Git main", ver=main_ver, commit=main_h, when=main_when,
             how="自動", note="程式碼的源頭"),
        dict(name="221 測試機", ver=ver, commit=commit, when=started,
             how="自動", note="YOUR_SERVER_IP:3000"),
        dict(name="公司機", ver=COMPANY["version"], commit="—",
             when=COMPANY["seen"], how="人工",
             note=COMPANY["host"] + "：" + COMPANY["note"]),
    ]


def items_and_acceptance():
    """驗收清單與使用者已經給過的結論。"""
    import json
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "items.json"), encoding="utf-8") as f:
            items = json.load(f).get("items", [])
    except Exception:
        items = []
    try:
        with open(os.path.join(here, "acceptance.json"), encoding="utf-8") as f:
            acc = json.load(f)
    except Exception:
        acc = {}
    return items, acc


def events(now, limit=40):
    """事件變更：誰在什麼時候做了什麼。時間序，最新在上。"""
    import json
    ev = []
    for sess in SESSIONS:
        for br in ([sess["branch"]] if sess["branch"] else []) + list(sess.get("extra") or []):
            raw = run(f'git log -12 --pretty=%cI%h%s origin/{br}')
            for ln in raw.splitlines():
                bits = ln.split("")
                if len(bits) != 3:
                    continue
                try:
                    t = datetime.datetime.fromisoformat(bits[0]).replace(tzinfo=None)
                except ValueError:
                    continue
                ev.append(dict(t=t, who=sess["name"], kind="推版",
                               text=bits[2][:120], tag=br.replace("feature/", "")))

    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "acceptance.json"), encoding="utf-8") as f:
            for k, v in json.load(f).items():
                t = datetime.datetime.strptime(v["at"], "%Y-%m-%d %H:%M")
                word = "驗收通過" if v["verdict"] == "pass" else "退回再改"
                ev.append(dict(t=t, who="使用者", kind=word,
                               text=(v.get("note") or k)[:120], tag=k))
    except Exception:
        pass
    try:
        with open(os.path.join(here, "inbox.json"), encoding="utf-8") as f:
            for m in json.load(f):
                t = datetime.datetime.strptime(m["at"], "%Y-%m-%d %H:%M")
                ev.append(dict(t=t, who="使用者", kind="指示",
                               text=m["text"][:200], tag=""))
    except Exception:
        pass

    ev.sort(key=lambda e: e["t"], reverse=True)
    return ev[:limit]


def live_state(now):
    """真正的忙／閒。只有 wbs 問得到（ListAgents），寫成檔給這支讀。

    使用者 2026-09-24：「又抓到 AIX 沒作業，但你跑 73%」——
    用「多久沒回報」推忙碌度會把閒置的畫成在跑。取樣過期就老實說不知道。
    """
    import json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        t = datetime.datetime.strptime(d["sampled_at"], "%Y-%m-%d %H:%M")
        age = int((now - t).total_seconds() // 60)
        return d.get("state", {}), d["sampled_at"], age
    except Exception:
        return {}, "", None


def collect():
    run("git fetch -q")
    now = datetime.datetime.now()
    rows = []
    for s in SESSIONS:
        last, mins, dirty = None, None, None
        if s["branch"]:
            iso = run(f'git log --pretty=%cI -1 origin/{s["branch"]}')
            if iso:
                last = datetime.datetime.fromisoformat(iso).replace(tzinfo=None)
                mins = int((now - last).total_seconds() // 60)
        rmins = None
        if s.get("asked"):
            try:
                t = datetime.datetime.strptime(f'{now.year}-{s["asked"]}', "%Y-%m-%d %H:%M")
                rmins = max(0, int((now - t).total_seconds() // 60))
            except ValueError:
                rmins = None
        # 產出強度：最近一小時這條分支推了幾筆、動了幾行。
        # 這是唯一分得出「大忙／小忙」的客觀訊號——忙／閒只是是非題。
        # 限制要講明：打 patch、跑測試、寫文件都不會進 git，所以低不代表沒做事。
        n_c = lines_ch = 0
        if s["branch"]:
            brs = [s["branch"]] + list(s.get("extra") or [])
            raw = chr(10).join(
                run(f'git log --since="60 minutes ago" --numstat '
                    f'--pretty=%h origin/{b}') for b in brs)
            for ln in raw.splitlines():
                bits = ln.split("	")
                if len(bits) == 3:
                    for v in bits[:2]:
                        if v.isdigit():
                            lines_ch += int(v)
                elif ln.strip():
                    n_c += 1
        cands = [x for x in (mins, rmins) if x is not None]
        act_mins = min(cands) if cands else None
        rows.append({**s, "last": last, "mins": mins, "rmins": rmins,
                     "act_mins": act_mins, "commits": n_c,
                     "lines": lines_ch, "dirty": dirty})
    return rows, now


def esc(x) -> str:
    return html.escape(str(x))


CSS = """
:root{
  --bg:#f4f4f1; --surface:#ffffff; --ink:#191d22; --muted:#6a7178;
  --line:#e2e2dc; --line-soft:#eeeee9; --accent:#1f5b4e;
  --go:#1d7a5f; --go-bg:#e6f2ec;
  --wait:#a9720c; --wait-bg:#f8efdc;
  --idle:#767c84; --idle-bg:#eceded;
  --stop:#a9382a; --stop-bg:#f8e6e2;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    color-scheme:dark;
    --bg:#14171a; --surface:#1c2024; --ink:#e9eaea; --muted:#969ca3;
    --line:#2b3035; --line-soft:#23282c; --accent:#6fbfa8;
    --go:#54c79c; --go-bg:#16332a;
    --wait:#e0aa4a; --wait-bg:#33280f;
    --idle:#8d949b; --idle-bg:#24282c;
    --stop:#e3806e; --stop-bg:#331e1a;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --bg:#14171a; --surface:#1c2024; --ink:#e9eaea; --muted:#969ca3;
  --line:#2b3035; --line-soft:#23282c; --accent:#6fbfa8;
  --go:#54c79c; --go-bg:#16332a;
  --wait:#e0aa4a; --wait-bg:#33280f;
  --idle:#8d949b; --idle-bg:#24282c;
  --stop:#e3806e; --stop-bg:#331e1a;
}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{
  background:var(--bg); color:var(--ink); margin:0;
  font-family:"IBM Plex Sans","Noto Sans TC",system-ui,sans-serif;
  font-size:15px; line-height:1.6;
}
.wrap{max-width:900px; margin:0 auto; padding-inline:16px; padding-block:28px 56px}
header{
  border-bottom:2px solid var(--ink); padding-bottom:9px; margin-bottom:4px;
  display:flex; align-items:baseline; gap:14px; flex-wrap:wrap;
}
h1{font-size:22px; font-weight:600; letter-spacing:-.01em; margin:0}
.stamp{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:12.5px;
  color:var(--muted);
}
h2{
  font-size:12px; font-weight:600; letter-spacing:.09em; text-transform:uppercase;
  color:var(--muted); margin:34px 0 12px;
}
.rows{display:flex; flex-direction:column; gap:8px}
.row{
  background:var(--surface); border:1px solid var(--line); border-left-width:4px;
  border-radius:3px; padding:13px 15px;
  display:grid; grid-template-columns:1fr auto; gap:4px 18px; align-items:baseline;
}
.row.go{border-left-color:var(--go)} .row.wait{border-left-color:var(--wait)}
.row.stop{border-left-color:var(--stop)}
.row.idle{border-left-color:var(--idle)} .row.stop{border-left-color:var(--stop)}
.who{font-weight:600; font-size:16px}
.who span{font-weight:400; color:var(--muted); font-size:13px; margin-left:8px}
.doing{grid-column:1; color:var(--ink)}
.meta{
  grid-column:2; grid-row:1/3; text-align:right; white-space:nowrap;
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:12.5px; color:var(--muted);
}
.pill{
  display:inline-block; padding:1px 9px; border-radius:99px;
  font-size:12px; font-weight:600; font-family:"IBM Plex Sans","Noto Sans TC",sans-serif;
}
.pill.go{background:var(--go-bg); color:var(--go)}
.pill.wait{background:var(--wait-bg); color:var(--wait)}
.pill.idle{background:var(--idle-bg); color:var(--idle)}
.pill.stop{background:var(--stop-bg); color:var(--stop)}
.gap{display:block; margin-top:5px}
.gauge{width:100%; max-width:104px; height:auto; display:block; margin:0 auto}
.cluster{
  margin-top:14px; display:grid; grid-template-columns:repeat(3,1fr); gap:6px 14px;
  background:var(--surface); border:1px solid var(--line);
  border-radius:3px; padding:16px 14px 13px;
}
.dial{text-align:left; min-width:0}
.lampdot{
  width:11px; height:11px; border-radius:50%; display:inline-block;
  vertical-align:middle; margin-right:7px; box-sizing:border-box;
}
.lampdot.on{background:var(--accent)}
.lampdot.off{border:1.5px solid var(--muted); background:transparent}
.lampdot.blocked{background:var(--wait)}
.dial-sub{
  display:block; font-size:12px; color:var(--muted); margin-top:2px;
  padding-left:18px;
}
.stuckbox{
  margin-top:14px; padding:12px; background:var(--surface);
  border:1px solid var(--line); border-radius:3px; text-align:center;
}
.stuckbox .gauge{max-width:150px; margin:0 auto}
.stucknum{
  font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:22px; font-weight:600; margin-top:-6px;
}
.stucknum.go{color:var(--go)} .stucknum.wait{color:var(--wait)}
.stucknum.stop{color:var(--stop)} .stucknum.ok{color:var(--go)}
.stucklab{font-size:13px; color:var(--ink); margin-top:3px}
.stucklab span{color:var(--muted); font-size:12px}
.tl{font-size:12px; margin:5px 0}
.tl b{color:var(--muted); font-weight:600}
.tl ol{margin:2px 0 0; padding-left:18px}
.tnote2{font-size:12px; color:var(--wait); margin:4px 0}
.tnote{display:block; font-size:11.5px; color:var(--muted); margin-top:4px}
.tally{
  margin-top:12px; font-size:13.5px; color:var(--muted);
}
.tally b{color:var(--ink); font-family:"IBM Plex Mono",monospace; font-size:15px}
.tally b.bad{color:var(--stop)}
.dial summary{
  list-style:none; cursor:pointer; border-radius:3px; padding:2px;
  position:relative;
}
.corner{
  position:absolute; top:2px; left:4px;
  font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:13px; font-weight:600; line-height:1;
}
.dial summary::-webkit-details-marker{display:none}
.dial summary:hover{background:var(--line-soft)}
.dial summary:focus-visible{outline:2px solid var(--accent); outline-offset:-2px}
.dmore{
  text-align:left; font-size:12px; margin-top:6px; padding:7px 9px;
  background:var(--bg); border-radius:3px;
}
.wst{
  font-size:12px; font-weight:600; margin-bottom:5px;
  padding:2px 8px; border-radius:99px; display:inline-block;
}
.wst.stop{background:var(--stop-bg); color:var(--stop)}
.wst.wait{background:var(--wait-bg); color:var(--wait)}
.wst.idle{background:var(--idle-bg); color:var(--idle)}
.blocked{
  font-size:12.5px; color:var(--wait); background:var(--wait-bg);
  padding:6px 9px; border-radius:3px; margin-bottom:5px;
}
.need{
  font-size:13px; font-weight:600; line-height:1.45; margin-bottom:4px;
  color:var(--stop);
}
.runs{font-size:12.5px; line-height:1.45; margin-bottom:5px; color:var(--ink)}
.dwhy{font-size:12px; margin-bottom:4px}
.dwhy.go{color:var(--go)} .dwhy.stop{color:var(--stop)} .dwhy.wait{color:var(--wait)}
.dgit{
  font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:11px; color:var(--muted);
}
.dial-pct{
  font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:15px; font-weight:600; color:var(--ink); margin-top:-4px;
}
.st.go{color:var(--go)}
.st.wait{color:var(--wait)}
.st.stop{color:var(--stop)}
.st.idle{color:var(--muted)}
.dial-name{
  font-weight:600; font-size:13.5px;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.dial-val{
  font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:13px; color:var(--muted);
}


@media(max-width:620px){.cluster{grid-template-columns:1fr}}
.dstamp{
  font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:11.5px; color:var(--muted); margin:2px 0 10px;
}
.legend{
  display:flex; flex-wrap:wrap; gap:6px 18px; align-items:center;
  font-size:12.5px; color:var(--muted); margin:-2px 0 12px;
}
.legend i{
  display:inline-block; width:22px; height:5px; border-radius:99px;
  margin-right:6px; vertical-align:middle;
}
.blk{
  background:var(--surface); border:1px solid var(--line);
  border-left:4px solid var(--stop); border-radius:3px; padding:13px 15px; margin-bottom:8px;
}
.blk h3{margin:0 0 3px; font-size:15.5px; font-weight:600}
.blk p{margin:0 0 8px; color:var(--muted); font-size:14px}
.blk .act{
  display:flex; flex-wrap:wrap; gap:6px 14px; align-items:baseline;
  font-size:13px; padding-top:8px; border-top:1px solid var(--line-soft);
}
.tag{
  font-size:11.5px; font-weight:600; letter-spacing:.05em;
  padding:1px 7px; border-radius:2px; background:var(--idle-bg); color:var(--idle);
}
.tag.ev{background:var(--stop-bg); color:var(--stop)}
.asked{
  display:block; margin-top:4px; font-size:12px; color:var(--muted);
  font-family:"IBM Plex Mono",ui-monospace,monospace;
}
.asked.old{color:var(--wait)}
.stale{color:var(--muted)}
.stale i{font-style:normal; font-size:12px; color:var(--wait); margin-left:4px}
.fact{
  display:block; margin-top:4px;
  font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:12px; color:var(--muted);
}
.dial.off{background:var(--stop-bg); border-radius:4px}
.dial.off .dial-name{color:var(--stop)}
.callout{
  margin-top:12px; padding:9px 13px; border-radius:3px; font-size:13px;
  background:var(--stop-bg); color:var(--stop);
  display:flex; gap:10px; flex-wrap:wrap; align-items:baseline;
}
.callout b{font-weight:600}
.callout .ok{color:var(--muted); margin-left:auto}
.callout.ok-all{background:var(--go-bg); color:var(--go)}
.posgrid{margin-top:10px; display:grid; grid-template-columns:repeat(3,1fr); gap:8px}
.posbox{
  background:var(--surface); border:1px solid var(--line); border-radius:3px;
}
.posbox summary{
  padding:8px 11px; display:flex; align-items:baseline; gap:8px; flex-wrap:wrap;
  cursor:pointer; list-style:none;
}
.posbox summary::-webkit-details-marker{display:none}
.posbox summary::after{
  content:"＋"; margin-left:auto; color:var(--muted); font-size:12px;
}
.posbox[open] summary::after{content:"−"}
.posbox summary:hover{background:var(--line-soft)}
.posbox summary:focus-visible{outline:2px solid var(--accent); outline-offset:-2px}
.det{
  margin:0; padding:2px 11px 10px; font-size:12.5px;
  display:grid; grid-template-columns:auto 1fr; gap:2px 10px;
  border-top:1px solid var(--line-soft);
}
.det dt{color:var(--muted)}
.det dd{margin:0}
.det .mono{font-family:"IBM Plex Mono",ui-monospace,monospace}
.pname{font-weight:600; font-size:13.5px}
.pmeta{
  font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:11.5px; color:var(--muted);
}
@media(max-width:620px){.posgrid{grid-template-columns:1fr}}
.ver{
  font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:15px; font-weight:600; color:var(--ink);
}
.src{
  font-size:11.5px; font-weight:600; letter-spacing:.05em;
  padding:1px 7px; border-radius:2px;
}
.src.auto{background:var(--go-bg); color:var(--go)}
.src.manual{background:var(--wait-bg); color:var(--wait)}
.mism{
  display:block; margin-top:6px; font-size:12.5px; color:var(--stop);
  border-left:2px solid var(--stop); padding-left:8px;
}
.path{font-family:"IBM Plex Mono",ui-monospace,monospace; color:var(--ink)}
.lamp{
  position:fixed; top:calc(10px + env(safe-area-inset-top, 0px)); right:14px;
  z-index:50; font:inherit; font-size:13px; font-weight:600; cursor:pointer;
  background:var(--stop); color:#fff; border:0; border-radius:99px;
  padding:6px 14px 6px 11px; display:flex; align-items:center; gap:7px;
  box-shadow:0 2px 10px rgba(0,0,0,.18);
}
.lamp .dot{
  width:9px; height:9px; border-radius:50%; background:#fff;
  box-shadow:0 0 0 0 rgba(255,255,255,.7); animation:pulse 2s infinite;
}
@keyframes pulse{
  70%{box-shadow:0 0 0 7px rgba(255,255,255,0)}
  100%{box-shadow:0 0 0 0 rgba(255,255,255,0)}
}
@media (prefers-reduced-motion:reduce){.lamp .dot{animation:none}}
.lampbox{
  position:fixed; top:calc(46px + env(safe-area-inset-top, 0px)); right:14px;
  z-index:50; max-width:min(420px, calc(100vw - 28px));
  background:var(--surface); border:1px solid var(--stop); border-radius:4px;
  box-shadow:0 6px 24px rgba(0,0,0,.18); padding:6px;
}
.lampitem{font-size:13px; padding:7px 9px; border-bottom:1px solid var(--line-soft)}
.lampitem:last-child{border-bottom:0}
.lampitem b{display:block; font-size:11.5px; color:var(--muted); font-weight:600}
.warnbar{
  background:var(--wait-bg); color:var(--wait); border-radius:3px;
  padding:7px 12px; font-size:12.5px; margin-top:10px;
}
.tabs{display:flex; gap:4px; margin:34px 0 12px; border-bottom:1px solid var(--line)}
.tab{
  font:inherit; font-size:13.5px; font-weight:600; color:var(--muted);
  background:none; border:0; border-bottom:2px solid transparent;
  padding:7px 14px; cursor:pointer; margin-bottom:-1px;
}
.tab:hover{color:var(--ink)}
.tab[aria-selected="true"]{color:var(--ink); border-bottom-color:var(--accent)}
.tab:focus-visible{outline:2px solid var(--accent); outline-offset:-2px}
.inbox{
  background:var(--surface); border:1px solid var(--line); border-radius:3px;
  padding:12px 14px; margin-bottom:18px;
}
.inbox label{display:block; font-size:12.5px; color:var(--muted); margin-bottom:6px}
.inbox textarea, .item textarea{
  width:100%; font:inherit; font-size:13.5px; padding:7px 9px;
  border:1px solid var(--line); border-radius:3px;
  background:var(--bg); color:var(--ink); resize:vertical;
}
.inbox-act, .it-act{display:flex; align-items:center; gap:8px; margin-top:8px; flex-wrap:wrap}
.btn{
  font:inherit; font-size:13px; font-weight:600; padding:5px 13px;
  border-radius:3px; border:1px solid transparent; cursor:pointer;
}
.btn.go{background:var(--go); color:#fff}
.btn.stop{background:var(--stop); color:#fff}
.btn.plain{background:none; border-color:var(--line); color:var(--muted)}
.btn:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
.said{font-size:12px; color:var(--muted); font-family:"IBM Plex Mono",monospace}
.gh{
  font-size:13px; font-weight:600; color:var(--ink);
  margin:20px 0 9px; display:flex; align-items:baseline; gap:7px;
}
.gh span{color:var(--muted); font-weight:400; font-size:12px}
.item{
  background:var(--surface); border:1px solid var(--line); border-left:4px solid var(--line);
  border-radius:3px; padding:11px 14px; margin-bottom:8px;
}
.item.pass{border-left-color:var(--go)}
.item.reject{border-left-color:var(--stop)}
.it-top{display:flex; align-items:baseline; gap:9px; flex-wrap:wrap}
.it-top b{font-size:14.5px}
.by{font-size:12px; color:var(--muted)}
.it-where{font-size:12.5px; color:var(--muted); margin-top:2px}
.mono{font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:12px}
.steps{margin:8px 0; padding-left:20px; font-size:13.5px}
.steps li{margin-bottom:2px}
.verdict{
  font-size:13px; font-weight:600; margin:8px 0;
  padding:6px 10px; border-radius:3px; background:var(--line-soft);
}
.vnote{font-weight:400; color:var(--muted); margin-top:3px}
.vby{
  font-size:12.5px; color:var(--muted); margin:6px 0 0;
  padding-top:6px; border-top:1px dashed var(--line);
}
.claim{
  margin-top:7px; padding:7px 10px; border-radius:3px;
  background:var(--wait-bg); color:var(--wait); font-size:12.5px;
}
.claim.off{background:var(--idle-bg); color:var(--idle)}
.saywrap{margin-top:8px; border-left:3px solid var(--accent); padding-left:9px}
.sayhd{font-size:12px; color:var(--muted); margin-bottom:3px}
.say{font-size:13px; margin-bottom:5px}
.say span{
  font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:11.5px; color:var(--muted); margin-right:7px;
}
.thumb{
  max-width:150px; display:block; margin-top:5px;
  border:1px solid var(--line); border-radius:3px;
}
.say img{max-width:200px; display:block; margin-top:4px; border:1px solid var(--line)}
.badge{
  background:var(--stop); color:#fff; font-size:11px; border-radius:99px;
  padding:0 6px; margin-left:6px; font-weight:600;
}

.feed{background:var(--surface); border:1px solid var(--line); border-radius:3px}
.fday{
  padding:7px 14px 5px; font-size:11.5px; font-weight:600; letter-spacing:.06em;
  color:var(--muted); background:var(--line-soft);
}
.fev{
  display:flex; align-items:baseline; gap:9px; flex-wrap:wrap;
  padding:7px 14px; border-bottom:1px solid var(--line-soft); font-size:13.5px;
}
.fev:last-child{border-bottom:0}
.ftime{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:12.5px;
  color:var(--muted); min-width:40px;
}
.fkind{
  font-size:11.5px; font-weight:600; padding:1px 7px; border-radius:2px; white-space:nowrap;
}
.fkind.go{background:var(--go-bg); color:var(--go)}
.fkind.stop{background:var(--stop-bg); color:var(--stop)}
.fkind.wait{background:var(--wait-bg); color:var(--wait)}
.fkind.idle{background:var(--idle-bg); color:var(--idle)}
.fwho{font-weight:600; font-size:13px; white-space:nowrap}
.ftext{flex:1 1 220px; min-width:0}
.ftag{
  font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:11.5px; color:var(--muted);
}
.fago{
  font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:11.5px; color:var(--muted); margin-left:auto; white-space:nowrap;
}
.empty{color:var(--muted); font-size:13.5px}

footer{
  margin-top:38px; padding-top:14px; border-top:1px solid var(--line);
  color:var(--muted); font-size:13px;
}
footer b{color:var(--ink); font-weight:600}
@media(max-width:560px){
  .row{grid-template-columns:1fr}
  .meta{grid-column:1; grid-row:auto; text-align:left}
}
"""


# ── 車速表 ───────────────────────────────────────────────────────────
# 指針指的是「停多久」：越靠右＝停越久。刻度用對數，因為 3 分鐘與 30 分鐘的
# 差別，比 20 小時與 23 小時的差別重要得多。
import math

DIAL = [(0, .0), (45, .34), (240, .67), (1440, 1.0)]   # 分鐘 → 指針位置


def dial_frac(mins) -> float:
    if mins is None:
        return None
    mins = max(0, mins)   # 手填的回報時間可能比現在晚幾分鐘，夾住不要算成負的
    if mins >= DIAL[-1][0]:
        return 1.0
    for (m0, f0), (m1, f1) in zip(DIAL, DIAL[1:]):
        if mins <= m1:
            span = math.log1p(m1) - math.log1p(m0)
            k = 0.0 if span <= 0 else (math.log1p(mins) - math.log1p(m0)) / span
            return f0 + (f1 - f0) * k
    return 1.0


def _pt(frac, r, cx=70.0, cy=68.0):
    a = math.radians(180 - 180 * frac)
    return cx + r * math.cos(a), cy - r * math.sin(a)


def _arc(f0, f1, r, w, var):
    x0, y0 = _pt(f0, r)
    x1, y1 = _pt(f1, r)
    big = 1 if (f1 - f0) > .5 else 0
    return (f'<path d="M {x0:.1f} {y0:.1f} A {r} {r} 0 {big} 1 {x1:.1f} {y1:.1f}" '
            f'fill="none" stroke="var(--{var})" stroke-width="{w}" stroke-linecap="butt"/>')


def busy_frac(mins):
    """最後動作到現在 → 忙碌度。0＝閒置，1＝正在動。"""
    f = dial_frac(mins)
    return None if f is None else 1.0 - f


def gauge_frac_svg(f, label) -> str:
    """直接給指針位置（0＝閒置、1＝在跑）；None＝不知道，不畫指針。"""
    parts = [f'<svg class="gauge" viewBox="0 0 140 82" role="img" '
             f'aria-label="{label}">']
    parts.append(_arc(0, .34, 52, 9, "go"))
    parts.append(_arc(.34, .67, 52, 9, "wait"))
    parts.append(_arc(.67, 1, 52, 9, "stop"))
    if f is None:
        f = 0.0
    nx, ny = _pt(f, 44)
    parts.append(f'<line x1="70" y1="68" x2="{nx:.1f}" y2="{ny:.1f}" '
                 f'stroke="var(--ink)" stroke-width="2.6" stroke-linecap="round"/>')
    parts.append('<circle cx="70" cy="68" r="4.5" fill="var(--ink)"/>')
    parts.append('</svg>')
    return "".join(parts)


STATE_GROUP = [("done", "已完成・等你驗"), ("doing", "進行中"), ("queued", "排隊中")]
VERDICT_LABEL = {"pass": "你驗過了：通過", "reject": "你退回了：要再改"}


def render_accept(items, acc) -> str:
    """驗收清單。已完成的要講清楚怎麼驗，讓使用者自己勾通過或退回。"""
    o = ['<div class="inbox">'
         '<label for="note-text">寫給 wbs 的指示（下一步要做什麼、哪裡不對）</label>'
         '<textarea id="note-text" rows="2" '
         'placeholder="例：AIX 那頁的硬體分頁還是空的，先查這個"></textarea>'
         '<div class="inbox-act"><button id="send-note" class="btn go">送出</button>'
         '<span id="note-said" class="said"></span></div></div>']

    for key, title in STATE_GROUP:
        group = [i for i in items if i.get("state") == key]
        if not group:
            continue
        o.append(f'<h3 class="gh">{esc(title)}　<span>{len(group)}</span></h3>')
        for it in group:
            got = acc.get(it["id"])
            cls = "item"
            if got:
                cls += " pass" if got["verdict"] == "pass" else " reject"
            o.append(f'<div class="{cls}" data-id="{esc(it["id"])}">')
            o.append(f'<div class="it-top"><b>{esc(it["title"])}</b>'
                     f'<span class="by">{esc(it.get("by", ""))}</span></div>')
            where = it.get("where") or ""
            url = it.get("url") or ""
            if where:
                o.append(f'<div class="it-where">{esc(where)}'
                         + (f'　<span class="mono">{esc(url)}</span>' if url else '')
                         + '</div>')
            if key == "done":
                steps = it.get("verify") or []
                if steps:
                    o.append('<ol class="steps">'
                             + "".join(f'<li>{esc(x)}</li>' for x in steps)
                             + '</ol>')
                if got:
                    o.append(f'<div class="verdict">{esc(VERDICT_LABEL[got["verdict"]])}'
                             f'　<span class="mono">{esc(got["at"])}</span>'
                             + (f'<div class="vnote">{esc(got["note"])}</div>'
                                if got.get("note") else '')
                             + '</div>')
                o.append('<textarea rows="1" placeholder="不過的話，寫哪裡不對"></textarea>')
                o.append('<div class="it-act">'
                         '<button class="btn go" data-verdict="pass">通過</button>'
                         '<button class="btn stop" data-verdict="reject">退回再改</button>'
                         '<button class="btn plain" data-verdict="clear">清掉</button>'
                         '<span class="said"></span></div>')
            o.append('</div>')
    return "".join(o)


KIND_CLS = {"推版": "go", "驗收通過": "go", "退回再改": "stop", "指示": "wait"}


def render_feed(ev, now) -> str:
    """事件變更：時間序，最新在上。git 看得到的推版，加上使用者按的與寫的。"""
    if not ev:
        return '<p class="empty">最近沒有事件。</p>'
    o, last_day = [], None
    for e in ev:
        day = e["t"].strftime("%m-%d")
        if day != last_day:
            o.append(f'<div class="fday">{esc(day)}</div>')
            last_day = day
        mins = int((now - e["t"]).total_seconds() // 60)
        cls = KIND_CLS.get(e["kind"], "idle")
        o.append(
            f'<div class="fev"><span class="ftime">{e["t"]:%H:%M}</span>'
            f'<span class="fkind {cls}">{esc(e["kind"])}</span>'
            f'<span class="fwho">{esc(e["who"])}</span>'
            f'<span class="ftext">{esc(e["text"])}</span>'
            + (f'<span class="ftag">{esc(e["tag"])}</span>' if e["tag"] else '')
            + f'<span class="fago">{esc(humanise(max(0, mins)))}前</span></div>')
    return '<div class="feed">' + "".join(o) + '</div>'


def stuck_gauge(need, now):
    """錶量的是「等你那類裡，卡最久的一件卡了多久」。

    為什麼不量忙碌度：忙碌沒有分母，只能編中間值。
    「卡了幾小時」有真實刻度，而且直接對應使用者要不要現在處理。
    """
    import math
    worst, worst_h = None, -1.0
    for name, what, since in need:
        if not since:
            continue
        try:
            t = datetime.datetime.strptime(since, "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        h = max(0.0, (now - t).total_seconds() / 3600)
        if h > worst_h:
            worst, worst_h = (name, what, h), h
    if worst is None:
        return ('<div class="stuckbox"><div class="stucknum ok">0</div>'
                '<div class="stucklab">沒有卡住的事</div></div>')
    frac = min(1.0, math.log1p(worst_h) / math.log1p(48))
    cls = "go" if worst_h < 1 else ("wait" if worst_h < 24 else "stop")
    if worst_h < 1:
        txt = str(round(worst_h * 60)) + " 分鐘"
    elif worst_h < 48:
        txt = "%.1f 小時" % worst_h
    else:
        txt = "%.1f 天" % (worst_h / 24)
    return ('<div class="stuckbox">'
            + gauge_frac_svg(frac, "卡最久 " + txt)
            + '<div class="stucknum ' + cls + '">' + esc(txt) + '</div>'
            + '<div class="stucklab">最久一件：' + esc(worst[1])
            + '<span>（' + esc(worst[0]) + '）</span></div></div>')


def build(rows, now) -> str:
    import json as _jh
    _hh = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(_hh, "blockers.json"), encoding="utf-8") as f:
            bstate_head = _jh.load(f)
    except Exception:
        bstate_head = {}
    items, acc = items_and_acceptance()
    full = os.environ.get("BUSY_FULLDOC") == "1"
    out = []
    if full:
        out += ['<!doctype html>', '<html lang="zh-Hant">', '<head>',
                '<meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width,initial-scale=1">',
                # 頁面每 30 秒自己重讀一次，跟排程同步
                '<meta http-equiv="refresh" content="30">']
    out += ['<title>AI 戰情室</title>',
           '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
           'family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600&'
           'family=Noto+Sans+TC:wght@400;600&display=swap">',
           f"<style>{CSS}</style>"]
    if full:
        out += ['</head>', '<body>']
    out.append('<div class="wrap">')

    need = [(r["name"], r["need_you"]) for r in rows if r.get("need_you")]
    for b in BLOCKERS:
        st = (bstate_head.get(b["id"]) or {})
        if st.get("status") == "wont_do":
            continue
        tail = "（你說已處理，等系統驗證）" if st.get("status") == "claimed" else ""
        need.append((b["who"], b["how"] + tail))
    need = [n for n in need if n[0] == "使用者" or True]
    need3 = [(r["name"], r["need_you"], r.get("need_since"))
             for r in rows if r.get("need_you")]
    for b in BLOCKERS:
        st = (bstate_head.get(b["id"]) or {})
        if st.get("status") == "wont_do":
            continue
        need3.append((b["who"], b["how"], b.get("since")))
    out_gauge = stuck_gauge(need3, now)
    if need:
        out.append('<button id="lamp" class="lamp" aria-expanded="false">'
                   f'<span class="dot"></span>等你 {len(need)}</button>')
        out.append('<div id="lampbox" class="lampbox" hidden>'
                   + "".join(f'<div class="lampitem"><b>{esc(w)}</b>{esc(t)}</div>'
                             for w, t in need)
                   + '</div>')
    out.append('<header><h1>AI 戰情室</h1>'
               f'<span class="stamp">更新時間 {now:%Y-%m-%d %H:%M:%S}</span></header>')
    _ls, _lat, _lage = live_state(now)
    if _lage is None or _lage > 15:
        out.append('<div class="warnbar">取不到 session 狀態（心跳來源 live.json '
                   + (f'最後一次成功 {esc(_lat)}' if _lat else '從來沒寫成功過')
                   + '）——下面的狀態改用「在跑那一行有沒有東西」判斷</div>')

    lstate, lat, lage = live_state(now)
    fresh = lage is not None and lage <= 15
    out.append(out_gauge)
    out.append('<div class="cluster">')
    for r in rows:
        r.setdefault("idlish", False)
        v = lstate.get(r["name"]) if fresh else None
        r["live"] = None
        r["quiet"] = None
        if isinstance(v, dict):
            r["live"] = "busy" if v.get("running") else "idle"
            try:
                t = datetime.datetime.strptime(v["last_activity"], "%Y-%m-%d %H:%M")
                r["quiet"] = max(0, int((now - t).total_seconds() // 60))
            except Exception:
                pass
        elif isinstance(v, str):
            r["live"] = v
        g = humanise(r["mins"]) if r["mins"] is not None else "不寫程式"
        act = f'{r["last"]:%m-%d %H:%M}' if r["last"] else "沒有分支"
        # 時速表讀數：一筆 commit 約等於 60 行的份量，800 分當滿速。
        q = r.get("quiet")
        if q is None:
            heat = None
        elif q < 3:
            heat = 0.85
        elif q < 10:
            heat = 0.60
        elif q < 20:
            heat = 0.35
        elif q < 30:
            heat = 0.18
        elif q < 45:
            heat = 0.08
        else:
            heat = 0.0
        if r.get("state") == "waiting" and not (q is not None and q <= 5):
            heat = 0.0
        work = r["commits"] * 60 + r["lines"]
        speed = min(100, round(work / 800 * 100))
        fresher = (r.get("quiet") is not None and r["quiet"] <= 5)
        has_run = bool(r.get("running_on"))
        has_wait = bool(r.get("need_you"))
        if has_run:
            wstate, wlabel, wcls = "busy", "忙碌", "stop"
        elif has_wait:
            wstate, wlabel, wcls = "blocked", "被擋住", "wait"
        elif r.get("standby"):
            wstate, wlabel, wcls = "standby", "待命（正常）", "idle"
        else:
            wstate, wlabel, wcls = "idle", "閒置，可以派工", "idle"
        r["wstate"], r["wlabel"] = wstate, wlabel
        if r.get("state") == "waiting" and not fresher:
            bf, pc = 0.0, "stop"
            pct = f'在等（{r.get("idle_why") or "沒說等什麼"}）'
        elif r["live"] == "busy" or fresher:
            pc = "go"
            bf = 1.0 if has_run else 0.0
            if r.get("state") == "waiting":
                pct = "在跑"
            elif not r["branch"]:
                pct = "在跑"
            else:
                pct = f"在跑・這小時 {r['commits']} 筆進 git"
        elif r.get("long_job"):
            bf, pc = 0.55, "wait"
            pct = f'長工作執行中（{r["long_job"]}・{r.get("long_eta", "時間未定")}）'
        elif r["live"] == "idle":
            why = r.get("idle_why")
            bf = heat if heat is not None else 0.0
            if bf > 0:
                pct = f"剛動過 {esc(humanise(q))}前" + (f"（{why}）" if why else "")
                pc = "wait"
            else:
                pct = "0%　閒置" + (f"（{why}）" if why else "（沒說原因）")
                pc = "stop"
        else:
            bf = 1.0 if has_run else 0.0
            pct, pc = wlabel, wcls
        rg = humanise(r["rmins"]) if r["rmins"] is not None else "沒回報過"
        if bf is None:
            bf = 1.0 if has_run else 0.0
            pct = wlabel
        elif not pct.startswith(f"{round(bf * 100)}%"):
            pct = f"{round(bf * 100)}%　" + pct.split("　", 1)[-1]                 if pct[:1].isdigit() else f"{round(bf * 100)}%　{pct}"
        idlish = (wstate in ("blocked", "idle")) or (not fresher and not r.get("standby", False)
                  and (r.get("state") == "waiting"
                       or (r["live"] == "idle" and not r.get("long_job"))))
        r["idlish"], r["short"] = idlish, (r.get("idle_why") or "沒說原因")
        pc = "go" if (bf or 0) < .34 else ("wait" if (bf or 0) < .67 else "stop")
        head, rest = (pct.split("　", 1) + [""])[:2] if "　" in pct else (pct, "")
        detail = rest or pct
        git = (f'1 小時內 {r["commits"]} 筆・{r["lines"]} 行'
               if r["branch"] else "沒有程式分支")
        dot = ("on" if wstate == "busy" else
               "blocked" if wstate == "blocked" else "off")
        out.append(
            f'<details class="dial"><summary>'
            f'<span class="lampdot {dot}"></span>'
            f'<span class="dial-name">{esc(r["name"])}</span>'
            f'<span class="dial-sub">{esc(wlabel)}　今天 '
            f'{r.get("t_done",0)+len(r.get("t_wait") or [])+r.get("t_run",0)} 件：'
            f'完成 {r.get("t_done",0)}・待你驗證 {len(r.get("t_wait") or [])}'
            f'・在跑 {r.get("t_run",0)}</span>'
            f'</summary>'
            f'<div class="dmore">'
            + ('<div class="blocked">等你回覆，而且沒有其他工作可做'
               '（BOSS 未補派）</div>' if wstate == "blocked" else '')
            + '<div class="need">⏳ 等你：'
            + (esc(r.get("need_you")) if r.get("need_you") else "無")
            + '</div>'
            + '<div class="runs">▶ 在跑：'
            + (esc(r.get("running_on")) or "沒有（可以派工）")
            + '</div>'
            + ('<div class="tl"><b>完成（你驗過的）</b><ol>'
               + "".join(f'<li>{esc(x)}</li>' for x in (r.get("t_donelist") or []))
               + '</ol></div>' if r.get("t_donelist") else '')
            + ('<div class="tl"><b>待你驗證</b><ol>'
               + "".join(f'<li>{esc(x)}</li>' for x in (r.get("t_wait") or []))
               + '</ol></div>' if r.get("t_wait") else '')
            + (f'<div class="tnote2">{esc(r.get("t_note"))}</div>'
               if r.get("t_note") else '')
            + f'<div class="dgit">最後回報 {esc(r.get("asked") or "沒回報過")}</div>'
            + '</div></details>')
    out.append('</div>')
    n_run = sum(1 for r in rows if r.get("wstate") == "busy")
    n_blk = sum(1 for r in rows if r.get("wstate") == "blocked")
    n_able = sum(1 for r in rows if not r.get("standby", False))
    done_ok = sum(1 for v in acc.values() if v.get("verdict") == "pass")
    wait_v = sum(1 for it in items if it["state"] == "done" and it["id"] not in acc)
    out.append(f'<div class="tally">在跑 <b>{n_run}</b> / 可工作 <b>{n_able}</b>'
               + (f'　　被擋住 <b class="bad">{n_blk}</b>' if n_blk else '')
               + f'　　驗過 <b>{done_ok}</b>・待你驗證 <b>{wait_v}</b>（今天 00:00 起）'
               + '<span class="tnote">燈只代表程序在不在跑，不代表有進展</span></div>')
    _skip = (f'<div class="tally">在跑 <b>{n_run}</b> / 可工作 <b>{n_able}</b>'
               + (f'　　被擋住 <b class="bad">{n_blk}</b>' if n_blk else '')
               + '</div>')
    off = [r for r in rows if r.get("idlish")]
    on = [r for r in rows if not r.get("idlish") and not r.get("standby", False)]
    if off:
        out.append('<div class="callout"><b>沒在做事 ' + str(len(off)) + '：</b>'
                   + '　'.join(f'{esc(r["name"])}（{esc(r.get("wlabel", ""))}）'
                                for r in off)
                   + f'<span class="ok">在跑 {len(on)}：'
                   + '、'.join(esc(r["name"]) for r in on) + '</span></div>')
    else:
        out.append('<div class="callout ok-all">每一條線都有事在做，沒有需要你處理的</div>')

    out.append('<div class="posgrid">')
    for q in positions(now):
        out.append(
            f'<details class="posbox"><summary>'
            f'<span class="pname">{esc(q["name"])}</span>'
            f'<span class="ver">{esc(q["ver"])}</span>'
            f'<span class="pmeta">{esc(q["when"])}</span></summary>'
            f'<dl class="det">'
            f'<dt>版本</dt><dd>{esc(q["ver"])}</dd>'
            f'<dt>commit</dt><dd class="mono">{esc(q["commit"])}</dd>'
            f'<dt>時間</dt><dd class="mono">{esc(q["when"])}</dd>'
            f'<dt>怎麼來的</dt><dd>{esc(q["how"])}</dd>'
            f'<dt>說明</dt><dd>{esc(q["note"])}</dd>'
            f'</dl></details>')
    out.append('</div>')

    n_wait = sum(1 for it in items
                 if it["state"] == "done" and it["id"] not in acc)
    out.append('<div class="tabs" role="tablist">'
               '<button class="tab" data-t="feed" role="tab">事件變更</button>'
               f'<button class="tab" data-t="accept" role="tab">驗收'
               + (f'<b class="badge">{n_wait}</b>' if n_wait else '') + '</button>'
               '<button class="tab" data-t="lines" role="tab">誰在忙</button>'
               f'<button class="tab" data-t="stuck" role="tab">卡住的 {len(BLOCKERS)} 件</button>'
               '</div>')
    ev = events(now)
    out.append('<section id="tab-feed">')
    out.append('<div class="dstamp">最新一筆事件 '
               + (f'{ev[0]["t"]:%m-%d %H:%M}' if ev else '無')
               + f'　頁面產生 {now:%H:%M:%S}</div>')
    out.append(render_feed(ev, now))
    out.append('</section>')
    out.append('<section id="tab-accept" hidden>')
    last_acc = max((v.get("at", "") for v in acc.values()), default="")
    out.append('<div class="dstamp">清單由 wbs 維護　'
               + (f'你最後一次勾選 {esc(last_acc)}' if last_acc else '你還沒勾過任何一項')
               + '</div>')
    out.append(render_accept(items, acc))
    out.append('</section>')
    newest = max((r["asked"] for r in rows if r.get("asked")), default="")
    out.append('<section id="tab-lines">'
               f'<div class="dstamp">現況由各 session 回報，最新一筆 {esc(newest or "無")}'
               f'　忙／閒取樣 {esc(lat or "無")}　頁面產生 {now:%H:%M:%S}</div>'
               '<div class="legend"><span>指針＝在不在動（安靜超過一小時才是 0）：</span>'
               '<span><i style="background:var(--stop)"></i>閒置</span>'
               '<span><i style="background:var(--wait)"></i>剛動過／長工作</span>'
               '<span><i style="background:var(--stop)"></i>超過 4 小時</span></div>'
               '<div class="rows">')
    for r in rows:
        label, cls = STATE_LABEL.get(r["state"], ("進行中", "go"))
        mism = (r["state"] == "working" and r["mins"] is not None and r["mins"] > 240)
        asked = r.get("asked") or ""
        stale = ""
        # 三層：機器算的事實（不會過期）＋ 最後一次自述（舊了就轉灰）＋ 判讀。
        # 使用者問「資料已過期，那我要判斷什麼？」——畫面要直接回答「要不要插話」。
        old = r["rmins"] is not None and r["rmins"] > 30
        cm = r["mins"]                      # 距離最後一筆 commit
        fact = (f'最後推版 {r["last"]:%m-%d %H:%M}（{humanise(cm)}前）'
                if r["last"] else "這條線不寫程式")
        if cm is not None and cm <= 30:
            vk, vc = "在動", "go"
            if old:
                vk = "在動，只是沒回報"
        elif r["live"] == "busy" or r.get("long_job"):
            vk, vc = "在動", "go"
        elif r.get("state") == "waiting":
            vk, vc = f'在等：{r.get("idle_why") or "沒說等什麼"}', "wait"
        elif old and (cm is None or cm > 60):
            vk, vc = "可能真的卡住，值得問一句", "stop"
        else:
            vk, vc = "正常", "go"

        said = esc(r["doing"])
        if old and r.get("asked"):
            said = (f'<span class="stale">{esc(r["doing"])}'
                    f'<i>（這是 {esc(r["asked"])} 說的，可能已經變了）</i></span>')

        out.append(
            f'<div class="row {vc}">'
            f'<div class="who">{esc(r["name"])}<span>{esc(r["role"])}</span></div>'
            f'<div class="doing">{said}'
            f'<span class="fact">{esc(fact)}</span></div>'
            f'<div class="meta"><span class="pill {vc}">{esc(vk)}</span>'
            f'<span class="gap">回報 '
            + (esc(humanise(r["rmins"])) + "前" if r["rmins"] is not None else "沒回報過")
            + '</span></div></div>')
    out.append('</div>')

    out.append('</section><section id="tab-stuck" hidden>')
    out.append('<div class="dstamp">人工維護，每一列自己標證據等級</div>')
    import json as _j
    _here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(_here, "blockers.json"), encoding="utf-8") as f:
            bstate = _j.load(f)
    except Exception:
        bstate = {}
    for b in BLOCKERS:
        tag = "ev" if b["level"] == "證據" else ""
        st = bstate.get(b["id"]) or {}
        badge = ""
        if st.get("status") == "claimed":
            badge = ('<div class="claim">你說已處理（' + esc(st.get("claimed_at", ""))
                     + '）　<b>等系統驗證</b>：' + esc(b["verify_by"]) + '</div>')
        elif st.get("status") == "wont_do":
            badge = ('<div class="claim off">你說不做了（'
                     + esc(st.get("claimed_at", "")) + '）</div>')
        says = "".join(
            f'<div class="say"><span>{esc(x["at"])}</span>{esc(x["text"])}'
            + "".join(f'<a href="{esc(u)}" target="_blank">'
                      f'<img src="{esc(u)}" alt="附圖"></a>'
                      for u in (x.get("images") or []))
            + '</div>' for x in (st.get("says") or []))
        out.append(
            f'<div class="blk" data-bid="{esc(b["id"])}"><h3>{esc(b["what"])}</h3>'
            f'<p>{esc(b["detail"])}</p>'
            f'<div class="act"><span class="tag {tag}">{esc(b["level"])}</span>'
            f'<span>卡在 <b>{esc(b["who"])}</b></span>'
            f'<span class="path">{esc(b["how"])}</span></div>'
            f'<div class="vby">怎麼算解決了：{esc(b["verify_by"])}</div>'
            + badge
            + (f'<div class="saywrap"><div class="sayhd">你說的</div>{says}</div>'
               if says else '')
            + '<textarea rows="1" placeholder="回一句話（可以直接貼圖）"></textarea>'
            + '<div class="it-act">'
              '<button class="btn plain" data-blk="reply">回話</button>'
              '<button class="btn go" data-blk="claim_done">我處理好了</button>'
              '<button class="btn stop" data-blk="wont_do">不做了／先擱著</button>'
              '<span class="said"></span></div>'
            + '</div>')

    out.append('</section>')
    out.append('<footer>指針是「現在忙不忙」，取樣超過 15 分鐘會顯示「不知道」。'
               '每一行的現況都標最後回報時間，超過 30 分鐘就不再當成現況。'
               '公司機那格連不到，是人工填的。</footer></div>')
    out.append("""<script>
// 整頁每 30 秒重載，選到的頁簽記在網址的 #，重載後才不會跳回第一頁。
(function () {
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
  function show(name) {
    tabs.forEach(function (t) {
      var on = t.dataset.t === name;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      var sec = document.getElementById('tab-' + t.dataset.t);
      if (sec) { sec.hidden = !on; }
    });
  }
  tabs.forEach(function (t) {
    t.addEventListener('click', function () {
      location.hash = t.dataset.t;
      show(t.dataset.t);
    });
  });
  show((location.hash || '').replace('#', '') === 'stuck' ? 'stuck' : 'lines');
})();
</script>""")
    if full:
        out += ['</body>', '</html>']
    return "\n".join(out)


if __name__ == "__main__":
    dest = sys.argv[1] if len(sys.argv) > 1 else "busy_board.html"
    rows, now = collect()
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(build(rows, now))
    print(f"written: {dest}")
