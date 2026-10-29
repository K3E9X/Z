import { describe, expect, it } from 'vitest'
import { describeCron } from '@/lib/cron'
import {
  clockDuration,
  compactNumber,
  countWord,
  dateTime,
  initials,
  percent,
  relative,
  shortHash,
} from '@/lib/format'

describe('format', () => {
  it('compacts token counts like the mockups', () => {
    expect(compactNumber(18_400_000)).toBe('18.4M')
    expect(compactNumber(412_000)).toBe('412k')
    expect(compactNumber(2_000_000)).toBe('2M')
    expect(compactNumber(950)).toBe('950')
    expect(compactNumber(null)).toBe('—')
  })
  it('formats durations and relative times', () => {
    expect(clockDuration(18 * 60 + 4)).toBe('18:04')
    expect(clockDuration(3600 + 61)).toBe('1:01:01')
    const now = Date.parse('2026-10-28T12:00:00Z')
    expect(relative('2026-10-28T11:56:00Z', now)).toBe('4 min ago')
    expect(relative('2026-10-28T11:59:50Z', now)).toBe('now')
    expect(relative('2026-10-27T10:00:00Z', now)).toBe('yesterday')
    expect(relative('2026-10-30T12:00:00Z', now)).toBe('in 2 days')
  })
  it('writes small counts as words and shortens hashes', () => {
    expect(countWord(5)).toBe('Five')
    expect(countWord(0)).toBe('No')
    expect(countWord(12)).toBe('12')
    expect(percent(0.613)).toBe('61%')
    expect(initials('Sarah Martin')).toBe('SM')
    expect(shortHash('9f2c0000000000000000000041ab')).toBe('sha256:9f2c…41ab')
  })
  it('describes common cron expressions in plain English', () => {
    expect(describeCron('0 8 * * 1')).toBe('Every Monday, 8:00')
    expect(describeCron('0 16 * * 5')).toBe('Every Friday, 16:00')
    expect(describeCron('30 8 * * 1-5')).toBe('Weekdays, 8:30')
    expect(describeCron('0 9 1 * *')).toBe('1st of every month, 9:00')
    expect(describeCron(undefined)).toBe('On demand')
    expect(describeCron('*/5 * * * *')).toBe('*/5 * * * *')
  })

  it('formats a date and time in UTC when asked, whatever the local zone', () => {
    const tz = process.env.TZ
    process.env.TZ = 'Pacific/Kiritimati'
    try {
      expect(dateTime('2026-03-01T23:30:05Z', { utc: true })).toBe('Mar 1, 2026 23:30:05')
      expect(dateTime('2026-03-01T23:30:05Z')).toBe('Mar 2, 2026 13:30:05')
    } finally {
      if (tz === undefined) delete process.env.TZ
      else process.env.TZ = tz
    }
  })
})
