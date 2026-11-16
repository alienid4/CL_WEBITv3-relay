<script setup lang="ts">
// 資產編輯（V20 第 3 批，2.054.0；原 pages/assets/[serial].vue 的就地編輯）。⚠D4 純搬移：
//   · 讀：GET /api/assets/{序號}、/api/assets/field-groups、/api/field-meta（欄位說明與選項，拿不到就退回純輸入框）
//   · 寫：只有 PUT /api/assets/{序號}，body＝{ fields }，跟舊頁同一套規則（utils/assetEdit：可編輯欄位、主鍵不給改、
//     資產狀態＝待 CIA 異動且只在真的改了才送）。驗證在後端（同一支），畫面不另加。
//   · 等級：後端這支是一般帳號以上（access_policy USER_WRITE）；只能看的帳號畫面寫「編輯限一般帳號以上」，不是按了才被擋。
// 儲存成功：回到檢視（舊頁也是存完就離開編輯）。
import { buildDraft, editableKeys, fieldLabel, PERSON_FIELDS, saveFields, sections, type FieldGroups } from '~/utils/assetEdit'

const props = defineProps<{ serial: string }>()
const emit = defineEmits<{ (e: 'saved'): void; (e: 'cancel'): void }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isViewer } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '請稍後重試' }

const hw = ref<Record<string, any> | null>(null)
const fg = ref<FieldGroups | null>(null)
const meta = ref<any>(null)
const loadErr = ref('')
const draft = reactive<Record<string, any>>({})
const saving = ref(false)
async function load() {
  try {
    const [d, g] = await Promise.all([apiFetch<any>(`/api/assets/${props.serial}`), apiFetch<FieldGroups>('/api/assets/field-groups')])
    hw.value = d.hardware; fg.value = g; loadErr.value = ''
    Object.keys(draft).forEach(k => delete draft[k])
    Object.assign(draft, buildDraft(d.hardware, editableKeys(g)))
  } catch (e: any) { loadErr.value = `資產資料載入失敗：${errMsg(e)}` }
  // 欄位說明／選項拿不到就退回純輸入框，不擋編輯（不是資料，不會被讀成「沒有」）——舊頁同一條
  try { meta.value = await apiFetch<any>('/api/field-meta') } catch { meta.value = null /* 刻意靜默：只是欄位說明與選項，拿不到退回純輸入框（舊頁同一條） */ }
}
await load()
const secs = computed(() => sections(fg.value))
const optionsOf = (k: string): string[] | null => meta.value?.fields?.[k]?.options ?? null
const helpOf = (k: string): string => meta.value?.fields?.[k]?.help ?? ''
const pendingNote = computed(() => (hw.value?.pending_status && hw.value.pending_status !== hw.value.asset_status ? hw.value.pending_status : ''))

async function save() {
  if (!hw.value) return
  saving.value = true
  try {
    const res = await apiFetch<any>(`/api/assets/${hw.value.asset_serial}`, { method: 'PUT', body: { fields: saveFields(draft, hw.value) } })
    hw.value = res.hardware
    showToast('已儲存', 'success')
    emit('saved')
  } catch (err: any) {
    showToast(`儲存失敗：${errMsg(err)}`, 'error')
  } finally { saving.value = false }
}
</script>

<template>
  <div>
    <p v-if="loadErr" class="err">{{ loadErr }}</p>
    <p v-else-if="isViewer" class="muted">編輯限一般帳號以上<InfoNote>你的帳號是「只能看」；要改資產欄位請找一般帳號或管理員。</InfoNote></p>
    <form v-else-if="hw" @submit.prevent="save">
      <div class="bar">
        <b>{{ hw.hostname || '（未登記主機名稱）' }}</b><span class="muted mono">{{ hw.asset_serial }}</span>
        <span class="sp" />
        <button type="submit" class="btn" :disabled="saving">{{ saving ? '儲存中…' : '儲存' }}</button>
        <button type="button" class="chip" :disabled="saving" @click="emit('cancel')">取消</button>
        <InfoNote>序號不能改（改序號等於換一台，會弄丟人員與軟體的關聯）。存的是這一筆登記。</InfoNote>
      </div>
      <fieldset class="sec">
        <legend>資產狀態</legend>
        <div class="f">
          <label for="ae-status">待 CIA 異動<InfoNote>資產狀態只有 CIA 可以寫：這裡選的值存成「待 CIA 異動」，不改資產狀態與台數，CIA 改好重匯才生效。沒改就不送。</InfoNote></label>
          <select id="ae-status" v-model="draft.asset_status" class="ein">
            <option value="">—</option>
            <option v-for="o in (optionsOf('asset_status') ?? [])" :key="o" :value="o">{{ o }}</option>
          </select>
          <span class="muted small">目前：{{ hw.asset_status || '—' }}<template v-if="pendingNote">・待異動：{{ pendingNote }}</template></span>
        </div>
      </fieldset>
      <fieldset v-for="s in secs" :key="s.k" class="sec">
        <legend>{{ s.label }}</legend>
        <div class="grid">
          <div v-for="key in s.keys" :key="key" class="f">
            <label :for="`ae-${key}`" :title="helpOf(key) || undefined">{{ fieldLabel(key) }}</label>
            <SearchableSelect v-if="PERSON_FIELDS.has(key)" :model-value="draft[key] ?? ''" :options="optionsOf(key) ?? []" :allow-new="true" :new-label="fieldLabel(key)"
                              placeholder="輸入姓名搜尋，查無可直接新增" @update:model-value="(v: any) => draft[key] = v" />
            <select v-else-if="optionsOf(key)" :id="`ae-${key}`" v-model="draft[key]" class="ein">
              <option value="">—</option>
              <option v-for="o in optionsOf(key)" :key="o" :value="o">{{ o }}</option>
            </select>
            <input v-else :id="`ae-${key}`" v-model="draft[key]" class="ein">
          </div>
        </div>
      </fieldset>
      <div class="bar end">
        <button type="submit" class="btn" :disabled="saving">{{ saving ? '儲存中…' : '儲存' }}</button>
        <button type="button" class="chip" :disabled="saving" @click="emit('cancel')">取消</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; font-size: 13px; }
.bar.end { justify-content: flex-end; }
.sp { flex: 1; }
.sec { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); padding: 10px 14px 12px; margin: 0 0 12px; }
.sec legend { font-size: 13px; font-weight: 700; color: var(--ink-soft); padding: 0 6px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px 16px; }
.f { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.f label { color: var(--ink-aux); }
.ein { font-family: inherit; font-size: 13px; padding: 5px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.muted { color: var(--muted); font-size: 13px; } .small { font-size: 12px; }
.mono { font-family: var(--disp); }
.err { color: var(--bad); }
</style>
