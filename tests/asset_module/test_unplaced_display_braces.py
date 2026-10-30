"""機櫃「空 U」顯示（W221-001）：模板插值要用 {{ }}，單層 { } 會被 Vue 當成字面文字印出來。
獨立審查者 2026-10-31 抓到 4 處（RackElevation／datacenter／v2/datacenter），編譯會過、畫面會壞，所以用靜態測試釘住。
"""
import re
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"
FILES = ["components/RackElevation.vue", "pages/datacenter.vue", "pages/v2/datacenter.vue", "components/SystemMap.vue"]


def test_空U顯示沒有單層大括號插值():
    bad = []
    for f in FILES:
        s = (FE / f).read_text(encoding="utf-8")
        tpl = s.split("<style")[0]
        for i, line in enumerate(tpl.splitlines(), 1):
            if "未定位" in line or "free_u" in line:
                # 文字節點裡的 { 變數.欄位 }（不是 {{ }}、不是 ${ }、不是 :class="{ ... }" 物件）
                for m in re.finditer(r"(?<![{$\"':=\s])\s*\{ [A-Za-z_][\w.]* \}", line):
                    bad.append(f"{f}:{i} {m.group(0).strip()}")
                if re.search(r">[^<{]*(?<![{$])\{ [A-Za-z_][\w.]* \}[^}]", line):
                    bad.append(f"{f}:{i} 文字節點單層插值")
    assert not bad, bad


def test_unplaced為0時畫面文字與原本一致():
    s = (FE / "components/RackElevation.vue").read_text(encoding="utf-8")
    assert "`${det.power.free_u}U`" in s                 # 0 台時走原本的「空 NU」
    d = (FE / "pages/datacenter.vue").read_text(encoding="utf-8")
    assert "`${room.power.free_u}U`" in d
