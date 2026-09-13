<script setup lang="ts">
// 方案B：一批主機、同一帳號、多組候選密碼，回報「哪一組通」。
// 2026-09-06 使用者的情境：機隊裡 root 密碼混了 A/B 兩種，不想一台一台手動試。
//
// 跟一鍵納管（OnboardModal）不一樣：這支**不建帳號、不執行任何腳本**，純粹
// 唯讀探測「這組密碼登得進去嗎」。密碼只用這一次，前端用完即清、後端用完
// 即丟——回傳值裡也只有「第幾組密碼對」，不會出現密碼本身。
const props = defineProps<{ ips: string[] }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const { apiFetch } = useApi()
const { showToast } = useToast()

const username = ref('sysinfra')
const passwordA = ref('')
const passwordB = ref('')
const busy = ref(false)

interface ProbeRow {
  ip: string; matched_password_index: number | null; platform: string | null
  uid: number | null; has_sudo: boolean | null; error: string | null
}
const results = ref<ProbeRow[] | null>(null)
const summary = ref<{ total: number; matched: number; unmatched: number } | null>(null)

async function run() {
  const passwords = [passwordA.value, passwordB.value].filter((p) => p)
  if (!username.value) { showToast('請輸入帳號', 'warn'); return }
  if (!passwords.length) { showToast('至少輸入一組密碼', 'warn'); return }
  busy.value = true
  results.value = null
  try {
    const r = await apiFetch<{ total: number; matched: number; unmatched: number; results: ProbeRow[] }>(
      '/api/onboard/batch-probe', {
        method: 'POST',
        body: { ips: props.ips, username: username.value, passwords },
      })
    results.value = r.results
    summary.value = { total: r.total, matched: r.matched, unmatched: r.unmatched }
    showToast(`比對完成：${r.matched}/${r.total} 台找到能用的密碼`, r.unmatched ? 'warn' : 'success')
  } catch (err: any) {
    showToast(err?.data?.detail ?? '比對失敗，請稍後重試', 'error')
  } finally {
    // 密碼一律用完即清——不管成功失敗，畫面上都不留著
    passwordA.value = ''
    passwordB.value = ''
    busy.value = false
  }
}

function labelFor(r: ProbeRow): string {
  if (r.matched_password_index === 0) return '密碼 A'
  if (r.matched_password_index === 1) return '密碼 B'
  return '都不對'
}
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal">
      <div class="mhead">🔑 批次試密碼 — {{ ips.length }} 台</div>
      <p class="mhint">
        對這 {{ ips.length }} 台，依序試同一個帳號的兩組候選密碼，回報「哪一組對得上」。
        <b>只探測連得不連得進去，不建帳號、不執行任何腳本</b>；密碼用完即丟，
        不進資料庫、不進紀錄。
      </p>
      <p class="mwarn">
        ⚠️ 這是對多台主機連續嘗試密碼，形狀跟「密碼噴灑」一樣——目標機若有
        fail2ban 之類的鎖定機制，連續試錯可能鎖帳號。台數大時建議先抓一小批試。
      </p>
      <label class="mf">登入帳號
        <input v-model="username" autocomplete="off" placeholder="例：sysinfra 或 root" />
      </label>
      <label class="mf">密碼 A
        <input v-model="passwordA" type="password" autocomplete="new-password" />
      </label>
      <label class="mf">密碼 B（沒有就留空）
        <input v-model="passwordB" type="password" autocomplete="new-password" @keyup.enter="run" />
      </label>
      <div class="macts">
        <button class="btn primary" :disabled="busy" @click="run">
          {{ busy ? '比對中…' : '開始比對' }}
        </button>
        <button class="btn ghost" :disabled="busy" @click="emit('close')">關閉</button>
      </div>

      <div v-if="summary" class="summary">
        共 {{ summary.total }} 台，<b class="ok">{{ summary.matched }}</b> 台找到能用的密碼，
        <b class="bad">{{ summary.unmatched }}</b> 台兩組都不對。
      </div>
      <div v-if="results" class="tbl-wrap">
        <table>
          <thead><tr><th>IP</th><th>結果</th><th>平台</th><th>備註</th></tr></thead>
          <tbody>
            <tr v-for="r in results" :key="r.ip">
              <td class="mono">{{ r.ip }}</td>
              <td>
                <span class="pill" :class="r.matched_password_index !== null ? 'ok' : 'bad'">
                  {{ labelFor(r) }}
                </span>
              </td>
              <td>{{ r.platform || '—' }}</td>
              <td class="small">{{ r.error || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.55); display: flex;
  align-items: center; justify-content: center; z-index: 50; }
.modal { width: 480px; max-width: 92vw; max-height: 86vh; overflow: auto;
  background: var(--card-solid, #12211c); border: 1px solid var(--border-strong);
  border-radius: 12px; padding: 22px 24px; }
.mhead { font-size: 15px; font-weight: 700; margin-bottom: 10px; color: var(--brand-dark); }
.mhint { font-size: 11.5px; color: var(--muted); line-height: 1.6; margin: 0 0 10px; }
.mwarn { font-size: 11.5px; color: var(--warn-text, #a8690a); line-height: 1.6;
  background: var(--warn-soft, rgba(176,106,0,.08)); border: 1px solid rgba(176,106,0,.25);
  border-radius: 6px; padding: 7px 10px; margin: 0 0 14px; }
.mf { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); margin-bottom: 12px; }
.mf input { font-family: inherit; font-size: 13px; padding: 8px 10px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 6px; }
.macts { display: flex; gap: 10px; margin-top: 4px; margin-bottom: 14px; }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 700; padding: 9px 20px;
  border: none; border-radius: 6px; cursor: pointer; }
.btn.primary { background: var(--brand); color: #fff; }
.btn.primary:hover:not(:disabled) { background: var(--brand-dark); }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.summary { font-size: 12.5px; color: var(--ink-soft); margin-bottom: 10px; }
.summary .ok { color: var(--good, #009142); }
.summary .bad { color: var(--bad); }
.tbl-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { border-bottom: 1px solid var(--border-strong); padding: 6px 8px; text-align: left; }
.mono { font-family: ui-monospace, Consolas, monospace; }
.small { font-size: 11px; color: var(--muted); }
.pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }
.pill.ok { background: rgba(0,145,66,.15); color: var(--good, #009142); }
.pill.bad { background: var(--bad-soft); color: var(--bad); }
</style>
