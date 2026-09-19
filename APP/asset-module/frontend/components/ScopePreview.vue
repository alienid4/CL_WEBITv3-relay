<script setup lang="ts">
// [B-08] 動作前預覽：「這個動作會改到這 N 筆」（2026-09-18）。
// 下線、豁免以前只改畫面上選的那一筆；一台常有好幾筆登記（每個服務／VIP 一筆），
// 改一筆等於沒改。現在動作前先把「真的會改到哪些」攤開給人看，確認了才做。
interface Row {
  asset_serial: string; hostname: string | null; ip: string | null
  asset_name: string | null; asset_purpose: string | null; api_id: string | null; asset_status: string | null
}
const props = defineProps<{ serials: string[]; scope: 'single' | 'machine' }>()
const emit = defineEmits<{ (e: 'count', n: number | null): void }>()

const { apiFetch } = useApi()
const rows = ref<Row[] | null>(null)
const err = ref('')

async function load() {
  rows.value = null
  err.value = ''
  emit('count', null)
  try {
    const r = await apiFetch<{ rows: Row[]; count: number }>('/api/assets/scope-preview',
      { method: 'POST', body: { serials: props.serials, scope: props.scope } })
    rows.value = r.rows
    emit('count', r.count)
  } catch (e: any) {
    err.value = `預覽載入失敗：${e?.data?.detail ?? e?.message ?? e}`
  }
}
watch(() => [props.scope, props.serials.join('|')], load, { immediate: true })
</script>

<template>
  <div class="sp">
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="!rows" class="dim">計算會改到哪幾筆…</p>
    <template v-else>
      <p class="sp-h">這個動作會改到 <b>{{ rows.length }}</b> 筆登記：</p>
      <div class="sp-list">
        <table>
          <thead><tr><th>資產序號</th><th>主機名</th><th>IP</th><th>名稱／用途</th><th>目前狀態</th></tr></thead>
          <tbody>
            <tr v-for="r in rows" :key="r.asset_serial">
              <td class="mono">{{ r.asset_serial }}</td>
              <td>{{ r.hostname || '—' }}</td>
              <td class="mono">{{ r.ip || '—' }}</td>
              <td>{{ r.asset_name || r.asset_purpose || '—' }}</td>
              <td>{{ r.asset_status || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.sp { margin: 4px 0 12px; }
.sp-h { font-size: 13px; margin: 0 0 6px; }
.sp-list { max-height: 200px; overflow: auto; border: 1px solid var(--border); border-radius: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { padding: 3px 8px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; }
th { position: sticky; top: 0; background: var(--card); color: var(--ink-soft); }
.mono { font-family: ui-monospace, monospace; }
.dim { font-size: 12px; color: var(--muted); }
.err { font-size: 12px; color: #b91c1c; }
</style>
