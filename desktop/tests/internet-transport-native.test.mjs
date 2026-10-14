/** Exercise real loopback CONNECT tunnelling and verified TLS using a public, test-only certificate and synthetic response. */
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

// This synthetic certificate/key is public test data, trusted only inside this
// isolated test process. Production still uses the operating Node trust roots.
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
const options = { accept: 'application/json', maxBytes: 1024 }
async function fixture(t, { rejectConnect = false, holdConnect = false } = {}) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-internet-native-'))
  const requests = [], tunnels = [], sockets = new Set(), serverNames = []
  const server = https.createServer({ key: testOnlyKey, cert: testOnlyCertificate }, (request, response) => {
    requests.push({ url: request.url, host: request.headers.host, method: request.method })
    response.writeHead(200, { 'content-type': 'application/json' })
    response.end('{"synthetic":true}')
  })
  server.on('secureConnection', socket => { serverNames.push(socket.servername) })
  server.on('tlsClientError', () => {})
  const proxy = http.createServer()
  for (const listener of [server, proxy]) listener.on('connection', socket => {
    sockets.add(socket); socket.once('close', () => sockets.delete(socket))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  proxy.on('connect', (request, client, head) => {
    tunnels.push({ url: request.url, host: request.headers.host, head: head.byteLength })
    if (holdConnect) return
    if (rejectConnect) { client.end('HTTP/1.1 407 Proxy Authentication Required\r\nConnection: close\r\n\r\n'); return }
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
    for (const socket of sockets) socket.destroy()
    await Promise.all([new Promise(resolve => server.close(resolve)), new Promise(resolve => proxy.close(resolve))])
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()))
    assert.match(path.basename(directory), /^elysia-internet-native-/u)
    await rm(directory, { recursive: true, force: true })
  })
  const policy = new InternetPolicyService(path.join(directory, 'policy.json'))
  await policy.update({ expectedRevision: 0, enabled: true, allowedDomains: ['api.github.com', 'en.wikipedia.org'],
    proxy: { mode: 'local-http', port: proxy.address().port }, timeoutMs: 1000 })
  const transport = new InternetTransport(policy, { native: { resolve: async () => ['140.82.114.5'] } })
  return { policy, transport, requests, tunnels, serverNames }
}
function trustFixture(t) {
  const original = tls.getCACertificates('default')
  tls.setDefaultCACertificates([...original, testOnlyCertificate])
  t.after(() => { tls.setDefaultCACertificates(original) })
}

test('Native CONNECT uses the vetted literal, verifies original SNI/Host, and returns only after actual close', async t => {
  trustFixture(t)
  const { policy, transport, requests, tunnels, serverNames } = await fixture(t)
  const result = await transport.read('https://api.github.com/synthetic', options)
  assert.deepEqual(JSON.parse(Buffer.from(result.body).toString()), { synthetic: true })
  assert.deepEqual(tunnels, [{ url: '140.82.114.5:443', host: '140.82.114.5:443', head: 0 }])
  assert.deepEqual(requests, [{ url: '/synthetic', host: 'api.github.com', method: 'GET' }])
  assert.deepEqual(serverNames, ['api.github.com'])
  assert.equal(policy.hasActiveWork(), false)
})

test('Native proxy TLS rejects a certificate for another authorized host and never sends HTTP through the tunnel', async t => {
  trustFixture(t)
  const { policy, transport, requests, tunnels } = await fixture(t)
  await assert.rejects(transport.read('https://en.wikipedia.org/synthetic', options), error => error.code === 'network_failed')
  assert.equal(tunnels.length, 1)
  assert.equal(requests.length, 0)
  assert.equal(policy.hasActiveWork(), false)
})

test('Native proxy refuses authentication challenges without direct fallback or secret forwarding', async t => {
  const { policy, transport, requests, tunnels } = await fixture(t, { rejectConnect: true })
  await assert.rejects(transport.read('https://api.github.com/synthetic', options), error => error.code === 'network_failed')
  assert.equal(tunnels.length, 1)
  assert.equal(requests.length, 0)
  assert.equal(policy.hasActiveWork(), false)
})

test('Native proxy handshake consumes the total timeout and the lease drains with its closed socket', async t => {
  const { policy, transport, requests, tunnels } = await fixture(t, { holdConnect: true })
  await assert.rejects(transport.read('https://api.github.com/synthetic', options), error => error.code === 'timeout')
  assert.equal(tunnels.length, 1)
  assert.equal(requests.length, 0)
  assert.equal(policy.hasActiveWork(), false)
})
