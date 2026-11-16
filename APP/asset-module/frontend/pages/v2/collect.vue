<script setup lang="ts">
// 6.2 設備收集（2.x 第三批）：SAN 交換器｜HMC。收集動作（帳密當場輸入、只跑唯讀指令）屬送到目標設備執行的範圍，
// 同一組 API，不改任何收集邏輯。
definePageMeta({ layout: 'v2' })
// HMC（V35）：2.048.8 起 V2 原生（V2Hmc）；SAN（V52）：2.048.12 起 V2 原生（V2San）。都不再嵌入舊頁
// 2.066 畫面重看：頁籤只留名稱，台數移到頁首一行結論＋「要你處理」（未收集台數，點「查看」切到該頁籤）
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const n = reactive<{ san: any; hmc: any }>({ san: null, hmc: null })
try { n.san = await apiFetch('/api/san/switches') } catch { n.san = null /* 結論讀不到時不顯示該段，內容區會自己顯示錯誤 */ }
try { n.hmc = await apiFetch('/api/hmc/consoles') } catch { n.hmc = null /* 同上 */ }
const TABS = computed(() => [
  { k: 'san', label: `SAN 交換器` },
  { k: 'hmc', label: `HMC` }])
const tab = computed({ get: () => (route.query.tab === 'hmc' ? 'hmc' : 'san'), set: (v: string) => router.replace({ query: { tab: v } }) })
const left = (x: any) => (x ? Math.max(0, (x.total ?? 0) - (x.collected ?? 0)) : 0)
const concl = computed(() => [
  n.san ? `SAN 已收集 ${n.san.collected}／${n.san.total} 台` : '',
  n.hmc ? `HMC 已收集 ${n.hmc.collected}／${n.hmc.total} 台` : ''].filter(Boolean).join('・'))
const todo = computed(() => [
  { k: 'san', n: left(n.san), text: `SAN 交換器 ${left(n.san)} 台未收集` },
  { k: 'hmc', n: left(n.hmc), text: `HMC ${left(n.hmc)} 台未收集` }].filter(t => t.n > 0))
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">6.2 設備收集</h1></template>
    <template #conclusion>
      <span v-if="concl" data-testid="collect-concl">{{ concl }}</span>
      <InfoNote>SAN 交換器（Brocade）與 HMC（IBM Power）不建帳號、不納管：帳號密碼當場輸入、只在記憶體、收完即清，只跑唯讀查詢指令，不改設備設定。</InfoNote>
    </template>
    <template #tab>
      <div class="todo" data-testid="collect-todo"><b>要你處理</b>
        <div v-for="t in todo" :key="t.k" class="it"><span class="sp">{{ t.text }}</span><NuxtLink class="pbtn" :to="{ query: { tab: t.k } }">查看</NuxtLink></div>
        <div v-if="!todo.length" class="it muted">沒有要你處理的事</div></div>
      <ClientOnly>
        <V2San v-if="tab === 'san'" />
        <V2Hmc v-else />
        <template #fallback><p class="muted">載入中…</p></template>
      </ClientOnly>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.muted { color: var(--muted); font-size: 13px; }
.todo { background: var(--card); border: 1px solid var(--border); border-left: 4px solid var(--warn); border-radius: 12px; padding: 8px 12px; margin: 0 0 10px; font-size: 14px; }
.todo .it { display: flex; gap: 10px; align-items: center; padding: 5px 0; border-top: 1px solid var(--line); }
.todo .sp { flex: 1; }
.pbtn { font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--link); text-decoration: none; display: inline-block; }
</style>
