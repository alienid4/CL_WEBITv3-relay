<script setup lang="ts">
// 主機履歷的一個頁籤（影響範圍／硬體與位置／軟體與服務／帳號與安全／關聯設備／異動紀錄／原始資料／系統重建資料）。
// 頁籤下再一排小頁籤（每段的筆數或「無資料（原因）」，數字來自 /api/v2/machine/summary），點了才載那一段
// （/api/v2/machine/section；資料來源＝舊資產頁同一支計算，見後端 v2_sections.py）。
interface Sec { key: string; group: string; label: string; count: number | null; unknown: string | null; note: string | null; kind?: string; cols?: any[]; rows?: any[]; na?: boolean }
const props = defineProps<{ mkey: string; group: string; secs: Sec[]; total?: { known: boolean; text: string; link: string | null; deps_available?: boolean } | null }>()
const { apiFetch } = useApi()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
// na＝跟這台無關的段（例：AIX 主機的 SAN 段、非 OpenShift 的 OCP 段），不顯示（找碴 C18）；整組都無關才留著講原因
const list = computed(() => { const all = props.secs.filter(s => s.group === props.group); const rel = all.filter(s => !s.na); return rel.length ? rel : all })
const route = useRoute()
const pick = ref(String(route.query.sec || ''))   // 從概要點「同業務系統 N 台／同機櫃 N 台」直接開那一段（找碴 C37）
watch(() => route.query.sec, v => { if (v) pick.value = String(v) })
const current = computed(() => pick.value && list.value.some(s => s.key === pick.value) ? pick.value : (list.value.find(s => s.count)?.key || list.value[0]?.key || ''))
const data = ref<Record<string, Sec>>({})
const loading = ref(false)
const err = ref('')
async function load(name: string) {
  if (!name || data.value[name]) return
  loading.value = true
  err.value = ''
  try { data.value[name] = await apiFetch<Sec>('/api/v2/machine/section', { query: { key: props.mkey, name } }) } catch (e: any) { err.value = `載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { loading.value = false }
}
watch(current, load, { immediate: true })
watch(() => props.mkey, () => { data.value = {}; pick.value = ''; load(current.value) })
const tabText = (s: Sec) => (s.unknown ? '無資料' : String(s.count))
</script>

<template>
  <div class="hg">
    <div v-if="group === 'impact' && total" class="total">
      <b class="tl">{{ total.text }}</b>
      <span v-if="total.known && !total.deps_available" class="muted">上下游資料不足，實際影響可能更多</span>
      <InfoNote>與影響範圍查詢（3-2）同一份計算。下方依關係拆開：受影響業務系統（本系統＋上下游，含重要度與 AP 部門）、通知對象、承載的主機（ESXi 上的 VM、同一箱體的 LPAR）、使用其儲存、連入主機、相依元件。</InfoNote>
    </div>
    <p v-if="group === 'near'" class="muted"><InfoNote>同業務系統、同機櫃、同叢集的設備僅為關聯，不代表此主機停機會影響它們；影響請看「影響範圍」頁籤。</InfoNote></p>
    <div v-if="list.length > 1" class="subs">
      <button v-for="s in list" :key="s.key" type="button" class="sub" :class="{ on: current === s.key, unk: !!s.unknown }" :title="s.unknown || ''" @click="pick = s.key">{{ s.label }} <b>{{ tabText(s) }}</b></button>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="loading || !data[current]" class="muted">載入中…</p>
    <template v-else-if="data[current].kind === 'rebuild'">
      <div class="rbh"><span class="muted">{{ data[current].note }}</span><a class="act" :href="`${apiBase}/api/v2/machine/export.xlsx?key=${encodeURIComponent(mkey)}&only=rebuild`">匯出系統重建資料</a></div>
      <V2Unknown v-if="data[current].unknown" :why="data[current].unknown!" />
      <V2Rebuild v-else :rows="data[current].rows ?? []" />
    </template>
    <V2Section v-else :sec="data[current]" />
  </div>
</template>

<style scoped>
.hg { display: flex; flex-direction: column; gap: 10px; }
.total { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 8px 12px; border-radius: 10px; border: 2px solid var(--v2-danger); }
.tl { color: var(--st-missing); font-weight: 700; text-decoration: none; }
.subs { display: flex; gap: 6px; flex-wrap: wrap; border-bottom: 1px solid var(--line); padding-bottom: 8px; }
.sub { border: 0; background: none; padding: 4px 10px; border-radius: 8px; font-size: 13px; font-family: inherit; cursor: pointer; color: var(--ink-soft); }
.sub b { font-family: var(--disp); color: var(--ink); }
.sub.unk b { font-family: inherit; font-weight: 400; color: var(--st-unknown); }
.sub.on { background: var(--st-online-bg); color: var(--st-online); }
.rbh { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.act { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 10px; font-size: 13px; text-decoration: none; }
.muted { color: var(--muted); font-size: 13px; margin: 0; }
.err { color: var(--bad); }
</style>
