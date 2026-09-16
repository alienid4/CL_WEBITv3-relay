<script setup lang="ts">
// 標記非納管設備（2026-09-16 使用者：「沒辦法納管、也不是下線，譬如客製化系統或者是 Oracle」）。
//
// 跟「下線」是兩回事，不要混：
// ・下線   = 機器不在了 → 改資產狀態 → **CIA 清冊也要改**（記 CIA 待異動）
// ・非納管 = 機器還在，但我們不去納管它 → 純粹是我們的作業判斷，**不用動 CIA 清冊**
//
// 原因必填。分類只是選單，不能取代「為什麼」。隨時可以取消豁免。

const props = withDefaults(defineProps<{
  serials: string[]
  who?: string
}>(), { who: '' })

const emit = defineEmits<{ (e: 'done'): void; (e: 'close'): void }>()

const { apiFetch } = useApi()
const { showToast } = useToast()

const KINDS = ['客製化系統', '資料庫主機（Oracle 等）', '廠商維護中', 'OS 不支援', '資安政策限制', '其他']
const kind = ref(KINDS[0])
const reason = ref('')
const busy = ref(false)

async function submit() {
  if (!reason.value.trim()) { showToast('要填原因（例：客製化系統，廠商不准建帳號）', 'warn'); return }
  busy.value = true
  try {
    const r = await apiFetch<{ added: string[]; already: string[]; not_found: string[] }>(
      '/api/assets/exempt',
      { method: 'POST', body: { serials: props.serials, reason: reason.value, kind: kind.value } })
    showToast(`已標記 ${r.added.length} 台為非納管設備`
      + (r.already.length ? `，${r.already.length} 台本來就是` : ''), 'success')
    emit('done')
  } catch (e: any) {
    showToast(`標記失敗：${e?.data?.detail ?? e?.message}`, 'error', 12000)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="mask" @click.self="emit('close')">
    <div class="box">
      <h3>標記非納管設備</h3>
      <p class="who">對象：<b>{{ who || `${serials.length} 台` }}</b></p>
      <label class="fld">分類
        <select v-model="kind" class="in">
          <option v-for="k in KINDS" :key="k" :value="k">{{ k }}</option>
        </select>
      </label>
      <label class="fld">原因
        <input v-model="reason" class="in wide" placeholder="必填，例：客製化系統，廠商不准建帳號" />
      </label>
      <p class="note">
        標記後這台就<b>不算「要處理」</b>，納管漏斗與統計都會把它放到「非納管設備」那一類。<br>
        這<b>不會</b>改資產狀態，也<b>不用</b>改 CIA 清冊——它只是「我們不去納管它」。<br>
        情況改變（系統改版、廠商同意）隨時可以在資產詳細頁取消豁免。
      </p>
      <div class="acts">
        <button class="btn" type="button" :disabled="busy" @click="emit('close')">取消</button>
        <button class="btn primary" type="button" :disabled="busy" @click="submit">
          {{ busy ? '處理中…' : '確定' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mask { position: fixed; inset: 0; background: rgba(0,0,0,.35); display: flex;
        align-items: center; justify-content: center; z-index: 60; }
.box { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
       box-shadow: var(--shadow); padding: 18px 20px; width: min(540px, 92vw); }
h3 { margin: 0 0 10px; font-size: 16px; }
.who { font-size: 13px; margin: 0 0 12px; }
.fld { display: flex; align-items: center; gap: 8px; font-size: 13px; margin-bottom: 10px; }
.in { padding: 5px 9px; border: 1px solid var(--border-strong); border-radius: 5px;
      background: var(--card); color: var(--ink); }
.in.wide { flex: 1; }
.note { font-size: 12px; background: rgba(0,0,0,.04); color: var(--ink-soft);
        padding: 8px 10px; border-radius: 6px; margin: 4px 0 14px; line-height: 1.7; }
.acts { display: flex; justify-content: flex-end; gap: 8px; }
.btn { padding: 6px 14px; border-radius: 5px; border: 1px solid var(--border-strong);
       background: var(--card); color: var(--ink); cursor: pointer; }
.btn.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.btn:disabled { opacity: .6; cursor: wait; }
</style>
