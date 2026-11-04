/** 相機掃條碼（2.044.6）：不支援就不顯示；掃到的字整理成序號。 */
import { describe, expect, it } from 'vitest'
import { canScan, scannedText } from '../utils/barcode'

describe('barcode', () => {
  it('沒有 BarcodeDetector、沒有相機 API、不是安全連線，都不顯示掃描', () => {
    const ok = { BarcodeDetector: class {}, navigator: { mediaDevices: { getUserMedia: () => null } }, isSecureContext: true }
    expect(canScan(ok)).toBe(true)
    const { BarcodeDetector: _x, ...noBd } = ok
    expect(canScan(noBd)).toBe(false)
    expect(canScan({ ...ok, navigator: {} })).toBe(false)
    expect(canScan({ ...ok, isSecureContext: false })).toBe(false)
    expect(canScan(undefined)).toBe(false)
  })
  it('掃到的字：去換行；QR 裡有 SN= 就取序號', () => {
    expect(scannedText('  SNX123\n')).toBe('SNX123')
    expect(scannedText('MODEL=R760;SN=ABC-123;LOC=B8')).toBe('ABC-123')
    expect(scannedText('S/N: 7XYZ')).toBe('7XYZ')
    expect(scannedText('10.99.1.5')).toBe('10.99.1.5')
  })
})
