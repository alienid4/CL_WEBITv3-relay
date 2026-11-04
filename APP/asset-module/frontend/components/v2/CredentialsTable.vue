<script setup lang="ts">
// 9-3 收集憑證清單：類型篩選、每欄可排序、項次（C1…，以名稱排序後固定）、匯出、「被引用」可展開並連到引用處、最近使用時間。
// 只顯示 metadata；密碼／密文後端從不回傳。操作（測試／編輯／刪除）交給父頁處理。
interface Ref { type: string; label: string; link: string }
interface Cred {
  id: number; name: string; kind: string; username: string; scope: string | null; note: string | null
  updated_at: string; has_secret: boolean; refs?: Ref[]; ref_count?: number; last_used_at?: string | null; last_used_ok?: boolean | null
}
const props = defineProps<{ creds: Cred[] }>()
const emit = defineEmits<{ test: [c: Cred]; edit: [c: Cred]; remove: [name: string] }>()
const KINDS = ['winrm', 'ssh', 'whatsup', 'vrops', 'storage']
const kind = ref('')
const rows = computed(() => props.creds
  .map((c, i) => ({ ...c, __no: i + 1, ref_total: c.ref_count ?? 0 }))
  .filter(c => !kind.value || c.kind === kind.value))
const cols = [
  { k: 'name', label: '名稱' }, { k: 'kind', label: '類型' }, { k: 'username', label: '帳號', mono: true },
  { k: 'scope', label: '適用範圍', mono: true, text: (r: any) => r.scope || '（通用）' },
  { k: 'has_secret', label: '密碼', text: (r: any) => (r.has_secret ? '已加密儲存' : '未設定') },
  { k: 'ref_total', label: '被引用', mono: true, text: (r: any) => (r.ref_total ? `${r.ref_total} 處：${(r.refs ?? []).map((x: Ref) => x.label).join('、')}` : '—') },
  { k: 'last_used_at', label: '最近使用', mono: true, text: (r: any) => (r.last_used_at ? `${r.last_used_at}${r.last_used_ok ? '' : '（失敗）'}` : '從未使用') },
  { k: 'updated_at', label: '更新時間', mono: true },
  { k: 'ops', label: '操作', text: () => '' },
]
</script>

<template>
  <div class="ct">
    <div class="flt">
      <label>類型
        <select v-model="kind" class="kind">
          <option value="">全部</option>
          <option v-for="k in KINDS" :key="k" :value="k">{{ k }}</option>
        </select>
      </label>
      <InfoNote>「被引用」是這組憑證現在被哪些整合、設備或主機挑去用，點開可前往設定；仍被引用的憑證不能刪除，要先到引用的地方改選別組。「最近使用」來自使用稽核（只記哪組、對哪台、成不成功，不含密碼）。</InfoNote>
    </div>
    <V2Table :rows="rows" :cols="cols" prefix="C" empty="尚未設定任何收集憑證" export-name="收集憑證">
      <template #cell-ref_total="{ row }">
        <details v-if="row.ref_total" class="refs">
          <summary>{{ row.ref_total }} 處</summary>
          <ul><li v-for="(r, i) in row.refs" :key="i"><NuxtLink :to="r.link">{{ r.label }}</NuxtLink></li></ul>
          <span v-if="row.ref_total > (row.refs ?? []).length" class="more">另有 {{ row.ref_total - (row.refs ?? []).length }} 處未列出</span>
        </details>
        <span v-else>—</span>
      </template>
      <template #cell-ops="{ row }">
        <span class="ops">
          <button type="button" class="btn small b-test" @click="emit('test', row)">測試</button>
          <button type="button" class="btn small b-edit" @click="emit('edit', row)">編輯</button>
          <button type="button" class="btn danger small b-del" @click="emit('remove', row.name)">刪除</button>
        </span>
      </template>
    </V2Table>
  </div>
</template>

<style scoped>
.flt { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; font-size: 13px; }
.kind { font-family: inherit; font-size: 13px; }
.refs summary { cursor: pointer; }
.refs ul { margin: 4px 0; padding-left: 16px; }
.more { color: var(--muted); font-size: 12px; }
.ops { display: flex; gap: 6px; white-space: nowrap; }
</style>
