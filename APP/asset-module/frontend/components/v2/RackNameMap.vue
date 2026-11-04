<script setup lang="ts">
// 5.1「Excel 對照」頁籤下方：機櫃名稱對照（2.043.13，同一實體櫃合一）。CIA 寫「1-1」、Excel 寫「A1」＝同一櫃，畫面一律用 Excel 的櫃名。
// 自動推得（CIA 機櫃欄的括號代碼）＋管理員設定（app_settings.rack_name_map）。只是改名：不動 CIA 資料、不增減任何一台。
const emit = defineEmits<{ changed: [] }>()
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const { isViewer } = useAuth()
const data = ref<any>(null)
const err = ref('')
async function load() {
  try { data.value = await apiFetch('/api/racks/name-map'); err.value = '' } catch (e: any) { err.value = `機櫃名稱對照載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(load)
const rows = computed(() => {
  const d = data.value
  if (!d) return []
  const keys = [...new Set([...Object.keys(d.derived ?? {}), ...Object.keys(d.stored ?? {})])].sort()
  return keys.map((k, i) => {
    const [room, rack] = k.split('/')
    const eff = d.effective?.[k]
    return { key: k, __no: i + 1, room, rack, derived: d.derived?.[k] ?? '—', stored: d.stored?.[k] ?? '—', eff: eff ?? `${rack}（不換）`,
      src: d.stored?.[k] !== undefined ? '已生效（管理員設定／套用 Excel 寫入）' : '只是建議（還沒套用，不生效）' }
  })
})
const canEdit = computed(() => !!data.value?.can_edit && !isViewer.value)
const fKey = ref('')
const fVal = ref('')
const busy = ref(false)
async function save(items: Record<string, string>) {
  busy.value = true
  try {
    await guardedFetch('/api/racks/name-map', { method: 'PUT', body: { items } })
    showToast('已更新機櫃名稱對照', 'success')
    await load(); emit('changed')
  } catch (e: any) { showToast(`更新失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { busy.value = false }
}
function addOne() {
  const k = fKey.value.trim()
  if (!k.includes('/')) { showToast('左邊要寫成「機房/系統櫃名」，例：內湖/1-1', 'warn'); return }
  save({ ...(data.value?.stored ?? {}), [k]: fVal.value.trim() })
  fKey.value = ''; fVal.value = ''
}
// 合櫃後台數改變（2.043.15，GPT 複核 D）：沒有管理員確認，部署時數字核對會擋下
const merges = computed(() => (data.value?.merge_changes ?? []).map((c: any, i: number) => ({
  key: `${c.room}/${c.rack}`, __no: i + 1, room: c.room, rack: c.rack, sys: (c.sys_names ?? []).join('、'),
  before: c.before, after: c.after,
  lost: (c.lost ?? []).map((x: any) => `${x.name}（${x.from_rack} ${x.u}）→ ${x.why}`).join('；'),
  state: c.confirmed ? `已確認（${c.confirmed.by}，${c.confirmed.at}）` : c.in_grace ? `未確認・請在 ${c.grace_until ?? '7 天內'} 前確認` : '未確認・已逾期（部署會擋）', raw: c,
})))
async function confirmMerge(c: any) {
  busy.value = true
  try {
    await guardedFetch('/api/racks/merge-confirm', { method: 'PUT', body: { room: c.room, rack: c.rack, keys: c.keys } })
    showToast(`已確認 ${c.room} ${c.rack} 合櫃後台數`, 'success')
    await load(); emit('changed')
  } catch (e: any) { showToast(`確認失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { busy.value = false }
}
function dropOne(k: string) {
  const next = { ...(data.value?.stored ?? {}) }
  delete next[k]
  save(next)
}
</script>

<template>
  <div class="box">
    <h3 class="h3">機櫃名稱對照<InfoNote>同一個實體櫃在 CIA 跟 Excel 寫法不同時（例：CIA「1-1」、Excel「A1」），全站一律顯示 Excel 的櫃名，台數不重複算。只有「管理員設定」那一欄會生效：在「Excel 對照」按「套用」時，系統會把「建議」（CIA 機櫃欄的括號代碼，例：1-1(A1)）寫進去；只做預覽、沒套用，畫面不變。管理員也可以自己設定；值寫成跟系統櫃名一樣＝這櫃不換名。CIA 原始資料不改。</InfoNote></h3>
    <p v-if="err" class="err">{{ err }}</p>
    <V2Table v-else :rows="rows" prefix="N" row-key="key" :empty="data ? '沒有需要對照的機櫃' : '載入中…'" export-name="機櫃名稱對照"
             :cols="[{ k: 'room', label: '機房' }, { k: 'rack', label: 'CIA 櫃名' }, { k: 'derived', label: '建議' }, { k: 'stored', label: '管理員設定' }, { k: 'eff', label: '畫面顯示' }, { k: 'src', label: '來源' }]" />
    <form v-if="canEdit" class="bar" @submit.prevent="addOne">
      <input v-model="fKey" class="in" placeholder="機房/系統櫃名（例：內湖/1-1）" aria-label="機房/系統櫃名">
      <span>→</span>
      <input v-model="fVal" class="in s" placeholder="Excel 櫃名（例：A1）" aria-label="Excel 櫃名">
      <button type="submit" class="btn" :disabled="busy">設定</button>
      <span v-if="Object.keys(data?.stored ?? {}).length" class="muted">移除管理員設定：</span>
      <button v-for="k in Object.keys(data?.stored ?? {})" :key="k" type="button" class="chip" :disabled="busy" @click="dropOne(k)">{{ k }} ✕</button>
    </form>
    <template v-if="merges.length">
      <h3 class="h3 mt">合櫃後台數改變<InfoNote>合櫃後，原本分在兩個櫃名下的設備可能被判成同一箱（同主機名＋同機型、管理卡），台數會變少。這可能是真的重複登記，也可能是名稱對照設錯。管理員看過每一台後按「確認」才算數；對照啟用 7 天內沒確認只會警告，超過 7 天還沒確認，部署時的數字核對會擋下。報告會列出確認人與時間。</InfoNote></h3>
      <V2Table :rows="merges" prefix="M" row-key="key" export-name="合櫃後台數改變"
               :cols="[{ k: 'room', label: '機房' }, { k: 'rack', label: '櫃名' }, { k: 'sys', label: '合併的系統櫃名' }, { k: 'before', label: '合併前' }, { k: 'after', label: '合併後' }, { k: 'lost', label: '少算的設備' }, { k: 'state', label: '狀態' }]" />
      <div v-if="canEdit" class="bar">
        <button v-for="m in merges.filter((x: any) => !x.raw.confirmed)" :key="m.key" type="button" class="btn" :disabled="busy" @click="confirmMerge(m.raw)">確認 M{{ m.__no }}（{{ m.key }}）</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.box { margin-top: 16px; }
.h3 { margin: 0 0 6px; font-size: 15px; }
.mt { margin-top: 16px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px; font-size: 13px; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; width: 220px; }
.in.s { width: 140px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.chip { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 999px; padding: 2px 10px; font-size: 12px; font-family: inherit; cursor: pointer; }
.err { color: var(--bad); font-size: 13px; }
.muted { color: var(--muted); font-size: 13px; }
</style>
