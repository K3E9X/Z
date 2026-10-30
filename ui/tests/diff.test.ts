import { describe, expect, it } from 'vitest'
import { parseUnified } from '@/components/DiffViewer'

describe('parseUnified', () => {
  it('parses a valid patch with line numbers and counts', () => {
    const files = parseUnified('--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1,2 +1,3 @@\n keep\n+added\n keep too\n')
    expect(files).toHaveLength(1)
    expect(files[0]?.added).toBe(1)
    expect(files[0]?.removed).toBe(0)
    const added = files[0]?.hunks[0]?.lines.find((l) => l.kind === 'add')
    expect(added?.text).toBe('added')
    expect(added?.newNo).toBe(2)
  })
  it('falls back line by line on an invalid patch, headers as meta, never as changes', () => {
    const files = parseUnified('--- a/x\n+++ b/x\n@@ -1,9 +1,9 @@\n ctx\n+new\n')
    const lines = files[0]?.hunks[0]?.lines ?? []
    expect(lines.filter((l) => l.kind === 'meta').map((l) => l.text)).toEqual([
      '--- a/x',
      '+++ b/x',
      '@@ -1,9 +1,9 @@',
    ])
    expect(files[0]?.added).toBe(1)
    expect(files[0]?.removed).toBe(0)
  })
})
