<script setup lang="ts">
// 10.2 分布統計「基礎設施」「AP 系統」頁籤（V66，2.059.0；設計 docs/ui-v2/V66分布統計_設計.md）。
// 舊 /distribution 同一批 API，不寫新的計算（⚠D3）：
//   清單＝GET /api/stats/systems?limit=0&kind=（一律全載，分群小計才不會少算）
//   下鑽＝GET /api/stats/systems/{api_id}（永遠全部環境；點某個環境只是標「從哪裡點進來」，不篩掉——09-10 使用者要求）
//   格子是哪幾台＝GET /api/stats/systems/{api_id}/assets（location／env 或 role／location）
//   改分級＝PUT /api/systems/class（後端 ADMIN_WRITE，限管理員——access_policy.py 2.060.x；傳空＝回到分級表判定）
// 網址：?tab=infra|ap &g=分群／分級 &sys=系統代號 &fe=從哪個環境點進來 &cl=&ce=（機房×環境格子）&rr=&rl=（角色×機房格子）
import { recordFilterQuery } from '~/utils/assetRecords'
const props = defineProps<{ kind: 'infra' | 'ap' }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const { isAdmin } = useAuth()   // 2.060.x：改分級限管理員
const route = useRoute()
const router = useRouter()
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const enc = encodeURIComponent
const n = (v: number | null | undefined) => (v ?? 0).toLocaleString()
const ENV_ORDER = ['正式', '備援', '測試', '未填']
const ordered = (present: string[], order: string[]) => [...order.filter(k => present.includes(k)), ...present.filter(k => !order.includes(k))]

const sys = ref<any>(null)
const err = ref('')
async function load() {
  err.value = ''
  try { sys.value = await apiFetch('/api/stats/systems', { query: { limit: 0, kind: props.kind } }) } catch (e: any) { err.value = `系統清單載入失敗：${errMsg(e)}` }
}
onMounted(load)

// 網址組裝：保留 tab，其餘照給的鍵
function url(extra: Record<string, string | null | undefined>) {
  const qs = new URLSearchParams({ tab: props.kind })
  for (const [k, v] of Object.entries(extra)) if (v !== null && v !== undefined && v !== '') qs.set(k, v)
  return `/v2/dist?${qs.toString()}`
}
const envs = computed<string[]>(() => ordered(sys.value?.envs ?? [], ENV_ORDER))

// 分群（基礎設施）／分級（AP）頁籤：每群照後端給的順序；不在順序表裡的群也不丟
const groupOf = (s: any) => (props.kind === 'infra' ? s.group : s.sys_class)
const groups = computed(() => {
  const all: any[] = sys.value?.systems ?? []
  const order: string[] = (props.kind === 'infra' ? sys.value?.group_order : sys.value?.class_order) ?? []
  const keys = ordered([...new Set(all.map(groupOf))], order)
  return keys.map(g => ({ group: g, rows: all.filter(s => groupOf(s) === g) })).filter(x => x.rows.length)
})
const group = computed(() => (groups.value.some(g => g.group === q('g')) ? q('g') : groups.value[0]?.group ?? ''))
const groupTotal = (rows: any[]) => rows.reduce((a, b) => a + (b.total ?? 0), 0)
// 項次：每一群內照後端排序固定編號（重排表格後同一個系統還是同一號）
const sysRows = computed(() => (groups.value.find(g => g.group === group.value)?.rows ?? []).map((s: any, i: number) => {
  const o: any = { ...s, __no: i + 1, cls_text: `${s.sys_class}${s.sys_class_core ? '（核心）' : ''}${s.sys_class_override ? ' ✎' : ''}`, name_text: s.name || '（無名稱）', dept: s.ap_department || '' }
  envs.value.forEach((e, j) => { o[`e${j}`] = s.by_env?.[e] ?? 0 })
  return o
}))
const sysCols = computed(() => [
  { k: 'cls_text', label: '類別' },
  { k: 'name_text', label: '系統', link: (r: any) => url({ g: group.value, sys: r.api_id }) },
  { k: 'api_id', label: 'AP ID', mono: true, link: (r: any) => `/v2/system/${enc(r.api_id)}` },
  { k: 'dept', label: 'AP 部門' },
  ...envs.value.map((e, j) => ({ k: `e${j}`, label: `${e}（台）`, mono: true, link: (r: any) => (r[`e${j}`] ? url({ g: group.value, sys: r.api_id, fe: e }) : null) })),
  { k: 'total', label: '合計（台）', mono: true, link: (r: any) => url({ g: group.value, sys: r.api_id }) },
  { k: 'rows', label: '登記（筆）', mono: true },
])

// 改分級（限管理員才出現，跟後端 ADMIN_WRITE 一致，2.060.x）：按「改」→ 選 → 按「確認」才送（跟舊頁一樣要明確一步，2.059.5 審查：不在下拉 change 時就送）
const saving = ref('')
const editing = ref('')
const editVal = ref('')
function startEdit(row: any) { editing.value = row.api_id; editVal.value = row.sys_class }
async function saveClass(apiId: string, cls: string) {
  saving.value = apiId
  try {
    await apiFetch('/api/systems/class', { method: 'PUT', body: { api_id: apiId, sys_class: cls } })
    showToast(cls ? `分級已改成「${cls}」` : '已回到分級表判定', 'success')
    editing.value = ''
    await load()        // 重載：分級頁籤歸屬、排序、小計都要重算
  } catch (e: any) { showToast(`分級更新失敗：${errMsg(e)}`, 'error') } finally { saving.value = '' }
}

// ---- 下鑽：一個系統 ----
const sysId = computed(() => q('sys'))
const drill = ref<any>(null)
const drillErr = ref('')
let seq = 0
watch(sysId, async (id) => {
  const my = ++seq
  drill.value = null; drillErr.value = ''
  if (!id) return
  try { const r = await apiFetch(`/api/stats/systems/${enc(id)}`); if (my === seq) drill.value = r } catch (e: any) { if (my === seq) drillErr.value = `系統下鑽載入失敗：${errMsg(e)}` }
}, { immediate: true })
const back = computed(() => url({ g: q('g') }))
const sysBase = computed(() => ({ g: q('g'), sys: sysId.value, fe: q('fe') }))
const dEnvs = computed<string[]>(() => {
  const seen = new Set<string>()
  for (const l of drill.value?.locations ?? []) for (const k of Object.keys(l.by_env ?? {})) seen.add(k)
  return ordered([...seen], ENV_ORDER)
})
const locRows = computed(() => (drill.value?.locations ?? []).map((l: any, i: number) => {
  const o: any = { location: l.location, total: l.total, __no: i + 1 }
  dEnvs.value.forEach((e, j) => { o[`e${j}`] = l.by_env?.[e] ?? 0 })
  return o
}))
const locCols = computed(() => [
  { k: 'location', label: '機房' },
  ...dEnvs.value.map((e, j) => ({ k: `e${j}`, label: `${e}（台）`, mono: true, link: (r: any) => (r[`e${j}`] ? url({ ...sysBase.value, cl: r.location, ce: e }) : null) })),
  { k: 'total', label: '合計（台）', mono: true },
])
const envTotal = (e: string) => (drill.value?.locations ?? []).reduce((s: number, l: any) => s + (l.by_env?.[e] ?? 0), 0)
const dLocs = computed<string[]>(() => (drill.value?.locations ?? []).map((l: any) => l.location))
const roleRows = computed(() => (drill.value?.role_matrix ?? []).filter((x: any) => x.total).map((rm: any, i: number) => {
  const o: any = { role: rm.role, total: rm.total, __no: i + 1 }
  dLocs.value.forEach((l, j) => { o[`l${j}`] = rm.by_location?.[l] ?? 0 })
  return o
}))
const roleCols = computed(() => [
  { k: 'role', label: '角色' },
  ...dLocs.value.map((l, j) => ({ k: `l${j}`, label: `${l}（台）`, mono: true, link: (r: any) => (r[`l${j}`] ? url({ ...sysBase.value, rr: r.role, rl: l }) : null) })),
  { k: 'total', label: '合計（台）', mono: true },
])

// 格子是哪幾台（兩種格子各一組鍵，一次只開一個）
const cellQuery = computed<Record<string, string> | null>(() => {
  if (!sysId.value) return null
  if (q('cl') && q('ce')) return { location: q('cl'), env: q('ce') } as Record<string, string>
  if (q('rr') && q('rl')) return { role: q('rr'), location: q('rl') } as Record<string, string>
  return null
})
const cell = ref<any[] | null>(null)
const cellErr = ref('')
let cseq = 0
watch([sysId, cellQuery], async ([id, cq]) => {
  const my = ++cseq
  cell.value = null; cellErr.value = ''
  if (!id || !cq) return
  try { const r: any = await apiFetch(`/api/stats/systems/${enc(id)}/assets`, { query: cq }); if (my === cseq) cell.value = r.items ?? [] } catch (e: any) { if (my === cseq) cellErr.value = `這一格的主機載入失敗：${errMsg(e)}` }
}, { immediate: true })
const cellLabel = computed(() => (q('cl') ? `${q('cl')}・${q('ce')}` : `${q('rl')}・${q('rr')}`))
const cellRows = computed(() => (cell.value ?? []).map((a: any, i: number) => ({ ...a, __no: i + 1, name: a.hostname || '（無主機名）', basis: a.purpose || a.device_model || '', onb: a.onboarded ? '已納管' : '', rc: a.row_count ?? 1 })))
const cellCols = [
  { k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${enc(r.asset_serial)}` },
  { k: 'ip', label: 'IP', mono: true }, { k: 'os', label: '作業系統' }, { k: 'environment', label: '環境' }, { k: 'location', label: '機房' },
  { k: 'role', label: '角色' }, { k: 'basis', label: '用途／判定依據' }, { k: 'onb', label: '納管' }, { k: 'rc', label: '登記（筆）', mono: true },
]
const closeCell = computed(() => url(sysBase.value))

// 結論列的連結：跟舊頁同一組條件，照轉址表同一條規則（帶資產篩選條件＝2.1 逐筆登記頁籤）換成 V2 網址
const assetsLink = (query: Record<string, string>) => ({ path: '/v2/hosts', query: { tab: 'records', ...recordFilterQuery(query) } })
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <template v-else-if="sys">
      <!-- 下鑽：一個系統 -->
      <template v-if="sysId">
        <div class="bar"><NuxtLink :to="back">‹ 返回清單</NuxtLink>
          <b v-if="drill">{{ drill.name || drill.api_id }}</b><span v-if="drill" class="mono muted">{{ drill.api_id }}</span>
          <span v-if="drill" class="muted">全部 {{ n(drill.total) }} 台<template v-if="drill.rows && drill.rows !== drill.total">（{{ n(drill.rows) }} 筆登記）</template></span>
          <span v-if="q('fe')" class="chip">從「{{ q('fe') }}」點進來</span>
          <NuxtLink v-if="drill" :to="`/v2/system/${encodeURIComponent(drill.api_id)}`">系統履歷 ›</NuxtLink>
          <InfoNote>點進系統之後永遠顯示全部環境；從某個環境點進來只會標出來，不會把其他環境藏起來（以前切掉其他環境，曾讓人以為系統上沒有某台）。每一格都可以點，看是哪幾台；格子台數＝點下去的列數（同一支計算）。</InfoNote>
        </div>
        <p v-if="drillErr" class="err">{{ drillErr }}</p>
        <template v-else-if="drill">
          <h3 class="h3">機房 × 環境</h3>
          <V2Table :rows="locRows" :cols="locCols" prefix="L" unit="列" empty="沒有主機" />
          <p class="muted sum">合計：<template v-for="e in dEnvs" :key="e">{{ e }} {{ n(envTotal(e)) }}・</template>全部 {{ n(drill.total) }}（台）</p>
          <template v-if="q('cl')">
            <div class="bar"><b>{{ cellLabel }}</b><span v-if="cell" class="muted">{{ cell.length }} 台</span><NuxtLink :to="closeCell">收起</NuxtLink></div>
            <p v-if="cellErr" class="err">{{ cellErr }}</p>
            <V2Table v-else-if="cell" :rows="cellRows" :cols="cellCols" prefix="H" unit="台" empty="沒有主機" />
            <p v-else class="muted">載入中…</p>
          </template>

          <h3 class="h3">角色 × 機房 <InfoNote>角色由強到弱判斷：機器上實際跑的服務（證據）→ 網通／儲存／容器平台節點 → 登記欄位關鍵字（推論）。「資料不完整」不等於沒資料，是缺判角色的線索，納管並收一輪服務可補上大部分。每列合計加起來＝這個系統的總台數。<template v-if="drill.role_basis"><br>判斷依據：{{ drill.role_basis }}</template></InfoNote></h3>
          <V2Table :rows="roleRows" :cols="roleCols" prefix="R" unit="列" empty="沒有主機" />
          <template v-if="q('rr')">
            <div class="bar"><b>{{ cellLabel }}</b><span v-if="cell" class="muted">{{ cell.length }} 台</span><NuxtLink :to="closeCell">收起</NuxtLink></div>
            <p v-if="cellErr" class="err">{{ cellErr }}</p>
            <V2Table v-else-if="cell" :rows="cellRows" :cols="cellCols" prefix="H" unit="台" empty="沒有主機" />
            <p v-else class="muted">載入中…</p>
          </template>
        </template>
        <p v-else class="muted">載入中…</p>
      </template>

      <!-- 清單 -->
      <template v-else>
        <p class="muted">
          本頁涵蓋 <b>{{ n(sys.mapped) }}</b> 台<template v-if="sys.mapped_rows && sys.mapped_rows !== sys.mapped">（{{ n(sys.mapped_rows) }} 筆登記）</template>・{{ n(sys.system_count) }} 個系統
          <template v-if="sys.partition">
            ／全庫 {{ n(sys.total_assets) }} 台 ＝ <NuxtLink to="/v2/hosts">CIA 登記 {{ n(sys.partition.managed) }}</NuxtLink>
            ＋ <NuxtLink :to="assetsLink({ source: 'offbook' })">CIA 待補登 {{ n(sys.partition.offbook) }}</NuxtLink>
            （另有 <NuxtLink :to="assetsLink({ filter_field: 'asset_status', filter_value: '__retired__' })">退役備查 {{ n(sys.partition.retired) }}</NuxtLink>，不計入）
            ／<NuxtLink :class="{ warn: (sys.unmapped_active_cia ?? 0) > 0 }" :to="assetsLink({ filter_field: 'api_id', filter_value: '', source: 'registered' })">CIA 登記卻沒填 AP ID {{ n(sys.unmapped_active_cia ?? 0) }} 台</NuxtLink>
          </template>
          <span v-else :class="{ warn: sys.unmapped > 0 }">／全庫對不上業務系統 {{ n(sys.unmapped) }} 台（共 {{ n(sys.total_assets) }} 台）</span>
          <InfoNote>
            基礎設施與 AP 系統依業務系統代碼前綴分：I- 是基礎設施、N- 是業務系統（照代碼分，不是人工判斷）<template v-if="Object.keys(sys.kind_overrides || {}).length">；人工指定搬過的：{{ Object.keys(sys.kind_overrides).join('、') }}</template>。
            環境只有四欄：使用者測試(UAT)、開發環境(DEV) 併進「測試」，跟來源系統對帳時要記得。
            「台」是機器台數（主機名＋IP 相同算同一台，同一台在同一個系統只算一次），「筆」是 CIA 登記筆數。同一台掛兩個系統，兩個系統各算一台，但全庫只算一台。
            CIA 待補登本來就沒有 AP ID、退役不用管，所以「沒填 AP ID」只算 CIA 登記的——那才是要回頭補的。
          </InfoNote>
        </p>
        <p v-if="sys.mapping && sys.mapping.reason" class="err">為什麼是 0 台：{{ sys.mapping.reason }}
          <InfoNote>業務系統對照表 {{ n(sys.mapping.business_system_rows) }} 筆，代碼例如：{{ (sys.mapping.sample_table_codes || []).join('、') || '（空）' }}；資產 {{ n(sys.mapping.assets_total) }} 筆，其中有填 AP ID 的 {{ n(sys.mapping.assets_with_api_id) }} 筆，代碼例如：{{ (sys.mapping.sample_asset_codes || []).join('、') || '（全空白）' }}</InfoNote></p>
        <div v-if="groups.length" class="gtabs">
          <NuxtLink v-for="g in groups" :key="g.group" :to="url({ g: g.group })" class="gtab" :class="{ on: group === g.group }">{{ g.group }}<span class="gtc">{{ n(groupTotal(g.rows)) }}</span></NuxtLink>
          <InfoNote v-if="kind === 'infra' && group === '未分群'">這些系統放不進三群（網路設備／Storage／其他大型系統），沒有硬塞進最像的那一群。要歸哪一群告訴系統管理員加進設定。</InfoNote>
          <InfoNote v-if="kind === 'ap'">分級來自業務系統分級表；有寫入權的人可以在「類別」欄直接改（分級表重匯不會沖掉），改過的標 ✎，選「回到分級表判定」可以取消。</InfoNote>
        </div>
        <V2Table :rows="sysRows" :cols="sysCols" prefix="S" unit="個系統" :max="200" empty="這一群沒有系統">
          <template v-if="isAdmin" #cell-cls_text="{ row }: { row: any }">
            <template v-if="editing === row.api_id">
              <select v-model="editVal" class="sel" :disabled="saving === row.api_id" :aria-label="`${row.api_id} 分級`">
                <option v-for="c in sys.class_order" :key="c" :value="c">{{ c }}</option>
                <option v-if="row.sys_class_override" value="">回到分級表判定（{{ row.sys_class_computed || '未分級' }}）</option>
              </select>
              <button type="button" class="sb" data-test="cls-ok" :disabled="saving === row.api_id || editVal === row.sys_class" @click="saveClass(row.api_id, editVal)">確認</button>
              <button type="button" class="sb" data-test="cls-cancel" :disabled="saving === row.api_id" @click="editing = ''">取消</button>
            </template>
            <template v-else>{{ row.sys_class }} <button type="button" class="sb" data-test="cls-edit" :aria-label="`改 ${row.api_id} 分級`" @click="startEdit(row)">改</button></template>
            <span v-if="row.sys_class_core" class="muted">核心</span>
            <span v-if="row.sys_class_override" class="muted" :title="`手動改過（分級表原判：${row.sys_class_computed || '未分級'}）`">✎</span>
          </template>
        </V2Table>
      </template>
    </template>
    <p v-else class="muted">載入中…</p>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 8px 0; font-size: 13px; }
.h3 { font-size: 15px; margin: 16px 0 6px; display: flex; align-items: center; gap: 6px; }
.muted { color: var(--muted); font-size: 13px; }
.mono { font-family: var(--disp); }
.sum { margin-top: 6px; }
.err { color: var(--bad); }
.warn { color: var(--warn-text); font-weight: 600; }
.chip { font-size: 12px; padding: 1px 9px; border-radius: 10px; background: var(--mint); color: var(--brand-dark); }
.gtabs { display: flex; gap: 4px; flex-wrap: wrap; align-items: center; margin: 8px 0; }
.gtab { border: 1px solid var(--border); background: var(--sub); color: var(--ink-soft); padding: 4px 12px; border-radius: 8px; font-size: 13px; text-decoration: none; }
.gtab.on { background: var(--mint); border-color: var(--brand); color: var(--ink); font-weight: 600; }
.gtc { margin-left: 6px; font-size: 11px; color: var(--ink-aux); }
.sb { font-family: inherit; font-size: 12px; padding: 1px 8px; margin-left: 4px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.sb:disabled { opacity: .5; cursor: default; }
.sel { font-family: inherit; font-size: 12px; padding: 2px 4px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
</style>
