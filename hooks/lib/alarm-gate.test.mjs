// Regression suite for the SessionStart scorecard's delegation-alarm gate.
// Run: node --test ~/.claude/hooks/lib/alarm-gate.test.mjs
// Pins the behavior a 2026-08-12 red-team sweep found under-covered: garbage
// verdicts, empty reasons, the QUARANTINED backfill cutoff, dedup, and
// per-session vs spawn-tree keys acking separately. Plain node:test + assert
// — no new dependencies.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  parseAlarms,
  parseAcks,
  computeUnacked,
  formatAlarmLines,
  computeAlarmLines,
  fallbackAlarmLines,
  checkTripwire,
  QUARANTINE_BACKFILL_CUTOFF,
} from './alarm-gate.mjs'

const PER_SESSION = (date, session) => `${date} ${session} something happened — BUILD or leakage?`
const SPAWN_TREE = (date, root) => `${date} spawn-tree root=${root} something else — evading the per-session alarm?`

test('unacked alarm blocks (non-empty alarmLines, header present)', () => {
  const alarms = PER_SESSION('2026-08-13', 'hub')
  const lines = computeAlarmLines(alarms, '', '2026-08-13')
  assert.ok(lines.length > 0)
  assert.match(lines[0], /UNANSWERED DELEGATION ALARM/)
  assert.match(lines[0], /^ ⛔ 1 /)
})

test('fully-acked is clean (empty alarmLines)', () => {
  const alarms = PER_SESSION('2026-08-13', 'hub')
  const acks = `KEY=2026-08-13|hub VERDICT=MISS REASON="should have delegated" ACKED=2026-08-13`
  const lines = computeAlarmLines(alarms, acks, '2026-08-13')
  assert.deepEqual(lines, [])
})

test('garbage VERDICT (e.g. SHRUG) does not clear the alarm', () => {
  const alarms = PER_SESSION('2026-08-13', 'hub')
  const acks = `KEY=2026-08-13|hub VERDICT=SHRUG REASON="whatever" ACKED=2026-08-13`
  const lines = computeAlarmLines(alarms, acks, '2026-08-13')
  assert.ok(lines.length > 0, 'garbage verdict must not satisfy the gate')
})

test('empty REASON does not clear the alarm', () => {
  const alarms = PER_SESSION('2026-08-13', 'hub')
  const acks = `KEY=2026-08-13|hub VERDICT=MISS REASON="" ACKED=2026-08-13`
  const lines = computeAlarmLines(alarms, acks, '2026-08-13')
  assert.ok(lines.length > 0, 'empty reason must not satisfy the gate')
})

test('QUARANTINED dated 2026-08-13 (after cutoff) does not clear', () => {
  const alarms = PER_SESSION('2026-08-13', 'hub')
  const acks = `KEY=2026-08-13|hub VERDICT=QUARANTINED REASON="backfill" ACKED=2026-08-13`
  const lines = computeAlarmLines(alarms, acks, '2026-08-13')
  assert.ok(lines.length > 0, 'QUARANTINED must not cover a post-cutoff alarm')
})

test('QUARANTINED dated 2026-08-10 (on/before cutoff) does clear', () => {
  assert.ok('2026-08-10' <= QUARANTINE_BACKFILL_CUTOFF)
  const alarms = PER_SESSION('2026-08-10', 'hub')
  const acks = `KEY=2026-08-10|hub VERDICT=QUARANTINED REASON="pre-enforcement backfill" ACKED=2026-08-13`
  const lines = computeAlarmLines(alarms, acks, '2026-08-13')
  assert.deepEqual(lines, [])
})

test('same alarm line repeated 3x counts once', () => {
  const line = PER_SESSION('2026-08-13', 'hub')
  const alarmsText = [line, line, line].join('\n')
  const alarms = parseAlarms(alarmsText)
  assert.equal(alarms.length, 1)
  const lines = computeAlarmLines(alarmsText, '', '2026-08-13')
  assert.match(lines[0], /^ ⛔ 1 /)
})

test('spawn-tree and per-session alarms for the same session ack separately', () => {
  const alarmsText = [
    PER_SESSION('2026-08-13', 'hub'),
    SPAWN_TREE('2026-08-13', 'hub'),
  ].join('\n')
  const alarms = parseAlarms(alarmsText)
  assert.equal(alarms.length, 2)
  assert.deepEqual(new Set(alarms.map(a => a.key)), new Set(['2026-08-13|hub', '2026-08-13|spawn-tree|hub']))

  // Acking only the per-session key leaves the spawn-tree alarm unacked.
  const acksOnlyPerSession = `KEY=2026-08-13|hub VERDICT=MISS REASON="reviewed" ACKED=2026-08-13`
  const unackedAfterOne = computeUnacked(alarms, parseAcks(acksOnlyPerSession))
  assert.equal(unackedAfterOne.length, 1)
  assert.equal(unackedAfterOne[0].key, '2026-08-13|spawn-tree|hub')

  // Acking both keys clears the gate entirely.
  const acksBoth = [
    `KEY=2026-08-13|hub VERDICT=MISS REASON="reviewed" ACKED=2026-08-13`,
    `KEY=2026-08-13|spawn-tree|hub VERDICT=BUILD REASON="justified" ACKED=2026-08-13`,
  ].join('\n')
  assert.deepEqual(computeUnacked(alarms, parseAcks(acksBoth)), [])
})

// --- Supporting coverage beyond the pinned fixture list ---

test('BUILD verdict also clears (not just MISS)', () => {
  const alarms = PER_SESSION('2026-08-13', 'hub')
  const acks = `KEY=2026-08-13|hub VERDICT=BUILD REASON="top-tier justified" ACKED=2026-08-13`
  assert.deepEqual(computeAlarmLines(alarms, acks, '2026-08-13'), [])
})

test('non-alarm commentary lines (VERDICT/CORRECTION/NOTE) are never counted', () => {
  const alarmsText = [
    PER_SESSION('2026-08-13', 'hub'),
    'CORRECTION: previous line was a typo',
    'NOTE: unrelated commentary interleaved in the log',
    'VERDICT=MISS stray line with no KEY=',
  ].join('\n')
  assert.equal(parseAlarms(alarmsText).length, 1)
})

test('formatAlarmLines shows at most 5 and reports the rest as older', () => {
  const alarms = Array.from({ length: 7 }, (_, i) => ({
    key: `2026-08-13|session-${i}`,
    raw: PER_SESSION('2026-08-13', `session-${i}`),
    idx: i,
  }))
  const lines = formatAlarmLines(alarms, '2026-08-13')
  assert.match(lines[0], /^ ⛔ 7 /)
  const shownKeyLines = lines.filter(l => l.includes('->'))
  assert.equal(shownKeyLines.length, 5)
  assert.ok(lines.some(l => /and 2 older unacked/.test(l)))
})

test('backlog >10 adds the Haiku bulk-triage suggestion', () => {
  const alarms = Array.from({ length: 11 }, (_, i) => ({
    key: `2026-08-13|session-${i}`,
    raw: PER_SESSION('2026-08-13', `session-${i}`),
    idx: i,
  }))
  const lines = formatAlarmLines(alarms, '2026-08-13')
  assert.ok(lines.some(l => /Haiku subagent/.test(l)))
})

test('fallbackAlarmLines: passive display, last 3 lines within 7 days, no gating', () => {
  const now = Date.parse('2026-08-13T00:00:00Z')
  const text = [
    '2026-07-01 old line outside window',
    '2026-08-11 within window one',
    '2026-08-12 within window two',
    '2026-08-13 within window three',
  ].join('\n')
  const lines = fallbackAlarmLines(text, now)
  assert.equal(lines.length, 3)
  assert.ok(lines.every(l => l.startsWith(' ⚠ delegation:')))
  assert.ok(!lines.some(l => l.includes('old line outside window')))
})

test('checkTripwire: no warning when breadcrumb missing or lines grew/held', () => {
  assert.equal(checkTripwire(null, 10), null)
  assert.equal(checkTripwire({ lines: 5, at: 'x' }, 5), null)
  assert.equal(checkTripwire({ lines: 5, at: 'x' }, 8), null)
})

test('checkTripwire: warns on shrink and on missing log, fails open on corrupt breadcrumb', () => {
  const shrink = checkTripwire({ lines: 10, at: '2026-08-12T00:00:00Z' }, 4)
  assert.match(shrink, /SHRANK \(10 -> 4 lines/)

  const missing = checkTripwire({ lines: 10, at: '2026-08-12T00:00:00Z' }, null)
  assert.match(missing, /is MISSING/)

  // Corrupt/partial breadcrumb (no numeric .lines) must fail open, not throw.
  assert.equal(checkTripwire({ lines: 'oops' }, 4), null)
  assert.equal(checkTripwire({}, 4), null)
  assert.equal(checkTripwire(undefined, 4), null)
})
