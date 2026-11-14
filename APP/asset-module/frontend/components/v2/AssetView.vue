<script setup lang="ts">
// 資產明細與操作（V20 收尾，2.059.2；設計 docs/ui-v2/V20資產明細_設計.md）：/v2/asset/{序號} 不帶 edit 時的 V2 原生畫面，不再嵌入舊頁。
//   主資料 GET /api/assets/{序號}（跟舊頁同一支）
//   頁首動作 V2AssetActions（⚠D1／D2：一鍵納管、重新納管、收集全部、收集規格、取消納管、下線、非納管、取消豁免——同一支 API、同一個彈窗；
//   VMware 不出現納管與收集按鈕）；頁籤（?at=）：標籤與分類｜對外入口與關聯｜單據｜收集與檢核。
//   唯讀內容（硬體規格、服務、帳號、弱點、組態檢核與 OS 重建明細、SAN 明細、同業務系統）主機履歷已有，這頁連過去不重做。
//   舊版完整明細（重點欄位、硬體資訊、人員、CIA、體檢分頁）還沒逐欄核對完，留 ?legacy=1 入口。
//   V20 補齊（純顯示，同舊頁的 API）：存在可信度 GET /api/existence/asset/{序號}、資料可信度 GET /api/trust/asset/{序號} 兩個徽章；
//   同 IP 多筆／實測不符小標籤 V2AssetSignals；網卡 MAC 表 V2AssetNics（?at=nic）。
const props = defineProps<{ serial: string }>()
const route = useRoute()
const router = useRouter()
const { apiFetch } = useApi()
const enc = encodeURIComponent
const detail = ref<any>(null)
const err = ref('')
const notFound = ref(false)
async function load() {
  err.value = ''; notFound.value = false
  try { detail.value = await apiFetch<any>(`/api/assets/${enc(props.serial)}`) } catch (e: any) {
    detail.value = null
    if ((e?.statusCode ?? e?.response?.status) === 404) notFound.value = true
    else err.value = `資產載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`
  }
}
// 存在可信度／資料可信度：查無（退役不評分回 404）就不顯示徽章，跟舊頁一樣
const existInfo = ref<any>(null)
const trustInfo = ref<any>(null)
const badgeErr = ref('')   // 1404 審查 P2：讀取失敗（權限、伺服器、網路）要講出來，不當成查無
async function loadBadges() {
  existInfo.value = null; trustInfo.value = null; badgeErr.value = ''
  const s = enc(props.serial)
  const [e, t] = await Promise.allSettled([apiFetch<any>(`/api/existence/asset/${s}`), apiFetch<any>(`/api/trust/asset/${s}`)])
  // 404＝查無／退役：照舊頁不顯示徽章；其他錯誤（401/403/500/網路）顯示「讀不到」
  const bad = (r: PromiseSettledResult<any>, name: string) => {
    if (r.status !== 'rejected') return ''
    const x: any = r.reason
    if (x?.statusCode === 404 || x?.status === 404 || x?.response?.status === 404) return ''
    return `${name}讀不到：${x?.data?.detail ?? x?.message ?? x}`
  }
  if (e.status === 'fulfilled') existInfo.value = e.value
  if (t.status === 'fulfilled') trustInfo.value = t.value
  badgeErr.value = [bad(t, '資料可信度'), bad(e, '存在可信度')].filter(Boolean).join('；')
}
onMounted(() => { load(); loadBadges() })
watch(() => props.serial, () => { load(); loadBadges() })
defineExpose({ load })
const hw = computed<any>(() => detail.value?.hardware ?? {})
const TABS = [{ k: 'tags', label: '標籤與分類' }, { k: 'nic', label: '網卡' }, { k: 'rel', label: '對外入口與關聯' }, { k: 'docs', label: '單據' }, { k: 'jobs', label: '收集與檢核' }]
const tab = computed({ get: () => (TABS.some(t => t.k === route.query.at) ? String(route.query.at) : 'tags'), set: (v: string) => router.replace({ query: { ...route.query, at: v } }) })
const jobsReload = ref(0)
function onCollected(job: string) { if (job === 'rebuild' || job === 'config_audit') jobsReload.value++ }
function afterReload() { load(); jobsReload.value++ }
const revoked = computed(() => String(hw.value.collect_error || '').includes('取消納管'))
const status = computed(() => hw.value.asset_status ?? (/^(DYN-|VC-|AUTO-|ADOPT-)/.test(String(hw.value.asset_serial ?? '')) ? 'CIA 未登記' : '未知'))
const osText = computed(() => detail.value?.os_view?.effective || hw.value.os || '')
</script>

<template>
  <V2Page v-model="tab" :back="{ to: `/v2/machine/${encodeURIComponent(serial)}`, label: '主機履歷' }" :tabs="detail ? TABS : []" width="wide">
    <template #title><h1 class="h1">{{ hw.hostname || '資產明細與操作' }}　<span class="mono">{{ serial }}</span></h1></template>
    <template v-if="detail" #conclusion>
      <span class="pill">{{ status }}</span>
      <span v-if="hw.pending_status && hw.pending_status !== hw.asset_status" class="pill warn">待 CIA 異動：{{ hw.pending_status }}<InfoNote>{{ hw.pending_note || '' }}（{{ hw.pending_by || '' }} {{ hw.pending_at || '' }}）——不改台數，CIA 改好重匯才生效。</InfoNote></span>
      <span v-if="hw.collect_ok === 1" class="pill good" :title="`最後確認：${hw.collect_checked_at || '—'}`">已納管</span>
      <span v-else-if="revoked" class="pill warn" :title="hw.collect_error">已取消納管</span>
      <span v-if="hw.root_key_at" class="pill warn">root 金鑰（暫時）<InfoNote>這台沒有可用的 sudo（或是 AIX），需要 root 的收集項目用專用的 root 收集金鑰收：只限收集器位址、不可開終端機、不可轉發，跑的是跟 sudo 時同一批固定指令（決策 ROOTKEY1，暫時例外）；取消納管會一併移除。確認可用：{{ hw.root_key_at }}</InfoNote></span>
      <span v-if="badgeErr" class="pill badge-err" data-test="badge-err">{{ badgeErr }}</span><NuxtLink v-if="trustInfo" to="/v2/datafix?tab=quality" class="pill badge" :class="trustTone(trustInfo.score)">資料可信度 {{ trustInfo.score }}</NuxtLink><InfoNote v-if="trustInfo"><template v-for="(l, i) in trustLines(trustInfo)" :key="i"><br v-if="i">{{ l }}</template></InfoNote>
      <NuxtLink v-if="existInfo" :to="{ path: '/v2/reconcile', query: { tab: 'existence', bucket: existBucket(existInfo) } }" class="pill badge" :class="existTone(existInfo)">存在可信度 {{ existInfo.score }}</NuxtLink><InfoNote v-if="existInfo"><template v-for="(l, i) in existLines(existInfo)" :key="i"><br v-if="i">{{ l }}</template></InfoNote>
      <span class="mono muted">{{ hw.ip || '無 IP' }}</span><span v-if="osText" class="muted">{{ osText }}</span>
    </template>
    <V2LegacyLinks :links="[{ label: '舊版完整明細（重點欄位、硬體資訊、人員、CIA、體檢）', to: `/assets/${encodeURIComponent(serial)}?legacy=1` }]" />
    <div v-if="notFound" class="nf"><b>查無資產序號「{{ serial }}」</b><span class="muted">可能是序號打錯，或這台還沒登記。</span><NuxtLink to="/v2/hosts">回 2.1 主機查詢</NuxtLink><NuxtLink to="/v2/onboard">看未登記主機</NuxtLink></div>
    <p v-else-if="err" class="err">{{ err }}</p>
    <p v-else-if="!detail" class="muted">載入中…</p>
    <V2AssetSignals v-if="detail" :detail="detail" :serial="serial" />
    <V2AssetActions v-if="detail":key="serial" :detail="detail" :serial="serial" @reload="afterReload" @collected="onCollected" />
    <template #tab>
      <template v-if="detail">
        <V2AssetTagsOs v-if="tab === 'tags'" :key="'t' + serial" :detail="detail" :serial="serial" @reload="load" />
        <V2AssetNics v-else-if="tab === 'nic'" :key="'n' + serial" :detail="detail" />
        <V2AssetRelations v-else-if="tab === 'rel'" :key="'r' + serial" :detail="detail" :serial="serial" />
        <V2AssetDocsJobs v-else-if="tab === 'docs'" :key="'d' + serial" mode="docs" :detail="detail" :serial="serial" />
        <V2AssetDocsJobs v-else :key="'j' + serial" mode="jobs" :detail="detail" :serial="serial" :reload-key="jobsReload" />
      </template>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.mono { font-family: var(--disp); }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.pill { font-size: 12px; padding: 2px 10px; border-radius: 10px; background: var(--sub); color: var(--ink-soft); display: inline-flex; gap: 4px; align-items: center; margin-right: 6px; }
.pill.good { background: var(--mint); color: var(--brand-dark); }
.pill.warn { background: var(--warn-soft); color: var(--warn-text); }
.pill.bad { background: var(--bad-soft); color: var(--bad); }
.pill.badge { text-decoration: none; font-weight: 700; margin-right: 0; }
.nf { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.badge-err { color: var(--bad); background: var(--bad-soft); }
</style>
