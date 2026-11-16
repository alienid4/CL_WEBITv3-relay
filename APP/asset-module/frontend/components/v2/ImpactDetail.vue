<script setup lang="ts">
// 4.1「影響範圍查詢」頁籤的明細（2.043.10，V62：舊版 /blast 剩下的功能搬過來）：
// 匯出 CSV（含通知清單）、存證快照與歷次快照（查看／下載／回到即時查詢）、連線推論沒採用的明細、
// 受影響業務系統與通知對象清單。全部沿用舊頁同一套 /api/blast/*（blast_radius.py／observed_deps.py），這裡不另算。
import { impactScale, notifySystems, snapshotBelongs, snapshotObsRows } from '~/utils/impactView'
const props = withDefaults(defineProps<{ nodeId: string; label: string; result: any; mode?: string }>(), { mode: 'incident' })
const emit = defineEmits<{ live: [] }>()
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isViewer } = useAuth()
const route = useRoute()
const router = useRouter()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
// 下載連結要給完整網址，表格裡的連結才會當成檔案下載、不會被當成站內頁面（B5）
const origin = useRequestURL().origin
const absApi = (path: string) => (/^https?:/.test(apiBase) ? `${apiBase}${path}` : `${origin}${apiBase}${path}`)
const errMsg = (e: any) => e?.data?.detail ?? e?.message ?? '未知錯誤'
const sub = computed(() => (['sys', 'notify', 'snap', 'obs', 'deps'].includes(String(route.query.isub)) ? String(route.query.isub) : 'sys'))
const go = (v: string) => router.replace({ query: { ...route.query, isub: v } })

// 目前看的是即時查詢還是某一份快照（快照＝當時算出來的結果，圖會變，所以要能回頭看）
const viewing = ref<any>(null)
const res = computed(() => viewing.value?.result ?? props.result)
const summary = computed(() => res.value?.summary ?? null)

// 受影響業務系統（每個系統一列，負責人合併）
const sysRows = computed(() => (summary.value?.by_biz_system ?? []).map((s: any, i: number) => {
  const owners = [...new Set((s.assets ?? []).flatMap((a: any) => (a.owners ?? []).map((o: any) => o.name)).filter(Boolean))]
  return { ...s, __no: i + 1, n: (s.assets ?? []).length, owners_t: owners.join('、') || '未登錄', sev: s.severity || '—' }
}))
// B2：通知對象加「影響系統」——為哪幾套受影響系統找他
const ownerSys = computed(() => notifySystems(summary.value))
const notifyRows = computed(() => (summary.value?.notify ?? []).map((o: any, i: number) => ({ ...o, __no: i + 1, phone_t: o.phone || '—', proxy_t: [o.proxy, o.proxy_phone].filter(Boolean).join(' ') || '—', role_t: o.role || '—', sys_t: (ownerSys.value[o.name] ?? []).join('、') || '—' })))
const scale = computed(() => impactScale(res.value))
const unknownN = computed(() => (summary.value?.unknown_owner ?? []).length)

// CSV：即時查詢走 /api/blast/impact/csv，看快照時走那份快照的 CSV（內容＝畫面上那一份）
function csv() {
  const url = viewing.value
    ? `${apiBase}/api/blast/snapshot/${viewing.value.id}/csv`
    : `${apiBase}/api/blast/impact/csv?` + new URLSearchParams({ node_id: props.nodeId, depth: '6', mode: props.mode }).toString()
  window.open(url, '_blank')
}

// 存證快照
const snaps = ref<any[]>([])
const snapErr = ref('')
async function loadSnaps() {
  try { snaps.value = await apiFetch<any[]>('/api/blast/snapshots', { query: { node_id: props.nodeId } }); snapErr.value = '' } catch (e: any) { snapErr.value = `歷次快照載入失敗：${errMsg(e)}` }
}
watch(() => props.nodeId, () => { loadSnaps() }, { immediate: true })
const reason = ref('')
const saving = ref(false)
async function saveSnap() {
  saving.value = true
  try {
    await guardedFetch('/api/blast/snapshot', { method: 'POST', body: { node_id: props.nodeId, reason: reason.value.trim() || null } })
    showToast('已存證快照', 'success'); reason.value = ''; await loadSnaps()
  } catch (e: any) { showToast(`存快照失敗：${errMsg(e)}`, 'error') } finally { saving.value = false }
}
// 點快照時間＝看那一份（網址帶 ?snap=，可分享；回到即時查詢＝拿掉 snap）
const snapLink = (id: number) => ({ query: { ...route.query, isub: 'sys', snap: String(id) } }) as any
// 監看「快照編號＋目前查詢對象」：同一個 snap、改查別的節點時也要重新驗證歸屬（2.043.12，GPT 複核 A3）
watch(() => [route.query.snap, props.nodeId], async ([v]) => {
  if (!v) { viewing.value = null; return }
  try {
    const snap = await apiFetch<any>(`/api/blast/snapshot/${encodeURIComponent(String(v))}`)
    // A3：網址上的快照不屬於目前查詢對象 → 不顯示、拿掉參數並提示（避免上面查 A、明細與 CSV 卻是 B）
    if (!snapshotBelongs(snap, props.nodeId)) {
      viewing.value = null
      showToast(`快照 #${v} 不是「${props.label}」的快照，已改顯示目前查詢結果`, 'warn')
      router.replace({ query: { ...route.query, snap: undefined } })
      return
    }
    viewing.value = snap
  } catch (e: any) { viewing.value = null; showToast(`讀取快照失敗：${errMsg(e)}`, 'error') }
}, { immediate: true })
function backLive() { router.replace({ query: { ...route.query, snap: undefined } }); emit('live') }
const snapRows = computed(() => snaps.value.map((s: any, i: number) => ({ ...s, __no: i + 1, reason_t: s.reason || '—', at: (s.asked_at || '').slice(0, 16), csv: '下載 CSV' })))

// 連線推論沒採用（自動判定管理連線／模稜兩可／排除清單）：數字點下去看是哪幾列連線
// 2.066.x：畫面上這塊叫「資料來源」（診斷，不是操作），排在最後
const OBS: Record<string, string> = { admin_ssh_auto: '管理連線', ambiguous: '一個 IP 對到多個系統', excluded: '排除清單' }
const obsStats = computed(() => res.value?.observed?.stats ?? {})
const bucket = computed(() => (Object.keys(OBS).includes(String(route.query.ob)) ? String(route.query.ob) : ''))
const obs = ref<any>(null)
const obsErr = ref('')
watch([bucket, viewing], async ([b]) => {
  obs.value = null
  if (!b || viewing.value) return          // A2：看快照時不查現在的連線列，改用快照裡存的那一份（snapObs）
  try { obs.value = await apiFetch('/api/blast/observed-rows', { query: { bucket: b } }); obsErr.value = '' } catch (e: any) { obsErr.value = `載入失敗：${errMsg(e)}` }
}, { immediate: true })
const obsRows = computed(() => (obs.value?.rows ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, dir: r.direction === 'inbound' ? '進' : '出', lp: r.local_port || '—', pp: r.peer_port || '—', last: r.last_seen || '—' })))
const snapObs = computed(() => (viewing.value && bucket.value ? snapshotObsRows(viewing.value.result, bucket.value) : null))
// mode=probe（舊 /blast 的「查肇因」）：另列它依賴誰（上游）
// 「它依賴誰」的節點顯示名稱（不顯示 hw:／bizsys: 這類內部代碼）：打開這個子頁籤才向 /api/blast/graph 要一次名稱
const EDGE: Record<string, string> = { depends_on: '依賴', runs_on: '跑在', stores_on: '存放在', located_in: '位於', member_of: '屬於', observed_conn: '連線推論' }
const NTYPE: Record<string, string> = { host: '主機', business_service: '業務系統', system: '系統', esxi: 'ESXi', cluster: '叢集', datastore: '儲存', rack: '機櫃', segment: '網段' }
const nodeNames = ref<Record<string, { label: string; type: string }>>({})
const namesFor = ref('')
watch([sub, () => props.nodeId], async ([s, nid]) => {
  if (s !== 'deps' || namesFor.value === nid) return
  try {
    const g = await apiFetch<{ nodes?: { data: { id: string; label: string; type: string } }[] }>('/api/blast/graph', { query: { node_id: nid, depth: 6, direction: 'both' } })
    nodeNames.value = Object.fromEntries((g.nodes ?? []).map(n => [n.data.id, { label: n.data.label, type: n.data.type }]))
    namesFor.value = String(nid)
  } catch (e: any) { showToast(`節點名稱載入失敗：${errMsg(e)}`, 'warn') }     // 讀不到就顯示去掉前綴的代碼，不擋表格
}, { immediate: true })
const plainId = (id: string) => String(id || '').replace(/^[a-z_]+:/, '')
const depRows = computed(() => (res.value?.dependencies ?? []).map((d: any, i: number) => {
  const n = nodeNames.value[d.node_id]
  return { ...d, __no: i + 1, name: n?.label || plainId(d.node_id), ntype: NTYPE[n?.type || ''] || '', edge: EDGE[d.edge_type] || d.edge_type || '—' }
}))
</script>

<template>
  <div class="box" data-testid="impact-detail">
    <div class="bar">
      <span class="scale" data-testid="impact-scale">受影響節點 <b>{{ scale.nodes }}</b> 個：主機 <b>{{ scale.hosts }}</b> 台、業務系統 <b>{{ scale.systems }}</b> 套、通知 <b>{{ scale.notify }}</b> 人</span>
      <b v-if="viewing">快照 {{ (viewing.asked_at || '').slice(0, 16) }}（{{ viewing.asked_by }}）</b>
      <button v-if="viewing" type="button" class="ibtn2" @click="backLive">回到即時查詢</button>
      <button type="button" class="ibtn2" @click="csv">匯出 CSV</button>
      <form v-if="!isViewer && !viewing" class="snapf" @submit.prevent="saveSnap">
        <input v-model="reason" class="in" placeholder="停機原因／變更單號（選填）" aria-label="快照原因">
        <button type="submit" class="ibtn2" :disabled="saving">{{ saving ? '存證中…' : '存快照' }}</button>
      </form>
      <InfoNote>CSV 含受影響系統、主機與通知清單。快照＝存下這次的結果，日後查得到當初的評估。</InfoNote>
    </div>
    <div class="chips">
      <button type="button" class="chip" :class="{ on: sub === 'sys' }" @click="go('sys')">受影響業務系統 {{ sysRows.length }}</button>
      <button type="button" class="chip" :class="{ on: sub === 'notify' }" @click="go('notify')">通知對象 {{ notifyRows.length }}<span v-if="unknownN" class="tg bad">查不到負責人 {{ unknownN }}</span></button>
      <button v-if="mode === 'probe'" type="button" class="chip" :class="{ on: sub === 'deps' }" @click="go('deps')">它依賴誰 {{ depRows.length }}</button>
      <button type="button" class="chip" :class="{ on: sub === 'snap' }" @click="go('snap')">歷次快照 {{ snaps.length }}</button>
      <button type="button" class="chip" :class="{ on: sub === 'obs' }" data-testid="impact-src" @click="go('obs')">資料來源</button>
    </div>
    <V2Table v-if="sub === 'sys'" :rows="sysRows" prefix="S" empty="沒有受影響的業務系統" export-name="受影響業務系統"
             :cols="[{ k: 'api_id', label: '代號', mono: true, link: (r: any) => `/v2/system/${encodeURIComponent(r.api_id)}` }, { k: 'name', label: '業務系統' }, { k: 'sev', label: '等級' }, { k: 'n', label: '受影響主機（台）', mono: true }, { k: 'owners_t', label: '負責人' }]" />
    <V2Table v-else-if="sub === 'notify'" :rows="notifyRows" prefix="N" empty="沒有通知對象（負責人未登錄）" export-name="通知對象"
             :cols="[{ k: 'name', label: '姓名' }, { k: 'sys_t', label: '影響系統', note: '為哪幾套受影響的業務系統找他（從受影響主機的負責人反查）。' }, { k: 'role_t', label: '角色' }, { k: 'phone_t', label: '電話', mono: true }, { k: 'proxy_t', label: '代理人' }]" />
    <V2Table v-else-if="sub === 'deps'" :rows="depRows" prefix="D" empty="沒有上游" export-name="它依賴誰"
             :cols="[{ k: 'name', label: '節點' }, { k: 'ntype', label: '類型' }, { k: 'depth', label: '層數', mono: true }, { k: 'edge', label: '關聯' }, { k: 'confidence', label: '可信度' }]" />
    <template v-else-if="sub === 'snap'">
      <p v-if="snapErr" class="err">{{ snapErr }}</p>
      <V2Table v-else :rows="snapRows" prefix="K" empty="這個查詢對象還沒有存過快照"
               :cols="[{ k: 'at', label: '時間', mono: true, link: (r: any) => snapLink(r.id) }, { k: 'asked_by', label: '查詢人' }, { k: 'reason_t', label: '原因' }, { k: 'csv', label: '下載', link: (r: any) => absApi(`/api/blast/snapshot/${r.id}/csv`) }]" />
    </template>
    <template v-else>
      <div class="chips">
        <NuxtLink v-for="(lab, k) in OBS" :key="k" class="chip" :class="{ on: bucket === k }" :to="{ query: { ...route.query, isub: 'obs', ob: bucket === k ? undefined : k } }">{{ lab }} {{ obsStats[k] || 0 }}</NuxtLink>
        <InfoNote>這些連線沒拿來推上下游。管理連線＝同一來源用 SSH 連進太多系統（跳板、自動化）；一個 IP 對到多個系統＝不猜是哪個；排除清單在系統設定。數字是連線列數。</InfoNote>
      </div>
      <template v-if="viewing && bucket">
        <p class="muted">快照 {{ (viewing.asked_at || '').slice(0, 16) }}・依來源 IP</p>
        <p v-if="snapObs === null" class="muted">快照只存了筆數 {{ obsStats[bucket] || 0 }}，看明細請回到即時查詢。</p>
        <V2Table v-else :rows="snapObs" prefix="B" empty="快照裡沒有這一類" :export-name="OBS[bucket]"
                 :cols="[{ k: 'ip', label: '來源 IP', mono: true }, { k: 'rows', label: '連線列數', mono: true }, { k: 'apis', label: '對到的系統' }, { k: 'extra', label: '說明' }]" />
      </template>
      <p v-else-if="obsErr" class="err">{{ obsErr }}</p>
      <V2Table v-else-if="bucket" :rows="obsRows" prefix="B" :empty="obs ? '沒有連線列' : '載入中…'" :export-name="OBS[bucket]"
               :cols="[{ k: 'host_ip', label: '收集的主機', mono: true }, { k: 'dir', label: '方向' }, { k: 'lp', label: '本機埠', mono: true }, { k: 'peer_ip', label: '對方', mono: true }, { k: 'pp', label: '對方埠', mono: true }, { k: 'count', label: '觀察次數', mono: true }, { k: 'last', label: '最後看到', mono: true }]" />
      <p v-else class="muted">點上面一類看是哪幾列連線。</p>
    </template>
  </div>
</template>

<style scoped>
.box { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.bar, .chips { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.snapf { display: flex; gap: 6px; align-items: center; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; width: 240px; max-width: 100%; }
.ibtn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; text-decoration: none; }
.chip { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 999px; padding: 3px 10px; font-size: 13px; font-family: inherit; cursor: pointer; text-decoration: none; }
.chip.on { background: var(--mint); border-color: var(--brand); color: var(--brand-dark); }
.snapacts { display: flex; gap: 12px; flex-wrap: wrap; font-size: 13px; }
.lb { background: none; border: none; color: var(--link); cursor: pointer; font-family: inherit; font-size: 13px; padding: 0 4px; text-decoration: none; }
.err { color: var(--bad); font-size: 13px; }
.scale { font-size: 13.5px; }
.muted { color: var(--muted); font-size: 13px; }
.tg { margin-left: 6px; padding: 0 7px; border-radius: 10px; font-size: 12px; }
.tg.bad { background: var(--bad-soft); color: var(--bad); }
</style>
