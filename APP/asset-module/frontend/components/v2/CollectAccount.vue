<script setup lang="ts">
// 設定 → 收集身分 → 收集帳號遷移（V24，2.048.12 起 V2 原生；取代嵌入的舊頁 pages/collect-account.vue）。同一批 API，行為不變（⚠D1）：
// GET /api/collect-account/migration、/migration/detail?state=、/migration/removal-plan、/migration/removal-script；
// POST /migration/probe（每台只跑 `id`，唯讀）、/migration/switch、/migration/revert（管理員；後端 ADMIN_WRITE）。
// 這頁顯示的是「實測結果」不是設定值（2026-09-23「設定說 A、機器上是 B」的坑）。移除舊帳號只產出指令，系統不代為執行。
// 網址：?st=identity&sub=migration&ms=<狀態>（點卡片下鑽）。
const { apiFetch } = useApi()
const route = useRoute()
const { isAdmin } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const data = ref<any>(null)
const err = ref('')
const msg = ref('')
const msgBad = ref(false)
const busy = ref(false)
async function loadSummary() {
  try { data.value = await apiFetch<any>('/api/collect-account/migration'); err.value = '' }
  catch (e: any) { err.value = `遷移看板載入失敗：${errMsg(e)}` }      // 已有看板時只在上方顯示，畫面不被換掉
}
onMounted(loadSummary)

// 「要做事的排前面」：可切換 > 待納管 > 兩個都進不去 > 已完成；無法判斷、不適用不是待辦，淡掉放最後
const ORDER = ['可切換', '待納管', '兩個都進不去', '已完成', '無法判斷', '不適用']
const cards = computed(() => ORDER
  .filter(k => (data.value?.counts?.[k] ?? 0) > 0 || ['可切換', '待納管', '已完成'].includes(k))
  .map(k => ({ key: k, n: data.value?.counts?.[k] ?? 0 })))
const ms = computed(() => (typeof route.query.ms === 'string' && ORDER.includes(route.query.ms) ? route.query.ms : ''))
const u = (state = '') => ({ path: '/v2/settings', query: Object.fromEntries(Object.entries({ st: 'identity', sub: 'migration', ms: state }).filter(([, v]) => v)) })

// ---- 下鑽明細（網址 ms）----
const detail = ref<any[] | null>(null)
const detErr = ref('')
const beginDet = useLatest()
async function loadDetail(refresh = false) {
  const isLatest = beginDet()
  if (!refresh) detail.value = null          // 動作後的背景重抓不清表（排序、分頁不重設）
  detErr.value = ''
  if (!ms.value) return
  try { const r = await apiFetch<any>(`/api/collect-account/migration/detail?state=${encodeURIComponent(ms.value)}`); if (isLatest()) detail.value = r.items ?? [] }
  catch (e: any) { if (isLatest()) detErr.value = `明細載入失敗：${errMsg(e)}` }
}
watch(ms, () => loadDetail(), { immediate: true })
const okText = (v: any) => (v === null || v === undefined ? '—' : v ? '連得上' : '連不上')
const detRows = computed(() => (detail.value ?? []).map((d: any, i: number) => ({ ...d, __no: i + 1, new_t: okText(d.new_ok) + (d.new_err ? ` · ${d.new_err}` : ''), old_t: okText(d.old_ok), probed_t: d.probed_at || '從未' })))

async function act(kind: 'probe' | 'switch' | 'revert', d?: any) {
  if (kind === 'switch' && !window.confirm(`要把 ${d.hostname || d.asset_serial}（${d.ip}）的收集帳號切成 ${data.value?.target_account} 嗎？\n\n`
    + '只有這一台會改變。切換後請按一次收集確認收得到；萬一收不到，這一列會出現「還原」可以立刻退回去。')) return
  busy.value = true; msg.value = ''; msgBad.value = false
  try {
    const url = kind === 'probe' ? '/api/collect-account/migration/probe' : `/api/collect-account/migration/${kind}?asset_serial=${encodeURIComponent(d.asset_serial)}`
    const r = await apiFetch<any>(url, { method: 'POST' })
    msg.value = kind === 'probe' ? `探測完成：${r.probed} 台` : r.text
    await loadSummary(); if (ms.value) await loadDetail(true)
  } catch (e: any) { msgBad.value = true; msg.value = `${kind === 'probe' ? '探測' : kind === 'switch' ? '切換' : '還原'}失敗：${errMsg(e)}` } finally { busy.value = false }
}

// ---- 移除舊帳號：先列清單、只產出指令，系統不代為執行 ----
const plan = ref<any>(null)
const script = ref('')
const planErr = ref('')
async function loadPlan() {
  planErr.value = ''
  try { plan.value = await apiFetch<any>('/api/collect-account/migration/removal-plan'); script.value = '' } catch (e: any) { planErr.value = `移除清單載入失敗：${errMsg(e)}` }
}
async function loadScript() {
  planErr.value = ''
  try { script.value = await apiFetch<string>('/api/collect-account/migration/removal-script', { responseType: 'text' } as any) } catch (e: any) { planErr.value = `指令清單產生失敗：${errMsg(e)}` }
}
const planRows = computed(() => (plan.value?.items ?? []).map((i: any, n: number) => ({ ...i, __no: n + 1 })))
</script>

<template>
  <div>
    <p v-if="err" class="err" data-test="err">{{ err }}</p>
    <p v-if="!data && !err" class="muted">載入中…</p>
    <template v-if="data">
      <div class="bar">
        <span>目標帳號 <code>{{ data.target_account }}</code>　舊名 <code>{{ data.legacy_account }}</code></span>
        <InfoNote>所有平台統一用新帳號、舊名逐台淘汰。8 個字元是被 AIX 的 <code>max_logname</code> 逼出來的（預設只給 8，放寬要 chdev 並重開機），
          Linux 沒有這個限制，所以兩邊能共用的只有短的那個。這一頁顯示的是實測結果，不是設定值。探測只在每台上跑 <code>id</code>——唯讀，不切換任何一台、不刪任何帳號。</InfoNote>
        <button v-if="isAdmin" type="button" class="btn" :disabled="busy" @click="act('probe')">{{ busy ? '處理中…' : '重新探測' }}</button>
        <span v-else class="muted" data-test="admin-only">探測、切換限管理員</span>
        <span v-if="msg" class="msg" :class="{ bad: msgBad }" data-test="msg">{{ msg }}</span>
      </div>

      <p v-if="!data.probed" class="muted big" data-test="never">還沒探測過任何機器（不是 0%：「還沒測」跟「一台都還沒完成」是兩件事）。</p>
      <template v-else>
        <div class="cards">
          <NuxtLink v-for="c in cards" :key="c.key" :to="u(ms === c.key ? '' : c.key)" class="card" :class="{ on: ms === c.key, dim: ['無法判斷', '不適用'].includes(c.key) }">
            <b class="n">{{ c.n }}</b><span class="k">{{ c.key }}</span></NuxtLink>
        </div>
        <p class="formula">完成率 <NuxtLink :to="u('已完成')" class="pct">{{ data.done_pct === null ? '—' : data.done_pct + '%' }}</NuxtLink>
          <span class="muted">＝ 已完成 <NuxtLink :to="u('已完成')">{{ data.counts['已完成'] }}</NuxtLink> ÷ 分母 {{ data.denominator }}
            （<NuxtLink :to="u('可切換')">可切換 {{ data.counts['可切換'] }}</NuxtLink>、<NuxtLink :to="u('待納管')">待納管 {{ data.counts['待納管'] }}</NuxtLink>、<NuxtLink :to="u('兩個都進不去')">兩個都進不去 {{ data.counts['兩個都進不去'] }}</NuxtLink>）</span>
          <InfoNote>{{ data.denominator_text }}</InfoNote></p>

        <template v-if="ms">
          <h3 class="h3">{{ ms }}</h3>
          <p v-if="detErr" class="err">{{ detErr }}</p>
          <p v-else-if="!detail" class="muted">載入中…</p>
          <V2Table v-else :rows="detRows" prefix="M" row-key="asset_serial" unit="台" :max="200" :export-name="`收集帳號遷移_${ms}`" empty="沒有機器"
                   :cols="[{ k: 'hostname', label: '主機名', link: (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null) }, { k: 'ip', label: 'IP', mono: true },
                           { k: 'platform', label: '平台' }, { k: 'new_t', label: '新帳號' }, { k: 'old_t', label: '舊帳號' }, { k: 'probed_t', label: '探測時間', mono: true },
                           { k: 'why', label: '依據' }, { k: 'act', label: '動作' }]">
            <template #cell-act="{ row }">
              <button v-if="isAdmin && row.state === '可切換'" type="button" class="lb" :disabled="busy" @click="act('switch', row)">切換這台</button>
              <button v-else-if="isAdmin && row.new_ok === 1" type="button" class="lb" :disabled="busy" @click="act('revert', row)">還原</button>
              <span v-else class="muted">—</span>
            </template>
          </V2Table>
        </template>
      </template>

      <h3 class="h3">移除舊帳號</h3>
      <div class="bar">
        <span class="muted">系統不會替你執行這些指令。</span>
        <InfoNote>刪帳號要 root，而收集帳號是唯讀非 root（刻意的）；為了一次性的遷移給常駐收集身分刪帳號的權力不划算。
          這裡產出的是純文字、可讀、可稽核的指令，一台一行，可以只挑幾台執行；刻意不做成一鍵管道、不做 base64、不落地暫存檔（在金融環境會被 SOC 當成事件）。</InfoNote>
        <button type="button" class="btn2" @click="loadPlan">看有哪些要移除</button>
        <button v-if="plan" type="button" class="btn2" @click="loadScript">產生指令清單</button>
      </div>
      <p v-if="planErr" class="err">{{ planErr }}</p>
      <template v-if="plan">
        <p class="muted">共 <b>{{ plan.count }}</b> 台。{{ plan.note }}</p>
        <V2Table v-if="plan.count" :rows="planRows" prefix="R" row-key="asset_serial" unit="台" :max="200" export-name="收集帳號遷移_移除計畫"
                 :cols="[{ k: 'hostname', label: '主機名' }, { k: 'ip', label: 'IP', mono: true }, { k: 'platform', label: '平台' }, { k: 'account', label: '要刪的帳號', mono: true },
                         { k: 'command', label: '指令', mono: true }, { k: 'probed_at', label: '探測於', mono: true }]" />
      </template>
      <pre v-if="script" class="script" data-test="script">{{ script }}</pre>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { margin: 14px 0 6px; font-size: 15px; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.btn:disabled, .lb:disabled { opacity: .5; cursor: default; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 12px; padding: 0 6px 0 0; }
.cards { display: flex; gap: 10px; flex-wrap: wrap; margin: 10px 0 8px; }
.card { min-width: 120px; padding: 10px 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); text-decoration: none; color: var(--ink); display: flex; flex-direction: column; }
.card.on { border-color: var(--brand); background: var(--mint); }
.card.dim { opacity: .6; }
.card .n { font-family: var(--disp); font-size: 24px; }
.card .k { font-size: 12px; color: var(--ink-soft); }
.formula { font-size: 13px; line-height: 1.9; }
.pct { font-family: var(--disp); font-weight: 700; font-size: 17px; }
.msg { color: var(--brand-dark); }
.msg.bad { color: var(--bad); }
.big { font-size: 14px; }
.script { background: var(--sub); padding: 10px; border-radius: 6px; white-space: pre-wrap; font-size: 12px; line-height: 1.6; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
code { background: var(--sub); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
</style>
