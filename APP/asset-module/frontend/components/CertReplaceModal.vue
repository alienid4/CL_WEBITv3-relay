<script setup lang="ts">
// 憑證盤點 › 單筆動作視窗（清單列與詳情頁共用）：
//   mode=replaced「標記已更換」：更換進度＝已更換＋登錄新到期日（必填，或明確勾「稍後補登」）＋可同時作廢舊觀測；
//     走後端批次路徑：單一交易、留批次紀錄，可在「批次紀錄」復原。
//   mode=schedule「排定更換」：預排日期＋更換進度＝已排程一次設定（兩欄一致）。
// 只是登記，不會通知任何人、不會自動更換。
const props = defineProps<{ mode: 'replaced' | 'schedule'; id: string; name: string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'done'): void }>()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const notAfter = ref('')
const later = ref(false)
const voidOld = ref(false)
const planned = ref('')
const busy = ref(false)
const err = ref('')
watch(later, (v) => { if (v) { notAfter.value = ''; voidOld.value = false } })

async function submit() {
  err.value = ''
  busy.value = true
  try {
    if (props.mode === 'replaced') {
      if (!later.value && !notAfter.value) { err.value = '請填新的到期時間，或明確勾選「稍後補登」'; return }
      await guardedFetch(`/api/certs/items/${props.id}/mark-replaced`, { method: 'POST', body: { not_after: later.value ? undefined : certLocalToApi(notAfter.value), later: later.value, void_old: voidOld.value } })
      showToast(later.value ? '已標記已更換；新到期日還沒登錄，這筆暫時歸在「無到期資料」、風險等級顯示「待補到期日」，請記得補登' : '已標記已更換並登錄新到期日；可在「批次紀錄」復原', 'success', 8000)
    } else {
      if (!planned.value) { err.value = '請選預排日期'; return }
      await guardedFetch(`/api/certs/items/${props.id}`, { method: 'PUT', body: { planned_date: planned.value, replace_status: '已排程' } })
      showToast('已排定更換（預排日期＋已排程）', 'success')
    }
    emit('done')
  } catch (e: any) { err.value = errMsg(e) } finally { busy.value = false }
}
</script>

<template>
  <div class="mask">
    <div class="box">
      <h3>{{ mode === 'replaced' ? '標記已更換' : '排定更換' }}：{{ name }}</h3>
      <template v-if="mode === 'replaced'">
        <label class="f">新的到期時間（UTC+8）<span class="req">*</span>
          <input v-model="notAfter" type="datetime-local" :disabled="later"></label>
        <label class="c"><input v-model="later" type="checkbox"> 稍後補登（這次先不填新到期日；補登前這筆顯示「待補到期日」）</label>
        <label class="c"><input v-model="voidOld" type="checkbox" :disabled="later"> 同時作廢舊的到期觀測（保留紀錄、不再計入到期判斷）</label>
        <p class="hint">更換進度會設為「已更換」。僅登記，不會通知任何人。<InfoNote>這是單一操作：一次完成「進度＝已更換」、登錄新到期日、（可選）作廢舊觀測，單一交易，不會做一半。要嘛填新到期日，要嘛明確勾「稍後補登」；勾稍後補登時，舊的到期日不再拿來判斷（舊憑證已經換掉了），這筆在補登新到期日之前會歸在「無到期資料」、風險等級顯示「待補到期日」，可用清單的「待補新到期日」篩選找出來補登；補登後自動恢復。操作會記在「批次紀錄」，可整批復原（復原會把新登錄的觀測作廢、舊觀測還原、進度回到原值；若之後項目又被改過會列為衝突、不覆蓋）。</InfoNote></p>
      </template>
      <template v-else>
        <label class="f">預排日期<span class="req"> *</span><input v-model="planned" type="date"></label>
        <p class="hint">預排日期與更換進度（已排程）會一起設定。僅登記，不會通知任何人。<InfoNote>預排日期與更換進度必須一致：已排程一定要有預排日期；有預排日期就不能停在待排程。所以排定更換是一次把兩欄設好，不要分開改。</InfoNote></p>
      </template>
      <p v-if="err" class="bad">{{ err }}</p>
      <div class="acts">
        <button type="button" class="btn" :disabled="busy" @click="emit('close')">取消</button>
        <button type="button" class="btn primary" :disabled="busy" @click="submit">{{ busy ? '處理中…' : '確定' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mask { position: fixed; inset: 0; background: rgba(0, 0, 0, .35); display: flex; align-items: center; justify-content: center; z-index: 60; }
.box { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); padding: 18px 20px; width: min(520px, 94vw); display: flex; flex-direction: column; gap: 10px; }
h3 { margin: 0; font-size: 16px; }
.f { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--ink-aux); }
.f input { padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; font-family: inherit; font-size: 13px; background: var(--card); color: var(--ink); }
.c { font-size: 13px; display: flex; gap: 6px; align-items: center; }
.req { color: var(--bad); font-weight: 700; }
.hint { margin: 0; font-size: 12px; color: var(--muted); }
.bad { color: var(--bad); font-size: 13px; margin: 0; }
.acts { display: flex; justify-content: flex-end; gap: 8px; }
.btn { padding: 6px 14px; border-radius: 5px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; font-family: inherit; }
.btn.primary { background: var(--brand); color: var(--type-on); border-color: var(--brand); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
</style>
