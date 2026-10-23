<script setup lang="ts">
// 主機清單（以台為單位，2026-09-18）。資產查詢是逐「筆」；這頁以 machine_key 去重、
// 一台一列，點開看同一台的多重登記（別名：不同序號/資產名稱/AP ID）。
// 台數＝首頁「在管 N 台」（同母體：排退役＋帳外）。
interface Reg {
  asset_serial: string; asset_name: string | null; asset_purpose: string | null
  api_id: string | null; environment: string | null
}
interface Host {
  machine_key: string; asset_serial: string; hostname: string | null; ip: string | null
  location: string | null; environment: string | null; os: string | null
  device_model: string | null; is_vm: boolean; asset_status: string | null
  reg_count: number; system_count: number; systems: string[]; registrations: Reg[]
  vip_count: number; service_count: number; account_count: number
}
interface Resp {
  hosts: Host[]; total_hosts: number; total_rows: number; multi_reg_hosts: number
  loose_candidate_hosts: number
}

const { apiFetch } = useApi()
const { showToast } = useToast()

const data = ref<Resp | null>(null)
const loading = ref(true)
const q = ref('')
const roomSel = ref<Set<string>>(new Set())
const envSel = ref<Set<string>>(new Set())
const onlyMulti = ref(false)
const openKey = ref('')

async function load() {
  loading.value = true
  try {
    data.value = await apiFetch<Resp>('/api/hosts')
  } catch (e: any) {
    showToast(`主機清單載入失敗：${e?.data?.detail ?? e?.message}`, 'error')
  } finally {
    loading.value = false
  }
}
onMounted(load)

const hosts = computed(() => data.value?.hosts ?? [])
const rooms = computed(() =>
  [...new Set(hosts.value.map((h) => h.location).filter(Boolean))].sort() as string[])
const envs = computed(() =>
  [...new Set(hosts.value.map((h) => h.environment).filter(Boolean))].sort() as string[])

function togRoom(v: string) {
  const s = new Set(roomSel.value); s.has(v) ? s.delete(v) : s.add(v); roomSel.value = s
}
function togEnv(v: string) {
  const s = new Set(envSel.value); s.has(v) ? s.delete(v) : s.add(v); envSel.value = s
}

const filtered = computed(() => {
  const needle = q.value.trim().toLowerCase()
  return hosts.value.filter((h) => {
    if (roomSel.value.size && !roomSel.value.has(h.location ?? '')) return false
    if (envSel.value.size && !envSel.value.has(h.environment ?? '')) return false
    if (onlyMulti.value && h.reg_count <= 1) return false
    if (!needle) return true
    return [h.hostname, h.ip, h.location, h.os, h.device_model, ...h.systems,
      ...h.registrations.map((r) => r.asset_name), ...h.registrations.map((r) => r.asset_serial)]
      .some((v) => String(v ?? '').toLowerCase().includes(needle))
  })
})
const { sortKey, sortDir, toggle, sorted } = useSort(filtered, 'reg_count', 'desc')

// 匯出目前（已篩選/排序）清單為 CSV，一台一列，多重登記的序號/名稱/AP ID 用「｜」串在同格。
// 純前端（用畫面上已載入的資料），不另打 API；UTF-8 BOM 讓 Excel 直接開不亂碼。
function exportCsv() {
  const head = ['主機名', 'IP', '機房', '環境', '虛實', 'OS', '系統數', 'VIP', '服務', '帳號',
    '登記筆數', '系統(AP ID)', '多重登記(序號｜名稱)']
  const esc = (v: any) => {
    const s = String(v ?? '')
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
  }
  const lines = [head.join(',')]
  for (const h of sorted.value) {
    lines.push([
      h.hostname, h.ip, h.location, h.environment, h.is_vm ? 'VM' : '實體', h.os,
      h.system_count, h.vip_count, h.service_count, h.account_count, h.reg_count, h.systems.join('｜'),
      h.registrations.map((r) => `${r.asset_serial}:${r.asset_name || r.asset_purpose || ''}`).join('｜'),
    ].map(esc).join(','))
  }
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `主機清單_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}

function toggleOpen(h: Host) {
  openKey.value = openKey.value === h.machine_key ? '' : h.machine_key
}
</script>

<template>
  <div>
    <div class="section-divider">盤點</div>
    <div class="breadcrumb-bar"><span class="pin">📌</span> <b>主機清單（以台為單位）</b>
      <InfoNote>資產查詢是一筆一列（同一台被多系統／多 VIP 各登記一筆會出現多列）。這頁以「主機名＋IP」去重、<b>一台一列</b>，點該台可展開它的多重登記（別名：各自的資產序號／名稱／AP ID）。台數跟首頁「在管 N 台」同口徑（排退役、排帳外）。同一個 IP 但主機名不同的（多半是閘道）不會被併成一台。</InfoNote>
    </div>

    <div v-if="data && data.loose_candidate_hosts > 0" class="susp-bar">
      <span>⚠ 有 <b>{{ data.loose_candidate_hosts }}</b> 台缺主機名或 IP，但放寬比對後找得到疑似同一台的機器——系統<b>不會自動合併</b>，要不要認定同一台由你決定。</span>
      <NuxtLink class="susp-go" to="/reports/host-sources">去確認 →</NuxtLink>
    </div>

    <div class="card">
      <div class="stats" v-if="data">
        <span><b>{{ data.total_hosts }}</b> 台</span>
        <span>登記 <b>{{ data.total_rows }}</b> 筆</span>
        <span>有多重登記 <b class="hi">{{ data.multi_reg_hosts }}</b> 台</span>
        <button class="btn-exp" type="button" :disabled="!sorted.length" @click="exportCsv">⬇ 匯出 CSV（{{ sorted.length }}）</button>
      </div>

      <div class="filters">
        <input v-model="q" class="search" type="search" placeholder="搜主機名／IP／系統／資產名稱…" />
        <div class="qrow" v-if="rooms.length">
          <span class="qlbl">機房</span>
          <button v-for="r in rooms" :key="r" class="chip" :class="{ on: roomSel.has(r) }" @click="togRoom(r)">{{ r }}</button>
          <button v-if="roomSel.size" class="chip clr" @click="roomSel = new Set()">清除</button>
        </div>
        <div class="qrow" v-if="envs.length">
          <span class="qlbl">環境</span>
          <button v-for="e in envs" :key="e" class="chip" :class="{ on: envSel.has(e) }" @click="togEnv(e)">{{ e }}</button>
          <button v-if="envSel.size" class="chip clr" @click="envSel = new Set()">清除</button>
        </div>
        <label class="ck"><input v-model="onlyMulti" type="checkbox" /> 只看有多重登記的</label>
      </div>

      <p v-if="loading" class="muted">載入中…</p>
      <div v-else class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th class="exp"></th>
              <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名</SortTh>
              <SortTh k="ip" :active="sortKey" :dir="sortDir" @sort="toggle">IP</SortTh>
              <SortTh k="location" :active="sortKey" :dir="sortDir" @sort="toggle">機房</SortTh>
              <SortTh k="environment" :active="sortKey" :dir="sortDir" @sort="toggle">環境</SortTh>
              <SortTh k="is_vm" :active="sortKey" :dir="sortDir" @sort="toggle">虛實</SortTh>
              <SortTh k="os" :active="sortKey" :dir="sortDir" @sort="toggle">OS</SortTh>
              <SortTh k="system_count" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">系統數</SortTh>
              <SortTh k="vip_count" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">VIP</SortTh>
              <SortTh k="service_count" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">服務</SortTh>
              <SortTh k="account_count" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">帳號</SortTh>
              <SortTh k="reg_count" :active="sortKey" :dir="sortDir" class="num" @sort="toggle">登記筆數</SortTh>
            </tr>
          </thead>
          <tbody>
            <template v-for="h in sorted" :key="h.machine_key">
              <tr :class="{ clickable: h.reg_count > 1, on: openKey === h.machine_key, multi: h.reg_count > 1 }"
                  @click="h.reg_count > 1 && toggleOpen(h)">
                <td class="exp">
                  <span v-if="h.reg_count > 1" class="caret">{{ openKey === h.machine_key ? '▾' : '▸' }}</span>
                </td>
                <td>
                  <NuxtLink :to="`/assets/${h.asset_serial}`" class="hn" @click.stop>{{ h.hostname || '（未登記主機名）' }}</NuxtLink>
                </td>
                <td class="mono">{{ h.ip || '—' }}</td>
                <td>{{ h.location || '—' }}</td>
                <td><span v-if="h.environment" class="tag" :class="h.environment === '測試' ? 'test' : 'prod'">{{ h.environment }}</span><span v-else class="muted">—</span></td>
                <td><span class="tag" :class="h.is_vm ? 'vm' : 'phy'">{{ h.is_vm ? 'VM' : '實體' }}</span></td>
                <td class="ell" :title="h.os || ''">{{ h.os || '—' }}</td>
                <td class="num mono">{{ h.system_count || '—' }}</td>
                <td class="num mono">{{ h.vip_count || '—' }}</td>
                <td class="num mono">{{ h.service_count || '—' }}</td>
                <td class="num mono">{{ h.account_count || '—' }}</td>
                <td class="num mono"><b :class="{ hi: h.reg_count > 1 }">{{ h.reg_count }}</b></td>
              </tr>
              <tr v-if="openKey === h.machine_key" class="drill">
                <td></td>
                <td colspan="11">
                  <div class="alias-hd">這台的多重登記（{{ h.reg_count }} 筆，像同一台的多個外號）</div>
                  <div class="alias-list">
                    <NuxtLink v-for="r in h.registrations" :key="r.asset_serial"
                              :to="`/assets/${r.asset_serial}`" class="alias-item">
                      <b class="mono">{{ r.asset_serial }}</b>
                      <span>{{ r.asset_name || r.asset_purpose || '（未填名稱）' }}</span>
                      <span v-if="r.api_id" class="ap mono">{{ r.api_id }}</span>
                    </NuxtLink>
                  </div>
                </td>
              </tr>
            </template>
            <tr v-if="!sorted.length"><td colspan="12" class="muted">沒有符合的主機</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.section-divider { margin: 0 0 16px; font-size: 11px; color: var(--brand-dark);
  font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }
.breadcrumb-bar { background: var(--mint); border: 1px solid var(--border-strong);
  padding: 8px 14px; font-size: 12.5px; color: var(--ink-soft); display: flex;
  align-items: center; gap: 8px; margin-bottom: 14px; }
.breadcrumb-bar b { color: var(--brand-dark); }
.susp-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  background: var(--warn-soft); border: 1px solid var(--warn); color: var(--warn-text);
  padding: 9px 14px; font-size: 12.5px; border-radius: 8px; margin-bottom: 14px; }
.susp-bar b { color: var(--warn-text); }
.susp-go { margin-left: auto; white-space: nowrap; font-weight: 600; color: var(--brand-dark);
  text-decoration: none; border-bottom: 1px solid var(--brand); }
.susp-go:hover { color: var(--brand); }
.card { border: 1px solid var(--border); background: var(--card); padding: 16px; margin-bottom: 16px; border-radius: var(--radius, 14px); }
.stats { display: flex; gap: 18px; font-size: 13px; color: var(--muted); margin-bottom: 12px; }
.stats b { color: var(--ink); }
.hi { color: var(--warn-text); }
.btn-exp { margin-left: auto; font-family: inherit; font-size: 12px; padding: 5px 12px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--brand-dark);
  border-radius: 8px; cursor: pointer; }
.btn-exp:hover { border-color: var(--brand); background: var(--mint); }
.btn-exp:disabled { opacity: .5; cursor: not-allowed; }
.filters { display: flex; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; align-items: center; }
.search { min-width: 240px; font-family: inherit; font-size: 12.5px; padding: 6px 10px;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 6px; }
.qrow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.qlbl { font-size: 12px; color: var(--muted); }
.chip { font-family: inherit; font-size: 12px; padding: 4px 11px; cursor: pointer;
  border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); border-radius: 999px; }
.chip.on { background: var(--mint); border-color: var(--brand); color: var(--brand-dark); font-weight: 600; }
.chip.clr { color: var(--muted); border-style: dashed; }
.ck { font-size: 12.5px; display: inline-flex; align-items: center; gap: 5px; }
.muted { color: var(--muted); font-size: 12.5px; }
.tbl-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; min-width: 820px; }
th, td { text-align: left; padding: 8px 11px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--ink-soft); font-weight: 700; font-size: 12px; background: var(--mint); position: sticky; top: 0; }
.num { text-align: right; }
.exp { width: 26px; text-align: center; }
.caret { color: var(--brand); }
.clickable { cursor: pointer; }
.clickable:hover td { background: var(--mint); }
.multi td { background: rgba(0,128,106,.03); }
.on td { background: var(--mint); }
.hn { color: var(--ink); font-weight: 600; text-decoration: none; }
.hn:hover { color: var(--brand-dark); text-decoration: underline; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.ell { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tag { font-size: 10.5px; padding: 1px 7px; border: 1px solid var(--border-strong); border-radius: 6px; }
.tag.prod { border-color: var(--brand); color: var(--brand-dark); }
.tag.test { color: var(--muted); }
.tag.vm { color: var(--brand-dark); border-color: var(--brand); }
.tag.phy { color: var(--ink-soft); }
.drill td { background: rgba(0,0,0,.02); }
.alias-hd { font-size: 12px; color: var(--ink-soft); margin-bottom: 6px; font-weight: 600; }
.alias-list { display: flex; flex-wrap: wrap; gap: 6px; }
.alias-item { display: inline-flex; align-items: baseline; gap: 7px; padding: 4px 10px;
  border: 1px solid var(--border-strong); border-radius: 8px; background: var(--card);
  font-size: 12px; text-decoration: none; color: var(--ink); }
.alias-item:hover { border-color: var(--brand); background: var(--mint); }
.alias-item b { color: var(--brand-dark); }
.ap { font-size: 11px; color: var(--muted); }
</style>
