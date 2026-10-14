/** Verify Work history redaction, bounded recovery disclosure, and exact conditional Undo correlation. */

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import {
  parseWorkAuditForChat, parseWorkAuditListParams, parseWorkAuditState,
  parseWorkUndoParams, parseWorkUndoResultForEntry,
} from '../dist-electron/work-audit-protocol.js'

const undoId = `undo_${'a'.repeat(32)}`
const hash = 'b'.repeat(64)

test('History preserves four actual decisions, legacy absence, and rejects nonmutating success or read approval', () => {
  for (const decision of ['approve', 'reject', 'modify', 'timeout']) {
    const audited = decision === 'approve' ? entry({ decision }) : entry({ decision,
      result: decision === 'timeout' ? 'failed' : 'denied', error: decision === 'timeout' ? 'timeout' : 'permission_denied', undoId: null })
    assert.deepEqual(parseWorkAuditState(page([audited])).entries[0], audited)
  }
  assert.equal(Object.hasOwn(parseWorkAuditState(page()).entries[0], 'decision'), false)
  for (const decision of ['modify', 'timeout', { approved: true }, 'command']) {
    assert.throws(() => parseWorkAuditState(page([entry({ decision })])), TypeError)
  }
  assert.throws(() => parseWorkAuditState(page([entry({ toolId: 'read_text', parameters: { path: 'notes.txt' },
    undoId: null, decision: 'approve' })])), TypeError)
})

function entry(overrides = {}) {
  return {
    timestamp: '2026-10-14T12:34:56.123456+00:00', chatId: 'chat_work', projectId: 'project_work',
    toolId: 'edit', parameters: { path: 'notes.txt', contentBytes: 3, contentSha256: hash },
    approver: 'user', result: 'succeeded', error: null, undoId, recoverable: false, recovery: null,
    ...overrides,
  }
}

function page(entries = [entry()], truncated = false) { return { kind: 'work.audit', entries, truncated } }

function recovery(overrides = {}) {
  const directory = `workspace/work/transactions/${undoId}`
  return { recoveryId: undoId, directory, backupPath: `${directory}/before.bin`, beforeSha256: hash, ...overrides }
}

function interrupted(overrides = {}) {
  return entry({ result: 'failed', error: 'io_error', undoId: null, recoverable: true, recovery: recovery(), ...overrides })
}

function result(data) { return { kind: 'work.result', result: { status: 'succeeded', data, error: null } } }

test('Work history accepts every closed tool with exact redacted parameters and detached data', () => {
  const entries = [
    entry({ toolId: 'list_directory', parameters: { path: '' }, undoId: null }),
    entry({ toolId: 'read_text', parameters: { path: 'notes.txt' }, undoId: null }),
    entry({ toolId: 'search_files', parameters: { path: '', queryCharacters: 4, querySha256: hash }, undoId: null }),
    entry({ toolId: 'metadata', parameters: { path: '' }, undoId: null, result: 'partial' }),
    entry({ toolId: 'create', undoId: `undo_${'c'.repeat(32)}` }),
    entry(),
    entry({ toolId: 'move', parameters: { path: 'notes.txt', destination: 'saved/notes.txt' }, undoId: `undo_${'d'.repeat(32)}` }),
    entry({ toolId: 'delete', parameters: { path: 'notes.txt' }, undoId: `undo_${'e'.repeat(32)}` }),
  ]
  const input = page(entries)
  const parsed = parseWorkAuditState(input)
  assert.deepEqual(parsed, input)
  input.entries[5].parameters.path = 'changed.txt'
  assert.equal(parsed.entries[5].parameters.path, 'notes.txt')
  assert.deepEqual(parseWorkAuditState(page([])), page([]))
})

test('Work history rejects content, literal search text, native paths, and unknown fields', () => {
  const invalid = [
    page([entry({ content: 'private original' })]),
    page([entry({ parameters: { ...entry().parameters, content: 'private replacement' } })]),
    page([entry({ toolId: 'search_files', undoId: null, parameters: {
      path: '', queryCharacters: 4, querySha256: hash, query: 'private query',
    } })]),
    page([entry({ rootPath: 'D:\\private' })]),
    page([entry({ toolId: 'shell', parameters: { path: 'notes.txt' } })]),
    { ...page(), privateJournal: {} },
  ]
  for (const value of invalid) assert.throws(() => parseWorkAuditState(value), TypeError)
  for (const path of ['../private', '/absolute', 'D:\\outside', 'a/../private', 'a\\b', 'NUL.txt', 'a//b', 'a.', '\ud800', '\udfff']) {
    assert.throws(() => parseWorkAuditState(page([entry({ parameters: { ...entry().parameters, path } })])), TypeError)
  }
  const hostile = entry()
  hostile.parameters = hostile
  assert.throws(() => parseWorkAuditState(page([hostile])), TypeError)
})

test('Work history requires complete bounded digest metadata without granting extra permissions', () => {
  for (const parameters of [
    { path: 'notes.txt', contentBytes: -1, contentSha256: hash },
    { path: 'notes.txt', contentBytes: 262145, contentSha256: hash },
    { path: 'notes.txt', contentBytes: true, contentSha256: hash },
    { path: 'notes.txt', contentBytes: 1, contentSha256: 'private content' },
    { path: 'notes.txt', contentBytes: 1 },
  ]) assert.throws(() => parseWorkAuditState(page([entry({ parameters })])), TypeError)
  for (const queryCharacters of [0, 257, 0.5, true]) {
    assert.throws(() => parseWorkAuditState(page([entry({ toolId: 'search_files', undoId: null,
      parameters: { path: '', queryCharacters, querySha256: hash },
    })])), TypeError)
  }
  assert.throws(() => parseWorkAuditState(page([entry({ toolId: 'read_text', parameters: { path: '' }, undoId: null })])), TypeError)
})

test('Work history distinguishes settled outcomes from interrupted forward and inverse writes', () => {
  for (const result of ['denied', 'cancelled', 'failed']) {
    assert.deepEqual(parseWorkAuditState(page([entry({ result, error: 'io_error', undoId: null })])),
      page([entry({ result, error: 'io_error', undoId: null })]))
    assert.throws(() => parseWorkAuditState(page([entry({ result, undoId: null })])), TypeError)
  }
  for (const result of ['succeeded', 'undone']) {
    assert.throws(() => parseWorkAuditState(page([entry({ result, error: 'io_error', undoId: null })])), TypeError)
    const ongoing = interrupted({ result })
    assert.deepEqual(parseWorkAuditState(page([ongoing])), page([ongoing]))
  }
  assert.deepEqual(parseWorkAuditState(page([entry({ result: 'undone', undoId: null })])), page([entry({ result: 'undone', undoId: null })]))
  for (const changes of [{ approver: 'agent' }, { error: 'native exception' }, { error: '__proto__' },
    { result: 'prepared' }, { recoverable: 1 }]) {
    assert.throws(() => parseWorkAuditState(page([entry(changes)])), TypeError)
  }
})

test('Work interrupted recovery permits only the exact relative journal and verified copy/digest pair', () => {
  const verified = interrupted()
  assert.deepEqual(parseWorkAuditState(page([verified])), page([verified]))
  const journalOnly = interrupted({ recovery: recovery({ backupPath: null, beforeSha256: null }) })
  assert.deepEqual(parseWorkAuditState(page([journalOnly])), page([journalOnly]))
  assert.deepEqual(parseWorkAuditState(page([interrupted({ toolId: 'create', recovery: journalOnly.recovery })])),
    page([interrupted({ toolId: 'create', recovery: journalOnly.recovery })]))
  for (const invalid of [
    null, recovery({ directory: 'D:\\private\\work' }),
    recovery({ directory: `workspace/work/transactions/${undoId}/../outside` }),
    recovery({ backupPath: `workspace/work/transactions/undo_${'c'.repeat(32)}/before.bin` }),
    recovery({ backupPath: null }), recovery({ beforeSha256: null }),
    recovery({ beforeSha256: hash.toUpperCase() }), recovery({ original: 'private bytes' }),
    recovery({ recoveryId: 'undo_other' }), recovery({ backupPath: 'before.bin' }),
  ]) assert.throws(() => parseWorkAuditState(page([interrupted({ recovery: invalid })])), TypeError)
  assert.throws(() => parseWorkAuditState(page([interrupted({ toolId: 'create' })])), TypeError)
  assert.throws(() => parseWorkAuditState(page([entry({ recovery: recovery() })])), TypeError)
  assert.throws(() => parseWorkAuditState(page([interrupted({ toolId: 'read_text', parameters: { path: 'notes.txt' } })])), TypeError)
})

test('Work history validates owner identifiers, unique Undo tokens, and read-only ineligibility', () => {
  for (const fields of [{ chatId: 'chat_' }, { chatId: 'chat_../other' }, { projectId: 'other' },
    { undoId: 'undo_owned' }, { undoId: `undo_${'A'.repeat(32)}` }, { undoId: `undo_${'a'.repeat(33)}` }]) {
    assert.throws(() => parseWorkAuditState(page([entry(fields)])), TypeError)
  }
  assert.throws(() => parseWorkAuditState(page([entry(), entry()])), /unique/u)
  for (const toolId of ['read_text', 'metadata', 'list_directory']) {
    assert.throws(() => parseWorkAuditState(page([entry({ toolId, parameters: { path: 'notes.txt' } })])), TypeError)
    assert.throws(() => parseWorkAuditState(page([entry({ toolId, parameters: { path: 'notes.txt' }, undoId: null, result: 'undone' })])), TypeError)
  }
  assert.throws(() => parseWorkAuditState(page([interrupted({ undoId })])), TypeError)
  assert.deepEqual(parseWorkAuditForChat(page(), 'chat_work'), page())
  assert.throws(() => parseWorkAuditForChat(page([entry({ chatId: 'chat_other' })]), 'chat_work'), /requested Chat/u)
  assert.throws(() => parseWorkAuditForChat(page([]), 'invalid'), TypeError)
})

test('Work history rejects invalid UTC dates and preserves microsecond newest-first ordering', () => {
  for (const timestamp of ['tomorrow', '2026-02-30T12:00:00Z', '2026-13-14T12:00:00Z',
    '0000-01-01T00:00:00Z', '2026-10-14T12:00:60Z', '2026-10-14T12:00:00+01:00',
    '2026-10-14T12:00:00', '2026-10-14T12:00:00.1234567Z']) {
    assert.throws(() => parseWorkAuditState(page([entry({ timestamp })])), TypeError)
  }
  const older = entry({ timestamp: '2026-10-14T12:34:56.123455Z', undoId: null })
  assert.deepEqual(parseWorkAuditState(page([entry(), older])), page([entry(), older]))
  assert.throws(() => parseWorkAuditState(page([older, entry()])), /newest first/u)
  const equal = entry({ timestamp: '2026-10-14T12:34:56.123456Z', undoId: null })
  assert.deepEqual(parseWorkAuditState(page([entry(), equal])), page([entry(), equal]))
})

function spacedJson(value) {
  if (Array.isArray(value)) return `[${value.map(spacedJson).join(', ')}]`
  if (value !== null && typeof value === 'object') {
    return `{${Object.entries(value).map(([key, item]) => `${JSON.stringify(key)}: ${spacedJson(item)}`).join(', ')}}`
  }
  return JSON.stringify(value)
}

test('Work history enforces the count and aggregate Python-spaced UTF-8 byte boundary', () => {
  assert.throws(() => parseWorkAuditState(page(Array.from({ length: 101 }, () => entry({ undoId: null })))), TypeError)
  assert.throws(() => parseWorkAuditState(page([], 'yes')), TypeError)
  const entries = Array.from({ length: 100 }, () => entry({ undoId: null, parameters: {
    path: 'a'.repeat(4096), contentBytes: 1, contentSha256: hash,
  } }))
  const payload = { entries, truncated: false }
  let remaining = 512 * 1024 - 1 - Buffer.byteLength(JSON.stringify(payload))
  assert.ok(remaining > 0 && remaining < entries.length * 4096)
  for (const item of entries) {
    const changed = Math.min(remaining, 4096)
    item.parameters.path = 'é'.repeat(changed) + 'a'.repeat(4096 - changed)
    remaining -= changed
  }
  assert.equal(remaining, 0)
  assert.equal(Buffer.byteLength(JSON.stringify(payload)), 512 * 1024 - 1)
  assert.ok(Buffer.byteLength(spacedJson(payload)) > 512 * 1024)
  assert.throws(() => parseWorkAuditState(page(entries)), /byte limit/u)
  const excess = Buffer.byteLength(spacedJson(payload)) - 512 * 1024
  entries[0].parameters.path = entries[0].parameters.path.slice(excess + 1)
  assert.ok(Buffer.byteLength(spacedJson(payload)) <= 512 * 1024)
  assert.equal(parseWorkAuditState(page(entries)).entries.length, 100)
})

test('Work history and Undo requests accept only exact identifiers and literal confirmation', () => {
  assert.deepEqual(parseWorkAuditListParams({ chatId: 'chat_work' }), { chatId: 'chat_work' })
  assert.throws(() => parseWorkAuditListParams({ chatId: 'chat_work', path: '../private' }), TypeError)
  const request = { chatId: 'chat_work', undoId, confirmed: true }
  assert.deepEqual(parseWorkUndoParams(request), request)
  for (const confirmed of [false, null, undefined, 1, 'true']) {
    assert.throws(() => parseWorkUndoParams({ ...request, confirmed }), /explicit confirmation/u)
  }
  for (const fields of [{ undoId: 'undo_other' }, { chatId: 'chat_../other' }, { path: 'retarget.txt' }, { permanent: true }]) {
    assert.throws(() => parseWorkUndoParams({ ...request, ...fields }), TypeError)
  }
})

test('Work Undo validates exact audited write path, move destination, and consumed token', () => {
  const valid = result({ path: 'notes.txt', undoId })
  assert.deepEqual(parseWorkUndoResultForEntry(valid, entry()), valid)
  const moved = entry({ toolId: 'move', parameters: { path: 'notes.txt', destination: 'saved/notes.txt' } })
  const movedResult = result({ path: 'notes.txt', destination: 'saved/notes.txt', undoId })
  assert.deepEqual(parseWorkUndoResultForEntry(movedResult, moved), movedResult)
  for (const data of [{ path: 'other.txt', undoId }, { path: 'notes.txt', undoId: `undo_${'c'.repeat(32)}` },
    { path: '../outside', undoId }, { path: 'notes.txt', undoId, original: 'private bytes' }]) {
    assert.throws(() => parseWorkUndoResultForEntry(result(data), entry()), TypeError)
  }
  assert.throws(() => parseWorkUndoResultForEntry(result({ ...movedResult.result.data, destination: 'elsewhere.txt' }), moved), TypeError)
  for (const expected of [entry({ undoId: null }), entry({ result: 'undone', undoId: null }), interrupted(),
    entry({ toolId: 'read_text', parameters: { path: 'notes.txt' }, undoId: null })]) {
    assert.throws(() => parseWorkUndoResultForEntry(valid, expected), /eligible/u)
  }
  assert.throws(() => parseWorkUndoResultForEntry({ kind: 'work.result', result: {
    status: 'partial', data: { path: 'notes.txt', undoId }, error: null,
  } }, entry()), /partial/u)
})

test('Work Undo keeps safe denials and rejects private or contradictory error details', () => {
  const denied = { kind: 'work.result', result: { status: 'denied', data: null,
    error: { code: 'undo_unavailable', message: 'The action can no longer be undone safely.' },
  } }
  assert.deepEqual(parseWorkUndoResultForEntry(denied, entry()), denied)
  const failed = { kind: 'work.result', result: { status: 'failed', data: null,
    error: { code: 'io_error', message: 'The local file operation failed.' },
  } }
  assert.deepEqual(parseWorkUndoResultForEntry(failed, entry()), failed)
  assert.throws(() => parseWorkUndoResultForEntry({ ...failed, result: { ...failed.result,
    error: { code: 'io_error', message: 'D:\\private\\before.bin could not be read' },
  } }, entry()), TypeError)
  assert.throws(() => parseWorkUndoResultForEntry({ ...denied, result: { ...denied.result, data: { path: 'notes.txt', undoId } } }, entry()), TypeError)
})

test('Actual Python write, read, interrupted recovery, and restart Undo records satisfy the desktop contract', () => {
  const script = `
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from work_tools.audit_wire import validate_audit_page
from work_tools.contracts import ToolCall
from work_tools.executor import WorkToolExecutor
from work_tools.workspace import WorkspaceBoundary
from work_tools.writes import WriteCoordinator

with TemporaryDirectory(prefix='elysia-audit-contract-') as temporary:
    base = Path(temporary)
    selected = base / 'selected'
    selected.mkdir()
    target = selected / 'notes-🧭.txt'
    target.write_text('private original content', encoding='utf-8')
    private = base / 'private' / 'work'
    owner = 'chat_work'
    project = 'project_work'
    boundary = WorkspaceBoundary(selected)
    writer = WriteCoordinator(private)
    call = ToolCall('edit', target.name, content='private replacement content')
    approval = writer.preview(owner, project, boundary, call)
    written = writer.confirm(owner, project, boundary, approval['approvalId'], True)
    assert written.status == 'succeeded'
    read = ToolCall('read_text', target.name)
    writer.record_read(owner, project, read, WorkToolExecutor(boundary).run(read))
    search = ToolCall('search_files', '', query='private search phrase')
    writer.record_read(owner, project, search, WorkToolExecutor(boundary).run(search))
    before = validate_audit_page({'kind': 'work.audit', **writer.audit_page(owner)})
    undo_entry = next(entry for entry in before['entries'] if entry['undoId'] is not None)
    restarted = WriteCoordinator(private)
    undone = restarted.undo(owner, project, boundary, undo_entry['undoId'])
    assert undone.status == 'succeeded'
    assert target.read_text(encoding='utf-8') == 'private original content'
    after = validate_audit_page({'kind': 'work.audit', **restarted.audit_page(owner)})
    retry = restarted.preview(owner, project, boundary, call)
    with patch.object(WriteCoordinator, '_apply', side_effect=OSError('private native error')):
        interrupted = restarted.confirm(owner, project, boundary, retry['approvalId'], True)
    assert interrupted.status == 'failed'
    recovery = validate_audit_page({'kind': 'work.audit', **restarted.audit_page(owner)})
    output = {'before': before, 'entry': undo_entry, 'undo': {'kind': 'work.result', 'result': undone.to_data()},
              'after': after, 'recovery': recovery}
    print(json.dumps(output, ensure_ascii=False))
`
  const produced = spawnSync(process.env.PYTHON_TEST_EXECUTABLE || 'python', ['-c', script], {
    cwd: new URL('../..', import.meta.url), encoding: 'utf8', timeout: 15_000,
    windowsHide: true, env: { ...process.env, PYTHONUTF8: '1' },
  })
  assert.equal(produced.status, 0, [produced.error?.message, produced.stderr].filter(Boolean).join('\n'))
  const fixture = JSON.parse(produced.stdout)
  for (const name of ['before', 'after', 'recovery']) {
    assert.deepEqual(parseWorkAuditForChat(fixture[name], 'chat_work'), fixture[name])
    const rendered = JSON.stringify(fixture[name])
    assert.equal(rendered.includes('private original content'), false)
    assert.equal(rendered.includes('private replacement content'), false)
    assert.equal(rendered.includes('private search phrase'), false)
    assert.equal(rendered.includes('private native error'), false)
  }
  assert.deepEqual(parseWorkUndoResultForEntry(fixture.undo, fixture.entry), fixture.undo)
  assert.equal(fixture.after.entries.some((item) => item.result === 'undone'), true)
  assert.equal(fixture.recovery.entries.some((item) => item.recoverable && item.recovery.backupPath?.endsWith('/before.bin')), true)
})
