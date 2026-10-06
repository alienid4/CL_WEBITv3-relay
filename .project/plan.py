#!/usr/bin/env python3
# C1 拆題器 (5.0) — 一句話需求 → AI 拆成切片 → 寫進 backlog.json。
# 這支補上「一次講完、AI 自己分配」的前半段：使用者只出題，切片由 AI 產。
#
# 用法：
#   python .project/plan.py "做一個 XX 功能"     # 拆新需求，append 進 backlog
#   python .project/plan.py --refill             # 「還缺什麼」完整性檢查（loop 待辦清空時自動呼叫）
# 引擎：loop_config.json 的 plan_cmd（空則落回 agent_cmd）。
# AI 必須輸出 JSON 陣列（可包在 ```json fenced block 裡）；解析失敗重試 1 次，再失敗就報錯停，不硬猜。
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
PROJ = ROOT / ".project"
CONFIG = PROJ / "loop_config.json"
BACKLOG = PROJ / "backlog.json"
RISK_LEVELS = ("small", "medium", "large", "high-risk")


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                       encoding="utf-8", errors="replace")
    return r.stdout


FORMAT_RULES = """輸出規則（嚴格遵守）：
- 只輸出一個 JSON 陣列（可包在 ```json fenced block 裡），不要其他說明文字。
- 每個切片物件包含：
  "id"        ：字串，短代號（如 "S1"）
  "desc"      ：一句話講這個切片要做什麼（會直接當 commit 訊息）
  "done_when" ：怎樣算對——要可驗證（能跑的命令 / 看得到的行為），不要寫「完成即可」
  "review"    ：true|false，重要或高風險切片標 true（會多過一道對抗式審查）
  "risk"      ：small|medium|large|high-risk，決定 checks.py 要跑多深——
                small=明確bug/文件小修/低風險UI文案；
                medium=一般功能切片、單一API+UI；
                large=跨模組、DB schema 改動、資料寫入流程；
                high-risk=金流/權限/AD/SSO/正式資料/不可逆操作（這個等級一定要同時標 review:true）。
                不確定就寧可標高一級，不要低估。
- 切片要小：一件 = 一次能改完、能驗證、能 commit 的量。
- 依賴順序排列：先做的排前面。
- 不確定或範圍太糊的不要硬拆，寧可少列。
- 如果沒有需要新增的切片，輸出空陣列 []。"""


def build_plan_prompt(request: str) -> str:
    return "\n".join([
        "你是拆題器。把下面的需求拆成小切片待辦清單，讓自主開發迴圈一件一件做。",
        "",
        f"需求：{request}",
        "",
        "專案近況（供參考）：",
        git("log", "--oneline", "-10").strip() or "（尚無 commit）",
        "",
        FORMAT_RULES,
    ])


def build_refill_prompt(backlog: list) -> str:
    done = [f"- {s.get('id')} {s.get('desc')}" for s in backlog if s.get("status") == "done"]
    blocked = [f"- {s.get('id')} {s.get('desc')}" for s in backlog if s.get("status") == "blocked"]
    return "\n".join([
        "你是完整性檢查者。目前待辦已清空，請檢查「還缺什麼」——",
        "已完成的切片如下，找出：沒做到的邊角、缺的測試、缺的文件、明顯的收尾工作。",
        "",
        "已完成：", *(done or ["（無）"]),
        "卡住（不要重複列這些）：", *(blocked or ["（無）"]),
        "",
        "專案近況：",
        git("log", "--oneline", "-10").strip() or "（尚無 commit）",
        "",
        "只補「真的缺」的切片，不要為了湊數而發明工作；真的沒缺就輸出空陣列 []。",
        "",
        FORMAT_RULES,
    ])


def extract_slices(output: str) -> list | None:
    """從 AI 輸出抓 JSON 陣列：先試 fenced block，再試整段、再試第一個 [...] 區塊。"""
    candidates: list[str] = []
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", output, re.DOTALL):
        candidates.append(m.group(1).strip())
    candidates.append(output.strip())
    m = re.search(r"\[.*\]", output, re.DOTALL)
    if m:
        candidates.append(m.group(0))
    for cand in candidates:
        try:
            data = json.loads(cand)
        except Exception:
            continue
        if isinstance(data, list) and all(isinstance(x, dict) for x in data):
            return data
    return None


def normalize(slices: list, existing_ids: set[str]) -> list:
    out = []
    n = 0
    for s in slices:
        if not s.get("desc"):
            continue
        sid = str(s.get("id") or "").strip()
        while not sid or sid in existing_ids:
            n += 1
            sid = f"P{n}"
        existing_ids.add(sid)
        risk = str(s.get("risk") or "small").strip().lower()
        if risk not in RISK_LEVELS:
            risk = "small"  # AI 輸出髒值不硬信，落回最保守的 small（不是最寬鬆，可攜 checks 對 small 一樣安全）
        review = bool(s.get("review", False))
        if risk == "high-risk" and not review:
            review = True  # high-risk 沒標 review 就幫它補上，checks.py 也會擋，這裡先做對的事不等擋下才發現
        out.append({
            "id": sid,
            "desc": str(s["desc"]).strip(),
            "done_when": str(s.get("done_when") or "checks 全 PASS").strip(),
            "review": review,
            "risk": risk,
            "status": "todo",
            "attempts": 0,
        })
    return out


def call_agent(cfg: dict, prompt: str) -> tuple[bool, str]:
    cmd = (cfg.get("plan_cmd") or cfg.get("agent_cmd") or "").strip()
    if not cmd:
        print("⛔ loop_config.json 沒填 plan_cmd（也沒有舊欄位 agent_cmd），無法拆題。")
        return False, ""
    timeout_min = cfg.get("agent_timeout_minutes", 15)
    try:
        r = subprocess.run(shlex.split(cmd), cwd=ROOT, input=prompt,
                           capture_output=True, encoding="utf-8", errors="replace",
                           timeout=timeout_min * 60)
    except subprocess.TimeoutExpired:
        print("⛔ 拆題引擎逾時。")
        return False, ""
    if r.returncode != 0:
        print(f"⛔ 拆題引擎非 0 離開：{(r.stderr or r.stdout or '').strip()[-300:]}")
        return False, ""
    return True, r.stdout or ""


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--refill"]
    refill = "--refill" in sys.argv[1:]
    if not refill and not args:
        print('用法：python .project/plan.py "一句話講你要做什麼"　或　--refill')
        return 2

    cfg = load_json(CONFIG, {})
    backlog = load_json(BACKLOG, [])
    if not isinstance(backlog, list):
        backlog = []
    # 範本殘留（desc 還是預設佔位字）不算真切片，拆題時直接汰換
    backlog = [s for s in backlog if "換成你的第一個切片" not in str(s.get("desc", ""))]
    existing_ids = {str(s.get("id")) for s in backlog}

    prompt = build_refill_prompt(backlog) if refill else build_plan_prompt(" ".join(args))

    slices = None
    for attempt in (1, 2):  # 解析失敗重試 1 次
        ok, output = call_agent(cfg, prompt)
        if not ok:
            return 3
        slices = extract_slices(output)
        if slices is not None:
            break
        print(f"⚠ 第 {attempt} 次輸出不是合法 JSON 陣列" + ("，重試一次…" if attempt == 1 else "。"))
        prompt += "\n\n（上次輸出無法解析成 JSON 陣列。這次請『只』輸出 JSON 陣列本身。）"
    if slices is None:
        print("⛔ 拆題失敗：AI 兩次都沒給出合法 JSON。不硬猜，請人工檢查或手寫 backlog.json。")
        return 3

    new_items = normalize(slices, existing_ids)
    if not new_items:
        print("沒有新增切片（AI 判斷沒有缺的了）。" if refill else "⚠ AI 回了空清單，沒有東西可加。")
        return 0

    backlog.extend(new_items)
    BACKLOG.write_text(json.dumps(backlog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已新增 {len(new_items)} 個切片進 {BACKLOG.name}：")
    for s in new_items:
        tag = f"[{s['risk']}]" + ("（review）" if s["review"] else "")
        print(f"  ▢ {s['id']} {tag} {s['desc']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
