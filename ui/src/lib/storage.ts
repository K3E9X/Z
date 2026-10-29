/**
 * Browser storage for UI preferences only (theme, typeface, density, last filters).
 * Never tokens, never API data: the session is an HttpOnly cookie set by z-server.
 */
const PREFIX = 'z.pref.'

function store(): Storage | null {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

export function readPref<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  const raw = store()?.getItem(PREFIX + key)
  return raw !== null && raw !== undefined && (allowed as readonly string[]).includes(raw) ? (raw as T) : fallback
}

export function readBoolPref(key: string, fallback: boolean): boolean {
  const raw = store()?.getItem(PREFIX + key)
  return raw === 'true' ? true : raw === 'false' ? false : fallback
}

export function writePref(key: string, value: string | boolean): void {
  try {
    store()?.setItem(PREFIX + key, String(value))
  } catch {
    // storage full or disabled: preferences just do not persist
  }
}
