<script setup lang="ts">
// vCenter／RVTools 的「全自動」匯入模式。
//
// 為什麼從系統設定搬到資料匯入頁（2026-08-15 使用者指出）：
// 這跟資料匯入頁上的 RVTools 手動上傳**是同一件事**——同一種檔（RVTools 匯出的 xlsx）、
// 同一條解析管線（vcenter_autoimport 直接沿用 rvtools_import），差別只在誰去拿檔：
// 半自動是人上傳，全自動是系統去監看資料夾抓最新的。
// 兩個 UI 拆在兩頁、名字又都叫「匯入」，使用者得先知道「哪個匯入在哪裡」才找得到。
// 欄位以後端 vcenter_autoimport.health() 的實際回傳為準。
// 搬元件時我憑印象重打了一份（寫成 lamp/message），跟後端的 status/reason 對不上——
// 執行期不會報錯（JS 讀不存在的屬性只是 undefined），畫面會安靜地少一塊；
// 是 vue-tsc 掃出來的（2026-08-15）。
interface VcHealth {
  enabled: boolean
  dir: string
  max_age_hours: number
  status: 'green' | 'yellow' | 'red' | 'off' | string
  reason?: string
  newest_file?: string | null
  newest_age_hours?: number | null
  last_at?: string
  last_result?: string
}
const { apiFetch } = useApi()
const { showToast } = useToast()

const vc = reactive({ enabled: false, dir: '', max_age_hours: 36 })
const vcHealth = ref<VcHealth | null>(null)
const vcLoading = ref(false)
const vcSaving = ref(false)
const vcRunning = ref(false)
const VC_LAMP_LABEL: Record<string, string> = {
  green: '正常收得到', yellow: '需要注意', red: '有問題', off: '未啟用',
}
// 要貼到那台 Windows 工作排程器的 RVTools 匯出指令（依使用者設的資料夾即時組出）
const vcExportCmd = computed(() => {
  const d = vc.dir?.trim() || 'D:\vcenter_export'
  return `"C:\Program Files (x86)\Robware\RVTools\RVTools.exe" -s <vCenter位址> -u <唯讀帳號> -p <密碼> -c ExportAll2xlsx -d "${d}"`
})

async function loadVcAuto() {
  vcLoading.value = true
  try {
    const h = await apiFetch<VcHealth>('/api/vcenter-autoimport')
    vcHealth.value = h
    vc.enabled = h.enabled; vc.dir = h.dir; vc.max_age_hours = h.max_age_hours
  } catch {
    showToast('vCenter 自動匯入設定載入失敗', 'error')
  } finally {
    vcLoading.value = false
  }
}
async function saveVcAuto() {
  vcSaving.value = true
  try {
    vcHealth.value = await apiFetch<VcHealth>('/api/vcenter-autoimport', {
      method: 'PUT',
      body: { enabled: vc.enabled, dir: vc.dir.trim(), max_age_hours: Number(vc.max_age_hours) },
    })
    showToast('已儲存自動匯入設定', 'success')
  } catch (err: any) {
    showToast(`儲存失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    vcSaving.value = false
  }
}
// 這支在搬元件時漏帶（樣板的「複製」按鈕還在呼叫它）。
// build 過、SSR 也不會炸，因為只有按下去才觸發——是 ESLint 的
// vue/no-undef-properties 掃出來的（2026-08-15）。
async function copyVcCmd() {
  try {
    await navigator.clipboard.writeText(vcExportCmd.value)
    showToast('已複製匯出指令', 'success')
  } catch {
    showToast('複製失敗，請手動選取', 'warn')
  }
}

const emit = defineEmits<{ imported: [] }>()
async function runVcAutoNow() {
  vcRunning.value = true
  try {
    const r = await apiFetch<any>('/api/vcenter-autoimport/run', { method: 'POST' })
    const MAP: Record<string, string> = {
      imported: r.line ? `已匯入最新檔：${r.line}` : '已匯入最新檔',
      already_current: '最新的那份已經匯過了，沒有新檔',
      no_file: '資料夾裡目前沒有可用的匯出檔',
      no_dir: '資料夾沒設定或不存在',
      error: `匯入失敗：${r.error ?? ''}`,
    }
    showToast(MAP[r.status] ?? r.status, r.status === 'error' ? 'error' : 'info')
    if (r.status === 'imported') emit('imported')
    await loadVcAuto()
  } catch (err: any) {
    showToast(`執行失敗：${err?.data?.detail ?? '請稍後重試'}`, 'error')
  } finally {
    vcRunning.value = false
  }
}
onMounted(loadVcAuto)
</script>

<template>
  <div class="card">
      <div class="ao-head">
        <div>
          <div class="card-title">vCenter 自動匯入</div>
          <p class="credhint" style="margin:6px 0 0">
            一台<b>常開的 Windows</b> 每晚用 <b>RVTools</b> 把 vCenter 盤點匯出到一個資料夾，
            系統就從那個資料夾<b>自動抓最新的檔</b>收進資產——我們不直接連 vCenter、不存它的帳號，
            改版風險交給 RVTools 扛。
          </p>
        </div>
        <label class="ao-switch" :class="{ on: vc.enabled }">
          <input type="checkbox" v-model="vc.enabled" />
          <span class="ao-switch-track"><span class="ao-switch-knob" /></span>
          <span class="ao-switch-label">{{ vc.enabled ? '自動匯入已啟用' : '自動匯入已關閉' }}</span>
        </label>
      </div>

      <div v-if="vcHealth && vcHealth.status !== 'off'" class="bk-hero" :class="vcHealth.status"
           style="margin-top:14px">
        <div class="bk-lamp" :class="vcHealth.status" />
        <div class="bk-hero-text">
          <div class="bk-verdict">匯入鮮度 · {{ VC_LAMP_LABEL[vcHealth.status] }}</div>
          <div class="bk-sub">{{ vcHealth.reason }}</div>
          <div v-if="vcHealth.last_at" class="bk-sub">
            上次匯入：{{ vcHealth.last_at }}（{{ vcHealth.last_result || '—' }}）
          </div>
        </div>
        <div class="bk-actions">
          <button class="btn" type="button" :disabled="vcRunning" @click="runVcAutoNow">
            {{ vcRunning ? '抓取中…' : '立即抓一次' }}
          </button>
        </div>
      </div>

      <div class="credform" style="margin-top:16px">
        <label>監看資料夾（伺服器看得到的路徑或共享）
          <input v-model="vc.dir" placeholder="例：D:\vcenter_export 或 \\NAS\vcenter" />
        </label>
        <label>逾時門檻（小時）
          <input v-model="vc.max_age_hours" type="number" min="1" />
        </label>
      </div>
      <div class="actions">
        <button class="btn" :disabled="vcSaving" @click="saveVcAuto">
          {{ vcSaving ? '儲存中…' : '儲存設定' }}
        </button>
        <button class="btn ghost" :disabled="vcRunning" @click="runVcAutoNow">
          {{ vcRunning ? '抓取中…' : '立即抓一次' }}
        </button>
      </div>

      <div class="card-title" style="margin-top:20px">那台 Windows 要設定的排程匯出</div>
      <p class="credhint" style="margin:0 0 8px">
        在那台常開的 Windows 開「工作排程器」，設一個每晚執行的工作，動作填下面這行
        （把 vCenter 位址、唯讀帳號、密碼換成實際值；資料夾要跟上面設的一致）：
      </p>
      <div class="vc-cmd">
        <code>{{ vcExportCmd }}</code>
        <button class="btn ghost small" @click="copyVcCmd">複製</button>
      </div>
      <p class="credhint" style="margin:8px 0 0">
        需先在那台裝 <b>RVTools</b>（VMware 官方認可的免費工具）。設好後每晚它自己匯出、
        系統自己抓，你完全不用手動——這一頁只是給你看「今晚到底收到了沒」。
      </p>
  </div>
</template>

<style scoped>
.credhint { font-size: 11.5px; color: var(--muted); line-height: 1.7; margin: 0 0 14px; }
.credhint code { color: var(--brand-dark); }
.credhint b { color: var(--ink-soft); }
.ao-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; flex-wrap: wrap; }
.ao-switch { display: inline-flex; align-items: center; gap: 9px; cursor: pointer; flex-shrink: 0; }
.ao-switch input { position: absolute; opacity: 0; width: 0; height: 0; }
.ao-switch-track {
  width: 44px; height: 24px; border-radius: 999px; background: var(--line, #33414f);
  position: relative; transition: background 0.18s; flex-shrink: 0;
}
.ao-switch-knob {
  position: absolute; top: 3px; left: 3px; width: 18px; height: 18px; border-radius: 50%;
  background: #fff; transition: transform 0.18s;
}
.ao-switch.on .ao-switch-track { background: var(--brand); }
.ao-switch.on .ao-switch-knob { transform: translateX(20px); }
.ao-switch-label { font-size: 12.5px; color: var(--ink-soft, #cbd5e1); font-weight: 600; }
.ao-run {
  display: flex; justify-content: space-between; align-items: center; gap: 20px; flex-wrap: wrap;
  margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--line, #2a3642);
}
.ao-result { margin-top: 12px; font-size: 13px; color: var(--ink-soft, #cbd5e1); }
.ao-result b { color: var(--brand-dark); }
.ao-detail { margin: 8px 0 0; padding-left: 18px; font-size: 12px; line-height: 1.75; }
.ao-detail .ok { color: var(--brand-dark); font-weight: 700; }
.ao-detail .bad { color: var(--bad); font-weight: 700; }
.ao-detail .dim { color: var(--muted); }
</style>
