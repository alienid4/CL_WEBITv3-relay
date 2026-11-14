<script setup lang="ts">
// 資產明細與操作（V20，2.059.2）「標籤與分類」頁籤：舊 /assets/[serial] 的標籤框與「搬移分類」原樣搬來（後端不動）。
//   標籤：GET／POST /api/assets/{序號}/tags、DELETE /api/assets/{序號}/tags/{標籤}；系統只建議（附依據），採不採用是人決定
//   分類（OS 類型覆寫）：選項 GET /api/adopt/move-choices（跟 6.1 改分類同一份）、PUT／DELETE /api/os-type-overrides
//   只影響分類顯示與統計，不改「能不能納管」（那條走白名單）
const props = defineProps<{ detail: any; serial: string }>()
const emit = defineEmits<{ reload: [] }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isViewer } = useAuth()
const enc = encodeURIComponent
const why = (e: any) => e?.data?.detail ?? e?.message ?? e

// ---- 標籤 ----
const tags = ref<any[]>([])
const sugg = ref<any[]>([])
const known = ref<any[]>([])
const tagErr = ref('')
const loaded = ref(false)
const busy = ref(false)
const newTag = ref('')
const newNote = ref('')
async function loadTags() {
  try { const r = await apiFetch<any>(`/api/assets/${enc(props.serial)}/tags`); tags.value = r.tags ?? []; sugg.value = r.suggestions ?? []; known.value = r.known_tags ?? []; tagErr.value = '' } catch (e: any) { tagErr.value = `標籤載入失敗：${why(e)}` } finally { loaded.value = true }
}
onMounted(loadTags)
async function addTag(tag: string, note = '', source = 'manual') {
  if (!tag.trim() || busy.value) return
  busy.value = true; tagErr.value = ''
  try { await apiFetch(`/api/assets/${enc(props.serial)}/tags`, { method: 'POST', body: { tag: tag.trim(), note, source } }); newTag.value = ''; newNote.value = ''; await loadTags() } catch (e: any) { tagErr.value = `貼不上去：${why(e)}` } finally { busy.value = false }
}
async function removeTag(tag: string) {
  if (busy.value) return
  if (!confirm(`拿掉標籤「${tag}」？`)) return
  busy.value = true; tagErr.value = ''
  try { await apiFetch(`/api/assets/${enc(props.serial)}/tags/${enc(tag)}`, { method: 'DELETE' }); await loadTags() } catch (e: any) { tagErr.value = `刪不掉：${why(e)}` } finally { busy.value = false }
}
const tagRows = computed(() => tags.value.map((t, i) => ({ ...t, __no: i + 1, note_t: t.note || '（沒寫原因）', src: t.source === 'suggested' ? '採用建議' : '手動' })))
const tagCols = [{ k: 'tag', label: '標籤', link: (r: any) => `/v2/onboard?tab=onboarded&mtag=${enc(r.tag)}` }, { k: 'note_t', label: '為什麼' }, { k: 'src', label: '來源' }, { k: 'created_by', label: '誰貼的' }, { k: 'created_at', label: '時間', mono: true }, { k: 'op', label: '操作', text: () => '' }]

// ---- 分類（OS 類型覆寫）----
const cls = computed<any>(() => props.detail?.classify ?? null)
const moveOpen = ref(false)
const choices = ref<{ os_type: string; label: string; disabled: boolean }[]>([])
const target = ref('')
const reason = ref('')
const moveBusy = ref(false)
async function openMove() {
  target.value = ''; reason.value = ''; moveOpen.value = true
  try {
    const r = await apiFetch<any>('/api/adopt/move-choices')
    const out: any[] = []
    for (const t of r.tabs || []) {
      if (!t.movable) { out.push({ os_type: '', label: `${t.label}（連線狀態，不能指定）`, disabled: true }); continue }
      for (const c of t.choices) out.push({ os_type: c.os_type, label: t.choices.length > 1 ? `${t.label} — ${c.label}` : t.label, disabled: false })
    }
    choices.value = out
  } catch (e: any) { choices.value = []; showToast(`讀不到可選類別：${why(e)}`, 'error') }
}
async function submitMove() {
  const hw = props.detail?.hardware
  if (!hw || !target.value || !reason.value.trim()) return
  moveBusy.value = true
  try {
    await apiFetch('/api/os-type-overrides', { method: 'PUT', body: { hostname: hw.hostname, ip: hw.ip, asset_serial: hw.asset_serial, os_type: target.value, auto_os_type: cls.value?.os_type_auto ?? null, reason: reason.value.trim() } })
    moveOpen.value = false; emit('reload')
    showToast(`已搬到「${choices.value.find(c => c.os_type === target.value)?.label ?? target.value}」`, 'success')
  } catch (e: any) { showToast(`搬移失敗：${why(e)}`, 'error') } finally { moveBusy.value = false }
}
async function restoreAuto() {
  const hw = props.detail?.hardware
  if (!hw) return
  if (!confirm('還原成機器判定？（手動指定的分類會拿掉）')) return
  moveBusy.value = true
  try {
    const q = new URLSearchParams()
    if (hw.hostname) q.set('hostname', hw.hostname)
    if (hw.ip) q.set('ip', hw.ip)
    if (hw.asset_serial) q.set('asset_serial', hw.asset_serial)
    await apiFetch(`/api/os-type-overrides?${q.toString()}`, { method: 'DELETE' })
    emit('reload'); showToast('已還原成機器判定', 'success')
  } catch (e: any) { showToast(`還原失敗：${why(e)}`, 'error') } finally { moveBusy.value = false }
}
</script>

<template>
  <div>
    <h3 class="h3">分類<InfoNote>這台被分到哪一類、是機器判的還是人搬的。分類跟 6.1 納管漏斗、統計共用同一套；搬移只影響分類顯示與統計，不改「能不能納管」——那條走白名單，避免有人把設備搬成 Linux 之後系統跑去設備上建帳號。</InfoNote></h3>
    <p v-if="!cls" class="muted">這台沒有分類資料</p>
    <div v-else class="row">
      <span class="pill">{{ cls.os_type }}<template v-if="cls.os_detail">／{{ cls.os_detail }}</template></span>
      <span v-if="cls.os_type_override" class="pill warn" :title="`${cls.os_type_override.by || '—'} 於 ${cls.os_type_override.at} 搬的${cls.os_type_override.reason ? '：' + cls.os_type_override.reason : ''}`">人工指定（機器判：{{ cls.os_type_auto }}）</span>
      <template v-if="!isViewer">
        <button v-if="!moveOpen" type="button" class="b" data-test="move-open" @click="openMove">搬移分類</button>
        <button v-if="cls.os_type_override && !moveOpen" type="button" class="b" :disabled="moveBusy" data-test="move-restore" @click="restoreAuto">還原成機器判定</button>
      </template>
    </div>
    <div v-if="moveOpen" class="row">
      <select v-model="target" class="inp" aria-label="搬到哪一類"><option value="">選一個類別…</option><option v-for="c in choices" :key="c.label" :value="c.os_type" :disabled="c.disabled">{{ c.label }}</option></select>
      <input v-model="reason" class="inp wide" placeholder="為什麼搬（必填）" aria-label="搬移原因" />
      <button type="button" class="b primary" data-test="move-submit" :disabled="!target || !reason.trim() || moveBusy" @click="submitMove">送出</button>
      <button type="button" class="b" :disabled="moveBusy" @click="moveOpen = false">取消</button>
    </div>

    <h3 class="h3">標籤<InfoNote>標籤掛在這一台上（不是某一筆登記），從同一台的任何一筆序號進來都看得到同一組。系統只會建議並附依據，採不採用你決定；每個標籤都記誰貼的、什麼時候、為什麼。標籤不影響任何判定與統計，點標籤看有這個標籤的已納管主機。</InfoNote></h3>
    <p v-if="tagErr" class="err">{{ tagErr }}</p>
    <V2Table v-if="loaded" :rows="tagRows" :cols="tagCols" prefix="T" unit="個標籤" empty="還沒有標籤">
      <template #cell-op="{ row }: { row: any }"><button v-if="!isViewer" type="button" class="b sm" :disabled="busy" :aria-label="`拿掉 ${row.tag}`" @click="removeTag(row.tag)">拿掉</button></template>
    </V2Table>
    <p v-else class="muted">載入中…</p>
    <div v-if="!isViewer" class="row">
      <input v-model="newTag" class="inp" list="v2-known-tags" placeholder="新增標籤" aria-label="新增標籤" @keyup.enter="addTag(newTag, newNote)" />
      <datalist id="v2-known-tags"><option v-for="k in known" :key="k.tag" :value="k.tag">{{ k.machines }} 台用過</option></datalist>
      <input v-model="newNote" class="inp wide" placeholder="為什麼？" aria-label="標籤原因" @keyup.enter="addTag(newTag, newNote)" />
      <button type="button" class="b primary" data-test="tag-add" :disabled="busy || !newTag.trim()" @click="addTag(newTag, newNote)">加上去</button>
    </div>
    <div v-for="s in sugg" :key="s.tag" class="sug">
      <b>建議：{{ s.tag }}</b><span class="pill">{{ s.confidence }}</span>
      <button v-if="!isViewer" type="button" class="b sm" :disabled="busy" @click="addTag(s.tag, s.why, 'suggested')">採用</button>
      <InfoNote>依據：{{ s.why }}。怎麼變成證據：{{ s.next }}</InfoNote>
    </div>
  </div>
</template>

<style scoped>
.h3 { font-size: 15px; margin: 14px 0 6px; display: flex; align-items: center; gap: 6px; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0; font-size: 13px; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); font-size: 13px; }
.pill { font-size: 12px; padding: 2px 10px; border-radius: 10px; background: var(--mint); color: var(--brand-dark); }
.pill.warn { background: var(--warn-soft); color: var(--warn-text); }
.inp { font-family: inherit; font-size: 13px; padding: 5px 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); color: var(--ink); }
.inp.wide { min-width: 260px; }
.b { font-family: inherit; font-size: 13px; padding: 5px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.b.sm { font-size: 12px; padding: 2px 8px; }
.b.primary { background: var(--brand); border-color: var(--brand); color: var(--card); }
.b:disabled { opacity: .55; cursor: default; }
.sug { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; margin: 6px 0; padding: 6px 10px; border: 1px dashed var(--border-strong); border-radius: 8px; }
</style>
