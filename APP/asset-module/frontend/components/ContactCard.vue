<script setup lang="ts">
// 出事聯絡卡（v1.499.0）：資產頁、4-1 系統主機部署圖右側、4-2 檢查清單共用。
// 沒登錄一律顯示「未登錄」，不留白、不猜。維護資訊標出來源層級（本機／系統／型號／合約匯入）。
// 管理員可以就地改：給 serial 改「本機」層、給 apiId 改「系統」層；值班電話一律掛系統（APID）。
const props = defineProps<{ serial?: string | null; apiId?: string | null; compact?: boolean }>()

interface Field { value: string | null; source: string | null }
interface Card {
  scope: { type: string; serial?: string; model?: string | null; api_id?: string; name?: string | null
    machines?: number; machines_with_info?: number }
  people: { owners: { api_id: string; name: string | null; department: string | null }[]
    custodians: string[]; ap_users: string[]; registered: { name: string; phone: string | null; department: string | null }[] }
  maint: Record<string, Field>
  oncall: { api_id: string; name: string | null; phone: string | null; note: string | null
    updated_by: string | null; updated_at: string | null }[]
  contract_band: string | null
  can_edit: boolean
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const card = ref<Card | null>(null)
const err = ref('')
const editing = ref(false)
const saving = ref(false)
const draft = reactive<Record<string, string>>({})
const ocDraft = reactive<Record<string, string>>({})
const ocEditing = ref<string | null>(null)

const MAINT = [
  ['vendor', '原廠／維護廠商'], ['phone', '維護電話'], ['contact', '窗口'],
  ['contract_no', '合約編號'], ['contract_end', '合約到期'],
] as const

async function load() {
  err.value = ''
  const q = props.serial ? `asset_serial=${encodeURIComponent(props.serial)}`
    : props.apiId ? `api_id=${encodeURIComponent(props.apiId)}` : ''
  if (!q) { card.value = null; return }
  try {
    card.value = await apiFetch<Card>(`/api/contacts/card?${q}`)
  } catch (e: any) {
    err.value = e?.data?.detail ?? e?.message ?? '載入失敗'
  }
}
watch(() => [props.serial, props.apiId], load, { immediate: true })

const level = computed(() => (props.serial ? 'asset' : 'system'))
const levelKey = computed(() => props.serial || props.apiId || '')
// 只有「這一層自己登錄的」才帶進編輯框；別層繼承來的不帶，免得存下去變成本層的值
const ownSource = computed(() => (props.serial ? '本機（手動）' : `系統 ${props.apiId}（手動）`))

function startEdit() {
  for (const [k] of MAINT) draft[k] = card.value?.maint[k]?.source === ownSource.value ? (card.value.maint[k].value ?? '') : ''
  draft.note = card.value?.maint.note?.source === ownSource.value ? (card.value.maint.note.value ?? '') : ''
  editing.value = true
}
async function save() {
  saving.value = true
  try {
    await apiFetch('/api/contacts/entry', { method: 'PUT', body: { level: level.value, key: levelKey.value, ...draft } })
    showToast('已儲存', 'success')
    editing.value = false
    await load()
  } catch (e: any) {
    showToast(e?.data?.detail ?? '儲存失敗', 'error')
  } finally {
    saving.value = false
  }
}
function startOc(api: string, phone: string | null, note: string | null) {
  ocDraft.phone = phone ?? ''
  ocDraft.note = note ?? ''
  ocEditing.value = api
}
async function saveOc() {
  if (!ocEditing.value) return
  saving.value = true
  try {
    await apiFetch('/api/contacts/oncall', { method: 'PUT', body: { api_id: ocEditing.value, ...ocDraft } })
    showToast('值班電話已儲存', 'success')
    ocEditing.value = null
    await load()
  } catch (e: any) {
    showToast(e?.data?.detail ?? '儲存失敗', 'error')
  } finally {
    saving.value = false
  }
}
const ownersText = computed(() => (card.value?.people.owners ?? [])
  .map((o) => [o.name, o.department && `（${o.department}）`].filter(Boolean).join('')).join('、'))
</script>

<template>
  <div class="cc" :class="{ compact }">
    <div class="cc-h">
      <b>出事聯絡</b>
      <InfoNote>
        負責人＝業務系統的 AP 負責人；保管人、AP 承辦＝資產登記欄位。維護資訊依序取：本機（手動）→ 合約匯入（序號／主機名）→
        系統（手動）→ 型號（手動）→ 合約匯入（型號）；括號是來源。值班電話掛在系統（APID）。管理員可在這裡或「聯絡資訊」頁補。
      </InfoNote>
      <span v-if="card?.contract_band" class="band">{{ card.contract_band }}</span>
    </div>
    <p v-if="err" class="bad">{{ err }}</p>
    <template v-else-if="card">
      <dl class="kv">
        <dt>負責人</dt><dd>{{ ownersText || '未登錄' }}</dd>
        <dt>保管人</dt><dd>{{ card.people.custodians.join('、') || '未登錄' }}</dd>
        <dt>AP 承辦</dt><dd>{{ card.people.ap_users.join('、') || '未登錄' }}</dd>
        <template v-if="card.people.registered.length">
          <dt>登記人員</dt>
          <dd>
            <div v-for="p in card.people.registered" :key="p.name">
              {{ p.name }} <span class="mono">{{ p.phone || '電話未登錄' }}</span>
            </div>
          </dd>
        </template>
      </dl>

      <dl v-if="!editing" class="kv">
        <template v-for="[k, lbl] in MAINT" :key="k">
          <dt>{{ lbl }}</dt>
          <dd>
            <span v-if="card.maint[k]?.value" :class="{ mono: k === 'phone' || k === 'contract_end' }">{{ card.maint[k].value }}</span>
            <span v-else class="none">未登錄</span>
            <span v-if="card.maint[k]?.source" class="src">{{ card.maint[k].source }}</span>
          </dd>
        </template>
      </dl>
      <div v-else class="form">
        <label v-for="[k, lbl] in MAINT" :key="k">{{ lbl }}
          <input v-model="draft[k]" :placeholder="k === 'contract_end' ? 'YYYY-MM-DD' : ''">
        </label>
        <label>備註<input v-model="draft.note"></label>
        <div class="acts">
          <button class="btn pri" :disabled="saving" @click="save">{{ saving ? '儲存中…' : '儲存' }}</button>
          <button class="btn" :disabled="saving" @click="editing = false">取消</button>
        </div>
      </div>
      <p v-if="card.scope.type === 'system'" class="cov">
        <NuxtLink :to="`/contacts?tab=todo&api=${encodeURIComponent(card.scope.api_id || '')}`">
          本系統 {{ card.scope.machines }} 台，{{ card.scope.machines_with_info }} 台有維護資料
        </NuxtLink>
      </p>

      <dl class="kv">
        <template v-for="o in card.oncall" :key="o.api_id">
          <dt>值班電話<span v-if="card.oncall.length > 1" class="src">{{ o.api_id }}</span></dt>
          <dd>
            <template v-if="ocEditing === o.api_id">
              <input v-model="ocDraft.phone" class="oc" placeholder="電話">
              <input v-model="ocDraft.note" class="oc" placeholder="備註">
              <button class="btn pri" :disabled="saving" @click="saveOc">存</button>
              <button class="btn" :disabled="saving" @click="ocEditing = null">取消</button>
            </template>
            <template v-else>
              <span v-if="o.phone" class="mono">{{ o.phone }}</span><span v-else class="none">未登錄</span>
              <span v-if="o.note" class="src">{{ o.note }}</span>
              <button v-if="card.can_edit" class="lnk" @click="startOc(o.api_id, o.phone, o.note)">改</button>
            </template>
          </dd>
        </template>
        <template v-if="!card.oncall.length"><dt>值班電話</dt><dd class="none">未登錄（未對應系統）</dd></template>
      </dl>
      <div v-if="card.can_edit && !editing" class="acts">
        <button class="btn" @click="startEdit">補{{ serial ? '本機' : '系統' }}維護資料</button>
        <NuxtLink class="btn" to="/contacts?tab=entries">型號層級／全部</NuxtLink>
      </div>
    </template>
    <p v-else class="none">載入中…</p>
  </div>
</template>

<style scoped>
.cc { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; font-size: 13px; }
.cc-h { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.band { margin-left: auto; font-size: 11px; padding: 1px 8px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); }
.kv { display: grid; grid-template-columns: 92px 1fr; gap: 4px 8px; margin: 6px 0; padding-top: 6px; border-top: 1px solid var(--line); }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; color: var(--ink); word-break: break-all; }
.src { margin-left: 6px; font-size: 11px; color: var(--muted); background: var(--sub); border: 1px solid var(--line); border-radius: 6px; padding: 0 5px; }
.none { color: var(--muted); }
.bad { color: var(--bad); }
.mono { font-family: ui-monospace, Consolas, monospace; }
.form { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 10px; margin: 6px 0; }
.form label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 2px; }
.form input, .oc { padding: 4px 6px; border: 1px solid var(--border-strong); border-radius: 6px; font: inherit; color: var(--ink); background: var(--card); }
.oc { width: 120px; margin-right: 4px; }
.acts { display: flex; gap: 6px; grid-column: 1 / -1; margin-top: 4px; flex-wrap: wrap; }
.btn { font: inherit; font-size: 12px; padding: 4px 10px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; text-decoration: none; }
.btn.pri { background: var(--brand); color: #fff; border-color: var(--brand); }
.btn:disabled { opacity: .6; cursor: progress; }
.lnk { font: inherit; font-size: 12px; border: 0; background: none; color: var(--link); cursor: pointer; margin-left: 6px; }
.cov { margin: 4px 0; font-size: 12px; }
.cov a { color: var(--link); }
.compact .kv { grid-template-columns: 80px 1fr; }
</style>
