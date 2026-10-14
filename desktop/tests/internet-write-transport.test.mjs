/** Verify the sole authenticated external write using real loopback CONNECT/TLS and synthetic credentials, never a real GitHub mutation. */
import assert from 'node:assert/strict'
import http from 'node:http'
import https from 'node:https'
import net from 'node:net'
import tls from 'node:tls'
import { mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { InternetPolicyService } from '../dist-electron/internet-policy.js'
import { InternetTransport } from '../dist-electron/internet-transport.js'

// Public synthetic key/certificate: only this isolated test process trusts it.
// The proxy reroutes a vetted public address to the local TLS fixture, preserving
// real target-name verification and HTTP framing without contacting GitHub.
const testOnlyKey = `-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIAvg++zGyg2bbnLigKl6S1fTRB5b1Av7NWRoz93pKdae
-----END PRIVATE KEY-----`
const testOnlyCertificate = `-----BEGIN CERTIFICATE-----
MIIBezCCAS2gAwIBAgIUKjysAU2wVTRrO1MFfJ/iIFrr7d8wBQYDK2VwMBkxFzAV
BgNVBAMMDmFwaS5naXRodWIuY29tMCAXDTI2MTAxNDE4MjMxNVoYDzIxMjYwOTIw
MTgyMzE1WjAZMRcwFQYDVQQDDA5hcGkuZ2l0aHViLmNvbTAqMAUGAytlcAMhACbH
VoJbvk5fLNiYTG+QfccOw9jepnw7cfFw/nGYqrc/o4GEMIGBMB0GA1UdDgQWBBST
Wcq6LnEwdffXagp9ntCFd8ZfoTAfBgNVHSMEGDAWgBSTWcq6LnEwdffXagp9ntCF
d8ZfoTAPBgNVHRMBAf8EBTADAQH/MBkGA1UdEQQSMBCCDmFwaS5naXRodWIuY29t
MBMGA1UdJQQMMAoGCCsGAQUFBwMBMAUGAytlcANBAGDSs2eh/62tkLAU7iv0mWlD
higlzIWb2TZU/ImPMA2VKatA7GXkNpGEPUSX8qXCom44bd4WCPEAuP96fVF0zAY=
-----END CERTIFICATE-----`
const payload = { owner: 'Synthetic-Owner', repo: 'Synthetic.repo', title: 'Synthetic UTF-8 标题', body: 'Only local fixture data.\n第二行' }
const token = 'synthetic-test-token'
const originalRoots = tls.getCACertificates('default')
const created = { number: 73, html_url: 'https://github.com/Synthetic-Owner/Synthetic.repo/issues/73' }
const outcome = name => ({ outcome: name, url: null, number: null })
function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
async function waitFor(check) {
  for (let count = 0; count < 100; count += 1) {
    if (check()) return
    await new Promise(resolve => setTimeout(resolve, 5))
  }
  assert.fail('native fixture did not receive expected operation')
}
async function fixture(t, options = {}) {
  tls.setDefaultCACertificates([...originalRoots, testOnlyCertificate])
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-write-transport-'))
  const requests = [], tunnels = [], dns = [], serverNames = [], sockets = new Set()
  let closedClientSockets = 0, completeResponses = 0
  const heldCloses = new Set()
  const server = https.createServer({ key: testOnlyKey, cert: testOnlyCertificate }, async (request, response) => {
    const chunks = []
    for await (const chunk of request) chunks.push(chunk)
    const body = Buffer.concat(chunks)
    requests.push({ method: request.method, path: request.url, headers: request.headers, body })
    if (options.holdResponse) return
    if (options.disconnect) { request.socket.destroy(); return }
    const reply = options.responseBody ?? JSON.stringify(created)
    response.writeHead(options.status ?? 201, { 'content-type': options.contentType ?? 'application/json',
      ...(options.holdClientClose ? { 'content-length': Buffer.byteLength(reply) } : {}), ...options.headers })
    // Complete HTTP framing while keeping the server socket open. The client
    // receives real TLS bytes/end, but its native destroy is held until released.
    if (options.holdClientClose) response.write(reply)
    else response.end(reply)
  })
  server.on('secureConnection', socket => { serverNames.push(socket.servername) })
  server.on('tlsClientError', () => {})
  const proxy = http.createServer()
  for (const listener of [server, proxy]) listener.on('connection', socket => {
    sockets.add(socket); socket.once('close', () => sockets.delete(socket))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  proxy.on('connect', (request, client, head) => {
    tunnels.push({ path: request.url, headers: request.headers, head: head.byteLength })
    if (options.holdConnect) return
    const upstream = net.connect(server.address().port, '127.0.0.1', () => {
      client.write('HTTP/1.1 200 Connection Established\r\n\r\n')
      client.pipe(upstream); upstream.pipe(client)
    })
    sockets.add(upstream); upstream.once('close', () => sockets.delete(upstream))
    upstream.on('error', () => client.destroy())
    client.on('error', () => upstream.destroy())
    client.once('close', () => upstream.destroy())
  })
  await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve))
  t.after(async () => {
    for (const close of heldCloses) close()
    for (const socket of sockets) socket.destroy()
    await Promise.all([new Promise(resolve => server.close(resolve)), new Promise(resolve => proxy.close(resolve))])
    tls.setDefaultCACertificates(originalRoots)
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()))
    assert.match(path.basename(directory), /^elysia-write-transport-/u)
    await rm(directory, { recursive: true, force: true })
  })
  const policy = new InternetPolicyService(path.join(directory, 'policy.json'))
  await policy.update({ expectedRevision: 0, enabled: options.enabled ?? true,
    allowedDomains: options.domains ?? ['api.github.com'], proxy: { mode: 'local-http', port: proxy.address().port }, timeoutMs: 1000 })
  const transportPolicy = options.denyApi ? new Proxy(policy, { get(target, key) {
    if (key === 'acquire') return (...args) => {
      const lease = target.acquire(...args)
      return { ...lease, policy: { ...lease.policy, allowedDomains: ['github.com'] } }
    }
    const value = Reflect.get(target, key)
    return typeof value === 'function' ? value.bind(target) : value
  } }) : policy
  const transport = new InternetTransport(transportPolicy, { native: {
    resolve: async (host, signal) => { dns.push(host); return options.resolve === undefined ? ['140.82.114.5'] : options.resolve(host, signal) },
    request: (settings, callback) => {
      const request = https.request(settings, response => {
        callback(response)
        response.once('end', () => { completeResponses += 1 })
      })
      request.once('socket', socket => {
        socket.once('close', () => { closedClientSockets += 1 })
        if (options.holdClientClose) {
          const destroy = socket.destroy.bind(socket)
          let held = true, destroyArgs = []
          socket.destroy = (...args) => { if (held) { destroyArgs = args; return socket }; return destroy(...args) }
          heldCloses.add(() => { held = false; destroy(...destroyArgs) })
        }
      })
      return request
    },
  } })
  return { policy, transport, requests, tunnels, dns, serverNames, releaseClose: () => { for (const close of heldCloses) close() },
    get completeResponses() { return completeResponses }, get closedClientSockets() { return closedClientSockets } }
}

test('Native Issue POST sends the exact reviewed UTF8 JSON and bearer only to the fixed certificate-verified API origin', async t => {
  const test = await fixture(t)
  assert.deepEqual(await test.transport.createGithubIssue(payload, token), { outcome: 'created', url: created.html_url, number: 73 })
  assert.deepEqual(test.dns, ['api.github.com'])
  assert.deepEqual(test.serverNames, ['api.github.com'])
  assert.equal(test.tunnels.length, 1)
  assert.equal(test.tunnels[0].path, '140.82.114.5:443')
  assert.equal(test.tunnels[0].headers.authorization, undefined)
  assert.equal(test.tunnels[0].head, 0)
  const request = test.requests[0]
  assert.equal(request.method, 'POST')
  assert.equal(request.path, '/repos/Synthetic-Owner/Synthetic.repo/issues')
  assert.equal(request.headers.host, 'api.github.com')
  assert.equal(request.headers.authorization, `Bearer ${token}`)
  assert.equal(request.headers.accept, 'application/vnd.github+json')
  assert.equal(request.headers['x-github-api-version'], '2026-03-10')
  assert.equal(request.headers['content-type'], 'application/json')
  assert.equal(Number(request.headers['content-length']), request.body.byteLength)
  assert.deepEqual(JSON.parse(request.body.toString('utf8')), { title: payload.title, body: payload.body })
  assert.equal(test.policy.hasActiveWork(), false)
  assert.equal(test.closedClientSockets, 1)
})

test('Malformed payloads and credentials cannot open DNS or transmit unreviewed headers, fields or paths', async t => {
  const test = await fixture(t)
  for (const change of [{ url: 'https://evil.com' }, { owner: 'a/b' }, { repo: '..' }, { title: 'x'.repeat(257) },
    { title: 'line\nbreak' }, { body: 'x'.repeat(4001) }, { body: 'invalid\u0000' }, { body: '\ud800' }]) {
    assert.deepEqual(await test.transport.createGithubIssue({ ...payload, ...change }, token), outcome('not_sent'))
  }
  for (const secret of ['', 'x'.repeat(4097), 'bad token', 'secret\r\nInjected: true']) {
    assert.deepEqual(await test.transport.createGithubIssue(payload, secret), outcome('not_sent'))
  }
  assert.equal(test.dns.length + test.requests.length + test.tunnels.length, 0)
})

test('Disabled policy, unapproved API domain and private DNS fail before any authenticated transmission', async t => {
  for (const settings of [{ enabled: false }, { denyApi: true }, { resolve: async () => ['127.0.0.1'] },
    { resolve: async () => ['140.82.114.5', '169.254.169.254'] }]) {
    const test = await fixture(t, settings)
    assert.deepEqual(await test.transport.createGithubIssue(payload, token), outcome('not_sent'))
    assert.equal(test.requests.length + test.tunnels.length, 0)
    assert.equal(test.policy.hasActiveWork(), false)
  }
})

test('Authenticated redirects never forward credentials or retry at another URL, even on the same host', async t => {
  for (const location of ['https://evil.com/stolen', 'https://api.github.com/repos/other/repo/issues']) {
    const test = await fixture(t, { status: 302, headers: { location } })
    assert.deepEqual(await test.transport.createGithubIssue(payload, token), outcome('unknown'))
    assert.equal(test.requests.length, 1)
    assert.equal(test.tunnels.length, 1)
    assert.deepEqual(test.dns, ['api.github.com'])
  }
})

test('Complete client rejection is distinct from server failure and neither outcome is retried', async t => {
  for (const status of [400, 401, 403, 404, 410, 422, 429, 500, 503]) {
    const test = await fixture(t, { status, responseBody: '{"message":"private upstream diagnostics"}' })
    const result = await test.transport.createGithubIssue(payload, token)
    assert.deepEqual(result, outcome(status < 500 ? 'rejected' : 'unknown'))
    assert.doesNotMatch(JSON.stringify(result), /private upstream|synthetic-test-token/u)
    assert.equal(test.requests.length, 1)
    assert.equal(test.closedClientSockets, 1)
  }
})

test('Malformed or mismatched creation replies remain unknown without exposing server response data', async t => {
  for (const responseBody of ['{', JSON.stringify({ ...created, number: 0 }),
    JSON.stringify({ ...created, html_url: 'https://github.com/other/repo/issues/73' }),
    JSON.stringify({ ...created, html_url: 'https://github.com/Synthetic-Owner/Synthetic.repo/issues/73?secret=x' }),
    JSON.stringify({ ...created, pull_request: { url: 'not-an-issue' } }), 'x'.repeat(128 * 1024 + 1)]) {
    const test = await fixture(t, { responseBody })
    assert.deepEqual(await test.transport.createGithubIssue(payload, token), outcome('unknown'))
    assert.equal(test.requests.length, 1)
    assert.equal(test.policy.hasActiveWork(), false)
  }
})

test('Cancelling or turning policy Off after the POST is received returns unknown only after the native socket closes', async t => {
  for (const control of ['abort', 'off']) {
    const test = await fixture(t, { holdResponse: true }), abort = new AbortController()
    const pending = test.transport.createGithubIssue(payload, token, abort.signal)
    await waitFor(() => test.requests.length === 1)
    assert.equal(test.policy.hasActiveWork(), true)
    if (control === 'abort') abort.abort()
    else {
      const current = test.policy.getState()
      await test.policy.update({ expectedRevision: current.revision, enabled: false,
        allowedDomains: current.allowedDomains, proxy: current.proxy, timeoutMs: current.timeoutMs })
    }
    assert.deepEqual(await pending, outcome('unknown'))
    assert.equal(test.closedClientSockets, 1)
    assert.equal(test.requests.length, 1)
    assert.equal(test.policy.hasActiveWork(), false)
  }
})

test('A complete validated creation or rejection survives Off or cancellation while the native socket is still draining', async t => {
  for (const control of ['abort', 'off']) {
    for (const status of [201, 403]) {
      const test = await fixture(t, { holdClientClose: true, status }), abort = new AbortController()
      let settled = false
      const pending = test.transport.createGithubIssue(payload, token, abort.signal).then(result => { settled = true; return result })
      await waitFor(() => test.completeResponses === 1)
      assert.equal(test.closedClientSockets, 0)
      assert.equal(test.policy.hasActiveWork(), true)
      if (control === 'abort') abort.abort()
      else {
        const current = test.policy.getState()
        await test.policy.update({ expectedRevision: current.revision, enabled: false,
          allowedDomains: current.allowedDomains, proxy: current.proxy, timeoutMs: current.timeoutMs })
      }
      await new Promise(resolve => setImmediate(resolve))
      assert.equal(settled, false)
      assert.equal(test.closedClientSockets, 0)
      assert.equal(test.policy.hasActiveWork(), true)
      test.releaseClose()
      assert.deepEqual(await pending, status === 201 ? { outcome: 'created', url: created.html_url, number: 73 } : outcome('rejected'))
      assert.equal(test.closedClientSockets, 1)
      assert.equal(test.policy.hasActiveWork(), false)
      assert.equal(test.requests.length, 1)
    }
  }
})

test('A malformed complete 201 still stays unknown after cancellation and cannot become a known creation', async t => {
  const test = await fixture(t, { holdClientClose: true, responseBody: '{"number":73,"html_url":"https://evil.com/"}' })
  const abort = new AbortController(), pending = test.transport.createGithubIssue(payload, token, abort.signal)
  await waitFor(() => test.completeResponses === 1)
  abort.abort()
  assert.equal(test.policy.hasActiveWork(), true)
  test.releaseClose()
  assert.deepEqual(await pending, outcome('unknown'))
  assert.equal(test.closedClientSockets, 1)
})

test('The completed-response preservation applies only to writes; cancelled GET still waits drain and rejects', async t => {
  const test = await fixture(t, { holdClientClose: true, status: 200, responseBody: '{}' })
  const abort = new AbortController()
  const pending = test.transport.read('https://api.github.com/repos/Synthetic-Owner/Synthetic.repo',
    { accept: 'application/json', maxBytes: 1024, signal: abort.signal })
  await waitFor(() => test.completeResponses === 1)
  abort.abort()
  assert.equal(test.policy.hasActiveWork(), true)
  const rejected = assert.rejects(pending, error => error.code === 'cancelled')
  test.releaseClose()
  await rejected
  assert.equal(test.closedClientSockets, 1)
  assert.equal(test.policy.hasActiveWork(), false)
})

test('Timeout or abrupt connection loss after transmission never claims that the external action was cancelled', async t => {
  for (const options of [{ holdResponse: true }, { disconnect: true }]) {
    const test = await fixture(t, options)
    assert.deepEqual(await test.transport.createGithubIssue(payload, token), outcome('unknown'))
    assert.equal(test.requests.length, 1)
    assert.equal(test.closedClientSockets, 1)
    assert.equal(test.policy.hasActiveWork(), false)
  }
})

test('Cancellation before DNS drains or before CONNECT succeeds remains not_sent and preserves capacity until drain', async t => {
  const held = deferred()
  let signal
  const dns = await fixture(t, { resolve: async (_host, active) => { signal = active; return held.promise } })
  const abort = new AbortController()
  const pending = dns.transport.createGithubIssue(payload, token, abort.signal)
  await waitFor(() => signal !== undefined)
  abort.abort()
  assert.equal(signal.aborted, true)
  assert.equal(dns.policy.hasActiveWork(), true)
  held.resolve(['140.82.114.5'])
  assert.deepEqual(await pending, outcome('not_sent'))
  assert.equal(dns.requests.length + dns.tunnels.length, 0)
  const proxy = await fixture(t, { holdConnect: true }), stop = new AbortController()
  const connecting = proxy.transport.createGithubIssue(payload, token, stop.signal)
  await waitFor(() => proxy.tunnels.length === 1)
  stop.abort()
  assert.deepEqual(await connecting, outcome('not_sent'))
  assert.equal(proxy.requests.length, 0)
  assert.equal(proxy.policy.hasActiveWork(), false)
})

test('Unauthenticated read remains GET-only after a write and never inherits the bearer or GitHub API headers', async t => {
  const test = await fixture(t, { status: 200, responseBody: '{}' })
  assert.deepEqual(await test.transport.createGithubIssue(payload, token), outcome('unknown'))
  await test.transport.read('https://api.github.com/repos/Synthetic-Owner/Synthetic.repo', { accept: 'application/json', maxBytes: 1024 })
  assert.equal(test.requests.length, 2)
  assert.equal(test.requests[1].method, 'GET')
  assert.equal(test.requests[1].headers.authorization, undefined)
  assert.equal(test.requests[1].headers['x-github-api-version'], undefined)
  assert.equal(test.requests[1].body.byteLength, 0)
})
