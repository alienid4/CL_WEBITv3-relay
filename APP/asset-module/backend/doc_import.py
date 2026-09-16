"""既有 Word 單據的歸檔與索引（主機及網路異動需求單／伺服器上線前檢查表）。

定位講在前面：**這是檔案室的索引卡，不是資料來源。**
抽的是識別欄位（單據編號、日期、主機名、IP、申請人）＋勾選欄位（主機型態／機房／
環境／服務類型…）。抽出來的東西**一律不直接覆蓋資產欄位**，只在畫面上跟現況並列給人比對。

關於勾選框：原本刻意完全不解讀，理由是「抓歪不會報錯＝安靜把錯資料寫進盤點清單」。
2026-08-15 使用者指出「□WIN ☑LINUX，有☑的代表選 LINUX」後實測，那個假設是錯的——
勾選狀態在三種存法下都讀得到（見 extract_checkboxes），6 份樣本比對截圖 100% 正確。
所以改成「抽、但標明來源與信心度，不自動套用」。
自由填寫的規格欄（CPU 核數／記憶體／硬碟／OS 版本）本來也不抽，理由同上。
使用者 2026-08-15 要求「每個都要抽，我才會知道他當初申請多少」並提出加人工審核關卡——
那個關卡正好解掉「抓歪不會報錯」這個反對理由（人看過就會報錯），所以改成：
**抽，但每份都要人確認過（review_status=confirmed）才算數**。

2026-08-15 用 6 份真實樣本（3 個 .doc + 3 個 .docx）實測的命中率：
單據編號 6/6、填表日期 6/6、主機名 6/6、IP 6/6。

**三方交叉驗證**是這支能自動綁定資產的關鍵：公司的主機命名把 IP 後兩段編進去
（SECSVR195-059 ↔ 10.99.195.59），所以「檔名的 IP」「內文的 IP」「主機名編碼的 IP」
三個獨立來源可以互相對帳。三方一致才自動綁，不一致的丟給人看——
不是憑單一來源猜，這比一律人工確認既省力又更安全（實測 6/6 一致）。

.doc（Word 97-2003 二進位）不需要 LibreOffice 也不需要 Word COM：
用 UTF-16LE 與 cp950 兩種解碼各撈一次，識別欄位就找得到了。正式機不用為了讀舊檔
長期養一個轉檔套件（金融業裝額外套件要報備，能省則省）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

ALLOWED_EXT = (".doc", ".docx")

# 底線也是 word char，所以不能用 \b——"_10.99.194.111" 會匹配失敗（實測踩到）
IP_RE = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])")
TAG_RE = re.compile(r"<[^>]+>")
# 主機命名：SECSVR195-059、SECSVR194-017T（尾碼字母代表測試機之類，保留）
HOST_RE = re.compile(r"(SEC[A-Z]*\d{3}-\d{3}[A-Z]?)")
HOST_PARTS_RE = re.compile(r"SEC[A-Z]*(\d{3})-(\d{3})")
# 單據編號有三種寫法：E800011504075 / 2607060008 / INF-20260616-31。
# 尾巴的 -31 一定要吃進來——這是兩種單互相對應的 join key，截掉就串不起來
# （2026-08-15 實測：少了 (?:-\d{1,3})? 會抽成 INF-20260616）。
DOC_NO_RE = re.compile(r"([A-Z]{0,4}-?\d{8,14}(?:-\d{1,3})?)")
DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Word 的符號字元（<w:sym>）。勾選框常用 Wingdings／Wingdings 2 的這幾個碼位，
# 實測樣本用的是 Wingdings 2 的 F052（✓）。沒列到的符號一律當空白，不要亂猜成勾選。
_SYM_RE = re.compile(r'<w:sym[^>]*w:char="([0-9A-Fa-f]{2,4})"[^>]*/>')
_SYM_CHECKED = {"F052", "F0FE", "F0FC", "F0FD", "F0FB", "F0A3"}
_SYM_UNCHECKED = {"F0A8", "F06F", "F071", "F0A1"}


def _sym_to_char(m: re.Match) -> str:
    code = m.group(1).upper()
    if code in _SYM_CHECKED:
        return "☑"
    if code in _SYM_UNCHECKED:
        return "☐"
    return " "

# 值取到哪裡為止。表單是「※標籤 值 ※標籤 值」串成一行，不切會把下一欄一起吃進來。
# **不能把換行當終止符**：Word 表格裡標籤與值是不同儲存格＝不同段落，中間一定有換行，
# 拿換行當終止會讓每個欄位都抽成空的（2026-08-15 實測 6/6 全空，就是踩到這個）。
_STOPS = ("※", "一、", "二、", "三、", "檢核項目")


def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        # 打勾符號常常不是文字而是 <w:sym w:font="Wingdings 2" w:char="F052"/>，
        # 直接去標籤的話這個記號會整個消失，只剩選項文字，就看不出哪個被勾了。
        # 先把它還原成 ☑／☐ 再去標籤，後面的判定就跟「直接打 ☑」同一套邏輯。
        xml = _SYM_RE.sub(_sym_to_char, xml)
        # 段落換行留著，欄位才不會全部黏成一行
        text = TAG_RE.sub("", re.sub(r"</w:p>", "\n", xml))
    else:
        raw = path.read_bytes()
        text = raw.decode("utf-16-le", "ignore") + "\n" + raw.decode("cp950", "ignore")
    # 舊 .doc 用 \x07 當表格儲存格分隔，會夾在字中間（"2026\x07年\x0705\x07月"），
    # 而 \x07 不算 \s，日期正規表達式會整個對不上。控制字元一律換成空白再處理。
    return CTRL_RE.sub(" ", text)


# 全文檢索用的可讀字元。舊 .doc 雙解碼會夾帶大量二進位亂碼，不濾掉的話一份檔要存
# 200KB 垃圾，而且搜尋會被亂碼誤命中。
_KEEP_RE = re.compile(r"[^一-鿿　-〿A-Za-z0-9./:_@\-()%\s＝=～~、，。：；（）]")


# 帳密遮罩。實測使用者提供的 6 份樣本裡，3 份的上線檢查表寫著
# 「帳密：arcsight / sys@8864」——把全文丟進資料庫做檢索，等於讓任何登入者都搜得到
# 正式主機的密碼。標籤（密碼設定／密碼原則）不遮，只遮「標籤 + 分隔符 + 值」的那個值。
_SECRET_RE = re.compile(
    r"(帳密|密碼|通行碼|口令|password|passwd|pwd)\s*[:：=]\s*"
    r"([^\s，。；、]{1,40}(?:\s*/\s*[^\s，。；、]{1,40})?)",
    re.IGNORECASE,
)
_MASK = "［已遮罩］"


def mask_secrets(text: str) -> tuple[str, bool]:
    """遮掉單據裡的帳號密碼，回傳 (遮罩後文字, 有沒有遮到東西)。

    **原始 Word 檔不動**——那是稽核證據，不能改。遮的是進資料庫做全文檢索的那份副本，
    因為檢索會讓密碼變成「任何登入者打三個字就找得到」。要看原文請下載原檔，
    下載會留稽核紀錄（doc_download_audit）。
    """
    found = False

    def repl(m):
        nonlocal found
        found = True
        return f"{m.group(1)}：{_MASK}"

    return _SECRET_RE.sub(repl, text), found


def searchable_text(raw: str, cap: int = 200_000) -> str:
    """把抽出來的原文整理成可以全文搜的字串。

    使用者的真實需求是「以前要找資料得把所有 Word 一份份打開」，所以光有單號索引不夠，
    內容也要搜得到。但不能原封不動存：舊 .doc 為了相容用了兩種編碼各解一次，
    產出的字串一半是亂碼，存進去既佔空間又會讓搜尋命中垃圾。

    處理：只留中英數與常見標點 → 壓空白 → 去掉重複出現的詞（雙解碼會讓每個詞出現兩次）。
    實測 6 份樣本：234KB → 63KB、173KB → 38KB，.docx 幾乎不變。
    """
    cleaned = re.sub(r"\s+", " ", _KEEP_RE.sub(" ", raw)).strip()
    cleaned, _ = mask_secrets(cleaned)
    out: list[str] = []
    seen: set[str] = set()
    for tok in cleaned.split(" "):
        if len(tok) > 1:
            if tok in seen:
                continue
            seen.add(tok)
        out.append(tok)
    return " ".join(out)[:cap]


def snippet(text: str, keyword: str, width: int = 60) -> str | None:
    """命中片段。搜尋結果只給檔名，使用者還是得一份份開來看——那等於沒解決問題。"""
    if not text or not keyword:
        return None
    i = text.lower().find(keyword.lower())
    if i < 0:
        return None
    start = max(0, i - width // 2)
    return ("…" if start else "") + text[start: start + width] + "…"


def _after(text: str, label: str, maxlen: int = 40) -> str | None:
    """標籤後面那個值。標籤在 Word 裡常被拆成多個 run，但我們比對的是去標籤後的純文字，
    所以照樣找得到。"""
    i = text.find(label)
    if i < 0:
        return None
    # 先把換行壓成空白再切：值可能在下一個段落（表格的下一格），順序反過來就全抽成空的
    seg = re.sub(r"\s+", " ", text[i + len(label): i + len(label) + maxlen * 2])
    seg = seg.lstrip("：: 　\t")
    cut = len(seg)
    for s in _STOPS:
        j = seg.find(s)
        if 0 <= j < cut:
            cut = j
    return seg[:cut].strip()[:maxlen] or None


# ===== 勾選欄位 =====
# 2026-08-15 使用者指出「□WIN ☑LINUX，有☑的代表選 LINUX」後補的。原本刻意不解讀勾選框，
# 但實測推翻了那個假設——三種存法都讀得到：
#   ① .docx 直接打的 ☑ 字元
#   ② .docx 用 Wingdings 字型的勾記號（純文字抽出來時記號會消失，只剩選項文字）
#   ③ 舊 .doc 的勾記號解碼成半形左括號 '('
# ③ 是最容易誤判的：'(' 在文件裡本來就到處都是（(西元)、(主機組填寫)）。所以**只認
# checkbox_fields.json 裡列出的選項詞**，不做通用的「找勾記號」——限定詞彙才不會把
# 一般括號當成勾選。
CHECKBOX_PATH = Path(__file__).parent / "checkbox_fields.json"
_CHECKED_MARKS = "☑☒✓✔("
_UNCHECKED_MARKS = "☐□"


def load_checkbox_fields() -> list[dict]:
    return json.loads(CHECKBOX_PATH.read_text(encoding="utf-8"))["fields"]


def extract_checkboxes(text: str) -> dict:
    """回傳 {欄位key: {"label":.., "selected":[..], "confidence":..}}。

    判定方式：對每個已知選項詞，看它前面緊鄰的是勾記號還是空框。
    兩者都沒有就當「這份表沒有這個欄位」，不猜。

    confidence：
      high   單選欄位剛好選中一個
      review 單選卻選了多個、或一個都沒選（表沒填完，或抽取沒認出來）——要人看
    """
    out: dict = {}
    flat = re.sub(r"\s+", "", text)
    for field in load_checkbox_fields():
        # **只在欄位標籤附近找**。整份文件找的話誤命中很嚴重：「DMZ」「新增」「測試」
        # 在檢查表裡到處都是，會把根本沒有這個欄位的表也判出一個值來
        # （2026-08-15 第一版就是這樣，上線檢查表被判出「申請項目→新增」）。
        seg = None
        for anchor in field.get("anchors", [field["label"]]):
            i = flat.find(re.sub(r"\s+", "", anchor))
            if i >= 0:
                seg = flat[i: i + field.get("window", 60)]
                break
        if seg is None:
            continue  # 這份表沒有這個欄位，不要硬生出一筆空的

        selected, seen_any = [], False
        for opt in field["options"]:
            # seg 已經壓掉空白，選項也要跟著壓——不然「System Level」這種帶空白的
            # 選項永遠對不上（2026-08-15 欄位盤點時發現「參數設定」整欄抽不到就是這個）
            for m in re.finditer(re.escape(re.sub(r"\s+", "", opt)), seg):
                prev = seg[m.start() - 1] if m.start() else ""
                if prev in _CHECKED_MARKS:
                    seen_any = True
                    if opt not in selected:
                        selected.append(opt)
                    break
                if prev in _UNCHECKED_MARKS:
                    seen_any = True
        if not seen_any:
            continue  # 標籤在但一個框都沒有＝抓錯位置，寧可不給值
        conf = "high"
        if field.get("single") and len(selected) != 1:
            conf = "review"
        out[field["key"]] = {
            "label": field["label"], "selected": selected, "confidence": conf,
            "asset_field": field.get("asset_field"),
            "asset_value": (field.get("value_map") or {}).get(selected[0])
            if (field.get("asset_field") and len(selected) == 1) else
            (selected[0] if field.get("asset_field") and len(selected) == 1 else None),
        }
    return out


def load_value_fields() -> list[dict]:
    return json.loads(CHECKBOX_PATH.read_text(encoding="utf-8")).get("value_fields", [])


# 填空欄位的終止字元：下一個標籤（※）、下一個框、換行
_VALUE_STOPS = "※☐☑(（\n\r"
# 終止詞：表單是一整條「A欄 值 B欄 值」，只靠終止字元切不乾淨，值會流進下一欄
# （實測 CPU 抽到「16 記憶體」、C 槽抽到「200GD:硬碟容量」）。碰到下一個欄位標籤就停。
_VALUE_STOP_WORDS = (
    "記憶體", "硬碟容量", "其他磁碟", "參數設定", "參數內容", "Windows", "Linux",
    "RHEL", "作業系統", "主機名稱", "主機IP", "內容規格", "C:", "D:", "使用需求",
)


def extract_values(text: str) -> dict:
    """自由填寫的規格欄（CPU Core／記憶體／硬碟容量／OS 版本）。

    2026-08-15 使用者要求：「每個都要抽，我才會知道他當初 CPU 跟記憶體申請是多少」。
    這些欄位長成「☐4 ☐8 ☑其他___16___」——被勾的是「其他」時，值在後面那條底線裡。

    **抽出來一律標成待審核**。我原本主張不抽這類欄位，理由是抓歪不會報錯；
    使用者提出加人工審核關卡，那個理由就不成立了——人看過就會報錯。
    實測就有一份的 CPU Core 抽到「512 GB」（明顯是填表或排版出錯），
    正是要靠人看才抓得到的那種。
    """
    out: dict = {}
    flat = re.sub(r"[ \t　]+", "", text)
    for field in load_value_fields():
        seg, anchor_checked = None, False
        for anchor in field["anchors"]:
            i = flat.find(anchor)
            if i >= 0:
                # ※ 是這份表的欄位分隔符。搜尋範圍不先切掉下一欄，window 一寬就會跨過去
                # 抓到別人的值（實測：OS 版本欄抓到下一欄的 CPU「512GB」）。
                seg = flat[i + len(anchor): i + len(anchor) + field.get("window", 40)].split("※")[0]
                # 有些欄位的勾記號在標籤**前面**（☑其他版本：___RedHat 9.6___），
                # 這時 seg 裡沒有勾記號，值就直接跟在標籤後面
                anchor_checked = i > 0 and flat[i - 1] in _CHECKED_MARKS
                break
        if seg is None:
            continue

        value, source = None, None
        if anchor_checked:
            cut = len(seg)
            for ch in _VALUE_STOPS:
                j = seg.find(ch)
                if 0 <= j < cut:
                    cut = j
            v = re.sub(r"_+", " ", seg[:cut]).strip("_＿ :：").strip()
            if v:
                out[field["key"]] = {"label": field["label"], "value": v[:40],
                                     "source": "其他填空"}
                continue
        # 被勾起來的那一個：可能是現成選項（☑8），也可能是「☑其他___16___」
        for m in re.finditer(f"[{_CHECKED_MARKS}]", seg):
            rest = seg[m.end():]
            if rest.startswith("其他"):
                rest = rest[2:]
                source = "其他填空"
            else:
                source = "選項"
            cut = len(rest)
            for ch in _VALUE_STOPS:
                j = rest.find(ch)
                if 0 <= j < cut:
                    cut = j
            for w in _VALUE_STOP_WORDS:
                j = rest.find(w)
                if 0 <= j < cut:
                    cut = j
            # 底線是空格的畫法，不是值的一部分；前後全形冒號同理
            v = rest[:cut].strip("_＿ :：\n").strip()
            v = re.sub(r"_+", " ", v).strip()
            if v:
                value = v[:40]
                break
        if value is None:
            continue
        out[field["key"]] = {"label": field["label"], "value": value, "source": source}
    return out


def _doc_type(name: str, text: str) -> str:
    if "上線前檢查" in name or "上線前檢查" in text[:4000]:
        return "golive_form"
    return "provision_form"


def _parse_date(s: str | None) -> str | None:
    if not s:
        return None
    m = DATE_RE.search(s)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d}"


def extract_fields(file_name: str, text: str) -> dict:
    """抽識別欄位並做三方交叉驗證。回傳的 warnings 是「要人看一下」的矛盾，不是錯誤。"""
    stem = Path(file_name).stem
    doc_type = _doc_type(stem, text)

    fn_ip = (IP_RE.findall(stem) or [None])[0]
    doc_ip = (IP_RE.findall(text) or [None])[0]
    host = (HOST_RE.findall(text) or [None])[0]

    host_tail = None
    m = HOST_PARTS_RE.search(text)
    if m:
        host_tail = f"{int(m.group(1))}.{int(m.group(2))}"

    # 檢查表上的「伺服器申請單據表單編號」是回指申請單的號碼，不是它自己的單號。
    # 這是兩種單能自動串起來的 join key，抽錯就串不起來，所以先找長標籤再找短的。
    # 標籤本身有兩種寫法（「伺服器申請單單據編號」「伺服器申請單據編號」），逐字比對會漏，
    # 所以改看「單據編號」前面 15 字裡有沒有「伺服器申請」——是的話這個號碼是回指申請單的。
    ref_no = None
    own_no = None
    idx = text.find("單據編號")
    if idx >= 0:
        raw = _after(text, "單據編號", 30)
        num = DOC_NO_RE.search(raw).group(1) if raw and DOC_NO_RE.search(raw) else None
        prefix = re.sub(r"\s+", "", text[max(0, idx - 15): idx])
        if "伺服器申請" in prefix:
            ref_no = num
        else:
            own_no = num

    # 檔名裡的日期流水（20260729-01）不是單據編號，但單號抽不到時它是唯一的識別碼
    fn_serial = None
    fm = re.search(r"((?:[A-Z]{2,4}-)?\d{8}-\d{2})", stem)
    if fm:
        fn_serial = fm.group(1)

    form_date = _parse_date(_after(text, "填表日期", 40))

    warnings: list[str] = []
    if fn_ip and doc_ip and fn_ip != doc_ip:
        warnings.append(f"檔名的 IP（{fn_ip}）與內文第一個 IP（{doc_ip}）不一樣")
    if fn_ip and host_tail and not fn_ip.endswith(host_tail):
        warnings.append(f"主機名編碼（…{host_tail}）與 IP（{fn_ip}）對不起來")
    if form_date and fn_serial:
        fy = re.search(r"(\d{4})\d{4}", fn_serial)
        if fy and fy.group(1) != form_date[:4]:
            warnings.append(
                f"檔名年份 {fy.group(1)} 與內文填表日期 {form_date} 不一致，"
                f"請確認哪個才對（不替你決定）"
            )

    return {
        "doc_type": doc_type,
        "request_no": own_no,
        "ref_request_no": ref_no,
        "file_serial": fn_serial,
        "form_date": form_date,
        "applicant_unit": _after(text, "申請單位", 30),
        "applicant": _after(text, "申請人員", 20),
        "unit_manager": _after(text, "單位主管", 20),
        "system_name": _after(text, "系統名稱", 60),
        # 使用需求＝這台機器要拿來做什麼，是單子上唯一講「用途」的自由文字欄，
        # 之後要對資產清單的 asset_purpose 就靠它（2026-08-15 欄位盤點時發現漏抽）
        "usage_desc": _after(text, "使用需求", 60),
        "hostname": host,
        "ip": fn_ip or doc_ip,
        "ip_in_filename": fn_ip,
        "ip_in_content": doc_ip,
        "hostname_tail": host_tail,
        "all_ips": list(dict.fromkeys(IP_RE.findall(text)))[:20],
        "warnings": warnings,
        "triple_match": bool(fn_ip and doc_ip and host_tail
                             and fn_ip == doc_ip and fn_ip.endswith(host_tail)),
    }


def _bind(conn: sqlite3.Connection, f: dict) -> tuple[str | None, str, list[str]]:
    """決定綁哪台資產、信心度多高。回傳 (asset_serial, confidence, 額外警告)。"""
    warn: list[str] = []
    row = None
    if f["ip"]:
        row = conn.execute(
            "SELECT asset_serial FROM hardware WHERE TRIM(ip) = ? LIMIT 1", (f["ip"],)
        ).fetchone()
    if row is None and f["hostname"]:
        # 主機名也會跟著 IP 一起回收（SECSVR195-059 這種命名本來就綁 IP），
        # 所以主機名對上**不代表是同一台**。只當成候選並標明要人確認，不自動採信。
        cand = conn.execute(
            "SELECT asset_serial, ip FROM hardware WHERE UPPER(TRIM(hostname)) = ? LIMIT 1",
            (f["hostname"].upper(),),
        ).fetchone()
        if cand is not None:
            row = cand
            if (cand["ip"] or "").strip() != (f["ip"] or "").strip():
                warn.append(
                    f"IP 對不到資產；主機名對上 {cand['asset_serial']}，但那台的 IP 是 "
                    f"{cand['ip'] or '空白'}、單據寫 {f['ip']}——主機名會隨 IP 回收再利用，"
                    f"請確認是不是同一台")
            else:
                warn.append("IP 對不到資產，改用主機名對上的，請確認是同一台")

    if row is None:
        warn.append("盤點清單裡找不到這台（IP 與主機名都對不到），需要人工指定")
        return None, "review", warn
    if f["triple_match"] and not f["warnings"] and not warn:
        return row["asset_serial"], "auto", warn
    return row["asset_serial"], "review", warn


# 下線單：表單第十四項「主機下線作業確認」有勾新增/異動/刪除時，代表這是一張下線單。
# 最新一張是下線單 ⇒ 機器已退役，清單還標「使用中」就是過期。
_DECOMM_RE = re.compile(r"主機下線作業確認[^※]{0,80}")


def _is_decommission(text: str) -> bool:
    flat = re.sub(r"\s+", "", text)
    m = _DECOMM_RE.search(flat)
    if not m:
        return False
    seg = m.group(0)
    # 「不適用」被勾＝這張單不是下線單；有勾異動/刪除才是
    for opt in ("異動", "刪除", "下線"):
        i = seg.find(opt)
        if i > 0 and seg[i - 1] in _CHECKED_MARKS:
            return True
    return False


def find_duplicate(conn: sqlite3.Connection, request_no: str | None,
                   ref_no: str | None, file_name: str) -> sqlite3.Row | None:
    """同一份單改個檔名再上傳，不該變成兩筆（我原本只用檔名當唯一鍵）。
    以單據編號為準；沒有單號的才退回用檔名比對。"""
    no = (request_no or ref_no or "").strip()
    if no:
        return conn.execute(
            "SELECT * FROM doc_archive WHERE COALESCE(request_no, ref_request_no) = ? "
            "AND file_name != ? LIMIT 1", (no, file_name),
        ).fetchone()
    return None


def import_document(
    conn: sqlite3.Connection, src: Path, store_dir: Path, who: str,
    original_name: str | None = None, refresh: bool = True,
) -> dict:
    """歸檔一份單據：抽索引 → 綁資產 → 原檔落地。回傳這份的處理結果。

    `refresh=False`：跳過 `refresh_current_flags()`，由呼叫端在整批做完後跑一次。
    那支要掃全表才能算出「每個 IP 現行的是哪一張」，每份檔都跑一次是 O(N²)——
    一次匯入 500 份就是 500 趟全表掃描加 500 次 commit。**中途跳過不影響正確性**
    （它是從整張表重算，不是累加），但整批做完一定要補跑，否則 is_current 會停在
    匯入前的狀態，比對頁會拿舊單去對現在的資產。
    """
    name = original_name or src.name
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"只接受 .doc／.docx，收到 {ext or '沒有副檔名'}")

    text = extract_text(src)
    f = extract_fields(name, text)
    _, has_secrets = mask_secrets(re.sub(r"\s+", " ", text))
    is_decomm = _is_decommission(text)
    if not f["form_date"]:
        f["warnings"].append(
            "抽不到填表日期——同一個 IP 有多張單時排不出新舊，無法判斷哪張是現行的，"
            "請人工確認日期")
    asset_serial, confidence, extra = _bind(conn, f)
    warnings = f["warnings"] + extra
    if is_decomm:
        warnings.append("這是一張下線單——若它是最新的一張，資產狀態不該還是「使用中」")
    dup = find_duplicate(conn, f["request_no"], f["ref_request_no"], name)
    if dup is not None:
        warnings.append(
            f"單據編號與既有的《{dup['file_name']}》相同，可能是同一份單的不同檔名，請確認")

    # 已經人工審核過的單，重匯時**不可以**把人修正的值沖掉——那等於審核白做，
    # 而且沒有任何提示（抽取邏輯改版後重跑全部檔案時就會發生）。
    prev = conn.execute(
        "SELECT values_json, review_status, reviewed_by, reviewed_at FROM doc_archive "
        "WHERE file_name = ?", (name,)
    ).fetchone()
    keep_reviewed = prev is not None and prev["review_status"] == "confirmed"

    store_dir.mkdir(parents=True, exist_ok=True)
    dest = store_dir / name
    if dest.exists():
        # 同名檔重傳：覆蓋原檔並更新索引，不要堆一堆 (1)(2)——檔案室裡同名兩份更難查
        dest.unlink()
    dest.write_bytes(src.read_bytes())

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO doc_archive "
        "(doc_type, file_name, file_path, file_ext, request_no, ref_request_no, form_date, "
        " applicant_unit, applicant, unit_manager, system_name, hostname, ip, asset_serial, "
        " bind_confidence, warnings, extracted, full_text, checkboxes, values_json, "
        " sections_json, checklist_json, review_status, has_secrets, is_decommission, "
        " imported_by, imported_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(file_name) DO UPDATE SET "
        " doc_type=excluded.doc_type, file_path=excluded.file_path, "
        " request_no=excluded.request_no, ref_request_no=excluded.ref_request_no, "
        " form_date=excluded.form_date, applicant_unit=excluded.applicant_unit, "
        " applicant=excluded.applicant, unit_manager=excluded.unit_manager, "
        " system_name=excluded.system_name, hostname=excluded.hostname, ip=excluded.ip, "
        " asset_serial=excluded.asset_serial, bind_confidence=excluded.bind_confidence, "
        " warnings=excluded.warnings, extracted=excluded.extracted, "
        " full_text=excluded.full_text, checkboxes=excluded.checkboxes, "
        " values_json=excluded.values_json, sections_json=excluded.sections_json, "
        " checklist_json=excluded.checklist_json, has_secrets=excluded.has_secrets, "
        " is_decommission=excluded.is_decommission, "
        " imported_by=excluded.imported_by, imported_at=excluded.imported_at",
        (
            f["doc_type"], name, str(dest), ext, f["request_no"], f["ref_request_no"],
            f["form_date"], f["applicant_unit"], f["applicant"], f["unit_manager"],
            f["system_name"], f["hostname"], f["ip"], asset_serial, confidence,
            json.dumps(warnings, ensure_ascii=False),
            json.dumps(f, ensure_ascii=False), searchable_text(text),
            json.dumps(extract_checkboxes(text), ensure_ascii=False),
            prev["values_json"] if keep_reviewed
            else json.dumps(extract_values(text), ensure_ascii=False),
            json.dumps(extract_sections(text), ensure_ascii=False),
            json.dumps(extract_checklist(text), ensure_ascii=False),
            "confirmed" if keep_reviewed else "pending",
            1 if has_secrets else 0, 1 if is_decomm else 0, who, now,
        ),
    )
    conn.commit()
    if refresh:
        refresh_current_flags(conn)
    return {
        "file_name": name, "doc_type": f["doc_type"], "request_no": f["request_no"],
        "ref_request_no": f["ref_request_no"], "hostname": f["hostname"], "ip": f["ip"],
        "asset_serial": asset_serial, "bind_confidence": confidence, "warnings": warnings,
        "has_secrets": has_secrets, "is_decommission": is_decomm,
    }


def refresh_current_flags(conn: sqlite3.Connection) -> int:
    """標出每個 IP「目前有效」的那張單。

    使用者 2026-08-15 提的關鍵情境：IP 會被回收再分配——10.99.0.1 三年前有人申請、
    兩年前釋放、上個月又發給別台。那張三年前的單描述的是**當時佔用這個 IP 的另一台機器**，
    拿它跟現在的資產比對，只會得到一整頁假不一致，而且會誤導人去「修正」正確的資料。

    排序（新→舊）：填表日期 → 檔名裡的流水（同一天可能有 -01 -02 兩張）→ 匯入順序。
    **沒有日期的單不會被當成最新**：排在最後，並且該 IP 若只有它一張才視為現行——
    猜錯方向的代價是拿舊資料覆蓋新資料，寧可保守。

    同一個 IP 的舊單保留不刪（歷史要查得到），只是 is_current=0、不參與比對。
    """
    rows = conn.execute(
        "SELECT id, ip, form_date, file_name, doc_type FROM doc_archive "
        "WHERE ip IS NOT NULL AND TRIM(ip) != ''"
    ).fetchall()
    by_ip: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_ip.setdefault(r["ip"].strip(), []).append(r)

    changed = 0
    for ip, group in by_ip.items():
        def sort_key(r):
            serial = ""
            m = re.search(r"((?:[A-Z]{2,4}-)?\d{8}-\d{2})", Path(r["file_name"]).stem)
            if m:
                serial = m.group(1)
            # 沒有日期的排最後（用空字串，字串比較下最小）
            return (r["form_date"] or "", serial, r["id"])

        # 兩種單各自算現行：一台機器同時有「最新的異動需求單」與「最新的上線檢查表」，
        # 兩張都該是現行的，不該互相蓋掉
        for dtype in {r["doc_type"] for r in group}:
            same = sorted([r for r in group if r["doc_type"] == dtype], key=sort_key)
            for i, r in enumerate(same):
                want = 1 if i == len(same) - 1 else 0
                changed += conn.execute(
                    "UPDATE doc_archive SET is_current = ? WHERE id = ? AND "
                    "COALESCE(is_current, 1) != ?", (want, r["id"], want)
                ).rowcount
    conn.commit()
    return changed


def list_documents(
    conn: sqlite3.Connection, confidence: str | None = None, q: str | None = None
) -> list[dict]:
    """單據清單。q 會**連內文一起搜**——使用者以前要找資料得把每份 Word 打開，
    只搜檔名和單號解決不了那個問題。命中時附上片段，不用開檔就看得出是不是要的那份。"""
    sql = (
        "SELECT d.*, h.hostname AS asset_hostname, h.asset_status "
        "FROM doc_archive d LEFT JOIN hardware h ON h.asset_serial = d.asset_serial"
    )
    where: list[str] = []
    params: list = []
    if confidence:
        where.append("d.bind_confidence = ?")
        params.append(confidence)
    kw = (q or "").strip()
    if kw:
        like = f"%{kw}%"
        where.append(
            "(d.full_text LIKE ? OR d.file_name LIKE ? OR d.request_no LIKE ? "
            " OR d.ref_request_no LIKE ? OR d.hostname LIKE ? OR d.ip LIKE ? "
            " OR d.applicant LIKE ? OR d.applicant_unit LIKE ? OR d.system_name LIKE ?)"
        )
        params.extend([like] * 9)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY d.form_date DESC, d.file_name"
    out = []
    for r in conn.execute(sql, params):
        d = dict(r)
        d["warnings"] = json.loads(d["warnings"] or "[]")
        d["snippet"] = snippet(d.get("full_text") or "", kw) if kw else None
        d["checkboxes"] = json.loads(d.get("checkboxes") or "{}")
        d["values"] = json.loads(d.get("values_json") or "{}")
        d["sections"] = json.loads(d.get("sections_json") or "{}")
        chk = json.loads(d.get("checklist_json") or "[]")
        # 清單頁只給統計，整包 43 列開詳細再看
        d["checklist_summary"] = {
            "total": len(chk),
            "done": sum(1 for x in chk if x["verdict"] == "完成"),
            "na": sum(1 for x in chk if x["verdict"] == "不需"),
            "blank": sum(1 for x in chk if x["verdict"] == "未填"),
        } if chk else None
        for k in ("values_json", "sections_json", "checklist_json"):
            d.pop(k, None)
        # 清單不回整包全文與抽取結果（一份可能好幾十 KB，幾百份會把回應撐爆）
        d.pop("extracted", None)
        d.pop("full_text", None)
        out.append(d)
    return out


def documents_of_asset(conn: sqlite3.Connection, asset_serial: str) -> list[dict]:
    """這台資產有哪幾張單。資產詳細頁要能一眼看到單據史。

    除了直接綁在這台的，也把「檢查表回指的申請單號」串進來——兩種單靠
    ref_request_no ↔ request_no 對上，這樣點進申請單就看得到它對應的上線檢查表。
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM doc_archive WHERE asset_serial = ? "
        "ORDER BY COALESCE(is_current,1) DESC, form_date DESC, doc_type",
        (asset_serial,),
    )]
    for d in rows:
        d["warnings"] = json.loads(d["warnings"] or "[]")
        d["checkboxes"] = json.loads(d.get("checkboxes") or "{}")
        d["values"] = json.loads(d.get("values_json") or "{}")
        d["sections"] = json.loads(d.get("sections_json") or "{}")
        d["checklist"] = json.loads(d.get("checklist_json") or "[]")
        for k in ("values_json", "sections_json", "checklist_json"):
            d.pop(k, None)
        d.pop("extracted", None)
        d.pop("full_text", None)
        d["linked_docs"] = [
            dict(x) for x in conn.execute(
                "SELECT id, file_name, doc_type, form_date FROM doc_archive "
                "WHERE (ref_request_no IS NOT NULL AND ref_request_no = ?) "
                "   OR (request_no IS NOT NULL AND request_no = ?) ",
                (d["request_no"], d["ref_request_no"]),
            ) if x["id"] != d["id"]
        ]
    return rows


def bind_document(conn: sqlite3.Connection, doc_id: int, asset_serial: str | None) -> None:
    """人工指定（或解除）綁定。人工結果標成 manual，跟自動判定分得開——
    之後要檢討自動綁的準確率時，不能把人改過的算進自動的成績。"""
    if asset_serial:
        if conn.execute(
            "SELECT 1 FROM hardware WHERE asset_serial = ?", (asset_serial,)
        ).fetchone() is None:
            raise ValueError(f"找不到資產 {asset_serial}")
    conn.execute(
        "UPDATE doc_archive SET asset_serial = ?, bind_confidence = ? WHERE id = ?",
        (asset_serial, "manual" if asset_serial else "review", doc_id),
    )
    conn.commit()


def summary(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT bind_confidence, doc_type, COUNT(*) n FROM doc_archive GROUP BY 1, 2"
    ).fetchall()
    by_conf: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for r in rows:
        by_conf[r["bind_confidence"]] = by_conf.get(r["bind_confidence"], 0) + r["n"]
        by_type[r["doc_type"]] = by_type.get(r["doc_type"], 0) + r["n"]
    total = sum(by_conf.values())
    return {
        "total": total,
        "by_confidence": by_conf,
        "by_type": by_type,
        "need_review": by_conf.get("review", 0),
    }


def review_document(
    conn: sqlite3.Connection, doc_id: int, values: dict | None, who: str
) -> dict:
    """人工審核一份單據：確認（或修正）抽出來的規格值。

    使用者 2026-08-15 提的關卡。自由填寫欄抓歪不會報錯，唯一有效的防線就是有人看過——
    所以審核時把抽到的值攤開讓人改，改完標 confirmed。沒審過的值不參與任何比對，
    避免「系統顯示的數字沒人看過卻被當成事實」。

    values 傳進來的是 {key: 修正後的字串}；空字串代表「這欄其實沒有值」，要能清掉，
    不能因為使用者想刪就刪不掉（那會逼他留一個錯的值在那）。
    """
    row = conn.execute("SELECT * FROM doc_archive WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise ValueError("找不到這份單據")

    current = json.loads(row["values_json"] or "{}")
    if values is not None:
        labels = {f["key"]: f["label"] for f in load_value_fields()}
        merged = {}
        for k, v in values.items():
            v = (v or "").strip()
            if not v:
                continue
            merged[k] = {
                "label": labels.get(k, current.get(k, {}).get("label", k)),
                "value": v[:40],
                # 人改過的要標出來，之後檢討抽取準確率時不能把人修的算成機器的成績
                "source": "人工確認" if current.get(k, {}).get("value") != v
                          else current.get(k, {}).get("source", "其他填空"),
            }
        current = merged

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE doc_archive SET values_json = ?, review_status = 'confirmed', "
        "reviewed_by = ?, reviewed_at = ? WHERE id = ?",
        (json.dumps(current, ensure_ascii=False), who, now, doc_id),
    )
    conn.commit()
    return {"id": doc_id, "review_status": "confirmed", "values": current,
            "reviewed_by": who, "reviewed_at": now}


def asset_timeline(conn: sqlite3.Connection, asset_serial: str) -> dict:
    """一台資產的單據時間軸：第一次申請什麼規格、後來被異動成什麼。

    使用者 2026-08-15 問「我才會知道他當初 CPU 跟記憶體申請是多少」——
    但一台機器常常有多張單（新增 → 異動 → 異動），「當初申請」跟「現在應該是多少」
    是兩個不同的問題，只給最新一張會答錯前者，只給第一張會答錯後者。所以兩個都給，
    並列出中間每一次規格變動。

    只採計人工審核過（confirmed）的規格值——沒人看過的數字不該被當成事實拿去比對。
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM doc_archive WHERE asset_serial = ? AND doc_type = 'provision_form' "
        "ORDER BY COALESCE(form_date, '9999'), id", (asset_serial,),
    )]
    entries = []
    for r in rows:
        vals = json.loads(r["values_json"] or "{}") if r["review_status"] == "confirmed" else {}
        entries.append({
            "id": r["id"], "form_date": r["form_date"], "request_no": r["request_no"],
            "file_name": r["file_name"], "is_current": r["is_current"],
            "review_status": r["review_status"], "is_decommission": r["is_decommission"],
            "values": vals,
        })

    changes = []
    for prev, cur in zip(entries, entries[1:]):
        for k, v in (cur["values"] or {}).items():
            old = (prev["values"] or {}).get(k, {}).get("value")
            if old is not None and old != v["value"]:
                changes.append({
                    "field": v["label"], "from": old, "to": v["value"],
                    "at": cur["form_date"], "request_no": cur["request_no"],
                })
    return {
        "entries": entries,
        "first": entries[0] if entries else None,
        "latest": entries[-1] if entries else None,
        "changes": changes,
        # 未審核的張數要講出來：時間軸上少了它們，使用者才不會以為「就這些」
        "unreviewed": sum(1 for e in entries if e["review_status"] != "confirmed"),
    }


# ===== 上線檢查表的檢核項目（約 90 列）=====
# 結構很規則：每一列都以「☐不需 ☑完成」（或勾起來的變體）結尾，前面是項目說明
# （Windows 欄／Linux 欄／備註分行）。所以用「判定欄」當切點反推項目，比逐格解析表格穩。
_VERDICT_RE = re.compile(r"[☐☑☒✓✔(]\s*不需\s*[☐☑☒✓✔(]\s*完成")
_CHECKLIST_CATEGORIES = (
    "基本設定(Policy)", "基本設定", "弱點修補", "WhatsUP", "ARCSight", "ARCSight(交易",
    "防網頁", "檔案竄改", "其他", "監控", "日誌",
)


def _readable_ratio(s: str) -> float:
    """可讀字元佔比。舊 .doc 雙解碼會產生整行亂碼，混進檢核項目名稱裡很難看，
    而且會讓搜尋命中垃圾。"""
    body = [ch for ch in s if not ch.isspace()]
    if len(body) < 3:
        return 0.0
    def good(ch):
        return ("一" <= ch <= "鿿") or (ch.isascii() and ch.isprintable())
    ok = sum(1 for ch in body if good(ch))
    # 光看比例不夠：一堆空白＋幾個 ASCII 符號也會過關。再要求有足夠的實質內容
    solid = sum(1 for ch in body if ("一" <= ch <= "鿿") or ch.isalnum())
    return ok / len(body) if solid >= 4 else 0.0


_ITEM_KEEP_RE = re.compile(r"[^一-鿿A-Za-z0-9()（）：:／/,.＋+\-_\s]")


def _clean_item(s: str) -> str:
    """把項目名稱裡的亂碼字元清掉（舊 .doc 雙解碼夾帶的二進位殘渣是零散夾雜的，
    用「整行是不是亂碼」判斷永遠會有漏網的）。"""
    return re.sub(r"\s{2,}", " ", _ITEM_KEEP_RE.sub("", s)).strip(" /")


def extract_checklist(text: str) -> list[dict]:
    """把上線檢查表的每一列抽成 {category, item, verdict}。

    verdict：完成／不需／未填（兩個框都沒勾＝這列沒處理，那本身就是要看的事實，
    不能當成「不需」跳過）。

    刻意**不把歷史檢查表變成系統裡的 golive 基線**：那些勾選是當年的狀態，
    轉成基線會立刻產生一堆過期的假 drift（見 golive.py）。這裡只是把紙本內容變成
    可查詢、可統計的資料——例如「哪幾台的 TLS 停用那一列是空的」。
    """
    out: list[dict] = []
    category = ""
    pos = 0
    for m in _VERDICT_RE.finditer(text):
        # 只取判定欄「前面 250 字」當項目說明：項目一定緊鄰它的判定欄，
        # 取整段的話第一列會把檔頭那堆二進位殘渣全吃進來（舊 .doc 用 cp950 解碼後
        # 那些殘渣長得像中文字，濾不掉，只能靠距離限制）。
        chunk = text[max(pos, m.start() - 250):m.start()]
        pos = m.end()
        lines = [x.strip() for x in chunk.split("\n") if x.strip()]
        if not lines:
            continue
        # 分類標題會單獨成行出現在該區塊第一列之前
        for ln in lines[:2]:
            for c in _CHECKLIST_CATEGORIES:
                if ln.replace(" ", "").startswith(c.replace(" ", "")) and len(ln) <= 14:
                    category = ln
                    break
        body = [x for x in lines if x != category][:3]
        item = _clean_item(" / ".join(body))[:120]
        if len(item) < 3:
            continue
        seg = m.group(0)
        # 「不需」與「完成」各自前面那個字元決定誰被勾
        need_mark = seg[0]
        done_mark = seg[seg.find("完成") - 1] if "完成" in seg else ""
        if done_mark in _CHECKED_MARKS:
            verdict = "完成"
        elif need_mark in _CHECKED_MARKS:
            verdict = "不需"
        else:
            verdict = "未填"
        out.append({"seq": len(out) + 1, "category": category, "item": item,
                    "verdict": verdict})
    return out


def extract_sections(text: str) -> dict:
    """需求單第二頁以後 13 個大區塊「適不適用」（DNS／F5／備份／WAF／資料庫…）。

    2026-08-15 使用者要求把整份單都抽。這裡刻意只抽區塊層級的狀態
    （新增／異動／刪除／不適用），不抽區塊內每一格：
      · 一眼看得出這台有沒有 DNS/F5/備份/WAF 需求，這是最常被問的
      · 勾選判定的可靠度遠高於自由填寫欄；區塊內細節留在全文檢索，要用時搜得到
      · 真實單子上絕大多數區塊都是「不適用」，逐格抽等於花一半工時服務最少的情境
    """
    cfg = json.loads(CHECKBOX_PATH.read_text(encoding="utf-8"))
    options = cfg.get("section_options", [])
    out: dict = {}
    flat = re.sub(r"\s+", "", text)
    for sec in cfg.get("sections", []):
        seg = None
        for anchor in sec["anchors"]:
            i = flat.find(anchor)
            if i >= 0:
                seg = flat[i: i + sec.get("window", 50)]
                break
        if seg is None:
            continue
        picked = []
        for opt in options:
            for m in re.finditer(re.escape(opt), seg):
                if m.start() and seg[m.start() - 1] in _CHECKED_MARKS:
                    picked.append(opt)
                    break
        # 四個都沒勾＝這個區塊整塊沒填。這本身是要看的事實（單子沒填完），
        # 直接跳過的話畫面上會看起來像「這台沒有這個區塊」，兩者意思完全不同。
        if not picked and not any(ch in seg for ch in _UNCHECKED_MARKS):
            continue
        out[sec["key"]] = {
            "label": sec["label"],
            "status": ("未填" if not picked
                       else picked[0] if len(picked) == 1 else "／".join(picked)),
            # 「不適用」以外都代表這台機器在這個區塊有需求，畫面用這個決定要不要highlight
            "applicable": bool(picked) and picked != ["不適用"],
        }
    return out
