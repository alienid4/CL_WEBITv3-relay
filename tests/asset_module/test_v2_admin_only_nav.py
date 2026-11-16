"""開發協作（🛠）只有管理員看得到、進得去（2026-11-14 三層分離 M52：開發工具不可出現在一般同仁的操作畫面）。

選單隱藏不等於進不去，所以兩邊都釘：useNavV2 的 admin 旗標＋對非管理員過濾；middleware/authz.global.ts 擋直接網址。
"""
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"


def test_開發協作標成管理員限定():
    s = (FE / "composables" / "useNavV2.ts").read_text(encoding="utf-8")
    line = next(x for x in s.splitlines() if "'/v2/devkit'" in x)
    assert "admin: true" in line


def test_選單對非管理員過濾admin項目():
    s = (FE / "composables" / "useNavV2.ts").read_text(encoding="utf-8")
    body = s[s.index("export function useV2Nav"):]
    assert "useAuth()" in body and ".admin" in body and "isAdmin.value" in body


def test_直接網址也擋():
    p = FE / "middleware" / "authz.global.ts"
    s = p.read_text(encoding="utf-8")
    assert "it.admin" in s and "isAdmin.value" in s and "navigateTo('/v2')" in s
    # 要跑在 auth.global.ts 之後（Nuxt 依檔名字母序跑全域 middleware），使用者資料才已載入
    names = sorted(x.name for x in (FE / "middleware").glob("*.global.ts"))
    assert names.index("auth.global.ts") < names.index("authz.global.ts")
