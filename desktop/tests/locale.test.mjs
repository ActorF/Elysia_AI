/** Verify persisted interface language, literal interpolation, and sibling-window synchronization. */

import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import ts from 'typescript'
import { messages } from '../src/i18n/catalog.ts'
import {
  getDateLocale,
  getLocale,
  initializeDocumentLocale,
  LOCALE_STORAGE_KEY,
  readLocalePreference,
  setLocale,
  subscribeLocale,
  synchronizeLocaleStorage,
  t,
  translate,
} from '../src/i18n/locale.ts'

/** Install isolated browser preference storage and restore all process globals afterward. */
function browserFixture(context, language = 'en-US') {
  const original = new Map(['navigator', 'window', 'document'].map((key) => [
    key, Object.getOwnPropertyDescriptor(globalThis, key),
  ]))
  const saved = new Map()
  const localStorage = {
    getItem: (key) => saved.get(key) ?? null,
    setItem: (key, value) => { saved.set(key, value) },
  }
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { language } })
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { localStorage } })
  Object.defineProperty(globalThis, 'document', {
    configurable: true, value: { documentElement: { lang: '' } },
  })
  context.after(() => {
    for (const [key, descriptor] of original) {
      if (descriptor === undefined) Reflect.deleteProperty(globalThis, key)
      else Object.defineProperty(globalThis, key, descriptor)
    }
    initializeDocumentLocale()
  })
  return { saved, localStorage }
}

test('defaults to device language and persists an explicit choice without rewriting other data', (context) => {
  const { saved } = browserFixture(context, 'zh-Hans-CN')
  saved.set('chat-draft', 'Keep this user-authored 中文 text')
  assert.equal(initializeDocumentLocale(), 'zh-CN')
  assert.equal(document.documentElement.lang, 'zh-CN')
  assert.equal(getDateLocale(), 'zh-CN')
  setLocale('en')
  assert.equal(saved.get(LOCALE_STORAGE_KEY), 'en')
  assert.equal(document.documentElement.lang, 'en')
  assert.equal(initializeDocumentLocale(), 'en')
  assert.equal(getDateLocale(), 'en-US')
  assert.equal(saved.get('chat-draft'), 'Keep this user-authored 中文 text')
})

test('synchronizes sibling-window changes and ignores unrelated storage events', (context) => {
  browserFixture(context)
  initializeDocumentLocale()
  let notifications = 0
  const unsubscribe = subscribeLocale(() => { notifications += 1 })
  context.after(unsubscribe)
  synchronizeLocaleStorage({ key: 'chat-draft', newValue: 'zh-CN' })
  assert.equal(getLocale(), 'en')
  assert.equal(notifications, 0)
  synchronizeLocaleStorage({ key: LOCALE_STORAGE_KEY, newValue: 'zh-CN' })
  assert.equal(getLocale(), 'zh-CN')
  assert.equal(document.documentElement.lang, 'zh-CN')
  assert.equal(t('Language'), '语言')
  assert.equal(notifications, 1)
  synchronizeLocaleStorage({ key: LOCALE_STORAGE_KEY, newValue: null })
  assert.equal(getLocale(), 'en')
  assert.equal(notifications, 2)
})

test('retains session language when local storage is unavailable', (context) => {
  const { localStorage } = browserFixture(context, 'zh-CN')
  localStorage.getItem = () => { throw new Error('Unavailable storage') }
  localStorage.setItem = () => { throw new Error('Unavailable storage') }
  assert.equal(readLocalePreference(), 'zh-CN')
  assert.doesNotThrow(() => { setLocale('en') })
  assert.equal(getLocale(), 'en')
  assert.equal(document.documentElement.lang, 'en')
})

test('interpolates data literally and leaves unknown source messages intact', () => {
  const path = 'D:\\Models\\$& {count} 中文'
  assert.equal(
    translate('Saved changes to {fields} will apply after restart.', 'zh-CN', { fields: path }),
    `已保存的${path}更改将在重启后生效。`,
  )
  assert.equal(translate('Unregistered user message', 'zh-CN'), 'Unregistered user message')
  assert.equal(translate('{count} files', 'zh-CN', { count: 2 }), '2 个文件')
  assert.equal(translate('{count} files', 'en', {}), '{count} files')
})

test('relocalizes an exact reviewed static notice already saved in either language', () => {
  const chinese = translate('Interface language', 'zh-CN')
  assert.equal(chinese, '界面语言')
  assert.equal(translate(chinese, 'en'), 'Interface language')
  assert.equal(translate('Image generation', 'zh-CN'), '图片生成')
  assert.equal(translate('图片生成', 'en'), 'Image generation')
  assert.equal(translate('界面语言 is my user-authored title', 'en'), '界面语言 is my user-authored title')
})

test('relocalizes a reviewed in-progress notice while retaining model and path substitutions', () => {
  const model = 'qwen-custom:{version} $& D:\\Models\\中文'
  const source = 'Restarting the local Backend with {model}…'
  const english = translate(source, 'en', { model })
  const chinese = translate(source, 'zh-CN', { model })
  assert.equal(translate(english, 'zh-CN'), chinese)
  assert.equal(translate(chinese, 'en'), english)
  assert.ok(chinese.includes(model))
  assert.equal(translate('Open my own user file', 'zh-CN'), 'Open my own user file')
  const oversized = `Unregistered diagnostic ${'x'.repeat(5000)}`
  assert.equal(translate(oversized, 'zh-CN'), oversized)
})

test('ships both languages with identical named placeholders for every catalog entry', () => {
  const placeholders = (copy) => [...new Set([...copy.matchAll(/\{([A-Za-z][A-Za-z0-9_]*)\}/g)]
    .map(match => match[1]))].sort()
  for (const [source, entry] of Object.entries(messages)) {
    assert.equal(typeof entry.en, 'string', `${source}: English entry missing`)
    assert.equal(typeof entry['zh-CN'], 'string', `${source}: Chinese entry missing`)
    assert.ok(entry.en.length > 0 && entry['zh-CN'].length > 0, `${source}: empty translation`)
    assert.deepEqual(placeholders(entry.en), placeholders(entry['zh-CN']), `${source}: placeholder mismatch`)
  }
})

test('registers every literal translation call in the renderer catalog', async () => {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src')
  const missing = []
  async function inspectDirectory(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const filename = path.join(directory, entry.name)
      if (entry.isDirectory()) {
        if (entry.name !== 'i18n') await inspectDirectory(filename)
      } else if (/\.tsx?$/.test(entry.name)) {
        const source = await readFile(filename, 'utf8')
        const tree = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true,
          entry.name.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
        function inspectArgument(argument) {
          // Language-invariant punctuation and decorative glyphs require no dictionary entry.
          if (ts.isStringLiteral(argument) && /\p{L}/u.test(argument.text) && messages[argument.text] === undefined) {
            missing.push(`${path.relative(root, filename)}: ${argument.text}`)
          } else if (ts.isConditionalExpression(argument)) {
            inspectArgument(argument.whenTrue)
            inspectArgument(argument.whenFalse)
          }
        }
        function visit(node) {
          if (ts.isCallExpression(node) && node.expression.getText(tree) === 't' && node.arguments[0]) {
            inspectArgument(node.arguments[0])
          }
          ts.forEachChild(node, visit)
        }
        visit(tree)
      }
    }
  }
  await inspectDirectory(root)
  assert.deepEqual(missing, [], `Missing literal interface translations:\n${missing.join('\n')}`)
})
