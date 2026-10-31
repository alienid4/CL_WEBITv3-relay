<script setup lang="ts">
// 7.2 帳號盤點（2.x 第二批）：共用盤點流程（V2InventoryFlow，任務＝凍結當下已收集的主機帳號＋與上一次比較）
// ＋「特權帳號」頁籤（2.007.52，舊版特權帳號清單翻新：sudo／UID 0／標準管理帳號，跟舊版帳號儀表板「特權帳號」卡同一個條件）
// ＋「稽核發現」頁籤（帳號規則檢核結果，依規則彙總；逐筆處理仍在舊版帳號稽核頁）。
// 收集身分與排除清單日後移到 ⚙ 系統設定，目前連到現行位置。
definePageMeta({ layout: 'v2' })
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋，這裡只是別讓人白按）
const { apiFetch } = useApi()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const EXTRA = [{ k: 'priv', label: '特權帳號' }, { k: 'findings', label: '稽核發現' }, { k: 'identity', label: '收集身分' }, { k: 'matrix', label: '合規矩陣' }]
// 收集身分設定（2.007.59）：直接嵌入既有「盤點作業」畫面（收集身分、排除清單、診斷、立即收集；功能與 API 不變）
const AccountOps = defineAsyncComponent(() => import('~/pages/account-ops.vue'))
const fd = ref<any>(null)
const fdErr = ref('')
async function loadFindings() {
  if (fd.value) return
  try { fd.value = await apiFetch<any>('/api/accounts/findings') } catch (e: any) { fdErr.value = `稽核發現載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
const byRule = computed(() => {
  const m: Record<string, any> = {}
  for (const x of fd.value?.items ?? []) {
    const r = (m[x.rule_id] ||= { rule_id: x.rule_id, label: x.label, severity: x.severity, total: 0, open: 0, hosts: new Set() })
    r.total++
    if (x.status === 'open') r.open++
    r.hosts.add(x.ip)
  }
  return Object.values(m).map((r: any) => ({ ...r, hosts: r.hosts.size })).sort((a: any, b: any) => String(a.rule_id).localeCompare(String(b.rule_id)))
})
const route = useRoute()
const pv = ref<any>(null)
const pvErr = ref('')
async function loadPriv() {
  if (pv.value) return
  try { pv.value = await apiFetch<any>('/api/accounts', { query: { privileged: true } }) } catch (e: any) { pvErr.value = `特權帳號載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
const YN = (v: any) => (v ? '是' : '否')
const pvRows = computed(() => (pv.value?.items ?? []).map((a: any, i: number) => ({ ...a, __no: i + 1, sudo: a.is_sudoer ? (a.sudo_nopasswd ? '是（免密碼）' : '是') : '否', login: YN(a.can_login),
  lastlog: (a.last_login || '').replace(/\s+/g, ' ').slice(0, 60) })))
const pvHosts = computed(() => new Set((pv.value?.items ?? []).map((a: any) => a.ip)).size)
// 合規矩陣（2.011.x）：有自己的儀表板／合規表／稽核發現／帳號清單分頁（用 ?tab=），所以獨立一頁 /v2/inventory/matrix，這個頁籤只是入口
watch(() => route.query.tab, t => { if (t === 'findings') loadFindings(); if (t === 'priv') loadPriv(); if (t === 'matrix') navigateTo({ path: '/v2/inventory/matrix', query: { tab: 'matrix' } }, { replace: true }) }, { immediate: true })
// ===== 逐筆處理（2.007.54，舊版帳號稽核頁「逐筆處理」翻新）：點規則的筆數看明細，勾選後一次設處置（待處理／已確認／核准例外／已修復）=====
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const DISP: Record<string, string> = { open: '待處理', ack: '已確認', exception: '核准例外', fixed: '已修復' }
const rule = computed(() => String(route.query.rule || ''))
const onlyOpen = computed(() => route.query.st === 'open')
const detail = computed(() => (fd.value?.items ?? []).filter((x: any) => x.rule_id === rule.value && (!onlyOpen.value || x.status === 'open'))
  .map((x: any, i: number) => ({ ...x, __no: i + 1, rk: `${x.ip}|${x.username}|${x.rule_id}`, host: x.hostname || x.ip, st: x.exempt_expired ? '待處理（例外已過期）' : (DISP[x.status] ?? x.status), until: x.exempt_until || '' })))
const picked = ref<Record<string, boolean>>({})
const pickedItems = computed(() => detail.value.filter((r: any) => picked.value[r.rk]))
const dStatus = ref('ack')
const dNote = ref('')
const dUntil = ref('')
const dFixedAt = ref('')
const dVerifiedAt = ref('')
const dVerifyDue = ref('')
const dRefUrl = ref('')
const dBusy = ref(false)
watch(rule, () => { picked.value = {} })
// 沒輸入的欄位就不送（後端「沒帶＝保留原值」）：批次改狀態不會把既有的修復時間／來源連結洗掉
function extraDisp() {
  const o: Record<string, string> = {}
  if (dStatus.value === 'fixed') {
    if (dFixedAt.value) o.fixed_at = dFixedAt.value
    if (dVerifiedAt.value) o.verified_at = dVerifiedAt.value
    if (dVerifyDue.value) o.verify_due = dVerifyDue.value
  }
  if (dRefUrl.value.trim()) o.ref_url = dRefUrl.value.trim()
  return o
}
async function applyDisp() {
  const items = pickedItems.value
  if (!items.length) { showToast('請先勾選要處置的發現', 'warn'); return }
  if (dStatus.value === 'exception' && (!dNote.value.trim() || !dUntil.value)) { showToast('核准例外要寫理由並選到期日', 'warn'); return }
  if (dRefUrl.value.trim() && !/^https?:\/\/\S+$/i.test(dRefUrl.value.trim())) { showToast('來源連結只接受 http:// 或 https:// 開頭的網址', 'warn'); return }
  if (!window.confirm(`把勾選的 ${items.length} 筆設為「${DISP[dStatus.value]}」？`)) return
  dBusy.value = true
  let ok = 0
  const bad: string[] = []
  for (const r of items) {
    try {
      await guardedFetch('/api/accounts/findings/disposition', { method: 'PUT', body: { ip: r.ip, username: r.username, rule_id: r.rule_id, status: dStatus.value, note: dNote.value.trim() || null, exempt_until: dStatus.value === 'exception' ? dUntil.value : null, ...extraDisp() } })
      ok++
    } catch (e: any) { bad.push(`${r.ip} ${r.username}：${e?.data?.detail ?? e?.message ?? '失敗'}`) }
  }
  showToast(bad.length ? `完成 ${ok} 筆、失敗 ${bad.length} 筆（${bad[0]}）` : `已處置 ${ok} 筆`, bad.length ? 'warn' : 'success')
  picked.value = {}
  fd.value = null
  await loadFindings()
  dBusy.value = false
}
</script>

<template>
  <V2InventoryFlow kind="account" no="7.2" title="帳號盤點" item-label="帳號" :extra-tabs="EXTRA">
    <template #extra="{ tab }">
      <template v-if="tab === 'priv'">
        <p v-if="pvErr" class="err">{{ pvErr }}</p>
        <p v-else-if="!pv" class="muted">載入中…</p>
        <template v-else>
          <div class="bar"><span class="muted">特權帳號 {{ pv.items.length.toLocaleString() }} 個（{{ pvHosts }} 台主機）</span>
            <InfoNote>特權＝有 sudo 權限、UID 0 或標準管理帳號（與舊版帳號儀表板「特權帳號」卡同一個條件）。資料來自已納管主機的帳號收集；沒收過的主機不在這裡。點 IP 看那台主機。</InfoNote></div>
          <V2Table :rows="pvRows" prefix="P" :max="200" empty="沒有特權帳號資料"
                   :cols="[{ k: 'ip', label: '主機 IP', mono: true, link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) }, { k: 'username', label: '帳號' }, { k: 'uid', label: 'UID', mono: true }, { k: 'login', label: '可登入' }, { k: 'sudo', label: 'sudo' }, { k: 'priv_groups', label: '特權群組' }, { k: 'pw_status', label: '密碼狀態' }, { k: 'pw_last_change', label: '密碼最後變更', mono: true }, { k: 'lastlog', label: '最後登入' }, { k: 'os_family', label: '作業系統' }]" />
        </template>
      </template>
      <V2Embed v-else-if="tab === 'identity'"><AccountOps /></V2Embed>
      <p v-else-if="tab === 'matrix'" class="muted">開啟中… <NuxtLink to="/v2/inventory/matrix?tab=matrix">帳號合規矩陣</NuxtLink></p>
      <template v-else-if="tab === 'findings'">
        <p v-if="fdErr" class="err">{{ fdErr }}</p>
        <p v-else-if="!fd" class="muted">載入中…</p>
        <template v-else>
          <div class="bar">
            <span class="muted">共 {{ fd.items.length }} 筆發現、{{ byRule.length }} 條規則</span>
            <a class="btn2" :href="`${apiBase}/api/accounts/findings/export`">匯出稽核發現</a>
            <a class="btn2" :href="`${apiBase}/api/accounts/export?fmt=standard`">匯出帳號盤點表</a>
            <InfoNote>匯出稽核發現含處置狀態與覆核紀錄；帳號盤點表為公司現行 18 欄格式。</InfoNote>
          </div>
          <template v-if="rule">
            <div class="bar"><NuxtLink :to="{ query: { tab: 'findings' } }">‹ 回規則清單</NuxtLink><b>{{ rule }}　{{ detail[0]?.label || '' }}</b><span class="muted">{{ detail.length }} 筆{{ onlyOpen ? '（只看待處理）' : '' }}</span></div>
            <form v-if="!isViewer" class="disp" @submit.prevent="applyDisp">
              <span>勾選 <b>{{ pickedItems.length }}</b> 筆，設為</span>
              <select v-model="dStatus" aria-label="處置狀態"><option v-for="(l, k) in DISP" :key="k" :value="k">{{ l }}</option></select>
              <input v-model="dNote" class="dnote" placeholder="備註／理由（核准例外必填）" aria-label="備註">
              <input v-if="dStatus === 'exception'" v-model="dUntil" type="date" aria-label="例外到期日">
              <template v-if="dStatus === 'fixed'">
                <label class="dl">實際修復 <input v-model="dFixedAt" type="datetime-local" aria-label="實際修復時間"></label>
                <label class="dl">複驗成功 <input v-model="dVerifiedAt" type="datetime-local" aria-label="複驗成功時間"></label>
                <label class="dl">複驗期限 <input v-model="dVerifyDue" type="date" aria-label="複驗期限"></label>
              </template>
              <input v-model="dRefUrl" class="dnote" maxlength="500" placeholder="來源連結（工單／變更單，選填）" aria-label="來源連結">
              <button type="submit" class="btn2 on" :disabled="dBusy || !pickedItems.length">{{ dBusy ? '處置中…' : '套用' }}</button>
              <InfoNote>處置狀態跨次盤點保留，並記錄是誰、何時決定的。核准例外要有理由與到期日，到期後會回到待處理。「已修復」可另填實際修復時間、複驗成功時間、複驗期限（都可留空，系統不會自動設期限；複驗期限由你自己定）。來源連結只收 http／https，系統不會去開它，表格以純文字顯示；留空＝不動原有的修復時間與連結（備註仍會覆蓋）。改成「待處理／已確認／核准例外」時，該筆原有的實際修復、複驗成功、複驗期限會清掉（它已不是已修復）；來源連結保留。「已確認」＝知道了、之後處理；「已修復」＝這筆已經處理掉。</InfoNote>
            </form>
            <V2Table v-model:selected="picked" :rows="detail" prefix="F" row-key="rk" selectable :max="200" empty="沒有符合的發現"
                     :cols="[{ k: 'host', label: '主機', link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) }, { k: 'ip', label: 'IP', mono: true }, { k: 'username', label: '帳號' }, { k: 'severity', label: '嚴重度' }, { k: 'detail', label: '細節' }, { k: 'st', label: '處置' }, { k: 'until', label: '例外到期', mono: true }, { k: 'fixed_at', label: '實際修復', mono: true }, { k: 'verified_at', label: '複驗成功', mono: true }, { k: 'verify_due', label: '複驗期限', mono: true }, { k: 'ref_url', label: '來源連結' }, { k: 'disp_note', label: '備註' }, { k: 'decided_by', label: '決定人' }]" />
          </template>
          <V2Table v-else :rows="byRule" prefix="R" empty="沒有稽核發現"
                   :cols="[{ k: 'rule_id', label: '規則', mono: true }, { k: 'label', label: '說明' }, { k: 'severity', label: '嚴重度' }, { k: 'total', label: '發現（筆）', mono: true, link: (r: any) => `/v2/inventory/account?tab=findings&rule=${encodeURIComponent(r.rule_id)}` }, { k: 'open', label: '待處理（筆）', mono: true, link: (r: any) => (r.open ? `/v2/inventory/account?tab=findings&rule=${encodeURIComponent(r.rule_id)}&st=open` : null) }, { k: 'hosts', label: '主機（台）', mono: true }]" />
        </template>
      </template>
    </template>
  </V2InventoryFlow>
</template>

<style scoped>
.dl { display: inline-flex; gap: 4px; align-items: center; }
.disp { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--sub); }
.dnote { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; width: 260px; }
.btn2.on { background: var(--brand); border-color: var(--brand); color: var(--type-on); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; text-decoration: none; }
</style>
