// 開發協作頁的去識別純函式與任務篩選（純函式，無 DOM）。範例位址一律用 192.0.2.x／10.99.x 之類虛構值。
import { describe, expect, it } from 'vitest'
import { emptyMaps, isNetmask, mapsAsText, sanitize } from '../utils/devkitSanitize'

describe('去識別：IPv4', () => {
  it('同一位址同一代號、不同位址不同代號，計數正確', () => {
    const r = sanitize('a 10.99.1.5 b 10.99.1.5 c 8.8.8.8 d 10.99.1.6')
    const codes = r.text.match(/192\.0\.2\.\d+/g)!
    expect(codes).toHaveLength(4)
    expect(codes[0]).toBe(codes[1])
    expect(new Set(codes).size).toBe(3)
    expect(r.distinct.ip).toBe(3)
    expect(r.hits.ip).toBe(4)
    expect(r.text).not.toMatch(/10\.99\.|8\.8\.8\.8/)
  })

  it('私有與公共位址都換，依出現順序給代號', () => {
    const r = sanitize('172.16.5.4 203.0.113.9 1.1.1.1')  // webit3:allow-private-ip 假的示範值
    expect(r.text).toBe('192.0.2.1 192.0.2.2 192.0.2.3')
  })

  it('超過一個文件網段（254 個）會接續用下一個網段，不重複', () => {
    const many = Array.from({ length: 300 }, (_, i) => `10.${Math.floor(i / 250)}.${i % 250}.7`).join(' ')
    const r = sanitize(many)
    const out = r.text.split(' ')
    expect(new Set(out).size).toBe(300)
    expect(out[0]).toBe('192.0.2.1')
    expect(out[254]).toBe('198.51.100.1')
  })

  it('不誤傷版本號、OID、時間、子網路遮罩', () => {
    const keep = [
      'Version: 5.5.0.0', 'v5.5.1.2', 'firmware=5.5.0.0', 'OS version 7.9.1.3',
      '5.5.0.0.6000236', 'OID 1.3.6.1.4.1.9', 'Uptime 12:30:45', 'mask 255.255.255.0', 'gw 0.0.0.0',
      'ts 2026-11-01 12:30:45', '版本 5.5.0.0',
    ]
    for (const s of keep) {
      const r = sanitize(s)
      expect(r.text).toBe(s)
      expect(r.hits.ip).toBe(0)
    }
  })

  it('超過 255 的不是位址，不換', () => {
    const s = '256.1.1.1 1.2.3.999'
    expect(sanitize(s).text).toBe(s)
    expect(sanitize(s).hits.ip).toBe(0)
  })

  it('(7a) 結尾 .0.0 的位址：沒有版本字樣就換（route 10.20.0.0），有版本字樣才保留', () => {
    const r = sanitize('route 10.20.0.0 via 10.20.1.1\nversion 5.5.0.0')
    expect(r.text).toBe('route 192.0.2.1 via 192.0.2.2\nversion 5.5.0.0')
    expect(r.hits.ip).toBe(2)
    // 形似版本號卻被換掉的，要列進人工確認；有版本字樣被保留的也要列
    expect(r.review).toEqual([
      { text: '10.20.0.0', action: 'replaced', reason: expect.stringContaining('形似版本號') },
      { text: '5.5.0.0', action: 'kept', reason: expect.stringContaining('版本字樣') },
    ])
  })

  it('(7a) 孤立的 5.5.0.0（前面沒字樣）不再默默保留：替換並列入人工確認', () => {
    const r = sanitize('image 5.5.0.0')
    expect(r.text).toBe('image 192.0.2.1')
    expect(r.review.map(x => x.text)).toEqual(['5.5.0.0'])
  })

  it('(7b) 前導零 IPv4 涵蓋並正規化：010.001.002.003 與 10.1.2.3 是同一個位址', () => {  // webit3:allow-private-ip 假的示範值
    const r = sanitize('a 010.001.002.003 b 10.1.2.3 c 192.168.001.010')  // webit3:allow-private-ip 假的示範值
    expect(r.text).toBe('a 192.0.2.1 b 192.0.2.1 c 192.0.2.2')
    expect(r.distinct.ip).toBe(2)
    expect(Object.keys(r.maps.ip)).toEqual(['10.1.2.3', '10.99.0.10'])  // webit3:allow-private-ip 假的示範值
  })

  it('(7c) IPv4-mapped IPv6 整段處理：保留 ::ffff: 前綴、位址與一般 IPv4 同代號，不被當成 IPv6', () => {
    const r = sanitize('peer ::ffff:10.50.1.2 and 10.50.1.2')  // webit3:allow-private-ip 假的示範值
    expect(r.text).toBe('peer ::ffff:192.0.2.1 and 192.0.2.1')
    expect(r.ipv6).toEqual([])
    expect(r.hits.ip).toBe(2)
    expect(r.distinct.ip).toBe(1)
  })

  it('(7c) 一般 IPv6 位址偵測並計數、不替換', () => {
    const s = 'link fe80::1c2:3d4 gw 2001:db8::5 full 2001:0db8:85a3:0000:0000:8a2e:0370:7334 again fe80::1c2:3d4'
    const r = sanitize(s)
    expect(r.text).toBe(s)
    expect(r.ipv6.sort()).toEqual(['2001:0db8:85a3:0000:0000:8a2e:0370:7334', '2001:db8::5', 'fe80::1c2:3d4'])
  })

  it('(7c) 時間、MAC、WWPN、C++ 的 std:: 都不會被算成 IPv6', () => {
    const r = sanitize('at 12:30:45 mac aa:bb:cc:00:11:22 wwpn 50:06:01:60:3b:60:1a:2c std::string')
    expect(r.ipv6).toEqual([])
  })

  it('(7d) 版本字樣必須是獨立字：server／dev 結尾不算；kept 清單只含真的有字樣的', () => {
    const r = sanitize('server 10.9.9.9 build 5.5.1.2 fw:1.2.3.4')  // webit3:allow-private-ip 假的示範值
    expect(r.text).toBe('server 192.0.2.1 build 5.5.1.2 fw:1.2.3.4')
    expect(r.review.map(x => `${x.action}:${x.text}`)).toEqual(['kept:5.5.1.2', 'kept:1.2.3.4'])
  })

  it('(7d) 沒有任何可疑時人工確認清單是空的；同一個位址只列一次', () => {
    expect(sanitize('host 10.99.1.5 10.99.1.6').review).toEqual([])
    expect(sanitize('x 10.20.0.0 y 10.20.0.0').review).toHaveLength(1)
  })

  it('位址緊貼標點、括號、冒號埠號仍會換，埠號保留', () => {
    const r = sanitize('(10.99.2.3) host=10.99.2.3:8443, ip:10.99.2.4;')
    expect(r.text).toBe('(192.0.2.1) host=192.0.2.1:8443, ip:192.0.2.2;')
  })

  it('isNetmask', () => {
    expect(isNetmask('255.255.255.0')).toBe(true)
    expect(isNetmask('255.255.254.0')).toBe(true)
    expect(isNetmask('255.0.255.0')).toBe(false)
    expect(isNetmask('10.99.1.1')).toBe(false)
  })
})

describe('去識別：MAC 與 email', () => {
  it('冒號、連字號、Cisco 點號寫法、大小寫混用，同一張網卡同一代號', () => {
    const r = sanitize('AA:BB:CC:00:11:22 aa-bb-cc-00-11-22 aabb.cc00.1122 11:22:33:44:55:66')
    const out = r.text.split(' ')
    expect(out[0]).toBe(out[1])
    expect(out[1]).toBe(out[2])
    expect(out[3]).not.toBe(out[0])
    expect(r.distinct.mac).toBe(2)
    expect(r.hits.mac).toBe(4)
    expect(out[0]).toMatch(/^02:00:00:00:00:00$/)
  })

  it('時間、8 組的 WWPN 不當成 MAC', () => {
    const s = 'at 12:30:45 wwpn 50:06:01:60:3b:60:1a:2c'
    const r = sanitize(s)
    expect(r.text).toBe(s)
    expect(r.hits.mac).toBe(0)
  })

  it('email 換成代號，不分大小寫視為同一個', () => {
    const r = sanitize('To: Alice.Lee@corp.example.test; alice.lee@corp.example.test bob@example.test')
    expect(r.text).toBe('To: user1@example.test; user1@example.test user2@example.test')
    expect(r.distinct.email).toBe(2)
    expect(r.hits.email).toBe(3)
  })

  it('email 裡的網域不會被當成 IP 再換一次', () => {
    const r = sanitize('admin@10.99.1.1 ok')
    expect(r.hits.ip).toBe(0)
    expect(r.text).toBe('user1@example.test ok')
  })

  it('字尾剛好是 ver／v 的普通單字（server、dev）不會讓後面的 IP 被當成版本號漏換', () => {
    const r = sanitize('server 10.99.1.1 dev 10.99.1.2 Version 5.5.0.0')
    expect(r.text).toBe('server 192.0.2.1 dev 192.0.2.2 Version 5.5.0.0')
  })
})

describe('去識別：對照表', () => {
  it('帶上一次的對照表，分批貼上仍同一代號，且不改動傳入的物件', () => {
    const a = sanitize('10.99.1.1 10.99.1.2')
    const frozen = JSON.stringify(a.maps)
    const b = sanitize('10.99.1.2 10.99.1.3', a.maps)
    expect(JSON.stringify(a.maps)).toBe(frozen)
    expect(b.text).toBe('192.0.2.2 192.0.2.3')
    expect(b.distinct.ip).toBe(2)             // 這一次輸入裡有 2 個不同位址（其中 1 個沿用舊代號）
    expect(Object.keys(b.maps.ip)).toHaveLength(3)
  })

  it('沒有可替換的內容時不產生對照表列', () => {
    const r = sanitize('nothing here', emptyMaps())
    expect(r.distinct).toEqual({ ip: 0, mac: 0, email: 0 })
    expect(mapsAsText(r.maps)).not.toContain('->')
  })

  it('對照表文字：原值 -> 代號，MAC 以冒號顯示，開頭警語', () => {
    const r = sanitize('10.99.1.1 aa-bb-cc-00-11-22 x@y.example.test')
    const t = mapsAsText(r.maps)
    expect(t).toContain('不要寄出')
    expect(t).toContain('10.99.1.1\t->\t192.0.2.1')
    expect(t).toContain('aa:bb:cc:00:11:22\t->\t02:00:00:00:00:00')
    expect(t).toContain('x@y.example.test\t->\tuser1@example.test')
  })
})
