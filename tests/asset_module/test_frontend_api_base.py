"""前端直接下載 API 檔案時，一定要帶 apiBase（2026-09-18）。

前端跑在 3000、API 在另一個埠。`window.open('/api/...')` 或 `href="/api/..."` 會打到
Nuxt 自己，回 404 "Page not found"。apiFetch 會自動補前綴，但直接開網址不會——
v1.216～v1.219 的兩個匯出按鈕就是這樣在公司機失敗的，test_frontend_api_paths
只比對路徑有沒有對應的後端路由，看不出前綴少了。
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"
BAD = re.compile(r"""(window\.open\(\s*['"`]/api|location\.href\s*=\s*['"`]/api|\bhref="/api|:href="'/api)""")


def test_直接開網址下載要帶apiBase():
    files = (list(FRONTEND.glob("pages/**/*.vue")) + list(FRONTEND.glob("components/*.vue"))
             + list(FRONTEND.glob("layouts/*.vue")) + list(FRONTEND.glob("composables/*.ts")))
    assert len(files) > 20, "前端檔案沒掃到"
    bad = []
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if BAD.search(line):
                bad.append(f"  {f.relative_to(FRONTEND)}:{i}  {line.strip()[:100]}")
    assert not bad, ("這些地方直接開 /api/... 沒帶 apiBase，會打到前端自己回 404：\n"
                     + "\n".join(bad) + "\n改成 `${apiBase}/api/...`（見 account-matrix.vue doExport）")


def test_下載失敗不可以只丟狀態碼():
    """`throw new Error(\`HTTP ${res.status}\`)` 會把後端的原因丟掉。

    2026-09-18 公司機「匯出失敗：HTTP 400」——後端其實回了「還沒設定收件人公鑰，
    無法匯出……」。全前端 8 處同寫法，統一改走 composables/httpReason.ts。
    """
    bad_pat = re.compile(r"throw new Error\(`HTTP \$\{res\.status\}`\)")
    files = list(FRONTEND.glob("pages/**/*.vue")) + list(FRONTEND.glob("components/*.vue"))
    bad = [f"  {f.relative_to(FRONTEND)}:{i}"
           for f in files
           for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
           if bad_pat.search(line)]
    assert not bad, ("這些地方失敗時只顯示狀態碼，後端寫的原因被丟掉了：\n" + "\n".join(bad)
                     + "\n改成 throw new Error(await httpReason(res))")
