<script setup lang="ts">
// 憑證盤點 › 批次更新（兩段式）：① 選要改哪些欄位、填新值 → 「預覽影響」只計算不寫庫 ② 看過筆數與前後差異 → 「確認套用」。
// 只有「更換相關欄位」（更換進度／預排日期／需公告／執行人）能批次改，身分鍵欄位不在這裡。
// 單一交易、留稽核（批次代號、筆數、欄位名，不含人名）、可到「批次紀錄」整批復原。後端 /api/certs/batch/*（certs.py）。
const props = defineProps<{ ids: string[]; isAdmin: boolean; statuses: string[]; max: number }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'done', batchId: string): void }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

interface Preview {
  selected: number; will_change: number; unchanged: number; inactive_skipped: number; digest: string; ids: string[]; max: number
  fields: { field: string; label: string; new: string; from: { value: string; count: number }[]; will_change: number }[]
  replace_op?: { later: boolean; new_expiry: string | null; void_old: boolean; obs_added: number; obs_voided: number }
  sample: { id: string; seq: number; name: string; environment: string; endpoint: string; changes: { label: string; old: string; new: string }[] }[]
}
const use = reactive({ replace_status: false, planned_date: false, announce_needed: false, infra_owner: false })
const rop = reactive({ not_after: '', later: false, void_old: false })      // 更換進度＝已更換時的附加動作
const val = reactive({ replace_status: '待排程', planned_date: '', announce_needed: '是', infra_owner: '' })
const pv = ref<Preview | null>(null)
const busy = ref(false)
const sampleRows = computed(() => (pv.value?.sample ?? []).map(r => ({ ...r, __no: r.seq, chg: r.changes.map(c => `${c.label}：${c.old} → ${c.new}`).join('；') })))
const done = ref('')
const err = ref('')

/** 目前勾選要改的欄位 → 後端 changes（空白＝清空該欄；預排日期留白＝清空） */
const changes = computed(() => {
  const c: Record<string, string> = {}
  for (const k of Object.keys(use) as (keyof typeof use)[]) if (use[k] && (k !== 'infra_owner' || props.isAdmin)) c[k] = val[k]
  return c
})
const nChecked = computed(() => Object.keys(changes.value).length)
// 改了任何條件，舊的預覽就作廢（避免按到「確認」套用的是另一個版本）
const isReplaced = computed(() => use.replace_status && val.replace_status === '已更換')
watch(() => rop.later, (v) => { if (v) { rop.not_after = ''; rop.void_old = false } })
// 已排程必須有預排日期：選了已排程就一併勾預排日期
watch(() => [use.replace_status, val.replace_status], () => { if (use.replace_status && val.replace_status === '已排程') use.planned_date = true })
watch([changes, rop, () => props.ids], () => { pv.value = null; err.value = '' }, { deep: true })
const replaceOp = computed(() => (isReplaced.value ? { not_after: rop.later ? undefined : certLocalToApi(rop.not_after), later: rop.later, void_old: rop.void_old } : undefined))

async function preview() {
  if (!nChecked.value) { showToast('請至少勾一個要修改的欄位', 'warn'); return }
  busy.value = true; err.value = ''
  try { pv.value = await apiFetch<Preview>('/api/certs/batch/preview', { method: 'POST', body: { ids: props.ids, changes: changes.value, replace_op: replaceOp.value } }) }
  catch (e: any) { pv.value = null; err.value = errMsg(e) } finally { busy.value = false }
}
async function apply() {
  if (!pv.value || !pv.value.will_change) return
  busy.value = true; err.value = ''
  try {
    const r = await apiFetch<{ batch_id: string; changed: number }>('/api/certs/batch/apply', { method: 'POST', body: { ids: pv.value.ids, changes: changes.value, digest: pv.value.digest, replace_op: replaceOp.value } })
    done.value = r.batch_id
    showToast(`已批次更新 ${r.changed} 筆（批次 ${r.batch_id.slice(0, 8)}，可到「批次紀錄」整批復原）`, 'success', 8000)
    emit('done', r.batch_id)
  } catch (e: any) { err.value = errMsg(e); if (e?.statusCode === 409 || e?.response?.status === 409) pv.value = null } finally { busy.value = false }
}
</script>

<template>
  <div class="mask">
    <div class="box">
      <h3>批次更新（{{ ids.length }} 筆）</h3>
      <p class="scope">本次處理全部符合條件的 <b>{{ ids.length }}</b> 筆（只會動你勾的欄位；已停用的不更新）</p>
      <p v-if="ids.length > max" class="bad">一次最多 {{ max }} 筆，請縮小條件或分批。</p>
      <p class="sub">勾選要改的欄位並填新值。
        <InfoNote>只有更換相關欄位可以批次修改（更換進度、預排日期、需公告、執行人）；環境／端點／埠／SNI／掛載型態這類身分欄位不能批次改。先「預覽影響」看筆數與前後差異，確認才寫入；整批是同一個交易，之後可以在「批次紀錄」整批復原（若項目在批次之後又被改過，會列為衝突、不會被覆蓋）。已停用的項目不會被更新。執行人是人名欄位，只有管理員可以設定，且不會顯示在預覽與紀錄中。</InfoNote>
      </p>
      <div class="grid">
        <label class="row"><input v-model="use.replace_status" type="checkbox"> 更換進度<span class="dim">僅登記，不會通知任何人</span>
          <select v-model="val.replace_status" :disabled="!use.replace_status"><option value="">（清空）</option><option v-for="s in statuses" :key="s" :value="s">{{ s }}</option></select></label>
        <label class="row"><input v-model="use.planned_date" type="checkbox"> 預排日期<span class="dim">僅登記，不會通知任何人</span>
          <input v-model="val.planned_date" type="date" :disabled="!use.planned_date"><span v-if="use.planned_date && !val.planned_date" class="dim">留白＝清空</span></label>
        <label class="row"><input v-model="use.announce_needed" type="checkbox"> 需公告
          <select v-model="val.announce_needed" :disabled="!use.announce_needed"><option value="是">是</option><option value="否">否</option><option value="">（清空）</option></select></label>
        <label v-if="isAdmin" class="row"><input v-model="use.infra_owner" type="checkbox"> 執行人
          <input v-model="val.infra_owner" :disabled="!use.infra_owner" placeholder="姓名（留白＝清空）"></label>
      </div>
      <div v-if="isReplaced" class="rop">
        <b>標記已更換：</b>
        <label class="row">新到期時間（UTC+8）<input v-model="rop.not_after" type="datetime-local" :disabled="rop.later"></label>
        <label class="row"><input v-model="rop.later" type="checkbox"> 稍後補登（這批先不填新到期日；補登前顯示「待補到期日」）</label>
        <label class="row"><input v-model="rop.void_old" type="checkbox" :disabled="rop.later"> 同時作廢舊的到期觀測</label>
        <span class="dim">新到期日要嘛填、要嘛明確勾稍後補登；同一個新到期時間會套用在全部 {{ ids.length }} 筆。</span>
      </div>
      <p v-if="err" class="bad">{{ err }}</p>

      <div v-if="pv" class="pv">
        <p class="sum big"><b>選取 {{ pv.selected }} 筆</b>：<b class="hl">{{ pv.will_change }} 筆會變更</b>，{{ pv.unchanged }} 筆本來就是這個值<template v-if="pv.inactive_skipped">，{{ pv.inactive_skipped }} 筆已停用不更新</template>。</p>
        <p v-if="pv.replace_op" class="sum">標記已更換：<b>{{ pv.replace_op.later ? '新到期日稍後補登' : `新增 ${pv.replace_op.obs_added} 筆新到期觀測（${pv.replace_op.new_expiry}）` }}</b><template v-if="pv.replace_op.void_old">，作廢舊觀測 <b>{{ pv.replace_op.obs_voided }}</b> 筆</template>。</p>
        <ul class="fl">
          <li v-for="f in pv.fields" :key="f.field">
            <b>{{ f.label }}</b> → <b>{{ f.new }}</b>　<span class="dim">（{{ f.will_change }} 筆；原本：{{ f.from.map(x => `${x.value} ${x.count}`).join('、') || '—' }}）</span>
          </li>
        </ul>
        <V2Table v-if="pv.sample.length" :rows="sampleRows" prefix="C" empty="" :max="20"
                 :cols="[{ k: 'name', label: '憑證名稱' }, { k: 'environment', label: '環境' }, { k: 'chg', label: '變更內容' }]" />
        <p v-if="pv.will_change > pv.sample.length" class="dim">只列前 {{ pv.sample.length }} 筆範例，共 {{ pv.will_change }} 筆會變更。</p>
      </div>

      <div class="acts">
        <button class="btn" type="button" :disabled="busy" @click="emit('close')">{{ done ? '關閉' : '取消' }}</button>
        <button v-if="!done" class="btn" type="button" :disabled="busy || !nChecked || ids.length > max" @click="preview">{{ busy && !pv ? '計算中…' : '預覽影響' }}</button>
        <button v-if="!done" class="btn primary" type="button" :disabled="busy || !pv || !pv.will_change" @click="apply">{{ busy && pv ? '套用中…' : pv ? `確認套用（${pv.will_change} 筆）` : '確認套用' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mask { position: fixed; inset: 0; background: rgba(0, 0, 0, .35); display: flex; align-items: center; justify-content: center; z-index: 60; }
.box { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); padding: 18px 20px; width: min(760px, 94vw); max-height: 92vh; overflow: auto; }
h3 { margin: 0 0 8px; font-size: 16px; }
.sub { font-size: 13px; color: var(--ink-soft); margin: 0 0 10px; }
.grid { display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; }
.row { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.row select, .row input[type=date], .row input:not([type]) { padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; font-family: inherit; font-size: 13px; background: var(--card); color: var(--ink); }
.row input:disabled, .row select:disabled { opacity: .5; }
.dim { color: var(--muted); font-size: 12px; }
.rop { border: 1px dashed var(--brand); border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.bad { color: var(--bad); font-size: 13px; }
.pv { border: 1px solid var(--border); background: var(--sub); border-radius: 10px; padding: 10px 12px; margin-bottom: 12px; }
.sum { margin: 0 0 6px; font-size: 14px; }
.sum.big { font-size: 17px; padding: 8px 10px; background: var(--mint); border-radius: 8px; }
.scope { margin: 0 0 8px; padding: 6px 10px; background: var(--sub); border: 1px solid var(--border); border-radius: 8px; font-size: 13px; }
.hl { color: var(--brand-dark); }
.fl { margin: 0 0 8px; padding-left: 18px; font-size: 13px; }
.acts { display: flex; justify-content: flex-end; gap: 8px; }
.btn { padding: 6px 14px; border-radius: 5px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; font-family: inherit; }
.btn.primary { background: var(--brand); color: var(--type-on); border-color: var(--brand); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
</style>
