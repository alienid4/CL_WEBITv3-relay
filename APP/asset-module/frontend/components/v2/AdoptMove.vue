<script setup lang="ts">
// 改分類視窗（批 23，V17 舊頁 /adopt 的「改分類」視窗純搬移）：同一支 GET /api/adopt/move-choices（分類全站一份在後端）、
// 同一支 POST /api/adopt/move-tab、body 一字不改（utils/adoptCollect.moveBody）。
// 寫的是系統這邊的分類（os_type_override）：只影響顯示與試連，不動資產清冊、不會自己去建帳號（test_adopt_move 釘住）。
import { canMove, moveBody, moveChoices, type CatTab, type MoveRow } from '~/utils/adoptCollect'

const props = defineProps<{ rows: MoveRow[] }>()
const emit = defineEmits<{ (e: 'done'): void; (e: 'close'): void }>()
const { apiFetch } = useApi()
const { showToast } = useToast()

const moveRows = computed(() => props.rows.filter(canMove))
const catTabs = ref<CatTab[]>([])
const catErr = ref('')
onMounted(async () => {
  try { catTabs.value = (await apiFetch<{ tabs: CatTab[] }>('/api/adopt/move-choices')).tabs || [] }
  catch (e: any) { catErr.value = `讀不到分類頁簽：${e?.data?.detail ?? e?.message ?? e}` }
})
const tabLabel = (t?: string | null) => catTabs.value.find(c => c.tab === t)?.label ?? (t || '')
const choices = computed(() => moveChoices(moveRows.value, catTabs.value))
const whyNot = computed(() => catTabs.value.filter(t => !t.movable && t.why_not).map(t => `${t.label}：${t.why_not}`))
const moveTab = ref('')
const moveReason = ref('')
const busy = ref(false)

async function submitMove(restore = false) {
  if (!moveRows.value.length) return
  if (!restore && !moveTab.value) return
  busy.value = true
  try {
    const out = await apiFetch<{ moved: string[]; failed: { ip: string; error: string }[] }>('/api/adopt/move-tab', {
      method: 'POST', body: moveBody(moveRows.value, moveTab.value, moveReason.value, restore),
    })
    const label = restore ? '機器自動判定' : (choices.value.find(c => c.value === moveTab.value)?.label ?? moveTab.value)
    if (out.moved.length) showToast(`已搬 ${out.moved.length} 台到「${label}」`, 'success')
    for (const f of out.failed) showToast(`${f.ip} 搬不了：${f.error}`, 'error')
    emit('done')
  } catch (e: any) {
    showToast(`搬移失敗：${e?.data?.detail ?? e?.message ?? e}`, 'error')
  } finally { busy.value = false }
}
</script>

<template>
  <div class="mask">
    <div class="box" data-test="move-dialog">
      <div class="bar">
        <b>改分類 — {{ moveRows.length === 1 ? moveRows[0].ip : `勾選的 ${moveRows.length} 台` }}</b>
        <InfoNote>分類是系統依資產登記的「作業系統」欄與設備機型自動判的，總會有少數判錯。這裡改的是系統這一邊的分類，不會去動資產清冊的原始資料，所以重匯 CIA 也不會把你的指定沖掉。<br><br>搬成「SSH 收集」只影響顯示與試連（唯讀登入試一次）——系統不會自己跑去那台建帳號；要建帳號仍然得由人按「一鍵納管」。ESXi／vCenter 不論搬到哪裡，納管一律擋下。</InfoNote>
      </div>
      <p v-if="moveRows.length < rows.length" class="err">有 {{ rows.length - moveRows.length }} 台認不出是哪一台（沒有主機名也沒有序號），不會搬。</p>
      <p v-if="moveRows.length === 1" class="muted">
        目前在「{{ tabLabel(moveRows[0].tab) }}」。
        <template v-if="moveRows[0].os_override">這是人工指定的（{{ moveRows[0].os_override.by ?? '不詳' }} 於 {{ moveRows[0].os_override.at }}）。</template>
        <template v-else>{{ moveRows[0].message }}</template>
      </p>
      <p v-else class="muted mono">{{ moveRows.map(r => r.ip).join('、') }}</p>
      <label class="fl">搬到哪個頁簽
        <select v-model="moveTab" class="in" aria-label="搬到哪個頁簽">
          <option value="">（請選）</option>
          <option v-for="c in choices" :key="c.value" :value="c.value" :disabled="c.disabled">{{ c.label }}{{ c.note }}</option>
        </select>
        <InfoNote v-if="whyNot.length"><template v-for="w in whyNot" :key="w">{{ w }}<br></template></InfoNote>
      </label>
      <p v-if="catErr" class="err">{{ catErr }}</p>
      <label class="fl">為什麼（選填，會跟著紀錄留下來）
        <textarea v-model="moveReason" rows="2" class="in" placeholder="例：這台是一般伺服器，資產的作業系統欄登記錯了" />
      </label>
      <div class="bar">
        <button type="button" class="btn" data-test="move-submit" :disabled="!moveTab || busy || !moveRows.length" @click="submitMove(false)">{{ busy ? '搬移中…' : '搬過去' }}</button>
        <button v-if="moveRows.some(r => r.os_override)" type="button" class="btn2" data-test="move-restore" :disabled="busy" @click="submitMove(true)">還原成機器判定</button>
        <button type="button" class="btn2" :disabled="busy" @click="emit('close')">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mask { position: fixed; inset: 0; background: rgba(0, 0, 0, .35); display: flex; align-items: center; justify-content: center; z-index: 60; }
.box { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); padding: 16px 18px; width: min(560px, 92vw); max-height: 92vh; overflow: auto; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; font-size: 13px; }
.fl { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--ink-aux); margin: 8px 0; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.btn { font-family: inherit; font-size: 13px; padding: 5px 14px; border-radius: 8px; border: 1px solid var(--brand); background: var(--brand); color: var(--card); cursor: pointer; }
.btn:disabled, .btn2:disabled { opacity: .5; cursor: default; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.muted { color: var(--muted); font-size: 12px; }
.mono { font-family: ui-monospace, monospace; }
.err { color: var(--bad); font-size: 12px; }
</style>
