<script setup lang="ts">
// 2.1 主機頁「納管狀態」（2.069.3）：兩行——管理關係（已納管／未納管／不納管）與最近收集結果（正常／過期／失敗／沒收到），再給動作。
// 判斷全在 utils/hostOnboard.planHost（純函式、fail-closed：任何資料讀不到就「納管狀態查不到」、動作全停用）；這裡只負責讀資料與畫面。
// 沿用 6.1：納管視窗＝OnboardModal（SSH／AIX）／WinOnboardModal（Windows）（帳密只在視窗裡、用完即清）、
// 上次失敗＝POST /api/onboard/audit/latest-by-ip、Windows 狀態＝GET /api/onboard/windows/state、收集全部＝POST /api/onboard/post-collect-batch。
// 另用 GET /api/onboard/target-check（唯讀）問伺服器這個 IP 能不能納管（VMware 含反解、退役；判不出來＝不給）。
// 權限同 6.1：納管、收集全部限管理員（後端另外擋）；盤中靜默由後端 quiet_deps 擋、視窗照舊處理。
import { latestFailure, planHost, preBlock, recollectConfirm, recollectOutcome } from '~/utils/hostOnboard'
import type { Load } from '~/utils/hostOnboard'
const props = defineProps<{
  ips: string[]; serials: string[]; hostname?: string | null; os?: string | null; measuredOs?: string | null
  onboarded?: boolean | null; collect?: 'fresh' | 'stale' | null
  vmware?: boolean | null; osClass?: string | null; badgeCode?: string | null; retired?: boolean | null; assetStatus?: string | null; lpar?: boolean
}>()
const emit = defineEmits<{ (e: 'done'): void }>()
const { apiFetch } = useApi()
const { isAdmin } = useAuth()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const attempts = ref<Load>({ st: 'loading' })
const win = ref<Load>({ st: 'loading' })
const check = ref<Load>({ st: 'loading' })
const pickedIp = ref('')
const cleanIps = computed(() => [...new Set((props.ips ?? []).map(x => String(x || '').trim()).filter(Boolean))])
const pre = computed(() => preBlock({ vmware: props.vmware, osClass: props.osClass, badgeCode: props.badgeCode, retired: props.retired, assetStatus: props.assetStatus }))

async function one(target: { value: Load }, path: string, opts: any, pick: (r: any) => any) {
  try { target.value = { st: 'ok', data: pick(await apiFetch(path, opts)) } }
  catch (e: any) { target.value = { st: 'error', err: `${path} 讀取失敗：${errMsg(e)}` } }
}
async function loadAll() {
  attempts.value = { st: 'loading' }; win.value = { st: 'loading' }; check.value = { st: 'loading' }
  if (pre.value) return                                   // 天條擋下的不去讀納管資料，planHost 一樣回 blocked
  if (!cleanIps.value.length) { attempts.value = { st: 'ok', data: {} }; win.value = { st: 'ok', data: {} }; check.value = { st: 'ok', data: {} }; return }   // 沒有 IP：planHost 會寫「這台沒有 IP」
  const ips = cleanIps.value
  await Promise.all([
    one(attempts, '/api/onboard/audit/latest-by-ip', { method: 'POST', body: { ips } }, r => r ?? {}),
    one(win, '/api/onboard/windows/state', { query: { ips: ips.join(',') } }, r => r?.items ?? {}),
    one(check, '/api/onboard/target-check', { query: { ips: ips.join(',') } }, r => r?.items ?? {}),
  ])
  // 多個 IP：失敗過的那個預選起來（下拉會顯示它）；沒失敗過就留空，要使用者自己選
  if (ips.length > 1 && !pickedIp.value) pickedIp.value = latestFailure(ips, attempts.value.data)?.ip ?? ''
}
onMounted(loadAll)
watch(() => [cleanIps.value.join(','), props.onboarded, props.collect, pre.value], loadAll)

const plan = computed(() => planHost({
  vmware: props.vmware, osClass: props.osClass, badgeCode: props.badgeCode, retired: props.retired, assetStatus: props.assetStatus, lpar: props.lpar,
  onboarded: props.onboarded, collect: props.collect, ips: cleanIps.value, serials: props.serials ?? [], hostname: props.hostname, os: props.os, measuredOs: props.measuredOs,
  isAdmin: isAdmin.value, attempts: attempts.value, win: win.value, check: check.value, pickedIp: pickedIp.value,
}))

const sshIp = ref<string | null>(null)
const winRows = ref<any[] | null>(null)
const targetSerial = computed(() => check.value.data?.[plan.value.targetIp]?.serial ?? null)
function openOnboard() {
  const p = plan.value
  if (!p.targetIp || !p.platform) { showToast('這台現在不能納管', 'warn'); return }
  if (p.platform === 'windows') winRows.value = [{ ip: p.targetIp, hostname: props.hostname ?? null, opted_in: p.optedIn }]   // 真實的 Windows 納管狀態（未納管＝false，視窗寫「納管」）
  else sshIp.value = p.targetIp
}
function onDone() { sshIp.value = null; winRows.value = null; loadAll(); emit('done') }

const busy = ref(false)
async function recollect() {
  const p = plan.value
  if (!p.canRecollect || !p.recollectSerial) return
  const ip = p.targetIp || pickedIp.value || cleanIps.value[0] || ''
  if (!window.confirm(recollectConfirm(props.hostname ?? '', ip, p.recollectSerial))) return
  busy.value = true
  let keepLock = false
  try {
    const r = await apiFetch<any>('/api/onboard/post-collect-batch', { method: 'POST', body: { asset_serials: [p.recollectSerial] } })
    const o = recollectOutcome(r)
    showToast(o.msg, o.ok ? 'success' : 'error')
    if (o.ok) keepLock = true
    else emit('done')            // 沒排進去：重讀主機資料，不讓畫面停在舊狀態
  } catch (e: any) { showToast(`重新收集沒有送出：${errMsg(e)}`, 'error') }
  finally {
    if (keepLock) setTimeout(() => { busy.value = false }, 5000)   // 排入後保持停用數秒，避免重複點擊
    else busy.value = false
  }
}
const tone = computed(() => ({ blocked: 'warn', unknown: 'bad', loading: 'muted', onboarded: 'ok', stale: 'ok', failed: 'warn', not_onboarded: 'warn' } as Record<string, string>)[plan.value.phase])
const collectTone = computed(() => ({ failed: 'bad', stale: 'warn', onboarded: 'ok' } as Record<string, string>)[plan.value.phase] ?? 'muted')
</script>

<template>
  <span class="hoa" data-test="host-onboard">
    <span class="row1"><span :class="tone" data-test="hoa-state">{{ plan.mgmt }}</span>
      <span v-if="plan.reason" class="sub" :class="{ bad: plan.phase === 'unknown' }" data-test="hoa-why">{{ plan.reason }}</span>
      <select v-if="isAdmin && cleanIps.length > 1 && plan.phase !== 'blocked'" v-model="pickedIp" class="sel" aria-label="要處理的 IP" data-test="hoa-ip">
        <option value="">— 請選 IP —</option>
        <option v-for="ip in cleanIps" :key="ip" :value="ip">{{ ip }}</option>
      </select>
      <span v-if="plan.platform && (plan.canOnboard || plan.canRetry || plan.canReonboard)" class="sub" data-test="hoa-target">{{ plan.targetIp }}・{{ plan.platform === 'windows' ? 'Windows（WinRM）' : 'SSH' }}</span>
      <button v-if="plan.canOnboard" type="button" class="btn2" data-test="hoa-onboard" @click="openOnboard">納管</button>
      <button v-if="plan.canRetry" type="button" class="btn2" data-test="hoa-retry" @click="openOnboard">重新納管</button>
      <button v-if="plan.canRecollect" type="button" class="btn2" data-test="hoa-recollect" :disabled="busy" @click="recollect">{{ busy ? '排入中…' : '收集全部' }}</button>
      <button v-if="plan.canReload" type="button" class="btn2" data-test="hoa-reload" @click="loadAll">重新讀取</button>
      <InfoNote v-if="plan.canOnboard || plan.canRetry || plan.canRecollect">納管：開 6.1 同一個納管視窗與同一支納管 API，帳密只在視窗裡用一次、不存；Windows 走 WinRM 那套（不要密碼）。收集全部：對這台收一輪六項（硬體規格／服務／軟體／帳號／OS 重建／組態檢核），唯讀、背景排隊，跟 6.1「已納管」的收集全部同一支。盤中靜默時段由伺服器擋下。</InfoNote>
    </span>
    <span v-if="plan.collectText" class="row2" :class="collectTone" data-test="hoa-collect">最近收集：{{ plan.collectText }}</span>
    <span v-if="plan.recollectNote && plan.canRecollect" class="row2 sub0" data-test="hoa-note">{{ plan.recollectNote }}</span>
    <span v-if="plan.canReonboard" class="row2" data-test="hoa-stale">
      已納管但最近連不上，先「收集全部」看是否恢復；仍不行再
      <button type="button" class="lnkbtn" data-test="hoa-reonboard" @click="openOnboard">重新納管</button>
      <InfoNote>重新納管會再用你輸入的帳密登入這台，重新建立唯讀收集帳號與收集公鑰、修回被動過的帳號或金鑰（Windows 則重新驗證 WinRM 憑證）。這不是第一次納管，只在連線一直恢復不了時使用。</InfoNote>
    </span>
    <OnboardModal v-if="sshIp" :ip="sshIp" :os-guess="measuredOs || os" :hostname="hostname" :serial="targetSerial" :basis="plan.platformBasis" @done="onDone" @close="sshIp = null" />
    <WinOnboardModal v-if="winRows" :rows="winRows" @done="onDone" @close="winRows = null" />
  </span>
</template>

<style scoped>
.hoa { display: inline-flex; flex-direction: column; gap: 4px; }
.row1 { display: inline-flex; align-items: center; flex-wrap: wrap; gap: 4px; }
.row2 { font-size: 13px; color: var(--ink-aux); }
.sub { margin-left: 8px; font-size: 13px; color: var(--ink-aux); }
.sub0 { color: var(--ink-aux); }
.ok { color: var(--st-online); }
.warn { color: var(--st-stale); }
.bad { color: var(--bad); }
.muted { color: var(--ink-aux); }
.row2.ok { color: var(--st-online); } .row2.warn { color: var(--st-stale); } .row2.bad { color: var(--bad); }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 3px 12px; font-size: 13px; font-family: inherit; cursor: pointer; margin-left: 8px; }
.btn2:disabled { opacity: .6; cursor: default; }
.lnkbtn { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 13px; padding: 0; }
.sel { margin-left: 8px; font-size: 13px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); color: var(--ink); padding: 2px 6px; }
</style>
