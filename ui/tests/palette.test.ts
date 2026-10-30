import { describe, expect, it } from 'vitest'
import { paletteFilter } from '@/shell/CommandPalette'

describe('paletteFilter', () => {
  it('needs every typed word, not scattered letters', () => {
    expect(paletteFilter('run playbook Weekly report', 'crit')).toBe(0)
    expect(paletteFilter('mission Critical findings triage msn_1', 'crit')).toBeGreaterThan(0)
    expect(paletteFilter('mission Critical findings triage', 'crit tri')).toBeGreaterThan(0)
    expect(paletteFilter('mission Critical findings triage', 'crit deploy')).toBe(0)
  })
  it('ranks a word prefix above an inner match and uses keywords', () => {
    expect(paletteFilter('go to Review', 'rev')).toBeGreaterThan(paletteFilter('go to Preview', 'rev'))
    expect(paletteFilter('agent Atlas', 'secops', ['secops backlog'])).toBeGreaterThan(0)
    expect(paletteFilter('anything', '')).toBe(1)
  })
})

describe('paletteFilter, Ask Z', () => {
  it('always keeps the new mission entry, ranked first', () => {
    expect(paletteFilter('ask z new mission zzz', 'zzz qqq')).toBe(1)
  })
})
