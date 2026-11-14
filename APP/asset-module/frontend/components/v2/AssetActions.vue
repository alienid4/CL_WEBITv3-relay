<script setup lang="ts">
// 資產明細與操作（V20，2.059.2）頁首動作——⚠D1／D2：只接舊 /assets/[serial] 已在用的同一支 API、同一個彈窗，後端不動。
//   一鍵納管／重新納管：OnboardModal（使用者當場輸入帳密，用完即清）
//   收集全部：POST /api/onboard/post-collect/{序號}（跟納管後同一套六樣），每 5 秒輪詢 GET 同一支
//   收集規格：POST /api/host-spec/collect?asset_serial=
//   取消納管：RevokeModal（平台照 OS 判斷 aix／windows／linux）
//   下線：OfflineMarkModal；非納管：ExemptModal；取消豁免：POST /api/onboard-exempt/remove
//   偵測存活：AliveCheck（只偵測不改）
// VMware（ESXi／vCenter）：納管與收集按鈕一律不出現（utils/assetOps.actionFlags，比舊頁嚴）；只能看的帳號沒有寫入按鈕。
import { actionFlags, chipMark, isVmwareAsset, revokePlatform } from '~/utils/assetOps'
const props = defineProps<{ detail: any; serial: string }>()
const emit = defineEmits<{ reload: []; collected: [job: string] }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isViewer } = useAuth()
const hw = computed<any>(() => props.detail?.hardware ?? {})
const vmware = computed(() => isVmwareAsset(hw.value, props.detail?.os_view))
const can = computed(() => actionFlags(hw.value, { vmware: vmware.value, viewer: !!isViewer.value }))
const enc = encodeURIComponent

const showOnboard = ref(false)
const showRevoke = ref(false)
const showOffline = ref(false)
const showExempt = ref(false)
const offlineReason = ref('')

// ---- 六樣收集（納管後自動／手動收集全部同一列）----
const post = ref<{ running: boolean; results: any[] } | null>(null)
const progress = ref<any>(null)
const hostNotice = ref<string | null>(null)
let timer: ReturnType<typeof setInterval> | null = null
let tries = 0
const reloaded = new Set<string>()
function stop() { if (timer) { clearInterval(timer); timer = null } }
async function poll() {
  try {
    const r = await apiFetch<any>(`/api/onboard/post-collect/${enc(props.serial)}`)
    progress.value = r?.progress ?? null
    hostNotice.value = r?.host_notice ?? null
    if (r.running) for (const d of r.progress?.done ?? []) if (d?.job && d.ok === true && !reloaded.has(d.job)) { reloaded.add(d.job); emit('collected', d.job) }
    post.value = { running: !!r.running, results: r.results ?? [] }
    if (!r.running && (r.results ?? []).length) {
      stop(); emit('reload')
      const missed = (r.results ?? []).filter((x: any) => x.ok === false || x.warn).map((x: any) => x.label)
      showToast(missed.length ? `收集完成，但 ${missed.join('、')} 沒收齊（看上方原因）` : '收集完成（六樣）', missed.length ? 'warn' : 'success', 10000)
    } else if (!r.running && tries++ > 6) { stop(); post.value = null }   // 背景沒在跑、也沒有任何紀錄：不要無限輪詢
  } catch { /* 舊版後端沒有這支：不顯示這一列，其他照常 */ }
}
function watchPost() { stop(); tries = 0; reloaded.clear(); post.value = { running: true, results: [] }; poll(); timer = setInterval(poll, 5000) }
onBeforeUnmount(stop)
onMounted(async () => {
  try {
    const r = await apiFetch<any>(`/api/onboard/post-collect/${enc(props.serial)}`)
    progress.value = r?.progress ?? null; hostNotice.value = r?.host_notice ?? null
    if (r.running) watchPost()
    else if ((r.results ?? []).length) post.value = { running: false, results: r.results }
  } catch { /* 舊版後端沒有這支：不顯示這一列 */ }
})
const collectingAll = computed(() => !!post.value?.running)
async function collectAll() {
  if (collectingAll.value || collectingSpec.value || !can.value.collect) return
  try { await apiFetch(`/api/onboard/post-collect/${enc(props.serial)}`, { method: 'POST' }); watchPost() } catch (e: any) { showToast(`收集全部沒有啟動：${e?.data?.detail ?? e?.message ?? e}`, 'error', 12000) }
}
const collectingSpec = ref(false)
async function collectSpec() {
  if (collectingSpec.value || !can.value.collect) return
  collectingSpec.value = true
  try {
    const r = await apiFetch<any>(`/api/host-spec/collect?asset_serial=${enc(props.serial)}`, { method: 'POST' })
    emit('reload')
    if (r.ok >= 1) showToast(r.ok_detail?.[0]?.notes?.length ? '規格已收集（部分項目權限不足）' : '規格已收集', r.ok_detail?.[0]?.notes?.length ? 'warn' : 'success')
    else if (r.unsupported >= 1) showToast('這台平台暫不支援收集', 'warn', 8000)
    else showToast(`收集未成功：${r.fail_detail?.[0]?.error ?? '這台連不到或非已納管'}`, 'error', 12000)
  } catch (e: any) { showToast(`收集失敗：${e?.data?.detail ?? e?.message ?? '請稍後重試'}`, 'error', 12000) } finally { collectingSpec.value = false }
}
async function unexempt() {
  try { await apiFetch('/api/onboard-exempt/remove', { method: 'POST', body: { serials: [props.serial] } }); showToast('已取消豁免，這台回到納管流程', 'success'); emit('reload') } catch (e: any) { showToast(`取消失敗：${e?.data?.detail ?? e?.message}`, 'error') }
}
function onOnboarded() { showOnboard.value = false; emit('reload'); watchPost() }
function onRevoked() { showRevoke.value = false; emit('reload') }
const exemptInfo = computed(() => hw.value.onboard_exempt ?? null)
const who = computed(() => `${hw.value.hostname || hw.value.asset_serial}（${hw.value.ip ?? '無 IP'}）`)
</script>

<template>
  <div>
    <div class="acts">
      <template v-if="vmware"><span class="note">VMware 設備不納管、不收集<InfoNote>ESXi／vCenter 的資料只從 vCenter／RVTools 取得；納管、重新納管、收集全部、收集規格都不提供（天條）。</InfoNote></span></template>
      <button v-if="can.onboard" type="button" class="b primary" data-test="act-onboard" title="系統自動進去建收集帳號" @click="showOnboard = true">一鍵納管</button>
      <button v-if="can.reonboard" type="button" class="b" data-test="act-reonboard" title="再跑一次納管腳本：補佈帳號盤點輔助程式、修回被動過的帳號或金鑰。不會重複建帳號，動手前會先備份" @click="showOnboard = true">重新納管</button>
      <button v-if="can.collect" type="button" class="b primary" data-test="act-collect-all" :disabled="collectingAll || collectingSpec" title="依序收：硬體規格、服務、軟體、帳號、OS 重建、組態檢核（跟納管後同一套）" @click="collectAll">{{ collectingAll ? '收集中…' : '收集全部' }}</button>
      <button v-if="can.collect" type="button" class="b" data-test="act-collect-spec" :disabled="collectingSpec || collectingAll" title="用收集金鑰唯讀撈 CPU／RAM／Storage／PSU 與 NIC／HBA" @click="collectSpec">{{ collectingSpec ? '收集中…' : '收集規格' }}</button>
      <button v-if="can.revoke" type="button" class="b danger" data-test="act-revoke" title="移除這台上的收集帳號、金鑰與 sudo 白名單" @click="showRevoke = true">取消納管</button>
      <AliveCheck v-if="hw.ip" :ip="hw.ip" size="normal" :can-offline="!isViewer" @offline="(r: string) => { offlineReason = r; showOffline = true }" />
      <button v-if="can.offline" type="button" class="b" data-test="act-offline" title="標記成停用／報廢／閒置（原因必填，會記進 CIA 待異動）" @click="offlineReason = ''; showOffline = true">下線</button>
      <button v-if="can.exempt" type="button" class="b" data-test="act-exempt" title="不能納管、也不是下線（客製化系統／Oracle／廠商維護）" @click="showExempt = true">非納管</button>
      <button v-if="can.unexempt" type="button" class="b" data-test="act-unexempt" :title="`非納管設備：${exemptInfo?.reason}（${exemptInfo?.created_by} 於 ${exemptInfo?.created_at} 標記）點一下取消豁免`" @click="unexempt">非納管設備 ✕</button>
      <span v-else-if="exemptInfo" class="note">非納管設備：{{ exemptInfo.reason }}</span>
      <span v-if="hw.onboard_block && !vmware" class="note">不能納管：{{ hw.onboard_block.label }}<InfoNote>{{ hw.onboard_block.reason }}</InfoNote></span>
      <NuxtLink v-if="!isViewer" class="b" :to="`/v2/asset/${enc(serial)}?edit=1`">編輯</NuxtLink>
    </div>

    <div v-if="hostNotice" class="cres"><span class="chip warn">⚠ {{ hostNotice }}</span></div>
    <div v-if="post" class="cres" data-test="post-collect">
      <template v-if="post.running">
        <span v-for="d in (progress?.done ?? [])" :key="'d' + d.label" class="chip" :class="{ bad: d.ok === false, warn: d.warn }" :title="d.message || ''">{{ chipMark(d) }} {{ d.label }}<span class="msg">{{ d.secs != null ? d.secs + ' 秒' : '' }}<template v-if="(d.ok === false || d.warn) && d.message">・{{ d.message }}</template></span></span>
        <span v-if="progress?.stale" class="chip bad">可能已中斷：「{{ progress.label }}」已跑 {{ Math.round((progress.elapsed_s ?? 0) / 60) }} 分鐘沒有進度</span>
        <span v-else-if="progress?.state === 'running'" class="chip">收集中：第 {{ progress.step }}/{{ progress.total }} 樣「{{ progress.label }}」・已跑 {{ progress.elapsed_s ?? 0 }} 秒</span>
        <span v-else class="chip">排隊中…（同時最多收 2 台）</span>
      </template>
      <template v-else>
        <span v-for="r in post.results" :key="r.label" class="chip" :class="{ bad: r.ok === false, warn: r.warn }" :title="r.message">{{ chipMark(r) }} {{ r.label }}<span class="msg">{{ r.secs != null ? `${r.secs} 秒・` : '' }}{{ r.message }}</span></span>
        <button type="button" class="lnk" @click="post = null">關閉</button>
      </template>
    </div>

    <OfflineMarkModal v-if="showOffline" :serials="[hw.asset_serial]" :who="who" :default-reason="offlineReason" @close="showOffline = false" @done="showOffline = false; emit('reload')" />
    <ExemptModal v-if="showExempt" :serials="[hw.asset_serial]" :who="`${hw.hostname || hw.asset_serial}`" @close="showExempt = false" @done="showExempt = false; emit('reload')" />
    <OnboardModal v-if="showOnboard && hw.ip && (can.onboard || can.reonboard)" :ip="hw.ip" :os-guess="hw.os" @done="onOnboarded" @close="showOnboard = false" />
    <RevokeModal v-if="showRevoke && hw.ip" :ip="hw.ip" :platform="revokePlatform(hw.os)" @done="onRevoked" @close="showRevoke = false" />
  </div>
</template>

<style scoped>
.acts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; }
.b { font-family: inherit; font-size: 13px; padding: 5px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; text-decoration: none; }
.b.primary { background: var(--brand); border-color: var(--brand); color: var(--card); }
.b.primary:hover:not(:disabled) { background: var(--brand-hover); }
.b.danger { border-color: var(--bad); color: var(--bad); }
.b:disabled { opacity: .55; cursor: default; }
.note { font-size: 12px; color: var(--ink-aux); display: inline-flex; gap: 4px; align-items: center; }
.cres { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 8px 0; padding: 8px 12px; border-radius: 8px; background: var(--sub); font-size: 12.5px; }
.chip { display: inline-flex; align-items: baseline; gap: 5px; padding: 2px 9px; border-radius: 10px; background: var(--mint); color: var(--brand-dark); }
.chip.bad, .chip.warn { background: var(--warn-soft); color: var(--warn-text); }
.msg { font-size: 11px; opacity: .85; }
.lnk { border: 0; background: none; padding: 0; font: inherit; color: var(--link); cursor: pointer; }
</style>
