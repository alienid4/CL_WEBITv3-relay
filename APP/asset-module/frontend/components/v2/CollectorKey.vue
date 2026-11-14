<script setup lang="ts">
// ⚙ 收集身分 › 收集金鑰與備註 最上面的「收集金鑰（Linux）」（2.056.8，V56 第 8 批；原舊 /settings credentials 分頁）純搬移，⚠D5 D1：
//   同一組 API：GET /api/collector-key（只回路徑、指紋、公鑰、已納管台數，**不回私鑰**）、POST /api/collector-key/import。
//   body 一字不改：{ confirm, private_key } 或 { confirm, from_path }；要打 REPLACE（後端也檢查）。
//   私鑰只在送出那一次存在於畫面：輸入框一律空白開始（不預填），送出後不論成功或失敗都清掉（舊頁只在成功時清，這是收緊）。
interface CollectorKeyStatus {
  has_key: boolean
  path: string
  fingerprint: string | null
  public_key: string | null
  modified_at: string | null
  onboarded_hosts: number
  last_import: string | null
}
const { apiFetch } = useApi()
const { showToast } = useToast()
const ckStatus = ref<CollectorKeyStatus | null>(null)
const ckErr = ref('')
const ckMode = ref<'paste' | 'path'>('path')   // 預設走「不過網路」那條
const ckPrivate = ref('')
const ckPath = ref('')
const ckConfirm = ref('')
const ckBusy = ref(false)
const ckResult = ref<any>(null)

async function loadCollectorKey() {
  try {
    ckStatus.value = await apiFetch<CollectorKeyStatus>('/api/collector-key')
    ckErr.value = ''
  } catch (e: any) {
    // 舊頁讀不到就整塊不顯示；V2 寫出原因（不可把錯誤吞成「沒有金鑰」）
    ckErr.value = `收集金鑰狀態讀取失敗：${e?.data?.detail ?? e?.message ?? '請稍後重試'}`
  }
}

const ckReady = computed(() =>
  !ckBusy.value && ckConfirm.value === 'REPLACE'
  && (ckMode.value === 'paste' ? !!ckPrivate.value.trim() : !!ckPath.value.trim()))

async function importCollectorKey() {
  if (!ckReady.value) return
  ckBusy.value = true
  ckResult.value = null
  try {
    const body: any = { confirm: ckConfirm.value }
    if (ckMode.value === 'paste') body.private_key = ckPrivate.value
    else body.from_path = ckPath.value.trim()
    ckResult.value = await apiFetch<any>('/api/collector-key/import',
                                         { method: 'POST', body })
    ckConfirm.value = ''
    await loadCollectorKey()
    showToast(ckResult.value.changed ? '金鑰已更換' : '指紋相同——就是同一把金鑰',
              ckResult.value.changed ? 'warn' : 'success', 10000)
  } catch (e: any) {
    showToast(e?.data?.detail || '匯入失敗', 'error', 10000)
  } finally {
    ckPrivate.value = ''          // 私鑰用完就從畫面清掉（成功或失敗都清），不要留在輸入框裡等人截圖
    ckBusy.value = false
  }
}
onMounted(loadCollectorKey)
</script>

<template>
  <div class="card">
    <div class="ct">收集金鑰（Linux）
      <InfoNote>Linux 主機走 SSH 金鑰收集（Windows 走收集憑證裡的服務帳號）。私鑰存在伺服器 {{ ckStatus?.path ?? '' }}（0600），畫面與 API 永遠不會回傳私鑰。平常不用動這裡——deploy.sh 第一次就會產一把。會用到匯入，是因為公司可能已經有一把佈遍全機隊的金鑰（例如巡檢用的），匯入它就省掉重新納管每一台。</InfoNote>
    </div>
    <p v-if="ckErr" class="err">{{ ckErr }}</p>
    <div v-if="ckStatus" class="bar" data-test="ck-status">
      <span>指紋 <code>{{ ckStatus.fingerprint ?? '（還沒有金鑰）' }}</code></span>
      <span>已納管 <b>{{ ckStatus.onboarded_hosts }}</b> 台
        <InfoNote>換金鑰會讓這 {{ ckStatus.onboarded_hosts }} 台的收集當場失效——它們的 authorized_keys 裡是舊公鑰，新鑰匙不開那些鎖。除非你匯入的正是它們認得的那一把，否則全部要重新納管。這就是這裡要打字確認的原因。</InfoNote>
      </span>
      <span v-if="ckStatus.modified_at" class="muted">更新於 {{ ckStatus.modified_at }}</span>
    </div>

    <details class="imp">
      <summary>匯入既有金鑰</summary>
      <form class="body" autocomplete="off" @submit.prevent="importCollectorKey">
        <div class="bar">
          <select v-model="ckMode" class="in" aria-label="匯入方式">
            <option value="path">指定主機上的檔案路徑</option>
            <option value="paste">直接貼上私鑰</option>
          </select>
          <InfoNote>兩種方式的差別是私鑰會不會經過網路。指定路徑：你自己用 scp／WinSCP 把私鑰放到這台主機上，網頁只負責驗證配對、設好權限、搬到正確位置、重啟服務，私鑰完全不過網路。直接貼上：方便，但這套系統走的是內網 http（沒有 TLS），私鑰會以明文經過網路。自己權衡。</InfoNote>
        </div>
        <div v-if="ckMode === 'path'" class="bar">
          <span class="lb">私鑰路徑</span>
          <input v-model="ckPath" class="in wide" placeholder="/tmp/id_ed25519" :disabled="ckBusy" aria-label="私鑰路徑">
        </div>
        <div v-else class="bar">
          <textarea v-model="ckPrivate" class="in wide mono" rows="6" :disabled="ckBusy" autocomplete="off" spellcheck="false"
                    placeholder="貼上私鑰全文（含開頭與結尾那兩行 BEGIN／END）" aria-label="私鑰" />
        </div>
        <div class="bar">
          <span class="lb">確認</span>
          <input v-model="ckConfirm" class="in" placeholder="輸入 REPLACE" :disabled="ckBusy" aria-label="確認文字">
          <button type="submit" class="btn" :disabled="!ckReady">{{ ckBusy ? '匯入中…' : '匯入金鑰' }}</button>
          <InfoNote>這個動作會讓已經在運作的東西停止運作（{{ ckStatus?.onboarded_hosts ?? 0 }} 台的收集），按鈕太容易誤觸，所以要打字。</InfoNote>
        </div>
        <p v-if="ckResult" class="res" :class="ckResult.changed ? 'warn' : 'ok'">{{ ckResult.next_step }}</p>
      </form>
    </details>
  </div>
</template>

<style scoped>
.card { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); padding: 10px 14px; margin-bottom: 12px; }
.ct { font-size: 13px; font-weight: 700; color: var(--ink-soft); margin-bottom: 6px; }
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13px; margin-top: 8px; }
.lb { min-width: 72px; color: var(--ink-soft); }
.imp { margin-top: 12px; }
.imp summary { cursor: pointer; font-size: 13px; color: var(--ink-soft); }
.in { font-family: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.in.wide { flex: 1; min-width: 260px; }
.in.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; width: 100%; }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--card); border-radius: 8px; padding: 5px 14px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .5; }
.res { font-size: 13px; margin-top: 10px; }
.res.warn { color: var(--warn-text); }
.res.ok { color: var(--good); }
.err { color: var(--bad); font-size: 13px; margin: 0 0 8px; }
.muted { color: var(--muted); }
</style>
