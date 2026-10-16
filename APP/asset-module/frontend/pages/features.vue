<script setup lang="ts">
// 功能查詢（2026-09-16 使用者：「你還欠我一個 功能查詢頁面」「記得做功能搜尋」）。
//
// 上面那個全域搜尋查的是**資料**（資產、IP、服務…），查不到「功能」。
// 30 幾個頁面各自能做什麼、某個動作藏在哪一頁，沒人記得住——尤其是
// 「偵測存活」「標記非納管」這種不在選單上的動作，找不到就等於沒有。
//
// 目錄跟左側選單共用同一份 NAV（composables/useNav.ts），不另抄一份頁面清單。

const q = ref('')
const kindFilter = ref<'all' | 'page' | 'action'>('all')
// 編號照「實際顯示的選單」算（含使用者自訂過的順序），跟左側選單一致
const { visibleNav, loadNavLayout } = useVisibleNav()
onMounted(loadNavLayout)
const all = computed(() => featureCatalog(visibleNav.value))

const rows = computed(() => {
  let r = searchFeatures(q.value, all.value)
  if (kindFilter.value !== 'all') r = r.filter((x) => x.kind === kindFilter.value)
  return r
})

// 鐵規則：每一欄都可排序
const { sortKey, sortDir, toggle, sorted } = useSort(rows, 'group')

const pageCount = computed(() => all.value.filter((r) => r.kind === 'page').length)
const actionCount = computed(() => all.value.filter((r) => r.kind === 'action').length)

useHead({ title: '功能查詢' })
</script>

<template>
  <div class="wrap">
    <h2>功能查詢</h2>
    <p class="sub">
      這個系統有哪些功能、各自在哪一頁。
      <InfoNote>
        上方那個搜尋框查的是<b>資料</b>（資產、IP、服務、埠、業務系統、人員、機櫃）；<br>
        這一頁查的是<b>功能</b>——包含<b>藏在頁面裡的動作</b>（例如「偵測存活」「標記非納管」），
        那些不在左側選單上，不知道名字就找不到。<br><br>
        目錄跟左側選單是同一份資料，選單改了這裡就會跟著改。
      </InfoNote>
    </p>

    <div class="bar">
      <input v-model="q" class="kw" placeholder="搜功能名稱、說明或別名（例：ping、下線、EOS、匯出）" />
      <div class="segs">
        <button type="button" :class="{ on: kindFilter === 'all' }" @click="kindFilter = 'all'">
          全部（{{ all.length }}）
        </button>
        <button type="button" :class="{ on: kindFilter === 'page' }" @click="kindFilter = 'page'">
          頁面（{{ pageCount }}）
        </button>
        <button type="button" :class="{ on: kindFilter === 'action' }" @click="kindFilter = 'action'">
          頁面內的動作（{{ actionCount }}）
        </button>
      </div>
      <div class="spacer" />
      <span class="cnt">符合 {{ rows.length }} 項</span>
    </div>

    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <SortTh k="group" :active="sortKey" :dir="sortDir" @sort="toggle">分類</SortTh>
            <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">編號</SortTh>
            <SortTh k="label" :active="sortKey" :dir="sortDir" @sort="toggle">功能</SortTh>
            <SortTh k="desc" :active="sortKey" :dir="sortDir" @sort="toggle">說明</SortTh>
            <SortTh k="where" :active="sortKey" :dir="sortDir" @sort="toggle">在哪裡</SortTh>
            <th>前往</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in sorted" :key="r.kind + r.label + r.to">
            <td><span class="tag" :class="r.kind">{{ r.group }}</span></td>
            <td class="mono dim">{{ r.no || '—' }}</td>
            <td class="nm">
              <NuxtLink class="dl" :to="r.to">{{ r.label }}</NuxtLink>
              <div v-if="r.kw.length" class="kws">
                <span v-for="k in r.kw" :key="k" class="kwchip">{{ k }}</span>
              </div>
            </td>
            <td class="ds">{{ r.desc || '—' }}</td>
            <td class="wh">{{ r.where }}</td>
            <td><NuxtLink class="btn small ghost" :to="r.to">開啟</NuxtLink></td>
          </tr>
          <tr v-if="!sorted.length">
            <td colspan="6" class="dim">
              找不到「{{ q }}」。試試別的說法——例如 ping／存活、下線／停用、匯出／Excel。
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.wrap { padding: 18px 22px; }
h2 { margin: 0 0 4px; font-size: 20px; }
.sub { color: var(--ink-soft); font-size: 13px; margin: 0 0 14px;
       display: flex; align-items: center; gap: 6px; }
.bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
.kw { padding: 6px 10px; border: 1px solid var(--border-strong); border-radius: 6px;
      background: var(--card); color: var(--ink); min-width: 340px; font-size: 13px; }
.segs { display: flex; gap: 0; }
.segs button { padding: 5px 12px; border: 1px solid var(--border-strong); background: var(--card);
               color: var(--ink); cursor: pointer; font-size: 12.5px; }
.segs button:first-child { border-radius: 6px 0 0 6px; }
.segs button:last-child { border-radius: 0 6px 6px 0; }
.segs button + button { border-left: none; }
.segs button.on { background: var(--brand); color: #fff; border-color: var(--brand); }
.spacer { flex: 1; }
.cnt { color: var(--muted); font-size: 12.5px; }
.tag { font-size: 11.5px; padding: 1px 8px; border-radius: 10px; background: rgba(0,0,0,.05);
       color: var(--ink-soft); white-space: nowrap; }
.tag.action { background: var(--warn-soft); color: var(--warn-text); }
.nm { min-width: 180px; }
.ds { font-size: 12.5px; color: var(--ink-soft); max-width: 520px; }
.wh { font-size: 12px; color: var(--muted); max-width: 240px; }
.kws { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 3px; }
.kwchip { font-size: 10.5px; padding: 0 5px; border-radius: 7px; background: var(--sub); color: var(--muted); }
.mono { font-family: ui-monospace, monospace; }
.dim { color: var(--muted); }
</style>
