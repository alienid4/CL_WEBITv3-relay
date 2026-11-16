<script setup lang="ts">
// 2.1 主機查詢「重複登記」頁籤：同一台（machine_key，全站同一把尺）有多筆登記的組。
// 2.041.84：唯讀清單（GET /api/v2/hosts/duplicates）。
// 2.041.97：管理員多一條「釋放序號」流程（勾選 → 預覽 → 輸入確認 → 執行）與「釋放紀錄／還原」；
// 後端另外擋權限（ADMIN_WRITE），這裡只是不給一般帳號看到按鈕。
import { confirmOk, countsText, dupFilter, diffText, groupDiffs, groupDiffText, selectedRelease } from '~/composables/hostLookup'
import type { DupData, DupGroup, DupMember, ReleaseList, ReleasePreview } from '~/composables/hostLookup'

const { apiFetch } = useApi()
const { isAdmin, isViewer } = useAuth()
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
const opened = ref<Record<string, boolean>>({})
const diffsOf = (g: any) => groupDiffs(g as DupGroup, data.value?.members ?? [])
const host = (g: DupGroup) => `/v2/machine/${encodeURIComponent(g.key)}`
const gCols = [
  { k: 'hostname', label: '主機' },
  { k: 'kind_label', label: '類型' },
  { k: 'count', label: '筆數', mono: true },
  { k: 'origin_text', label: '來源' },
  // 2.060.x：「N 個」可展開看每個欄位、每筆登記的值；匯出是展開後的全文
  { k: 'diff_fields', label: '不同欄位', text: (g: DupGroup) => groupDiffText(g, data.value?.members ?? []) },
  { k: 'systems', label: '業務系統' },
  { k: 'dismiss', label: '操作', text: () => '' },
]
const mCols = [
  { k: 'group_no', label: '所屬組', text: (m: DupMember) => `R${m.group_no}` },
  { k: 'serial', label: '資產編號', mono: true },
  { k: 'how', label: '來源' },
  { k: 'created_at', label: '建立時間', mono: true },
  { k: 'api_id', label: '業務系統', mono: true },
  { k: 'diff', label: '不同的值', text: diffText, brief: (m: DupMember) => { const t = diffText(m); return t.length > 40 ? t.slice(0, 40) : '' } },
]
// 畫面放不下的欄只在匯出（2026-11-15 畫面重看：每張表 ≤7 欄）
const gXcols = [{ k: 'ip', label: 'IP' }, { k: 'extra_rows', label: '多出筆數' }, { k: 'serials', label: '成員資產編號' }]
const mXcols = [{ k: 'updated_at', label: '更新時間' }, { k: 'from_import', label: '有匯入來源紀錄' }, { k: 'strong_ids', label: '強識別碼' }]

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
  { k: 'serial', label: '釋放的序號', mono: true },
  { k: 'kept_serials', label: '保留的序號', mono: true },
  { k: 'rows', label: '刪除筆數', mono: true },
  { k: 'state', label: '狀態' },
  { k: 'act', label: '操作', text: (r: any) => (r.restorable ? '還原' : '—') },
]
const rXcols = [{ k: 'actor', label: '操作人' }, { k: 'hostname', label: '主機名稱' }, { k: 'ip', label: 'IP' }, { k: 'from_cia', label: '來自 CIA' },
  { k: 'counts', label: '各項筆數', text: (r: any) => countsText(r.counts ?? {}) }, { k: 'state_note', label: '狀態說明', text: (r: any) => (r.restored_at ? `${r.restored_by ?? '—'} ${r.restored_at}` : r.reason ?? '') }]
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

// ===== 這組不是重複（2.057.0，V61 ⑤；原舊 /assets 重複登記管理面板）：純搬移同一支 POST /api/assets/duplicates/dismiss、
// 同一個 body { hostname, ip }、同一句確認；只影響這份清單，不刪任何資料。唯讀帳號不顯示（後端另外擋）。
const dismissBusy = ref('')
async function dismissGroup(g: DupGroup) {
  const ok = confirm(`確定要把「${g.hostname} / ${g.ip}」這組標記為「不是重複登記」嗎？\n之後這組不會再出現在這份清單裡。`)
  if (!ok) return
  dismissBusy.value = g.key
  try {
    await apiFetch('/api/assets/duplicates/dismiss', {
      method: 'POST',
      body: { hostname: g.hostname, ip: g.ip },
    })
    showToast('已標記為不是重複登記', 'success')
    await load()
  } catch (err: any) {
    const d = err?.data?.detail
    showToast((typeof d === 'string' ? d : d?.message) ?? '標記失敗，請稍後再試', 'error')
  } finally {
    dismissBusy.value = ''
  }
}
</script>

<template>
  <section class="dp">
    <div class="bar">
      <b>重複登記</b>
      <InfoNote>同一台有兩筆以上現役登記。只有「疑似真重複」是重複；一機多系統、VIP 或用途分列、堆疊／機箱元件都是真實登記。來源：掃描收編、自動納管、匯入或手動新增。
        管理員可勾同一組的序號釋放：先預覽，刪除前留快照，可在「釋放紀錄」還原；每組至少留一筆。來自 CIA 的序號要同步改 CIA 清冊，不然下次匯入會再出現。</InfoNote>
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
      <V2Table :rows="groups" :cols="gCols" :export-cols="gXcols" prefix="R" unit="組" empty="目前沒有重複登記的組" :max="100">
        <template #cell-hostname="{ row }"><NuxtLink :to="host(row as any)"><b>{{ (row as any).hostname }}</b></NuxtLink><div class="sm">{{ (row as any).ip || '—' }}</div></template>
        <template #cell-count="{ row }">{{ (row as any).count }}<div v-if="(row as any).extra_rows" class="sm">多 {{ (row as any).extra_rows }} 筆</div></template>
        <template #cell-diff_fields="{ row }">
          <span v-if="!(row as any).diff_fields.length" class="muted">無差異</span>
          <template v-else>
            <button type="button" class="more" :aria-expanded="!!opened[(row as any).key]" @click="opened = { ...opened, [(row as any).key]: !opened[(row as any).key] }">{{ (row as any).diff_fields.length }} 個 {{ opened[(row as any).key] ? '▾' : '▸' }}</button>
            <dl v-if="opened[(row as any).key]" class="dd">
              <template v-for="d in diffsOf(row)" :key="d.field"><dt>{{ d.label }}</dt>
                <dd v-for="v in d.values" :key="v.serial"><span class="mono">{{ v.serial }}</span>：{{ v.value || '（空白）' }}</dd></template>
            </dl>
          </template>
        </template>
        <template #cell-dismiss="{ row }">
          <button v-if="!isViewer" type="button" class="btn sm" :disabled="dismissBusy === row.key" @click="dismissGroup(row)">不是重複</button>
        </template>
      </V2Table>
      <h4>每筆登記明細<InfoNote>「所屬組」對應上表的項次。</InfoNote></h4>
      <div v-if="isAdmin" class="act">
        <button type="button" class="btn" :disabled="busy || !picked.serials.length || !!pickMsg" @click="doPreview">預覽釋放（已勾選 {{ picked.serials.length }} 筆）</button>
        <span v-if="pickMsg" class="err">{{ pickMsg }}</span>
      </div>
      <V2Table v-model:selected="sel" :rows="members" :cols="mCols" :export-cols="mXcols" prefix="M" unit="筆" empty="沒有登記明細" :max="100" :selectable="isAdmin" row-key="serial" />

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
        <h4>釋放紀錄／還原<InfoNote>每個被釋放的序號一列。「可還原」＝把當時的快照原樣寫回；序號已被其他資產使用、或與現有資料衝突時會拒絕，不會覆蓋任何資料。較早的紀錄只存了資產主檔，無法完整還原。</InfoNote></h4>
        <p v-if="relErr" class="err" role="alert">{{ relErr }}</p>
        <V2Table v-else-if="rel" :rows="rel.items" :cols="rCols" :export-cols="rXcols" prefix="X" unit="筆" empty="目前沒有釋放紀錄" :max="100">
          <template #cell-merged_at="{ row }">{{ row.merged_at }}<div class="sm">{{ row.actor || '—' }}</div></template>
          <template #cell-serial="{ row }"><b class="mono">{{ row.serial }}</b><div class="sm">{{ row.hostname || '—' }}<template v-if="row.ip">・{{ row.ip }}</template><template v-if="row.from_cia">・來自 CIA</template></div></template>
          <template #cell-rows="{ row }">{{ row.rows }}<div class="sm">{{ countsText(row.counts ?? {}) }}</div></template>
          <template #cell-state="{ row }">{{ row.state }}<div v-if="row.restored_at || row.reason" class="sm">{{ row.restored_at ? `${row.restored_by ?? '—'} ${row.restored_at}` : row.reason }}</div></template>
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
.sm { color: var(--ink-aux); font-size: 12px; }
.more { font-family: inherit; font-size: 12px; padding: 0 8px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); cursor: pointer; }
.dd { margin: 4px 0 0; font-size: 12px; } .dd dt { font-weight: 700; color: var(--ink); margin-top: 4px; } .dd dd { margin: 0 0 0 10px; color: var(--ink-soft); }
.tagc { display: inline-block; margin-left: 6px; font-size: 12px; padding: 0 8px; border-radius: 10px; background: var(--warn-soft); color: var(--warn-text); }
.cf { font-size: 13px; color: var(--ink); display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.cf input { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; min-width: 200px; }
</style>
