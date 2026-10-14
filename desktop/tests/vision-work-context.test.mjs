/** Keep renderer review references separate from Main-resolved, text-only Work evidence. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { parseWorkPlanCreateParams, parseWorkPlanCreateWireParams } from '../dist-electron/work-plan-protocol.js'

test('Work IPC requires a native receipt and the wire requires resolved bounded evidence', () => {
  const contextId = 'vision_context_' + 'a'.repeat(32)
  const evidence = { imageReference: { imageId: 'vision_' + 'b'.repeat(32), sha256: 'c'.repeat(64), width: 20, height: 30,
    source: { kind: 'region', label: 'Synthetic display region' } }, ocrExtraction: null, modelInference: null,
  userDescription: 'Inspect the selected note only.' }
  const plan = { chatId: 'chat_visual', title: 'Read note', calls: [{ toolId: 'read_text', arguments: { path: 'note.txt' } }] }
  assert.equal(parseWorkPlanCreateParams({ ...plan, visionContextId: contextId }).visionContextId, contextId)
  assert.throws(() => parseWorkPlanCreateParams({ ...plan, visionContext: evidence }))
  assert.throws(() => parseWorkPlanCreateWireParams({ ...plan, visionContextId: contextId }))
  assert.deepEqual(parseWorkPlanCreateWireParams({ ...plan, visionContext: evidence }).visionContext, evidence)
  assert.throws(() => parseWorkPlanCreateWireParams({ ...plan, visionContext: { ...evidence, approved: true } }))
})
