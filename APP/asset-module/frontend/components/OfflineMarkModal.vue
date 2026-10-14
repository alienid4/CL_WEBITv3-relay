<script setup lang="ts">
// 標記下線（共用元件，2026-09-16）。
//
// 走的是 v1.185.0 已經有的 /api/assets/batch-status：一台或多台都同一支，
// **原因必填**，每台自動記一筆「CIA 待異動」——因為 CIA 清冊重匯會把系統裡改的蓋回去，
// 不提醒回頭改清冊，這個改動下次匯入就不見了。
//
// 這裡不自己寫 UPDATE，也不另開端點：同一件事只能有一個入口，不然稽核軌跡會有兩套。

const props = withDefaults(defineProps<{
  serials: string[]
  /** 偵測存活帶過來的原因，人可以改 */
  defaultReason?: string
  /** 顯示用：這是哪一台（單台時帶主機名／IP） */
  who?: string
}>(), { defaultReason: '', who: '' })

const emit = defineEmits<{ (e: 'done'): void; (e: 'close'): void }>()

const { apiFetch } = useApi()
const { showToast } = useToast()

const STATUSES = ['停用', '報廢', '閒置', '使用中']
const status = ref('停用')
const reason = ref(props.defaultReason)
const busy = ref(false)
// [B-08] 整台下線 vs 只下線選的這幾筆（某個服務）。CIA 一筆＝一個服務，兩件事不同，要人選。
// 預設整台：「這台下線了」是最常見的意思；只收掉某個服務時再切過去。
const scope = ref<'machine' | 'single'>('machine')
const previewCount = ref<number | null>(null)

async function submit() {
  if (!reason.value.trim()) { showToast('要填原因（例：現場確認已下線）', 'warn'); return }
  if (previewCount.value === null) { showToast('還在計算會改到哪幾筆，請稍等', 'warn'); return }
  busy.value = true
  try {
    const r = await apiFetch<{ updated: string[]; unchanged: string[]; not_found: string[] }>(
      '/api/assets/batch-status',
      { method: 'POST', body: { serials: props.serials, status: status.value, reason: reason.value, scope: scope.value } })
    showToast(`已改 ${r.updated.length} 筆為「${status.value}」`
      + (r.unchanged.length ? `，${r.unchanged.length} 台本來就是` : '')
      + '；已記進 CIA 待異動，記得回頭改 CIA 清冊', 'success', 10000)
    emit('done')
  } catch (e: any) {
    showToast(`變更失敗：${e?.data?.detail ?? e?.message}`, 'error', 12000)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <!-- 點背景**不關視窗**（2026-09-17 使用者：「這個頁面如果我沒有點好，很容易整個頁面就跳掉」）。
       這幾個視窗都要打字（密碼、原因），點歪一下就整個關掉、打的東西全沒了。
       關閉一律走「取消」按鈕——明確的動作才關，滑鼠失手不算。 -->
    <div class="mask">
    <div class="box">
      <h3>標記下線</h3>
      <p class="who">
        對象：<b>{{ who || `${serials.length} 台` }}</b>
        <span v-if="!who && serials.length" class="dim">（{{ serials.slice(0, 5).join('、') }}{{ serials.length > 5 ? ' …' : '' }}）</span>
      </p>
      <label class="fld">改成
        <select v-model="status" class="in">
          <option v-for="s in STATUSES" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <label class="fld">原因
        <input v-model="reason" class="in wide" placeholder="必填，例：現場確認已下線" />
      </label>
      <!-- [B-08] 範圍：整台 vs 只這幾筆；動作前先預覽會改到哪幾筆 -->
      <div class="fld scope">範圍
        <label><input v-model="scope" type="radio" value="machine" /> 整台下線（同一台的所有登記）</label>
        <label><input v-model="scope" type="radio" value="single" /> 只下線選的這幾筆（某個服務）</label>
      </div>
      <ScopePreview :serials="serials" :scope="scope" @count="previewCount = $event" />
      <p class="note">
        改完會記一筆「CIA 待異動」。<b>CIA 資產清冊也要改</b>——不然下次重匯清冊，
        會用清冊上的舊值把這裡改的蓋回去。
      </p>
      <div class="acts">
        <button class="btn" type="button" :disabled="busy" @click="emit('close')">取消</button>
        <button class="btn primary" type="button" :disabled="busy || previewCount === null" @click="submit">
          {{ busy ? '處理中…' : (previewCount !== null ? `確定（改 ${previewCount} 筆）` : '確定') }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mask { position: fixed; inset: 0; background: rgba(0,0,0,.35); display: flex;
        align-items: center; justify-content: center; z-index: 60; }
.box { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
       box-shadow: var(--shadow); padding: 18px 20px; width: min(520px, 92vw); }
h3 { margin: 0 0 10px; font-size: 16px; }
.who { font-size: 13px; margin: 0 0 12px; }
.dim { color: var(--muted); }
.fld { display: flex; align-items: center; gap: 8px; font-size: 13px; margin-bottom: 10px; }
.in { padding: 5px 9px; border: 1px solid var(--border-strong); border-radius: 5px;
      background: var(--card); color: var(--ink); }
.in.wide { flex: 1; }
.fld.scope { flex-wrap: wrap; gap: 4px 14px; }
.fld.scope label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
.box { max-height: 92vh; overflow: auto; }
.note { font-size: 12px; background: var(--warn-soft); color: var(--warn-text);
        padding: 8px 10px; border-radius: 6px; margin: 4px 0 14px; }
.acts { display: flex; justify-content: flex-end; gap: 8px; }
.btn { padding: 6px 14px; border-radius: 5px; border: 1px solid var(--border-strong);
       background: var(--card); color: var(--ink); cursor: pointer; }
.btn.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.btn:disabled { opacity: .6; cursor: wait; }
</style>
