/** Verify closed Character State visuals and the permanently static surface. */

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  getCharacterEmotionPresentation,
  getCharacterPresentation,
} from '../src/character/character-presentation.ts'
import {
  isCharacterEmotion,
  resolveCharacterEmotion,
} from '../src/character/character-emotion.ts'
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

test('maps ten strict emotions to reviewed expression cells and cues', () => {
  const emotions = [
    'neutral',
    'happy',
    'sad',
    'caring',
    'moved',
    'playful',
    'affectionate',
    'teasing',
    'serious',
    'surprised',
  ]
  const presentations = emotions.map(getCharacterEmotionPresentation)
  const cells = presentations.map(
    ({ atlasColumn, atlasRow }) => `${atlasColumn}:${atlasRow}`,
  )

  assert.deepEqual(cells, [
    '0:0',
    '1:1',
    '2:1',
    '2:3',
    '4:3',
    '0:2',
    '1:2',
    '4:1',
    '4:0',
    '2:2',
  ])
  assert.deepEqual(presentations.map(({ expression }) => expression), [
    'soft-smile',
    'happy',
    'gentle-sad',
    'tender-comfort',
    'light-tears',
    'playful',
    'shy',
    'wink',
    'serious',
    'surprised',
  ])
  assert.equal(new Set(cells).size, emotions.length)
  for (const invalid of [null, '', 'Happy', '../happy', 'not-valid', {}, 1]) {
    assert.equal(isCharacterEmotion(invalid), false)
    assert.equal(resolveCharacterEmotion(invalid), 'neutral')
  }
})

test('main CharacterArtwork is static and never advertises mouth animation', async () => {
  const source = await readFile(
    new URL('../src/character/CharacterArtwork.tsx', import.meta.url),
    'utf8',
  )

  assert.match(source, /data-character-performance="still"/u)
  assert.doesNotMatch(source, /Live2DCharacterCanvas/u)
  assert.doesNotMatch(source, /data-character-mouth-capable/u)
  assert.doesNotMatch(source, /character-performance\.ts/u)
})
