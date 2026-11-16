<script setup lang="ts">
// 未登記主機「手動登記」成資產（批 23，V17 舊頁 /adopt「登記成資產」表單；⚠D4）純搬移：
//   欄位照 GET /api/field-meta 動態分區（技術層有值唯讀、沒值可補；業務層手填）；
//   預帶 IP／主機名稱／網段／MAC／VM 同舊頁 openAdopt；必填只檢查資產用途、環境別（同舊頁 submitAdopt）；
//   同一支 POST /api/assets/adopt、body { fields: {...form} }。只登記，不建收集帳號、不連目標機。
import { adoptPrefill } from '~/utils/adoptCollect'

interface FieldMeta { label: string; category: string; layer: 'tech' | 'biz'; required?: boolean; options?: string[]; help?: string }
interface Meta { categories: { key: string; label: string }[]; fields: Record<string, FieldMeta> }
const props = defineProps<{ host: { ip: string; hostname?: string | null; segment?: string | null; mac?: string | null; is_vm?: number | null } }>()
const emit = defineEmits<{ (e: 'done', ip: string): void; (e: 'close'): void }>()
const { apiFetch } = useApi()
const { showToast } = useToast()

const meta = ref<Meta | null>(null)
const metaErr = ref('')
const form = reactive<Record<string, any>>(adoptPrefill(props.host))
const saving = ref(false)
const submitAttempted = ref(false)
const isMissing = (key: string, required?: boolean) => submitAttempted.value && required && !form[key]
const fieldsOf = (cat: string) => Object.entries(meta.value?.fields ?? {}).filter(([, f]) => f.category === cat).map(([key, f]) => ({ key, ...f }))

onMounted(async () => {
  try { meta.value = await apiFetch<Meta>('/api/field-meta') }
  catch (e: any) { metaErr.value = `欄位設定讀取失敗：${e?.data?.detail ?? e?.message ?? '請稍後重試'}` }
})

async function submitAdopt() {
  submitAttempted.value = true
  if (!form.asset_purpose || !form.environment) { showToast('請填紅框的必填欄位：資產用途、環境別', 'warn'); return }
  saving.value = true
  try {
    await apiFetch('/api/assets/adopt', { method: 'POST', body: { fields: { ...form } } })
    showToast(`已納入管理：${props.host.ip}`, 'success')
    emit('done', props.host.ip)
  } catch (err: any) {
    showToast(`納入失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally { saving.value = false }
}
</script>

<template>
  <section class="card" data-test="adopt-form">
    <div class="bar">
      <b>手動登記成資產 — <span class="mono">{{ host.ip }}</span></b><span v-if="host.hostname" class="muted">（{{ host.hostname }}）</span>
      <InfoNote>只登記成資產、不建收集帳號。「自動」欄位是掃描帶進來的，沒有值可以手動補；「手填」是業務欄位。必填：資產用途、環境別。</InfoNote>
      <span class="sp" />
      <button type="button" class="btn2" @click="emit('close')">← 回清單</button>
    </div>
    <p v-if="metaErr" class="err" role="alert">{{ metaErr }}</p>
    <p v-else-if="!meta" class="muted">讀取欄位中…</p>
    <template v-else>
      <template v-for="cat in meta.categories" :key="cat.key">
        <h4>{{ cat.label }}</h4>
        <div class="grid">
          <label v-for="f in fieldsOf(cat.key)" :key="f.key" class="f">
            <span>{{ f.label }}<b v-if="f.required" class="req">必填</b><i class="layer">{{ f.layer === 'tech' ? '自動' : '手填' }}</i></span>
            <span v-if="f.layer === 'tech' && form[f.key]" class="ro">{{ form[f.key] }}</span>
            <input v-else-if="f.layer === 'tech'" v-model="form[f.key]" class="in" placeholder="未取得（可手動補）">
            <select v-else-if="f.options" v-model="form[f.key]" class="in" :class="{ missing: isMissing(f.key, f.required) }">
              <option value="">—</option>
              <option v-for="o in f.options" :key="o" :value="o">{{ o }}</option>
            </select>
            <input v-else v-model="form[f.key]" class="in" :class="{ missing: isMissing(f.key, f.required) }">
          </label>
        </div>
      </template>
      <div class="bar">
        <button type="button" class="btn" :disabled="saving" data-test="adopt-submit" @click="submitAdopt">{{ saving ? '登記中…' : '納入管理' }}</button>
        <button type="button" class="btn2" @click="emit('close')">取消</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; margin-bottom: 12px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; font-size: 13px; }
.sp { flex: 1; }
h4 { margin: 12px 0 6px; font-size: 13px; color: var(--ink-soft); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px 12px; }
.f { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--ink-aux); }
.req { color: var(--bad); font-weight: normal; margin-left: 4px; }
.layer { font-style: normal; color: var(--muted); margin-left: 4px; }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.in.missing { border-color: var(--bad); background: var(--bad-soft); }
.ro { font-size: 13px; padding: 4px 8px; border-radius: 6px; background: var(--sub); color: var(--ink-soft); }
.btn { font-family: inherit; font-size: 13px; padding: 5px 14px; border-radius: 8px; border: 1px solid var(--brand); background: var(--brand); color: var(--card); cursor: pointer; }
.btn:disabled { opacity: .5; cursor: default; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.muted { color: var(--muted); font-size: 12px; }
.mono { font-family: ui-monospace, monospace; }
.err { color: var(--bad); }
</style>
