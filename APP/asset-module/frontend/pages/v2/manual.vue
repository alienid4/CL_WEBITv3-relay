<script setup lang="ts">
// 使用手冊（設定群組最後一項）。內容頁，所以說明文字直接顯示（不收進 InfoNote）。
// 內容全部在 content/manual.ts；這頁只負責顯示：左側任務目錄（一次只顯示一節，不做一頁超長）、每節四段可展開收合、
// 示意圖（內嵌 SVG，之後可換成圖檔）、列印（列印時全部展開）。
import { MANUAL_SECTIONS, MANUAL_VERSION, manualBehind, pickSection } from '~/content/manual'
definePageMeta({ layout: 'v2' })
const route = useRoute()
const router = useRouter()
const { version, ensureLoaded } = useServerInfo()
await ensureLoaded()
const sid = computed({
  get: () => pickSection(MANUAL_SECTIONS, String(route.query.s || '')).id,
  set: (v: string) => router.replace({ query: { ...route.query, s: v } }),
})
const cur = computed(() => pickSection(MANUAL_SECTIONS, sid.value))
const behind = computed(() => manualBehind(MANUAL_VERSION, version.value))
// 列印：先把全部章節展開，列印完（或取消）再回到單節顯示
const printing = ref(false)
const shown = computed(() => (printing.value ? MANUAL_SECTIONS : [cur.value]))
function doPrint() {
  printing.value = true
  nextTick(() => window.print())
}
onMounted(() => window.addEventListener('afterprint', () => { printing.value = false }))
const open = reactive<Record<string, boolean>>({})
const isOpen = (id: string, part: string) => open[`${id}:${part}`] !== false
function toggle(id: string, part: string) { open[`${id}:${part}`] = !isOpen(id, part) }
const view = computed(() => (route.query.view === 'features' ? 'features' : 'manual'))
const PARTS = [{ k: 'when', t: '什麼時候用' }, { k: 'steps', t: '怎麼做' }, { k: 'sees', t: '會看到什麼' }, { k: 'notes', t: '注意事項' }]
</script>

<template>
  <V2Page width="wide">
    <template #title><h1 class="h1">⚙ 使用手冊</h1></template>
    <template #conclusion>
      <span>手冊內容更新到版本 <b class="mono">{{ MANUAL_VERSION }}</b></span>
      <span v-if="version" class="muted">　系統目前版本 <span class="mono">{{ version }}</span></span>
      <span v-if="behind" class="late">系統比手冊新，手冊可能還沒跟上，以畫面為準。</span>
    </template>
    <template #actions><button type="button" class="btn noprint" @click="doPrint">列印</button></template>

    <!-- V73（2.043.10）：功能查詢（舊版 /features）＝手冊頁的第二個檢視，?view=features -->
    <div class="views noprint">
      <NuxtLink class="vchip" :class="{ on: view !== 'features' }" :to="{ query: { ...route.query, view: undefined } }">使用手冊</NuxtLink>
      <NuxtLink class="vchip" :class="{ on: view === 'features' }" :to="{ query: { ...route.query, view: 'features' } }">功能查詢</NuxtLink>
    </div>
    <V2Features v-if="view === 'features'" />
    <div v-else class="man">
      <nav class="toc noprint" aria-label="手冊目錄">
        <div class="toc-h">任務目錄</div>
        <button v-for="s in MANUAL_SECTIONS" :key="s.id" type="button" class="toc-i" :class="{ on: s.id === cur.id }" @click="sid = s.id">
          <span class="no">{{ s.no }}</span>{{ s.title }}
        </button>
      </nav>

      <div class="body">
        <section v-for="s in shown" :id="`sec-${s.id}`" :key="s.id" class="sec">
          <h2 class="h2"><span class="no">{{ s.no }}</span>{{ s.title }}</h2>

          <div class="part">
            <button type="button" class="ph" :aria-expanded="isOpen(s.id, 'when')" @click="toggle(s.id, 'when')"><span class="ar">{{ isOpen(s.id, 'when') ? '▾' : '▸' }}</span>{{ PARTS[0].t }}</button>
            <p v-show="isOpen(s.id, 'when')" class="pb">{{ s.when }}</p>
          </div>
          <div class="part">
            <button type="button" class="ph" :aria-expanded="isOpen(s.id, 'steps')" @click="toggle(s.id, 'steps')"><span class="ar">{{ isOpen(s.id, 'steps') ? '▾' : '▸' }}</span>{{ PARTS[1].t }}</button>
            <ol v-show="isOpen(s.id, 'steps')" class="pb steps"><li v-for="(st, i) in s.steps" :key="i">{{ st }}</li></ol>
          </div>
          <div v-if="s.figures.length" class="figs">
            <figure v-for="f in s.figures" :key="f.id" class="fig">
              <img v-if="f.src" :src="f.src" :alt="f.alt">
              <!-- 內容是 content/manual.ts 自己產生的固定示意圖，不含使用者輸入 -->
              <div v-else class="svgbox" role="img" :aria-label="f.alt" v-html="f.svg" />
              <figcaption>{{ f.caption }}</figcaption>
            </figure>
          </div>
          <div class="part">
            <button type="button" class="ph" :aria-expanded="isOpen(s.id, 'sees')" @click="toggle(s.id, 'sees')"><span class="ar">{{ isOpen(s.id, 'sees') ? '▾' : '▸' }}</span>{{ PARTS[2].t }}</button>
            <p v-show="isOpen(s.id, 'sees')" class="pb">{{ s.sees }}</p>
          </div>
          <div class="part">
            <button type="button" class="ph" :aria-expanded="isOpen(s.id, 'notes')" @click="toggle(s.id, 'notes')"><span class="ar">{{ isOpen(s.id, 'notes') ? '▾' : '▸' }}</span>{{ PARTS[3].t }}</button>
            <ul v-show="isOpen(s.id, 'notes')" class="pb notes"><li v-for="(n, i) in s.notes" :key="i">{{ n }}</li></ul>
          </div>
          <div v-if="s.links?.length" class="part">
            <div class="ph static">操作 SOP 頁面</div>
            <ul class="pb notes links">
              <li v-for="l in s.links" :key="l.href"><a :href="l.href" target="_blank" rel="noopener">{{ l.label }}</a><span class="ltag">{{ l.tag }}</span></li>
            </ul>
          </div>
          <div v-if="s.faq?.length" class="part">
            <div class="ph static">問與答</div>
            <dl class="pb faq"><template v-for="(x, i) in s.faq" :key="i"><dt>{{ i + 1 }}. {{ x.q }}</dt><dd>{{ x.a }}</dd></template></dl>
          </div>
        </section>
      </div>
    </div>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.mono { font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); font-size: 13px; }
.late { margin-left: 12px; padding: 2px 10px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
.btn { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 7px 14px; font-size: 13.5px; font-family: inherit; cursor: pointer; }
.btn:hover { border-color: var(--brand); }
.man { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 800px) { .man { grid-template-columns: 1fr; } }
.toc { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px; display: flex; flex-direction: column; gap: 4px; position: sticky; top: 12px; }
.toc-h { font-size: 12px; color: var(--ink-aux); padding: 2px 8px 6px; }
.toc-i { text-align: left; border: 0; background: transparent; color: var(--ink); font: inherit; font-size: 14px; padding: 8px; border-radius: 8px; cursor: pointer; display: flex; gap: 8px; line-height: 1.4; }
.toc-i:hover { background: var(--sub); }
.toc-i.on { background: var(--mint); color: var(--brand-dark); font-weight: 700; }
.no { display: inline-flex; align-items: center; justify-content: center; min-width: 22px; height: 22px; border-radius: 11px; background: var(--brand); color: var(--type-on); font-size: 12px; font-weight: 700; flex: none; }
.body { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px 20px; }
.sec + .sec { margin-top: 28px; padding-top: 20px; border-top: 1px solid var(--line); }
.h2 { margin: 0 0 12px; font-size: 19px; display: flex; align-items: center; gap: 10px; }
.part { border-top: 1px solid var(--line); }
.part:first-of-type { border-top: 0; }
.ph { width: 100%; text-align: left; border: 0; background: transparent; font: inherit; font-size: 15px; font-weight: 700; color: var(--ink); padding: 10px 0 6px; cursor: pointer; display: flex; gap: 8px; }
.ph.static { cursor: default; }
.ar { color: var(--ink-aux); width: 14px; }
.pb { margin: 0 0 10px 22px; font-size: 14.5px; line-height: 1.75; color: var(--ink); }
.steps { padding-left: 22px; }
.steps li, .notes li { margin-bottom: 4px; }
.notes { padding-left: 22px; }
.links a { color: var(--brand-dark); text-decoration: underline; }
.ltag { margin-left: 8px; padding: 1px 8px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); font-size: 12px; white-space: nowrap; }
.faq dt { font-weight: 700; margin-top: 8px; }
.faq dd { margin: 2px 0 6px; color: var(--ink-soft); }
.figs { display: flex; flex-direction: column; gap: 14px; margin: 8px 0 6px; }
.fig { margin: 0; }
.svgbox :deep(svg), .fig img { width: 100%; max-width: 760px; height: auto; display: block; }
.fig figcaption { font-size: 13px; color: var(--ink-aux); margin-top: 6px; line-height: 1.6; }
.views { display: flex; gap: 8px; margin-bottom: 10px; }
.vchip { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 999px; padding: 3px 12px; font-size: 13px; text-decoration: none; }
.vchip.on { background: var(--mint); border-color: var(--brand); color: var(--brand-dark); }
</style>

<style>
@media print {
  .v2nav, .noprint, .topbar, .toast-wrap { display: none !important; }
  .v2shell { display: block !important; }
  .pb { display: block !important; }
  body { background: #fff !important; }
  .man { display: block !important; }
  .body { border: 0 !important; padding: 0 !important; }
  .sec { break-inside: avoid-page; page-break-after: always; }
  .sec:last-child { page-break-after: auto; }
  .fig { break-inside: avoid; }
}
</style>
