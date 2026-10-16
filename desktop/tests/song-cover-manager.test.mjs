/** Verify bounded Song Cover process ownership, state, playback, and export. */

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import {
  chmod,
  copyFile,
  link,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rename as renamePath,
  rm,
  stat,
  symlink,
  writeFile,
} from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  parseChooseSongCoverRequest,
  parseSongCoverReadiness,
  parseSongCoverWorkerEvent,
} from '../dist-electron/song-cover-contracts.js'
import { SongCoverManager } from '../dist-electron/song-cover-manager.js'
import { GpuAdmissionCoordinator } from '../dist-electron/gpu-admission.js'

const FAKE_WORKER = String.raw`
const fs = require('node:fs')
const path = require('node:path')
const arguments = process.argv.slice(2)
const option = (name) => {
  const index = arguments.indexOf(name)
  return index < 0 ? undefined : arguments[index + 1]
}
const cleanupToken = option('--cleanup-private-job')
if (cleanupToken !== undefined) {
  if (fs.existsSync(path.join(process.cwd(), 'hang-cleanup'))) {
    setInterval(() => {}, 1_000)
    return
  }
  fs.writeFileSync(
    path.join(process.cwd(), 'cleanup-' + cleanupToken + '.marker'),
    cleanupToken,
  )
  const cleanupResponsePath = path.join(
    process.cwd(),
    'cleanup-response.bin',
  )
  // Python expands its text newline to CRLF on Windows, matching the real SVS
  // worker. A binary override lets protocol tests preserve every near-miss byte.
  process.stdout.write(
    fs.existsSync(cleanupResponsePath)
      ? fs.readFileSync(cleanupResponsePath)
      : Buffer.from(
        'ELYSIA_SONG_COVER_CLEANUP {"status":"complete"}\r\n',
        'utf8',
      ),
  )
  process.exit(0)
}
const jobDirectory = option('--job-dir')
const source = option('--input') ?? option('--vocal-input')
const emit = (stage, progressPercent, message) => {
  process.stdout.write('ELYSIA_SONG_COVER ' + JSON.stringify({
    stage,
    progressPercent,
    message,
  }) + '\n')
}
if (!fs.existsSync(jobDirectory)) {
  fs.mkdirSync(jobDirectory, { recursive: false })
}
const sourceName = path.basename(source)
const wslSinging = path.basename(process.argv[1]) === 'song_svs_worker.py'
const sourceSvc = wslSinging && option('--engine') === 'source-svc'
const lyricsSvs = wslSinging && !sourceSvc
const shiftAccompaniment = option('--shift-accompaniment')
if (!['true', 'false'].includes(shiftAccompaniment)) {
  process.exit(9)
}
const initialLyricsFiles = wslSinging
  ? fs.readdirSync(jobDirectory).sort()
  : []
if (
  lyricsSvs
  && (
    ['Private Title', 'Private Artist', '第一句'].some(
      (value) => arguments.some((argument) => argument.includes(value)),
    )
    || !initialLyricsFiles.includes('lyrics.lrc')
    || !initialLyricsFiles.includes('lyrics-manifest.json')
    || initialLyricsFiles.some((name) => ![
      'lyrics-manifest.json',
      'lyrics.lrc',
      'lyrics.txt',
    ].includes(name))
  )
) {
  process.exit(8)
}
if (sourceSvc) {
  const request = JSON.parse(fs.readFileSync(path.join(jobDirectory, 'singing-request.json'), 'utf8'))
  if (initialLyricsFiles.join(',') !== 'singing-request.json'
      || request.schema_version !== 1
      || !['fairy', 'elysia'].includes(request.reference)
      || request.key_shift_semitones !== Number(option('--key-shift-semitones'))) {
    process.exit(10)
  }
}
if (
  sourceName === 'isolated-vocals.wav'
  && (
    option('--source-mode') !== 'stems'
    || option('--key-shift-semitones') !== '12'
    || shiftAccompaniment !== 'false'
    || option('--accompaniment-input') !== 'D:/private/instrumental.wav'
  )
) {
  process.exit(4)
}
const privateMessage = sourceName === 'message.mp3'
  ? 'PRIVATE RUNTIME DIAGNOSTIC'
  : 'Reading the selected audio'
if (
  sourceName !== 'skip-stages.mp3'
  && sourceName !== 'silent.mp3'
) {
  emit('validating', 5, privateMessage)
}
if (sourceName === 'alignment-error.mp3') {
  emit('separating', 15, 'Preparing vocals and accompaniment')
  emit('transcribing', 35, 'Transcribing')
  emit('aligning', 52, 'Aligning')
  setTimeout(() => process.exit(2), 200)
} else if (sourceName === 'worker-error.mp3') {
  setTimeout(() => process.exit(2), 200)
} else if (sourceName === 'slow.mp3' || sourceName === 'silent.mp3') {
  setInterval(() => {}, 1_000)
} else if (sourceName === 'overlong-stdout.mp3') {
  process.stdout.write(Buffer.alloc((64 * 1024) + 1, 0x41))
  setInterval(() => {}, 1_000)
} else {
  if (sourceName === 'malformed-event.mp3') {
    process.stdout.write('ELYSIA_SONG_COVER {malformed-json}\n')
  }
  if (sourceName !== 'skip-stages.mp3') {
    emit('separating', 15, 'Preparing vocals and accompaniment')
    if (lyricsSvs) {
      emit('transcribing', 35, 'Transcribing')
      emit('aligning', 52, 'Aligning')
      emit('synthesizing', 58, 'Singing')
    } else {
      emit('converting', 55, 'Converting the vocal melody to Elysia')
      if (sourceSvc && sourceName.startsWith('progress-')) {
        emit('converting', 56, 'PRIVATE stage detail')
        emit('converting', sourceName === 'progress-regress.mp3' ? 55 : 58, 'PRIVATE stage detail')
        emit('converting', sourceName === 'progress-high.mp3' ? 90 : 88, 'PRIVATE stage detail')
      }
    }
    emit('mixing', 90, 'Mixing Elysia with the accompaniment')
  }
  if (wslSinging) {
    for (const name of initialLyricsFiles) {
      fs.rmSync(path.join(jobDirectory, name))
    }
  }
  const pcmBytes = sourceName === 'timeline.mp3' ? 176_400 * 10 : 4
  const wav = Buffer.alloc(44 + pcmBytes)
  wav.write('RIFF', 0, 'ascii')
  wav.writeUInt32LE(36 + pcmBytes, 4)
  wav.write('WAVE', 8, 'ascii')
  wav.write('fmt ', 12, 'ascii')
  wav.writeUInt32LE(16, 16)
  wav.writeUInt16LE(1, 20)
  wav.writeUInt16LE(2, 22)
  wav.writeUInt32LE(44_100, 24)
  wav.writeUInt32LE(176_400, 28)
  wav.writeUInt16LE(4, 32)
  wav.writeUInt16LE(16, 34)
  wav.write('data', 36, 'ascii')
  wav.writeUInt32LE(pcmBytes, 40)
  fs.writeFileSync(
    path.join(jobDirectory, 'elysia-cover.wav'),
    sourceName === 'invalid-wav.mp3' ? 'not-a-wave' : wav,
  )

  const mp3 = Buffer.alloc(427)
  mp3.write('ID3', 0, 'ascii')
  mp3[3] = 4
  mp3[10] = 0xff
  mp3[11] = 0xfb
  mp3[12] = 0x90
  mp3[13] = 0x64
  fs.writeFileSync(
    path.join(jobDirectory, 'elysia-cover.mp3'),
    sourceName === 'invalid-mp3.mp3' ? 'not-an-mp3' : mp3,
  )
  emit('complete', 100, '10.000')
  if (
    sourceName === 'malformed-event.mp3'
    || sourceName === 'skip-stages.mp3'
    || sourceName === 'progress-regress.mp3'
    || sourceName === 'progress-high.mp3'
  ) {
    setInterval(() => {}, 1_000)
  }
}
`

test('parses only exact Song Cover setup requests', () => {
  assert.deepEqual(
    parseChooseSongCoverRequest({
      engine: 'lyrics-svs',
      sourceMode: 'stems',
      keyShiftSemitones: -12,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: {
        title: '  测试歌曲  ',
        artist: ' 测试歌手 ',
      },
    }),
    {
      engine: 'lyrics-svs',
      sourceMode: 'stems',
      keyShiftSemitones: -12,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: {
        title: '测试歌曲',
        artist: '测试歌手',
      },
    },
  )
  assert.deepEqual(
    parseChooseSongCoverRequest({
      engine: 'legacy-svc',
      sourceMode: 'song',
      keyShiftSemitones: 12,
      shiftAccompaniment: false,
      singingReference: null,
      lyricsMetadataOverride: null,
    }),
    {
      engine: 'legacy-svc',
      sourceMode: 'song',
      keyShiftSemitones: 12,
      shiftAccompaniment: false,
      singingReference: null,
      lyricsMetadataOverride: null,
    },
  )
  assert.deepEqual(
    parseChooseSongCoverRequest({
      engine: 'lyrics-svs',
      sourceMode: 'song',
      keyShiftSemitones: 0,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: { title: '  ', artist: '' },
    }),
    {
      engine: 'lyrics-svs',
      sourceMode: 'song',
      keyShiftSemitones: 0,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: null,
    },
  )
  for (const value of [
    null,
    {},
    { sourceMode: 'song' },
    {
      engine: 'lyrics-svs', sourceMode: 'lyrics', keyShiftSemitones: 0,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: null,
    },
    {
      engine: 'other', sourceMode: 'song', keyShiftSemitones: 0,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: null,
    },
    {
      engine: 'lyrics-svs', sourceMode: 'song', keyShiftSemitones: 13,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: null,
    },
    {
      engine: 'lyrics-svs', sourceMode: 'song', keyShiftSemitones: -13,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: null,
    },
    {
      engine: 'lyrics-svs', sourceMode: 'song', keyShiftSemitones: -1.5,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: null,
    },
    {
      engine: 'lyrics-svs', sourceMode: 'song', keyShiftSemitones: 0,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: { title: '只有标题', artist: '' },
    },
    {
      engine: 'legacy-svc', sourceMode: 'song', keyShiftSemitones: 0,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: { title: '歌名', artist: '歌手' },
    },
    {
      engine: 'lyrics-svs', sourceMode: 'song', keyShiftSemitones: 0,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: { title: 'a'.repeat(257), artist: '歌手' },
    },
    {
      engine: 'lyrics-svs', sourceMode: 'song', keyShiftSemitones: 0,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: { title: '歌\n名', artist: '歌手' },
    },
    {
      engine: 'lyrics-svs', sourceMode: 'song', keyShiftSemitones: 0,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: {
        title: '歌名', artist: '歌手', provider: 'unexpected',
      },
    },
    {
      engine: 'lyrics-svs',
      sourceMode: 'song',
      keyShiftSemitones: 0,
      shiftAccompaniment: true,
      singingReference: null,
      lyricsMetadataOverride: null,
      nativePath: 'D:/private',
    },
    {
      engine: 'lyrics-svs', sourceMode: 'song', keyShiftSemitones: 0,
      singingReference: null,
      lyricsMetadataOverride: null,
    },
    {
      engine: 'lyrics-svs', sourceMode: 'song', keyShiftSemitones: 0,
      shiftAccompaniment: 'true', singingReference: null,
      lyricsMetadataOverride: null,
    },
  ]) {
    assert.equal(parseChooseSongCoverRequest(value), null)
  }
})

test('binds singing references exclusively to source conversion requests', () => {
  const request = {
    engine: 'source-svc', sourceMode: 'song', keyShiftSemitones: 12,
    shiftAccompaniment: false, lyricsMetadataOverride: null,
    singingReference: 'elysia',
  }
  assert.deepEqual(parseChooseSongCoverRequest(request), request)
  for (const patch of [
    { singingReference: null }, { singingReference: 'custom' },
    { singingReference: 'D:/private/reference.wav' },
    { engine: 'lyrics-svs' }, { engine: 'legacy-svc' },
    { lyricsMetadataOverride: { title: 'Title', artist: 'Artist' } },
    { cfg: 3 },
  ]) {
    assert.equal(parseChooseSongCoverRequest({ ...request, ...patch }), null)
  }
  const { singingReference: _omitted, ...oldRequest } = request
  assert.equal(parseChooseSongCoverRequest(oldRequest), null)
})

test('parses only the closed renderer-safe Song Cover readiness contract', () => {
  for (const readiness of [
    { status: 'available', reason: null },
    { status: 'unavailable', reason: 'not-included-in-build' },
    { status: 'unavailable', reason: 'local-runtime-unavailable' },
  ]) {
    assert.deepEqual(parseSongCoverReadiness(readiness), readiness)
  }
  for (const malformed of [
    null,
    {},
    { status: 'available' },
    { status: 'available', reason: 'local-runtime-unavailable' },
    { status: 'unavailable', reason: null },
    { status: 'unavailable', reason: 'private-path-missing' },
    {
      status: 'unavailable',
      reason: 'local-runtime-unavailable',
      path: 'D:/private/runtime',
    },
  ]) {
    assert.equal(parseSongCoverReadiness(malformed), null)
  }
})

async function createFixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), 'elysia-song-cover-'))
  const projectRoot = path.join(root, 'project')
  const dataRoot = path.join(root, 'data')
  const python = path.join(
    projectRoot,
    'models',
    'cache',
    'GPT-SoVITS-v2-240821',
    'runtime',
    'python.exe',
  )
  await mkdir(path.dirname(python), { recursive: true })
  await mkdir(path.join(projectRoot, 'scripts'), { recursive: true })
  try {
    await link(process.execPath, python)
  } catch {
    await copyFile(process.execPath, python)
  }
  await chmod(python, 0o755)
  await writeFile(
    path.join(projectRoot, 'scripts', 'song_cover_worker.py'),
    FAKE_WORKER,
    'utf8',
  )
  await writeFile(
    path.join(projectRoot, 'scripts', 'song_svs_worker.py'),
    FAKE_WORKER,
    'utf8',
  )
  return { dataRoot, projectRoot, root }
}

test('fails packaged readiness closed and admits only regular development files', async () => {
  const fixture = await createFixture()
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
  )
  const scriptsRoot = path.join(fixture.projectRoot, 'scripts')
  const runtimeRoot = path.join(
    fixture.projectRoot,
    'models',
    'cache',
    'GPT-SoVITS-v2-240821',
  )
  const requiredFiles = [
    path.join(runtimeRoot, 'ffmpeg.exe'),
    path.join(runtimeRoot, 'ffprobe.exe'),
    path.join(scriptsRoot, 'song_rvc_runtime.py'),
    path.join(scriptsRoot, 'song_rvc_pipeline.py'),
    path.join(scriptsRoot, 'song_svs_wsl_bridge.py'),
    path.join(scriptsRoot, 'song_svs_runtime.py'),
    path.join(scriptsRoot, 'song_svs_inference.py'),
    path.join(scriptsRoot, 'song_svs_quality.py'),
    path.join(scriptsRoot, 'song_svc_inference.py'),
    path.join(scriptsRoot, 'song_svc_assets.py'),
    path.join(scriptsRoot, 'song_svc_pitch.py'),
    path.join(scriptsRoot, 'song_lyrics_alignment.py'),
  ]
  try {
    assert.deepEqual(await manager.getReadiness(true), {
      status: 'unavailable',
      reason: 'not-included-in-build',
    })
    assert.deepEqual(await manager.getReadiness(false), {
      status: 'unavailable',
      reason: 'local-runtime-unavailable',
    })

    await Promise.all(requiredFiles.map(async (filePath) => {
      await mkdir(path.dirname(filePath), { recursive: true })
      await writeFile(filePath, 'fixture', 'utf8')
    }))
    assert.deepEqual(await manager.getReadiness(false), {
      status: 'available',
      reason: null,
    })

    const replacedScript = path.join(scriptsRoot, 'song_rvc_pipeline.py')
    await rm(replacedScript)
    await mkdir(replacedScript)
    assert.deepEqual(await manager.getReadiness(false), {
      status: 'unavailable',
      reason: 'local-runtime-unavailable',
    })
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

async function waitFor(predicate, message, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = predicate()
    if (value) {
      return value
    }
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  throw new Error(message)
}

async function prepareFixtureLyrics({ jobDirectory }) {
  await writeFile(
    path.join(jobDirectory, 'lyrics.lrc'),
    '[00:00.00]测试歌词',
    'utf8',
  )
  await writeFile(
    path.join(jobDirectory, 'lyrics-manifest.json'),
    '{}',
    'utf8',
  )
  return {
    status: 'ready',
    files: ['lyrics.lrc', 'lyrics-manifest.json'],
    manifest: {
      source: 'lrclib',
      recordId: 1,
      confidence: 1,
      hasSyncedLyrics: true,
    },
  }
}

/** Start legacy conversion with an explicit accompaniment-follow preference. */
function startLegacy(
  manager,
  sourcePath,
  sourceName,
  keyShiftSemitones = 0,
  accompaniment = null,
  shiftAccompaniment = true,
) {
  return manager.start(
    sourcePath,
    sourceName,
    keyShiftSemitones,
    accompaniment,
    'legacy-svc',
    null,
    shiftAccompaniment,
  )
}

test('defaults to source conversion with a closed durable singing request', async () => {
  const fixture = await createFixture()
  const launches = []
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => ({ cancel() {}, play: () => Promise.resolve() }),
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined, undefined, undefined, undefined,
    async () => { throw new Error('Source conversion must not request lyrics') },
    (command, args, options) => {
      if (!args.includes('--cleanup-private-job')) {
        const jobDirectory = args[args.indexOf('--job-dir') + 1]
        launches.push({ args: [...args], jobDirectory })
      }
      return spawn(command, args, options)
    },
  )
  try {
    for (const reference of ['fairy', 'elysia']) {
      const started = reference === 'fairy'
        ? await manager.start('D:/private/slow.mp3', 'slow.mp3')
        : await manager.start('D:/private/slow.mp3', 'slow.mp3', 12, null,
          'source-svc', null, false, reference)
      assert.equal(started.engine, 'source-svc')
      const launch = launches.at(-1)
      assert.equal(path.basename(launch.args[0]), 'song_svs_worker.py')
      assert.equal(launch.args[launch.args.indexOf('--engine') + 1], 'source-svc')
      assert.equal(launch.args.includes('--svc-root'), false)
      assert.deepEqual(await readdir(launch.jobDirectory), ['singing-request.json'])
      assert.deepEqual(JSON.parse(await readFile(
        path.join(launch.jobDirectory, 'singing-request.json'), 'utf8',
      )), {
        schema_version: 1, reference,
        key_shift_semitones: reference === 'fairy' ? 0 : 12,
      })
      await manager.cancel(started.jobId)
    }
    await manager.start('D:/private/song.mp3', 'song.mp3')
    await waitFor(() => ['ready', 'error'].includes(manager.getState().stage), 'Source conversion did not finish')
    assert.equal(manager.getState().stage, 'ready', JSON.stringify(manager.getState()))
    assert.equal(manager.getState().outputAvailable, true)
    assert.deepEqual((await readdir(launches.at(-1).jobDirectory)).sort(), [
      'elysia-cover.mp3', 'elysia-cover.wav',
    ])
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('launches the legacy wire engine with fixed private RVC paths', async () => {
  const fixture = await createFixture()
  let launchArguments = null
  let launchEnvironment = null
  let profileExistedAtLaunch = false
  let shellFoldersExistedAtLaunch = false
  let jobExistedAtLaunch = true
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => ({ cancel() {}, play: () => Promise.resolve() }),
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    (command, args, options) => {
      if (path.basename(String(args[0])) === 'song_cover_worker.py') {
        launchArguments = [...args]
        launchEnvironment = { ...options.env }
        profileExistedAtLaunch = existsSync(options.env.USERPROFILE ?? '')
        shellFoldersExistedAtLaunch = (
          existsSync(options.env.LOCALAPPDATA ?? '')
          && existsSync(options.env.APPDATA ?? '')
        )
        jobExistedAtLaunch = existsSync(args[args.indexOf('--job-dir') + 1])
      }
      return spawn(command, args, options)
    },
  )
  try {
    await startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    await waitFor(
      () => manager.getState().outputAvailable && !manager.hasActiveWork(),
      'The RVC-backed Song Cover worker did not finish.',
    )
    assert.notEqual(launchArguments, null)
    assert.notEqual(launchEnvironment, null)
    assert.equal(
      launchEnvironment.USERPROFILE,
      path.join(fixture.dataRoot, 'audio', 'song-covers'),
    )
    assert.equal(launchEnvironment.USERPROFILE, launchEnvironment.TEMP)
    assert.equal(profileExistedAtLaunch, true)
    assert.equal(shellFoldersExistedAtLaunch, true)
    assert.equal(
      launchEnvironment.LOCALAPPDATA,
      path.join(launchEnvironment.USERPROFILE, 'AppData', 'Local'),
    )
    assert.equal(
      launchEnvironment.APPDATA,
      path.join(launchEnvironment.USERPROFILE, 'AppData', 'Roaming'),
    )
    // Import-time home resolution must not pre-create the Legacy UUID job:
    // the worker claims that directory exclusively before accepting assets.
    assert.equal(jobExistedAtLaunch, false)
    for (const name of ['HOME', 'HOMEDRIVE', 'HOMEPATH', 'PYTHONPATH']) {
      assert.equal(Object.hasOwn(launchEnvironment, name), false)
    }
    const option = (name) => {
      const index = launchArguments.indexOf(name)
      return index === -1 ? null : launchArguments[index + 1]
    }
    assert.equal(
      option('--rvc-root'),
      path.join(fixture.projectRoot, 'models', 'cache', 'rvc-v2-40k'),
    )
    assert.equal(option('--shift-accompaniment'), 'true')
    assert.equal(
      option('--rvc-model'),
      path.join(
        fixture.projectRoot,
        'models',
        'weights',
        'rvc',
        'elysia-v2-40k',
        'model.pth',
      ),
    )
    assert.equal(
      option('--rvc-index'),
      path.join(
        fixture.projectRoot,
        'models',
        'weights',
        'rvc',
        'elysia-v2-40k',
        'model.index',
      ),
    )
    assert.equal(launchArguments.includes('--svc-root'), false)
    assert.equal(launchArguments.includes('--svc-model-root'), false)
    assert.equal(manager.getState().engine, 'legacy-svc')
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('rejects a redirected private AppData folder before starting a worker', async () => {
  const fixture = await createFixture()
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
  )
  const outside = path.join(fixture.root, 'outside-profile')
  try {
    await waitFor(
      () => !manager.hasActiveWork(),
      'Startup cleanup did not settle before the redirected profile fixture.',
    )
    const jobsRoot = path.join(fixture.dataRoot, 'audio', 'song-covers')
    await mkdir(jobsRoot, { recursive: true })
    await mkdir(outside)
    await symlink(outside, path.join(jobsRoot, 'AppData'), 'junction')
    await assert.rejects(
      startLegacy(manager, 'D:/private/original.mp3', 'original.mp3'),
      /could not be started/iu,
    )
    assert.equal(manager.getState().stage, 'error')
    assert.equal(manager.getState().outputAvailable, false)
    assert.deepEqual(await readdir(outside), [])
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

/** Build a manager with one narrowly injected export operation. */
function createExportTestManager(fixture, exportOperations = {}) {
  return new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => ({ cancel() {}, play: () => Promise.resolve() }),
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    exportOperations,
  )
}

async function replaceFileIdentity(filePath) {
  const replacementPath = `${filePath}.replacement`
  await writeFile(replacementPath, await readFile(filePath))
  await rm(filePath)
  try {
    await link(replacementPath, filePath)
  } catch {
    await copyFile(replacementPath, filePath)
  }
}

test('parses only exact bounded Song Cover worker progress', () => {
  const parsed = parseSongCoverWorkerEvent({
    stage: 'converting',
    progressPercent: 55,
    message: 'Converting the vocal melody to Elysia',
  })
  assert.deepEqual(parsed, {
    stage: 'converting',
    progressPercent: 55,
    message: 'Converting the vocal melody to Elysia',
  })
  assert.equal(parseSongCoverWorkerEvent({ ...parsed, privatePath: 'D:/song' }), null)
  assert.equal(parseSongCoverWorkerEvent({ ...parsed, progressPercent: 101 }), null)
  assert.equal(parseSongCoverWorkerEvent({ ...parsed, message: 'bad\nline' }), null)
})

test('manager independently rejects lyrics metadata outside its engine contract', async () => {
  const fixture = await createFixture()
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
  )
  try {
    await assert.rejects(
      manager.start(
        'D:/private/song.mp3',
        'song.mp3',
        0,
        null,
        'legacy-svc',
        { title: '歌名', artist: '歌手' },
      ),
      /lyrics metadata is invalid/iu,
    )
    await assert.rejects(
      manager.start(
        'D:/private/song.mp3',
        'song.mp3',
        0,
        null,
        'lyrics-svs',
        { title: '歌\n名', artist: '歌手' },
      ),
      /lyrics metadata is invalid/iu,
    )
    assert.equal(manager.getState().stage, 'idle')
    assert.equal(manager.getState().jobId, null)
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('runs the complete lyrics SVS path with private metadata lookup only', async () => {
  const fixture = await createFixture()
  const prepared = []
  const published = []
  const prepareLyricsAssets = async (input) => {
    prepared.push(input)
    assert.equal(input.signal.aborted, false)
    assert.equal((await stat(input.jobDirectory)).isDirectory(), true)
    await Promise.all([
      writeFile(
        path.join(input.jobDirectory, 'lyrics.lrc'),
        '[00:00.00]第一句\n',
        'utf8',
      ),
      writeFile(
        path.join(input.jobDirectory, 'lyrics.txt'),
        '第一句\n',
        'utf8',
      ),
      writeFile(
        path.join(input.jobDirectory, 'lyrics-manifest.json'),
        '{"source":"lrclib"}\n',
        'utf8',
      ),
    ])
    return {
      status: 'ready',
      files: ['lyrics.lrc', 'lyrics.txt', 'lyrics-manifest.json'],
      manifest: {
        source: 'lrclib',
        recordId: 1,
        confidence: 1,
        hasSyncedLyrics: true,
      },
    }
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => ({ cancel() {}, async play() {} }),
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    (state) => { published.push(state) },
    undefined,
    undefined,
    undefined,
    undefined,
    prepareLyricsAssets,
  )
  try {
    const started = await manager.start(
      'D:/private/ScreenRecording.wav',
      'ScreenRecording.wav',
      0,
      null,
      'lyrics-svs',
      { title: ' Private Title ', artist: ' Private Artist ' },
    )
    const ready = await waitFor(
      () => (
        (
          manager.getState().stage === 'ready'
          || manager.getState().stage === 'error'
        )
        && !manager.hasActiveWork()
        && manager.getState()
      ),
      'Lyrics-driven Song Cover did not finish.',
    )
    assert.equal(ready.stage, 'ready', JSON.stringify(ready))
    assert.equal(ready.jobId, started.jobId)
    assert.equal(ready.engine, 'lyrics-svs')
    assert.equal(prepared.length, 1)
    assert.equal(prepared[0].sourcePath, 'D:/private/ScreenRecording.wav')
    assert.deepEqual(prepared[0].metadataOverride, {
      title: 'Private Title',
      artist: 'Private Artist',
    })
    const publishedStages = published
      .map((state) => state.stage)
      .filter((stage, index, stages) => index === 0 || stage !== stages[index - 1])
    assert.deepEqual(
      publishedStages.slice(0, 7),
      [
        'validating',
        'separating',
        'transcribing',
        'aligning',
        'synthesizing',
        'mixing',
        'ready',
      ],
    )
    assert.equal(JSON.stringify(published).includes('Private Title'), false)
    assert.equal(JSON.stringify(published).includes('第一句'), false)
    assert.deepEqual(
      (await readdir(path.join(
        fixture.dataRoot,
        'audio',
        'song-covers',
        started.jobId,
      ))).sort(),
      ['elysia-cover.mp3', 'elysia-cover.wav'],
    )
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('rejects lyrics SVS before spawn when synchronized lyrics are absent', async () => {
  const fixture = await createFixture()
  const prepareLyricsAssets = async (input) => {
    await writeFile(
      path.join(input.jobDirectory, 'lyrics-manifest.json'),
      '{"source":"lrclib"}\n',
      'utf8',
    )
    return {
      status: 'ready',
      files: ['lyrics-manifest.json'],
      manifest: {
        source: 'lrclib',
        recordId: 1,
        confidence: 1,
        hasSyncedLyrics: false,
      },
    }
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined,
    undefined,
    undefined,
    undefined,
    prepareLyricsAssets,
  )
  try {
    await assert.rejects(
      manager.start(
        'D:/private/generic-vocals.wav',
        'generic-vocals.wav',
        0,
        null,
        'lyrics-svs',
        { title: '歌名', artist: '歌手' },
      ),
      /No synchronized lyrics/iu,
    )
    assert.equal(manager.hasActiveWork(), false)
    assert.equal(manager.hasManagedOutput(), false)
    assert.equal(manager.getState().errorCode, 'lyrics-no-sync')
    const jobsRoot = path.join(fixture.dataRoot, 'audio', 'song-covers')
    assert.deepEqual(await readdir(jobsRoot), [])
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('publishes closed startup error codes without provider diagnostics', async () => {
  const scenarios = [
    {
      expectedCode: 'invalid-audio',
      prepare: async () => ({ status: 'invalid-audio' }),
    },
    {
      expectedCode: 'lyrics-no-match',
      prepare: async () => ({ status: 'low-confidence' }),
    },
    {
      expectedCode: 'lyrics-needs-metadata',
      prepare: async () => ({ status: 'needs-metadata' }),
    },
    {
      expectedCode: 'lyrics-network',
      prepare: async () => {
        throw new Error('Online lyrics could not be retrieved safely.')
      },
    },
  ]
  for (const scenario of scenarios) {
    const fixture = await createFixture()
    const manager = new SongCoverManager(
      fixture.projectRoot,
      () => fixture.dataRoot,
      () => null,
      () => {},
      async () => ({ outputDeviceId: null, volumePercent: 100 }),
      () => {},
      undefined,
      undefined,
      undefined,
      undefined,
      scenario.prepare,
    )
    try {
      await assert.rejects(manager.start(
        'D:/private/song.mp3',
        'song.mp3',
        0,
        null,
        'lyrics-svs',
        { title: '歌名', artist: '歌手' },
      ))
      assert.equal(manager.getState().stage, 'error')
      assert.equal(manager.getState().errorCode, scenario.expectedCode)
      assert.equal(JSON.stringify(manager.getState()).includes(fixture.root), false)
    } finally {
      await manager.shutdown()
      await rm(fixture.root, { recursive: true, force: true })
    }
  }
})

test('classifies alignment failures separately from other runtime failures', async () => {
  const alignmentFixture = await createFixture()
  const alignmentManager = new SongCoverManager(
    alignmentFixture.projectRoot,
    () => alignmentFixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined,
    undefined,
    undefined,
    undefined,
    prepareFixtureLyrics,
  )
  try {
    await alignmentManager.start(
      'D:/private/alignment-error.mp3',
      'alignment-error.mp3',
      0,
      null,
      'lyrics-svs',
    )
    const alignmentFailure = await waitFor(
      () => !alignmentManager.hasActiveWork() && alignmentManager.getState(),
      'Alignment failure did not settle.',
    )
    assert.equal(alignmentFailure.stage, 'error')
    assert.equal(alignmentFailure.errorCode, 'lyrics-alignment')
  } finally {
    await alignmentManager.shutdown()
    await rm(alignmentFixture.root, { recursive: true, force: true })
  }

  const runtimeFixture = await createFixture()
  const runtimeManager = new SongCoverManager(
    runtimeFixture.projectRoot,
    () => runtimeFixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
  )
  try {
    await startLegacy(
      runtimeManager,
      'D:/private/worker-error.mp3',
      'worker-error.mp3',
    )
    const runtimeFailure = await waitFor(
      () => !runtimeManager.hasActiveWork() && runtimeManager.getState(),
      'Runtime failure did not settle.',
    )
    assert.equal(runtimeFailure.stage, 'error')
    assert.equal(runtimeFailure.errorCode, 'singing-runtime')
  } finally {
    await runtimeManager.shutdown()
    await rm(runtimeFixture.root, { recursive: true, force: true })
  }
})

test('creates, privately plays, exports, and removes one completed cover', async () => {
  const fixture = await createFixture()
  let releasePlayback
  const playback = new Promise((resolve) => {
    releasePlayback = resolve
  })
  const plays = []
  const published = []
  const owner = {
    cancel() {},
    play(bytes, outputDeviceId, volumePercent) {
      plays.push({ bytes: [...bytes], outputDeviceId, volumePercent })
      return playback
    },
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => owner,
    () => {},
    async () => ({ outputDeviceId: 'speaker-fixture', volumePercent: 64 }),
    (state) => { published.push(state) },
  )
  try {
    await startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    const playing = await waitFor(
      () => manager.getState().stage === 'playing' && manager.getState(),
      'Song Cover did not reach private playback.',
    )
    assert.match(playing.jobId, /^[0-9a-f-]{36}$/u)
    assert.equal(playing.sourceName, 'original.mp3')
    assert.equal(playing.outputAvailable, true)
    assert.equal(manager.hasManagedOutput(), true)
    assert.equal(plays.length, 1)
    assert.equal(Buffer.from(plays[0].bytes).subarray(0, 3).toString('ascii'), 'ID3')
    assert.equal(plays[0].outputDeviceId, 'speaker-fixture')
    assert.equal(plays[0].volumePercent, 64)
    assert.equal(
      JSON.stringify(published).includes('D:/private'),
      false,
    )

    const exported = path.join(fixture.root, 'exported.wav')
    await writeFile(exported, 'existing user export', 'utf8')
    await manager.exportWav(playing.jobId, exported)
    const exportedBytes = await readFile(exported)
    assert.equal(exportedBytes.subarray(0, 4).toString('ascii'), 'RIFF')
    assert.equal(exportedBytes.subarray(8, 12).toString('ascii'), 'WAVE')
    assert.equal(exportedBytes.readUInt16LE(20), 1)
    assert.equal(exportedBytes.readUInt16LE(22), 2)
    assert.equal(exportedBytes.readUInt32LE(24), 44_100)
    assert.equal(exportedBytes.readUInt16LE(34), 16)
    await assert.rejects(
      manager.exportWav('00000000-0000-4000-8000-000000000000', exported),
      /no longer|before exporting/iu,
    )

    releasePlayback()
    await waitFor(
      () => manager.getState().stage === 'ready',
      'Song Cover did not settle to ready.',
    )
    const outputDirectory = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      playing.jobId,
    )
    assert.equal((await stat(outputDirectory)).isDirectory(), true)
    await manager.shutdown()
    assert.equal(manager.hasManagedOutput(), false)
    await assert.rejects(stat(outputDirectory))
  } finally {
    releasePlayback?.()
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('keeps a validated timeline through ready seek, playback, stop, and completion', async () => {
  const fixture = await createFixture()
  const plays = []
  const seeks = []
  let preferencesAvailable = false
  let acceptSeek = true
  const owner = {
    cancel() { plays.at(-1)?.resolve() },
    seek(position) { seeks.push(position); return acceptSeek },
    play(_bytes, _device, _volume, startPosition, onTimeline) {
      return new Promise((resolve) => plays.push({ startPosition, onTimeline, resolve }))
    },
  }
  const manager = new SongCoverManager(
    fixture.projectRoot, () => fixture.dataRoot, () => owner, () => {},
    async () => {
      if (!preferencesAvailable) throw new Error('Autoplay unavailable')
      return { outputDeviceId: null, volumePercent: 100 }
    },
    () => {},
  )
  try {
    assert.equal(manager.getState().positionSeconds, 0)
    assert.equal(manager.getState().durationSeconds, 0)
    const started = await startLegacy(manager, 'D:/private/timeline.mp3', 'timeline.mp3')
    await waitFor(() => !manager.hasActiveWork(), 'Conversion did not release its slot')
    assert.equal(manager.getState().outputAvailable, true)
    assert.equal(manager.getState().durationSeconds, 10)
    assert.equal(plays.length, 0)
    for (const position of [-1, NaN, Infinity, 10.01]) {
      assert.throws(() => manager.seek(started.jobId, position), /position is invalid/u)
    }
    assert.throws(() => manager.seek('00000000-0000-4000-8000-000000000000', 1), /no longer/u)
    const revision = manager.getState().revision
    assert.equal(manager.seek(started.jobId, 4).positionSeconds, 4)
    assert.equal(manager.getState().revision, revision + 1)
    assert.deepEqual(seeks, [])
    preferencesAvailable = true
    await manager.play(started.jobId)
    assert.equal(plays[0].startPosition, 4)
    plays[0].onTimeline({ positionSeconds: 4.5, durationSeconds: 10.02 })
    assert.equal(manager.getState().positionSeconds, 4.5)
    assert.equal(manager.getState().durationSeconds, 10.02)
    assert.equal(manager.seek(started.jobId, 7).positionSeconds, 7)
    assert.deepEqual(seeks, [7])
    acceptSeek = false
    assert.throws(() => manager.seek(started.jobId, 8), /could not seek/u)
    assert.equal(manager.getState().positionSeconds, 7)
    acceptSeek = true
    assert.equal(manager.stopPlayback(started.jobId).positionSeconds, 7)
    plays[0].onTimeline({ positionSeconds: 2, durationSeconds: 10.02 })
    await Promise.resolve()
    assert.equal(manager.getState().stage, 'ready')
    assert.equal(manager.getState().positionSeconds, 7)
    await manager.play(started.jobId)
    assert.equal(plays[1].startPosition, 7)
    plays[1].resolve()
    await waitFor(() => manager.getState().stage === 'ready', 'Natural end did not settle')
    assert.equal(manager.getState().positionSeconds, 0)
    manager.seek(started.jobId, manager.getState().durationSeconds)
    await manager.play(started.jobId)
    assert.equal(plays[2].startPosition, 0)
    manager.stopPlayback(started.jobId)
    const replacement = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    assert.equal(manager.getState().durationSeconds, 0)
    assert.equal(manager.getState().positionSeconds, 0)
    plays[2].onTimeline({ positionSeconds: 9, durationSeconds: 10 })
    assert.equal(manager.getState().durationSeconds, 0)
    assert.throws(() => manager.seek(started.jobId, 0), /no longer/u)
    await manager.cancel(replacement.jobId)
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('publishes bounded source-conversion progress with safe phase messages', async () => {
  const fixture = await createFixture()
  const published = []
  const manager = new SongCoverManager(
    fixture.projectRoot, () => fixture.dataRoot,
    () => ({ cancel() {}, play: () => Promise.resolve() }), () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    (state) => published.push(state),
  )
  try {
    await manager.start('D:/private/progress-good.mp3', 'progress-good.mp3')
    await waitFor(() => !manager.hasActiveWork(), 'Source conversion did not finish')
    assert.equal(manager.getState().stage, 'ready')
    const converting = published.filter((state) => state.stage === 'converting')
    assert.deepEqual(converting.map((state) => state.progressPercent), [55, 56, 58, 88])
    assert.deepEqual(converting.map((state) => state.message), [
      'Preparing singing voice', 'Reading source vocal',
      'Checking vocal pitch', 'Preparing converted vocal for mixing',
    ])
    assert.equal(JSON.stringify(published).includes('PRIVATE'), false)
    for (const sourceName of ['progress-regress.mp3', 'progress-high.mp3']) {
      await manager.start(`D:/private/${sourceName}`, sourceName)
      await waitFor(() => !manager.hasActiveWork(), 'Invalid progress did not settle')
      assert.equal(manager.getState().stage, 'error')
      assert.equal(manager.getState().outputAvailable, false)
    }
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('preserves an existing export when a transactional export fails', async (t) => {
  const failureCases = [
    {
      name: 'source read',
      operations: {
        read: async () => { throw new Error('simulated source read failure') },
      },
    },
    {
      name: 'temporary write',
      operations: {
        write: async () => { throw new Error('simulated temporary write failure') },
      },
    },
    {
      name: 'atomic publish before replacement',
      operations: {
        rename: async () => { throw new Error('simulated publish failure') },
      },
    },
    {
      name: 'uncertain publish after replacement',
      operations: (() => {
        let renameCalls = 0
        return {
          rename: async (oldPath, newPath) => {
            renameCalls += 1
            await renamePath(oldPath, newPath)
            if (renameCalls === 1) {
              throw new Error('simulated uncertain publish result')
            }
          },
        }
      })(),
    },
  ]

  for (const failureCase of failureCases) {
    await t.test(failureCase.name, async () => {
      const fixture = await createFixture()
      const manager = createExportTestManager(
        fixture,
        failureCase.operations,
      )
      const destination = path.join(fixture.root, 'existing-export.wav')
      const original = Buffer.from(`original bytes: ${failureCase.name}`, 'utf8')
      await writeFile(destination, original)
      try {
        const started = await startLegacy(
          manager,
          'D:/private/original.mp3',
          'original.mp3',
        )
        await waitFor(
          () => !manager.hasActiveWork() && manager.getState().stage === 'ready',
          'Song Cover did not settle before the transactional export test.',
        )

        await assert.rejects(
          manager.exportWav(started.jobId, destination),
          /could not be exported/iu,
        )
        assert.deepEqual(await readFile(destination), original)
        assert.equal(
          (await readdir(fixture.root)).some(
            (name) => name.startsWith('.elysia-song-cover-'),
          ),
          false,
        )
        assert.equal(manager.getState().outputAvailable, true)
      } finally {
        await manager.shutdown()
        await rm(fixture.root, { recursive: true, force: true })
      }
    })
  }
})

test('keeps stem paths private and applies only a closed whole-song key shift', async () => {
  const fixture = await createFixture()
  const published = []
  const owner = {
    cancel() {},
    async play() {},
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => owner,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 80 }),
    (state) => { published.push(state) },
  )
  try {
    await startLegacy(
      manager,
      'D:/private/isolated-vocals.wav',
      'isolated-vocals.wav',
      12,
      {
        path: 'D:/private/instrumental.wav',
        name: 'instrumental.wav',
      },
      false,
    )
    const ready = await waitFor(
      () => (
        manager.getState().stage === 'ready'
        && !manager.hasActiveWork()
        && manager.getState()
      ),
      'Stem-based Song Cover did not finish.',
    )
    assert.equal(ready.sourceMode, 'stems')
    assert.equal(ready.sourceName, 'isolated-vocals.wav')
    assert.equal(ready.accompanimentName, 'instrumental.wav')
    assert.equal(ready.keyShiftSemitones, 12)
    assert.equal(ready.shiftAccompaniment, false)
    assert.equal(JSON.stringify(published).includes('D:/private'), false)
    await assert.rejects(
      startLegacy(
        manager,
        'D:/private/same.wav',
        'same.wav',
        0,
        { path: 'd:/PRIVATE/same.wav', name: 'same.wav' },
      ),
      /different supported accompaniment/iu,
    )
    await assert.rejects(
      startLegacy(manager, 'D:/private/original.mp3', 'original.mp3', 13),
      /key adjustment/iu,
    )
    await assert.rejects(
      startLegacy(manager, 'D:/private/original.mp3', 'original.mp3', -13),
      /key adjustment/iu,
    )
    await assert.rejects(
      manager.start(
        'D:/private/original.mp3',
        'original.mp3',
        0,
        null,
        'legacy-svc',
        null,
        'true',
      ),
      /accompaniment adjustment/iu,
    )
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('rejects a replaced MP3 identity before playback without leaking paths', async () => {
  const fixture = await createFixture()
  const owner = {
    cancel() {},
    play() {
      return Promise.resolve()
    },
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => owner,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
  )
  try {
    const started = await startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    await waitFor(
      () => !manager.hasActiveWork() && manager.getState().stage === 'ready',
      'Song Cover did not settle before the MP3 replacement test.',
    )
    const mp3Path = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      started.jobId,
      'elysia-cover.mp3',
    )
    const outputDirectory = path.dirname(mp3Path)
    await replaceFileIdentity(mp3Path)

    await assert.rejects(manager.play(started.jobId), (error) => {
      assert.equal(error.message, 'The Song Cover could not be played.')
      assert.equal(error.message.includes(fixture.root), false)
      assert.equal(error.message.includes(mp3Path), false)
      return true
    })
    assert.equal(manager.getState().stage, 'error')
    assert.equal(manager.getState().outputAvailable, false)
    assert.equal(manager.hasManagedOutput(), false)
    await assert.rejects(stat(outputDirectory))
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('retains cleanup ownership when an invalid preview directory is locked', async () => {
  const fixture = await createFixture()
  let blockedDirectory = null
  const removePath = async (target, options) => {
    if (
      blockedDirectory !== null
      && path.resolve(String(target)) === blockedDirectory
    ) {
      throw new Error('simulated invalid-preview cleanup failure')
    }
    await rm(target, options)
  }
  const owner = {
    cancel() {},
    play() {
      return Promise.resolve()
    },
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => owner,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined,
    removePath,
  )
  try {
    const started = await startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    await waitFor(
      () => !manager.hasActiveWork() && manager.getState().stage === 'ready',
      'Song Cover did not settle before the locked-preview test.',
    )
    const outputDirectory = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      started.jobId,
    )
    await replaceFileIdentity(path.join(outputDirectory, 'elysia-cover.mp3'))
    blockedDirectory = path.resolve(outputDirectory)

    await assert.rejects(
      manager.play(started.jobId),
      /could not be played/iu,
    )
    assert.equal(manager.getState().error, (
      'Private Song Cover audio could not be cleared. Close Elysia and try again.'
    ))
    assert.equal(manager.hasManagedOutput(), true)
    assert.equal((await stat(outputDirectory)).isDirectory(), true)

    blockedDirectory = null
    await manager.discardOutput()
    assert.equal(manager.hasManagedOutput(), false)
    await assert.rejects(stat(outputDirectory))
  } finally {
    blockedDirectory = null
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('does not let delayed invalid-preview cleanup overwrite a replacement', async () => {
  const fixture = await createFixture()
  let delayedDirectory = null
  let delayedAttempts = 0
  let releaseDelayedCleanup
  let reportDelayedCleanup
  const cleanupStarted = new Promise((resolve) => {
    reportDelayedCleanup = resolve
  })
  const cleanupGate = new Promise((resolve) => {
    releaseDelayedCleanup = resolve
  })
  const removePath = async (target, options) => {
    if (
      delayedDirectory !== null
      && path.resolve(String(target)) === delayedDirectory
    ) {
      delayedAttempts += 1
      if (delayedAttempts === 1) {
        reportDelayedCleanup()
        await cleanupGate
      }
    }
    await rm(target, options)
  }
  const owner = {
    cancel() {},
    play() {
      return Promise.resolve()
    },
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => owner,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined,
    removePath,
  )
  try {
    const original = await startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    await waitFor(
      () => !manager.hasActiveWork() && manager.getState().stage === 'ready',
      'The original Song Cover did not settle before the cleanup race.',
    )
    const outputDirectory = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      original.jobId,
    )
    await replaceFileIdentity(path.join(outputDirectory, 'elysia-cover.mp3'))
    delayedDirectory = path.resolve(outputDirectory)

    const failedPlayback = manager.play(original.jobId)
    await cleanupStarted
    const replacement = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    releaseDelayedCleanup()
    await assert.rejects(failedPlayback, /could not be played/iu)
    await waitFor(
      () => manager.getState().progressPercent === 5,
      'The replacement worker did not remain current after old cleanup.',
    )
    assert.equal(manager.getState().jobId, replacement.jobId)
    assert.notEqual(manager.getState().stage, 'error')
    await manager.cancel(replacement.jobId)
  } finally {
    releaseDelayedCleanup?.()
    delayedDirectory = null
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('publishes invalid output after pending playback is stopped', async () => {
  const fixture = await createFixture()
  let delayedDirectory = null
  let releaseDelayedCleanup
  let reportDelayedCleanup
  const cleanupStarted = new Promise((resolve) => {
    reportDelayedCleanup = resolve
  })
  const cleanupGate = new Promise((resolve) => {
    releaseDelayedCleanup = resolve
  })
  const removePath = async (target, options) => {
    if (
      delayedDirectory !== null
      && path.resolve(String(target)) === delayedDirectory
    ) {
      reportDelayedCleanup()
      await cleanupGate
    }
    await rm(target, options)
  }
  const owner = {
    cancel() {},
    play() {
      return Promise.resolve()
    },
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => owner,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined,
    removePath,
  )
  try {
    const started = await startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    await waitFor(
      () => !manager.hasActiveWork() && manager.getState().stage === 'ready',
      'Song Cover did not settle before the stopped-playback test.',
    )
    const outputDirectory = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      started.jobId,
    )
    await replaceFileIdentity(path.join(outputDirectory, 'elysia-cover.mp3'))
    delayedDirectory = path.resolve(outputDirectory)

    const failedPlayback = manager.play(started.jobId)
    await cleanupStarted
    assert.equal(manager.stopPlayback(started.jobId).stage, 'ready')
    releaseDelayedCleanup()
    await assert.rejects(failedPlayback, /could not be played/iu)
    assert.equal(manager.getState().stage, 'error')
    assert.equal(manager.getState().outputAvailable, false)
    assert.equal(manager.hasManagedOutput(), false)
  } finally {
    releaseDelayedCleanup?.()
    delayedDirectory = null
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('rejects a replaced WAV hard-link identity on export without leaking paths', async () => {
  const fixture = await createFixture()
  const owner = {
    cancel() {},
    play() {
      return Promise.resolve()
    },
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => owner,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
  )
  try {
    const started = await startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    await waitFor(
      () => !manager.hasActiveWork() && manager.getState().stage === 'ready',
      'Song Cover did not settle before the WAV replacement test.',
    )
    const wavPath = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      started.jobId,
      'elysia-cover.wav',
    )
    const exportPath = path.join(fixture.root, 'replaced-export.wav')
    await replaceFileIdentity(wavPath)

    await assert.rejects(
      manager.exportWav(started.jobId, exportPath),
      (error) => {
        assert.equal(error.message, 'The Song Cover could not be exported.')
        assert.equal(error.message.includes(fixture.root), false)
        assert.equal(error.message.includes(wavPath), false)
        assert.equal(error.message.includes(exportPath), false)
        return true
      },
    )
    await assert.rejects(stat(exportPath))
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('rejects a hard-link export alias without truncating the managed WAV', async () => {
  const fixture = await createFixture()
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => ({ cancel() {}, play: () => Promise.resolve() }),
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
  )
  try {
    const started = await startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    await waitFor(
      () => !manager.hasActiveWork() && manager.getState().stage === 'ready',
      'Song Cover did not settle before the export-alias test.',
    )
    const sourcePath = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      started.jobId,
      'elysia-cover.wav',
    )
    const aliasPath = path.join(fixture.root, 'managed-wave-alias.wav')
    await link(sourcePath, aliasPath)
    const originalBytes = await readFile(sourcePath)

    await assert.rejects(
      manager.exportWav(started.jobId, aliasPath),
      /could not be exported/iu,
    )
    assert.deepEqual(await readFile(sourcePath), originalBytes)
    assert.equal(manager.getState().outputAvailable, true)
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('times out a silent worker, releases its slot, and removes its private job', {
  timeout: 30_000,
}, async () => {
  const fixture = await createFixture()
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    250,
  )
  try {
    const started = await startLegacy(manager, 'D:/private/silent.mp3', 'silent.mp3')
    const jobDirectory = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      started.jobId,
    )
    const failed = await waitFor(
      () => (
        !manager.hasActiveWork()
        && manager.getState().stage === 'error'
        && manager.getState()
      ),
      'Silent Song Cover worker did not time out and release its slot.',
      15_000,
    )
    assert.equal(failed.stage, 'error')
    assert.equal(failed.outputAvailable, false)
    assert.equal(failed.error, 'Song Cover took too long and was stopped.')
    await assert.rejects(stat(jobDirectory))

    const replacement = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    assert.equal(manager.hasActiveWork(), true)
    await manager.cancel(replacement.jobId)
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

for (const engine of ['lyrics-svs', 'source-svc']) {
for (const scenario of [
  { name: 'cancellation', source: 'slow.mp3', timeoutMs: 10_000 },
  { name: 'timeout', source: 'silent.mp3', timeoutMs: 250 },
  { name: 'bridge crash', source: 'worker-error.mp3', timeoutMs: 10_000 },
]) {
  test(`cleans the exact private WSL job after ${engine} ${scenario.name}`, {
    timeout: 30_000,
  }, async () => {
    const fixture = await createFixture()
    const manager = new SongCoverManager(
      fixture.projectRoot,
      () => fixture.dataRoot,
      () => null,
      () => {},
      async () => ({ outputDeviceId: null, volumePercent: 100 }),
      () => {},
      scenario.timeoutMs,
      undefined,
      null,
      5_000,
      prepareFixtureLyrics,
    )
    try {
      const started = await manager.start(
        `D:/private/${scenario.source}`,
        scenario.source,
        0,
        null,
        engine,
      )
      if (scenario.name === 'cancellation') {
        const cancelled = await manager.cancel(started.jobId)
        assert.equal(cancelled.stage, 'cancelled')
      } else {
        await waitFor(
          () => !manager.hasActiveWork() && manager.getState().stage === 'error',
          `Lyrics-SVS ${scenario.name} did not settle.`,
          15_000,
        )
      }
      assert.equal(
        await readFile(
          path.join(
            fixture.projectRoot,
            `cleanup-${started.jobId}.marker`,
          ),
          'utf8',
        ),
        started.jobId,
      )
      await assert.rejects(stat(path.join(
        fixture.dataRoot,
        'audio',
        'song-covers',
        started.jobId,
      )))
    } finally {
      await manager.shutdown()
      await rm(fixture.root, { recursive: true, force: true })
    }
  })
}

}

for (const manifest of ['lyrics-manifest.json', 'singing-request.json']) {
test(`recovers an exact crash-left WSL UUID from ${manifest}`, async () => {
  const fixture = await createFixture()
  const token = '12345678-1234-4234-9234-123456789abc'
  const jobDirectory = path.join(
    fixture.dataRoot,
    'audio',
    'song-covers',
    token,
  )
  await mkdir(jobDirectory, { recursive: true })
  await writeFile(
    path.join(jobDirectory, manifest),
    '{}',
    'utf8',
  )
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
  )
  try {
    await waitFor(
      () => !manager.hasActiveWork(),
      'Crash-left lyrics-SVS startup cleanup did not settle.',
      15_000,
    )
    assert.equal(
      await readFile(
        path.join(fixture.projectRoot, `cleanup-${token}.marker`),
        'utf8',
      ),
      token,
    )
    await assert.rejects(stat(jobDirectory))
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

}

for (const scenario of [
  {
    name: 'LF',
    frame: 'ELYSIA_SONG_COVER_CLEANUP {"status":"complete"}\n',
  },
  {
    name: 'Windows CRLF',
    frame: 'ELYSIA_SONG_COVER_CLEANUP {"status":"complete"}\r\n',
  },
]) {
  test(`accepts the exact ${scenario.name} private cleanup frame`, async () => {
    const fixture = await createFixture()
    const token = '12345678-1234-4234-9234-123456789abd'
    const jobDirectory = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      token,
    )
    await mkdir(jobDirectory, { recursive: true })
    await writeFile(
      path.join(jobDirectory, 'lyrics-manifest.json'),
      '{}',
      'utf8',
    )
    await writeFile(
      path.join(fixture.projectRoot, 'cleanup-response.bin'),
      scenario.frame,
      'utf8',
    )
    const manager = new SongCoverManager(
      fixture.projectRoot,
      () => fixture.dataRoot,
      () => null,
      () => {},
      async () => ({ outputDeviceId: null, volumePercent: 100 }),
      () => {},
    )
    try {
      await waitFor(
        () => !manager.hasActiveWork(),
        `${scenario.name} private cleanup did not settle.`,
        15_000,
      )
      assert.equal(manager.hasManagedOutput(), false)
      await assert.rejects(stat(jobDirectory))
    } finally {
      await manager.shutdown()
      await rm(fixture.root, { recursive: true, force: true })
    }
  })
}

for (const scenario of [
  {
    name: 'whitespace before Windows CRLF',
    frame: 'ELYSIA_SONG_COVER_CLEANUP {"status":"complete"} \r\n',
  },
  {
    name: 'an extra byte after Windows CRLF',
    frame: 'ELYSIA_SONG_COVER_CLEANUP {"status":"complete"}\r\nx',
  },
]) {
  test(`rejects a private cleanup frame with ${scenario.name}`, async () => {
    const fixture = await createFixture()
    const token = '12345678-1234-4234-9234-123456789abe'
    const jobDirectory = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      token,
    )
    const responsePath = path.join(
      fixture.projectRoot,
      'cleanup-response.bin',
    )
    await mkdir(jobDirectory, { recursive: true })
    await writeFile(
      path.join(jobDirectory, 'lyrics-manifest.json'),
      '{}',
      'utf8',
    )
    await writeFile(responsePath, scenario.frame, 'utf8')
    const manager = new SongCoverManager(
      fixture.projectRoot,
      () => fixture.dataRoot,
      () => null,
      () => {},
      async () => ({ outputDeviceId: null, volumePercent: 100 }),
      () => {},
    )
    try {
      await waitFor(
        () => !manager.hasActiveWork(),
        `Invalid private cleanup with ${scenario.name} did not settle.`,
        15_000,
      )
      assert.equal(manager.hasManagedOutput(), true)
      assert.equal((await stat(jobDirectory)).isDirectory(), true)

      await rm(responsePath, { force: true })
      await manager.shutdown()
      assert.equal(manager.hasManagedOutput(), false)
      await assert.rejects(stat(jobDirectory))
    } finally {
      await rm(responsePath, { force: true })
      await manager.shutdown().catch(() => {})
      await rm(fixture.root, { recursive: true, force: true })
    }
  })
}

test('tree-stops a timed-out cleanup before retaining its exact retry lease', {
  timeout: 30_000,
}, async () => {
  const fixture = await createFixture()
  const hangMarker = path.join(fixture.projectRoot, 'hang-cleanup')
  await writeFile(hangMarker, 'hang', 'utf8')
  let terminationAttempts = 0
  const terminateProcessTree = async (child) => {
    terminationAttempts += 1
    if (child.exitCode !== null || child.signalCode !== null) {
      return true
    }
    return new Promise((resolve) => {
      const timeout = setTimeout(() => { resolve(false) }, 2_000)
      child.once('close', () => {
        clearTimeout(timeout)
        resolve(true)
      })
      try {
        child.kill('SIGKILL')
      } catch {
        clearTimeout(timeout)
        resolve(false)
      }
    })
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    10_000,
    undefined,
    terminateProcessTree,
    5_000,
    prepareFixtureLyrics,
    undefined,
    100,
  )
  let jobDirectory = null
  let jobId = null
  try {
    const started = await manager.start(
      'D:/private/slow.mp3',
      'slow.mp3',
      0,
      null,
      'lyrics-svs',
    )
    jobId = started.jobId
    jobDirectory = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      started.jobId,
    )
    await assert.rejects(
      manager.cancel(started.jobId),
      /private Song Cover audio could not be cleared/iu,
    )
    assert.equal(terminationAttempts, 2)
    assert.equal((await stat(jobDirectory)).isDirectory(), true)
    assert.equal(manager.hasManagedOutput(), true)

    await rm(hangMarker, { force: true })
    await manager.shutdown()
    assert.equal(manager.hasManagedOutput(), false)
    await assert.rejects(stat(jobDirectory))
    assert.equal(
      await readFile(
        path.join(fixture.projectRoot, `cleanup-${jobId}.marker`),
        'utf8',
      ),
      jobId,
    )
  } finally {
    await rm(hangMarker, { force: true })
    await manager.shutdown().catch(() => {})
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('rejects overlong unterminated worker stdout and releases its slot', {
  timeout: 30_000,
}, async () => {
  const fixture = await createFixture()
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
  )
  try {
    const started = await startLegacy(
      manager,
      'D:/private/overlong-stdout.mp3',
      'overlong-stdout.mp3',
    )
    const failed = await waitFor(
      () => (
        !manager.hasActiveWork()
        && manager.getState().stage === 'error'
        && manager.getState()
      ),
      'Overlong Song Cover stdout did not fail and release its slot.',
      15_000,
    )
    assert.equal(failed.stage, 'error')
    assert.equal(failed.outputAvailable, false)
    assert.equal(
      failed.error,
      'The Song Cover worker returned an invalid response.',
    )
    const jobDirectory = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      started.jobId,
    )
    await assert.rejects(stat(jobDirectory))
    assert.equal(manager.hasActiveWork(), false)

    const replacement = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    assert.equal(manager.hasActiveWork(), true)
    await manager.cancel(replacement.jobId)
    assert.equal(manager.hasActiveWork(), false)
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('settles failed startup cleanup and rejects the first job safely', async () => {
  const fixture = await createFixture()
  const startupRoot = path.resolve(path.join(
    fixture.dataRoot,
    'audio',
    'song-covers',
  ))
  let blockStartupCleanup = true
  const removePath = async (target, options) => {
    if (
      blockStartupCleanup
      && path.resolve(String(target)) === startupRoot
    ) {
      throw new Error('simulated startup cleanup failure')
    }
    await rm(target, options)
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined,
    removePath,
  )
  try {
    await assert.rejects(
      startLegacy(manager, 'D:/private/original.mp3', 'original.mp3'),
      new Error(
        'Private Song Cover audio could not be cleared. Close Elysia and try again.',
      ),
    )
    assert.equal(manager.getState().stage, 'error')
    assert.equal(manager.hasActiveWork(), false)
    assert.equal(manager.hasManagedOutput(), true)
  } finally {
    // Shutdown must retry the unresolved lease. Releasing the injected fault
    // proves that retry settles instead of making the fixture platform-specific.
    blockStartupCleanup = false
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('tracks and retries failed startup cleanup before admitting audio', async () => {
  const fixture = await createFixture()
  const startupRoot = path.resolve(path.join(
    fixture.dataRoot,
    'audio',
    'song-covers',
  ))
  let cleanupAttempts = 0
  let releaseFirstCleanup
  let reportCleanupStarted
  const firstCleanupStarted = new Promise((resolve) => {
    reportCleanupStarted = resolve
  })
  const firstCleanupGate = new Promise((resolve) => {
    releaseFirstCleanup = resolve
  })
  const removePath = async (target, options) => {
    if (path.resolve(String(target)) === startupRoot) {
      cleanupAttempts += 1
      if (cleanupAttempts === 1) {
        reportCleanupStarted()
        await firstCleanupGate
        throw new Error('simulated startup cleanup failure')
      }
    }
    await rm(target, options)
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined,
    removePath,
  )
  try {
    await firstCleanupStarted
    assert.equal(manager.hasActiveWork(), true)
    assert.equal(manager.hasManagedOutput(), true)

    releaseFirstCleanup()
    await waitFor(
      () => !manager.hasActiveWork(),
      'The failed startup cleanup lease did not settle.',
    )
    assert.equal(manager.hasManagedOutput(), true)

    const started = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    assert.equal(cleanupAttempts, 2)
    assert.equal(manager.hasManagedOutput(), false)
    await manager.cancel(started.jobId)
    assert.equal(manager.hasManagedOutput(), false)
  } finally {
    releaseFirstCleanup?.()
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

for (const [sourceName, testName] of [
  [
    'invalid-wav.mp3',
    'rejects arbitrary WAV bytes before publishing ready',
  ],
  [
    'invalid-mp3.mp3',
    'rejects arbitrary MP3 bytes before publishing ready',
  ],
  [
    'malformed-event.mp3',
    'rejects malformed prefixed JSON even when valid completion follows',
  ],
  [
    'skip-stages.mp3',
    'rejects a worker that skips required lifecycle stages',
  ],
]) {
  test(testName, async () => {
    const fixture = await createFixture()
    const owner = {
      cancel() {},
      play() {
        return Promise.resolve()
      },
    }
    const manager = new SongCoverManager(
      fixture.projectRoot,
      () => fixture.dataRoot,
      () => owner,
      () => {},
      async () => ({ outputDeviceId: null, volumePercent: 100 }),
      () => {},
    )
    try {
      const started = await startLegacy(
        manager,
        `D:/private/${sourceName}`,
        sourceName,
      )
      const settled = await waitFor(
        () => (
          !manager.hasActiveWork()
          && manager.getState().stage === 'error'
          && manager.getState()
        ),
        `Song Cover accepted invalid worker behavior for ${sourceName}.`,
      )
      assert.equal(settled.jobId, started.jobId)
      assert.equal(settled.stage, 'error')
      assert.equal(settled.outputAvailable, false)
      const jobDirectory = path.join(
        fixture.dataRoot,
        'audio',
        'song-covers',
        started.jobId,
      )
      await assert.rejects(stat(jobDirectory))
    } finally {
      await manager.shutdown()
      await rm(fixture.root, { recursive: true, force: true })
    }
  })
}

test('cancels only the exact active worker and deletes incomplete output', async () => {
  const fixture = await createFixture()
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
  )
  try {
    const started = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    assert.notEqual(started.jobId, null)
    await assert.rejects(
      manager.cancel('00000000-0000-4000-8000-000000000000'),
      /no longer active/iu,
    )
    const cancelled = await manager.cancel(started.jobId)
    assert.equal(cancelled.stage, 'cancelled')
    assert.equal(cancelled.outputAvailable, false)
    const jobDirectory = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      started.jobId,
    )
    await assert.rejects(stat(jobDirectory))
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('retains and retries an exact private directory after cleanup fails', async () => {
  const fixture = await createFixture()
  let blockedDirectory = null
  const removePath = async (target, options) => {
    if (
      blockedDirectory !== null
      && path.resolve(String(target)) === blockedDirectory
    ) {
      throw new Error('simulated cleanup failure')
    }
    await rm(target, options)
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined,
    removePath,
  )
  try {
    const started = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    const jobDirectory = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      started.jobId,
    )
    blockedDirectory = path.resolve(jobDirectory)
    await waitFor(
      () => manager.getState().progressPercent === 5,
      'The private worker directory was not created before cancellation.',
    )

    await assert.rejects(
      manager.cancel(started.jobId),
      /private Song Cover audio could not be cleared/iu,
    )
    assert.equal(manager.getState().stage, 'error')
    assert.equal(manager.getState().outputAvailable, false)
    assert.equal(manager.hasManagedOutput(), true)
    assert.equal((await stat(jobDirectory)).isDirectory(), true)
    await assert.rejects(
      startLegacy(manager, 'D:/private/replacement.mp3', 'replacement.mp3'),
      /private Song Cover audio could not be cleared/iu,
    )

    blockedDirectory = null
    await manager.discardOutput()
    await assert.rejects(stat(jobDirectory))
    assert.equal(manager.hasManagedOutput(), false)
    assert.equal(manager.getState().stage, 'idle')
  } finally {
    blockedDirectory = null
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('retains failed-worker audio until exact cleanup can be retried', async () => {
  const fixture = await createFixture()
  let blockedDirectory = null
  const removePath = async (target, options) => {
    if (
      blockedDirectory !== null
      && path.resolve(String(target)) === blockedDirectory
    ) {
      throw new Error(`private injected failure at ${target}`)
    }
    await rm(target, options)
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined,
    removePath,
  )
  try {
    const started = await startLegacy(
      manager,
      'D:/private/worker-error.mp3',
      'worker-error.mp3',
    )
    const failedDirectory = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      started.jobId,
    )
    blockedDirectory = path.resolve(failedDirectory)
    await waitFor(
      () => manager.getState().stage === 'error' && !manager.hasActiveWork(),
      'The failed worker did not settle its cleanup state.',
    )

    const failedState = manager.getState()
    assert.equal(
      failedState.error,
      'Private Song Cover audio could not be cleared. Close Elysia and try again.',
    )
    assert.equal(failedState.error.includes(fixture.root), false)
    assert.equal(failedState.error.includes('injected failure'), false)
    assert.equal(manager.hasManagedOutput(), true)
    assert.equal((await stat(failedDirectory)).isDirectory(), true)

    blockedDirectory = null
    const replacement = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    await assert.rejects(stat(failedDirectory))
    await waitFor(
      () => manager.getState().progressPercent === 5,
      'The replacement worker did not start after cleanup recovered.',
    )
    await manager.cancel(replacement.jobId)
  } finally {
    blockedDirectory = null
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('coalesces concurrent cancellation for one owned process tree', async () => {
  const fixture = await createFixture()
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
  )
  try {
    const started = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    const [first, second] = await Promise.all([
      manager.cancel(started.jobId),
      manager.cancel(started.jobId),
    ])
    assert.equal(first.stage, 'cancelled')
    assert.equal(second.stage, 'cancelled')
    assert.equal(manager.hasActiveWork(), false)
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('coalesces timeout cleanup with concurrent user cancellation', {
  timeout: 30_000,
}, async () => {
  const fixture = await createFixture()
  let blockedDirectory = null
  let releaseCleanup
  let reportCleanupStarted
  let targetCalls = 0
  const cleanupStarted = new Promise((resolve) => {
    reportCleanupStarted = resolve
  })
  const cleanupGate = new Promise((resolve) => {
    releaseCleanup = resolve
  })
  const removePath = async (target, options) => {
    if (
      blockedDirectory !== null
      && path.resolve(String(target)) === blockedDirectory
    ) {
      targetCalls += 1
      reportCleanupStarted()
      await cleanupGate
    }
    await rm(target, options)
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    50,
    removePath,
  )
  try {
    const started = await startLegacy(manager, 'D:/private/silent.mp3', 'silent.mp3')
    blockedDirectory = path.resolve(path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      started.jobId,
    ))
    await cleanupStarted
    assert.equal(manager.hasActiveWork(), true)

    const cancellation = manager.cancel(started.jobId)
    await assert.rejects(
      startLegacy(manager, 'D:/private/replacement.mp3', 'replacement.mp3'),
      /current Song Cover/iu,
    )
    assert.equal(targetCalls, 1)

    releaseCleanup()
    const settled = await cancellation
    assert.equal(settled.stage, 'error')
    assert.equal(settled.error, 'Song Cover took too long and was stopped.')
    assert.equal(manager.getState().stage, 'error')
    assert.equal(targetCalls, 1)
    assert.equal(manager.hasActiveWork(), false)
  } finally {
    releaseCleanup?.()
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('cancels an SVS launch through its shared AbortSignal before worker admission', async () => {
  const fixture = await createFixture()
  let observedSignal
  let markPreparationStarted
  const preparationStarted = new Promise((resolve) => {
    markPreparationStarted = resolve
  })
  const prepareLyricsAssets = async ({ signal }) => {
    observedSignal = signal
    markPreparationStarted()
    return new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => {
        reject(signal.reason)
      }, { once: true })
    })
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined,
    undefined,
    undefined,
    undefined,
    prepareLyricsAssets,
  )
  try {
    const starting = manager.start('D:/private/slow.mp3', 'slow.mp3', 0, null, 'lyrics-svs')
    const jobId = manager.getState().jobId
    assert.notEqual(jobId, null)
    assert.equal(manager.getState().engine, 'lyrics-svs')
    await preparationStarted
    const cancelling = manager.cancel(jobId)
    const [started, cancelled] = await Promise.race([
      Promise.all([starting, cancelling]),
      new Promise((_resolve, reject) => {
        setTimeout(() => {
          reject(new Error('Launch cancellation did not settle promptly.'))
        }, 1_000)
      }),
    ])
    assert.equal(started.stage, 'cancelled')
    assert.equal(cancelled.stage, 'cancelled')
    assert.equal(observedSignal.aborted, true)
    assert.equal(manager.hasActiveWork(), false)
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('invalidates pending auto-play when a completed job is cancelled', async () => {
  const fixture = await createFixture()
  let releasePreferences
  const preferences = new Promise((resolve) => {
    releasePreferences = resolve
  })
  let playCalls = 0
  const owner = {
    cancel() {},
    play() {
      playCalls += 1
      return Promise.resolve()
    },
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => owner,
    () => {},
    () => preferences,
    () => {},
  )
  try {
    const started = await startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    await waitFor(
      () => manager.getState().stage === 'ready' && manager.hasActiveWork(),
      'Song Cover did not enter pending auto-play.',
    )
    const cancelling = manager.cancel(started.jobId)
    releasePreferences({ outputDeviceId: null, volumePercent: 100 })
    const cancelled = await cancelling
    assert.equal(cancelled.stage, 'cancelled')
    assert.equal(cancelled.outputAvailable, false)
    assert.equal(playCalls, 0)
  } finally {
    releasePreferences?.({ outputDeviceId: null, volumePercent: 100 })
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('keeps output ready without playing when preferences are unavailable', async () => {
  const fixture = await createFixture()
  let playCalls = 0
  const owner = {
    cancel() {},
    play() {
      playCalls += 1
      return Promise.resolve()
    },
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => owner,
    () => {},
    async () => { throw new Error('private Backend preference failure') },
    () => {},
  )
  try {
    const started = await startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    const settled = await waitFor(
      () => !manager.hasActiveWork() && manager.getState(),
      'Song Cover did not settle after preference lookup failed.',
    )
    assert.equal(settled.jobId, started.jobId)
    assert.equal(settled.stage, 'error')
    assert.equal(settled.outputAvailable, true)
    assert.equal(
      settled.error,
      'The Song Cover is ready, but playback could not start.',
    )
    assert.equal(playCalls, 0)
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('bounds automatic playback preference lookup after conversion', async () => {
  const fixture = await createFixture()
  const preferences = new Promise(() => {})
  const owner = {
    cancel() {},
    play() {
      assert.fail('Playback must not start without bounded preferences.')
    },
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => owner,
    () => {},
    () => preferences,
    () => {},
    undefined,
    undefined,
    null,
    25,
  )
  try {
    const started = await startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    const settled = await waitFor(
      () => !manager.hasActiveWork() && manager.getState(),
      'Song Cover retained its worker slot after playback setup timed out.',
    )
    assert.equal(settled.jobId, started.jobId)
    assert.equal(settled.stage, 'error')
    assert.equal(settled.outputAvailable, true)
    assert.equal(
      settled.error,
      'The Song Cover is ready, but playback could not start.',
    )
    assert.equal(manager.hasManagedOutput(), true)
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('invalidates manual playback while it awaits private preferences', async () => {
  const fixture = await createFixture()
  let preferenceMode = 'initial'
  let releasePreferences
  let reportPreferencesRequested
  let playCalls = 0
  const manualPreferences = new Promise((resolve) => {
    releasePreferences = resolve
  })
  const preferencesRequested = new Promise((resolve) => {
    reportPreferencesRequested = resolve
  })
  const owner = {
    cancel() {},
    play() {
      playCalls += 1
      return Promise.resolve()
    },
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => owner,
    () => {},
    async () => {
      if (preferenceMode === 'initial') {
        return { outputDeviceId: null, volumePercent: 100 }
      }
      reportPreferencesRequested()
      return manualPreferences
    },
    () => {},
  )
  try {
    const started = await startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    await waitFor(
      () => manager.getState().outputAvailable && !manager.hasActiveWork(),
      'Song Cover did not finish automatic playback before the race test.',
    )
    playCalls = 0
    preferenceMode = 'manual'
    const pendingPlayback = manager.play(started.jobId)
    await preferencesRequested

    const stopped = manager.stopPlayback(started.jobId)
    assert.equal(stopped.stage, 'ready')
    releasePreferences({ outputDeviceId: null, volumePercent: 100 })
    const settled = await pendingPlayback
    assert.equal(settled.stage, 'ready')
    assert.equal(playCalls, 0)
  } finally {
    releasePreferences?.({ outputDeviceId: null, volumePercent: 100 })
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('keeps completed output published when replacement cleanup fails', async () => {
  const fixture = await createFixture()
  let blockedDirectory = null
  const published = []
  const removePath = async (target, options) => {
    if (
      blockedDirectory !== null
      && path.resolve(String(target)) === blockedDirectory
    ) {
      throw new Error('simulated retained output cleanup failure')
    }
    await rm(target, options)
  }
  const owner = {
    cancel() {},
    play() {
      return Promise.resolve()
    },
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => owner,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    (state) => { published.push(state) },
    undefined,
    removePath,
  )
  try {
    const original = await startLegacy(
      manager,
      'D:/private/original.mp3',
      'original.mp3',
    )
    const ready = await waitFor(
      () => (
        manager.getState().stage === 'ready'
        && !manager.hasActiveWork()
        && manager.getState()
      ),
      'The original Song Cover did not settle before replacement.',
    )
    const outputDirectory = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      original.jobId,
    )
    blockedDirectory = path.resolve(outputDirectory)
    const publicationCount = published.length

    await assert.rejects(
      startLegacy(manager, 'D:/private/replacement.mp3', 'replacement.mp3'),
      /private Song Cover audio could not be cleared/iu,
    )
    assert.deepEqual(manager.getState(), ready)
    assert.equal(published.length, publicationCount)
    assert.equal(manager.hasManagedOutput(), true)
    assert.equal((await stat(path.join(
      outputDirectory,
      'elysia-cover.wav',
    ))).isFile(), true)

    const exported = path.join(fixture.root, 'retained-output.wav')
    await manager.exportWav(original.jobId, exported)
    assert.equal((await stat(exported)).isFile(), true)

    blockedDirectory = null
    await manager.discardOutput()
  } finally {
    blockedDirectory = null
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('reserves the worker slot before asynchronous launch preparation', async () => {
  const fixture = await createFixture()
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
  )
  try {
    const firstStart = startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    await assert.rejects(
      startLegacy(manager, 'D:/private/replacement.mp3', 'replacement.mp3'),
      /current Song Cover/iu,
    )
    await firstStart
    await waitFor(
      () => manager.getState().outputAvailable,
      'The exclusively owned job did not finish.',
    )
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('shutdown waits for a pending launch reservation without orphaning a worker', async () => {
  const fixture = await createFixture()
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
  )
  try {
    const starting = startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    const closing = manager.shutdown()
    await assert.rejects(starting, /shutting down/iu)
    await closing
    assert.equal(manager.hasActiveWork(), false)
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('blocks shutdown until active process-tree termination is confirmed', async () => {
  const fixture = await createFixture()
  let allowTermination = false
  let terminationAttempts = 0
  const terminateProcessTree = async (child) => {
    terminationAttempts += 1
    if (!allowTermination) {
      return false
    }
    if (child.exitCode !== null || child.signalCode !== null) {
      return true
    }
    return new Promise((resolve) => {
      const timeout = setTimeout(() => { resolve(false) }, 2_000)
      child.once('close', () => {
        clearTimeout(timeout)
        resolve(true)
      })
      try {
        child.kill('SIGKILL')
      } catch {
        clearTimeout(timeout)
        resolve(false)
      }
    })
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined,
    undefined,
    terminateProcessTree,
  )
  try {
    const started = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    await waitFor(
      () => manager.getState().progressPercent === 5,
      'The worker did not start before the shutdown test.',
    )

    await assert.rejects(
      manager.shutdown(),
      /process-tree termination could not be confirmed/iu,
    )
    assert.equal(manager.hasActiveWork(), true)
    assert.equal(terminationAttempts, 1)

    allowTermination = true
    await manager.shutdown()
    assert.equal(manager.hasActiveWork(), false)
    assert.equal(terminationAttempts, 2)
    await assert.rejects(stat(path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      started.jobId,
    )))
  } finally {
    allowTermination = true
    await manager.shutdown().catch(() => {})
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('never upgrades a closed parent after tree termination was uncertain', async () => {
  const fixture = await createFixture()
  let terminationAttempts = 0
  const terminateProcessTree = async (child) => {
    terminationAttempts += 1
    if (child.exitCode === null && child.signalCode === null) {
      await new Promise((resolve) => {
        child.once('close', resolve)
        child.kill('SIGKILL')
      })
    }
    return false
  }
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    undefined,
    undefined,
    terminateProcessTree,
  )
  let jobDirectory = null
  try {
    const started = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    jobDirectory = path.join(
      fixture.dataRoot,
      'audio',
      'song-covers',
      started.jobId,
    )
    await waitFor(
      () => manager.getState().progressPercent === 5,
      'The worker did not start before uncertain tree termination testing.',
    )

    await assert.rejects(
      manager.shutdown(),
      /process-tree termination could not be confirmed/iu,
    )
    assert.equal(terminationAttempts, 1)
    assert.equal((await stat(jobDirectory)).isDirectory(), true)
    await assert.rejects(
      manager.shutdown(),
      /process-tree termination could not be confirmed/iu,
    )
    assert.equal(terminationAttempts, 1)
    assert.equal((await stat(jobDirectory)).isDirectory(), true)
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('cancels a failed spawn without retaining a nonexistent process', async () => {
  const fixture = await createFixture()
  const python = path.join(
    fixture.projectRoot,
    'models',
    'cache',
    'GPT-SoVITS-v2-240821',
    'runtime',
    'python.exe',
  )
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
  )
  try {
    await rm(python, { force: true })
    const started = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    const cancelled = await manager.cancel(started.jobId)
    assert.equal(cancelled.stage, 'cancelled')
    assert.equal(manager.hasActiveWork(), false)
    assert.equal(manager.hasManagedOutput(), false)
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('maps worker text to fixed Main-owned progress messages', async () => {
  const fixture = await createFixture()
  const published = []
  const manager = new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    (state) => { published.push(state) },
  )
  try {
    await startLegacy(manager, 'D:/private/message.mp3', 'message.mp3')
    await waitFor(
      () => manager.getState().outputAvailable,
      'Song Cover did not finish.',
    )
    assert.equal(
      JSON.stringify(published).includes('PRIVATE RUNTIME DIAGNOSTIC'),
      false,
    )
    assert.equal(
      published.some((state) => state.message === 'Reading the selected audio'),
      true,
    )
  } finally {
    await manager.shutdown()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

function gpuDeferred() {
  let resolve
  const promise = new Promise((accepted) => { resolve = accepted })
  return { promise, resolve }
}

function gpuTestManager(fixture, gpu, options = {}) {
  return new SongCoverManager(
    fixture.projectRoot,
    () => fixture.dataRoot,
    () => options.playback ?? null,
    () => {},
    async () => ({ outputDeviceId: null, volumePercent: 100 }),
    () => {},
    options.timeoutMs,
    options.removePath,
    options.terminateProcessTree ?? null,
    undefined,
    undefined,
    options.spawnWorker,
    options.cleanupTimeoutMs,
    {},
    options.acquireGpu ?? ((signal) => gpu.acquire('song-cover', { signal })),
  )
}

async function stopGpuFixtureProcess(child) {
  if (child.exitCode !== null || child.signalCode !== null) return true
  return new Promise((resolve) => {
    child.once('close', () => resolve(true))
    child.kill('SIGKILL')
  })
}

test('GPU queue waiting can be cancelled before any Song Cover worker starts', async () => {
  const fixture = await createFixture()
  const gpu = new GpuAdmissionCoordinator()
  const ollama = await gpu.acquire('ollama')
  let spawned = 0
  const manager = gpuTestManager(fixture, gpu, { spawnWorker: (...args) => { spawned += 1; return spawn(...args) } })
  try {
    const starting = startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    await waitFor(() => gpu.getSnapshot().queued.length === 1, 'Song Cover did not queue for GPU admission')
    assert.equal(spawned, 0)
    const cancelling = manager.cancel(manager.getState().jobId)
    assert.equal((await starting).stage, 'cancelled')
    assert.equal((await cancelling).stage, 'cancelled')
    assert.equal(spawned, 0)
    assert.equal(gpu.getSnapshot().active.owner, 'ollama')
    assert.deepEqual(gpu.getSnapshot().queued, [])
  } finally {
    ollama.release()
    await manager.shutdown()
    await gpu.close()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('GPU ownership ends after successful worker cleanup while playback may continue', async () => {
  const fixture = await createFixture()
  const gpu = new GpuAdmissionCoordinator()
  const playback = gpuDeferred()
  const manager = gpuTestManager(fixture, gpu, { playback: { cancel() {}, play: () => playback.promise } })
  try {
    await startLegacy(manager, 'D:/private/original.mp3', 'original.mp3')
    assert.equal(gpu.getSnapshot().active.owner, 'song-cover')
    await waitFor(() => manager.getState().stage === 'playing', 'The completed GPU cover did not enter playback')
    assert.equal(gpu.getSnapshot().active, null)
    const image = await gpu.acquire('image-generation')
    assert.equal(image.owner, 'image-generation')
    image.release()
  } finally {
    playback.resolve()
    await manager.shutdown()
    await gpu.close()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('GPU cancellation retains the lease until process-tree termination is confirmed', async () => {
  const fixture = await createFixture()
  const gpu = new GpuAdmissionCoordinator()
  const termination = gpuDeferred()
  let releaseCalls = 0
  const manager = gpuTestManager(fixture, gpu, {
    terminateProcessTree: async (child) => { await termination.promise; return stopGpuFixtureProcess(child) },
    acquireGpu: async (signal) => {
      const lease = await gpu.acquire('song-cover', { signal })
      return { ...lease, release: () => { releaseCalls += 1; return lease.release() } }
    },
  })
  try {
    const started = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    await waitFor(() => manager.getState().progressPercent === 5, 'The GPU worker did not start')
    const imagePromise = gpu.acquire('image-generation')
    const cancelling = manager.cancel(started.jobId)
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(gpu.getSnapshot().active.owner, 'song-cover')
    assert.equal(releaseCalls, 0)
    termination.resolve()
    assert.equal((await cancelling).stage, 'cancelled')
    const image = await imagePromise
    assert.equal(releaseCalls, 1)
    image.release()
  } finally {
    termination.resolve()
    await manager.shutdown()
    assert.equal(releaseCalls, 1)
    await gpu.close()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('GPU worker failure frees capacity only after cleanup and permits a later generation', async () => {
  const fixture = await createFixture()
  const gpu = new GpuAdmissionCoordinator()
  const manager = gpuTestManager(fixture, gpu)
  try {
    await startLegacy(manager, 'D:/private/worker-error.mp3', 'worker-error.mp3')
    const firstGeneration = gpu.getSnapshot().active.generation
    await waitFor(() => !manager.hasActiveWork(), 'Failed GPU worker did not finish cleanup')
    assert.equal(manager.getState().stage, 'error')
    assert.equal(gpu.getSnapshot().active, null)
    const retry = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    assert.ok(gpu.getSnapshot().active.generation > firstGeneration)
    await manager.cancel(retry.jobId)
    assert.equal(gpu.getSnapshot().active, null)
  } finally {
    await manager.shutdown()
    await gpu.close()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('GPU ownership survives failed WSL cleanup until an exact recovery retry succeeds', async () => {
  const fixture = await createFixture()
  const gpu = new GpuAdmissionCoordinator()
  const manager = gpuTestManager(fixture, gpu, { terminateProcessTree: stopGpuFixtureProcess })
  const badResponse = path.join(fixture.projectRoot, 'cleanup-response.bin')
  try {
    await writeFile(badResponse, 'invalid cleanup response\n')
    const started = await manager.start('D:/private/slow.mp3', 'slow.mp3')
    await waitFor(() => manager.getState().progressPercent === 5, 'The WSL GPU fixture did not start')
    await assert.rejects(manager.cancel(started.jobId), /private Song Cover audio could not be cleared/iu)
    assert.equal(gpu.getSnapshot().active.owner, 'song-cover')
    assert.equal(manager.hasActiveWork(), true)
    const imagePromise = gpu.acquire('image-generation')
    await rm(badResponse)
    await manager.shutdown()
    const image = await imagePromise
    assert.equal(image.owner, 'image-generation')
    image.release()
  } finally {
    await rm(badResponse, { force: true })
    await manager.shutdown()
    await gpu.close()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('GPU termination uncertainty retains physical ownership through a successful shutdown retry', async () => {
  const fixture = await createFixture()
  const gpu = new GpuAdmissionCoordinator()
  let allowTermination = false
  const manager = gpuTestManager(fixture, gpu, {
    terminateProcessTree: async (child) => allowTermination ? stopGpuFixtureProcess(child) : false,
  })
  try {
    const started = await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    await waitFor(() => manager.getState().progressPercent === 5, 'The uncertain GPU worker did not start')
    await assert.rejects(manager.cancel(started.jobId), /termination could not be confirmed/iu)
    assert.equal(gpu.getSnapshot().active.owner, 'song-cover')
    allowTermination = true
    await manager.shutdown()
    assert.equal(gpu.getSnapshot().active, null)
  } finally {
    allowTermination = true
    await manager.shutdown()
    await gpu.close()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('GPU child error alone cannot release capacity before its owned process closes', async () => {
  const fixture = await createFixture()
  const gpu = new GpuAdmissionCoordinator()
  const termination = gpuDeferred()
  let worker
  const manager = gpuTestManager(fixture, gpu, {
    spawnWorker: (...args) => { worker = spawn(...args); return worker },
    terminateProcessTree: async (child) => { await termination.promise; return stopGpuFixtureProcess(child) },
  })
  try {
    await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')
    await waitFor(() => manager.getState().progressPercent === 5, 'The error GPU fixture did not start')
    worker.emit('error', new Error('private native process error'))
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(gpu.getSnapshot().active.owner, 'song-cover')
    assert.equal(worker.exitCode, null)
    termination.resolve()
    await waitFor(() => !manager.hasActiveWork(), 'The error GPU fixture did not physically settle')
    assert.equal(gpu.getSnapshot().active, null)
  } finally {
    termination.resolve()
    await manager.shutdown()
    await gpu.close()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('GPU coordinator shutdown aborts native work and joins the manager physical stop', async () => {
  const fixture = await createFixture()
  const gpu = new GpuAdmissionCoordinator()
  const cleanupEntered = gpuDeferred()
  const cleanupRelease = gpuDeferred()
  let jobId = null
  let deletionCount = 0
  const manager = gpuTestManager(fixture, gpu, {
    terminateProcessTree: stopGpuFixtureProcess,
    removePath: async (target, options) => {
      if (jobId !== null && path.basename(target) === jobId) {
        deletionCount += 1
        assert.equal(deletionCount, 1, 'Concurrent stop owners must share exact private cleanup.')
        cleanupEntered.resolve()
        await cleanupRelease.promise
      }
      return rm(target, options)
    },
  })
  try {
    jobId = (await startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3')).jobId
    await waitFor(() => manager.getState().progressPercent === 5, 'The shutdown GPU worker did not start')
    const closing = gpu.close()
    const shutdown = manager.shutdown()
    await cleanupEntered.promise
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(deletionCount, 1)
    assert.equal(gpu.getSnapshot().active.owner, 'song-cover')
    cleanupRelease.resolve()
    await shutdown
    await closing
    assert.equal(gpu.getSnapshot().active, null)
    assert.equal(manager.hasActiveWork(), false)
  } finally {
    cleanupRelease.resolve()
    await manager.shutdown()
    await gpu.close()
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('GPU admission is released when synchronous spawn fails before any physical worker exists', async () => {
  const fixture = await createFixture()
  const gpu = new GpuAdmissionCoordinator()
  const manager = gpuTestManager(fixture, gpu, { spawnWorker: () => { throw new Error('private spawn diagnostic') } })
  try {
    await assert.rejects(startLegacy(manager, 'D:/private/slow.mp3', 'slow.mp3'), /could not be started/iu)
    assert.equal(gpu.getSnapshot().active, null)
    assert.equal(manager.hasActiveWork(), false)
  } finally {
    await manager.shutdown()
    await gpu.close()
    await rm(fixture.root, { recursive: true, force: true })
  }
})
