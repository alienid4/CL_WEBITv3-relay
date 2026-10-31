"""全文搜尋的關鍵字處理與通用掃表。

## 為什麼有這支（2026-08-27 使用者的批評）

原本的 `/api/search` 是**寫死九個欄位**：
`hostname / ip / asset_serial / device_model / asset_purpose / custodian` …

使用者原話：「**全文代表 DB 我都可以查，不是我說一個案例多一個功能，
沒說就查不到**」。這個批評是對的——那不是搜尋，是「我事先想到的清單」。
每問一個新問題就要改一次程式，而沒改到的東西**完全不會出現**，
連「有這個東西但我沒搜到」都看不出來。

所以這支做兩件事：

1. **關鍵字**：空白拆字後 AND、同義詞展開（測試↔test/uat/dev）
2. **通用掃表**：掃過所有該掃的表的所有文字欄位，不是寫死清單

## 為什麼不用 FTS5（實測過才決定的）

FTS5 在這份資料上**兩種斷詞都不能用**，因為機房與環境別全是**兩個字的中文**：

    查詢        unicode61   trigram   LIKE(正確)
    板橋            24         0        1339
    測試           697         0        ~700
    板橋 oob         0       115(只中oob)  57

`unicode61` 把「01_板橋機房」當成一個 token，搜「板橋」對不上；
`trigram` 需要 ≥3 字元，兩字詞直接 0 筆。

改回 LIKE 之後實測：掃 15 張表全部文字欄位 **30~35 毫秒**。夠快，
而且沒有「索引過期但沒人發現」那種騙人的失敗模式。
"""
from __future__ import annotations

import json
import pathlib
import re
import sqlite3

#: **絕對不掃的表。這條寫死，不放設定檔。**
#:
#: 放設定檔的東西會被人改，而這幾張改錯的後果是把登入 token 或密碼雜湊
#: 送進搜尋結果——那不是「設定不當」，是外洩。
#:
#: source_record / ci_edge 是另一個理由：內容跟 hardware 高度重複（原始 payload），
#: 掃它們只會讓每一筆查詢都命中一堆重複的東西，把真正的答案淹掉。
EXCLUDE_TABLES = frozenset({
    "sessions",        # 登入 token
    "users",           # 密碼雜湊
    "app_settings",    # 含真實對照表（APID→分類、機房實名…）
    "host_api_key",    # agent 金鑰（只存 hash，但仍不該出現在搜尋結果）
    "source_record",   # 原始 payload，內容與 hardware 重複
    "ci_edge",         # 邊的 evidence 字串，重複且對人沒有意義
    "sqlite_sequence",
})

#: 這幾欄即使在允許的表裡也不掃——長文或雜湊，命中了對人沒有幫助。
EXCLUDE_COLUMNS = frozenset({
    "full_text",       # 單據全文（有專屬的搜尋入口，混進來會洗版）
    "payload",
    "key_hash",
    "password_hash",
    "token",
})

_SYN_PATH = pathlib.Path(__file__).with_name("search_synonyms.json")
_SYN: dict | None = None


def _synonyms() -> dict[str, list[str]]:
    """同義詞表。放設定檔不寫死——使用者遇到新的寫法時改 JSON 就好。

    2026-08-27 使用者拍板要做同義詞，理由是同一件事在資料裡有五種寫法：
    環境別寫「測試」，但資產名稱可能是 `DBACTEST`、`NHCRMUAT`、`rhel9.4devops`，
    主機名可能只是結尾一個 `T`。用哪個字都會漏。
    """
    global _SYN
    if _SYN is None:
        try:
            _SYN = json.loads(_SYN_PATH.read_text(encoding="utf-8")).get("groups") or {}
        except (OSError, ValueError):
            _SYN = {}
    return _SYN


def expand(term: str) -> list[str]:
    """一個關鍵字 → 要實際比對的字串清單（含同義詞）。

    大小寫不分（LIKE 在 SQLite 對 ASCII 本來就不分大小寫）。
    找不到同義詞就回自己一個——**不猜**。
    """
    t = term.strip()
    if not t:
        return []
    low = t.lower()
    for group in _synonyms().values():
        if any(low == str(x).lower() for x in group):
            # 自己排第一，讓畫面顯示時看得出來哪個是使用者打的
            rest = [x for x in group if str(x).lower() != low]
            return [t, *rest]
    return [t]


def parse_query(q: str) -> list[dict]:
    """把使用者輸入拆成多個關鍵字，各自帶同義詞。

    空白拆字後 **AND**：搜「板橋 oob ftp」要三個條件都中。
    原本整串當一個關鍵字，所以 `板橋 oob` 是 0 筆——沒有任何欄位同時包含
    這兩個字加中間的空白。
    """
    terms = [t for t in re.split(r"\s+", (q or "").strip()) if t]
    return [{"term": t, "match": expand(t)} for t in terms]


def searchable_tables(conn: sqlite3.Connection) -> list[tuple[str, list[str]]]:
    """回 [(表名, [文字欄位…])]。這是「通用」的來源——**不寫死清單**。

    型別空字串的欄位也收：SQLite 的動態型別讓很多欄位宣告時沒寫型別，
    只收 TEXT 會漏掉一半。
    """
    out: list[tuple[str, list[str]]] = []
    for (t,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ):
        if t in EXCLUDE_TABLES:
            continue
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")
                if (r[2] or "").upper() in ("TEXT", "") and r[1] not in EXCLUDE_COLUMNS]
        if cols:
            out.append((t, cols))
    return out


#: 串接欄位值用的分隔符。
#:
#: ⚠️ **不可以用空字串**。欄位值直接相黏會製造跨欄位的假命中：
#: A 欄結尾是「板」、B 欄開頭是「橋」，黏起來就被當成命中「板橋」。
#: 用 US（unit separator）——它不會出現在真實資料裡，也不會出現在
#: 任何人打得出來的關鍵字裡。寫成逸出序列而不是字元本身：
#: 原始碼裡嵌看不見的控制字元很脆弱，編輯器或複製貼上都可能吃掉它。
#: 串接欄位值用的分隔符。
#:
#: ⚠️ **不可以用空字串**。欄位值直接相黏會製造跨欄位的假命中：
#: A 欄結尾是「板」、B 欄開頭是「橋」，黏起來就被當成命中「板橋」。
#: 用 US（unit separator, 0x1f）——它不會出現在真實資料裡，也不會出現在
#: 任何人打得出來的關鍵字裡。
#:
#: 寫成 chr(31) 而不是把字元本身貼進原始碼：嵌一個看不見的控制字元很脆弱，
#: 編輯器、複製貼上、去識別化流程都可能把它吃掉，而吃掉之後**不會報錯**，
#: 只會安靜地開始出現跨欄位假命中。
SEP = chr(31)


#: 超過這個列數的表，通用掃表**不掃**。
#:
#: 2026-09-24 量出來的：這個掃描是把每張表每一列讀進 Python 比對，
#: 成本跟資料量成正比、跟關鍵字無關。90 萬列的 software 讓整支搜尋要 8.6～10 秒。
#: （順帶一提：原本以為慢的是資產那組的 6 個前置萬用字元 LIKE，實測只花 7 毫秒——
#:  差三個數量級而且方向完全錯，是量了才知道。三種「交給 SQL」的改法都更慢：
#:  instr 內嵌 36 秒、子查詢 32 秒、純 Python 微調在雜訊內。別再走那三條。）
#:
#: 正解是全文索引（FTS5），那是另一案。這個門檻是**擋住現在**用的，
#: 而且**被略過的表一定要在畫面上講出來**——見 scan() 回傳的 skipped。
#:
#: ⚠️ **這個門檻擋得到什麼、擋不到什麼，講清楚免得後人誤會：**
#:
#:   擋得到：列數很多的表（今天的 software 90 萬列就是）。
#:   **擋不到：列少但欄位多、或單欄很長的表。**
#:
#: 真正的成本是「掃過的列數 **×** 每列要串起來的文字長度」，而門檻只看列數。
#: 一張 4 萬列、每列 30 個文字欄的表不到門檻、照樣會被掃、照樣慢——
#: 而那個時候不會有人想到來看這個門檻，因為「我們不是已經加過門檻了嗎」。
#:
#: 所以：**有門檻 ≠ 搜尋不會再慢。** 下次再遇到慢，先量（見上面那串實測數字
#: 與三條走不通的路），不要假設是這裡擋住了。
BIG_TABLE_ROWS = 50_000


def scan(conn: sqlite3.Connection, parsed: list[dict],
         tables: list[tuple[str, list[str]]],
         skip_tables: frozenset[str] = frozenset(),
         per_table: int = 3,
         big_table_rows: int | None = None) -> dict:
    """**一趟掃完**：每張表只讀一次，同時算出總命中、各關鍵字單獨命中、樣本。

    ## 為什麼不是每項各一句 SQL

    第一版是「generic_hits 一輪 SQL、term_hits 再一輪」，加起來 120 句，
    實測 479 毫秒（同義詞展開後條件數是 8 倍：8 個詞 × 35 欄 = 280 個 LIKE）。

    改成把每一列的所有欄位串成一個字串、在 Python 裡比對：
    每列只做「詞數」次子字串比對，而不是「詞數 × 欄位數」次。
    跟網段那次（每段一句 COUNT → 一次算完，317 倍）是同一個手法。

    ⚠️ 這裡刻意**不做正規化／不做斷詞**——就是單純的子字串比對，
    跟 SQL 的 LIKE 語意一致。改動語意會讓「為什麼這筆會中」變得說不清楚。
    """
    # **執行時才取常數**，不要寫成預設參數。
    # `def scan(..., big_table_rows=BIG_TABLE_ROWS)` 是**定義時**綁值，
    # 之後改模組常數（測試、或未來做成可設定）完全不會生效——
    # 而症狀是「設定改了沒反應」，最難查的那一種。
    # 同一個坑 health_probe.check_batch 的 `_one=check_one` 也踩過。
    if big_table_rows is None:
        big_table_rows = BIG_TABLE_ROWS

    words_per_term = [[w.lower() for w in p["match"]] for p in parsed]
    term_names = [p["term"] for p in parsed]
    term_total = {name: 0 for name in term_names}
    groups: list[dict] = []
    total = 0

    skipped: list[dict] = []
    for t, cols in tables:
        # 太大的表先量列數再決定掃不掃。COUNT(*) 在 SQLite 上是 O(列數) 但很便宜
        # （純計數、不建字串、不進 Python），跟整張表讀進來比是兩回事。
        try:
            n_rows = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except sqlite3.Error:
            continue
        if n_rows > big_table_rows:
            # **略過要留下痕跡。** 只是不掃、什麼都不說，等於讓使用者以為
            # 「這個關鍵字在這張表裡沒有東西」——那是拿一句確定的話蓋掉不知道。
            skipped.append({"table": t, "rows": n_rows})
            continue

        quoted = ", ".join(f'"{c}"' for c in cols)
        try:
            rows = conn.execute(f'SELECT {quoted} FROM "{t}"').fetchall()
        except sqlite3.Error:
            continue          # 表結構怪掉不該讓整個搜尋掛掉
        if not rows:
            continue

        hit_rows: list[tuple] = []
        for r in rows:
            vals = [("" if v is None else str(v)) for v in r]
            blob = SEP.join(vals).lower()
            all_hit = True
            for i, words in enumerate(words_per_term):
                if any(w in blob for w in words):
                    term_total[term_names[i]] += 1
                else:
                    all_hit = False
            if all_hit:
                hit_rows.append(vals)

        if hit_rows:
            total += len(hit_rows)
            if t not in skip_tables:
                groups.append({
                    "table": t,
                    "count": len(hit_rows),
                    "samples": [_summarize(dict(zip(cols, v)), cols, parsed)
                                for v in hit_rows[:per_table]],
                })

    groups.sort(key=lambda x: -x["count"])
    skipped.sort(key=lambda x: -x["rows"])
    return {"total": total, "groups": groups, "term_hits": term_total,
            # 略過了哪些表、各幾列、門檻是多少。**門檻要給出去**——
            # 不然使用者不知道什麼時候會被略過、什麼時候不會。
            "skipped": skipped, "big_table_rows": big_table_rows}


def _summarize(row: dict, cols: list[str], parsed: list[dict]) -> dict:
    """一筆命中列的摘要：哪個欄位中的、值是什麼。

    **要講出命中在哪一欄**——搜 `esxi` 命中「主機名」跟命中「資產用途」
    是兩種不同的東西（一個是機器本身，一個是它的用途描述），
    混在一起人要逐筆點開才知道為什麼中。
    """
    words = [w.lower() for p in parsed for w in p["match"]]
    matched: list[dict] = []
    for c in cols:
        v = row.get(c)
        if v is None:
            continue
        sv = str(v)
        if any(w in sv.lower() for w in words):
            matched.append({"field": c, "value": sv[:80]})
        if len(matched) >= 3:
            break
    # 找一個像「名字」的欄位當標題，找不到就用第一個有值的
    for key in ("hostname", "asset_name", "name", "label", "person_name",
                "cidr", "raw_cidr", "file_name", "node_id", "title"):
        if row.get(key):
            title = str(row[key])
            break
    else:
        title = next((str(v) for v in row.values() if v not in (None, "")), "(空白)")
    return {"title": title[:60], "matched": matched}
