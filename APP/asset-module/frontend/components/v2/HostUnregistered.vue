<script setup lang="ts">
// 2.1 主機查詢「掃到未登記」頁籤（2.057.0，V61 ⑥；原舊 /assets「網路上發現 N 台尚未登記的主機」）。
// 2.058.4（批 23，V17 舊 /adopt「未登記主機」頁的動作搬過來，⚠D1 D4 純搬移）：
//   · 每列「一鍵納管」＝共用 OnboardModal（每台 /api/onboard，ESXi／vCenter 由後端擋）、「手動登記」＝V2AdoptRegister（POST /api/assets/adopt）；
//   · 勾選「我有密碼：網頁一鍵納管」＝共用 BatchOnboardModal；送出去的＝目前畫面上看得到而且勾了的（搜尋藏起來的不送，比舊頁收緊）；
//   · 「上次收集」欄＝最近一次開始收集的結果（同一支 GET /api/collect/dispatch/latest，只讀）。
//   按鈕可見度照舊頁（不分帳號等級，後端擋）；例外：取得納管指令只給管理員（2.058.8 收緊）。載入只讀。
import { STATUS_LABEL, filterUnreg, pruneIps, visibleIps } from '~/utils/adoptCollect'

const { apiFetch } = useApi()
// 取得納管指令只給管理員（審查 P2，2.058.8 收緊）：後端 /api/onboard/paste/issue 是 ADMIN_WRITE，一般帳號不該被邀請去準備 root 指令
const { isAdmin } = useAuth()
const rows = ref<Record<string, any>[] | null>(null)
const err = ref('')
const lastByIp = ref<Record<string, any>>({})
const lastErr = ref('')
async function load() {
  try { rows.value = await apiFetch<Record<string, any>[]>('/api/scan/unregistered'); err.value = '' }
  catch (e: any) { err.value = `掃到未登記的主機讀取失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }   // 舊頁讀不到時當成 0 台；V2 寫出原因
}
async function loadLast() {
  try {
    const d = await apiFetch<any>('/api/collect/dispatch/latest')
    lastByIp.value = Object.fromEntries((d?.results ?? []).map((r: any) => [r.ip, r]))
    lastErr.value = ''
  } catch (e: any) {   // 讀不到不是「沒掃到」：另外寫出原因（審查 P2），欄位留空
    lastByIp.value = {}
    lastErr.value = `上次收集結果讀取失敗（「上次收集」欄暫時空白，不代表沒掃到）：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`
  }
}
onMounted(() => { load(); loadLast() })

const uQ = ref('')
const guess = (u: any) => [u.is_vm ? 'VM' : '', u.mac_vendor, u.os_guess || '線索不足'].filter(Boolean).join(' · ') + (u.open_ports ? ` · 埠 ${u.open_ports}` : '')
const shown = computed<any[]>(() => filterUnreg(rows.value ?? [], uQ.value).map(u => {
  const l = lastByIp.value[u.ip]
  return { ...u, name_text: u.hostname || '', guess_text: guess(u), last_text: l ? (STATUS_LABEL[l.status] || l.status) : '', last_msg: l?.message || '' }
}))

// ===== 勾選與批次 =====
const sel = ref<Record<string, boolean>>({})
watch(shown, (s) => { sel.value = pruneIps(sel.value, s) })
const toSend = computed(() => visibleIps(sel.value, shown.value))
const batchIps = ref<string[] | null>(null)
// 「我沒有密碼：取得納管指令」（2.058.7，舊頁同一顆）：共用 PasteOnboardModal——只發指令與下載腳本，這頁不碰密碼
const pasteIps = ref<string[] | null>(null)
function dropIps(ips: string[]) {
  const ok = new Set(ips)
  if (rows.value) rows.value = rows.value.filter(h => !ok.has(h.ip))
}
function onBatchDone(okIps: string[]) { dropIps(okIps) }

// ===== 每列 =====
const onboardHost = ref<any>(null)
function onOnboarded() { if (onboardHost.value) dropIps([onboardHost.value.ip]); onboardHost.value = null }
const adoptHost = ref<any>(null)
function onAdopted(ip: string) { dropIps([ip]); adoptHost.value = null }
</script>

<template>
  <section>
    <V2AdoptRegister v-if="adoptHost" :host="adoptHost" @done="onAdopted" @close="adoptHost = null" />
    <template v-else>
      <div class="bar">
        <b>掃到未登記</b>
        <span v-if="rows" class="muted">共 {{ rows.length }} 台</span>
        <input v-model="uQ" class="fin" type="search" placeholder="搜尋 IP、主機名稱、網段…" aria-label="搜尋未登記主機">
        <span class="muted">已勾 {{ toSend.length }} 台</span>
        <button type="button" class="btn2" :disabled="!toSend.length" data-test="unreg-batch-onboard" @click="batchIps = toSend">我有密碼：網頁一鍵納管</button>
        <button v-if="isAdmin" type="button" class="btn2" :disabled="!toSend.length" data-test="unreg-paste" @click="pasteIps = toSend">我沒有密碼：取得納管指令</button>
        <InfoNote>掃描看得到、還沒登記成資產的主機；「可能是」只是網路探測的線索。一鍵納管會建收集帳號（VMware 設備一律擋下）；手動登記只登記、不建帳號。批次只送畫面上看得到的列。上次收集空白＝最近一輪沒掃到這台。</InfoNote>
      </div>
      <!-- 審查 P1（2.058.8）：lastErr 不可以夾在 v-if／v-else 鏈中間，不然「上次收集」讀不到時整張表不見 -->
      <p v-if="lastErr" class="err" data-test="unreg-last-err">{{ lastErr }}</p>
      <p v-if="err" class="err" role="alert">{{ err }}</p>
      <p v-else-if="!rows" class="muted">讀取中…</p>
      <V2Table v-else v-model:selected="sel" :rows="shown" prefix="U" unit="台" row-key="ip" selectable header-clears-all :max="100" empty="目前沒有掃到未登記的主機"
               :cols="[{ k: 'ip', label: '主機', mono: true }, { k: 'guess_text', label: '可能是' }, { k: 'segment', label: '網段', mono: true }, { k: 'last_text', label: '上次收集' }, { k: 'go', label: '操作', text: () => '' }]"
               :export-cols="[{ k: 'name_text', label: '主機名稱' }, { k: 'mac', label: 'MAC' }, { k: 'last_msg', label: '原因' }]">
        <template #cell-ip="{ row }"><b>{{ row.ip }}</b><div class="sm">{{ row.name_text || '沒有名稱' }}</div></template>
        <template #cell-last_text="{ row }">{{ row.last_text || '—' }}<div v-if="row.last_msg" class="sm" :title="row.last_msg">{{ String(row.last_msg).slice(0, 40) }}{{ String(row.last_msg).length > 40 ? '…' : '' }}</div></template>
        <template #cell-go="{ row }">
          <span class="ops">
            <button type="button" class="lb" @click="onboardHost = row">一鍵納管</button>
            <button type="button" class="lb" @click="adoptHost = row">手動登記</button>
          </span>
        </template>
      </V2Table>
    </template>
    <OnboardModal v-if="onboardHost" :ip="onboardHost.ip" :os-guess="onboardHost.os_guess" @done="onOnboarded" @close="onboardHost = null" />
    <BatchOnboardModal v-if="batchIps" :ips="batchIps" @done="onBatchDone" @close="batchIps = null" />
    <PasteOnboardModal v-if="pasteIps" :ips="pasteIps" @close="pasteIps = null" />
  </section>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; font-size: 13px; }
.fin { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); width: 260px; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.btn2:disabled { opacity: .5; cursor: default; }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 12px; padding: 0 8px 0 0; }
.ops { white-space: nowrap; }
.sm { color: var(--ink-aux); font-size: 12px; font-family: inherit; }
.muted { color: var(--muted); }
.err { color: var(--bad); }
</style>
