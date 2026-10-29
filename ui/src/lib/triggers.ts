/** Client-side checks for trigger forms (POST /triggers); z-server validates again. */

const FIELD = /^(\*|\d+(-\d+)?|[a-z]{3}(-[a-z]{3})?)(\/\d+)?$/i
const FIELD_NAMES = ['minute', 'hour', 'day of month', 'month', 'day of week']

/** Null when `expr` is a five-field cron expression (minute hour day-of-month month day-of-week). */
export function cronError(expr: string): string | null {
  const parts = expr.trim().split(/\s+/).filter(Boolean)
  if (parts.length !== 5) return 'Five fields: minute hour day-of-month month day-of-week'
  for (const [i, part] of parts.entries()) {
    if (!part.split(',').every((p) => FIELD.test(p))) return `Invalid ${FIELD_NAMES[i] ?? 'field'}: ${part}`
  }
  return null
}

/** Schedules are evaluated in UTC by z-server (config.timezone defaults to UTC). */
export const SCHEDULE_TZ_NOTE = 'Schedules run in UTC.'

export type SourceKind = 'jira' | 'gitlab' | 'webhook' | 'schedule' | 'email'

/** The trigger body for one intake source, or the first thing to fix. */
export function sourceTrigger(
  kind: SourceKind,
  f: { project: string; label: string; assignee: string; cron: string },
): { config: Record<string, unknown>; filter: Record<string, unknown> } | { error: string } {
  if (kind === 'jira') {
    // z-server polls Jira with config.project and config.label (docs/05 §11), no filter.
    if (!f.project.trim()) return { error: 'Name the Jira project to watch.' }
    if (!f.label.trim()) return { error: 'A label is needed, or every issue of the project becomes a task.' }
    return { config: { project: f.project.trim(), label: f.label.trim() }, filter: {} }
  }
  if (kind === 'gitlab') {
    if (!f.assignee.trim()) return { error: 'An assignee is needed, or every GitLab event becomes a task.' }
    return { config: {}, filter: { assignee: f.assignee.trim().replace(/^@/, '') } }
  }
  if (kind === 'schedule') {
    const err = cronError(f.cron)
    return err ? { error: err } : { config: { cron: f.cron.trim() }, filter: {} }
  }
  return { config: {}, filter: {} }
}
