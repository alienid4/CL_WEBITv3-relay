<script setup lang="ts">
// ① 主機：勾選要傳檔／下指令的主機（只列已納管、現役、平台明確的；VMware 設備不會出現）。
// 勾選結果在父元件，切到別的頁籤也還在；一次只對一個箱子（Windows 或 Linux 含 AIX）動作。
import type { FtHost } from '~/types/fileTransfer'
const props = defineProps<{ hosts: FtHost[]; box: 'windows' | 'linux'; excluded: Record<string, number>; picked: Record<string, boolean> }>()
const emit = defineEmits<{ 'update:picked': [v: Record<string, boolean>] }>()
const os = ref('')
const env = ref('')
const room = ref('')
const kw = ref('')
const osOptions = computed(() => [...new Set(props.hosts.map(h => h.os_label))].sort())
const envOptions = computed(() => [...new Set(props.hosts.map(h => h.env))].sort())
const roomOptions = computed(() => [...new Set(props.hosts.map(h => h.room))].sort())
const shown = computed(() => props.hosts.filter(h =>
  (!os.value || h.os_label === os.value) && (!env.value || h.env === env.value) && (!room.value || h.room === room.value)
  && (!kw.value.trim() || h.ip.startsWith(kw.value.trim()) || h.hostname.toLowerCase().includes(kw.value.trim().toLowerCase()))))
const model = computed({ get: () => props.picked, set: v => emit('update:picked', v) })
const pickedHere = computed(() => props.hosts.filter(h => props.picked[h.ip]).length)
function pickAll(v: boolean) {
  const n = { ...props.picked }
  for (const h of shown.value) n[h.ip] = v
  emit('update:picked', n)
}
const cols = [
  { k: 'hostname', label: '主機名' }, { k: 'ip', label: 'IP', mono: true }, { k: 'os_label', label: '系統' },
  { k: 'env', label: '環境' }, { k: 'room', label: '機房' },
]
</script>

<template>
  <div class="card">
    <div class="row">
      <b>勾選主機</b>
      <InfoNote>VMware 設備（ESXi、vCenter）不會出現在這裡，也不能被選。表格可排序（點欄位標題）。傳檔與指令都對這裡勾選的主機執行；一次只能對一個箱子動作。已排除：VMware {{ excluded.vmware ?? 0 }} 台、平台不明 {{ excluded.platform_unknown ?? 0 }} 台、Windows 未勾選納管 {{ excluded.not_opted_in ?? 0 }} 台。</InfoNote>
    </div>
    <div class="filters">
      <select v-model="os"><option value="">系統：全部</option><option v-for="o in osOptions" :key="o" :value="o">{{ o }}</option></select>
      <select v-model="env"><option value="">環境：全部</option><option v-for="o in envOptions" :key="o" :value="o">{{ o }}</option></select>
      <select v-model="room"><option value="">機房：全部</option><option v-for="o in roomOptions" :key="o" :value="o">{{ o }}</option></select>
      <input v-model="kw" type="text" placeholder="IP 開頭／主機名" aria-label="搜尋主機">
      <button type="button" class="btn ghost" @click="pickAll(true)">勾選目前篩選的 {{ shown.length }} 台</button>
      <button type="button" class="btn ghost" @click="pickAll(false)">取消這些</button>
      <span class="muted">已勾選 {{ pickedHere }} 台（共 {{ hosts.length }} 台，符合篩選 {{ shown.length }} 台）</span>
    </div>
    <V2Table v-model:selected="model" :rows="shown" :cols="cols" row-key="ip" selectable prefix="H" empty="這個箱子沒有可選的主機" unit="台" />
  </div>
</template>

<style scoped>
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); padding: 14px 16px; }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
.muted { color: var(--muted); font-size: 13px; }
select, input[type=text] { font: inherit; font-size: 13px; padding: 4px 8px; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--card); color: var(--ink); }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 12px; font: inherit; font-size: 13px; cursor: pointer; }
.btn.ghost { background: var(--card); color: var(--brand-dark); }
</style>
