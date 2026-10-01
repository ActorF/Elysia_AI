/** Verify closed Character State visuals and motion policy without a browser. */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getCharacterEmotionPresentation,
  getCharacterPresentation,
} from '../src/character/character-presentation.ts'
import {
  isCharacterEmotion,
  resolveCharacterEmotion,
} from '../src/character/character-emotion.ts'
import {
  isCharacterPerformancePreference,
  resolveCharacterPerformance,
} from '../src/character/character-performance.ts'

const states = [
  'idle',
  'listening',
  'thinking',
  'speaking',
  'working',
  'waiting_approval',
  'error',
]

test('maps every semantic state to one reviewed atlas cell', () => {
  const cells = states.map((state) => {
    const presentation = getCharacterPresentation(state)
    return `${presentation.atlasColumn}:${presentation.atlasRow}`
  })

  assert.deepEqual(cells, [
    '0:0',
    '1:0',
    '2:0',
    '3:0',
    '0:1',
    '1:1',
    '2:1',
  ])
  assert.equal(new Set(cells).size, states.length)
  assert.ok(!cells.includes('3:1'), 'success is not a canonical Character State')
})

test('exposes only closed semantic presentation tokens', () => {
  const presentations = states.map(getCharacterPresentation)
  const expressions = new Set(presentations.map(({ expression }) => expression))
  const actions = new Set(presentations.map(({ action }) => action))

  assert.deepEqual(expressions, new Set([
    'soft-smile',
    'attentive',
    'focused',
    'speaking-smile',
    'patient',
    'concerned',
  ]))
  assert.deepEqual(actions, new Set([
    'resting',
    'listening',
    'thinking',
    'speaking',
    'working',
    'waiting',
    'alert',
  ]))
  for (const presentation of presentations) {
    assert.deepEqual(Object.keys(presentation).sort(), [
      'action',
      'atlasColumn',
      'atlasRow',
      'expression',
    ])
  }
})

test('maps three strict emotions to distinct reviewed expression cells', () => {
  const emotions = ['neutral', 'happy', 'sad']
  const cells = emotions.map((emotion) => {
    const presentation = getCharacterEmotionPresentation(emotion)
    return `${presentation.atlasColumn}:${presentation.atlasRow}`
  })

  assert.deepEqual(cells, ['0:0', '1:1', '2:1'])
  assert.equal(new Set(cells).size, emotions.length)
  for (const invalid of [null, '', 'Happy', '../happy', 'playful', {}, 1]) {
    assert.equal(isCharacterEmotion(invalid), false)
    assert.equal(resolveCharacterEmotion(invalid), 'neutral')
  }
})

test('accepts only the two persisted character performance choices', () => {
  for (const value of ['animated', 'still']) {
    assert.equal(isCharacterPerformancePreference(value), true)
  }
  for (const value of [null, '', 'system', 'hidden', 'full', 1, {}]) {
    assert.equal(isCharacterPerformancePreference(value), false)
  }
})

test('never lets animation override a system reduced-motion request', () => {
  assert.equal(resolveCharacterPerformance('animated', false), 'animated')
  assert.equal(resolveCharacterPerformance('animated', true), 'still')
  assert.equal(resolveCharacterPerformance('still', false), 'still')
  assert.equal(resolveCharacterPerformance('still', true), 'still')
})
