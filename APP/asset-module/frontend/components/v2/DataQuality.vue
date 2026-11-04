<script setup lang="ts">
// 9.1「資料品質」頁籤（V27，2.048.18 起 V2 原生；取代嵌入的舊頁 pages/data-quality.vue）。⚠D3（正確率口徑）⚠D4（人名合併改寫資料）。同一批 API，行為不變：
// GET /api/data-quality（各維度）、/api/data-quality/<維度>（不符合的是哪幾台）、/api/measured-mismatch（實測不符筆數，點進 9.1 實測不符）、
// /api/trust/distribution＋/api/trust/hosts（可信度分布與下鑽，同一份後端計算）、/api/data-quality/field-fill、/single-person-names；
// 人名合併＝POST /person-merge/preview（先預覽，只讀）→ 確認視窗 → POST /person-merge（一般帳號以上，後端 USER_WRITE）。SSH 測連線沿用共用元件 SshProbe。
// 下鑽在網址：?tab=quality&qd=<維度>｜qt=s|c&qv=<分數或來源組合>｜qn=1（展開只出現一次的人名）。
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const { canWrite } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const u = (extra: Record<string, string> = {}) => ({ path: '/v2/datafix', query: { tab: 'quality', ...extra } })
const machine = (s: string | null) => (s ? `/v2/machine/${encodeURIComponent(s)}` : null)

const summary = ref<any>(null)
const err = ref('')
const mm = ref<any>(null)
const mmErr = ref('')
const trust = ref<any>(null)
const trustErr = ref('')
const fill = ref<{ fields: any[]; total_rows: number } | null>(null)
const singleNames = ref<string[]>([])
const singleErr = ref('')
async function loadSingle() {
  try { singleNames.value = (await apiFetch<any>('/api/data-quality/single-person-names')).names; singleErr.value = '' } catch (e: any) { singleErr.value = `人名清單載入失敗：${errMsg(e)}` }
}
onMounted(async () => {
  await Promise.all([
    apiFetch<any>('/api/data-quality').then(v => { summary.value = v }).catch(e => { err.value = `資料品質載入失敗：${errMsg(e)}` }),
    apiFetch<any>('/api/measured-mismatch').then(v => { mm.value = v }).catch(e => { mmErr.value = `系統校正清單載入失敗：${errMsg(e)}` }),
    // 可信度分布、填充率是輔助看板：載不到只在那一塊寫原因，不擋整頁
    apiFetch<any>('/api/trust/distribution').then(v => { trust.value = v }).catch(e => { trustErr.value = `可信度分布載入失敗：${errMsg(e)}` }),
    apiFetch<any>('/api/data-quality/field-fill').then(v => { fill.value = v }).catch(() => { /* 輔助看板，載不到就不顯示那一塊（舊頁同樣） */ }),
    loadSingle(),
  ])
})

const KIND_TEXT: Record<string, string> = { verifiable: '機器可驗', filled: '填寫率', freshness: '新鮮度', coverage: '涵蓋率' }
const dimRows = computed(() => (summary.value?.dimensions ?? []).map((d: any, i: number) => ({ ...d, __no: i + 1, kind_t: KIND_TEXT[d.kind] ?? d.kind,
  rate_t: d.rate === null ? '' : `${d.rate}%` })))
const dimCols = [{ k: 'label', label: '維度', brief: (r: any) => r.label, text: (r: any) => (r.note ? `${r.label}（${r.note}）` : r.label) },
  { k: 'kind_t', label: '性質' }, { k: 'checked', label: '母體', mono: true }, { k: 'ok', label: '符合', mono: true },
  { k: 'bad', label: '不符合', mono: true, link: (r: any) => u({ qd: r.key }) }, { k: 'rate_t', label: '比率', mono: true }]
const fillRows = computed(() => (fill.value?.fields ?? []).slice(0, 12).map((f: any, i: number) => ({ ...f, __no: i + 1, pct: `${Math.round((f.fill_rate ?? 0) * 100)}%` })))

// 可信度分布
const SRC_NAME: Record<string, string> = { dynassets: 'dynassets', rvtools: 'RVTools', cia: 'CIA' }
const comboLabel = (c: string) => (c.startsWith('（') ? c : c.split('+').map(s => SRC_NAME[s] ?? s).join('＋'))
const scoreRows = computed(() => (trust.value?.by_score ?? []).map((b: any, i: number) => ({ ...b, __no: i + 1, reason_text: b.reasons.map((x: any) => `${x.text} ×${x.count}`).join('／') })))
const comboRows = computed(() => (trust.value?.by_combo ?? []).map((c: any, i: number) => ({ ...c, __no: i + 1, label: comboLabel(c.combo) })))

// ===== 下鑽 =====
const drillQ = computed(() => {
  if (q('qd')) return { kind: 'dim', key: q('qd') }
  if ((q('qt') === 's' || q('qt') === 'c') && route.query.qv !== undefined) return { kind: q('qt'), key: q('qv') }
  return null
})
const drillTitle = computed(() => {
  const d = drillQ.value
  if (!d) return ''
  if (d.kind === 'dim') return `${(summary.value?.dimensions ?? []).find((x: any) => x.key === d.key)?.label ?? d.key}：不符合`
  return d.kind === 's' ? `可信度 ${d.key} 分` : comboLabel(d.key)
})
const KIND_NAME: Record<string, string> = { vm: 'VM', physical: '實體機', esxi: 'ESXi 主機', unknown: '不明（照 VM 算）' }
const srcText = (v: boolean | null | undefined) => (v === null ? '不適用' : v ? '有' : '沒有')
const evidText = (h: any) => (h.evidence === 'managed' ? '已納管 +30' : h.evidence === 'collected' ? '已收集（設備）+30'
  : h.evidence === 'alive' ? (h.alive_basis === 'dynassets' ? `網路通 +20（dynassets ${(h.dynassets_imported_at || '').slice(0, 10)}）` : '網路通 +20（掃描）') : '')
const drillRows = ref<any[]>([])
const drillErr = ref('')
const drillLoading = ref(false)
const beginDrill = useLatest()
watch(drillQ, async (d) => {
  const isLatest = beginDrill()
  drillRows.value = []; drillErr.value = ''; drillLoading.value = false
  if (!d) return
  drillLoading.value = true
  try {
    const r = d.kind === 'dim'
      ? await apiFetch<any>(`/api/data-quality/${encodeURIComponent(d.key)}`)
      : await apiFetch<any>('/api/trust/hosts', { query: d.kind === 's' ? { score: d.key } : { combo: d.key } })
    if (!isLatest()) return
    drillRows.value = (r.items ?? []).map((h: any, i: number) => ({ ...h, __no: i + 1,
      ...(d.kind === 'dim' ? {} : { kind_t: KIND_NAME[h.kind] ?? h.kind, src_dyn: srcText(h.sources?.dynassets), src_rv: srcText(h.sources?.rvtools), src_cia: srcText(h.sources?.cia), evid_text: evidText(h) }) }))
  } catch (e: any) { if (isLatest()) drillErr.value = `清單載入失敗：${errMsg(e)}` } finally { if (isLatest()) drillLoading.value = false }
}, { immediate: true })
const drillCols = computed(() => (drillQ.value?.kind === 'dim'
  ? [{ k: 'asset_serial', label: '資產序號', mono: true, link: (r: any) => machine(r.asset_serial) }, { k: 'hostname', label: '主機名稱' }, { k: 'ip', label: 'IP', mono: true },
     { k: 'asset_status', label: '狀態' }, { k: 'os', label: 'OS' }, { k: 'inventory_department', label: '部門' }, { k: 'custodian', label: '保管者' },
     { k: 'updated_at', label: '最後更新', mono: true }, { k: 'reason', label: '原因' }]
  : [{ k: 'asset_serial', label: '資產序號', mono: true, link: (r: any) => machine(r.asset_serial) }, { k: 'hostname', label: '主機名稱' }, { k: 'ip', label: 'IP', mono: true },
     { k: 'score', label: '分數', mono: true }, { k: 'kind_t', label: '型態' }, { k: 'src_dyn', label: 'dynassets' }, { k: 'src_rv', label: 'RVTools' }, { k: 'src_cia', label: 'CIA' },
     { k: 'evid_text', label: '機器證據' }]))

// ===== 人名合併：先預覽會改幾筆，確認才寫（改的是真資料，強制預覽這道關）=====
const mergeFrom = ref('')
const mergeTo = ref('')
const mergePreview = ref<any>(null)
const mergeBusy = ref(false)
function pickMergeFrom(n: string) { mergeFrom.value = n; mergePreview.value = null }
// 預覽跟輸入框對不上就清掉（後端會去掉前後空白，所以比 trim 後的值）；慢回來的預覽也一樣
const previewMatches = (p: any) => p.from_name === mergeFrom.value.trim() && p.to_name === mergeTo.value.trim()
watch([mergeFrom, mergeTo], () => { if (mergePreview.value && !previewMatches(mergePreview.value)) mergePreview.value = null })
async function doMergePreview() {
  if (!mergeFrom.value.trim() || !mergeTo.value.trim()) { showToast('原名與正確名都要填', 'warn'); return }
  mergeBusy.value = true
  try {
    const pv = await apiFetch<any>('/api/data-quality/person-merge/preview', { method: 'POST', body: { from_name: mergeFrom.value, to_name: mergeTo.value } })
    if (!previewMatches(pv)) { showToast('輸入已改，請重新預覽', 'warn'); return }
    mergePreview.value = pv
    if (mergePreview.value.total === 0) showToast('這個原名在資料裡找不到（要完全一致）', 'warn')
  } catch (e: any) { showToast(`預覽失敗：${errMsg(e)}`, 'error') } finally { mergeBusy.value = false }
}
async function doMergeApply() {
  const p = mergePreview.value
  if (!p || !p.total) return
  if (!window.confirm(`確定把「${p.from_name}」全部改成「${p.to_name}」？共 ${p.total} 筆，這個動作會直接改資料。`)) return
  mergeBusy.value = true
  try {
    const r = await apiFetch<any>('/api/data-quality/person-merge', { method: 'POST', body: { from_name: p.from_name, to_name: p.to_name } })
    showToast(`已合併 ${r.total} 筆`, 'success')
    mergePreview.value = null; mergeFrom.value = ''; mergeTo.value = ''
    await loadSingle()          // 併完錯名應該消失
  } catch (e: any) { showToast(`合併失敗：${errMsg(e)}`, 'error') } finally { mergeBusy.value = false }
}
const previewFields = computed(() => (mergePreview.value?.fields ?? []).map((f: any, i: number) => ({ ...f, __no: i + 1 })))
const previewSample = computed(() => (mergePreview.value?.sample ?? []).map((s: any, i: number) => ({ ...s, __no: i + 1 })))
</script>

<template>
  <div>
    <template v-if="drillQ">
      <div class="bar"><NuxtLink :to="u()">‹ 返回</NuxtLink><b>{{ drillTitle }}</b><span class="muted">{{ drillLoading ? '載入中…' : drillErr ? '—' : `${drillRows.length.toLocaleString()} 筆` }}</span></div>
      <p v-if="drillErr" class="err">{{ drillErr }}</p>
      <V2Table v-else :rows="drillRows" prefix="D" row-key="asset_serial" :max="200" :cols="drillCols" :export-name="`資料品質_${drillTitle}`"
               :empty="drillLoading ? '載入中…' : drillQ.kind === 'dim' ? '這個維度沒有不符合的資產' : '沒有符合的登記'" />
    </template>
    <template v-else>
      <p v-if="err" class="err">{{ err }}</p>
      <p v-else-if="!summary" class="muted">計算中…</p>
      <template v-else>
        <div class="hero">
          <div class="score-box">
            <div class="score" :class="{ none: summary.score === null }">{{ summary.score === null ? '—' : summary.score }}<small v-if="summary.score !== null">%</small></div>
            <div class="sl">可驗證資料正確率
              <InfoNote>量測母體：{{ summary.asset_total }} 筆非退役資產。{{ summary.score_basis }}。「近 {{ summary.fresh_days }} 天被機器看到」＝掃描或服務採集有紀錄；超過 {{ summary.stale_days }} 天沒更新的資料另計新鮮度。沒收到機器資料的主機一律不列入分母——沒有證據不等於錯，也不等於對。
                這個分數不能告訴你什麼：填寫率高不代表填得對（保管者每一筆都有值，但那個人可能已離職）；機器驗不了的欄位只有定期複核＋指定負責人能拉高正確率。這頁量的是導入前後的差距與該先修哪一類，不是宣稱清單已經正確。</InfoNote></div>
            <div class="sl">依據 {{ summary.score_sample }} 筆機器證據</div>
          </div>
          <p v-if="summary.coverage_rate !== null && summary.coverage_rate < 90" class="warn">掃描只涵蓋 <b>{{ summary.coverage_rate }}%</b> 的使用中資產
            <InfoNote>分數只代表已涵蓋的那部分，不是整份清單的正確率。</InfoNote></p>
        </div>

        <h3 class="h3">實測與 CIA 不符<InfoNote>已納管主機登入收集到的（或 RVTools 回報的）跟 CIA 登記不一樣：畫面與分類已用實測值「⟲ 系統校正」，CIA 登記值沒有被改，清單交給 CIA 窗口更正。只有存活掃描講不一樣的列「疑似不符」，不自動校正。</InfoNote></h3>
        <p v-if="mmErr" class="err">{{ mmErr }}</p>
        <div v-else-if="mm" class="bar">
          <NuxtLink class="chip" :to="{ path: '/v2/datafix', query: { tab: 'mismatch' } }">系統校正 <b>{{ mm.corrected.length }}</b> 筆（CIA 待更正）</NuxtLink>
          <NuxtLink class="chip" :to="{ path: '/v2/datafix', query: { tab: 'mismatch', mk: 'suspect' } }">疑似不符 <b>{{ mm.suspect.length }}</b> 筆</NuxtLink>
        </div>

        <h3 class="h3">各維度<InfoNote>點「不符合」的數字看是哪幾台。只有拿得出機器證據的維度才給正確率並計入總分；保管者／用途／CIA 這種人為判斷的欄位改列填寫率。</InfoNote></h3>
        <V2Table :rows="dimRows" prefix="Q" row-key="key" unit="個維度" export-name="資料品質_各維度" :cols="dimCols" />

        <template v-if="trust">
          <h3 class="h3">資料可信度分布（{{ trust.total }} 筆登記<span v-if="trust.machines">／{{ trust.machines }} 台</span>，非退役）
            <InfoNote>一筆登記一個分數，越高越可信：分數問的是「這一筆登記有幾個來源佐證」，所以母體是登記筆數；同一台若在 CIA 與 DY 各有一筆，會各自計分，括號裡另附去重後的台數。{{ trust.rule }}。實體機不會出現在 RVTools，所以實體機只算 dynassets＋CIA（照比例換算成 70 分滿分）；型態不明的照 VM 算。點筆數看是哪幾筆。</InfoNote></h3>
          <V2Table :rows="scoreRows" prefix="S" row-key="score" unit="級" export-name="資料品質_可信度分數"
                   :cols="[{ k: 'score', label: '分數', mono: true }, { k: 'count', label: '筆數', mono: true, link: (r: any) => u({ qt: 's', qv: String(r.score) }) },
                           { k: 'reason_text', label: '分數怎麼來的', note: '型態・有哪幾個來源（來源分）＋機器證據（加分）。同一個分數可能由不同組合湊出來，分別列出各幾筆，加總就是筆數。' }]" />
          <V2Table :rows="comboRows" prefix="C" row-key="combo" unit="種" export-name="資料品質_來源組合"
                   :cols="[{ k: 'label', label: '出現在哪幾個來源' }, { k: 'count', label: '筆數', mono: true, link: (r: any) => u({ qt: 'c', qv: r.combo }) }]" />
        </template>
        <p v-else-if="trustErr" class="err">{{ trustErr }}</p>

        <template v-if="fillRows.length">
          <h3 class="h3">填充率最低的欄位（待補資料）<InfoNote>人可以維護的欄位裡，目前收得最少的前 12 個。分母＝{{ fill?.total_rows }} 筆 hardware。待處理旗標、收集錯誤、時間戳、*_override／*_source 這類系統狀態欄位可以合法為空，不列在這裡。</InfoNote></h3>
          <V2Table :rows="fillRows" prefix="F" row-key="column" unit="欄" export-name="資料品質_填充率最低"
                   :cols="[{ k: 'meaning', label: '欄位' }, { k: 'column', label: '欄位代號', mono: true }, { k: 'filled', label: '已填（筆）', mono: true }, { k: 'pct', label: '填充率', mono: true }]" />
        </template>

        <SshProbe />

        <h3 class="h3"><NuxtLink :to="q('qn') === '1' ? u() : u({ qn: '1' })">{{ q('qn') === '1' ? '▾' : '▸' }} 只出現一次的人名（可能打錯字）{{ singleNames.length }} 個</NuxtLink>
          <InfoNote>跨「使用者／保管者／人員名冊」統計，只用在一筆的名字。可能是真的只管一台，也可能是把「李太明」打成「李太名」。點名字帶入下方合併的「原名」。</InfoNote></h3>
        <p v-if="singleErr" class="err">{{ singleErr }}</p>
        <template v-if="q('qn') === '1'">
          <div class="names">
            <button v-for="n in singleNames" :key="n" type="button" class="nchip" :class="{ on: mergeFrom === n }" @click="pickMergeFrom(n)">{{ n }}</button>
            <span v-if="!singleNames.length" class="muted">沒有只出現一次的人名。</span>
          </div>
          <div class="merge">
            <b>合併到正確人名</b><InfoNote>把打錯字的名字（原名）併到正確名字。會改使用者／保管者／人員名冊三處完全一致的值。先按「預覽」看會動幾筆，確認無誤再按「確認合併」——這個動作直接改資料。</InfoNote>
            <div class="bar">
              <input v-model="mergeFrom" class="minp" type="text" placeholder="原名（打錯的，可點上面名字帶入）" aria-label="原名">
              <span class="muted">→</span>
              <input v-model="mergeTo" class="minp" type="text" placeholder="正確名" aria-label="正確名">
              <button type="button" class="btn2" :disabled="mergeBusy" @click="doMergePreview">預覽</button>
            </div>
            <template v-if="mergePreview">
              <template v-if="mergePreview.total">
                <p>會把「<b>{{ mergePreview.from_name }}</b>」改成「<b>{{ mergePreview.to_name }}</b>」，共 <b>{{ mergePreview.total }}</b> 筆</p>
                <V2Table :rows="previewFields" prefix="M" row-key="label" unit="欄" export-name="人名合併_預覽_欄位" :cols="[{ k: 'label', label: '欄位' }, { k: 'count', label: '筆數', mono: true }]" />
                <V2Table v-if="previewSample.length" :rows="previewSample" prefix="E" row-key="__no" unit="筆" export-name="人名合併_預覽_影響的機器"
                         :cols="[{ k: 'hostname', label: '影響的機器（前幾筆）', text: (r: any) => r.hostname || r.asset_serial, link: (r: any) => machine(r.asset_serial) }, { k: 'field', label: '欄位' }]" />
                <button v-if="canWrite" type="button" class="btn2 danger" :disabled="mergeBusy" @click="doMergeApply">確認合併（{{ mergePreview.total }} 筆）</button>
                <span v-else class="muted">合併限一般帳號以上</span>
              </template>
              <p v-else class="muted">找不到完全一致的「{{ mergePreview.from_name }}」，沒有可合併的資料。</p>
            </template>
          </div>
        </template>
      </template>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { margin: 16px 0 6px; font-size: 15px; display: flex; align-items: center; gap: 6px; }
.h3 a { color: var(--ink); text-decoration: none; }
.hero { display: flex; gap: 24px; align-items: center; flex-wrap: wrap; padding: 12px 16px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.score-box { text-align: center; min-width: 150px; }
.score { font-family: var(--disp); font-size: 44px; font-weight: 700; color: var(--brand-dark); line-height: 1; }
.score small { font-size: 20px; }
.score.none { color: var(--muted); }
.sl { font-size: 12px; color: var(--muted); margin-top: 4px; display: flex; gap: 4px; justify-content: center; align-items: center; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); text-decoration: none; }
.chip b { font-family: var(--disp); color: var(--brand-dark); }
.names { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
.nchip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 999px; background: var(--sub); color: var(--ink); border: 1px solid var(--border); cursor: pointer; }
.nchip.on { background: var(--mint); border-color: var(--brand); color: var(--brand-dark); }
.merge { margin-top: 10px; padding-top: 8px; border-top: 1px dashed var(--border); font-size: 13px; }
.minp { font-family: inherit; font-size: 13px; padding: 4px 10px; min-width: 200px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.btn2.danger { border-color: var(--bad); color: var(--bad); margin-top: 8px; }
.btn2:disabled { opacity: .45; cursor: default; }
.warn { color: var(--warn-text); background: var(--warn-soft); border-radius: 10px; padding: 6px 12px; font-size: 13px; display: flex; gap: 6px; align-items: center; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
</style>
