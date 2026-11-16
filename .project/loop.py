#!/usr/bin/env python3
# C1 自主驅動器 (5.0) — 把 snapshot / checks / plan / review / decisions 串成「按一次、走開」的迴圈。
# 規格來源：一次性開發提示詞_5.0_FINAL.md（提示詞即規格）
#
# 5.0 引擎在 4.x 之上「機制上」多強制了這些：
#   - timeout      ：AI CLI 掛住不會拖死 loop（agent_timeout_minutes）
#   - 失敗回灌     ：上次失敗原因（checks 輸出 + diff 摘要）餵回下一次 prompt，不盲賭重試
#   - 失敗清理     ：每次 FAIL 先把 diff 存進 logs/ 當證據，再還原工作區，不讓髒改動混進下次 commit
#   - 每回合留檔   ：.project/logs/round_NNN_<id>.md（prompt / AI 輸出 / checks 輸出 / 結果）
#   - 現況餵入     ：每回合先跑 snapshot，prompt 附上 HEAD 與近期 commit
#   - 機制絆線     ：AI 碰到 tripwire_paths（.env / migrations / 憑證…）或大量刪檔 → 還原、寫 decisions、整個停下
#   - multi-engine ：plan_cmd / exec_cmd / review_cmd 三格（可填不同家引擎；舊 agent_cmd 仍可當 fallback）
#   - 自我補題     ：待辦清空後呼叫 plan.py --refill 做「還缺什麼」檢查（最多 max_refills 次）
#   - 重要切片審查 ：backlog 標 "review": true 的切片，checks PASS 後還要過 review.py 對抗式審查
# 4.x 既有的照舊：待辦自動拉、回合/時間/失敗次數煞車、卡住升級 blocked、只在非保護分支跑。
#
# 用法：
#   python .project/loop.py --dry-run   # 只看計畫與煞車，不呼叫 AI、不改東西（先測骨架）
#   python .project/loop.py --once      # 只跑一件就停（第一次真的跑，建議先這個）
#   python .project/loop.py             # 一路跑到待辦清空或觸發煞車
from __future__ import annotations

import fnmatch
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
PROJ = ROOT / ".project"
CONFIG = PROJ / "loop_config.json"
BACKLOG = PROJ / "backlog.json"
DECISIONS = PROJ / "decisions.json"
REPORT = PROJ / "run_report.md"
LOGS = PROJ / "logs"

DEFAULT_CONFIG = {
    "max_rounds": 20,
    "max_minutes": 60,
    "max_fails_per_slice": 3,
    "agent_timeout_minutes": 15,
    "protect_branches": ["main", "master"],
    # multi-engine 三格：拆題 / 實作 / 審查，可各填不同 AI CLI（審查建議填另一家，跨引擎互驗）。
    #   claude 例： "claude -p --permission-mode acceptEdits"
    #   codex 例：  "codex exec --full-auto"
    # 留空 = 落回 agent_cmd（舊版欄位，向後相容）；連 agent_cmd 也空 = 不真的呼叫（只測骨架）。
    "plan_cmd": "",
    "exec_cmd": "",
    "review_cmd": "",
    "agent_cmd": "",
    "agent_prompt_via": "stdin",  # stdin | file
    # 機制絆線：AI 的改動碰到這些 → 還原、寫 decisions.json、整個 loop 停下問人（prompt 軟護欄之外的硬擋）
    "tripwire_paths": [".env", ".env.*", "migrations/", "*.pem", "*.key"],
    "max_deleted_files": 5,
    # 待辦清空後，自動跑 plan.py --refill「還缺什麼」的次數上限
    "max_refills": 1,
}

DRY = "--dry-run" in sys.argv
ONCE = "--once" in sys.argv


def log(msg: str) -> None:
    print(msg, flush=True)


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(p: Path, data) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace")


def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def worktree_dirty() -> str:
    return git("status", "--porcelain").stdout.strip()


def ensure_logs_dir() -> None:
    # logs/ 自帶 .gitignore（內容 *）→ 不會被 add -A 掃進 commit，也不會被 clean -fd 清掉
    LOGS.mkdir(parents=True, exist_ok=True)
    gi = LOGS / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")


def resolve_cmd(cfg: dict, role: str) -> str:
    # role: plan | exec | review。空的落回舊欄位 agent_cmd（向後相容）。
    return (cfg.get(f"{role}_cmd") or cfg.get("agent_cmd") or "").strip()


def run_agent(cfg: dict, role: str, prompt: str, timeout_min: float) -> tuple[str, str]:
    """回傳 (status, output)。status: skipped | ok | failed | timeout"""
    cmd = resolve_cmd(cfg, role)
    if not cmd:
        return "skipped", ""
    via = cfg.get("agent_prompt_via", "stdin")
    kwargs = dict(cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace",
                  timeout=timeout_min * 60)
    try:
        if via == "file":
            ensure_logs_dir()
            pf = LOGS / ".loop_prompt.txt"
            pf.write_text(prompt, encoding="utf-8")
            r = subprocess.run(shlex.split(cmd.replace("{prompt_file}", str(pf))), **kwargs)
        else:  # stdin
            r = subprocess.run(shlex.split(cmd), input=prompt, **kwargs)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "")
        return "timeout", out if isinstance(out, str) else ""
    output = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr and r.stderr.strip() else "")
    return ("ok" if r.returncode == 0 else "failed"), output


def run_checks(slice_: dict) -> tuple[bool, str]:
    env = {**os.environ,
           "SLICE_RISK": str(slice_.get("risk") or "small"),
           "SLICE_REVIEW": "1" if slice_.get("review") else "0"}
    r = subprocess.run([sys.executable, str(PROJ / "checks.py")], cwd=ROOT, env=env,
                       capture_output=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def run_review(slice_: dict) -> tuple[bool, str]:
    r = subprocess.run([sys.executable, str(PROJ / "review.py"), "--staged",
                        "--desc", slice_.get("desc", ""), "--done-when", slice_.get("done_when", "")],
                       cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def run_refill() -> int:
    """呼叫 plan.py --refill 補題。回傳新增的 todo 數。"""
    before = sum(1 for s in load_json(BACKLOG, []) if s.get("status") in (None, "todo"))
    r = subprocess.run([sys.executable, str(PROJ / "plan.py"), "--refill"], cwd=ROOT,
                       capture_output=True, encoding="utf-8", errors="replace")
    tail = (r.stdout + r.stderr).strip().splitlines()
    if tail:
        log("  refill：" + tail[-1])
    after = sum(1 for s in load_json(BACKLOG, []) if s.get("status") in (None, "todo"))
    return max(0, after - before)


def snapshot_context() -> str:
    """每回合先更新現況，回傳給 prompt 用的精簡摘要。"""
    subprocess.run([sys.executable, str(PROJ / "snapshot.py")], cwd=ROOT,
                   capture_output=True, encoding="utf-8", errors="replace")
    head = git("rev-parse", "--short", "HEAD").stdout.strip() or "(尚無 commit)"
    recent = git("log", "--oneline", "-5").stdout.strip() or "(尚無 commit)"
    return f"HEAD: {head}\n近 5 筆 commit：\n{recent}"


def stage_all() -> None:
    git("add", "-A")


def staged_name_status() -> list[tuple[str, str]]:
    """回傳 [(狀態, 路徑)]，狀態如 A/M/D/R100…（R/C 取重新命名後的目標路徑）"""
    out = git("diff", "--cached", "--name-status", "HEAD").stdout
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0], parts[-1]))
    return rows


def tripwire_hits(cfg: dict, changes: list[tuple[str, str]]) -> list[str]:
    hits: list[str] = []
    pats = cfg.get("tripwire_paths", [])
    for status, path in changes:
        base = path.rsplit("/", 1)[-1]
        for pat in pats:
            if pat.endswith("/"):
                if path.startswith(pat) or f"/{pat}" in f"/{path}":
                    hits.append(f"{path}（命中目錄絆線 {pat}）")
                    break
            elif fnmatch.fnmatch(base, pat) or fnmatch.fnmatch(path, pat):
                hits.append(f"{path}（命中絆線 {pat}）")
                break
    deleted = [p for st, p in changes if st.startswith("D")]
    if len(deleted) > cfg.get("max_deleted_files", 5):
        hits.append(f"一次刪了 {len(deleted)} 個檔（> max_deleted_files={cfg.get('max_deleted_files', 5)}）")
    return hits


def save_fail_patch(round_no: int, slice_id: str) -> Path:
    ensure_logs_dir()
    patch = LOGS / f"round_{round_no:03d}_{slice_id}.patch"
    diff = git("diff", "--cached", "HEAD").stdout
    patch.write_text(diff or "(無 diff)", encoding="utf-8")
    return patch


def reset_worktree() -> None:
    """還原到 HEAD。logs/ 因自帶 .gitignore 不受影響（clean -fd 不清 ignored）。"""
    git("reset", "--hard")
    git("clean", "-fd")


def diff_stat_summary() -> str:
    return git("diff", "--cached", "--stat", "HEAD").stdout.strip()[-800:]


def record_fail(slice_: dict, reason: str, detail: str) -> None:
    slice_["attempts"] = slice_.get("attempts", 0) + 1
    slice_["last_error"] = (reason + "\n" + detail).strip()[:1500]


def escalate(slice_: dict, reason: str) -> None:
    d = load_json(DECISIONS, {"decisions": [], "doc_scan_ignore": []})
    d.setdefault("loop_escalations", [])
    d["loop_escalations"].append({
        "id": slice_.get("id"), "desc": slice_.get("desc"),
        "reason": reason, "attempts": slice_.get("attempts", 0),
    })
    save_json(DECISIONS, d)


def build_prompt(slice_: dict, context: str) -> str:
    parts = [
        "以 5.0 自主模式，只實作『這一個切片』，不碰別的、不做無關重構。\n",
        f"專案現況：\n{context}\n",
        f"切片：{slice_.get('desc', '')}",
        f"完成的定義（怎樣算對）：{slice_.get('done_when', '')}\n",
    ]
    if slice_.get("last_error"):
        parts.append("⚠ 上一次嘗試失敗，原因如下，這次請針對它修正：\n"
                     f"{slice_['last_error']}\n")
    parts.append(
        "規則：\n"
        "- 只動這個切片需要的檔案。\n"
        "- 不要碰 .env、migrations、憑證、金流/權限/刪資料相關（引擎有機制絆線，碰了整個會被擋下）。\n"
        "- 不要自己 git commit，改完即可，由驅動器驗證後 commit。\n"
        "- 做完確認上面『完成的定義』達成。\n"
    )
    return "\n".join(parts)


def write_round_log(round_no: int, slice_: dict, prompt: str, agent_out: str,
                    checks_out: str, result: str) -> None:
    ensure_logs_dir()
    f = LOGS / f"round_{round_no:03d}_{slice_.get('id', '')}.md"
    f.write_text("\n".join([
        f"# 回合 {round_no} — {slice_.get('id')} {slice_.get('desc', '')}",
        f"\n結果：{result}\n",
        "## Prompt\n", "```", prompt, "```\n",
        "## AI 輸出\n", "```", (agent_out or "(無輸出)").strip()[-8000:], "```\n",
        "## checks / review 輸出\n", "```", (checks_out or "(未跑)").strip()[-4000:], "```",
    ]), encoding="utf-8")


def write_report(cfg: dict, rounds: int, elapsed_min: float, backlog: list, note: str) -> None:
    done = [s for s in backlog if s.get("status") == "done"]
    blocked = [s for s in backlog if s.get("status") == "blocked"]
    todo = [s for s in backlog if s.get("status") in (None, "todo", "doing")]

    def sec(title: str, items: list) -> list:
        return [f"## {title}", *(items or ["- （無）"]), ""]

    lines = [
        "# Run Report（自主迴圈）", "",
        f"- 狀態：{note}",
        f"- 回合：{rounds} / {cfg['max_rounds']}　時間：{elapsed_min:.1f} / {cfg['max_minutes']} 分",
        f"- 完成 {len(done)}　卡住 {len(blocked)}　待辦剩 {len(todo)}",
        "- 每回合詳細軌跡：`.project/logs/`", "",
    ]
    lines += sec("完成", [f"- ✅ {s['id']} {s['desc']}" for s in done])
    lines += sec("卡住（已升級給人）",
                 [f"- 🛑 {s['id']} {s['desc']}（試 {s.get('attempts', 0)} 次）"
                  + (f"\n  - 最後失敗原因：{s['last_error'].splitlines()[0][:120]}" if s.get("last_error") else "")
                  for s in blocked])
    lines += sec("還沒做", [f"- ▢ {s['id']} {s['desc']}" for s in todo])
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    cfg = {**DEFAULT_CONFIG, **load_json(CONFIG, {})}
    if not CONFIG.exists():
        save_json(CONFIG, DEFAULT_CONFIG)
        log(f"已建立預設 {CONFIG.name}。")

    # 護欄 1：不在保護分支上跑
    br = current_branch()
    if br in cfg["protect_branches"]:
        log(f"⛔ 目前在保護分支「{br}」，拒絕自主跑。請先切到工作分支：")
        log("   git checkout -b work/auto-loop")
        return 2

    # 護欄 2：工作區必須乾淨（失敗清理會 reset --hard，不能拿使用者未 commit 的東西冒險）
    dirty = worktree_dirty()
    if dirty and not DRY:
        log("⛔ 工作區有未 commit 的改動，拒絕自主跑（失敗清理會 reset，可能吃掉你的東西）：")
        log("\n".join("   " + ln for ln in dirty.splitlines()[:10]))
        log("   請先 commit 或 stash 再跑。")
        return 2

    backlog = load_json(BACKLOG, None)
    if not isinstance(backlog, list) or not backlog:
        log(f"⛔ 找不到有效的 {BACKLOG.name}（要是一個切片陣列）。兩個建法：")
        log('   ① python .project/plan.py "一句話講你要做什麼"（AI 幫你拆）')
        log('   ② 手寫：[{ "id":"S1", "desc":"...", "done_when":"...", "status":"todo", "attempts":0 }]')
        return 2

    log(f"分支：{br}｜待辦 {len(backlog)} 件｜煞車：{cfg['max_rounds']} 回合 / {cfg['max_minutes']} 分 / "
        f"每件 {cfg['max_fails_per_slice']} 次 / 單次呼叫 {cfg['agent_timeout_minutes']} 分"
        + ("　[DRY-RUN]" if DRY else ""))

    start = time.monotonic()
    rounds = 0
    refills_used = 0

    def elapsed_min() -> float:
        return (time.monotonic() - start) / 60.0

    while True:
        # 煞車：預算檢查
        if rounds >= cfg["max_rounds"]:
            write_report(cfg, rounds, elapsed_min(), backlog, "到回合上限，停下（待續）")
            log("⛽ 到回合上限，停下，出待續報告。"); return 0
        if elapsed_min() >= cfg["max_minutes"]:
            write_report(cfg, rounds, elapsed_min(), backlog, "到時間上限，停下（待續）")
            log("⛽ 到時間上限，停下，出待續報告。"); return 0

        # 拉下一件；清空則先試補題（自我完整性檢查），補不出來才收工
        nxt = next((s for s in backlog if s.get("status") in (None, "todo", "doing")), None)
        if nxt is None:
            if not DRY and refills_used < cfg["max_refills"] and resolve_cmd(cfg, "plan"):
                refills_used += 1
                log(f"\n待辦清空 → 自我補題（第 {refills_used}/{cfg['max_refills']} 次）：問 AI「還缺什麼」…")
                if run_refill() > 0:
                    backlog = load_json(BACKLOG, backlog)
                    continue
                log("  補題結果：沒有新切片。")
            write_report(cfg, rounds, elapsed_min(), backlog, "待辦清空，全部處理完")
            log("✅ 待辦清空，收工。"); return 0

        rounds += 1
        log(f"\n── 回合 {rounds}：{nxt['id']} {nxt.get('desc', '')}")

        if DRY:
            log(f"  [dry-run] 會做這件；完成定義：{nxt.get('done_when', '')}")
            nxt["status"] = "done"  # 只在記憶體標記，dry-run 不存檔
            if ONCE:
                log("  [dry-run] --once，停。"); return 0
            continue

        nxt["status"] = "doing"; save_json(BACKLOG, backlog)

        # 現況餵入 + 呼叫 AI 實作這件（有 timeout）
        ctx = snapshot_context()
        prompt = build_prompt(nxt, ctx)
        log(f"  呼叫 AI（上限 {cfg['agent_timeout_minutes']} 分）…")
        status, agent_out = run_agent(cfg, "exec", prompt, cfg["agent_timeout_minutes"])
        for ln in agent_out.strip().splitlines()[-3:]:
            log("    │ " + ln[:160])

        if status == "skipped":
            log("  ⚠ loop_config.json 沒填 exec_cmd（或舊欄位 agent_cmd），無法實作切片。停。（見 SETUP.md）")
            nxt["status"] = "todo"; save_json(BACKLOG, backlog); return 3

        if status in ("failed", "timeout"):
            reason = "AI 執行逾時（timeout）" if status == "timeout" else "AI 執行失敗（非 0 離開）"
            stage_all(); save_fail_patch(rounds, nxt["id"]); reset_worktree()
            record_fail(nxt, reason, agent_out.strip()[-600:])
            if nxt["attempts"] >= cfg["max_fails_per_slice"]:
                nxt["status"] = "blocked"; escalate(nxt, reason)
                log(f"  🛑 連續失敗 {nxt['attempts']} 次 → blocked、寫進 decisions.json。")
            else:
                nxt["status"] = "todo"
                log(f"  ✗ {reason}（第 {nxt['attempts']} 次），失敗原因會餵回下次重試。")
            save_json(BACKLOG, backlog); write_report(cfg, rounds, elapsed_min(), backlog, "進行中")
            write_round_log(rounds, nxt, prompt, agent_out, "", reason)
            if ONCE:
                log("\n--once，停。"); return 0
            continue

        # 進 staging，之後絆線 / checks / review / commit 都以 staged 內容為準
        stage_all()
        changes = staged_name_status()

        # 機制絆線：碰敏感路徑或大量刪檔 → 還原、升級、整個 loop 停下問人
        hits = tripwire_hits(cfg, changes)
        if hits:
            save_fail_patch(rounds, nxt["id"]); reset_worktree()
            nxt["status"] = "blocked"
            record_fail(nxt, "機制絆線觸發", "\n".join(hits))
            escalate(nxt, "機制絆線觸發：" + "; ".join(hits))
            save_json(BACKLOG, backlog)
            write_report(cfg, rounds, elapsed_min(), backlog, "🛑 機制絆線觸發，停下問人")
            write_round_log(rounds, nxt, prompt, agent_out, "\n".join(hits), "機制絆線觸發，已還原改動")
            log("  🛑 機制絆線觸發，已還原改動、寫進 decisions.json，整個停下：")
            for h in hits:
                log("     - " + h)
            return 4

        # 自驗：checks（全 PASS 才算，依切片 risk 分級跑不同深度），重要切片再過 review 對抗式審查
        ok, checks_out = run_checks(nxt)
        summary = checks_out.splitlines()[-1] if checks_out else ""
        log(f"  checks：{'PASS' if ok else 'FAIL'} — {summary}")
        verify_out = checks_out
        if ok and nxt.get("review") and resolve_cmd(cfg, "review"):
            log("  重要切片 → review.py 對抗式審查…")
            ok, review_out = run_review(nxt)
            verify_out = checks_out + "\n\n[review]\n" + review_out
            summary = review_out.splitlines()[-1] if review_out else ""
            log(f"  review：{'PASS' if ok else 'FAIL'} — {summary}")

        if ok:
            commit = git("commit", "-m", nxt.get("desc", nxt["id"]))
            committed = commit.returncode == 0
            log("  commit：" + ("已存檔" if committed else "無變更可存（可能 AI 未產生改動）"))
            nxt["status"] = "done"; nxt["attempts"] = 0; nxt.pop("last_error", None)
            write_round_log(rounds, nxt, prompt, agent_out, verify_out, "PASS，已 commit")
        else:
            stat = diff_stat_summary()
            save_fail_patch(rounds, nxt["id"]); reset_worktree()
            record_fail(nxt, f"驗證沒過：{summary}",
                        "checks/review 輸出（尾段）：\n" + verify_out[-800:] + "\n\n改動範圍：\n" + stat)
            if nxt["attempts"] >= cfg["max_fails_per_slice"]:
                nxt["status"] = "blocked"
                escalate(nxt, f"連續 {nxt['attempts']} 次沒過驗證：{summary}")
                log(f"  🛑 卡住 {nxt['attempts']} 次 → 標 blocked、寫進 decisions.json，跳下一件。")
            else:
                nxt["status"] = "todo"
                log(f"  ✗ 沒過（第 {nxt['attempts']} 次），已還原改動，失敗原因會餵回下次重試。")
            write_round_log(rounds, nxt, prompt, agent_out, verify_out, f"FAIL（第 {nxt['attempts']} 次）")

        save_json(BACKLOG, backlog)
        write_report(cfg, rounds, elapsed_min(), backlog, "進行中")

        if ONCE:
            log("\n--once，停。"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
