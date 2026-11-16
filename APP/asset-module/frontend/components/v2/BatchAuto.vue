<script setup lang="ts">
// 6.1 納管作業「一鍵批次自動納管（測試環境）」（V22，2.048.14 起 V2 原生；取代嵌入的舊頁 pages/batch-onboard.vue）。同一批 API，行為不變（⚠D1）：
// POST /api/onboard/batch-auto/preview、POST /api/onboard/batch-auto（管理員；後端 ADMIN_WRITE）、GET /api/onboard/batch-auto/status（每 2 秒輪詢）、
// GET /api/onboard/batch-auto/export（管理員匯出）。設計定案見 AI/計畫_一鍵批次自動納管.md。
// 安全重點照舊：只跑環境別＝測試；正式／備援擋掉；未登記要人逐台勾；資產庫登記不是伺服器 Linux 的（設備、iDRAC、RHCOS）排除；
// 統一密碼預設不勾、勾了要打 YES；密碼用完即清，不進網址、不存瀏覽器。中斷後不自動接著跑（避免像暴力破解），要重新輸入密碼再按。
// 入口只在 6.1 管理員畫面（父頁 isAdmin 才顯示按鈕）。
const { apiFetch } = useApi()
const { showToast } = useToast()
const runtimeConfig = useRuntimeConfig()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }

const form = reactive({ ipsText: '', username: 'root', passwordA: '', passwordB: '', unify: false, confirm: '' })
const parseIps = () => form.ipsText.split(/[\s,;]+/).map(s => s.trim()).filter(Boolean)

// ---- 預覽 ----
const preview = ref<any>(null)
const includeUnknown = ref<Record<string, boolean>>({})
const previewing = ref(false)
async function doPreview() {
  const ips = parseIps()
  if (!ips.length) { showToast('先貼上要納管的機器 IP', 'warn'); return }
  previewing.value = true
  try {
    preview.value = await apiFetch<any>('/api/onboard/batch-auto/preview', { method: 'POST', body: { ips, username: form.username, passwords: [form.passwordA] } })
    includeUnknown.value = {}
  } catch (e: any) { showToast(`預覽失敗：${errMsg(e)}`, 'error') } finally { previewing.value = false }
}
const BUCKETS = [
  { k: 'run', label: '會自動納管（Linux 測試機）', tone: 'ok' }, { k: 'windows', label: 'Windows（要另走 WinRM）' },
  { k: 'blocked_production', label: '擋下（正式／備援，不碰）', tone: 'warn' }, { k: 'unknown', label: '未登記，要你確認' },
  { k: 'other', label: '其他（沒開 SSH）' }, { k: 'excluded', label: '另有納管方式（設備）', tone: 'warn' }]
const pv = ref('')                                                     // 點卡片看那一桶是哪幾台
const pvRows = computed(() => ((preview.value?.[pv.value] ?? []) as any[]).map((b: any, i: number) => ({ ...b, __no: i + 1 })))
const unkRows = computed(() => ((preview.value?.unknown ?? []) as any[]).map((b: any, i: number) => ({ ...b, __no: i + 1 })))
const pickedUnknown = computed(() => Object.keys(includeUnknown.value).filter(k => includeUnknown.value[k]))

// ---- 執行＋輪詢進度 ----
const status = ref<any>(null)
const statusErr = ref('')
const starting = ref(false)
let poller: ReturnType<typeof setInterval> | null = null
async function pollStatus() {
  try {
    status.value = await apiFetch<any>('/api/onboard/batch-auto/status'); statusErr.value = ''
    if (status.value && !status.value.running && poller) { clearInterval(poller); poller = null }
  } catch (e: any) { statusErr.value = `進度讀取失敗（會繼續重試）：${errMsg(e)}` }      // 輪詢失敗不中斷，但要看得到
}
onBeforeUnmount(() => { if (poller) clearInterval(poller) })
// 隔天回來也看得到上次那批；收起再展開時若那批還在跑，接著輪詢（不然進度條停住）
onMounted(async () => { await pollStatus(); if (status.value?.running && !poller) poller = setInterval(pollStatus, 2000) })
async function start(overrideIps?: string[]) {
  const ips = overrideIps ?? parseIps()
  const passwords = [form.passwordA, form.passwordB].filter(Boolean)
  if (!passwords.length) { showToast('至少輸入一組密碼', 'warn'); return }
  if (form.unify && form.confirm !== 'YES') { showToast('勾了統一密碼，要在確認欄輸入大寫 YES', 'warn'); return }
  starting.value = true
  try {
    await apiFetch('/api/onboard/batch-auto', { method: 'POST', body: {
      ips, username: form.username, passwords, unify_password: form.unify, confirm: form.confirm,
      // 繼續剩下的：目標已明確，直接當「勾選納入」送，不再經測試／未知分桶
      include_ips: overrideIps ? overrideIps : pickedUnknown.value } })
    form.passwordA = ''; form.passwordB = ''; form.confirm = ''        // 密碼用完即清
    showToast('已開始，下方會即時顯示進度', 'success')
    if (poller) clearInterval(poller)
    poller = setInterval(pollStatus, 2000)
    pollStatus()
  } catch (e: any) { showToast(`啟動失敗：${errMsg(e)}`, 'error') } finally { starting.value = false }
}
function resume() {
  const rem = (status.value?.remaining ?? []).map((t: any) => t.ip)
  if (!rem.length) { showToast('沒有待繼續的目標', 'warn'); return }
  start(rem)
}
const pwLabel = (idx: number | null, ok: number) => (idx === 0 ? '密碼 A' : idx === 1 ? '密碼 B' : ok ? '—' : '都不對')
const RES = [{ k: '', label: '全部' }, { k: 'onboarded', label: '已納管' }, { k: 'unified', label: '改成 A' }, { k: 'failed', label: '失敗（要處理）' }]
const rf = ref('')
const allRes = computed(() => ((status.value?.results ?? []) as any[]).map((r: any, i: number) => ({ ...r, __no: i + 1,
  login_t: r.login_ok ? '成功' : '失敗', pw_t: pwLabel(r.password_index, r.login_ok), ob_t: r.onboarded ? '是' : '—', un_t: r.password_unified ? '是' : r.login_ok ? '' : '—' })))
const cnt = computed(() => ({ onboarded: allRes.value.filter((r: any) => r.onboarded).length, unified: allRes.value.filter((r: any) => r.password_unified).length, failed: allRes.value.filter((r: any) => !r.onboarded).length }))
const resRows = computed(() => allRes.value.filter((r: any) => (rf.value === 'onboarded' ? r.onboarded : rf.value === 'unified' ? r.password_unified : rf.value === 'failed' ? !r.onboarded : true)))

async function doExport() {
  try {
    const res = await fetch(`${runtimeConfig.public.apiBase}/api/onboard/batch-auto/export`, { credentials: 'include' })
    if (!res.ok) throw new Error(await httpReason(res))
    const blob = await res.blob()
    const m = (res.headers.get('Content-Disposition') || '').match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = m ? decodeURIComponent(m[1]) : 'batch_onboard.xlsx'; a.click(); URL.revokeObjectURL(url)
  } catch (e: any) { showToast(`匯出失敗：${e?.message ?? '請稍後再試'}`, 'error') }
}
</script>

<template>
  <div class="ba">
    <div class="bar"><b>一鍵批次自動納管</b><span class="muted">一批測試機：試密碼 → 佈金鑰（納管）→（可選）把密碼統一成 A</span>
      <InfoNote>只跑環境別＝測試的機器；正式／備援會被擋、未登記的要你逐台確認。密碼用完即清，不留在畫面也不進系統任何地方。</InfoNote></div>
    <label class="f">要納管的機器 IP 或整個網段（一行一個，或用空白／逗號分隔）
      <textarea v-model="form.ipsText" class="in" rows="4" placeholder="10.99.1.11&#10;10.99.1.12&#10;10.99.1.0/24（整段：只會納管掃描掃到活著的）" /></label>
    <div class="bar">
      <label class="f">登入帳號<input v-model="form.username" class="in" autocomplete="off" placeholder="root"></label>
      <label class="f">密碼 A（現行）<input v-model="form.passwordA" class="in" type="password" autocomplete="new-password"></label>
      <label class="f">密碼 B（舊的，沒有留空）<input v-model="form.passwordB" class="in" type="password" autocomplete="new-password"></label>
      <button type="button" class="btn2" :disabled="previewing" @click="doPreview">{{ previewing ? '檢查中…' : '先預覽範圍' }}</button>
    </div>

    <template v-if="preview">
      <h3 class="h3">這批的分類</h3>
      <div class="cards">
        <button v-for="b in BUCKETS" :key="b.k" type="button" class="card" :class="[b.tone, { on: pv === b.k }]" :data-bucket="b.k" @click="pv = pv === b.k ? '' : b.k">
          <b class="n">{{ preview[b.k]?.length ?? 0 }}</b><span class="k">{{ b.label }}</span></button>
      </div>
      <p v-if="pv === 'blocked_production'" class="note">正式／備援不會被自動納管（要走 PAM＋金鑰）。</p>
      <p v-else-if="pv === 'other'" class="note">沒開 22（SSH）——這支只能納管走 SSH 的機器，跳過。</p>
      <V2Table v-if="pv && pv !== 'unknown'" :rows="pvRows" prefix="P" row-key="ip" unit="台" :export-name="`批次納管預覽_${pv}`" empty="這一桶沒有機器"
               :cols="[{ k: 'ip', label: 'IP', mono: true }, { k: 'environment', label: '環境別' }, { k: 'reason', label: '原因' }]" />
      <p v-if="preview.excluded?.length" class="note warn">{{ preview.excluded.length }} 台不會被納管——資產庫登記的作業系統不是伺服器 Linux（點「另有納管方式」看是哪幾台）
        <InfoNote>交換器、儲存設備、F5、FortiGate、KVM 切換器、還有伺服器的管理卡（iDRAC）都吃 SSH，banner 還常自報 Linux——只看「有沒有開 22」會把它們全收進來，在上面建帳號是會出事的等級。
          所以改成看資產庫登記的作業系統，而且是白名單：認得出是伺服器 Linux 才跑，認不出來就跳過。CoreOS／RHCOS（OpenShift 節點）雖然是 Linux，也一律排除：本機帳號會在節點重佈時消失，而且那批機器歸 OpenShift 管。</InfoNote></p>
      <p v-if="preview.windows?.length" class="note">Windows 不會被這支納管、也不會拿密碼去試，要走 WinRM 另外處理。</p>
      <template v-if="preview.unknown?.length">
        <p class="note">未登記、系統無法確認是不是測試機。你確認是測試機的才勾，勾了才會納管（已勾 {{ pickedUnknown.length }} 台）：</p>
        <!-- 逐台勾（不用表格的全選）：未登記的要人一台一台確認是測試機 -->
        <V2Table :rows="unkRows" prefix="U" row-key="ip" unit="台" export-name="批次納管_未登記"
                 :cols="[{ k: 'pick', label: '確認是測試機', text: (r: any) => (includeUnknown[r.ip] ? '已勾' : '') }, { k: 'ip', label: 'IP', mono: true }, { k: 'reason', label: '原因' }]">
          <template #cell-pick="{ row }">
            <input type="checkbox" class="pick1" :checked="!!includeUnknown[row.ip]" :aria-label="`確認 ${row.ip} 是測試機`" @change="includeUnknown = { ...includeUnknown, [row.ip]: ($event.target as HTMLInputElement).checked }">
          </template>
        </V2Table>
      </template>
      <div class="unify">
        <label class="ck"><input v-model="form.unify" type="checkbox"> <b>順便把密碼統一成 A</b>（用舊密碼 B 進去的，改成 A）</label>
        <p class="warn">這會改機器的 root 密碼、而且收不回；這批之後共用同一個密碼 A。</p>
        <label v-if="form.unify" class="f">確認（輸入大寫 YES 才會執行改密碼）<input v-model="form.confirm" class="in" placeholder="YES" aria-label="確認 YES"></label>
      </div>
      <div class="bar"><button type="button" class="btn" :disabled="starting || status?.running" @click="start()">{{ status?.running ? '正在跑上一批…' : '開始自動納管' }}</button></div>
    </template>

    <p v-if="statusErr" class="err">{{ statusErr }}</p>
    <template v-if="status && status.run">
      <div class="bar"><h3 class="h3">執行結果</h3><button type="button" class="btn2" @click="doExport">匯出 Excel</button>
        <span class="muted">{{ status.run.started_at }}</span>
        <span v-if="status.running" class="muted" data-test="running">執行中 {{ status.done }} / {{ status.total }}</span>
        <span v-else-if="status.interrupted" class="warn" data-test="interrupted">已停下（跑到 {{ status.done }} / {{ status.total }}，還剩 {{ status.remaining?.length ?? 0 }} 台沒跑）</span>
        <span v-else class="muted">已完成（{{ status.run.status }}）</span>
        <span v-if="status.run.unify" class="muted">含統一密碼</span></div>
      <div v-if="status.running" class="pbar"><i :style="{ width: (status.total ? status.done / status.total * 100 : 0) + '%' }" /></div>
      <div v-if="status.interrupted && status.remaining?.length" class="bar resume">
        <span>上一批中途停了，還有 <b>{{ status.remaining.length }}</b> 台沒跑。要繼續請在上方重新輸入密碼，再按：</span>
        <button type="button" class="btn" :disabled="starting" @click="resume">繼續剩下的 {{ status.remaining.length }} 台</button>
        <InfoNote>不會自動接著跑——連續拿錯帳密打整批，看起來就像暴力破解，會鎖帳號、觸發資安告警。所以一定要人重新輸入密碼、手動按。</InfoNote>
      </div>
      <div class="cards">
        <button v-for="c in RES.slice(1)" :key="c.k" type="button" class="card" :class="[{ on: rf === c.k }, c.k === 'failed' ? 'warn' : c.k === 'onboarded' ? 'ok' : '']" :data-res="c.k" @click="rf = rf === c.k ? '' : c.k">
          <b class="n">{{ (cnt as any)[c.k] }}</b><span class="k">{{ c.label }}</span></button>
      </div>
      <V2Table :rows="resRows" prefix="R" row-key="ip" unit="台" :max="200" export-name="批次自動納管結果" empty="尚無結果"
               :cols="[{ k: 'ip', label: 'IP', mono: true }, { k: 'environment', label: '環境別' }, { k: 'login_t', label: '登入', tone: (r: any) => (r.login_ok ? 'dr' : 'test') },
                       { k: 'pw_t', label: '用哪組' }, { k: 'ob_t', label: '已納管' }, { k: 'un_t', label: '改成 A' }, { k: 'fail_reason', label: '失敗原因' }]" />
    </template>
  </div>
</template>

<style scoped>
.ba { font-size: 13px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { margin: 12px 0 6px; font-size: 15px; }
.f { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--muted); margin-bottom: 6px; }
.in { padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); font: inherit; font-size: 13px; }
textarea.in { width: 100%; font-family: var(--disp); }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.btn:disabled, .btn2:disabled { opacity: .5; cursor: default; }
.cards { display: flex; gap: 8px; flex-wrap: wrap; margin: 6px 0 10px; }
.card { min-width: 120px; padding: 8px 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); display: flex; flex-direction: column; align-items: flex-start; cursor: pointer; font-family: inherit; color: var(--ink); }
.card.ok .n { color: var(--good); }
.card.warn .n { color: var(--warn-text); }
.card.on { border-color: var(--brand); background: var(--mint); }
.card .n { font-family: var(--disp); font-size: 22px; }
.card .k { font-size: 12px; color: var(--ink-soft); }
.note { font-size: 13px; color: var(--ink-soft); }
.note.warn, .warn { color: var(--warn-text); }
.unify { margin: 10px 0; padding: 10px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--sub); }
.ck { display: flex; gap: 6px; align-items: center; }
.pbar { height: 6px; background: var(--sub); border-radius: 3px; overflow: hidden; margin: 0 0 10px; }
.pbar i { display: block; height: 100%; background: var(--brand); }
.resume { padding: 8px 12px; border-radius: 10px; background: var(--warn-soft); }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
