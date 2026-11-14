<script setup lang="ts">
// 5.1 機房平面圖「差異待辦」頁籤（2.043.17，R5–R8；規格附錄 A2 ⑧＋⑩）。
// 狀態 chip（筆數＝點下去的列數，同一份清單）、可排序／匯出／項次固定（D＋編號）；點「處理」在下方展開詳細、可用的動作與稽核紀錄。
// 管理員可勾選批次：先預覽（伺服器記下這一批、給一個 token），送出只帶 token，資料變了就整批不做。網址帶 ds（狀態）、di（展開哪一筆）。
// 2.043.18（GPT B2／B3／A8／B5）：詳細改成右側抽屜（點「處理」就在畫面上打開，不跑到表格最下面）；會改機櫃圖／資產的動作送出前先列確認摘要；
// 預覽也走寫入守門（要管理密碼時會跳出輸入框）；失敗時寫下一步要做什麼。
import { ACTION_NEEDS, STATUS, actProblem, actSummary } from '~/utils/rackDiff'
const props = defineProps<{ room: string }>()
const emit = defineEmits<{ changed: [] }>()
const route = useRoute()
const router = useRouter()
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isViewer } = useAuth()
const data = ref<any>(null)
const err = ref('')
const errText = (e: any) => e?.data?.detail?.message ?? e?.data?.detail ?? e?.message ?? '未知錯誤'
async function load() {
  try { data.value = await apiFetch('/api/racks/diff', { query: { room: props.room } }); err.value = '' } catch (e: any) { err.value = `差異待辦載入失敗：${errText(e)}` }
}
watch(() => props.room, load, { immediate: true })
const ds = computed(() => (typeof route.query.ds === 'string' && route.query.ds in STATUS ? route.query.ds : ''))
const di = computed(() => Number(route.query.di) || null)
const setQ = (q: Record<string, any>) => router.replace({ query: { ...route.query, ...q } })
const canEdit = computed(() => !!data.value?.can_edit && !isViewer.value)
const rackUrl = (r: any) => `/v2/datacenter?room=${encodeURIComponent(r.room)}&tab=front&rack=${encodeURIComponent(r.rack)}`
const all = computed(() => (data.value?.items ?? []).map((x: any) => ({ ...x, __no: x.id })))
const rows = computed(() => all.value.filter((x: any) => !ds.value || x.status === ds.value))
const COLS = [
  { k: 'status_label', label: '狀態' }, { k: 'rack', label: '機櫃', link: rackUrl }, { k: 'u_text', label: 'U', mono: true },
  { k: 'subject_label', label: '對象' }, { k: 'who', label: '設備／Excel 文字' }, { k: 'reason_label', label: '原因' },
  { k: 'note', label: '備註' }, { k: 'created_by', label: '回報人' }, { k: 'created_at', label: '回報時間', mono: true },
  { k: 'close_label', label: '處理' }, { k: 'id', label: '詳細', text: () => '處理', link: (r: any) => `/v2/datacenter?room=${encodeURIComponent(r.room)}&tab=diff${ds.value ? '&ds=' + ds.value : ''}&di=${r.id}` },
]
// ---- 單筆詳細與動作
const item = ref<any>(null)
async function loadItem() {
  if (!di.value) { item.value = null; return }
  try { item.value = await apiFetch(`/api/racks/diff/${di.value}`) } catch (e: any) { item.value = null; showToast(`這筆待辦載入失敗：${errText(e)}`, 'error') }
}
watch(di, loadItem, { immediate: true })
const f = reactive({ action: '', note: '', target_rack: '', target_u_start: '', target_u_end: '', bind_q: '', approver: '', basis: '' })
watch(item, () => Object.assign(f, { action: '', note: '', target_rack: '', target_u_start: '', target_u_end: '', bind_q: '', approver: '', basis: '' }))
const need = computed(() => ACTION_NEEDS[f.action] ?? [])
const problem = computed(() => (f.action ? actProblem(f.action, f, item.value ?? {}) : '請選動作'))
const busy = ref(false)
const summary = computed(() => (item.value && f.action ? actSummary(f.action, item.value, f) : null))
const confirming = ref(false)
watch(() => f.action, () => { confirming.value = false })
function closeItem() { setQ({ di: undefined }) }
function askAct() {
  if (problem.value || !item.value) return
  if (summary.value && !confirming.value) { confirming.value = true; return }     // 會改正式資料：先看摘要再確認
  doAct()
}
async function doAct() {
  if (problem.value || !item.value) return
  confirming.value = false
  busy.value = true
  try {
    const body: Record<string, any> = { action: f.action, note: f.note || null }
    if (need.value.includes('target') && f.target_rack) Object.assign(body, { target_rack: f.target_rack, target_u_start: Number(f.target_u_start), target_u_end: Number(f.target_u_end || f.target_u_start) })
    if (need.value.includes('bind') && f.bind_q) body.bind_q = f.bind_q
    if (need.value.includes('approver')) Object.assign(body, { approver: f.approver, basis: f.basis })
    item.value = await guardedFetch(`/api/racks/diff/${item.value.id}/act`, { method: 'POST', body })
    showToast(`D${item.value.id}：${data.value?.actions?.[f.action] ?? f.action}，完成`, 'success')
    await load(); emit('changed')
  } catch (e: any) { showToast(`處理失敗：${errText(e)}。下一步：按 F5 重新整理看這筆最新的狀態，再處理一次；還是失敗請把這段訊息給系統管理員`, 'error', 10000) } finally { busy.value = false }
}
const logRows = computed(() => (item.value?.log ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1, act: x.action === 'report' ? '回報' : (data.value?.actions?.[x.action] ?? x.action),
  from: STATUS[x.status_before] ?? '', to: STATUS[x.status_after] ?? '' })))
// ---- 批次
const selected = ref<Record<string, boolean>>({})
watch([ds, () => props.room], () => { selected.value = {}; pv.value = null })
const picked = computed(() => rows.value.filter((r: any) => selected.value[String(r.id)]).map((r: any) => r.id))
const bAction = ref('')
const bNote = ref('')
const pv = ref<any>(null)
watch([selected, bAction, bNote], () => { pv.value = null })      // 備註改了＝預覽作廢，要重新預覽（GPT 複核 P1）
const bErr = ref('')
async function preview() {
  bErr.value = ''
  try { pv.value = await guardedFetch('/api/racks/diff/batch-preview', { method: 'POST', body: { ids: picked.value, action: bAction.value, note: bNote.value || null } }) } catch (e: any) {
    bErr.value = `預覽沒成功：${errText(e)}。下一步：${e?.status === 428 || e?.statusCode === 428 ? '輸入管理密碼後再按一次「預覽」' : '檢查動作與備註（駁回要寫原因），再按一次「預覽」'}`
  }
}
async function applyBatch() {
  if (!pv.value) return
  busy.value = true
  try {
    const r: any = await guardedFetch('/api/racks/diff/batch-apply', { method: 'POST', body: { token: pv.value.token, note: bNote.value || null } })
    showToast(`已處理 ${r.count} 筆`, 'success')
    selected.value = {}; pv.value = null; bErr.value = ''
    await load(); await loadItem(); emit('changed')
  } catch (e: any) {
    pv.value = null
    bErr.value = `整批沒做：${errText(e)}。下一步：再按一次「預覽」看最新的清單，確認後再送出；一直失敗請把這段訊息給系統管理員`
    await load()
  } finally { busy.value = false }
}
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-else>
      <div class="bar" role="tablist">
        <button class="chip" :class="{ on: !ds }" @click="setQ({ ds: undefined, di: undefined })">全部 {{ all.length }}</button>
        <button v-for="(t, k) in STATUS" :key="k" class="chip" :class="{ on: ds === k }" :data-testid="`diff-chip-${k}`" @click="setQ({ ds: k, di: undefined })">{{ t }} {{ data?.counts?.[k] ?? 0 }}</button>
        <InfoNote>現場在機櫃正面圖每一格右上「⋯」回報；這裡由管理員處理。結案動作才會改機櫃圖：關機＝保留 U、不算用電與台數（同「關機未下架」）；實體移除＝已不在櫃上；退役要登錄資產負責人（核准人）與依據，只記「CIA 待異動：報廢」，CIA 匯入改了才是真的狀態。每一步都有紀錄，可以還原；結案後那台又被別人改過就不能直接還原。</InfoNote>
      </div>
      <V2Table v-model:selected="selected" :selectable="canEdit" :rows="rows" prefix="D" row-key="id" export-name="機櫃差異待辦"
               :empty="data ? '沒有差異待辦' : '載入中…'" :cols="COLS" />
      <div v-if="canEdit && picked.length" class="bar bbar">
        已選 {{ picked.length }} 筆
        <select v-model="bAction" aria-label="批次動作"><option value="" disabled>批次動作</option>
          <option v-for="a in data?.batchable ?? []" :key="a" :value="a">{{ data?.actions?.[a] ?? a }}</option></select>
        <input v-model="bNote" class="in" placeholder="備註（駁回要寫原因）" aria-label="批次備註">
        <button class="btn2" :disabled="!bAction" @click="preview">預覽</button>
        <template v-if="pv">
          <span>可以做 {{ pv.count }} 筆<span v-if="pv.skip.length">、不能做 {{ pv.skip.length }} 筆（{{ pv.skip.map((x: any) => `D${x.id} ${x.why}`).join('；') }}）</span></span>
          <button class="btn" :disabled="busy || !pv.count" @click="applyBatch">確認處理 {{ pv.count }} 筆</button>
        </template>
      </div>
      <p v-if="bErr" class="err" data-testid="diff-batch-err">{{ bErr }}</p>
      <aside v-if="item" class="drawer" role="dialog" :aria-label="`差異待辦 D${item.id}`" data-testid="diff-detail">
        <div class="dhead"><h3 class="h3">D{{ item.id }}　{{ item.status_label }}　<span class="muted">{{ item.subject_label }}・{{ item.reason_label }}</span></h3>
          <button type="button" class="btn2 x" aria-label="關閉" @click="closeItem">關閉</button></div>
        <dl class="kv">
          <dt>位置</dt><dd><NuxtLink :to="rackUrl(item)">{{ item.room }} {{ item.rack }}</NuxtLink> {{ item.u_text }}</dd>
          <dt>{{ item.subject === 'sys' ? '設備' : 'Excel 文字' }}</dt><dd>{{ item.who || '—' }}</dd>
          <dt>回報</dt><dd>{{ item.created_by }}　{{ item.created_at }}</dd>
          <dt>備註</dt><dd>{{ item.note || '—' }}</dd>
          <dt>序號</dt><dd>{{ item.serial || '—' }}</dd>
          <template v-if="item.target_rack"><dt>新位置</dt><dd>{{ item.target_room }} {{ item.target_rack }} {{ item.target_u_start }}{{ item.target_u_end !== item.target_u_start ? '-' + item.target_u_end : '' }}U</dd></template>
          <template v-if="item.approver"><dt>核准</dt><dd>{{ item.approver }}　依據 {{ item.basis }}</dd></template>
        </dl>
        <form v-if="canEdit && item.actions.length" class="acts" @submit.prevent="askAct">
          <select v-model="f.action" aria-label="處理動作" data-testid="diff-action"><option value="" disabled>處理動作</option>
            <option v-for="a in item.actions" :key="a" :value="a">{{ data?.actions?.[a] ?? a }}</option></select>
          <template v-if="need.includes('target')">
            <input v-model="f.target_rack" class="in s" :placeholder="item.target_rack || '櫃'" aria-label="新位置的櫃">
            <input v-model="f.target_u_start" class="in xs" inputmode="numeric" :placeholder="item.target_u_start ? String(item.target_u_start) : '起 U'" aria-label="起始 U">
            <input v-model="f.target_u_end" class="in xs" inputmode="numeric" placeholder="迄 U" aria-label="結束 U">
          </template>
          <input v-if="need.includes('bind')" v-model="f.bind_q" class="in" :placeholder="item.bind_key ? '回報時指定的那台（可改）' : '主機名、IP 或資產編號'" aria-label="綁到系統哪一台">
          <template v-if="need.includes('approver')">
            <input v-model="f.approver" class="in s2" placeholder="核准人（資產負責人）" aria-label="核准人">
            <input v-model="f.basis" class="in" placeholder="依據（簽呈、單號）" aria-label="依據">
          </template>
          <input v-model="f.note" class="in" placeholder="備註" aria-label="處理備註">
          <button v-if="!confirming" type="submit" class="btn" :disabled="busy || !!problem">{{ busy ? '處理中…' : (summary ? '下一步：確認' : '送出') }}</button>
          <span v-if="f.action && problem" class="muted">{{ problem }}</span>
          <div v-if="confirming && summary" class="confirm" data-testid="diff-confirm">
            <b>確認要做「{{ data?.actions?.[f.action] ?? f.action }}」？</b>
            <div v-for="l in summary.lines" :key="l">{{ l }}</div>
            <div class="muted">{{ summary.undo }}</div>
            <div class="bar"><button type="submit" class="btn" :disabled="busy">{{ busy ? '處理中…' : '確認送出' }}</button>
              <button type="button" class="btn2" :disabled="busy" @click="confirming = false">再想想</button></div>
          </div>
        </form>
        <V2Table :rows="logRows" prefix="L" export-name="差異待辦紀錄"
                 :cols="[{ k: 'at', label: '時間', mono: true }, { k: 'by', label: '操作人' }, { k: 'act', label: '動作' }, { k: 'from', label: '原狀態' }, { k: 'to', label: '新狀態' }, { k: 'note', label: '備註' }]" />
      </aside>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.bbar { margin-top: 8px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.drawer { position: fixed; top: 0; right: 0; bottom: 0; width: min(520px, 100vw); overflow-y: auto; z-index: 40; padding: 16px;
  background: var(--card); border-left: 1px solid var(--border-strong); box-shadow: var(--shadow); }
.dhead { display: flex; gap: 8px; align-items: flex-start; justify-content: space-between; }
.x { flex: none; min-height: 44px; }
.confirm { flex-basis: 100%; padding: 10px; border: 1px solid var(--warn); border-radius: 8px; background: var(--warn-soft); font-size: 13px; display: grid; gap: 4px; }
.h3 { margin: 0 0 8px; font-size: 15px; }
.kv { display: grid; grid-template-columns: 90px 1fr; gap: 4px 10px; margin: 0 0 10px; font-size: 13px; }
.kv dt { color: var(--ink-aux); }
.kv dd { margin: 0; }
.acts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; width: 200px; }
.in.s { width: 90px; }
.in.s2 { width: 160px; }
.in.xs { width: 64px; }
select { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.err { color: var(--bad); font-size: 13px; }
.muted { color: var(--muted); font-size: 12px; }
@media (max-width: 640px) { .in { width: 100%; } }
</style>
