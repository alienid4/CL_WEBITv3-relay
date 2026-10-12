"""`fcb_aixV###.ksh` 的版本資訊不可以漂掉。

## 為什麼需要這道關卡

2026-09-24 使用者問「你檔名有版控嗎」，查下去發現：
**版本戳是有的（`SCRIPTV`，而且會印進輸出表頭的「腳本版本:」），但它是過期的。**

它宣稱 `SV.AIX.202609210900`（09-21 09:00），
可是 09-23 那五條判定邏輯的修正（A1 showmount／B1 nodev／A2／A3／A4）
之後沒有人動過它。

**過期的版本戳比沒有版本戳更糟**：
沒有戳，我們知道自己不知道；有一個錯的戳，我們會**以為自己知道**——
拿到第一份真機輸出時會把「修正後的結果」歸給「修正前的腳本」，
然後往完全錯的方向查。這就是通則第一條：不准拿一句確定的話去蓋一個不知道。

## 2026-09-30：同一支腳本現在有三個地方各自記版本

使用者要檔名帶版號（`fcb_aixV001.ksh`），而且「每次寫版號」＝內容一改就遞增。
於是變成：

1. **檔名**的 `V###`
2. 腳本裡的 **`SCRIPTV`**（`SV.AIX.YYYYMMDDHHMM`）
3. 報告抬頭印出來的版本戳（來源就是 2）

三個一漂掉，使用者看到的就是互相矛盾的版本資訊——**而他要版號正是為了
搞清楚手上是哪一版**。所以三個都要有守門，不能只靠「記得改」。

## 這道關卡怎麼運作

| 測試 | 擋什麼 | 靠人記得嗎 |
|---|---|---|
| `test_改了腳本就要改版號` | 內容變了但什麼都沒更新 | 否（指紋對不上） |
| `test_版號要跟得上內容_不可以停在過去` | 更新了指紋但 `SCRIPTV` 停在過去 | 半 — 日期下限要手動抬 |
| `test_檔名流水號要跟測試裡記的那一版一致` | 只改了檔名或只改了測試 | 否 |
| `test_內容改了流水號就要遞增` | **更新了指紋卻沿用同一個檔名** | **否——拿 HEAD 的內容比** |

最後那條是唯一完全不用靠人記得的：它把工作目錄的內容跟 HEAD 上那一版比，
不一樣就要求流水號必須大於 HEAD 那支。

**改了腳本要做三件事**：檔名 `V###` +1、`SCRIPTV` 換成現在時間、
`BODY_SHA256` 換成新值（測試失敗訊息會把新值印給你）。
三個都要動，才逼得出「想一下版號」這個動作。
"""
import hashlib
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "APP" / "asset-module" / "scripts"

#: 檔名樣式。**不寫死檔名**——檔名本身帶流水號，寫死等於每次遞增都要回來改這裡，
#: 而「要記得回來改」正是這道關卡想消滅的東西。
NAME_RE = re.compile(r"^fcb_aixV(\d{3})\.ksh$")

#: 檔名上的流水號。跟下面的 BODY_SHA256 是**一組**的：
#: 這個雜湊，對應的就是 V{SERIAL} 這一版的內容。
SERIAL = 6

#: 腳本本體（不含 SCRIPTV 那一行）的 sha256。改腳本就要一起更新這個值。
BODY_SHA256 = "a04c8b8458be0ad0b3430560eb031d983fb02bacd488002eaa4dbd5e752d0596"


def _find_script():
    """找出 scripts/ 底下唯一那支 fcb_aixV###.ksh，回（路徑, 流水號）。

    多於一支就是改名時舊的沒刪掉——那會讓公司主機上新舊並存、
    有人跑到舊的拿到過期結果還不知道，所以直接擋下來。
    """
    found = sorted(
        (int(m.group(1)), q)
        for q in SCRIPTS.iterdir()
        for m in [NAME_RE.match(q.name)] if m
    )
    assert found, (
        "{} 底下找不到 fcb_aixV###.ksh。".format(SCRIPTS) +
        "檔名規則是 fcb_aixV001.ksh、fcb_aixV002.ksh……（三位數流水號）。")
    assert len(found) == 1, (
        "scripts/ 底下有不只一支 fcb_aixV###.ksh：" +
        str([q.name for _, q in found]) +
        "。改名之後舊的那支要刪掉，不然公司主機上會新舊並存，"
        "有人跑到舊的會拿到過期結果而且不知道。")
    serial, path = found[0]
    return path, serial


SH, SERIAL_IN_NAME = _find_script()


def _text() -> str:
    # ⚠️ 不要用 Path.read_text(newline=...)：那是 Python 3.13 才有的參數。
    # 本機 py -3 是 3.14 過得了，但**專案 .venv 與 221 都是 3.11**，
    # 會炸 TypeError。用 open() 寫，所有版本都支援。
    with SH.open(encoding="utf-8", newline="") as f:
        return f.read()


def _body(text: str) -> str:
    return "\n".join(l for l in text.split("\n") if not l.startswith("SCRIPTV="))


def test_有版本戳而且格式固定():
    m = re.search(r'^SCRIPTV="(SV\.AIX\.\d{12})"$', _text(), re.M)
    assert m, "找不到 SCRIPTV，或格式不是 SV.AIX.YYYYMMDDHHMM"


def test_版本戳會印進輸出表頭():
    """印不出去的版本戳等於沒有——我們看的是那份結果檔，不是腳本本身。"""
    assert '腳本版本: $SCRIPTV' in _text()


def test_改了腳本就要改版號():
    """⚠️ 這條紅燈的意思是：你改了腳本內容，但版號沒動。

    照著做：
      1. 檔名的 V### +1（git mv）
      2. 把 SCRIPTV 換成今天的（`SV.AIX.YYYYMMDDHHMM`）
      3. 把這個檔的 SERIAL 與 BODY_SHA256 換成下面訊息印出來的值
    """
    got = hashlib.sha256(_body(_text()).encode("utf-8")).hexdigest()
    # ⚠️ 反斜線不可以出現在 f-string 的運算式裡——那是 Python 3.12 才放寬的。
    # 2026-09-24 踩過：本機 3.14 過得了，**專案 venv 與 221 都是 3.11**，
    # 在收集階段就 SyntaxError，而且訊息長得像這個檔壞掉，查半天。
    # 先算好再插值，兩邊都過。
    _m = re.search(r'SCRIPTV="([^"]+)"', _text())
    cur_v = _m.group(1) if _m else "（找不到 SCRIPTV）"
    assert got == BODY_SHA256, (
        "{} 的內容變了，但版號可能沒跟著改。\n".format(SH.name) +
        "  目前 SCRIPTV：{}\n".format(cur_v) +
        "  新的 BODY_SHA256：{}\n".format(got) +
        "確認版號已更新之後，把上面那個值貼進 BODY_SHA256。")


def test_版號要跟得上內容_不可以停在過去():
    """版號的日期不可以早於這次改動。

    這條擋的是「改了腳本、也更新了指紋，但版號忘了動」——
    只靠指紋擋不住那種情況（指紋會被一起更新），所以再加一道時間檢查。
    """
    m = re.search(r'SCRIPTV="SV\.AIX\.(\d{8})\d{4}"', _text())
    assert m, "版號格式不對"
    assert m.group(1) >= "20261001", (
        "版號日期是 {}，早於最近一次改動——".format(m.group(1)) +
        "那表示戳過期了。過期的戳比沒有戳更糟：它會讓人以為自己知道是哪一版。")


# ---------------------------------------------------------------------------
# 檔名流水號的守門（2026-09-30 加）
# ---------------------------------------------------------------------------

def test_檔名流水號要跟測試裡記的那一版一致():
    """擋的是「改了檔名但沒更新測試」或反過來。

    照著做：把這個檔的 SERIAL 改成檔名上的數字。
    """
    assert SERIAL_IN_NAME == SERIAL, (
        "檔名是 {}（流水號 {}），".format(SH.name, SERIAL_IN_NAME) +
        "但這個測試記的是 SERIAL = {}。\n".format(SERIAL) +
        "兩邊要一致——不一致就代表有人只改了一邊，"
        "而版本資訊只要有一個是錯的，整組就不能信。")


def _git(*args):
    """跑一個唯讀的 git 指令；跑不動就回 None（不讓測試因為環境而紅）。"""
    try:
        out = subprocess.run(("git",) + args, cwd=str(ROOT),
                             capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", "replace")


def test_內容改了流水號就要遞增():
    """⚠️ 這條紅燈的意思是：你改了腳本內容，但檔名的 V### 沒有跟著 +1。

    **這是唯一一條不用靠人記得的檢查**：拿工作目錄的內容去跟 HEAD 上那一版比。
      · 內容跟 HEAD 一樣   -> 沒改東西，不要求遞增
      · 內容跟 HEAD 不一樣 -> 這是還沒 commit 的改動，流水號必須大於 HEAD 那支

    上面 BODY_SHA256 擋的是「改了內容什麼都沒更新」；
    但只更新雜湊、卻沿用同一個檔名的話它是綠的——那個洞由這一條補。

    拿不到 git 資訊時 skip（shallow clone、或打包出去的原始碼樹）。
    **skip 不等於通過**，那時就只剩「記得改」這一層。
    """
    listing = _git("ls-tree", "--name-only", "HEAD",
                   "APP/asset-module/scripts/")
    if listing is None:
        pytest.skip("這裡拿不到 git 資訊（非 git 樹或 git 不可用），跳過自動比對")

    head_names = [Path(x).name for x in listing.split("\n") if x.strip()]
    head = [(int(m.group(1)), n)
            for n in head_names for m in [NAME_RE.match(n)] if m]
    if not head:
        pytest.skip("HEAD 上還沒有 fcb_aixV###.ksh（這次就是第一版），跳過")

    head_serial, head_name = max(head)
    blob = _git("show", "HEAD:APP/asset-module/scripts/" + head_name)
    if blob is None:
        pytest.skip("讀不到 HEAD 上的 {}，跳過".format(head_name))

    if blob == _text():
        return          # 內容跟 HEAD 一樣，沒有東西要遞增

    nxt = head_serial + 1
    assert SERIAL_IN_NAME > head_serial, (
        "腳本內容跟 HEAD 上的 {} 不一樣，".format(head_name) +
        "但檔名流水號還是 V{:03d}。\n".format(SERIAL_IN_NAME) +
        "使用者要的是「看檔名就知道是哪一版」，內容改了卻沿用同一個檔名，"
        "他手上那份 V{:03d} 就會有兩種內容——那比沒有版號更糟。\n".format(SERIAL_IN_NAME) +
        "照著做（三件事一起）：\n" +
        "  1. git mv APP/asset-module/scripts/{} ".format(SH.name) +
        "APP/asset-module/scripts/fcb_aixV{:03d}.ksh\n".format(nxt) +
        "  2. 把這個檔的 SERIAL 改成 {}\n".format(nxt) +
        "  3. SCRIPTV 換成現在時間、BODY_SHA256 換成新值")


# ---------------------------------------------------------------------------
# 「查核／修正／生效」三欄的覆蓋率守門（2026-09-30 加）
#
# 使用者拿到真機報告後回報「有些未確認是什麼狀態」——0018~0098 那一區整段空白，
# 報告後半段對他等於沒用。補完之後要有東西釘住，不然下次有人改檢查、
# 指令留在原地，就是 09-24「版本戳停在過去」那件事的翻版。
#
# 怎麼釘：
#   · 走共用函式的（ck_*）—— 斷言每個函式自己都有 set_fix。函式知道自己在讀
#     什麼參數，指令從那些參數長出來，結構上就不會跟判定走岔。
#   · 一次性 emit 的 —— 斷言 set_fix **就貼在那一條的檢查區塊上方**。
#     物理相鄰是這裡的防漂機制：改檢查的人不可能看不到指令。
#     （刻意不做成一張「ID -> 指令」的中央表：那張表離檢查很遠，
#       改了檢查不會碰到表，正是「各走各路」的標準長法。）
# ---------------------------------------------------------------------------

#: 一次性 emit、而且已經補上指令的條目。值是該條檢查區塊的起始標記。
_ONEOFF_ANCHORS = {
    "0018": "_std18=", "0019": '_std19="NFS', "0020": "_std20='NFS",
    "0021": "_std21=", "0022": "_std22=",
    "0051": "_std51=", "0052": '_std52="/etc/hosts.equiv',
    "0054": '_std54="OpenSSH 版本', "0055": '_std55="/etc/shosts.equiv',
    "0056": '_std56="各使用者家目錄的 .shosts', "0060": "_std60=",
    "0067": '_std67="sendmail 服務狀態', "0068": '_std68="登入警語 herald',
    "0070": '_std70="/audit 與 /etc/security/audit',
    "0085": '_std85="/etc/motd 登入警示語',
    "0087": '_std87="帳號密碼雜湊', "0088": '_std88="群組 GID 唯一性',
    "0089": '_std89="/etc/security/passwd 的 NOCHECK 旗標',
    "0098": '_std98="密碼雜湊演算法',
}


def test_每個共用檢查函式都要自己帶指令():
    """ck_* 函式是「檢查」與「指令」不會走岔的結構保證——每個都要有 set_fix。

    新增一個 ck_* 卻沒有 set_fix，就是又多出一批空白的三欄。
    """
    lines = _text().split("\n")
    starts = [(i, l.split("(")[0]) for i, l in enumerate(lines)
              if re.match(r"^ck_[a-z_]+\(\) *\{", l)]
    assert len(starts) >= 10, "ck_* 函式數量變少了？現在只找到 %d 個" % len(starts)
    missing = []
    for n, (i, name) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        body = "\n".join(lines[i:end])
        if "set_fix" not in body:
            missing.append(name)
    assert not missing, (
        "這些共用檢查函式沒有 set_fix，走它們的條目三欄會是空白：" + str(missing) +
        "\n在函式裡用它自己的參數組出查核／修正指令，不要另外寫一張表。")


def test_一次性檢查的指令要貼在檢查區塊旁邊():
    """物理相鄰＝改檢查的人一定看得到指令。

    這條紅了代表某一條的 set_fix 被搬走或刪掉了。
    """
    text = _text()
    lines = text.split("\n")
    far = []
    for fid, anchor in sorted(_ONEOFF_ANCHORS.items()):
        idx = [i for i, l in enumerate(lines) if l.startswith(anchor)]
        assert idx, "找不到 FCB-AIX-%s 的檢查區塊起點：%s" % (fid, anchor)
        i = idx[0]
        window = "\n".join(lines[max(0, i - 14):i])
        if "set_fix" not in window and "_f89c=" not in window:
            far.append(fid)
    assert not far, (
        "這些條目的檢查區塊上方 14 行內找不到 set_fix：" + str(far) +
        "\n指令要貼在它檢查的那段程式碼旁邊——離太遠就會變成改了檢查、"
        "指令留在原地，那正是這道守門要擋的事。")


# ---------------------------------------------------------------------------
# 報告字串的用字守門（2026-10-01 加）
#
# 真機報告 FCB-AIX-0053 的「標準設定值」長這樣：
#   ◆◆註：與項次 2（dt）查同一個 inittab ident，結果必然一致；本表依客戶檢核表列為兩項
# 三個問題：開頭的 ◆◆ 是表意空格 U+3000 被打壞、內容是開發者的實作註記、
# 而且出現「客戶」兩個字——**而讀者就是那個「客戶」**（老闆與稽核）。
# ---------------------------------------------------------------------------

#: 會被輸出路徑打壞、看不見、或純裝飾的字元。
#:
#: 2026-10-01 這道守門第一版是**壞的**：它只掃「含 `_std=` / `emit "` / `set_fix `
#: 這幾個樣式的那一行」，於是
#:   - 用反斜線續行、字串落在**下一行**的呼叫（ck_sec、ck_sshd… 的標準描述）
#:   - 抬頭區塊的 `printf '<dt>…</dt>'`
#: 全部掃不到。結果是：我回報「U+3000 只剩 1 處」，實際上全檔有 20 處。
#: 「測試綠了」不等於「測試有在測」——所以改成掃**全部字串字面值**，
#: 不再猜哪幾行才算「會進報告」。
_BANNED_CHARS = {
    "　": "表意空格 U+3000（真機上被打壞成菱形亂碼）",
    " ": "不斷行空格 NBSP U+00A0",
    "​": "零寬空格 U+200B", "‌": "零寬不連字 U+200C",
    "‍": "零寬連字 U+200D", "⁠": "零寬不斷行 U+2060",
    "﻿": "位元組順序記號 U+FEFF", "­": "軟連字號 U+00AD",
    "️": "表情變體選擇子 U+FE0F（跟在符號後面，肉眼看不見）",
    "⚠": "警告號（裝飾符號，要強調請寫「注意：」）",
    "◆": "菱形", "✗": "打叉", "✓": "打勾",
    "★": "星號", "●": "實心圓", "■": "實心方",
    "→": "箭頭", "※": "米字號",
}

#: 開發者視角的用詞。報告是給老闆與稽核看的，不是給寫腳本的人看的。
_DEV_WORDS = ("客戶", "本表", "項次", "我們")


def _literals_in(line):
    """把一行 shell 拆出所有字串字面值（單引號與雙引號都算）。

    - 引號外面的 `#` 之後視為註解，整段丟掉（註解裡愛寫什麼都行）
    - 引號內的 `#` 不算註解
    - 雙引號內的反斜線跳脫要跳過
    收不掉的（續行到下一行的字串）就把已經讀到的那一段當作字面值回傳，
    寧可多掃一點，也不要像舊版那樣整段漏掉。
    """
    out = []
    i, n = 0, len(line)
    quote = None
    buf = ""
    while i < n:
        c = line[i]
        if quote is None:
            if c == "#" and (i == 0 or line[i - 1] in " \t;"):
                break
            if c in "\"'":
                quote, buf = c, ""
            elif c == "\\":
                i += 1
        else:
            if c == "\\" and quote == '"':
                i += 1
                if i < n:
                    buf += line[i]
            elif c == quote:
                out.append(buf)
                quote = None
            else:
                buf += c
        i += 1
    if quote is not None:
        out.append(buf)
    return out


def _report_string_lines():
    """(行號, 字串字面值)——腳本裡**每一個**字串字面值，整行註解除外。

    刻意不再篩「哪些字串會進報告」：篩選漏掉的那些，正是上次漏網的那些。
    """
    out = []
    for i, l in enumerate(_text().split("\n")):
        if l.lstrip().startswith("#"):
            continue
        for s in _literals_in(l):
            out.append((i + 1, s))
    return out


def test_報告字串不可以有裝飾符號或怪空白():
    bad = []
    for ln, s in _report_string_lines():
        for ch, why in _BANNED_CHARS.items():
            if ch in s:
                bad.append("第 %d 行有 %s：%s" % (ln, why, s.strip()[:60]))
    assert not bad, (
        "這些字串用了會被打壞、看不見、或純裝飾的字元：\n  " + "\n  ".join(bad) +
        "\n程式碼註解裡愛用什麼都行，但字串只用中文字與一般標點。")


def test_這道守門自己要抓得到():
    """守門壞掉時**自己要紅**——上一次就是它靜靜地放行了 20 個 U+3000。

    直接餵幾種真實寫法給拆解器，確認每一種都被抓到：
    單行、續行到下一行、printf 的單引號格式字串。
    """
    samples = [
        'emit "$1" "Compliant" "$CAT" "名稱" "有　表意空格" "值"',
        '       "續行的標準描述裡有　表意空格" "週"',
        "printf '<dt>執行時間</dt><dd>%s　~　%s</dd>",
        'echo "警告⚠️ 有裝飾符號"',
        'set_fix "a" "b" "有 NBSP" "可執行"',
    ]
    for src in samples:
        hits = [ch for lit in _literals_in(src)
                for ch in _BANNED_CHARS if ch in lit]
        assert hits, "拆解器沒抓到這一行的壞字元，守門等於失效：" + src
    # 反向：正常的中文字串不可以被誤判
    ok = 'emit "$1" "Compliant" "$CAT" "密碼最長使用期限（maxage）" "標準值" "目前值"'
    hits = [ch for lit in _literals_in(ok)
            for ch in _BANNED_CHARS if ch in lit]
    assert not hits, "正常中文被誤判成壞字元：" + str(hits)
    # 註解裡的裝飾符號不該被擋（那不會進輸出）
    cm = '    # 注意 ⚠️ 這是註解'
    assert not _literals_in(cm.split("#")[0]), "註解行不該被當成字串"


def test_報告字串不可以用開發者視角的用詞():
    """擋「客戶」「本表」「項次」這類話——讀者就是那個「客戶」。"""
    bad = []
    for ln, l in _report_string_lines():
        for w in _DEV_WORDS:
            if w in l:
                bad.append("第 %d 行出現「%s」：%s" % (ln, w, l.strip()[:60]))
    assert not bad, (
        "這些會進報告的字串用了開發者視角的說法：\n  " + "\n  ".join(bad) +
        "\n報告是給老闆與稽核看的。實作理由與排表理由請寫在程式碼註解裡。")


# ---------------------------------------------------------------------------
# printf 的格式字串與參數個數必須相等（2026-10-01 加）
#
# ⚠️ 我在這支腳本上**連續三次**踩到同一個坑，而且三次 `bash -n` 都是綠的：
#   1. 用行索引改 printf，格式字串裡被插進一個真的換行
#   2. 改 tail 區塊時留下一行舊參數
#   3. 改 awk 那段時又留下兩行舊參數（17 個 %s 對 16 個參數）
#      -> 整份 HTML 每一列都噴 awk syntax error，實跑才看得見
#
# 結論：`bash -n` 抓不到的東西，就是該用測試釘住的東西。
# 這條測試把「格式字串的 %s 個數 == 參數個數」變成自動檢查，
# 不是靠「我下次會記得」。
# ---------------------------------------------------------------------------

def _printf_blocks():
    """抓出腳本裡每一段 printf「格式字串 + 續行參數」。

    只認多行（用反斜線續行）的那種——單行 printf 一眼就看得出對不對，
    會出事的一直是被拆成好幾行、改的時候只改到一半的那些。
    """
    lines = _text().split("\n")
    out = []
    for i, l in enumerate(lines):
        if "printf " not in l or not l.rstrip().endswith("\\"):
            continue
        fmt = l
        args, j = [], i + 1
        while j < len(lines):
            args.append(lines[j])
            if not lines[j].rstrip().endswith("\\"):
                break
            j += 1
        out.append((i + 1, fmt, "\n".join(args)))
    return out


def test_printf的格式與參數個數要一致():
    bad = []
    for ln, fmt, args in _printf_blocks():
        n_fmt = fmt.count("%s")
        # 參數以逗號分隔；最後一個參數後面接的是重導向，不算
        n_arg = len([x for x in args.split(",") if x.strip()])
        if n_fmt != n_arg:
            bad.append("第 %d 行：格式字串有 %d 個 %%s，參數卻有 %d 個\n      %s"
                       % (ln, n_fmt, n_arg, fmt.strip()[:90]))
    assert not bad, (
        "printf 的格式字串與參數對不起來：\n  " + "\n  ".join(bad) +
        "\n這種錯 bash -n 抓不到，實跑才會整批噴錯。"
        "改多行 printf 的時候整段重寫，不要用行索引只改其中幾行。")


def test_格式字串裡不可以有真的換行():
    """擋第 1 種：格式字串被插進真的換行，整個 printf 就散掉了。"""
    bad = []
    for ln, fmt, _ in _printf_blocks():
        # 續行的 printf，格式字串那一行一定要自己成對地收掉引號
        if fmt.count("'") % 2 != 0 and fmt.count('"') % 2 != 0:
            bad.append("第 %d 行：%s" % (ln, fmt.strip()[:90]))
    assert not bad, (
        "這些 printf 的格式字串引號沒有成對收掉，八成是被插進了真的換行：\n  "
        + "\n  ".join(bad))
