<script setup lang="ts">
// 5.2 儲存容量「已知例外」頁籤（2026-11-14）：Datastore／主機磁碟的「不提醒」清單。後端 capacity_exempt（單一判斷，
// 5.2、1.5、Teams 磁碟告警、1.4、首頁、每日異動都用同一份）。設定／編輯／移除／知道了只有管理員；稽核只增不改。
// 檢視放網址 xv＝active（生效中）｜notice（到期／失效待確認）｜all｜legacy（1.5 舊門檻調整）｜unmapped（對不到的磁碟）｜log；
// add＝預先選好要設例外的項目（kind:key，從 VMware Disk／實體主機磁碟的「設為例外」連過來）。
const emit = defineEmits<{ changed: [] }>()
const { apiFetch } = useApi()
const { isAdmin } = useAuth()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const data = ref<any>(null)
const err = ref('')
async function load() {
  try { data.value = await apiFetch<any>('/api/storage/exempt'); err.value = '' } catch (e: any) { err.value = `已知例外載入失敗：${errMsg(e)}` }
}
await load()

const VIEWS = ['active', 'notice', 'all', 'legacy', 'unmapped', 'log']
const view = computed(() => (VIEWS.includes(q('xv')) ? q('xv') : 'active'))
function go(patch: Record<string, string | undefined>) { router.replace({ query: { ...route.query, ...patch } }) }
const ST = { active: '生效中', removed: '已移除', expired: '已到期', void: '已失效' } as Record<string, string>
const DUR = (d: string) => (d === 'eos' ? '到 EOS 日' : `${d} 天`)
const today = computed(() => data.value?.today ?? '')
const fmt = (v: any, u: string) => (v == null ? '—' : `${Math.round(v * 10) / 10}${u}`)
const allRows = computed(() => (data.value?.rows ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, kind_t: r.kind === 'ds' ? 'Datastore' : '主機磁碟',
  st_t: r.status === 'active' && r.expires <= today.value ? '已到期（待系統更新）' : (ST[r.status] ?? r.status),
  pending: ['expired', 'void'].includes(r.status) && !r.acked_at, dur_t: DUR(r.duration),
  base_t: `用量 ${fmt(r.base_used_pct, '%')}・剩 ${fmt(r.base_free_gb, ' GB')}・共 ${fmt(r.base_cap_gb, ' GB')}`,
  by_t: `${r.created_by ?? '—'} ${r.created_at ?? ''}`, end_t: r.ended_at ? `${r.ended_by ?? ''} ${r.ended_at}：${r.end_note ?? ''}` : '' })))
const activeRows = computed(() => allRows.value.filter((r: any) => r.status === 'active'))
const noticeRows = computed(() => allRows.value.filter((r: any) => r.pending))
const legacyRows = computed(() => (data.value?.legacy ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1 })))
const unmappedRows = computed(() => (data.value?.unmapped ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1 })))
const ACT = { set: '設定', edit: '編輯', remove: '移除', expire: '到期', void: '失效', ack: '知道了' } as Record<string, string>
const logRows = computed(() => (data.value?.log ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1, act_t: ACT[x.action] ?? x.action })))
const shown = computed(() => (view.value === 'notice' ? noticeRows.value : view.value === 'all' ? allRows.value : activeRows.value))

// ===== 新增／編輯（管理員）=====
const cands = computed(() => data.value?.candidates ?? [])
const form = reactive({ open: false, id: 0 as number, pick: '', reason: '', duration: '90', note: '' })
const picked = computed(() => cands.value.find((c: any) => `${c.kind}:${c.key}` === form.pick) ?? null)
const editing = computed(() => (form.id ? allRows.value.find((r: any) => r.id === form.id) ?? null : null))
const eosOf = computed(() => (form.id ? cands.value.find((c: any) => editing.value && c.kind === editing.value.kind && c.key === editing.value.key) : picked.value))
const busy = ref(false)
function openAdd(pick = '') { Object.assign(form, { open: true, id: 0, pick, reason: '', duration: String(data.value?.default_days ?? 90), note: '' }) }
function openEdit(r: any) { Object.assign(form, { open: true, id: r.id, pick: '', reason: r.reason, duration: '', note: r.note ?? '' }) }
watch(() => q('add'), (v) => { if (v && isAdmin.value) { openAdd(v); go({ add: undefined }) } }, { immediate: true })
async function submit() {
  if (!form.reason.trim()) { showToast('原因必填', 'error'); return }
  busy.value = true
  try {
    if (form.id) {
      const r = await apiFetch<any>(`/api/storage/exempt/${form.id}/edit`, { method: 'POST', body: { reason: form.reason, duration: form.duration || null, note: form.note } })
      showToast(Object.keys(r.changed ?? {}).length ? '已更新' : '沒有變動', 'success')
    } else {
      if (!picked.value) { showToast('請選要設例外的項目', 'error'); busy.value = false; return }
      await apiFetch('/api/storage/exempt', { method: 'POST', body: { kind: picked.value.kind, key: picked.value.key, reason: form.reason, duration: form.duration, note: form.note || null } })
      showToast('已設為已知例外', 'success')
    }
    form.open = false
    await load(); emit('changed')
  } catch (e: any) { showToast(`失敗：${errMsg(e)}`, 'error') } finally { busy.value = false }
}
async function act(r: any, what: 'remove' | 'ack') {
  const note = what === 'remove' ? window.prompt(`移除「${r.label}」的例外，之後照常提醒。備註（可空白）：`, '') : ''
  if (note === null) return
  try {
    await apiFetch(`/api/storage/exempt/${r.id}/${what}`, { method: 'POST', body: what === 'remove' ? { note } : undefined })
    showToast(what === 'remove' ? '已移除' : '已確認', 'success')
    await load(); emit('changed')
  } catch (e: any) { showToast(`失敗：${errMsg(e)}`, 'error') }
}
const candLabel = (c: any) => `${c.kind === 'ds' ? 'Datastore' : '磁碟'}｜${c.label}｜用量 ${fmt(c.used_pct, '%')}${c.free_gb != null ? `・剩 ${fmt(c.free_gb, ' GB')}` : ''}｜${c.source}`
const candSorted = computed(() => [...cands.value].sort((a: any, b: any) => (b.used_pct ?? 0) - (a.used_pct ?? 0)))
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-else-if="data">
      <p v-if="data.error" class="err">{{ data.error }}</p>
      <div class="bar">
        <button type="button" class="chip" :class="{ on: view === 'active' }" @click="go({ xv: 'active' })">生效中 {{ activeRows.length }}</button>
        <button type="button" class="chip warnc" :class="{ on: view === 'notice' }" @click="go({ xv: 'notice' })">到期／失效待確認 {{ noticeRows.length }}</button>
        <button type="button" class="chip" :class="{ on: view === 'all' }" @click="go({ xv: 'all' })">全部紀錄 {{ allRows.length }}</button>
        <button type="button" class="chip" :class="{ on: view === 'legacy' }" @click="go({ xv: 'legacy' })">1.5 舊門檻調整 {{ legacyRows.length }}</button>
        <button type="button" class="chip" :class="{ on: view === 'unmapped' }" @click="go({ xv: 'unmapped' })">對不到的磁碟 {{ unmappedRows.length }}</button>
        <button type="button" class="chip" :class="{ on: view === 'log' }" @click="go({ xv: 'log' })">稽核 {{ logRows.length }}</button>
        <button v-if="isAdmin" type="button" class="chip on" @click="openAdd()">＋ 設定例外</button>
        <InfoNote>已知例外＝確定不用處理的 Datastore 或主機磁碟（例如已不使用、空間不會再增加）。生效中的不列「處理」、不進容量預警、首頁、1.4、每日異動、1.5 每日檢核，也不發 Teams 磁碟告警；容量總量照算，VMware Disk 頁籤標「例外」。只有管理員能設定、編輯、移除，原因必填；期限 {{ (data.presets ?? []).join('／') }} 天或「到 EOS 日」（主機查得到 EOS 才能選），最多 {{ data.max_days }} 天、沒有永久。設定時記下用量、剩餘、容量當基準：用量比基準多 {{ data.growth_pp }} 個百分點以上連續 {{ data.consec }} 次、剩餘少 {{ data.free_drop_pct }}% 以上、或容量變小，例外立刻失效（容量變大不影響）。到期或失效會在 1.4 追蹤處理出現一次，按「知道了」或重新設定才消失。主機磁碟以「同一台（machine_key）＋掛載點／磁碟機」對；WhatsUp 的 IP 對不到資產的磁碟不能設例外，列在「對不到的磁碟」。稽核紀錄只增不改。</InfoNote>
      </div>

      <form v-if="form.open" class="pane" @submit.prevent="submit">
        <p class="h3">{{ form.id ? `編輯例外：${editing?.label ?? ''}` : '設定已知例外' }}</p>
        <label v-if="!form.id">項目
          <select v-model="form.pick" class="inp wide" aria-label="要設例外的項目">
            <option value="">— 選一個 —</option>
            <option v-for="c in candSorted" :key="`${c.kind}:${c.key}`" :value="`${c.kind}:${c.key}`">{{ candLabel(c) }}</option>
          </select>
        </label>
        <label>原因（必填）<input v-model="form.reason" class="inp wide" maxlength="500" aria-label="原因"></label>
        <label>期限
          <select v-model="form.duration" class="inp" aria-label="期限">
            <option v-if="form.id" value="">不變（到 {{ editing?.expires }}）</option>
            <option v-for="d in data.presets" :key="d" :value="String(d)">{{ d }} 天</option>
            <option value="eos" :disabled="!eosOf?.eos_date">到 EOS 日{{ eosOf?.eos_date ? `（${eosOf.eos_date}，${eosOf.eos_source}）` : '（查不到 EOS 日）' }}</option>
          </select>
        </label>
        <label>備註<input v-model="form.note" class="inp wide" maxlength="500" aria-label="備註"></label>
        <p v-if="picked && !form.id" class="muted">基準：{{ candLabel(picked) }}</p>
        <div class="bar"><button type="submit" class="chip on" :disabled="busy">{{ busy ? '儲存中…' : '儲存' }}</button><button type="button" class="chip" @click="form.open = false">取消</button></div>
      </form>

      <V2Table v-if="['active', 'notice', 'all'].includes(view)" :rows="shown" prefix="E" empty="沒有已知例外" :max="500"
               :cols="[{ k: 'kind_t', label: '類別' }, { k: 'label', label: '名稱' }, { k: 'reason', label: '原因' }, { k: 'note', label: '備註' }, { k: 'dur_t', label: '期限' }, { k: 'expires', label: '到期日（起恢復）', mono: true }, { k: 'base_t', label: '設定時基準' }, { k: 'st_t', label: '狀態', tone: (r: any) => (r.pending ? 'test' : r.status === 'active' ? 'dr' : 'none') }, { k: 'end_t', label: '結束' }, { k: 'by_t', label: '設定人／時間', mono: true }, { k: 'ops', label: '操作' }]">
        <template #cell-ops="{ row }">
          <template v-if="isAdmin">
            <button v-if="row.status === 'active'" type="button" class="lnk" @click="openEdit(row)">編輯</button>
            <button v-if="row.status === 'active'" type="button" class="lnk" @click="act(row, 'remove')">移除</button>
            <button v-if="row.pending" type="button" class="lnk" @click="act(row, 'ack')">知道了</button>
            <button v-if="row.pending" type="button" class="lnk" @click="openAdd(`${row.kind}:${row.key}`)">重新設定</button>
          </template>
          <span v-else class="muted">—</span>
        </template>
      </V2Table>
      <V2Table v-else-if="view === 'legacy'" :rows="legacyRows" prefix="L" empty="沒有 1.5 舊門檻調整"
               :cols="[{ k: 'type', label: '類型' }, { k: 'ip', label: 'IP', mono: true }, { k: 'threshold', label: '改用門檻 %', mono: true }, { k: 'expires', label: '到期日', mono: true }, { k: 'reason', label: '原因' }]" />
      <V2Table v-else-if="view === 'unmapped'" :rows="unmappedRows" prefix="U" empty="沒有對不到的磁碟"
               :cols="[{ k: 'ip', label: 'IP', mono: true }, { k: 'label', label: '設備／磁碟' }, { k: 'item', label: '磁碟名稱' }, { k: 'used_pct', label: '用量 %', mono: true }]" />
      <V2Table v-else :rows="logRows" prefix="A" empty="還沒有稽核紀錄" :max="300"
               :cols="[{ k: 'at', label: '時間', mono: true }, { k: 'act_t', label: '動作' }, { k: 'actor', label: '誰' }, { k: 'kind', label: '類別' }, { k: 'key', label: '項目', mono: true }, { k: 'detail', label: '內容（舊→新）', brief: (r: any) => String(r.detail ?? '').slice(0, 40) }]" />
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; margin-bottom: 8px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.chip.warnc { border-color: var(--warn); color: var(--warn-text); } .chip.warnc.on { background: var(--warn-soft); }
.pane { display: grid; gap: 8px; margin: 0 0 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--sub); font-size: 13px; }
.pane label { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.inp { font-family: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.wide { min-width: min(520px, 100%); }
.h3 { margin: 0; font-weight: 700; }
.lnk { font-family: inherit; font-size: 12px; border: 0; background: none; color: var(--brand); cursor: pointer; padding: 0 4px; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
</style>
