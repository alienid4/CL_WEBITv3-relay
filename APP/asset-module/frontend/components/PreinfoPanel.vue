<script setup lang="ts">
// 2-1「納管前資訊」（v1.468.7，2026-10-19 使用者：怕把網路設備、或不是 RHEL／CentOS／Debian 的納管進來）。
// 把這台我們**已經知道**的線索攤開：只讀資料庫，不碰網路、不登入、不改任何東西。查不到就寫「沒有」。
const props = defineProps<{ ip: string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'pick-tag', tag: string): void; (e: 'precheck', ip: string): void }>()
const { apiFetch } = useApi()

const d = ref<any>(null)
const err = ref('')
onMounted(async () => {
  try { d.value = await apiFetch<any>('/api/adopt/preinfo', { params: { ip: props.ip } }) }
  catch (e: any) { err.value = e?.data?.detail ?? e?.message ?? '讀取失敗' }
})
const NONE = '沒有'
const v = (x: any) => (x === null || x === undefined || x === '' ? NONE : String(x))
const BANNER_CLS: Record<string, string> = { device: 'bad', windows: 'warn', linux_like: 'mid', unknown: 'mid' }
</script>

<template>
  <div class="modal-mask">
    <div class="panel">
      <div class="phead">
        <span>ⓘ 納管前資訊 — <span class="mono">{{ ip }}</span></span>
        <InfoNote>這台我們已經知道的全部線索：網段掃描、2-1 收集結果、資產登記、納管紀錄、標籤，以及系統的判斷。<b>只讀資料，不連線、不登入、不改任何東西</b>。要登進去看發行版與權限，用「預檢」。</InfoNote>
        <button class="btn ghost small" title="唯讀登入一次看作業系統、權限、設備標記與能不能納管——不改任何東西（要帳密）"
                @click="emit('precheck', ip)">🔍 預檢（只看不動）</button>
        <button class="btn ghost small" @click="emit('close')">關閉</button>
      </div>
      <p v-if="err" class="bad">讀取失敗：{{ err }}</p>
      <p v-else-if="!d" class="muted">讀取中…</p>
      <template v-else>
        <section>
          <h4>判斷</h4>
          <div class="kv"><span>是什麼機器</span><b>{{ d.judgement.kind === 'unknown' ? '判斷不出' : d.judgement.label }}（{{ d.judgement.confidence_label }}）</b></div>
          <ul class="ev"><li v-for="(e, i) in d.judgement.evidence" :key="i">{{ e }}</li></ul>
          <div class="kv"><span>廠牌</span><b>{{ d.vendor.vendor || '廠牌不明' }}（{{ d.vendor.confidence_label }}）</b></div>
        </section>
        <section>
          <h4>SSH banner</h4>
          <template v-if="d.banner">
            <div class="kv"><span>原始</span><code>{{ v(d.banner.raw) }}</code></div>
            <div v-if="d.banner.product" class="kv"><span>自報產品</span><b>{{ d.banner.product }}</b></div>
            <div v-if="d.banner.hint" class="kv"><span>代表</span><b :class="BANNER_CLS[d.banner.hint.kind]">{{ d.banner.hint.text }}</b></div>
          </template>
          <p v-else class="muted small">沒有（沒開 22、或收集時沒拿到 banner）</p>
        </section>
        <section>
          <h4>網段掃描</h4>
          <template v-if="d.scan">
            <div class="kv"><span>掃描時間</span>{{ v(d.scan.scan_time) }}</div>
            <div class="kv"><span>反解主機名</span>{{ v(d.scan.hostname) }}</div>
            <div class="kv"><span>開放埠</span>{{ v(d.scan.open_ports) }}</div>
            <div class="kv"><span>TTL</span>{{ v(d.scan.ttl) }}</div>
            <div class="kv"><span>MAC／廠商</span>{{ v(d.scan.mac) }}／{{ v(d.scan.mac_vendor) }}</div>
            <div class="kv"><span>研判</span>{{ d.scan.os_guess || '線索不足' }}</div>
          </template>
          <p v-else class="muted small">沒有（網段掃描沒掃到這個位址）</p>
        </section>
        <section>
          <h4>2-1 收集結果</h4>
          <template v-if="d.dispatch">
            <div class="kv"><span>收集時間</span>{{ v(d.dispatch.created_at) }}</div>
            <div class="kv"><span>走哪條路／結果</span>{{ d.dispatch.route_label }}／{{ d.dispatch.status_label }}</div>
            <div class="kv"><span>開放埠</span>{{ v(d.dispatch.open_ports) }}</div>
            <div class="kv msg"><span>訊息</span>{{ v(d.dispatch.message) }}</div>
          </template>
          <p v-else class="muted small">沒有（還沒收集過這個位址）</p>
        </section>
        <section>
          <h4>資產登記 <span v-if="d.registry_count > 1" class="muted small">（{{ d.registry_count }} 筆，顯示現役那筆）</span></h4>
          <template v-if="d.registry">
            <div class="kv"><span>資產序號</span><NuxtLink class="dl" :to="`/assets/${d.registry.asset_serial}`">{{ d.registry.asset_serial }}</NuxtLink></div>
            <div class="kv"><span>資產名稱</span>{{ v(d.registry.asset_name) }}</div>
            <div class="kv"><span>設備機型</span>{{ v(d.registry.device_model) }}</div>
            <div class="kv"><span>作業系統</span>{{ v(d.registry.os) }}</div>
            <div class="kv"><span>業務系統（API ID）</span>{{ v(d.registry.api_id) }}</div>
            <div class="kv"><span>用途</span>{{ v(d.registry.asset_purpose) }}</div>
            <div class="kv"><span>擁有者／保管者／使用者</span>{{ v(d.registry.owner) }}／{{ v(d.registry.custodian) }}／{{ v(d.registry.user_name) }}</div>
            <div class="kv"><span>環境</span>{{ v(d.registry.environment) }}</div>
            <div class="kv"><span>資產狀態</span>{{ v(d.registry.asset_status) }}</div>
          </template>
          <p v-else class="muted small">沒有（資產庫沒登記這個 IP）</p>
        </section>
        <section>
          <h4>納管紀錄與標籤</h4>
          <div class="kv"><span>上次納管</span>
            <template v-if="d.last_attempt">{{ d.last_attempt.created_at }}（{{ d.last_attempt.login_user || '—' }}）
              <b v-if="d.last_attempt.fail" class="bad">失敗：{{ d.last_attempt.fail.short }}</b><b v-else class="ok">成功</b></template>
            <template v-else>沒有</template>
          </div>
          <div class="kv"><span>標籤</span>
            <template v-if="d.tags.length">
              <button v-for="t in d.tags" :key="t.tag" type="button" class="tagchip" :title="`${t.note || ''}\n${t.created_by ?? ''} 於 ${t.created_at ?? ''}`"
                      @click="emit('pick-tag', t.tag)">{{ t.tag }}</button>
            </template>
            <template v-else>{{ d.registry ? '沒有' : '沒有（沒登記，不能貼標籤）' }}</template>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: flex; justify-content: flex-end; z-index: 55; }
.panel { width: 560px; max-width: 96vw; height: 100vh; overflow: auto; background: var(--card);
  border-left: 1px solid var(--border-strong); padding: 18px 20px; }
.phead { display: flex; align-items: center; gap: 8px; font-weight: 700; color: var(--brand-dark); margin-bottom: 10px; }
.phead .btn:first-of-type { margin-left: auto; }
section { border-top: 1px solid var(--line); padding: 10px 0; }
h4 { margin: 0 0 6px; font-size: 13px; color: var(--ink); }
.kv { display: grid; grid-template-columns: 150px 1fr; gap: 8px; font-size: 12.5px; color: var(--ink-soft); padding: 2px 0; }
.kv > span:first-child { color: var(--muted); }
.kv.msg { word-break: break-all; }
.ev { margin: 4px 0 6px 18px; padding: 0; font-size: 12px; color: var(--ink-aux); }
.mono, code { font-family: ui-monospace, Consolas, monospace; }
.muted { color: var(--muted); }
.small { font-size: 11.5px; }
.bad { color: var(--bad); }
.warn { color: var(--warn-text); }
.ok { color: var(--brand-dark); }
.mid { color: var(--ink); }
.tagchip { font-family: inherit; font-size: 11px; padding: 1px 8px; margin-right: 4px; border-radius: 999px;
  border: 1px solid var(--border-strong); background: var(--sub); color: var(--ink-soft); cursor: pointer; }
.btn { font-family: inherit; font-size: 12px; font-weight: 700; padding: 6px 14px; border-radius: 6px; cursor: pointer; }
.btn.ghost { background: var(--card); border: 1px solid var(--border-strong); color: var(--ink-soft); }
</style>
