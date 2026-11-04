<script setup lang="ts">
// 2.1 主機查詢「重複登記」頁籤：同一台（machine_key，全站同一把尺）有多筆登記的組。
// 2.041.84：唯讀清單（GET /api/v2/hosts/duplicates）。
// 2.041.97：管理員多一條「釋放序號」流程（勾選 → 預覽 → 輸入確認 → 執行）與「釋放紀錄／還原」；
// 後端另外擋權限（ADMIN_WRITE），這裡只是不給一般帳號看到按鈕。
import { confirmOk, countsText, dupFilter, diffText, fieldLabel, selectedRelease } from '~/composables/hostLookup'
import type { DupData, DupGroup, DupMember, ReleaseList, ReleasePreview } from '~/composables/hostLookup'

const { apiFetch } = useApi()
const { isAdmin } = useAuth()
const { showToast } = useToast()
const data = ref<DupData | null>(null)
const err = ref('')
const kind = ref('all')
const rel = ref<ReleaseList | null>(null)
const relErr = ref('')
async function load() {
  data.value = null; err.value = ''
  try { data.value = await apiFetch<DupData>('/api/v2/hosts/duplicates') } catch (e: any) { err.value = `重複登記讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
async function loadReleases() {
  if (!isAdmin.value) return
  relErr.value = ''
  try { rel.value = await apiFetch<ReleaseList>('/api/v2/hosts/duplicates/releases') } catch (e: any) { relErr.value = `釋放紀錄讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(() => { load(); loadReleases() })
const groups = computed(() => dupFilter(data.value?.groups ?? [], kind.value).map(g => ({ ...g, __no: g.no })))
const members = computed(() => dupFilter(data.value?.members ?? [], kind.value).map((m, i) => ({ ...m, __no: i + 1 })))
const tiles = computed<[string, string, number][]>(() => (data.value?.kind_order ?? []).map(k => [k, data.value!.kind_labels[k] || k, data.value!.by_kind[k] ?? 0]))
function pick(k: string) { kind.value = kind.value === k ? 'all' : k }
const host = (g: DupGroup) => `/v2/machine/${encodeURIComponent(g.key)}`
const gCols = [
  { k: 'hostname', label: '主機名稱', link: host },
  { k: 'ip', label: 'IP', mono: true },
  { k: 'kind_label', label: '類型' },
  { k: 'count', label: '筆數', mono: true },
  { k: 'extra_rows', label: '多出筆數', mono: true },
  { k: 'serials', label: '成員資產編號', mono: true },
  { k: 'origin_text', label: '來源路徑' },
  { k: 'diff_fields', label: '不同欄位', text: (g: DupGroup) => (g.diff_fields.length ? g.diff_fields.map(fieldLabel).join('、') : '無差異') },
  { k: 'systems', label: '業務系統' },
]
const mCols = [
  { k: 'group_no', label: '所屬組', text: (m: DupMember) => `R${m.group_no}` },
  { k: 'serial', label: '資產編號', mono: true },
  { k: 'how', label: '來源路徑' },
  { k: 'created_at', label: '建立時間', mono: true },
  { k: 'updated_at', label: '更新時間', mono: true },
  { k: 'from_import', label: '有匯入來源紀錄' },
  { k: 'strong_ids', label: '強識別碼' },
  { k: 'api_id', label: '業務系統', mono: true },
  { k: 'diff', label: '各欄位值（僅有差異的欄位）', text: diffText, brief: (m: DupMember) => { const t = diffText(m); return t.length > 40 ? t.slice(0, 40) : '' } },
]

// ===== 釋放序號（管理員）=====
const sel = ref<Record<string, boolean>>({})
const picked = computed(() => selectedRelease(data.value?.members ?? [], sel.value))
const pickMsg = computed(() => {
  if (!picked.value.serials.length) return ''
  if (picked.value.keys.length > 1) return '一次只能釋放同一組的序號，請只勾選同一組的明細'
  return ''
})
const pv = ref<ReleasePreview | null>(null)
const pvErr = ref('')
const confirmText = ref('')
const busy = ref(false)
const done = ref('')
async function doPreview() {
  pv.value = null; pvErr.value = ''; confirmText.value = ''; done.value = ''
  busy.value = true
  try {
    pv.value = await apiFetch<ReleasePreview>('/api/v2/hosts/duplicates/release-preview', { method: 'POST', body: { key: picked.value.keys[0], release_serials: picked.value.serials } })
  } catch (e: any) { pvErr.value = `預覽失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { busy.value = false }
}
function cancelPreview() { pv.value = null; pvErr.value = ''; confirmText.value = '' }
const canRun = computed(() => !!pv.value && !pv.value.blocked && !busy.value && confirmOk(confirmText.value, pv.value.confirm_word, pv.value.release.map(r => r.serial)))
async function doRelease() {
  if (!pv.value || !canRun.value) return
  busy.value = true; pvErr.value = ''
  try {
    const r = await apiFetch<{ released: string[]; rows: number }>('/api/v2/hosts/duplicates/release', {
      method: 'POST', body: { key: pv.value.key, release_serials: pv.value.release.map(x => x.serial), confirm: confirmText.value.trim(), fingerprint: pv.value.fingerprint } })
    done.value = `已釋放 ${r.released.join('、')}（共刪除 ${r.rows} 筆資料，已留快照，可在下方「釋放紀錄」還原）`
    showToast('已釋放序號', 'success')
    pv.value = null; confirmText.value = ''; sel.value = {}
    await Promise.all([load(), loadReleases()])
  } catch (e: any) { pvErr.value = `釋放失敗，資料沒有變動：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { busy.value = false }
}

// ===== 釋放紀錄／還原 =====
const rCols = [
  { k: 'merged_at', label: '釋放時間', mono: true },
  { k: 'actor', label: '操作人' },
  { k: 'serial', label: '釋放的序號', mono: true },
  { k: 'hostname', label: '主機名稱' },
  { k: 'ip', label: 'IP', mono: true },
  { k: 'kept_serials', label: '保留的序號', mono: true },
  { k: 'from_cia', label: '來自 CIA' },
  { k: 'rows', label: '刪除筆數', mono: true },
  { k: 'counts', label: '各項筆數', text: (r: any) => countsText(r.counts ?? {}) },
  { k: 'state', label: '狀態', text: (r: any) => r.state + (r.restored_at ? `（${r.restored_by ?? '—'}，${r.restored_at}）` : r.reason ? `：${r.reason}` : '') },
  { k: 'act', label: '操作', text: (r: any) => (r.restorable ? '還原' : '—') },
]
const askRestore = ref<number | null>(null)
async function doRestore(id: number) {
  busy.value = true
  try {
    const r = await apiFetch<{ serial: string; restored_rows: number }>('/api/v2/hosts/duplicates/restore', { method: 'POST', body: { audit_id: id } })
    showToast(`已還原 ${r.serial}（${r.restored_rows} 筆）`, 'success')
    done.value = `已還原 ${r.serial}（${r.restored_rows} 筆）`
    askRestore.value = null
    await Promise.all([load(), loadReleases()])
  } catch (e: any) {
    askRestore.value = null
    showToast(`還原失敗，資料沒有變動：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally { busy.value = false }
}
</script>

<template>
  <section class="dp">
    <div class="bar">
      <b>重複登記</b>
      <InfoNote>同一台（依全站同一個「同一台」判定：主機名統一格式，缺主機名再比對 IP）有兩筆以上的現役登記，分三類：疑似真重複（系統、VIP、用途、名稱完全相同的筆數）、一機多系統、VIP 或用途分列。
        後兩類是真實的服務登記，不是重複。已標記為「不是重複」的組不列入。「來源路徑」依資產編號前綴判斷：ADOPT- 為掃描收編，AUTO- 為批次納管自動補，其餘為匯入或手動新增。
        有匯入來源紀錄欄為「未取得」代表此資料庫沒有來源紀錄表，不等於「否」。
        管理員可在「每筆登記明細」勾選同一組的序號後釋放：會先列出將刪除的內容讓你確認，刪除前自動留下完整快照，可在「釋放紀錄」還原；每組至少保留一筆。
        釋放的是這個序號在系統內所有以序號為鍵的資料（人員、軟體、收集結果、標籤等）；歷史與稽核紀錄保留不動。來自 CIA 清冊的序號釋放後，下次匯入 CIA 會再出現，需同步修改 CIA 清冊。</InfoNote>
    </div>
    <p v-if="err" class="err" role="alert">{{ err }}</p>
    <p v-else-if="!data" class="muted">讀取中…</p>
    <template v-else>
      <p v-if="done" class="ok" role="status">{{ done }}</p>
      <div class="tiles">
        <button type="button" class="tile" :class="{ on: kind === 'all' }" @click="kind = 'all'">全部 <b>{{ data.groups.length }}</b> 組</button>
        <button v-for="[k, lb, n] in tiles" :key="k" type="button" class="tile" :class="{ on: kind === k }" @click="pick(k)">{{ lb }} <b>{{ n }}</b></button>
        <span class="muted">疑似真重複可少 {{ data.extra_rows }} 筆<template v-if="data.dismissed_groups !== null">；已標記不是重複 {{ data.dismissed_groups }} 組（不在清單內）</template></span>
      </div>
      <V2Table :rows="groups" :cols="gCols" prefix="R" unit="組" empty="目前沒有重複登記的組" :max="100" />
      <h4>每筆登記明細<InfoNote>同一個類型篩選下，各組的每一筆登記。「所屬組」對應上表的項次。</InfoNote></h4>
      <div v-if="isAdmin" class="act">
        <button type="button" class="btn" :disabled="busy || !picked.serials.length || !!pickMsg" @click="doPreview">預覽釋放（已勾選 {{ picked.serials.length }} 筆）</button>
        <span v-if="pickMsg" class="err">{{ pickMsg }}</span>
      </div>
      <V2Table v-model:selected="sel" :rows="members" :cols="mCols" prefix="M" unit="筆" empty="沒有登記明細" :max="100" :selectable="isAdmin" row-key="serial" />

      <section v-if="isAdmin && (pv || pvErr)" class="pv" aria-label="釋放預覽">
        <p v-if="pvErr" class="err" role="alert">{{ pvErr }}</p>
        <template v-if="pv">
          <h4>將釋放：{{ pv.hostname }}（{{ pv.ip || '無 IP' }}）・{{ pv.kind_label }}</h4>
          <p class="line">這組目前 <b>{{ pv.count_before }}</b> 筆，釋放後剩 <b>{{ pv.count_after }}</b> 筆（保留：{{ pv.keep.join('、') }}）。共刪除 <b>{{ pv.totals.rows }}</b> 筆資料。</p>
          <ul class="warns"><li v-for="w in pv.warnings" :key="w">{{ w }}</li></ul>
          <div v-if="pv.blocked" class="blk" role="alert"><b>目前不能釋放，請先處理：</b><ul><li v-for="b in pv.blockers" :key="b">{{ b }}</li></ul></div>
          <ul class="items">
            <li v-for="it in pv.release" :key="it.serial">
              <b class="mono">{{ it.serial }}</b>
              <span v-if="it.from_cia" class="tagc">來自 CIA</span>
              <span class="muted">（{{ it.hostname }}／{{ it.ip || '無 IP' }}／{{ it.api_id || '無業務系統' }}）</span>
              將刪除：{{ countsText(it.counts) }}
              <span v-if="Object.keys(it.kept_refs).length" class="muted">；保留不動的歷史紀錄：{{ countsText(it.kept_refs) }}</span>
            </li>
          </ul>
          <label class="cf">請輸入「{{ pv.confirm_word }}」或要釋放的序號以確認：<input v-model="confirmText" type="text" autocomplete="off" aria-label="確認文字" @keyup.enter="doRelease"></label>
          <div class="act">
            <button type="button" class="btn danger" :disabled="!canRun" @click="doRelease">{{ busy ? '處理中…' : '執行釋放' }}</button>
            <button type="button" class="btn" :disabled="busy" @click="cancelPreview">取消</button>
          </div>
        </template>
      </section>

      <template v-if="isAdmin">
        <h4>釋放紀錄／還原<InfoNote>每個被釋放的序號一列。「可還原」＝把當時的快照原樣寫回；序號已被其他資產使用、或與現有資料衝突時會拒絕並說明，不會覆蓋任何資料。舊版（2.041.97 之前）留下的紀錄只有資產主檔一列，無法完整還原。</InfoNote></h4>
        <p v-if="relErr" class="err" role="alert">{{ relErr }}</p>
        <V2Table v-else-if="rel" :rows="rel.items" :cols="rCols" prefix="X" unit="筆" empty="目前沒有釋放紀錄" :max="100">
          <template #cell-act="{ row }">
            <template v-if="row.restorable">
              <template v-if="askRestore === row.id">
                <button type="button" class="btn danger sm" :disabled="busy" @click="doRestore(row.id)">確定還原 {{ row.serial }}</button>
                <button type="button" class="btn sm" :disabled="busy" @click="askRestore = null">取消</button>
              </template>
              <button v-else type="button" class="btn sm" :disabled="busy" @click="askRestore = row.id">還原</button>
            </template>
            <template v-else>—</template>
          </template>
        </V2Table>
      </template>
    </template>
  </section>
</template>

<style scoped>
.dp { display: flex; flex-direction: column; gap: 8px; }
.dp h4 { margin: 8px 0 0; font-size: 14px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.tiles { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.tile { border: 1px solid var(--border); background: var(--card); color: var(--ink-soft); border-radius: 999px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.tile.on { border-color: var(--brand); background: var(--mint); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; margin: 0; }
.ok { color: var(--good); font-size: 13px; margin: 0; }
.act { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.btn { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.btn.sm { font-size: 12px; padding: 2px 8px; }
.btn.danger { border-color: var(--bad); color: var(--bad); }
.btn:disabled { opacity: .45; cursor: default; }
.pv { border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--sub); padding: 10px 14px; display: flex; flex-direction: column; gap: 8px; }
.pv h4 { margin: 0; }
.line { margin: 0; font-size: 13px; color: var(--ink-soft); }
.blk { border: 1px solid var(--bad); border-radius: 6px; padding: 6px 10px; color: var(--bad); font-size: 13px; }
.blk ul { margin: 4px 0 0; padding-left: 18px; }
.warns { margin: 0; padding-left: 18px; font-size: 13px; color: var(--warn-text); }
.items { margin: 0; padding-left: 18px; font-size: 13px; color: var(--ink-soft); display: flex; flex-direction: column; gap: 4px; }
.mono { font-family: var(--disp); }
.tagc { display: inline-block; margin-left: 6px; font-size: 12px; padding: 0 8px; border-radius: 10px; background: var(--warn-soft); color: var(--warn-text); }
.cf { font-size: 13px; color: var(--ink); display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.cf input { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; min-width: 200px; }
</style>
