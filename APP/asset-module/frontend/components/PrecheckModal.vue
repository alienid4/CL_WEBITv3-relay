<script setup lang="ts">
// 預檢（只看不動，v1.468.9）：登進去問這台是什麼、能不能納管——不改這台任何東西。
// 結論由一鍵納管自己的判斷產生（onboard_engine.onboard_decision，2026-10-20）：這裡說什麼，一鍵納管就做什麼。
// ⚠️ 密碼只在這個視窗的記憶體，送出後立刻清掉，不進 DB／log。
const props = defineProps<{ ips: string[] }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const { apiFetch } = useApi()
const { showToast } = useToast()

const cred = reactive({ username: 'sysinfra', password: '' })
const busy = ref(false)
const out = ref<any>(null)
interface Row { no: number; ip: string; verdict: string; reason: string; pretty_name: string | null
  uname: string | null; uid: number | null; is_root: boolean; has_sudo: boolean; appliance: string[] }
const rows = ref<Row[]>([])
const { sortKey, sortDir, toggle, sorted } = useSort(rows, 'no')
const VCLS: Record<string, string> = { 可以納管: 'ok', '需改用 root': 'warn', 不會納管: 'bad', 登不進去: 'warn', '未執行（已停止）': 'mid' }

async function run() {
  if (!cred.password) { showToast('請輸入登入密碼', 'warn'); return }
  const password = cred.password
  cred.password = ''
  busy.value = true
  try {
    out.value = await apiFetch<any>('/api/onboard/precheck', {
      method: 'POST', timeout: 5 * 60 * 1000,
      body: { ips: props.ips, username: cred.username, password },
    })
    rows.value = (out.value.results || []).map((r: any, i: number) => ({ no: i + 1, ...r }))
    if (out.value.auto_stopped) showToast(`已自動停止：${out.value.auth_rejected} 台帳密被拒，其餘沒試（避免帳號被鎖）`, 'error', 10000)
  } catch (e: any) {
    showToast(`預檢失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error')
  } finally {
    busy.value = false
  }
}
onBeforeUnmount(() => { cred.password = '' })
</script>

<template>
  <div class="modal-mask">
    <div class="modal">
      <div class="mhead">🔍 預檢（只看不動）— {{ ips.length }} 台
        <InfoNote>用你輸入的帳密<b>唯讀登入</b>一次，問這台：作業系統（os-release 的 PRETTY_NAME）、uname、是不是 root／有沒有 sudo、有沒有 VMware 或設備標記，然後用<b>一鍵納管自己的判斷</b>（同一支程式、同樣順序）預告一鍵納管會怎麼做：會納管（Linux／AIX／Windows 各走哪條）、需改用 root、或不會納管與原因。<br><br><b>不建帳號、不佈金鑰、不改 sudoers、不跑任何納管腳本</b>。密碼只用這一次、不儲存。<br><br>還沒有任何一台登入成功就出現「登入被拒」會自動停下，沒試的寫「未執行（已停止）」。</InfoNote>
      </div>
      <div v-if="!out" class="form">
        <label class="mf">登入帳號<input v-model="cred.username" autocomplete="off" /></label>
        <label class="mf">登入密碼<input v-model="cred.password" type="password" autocomplete="new-password" @keyup.enter="run" /></label>
      </div>
      <div v-if="out" class="summary">
        <span v-for="(n, k) in out.counts" :key="k">{{ k }} <b :class="VCLS[k as string]">{{ n }}</b></span>
      </div>
      <div v-if="rows.length" class="tbl-wrap">
        <table>
          <thead><tr>
            <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">#</SortTh>
            <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
            <SortTh k="verdict" :active="sortKey" :dir="sortDir" @sort="toggle">結論</SortTh>
            <SortTh k="pretty_name" :active="sortKey" :dir="sortDir" @sort="toggle">作業系統</SortTh>
            <SortTh k="uname" :active="sortKey" :dir="sortDir" @sort="toggle">uname</SortTh>
            <SortTh k="uid" :active="sortKey" :dir="sortDir" @sort="toggle">root／sudo</SortTh>
            <SortTh k="reason" :active="sortKey" :dir="sortDir" @sort="toggle">原因</SortTh>
          </tr></thead>
          <tbody>
            <tr v-for="r in sorted" :key="r.no">
              <td class="small">{{ r.no }}</td>
              <td class="mono">{{ r.ip }}</td>
              <td><b :class="VCLS[r.verdict]">{{ r.verdict }}</b></td>
              <td>{{ r.pretty_name || '—' }}<span v-if="r.appliance?.length" class="bad small">（設備標記：{{ r.appliance.join('、') }}）</span></td>
              <td class="mono small">{{ r.uname || '—' }}</td>
              <td class="small">{{ r.uid === null ? '—' : (r.is_root ? 'root' : (r.has_sudo ? '有 sudo' : '沒有 sudo')) }}</td>
              <td class="msg">{{ r.reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="macts">
        <button v-if="!out" class="btn primary" :disabled="busy" @click="run">{{ busy ? '預檢中…' : `預檢 ${ips.length} 台` }}</button>
        <button v-else class="btn ghost" :disabled="busy" @click="out = null; rows = []">換帳密再預檢</button>
        <button class="btn ghost" :disabled="busy" @click="cred.password = ''; emit('close')">{{ out ? '關閉' : '取消' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex; align-items: center; justify-content: center; z-index: 60; }
.modal { width: 900px; max-width: 96vw; max-height: 90vh; overflow: auto; background: var(--card);
  border: 1px solid var(--border-strong); border-radius: 12px; padding: 22px 24px; }
.mhead { font-size: 15px; font-weight: 700; margin-bottom: 12px; color: var(--brand-dark); }
.form { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.mf { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.mf input { font-family: inherit; font-size: 13px; padding: 8px 10px; border: 1px solid var(--border-strong);
  background: var(--card); color: var(--ink); border-radius: 6px; }
.summary { display: flex; gap: 16px; font-size: 12.5px; color: var(--muted); margin: 6px 0 8px; }
.tbl-wrap { overflow: auto; max-height: 420px; border: 1px solid var(--border); }
table { border-collapse: collapse; width: 100%; font-size: 12px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { background: var(--mint); color: var(--ink-soft); position: sticky; top: 0; }
.mono { font-family: ui-monospace, Consolas, monospace; }
.small { font-size: 11px; color: var(--muted); }
.msg { font-size: 11.5px; color: var(--ink-soft); }
.ok { color: var(--brand-dark); } .bad { color: var(--bad); } .warn { color: var(--warn-text); } .mid { color: var(--ink-aux); }
.macts { display: flex; gap: 10px; margin-top: 12px; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 9px 20px; border: none; border-radius: 6px; cursor: pointer; }
.btn.primary { background: var(--brand); color: #fff; }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
</style>
