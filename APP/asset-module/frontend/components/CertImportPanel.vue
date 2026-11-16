<script setup lang="ts">
// 資料匯入頁 › 憑證調查表（7.5 憑證盤點的資料來源）。2026-11-01 從憑證盤點頁搬來：匯入入口統一在「資料匯入」頁。
// 流程不變：選檔→預覽（新增／更新／略過／衝突／錯誤，每列寫判斷原因）→人工確認才寫入；沿用 /api/certs/import/*。
// 新增：預覽若發現「未登記的掛載型態」（例 Tomcat），明確列出來讓人選（新增型態／歸「其他」／略過這些列），不默默歸「其他」。
const props = defineProps<{ file: File | null; isAdmin: boolean }>()
const emit = defineEmits<{ (e: 'imported'): void }>()
const cfg = useRuntimeConfig()
const { showToast } = useToast()

const plan = ref<any>(null)
const picks = reactive<Record<string, string>>({})
const mountPicks = reactive<Record<string, string>>({})
const pvFilter = ref('')
const busy = ref(false)
const ST_LABEL: Record<string, string> = { new: '新增', update: '更新', skip: '略過', conflict: '衝突', error: '錯誤' }

async function post(path: string, extra: Record<string, string> = {}) {
  const fd = new FormData()
  fd.append('file', props.file!)
  for (const [k, v] of Object.entries(extra)) fd.append(k, v)
  const res = await fetch(`${cfg.public.apiBase}${path}`, { method: 'POST', body: fd, credentials: 'include', headers: csrfHeaders('POST') })
  const body = await res.json().catch(() => ({}))     // 回應不是 JSON：下一行照狀態碼報錯，不當成功
  if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `${res.status}`)
  return body
}
function reset() {
  plan.value = null; pvFilter.value = ''
  for (const k of Object.keys(picks)) delete picks[k]
  for (const k of Object.keys(mountPicks)) delete mountPicks[k]
}
watch(() => props.file, async (f) => {
  reset()
  if (!f) return
  if (!props.isAdmin) { showToast('只有管理員可以匯入憑證調查表', 'warn'); return }
  busy.value = true
  try { plan.value = await post('/api/certs/import/preview') } catch (e: any) { showToast(`預覽失敗：${e?.message ?? '未知錯誤'}`, 'error') } finally { busy.value = false }
})

const planRows = computed(() => (plan.value?.rows ?? []).filter((r: any) => !pvFilter.value || r.status === pvFilter.value)
  .map((r: any) => ({ ...r, __no: r.row_no, status_label: ST_LABEL[r.status] ?? r.status, chg: certChangeSummary(r.changes), pick: picks[String(r.row_no)] || '' })))
const unresolved = computed(() => certUnresolved(plan.value?.rows ?? [], picks))
const unknownMounts = computed<{ name: string; rows: number }[]>(() => plan.value?.unknown_mounts ?? [])
const mountUnresolved = computed(() => unknownMounts.value.filter(m => !mountPicks[m.name]).reduce((n, m) => n + m.rows, 0))

async function apply() {
  const c = plan.value?.counts
  if (!c) return
  const lines = [`確認寫入：新增 ${c.new}、更新 ${c.update} 列；衝突 ${c.conflict} 列中已選 ${c.conflict - unresolved.value} 列，另 ${unresolved.value} 列未選不會寫入；略過 ${c.skip}、錯誤 ${c.error} 不寫入。`]
  if (unknownMounts.value.length) lines.push(`未登記的掛載型態：${unknownMounts.value.map(m => `${m.name}（${m.rows} 列）→${({ add: '新增型態', other: '歸「其他」', skip: '略過' } as any)[mountPicks[m.name]] ?? '未選，不寫入'}`).join('、')}。`)
  lines.push('人工修改過的欄位與既有到期觀測不會被覆蓋。確定？')
  if (!window.confirm(lines.join('\n'))) return
  busy.value = true
  try {
    const r = await post('/api/certs/import/apply', {
      file_sha256: plan.value.file_sha256, plan_digest: plan.value.plan_digest,
      decisions: JSON.stringify(certDecisions(plan.value.rows, picks)), mount_decisions: JSON.stringify({ ...mountPicks }),
    })
    showToast(`已寫入：新增 ${r.created}、更新 ${r.updated}、到期觀測 ${r.observations}；略過 ${r.skipped}、未選 ${r.unresolved}${r.mounts_added?.length ? `；新增型態：${r.mounts_added.join('、')}` : ''}`, 'success', 9000)
    reset()
    emit('imported')
  } catch (e: any) { showToast(`匯入失敗：${e?.message ?? '未知錯誤'}`, 'error', 10000) } finally { busy.value = false }
}
</script>

<template>
  <div id="certs" class="card">
    <div class="card-title">憑證調查表（7.5 憑證盤點）明細與預覽
      <InfoNote>檔案在上方「匯入／匯出」表選「憑證調查表」那列上傳（xlsx；表頭：憑證名稱、F5 BIG IP、主機 IP、F5是否掛憑證、OS/站台類型、正/測、申請單位、申請窗口、AP窗口、Infra執行人員、預排更換時間、AP驗測人員、憑證更換是否需要公告；可選「憑證到期日」）。流程：預覽（新增／更新／略過／衝突，每列寫明判斷原因）→人工確認才寫入。不會用憑證名稱自動合併；同名同環境但端點不同的列標「衝突」請你選（略過／當新項目／視為某筆既有項目），沒選就不寫。重匯不會覆蓋人工修改過的欄位，也不會動既有的到期觀測；檔內的到期日只在該項目還沒有任何觀測時才當第一筆。「OS/站台類型」對不到已登記的掛載型態時，預覽會列出來讓你選新增型態、歸「其他」或略過，不會默默歸類。檔案只在這個請求內處理，不存檔。</InfoNote>
    </div>
    <p v-if="!plan && !busy" class="dim">尚未選檔。請在上方表格「憑證調查表」那一列選 xlsx 檔，這裡會出現預覽。</p>
    <p v-if="busy" class="dim">處理中…</p>
    <template v-if="plan">
      <p class="sum">
        <b>{{ plan.filename }}</b>：共 {{ plan.row_count }} 列
        <button v-for="(lab, k) in ST_LABEL" :key="k" type="button" class="chip btn" :class="{ on: pvFilter === k }" @click="pvFilter = pvFilter === k ? '' : String(k)">{{ lab }} {{ plan.counts[k] }}</button>
      </p>
      <div v-if="unknownMounts.length" class="warnbox">
        <b>發現未登記的型態：{{ unknownMounts.map(m => `${m.name}（${m.rows} 列）`).join('、') }}，要新增嗎？</b>
        <div v-for="m in unknownMounts" :key="m.name" class="mrow">
          <span class="mn">{{ m.name }}</span>
          <select :value="mountPicks[m.name] || ''" class="sel" @change="mountPicks[m.name] = ($event.target as HTMLSelectElement).value">
            <option value="">（未選，這 {{ m.rows }} 列不寫入）</option>
            <option value="add">新增「{{ m.name }}」這個型態並匯入</option>
            <option value="other">歸入「其他」匯入</option>
            <option value="skip">略過這 {{ m.rows }} 列</option>
          </select>
        </div>
        <span v-if="mountUnresolved" class="dim">還有 {{ mountUnresolved }} 列的型態沒選，確認後不會寫入。</span>
      </div>
      <V2Table :rows="planRows" prefix="R" empty="沒有符合的列"
               :cols="[{ k: 'name', label: '憑證名稱' }, { k: 'environment', label: '環境' }, { k: 'status_label', label: '判斷' }, { k: 'reason', label: '判斷原因', brief: (r: any) => (r.reason || '').slice(0, 40) }, { k: 'key_text', label: '唯一條件', brief: (r: any) => (r.key_text || '').slice(0, 24) }, { k: 'chg', label: '變更', brief: (r: any) => (r.chg || '').slice(0, 40) }, { k: 'pick', label: '衝突時的選擇' }]">
        <template #cell-pick="{ row }">
          <select v-if="row.status === 'conflict'" :value="picks[String(row.row_no)] || ''" class="sel" @change="picks[String(row.row_no)] = ($event.target as HTMLSelectElement).value">
            <option value="">（未選，不寫入）</option>
            <option value="skip">略過這列</option>
            <option value="new">當作新項目</option>
            <option v-for="c in row.candidates" :key="c.id" :value="c.id">視為既有：{{ c.name }}｜{{ c.endpoint || '無端點' }}:{{ c.port }}｜{{ c.mount_type || '掛載未填' }}{{ c.active ? '' : '（已停用）' }}</option>
          </select>
          <span v-else>—</span>
        </template>
      </V2Table>
      <p>
        <button type="button" class="btn small primary" :disabled="busy || (plan.counts.new + plan.counts.update + (plan.counts.conflict - unresolved) === 0)" @click="apply">確認寫入</button>
        <span v-if="unresolved" class="dim">　還有 {{ unresolved }} 列衝突沒選，確認後這些列不會寫入</span>
      </p>
    </template>
  </div>
</template>

<style scoped>
.dim { color: var(--ink-aux); font-size: 13px; }
.sum { margin: 0 0 8px; font-size: 13px; }
.chip { font-size: 12px; padding: 2px 10px; border-radius: 999px; background: var(--mint); color: var(--brand-dark); }
.chip.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; margin-left: 6px; font-family: inherit; }
.chip.btn.on { background: var(--brand); color: var(--type-on); border-color: var(--brand); }
.warnbox { background: var(--warn-soft); color: var(--warn-text); border-radius: 8px; padding: 8px 12px; margin: 0 0 10px; font-size: 13px; display: flex; flex-direction: column; gap: 6px; }
.mrow { display: flex; align-items: center; gap: 10px; }
.mn { min-width: 110px; font-weight: 600; }
.sel { padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; font-family: inherit; font-size: 13px; background: var(--card); color: var(--ink); }
</style>
