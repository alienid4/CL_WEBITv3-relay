"""交付物裡不准有私有網段位址字面值。

## 為什麼要有這一道（2026-10-06）

家裡開發機那台的位址（`192.168.1.x` 網段上的一台）被寫死在程式碼裡**當 fallback**
（`deploy.sh` 的 `API_HOST="${API_HOST:-...}"`、`auto_onboard.py` 的
`os.environ.get("ASSET_COLLECTOR_IP", "...")`、`winrm_collector.enable_hint()`
的簽章預設），然後一路被打進交付給公司正式機的 app 包——含 `frontend/.output`
裡 build 出來的 JS bundle。

**既有的「無內網/公司識別字」關卡沒擋下來，而且不是它寫錯了，是它回答的是另一個問題。**
那道關卡（`.project/checks.py`）會**先套去識別化替換表再掃**，因為它要回答的是
「送出去之後還會不會剩下識別字」。那個位址在替換表裡（→ `YOUR_SERVER_IP`），
所以它永遠看不到。那個前提對 **relay／patch** 成立（兩者都會跑替換表），
但對 **app 包不成立**——`make_install_pack.py` 根本沒有 import 去識別化規則，
它是把 `APP/` 原封不動打包。關卡的前提與新增的交付管道對不上，就這樣漏了。

所以這一道刻意**不套替換表、直接掃字面值**：回答的是
「交付物裡有沒有私有網段位址」，跟「會不會外洩」是兩個不同的問題。

## 豁免方式：同行標記，不是檔案黑名單

合理的例外是有的（假資料、說明例子、範圍寫法示範）。豁免**一律是同行加標記**：

    SMTP_EXAMPLE = "10.0.0.25"   # webit3:allow-private-ip 假的示範值

刻意不做「這個檔跳過」的黑名單——那會讓整個檔案從此失去保護，
而檔案只會愈長愈大，下一個人在同一個檔裡塞真實位址就沒人擋了。
標記是逐行的，豁免範圍剛好等於看得到的那一行。

## build 產物怎麼辦（這裡是設計上最彎的一段，值得讀）

`.output` 裡的 bundle 是 minify 過的，**沒辦法帶註解標記**，而它一定會把
`.vue`／`.ts` 裡的字串原封不動搬進去。所以 bundle 不能用標記，分兩段處理：

  · **原始碼**：每個私有位址字面值都要有同行標記。（純標記機制，如上）
  · **`.output`**：允許清單**從原始碼推導**——只准出現「已經被原始碼某一行標記核可過」
    的那些位址字串。bundle 裡冒出沒人核可過的位址就紅。

這樣 bundle 沒有自己的豁免管道（不會變成第二個漏洞），而且會自我修復：
原始碼清乾淨＋重 build，bundle 自然就乾淨。開發機那個位址當初就是這樣漏的——
原始碼沒人核可它，bundle 卻有，正是這條規則要抓的形狀。

順帶一個真實好處：`.output` 比原始碼舊（改完沒重 build）時這裡會紅，
而「忘記重 build」本來就是這次要處理的問題之一。

## 不管的範圍

- `127.0.0.1`／`0.0.0.0`／`localhost`：不是 RFC1918 主機位址，CORS 預設值到處都要用，不掃。
- RFC 5737 TEST-NET（`192.0.2.x`／`198.51.100.x`／`203.0.113.x`）：本來就是文件專用網段、
  不是私有網段，不在偵測樣式內，要用直接用，不需要標記。
- 正規表示式裡的**樣式**（例如 api.py CORS 的 `192\\.168\\.\\d{1,3}\\.\\d{1,3}`）：
  帶反斜線，不是四段數字，天然不會命中。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "APP" / "asset-module"
OUTPUT = APP / "frontend" / ".output"

#: 豁免標記。要跟位址在**同一行**。
MARKER = "webit3:allow-private-ip"

#: 本專案指定的「示範／假資料」網段，這幾段不需要標記。
#:
#: 為什麼有這個而不是逐行都要標記：**這是既有的使用者裁示，不是這道關卡自己開的後門。**
#: 2026-08-21 處理公司網段外洩時，使用者在「開白名單豁免」與「說明例子一律改用示範網段」
#: 之間選了後者，理由記在 .project/desensitize_rules.py：白名單會慢慢腐爛，而且
#: 「說明例子用真網段」本來就沒必要。所以示範網段的存在本身就是那個決定的延伸。
#:
#: 這三段為什麼可信：
#:   · 10.99.x —— 說明例子專用（2026-08-21 起的慣例，所有文件範例都改用它）
#:   · 10.20.x —— 假資產示範資料（seed_demo.py 的天龍八部虛構主機、scan_sources.py 的 mock）
#:   · 10.30.x —— 同上的「測試環境」那一批
#: 真實環境的網段**都不在裡面**，所以照樣會紅：
#:   家裡開發機 192.168.1.x、公司 10.92.x／10.93.x、公司原檔的 172.16.x。
#:
#: ⚠️ 這是**內容規則**不是檔案規則：它判斷的是「這個位址值本身是假的」，
#: 跟位址出現在哪個檔案無關。所以同一條規則在原始碼與 minify 過的 bundle 裡
#: 行為完全一致——這很重要，bundle 帶不了註解標記。
DEMO_SEGMENT = re.compile(r"^10\.(?:20|30|99)\.")

#: RFC1918 私有網段的四段字面值。
#: 刻意不含 127./0.0.0.0（見模組 docstring「不管的範圍」）。
PRIVATE_IP = re.compile(
    r"\b(?:"
    r"192\.168\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")\b"
)

#: 會進 app 包的副檔名（跟 make_install_pack.py 打包的內容對齊）。
#: .mjs/.cjs 一定要在：Nuxt 的 server chunk 就是 .mjs，而那正是這次漏掉的其中一個檔。
TEXT_EXT = {
    ".py", ".sh", ".vue", ".ts", ".js", ".mjs", ".cjs",
    ".json", ".md", ".txt", ".yml", ".yaml", ".html", ".css",
}

#: 不會進 app 包的目錄（make_install_pack 的 ignore_patterns 就是這幾個）。
SKIP_DIRS = {"node_modules", ".nuxt", ".data", "__pycache__", ".git", ".venv", "venv"}


def _scan_files(root: Path, *, inside_output: bool) -> list[Path]:
    """要掃的檔案。`.output` 是 build 產物、不進版控，所以走檔案系統而不是 git ls-files。"""
    out = []
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        if any(p in SKIP_DIRS for p in f.parts):
            continue
        if f.suffix.lower() not in TEXT_EXT:
            continue
        is_out = OUTPUT in f.parents
        if is_out != inside_output:
            continue
        out.append(f)
    return out


def _hits(f: Path):
    """回 (行號, 位址, 整行) 清單。讀不到就當沒有——這裡不該因為一個怪檔讓整道關卡掛掉。"""
    try:
        txt = f.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    found = []
    for lineno, line in enumerate(txt.splitlines(), 1):
        for m in PRIVATE_IP.finditer(line):
            found.append((lineno, m.group(0), line))
    return found


def _source_approved() -> tuple[set[str], int, int]:
    """掃原始碼。回 (被標記核可的位址集合, 掃過的檔數, 命中總數)。"""
    approved: set[str] = set()
    unmarked: list[str] = []
    files = _scan_files(APP, inside_output=False)
    total = 0
    for f in files:
        for lineno, ip, line in _hits(f):
            total += 1
            if DEMO_SEGMENT.match(ip):
                approved.add(ip)
            elif MARKER in line:
                approved.add(ip)
            else:
                unmarked.append(
                    f"{f.relative_to(ROOT).as_posix()}:{lineno}  {ip}\n"
                    f"      {line.strip()[:120]}"
                )
    if unmarked:
        pytest.fail(
            f"原始碼裡有 {len(unmarked)} 處私有網段位址字面值沒有豁免標記：\n\n"
            + "\n".join(unmarked[:25])
            + (f"\n   …另外還有 {len(unmarked) - 25} 處" if len(unmarked) > 25 else "")
            + "\n\n這些位址會原封不動進到交付給正式機的 app 包。三條路選一條：\n"
            "  1. 說明例子／假資料 → **改用示範網段**（最好的做法，不留任何例外）：\n"
            "     10.99.x＝說明例子、10.20.x＝假資產資料、10.30.x＝假的測試環境。\n"
            f"  2. 真的不能改值（例如要示範某種寫法）→ 同一行加標記： # {MARKER}\n"
            "  3. 是真實環境的位址（開發機／公司主機）→ **不要加標記，把它拿掉**。\n"
            "     特別是「沒設定就退回這個位址」這種 fallback：換成另一個預設值只是把問題\n"
            "     藏起來，請改成「沒設定就明確失敗並說明怎麼設」（deploy.sh 的 API_HOST 是範本）。"
        )
    return approved, len(files), total


def test_原始碼裡的私有網段位址都有同行豁免標記():
    """原始碼（不含 .output）每個私有位址字面值都要有同行標記。

    這一道是「人有沒有看過並簽名」。紅的時候要先問的不是「怎麼讓它綠」，
    而是「這個位址是真實環境的嗎」——是的話答案是拿掉，不是加標記。
    """
    approved, n_files, n_hits = _source_approved()
    # 刻意把掃描規模印出來：「0 命中」必須能跟「根本沒掃到檔案」分辨開。
    # 只給一個綠燈不給依據，等於要人自己猜這道關卡到底有沒有在工作。
    print(f"\n原始碼掃過 {n_files} 個檔、{n_hits} 處位址命中、"
          f"{len(approved)} 個不同位址被標記核可")
    assert n_files > 100, (
        f"只掃到 {n_files} 個檔案，不合理（APP/asset-module 底下應該有幾百個）。"
        "掃描範圍或副檔名清單可能被改壞了——這種情況下的「通過」是假的。")


def test_build產物裡不准有原始碼沒核可過的私有網段位址():
    """`.output` 只准出現「原始碼某一行標記核可過」的位址。

    bundle 是 minify 過的、帶不了註解標記，所以它沒有自己的豁免管道——
    允許清單完全從原始碼推導。這同時擋掉兩件事：
      · 真實位址被 build 進 bundle（2026-10-06 就是這樣漏的）
      · 原始碼改乾淨了但忘記重 build（舊 bundle 還留著舊值）
    """
    if not OUTPUT.is_dir():
        pytest.skip(
            f"{OUTPUT.relative_to(ROOT).as_posix()} 不存在（這份 checkout 沒 build 過前端）。"
            "刻意用 skip 而不是靜默通過：綠燈必須代表「掃過且乾淨」，"
            "不能跟「根本沒東西可掃」長得一樣。")

    approved, _, _ = _source_approved()
    files = _scan_files(OUTPUT, inside_output=True)
    bad: list[str] = []
    for f in files:
        for lineno, ip, line in _hits(f):
            if DEMO_SEGMENT.match(ip) or ip in approved:
                continue
            # bundle 是一行幾十萬字的 minify 內容，整行印出來沒有意義也沒法讀，
            # 只給位址前後一小段當定位用。
            col = line.find(ip)
            around = line[max(0, col - 60):col + len(ip) + 60]
            bad.append(
                f"{f.relative_to(ROOT).as_posix()}:{lineno}  {ip}\n      …{around}…")

    print(f"\n.output 掃過 {len(files)} 個檔；原始碼核可清單 {sorted(approved)}")
    assert files, (
        f"{OUTPUT.relative_to(ROOT).as_posix()} 存在卻掃不到任何檔案——"
        "這種「通過」是假的，請檢查 TEXT_EXT／SKIP_DIRS。")
    if bad:
        pytest.fail(
            f"build 產物裡有 {len(bad)} 處原始碼沒核可過的私有網段位址：\n\n"
            + "\n".join(bad[:20])
            + (f"\n   …另外還有 {len(bad) - 20} 處" if len(bad) > 20 else "")
            + "\n\n兩種可能，先分清楚是哪一種：\n"
            "  1. 原始碼已經清乾淨，但 .output 是舊的 → **重 build 前端**：\n"
            "     cd APP/asset-module/frontend && npm run build\n"
            "  2. 原始碼還有沒清掉的位址 → 先讓上面那個\n"
            "     test_原始碼裡的私有網段位址都有同行豁免標記 綠，再重 build。\n"
            "（注意：bundle 沒有豁免標記這條路。要豁免請在原始碼那一行加標記後重 build。）"
        )


def test_偵測樣式本身是對的():
    """關卡自己的樣式要能驗證——不然壞了也沒人知道，只會一直綠。

    這一道回答的是「上面兩道的『綠』可不可信」。偵測樣式寫壞（例如少一段、
    被改成永不命中）時，上面兩道會安靜地一直通過，那比沒有這道關卡更糟：
    它會讓人**以為**有在擋。
    """
    # 該抓的：RFC1918 三段網段。
    # ⚠️ 這裡刻意**不寫任何真實環境的位址**（開發機／公司主機）當樣本——
    # 這支測試自己也在交付範圍內，寫進來就等於在「防止真實位址進交付物」的檔案裡
    # 放一個真實位址。（2026-10-06 實際被 .project/checks.py 的去識別化關卡擋下過一次，
    # 那道關卡是對的。）要證明的是「這幾個網段會被抓」，用不存在的位址一樣證明得了。
    for ip in ("192.168.77.88", "10.11.12.13", "10.0.0.25",
               "172.16.5.6", "172.31.255.254"):
        assert PRIVATE_IP.search(ip), f"漏抓私有位址 {ip}"

    # 不該抓的：loopback／未指定／公網／RFC5737 TEST-NET
    for ip in ("127.0.0.1", "0.0.0.0", "8.8.8.8", "172.15.0.1", "172.32.0.1",
               "192.0.2.5", "198.51.100.5", "203.0.113.5"):
        assert not PRIVATE_IP.search(ip), f"誤抓了不該抓的 {ip}"

    # 正規表示式樣式（帶反斜線）不該命中——api.py 的 CORS regex 就長這樣
    assert not PRIVATE_IP.search(r"192\.168\.\d{1,3}\.\d{1,3}")
    assert not PRIVATE_IP.search(r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}")

    # 同行標記要真的認得
    assert MARKER in f'X = "10.0.0.25"   # {MARKER} 假值'

    # 示範網段要認得
    for ip in ("10.99.1.5", "10.20.30.41", "10.30.13.31"):
        assert DEMO_SEGMENT.match(ip), f"示範網段 {ip} 應該免標記"

    # ⚠️ 示範網段是唯一的免標記管道，它要是誤放了真實網段，上面兩道的綠燈就沒意義了。
    #
    # 這裡**窮舉** 10.x 的第二段 0~255，確定只有 20／30／99 這三個被放過。
    # 為什麼用窮舉而不是列幾個真實網段當反例：
    #   1. 列真實網段 = 在這個檔案裡寫進真實位址，正是這道關卡要防的事
    #      （而且會被 checks.py 的去識別化關卡擋下來，實際發生過）。
    #   2. 窮舉比列舉**強**：列舉只證明「我想到的那幾個不會過」，
    #      窮舉證明「除了那三段，10.x 底下沒有任何第二段會過」——
    #      公司網段不管是哪一段、以後換成哪一段，都涵蓋在內。
    allowed_second = {20, 30, 99}
    for second in range(256):
        ip = f"10.{second}.1.1"
        if second in allowed_second:
            assert DEMO_SEGMENT.match(ip), f"示範網段 {ip} 應該免標記"
        else:
            assert not DEMO_SEGMENT.match(ip), (
                f"示範網段規則放過了 10.{second}.x —— 那不是示範網段。"
                "真實網段一旦被放過，整道關卡就形同虛設。")

    # 192.168.x 與 172.16-31.x 完全不在示範網段內（不管哪一段都要紅）
    for ip in ("192.168.0.1", "192.168.255.254", "172.16.0.1", "172.31.255.254"):
        assert not DEMO_SEGMENT.match(ip), f"示範網段規則誤放了 {ip}"
