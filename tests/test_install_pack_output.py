"""離線安裝包的 `.output` 完整性閘門（防 2026-10-12 那次前端整個起不來的回歸）。

那次的事故：`make_install_pack` 複製 frontend 時用了 `shutil.ignore_patterns(
"node_modules", ...)`——名稱比對、不分層級，連 `.output/server/node_modules`
（Nuxt build 產物**自帶的相依**）一起濾掉。目標機上：

    Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'vue-bundle-renderer'
    imported from .../.output/server/chunks/routes/renderer.mjs

這支測兩件事：
  1. 複製規則只擋 frontend/ 頂層的開發用目錄，`.output` 底下一律不擋。
  2. 包裡少了 `.output/server/node_modules` 時，閘門會紅燈**並刪掉那個壞包**。
"""
from __future__ import annotations

import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".project"))

import make_install_pack as mip  # noqa: E402


def _make_pack(tmp_path: Path, *, with_output_nm: bool) -> Path:
    """做一個最小的假 app 包。`with_output_nm=False` 就是事故當時那種壞包。"""
    stage = tmp_path / "stage"
    srv = stage / "frontend" / ".output" / "server"
    (srv / "chunks" / "routes").mkdir(parents=True)
    (srv / "index.mjs").write_text("// nitro\n", encoding="utf-8")
    (srv / "chunks" / "routes" / "renderer.mjs").write_text(
        "import { x } from 'vue-bundle-renderer/runtime';\n", encoding="utf-8")
    if with_output_nm:
        pkg = srv / "node_modules" / "vue-bundle-renderer" / "dist"
        pkg.mkdir(parents=True)
        (pkg / "runtime.mjs").write_text("export const x = 1;\n", encoding="utf-8")
        (pkg.parent / "package.json").write_text('{"name":"vue-bundle-renderer"}\n',
                                                 encoding="utf-8")

    pack = tmp_path / "webit3-app-vTEST.tar.gz"
    with tarfile.open(pack, "w:gz") as t:
        for child in sorted(stage.iterdir()):
            t.add(child, arcname=child.name)
    return pack


def test_完整的包過閘門(tmp_path):
    pack = _make_pack(tmp_path, with_output_nm=True)
    mip.verify_app_pack_output_or_die(pack)
    assert pack.is_file(), "通過的包不該被動到"


def test_缺_output_的_node_modules_會紅燈並刪包(tmp_path):
    pack = _make_pack(tmp_path, with_output_nm=False)
    sha = pack.parent / (pack.name + ".sha256")
    sha.write_text("dummy\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        mip.verify_app_pack_output_or_die(pack)

    assert not pack.exists(), "壞包必須被刪掉，不能留在 dist 等人誤傳"
    assert not sha.exists(), "壞包的 .sha256 也要一起清掉"


def test_複製規則只擋頂層的開發用目錄():
    """`.output` 底下有同名目錄時不准被擋——那是 build 產物的一部分。"""
    fe = mip.APP / "frontend"
    names = ["node_modules", ".nuxt", ".data", ".output", "pages", "nuxt.config.ts"]

    assert mip._fe_copy_ignore(fe, names) == {"node_modules", ".nuxt", ".data"}
    assert mip._fe_copy_ignore(fe / ".output" / "server", names) == set()
    assert mip._fe_copy_ignore(fe / "pages", names) == set()
