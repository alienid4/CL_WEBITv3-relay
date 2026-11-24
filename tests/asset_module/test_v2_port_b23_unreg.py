"""V2 移植第 23 批 第 4 片（2.058.4）：舊 /adopt「未登記主機」頁的動作搬到 2.1「掃到未登記」。⚠D1 D4 純搬移：
手動登記＝同一支 POST /api/assets/adopt、同樣的預帶與必填；一鍵納管／批次納管＝共用 OnboardModal／BatchOnboardModal。"""
from pathlib import Path

from test_v2_port_b23 import _api_calls, _src

REG = "components/v2/AdoptRegister.vue"
UNREG = "components/v2/HostUnregistered.vue"


def test_手動登記_端點body必填跟舊頁一樣():
    old, new = _src("pages/adopt.vue"), _src(REG)
    assert _api_calls(new) == {("GET", "/api/field-meta"), ("POST", "/api/assets/adopt")}
    assert _api_calls(new) <= _api_calls(old)
    for frag in ("await apiFetch('/api/assets/adopt', { method: 'POST', body: { fields: { ...form } } })",
                 "if (!form.asset_purpose || !form.environment)", "請填紅框的必填欄位：資產用途、環境別"):
        assert frag in old and frag in new, frag
    u = _src("utils/adoptCollect.ts")
    for frag in ("form.ip = host.ip", "form.hostname = host.hostname ?? ''", "form.subnet = host.segment ?? ''",
                 "if (host.mac) form.mac = host.mac", "if (host.is_vm) form.is_vm = 'VM'"):
        assert frag in old, frag
    assert "{ ip: h.ip, hostname: h.hostname ?? '', subnet: h.segment ?? '' }" in u
    assert "if (h.mac) f.mac = h.mac" in u and "if (h.is_vm) f.is_vm = 'VM'" in u


def test_掃到未登記_納管沿用共用元件_只送看得到的勾選():
    s = _src(UNREG)
    assert '<OnboardModal v-if="onboardHost" :ip="onboardHost.ip" :os-guess="onboardHost.os_guess"' in s
    assert '<BatchOnboardModal v-if="batchIps" :ips="batchIps"' in s
    assert '@click="batchIps = toSend"' in s
    assert "const toSend = computed(() => visibleIps(sel.value, shown.value))" in s
    assert "watch(shown, (s) => { sel.value = pruneIps(sel.value, s) })" in s
    assert "header-clears-all" in s and "<V2AdoptRegister" in s
    # 自己不直接打任何寫入端點（寫入都在共用元件／V2AdoptRegister 裡）
    assert all(m == "GET" for m, _ in _api_calls(s))
    assert "<table" not in s and "'/adopt" not in s
