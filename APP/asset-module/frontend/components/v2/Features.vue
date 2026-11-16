<script setup lang="ts">
// 📖 使用手冊「功能查詢」（2.043.10，V73：舊版 /features 搬過來）：這個系統有哪些功能、各在哪一頁，
// 含藏在頁面裡的動作與舊名稱（打舊頁名也找得到它現在在哪）。目錄來源見 utils/v2Features.ts。
import { featureCatalog } from '~/composables/useNav'
import { searchV2Features, v2FeatureCatalog } from '~/utils/v2Features'
const route = useRoute()
const router = useRouter()
// 項次照完整目錄的順序固定（篩選、搜尋後同一項還是同一個號碼）
const all = computed(() => v2FeatureCatalog(featureCatalog()).map((r, i) => ({ ...r, __no: i + 1 })))
const KIND: Record<string, string> = { page: '頁面', action: '頁面內的動作', old: '舊名稱' }
const fq = computed(() => (typeof route.query.fq === 'string' ? route.query.fq : ''))
const fk = computed(() => (['page', 'action', 'old'].includes(String(route.query.fk)) ? String(route.query.fk) : ''))
const kw = ref(fq.value)
function search() { router.replace({ query: { ...route.query, fq: kw.value.trim() || undefined } }) }
const hit = computed(() => searchV2Features(fq.value, all.value))
const rows = computed(() => hit.value.filter(r => !fk.value || r.kind === fk.value).map(r => ({ ...r, key: `${r.kind}|${r.label}|${r.to}`, kind_t: KIND[r.kind], kw_t: r.kw.join('、') })))
const n = (k: string) => hit.value.filter(r => r.kind === k).length
const to = (k: string) => ({ query: { ...route.query, fk: k || undefined } })
</script>

<template>
  <div>
    <form class="bar" @submit.prevent="search">
      <input v-model="kw" class="in" type="search" placeholder="搜功能名稱、說明或別名（例：ping、下線、EOS、匯出、舊頁名）" aria-label="功能查詢">
      <button type="submit" class="btn">查詢</button>
      <InfoNote>上方全站搜尋框查的是資料（主機、IP、系統…）；這裡查的是功能——包含藏在頁面裡的動作（例如「偵測存活」「標記非納管」），以及舊版頁面的名稱（打舊名稱也找得到它現在在哪一頁）。目錄跟選單、舊網址轉址表是同一份資料。</InfoNote>
    </form>
    <div class="bar">
      <NuxtLink class="chip" :class="{ on: !fk }" :to="to('')">全部 {{ hit.length }}</NuxtLink>
      <NuxtLink class="chip" :class="{ on: fk === 'page' }" :to="to('page')">頁面 {{ n('page') }}</NuxtLink>
      <NuxtLink class="chip" :class="{ on: fk === 'action' }" :to="to('action')">頁面內的動作 {{ n('action') }}</NuxtLink>
      <NuxtLink class="chip" :class="{ on: fk === 'old' }" :to="to('old')">舊名稱 {{ n('old') }}</NuxtLink>
    </div>
    <V2Table :rows="rows" prefix="F" :max="1000" row-key="key" empty="找不到符合的功能" export-name="功能查詢"
             :cols="[{ k: 'kind_t', label: '種類' }, { k: 'label', label: '功能', link: (r: any) => r.to }, { k: 'where', label: '在哪裡' }, { k: 'desc', label: '說明', min: 260 }, { k: 'kw_t', label: '別名' }]" />
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.in { font-family: inherit; font-size: 13px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; width: 420px; max-width: 100%; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.chip { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 999px; padding: 3px 10px; font-size: 13px; text-decoration: none; }
.chip.on { background: var(--mint); border-color: var(--brand); color: var(--brand-dark); }
</style>
