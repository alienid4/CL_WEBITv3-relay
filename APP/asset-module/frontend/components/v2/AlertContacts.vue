<script setup lang="ts">
// 告警聯絡資訊（2.044.2，需求 R15）：每個業務系統代號（APID）一列，五欄＝AP 團隊、單位、系統名稱、分級、email。
// 資料：/api/v2/alert-contacts（alert_contact.listing）。取值順序固定：人工覆寫 ＞ 匯入 ＞ 業務系統對照表（系統名稱、單位、分級）＞「未提供」，
// 每格標來源。匯入一律走「資料匯入」頁（這裡只顯示資料來源與最後匯入時間）；管理員可以在這裡手動改一列（空白＝清掉覆寫）。
// 上方「缺 X」數字＝下面清單篩成缺那一欄的筆數（同一份 rows）。篩選寫在網址（acq／acmiss／acovr）。
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const data = ref<any>(null)
const err = ref('')
async function load() {
  try { data.value = await apiFetch('/api/v2/alert-contacts'); err.value = '' } catch (e: any) { err.value = `告警聯絡資訊載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(load)
const qv = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
function setQ(p: Record<string, string>) { const n: Record<string, any> = { ...route.query }; for (const [k, v] of Object.entries(p)) n[k] = v || undefined; router.replace({ query: n }) }
const kw = computed({ get: () => qv('acq'), set: (v: string) => setQ({ acq: v }) })
const miss = computed({ get: () => qv('acmiss'), set: (v: string) => setQ({ acmiss: v }) })
const ovr = computed({ get: () => qv('acovr') === '1', set: (v: boolean) => setQ({ acovr: v ? '1' : '' }) })
const FIELDS = [{ k: 'ap_team', label: 'AP 團隊' }, { k: 'unit', label: '單位' }, { k: 'system_name', label: '系統名稱' }, { k: 'sys_class', label: '分級' }, { k: 'email', label: 'email' }]
const rows = computed(() => {
  const t = kw.value.trim().toLowerCase()
  return (data.value?.rows ?? []).filter((r: any) => (!miss.value || r.missing.includes(miss.value)) && (!ovr.value || r.overridden.length)
    && (!t || [r.api_id, ...FIELDS.map(f => r[f.k])].some(x => String(x ?? '').toLowerCase().includes(t))))
    .map((r: any) => ({ ...r, src_text: FIELDS.filter(f => r[f.k + '_src']).map(f => `${f.label}：${r[f.k + '_src']}`).join('；'), miss_text: r.missing.join('、'), ovr_text: r.overridden.join('、') }))
})
const COLS = [
  { k: 'api_id', label: 'APID', mono: true }, ...FIELDS.map(f => ({ k: f.k + '_text', label: f.label })),
  { k: 'miss_text', label: '缺' }, { k: 'ovr_text', label: '人工覆寫' }, { k: 'src_text', label: '來源', brief: (r: any) => String(r.src_text).slice(0, 24) },
  { k: 'act', label: '操作', text: () => '' },
]
// ===== 匯入前快照（2.044.3 GPT A1）：每次匯入／還原前存一份，可以還原回去（還原前也會再存一份） =====
const SNAP_COLS = [{ k: 'taken_at', label: '時間', mono: true }, { k: 'reason', label: '原因' }, { k: 'source_file', label: '當時要匯的檔' }, { k: 'n', label: '筆數', mono: true }, { k: 'by', label: '誰' }, { k: 'act', label: '操作', text: () => '' }]
async function restore(s: any) {
  if (!window.confirm(`把告警聯絡資訊的匯入資料還原成快照 #${s.id}（${s.taken_at}，${s.n} 筆）？\n人工覆寫不受影響；還原前會再存一份，可以再還原回來。`)) return
  busy.value = true
  try {
    const r = await guardedFetch<any>(`/api/v2/alert-contacts/restore/${s.id}`, { method: 'POST' })
    showToast(`已還原 ${r.restored} 筆`, 'success')
    await load()
  } catch (e: any) { showToast(`還原失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { busy.value = false }
}
// ===== 管理員手動改 =====
const edit = ref<any>(null)
const busy = ref(false)
function openEdit(r: any) { edit.value = { api_id: r.api_id, cur: r, ...Object.fromEntries(FIELDS.map(f => [f.k, r.overridden.includes(f.label) ? (r[f.k] ?? '') : ''])) } }
async function save() {
  busy.value = true
  try {
    const body = Object.fromEntries(FIELDS.map(f => [f.k, String(edit.value[f.k] ?? '').trim() || null]))
    await guardedFetch(`/api/v2/alert-contacts/${encodeURIComponent(edit.value.api_id)}`, { method: 'PUT', body })
    showToast(`${edit.value.api_id} 已儲存`, 'success')
    edit.value = null
    await load()
  } catch (e: any) { showToast(`儲存失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { busy.value = false }
}
</script>

<template>
  <div class="box">
    <p v-if="err" class="err">{{ err }}</p>
    <template v-if="data">
      <div class="hd">
        <b>告警聯絡資訊（APID）</b>
        <span class="muted">共 {{ data.total }} 個 APID・人工覆寫 {{ data.overridden }} 個・{{ data.import ? `最後匯入 ${data.import.at}（${data.import.file}，${data.import.rows} 筆）` : '尚未匯入' }}</span>
        <NuxtLink to="/v2/settings?st=import" class="lnk">到資料匯入 ›</NuxtLink>
        <InfoNote>告警事件、首頁今日、夜間速查的告警列都會帶這五欄。每一欄的取值順序固定：管理員在這裡手動改的（人工覆寫）＞「資料匯入」頁匯入的告警聯絡資訊＞業務系統對照表（只有系統名稱、單位、分級）＞都沒有就寫「未提供」。重新匯入不會蓋掉人工覆寫；要回到匯入的值，把那一欄清空再儲存。分級只能是第一類、第二類、第三類；email 可以填多個，用分號隔開。範例檔在「資料匯入」頁下載。</InfoNote>
      </div>
      <div class="cards">
        <button v-for="f in FIELDS" :key="f.k" type="button" class="card" :class="{ on: miss === f.label }" @click="miss = miss === f.label ? '' : f.label">
          <span>缺 {{ f.label }}</span><b>{{ data.missing[f.label] }}</b>
        </button>
      </div>
      <div class="bar">
        <input v-model.lazy="kw" type="search" placeholder="搜尋 APID、團隊、單位、系統、email" aria-label="搜尋">
        <label class="ck"><input v-model="ovr" type="checkbox"> 只看人工覆寫</label>
        <span class="muted">顯示 {{ rows.length }}／{{ data.total }}</span>
      </div>
      <form v-if="edit" class="ed" @submit.prevent="save">
        <b>改 {{ edit.api_id }}</b>
        <label v-for="f in FIELDS" :key="f.k">{{ f.label }}
          <input v-model="edit[f.k]" :placeholder="edit.cur[f.k] ? `目前：${edit.cur[f.k]}（${edit.cur[f.k + '_src']}）` : '未提供'">
        </label>
        <button type="submit" class="btn on" :disabled="busy">{{ busy ? '儲存中…' : '儲存' }}</button>
        <button type="button" class="btn" @click="edit = null">取消</button>
        <InfoNote>只填要改的欄位；空白＝這一欄不覆寫（用匯入或業務系統對照表的值）。每次儲存都留紀錄（誰、何時、改前、改後）。</InfoNote>
      </form>
      <V2Table :rows="rows" prefix="K" :max="50" empty="沒有符合條件的 APID" :cols="COLS">
        <template #cell-act="{ row }"><button v-if="data.can_edit" type="button" class="mini" @click="openEdit(row)">改</button></template>
      </V2Table>
      <details v-if="data.snapshots?.length" class="snaps">
        <summary>匯入前快照 {{ data.snapshots.length }} 份（可還原）</summary>
        <V2Table :rows="data.snapshots" prefix="S" :max="20" :cols="SNAP_COLS">
          <template #cell-act="{ row }"><button v-if="data.can_edit" type="button" class="mini" :disabled="busy" @click="restore(row)">還原</button></template>
        </V2Table>
      </details>
    </template>
    <p v-else-if="!err" class="muted">讀取中…</p>
  </div>
</template>

<style scoped>
.box { display: flex; flex-direction: column; gap: 10px; }
.hd { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); margin: 0; }
.lnk { font-size: 13px; color: var(--link); text-decoration: none; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; }
.card { text-align: left; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 8px 12px; display: flex; flex-direction: column; cursor: pointer; font-family: inherit; color: var(--ink); }
.card span { font-size: 12px; color: var(--ink-soft); }
.card b { font-family: var(--disp); font-size: 20px; }
.card.on { border-color: var(--brand); background: var(--mint); }
.bar { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; }
.bar input[type=search] { border: 1px solid var(--border); border-radius: 8px; padding: 5px 10px; font-family: inherit; min-width: 240px; background: var(--card); color: var(--ink); }
.ck { font-size: 13px; display: flex; gap: 4px; align-items: center; }
.ed { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; background: var(--sub); border: 1px solid var(--line); border-radius: var(--radius); padding: 10px 12px; font-size: 13px; }
.ed label { display: flex; flex-direction: column; gap: 2px; }
.ed input { border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; font-family: inherit; min-width: 160px; background: var(--card); color: var(--ink); }
.btn { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.btn.on { border-color: var(--brand); background: var(--brand); color: var(--type-on); }
.mini { font-family: inherit; font-size: 12px; padding: 1px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
</style>
