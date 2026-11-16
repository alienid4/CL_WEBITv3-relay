"""部署後端到端驗收關卡（決策 ACCEPT-E2E1，2026-10-18）。

釘住三件事：
1. deploy.sh 在換版驗證之後真的會跑這一關、失敗會中止；只有明確的環境變數能略過（而且大聲）。
2. 公司機（patch.sh／setup.sh）不跑自動版，改印手動三步——公司不保證收集器自己已納管。
3. 驗收腳本只打瀏覽器會打的端點，而且「沒收到／沒原因的略過／舊資料」都判不通過。
"""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "APP" / "asset-module"


def _load():
    spec = importlib.util.spec_from_file_location("deploy_e2e_accept", APP / "deploy_e2e_accept.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_deploy_sh_有端到端關卡_在換版驗證之後_失敗中止():
    s = (APP / "deploy.sh").read_text(encoding="utf-8")
    i76, i78 = s.index("[7.6/7]"), s.index("[7.8/7]")
    assert i76 < i78 < s.index("部署完成。前端")
    block = s[i78:s.index("部署完成。前端")]
    assert "deploy_e2e_accept.py" in block and "--mode script" in block
    assert re.search(r"else\s+echo \"!! 端到端驗收不通過.*?exit 1", block, re.S), "不通過要中止部署"
    assert 'WEBIT_SKIP_E2E:-}" = "1"' in block and "!!!!" in block, "略過要明確、要大聲"
    assert 'WEBIT_E2E_ACCEPT:-auto}" = "manual"' in block
    assert "端到端　: ${E2E_STATUS" in s, "結尾要攤出這一關的狀態"


def test_公司機_patch與setup_改手動三步():
    mpf = ROOT / ".project" / "make_patch.py"
    if mpf.exists():          # 公開 relay 快照不含 make_patch.py（出包在私有 repo 做）
        mp = mpf.read_text(encoding="utf-8")
        assert 'WEBIT_E2E_ACCEPT=manual bash "$APP/deploy.sh"' in mp
        assert "請手動驗收「重新納管後資料進得來」" in mp
    st = (APP / "setup.sh").read_text(encoding="utf-8")
    assert 'WEBIT_E2E_ACCEPT=manual bash "$APP_DIR/deploy.sh"' in st


def test_驗收腳本只打瀏覽器會打的端點():
    src = (APP / "deploy_e2e_accept.py").read_text(encoding="utf-8")
    fe = "\n".join(p.read_text(encoding="utf-8") for p in
                   [APP / "frontend/components/OnboardModal.vue",
                    APP / "frontend/components/PasteOnboardModal.vue",
                    APP / "frontend/pages/assets/[serial].vue"])
    for ep in ("/api/onboard/script", "/api/onboard/verify", "/api/onboard/paste/issue",
               "/api/onboard/bundle/download", "/api/onboard/paste/status",
               "/api/onboard/post-collect/", "/api/host-spec/rebuild", "/api/config-audit/by-asset"):
        assert ep in src, ep
        assert ep in fe, f"{ep} 不是畫面用的端點——驗收不可以走畫面沒走的路"
    assert "post_onboard" not in src.split('"""', 2)[2].replace("kind=post_onboard", ""), \
        "驗收腳本不可以 import／直接呼叫內部函式"
    assert not re.search(r"\b10\.9[0-8]\.\d+\.\d+|\b192\.168\.\d+\.\d+", src.split('"""', 2)[2]), "會進公開 relay，不寫死 IP"


class FakeApi:
    def __init__(self, rebuild_at, audit_at):
        self.rebuild_at, self.audit_at = rebuild_at, audit_at
        self.calls = []

    def call(self, method, path, params=None, **k):
        self.calls.append(path)
        if path == "/api/host-spec/rebuild":
            return 200, {"host_rebuild": {"collected_at": self.rebuild_at,
                                          "data": {"summary": {"a_collected": 1, "total_a": 2}}}}
        if path == "/api/config-audit/by-asset":
            return 200, {"state": "有檢核結果", "run": {"id": 1, "imported_at": self.audit_at}, "items": [1]}
        raise AssertionError(path)


def _pc(**over):
    labels = {"spec": "硬體規格", "service": "服務", "software": "軟體", "account": "帳號",
              "rebuild": "OS 重建資料", "config_audit": "組態檢核"}
    res = [{"job": j, "label": l, "ok": True, "message": "收到了"} for j, l in labels.items()]
    for r in res:
        r.update(over.get(r["job"], {}))
    return {"results": res}


def test_驗收判定_全收到且是這次之後才通過():
    m = _load()
    m.T0 = "2026-10-18 18:00:00"
    assert m.check(FakeApi("2026-10-18 18:01:00", "2026-10-18 18:01:00"), "S", _pc(), None) == []


def test_驗收判定_舊資料不算():
    m = _load()
    m.T0 = "2026-10-18 18:00:00"
    bad = m.check(FakeApi("2026-10-18 17:00:00", "2026-10-18 17:00:00"), "S", _pc(), None)
    assert any("OS 重建" in b for b in bad) and any("組態檢核" in b for b in bad), bad


def test_驗收判定_沒收到或沒原因的略過都不通過():
    m = _load()
    m.T0 = "2026-10-18 18:00:00"
    api = FakeApi("2026-10-18 18:01:00", "2026-10-18 18:01:00")
    bad = m.check(api, "S", _pc(rebuild={"ok": False, "message": "一項都沒收到"},
                                config_audit={"ok": None, "message": ""}), None)
    assert any("一項都沒收到" in b for b in bad) and any("略過但沒有原因" in b for b in bad), bad
    # 有原因的略過（VMware 設備／AIX 的 OS 重建）是正當的（Windows 自 v1.475.0 起有組態檢核，不再略過）
    assert m.check(api, "S", _pc(config_audit={"ok": None,
                                               "message": "VMware 設備（ESXi／vCenter）不經 SSH 收集"}), None) == []
    # 少一樣也不行
    pc = _pc()
    pc["results"] = pc["results"][:5]
    assert any("沒有結果" in b for b in m.check(api, "S", pc, None))
