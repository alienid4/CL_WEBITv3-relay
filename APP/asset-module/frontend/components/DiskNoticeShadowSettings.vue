<script setup lang="ts">
// ⚙ 系統設定 → 通知設定 → 硬碟通知（影子模式）：只記錄「如果正式開通，今天會通知誰」，不寄信、不送 Teams。
// API：/api/daily-check/disk-notice-shadow（GET，admin）、/settings（PUT，admin）、/run（POST，admin，只寫本機表）。說明收進 InfoNote。
interface Row { __no: number; day: string; ip: string; host: string | null; disk: string; pct: number; prev_pct: number | null; reason: string; reason_text: string
  immediate: boolean; target: string; target_src: string; note: string | null; rule_ver: string }
interface Out { enabled: boolean; days: number; rows: Row[]; rule_ver: string; last_run: { at: string; day: string } | null
  summary: { total: number; owner_disks: number; no_owner: number; immediate: number; per_person: { target: string; disks: number }[] } }
const { apiFetch } = useApi()
const { showToast } = useToast()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : e?.message ?? '未知錯誤' }
const sv = ref<Out | null>(null)
const err = ref('')
const busy = ref(false)
const pick = ref<'all' | 'owner' | 'sp' | 'immediate'>('all')
async function load() {
  err.value = ''
  try { sv.value = await apiFetch<Out>('/api/daily-check/disk-notice-shadow') } catch (e: any) { sv.value = null; err.value = `載入失敗：${errMsg(e)}` }
}
onMounted(load)
async function toggle(on: boolean) {
  busy.value = true
  try { await apiFetch('/api/daily-check/disk-notice-shadow/settings', { method: 'PUT', body: { enabled: on } }); showToast(on ? '已開啟（只記錄、不寄信）' : '已關閉', 'success'); await load() } catch (e: any) { showToast(`儲存失敗：${errMsg(e)}`, 'error', 12000) } finally { busy.value = false }
}
async function runNow() {
  busy.value = true
  try {
    const r = await apiFetch<{ status: string; added?: number }>('/api/daily-check/disk-notice-shadow/run', { method: 'POST' })
    showToast(r.status === 'ok' ? `已試算，新增 ${r.added ?? 0} 筆（不會寄信）` : `沒有試算：${({ disabled: '尚未開啟', no_data: '還沒有 WhatsUp 磁碟資料', stale: '最新資料太舊' } as Record<string, string>)[r.status] ?? r.status}`, r.status === 'ok' ? 'success' : 'warn')
    await load()
  } catch (e: any) { showToast(`試算失敗：${errMsg(e)}`, 'error', 12000) } finally { busy.value = false }
}
const rows = computed(() => (sv.value?.rows ?? []).filter(r => pick.value === 'all' || (pick.value === 'immediate' ? r.immediate : pick.value === 'sp' ? r.target_src === '對不到→SP' && !r.immediate : r.target_src !== '對不到→SP' && !r.immediate)))
const cols = [
  { k: 'day', label: '資料日', mono: true },
  { k: 'host', label: '主機', text: (r: Row) => r.host || '—' },
  { k: 'ip', label: 'IP', mono: true },
  { k: 'disk', label: '硬碟' },
  { k: 'pct', label: '使用率', mono: true, text: (r: Row) => `${r.pct}%` },
  { k: 'prev_pct', label: '前次', mono: true, text: (r: Row) => (r.prev_pct == null ? '—' : `${r.prev_pct}%`) },
  { k: 'reason_text', label: '原因' },
  { k: 'target', label: '預計通知' },
  { k: 'target_src', label: '對象來源' },
  { k: 'note', label: '診斷', brief: (r: Row) => r.note || '—' },
  { k: 'rule_ver', label: '規則版本', mono: true },
]
</script>

<template>
  <div class="panel" data-testid="disk-shadow">
    <div class="hd"><b>硬碟通知（影子模式）</b>
      <InfoNote>只記錄、不寄信：每天 07:00 後用 WhatsUp 中繼的磁碟資料算一次，列出「如果正式開通，今天會通知誰」。規則：當天最高使用率剛跨過 90%（前一天沒到）＝通知負責人（CIA 負責人，沒有就保管者，信箱查不到或對不到就改 SP）；≥95% 或照最近 7 天趨勢 3 天內會滿＝即時通知 SP。同一天同一台同一顆硬碟只記一筆。觀察約一週，確認人選與封數合理後，才討論要不要正式寄。開關預設關閉；只有管理員能看與改。</InfoNote>
    </div>
    <p v-if="err" class="err" data-testid="shadow-err">{{ err }}</p>
    <template v-if="sv">
      <div class="acts">
        <label class="sw"><input type="checkbox" :checked="sv.enabled" :disabled="busy" data-testid="shadow-switch" @change="toggle(($event.target as HTMLInputElement).checked)"> 影子模式{{ sv.enabled ? '：開啟中' : '：關閉' }}</label>
        <button class="btn small" :disabled="busy || !sv.enabled" data-testid="shadow-run" @click="runNow">立即試算</button>
        <span class="mono">{{ sv.last_run ? `上次算：${sv.last_run.at}（資料日 ${sv.last_run.day}）` : '還沒算過' }}</span>
      </div>
      <div class="cards">
        <button type="button" class="kc" :class="{ on: pick === 'all' }" data-testid="k-all" @click="pick = 'all'"><b>{{ sv.summary.total }}</b><span>近 {{ sv.days }} 天預計通知（顆）</span></button>
        <button type="button" class="kc" :class="{ on: pick === 'owner' }" data-testid="k-owner" @click="pick = 'owner'"><b>{{ sv.summary.owner_disks }}</b><span>通知負責人（顆）</span></button>
        <button type="button" class="kc" :class="{ on: pick === 'sp' }" data-testid="k-sp" @click="pick = 'sp'"><b>{{ sv.summary.no_owner }}</b><span>對不到負責人→SP（顆）</span></button>
        <button type="button" class="kc" :class="{ on: pick === 'immediate' }" data-testid="k-imm" @click="pick = 'immediate'"><b>{{ sv.summary.immediate }}</b><span>即時類→SP（顆）</span></button>
      </div>
      <p v-if="sv.summary.per_person.length" class="pp" data-testid="per-person">
        <span v-for="p in sv.summary.per_person" :key="p.target" class="chip">{{ p.target }} {{ p.disks }} 顆</span>
      </p>
      <V2Table :rows="rows" :cols="cols" prefix="D" empty="沒有預計通知" export-name="硬碟通知影子模式" />
    </template>
  </div>
</template>

<style scoped>
.panel { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
.hd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.acts { display: flex; gap: 12px; flex-wrap: wrap; margin: 8px 0; align-items: center; font-size: 13px; color: var(--ink-soft); }
.sw { display: flex; align-items: center; gap: 6px; }
.mono { font-family: var(--disp); font-size: 12.5px; color: var(--ink-aux); }
.cards { display: flex; gap: 10px; flex-wrap: wrap; margin: 8px 0; }
.kc { font-family: inherit; display: flex; flex-direction: column; gap: 2px; min-width: 130px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--sub); color: var(--ink); cursor: pointer; text-align: left; }
.kc b { font-family: var(--disp); font-size: 20px; }
.kc span { font-size: 12px; color: var(--ink-aux); }
.kc.on { border-color: var(--brand); background: var(--mint); }
.pp { display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0; font-size: 12.5px; }
.chip { padding: 2px 10px; border-radius: 16px; background: var(--mint); color: var(--brand-dark); }
.btn { font-family: inherit; font-size: 12.5px; font-weight: 600; padding: 6px 14px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink); cursor: pointer; }
.btn:hover:not(:disabled) { border-color: var(--brand); color: var(--brand-dark); }
.err { color: var(--bad); }
</style>
