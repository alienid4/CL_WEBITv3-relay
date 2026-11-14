<script setup lang="ts">
// 主機頁「事件與維運紀錄」→ 例行時段（2.066.x，使用者 11-13）。資料：/api/v2/routine?key=（alert_attention.py）。
// 例行時段開始之後才發生的斷線不列 1.1「需要注意」（時段開始前就斷的照常判斷；已推過通知的不會因為進入時段而消失）、不推 Email／Teams；時段結束還沒恢復就列進去。
// 管理員：新增／修改／刪除（每次留紀錄：誰、何時、改前、改後）、確認或拒絕「建議例行」（確認才生效）、改需要注意門檻。
const props = defineProps<{ mkey: string }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const d = ref<any>(null)
const err = ref('')
const busy = ref(false)
const form = reactive({ id: 0, start: '', end: '', expires_on: '', note: '' })
const minInput = ref(10)
const openInput = ref(5)
const longInput = ref(24)
async function load() {
  err.value = ''
  try {
    d.value = await apiFetch('/api/v2/routine', { query: { key: props.mkey } })
    minInput.value = d.value.attention_min
    openInput.value = d.value.attention_open_min ?? 5
    longInput.value = d.value.longterm_hours ?? 24
  } catch (e: any) { err.value = `例行時段讀取失敗：${errMsg(e)}` }
}
onMounted(load)
async function run(fn: () => Promise<any>, ok: string) {
  if (busy.value) return
  busy.value = true
  try { await fn(); showToast(ok, 'success'); await load() } catch (e: any) { showToast(`失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
function edit(w: any) { Object.assign(form, { id: w.id, start: w.start_hhmm, end: w.end_hhmm, expires_on: w.expires_on || '', note: w.note || '' }) }
function reset() { Object.assign(form, { id: 0, start: '', end: '', expires_on: '', note: '' }) }
function save() {
  const body = { key: props.mkey, start: form.start, end: form.end, expires_on: form.expires_on || null, note: form.note }
  return run(async () => {
    if (form.id) await apiFetch(`/api/v2/routine/${form.id}`, { method: 'PUT', body })
    else await apiFetch('/api/v2/routine', { method: 'POST', body })
    reset()
  }, form.id ? '已修改例行時段' : '已新增例行時段')
}
function del(w: any) {
  if (!window.confirm(`刪除例行時段 ${w.start_hhmm}–${w.end_hhmm}？（會留紀錄）`)) return
  return run(() => apiFetch(`/api/v2/routine/${w.id}`, { method: 'DELETE' }), '已刪除')
}
function decide(s: any, decision: 'confirm' | 'reject') {
  return run(() => apiFetch('/api/v2/routine/decide', { method: 'POST', body: { key: props.mkey, start: s.start_hhmm, end: s.end_hhmm, decision } }),
    decision === 'confirm' ? '已確認，之後這個時段的斷線列例行' : '已拒絕，不再建議')
}
function saveMin() { return run(() => apiFetch('/api/v2/routine-settings', { method: 'PUT', body: { attention_min: Number(minInput.value), attention_open_min: Number(openInput.value), longterm_hours: Number(longInput.value) } }), '已儲存門檻') }

const ACT = { add: '新增', update: '修改', delete: '刪除', reject_suggestion: '拒絕建議' } as Record<string, string>
const span = (j: string | null) => { if (!j) return '—'; try { const o = JSON.parse(j); return o.start_hhmm ? `${o.start_hhmm}–${o.end_hhmm}${o.expires_on ? `（到 ${o.expires_on}）` : ''}` : '—' } catch { return j } }  // 紀錄是自己寫的 JSON；萬一壞了就原文照列，不吞
const wCols = [{ k: 'start_hhmm', label: '開始', mono: true }, { k: 'end_hhmm', label: '結束', mono: true },
  { k: 'expires_on', label: '到期日', mono: true, text: (r: any) => (r.expires_on ? r.expires_on + (r.expired ? '（已過期）' : '') : '不到期') },
  { k: 'source', label: '來源', text: (r: any) => (r.source === 'suggested' ? '建議確認' : '手動登記') }, { k: 'note', label: '備註' },
  { k: 'created_by', label: '登記人' }, { k: 'act', label: '' }]
const sCols = [{ k: 'start_hhmm', label: '建議開始', mono: true }, { k: 'end_hhmm', label: '建議結束', mono: true }, { k: 'days', label: '14 天內幾天', mono: true }, { k: 'reason', label: '理由', min: 160 }, { k: 'act', label: '' }]
const lCols = [{ k: 'at', label: '時間', mono: true }, { k: 'who', label: '誰' }, { k: 'action', label: '動作', text: (r: any) => ACT[r.action] || r.action },
  { k: 'before_json', label: '改前', text: (r: any) => span(r.before_json) }, { k: 'after_json', label: '改後', text: (r: any) => span(r.after_json) }]
</script>

<template>
  <section class="card rw" aria-label="例行時段">
    <div class="bh"><h3>例行時段</h3>
      <span v-if="d" class="sub">需要注意門檻：非開盤 {{ d.attention_min }} 分・開盤 {{ d.attention_open_min }} 分・長期未恢復 {{ d.longterm_hours }} 小時</span>
      <InfoNote>這台每天固定會斷的時段（例如排程重開機）。時段內的斷線不列 1.1「需要注意」、不推 Email／Teams；時段結束還沒恢復，那一刻起就列進需要注意並推一次。可跨午夜（例：23:30–00:30），到期日之後自動失效。「建議例行」是系統從最近 14 天找到的規律（同一時刻 ±15 分、10 天以上），管理員確認才生效；告警紀錄不滿 14 天的主機不會有建議。每次新增、修改、刪除、拒絕都記在下面的異動紀錄。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-else-if="d">
      <V2Table :rows="d.windows" :cols="wCols" prefix="W" :max="20" empty="沒有登記例行時段" export-name="例行時段">
        <template #cell-act="{ row }">
          <span v-if="d.can_edit" class="acts"><button type="button" class="mini" :disabled="busy" @click="edit(row)">修改</button><button type="button" class="mini" :disabled="busy" @click="del(row)">刪除</button></span>
        </template>
      </V2Table>
      <form v-if="d.can_edit" class="frm" data-testid="rw-form" @submit.prevent="save">
        <label>開始 <input v-model="form.start" type="time" required aria-label="開始時間"></label>
        <label>結束 <input v-model="form.end" type="time" required aria-label="結束時間"></label>
        <label>到期日 <input v-model="form.expires_on" type="date" aria-label="到期日"></label>
        <label>備註 <input v-model="form.note" type="text" maxlength="200" aria-label="備註"></label>
        <button type="submit" class="btn" :disabled="busy">{{ form.id ? '儲存修改' : '新增' }}</button>
        <button v-if="form.id" type="button" class="mini" @click="reset">取消</button>
      </form>
      <h4>建議例行 <span class="sub">{{ d.suggestions.length }} 筆待確認</span></h4>
      <V2Table :rows="d.suggestions" :cols="sCols" prefix="S" :max="10" empty="目前沒有建議" export-name="建議例行">
        <template #cell-act="{ row }">
          <span v-if="d.can_edit" class="acts"><button type="button" class="mini" :disabled="busy" @click="decide(row, 'confirm')">確認</button><button type="button" class="mini" :disabled="busy" @click="decide(row, 'reject')">拒絕</button></span>
        </template>
      </V2Table>
      <details class="log">
        <summary>異動紀錄 {{ d.log.length }} 筆</summary>
        <V2Table :rows="d.log" :cols="lCols" prefix="L" :max="20" empty="沒有異動" export-name="例行時段異動紀錄" />
      </details>
      <div v-if="d.can_edit" class="frm">
        <label>需要注意門檻・非開盤 <input v-model.number="minInput" type="number" min="0" max="240" aria-label="非開盤需要注意門檻（分鐘）"> 分</label>
        <label>開盤 <input v-model.number="openInput" type="number" min="0" max="240" aria-label="開盤需要注意門檻（分鐘）" data-testid="rw-open-min"> 分</label>
        <label>長期未恢復 <input v-model.number="longInput" type="number" min="1" max="720" aria-label="長期未恢復（小時）" data-testid="rw-long-h"> 小時</label>
        <button type="button" class="btn" :disabled="busy" @click="saveMin">儲存</button>
        <InfoNote>全站共用：斷線滿這麼多分鐘還沒恢復（且不在例行時段）才列需要注意、才推 Email／Teams。斷線發生在開盤時段用「開盤」那一格，其他時間用「非開盤」；0＝一斷就算。斷超過「長期未恢復」小時數還沒恢復的移到首頁「長期未恢復」，不算需要注意、不再推（推過「發生」的恢復時照樣推恢復），每日彙整寫一行台數。</InfoNote>
      </div>
    </template>
  </section>
</template>

<style scoped>
.rw { margin-top: 12px; display: flex; flex-direction: column; gap: 10px; }
.bh { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
.bh h3, h4 { margin: 0; font-size: 15px; }
.sub { font-size: 13px; color: var(--ink-aux); font-weight: 400; }
.err { color: var(--bad); }
.frm { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; font-size: 14px; color: var(--ink-soft); }
.frm input { font: inherit; padding: 4px 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); color: var(--ink); }
.frm input[type=number] { width: 80px; }
.btn { font: inherit; font-size: 14px; padding: 4px 14px; border-radius: 10px; border: 1px solid var(--brand); background: var(--brand); color: var(--card); cursor: pointer; }
.btn:disabled { opacity: .6; }
.mini { font-family: inherit; font-size: 12px; padding: 1px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.acts { display: inline-flex; gap: 6px; }
.log summary { cursor: pointer; font-size: 14px; color: var(--ink-soft); }
</style>
