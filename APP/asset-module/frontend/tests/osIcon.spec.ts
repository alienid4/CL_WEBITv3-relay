/**
 * 主機名後面的 OS 圖示（2026-10-21）：只看實測 OS；沒納管不顯示；實測不知道顯示「?」。
 * 使用者定的分法：Red Hat／Oracle Linux／OCP 各一個圖；Debian、CentOS、Rocky 與其他 Linux 都是企鵝；
 * Windows、AIX 各一個。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { netIconFor, netVendorKey, osIconFor, osIconKey } from '../composables/osIcon'
import OsIcon from '../components/OsIcon.vue'

describe('osIconKey', () => {
  it('Red Hat／Oracle／OCP 各有自己的圖', () => {
    expect(osIconKey('Red Hat Enterprise Linux 9.4')).toBe('rhel')
    expect(osIconKey('RedHat 9.4')).toBe('rhel')
    expect(osIconKey('RHEL 8.10')).toBe('rhel')
    expect(osIconKey('Oracle Linux Server 8.9')).toBe('oracle')
    expect(osIconKey('Red Hat Enterprise Linux CoreOS 415.92')).toBe('openshift')
    expect(osIconKey('OpenShift Container Platform 4.15')).toBe('openshift')
    expect(osIconKey('OCP 4.14 worker')).toBe('openshift')
  })
  it('Debian、CentOS、Rocky 與其他 Linux 都是企鵝', () => {
    for (const os of ['Debian GNU/Linux 12 (bookworm)', 'CentOS Linux 7.9.2009', 'Rocky Linux 9.3 (Blue Onyx)',
      'Ubuntu 22.04.4 LTS', 'SUSE Linux Enterprise Server 15 SP5', 'SLES 12', 'AlmaLinux 9.4', 'Linux']) {
      expect(osIconKey(os), os).toBe('linux')
    }
  })
  it('Windows、AIX', () => {
    expect(osIconKey('Microsoft Windows Server 2019 Standard')).toBe('windows')
    expect(osIconKey('AIX 7.2')).toBe('aix')
  })
  it('空白或認不出來＝unknown', () => {
    expect(osIconKey('')).toBe('unknown')
    expect(osIconKey(null)).toBe('unknown')
    expect(osIconKey('HP-UX 11.31')).toBe('unknown')
  })
})

describe('osIconFor', () => {
  it('沒納管不顯示', () => {
    expect(osIconFor(false, 'Red Hat Enterprise Linux 9.4')).toBeNull()
  })
  it('已納管：用實測 OS，提示全名＋版本（企鵝也分得出是哪一家）', () => {
    expect(osIconFor(true, 'Red Hat Enterprise Linux 9.4')).toEqual({ key: 'rhel', title: 'Red Hat Enterprise Linux 9.4' })
    expect(osIconFor(true, 'Rocky Linux 9.3')).toEqual({ key: 'linux', title: 'Rocky Linux 9.3' })
  })
  it('已納管但實測 OS 不知道 → ? ＋「作業系統不知道」', () => {
    expect(osIconFor(true, null)).toEqual({ key: 'unknown', title: '作業系統不知道' })
    expect(osIconFor(true, '  ')).toEqual({ key: 'unknown', title: '作業系統不知道' })
  })
  it('有字樣但認不出來：? 圖示，提示照寫 OS 名', () => {
    expect(osIconFor(true, 'HP-UX 11.31')?.title).toBe('HP-UX 11.31（沒有對應的圖示）')
  })
})

describe('網路設備廠牌圖示', () => {
  it('Cisco／HP（HPE、Aruba、H3C）／F5；其他＝一般網路設備', () => {
    expect(netVendorKey('Cisco')).toBe('cisco')
    expect(netVendorKey('HPE')).toBe('hp')
    expect(netVendorKey('Aruba')).toBe('hp')
    expect(netVendorKey('H3C／HPE 網通')).toBe('hp')
    expect(netVendorKey('F5')).toBe('f5')
    expect(netVendorKey('BIG-IP')).toBe('f5')
    expect(netVendorKey('Juniper')).toBe('network')
    expect(netVendorKey(null)).toBe('network')
  })
  it('提示寫廠牌＋依據；不知道就寫「廠牌不知道」＋登記機型', () => {
    const c = netIconFor({ vendor: 'Cisco', confidence_label: '確定', evidence: ['資產登記的設備機型：Catalyst 9300'] })
    expect(c).toEqual({ key: 'cisco', title: '網路設備・Cisco・確定（依據：資產登記的設備機型：Catalyst 9300）' })
    expect(netIconFor({ vendor: null, confidence_label: null, evidence: [] }, 'XYZ-48')).toEqual(
      { key: 'network', title: '網路設備・廠牌不知道（登記機型：XYZ-48）' })
    expect(netIconFor(null)).toBeNull()
  })
})

describe('OsIcon.vue', () => {
  it('畫出對應圖示、提示文字在 title；每種圖都不一樣', () => {
    const w = mount(OsIcon, { props: { k: 'windows', title: 'Windows Server 2019' } })
    expect(w.find('svg').attributes('data-os')).toBe('windows')
    expect(w.find('title').text()).toBe('Windows Server 2019')
    const u = mount(OsIcon, { props: { k: 'unknown', title: '作業系統不知道' } })
    expect(u.find('text').text()).toBe('?')
    const keys = ['rhel', 'oracle', 'openshift', 'linux', 'windows', 'aix', 'unknown', 'cisco', 'hp', 'f5', 'network'] as const
    const shapes = keys.map((k) => mount(OsIcon, { props: { k, title: k } }).find('g').html())
    expect(new Set(shapes).size).toBe(keys.length)
  })
})
