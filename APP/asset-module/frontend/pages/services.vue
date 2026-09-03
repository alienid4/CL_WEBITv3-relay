<script setup lang="ts">
// M2 第一片：服務盤點——「這台主機看得出什麼服務」。
//
// 這頁跟掃描結果的差別：掃描結果是「從外面看得到誰活著」，這頁是「進去問機器你在跑什麼」。
// 兩者會不一致（只綁 127.0.0.1 的服務外面掃不到），不一致本身就是有用的資訊。
//
// 資料可信度分兩級，畫面必須讓人一眼看出來：
//   確定 = 機器自己報的行程名（要 root 才拿得到）
//   推測 = 只拿到埠號，用對照表猜的（收集帳號是唯讀非 root，這是常態）
interface ServiceRow {
  id: number
  ip: string
  hostname: string | null
  asset_serial: string | null
  proto: string
  port: number
  bind_addr: string | null
  exposure: string | null
  process: string | null
  service_guess: string | null
  guess_source: string | null
  is_infra: number
  source: string
  first_seen: string | null
  last_seen: string | null
  gone_at: string | null
}
interface Summary {
  total: number; live: number; gone: number; hosts: number
  exposed: number; confirmed: number
  last_run: { started_at: string | null; status: string; service_count: number | null } | null
}

const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()

// ?port=3306 → 只看這個埠（天條二：埠點下去要看到「還有誰也在跑它」）
// ?ip= / ?asset_serial= → 從資產詳細頁點過來時只看那一台
const portFilter = computed(() => {
  const p = Number(route.query.port)
  return Number.isFinite(p) && p > 0 ? p : null
})
const ipFilter = computed(() => (route.query.ip as string) || '')
const serialFilter = computed(() => (route.query.asset_serial as string) || '')
const hasDrill = computed(() => !!(portFilter.value || ipFilter.value || serialFilter.value))

async function clearDrill() {
  await router.replace({ path: '/services', query: {} })
}

const rows = ref<ServiceRow[]>([])
const summary = ref<Summary | null>(null)
const loading = ref(false)
const errorMessage = ref('')

// 篩選
const showInfra = ref(false)      // 預設收合 SSH/NTP 這類管理服務，先看業務服務
const showGone = ref(false)
const exposureFilter = ref('')
const keyword = ref('')

// 排序走後端（這張表可能上千列，前端排只會排到目前這批）
const sortKey = ref('ip')
const sortDir = ref<'asc' | 'desc'>('asc')
function toggle(k: string) {
  if (sortKey.value === k) sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  else { sortKey.value = k; sortDir.value = 'asc' }
  load()
}

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    const data = await apiFetch<{ items: ServiceRow[]; summary: Summary }>('/api/services', {
      query: {
        // 下鑽時強制含管理服務與已消失：點「22」是想看全部在跑 SSH 的機器，
        // 結果被預設篩選擋掉一半會讓人以為資料不見了
        include_infra: hasDrill.value ? true : showInfra.value,
        include_gone: hasDrill.value ? true : showGone.value,
        exposure: exposureFilter.value || undefined,
        port: portFilter.value || undefined,
        ip: ipFilter.value || undefined,
        asset_serial: serialFilter.value || undefined,
        sort_by: sortKey.value,
        order: sortDir.value,
      },
    })
    rows.value = data.items
    summary.value = data.summary
  } catch {
    errorMessage.value = '服務清單載入失敗，請稍後再試'
  } finally {
    loading.value = false
  }
}
await load()
watch([showInfra, showGone, exposureFilter], load)
watch(() => route.query, load)

// 關鍵字只在目前這批做本地過濾（伺服器已排序，這裡不改順序）
const shown = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return rows.value
  return rows.value.filter(r =>
    [r.ip, r.hostname, r.process, r.service_guess, String(r.port)]
      .some(v => (v || '').toString().toLowerCase().includes(kw)))
})

// 採集：會真的連出去，可能久——三態回饋（async-feedback 規範）
const collecting = ref(false)
async function collect() {
  if (collecting.value) return
  collecting.value = true
  showToast('開始採集服務，連線中…', 'info')
  try {
    const r = await apiFetch<{ candidates: number; services: number; failed: any[]; hosts: any[] }>(
      '/api/services/collect', { method: 'POST' })
    const gone = r.hosts.reduce((n: number, h: any) => n + (h.gone || 0), 0)
    const added = r.hosts.reduce((n: number, h: any) => n + (h.added || 0), 0)
    if (r.candidates === 0) {
      showToast('沒有已納管的主機可收——先到「納入管理」把主機納管', 'warn')
    } else if (r.failed.length) {
      showToast(`收了 ${r.candidates} 台：新增 ${added}、消失 ${gone}；${r.failed.length} 台失敗`, 'warn')
    } else {
      showToast(`完成：${r.candidates} 台、${r.services} 個服務（新增 ${added}、消失 ${gone}）`, 'success')
    }
    await load()
  } catch (e: any) {
    showToast(`採集失敗：${e?.data?.detail || e?.message || '未知錯誤'}`, 'error')
  } finally {
    collecting.value = false
  }
}

const EXPOSURE_LABEL: Record<string, string> = {
  all: '對外', localhost: '僅本機', specific: '限特定網卡', unknown: '未知',
}
</script>

<template>
  <div>
    <div class="section-divider">服務盤點</div>

    <p class="lead">
      進到已納管主機問「你在跑什麼」，收監聽埠與行程。這是主機自己講的，
      跟掃描結果（從外面看）不同——只綁 127.0.0.1 的服務外面掃不到，這裡看得到。
    </p>

    <!-- 摘要：全是「這份資料有多真」的線索，不是裝飾 -->
    <div v-if="summary" class="tiles">
      <div class="tile">
        <div class="t-num mono">{{ summary.live }}</div>
        <div class="t-lbl">目前在聽的服務</div>
      </div>
      <div class="tile">
        <div class="t-num mono">{{ summary.hosts }}</div>
        <div class="t-lbl">收到服務的主機</div>
      </div>
      <div class="tile">
        <div class="t-num mono">{{ summary.exposed }}</div>
        <div class="t-lbl">對外開放（別台連得到）</div>
      </div>
      <div class="tile">
        <div class="t-num mono">{{ summary.confirmed }}<small class="of">/{{ summary.live }}</small></div>
        <div class="t-lbl">有行程名佐證<span class="hint">其餘只有埠號推測</span></div>
      </div>
      <div class="tile" :class="{ warn: summary.gone > 0 }">
        <div class="t-num mono">{{ summary.gone }}</div>
        <div class="t-lbl">曾經有、現在不見了</div>
      </div>
    </div>

    <div v-if="hasDrill" class="drill">
      正在看：
      <b v-if="portFilter">埠 {{ portFilter }}</b>
      <b v-if="ipFilter">{{ ipFilter }}</b>
      <b v-if="serialFilter">{{ serialFilter }}</b>
      <span class="dim">（已含管理服務與已消失的）</span>
      <button class="btn small" type="button" @click="clearDrill">看全部</button>
    </div>

    <div class="bar">
      <button class="btn primary" type="button" :disabled="collecting" @click="collect">
        {{ collecting ? '採集中…' : '立即採集' }}
      </button>
      <span v-if="summary?.last_run" class="when">
        上次採集 · {{ summary.last_run.started_at || '—' }}
        <span :class="summary.last_run.status === 'ok' ? 'ok' : 'bad'">
          {{ summary.last_run.status === 'ok' ? '成功' : '失敗' }}
        </span>
      </span>

      <div class="spacer"></div>

      <input v-model="keyword" class="kw" type="search" placeholder="搜尋 IP／主機／服務／埠" />
      <select v-model="exposureFilter" class="sel">
        <option value="">全部曝露</option>
        <option value="all">對外</option>
        <option value="localhost">僅本機</option>
        <option value="specific">限特定網卡</option>
      </select>
      <label class="chk"><input v-model="showInfra" type="checkbox" />含管理服務（SSH/NTP…）</label>
      <label class="chk"><input v-model="showGone" type="checkbox" />含已消失</label>
    </div>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-else-if="loading" class="muted">載入中…</p>
    <p v-else-if="shown.length === 0" class="muted">
      <template v-if="summary && summary.total === 0">
        還沒有採集過服務。按「立即採集」對已納管的主機收一輪。
      </template>
      <template v-else>這個條件下沒有服務。</template>
    </p>

    <table v-else class="tbl">
      <thead>
        <tr>
          <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機</SortTh>
          <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
          <SortTh k="port" :active="sortKey" :dir="sortDir" @sort="toggle">埠</SortTh>
          <SortTh k="proto" :active="sortKey" :dir="sortDir" @sort="toggle">協定</SortTh>
          <SortTh k="service_guess" :active="sortKey" :dir="sortDir" @sort="toggle">服務</SortTh>
          <SortTh k="guess_source" :active="sortKey" :dir="sortDir" @sort="toggle">依據</SortTh>
          <SortTh k="exposure" :active="sortKey" :dir="sortDir" @sort="toggle">曝露</SortTh>
          <SortTh k="last_seen" :active="sortKey" :dir="sortDir" @sort="toggle">最後看到</SortTh>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in shown" :key="r.id" :class="{ gone: r.gone_at }">
          <td>
            <NuxtLink v-if="r.asset_serial" class="dl" :to="`/assets/${r.asset_serial}`">
              {{ r.hostname || r.asset_serial }}
            </NuxtLink>
            <span v-else class="dim">（未登記）</span>
          </td>
          <td class="mono">
            <NuxtLink v-if="r.asset_serial" class="dl" :to="`/assets/${r.asset_serial}`">{{ r.ip }}</NuxtLink>
            <template v-else>{{ r.ip }}</template>
          </td>
          <!-- 天條二：埠可點——列出全部同樣開這個埠的主機（「誰在跑 MySQL」一眼看完） -->
          <td class="mono">
            <NuxtLink class="dl" :to="{ path: '/services', query: { port: r.port } }">{{ r.port }}</NuxtLink>
          </td>
          <td class="dim">{{ r.proto }}</td>
          <td>
            <span v-if="r.service_guess">{{ r.service_guess }}</span>
            <span v-else class="dim">—</span>
            <span v-if="r.is_infra" class="tag infra">管理</span>
          </td>
          <td>
            <span v-if="r.guess_source === 'process'" class="pill done" title="機器自己報的行程名">確定</span>
            <span v-else-if="r.guess_source === 'port'" class="pill open" title="只拿得到埠號，依對照表推測（收集帳號非 root）">推測</span>
            <span v-else class="dim">—</span>
          </td>
          <td>
            <span class="exp" :class="r.exposure || 'unknown'">{{ EXPOSURE_LABEL[r.exposure || 'unknown'] }}</span>
            <span class="bind mono dim">{{ r.bind_addr }}</span>
          </td>
          <td class="mono dim">
            <span v-if="r.gone_at" class="pill gone-pill" :title="`最後看到 ${r.last_seen}`">已消失</span>
            <template v-else>{{ r.last_seen || '—' }}</template>
          </td>
        </tr>
      </tbody>
    </table>

    <p v-if="summary && summary.live > 0 && summary.confirmed === 0" class="foot-note">
      目前全部服務都只有埠號推測——收集帳號 <code>webit3scan</code> 是唯讀非 root，
      看不到行程名。這是刻意不擴權的結果，不是採集失敗；埠與曝露仍然是機器實際回報的事實。
    </p>
  </div>
</template>

<style scoped>
.lead { color: var(--muted); margin: 0 0 16px; line-height: 1.7; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 16px; }
.tile { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 14px 16px; }
.tile.warn { border-color: rgba(230, 170, 60, .45); }
.t-num { font-size: 26px; font-weight: 700; color: var(--brand-dark); line-height: 1.1; }
.t-num .of { font-size: 14px; color: var(--muted); font-weight: 500; }
.t-lbl { font-size: 12px; color: var(--muted); margin-top: 4px; }
.t-lbl .hint { display: block; font-size: 10px; opacity: .7; }

.drill { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px;
  background: rgba(0,145,66,.08); border: 1px solid rgba(0,145,66,.28);
  border-radius: 10px; padding: 8px 12px; margin-bottom: 12px; }
.drill b { color: var(--brand-dark); }
.btn.small { padding: 3px 10px; font-size: 12px; }
.bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
.spacer { flex: 1; }
.btn { border-radius: 9px; border: 1px solid var(--border); background: transparent; color: inherit;
  padding: 7px 14px; cursor: pointer; font-size: 13px; }
.btn.primary { background: var(--brand); border-color: transparent; color: #fff; font-weight: 600; }
.btn:disabled { opacity: .55; cursor: progress; }
.kw, .sel { background: var(--card); border: 1px solid var(--border); color: inherit;
  border-radius: 9px; padding: 6px 10px; font-size: 13px; }
.kw { min-width: 200px; }
.chk { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 5px; cursor: pointer; }
.when { font-size: 12px; color: var(--muted); }
.when .ok { color: var(--brand-dark); }
.when .bad { color: var(--bad); }

.tbl tr.gone td { opacity: .5; }
.tag.infra { font-size: 10px; padding: 1px 6px; border-radius: 6px; margin-left: 6px;
  border: 1px solid var(--border); color: var(--muted); }
.pill { font-size: 11px; padding: 2px 8px; border-radius: 999px; }
.pill.done { background: rgba(0,145,66,.16); color: var(--brand-dark); }
.pill.open { background: rgba(230,170,60,.16); color: var(--warn-text); }
.pill.gone-pill { background: rgba(224,108,108,.16); color: var(--bad); }
.exp { font-size: 12px; }
.exp.all { color: var(--warn-text); }
.exp.localhost { color: var(--muted); }
.bind { display: block; font-size: 10px; }
.dim { color: var(--muted); }
.foot-note { margin-top: 14px; font-size: 12px; color: var(--muted); line-height: 1.7;
  border-left: 2px solid var(--border); padding-left: 12px; }
.foot-note code { background: rgba(15,23,42,.06); padding: 1px 5px; border-radius: 4px; }
</style>
