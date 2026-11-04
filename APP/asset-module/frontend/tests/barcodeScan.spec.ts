/** 掃碼元件（2.044.7，GPT 複核 P1）：初始化任何一步失敗、元件關掉、開相機期間被關掉，相機串流都要停。 */
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Scan from '../components/v2/BarcodeScan.vue'
import { latestOnly } from '../utils/barcode'

enableAutoUnmount(afterEach)

let stopped = 0
let resolveCam: ((s: any) => void) | null = null
const fakeStream = () => ({ getTracks: () => [{ stop: () => { stopped++ } }] })

function setup(o: { play?: () => Promise<void>; detector?: any; slowCam?: boolean } = {}) {
  stopped = 0
  for (const [k, v] of Object.entries({ ref, onMounted, onBeforeUnmount })) vi.stubGlobal(k, v)
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: () => (o.slowCam ? new Promise(r => { resolveCam = r }) : Promise.resolve(fakeStream())) } })
  vi.stubGlobal('BarcodeDetector', o.detector ?? class { detect() { return Promise.resolve([]) } })
  Object.defineProperty(HTMLMediaElement.prototype, 'play', { configurable: true, value: o.play ?? (() => Promise.resolve()) })
  Object.defineProperty(HTMLMediaElement.prototype, 'srcObject', { configurable: true, get() { return (this as any)._so ?? null }, set(v) { (this as any)._so = v } })
}
beforeEach(() => { vi.unstubAllGlobals() })

describe('BarcodeScan', () => {
  it('播放失敗：關相機、顯示原因', async () => {
    setup({ play: () => Promise.reject(new Error('NotAllowed')) })
    const w = mount(Scan)
    await flushPromises()
    expect(stopped).toBe(1)
    expect(w.find('[data-testid="scan-err"]').text()).toContain('打不開相機')
  })
  it('建立偵測器失敗：關相機', async () => {
    setup({ detector: class { constructor() { throw new Error('不支援') } } })
    mount(Scan)
    await flushPromises()
    expect(stopped).toBe(1)
  })
  it('正常開著，元件關掉時關相機', async () => {
    setup()
    const w = mount(Scan)
    await flushPromises()
    expect(stopped).toBe(0)
    expect(w.find('[data-testid="scan-err"]').exists()).toBe(false)
    w.unmount()
    expect(stopped).toBe(1)
  })
  it('開相機還沒完成就被關掉：拿到的串流馬上停', async () => {
    setup({ slowCam: true })
    const w = mount(Scan)
    await flushPromises()
    w.unmount()
    resolveCam!(fakeStream())
    await flushPromises()
    expect(stopped).toBe(1)
  })
})

describe('latestOnly', () => {
  it('舊請求晚回來不採用', () => {
    const l = latestOnly()
    const a = l.next()
    const b = l.next()
    expect(l.isLatest(a)).toBe(false)
    expect(l.isLatest(b)).toBe(true)
  })
})
