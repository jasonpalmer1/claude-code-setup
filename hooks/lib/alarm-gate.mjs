/**
 * Pure logic for the SessionStart scorecard's delegation-alarm gate.
 * Extracted from operator-scorecard.mjs (2026-08-13) so the parse/dedup/
 * ack-validation/tripwire rules can be pinned with a regression suite instead
 * of only being exercised implicitly by whatever alarms happen to be on disk
 * that day. No I/O here — the hook does all fs reads/writes and passes their
 * contents in; these functions only transform strings/objects into results.
 *
 * Behavior contract (must match the hook's original inline logic exactly):
 * - Two alarm line shapes are recognized by fixed suffix: per-session
 *   ("... BUILD or leakage?") and spawn-tree ("... evading the per-session
 *   alarm?"). Anything else (VERDICT/CORRECTION/NOTE commentary lines) is
 *   ignored, never counted as an alarm.
 * - Alarms dedupe by key (date+session, or date+spawn-tree+root) — a
 *   repeated identical line collapses to one alarm.
 * - An ack line clears its key only if it matches the strict format
 *   (KEY=... VERDICT=MISS|BUILD|QUARANTINED ... REASON="non-empty" ACKED=...)
 *   AND, for QUARANTINED, the alarm's date is on/before the backfill cutoff
 *   — QUARANTINED cannot be used to wave off a fresh alarm.
 * - Per-session and spawn-tree alarms for the same session/date have
 *   different keys and must be acked separately.
 */

export const QUARANTINE_BACKFILL_CUTOFF = '2026-08-11'

const SPAWN_TREE_RE = /^(\d{4}-\d{2}-\d{2})\s+spawn-tree\s+root=(\S+)\b.*evading the per-session alarm\?\s*$/
const PER_SESSION_RE = /^(\d{4}-\d{2}-\d{2})\s+(\S+)\s+.*BUILD or leakage\?\s*$/
const ACK_RE = /^KEY=(\S+)\s+VERDICT=(MISS|BUILD|QUARANTINED)\b.*\bREASON="[^"]+"\s+ACKED=\S+/

/** Parse the raw alarms log into a deduped array of { key, raw, idx }. */
export function parseAlarms(alarmsLogText) {
  const alarmsByKey = new Map()
  alarmsLogText.split('\n').forEach((line, idx) => {
    const l = line.trim()
    if (!l) return
    let m = l.match(SPAWN_TREE_RE)
    if (m) {
      const key = `${m[1]}|spawn-tree|${m[2]}`
      if (!alarmsByKey.has(key)) alarmsByKey.set(key, { key, raw: l, idx })
      return
    }
    m = l.match(PER_SESSION_RE)
    if (m) {
      const key = `${m[1]}|${m[2]}`
      if (!alarmsByKey.has(key)) alarmsByKey.set(key, { key, raw: l, idx })
    }
  })
  return [...alarmsByKey.values()]
}

/** Parse the acks log into a Set of cleared alarm keys. */
export function parseAcks(acksLogText) {
  const acked = new Set()
  acksLogText.split('\n').forEach(line => {
    const m = line.trim().match(ACK_RE)
    if (!m) return
    const [, key, verdict] = m
    if (verdict === 'QUARANTINED') {
      const dateMatch = key.match(/^(\d{4}-\d{2}-\d{2})\|/)
      if (!dateMatch || dateMatch[1] > QUARANTINE_BACKFILL_CUTOFF) return
    }
    acked.add(key)
  })
  return acked
}

/** Alarms not covered by an acked key. */
export function computeUnacked(alarms, acked) {
  return alarms.filter(al => !acked.has(al.key))
}

/** Build the display block for unacked alarms (empty array = clean scorecard). */
export function formatAlarmLines(unacked, today) {
  if (unacked.length === 0) return []
  const shown = [...unacked].sort((x, y) => y.idx - x.idx).slice(0, 5)
  const older = unacked.length - shown.length
  return [
    ` ⛔ ${unacked.length} UNANSWERED DELEGATION ALARM${unacked.length === 1 ? '' : 'S'} — TRIAGE BEFORE ANY OTHER WORK.`,
    ...shown.map(al => `   ${al.key}  ->  ${al.raw}`),
    ...(older > 0 ? [`   …and ${older} older unacked`] : []),
    ` For EACH alarm above: judge it, then append ONE line per alarm to`,
    ` ~/.claude/hub/delegation-alarms-acks.log in EXACTLY this format:`,
    `   KEY=<key> VERDICT=MISS|BUILD REASON="<why, <=10 words>" ACKED=${today}`,
    ` (MISS = should have delegated more; BUILD = top-tier work was justified.)`,
    ` Per-session and spawn-tree alarms for the same session ack SEPARATELY — own key each.`,
    ...(unacked.length > 10
      ? [` Backlog >10: also bulk-triage the older ones via a Haiku subagent (model: haiku) that reads context per alarm and appends its own verdict lines.`]
      : []),
  ]
}

/** End-to-end: raw alarms + acks text -> display lines for the scorecard. */
export function computeAlarmLines(alarmsLogText, acksLogText, today) {
  const alarms = parseAlarms(alarmsLogText)
  const acked = parseAcks(acksLogText)
  const unacked = computeUnacked(alarms, acked)
  return formatAlarmLines(unacked, today)
}

/**
 * Fallback path used when the primary parse/ack pipeline throws (e.g. the
 * alarms log itself can't be read). Passive last-3-lines display, no gating.
 */
export function fallbackAlarmLines(alarmsLogText, nowMs) {
  const cutoff = nowMs - 7 * 86400000
  return alarmsLogText.trim().split('\n')
    .filter(l => { const d = Date.parse(l.slice(0, 10)); return !isNaN(d) && d >= cutoff })
    .slice(-3).map(l => ` ⚠ delegation: ${l}`)
}

/**
 * Deletion/shrinkage tripwire: compares current alarms-log line count against
 * a prior breadcrumb. Returns a warning string or null. Fails open (returns
 * null) on a missing/corrupt breadcrumb or missing baseline — never blocks.
 */
export function checkTripwire(breadcrumb, currentLines) {
  if (breadcrumb && typeof breadcrumb.lines === 'number' && breadcrumb.lines > 0) {
    if (currentLines == null) {
      return ` ⚠ delegation-alarms.log is MISSING (last seen: ${breadcrumb.lines} lines, ${breadcrumb.at}) — verify this wasn't a bypass.`
    } else if (currentLines < breadcrumb.lines) {
      return ` ⚠ delegation-alarms.log SHRANK (${breadcrumb.lines} -> ${currentLines} lines since ${breadcrumb.at}) — verify this wasn't a bypass.`
    }
  }
  return null
}
