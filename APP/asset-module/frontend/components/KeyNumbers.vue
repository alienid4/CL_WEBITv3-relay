<script setup lang="ts">
// [B-09] 漏斗頂端 5 個關鍵數字（總數／已納管／失聯／未涵蓋／AIX），2026-09-18。
// 每個數字：
//  ・ⓘ 滑過（或點一下釘住）看得到「怎麼算、資料來源、已知限制、零的意義」
//  ・點數字 → 下鑽到那幾台（下面的表會套同一組條件，筆數要跟數字一樣）
//  ・是 0 的時候直接講「查過真的沒有」還是「沒查」——0 不能讓人猜
// 說明文字由後端 number_proof.EXPLAIN 給，畫面不自己寫一份（兩份會對不上）。
interface KeyNum {
  key: string; label: string; value: number
  how: string; source: string; limits: string; zero: { checked: boolean; why: string; text: string | null }
}
defineProps<{ items: KeyNum[]; active?: string }>()
const emit = defineEmits<{ (e: 'drill', key: string): void }>()
const pinned = ref('')
</script>

<template>
  <div class="kn">
    <div v-for="n in items" :key="n.key" class="kn-c" :class="{ on: active === n.key }">
      <button type="button" class="kn-n mono" :title="`點一下列出這 ${n.value} 台`" @click="emit('drill', n.key)">
        {{ n.value.toLocaleString() }}
      </button>
      <div class="kn-l">
        {{ n.label }}
        <span class="kn-i" :class="{ pin: pinned === n.key }"
              tabindex="0" role="button" :aria-label="`${n.label} 怎麼算`"
              @click.stop="pinned = pinned === n.key ? '' : n.key">ⓘ
          <span class="kn-tip" role="note">
            <b>怎麼算</b>{{ n.how }}<br>
            <b>資料來源</b>{{ n.source }}<br>
            <b>已知限制</b>{{ n.limits }}<br>
            <b>0 的意義</b>{{ n.zero?.text || n.zero?.why }}
          </span>
        </span>
      </div>
      <div v-if="n.value === 0 && n.zero?.text" class="kn-z" :class="n.zero.checked ? 'ok' : 'no'">
        {{ n.zero.text }}
      </div>
    </div>
    <NuxtLink to="/number-check" class="kn-hc" title="畫面值 vs 獨立重算值，差異逐台說明">數字健檢 →</NuxtLink>
  </div>
</template>

<style scoped>
.kn { display: flex; flex-wrap: wrap; gap: 8px; align-items: stretch; margin: 6px 0 10px; }
.kn-c { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
        padding: 6px 12px; min-width: 104px; }
.kn-c.on { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand) inset; }
.kn-n { font-size: 20px; font-weight: 700; background: none; border: 0; padding: 0;
        color: var(--brand-dark, var(--brand)); cursor: pointer; text-decoration: underline dotted; }
.kn-l { font-size: 12px; color: var(--ink-soft); display: flex; align-items: center; gap: 4px; }
.kn-i { position: relative; cursor: help; color: var(--muted); font-size: 13px; }
.kn-tip { display: none; position: absolute; left: -8px; top: calc(100% + 4px); z-index: 50;
          width: 360px; max-width: 82vw; background: var(--card); color: var(--ink);
          border: 1px solid var(--border-strong); border-radius: 8px; box-shadow: var(--shadow);
          padding: 8px 10px; font-size: 12px; line-height: 1.7; white-space: normal; }
.kn-tip b { display: inline-block; min-width: 64px; color: var(--ink-soft); margin-right: 4px; }
.kn-i:hover .kn-tip, .kn-i:focus .kn-tip, .kn-i.pin .kn-tip { display: block; }
.kn-z { font-size: 11px; margin-top: 2px; }
.kn-z.ok { color: var(--ok-text, #15803d); }
.kn-z.no { color: #b91c1c; font-weight: 600; }
.kn-hc { align-self: center; font-size: 12px; margin-left: 4px; }
.mono { font-family: ui-monospace, monospace; }
</style>
