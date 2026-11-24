<script setup lang="ts">
// 相機掃條碼／QR（2.044.6，GPT B4）：開後鏡頭，每 0.3 秒找一次條碼，找到就帶回去並關相機。只在 canScan() 為真時才會被打開。
// 2.044.7（GPT 複核 P1）：任何初始化失敗（播放、建立偵測器）都關相機；元件關掉時也關；開相機還沒完成就被關掉，拿到的串流馬上停。
import { scannedText } from '~/utils/barcode'
const emit = defineEmits<{ result: [text: string]; close: [] }>()
const video = ref<HTMLVideoElement | null>(null)
const err = ref('')
let stream: MediaStream | null = null
let timer: ReturnType<typeof setInterval> | null = null
let alive = true
function stop() {
  if (timer) clearInterval(timer)
  timer = null
  stream?.getTracks().forEach(t => t.stop())
  stream = null
  if (video.value) video.value.srcObject = null
}
onMounted(async () => {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false })
    stream = s
    if (!alive || !video.value) { stop(); return }      // 開相機期間就被關掉了：拿到的串流馬上停
    video.value.srcObject = s
    await video.value.play()
    const det = new (window as any).BarcodeDetector()
    if (!alive) { stop(); return }
    timer = setInterval(async () => {
      if (!video.value) return
      try {
        const codes = await det.detect(video.value)
        if (codes?.length) { const t = scannedText(codes[0].rawValue); stop(); emit('result', t) }
      } catch (e: any) { err.value = `讀不到條碼：${e?.message ?? e}` }      // 單張影格失敗：顯示原因，下一張再試
    }, 300)
  } catch (e: any) {
    stop()                                               // 初始化任何一步失敗都要關相機（不讓鏡頭一直開著）
    err.value = `打不開相機：${e?.message ?? e}（請允許使用相機，或改用手打）`
  }
})
onBeforeUnmount(() => { alive = false; stop() })
</script>

<template>
  <div class="scan" role="dialog" aria-label="掃條碼">
    <video ref="video" class="vid" playsinline muted />
    <p class="hint">把條碼或 QR 放在畫面中間</p>
    <p v-if="err" class="err" data-testid="scan-err">{{ err }}</p>
    <button type="button" class="btn2" @click="stop(); emit('close')">取消掃描</button>
  </div>
</template>

<style scoped>
.scan { display: grid; gap: 6px; margin: 6px 0 10px; padding: 8px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--sub); }
.vid { width: 100%; max-height: 260px; background: #000; border-radius: 6px; object-fit: cover; }
.hint { margin: 0; font-size: 13px; color: var(--ink-soft); }
.err { margin: 0; font-size: 13px; color: var(--bad); }
.btn2 { min-height: 44px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
</style>
