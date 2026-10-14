/** Load actual Electron/ASAR Internet modules against inert synthetic data, isolated preferences and denied network entrypoints. */

const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const { syncBuiltinESMExports } = require('node:module')
const { app, BrowserWindow } = require('electron')

const directory = process.env.ELYSIA_INTERNET_FIXTURE_DIRECTORY
assert.ok(directory && path.isAbsolute(directory), 'An isolated acceptance directory is required.')
assert.match(path.basename(directory), /^elysia-internet-native-/u)
app.setPath('userData', path.join(directory, 'userData'))
app.commandLine.appendSwitch('disable-background-networking')

let networkCalls = 0
function denyNetwork() { networkCalls += 1; throw new Error('Offline acceptance attempted a network operation.') }
// Patch before importing product modules, including named built-in ESM exports.
// This test cannot accidentally reach a real provider if default-off admission
// regresses; injected native probes independently count the same boundary.
for (const name of ['node:http', 'node:https']) {
  const module = require(name)
  module.request = denyNetwork; module.get = denyNetwork
}
const dns = require('node:dns')
dns.lookup = denyNetwork; dns.resolve4 = denyNetwork; dns.resolve6 = denyNetwork
dns.promises.lookup = denyNetwork; dns.promises.resolve4 = denyNetwork; dns.promises.resolve6 = denyNetwork
const tls = require('node:tls'), net = require('node:net')
tls.connect = denyNetwork; net.connect = denyNetwork; net.createConnection = denyNetwork
globalThis.fetch = denyNetwork
syncBuiltinESMExports()

void app.whenReady().then(async () => {
  let policy, research, writes
  try {
    const archive = process.env.ELYSIA_INTERNET_PACKAGE_ASAR
    const modules = archive ? path.join(archive, 'dist-electron') : path.join(__dirname, '../dist-electron')
    const load = name => import(pathToFileURL(path.join(modules, `${name}.js`)).href)
    const [{ InternetPolicyService }, { InternetTransport }, { InternetResearchController },
      { searchWikipedia }, { readInternetPage }, { InternetCacheStore, INTERNET_CACHE_WEB_TTL_MS },
      { InternetWriteController }] = await Promise.all([
      load('internet-policy'), load('internet-transport'), load('internet-research'), load('internet-search'),
      load('internet-reader'), load('internet-cache'), load('internet-write'),
    ])
    policy = new InternetPolicyService(path.join(directory, 'policy.json'))
    assert.equal((await policy.load()).enabled, false)
    const transport = new InternetTransport(policy, { native: { resolve: denyNetwork, request: denyNetwork, proxy: denyNetwork } })
    const owner = { chatId: 'chat_internet_native', projectId: null, mode: 'chat', ready: true, archived: false, busy: false }
    research = new InternetResearchController({ policy, transport, getOwner: () => owner })
    const signal = new AbortController().signal
    const isDisabled = error => error.code === 'disabled'
    const page = 'https://example.com/offline-fixture'
    await assert.rejects(transport.read(page, { accept: 'text/html, text/plain', maxBytes: 1024, signal }), isDisabled)
    await assert.rejects(readInternetPage(transport, page, signal), isDisabled)
    await assert.rejects(searchWikipedia(transport, { chatId: owner.chatId, language: 'en', query: 'Offline fixture' }, signal), isDisabled)
    await assert.rejects(research.readPage({ chatId: owner.chatId, url: page }), isDisabled)
    await assert.rejects(research.search({ chatId: owner.chatId, language: 'en', query: 'Offline fixture' }), isDisabled)
    assert.equal(policy.hasActiveWork(), false)

    // Calling the actual packaged reader proves parse5 and its production
    // dependencies are present. The only response is a local synthetic value;
    // script, image and frame URLs must not create subresource requests.
    const fetchedAt = new Date().toISOString()
    const source = await readInternetPage({ read: async () => ({ url: page, status: 200,
      headers: { 'content-type': 'text/html; charset=utf-8' }, fetchedAt,
      body: Buffer.from('<!doctype html><html><head><title>Offline parser</title></head><body>'
        + '<nav>Ignore navigation</nav><main><p>Offline &amp; safe evidence.</p>'
        + '<script src="https://example.com/script.js">Ignore script</script><img src="https://example.com/image.png">'
        + '<iframe src="https://example.com/frame">Ignore frame</iframe></main></body></html>') }) }, page, signal)
    assert.equal(source.title, 'Offline parser')
    assert.equal(source.content, 'Offline & safe evidence.')
    const cache = new InternetCacheStore()
    const entry = cache.put('synthetic-page', 'Offline parser', [source], INTERNET_CACHE_WEB_TTL_MS)
    const restored = cache.restore(entry.id, false)[0]
    assert.notEqual(restored.id, source.id)
    assert.equal(restored.fromCache, true)
    assert.equal(restored.content, source.content)
    assert.equal(restored.fetchedAt, source.fetchedAt)
    assert.deepEqual(research.getCache(owner.chatId).entries, [])

    writes = new InternetWriteController({ policy, getOwner: () => owner, readToken: () => null,
      confirm: async () => { throw new Error('Unconfigured writes must not open a native confirmation.') }, write: denyNetwork })
    assert.deepEqual(writes.getInternetWriteStatus(), { configured: false })
    // An isolated permission change lets the missing-credential boundary be
    // tested independently of default Off; no user preferences are involved.
    await policy.update({ expectedRevision: 0, enabled: true, allowedDomains: ['api.github.com'], proxy: { mode: 'direct' }, timeoutMs: 15000 })
    assert.throws(() => writes.prepareGithubIssue({ chatId: owner.chatId, owner: 'octocat', repo: 'Hello-World', title: 'Never sent', body: '' }),
      error => error.code === 'not_configured')
    await policy.update({ expectedRevision: 1, enabled: false, allowedDomains: ['api.github.com'], proxy: { mode: 'direct' }, timeoutMs: 15000 })
    assert.equal(networkCalls, 0)
    assert.equal(BrowserWindow.getAllWindows().length, 0)
    writes.shutdown(); research.shutdown(); policy.shutdown()
    await Promise.all([writes.whenIdle(), research.whenIdle(), policy.whenIdle()])
    process.stdout.write('INTERNET_NATIVE_RESULT:' + JSON.stringify({ packaged: Boolean(archive), defaultOff: true, deniedReads: 5,
      inertHtml: true, cacheRestored: true, missingTokenRejected: true, networkCalls }) + '\n')
    process.stdout.write('INTERNET_PACKAGE_NATIVE_SMOKE_OK\n')
    app.exit(0)
  } catch (error) {
    writes?.shutdown(); research?.shutdown(); policy?.shutdown()
    process.stderr.write(`Offline Internet acceptance failed: ${String(error?.stack ?? error)}\n`)
    app.exit(1)
  }
}).catch(error => { process.stderr.write(String(error?.stack ?? error)); app.exit(1) })
