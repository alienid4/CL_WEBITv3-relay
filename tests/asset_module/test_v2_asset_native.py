"""2.059.2（V20 收尾）：資產明細與操作改 V2 原生——⚠D1／D2 頁首動作的守門。
按鈕只接舊 /assets/[serial] 已在用的同一支 API、同一個彈窗；VMware（ESXi／vCenter）不出現納管與收集按鈕（比舊頁嚴，天條）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"


def _src(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def test_頁首動作用舊頁同一支API與同一個彈窗():
    old = _src("pages/assets/[serial].vue")
    act = _src("components/v2/AssetActions.vue")
    for s in ("/api/onboard/post-collect/", "/api/host-spec/collect?asset_serial=", "/api/onboard-exempt/remove",
              "<OnboardModal", "<RevokeModal", "<OfflineMarkModal", "<ExemptModal", "<AliveCheck"):
        assert s in old, s
        assert s in act, f"V2 頁首少了 {s}"
    jobs = _src("components/v2/AssetDocsJobs.vue")
    for s in ("'/api/config-audit/collect'", "/api/host-spec/rebuild-collect?asset_serial=", "/api/assets/${enc(props.serial)}/provision", "/api/assets/${enc(props.serial)}/documents"):
        assert s.replace("enc(props.serial)", "route.params.serial") in old or s in old, s
        assert s in jobs, s
    tags = _src("components/v2/AssetTagsOs.vue")
    for s in ("'/api/adopt/move-choices'", "'/api/os-type-overrides'", "/api/os-type-overrides?${q.toString()}", "/tags/${enc(tag)}"):
        assert s in tags, s


def test_VMware不出現納管與收集():
    ops = _src("utils/assetOps.ts")
    assert "hw?.onboard_block?.kind === 'esxi'" in ops                  # 後端判定
    assert "esxi|vmware esx|vsphere hypervisor|photon|vcenter|vcsa|vm vc" in ops   # 跟 os_class_groups「虛擬化平台」同一串
    assert "onboard: !opts.vmware" in ops and "reonboard: !opts.vmware" in ops and "collect: !opts.vmware" in ops
    act = _src("components/v2/AssetActions.vue")
    assert "isVmwareAsset(hw.value, props.detail?.os_view)" in act
    for btn in ('v-if="can.onboard"', 'v-if="can.reonboard"', 'v-if="can.collect"'):
        assert btn in act, btn
    assert "if (collectingAll.value || collectingSpec.value || !can.value.collect) return" in act
    jobs = _src("components/v2/AssetDocsJobs.vue")
    assert 'v-if="!vmware && !isViewer"' in jobs and "if (vmware.value || collectingAudit.value) return" in jobs
    assert "if (vmware.value || !onboarded.value || collectingRebuild.value) return" in jobs      # 2.059.5：OS 重建先納管
    assert "isVmwareAsset(props.detail?.hardware, props.detail?.os_view)" in jobs                    # 2.059.5：跟頁首同一個判斷


def test_頁面不再嵌入舊頁_留legacy入口():
    p = _src("pages/v2/asset/[serial].vue")
    assert "pages/assets/[serial].vue')" not in p and "<V2AssetView" in p and "<V2AssetEdit" in p
    v = _src("components/v2/AssetView.vue")
    assert "?legacy=1" in v and "<V2AssetActions" in v


def test_舊版完整明細_legacy進來只給看():
    """2.059.5 審查：V2 的「舊版完整明細」連到 /assets/X?legacy=1，那頁沒有 VMware／唯讀帳號的新閘門，所以 legacy 進來不出現任何動作。"""
    old = _src("pages/assets/[serial].vue")
    assert "const legacyView = computed(() => route.query.legacy" in old
    head = old[old.index('<div class="head-right">'):old.index("<!-- 六樣收集（納管後自動")]
    for btn in ("@click=\"showOnboard = true\"", "@click=\"collectAll\"", "@click=\"collectSpec\"", "@click=\"showRevoke = true\"", "showOffline = true", "@click=\"showExempt = true\"", "@click=\"unexempt\""):
        i = head.index(btn)
        tag = head[head.rindex("<", 0, i):i]
        assert "!legacyView" in tag, btn
    assert "舊版唯讀檢視" in head
    for s in ('<button v-if="!legacyView" class="btn-collect" type="button" :disabled="collectingAudit',
              '<button v-if="!legacyView" class="btn-collect" :disabled="collectingRebuild',
              '<a v-if="isOnboarded && !legacyView" class="speclink" @click="collectSpec">',
              '<a v-if="isOnboarded && !legacyView" class="speclink" @click="collectRebuild">'):
        assert s in old, s


def test_V20補齊_可信度徽章_同IP_實測不符_網卡用舊頁同一份資料():
    """2026-11-13：舊頁頁首的存在可信度／資料可信度徽章、同 IP 多筆／實測不符小標籤、網卡 MAC 表搬到 V2（純顯示）。
    資料只接舊頁已在用的同一支 API／同一個欄位；同一台的判斷只看後端 same_machine（machine_scope），前端不自己算。"""
    old = _src("pages/assets/[serial].vue")
    view = _src("components/v2/AssetView.vue")
    for s in ("/api/existence/asset/${", "/api/trust/asset/${"):
        assert s in old and s in view, s
    sig = _src("utils/assetSignals.ts")
    for s in ("collectStatus?.same_ip", "r.same_machine && !r.off_book", "r.same_machine && r.off_book", "!r.same_machine",
              "detail?.os_view?.mismatch", "detail?.ip_view", "detail?.hostname_view", "detail?.ip_error_view",
              "detail?.mac_view", "detail?.nics", "s >= 90 ? 'good' : s >= 50 ? 'warn' : 'bad'"):
        assert s in sig, s
    for s in ("same_ip ?? []", "r.same_machine && !r.off_book", "trustInfo.score >= 90 ? 'good' : trustInfo.score >= 50 ? 'warn' : 'bad'", "?.mac_view", "?.nics"):
        assert s in old, s
    # 不在前端自己判斷「同一台」：不准出現 hostname／ip 比對去重
    assert "hostname ===" not in sig and "machine_key" not in sig
    chips = _src("components/v2/AssetSignals.vue")
    assert "splitSameIp(props.detail?.collect_status)" in chips and "/api/measured-mismatch/export?asset_serial=" in chips
    assert "/api/measured-mismatch/export?asset_serial=" in old
    nics = _src("components/v2/AssetNics.vue")
    assert "<V2Table" in nics and "nicRows(props.detail)" in nics
    assert "{ k: 'nic', label: '網卡' }" in view and "<V2AssetNics" in view and "<V2AssetSignals" in view
    # 純顯示：新元件沒有任何寫入呼叫、沒有納管／收集按鈕
    for src in (chips, nics, sig):
        assert "apiFetch" not in src and "method:" not in src and "<button" not in src and "OnboardModal" not in src
