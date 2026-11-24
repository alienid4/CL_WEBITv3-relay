// 守門：所有 .vue 檔都要能被 Vue 編譯器解析（2026-11-02：HostBatchLookup 正則寫壞，vitest 沒掛到該元件、直到 221 前端建置才爆）。
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { compileScript, parse } from '@vue/compiler-sfc'
import { expect, it } from 'vitest'

function walk(dir: string, out: string[] = []): string[] {
  for (const n of readdirSync(dir)) {
    if (['node_modules', '.nuxt', '.output', 'tests'].includes(n)) continue
    const p = join(dir, n)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (p.endsWith('.vue')) out.push(p)
  }
  return out
}

it('全部 .vue 檔都能編譯（語法錯誤在這裡就會紅，不必等到前端建置）', () => {
  const bad: string[] = []
  for (const f of walk(join(__dirname, '..'))) {
    const r = parse(readFileSync(f, 'utf8'))
    if (r.errors.length) { bad.push(`${f}：${r.errors[0].message}`); continue }
    if (!r.descriptor.script && !r.descriptor.scriptSetup) continue
    try { compileScript(r.descriptor, { id: 'x' }) } catch (e: any) { bad.push(`${f}：${String(e.message).split('\n')[0]}`) }
  }
  expect(bad).toEqual([])
})
