<script setup lang="ts">
// 全域搜尋：戰情室頂端一個框找遍資產／服務／業務系統／未登記主機。
//
// 幾個刻意的行為：
//  - 分組顯示：搜「3306」時「哪台在聽 3306」跟「哪台叫 3306」是不同的東西，不混在一起
//  - 300ms debounce：邊打邊送會對後端打出一堆沒人要看的查詢
//  - 鍵盤可用：↑↓ 選、Enter 進、Esc 收（搜尋框如果只能用滑鼠，等於慢一倍）
//  - 「/」快捷鍵聚焦，跟大多數工具一致
interface Item { title: string; subtitle: string; to: string }
interface Group { key: string; label: string; items: Item[]; more: boolean; more_to: string }
// 2026-08-27：後端多回這三樣，用來回答「為什麼查不到」。
// 使用者的批評是「全文代表 DB 我都可以查，不是我說一個案例多一個功能」——
// 光是後端搜得到不夠，畫面上要**說得出這次搜了什麼、哪個字沒中**，
// 否則使用者看到 0 筆時分不出是「沒有這個東西」還是「我打錯字」。
interface Term { term: string; match: string[]; hits: number }
interface SearchResp {
  query: string
  groups: Group[]
  terms?: Term[]
  searched_tables?: string[]
  excluded_tables?: string[]
  total_hits?: number
}

const { apiFetch } = useApi()
const router = useRouter()

const q = ref('')
const groups = ref<Group[]>([])
const terms = ref<Term[]>([])
const searchedTables = ref<string[]>([])
const excludedTables = ref<string[]>([])
const totalHits = ref(0)
const showScope = ref(false)      // 「搜了哪些表」預設收起來，要看才展開
const open = ref(false)
const loading = ref(false)
const cursor = ref(-1)
const boxRef = ref<HTMLElement | null>(null)
const inputRef = ref<HTMLInputElement | null>(null)

// 攤平成一維，鍵盤上下鍵才好走（畫面仍照組顯示）
const flat = computed(() => groups.value.flatMap(g => g.items))

let timer: any = null
watch(q, (v) => {
  clearTimeout(timer)
  cursor.value = -1
  if (!v.trim()) { groups.value = []; open.value = false; return }
  timer = setTimeout(search, 300)
})

async function search() {
  loading.value = true
  try {
    const r = await apiFetch<SearchResp>('/api/search', { query: { q: q.value.trim() } })
    groups.value = r.groups
    terms.value = r.terms ?? []
    searchedTables.value = r.searched_tables ?? []
    excludedTables.value = r.excluded_tables ?? []
    totalHits.value = r.total_hits ?? 0
    open.value = true
  } catch {
    // 搜尋失敗就當沒結果，不要在下拉裡塞紅字嚇人
    groups.value = []
    terms.value = []
    totalHits.value = 0
  } finally {
    loading.value = false
  }
}

function go(to: string) {
  open.value = false
  q.value = ''
  router.push(to)
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') { open.value = false; inputRef.value?.blur(); return }
  if (!open.value || flat.value.length === 0) return
  if (e.key === 'ArrowDown') { e.preventDefault(); cursor.value = (cursor.value + 1) % flat.value.length }
  else if (e.key === 'ArrowUp') { e.preventDefault(); cursor.value = (cursor.value - 1 + flat.value.length) % flat.value.length }
  else if (e.key === 'Enter') {
    e.preventDefault()
    const item = cursor.value >= 0 ? flat.value[cursor.value] : flat.value[0]
    if (item) go(item.to)
  }
}

// 「/」聚焦搜尋框；在輸入框裡打「/」當然不算
function onGlobalKey(e: KeyboardEvent) {
  const el = e.target as HTMLElement
  const typing = el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)
  if (e.key === '/' && !typing) { e.preventDefault(); inputRef.value?.focus() }
}
function onDocClick(e: MouseEvent) {
  if (boxRef.value && !boxRef.value.contains(e.target as Node)) open.value = false
}
onMounted(() => {
  document.addEventListener('keydown', onGlobalKey)
  document.addEventListener('click', onDocClick)
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onGlobalKey)
  document.removeEventListener('click', onDocClick)
  clearTimeout(timer)
})

// 完全沒命中的關鍵字。這是「查不到 ≠ 沒有」的具體實作：
// 搜「測試區 vc」得 0 筆時，要看得出是「測試區」這個字 0 筆，
// 而不是兩個字都有但沒有交集——前者改個字就找得到，後者是真的沒有。
const deadTerms = computed(() => terms.value.filter((t) => t.hits === 0))
// 有展開同義詞的關鍵字。**不可以安靜地擴大範圍**：多出來的結果要說得出來源。
const expanded = computed(() => terms.value.filter((t) => t.match.length > 1))

// 攤平索引 → 給每組的每一項算出全域游標位置，highlight 才對得上
function indexOf(gi: number, ii: number) {
  let n = 0
  for (let i = 0; i < gi; i++) n += groups.value[i].items.length
  return n + ii
}
</script>

<template>
  <div ref="boxRef" class="gs">
    <span class="ic">⌕</span>
    <input
      ref="inputRef"
      v-model="q"
      class="in"
      type="search"
      placeholder="搜尋資產、IP、服務、埠、業務系統、人員、機櫃…"
      @keydown="onKey"
      @focus="q.trim() && (open = true)"
    />
    <kbd v-if="!q" class="hint">/</kbd>

    <div v-if="open" class="panel">
      <p v-if="loading" class="st">搜尋中…</p>
      <p v-else-if="groups.length === 0" class="st">
        找不到「{{ q }}」。
        <template v-if="deadTerms.length">
          <br>
          <b class="dead">「{{ deadTerms.map((t) => t.term).join('」「') }}」完全沒有命中</b>
          <template v-if="terms.length > deadTerms.length">
            ——其他關鍵字有命中（{{ terms.filter((t) => t.hits > 0)
              .map((t) => `${t.term} ${t.hits}`).join('、') }}），
            所以是這幾個字的問題，改掉再試一次。
          </template>
        </template>
        <template v-else>
          <br>每個關鍵字單獨都有命中，但<b>沒有同時符合全部條件</b>的資料。
          試著少打一個字。
        </template>
      </p>
      <template v-else>
        <div v-for="(g, gi) in groups" :key="g.key" class="grp">
          <div class="ghd">{{ g.label }}</div>
          <button
            v-for="(it, ii) in g.items"
            :key="`${g.key}-${ii}`"
            class="row"
            :class="{ on: cursor === indexOf(gi, ii) }"
            type="button"
            @click="go(it.to)"
            @mouseenter="cursor = indexOf(gi, ii)"
          >
            <span class="t">{{ it.title }}</span>
            <span class="s">{{ it.subtitle }}</span>
          </button>
          <button v-if="g.more" class="more" type="button" @click="go(g.more_to)">
            看全部 {{ g.label }} →
          </button>
        </div>
      </template>

      <!-- 這一段是為了讓數字說得出來源，不是裝飾。
           使用者 2026-08-27：「全文代表 DB 我都可以查」——那就要看得出
           「這次到底搜了什麼範圍」，不然「查不到」永遠有第二種解釋。 -->
      <div v-if="terms.length" class="foot">
        <div v-if="expanded.length" class="exp">
          同義詞：
          <span v-for="t in expanded" :key="t.term" class="expone">
            <b>{{ t.term }}</b> → {{ t.match.slice(1).join('、') }}
          </span>
        </div>
        <div v-if="deadTerms.length && groups.length" class="dead">
          ⚠ 「{{ deadTerms.map((t) => t.term).join('」「') }}」沒有命中任何資料，
          結果是靠其他關鍵字撈到的
        </div>
        <button class="scope" type="button" @click.stop="showScope = !showScope">
          共 {{ totalHits }} 筆 · 搜了 {{ searchedTables.length }} 張表
          {{ showScope ? '▲' : '▼' }}
        </button>
        <div v-if="showScope" class="scopelist">
          <p><b>搜了：</b>{{ searchedTables.join('、') }}</p>
          <p class="ex">
            <b>刻意不搜：</b>{{ excludedTables.join('、') }}
            —— 裡面是登入 token、密碼雜湊、含真實值的對照表，不該出現在搜尋結果
          </p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.foot { border-top: 1px solid var(--border); margin-top: 4px; padding: 6px 10px 8px;
  font-size: 11px; line-height: 1.7; opacity: .85; }
.foot .exp { color: var(--ink-aux); }
.foot .expone { margin-right: 10px; }
.foot .dead { color: var(--warn); }
.foot .scope { background: none; border: none; padding: 2px 0; cursor: pointer;
  color: var(--ink-aux); font-size: 11px; font-family: inherit; }
.foot .scope:hover { text-decoration: underline; }
.foot .scopelist { margin-top: 4px; color: var(--ink-aux); word-break: break-all; }
.foot .scopelist p { margin: 2px 0; }
.foot .scopelist .ex { opacity: .8; }
.st .dead { color: var(--warn); }
.gs { position: relative; display: flex; align-items: center; gap: 7px;
  background: rgba(15,23,42,.06); border: 1px solid var(--border);
  border-radius: 10px; padding: 6px 10px; min-width: 300px; }
.gs:focus-within { border-color: rgba(0,145,66,.5); box-shadow: 0 0 0 3px rgba(0,145,66,.1); }
.ic { opacity: .55; font-size: 15px; }
.in { flex: 1; background: none; border: none; outline: none; color: inherit; font-size: 13px; font-family: inherit; }
.in::-webkit-search-cancel-button { filter: invert(.6); }
.hint { font-size: 10px; opacity: .4; border: 1px solid var(--border); border-radius: 4px; padding: 0 5px; }

/* 背景一定要用 --card-solid 而不是 --card：
   --card 是 rgba(15,23,42,.04) 的「玻璃卡」，用在浮層上會讓底下的頁面整片透上來，
   看起來就像選單裡的文字自己疊在一起——實際上疊上來的是下方的按鈕與表格。
   （2026-07-29 實際回報：搜尋建議「文字重疊」，查了兩輪才發現是這個。） */
.panel { position: absolute; top: calc(100% + 8px); left: 0; right: 0; z-index: 60;
  background: var(--card-solid, #101c19); border: 1px solid var(--border); border-radius: 12px;
  box-shadow: 0 18px 44px rgba(0,0,0,.5); max-height: 60vh; overflow-y: auto; padding: 6px; min-width: 380px; }
.st { padding: 14px 12px; font-size: 12px; color: var(--muted); margin: 0; line-height: 1.6; }
.ghd { font-size: 10px; text-transform: uppercase; letter-spacing: 1.2px; color: var(--muted); padding: 8px 10px 4px; }
.row { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; width: 100%;
  background: none; border: none; color: inherit; text-align: left; cursor: pointer;
  padding: 7px 10px; border-radius: 8px; font-family: inherit; }
.row.on { background: rgba(0,145,66,.12); }
/* 這兩行一定要自己給 line-height：不給就會繼承外層（版面各處設定不一），
   繼承到偏小的值時，兩行字會疊在一起，下拉選單看起來像壞掉。 */
.row .t { font-size: 13px; line-height: 1.45; }
.row .s { font-size: 11px; color: var(--muted); line-height: 1.45; }
.more { width: 100%; text-align: left; background: none; border: none; color: var(--brand-dark);
  font-size: 11px; padding: 5px 10px 9px; cursor: pointer; font-family: inherit; }
</style>
