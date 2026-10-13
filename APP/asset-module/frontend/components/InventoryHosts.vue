<script setup lang="ts">
// 盤點主機清單（共用元件，2026-09-16）。
//
// 使用者看到帳號盤點只有彙總卡片時說：「我只知道四台? 不知道哪四台，應該是先列出哪幾台，
// 我再進去點後才是那一台的全部帳號資訊」，接著補了適用範圍：**「每個盤點都是這概念」**。
//
// 彙總數字對得起來也沒用——人要做的事是「去那台上面改」，
// 所以每一種盤點都先給主機清單，點進去才是那台的明細。
//
// 三頁共用這一支：服務、軟體、EOS。各頁各寫一份遲早會有一頁漏掉機房、
// 另一頁的「最後盤點」取到不同欄位。

const props = defineProps<{
  /** service / software / eos */
  kind: 'service' | 'software' | 'eos'
  /** 點主機時要帶到哪（會加上 ?ip=）；不給就只連到資產詳細頁 */
  drillTo?: string
}>()

interface HostRow {
  ip: string; asset_serial: string | null; hostname: string | null
  os: string | null; environment: string | null; physical_location: string | null
  items?: number; collected_at?: string | null; registered?: boolean
  exposed?: number; infra?: number; guessed?: number
  os_eos?: string | null; os_status?: string | null
  device_model?: string | null; hw_eos?: string | null; hw_status?: string | null
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const rt = useRuntimeConfig()

const rows = ref<HostRow[]>([])
const loading = ref(true)
const kw = ref('')
const open = ref(true)

const LABEL = { service: '服務', software: '軟體', eos: 'EOS' }[props.kind]

async function load() {
  loading.value = true
  try {
    const r = await apiFetch<{ items: HostRow[] }>(`/api/inventory/${props.kind}/hosts`)
    rows.value = r.items ?? []
  } catch (e: any) {
    rows.value = []
    showToast(`${LABEL}盤點主機清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally { loading.value = false }
}
onMounted(load)
defineExpose({ reload: load })

const shown = computed(() => {
  const q = kw.value.trim().toLowerCase()
  if (!q) return rows.value
  return rows.value.filter((h) => [h.ip, h.hostname, h.asset_serial, h.os,
                                   h.environment, h.physical_location, h.device_model]
    .some((v) => (v || '').toString().toLowerCase().includes(q)))
})
const { sortKey, sortDir, toggle, sorted } = useSort(shown, 'ip')

function hostLink(h: HostRow) {
  return props.drillTo ? { path: props.drillTo, query: { ip: h.ip } } : null
}
function statusTone(s: string | null | undefined) {
  if (!s) return ''
  if (s.includes('已過') || s === 'eol') return 'bad'
  if (s.includes('即將') || s === 'soon') return 'warn'
  return 'ok'
}
</script>

<template>
  <section class="ih">
    <div class="hd">
      <button class="tg" type="button" @click="open = !open">
        {{ open ? '▾' : '▸' }} 盤點到的主機 <b>{{ rows.length }}</b> 台
      </button>
      <InfoNote>
        <template v-if="kind === 'eos'">
          查得到 EOS 日期的主機。<b>查不到的不列</b>——「沒有資料」不等於「還在支援」，
          混在一起會讓人以為已經查過了。
        </template>
        <template v-else>
          這一輪實際收到{{ LABEL }}資料的就是這幾台。<b>點主機</b>進去看那一台的完整{{ LABEL }}清單。<br><br>
          沒出現在這裡的機器代表這一輪<b>沒收到</b>（還沒納管、收不到、或被排除），
          不是「這台沒有{{ LABEL }}」——這兩件事要做的處置完全不同。
        </template>
      </InfoNote>
      <span class="spacer" />
      <template v-if="open">
        <input v-model="kw" class="kw" placeholder="搜 IP／主機名／機房／OS" />
        <a class="btn small" :href="`${rt.public.apiBase}/api/inventory/${kind}/hosts/export`">⬇ 匯出 Excel</a>
      </template>
    </div>

    <template v-if="open">
      <p v-if="loading" class="dim">載入中…</p>
      <div v-else class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
              <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
              <SortTh k="physical_location" :active="sortKey" :dir="sortDir" @sort="toggle">機房</SortTh>
              <SortTh k="environment" :active="sortKey" :dir="sortDir" @sort="toggle">環境</SortTh>
              <SortTh k="os" :active="sortKey" :dir="sortDir" @sort="toggle">作業系統</SortTh>
              <template v-if="kind === 'eos'">
                <SortTh k="os_eos" :active="sortKey" :dir="sortDir" @sort="toggle">OS EOS</SortTh>
                <SortTh k="device_model" :active="sortKey" :dir="sortDir" @sort="toggle">設備型號</SortTh>
                <SortTh k="hw_eos" :active="sortKey" :dir="sortDir" @sort="toggle">硬體 EOS</SortTh>
              </template>
              <template v-else>
                <SortTh k="items" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">{{ LABEL }}筆數</SortTh>
                <SortTh v-if="kind === 'service'" k="exposed" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">對外曝露</SortTh>
                <SortTh v-if="kind === 'service'" k="guessed" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">靠埠號猜的</SortTh>
                <SortTh k="collected_at" :active="sortKey" :dir="sortDir" @sort="toggle">最後盤點</SortTh>
              </template>
            </tr>
          </thead>
          <tbody>
            <tr v-for="h in sorted" :key="h.ip">
              <td class="mono">
                <NuxtLink v-if="hostLink(h)" class="dl" :to="hostLink(h)!" :title="`只看 ${h.ip} 的${LABEL}`">{{ h.ip }}</NuxtLink>
                <template v-else>{{ h.ip }}</template>
                <span v-if="h.registered === false" class="tag bad" title="收到資料，但資產庫查不到這個 IP">未登記</span>
              </td>
              <td>
                <NuxtLink v-if="h.asset_serial" class="dl" :to="`/assets/${encodeURIComponent(h.asset_serial)}`"
                          title="看這台的資產詳細頁">{{ h.hostname || '—' }}</NuxtLink>
                <template v-else>{{ h.hostname || '—' }}</template>
              </td>
              <td>{{ h.physical_location || '—' }}</td>
              <td>{{ h.environment || '—' }}</td>
              <td class="ell" :title="h.os || ''">{{ h.os || '—' }}</td>
              <template v-if="kind === 'eos'">
                <td><span v-if="h.os_eos" class="tag" :class="statusTone(h.os_status)">{{ h.os_eos }}</span><span v-else class="dim">—</span></td>
                <td class="ell" :title="h.device_model || ''">{{ h.device_model || '—' }}</td>
                <td><span v-if="h.hw_eos" class="tag" :class="statusTone(h.hw_status)">{{ h.hw_eos }}</span><span v-else class="dim">—</span></td>
              </template>
              <template v-else>
                <td class="num">
                  <NuxtLink v-if="hostLink(h)" class="dl" :to="hostLink(h)!">{{ h.items }}</NuxtLink>
                  <template v-else>{{ h.items }}</template>
                </td>
                <td v-if="kind === 'service'" class="num" :class="{ badn: (h.exposed ?? 0) > 0 }">{{ h.exposed ?? 0 }}</td>
                <td v-if="kind === 'service'" class="num dim" title="行程名收不到時只能靠埠號猜——這些不是機器講的">{{ h.guessed ?? 0 }}</td>
                <td class="mono dim sm">{{ h.collected_at || '—' }}</td>
              </template>
            </tr>
            <tr v-if="!sorted.length">
              <td :colspan="kind === 'eos' ? 8 : (kind === 'service' ? 9 : 7)" class="dim">
                <template v-if="kw">沒有符合「{{ kw }}」的主機</template>
                <template v-else-if="kind === 'eos'">目前沒有任何主機查得到 EOS 日期</template>
                <template v-else>這一輪沒有收到任何主機的{{ LABEL }}資料</template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

<style scoped>
.ih { margin-bottom: 18px; }
.hd { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.tg { background: none; border: none; padding: 0; cursor: pointer; font-size: 15px;
      font-weight: 600; color: var(--ink); }
.spacer { flex: 1; }
.kw { padding: 5px 9px; border: 1px solid var(--border-strong); border-radius: 5px;
      background: var(--card); color: var(--ink); font-size: 13px; min-width: 200px; }
.ell { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tag { font-size: 11px; padding: 1px 7px; border-radius: 9px; background: rgba(0,0,0,.05); color: var(--ink-soft); }
.tag.ok { background: rgba(0,128,106,.1); color: var(--brand-dark); }
.tag.warn { background: var(--warn-soft); color: var(--warn-text); }
.tag.bad { background: var(--bad-soft, rgba(200,40,40,.1)); color: var(--bad); }
.badn { color: var(--bad); font-weight: 700; }
.sm { font-size: 11.5px; }
.mono { font-family: ui-monospace, monospace; }
.dim { color: var(--muted); }
</style>
