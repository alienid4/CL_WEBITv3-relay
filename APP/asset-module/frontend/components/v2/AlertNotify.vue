<script setup lang="ts">
// 告警一鍵通知（2.044.2，需求 R16）：按「通知」→ 這個視窗先顯示收件人與內容（GET …/notify，不送任何東西）→
// 按「確認送出」才排進 Teams 佇列（POST）。通知過的再送＝重送，要多勾「確認重送」。送不出去（Teams 沒啟用、設定不完整）時
// 按鈕不能按、寫原因。只能看的帳號不會出現「通知」按鈕（呼叫端判斷），後端也擋。
const props = defineProps<{ eventId: number | null }>()
const emit = defineEmits<{ close: []; sent: [r: any] }>()
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const pv = ref<any>(null)
const err = ref('')
const busy = ref(false)
const resend = ref(false)
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
async function load() {
  pv.value = null; err.value = ''; resend.value = false
  if (props.eventId == null) return
  try { pv.value = await apiFetch(`/api/v2/wug-alerts/events/${props.eventId}/notify`) } catch (e: any) { err.value = `讀取失敗：${errMsg(e)}` }
}
watch(() => props.eventId, load, { immediate: true })
const canGo = computed(() => !!pv.value?.can_send && !busy.value && (!pv.value?.is_resend || resend.value))
async function go() {
  if (!canGo.value) return
  busy.value = true
  try {
    const r = await guardedFetch<any>(`/api/v2/wug-alerts/events/${props.eventId}/notify`, { method: 'POST', body: { resend: resend.value, preview_hash: pv.value?.preview_hash || '' } })   // A6：送出時後端重算，不同就拒絕
    showToast(`已排進通知佇列（第 ${r.seq} 次${r.is_resend ? '・重送' : ''}）→ ${r.recipient}`, 'success')
    emit('sent', r)
    emit('close')
  } catch (e: any) {
    const m = `送出失敗：${errMsg(e)}`
    if (e?.status === 409 || e?.response?.status === 409) await load()     // 內容或收件人變了：重新預覽給人再看一次
    err.value = m
  } finally { busy.value = false }
}
const HIST_COLS = [{ k: 'at', label: '時間', mono: true }, { k: 'by', label: '誰' }, { k: 'kind_label', label: '第幾次' }, { k: 'target', label: '收件人' }, { k: 'result', label: '結果' }]
</script>

<template>
  <div v-if="eventId != null" class="ov" role="dialog" aria-modal="true" aria-label="告警通知" @click.self="emit('close')">
    <div class="dlg">
      <div class="dh"><h3>通知告警事件 #{{ eventId }}</h3><button type="button" class="x" aria-label="關閉" @click="emit('close')">✕</button></div>
      <p v-if="err" class="err">{{ err }}</p>
      <template v-if="pv">
        <div class="kv"><span>收件人</span><b>{{ pv.recipient }}</b>
          <InfoNote>{{ pv.note }}</InfoNote></div>
        <div class="kv"><span>內容</span><div class="msg"><b>{{ pv.title }}</b><div v-for="(l, i) in pv.lines" :key="i">{{ l }}</div></div></div>
        <p v-if="!pv.can_send" class="warn">現在送不出去：{{ pv.hold_reason }}</p>
        <template v-if="pv.history.length">
          <p class="warn">這則告警已經通知過 {{ pv.history.length }} 次，這次是第 {{ pv.seq }} 次（重送）。</p>
          <V2Table :rows="pv.history" :cols="HIST_COLS" prefix="N" :max="5" />
          <label class="ck"><input v-model="resend" type="checkbox"> 確認重送</label>
        </template>
        <div class="acts">
          <button type="button" class="btn" @click="emit('close')">取消</button>
          <button type="button" class="btn on" :disabled="!canGo" @click="go">{{ busy ? '送出中…' : '確認送出' }}</button>
        </div>
      </template>
      <p v-else-if="!err" class="muted">讀取中…</p>
    </div>
  </div>
</template>

<style scoped>
.ov { position: fixed; inset: 0; background: rgba(0, 0, 0, .35); display: flex; align-items: center; justify-content: center; z-index: 50; padding: 16px; }
.dlg { background: var(--card); color: var(--ink); border-radius: var(--radius); box-shadow: var(--shadow); width: min(640px, 100%); max-height: 90vh; overflow: auto; padding: 16px 18px; display: flex; flex-direction: column; gap: 10px; }
.dh { display: flex; align-items: center; justify-content: space-between; }
.dh h3 { margin: 0; font-size: 16px; }
.x { border: 0; background: none; font-size: 16px; cursor: pointer; color: var(--ink-soft); }
.kv { display: grid; grid-template-columns: 64px 1fr; gap: 8px; align-items: start; font-size: 14px; }
.kv > span { color: var(--ink-aux); }
.msg { background: var(--sub); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.warn { margin: 0; padding: 6px 10px; border-radius: 8px; background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
.err { color: var(--bad); margin: 0; }
.muted { color: var(--muted); font-size: 13px; }
.ck { font-size: 14px; display: flex; gap: 6px; align-items: center; }
.acts { display: flex; justify-content: flex-end; gap: 8px; }
.btn { font-family: inherit; font-size: 14px; padding: 5px 14px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.btn.on { border-color: var(--brand); background: var(--brand); color: var(--type-on); }
.btn:disabled { opacity: .5; cursor: default; }
</style>
