<script setup lang="ts">
// 7.4 服務盤點（2.x 第二批）：共用盤點流程（任務＝凍結當下已收集的監聽服務埠＋與上一次比較）
// ＋「服務清單」頁籤（2.007.52，舊版 /services 翻新：目前收到的監聽埠與行程，即時、不是盤點任務凍結的那份）。
// 資料＝/api/services（舊版同一支）；數字卡的條件在後端 service_inventory.CARD_WHERE，卡片數字＝點下去的列數。
definePageMeta({ layout: 'v2' })
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋，這裡只是別讓人白按）
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const EXTRA = [{ k: 'list', label: '服務清單' }]

type Card = 'live' | 'current' | 'stale' | 'hosts' | 'exposed' | 'confirmed' | 'gone'
const CARDS: { k: Card; label: string }[] = [
  { k: 'live', label: '目前在聽的服務' }, { k: 'current', label: '現行' }, { k: 'stale', label: '過期' }, { k: 'hosts', label: '收到服務的主機' }, { k: 'exposed', label: '對外開放' },
  { k: 'confirmed', label: '有行程名佐證' }, { k: 'gone', label: '曾經有、現在不見了' },
]
const EXPOSURE: Record<string, string> = { all: '對外', localhost: '僅本機', specific: '限特定網卡', unknown: '未知' }
const card = computed(() => (CARDS.some(c => c.k === q('card')) ? (q('card') as Card) : ''))
const port = computed(() => (/^\d+$/.test(q('port')) ? Number(q('port')) : null))
const showInfra = ref(false)      // 預設收合 SSH／NTP 這類管理服務，先看業務服務
const showGone = ref(false)
const exposure = ref('')
const kw = ref('')
const data = ref<{ items: any[]; summary: any } | null>(null)
const err = ref('')
const busy = ref(false)
const drilled = computed(() => !!(card.value || port.value))
async function load() {
  if (route.query.tab !== 'list') return
  busy.value = true
  try {
    data.value = await apiFetch('/api/services', { query: {
      // 下鑽（點卡片或埠）時含管理服務與已消失：點「22」是要看全部在跑 SSH 的機器，被預設篩選擋掉會以為資料不見了
      include_infra: drilled.value ? true : showInfra.value, include_gone: drilled.value ? true : showGone.value,
      exposure: exposure.value || undefined, port: port.value || undefined, card: card.value || undefined, sort_by: 'ip', order: 'asc' } })
    err.value = ''
  } catch (e: any) { err.value = `服務清單載入失敗：${errMsg(e)}` } finally { busy.value = false }
}
watch(() => [route.query.tab, route.query.card, route.query.port, showInfra.value, showGone.value, exposure.value], load, { immediate: true })
const pick = (c: Card) => router.push({ query: { tab: 'list', ...(card.value === c ? {} : { card: c }) } })
const clearDrill = () => router.push({ query: { tab: 'list' } })

// 項次以預設排序（IP、埠）為準、固定不變——使用者重排或篩選後同一筆仍是同一號
const rows = computed(() => {
  const ipKey = (ip: string) => (ip || '').split('.').map(n => n.padStart(3, '0')).join('.')
  const all = [...(data.value?.items ?? [])].sort((a, b) => ipKey(a.ip).localeCompare(ipKey(b.ip)) || a.port - b.port || a.id - b.id)
    .map((r, i) => ({ ...r, __no: i + 1, host: r.hostname || '—', exp: EXPOSURE[r.exposure] ?? r.exposure ?? '—',
      svc: r.service_guess || r.process || '未知服務', sure: r.process ? '確定（行程名）' : '推測（埠號對照）', state: r.gone_at ? '已消失' : (r.is_stale ? '在聽（資料過期）' : '在聽'),
      first: (r.first_seen || '').slice(0, 10), last: (r.last_seen || '').slice(0, 16) }))
  const t = kw.value.trim().toLowerCase()
  return t ? all.filter(r => [r.ip, r.hostname, r.process, r.service_guess, String(r.port)].some(v => String(v ?? '').toLowerCase().includes(t))) : all
})
const hostCount = computed(() => new Set(rows.value.map(r => r.ip)).size)

const collecting = ref(false)
async function collect() {
  if (collecting.value) return
  collecting.value = true
  showToast('開始收集服務，連線中…', 'info')
  try {
    const r = await guardedFetch<any>('/api/services/collect', { method: 'POST' })
    const gone = r.hosts.reduce((n: number, h: any) => n + (h.gone || 0), 0)
    const added = r.hosts.reduce((n: number, h: any) => n + (h.added || 0), 0)
    if (r.candidates === 0) showToast('沒有已納管的主機可收——先到 6.1 納管作業把主機納管', 'warn')
    else if (r.failed.length) showToast(`收了 ${r.candidates} 台：新增 ${added}、消失 ${gone}；${r.failed.length} 台失敗`, 'warn')
    else showToast(`完成：${r.candidates} 台、${r.services} 個服務（新增 ${added}、消失 ${gone}）`, 'success')
    await load()
  } catch (e: any) { showToast(`收集失敗：${errMsg(e)}`, 'error') } finally { collecting.value = false }
}
const host = (r: any) => (r.asset_serial ? `/v2/machine/${encodeURIComponent(r.asset_serial)}` : null)
</script>

<template>
  <V2InventoryFlow kind="service" no="7.4" title="服務盤點" item-label="服務埠" :extra-tabs="EXTRA">
    <template #extra="{ tab }">
      <template v-if="tab === 'list'">
        <p v-if="err" class="err">{{ err }}</p>
        <template v-else>
          <div class="bar">
            <button v-for="c in CARDS" :key="c.k" type="button" class="chip" :class="{ on: card === c.k }" @click="pick(c.k)">
              {{ c.label }} {{ data ? (data.summary[c.k] ?? 0) : '…' }}
            </button>
            <InfoNote>進到已納管主機問「你在跑什麼」收回來的監聽埠與行程。「確定」＝主機自己報的行程名；「推測」＝只拿到埠號，用對照表猜的（收集帳號是唯讀非 root，這是常態）。與網路掃描不一致本身就是資訊：只綁 127.0.0.1 的服務從外面掃不到。點上面的卡片看是哪幾筆，數字＝列數。</InfoNote>
          </div>
          <div class="bar">
            <template v-if="drilled">
              <b>{{ card ? CARDS.find(c => c.k === card)?.label : `埠 ${port}` }}</b>
              <button type="button" class="btn2" @click="clearDrill">清除下鑽</button>
            </template>
            <template v-else>
              <label class="ck"><input v-model="showInfra" type="checkbox"> 含管理服務（SSH、NTP…）</label>
              <label class="ck"><input v-model="showGone" type="checkbox"> 含已消失的</label>
            </template>
            <select v-model="exposure" class="fsel" aria-label="對外">
              <option value="">對外：全部</option>
              <option v-for="(l, k) in EXPOSURE" :key="k" :value="k">{{ l }}</option>
            </select>
            <input v-model="kw" class="fin" type="search" placeholder="IP／主機／行程／服務／埠" aria-label="搜尋服務">
            <span class="muted">{{ rows.length }} 筆（{{ hostCount }} 台）</span>
            <button v-if="!isViewer" type="button" class="btn" :disabled="collecting" @click="collect">{{ collecting ? '收集中…' : '收集服務' }}</button>
          </div>
          <V2Table :rows="rows" prefix="V" :max="200" :empty="busy ? '載入中…' : '沒有符合的服務'"
                   :cols="[{ k: 'ip', label: 'IP', mono: true, link: host }, { k: 'host', label: '主機名稱' }, { k: 'proto', label: '協定' }, { k: 'port', label: '埠', mono: true, link: (r: any) => `/v2/inventory/service?tab=list&port=${r.port}` },
                   { k: 'exp', label: '對外' }, { k: 'svc', label: '服務' }, { k: 'process', label: '行程' }, { k: 'sure', label: '可信度' }, { k: 'state', label: '狀態' }, { k: 'first', label: '首見', mono: true }, { k: 'last', label: '資料時間', mono: true }]" />
        </template>
      </template>
    </template>
  </V2InventoryFlow>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .45; cursor: default; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.fin { font-family: inherit; font-size: 13px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); min-width: 240px; }
.fsel { font-family: inherit; font-size: 13px; padding: 5px 8px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); }
.ck { display: inline-flex; gap: 4px; align-items: center; color: var(--ink-soft); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
</style>
