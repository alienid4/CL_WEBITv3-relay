<script setup lang="ts">
// ⚙ 系統設定 →「機型廠牌」：機型標準寫法「廠牌 型號」（例：IBM S922）用的廠牌清單。
// 在這裡增刪（存在資料庫、限管理員、過寫入防護、留操作紀錄），不用改檔案。只影響「以後新增設備」的機型檢查，不改既有資料。
interface V { name: string; aliases: string; serial: string }
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const rows = ref<V[]>([])
const canEdit = ref(false)
const example = ref('')
const loadErr = ref('')
const busy = ref(false)
const dirty = ref(false)
async function load() {
  loadErr.value = ''
  try {
    const r = await apiFetch<{ vendors: { name: string; aliases: string[]; serial: string }[]; example: string; can_edit: boolean }>('/api/model-vendors')
    rows.value = r.vendors.map(v => ({ name: v.name, aliases: v.aliases.join('、'), serial: v.serial }))
    canEdit.value = r.can_edit
    example.value = r.example
    dirty.value = false
  } catch (e: any) {
    loadErr.value = `載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`
  }
}
onMounted(load)
function add() { rows.value.push({ name: '', aliases: '', serial: '' }); dirty.value = true }
function remove(i: number) {
  const n = rows.value[i]?.name || '這一列'
  if (!window.confirm(`移除廠牌「${n}」？之後新增設備時寫這個廠牌會被擋下（既有資料不受影響）。`)) return
  rows.value.splice(i, 1); dirty.value = true
}
async function save() {
  busy.value = true
  try {
    const vendors = rows.value.filter(r => r.name.trim() || r.aliases.trim()).map(r => ({
      name: r.name.trim(), aliases: r.aliases.split(/[、,，;；\n]/).map(x => x.trim()).filter(Boolean), serial: r.serial.trim() }))
    const r = await guardedFetch<{ vendors: { name: string; aliases: string[]; serial: string }[] }>('/api/model-vendors', { method: 'PUT', body: { vendors } })
    rows.value = r.vendors.map(v => ({ name: v.name, aliases: v.aliases.join('、'), serial: v.serial }))
    dirty.value = false
    showToast(`已儲存，共 ${r.vendors.length} 個廠牌`, 'success')
  } catch (e: any) {
    showToast(`儲存失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally { busy.value = false }
}
</script>

<template>
  <div class="mv">
    <p v-if="loadErr" class="err">{{ loadErr }}</p>
    <div class="mvh">
      <b>機型廠牌</b>
      <InfoNote>新增設備時，機型要寫成「廠牌 型號」（例：{{ example }}）。廠牌不在這張清單裡會被擋下；別名是同一個廠牌的其他寫法（例：HP Enterprise 會自動變成 HPE）。序號碼數是這個廠牌機身序號（service tag）的碼數，寫 7 或 7-8；空白＝不限，只檢查是英數字；新增設備時序號會自動去掉空白與連字號、轉大寫，碼數不對會擋下。這裡只影響以後新增的設備，不會改既有資料。</InfoNote>
      <span class="grow" />
      <button v-if="canEdit" class="pbtn" @click="add">＋ 新增廠牌</button>
      <button v-if="canEdit" class="pbtn primary" :disabled="busy || !dirty" @click="save">{{ busy ? '儲存中…' : '儲存' }}</button>
    </div>
    <div class="mvg head"><span>項次</span><span>廠牌（標準寫法）</span><span>別名（用「、」分開）</span><span>序號碼數</span><span /></div>
    <div v-for="(r, i) in rows" :key="i" class="mvg">
      <span class="mono">V{{ i + 1 }}</span>
      <input v-model.trim="r.name" class="in" :disabled="!canEdit" @input="dirty = true">
      <input v-model="r.aliases" class="in" :disabled="!canEdit" @input="dirty = true">
      <input v-model="r.serial" class="in" :disabled="!canEdit" placeholder="不限" @input="dirty = true">
      <button v-if="canEdit" class="linkbtn" @click="remove(i)">移除</button>
    </div>
    <p v-if="!rows.length && !loadErr" class="muted">（還沒有廠牌）</p>
    <ModelCleanup />
  </div>
</template>

<style scoped>
.mv { display: flex; flex-direction: column; gap: 6px; max-width: 1100px; }
.mv > .mvh, .mv > .mvg { max-width: 880px; }
.mvh { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.grow { flex: 1; }
.mvg { display: grid; grid-template-columns: 56px 200px 1fr 90px 60px; gap: 8px; align-items: center; padding: 3px 0; border-bottom: 1px solid var(--line); }
.mvg.head { font-size: 12px; color: var(--ink-aux); font-weight: 600; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); min-width: 0; }
.in:disabled { background: var(--sub); }
.err { color: var(--bad); }
</style>
