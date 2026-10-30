<script setup lang="ts">
// Windows 取消納管（2026-10-22）：之後的收集不再登入這台。
// 跟 Linux 那個（RevokeModal）不同：Windows 納管時目標機上什麼都沒建，所以取消也不用登入、不用帳密，
// 目標機一樣什麼都不動；已收的資料保留。仍要按一次確認（防手滑），送出時帶 confirm=YES。
const props = defineProps<{ ip: string; hostname?: string | null }>()
const emit = defineEmits<{ (e: 'done'): void; (e: 'close'): void }>()

const { apiFetch } = useApi()
const { showToast } = useToast()
const busy = ref(false)
const err = ref('')

async function run() {
  if (busy.value) return
  busy.value = true
  err.value = ''
  try {
    const r = await apiFetch<{ ok: boolean; message: string }>('/api/onboard/windows/revoke', {
      method: 'POST', body: { ip: props.ip, confirm: 'YES' } })
    showToast(r.message || '已取消納管', 'success')
    emit('done')
  } catch (e: any) {
    err.value = e?.data?.detail ?? e?.message ?? String(e)
    showToast(`取消納管失敗：${err.value}`, 'error')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="modal-mask">
    <div class="modal">
      <div class="mhead">取消納管 — {{ ip }}<template v-if="hostname">（{{ hostname }}）</template>
        <InfoNote>之後 2-1 開始收集（手動／排程）不再登入這台，結果改列「待納管」。<b>目標機上什麼都不動</b>（Windows 納管本來就沒建任何東西），已收到的資料保留。要再收，勾選後按「納管」即可。</InfoNote>
      </div>
      <p class="q">確定不再收集這台？</p>
      <p v-if="err" class="err">{{ err }}</p>
      <div class="macts">
        <button class="btn danger" :disabled="busy" @click="run">{{ busy ? '取消中…' : '確定取消納管' }}</button>
        <button class="btn ghost" :disabled="busy" @click="emit('close')">不要</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: flex;
  align-items: center; justify-content: center; z-index: 50; padding: 16px; }
.modal { background: var(--card-solid); border-radius: var(--radius); padding: 22px 24px;
  max-width: 480px; width: 100%; border: 1px solid var(--border-strong); }
.mhead { font-weight: 700; font-size: 15px; margin-bottom: 12px; color: var(--bad); }
.q { font-size: 14px; color: var(--ink-soft); margin: 0 0 6px; }
.err { font-size: 12.5px; color: var(--bad); margin: 6px 0 0; }
.macts { display: flex; gap: 10px; margin-top: 16px; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 8px 18px;
  border-radius: 6px; cursor: pointer; border: 1px solid var(--border-strong); background: var(--card); }
.btn.danger { background: var(--bad); color: var(--card); border-color: var(--bad); }
.btn.ghost { color: var(--ink-soft); }
.btn:disabled { opacity: .55; cursor: not-allowed; }
</style>
