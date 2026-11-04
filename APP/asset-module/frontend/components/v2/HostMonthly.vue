<script setup lang="ts">
// 10.1 主機組月報（2.007.13）。使用者：「月報放在網頁上看就好，最好直接剪下、複製我們的畫面就可以當月報」。
// 每一頁固定 16:9（.slide），按「截圖模式」收掉按鈕與匯出列，直接截圖貼進月報。
// 自動算：生命週期（8.3 同一份）、虛擬化平台規模（2.1 同一份）、事件（4.1）、NMON 效能（上傳的檔）。
// 人填：整體判定、關注事項、風險追蹤、未來 3–6 個月、VM 效能判讀（VM 沒有 NMON，待 vROps 匯出檔）。當月沒填先帶上個月。
const { apiFetch } = useApi()
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋，這裡只是別讓人白按）
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => e?.data?.detail ?? e?.message ?? '未知錯誤'
const month = computed(() => (typeof route.query.month === 'string' ? route.query.month : ''))
const d = ref<any>(null)
const err = ref('')
async function load() {
  try { d.value = await apiFetch<any>('/api/v2/host-monthly', { query: month.value ? { month: month.value } : {} }); err.value = ''; reset() } catch (e: any) { err.value = `月報載入失敗：${errMsg(e)}` }
}
onMounted(load)
watch(month, load)
const setMonth = (m: string) => router.replace({ query: { ...route.query, month: m } })
const ym = computed(() => { const m = d.value?.month || ''; return m ? `${m.slice(0, 4)} 年 ${Number(m.slice(5))} 月` : '' })
const n = (v: any) => (v === null || v === undefined ? '—' : Number(v).toLocaleString())

// ===== 人填的部分 =====
const note = ref<any>(null)
const editing = ref(false)
const shot = ref(false)
function reset() { note.value = d.value ? JSON.parse(JSON.stringify(d.value.note)) : null; editing.value = false }
const saving = ref(false)
async function save() {
  saving.value = true
  try {
    const data = Object.fromEntries(Object.entries(note.value).filter(([k]) => !k.startsWith('_')))
    const r = await guardedFetch<any>('/api/v2/host-monthly/note', { method: 'PUT', body: { month: d.value.month, data } })
    d.value.note = r; reset(); showToast('已儲存', 'success')
  } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error') } finally { saving.value = false }
}
const addRow = (k: string, row: any) => { note.value[k] = [...(note.value[k] ?? []), row] }
const delRow = (k: string, i: number) => { note.value[k] = note.value[k].filter((_: any, j: number) => j !== i) }

// ===== NMON 上傳 =====
const upBusy = ref(false)
async function upload(ev: Event) {
  const files = Array.from((ev.target as HTMLInputElement).files ?? [])
  ;(ev.target as HTMLInputElement).value = ''
  if (!files.length) return
  upBusy.value = true
  const okList: string[] = []; const bad: string[] = []
  for (const f of files) {
    const fd = new FormData(); fd.append('file', f)
    try { const r = await guardedFetch<any>('/api/v2/host-monthly/nmon', { method: 'POST', body: fd }); okList.push(`${r.host}（${r.samples} 筆）`) } catch (e: any) { bad.push(`${f.name}：${errMsg(e)}`) }
  }
  upBusy.value = false
  if (okList.length) showToast(`已匯入 ${okList.length} 個檔：${okList.join('、')}`, 'success')
  if (bad.length) showToast(`有 ${bad.length} 個檔失敗：${bad.join('；')}`, 'error')
  await load()
}

// ===== 自動算的數字 =====
const lc = computed(() => d.value?.lifecycle ?? { rows: [], total: {} })
const lcRows = computed(() => lc.value.rows.map((r: any, i: number) => ({ ...r, __no: i + 1 })))
const nmonRows = computed(() => (d.value?.nmon ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, over_text: `${r.over} 次`, longest: r.longest_over_min ? `${r.longest_over_min} 分` : '—', range: `${r.from.slice(5, 10)}～${r.to.slice(5, 10)}` })))
const nmonOverHosts = computed(() => nmonRows.value.filter((r: any) => r.over > 0).length)
const incRows = computed(() => (d.value?.incidents ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, hours_text: r.hours == null ? '—' : `${r.hours} 小時` })))
const virt = computed(() => d.value?.scale ?? { virt_total: 0, virt_by_room: [] })
const virtText = computed(() => virt.value.virt_by_room.map((x: any) => `${x.room} ${x.n}`).join('／'))
const lcLevel = computed(() => (lc.value.total.expired > 0 ? '需關注' : lc.value.total.upcoming > 0 ? '黃燈' : '綠燈'))
const nmonLevel = computed(() => (!nmonRows.value.length ? '無資料' : nmonRows.value.some((r: any) => r.longest_over_min >= 30) ? '黃燈' : '綠燈'))
const tone = (lv: string) => (lv === '需關注' ? 'bad' : lv === '黃燈' ? 'warn' : lv === '綠燈' ? 'good' : 'mute')

// 有逐分鐘資料的主機，附錄用「每小時」CPU 忙碌（nmon_series）；沒有的退回舊的每天圖
const perfSeries = ref<Record<string, any[]>>({})
const perfHosts = computed(() => new Set<string>((d.value?.nmon_perf ?? []).map((h: any) => h.host)))
watch(d, async (v) => {
  perfSeries.value = {}
  for (const h of (v?.nmon_perf ?? [])) {
    try {
      const r = await apiFetch<any>('/api/v2/nmon/series', { query: { host: h.host, month: v.month, grain: 'hour', keys: 'cpu_busy' } })
      if (r.series?.cpu_busy?.length) perfSeries.value = { ...perfSeries.value, [h.host]: r.series.cpu_busy }
    } catch (e: any) { showToast(`${h.host} 的小時圖載入失敗：${errMsg(e)}`, 'warn') }
  }
})
// 舊圖的 X 軸：每隔幾天標一次日期（最多約 8 個）
function dayTicks(days: any[], w = 560) {
  if (!days.length) return []
  const every = Math.max(1, Math.ceil(days.length / 8))
  return days.map((p: any, i: number) => ({ i, x: days.length === 1 ? w / 2 : (i * w) / (days.length - 1), label: p.date.slice(5) })).filter((t: any) => t.i % every === 0)
}

// 趨勢圖：每天的尖峰與平均（0～100%），80% 警戒線
function path(days: any[], k: string, w = 560, h = 150) {
  if (!days.length) return ''
  const x = (i: number) => (days.length === 1 ? w / 2 : (i * w) / (days.length - 1))
  return days.map((p: any, i: number) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${(h - (Math.min(100, p[k]) / 100) * h).toFixed(1)}`).join(' ')
}
</script>

<template>
  <div class="hm" :class="{ shot }">
    <div class="tool">
      <label>月份 <select :value="d?.month" @change="setMonth(($event.target as HTMLSelectElement).value)">
        <option v-if="d && !d.months.includes(d.month)" :value="d.month">{{ d.month }}</option>
        <option v-if="d && d.prev && !d.months.includes(d.prev) && d.prev !== d.month" :value="d.prev">{{ d.prev }}</option>
        <option v-for="m in d?.months ?? []" :key="m" :value="m">{{ m }}</option></select></label>
      <label v-if="!isViewer" class="btn2">{{ upBusy ? '匯入中…' : '上傳 NMON 檔' }}<input type="file" multiple hidden :disabled="upBusy" @change="upload"></label>
      <button v-if="!editing && !isViewer" type="button" class="btn2" :disabled="!note" @click="editing = true">編輯管理判斷</button>
      <template v-else><button type="button" class="btn" :disabled="saving" @click="save">{{ saving ? '儲存中…' : '儲存' }}</button><button type="button" class="btn2" @click="reset">取消</button></template>
      <button type="button" class="btn2" @click="shot = !shot">{{ shot ? '結束截圖模式' : '截圖模式' }}</button>
      <span v-if="note?._inherited" class="warnc">管理判斷沿用 {{ note._inherited }}（還沒存本月）</span>
      <span v-else-if="note?._at" class="muted">管理判斷 {{ note._by }} {{ note._at?.slice(0, 16) }}</span>
      <InfoNote>每一頁固定 16:9，按「截圖模式」收掉按鈕後直接截圖。生命週期＝8.3 同一份（逐台）；虛擬化平台台數＝2.1 同一份；事件＝4.1 事件應變當月開始的事件；
        效能＝上傳的 NMON 檔（AIX，CPU 忙碌＝User%＋Sys%，警戒 {{ d?.threshold ?? 80 }}%）。一台一天一個檔也可以，可一次選多個檔；同一台同一時間再傳一次會覆蓋。
        VM（ESXi）沒有 NMON，效能判讀先人填，待 vROps 匯出檔。管理判斷當月沒存時先帶上個月的內容。</InfoNote>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="!d || !note" class="muted">載入中…</p>
    <template v-else>
      <!-- 1 總覽 -->
      <section class="slide">
        <h2>主機組 {{ ym }}份維運暨效能管理月報</h2>
        <p class="sub">{{ d.month }}｜管理範圍：主機伺服器、VM 虛擬化、IBM Power｜資料產生 {{ d.generated_at.slice(0, 16) }}</p>
        <div class="kpis">
          <div class="kpi"><span class="kl">Lifecycle</span><b :class="tone(lcLevel)">{{ lcLevel }}</b><small>{{ n(lc.total.expired) }} 已 EOS／{{ n(lc.total.upcoming) }} 一年內 EOS</small></div>
          <div class="kpi"><span class="kl">虛擬化平台</span><b>{{ n(virt.virt_total) }} 台</b><small>{{ virtText || '—' }}</small></div>
          <div class="kpi"><span class="kl">AIX 效能（NMON）</span><b :class="tone(nmonLevel)">{{ nmonLevel }}</b><small>{{ nmonRows.length }} 台・超過 {{ d.threshold }}% 的 {{ nmonOverHosts }} 台</small></div>
          <div class="kpi"><span class="kl">Incident</span><b>{{ incRows.length }} 件</b><small>{{ incRows[0]?.title || '本月無事件紀錄' }}</small></div>
          <div class="kpi"><span class="kl">整體判定</span>
            <input v-if="editing" v-model="note.verdict" class="in" placeholder="例：需關注"><b v-else :class="tone(note.verdict)">{{ note.verdict || '—' }}</b>
            <input v-if="editing" v-model="note.verdict_note" class="in" placeholder="一句話"><small v-else>{{ note.verdict_note }}</small></div>
        </div>
        <div class="two">
          <div><h3>Top Risk｜本月主要風險</h3>
            <ol class="risk"><li v-for="(a, i) in note.actions.slice(0, 3)" :key="i"><b>{{ a.risk }}</b>　{{ a.next }}<span class="tag">{{ a.status }}</span></li>
              <li v-if="!note.actions.length" class="muted">（在「風險與改善追蹤」頁填寫）</li></ol></div>
          <div><h3>關注事項｜快速回答</h3>
            <dl class="att"><template v-for="(a, i) in note.attention" :key="i"><dt>{{ a.k }}</dt><dd><input v-if="editing" v-model="a.v" class="in"><template v-else>{{ a.v || '—' }}</template></dd></template></dl></div>
        </div>
      </section>

      <!-- 2 Service Health & Incident -->
      <section class="slide">
        <h2>Service Health &amp; Incident</h2>
        <p class="sub">規模 → 事件 → 影響 → 處置 → 後續</p>
        <div class="kpis">
          <div class="kpi"><span class="kl">虛擬化平台</span><b>{{ n(virt.virt_total) }} 台</b><small>{{ virtText || '—' }}</small></div>
          <div class="kpi"><span class="kl">NMON 監控主機</span><b>{{ nmonRows.length }} 台</b><small>{{ nmonRows.map((r: any) => r.host).slice(0, 4).join('、') }}</small></div>
          <div class="kpi"><span class="kl">本月 Incident（正式）</span><b>{{ incRows.filter((r: any) => r.mode === 'formal').length }} 件</b><small>處理中 {{ incRows.filter((r: any) => r.mode === 'formal' && r.status === '處理中').length }} 件；演練 {{ incRows.filter((r: any) => r.mode === 'drill').length }} 件、未分類（歷史）{{ incRows.filter((r: any) => r.mode !== 'formal' && r.mode !== 'drill').length }} 件，不計入正式</small></div>
        </div>
        <h3>本月事件</h3>
        <V2Table :rows="incRows" prefix="I" row-key="id" unit="件" empty="本月沒有事件紀錄（4.1 事件應變）"
                 :cols="[{ k: 'started_at', label: '開始', mono: true }, { k: 'title', label: '事件', link: (r: any) => `/v2/incident/${r.id}` }, { k: 'type', label: '類型' }, { k: 'mode_label', label: '性質' }, { k: 'status', label: '狀態' }, { k: 'hours_text', label: '處理時間', mono: true }, { k: 'summary', label: '處置／後續' }]" />
      </section>

      <!-- 3 Performance & Capacity -->
      <section class="slide">
        <h2>Performance &amp; Capacity</h2>
        <p class="sub">以 {{ d.threshold }}% 為 CPU 警戒基準</p>
        <h3>AIX（NMON）</h3>
        <V2Table :rows="nmonRows" prefix="N" row-key="host" unit="台" empty="本月沒有 NMON 資料：按上方「上傳 NMON 檔」"
                 :cols="[{ k: 'host', label: '主機' }, { k: 'range', label: '資料期間', mono: true }, { k: 'avg', label: '平均 CPU %', mono: true }, { k: 'peak', label: '尖峰 CPU %', mono: true }, { k: 'over', label: `超過 ${d.threshold}%（次）`, mono: true }, { k: 'longest_over_min', label: '最長連續超標（分）', mono: true }, { k: 'samples', label: '樣本數', mono: true }]" />
        <h3>VM 虛擬化（人填，待 vROps 匯出檔）</h3>
        <table v-if="editing" class="et">
          <tr><th>平台</th><th>環境</th><th>使用狀態</th><th>80% 超標</th><th>Headroom</th><th>Risk</th><th /></tr>
          <tr v-for="(r, i) in note.vm_perf" :key="i"><td v-for="k in ['platform', 'env', 'usage', 'over80', 'headroom', 'risk']" :key="k"><input v-model="r[k]" class="in"></td><td><button type="button" class="x" @click="delRow('vm_perf', i)">刪</button></td></tr>
          <tr><td colspan="7"><button type="button" class="btn2" @click="addRow('vm_perf', { platform: 'VM', env: '', usage: '', over80: '', headroom: '', risk: '' })">＋ 加一列</button></td></tr>
        </table>
        <V2Table v-else :rows="note.vm_perf.map((r: any, i: number) => ({ ...r, __no: i + 1 }))" prefix="V" row-key="env" unit="列" empty="（編輯管理判斷後填寫）"
                 :cols="[{ k: 'platform', label: '平台' }, { k: 'env', label: '環境' }, { k: 'usage', label: '使用狀態' }, { k: 'over80', label: '80% 超標' }, { k: 'headroom', label: 'Headroom' }, { k: 'risk', label: 'Risk' }]" />
      </section>

      <!-- 4 Lifecycle -->
      <section class="slide">
        <h2>Lifecycle Management｜產品生命週期狀態</h2>
        <p class="sub">以已 EOS、一年內 EOS、需確認與支援中分級（逐台，與 8.3 生命週期同一份）</p>
        <div class="kpis">
          <div class="kpi"><span class="kl">納管平台</span><b>{{ n(lc.total.total) }}</b><small>管理母體（台）</small></div>
          <div class="kpi"><span class="kl">已 EOS</span><b class="bad">{{ n(lc.total.expired) }}</b><small>需優先改善</small></div>
          <div class="kpi"><span class="kl">一年內 EOS</span><b class="warn">{{ n(lc.total.upcoming) }}</b><small>需提前規劃</small></div>
          <div class="kpi"><span class="kl">需確認</span><b class="warn">{{ n(lc.total.unknown) }}</b><small>版本／支援狀態待確認</small></div>
          <div class="kpi"><span class="kl">支援中</span><b class="good">{{ n(lc.total.ok) }}</b><small>仍在支援週期</small></div>
        </div>
        <p v-if="lc.row_dup" class="sub">平台列依版本加總，{{ lc.row_dup }} 台同時登記兩種版本而各算一次；上方合計逐台不重複<InfoNote>同一台（machine_key）有兩筆登記、作業系統版本不同時，兩個版本列各算一次；合計與 8.3 生命週期磚塊同一份（逐台）。</InfoNote></p>
        <V2Table :rows="lcRows" prefix="L" row-key="platform" unit="類"
                 :cols="[{ k: 'platform', label: '平台' }, { k: 'total', label: '總量（台）', mono: true }, { k: 'expired', label: '已 EOS', mono: true }, { k: 'upcoming', label: '一年內 EOS', mono: true }, { k: 'unknown', label: '需確認', mono: true }, { k: 'ok', label: '支援中', mono: true }]" />
      </section>

      <!-- 5 Risk & Action Tracking -->
      <section class="slide">
        <h2>Risk &amp; Action Tracking</h2>
        <p class="sub">Lifecycle、VM、IBM Power 的改善事項收在同一張表，每月只更新 Status／Due Date</p>
        <table v-if="editing" class="et">
          <tr><th>Risk／Action</th><th>Platform</th><th>Status</th><th>Due Date</th><th>Next Step</th><th /></tr>
          <tr v-for="(r, i) in note.actions" :key="i">
            <td><input v-model="r.risk" class="in"></td><td><input v-model="r.platform" class="in"></td>
            <td><select v-model="r.status"><option v-for="s in d.status_options" :key="s">{{ s }}</option></select></td>
            <td><input v-model="r.due" class="in"></td><td><input v-model="r.next" class="in"></td>
            <td><button type="button" class="x" @click="delRow('actions', i)">刪</button></td></tr>
          <tr><td colspan="6"><button type="button" class="btn2" @click="addRow('actions', { risk: '', platform: '', status: 'On Track', due: '', next: '' })">＋ 加一列</button></td></tr>
        </table>
        <V2Table v-else :rows="note.actions.map((r: any, i: number) => ({ ...r, __no: i + 1 }))" prefix="R" row-key="risk" unit="項" empty="（編輯管理判斷後填寫）"
                 :cols="[{ k: 'risk', label: 'Risk／Action' }, { k: 'platform', label: 'Platform' }, { k: 'status', label: 'Status' }, { k: 'due', label: 'Due Date' }, { k: 'next', label: 'Next Step' }]" />
        <p class="def">Completed：已完成並確認結果　On Track：依計畫執行，無明顯延遲　Delay：逾原定時程，需填寫原因與新期限</p>
      </section>

      <!-- 6 Next 3–6 Months -->
      <section class="slide">
        <h2>Next 3–6 Months &amp; Management Attention</h2>
        <p class="sub">未有明確排程者不自行填日期</p>
        <table v-if="editing" class="et">
          <tr><th>事項</th><th>時程</th><th>風險／目的</th><th>目前狀態</th><th /></tr>
          <tr v-for="(r, i) in note.upcoming" :key="i"><td v-for="k in ['item', 'when', 'purpose', 'status']" :key="k"><input v-model="r[k]" class="in"></td><td><button type="button" class="x" @click="delRow('upcoming', i)">刪</button></td></tr>
          <tr><td colspan="5"><button type="button" class="btn2" @click="addRow('upcoming', { item: '', when: '待確認', purpose: '', status: '' })">＋ 加一列</button></td></tr>
        </table>
        <V2Table v-else :rows="note.upcoming.map((r: any, i: number) => ({ ...r, __no: i + 1 }))" prefix="U" row-key="item" unit="項" empty="（編輯管理判斷後填寫）"
                 :cols="[{ k: 'item', label: '事項' }, { k: 'when', label: '時程' }, { k: 'purpose', label: '風險／目的' }, { k: 'status', label: '目前狀態' }]" />
        <h3>下月資料補強優先順序</h3>
        <ol v-if="!editing" class="risk"><li v-for="(p, i) in note.priorities" :key="i">{{ p }}</li><li v-if="!note.priorities.length" class="muted">—</li></ol>
        <textarea v-else class="ta" :value="note.priorities.join('\n')" placeholder="一行一項" @input="note.priorities = ($event.target as HTMLTextAreaElement).value.split('\n').filter((x: string) => x.trim())" />
      </section>

      <!-- 7 Appendix：NMON 趨勢（2.007.19：有逐分鐘資料的用「每小時」圖；舊檔只有每天彙總的才退回舊圖） -->
      <section v-for="(r, i) in nmonRows" :key="r.host" class="slide">
        <h2>Appendix {{ i + 1 }}｜{{ r.host }} CPU 趨勢</h2>
        <p class="sub">NMON {{ r.from.slice(0, 10) }}～{{ r.to.slice(0, 10) }}・{{ perfSeries[r.host] ? '每小時：實線平均、細線尖峰' : '每天：實線尖峰、虛線平均（這台沒有逐分鐘資料，重新上傳 NMON 檔後會變成每小時）' }}・紅線 {{ d.threshold }}%
          <template v-if="!shot && perfHosts.has(r.host)">　<NuxtLink :to="`/v2/report?tab=perf&host=${encodeURIComponent(r.host)}&month=${d.month}`">看完整效能圖表（記憶體、磁碟、網路、檔案系統…）›</NuxtLink></template></p>
        <div class="chart">
          <V2NmonChart v-if="perfSeries[r.host]" :series="[{ key: 'cpu_busy', label: 'CPU 忙碌', color: 'var(--chart-1)', pts: perfSeries[r.host] }]" grain="hour" unit="%" :threshold="d.threshold" :w="620" :h="300" />
          <svg v-else viewBox="-40 -10 620 190">
            <line v-for="y in [0, 50, 100]" :key="y" x1="0" :y1="150 - y * 1.5" x2="560" :y2="150 - y * 1.5" class="grid" />
            <text v-for="y in [0, 50, 100]" :key="'t' + y" x="-8" :y="154 - y * 1.5" class="ax">{{ y }}%</text>
            <line x1="0" :y1="150 - d.threshold * 1.5" x2="560" :y2="150 - d.threshold * 1.5" class="th" />
            <path :d="path(r.days, 'peak')" class="pk" />
            <path :d="path(r.days, 'avg')" class="av" />
            <text v-for="t in dayTicks(r.days)" :key="t.i" :x="t.x" y="172" class="ax" text-anchor="middle">{{ t.label }}</text>
          </svg>
          <div class="side">
            <div class="kpi"><span class="kl">平均</span><b>{{ r.avg }}%</b></div>
            <div class="kpi"><span class="kl">尖峰</span><b :class="r.peak > d.threshold ? 'warn' : 'good'">{{ r.peak }}%</b></div>
            <div class="kpi"><span class="kl">超過 {{ d.threshold }}%</span><b>{{ r.over }} 次</b><small>最長連續 {{ r.longest_over_min }} 分</small></div>
          </div>
        </div>
      </section>
      <section v-if="!shot && d.nmon_files.length" class="files">
        <h3>本月 NMON 檔</h3>
        <V2Table :rows="d.nmon_files.map((f: any, i: number) => ({ ...f, __no: i + 1 }))" prefix="F" row-key="filename" unit="個檔"
                 :cols="[{ k: 'host', label: '主機' }, { k: 'filename', label: '檔名' }, { k: 'first_ts', label: '從', mono: true }, { k: 'last_ts', label: '到', mono: true }, { k: 'samples', label: '樣本數', mono: true }, { k: 'uploaded_by', label: '上傳人' }, { k: 'uploaded_at', label: '上傳時間', mono: true }]" />
      </section>
    </template>
  </div>
</template>

<style scoped>
.hm { display: flex; flex-direction: column; gap: 18px; }
.tool { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.tool select { font: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.muted { color: var(--muted); font-size: 13px; }
.warnc { color: var(--warn-text); background: var(--warn-soft); border-radius: 8px; padding: 2px 10px; }
.err { color: var(--bad); }
.slide { aspect-ratio: 16 / 9; width: 100%; max-width: 1280px; background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 22px 28px; overflow: hidden; display: flex; flex-direction: column; gap: 10px; }
.slide h2 { margin: 0; font-size: 26px; color: var(--brand-dark); border-bottom: 2px solid var(--brand); padding-bottom: 6px; }
.slide h3 { margin: 6px 0 2px; font-size: 15px; background: var(--brand-dark); color: var(--type-on); padding: 4px 10px; border-radius: 4px; }
.sub { margin: 0; font-size: 12.5px; color: var(--ink-aux); }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
.kpi { border: 1px solid var(--border); border-radius: 10px; padding: 8px 12px; display: flex; flex-direction: column; gap: 2px; background: var(--sub); }
.kl { font-size: 12px; color: var(--ink-soft); }
.kpi b { font-family: var(--disp); font-size: 24px; color: var(--brand-dark); }
.kpi small { font-size: 11.5px; color: var(--ink-aux); }
b.bad { color: var(--bad); } b.warn { color: var(--warn-text); } b.good { color: var(--good); } b.mute { color: var(--ink-aux); }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.risk { margin: 6px 0; padding-left: 22px; font-size: 14px; display: flex; flex-direction: column; gap: 6px; }
.tag { margin-left: 8px; font-size: 11px; border: 1px solid var(--border-strong); border-radius: 999px; padding: 1px 8px; color: var(--ink-aux); }
.att { display: grid; grid-template-columns: 120px 1fr; gap: 8px 12px; font-size: 13.5px; margin: 6px 0; }
.att dt { color: var(--brand-dark); font-weight: 600; } .att dd { margin: 0; }
.in { width: 100%; font: inherit; font-size: 13px; padding: 3px 6px; border: 1px solid var(--border-strong); border-radius: 6px; box-sizing: border-box; }
.et { width: 100%; border-collapse: collapse; font-size: 13px; }
.et th { text-align: left; font-size: 12px; color: var(--ink-aux); padding: 4px; }
.et td { padding: 3px 4px; }
.x { font: inherit; font-size: 12px; border: 1px solid var(--border-strong); background: var(--card); border-radius: 6px; cursor: pointer; }
.ta { width: 100%; min-height: 90px; font: inherit; font-size: 13px; padding: 6px; border: 1px solid var(--border-strong); border-radius: 6px; }
.def { font-size: 12px; color: var(--ink-aux); margin: 4px 0 0; }
.chart { display: grid; grid-template-columns: 1fr 200px; gap: 16px; flex: 1; min-height: 0; }
.chart svg { width: 100%; height: auto; align-self: start; }
.grid { stroke: var(--line); stroke-width: 1; }
.th { stroke: var(--bad); stroke-width: 1.5; }
.pk { fill: none; stroke: var(--chart-1); stroke-width: 2; }
.av { fill: none; stroke: var(--chart-2); stroke-width: 2; stroke-dasharray: 5 4; }
.ax { font-size: 10px; fill: var(--ink-aux); }
.side { display: flex; flex-direction: column; gap: 10px; }
.files { max-width: 1280px; }
.shot :deep(.xbar), .shot :deep(.pager) { display: none; }
</style>
