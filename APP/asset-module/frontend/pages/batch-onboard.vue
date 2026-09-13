<script setup lang="ts">
// 一鍵批次自動納管：一批測試機 → 試密碼 → 佈金鑰(納管) → (可選)統一密碼成 A。
// 設計定案見 AI/計畫_一鍵批次自動納管.md。
//
// 安全重點都在畫面上講清楚：只跑「環境別＝測試」的、正式/備援擋掉、未登記要人
// 勾選確認、統一密碼要打 YES。密碼用完即清，不留在畫面也不進系統任何地方。
definePageMeta({ ssr: false })

const { apiFetch } = useApi()
const { showToast } = useToast()
const runtimeConfig = useRuntimeConfig()

const form = reactive({
  ipsText: '', username: 'root', passwordA: '', passwordB: '',
  unify: false, confirm: '',
})

interface Bucket { ip: string; environment?: string; reason?: string }
interface Preview {
  run: Bucket[]; windows: Bucket[]; other: Bucket[]
  blocked_production: Bucket[]; unknown: Bucket[]
  // 資產庫登記的作業系統不是伺服器 Linux——設備、OpenShift 節點之類的
  excluded: (Bucket & { kind?: string; os?: string })[]
}
const preview = ref<Preview | null>(null)
const includeUnknown = ref<Set<string>>(new Set())
const previewing = ref(false)

function parseIps(): string[] {
  return form.ipsText.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean)
}

async function doPreview() {
  const ips = parseIps()
  if (!ips.length) { showToast('先貼上要納管的機器 IP', 'warn'); return }
  previewing.value = true
  try {
    preview.value = await apiFetch<Preview>('/api/onboard/batch-auto/preview', {
      method: 'POST', body: { ips, username: form.username, passwords: [form.passwordA] },
    })
    includeUnknown.value = new Set()
  } catch (err: any) {
    showToast(err?.data?.detail ?? '預覽失敗', 'error')
  } finally {
    previewing.value = false
  }
}

function toggleUnknown(ip: string) {
  const s = new Set(includeUnknown.value)
  if (s.has(ip)) s.delete(ip); else s.add(ip)
  includeUnknown.value = s
}

// ===== 執行 + 輪詢進度 =====
interface ResultRow {
  ip: string; environment: string | null; login_ok: number; password_index: number | null
  platform: string | null; onboarded: number; password_unified: number
  fail_stage: string | null; fail_reason: string | null
}
interface Status {
  running: boolean
  run: { id: number; status: string; unify: number; target_count: number
         triggered_by: string; started_at: string; finished_at: string | null; error: string | null } | null
  total: number; done: number; results: ResultRow[]
}
const status = ref<Status | null>(null)
const starting = ref(false)
let poller: ReturnType<typeof setInterval> | null = null

async function pollStatus() {
  try {
    status.value = await apiFetch<Status>('/api/onboard/batch-auto/status')
    if (status.value && !status.value.running && poller) {
      clearInterval(poller); poller = null
    }
  } catch { /* 輪詢失敗不中斷 */ }
}
onBeforeUnmount(() => { if (poller) clearInterval(poller) })
onMounted(pollStatus)   // 隔天回來也看得到上次那批

async function start() {
  const ips = parseIps()
  const passwords = [form.passwordA, form.passwordB].filter(Boolean)
  if (!passwords.length) { showToast('至少輸入一組密碼', 'warn'); return }
  if (form.unify && form.confirm !== 'YES') {
    showToast('勾了統一密碼，要在確認欄輸入大寫 YES', 'warn'); return
  }
  starting.value = true
  try {
    await apiFetch('/api/onboard/batch-auto', {
      method: 'POST',
      body: {
        ips, username: form.username, passwords,
        unify_password: form.unify, confirm: form.confirm,
        include_ips: [...includeUnknown.value],
      },
    })
    // 密碼用完即清
    form.passwordA = ''; form.passwordB = ''; form.confirm = ''
    showToast('已開始，下方會即時顯示進度', 'success')
    if (poller) clearInterval(poller)
    poller = setInterval(pollStatus, 2000)
    pollStatus()
  } catch (err: any) {
    showToast(err?.data?.detail ?? '啟動失敗', 'error')
  } finally {
    starting.value = false
  }
}

function pwLabel(idx: number | null, loginOk: number): string {
  if (idx === 0) return '密碼 A'
  if (idx === 1) return '密碼 B'
  return loginOk ? '—' : '都不對'
}

const summary = computed(() => {
  const rs = status.value?.results ?? []
  return {
    onboarded: rs.filter((r) => r.onboarded).length,
    unified: rs.filter((r) => r.password_unified).length,
    failed: rs.filter((r) => !r.onboarded).length,
  }
})

function exportUrl() {
  return `${runtimeConfig.public.apiBase}/api/onboard/batch-auto/export`
}
async function doExport() {
  try {
    const res = await fetch(exportUrl(), { credentials: 'include' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const cd = res.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = m ? decodeURIComponent(m[1]) : 'batch_onboard.xlsx'
    a.click(); URL.revokeObjectURL(url)
  } catch (err: any) {
    showToast(`匯出失敗：${err?.message ?? '請稍後再試'}`, 'error')
  }
}
</script>

<template>
  <div class="page">
    <header class="hd">
      <div>
        <h1>一鍵批次自動納管 <InfoNote><b>只跑環境別＝測試的機器</b>；正式／備援會被擋、未登記的要你逐台確認。試密碼 → 佈金鑰（納管）→（可選）把密碼統一成 A。</InfoNote></h1>
        <p class="sub">一批<b>測試機</b>：試密碼 → 佈金鑰（納管）→（可選）把密碼統一成 A</p>
      </div>
    </header>

    <!-- 輸入範圍 + 帳密 -->
    <section class="card">
      <label class="f">要納管的機器 IP 或整個網段（一行一個，或用空白／逗號分隔）
        <textarea v-model="form.ipsText" rows="4" placeholder="10.99.1.11&#10;10.99.1.12&#10;10.99.1.0/24（整段：只會納管掃描掃到活著的）" />
      </label>
      <div class="row">
        <label class="f">登入帳號<input v-model="form.username" autocomplete="off" placeholder="root" /></label>
        <label class="f">密碼 A（現行）<input v-model="form.passwordA" type="password" autocomplete="new-password" /></label>
        <label class="f">密碼 B（舊的，沒有留空）<input v-model="form.passwordB" type="password" autocomplete="new-password" /></label>
      </div>
      <div class="acts">
        <button class="btn" :disabled="previewing" @click="doPreview">
          {{ previewing ? '檢查中…' : '先預覽範圍' }}
        </button>
      </div>
    </section>

    <!-- 預覽：會跑哪些、擋哪些、要確認哪些 -->
    <section v-if="preview" class="card">
      <h2>這批的分類</h2>
      <div class="buckets">
        <div class="bk ok">
          <div class="bk-n">{{ preview.run.length }}</div>
          <div class="bk-l">會自動納管（Linux 測試機）</div>
        </div>
        <div class="bk">
          <div class="bk-n">{{ preview.windows.length }}</div>
          <div class="bk-l">Windows（要另走 WinRM）</div>
        </div>
        <div class="bk warn">
          <div class="bk-n">{{ preview.blocked_production.length }}</div>
          <div class="bk-l">擋下（正式／備援，不碰）</div>
        </div>
        <div class="bk">
          <div class="bk-n">{{ preview.unknown.length }}</div>
          <div class="bk-l">未登記，要你確認</div>
        </div>
        <div class="bk">
          <div class="bk-n">{{ preview.other.length }}</div>
          <div class="bk-l">其他（沒開 SSH）</div>
        </div>
        <div class="bk warn">
          <div class="bk-n">{{ preview.excluded?.length ?? 0 }}</div>
          <div class="bk-l">不納管（設備）</div>
        </div>
      </div>

      <!-- 開 22 不等於是伺服器。列出來並講清楚為什麼那台不能，不是靜默消失。 -->
      <div v-if="preview.excluded?.length" class="note warn">
        以下 <b>{{ preview.excluded.length }}</b> 台<b>不會</b>被納管——資產庫登記的作業系統不是伺服器 Linux
        <InfoNote>交換器、儲存設備、F5、FortiGate、KVM 切換器、還有伺服器的<b>管理卡（iDRAC）</b>都吃 SSH，banner 還常自報 Linux——只看「有沒有開 22」會把它們全收進來，在上面建帳號是會出事的等級。<br><br>所以改成看<b>資產庫登記的作業系統</b>，而且是<b>白名單</b>：認得出是伺服器 Linux 才跑，認不出來就跳過。黑名單救不了——沒見過的廠牌就會漏。<br><br><b>CoreOS／RHCOS（OpenShift 節點）雖然是 Linux，也一律排除</b>：本機帳號會在節點重佈時消失，而且那批機器歸 OpenShift 管。</InfoNote>：
        <div class="exlist">
          <div v-for="e in preview.excluded" :key="e.ip" class="exrow">
            <span class="mono">{{ e.ip }}</span>
            <span class="exwhy">{{ e.reason }}</span>
          </div>
        </div>
      </div>

      <div v-if="preview.windows.length" class="note">
        以下是 <b>Windows</b>（開 445/5985），<b>不會</b>被這支納管，要走 WinRM 另外處理
        <InfoNote>這支工具走 SSH 不做 Windows——不會納管它們，也不會拿密碼去試（避免誤判）。</InfoNote>：
        <span v-for="w in preview.windows" :key="w.ip" class="chip">{{ w.ip }}</span>
      </div>

      <div v-if="preview.blocked_production.length" class="note warn">
        以下是正式／備援，<b>不會</b>被自動納管（要走 PAM＋金鑰）：
        <span v-for="b in preview.blocked_production" :key="b.ip" class="chip">{{ b.ip }}（{{ b.environment }}）</span>
      </div>

      <div v-if="preview.other.length" class="note">
        以下沒開 22（SSH），這支只能納管走 SSH 的機器——跳過：
        <span v-for="o in preview.other" :key="o.ip" class="chip">{{ o.ip }}</span>
      </div>

      <div v-if="preview.unknown.length" class="note">
        以下未登記、系統無法確認是不是測試機。<b>你確認是測試機的才勾</b>，勾了才會納管：
        <label v-for="u in preview.unknown" :key="u.ip" class="ckline">
          <input type="checkbox" :checked="includeUnknown.has(u.ip)" @change="toggleUnknown(u.ip)" />
          {{ u.ip }} <span class="dim">— {{ u.reason }}</span>
        </label>
      </div>

      <!-- 統一密碼：預設不勾，勾了要打 YES -->
      <div class="unify">
        <label class="ckline">
          <input v-model="form.unify" type="checkbox" />
          <b>順便把密碼統一成 A</b>（用舊密碼 B 進去的，改成 A）
        </label>
        <p class="dim sm">
          ⚠️ 這會改機器的 root 密碼、而且收不回。也代表這批之後共用同一個密碼 A。
        </p>
        <label v-if="form.unify" class="f narrow">確認（輸入大寫 YES 才會執行改密碼）
          <input v-model="form.confirm" placeholder="YES" />
        </label>
      </div>

      <div class="acts">
        <button class="btn primary" :disabled="starting || (status?.running)" @click="start">
          {{ status?.running ? '正在跑上一批…' : '開始自動納管' }}
        </button>
      </div>
    </section>

    <!-- 進度 + 結果 -->
    <section v-if="status && status.run" class="card">
      <div class="sec-head">
        <h2>執行結果</h2>
        <button class="btn small" @click="doExport">⬇ 匯出 Excel</button>
      </div>
      <p class="dim sm">
        {{ status.run.started_at }}
        <span v-if="status.running">・執行中 {{ status.done }} / {{ status.total }}</span>
        <span v-else>・已完成（{{ status.run.status }}）</span>
        <span v-if="status.run.unify">・含統一密碼</span>
      </p>
      <div v-if="status.running" class="bar"><i :style="{ width: (status.total ? status.done / status.total * 100 : 0) + '%' }" /></div>

      <div class="buckets">
        <div class="bk ok"><div class="bk-n">{{ summary.onboarded }}</div><div class="bk-l">已納管</div></div>
        <div class="bk"><div class="bk-n">{{ summary.unified }}</div><div class="bk-l">改成 A</div></div>
        <div class="bk warn"><div class="bk-n">{{ summary.failed }}</div><div class="bk-l">失敗（要處理）</div></div>
      </div>

      <div class="tbl-wrap">
        <table>
          <thead><tr>
            <th>IP</th><th>環境別</th><th>登入</th><th>用哪組</th><th>已納管</th>
            <th>改成 A</th><th>失敗原因</th>
          </tr></thead>
          <tbody>
            <tr v-for="r in status.results" :key="r.ip">
              <td class="mono">{{ r.ip }}</td>
              <td>{{ r.environment || '—' }}</td>
              <td><span class="pill" :class="r.login_ok ? 'ok' : 'bad'">{{ r.login_ok ? '成功' : '失敗' }}</span></td>
              <td>{{ pwLabel(r.password_index, r.login_ok) }}</td>
              <td>{{ r.onboarded ? '✓' : '—' }}</td>
              <td>{{ r.password_unified ? '✓' : (r.login_ok ? '' : '—') }}</td>
              <td class="msg">{{ r.fail_reason || '' }}</td>
            </tr>
            <tr v-if="!status.results.length"><td colspan="7" class="dim">尚無結果</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.exlist { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }
.exrow { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; font-size: 12px; }
.exrow .mono { font-family: ui-monospace, Consolas, monospace; min-width: 118px; }
.exwhy { color: var(--ink-soft); }
.page { padding: 18px 22px 60px; }
.hd h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: var(--muted); font-size: 13px; margin: 0; max-width: 70ch; }
.sub b { color: var(--ink-soft); }
.card { background: var(--card); border: 1px solid var(--border-strong); border-radius: 12px;
  padding: 18px 20px; margin-top: 16px; }
h2 { font-size: 15px; margin: 0 0 12px; }
.f { display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: var(--muted); margin-bottom: 12px; }
.f.narrow { max-width: 220px; }
.f textarea, .f input { font-family: inherit; font-size: 13px; padding: 8px 10px;
  border: 1px solid var(--border-strong); background: var(--card-solid, #12211c); color: var(--ink); border-radius: 6px; }
.row { display: flex; gap: 14px; flex-wrap: wrap; }
.row .f { flex: 1 1 180px; }
.acts { display: flex; gap: 10px; margin-top: 4px; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 9px 18px; border: 1px solid var(--border-strong);
  border-radius: 6px; cursor: pointer; background: var(--card); color: var(--ink-soft); }
.btn.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.btn.primary:hover:not(:disabled) { background: var(--brand-dark); }
.btn.small { padding: 5px 12px; font-size: 11.5px; }
.btn:disabled { opacity: .55; cursor: not-allowed; }

.buckets { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.bk { flex: 1 1 120px; background: var(--card-solid, #12211c); border: 1px solid var(--border-strong);
  border-radius: 8px; padding: 12px 14px; text-align: center; }
.bk-n { font-size: 24px; font-weight: 700; color: var(--ink); font-family: ui-monospace, monospace; }
.bk.ok .bk-n { color: var(--good, #009142); }
.bk.warn .bk-n { color: var(--warn-text, #d08700); }
.bk-l { font-size: 11px; color: var(--muted); margin-top: 3px; }

.note { font-size: 12px; color: var(--ink-soft); line-height: 1.7; margin: 10px 0; padding: 9px 12px;
  border-radius: 6px; background: var(--card-solid, #12211c); border: 1px solid var(--border-strong); }
.note.warn { border-color: rgba(208,135,0,.4); }
.chip { display: inline-block; margin: 2px 4px 0 0; padding: 1px 8px; border-radius: 10px;
  background: rgba(208,135,0,.12); color: var(--warn-text, #d08700); font-size: 11px; }
.ckline { display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--ink-soft); margin: 4px 0; cursor: pointer; }
.dim { color: var(--muted); }
.dim.sm { font-size: 11px; }

.unify { margin-top: 14px; padding-top: 12px; border-top: 1px dashed var(--border-strong); }

.sec-head { display: flex; align-items: center; justify-content: space-between; }
.bar { height: 8px; border-radius: 999px; background: var(--card-solid, #12211c); overflow: hidden;
  border: 1px solid var(--border-strong); margin: 8px 0 14px; }
.bar i { display: block; height: 100%; background: var(--brand); transition: width .4s ease; }

.tbl-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { border-bottom: 1px solid var(--border-strong); padding: 6px 8px; text-align: left; }
.mono { font-family: ui-monospace, monospace; }
.msg { color: var(--muted); }
.pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }
.pill.ok { background: rgba(0,145,66,.15); color: var(--good, #009142); }
.pill.bad { background: var(--bad-soft); color: var(--bad); }
</style>
