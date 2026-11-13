<script setup lang="ts">
// 共用 IP／VIP 待核清單（Codex 與 CC 共同設計）：只是「提示＋人工確認」，不併機、不改任何台數。
// 三個分頁：待核／已確認的共用服務 IP／已排除（不是服務 IP、資料錯誤）。判定要管理員；每筆都能復原。
interface Sig { code: string; label: string; detail: string }
interface Mach { key: string; name: string; serial: string; model: string | null; room: string | null; ips: string[] }
interface Review { id: number; verdict: string; relation_type: string | null; note: string | null; decided_by: string; decided_at: string }
interface Item { item_key: string; kind: string; ip: string | null; ips: string[]; signals: Sig[]; machines: Mach[]; score: number; review?: Review; verdict_label?: string; reopened?: boolean; reopened_note?: string }
interface Out { pending: Item[]; confirmed: Item[]; dismissed: Item[]; counts: Record<string, number>; registered_vip?: number; relation_types: string[]; can_edit: boolean }

import { filterBatchRows, overLimitText, previewLines, selectAllFiltered, selectedKeys, type BatchFilter, type Preview } from '~/utils/ipBatch'

const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const data = ref<Out | null>(null)
const loadError = ref('')
const sub = ref<'pending' | 'confirmed' | 'dismissed' | 'batches'>('pending')
const err = (e: any) => e?.data?.detail ?? e?.message ?? '未知錯誤'

async function load() {
  loadError.value = ''
  try {
    data.value = await apiFetch<Out>('/api/ip-relations')
  } catch (e: any) {
    loadError.value = err(e)
  }
}
await load()

const rows = computed(() => (sub.value === 'batches' ? [] : (data.value?.[sub.value] ?? [])).map((it, i) => ({
  ...it, __no: i + 1, who: it.ip ?? (it.ips || []).join('、'),
  why: it.signals.map(s => `${s.label}（${s.detail}）`).join('；'),
  mach: `${it.machines.length} 台：${it.machines.map(m => m.name).slice(0, 5).join('、')}${it.machines.length > 5 ? '…' : ''}`,
  state: it.reopened ? (it.reopened_note ?? '') : (it.verdict_label ? `${it.verdict_label}${it.review?.relation_type ? '（' + it.review.relation_type + '）' : ''}${it.review?.note ? '：' + it.review.note : ''}` : ''),
})))

async function judge(it: Item, verdict: string) {
  let relation_type: string | null = null
  if (verdict === 'shared_service') {
    const types = data.value?.relation_types ?? []
    const pick = window.prompt(`這是哪一種共用服務 IP？\n輸入：${types.join('／')}`, types[0] ?? '')
    if (pick === null) return
    relation_type = pick.trim()
  }
  let note: string | null = null
  if (verdict === 'data_error') {
    note = window.prompt('資料錯在哪、要誰補？（一句話）', '')
    if (note === null) return
  } else if (verdict === 'shared_service') {
    note = window.prompt('備註（可空白）：例如對應哪個叢集或服務', '')
    if (note === null) return
  }
  try {
    await guardedFetch('/api/ip-relations/review', { method: 'POST', body: { item_key: it.item_key, verdict, relation_type, note } })
    showToast('已記錄判定（可在「已確認」或「已排除」復原）', 'success')
    await load()
  } catch (e: any) {
    showToast(`判定失敗：${err(e)}`, 'error', 8000)
  }
}
async function undo(it: Item) {
  if (!it.review) return
  const why = window.prompt('復原這筆判定（原因，可空白）：', '')
  if (why === null) return
  try {
    await guardedFetch(`/api/ip-relations/${it.review.id}/undo`, { method: 'POST', body: { note: why || null } })
    showToast('已復原，回到待核', 'success')
    await load()
  } catch (e: any) {
    showToast(`復原失敗：${err(e)}`, 'error', 8000)
  }
}
const SUBS = computed(() => [
  { k: 'pending', label: `待核 ${data.value?.counts.pending ?? 0}` },
  { k: 'confirmed', label: `已確認 ${data.value?.counts.confirmed ?? 0}` },
  { k: 'dismissed', label: `已排除 ${data.value?.counts.dismissed ?? 0}` },
  { k: 'batches', label: `批次紀錄 ${batches.value.length}` }])

// ---- 批次判定（管理員）：篩選 → 勾選（可全選篩選結果）→ 預覽 → 套用 → 可整批復原 ----
const SIGNALS = [['', '全部原因'], ['shared_ip', '同 IP 多台'], ['vip_shared', 'VIP 多台'], ['lb_model', 'F5／LB 型號'], ['name_keyword', '名稱關鍵字']]
const filt = ref<BatchFilter>({ text: '', signal: '' })
const filtered = computed(() => (sub.value === 'pending' ? filterBatchRows(rows.value as any[], filt.value) : rows.value) as typeof rows.value)
const selected = ref<Record<string, boolean>>({})
const picked = computed(() => selectedKeys(selected.value, filtered.value))     // 只算「目前畫面看得到且有勾」的，隱藏的舊勾選不送出
const limitMsg = computed(() => overLimitText(picked.value.length))
const filterText = computed(() => `${[filt.value.signal ? SIGNALS.find(x => x[0] === filt.value.signal)?.[1] : '', filt.value.text].filter(Boolean).join('＋') || '全部'}（實際 ${picked.value.length} 筆）`)
const hint = ref('')
function pickAll() { selected.value = selectAllFiltered(filtered.value, selected.value) }
const bv = ref({ verdict: 'not_service', relation_type: '', note: '', overwrite: false })
const pv = ref<(Preview & { digest: string; verdict_label: string; sample: { item_key: string; who: string; machines: string[] }[] }) | null>(null)
const lastBatch = ref<string>('')
const busy = ref(false)
const batches = ref<any[]>([])
watch(sub, () => { selected.value = {}; pv.value = null; hint.value = '' })
watch(filt, () => {      // 換篩選：清掉勾選（不保留看不到的），請重新勾
  if (Object.values(selected.value).some(Boolean)) hint.value = '篩選條件已變更，原本的勾選已清除，請重新勾選'
  selected.value = {}
  pv.value = null
}, { deep: true })
watch(bv, () => { pv.value = null }, { deep: true })
watch(selected, () => { pv.value = null })
const batchBody = () => ({ item_keys: picked.value, verdict: bv.value.verdict, relation_type: bv.value.verdict === 'shared_service' ? bv.value.relation_type : null, note: bv.value.note || null, overwrite: bv.value.overwrite })
async function loadBatches() {
  try { batches.value = (await apiFetch<{ batches: any[] }>('/api/ip-relations/batches')).batches } catch (e: any) { showToast(`批次紀錄載入失敗：${err(e)}`, 'error', 8000) }
}
await loadBatches()
async function previewBatch() {
  if (limitMsg.value || !picked.value.length) return
  busy.value = true
  try {
    pv.value = await apiFetch('/api/ip-relations/batch/preview', { method: 'POST', body: batchBody() })
  } catch (e: any) {
    pv.value = null
    showToast(`預覽失敗：${err(e)}`, 'error', 8000)
  } finally { busy.value = false }
}
async function applyBatch() {
  if (!pv.value) return
  busy.value = true
  try {
    const r: any = await guardedFetch('/api/ip-relations/batch/apply', { method: 'POST', body: { ...batchBody(), digest: pv.value.digest, filter_text: filterText.value } })
    lastBatch.value = r.batch_id
    showToast(`已套用 ${r.applied} 筆（批次 ${r.batch_id}）`, 'success')
    pv.value = null
    selected.value = {}
    await load()
    await loadBatches()
  } catch (e: any) {
    showToast(`套用失敗：${err(e)}`, 'error', 9000)
    pv.value = null
  } finally { busy.value = false }
}
async function undoBatch(id: string, skip = false) {
  const why = skip ? '' : window.prompt(`整批復原 ${id}（原因，可空白）：`, '')
  if (why === null) return
  try {
    const r: any = await guardedFetch(`/api/ip-relations/batch/${id}/undo`, { method: 'POST', body: { note: why || null, skip_conflicts: skip } })
    showToast(`已復原 ${r.restored} 筆${r.conflicts?.length ? `，${r.conflicts.length} 筆有衝突未動` : ''}`, 'success')
    if (id === lastBatch.value) lastBatch.value = ''
    await load()
    await loadBatches()
  } catch (e: any) {
    const conf = e?.data?.detail?.conflicts
    if (conf?.length) {
      const names = conf.slice(0, 8).map((c: any) => c.item_key).join('、')
      if (window.confirm(`這些項目在批次之後被改過，不會被蓋掉：\n${names}${conf.length > 8 ? '…' : ''}\n共 ${conf.length} 筆。\n要略過它們、只復原其餘的嗎？`)) await undoBatch(id, true)
    } else {
      showToast(`復原失敗：${e?.data?.detail?.message ?? err(e)}`, 'error', 8000)
    }
  }
}
const batchRows = computed(() => batches.value.map((b, i) => ({
  ...b, __no: i + 1, state: b.undone_at ? `已復原（${b.undone_by ?? ''} ${b.undone_at}）` : '生效中',
  what: `${b.verdict_label}${b.relation_type ? '（' + b.relation_type + '）' : ''}${b.note ? '：' + b.note : ''}${b.overwrite ? '・含覆蓋' : ''}`,
})))
</script>

<template>
  <div>
    <p v-if="loadError" class="dim">{{ loadError }}</p>
    <template v-else-if="data">
      <div class="subs">
        <button v-for="s in SUBS" :key="s.k" type="button" class="chip" :class="{ on: sub === s.k }" @click="sub = s.k as any">{{ s.label }}</button>
        <InfoNote>共用 IP、虛擬 IP（VIP）、F5 這類設備，可能讓一台看起來像好幾台，或好幾台看起來像一台。這裡只列出「值得看一眼」的項目並寫出原因，<b>不會自動合併、也不會改任何台數</b>。你判定為「共用服務 IP」時要選類型（RAC SCAN、資料庫 VIP、HA 浮動 IP、負載平衡 VIP…）；標過「不是服務 IP」或「資料錯誤」就不再出現，之後涉及的機器有變動才會重新出現。判定需要管理員，可復原。「BIG IP/VIP」欄單獨有值（一台一個 VIP）只是登記資料，不列入待核；<b>同一個 VIP 掛在多台</b>才會列入。</InfoNote>
        <span v-if="data.registered_vip" class="dim">另有 {{ data.registered_vip }} 台已登記 VIP（單獨出現，不需判定）</span>
      </div>
      <template v-if="sub === 'batches'">
        <V2Table :rows="batchRows" prefix="B" :max="50" row-key="batch_id" :cols="[
          { k: 'batch_id', label: '批次編號', mono: true },
          { k: 'created_at', label: '時間', mono: true },
          { k: 'created_by', label: '操作者' },
          { k: 'what', label: '判定' },
          { k: 'item_count', label: '筆數', mono: true },
          { k: 'filter_text', label: '篩選條件' },
          { k: 'state', label: '狀態' },
          { k: 'act', label: '處理' }]">
          <template #cell-act="{ row }">
            <span v-if="!data.can_edit" class="dim">需管理員</span>
            <button v-else-if="!row.undone_at" type="button" class="chip" @click="undoBatch(row.batch_id)">復原整批</button>
          </template>
        </V2Table>
      </template>
      <template v-else>
        <div v-if="sub === 'pending'" class="bar">
          <input v-model="filt.text" class="inp" placeholder="篩選：IP、原因、主機">
          <select v-model="filt.signal" class="inp"><option v-for="x in SIGNALS" :key="x[0]" :value="x[0]">{{ x[1] }}</option></select>
          <span class="dim">篩選結果 {{ filtered.length }} 筆</span>
          <template v-if="data.can_edit">
            <button type="button" class="chip" :disabled="!filtered.length" @click="pickAll">全選目前篩選結果（{{ filtered.length }} 筆）</button>
            <button type="button" class="chip" :disabled="!picked.length" @click="selected = {}; hint = ''">清除勾選</button>
            <b>已勾選 {{ picked.length }} 筆</b>
            <span v-if="hint" class="dim">{{ hint }}</span>
            <span v-if="limitMsg" class="bad">{{ limitMsg }}</span>
          </template>
        </div>
        <div v-if="sub === 'pending' && data.can_edit && picked.length" class="batch">
          <b>批次判定</b>
          <select v-model="bv.verdict" class="inp">
            <option value="not_service">不是服務 IP</option>
            <option value="shared_service">共用服務 IP</option>
            <option value="data_error">資料錯誤</option>
          </select>
          <select v-if="bv.verdict === 'shared_service'" v-model="bv.relation_type" class="inp">
            <option value="">選關係類型</option>
            <option v-for="t in data.relation_types" :key="t" :value="t">{{ t }}</option>
          </select>
          <input v-model="bv.note" class="inp" :placeholder="bv.verdict === 'data_error' ? '說明（必填）' : '備註（可空白）'">
          <label class="dim"><input v-model="bv.overwrite" type="checkbox"> 覆蓋已判定</label>
          <button type="button" class="chip" :disabled="busy || !!limitMsg" @click="previewBatch">預覽</button>
          <InfoNote>先預覽影響範圍，確認後才套用；預覽之後清單若有變動會拒絕，需重新預覽。單次最多 500 筆，全成功或全不寫。預設不覆蓋已判定的項目。套用後可在「批次紀錄」復原整批。</InfoNote>
        </div>
        <div v-if="pv" class="pv">
          <div v-for="(l, i) in previewLines(pv)" :key="i">{{ l }}</div>
          <div class="dim">判定：{{ pv.verdict_label }}；範例：{{ pv.sample.slice(0, 5).map(x => x.who || x.item_key).join('、') }}{{ pv.will_apply > 5 ? '…' : '' }}</div>
          <div>
            <button type="button" class="chip on" :disabled="busy || !pv.will_apply" @click="applyBatch">套用（{{ pv.will_apply }} 筆）</button>
            <button type="button" class="chip" @click="pv = null">取消</button>
          </div>
        </div>
        <div v-if="lastBatch" class="pv">
          <div>已套用批次 <b>{{ lastBatch }}</b></div>
          <button v-if="data.can_edit" type="button" class="chip" @click="undoBatch(lastBatch)">復原整批</button>
        </div>
        <V2Table v-model:selected="selected" :selectable="sub === 'pending' && data.can_edit" :rows="filtered" prefix="I" :max="100" row-key="item_key" :cols="[
          { k: 'who', label: 'IP／對象', mono: true },
          { k: 'why', label: '為什麼要看' },
          { k: 'mach', label: '涉及主機' },
          { k: 'state', label: sub === 'pending' ? '說明' : '判定' },
          { k: 'act', label: '處理' }]">
          <template #cell-act="{ row }">
            <span v-if="!data.can_edit" class="dim">需管理員</span>
            <template v-else-if="sub === 'pending'">
              <button type="button" class="chip" @click="judge(row, 'shared_service')">共用服務 IP</button>
              <button type="button" class="chip" @click="judge(row, 'not_service')">不是服務 IP</button>
              <button type="button" class="chip" @click="judge(row, 'data_error')">資料錯誤</button>
            </template>
            <button v-else type="button" class="chip" @click="undo(row)">復原</button>
          </template>
        </V2Table>
      </template>
    </template>
    <p v-else class="dim">載入中…</p>
  </div>
</template>

<style scoped>
.subs { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
.chip { border: 1px solid var(--border); background: var(--card); border-radius: 999px; padding: 4px 12px; cursor: pointer; font-size: 13px; margin-right: 6px; }
.chip.on { background: var(--brand); color: #fff; border-color: var(--brand); }
.dim { color: var(--muted); }
.bad { color: var(--bad); }
.bar, .batch { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.batch { padding: 8px 10px; background: var(--sub); border: 1px solid var(--border); border-radius: var(--radius); }
.pv { padding: 8px 10px; margin-bottom: 8px; border: 1px solid var(--border-strong); border-radius: var(--radius); font-size: 13px; display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.inp { font-family: inherit; font-size: 13px; padding: 3px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.chip:disabled { opacity: .5; cursor: default; }
</style>
