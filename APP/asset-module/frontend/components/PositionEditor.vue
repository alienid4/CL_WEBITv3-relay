<script setup lang="ts">
// 位置更正（現場盤點發現 U10 其實在 U11）：機房／機櫃／起始 U／U 數＋必填備註。限管理員（後端擋）。
// 存完回傳同櫃 U 位重疊的設備：只警告、不擋。機櫃正面與 4-5 設備清單共用這一個。
const props = defineProps<{
  device: { key: string; name: string; room: string; rack: string | null; u_start: number | null; u_end: number | null; position_corrected?: boolean }
}>()
const emit = defineEmits<{ (e: 'saved'): void; (e: 'close'): void }>()
const { apiFetch } = useApi()
const { showToast } = useToast()

const form = reactive({
  room: props.device.room || '',
  rack: props.device.rack || '',
  u_start: props.device.u_start ?? null as number | null,
  u_height: props.device.u_start && props.device.u_end ? props.device.u_end - props.device.u_start + 1 : 1,
  note: '',
})
const saving = ref(false)
const collisions = ref<{ asset_serial: string; name: string; u_start: number; u_end: number }[]>([])

async function save(clear = false) {
  if (!clear && !form.note.trim()) { showToast('要寫備註（例：10/23 現場盤點）', 'warn'); return }
  if (clear && !window.confirm(`清除「${props.device.name}」的位置更正，回到 CIA 的值？`)) return
  saving.value = true
  try {
    const r = await apiFetch<{ collisions?: any[] }>('/api/racks/position', {
      method: 'PUT',
      body: { key: props.device.key, room: form.room, rack: form.rack, u_start: form.u_start || null, u_height: form.u_height || 1, note: form.note, clear },
    })
    collisions.value = r.collisions ?? []
    showToast(clear ? '已清除位置更正' : (collisions.value.length ? `已更正，但跟 ${collisions.value.length} 台的 U 位重疊` : '已更正位置'), collisions.value.length ? 'warn' : 'success')
    emit('saved')
    if (!collisions.value.length) emit('close')
  } catch (e: any) {
    showToast(`更正失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="posed">
    <div class="ttl"><b>更正位置：{{ device.name }}</b>
      <InfoNote>照現場盤點結果改這台的機房／機櫃／U 位。更正存在系統自己的表，CIA 匯入不會蓋掉；等 CIA 改成一樣時自動標「CIA 已同步」並停止套用。每次更正都留紀錄。改完要記得把「CIA 待更正」清單交給 CIA 修正。</InfoNote>
    </div>
    <div class="row">
      <label>機房<input v-model="form.room" type="text"></label>
      <label>機櫃<input v-model="form.rack" type="text" class="short"></label>
      <label>起始 U<input v-model.number="form.u_start" type="number" min="1" max="60" class="num"></label>
      <label>U 數<input v-model.number="form.u_height" type="number" min="1" max="20" class="num"></label>
      <label class="wide">備註（必填）<input v-model="form.note" type="text" placeholder="例：10/23 現場盤點"></label>
    </div>
    <div class="row">
      <button class="btn" :disabled="saving" @click="save(false)">{{ saving ? '儲存中…' : '儲存' }}</button>
      <button v-if="device.position_corrected" class="mini" :disabled="saving" @click="save(true)">清除更正</button>
      <button class="mini" @click="emit('close')">取消</button>
    </div>
    <div v-if="collisions.length" class="warn">
      U 位跟這幾台重疊（已存，請確認現場）：
      <span v-for="c in collisions" :key="c.asset_serial" class="ci">{{ c.name }}（{{ c.u_start }}{{ c.u_end !== c.u_start ? `-${c.u_end}` : '' }}U）</span>
    </div>
  </div>
</template>

<style scoped>
.posed { border: 1px solid var(--border-strong); border-radius: 10px; background: var(--sub); padding: 10px 12px; margin: 8px 0; font-size: 12.5px; }
.ttl { margin-bottom: 6px; color: var(--ink); }
.row { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; margin-bottom: 6px; }
label { display: flex; flex-direction: column; gap: 3px; font-size: 11.5px; color: var(--muted); }
input { font-family: inherit; font-size: 12.5px; padding: 4px 7px; width: 120px; border: 1px solid var(--border-strong); border-radius: 6px; }
input.short { width: 80px; }
input.num { width: 64px; }
label.wide input { width: 240px; }
.btn { font-family: inherit; font-size: 12.5px; padding: 5px 14px; border-radius: 8px; border: 0; background: var(--brand); color: var(--card); cursor: pointer; }
.btn:disabled { opacity: .5; }
.mini { font-family: inherit; font-size: 12px; padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.warn { color: var(--warn-text); background: var(--warn-soft); border-radius: 8px; padding: 6px 8px; }
.ci { margin-left: 8px; }
</style>
