"""納管漏斗：每台機器現在走到哪一關，以及下一步該做什麼。

## 為什麼要有這頁（使用者 2026-08-16）

「測試機我有 300 台，要慢慢一台一台匯，但我至少要知道**哪些是我還需要處理的**。」

在這之前，系統回答得了「總共幾台」「有幾台已納管」，卻回答不了「**這一台走到哪、
下一步要做什麼**」。四態（未登記／未納管／已納管／失聯）只分到「連不連得進去」為止；
但連得進去之後還有好幾關——事實收了沒、服務收了沒、帳號盤點收了沒——那些關卡沒有
任何畫面，於是 300 台裡誰還缺什麼，只能靠人一台一台點進詳細頁看。

## 設計：一台機器剛好落在一關

關卡是**有序**的，每台機器落在「它還沒完成的第一關」。互斥且窮盡，所以各關加總
必定等於母體——這是對帳的基礎，也是這類看板可信的前提（數字對不起來就是有 bug，
不是「大概差不多」）。

四態的判定**直接沿用 manage_state.summarize**，不另外寫一套。理由是慘痛的：同一件事
兩處各算一次，遲早算出不同答案，然後兩個畫面互相打臉而沒人知道哪個對。這裡只做一件
事——把「已納管」那一格再往後拆成幾關。

## 失聯是終點不是關卡

登記在案卻掃不到的機器，問題不在「還沒收資料」而在「這台到底還在不在」，
下一步是去確認機器狀態，不是繼續往下收。所以它獨立成一關、排在最後。
"""
from __future__ import annotations

# 關卡定義：順序有意義（越後面越完整）。key 進 API 與畫面，label 給人看。
STAGES = [
    {
        "key": "unregistered",
        "label": "掃到但沒登記",
        "tone": "warn",
        "why": "網路上有回應，但資產清單裡沒有它",
        "next": "確認它是什麼，然後在「納入管理」建成資產",
        "action": "adopt",
    },
    {
        "key": "not_onboarded",
        "label": "已登記，進不去",
        "tone": "warn",
        "why": "是資產，但收集帳號連不進去",
        "next": "對這台執行納管（一鍵納管，或在該機貼一行指令）",
        "action": "onboard",
    },
    {
        "key": "no_facts",
        "label": "進得去，還沒收到主機事實",
        "tone": "info",
        "why": "連得上，但作業系統／序號／機型還是空的",
        "next": "等下一輪收集，或在資產詳細頁立即收一次",
        "action": "collect",
    },
    {
        "key": "no_services",
        "label": "有主機事實，還沒收服務",
        "tone": "info",
        "why": "知道它是什麼機器了，但不知道上面在跑什麼",
        "next": "到「服務盤點」對已納管主機收一輪",
        "action": "services",
    },
    {
        "key": "no_accounts",
        "label": "有服務，還沒盤點帳號",
        "tone": "info",
        "why": "服務清單有了，但帳號稽核還沒收",
        "next": "到「帳號盤點」收一輪（目前只支援 Linux）",
        "action": "accounts",
    },
    {
        "key": "complete",
        "label": "資料齊全",
        "tone": "ok",
        "why": "事實、服務、帳號都收得到",
        "next": "無需處理",
        "action": "",
    },
    {
        "key": "lost",
        "label": "失聯",
        "tone": "bad",
        "why": "登記在案，但這次掃描沒看到它",
        "next": "確認是否關機、換 IP、已下線，或防火牆擋住整段",
        "action": "check",
    },
]

STAGE_INDEX = {s["key"]: i for i, s in enumerate(STAGES)}
# 「還需要我處理的」＝除了資料齊全以外的每一關。畫面最重要的那個數字就是它。
TODO_STAGES = [s["key"] for s in STAGES if s["key"] != "complete"]


def _has_facts(row) -> bool:
    """收到主機事實了沒。判準：作業系統或硬體序號其中一個有真值。

    為什麼不要求全部欄位都有：序號/機型多半要目標主機 root 才讀得到，唯讀收集帳號
    常常拿不到（這是已知且刻意的取捨）。要求全有會讓幾乎所有機器永遠卡在這一關，
    那個數字就不再代表「收集有沒有在運作」。
    """
    for k in ("os", "hw_serial"):
        v = row[k] if k in row.keys() else None
        if v and str(v).strip() and str(v).strip().upper() != "N/A":
            return True
    return False


def _counted_serials(conn, table: str) -> set:
    """某張收集結果表裡出現過的資產序號。表還不存在時回空集合——
    舊 DB 或功能沒開的環境不該讓整頁 500。"""
    try:
        return {r[0] for r in conn.execute(
            f"SELECT DISTINCT asset_serial FROM {table} "
            "WHERE asset_serial IS NOT NULL AND asset_serial != ''")}
    except Exception:  # noqa: BLE001
        return set()


def summarize(conn) -> dict:
    """每台機器的關卡＋各關計數＋對帳。

    回傳的 items 一列一台，畫面直接拿來排序／篩選／匯出。
    """
    import manage_state

    base = manage_state.summarize(conn)

    # 資產側的補充事實：一次撈完，不要在迴圈裡逐台查（300 台會很慢）
    hw = {}
    for r in conn.execute(
            "SELECT asset_serial, hostname, ip, os, hw_serial, environment, "
            "collect_checked_at, collect_error FROM hardware"):
        hw[r["asset_serial"]] = r
    with_services = _counted_serials(conn, "host_service")
    with_accounts = _counted_serials(conn, "host_account")

    items = []
    counts = {s["key"]: 0 for s in STAGES}

    for it in base["items"]:
        state = it.get("state")
        serial = it.get("asset_serial")
        row = hw.get(serial)

        if state == manage_state.UNREGISTERED:
            key = "unregistered"
        elif state == manage_state.LOST:
            key = "lost"
        elif state == manage_state.NOT_ONBOARDED:
            key = "not_onboarded"
        elif row is None or not _has_facts(row):
            key = "no_facts"
        elif serial not in with_services:
            key = "no_services"
        elif serial not in with_accounts:
            key = "no_accounts"
        else:
            key = "complete"

        counts[key] += 1
        stage = STAGES[STAGE_INDEX[key]]
        items.append({
            "ip": it.get("ip"),
            "hostname": it.get("hostname"),
            "asset_serial": serial,
            "environment": (row["environment"] if row is not None else None),
            "os": (row["os"] if row is not None else None),
            "stage": key,
            "stage_label": stage["label"],
            "stage_index": STAGE_INDEX[key],
            "tone": stage["tone"],
            "next_action": stage["next"],
            "action": stage["action"],
            "last_check": it.get("collect_checked_at"),
            "error": it.get("collect_error"),
        })

    total = len(items)
    todo = sum(counts[k] for k in TODO_STAGES)
    return {
        "stages": STAGES,
        "counts": counts,
        "total": total,
        "todo": todo,
        "complete": counts["complete"],
        # 對帳：各關互斥且窮盡，加總必須等於母體。對不起來就是有 bug，
        # 畫面要看得到 ✗ 而不是安靜地顯示一組錯的數字。
        "reconcile": {
            "sum_of_stages": sum(counts.values()),
            "total": total,
            "ok": sum(counts.values()) == total,
        },
        "scan_time": base.get("scan_time"),
        "items": items,
    }


# ---- 診斷外掛 ----
try:
    import diagnostics

    @diagnostics.register("pipeline")
    def _diag(conn) -> dict:
        try:
            s = summarize(conn)
            return {"counts": s["counts"], "total": s["total"],
                    "reconcile_ok": s["reconcile"]["ok"]}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:200]}
except ImportError:
    pass
