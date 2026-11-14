"""EOS 指定收件人規則（2.059.17，使用者 11-10「要有系統比較好分配」）。

規則種類與優先序：主機（轉派產生）＞業務系統（APID）＞設備類別（os_class 大類）＞系統推算（原本的擁有者→部門主管）。
- 一條規則：{id, kind, key, track(hw|os), people[{employee_id, name}], label, by, at, note}；（kind, key, track）唯一
- 存在 app_settings `eos_assign_rules`（JSON list）。**跟 EOS 通知設定（eos_notify）分開存**：設定頁整包 PUT
  不會把規則洗掉，規則改動也不會動到設定。
- 規則指定的人 AD 查不到 → 那筆進「沒人收」，不改寄部門主管（指定了卻默默退回去，等於規則失效沒人發現）。
- 窗口可多人：names 每人都收同一筆。
- 純比對與存取，不寄信。改動寫 activity_log（由 router 呼叫 log）。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime

SETTING_KEY = "eos_assign_rules"
KIND_HOST = "host"
KIND_SYSTEM = "system"
KIND_DEVCLASS = "devclass"
KINDS = (KIND_HOST, KIND_SYSTEM, KIND_DEVCLASS)
KIND_LABEL = {KIND_HOST: "主機", KIND_SYSTEM: "業務系統", KIND_DEVCLASS: "設備類別"}
TRACKS = ("hw", "os")
MAX_NAMES = 10


class RulesCorrupt(Exception):
    """規則讀不出來（內容壞掉，不是沒設定）。不吞：吞了＝全部規則靜默失效、信寄回原本的人。"""


def load_rules(conn: sqlite3.Connection) -> list[dict]:
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    except sqlite3.OperationalError:
        return []          # 沒有 app_settings 表＝全新空庫、還沒有任何規則（不是壞掉）；內容壞掉由下面 RulesCorrupt 報出來
    if not row:
        return []
    raw = row["value"] if hasattr(row, "keys") else row[0]
    try:
        rules = json.loads(raw) or []
    except (ValueError, TypeError) as exc:
        raise RulesCorrupt(f"EOS 指定收件人規則讀不出來（內容壞掉）：{type(exc).__name__}: {exc}") from exc
    if not isinstance(rules, list):
        raise RulesCorrupt("EOS 指定收件人規則格式不對（應為清單）")
    return [r for r in rules if isinstance(r, dict) and r.get("kind") in KINDS]


def _save(conn: sqlite3.Connection, rules: list[dict]) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SETTING_KEY, json.dumps(rules, ensure_ascii=False)))


def clean_names(raw) -> list[str]:
    import eos_notify
    vals = raw if isinstance(raw, list) else [raw]
    out: list[str] = []
    for v in vals:
        for n in eos_notify.split_owners(str(v or "")):
            if n and n not in out:
                out.append(n)
    return out


def resolve_people(conn: sqlite3.Connection, names) -> list[dict]:
    """收件人名字 → AD 的人（**存員編**，GPT 審 #1：存名字會在同名／改名時寄錯）。

    每個名字都要在 AD 唯一對到、在職、有信箱，否則整條規則不存（ValueError 講是哪個名字、為什麼）。
    """
    import vuln_notify as vn
    ns = clean_names(names)
    if not ns:
        raise ValueError("至少要填一位收件人")
    if len(ns) > MAX_NAMES:
        raise ValueError(f"一條規則最多 {MAX_NAMES} 位收件人")
    people, bad = [], []
    for n in ns:
        p, why = vn.resolve_recipient(conn, n)
        if p is None:
            bad.append(f"「{n}」{why}")
        elif p["employee_id"] not in {x["employee_id"] for x in people}:
            people.append({"employee_id": p["employee_id"], "name": p.get("display_name") or n})
    if bad:
        raise ValueError("這些收件人不能用：" + "；".join(bad))
    return people


def validate(kind: str, key: str, track: str) -> tuple[str, str, str]:
    kind = (kind or "").strip()
    if kind not in KINDS:
        raise ValueError(f"規則種類只能是 {'、'.join(KINDS)}")
    key = (key or "").strip()
    if not key:
        raise ValueError("要指定對象（主機／APID／設備類別）")
    if kind == KIND_DEVCLASS:
        import os_class
        if key not in os_class.class_order():
            raise ValueError(f"設備類別「{key}」不在清單裡：{'、'.join(os_class.class_order())}")
    track = (track or "").strip()
    if track not in TRACKS:
        raise ValueError("軌別只能是 hw（硬體）或 os（作業系統）")
    return kind, key, track


def upsert(conn: sqlite3.Connection, kind: str, key: str, track: str, names, label: str | None = None,
           note: str | None = None, by: str | None = None, commit: bool = True) -> dict:
    """（種類、對象、軌別）唯一（GPT 審 #5）：再設一次＝取代那條。回傳那條規則。

    讀改寫在同一個 IMMEDIATE 交易裡（GPT 審 #2：兩個人同時改不會互相蓋掉）。
    """
    kind, key, track = validate(kind, key, track)
    people = resolve_people(conn, names)
    # 審查 P2：不論 commit 與否，只要還沒開交易就先 BEGIN IMMEDIATE 再讀——router 傳 commit=False，
    # 以前讀規則時沒鎖，兩個人同時存，後存的會用舊清單蓋掉前一個人的規則
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    rules = load_rules(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hit = next((r for r in rules if (r["kind"], r["key"], r.get("track")) == (kind, key, track)), None)
    if hit is None:
        hit = {"id": uuid.uuid4().hex[:12], "kind": kind, "key": key, "track": track}
        rules.append(hit)
    hit.update({"people": people, "label": (label or hit.get("label") or key).strip(),
                "note": (note or "").strip() or hit.get("note") or "", "by": by or "", "at": now})
    _save(conn, rules)
    if commit:
        conn.commit()
    return dict(hit)


def delete(conn: sqlite3.Connection, rule_id: str, commit: bool = True) -> dict | None:
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    rules = load_rules(conn)
    hit = next((r for r in rules if r.get("id") == rule_id), None)
    if hit is None:
        if commit:
            conn.commit()
        return None
    _save(conn, [r for r in rules if r.get("id") != rule_id])
    if commit:
        conn.commit()
    return hit


class Matcher:
    """一次載入規則，逐筆比對。cand 需帶 track、mk、api_ids、devclass。

    優先序：主機 ＞ 業務系統 ＞ 設備類別。**取最高的那一級**；同一級命中多條（一台掛多個業務系統）＝那一級全部收件人聯集
    （GPT 審 #4：明訂，不是「第一條」）。
    """

    def __init__(self, rules: list[dict]):
        self.idx: dict[tuple, list[dict]] = {}
        for r in rules:
            self.idx.setdefault((r["kind"], r["key"], r.get("track")), []).append(r)

    def match(self, cand: dict) -> tuple[list[dict], list[dict]] | None:
        """回 (people[{employee_id,name}] 聯集, 命中的規則們)；沒命中回 None。"""
        tr = cand["track"]
        levels = (
            self.idx.get((KIND_HOST, cand.get("mk") or "", tr), []),
            [r for a in (cand.get("api_ids") or []) for r in self.idx.get((KIND_SYSTEM, a, tr), [])],
            self.idx.get((KIND_DEVCLASS, cand.get("devclass") or "", tr), []),
        )
        for lv in levels:
            if lv:
                people: list[dict] = []
                for r in lv:
                    for p in r.get("people") or []:
                        if p["employee_id"] not in {x["employee_id"] for x in people}:
                            people.append(p)
                return people, lv
        return None


def current_person(conn: sqlite3.Connection, employee_id: str) -> tuple[dict | None, str | None]:
    """寄送／預覽當下用員編重查（GPT 審 #8：存規則時有效不代表寄的時候還有效）。回 (人, 不能寄的原因)。"""
    r = conn.execute("SELECT * FROM ad_person WHERE employee_id = ?", (employee_id,)).fetchone()
    if r is None:
        return None, "指定的收件人已不在 AD 名單"
    p = dict(r)
    if p.get("enabled") == 0:
        return None, "指定的收件人 AD 帳號已停用"
    if not (p.get("mail") or "").strip():
        return None, "指定的收件人 AD 沒有信箱"
    return p, None


def rule_text(hits: list[dict]) -> str:
    return "；".join(f"{KIND_LABEL[r['kind']]}規則「{r.get('label') or r['key']}」" for r in hits)
