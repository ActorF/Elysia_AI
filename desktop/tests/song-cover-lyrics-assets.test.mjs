/** Verify private online-lyrics preparation without renderer or argv leakage. */

import assert from 'node:assert/strict'
import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  writeFile,
} from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  deriveSongLyricsQuery,
  parseSongLyricsProbeDocument,
  SongLyricsAssetPreparer,
} from '../dist-electron/song-cover-lyrics-assets.js'

function foundLyrics(overrides = {}) {
  return Object.freeze({
    status: 'found',
    plainLyrics: '第一句\n第二句',
    syncedLyrics: '[00:01.00]第一句\n[00:04.50]第二句',
    providerRecord: Object.freeze({
      provider: 'lrclib',
      recordId: 81357,
      trackName: '测试歌曲',
      artistName: '测试歌手',
      albumName: '测试专辑',
      durationSeconds: 198.25,
      confidence: 0.9732,
      hasWordSync: false,
    }),
    ...overrides,
  })
}

async function createJobFixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), 'elysia-lyrics-assets-'))
  const jobDirectory = path.join(root, 'job')
  await mkdir(jobDirectory)
  return { jobDirectory, root }
}

/** Return a fresh signal that remains active for one ordinary preparation. */
function uncancelledSignal() {
  return new AbortController().signal
}

test('derives a query from bounded tags before considering the filename', () => {
  assert.deepEqual(
    deriveSongLyricsQuery({
      title: '测试歌曲',
      artist: '测试歌手',
      durationSeconds: 198.25,
    }, 'unrelated-name.mp3'),
    {
      title: '测试歌曲',
      artist: '测试歌手',
      durationSeconds: 198.25,
    },
  )
})

test('uses a two-part filename only when one media tag fixes its direction', () => {
  assert.deepEqual(
    deriveSongLyricsQuery({
      title: null,
      artist: '歌手',
      durationSeconds: 180,
    }, '歌手 - 歌名.flac'),
    { title: '歌名', artist: '歌手', durationSeconds: 180 },
  )
  assert.deepEqual(
    deriveSongLyricsQuery({
      title: '歌名',
      artist: null,
      durationSeconds: 180,
    }, '歌名 - 歌手.flac'),
    { title: '歌名', artist: '歌手', durationSeconds: 180 },
  )
  assert.deepEqual(
    deriveSongLyricsQuery({
      title: null,
      artist: '要不要买菜',
      durationSeconds: 215.4,
    }, '要不要买菜_勿忘我【動態歌詞_Lyrics_Video】.mp3'),
    { title: '勿忘我', artist: '要不要买菜', durationSeconds: 215.4 },
  )
  assert.deepEqual(
    deriveSongLyricsQuery({
      title: '勿忘我',
      artist: null,
      durationSeconds: 215.4,
    }, '要不要买菜_勿忘我【动态歌词_Lyrics_Video】.mp3'),
    { title: '勿忘我', artist: '要不要买菜', durationSeconds: 215.4 },
  )
  assert.equal(
    deriveSongLyricsQuery({
      title: null,
      artist: null,
      durationSeconds: 180,
    }, '歌手 - 歌名.flac'),
    null,
  )
  for (const sourceName of [
    'unknown.mp3',
    'A - B - C.mp3',
    'ScreenRecording_06-04-2026-01-08-52_1.mp3',
    '歌手 - ScreenRecording_06-04-2026-01-08-52_1.mp3',
    '2026-09-30-23-05-30_0.png',
    '20260930_230530.wav',
    '1f40a133-ed82-4ef2-8512-44f0882d4b15.flac',
    'vocals - song.wav',
    '../A - B.mp3',
    '要不要买菜_勿忘我.mp3',
    '要不要买菜_勿忘我【现场版】.mp3',
    '要_不要买菜_勿忘我【動態歌詞_Lyrics_Video】.mp3',
  ]) {
    assert.equal(
      deriveSongLyricsQuery({
        title: null,
        artist: null,
        durationSeconds: 180,
      }, sourceName),
      null,
    )
  }
  assert.equal(
    deriveSongLyricsQuery({
      title: '另一首歌',
      artist: null,
      durationSeconds: 180,
    }, '歌手 - 歌名.flac'),
    null,
  )
  assert.equal(
    deriveSongLyricsQuery({
      title: null,
      artist: '歌手',
      durationSeconds: 180,
    }, '歌手 - ScreenRecording_06-04-2026-01-08-52_1.mp3'),
    null,
  )
})

test('selects stream duration and falls back across bounded media tags', () => {
  assert.deepEqual(parseSongLyricsProbeDocument({
    streams: [{
      duration: '198.25',
      tags: {
        title: '流标题',
        artist: '流艺术家',
      },
    }],
    format: {
      duration: '201.00',
      tags: {
        TITLE: ' 容器标题 ',
        ALBUM_ARTIST: '专辑艺术家',
      },
    },
  }), {
    title: '容器标题',
    artist: '流艺术家',
    durationSeconds: 198.25,
  })
  assert.deepEqual(parseSongLyricsProbeDocument({
    streams: [{ duration: 'N/A', tags: {} }],
    format: {
      duration: '201.00',
      tags: { title: '容器标题', albumartist: '专辑艺术家' },
    },
  }), {
    title: '容器标题',
    artist: '专辑艺术家',
    durationSeconds: 201,
  })
  assert.equal(parseSongLyricsProbeDocument({
    streams: [{ duration: '0' }],
    format: { duration: 'N/A' },
  }), null)
  assert.equal(parseSongLyricsProbeDocument({
    streams: [{ duration: '720.01' }],
    format: { duration: '200' },
  }), null)
})

test('writes synchronized and plain lyrics with provenance-only manifest', async () => {
  const fixture = await createJobFixture()
  const seenQueries = []
  const preparer = new SongLyricsAssetPreparer(
    async (query) => {
      seenQueries.push(query)
      return foundLyrics()
    },
    async () => ({
      title: '测试歌曲',
      artist: '测试歌手',
      durationSeconds: 198.25,
    }),
  )
  try {
    const result = await preparer.prepare({
      jobDirectory: fixture.jobDirectory,
      sourcePath: path.join(fixture.root, 'private', 'original.mp3'),
      sourceName: 'original.mp3',
      ffprobePath: path.join(fixture.root, 'private', 'ffprobe'),
      signal: uncancelledSignal(),
      metadataOverride: null,
    })
    assert.deepEqual(seenQueries, [{
      title: '测试歌曲',
      artist: '测试歌手',
      durationSeconds: 198.25,
    }])
    assert.deepEqual(result, {
      status: 'ready',
      files: ['lyrics.lrc', 'lyrics.txt', 'lyrics-manifest.json'],
      manifest: {
        source: 'lrclib',
        recordId: 81357,
        confidence: 0.9732,
        hasSyncedLyrics: true,
      },
    })
    assert.equal(
      await readFile(path.join(fixture.jobDirectory, 'lyrics.lrc'), 'utf8'),
      '[00:01.00]第一句\n[00:04.50]第二句\n',
    )
    assert.equal(
      await readFile(path.join(fixture.jobDirectory, 'lyrics.txt'), 'utf8'),
      '第一句\n第二句\n',
    )
    const manifestText = await readFile(
      path.join(fixture.jobDirectory, 'lyrics-manifest.json'),
      'utf8',
    )
    assert.deepEqual(JSON.parse(manifestText), result.manifest)
    assert.equal(manifestText.includes('第一句'), false)
    assert.equal(manifestText.includes('测试歌曲'), false)
    assert.equal(JSON.stringify(result).includes(fixture.root), false)
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('manual title and artist override tags while duration remains probe-owned', async () => {
  const fixture = await createJobFixture()
  const seenQueries = []
  const preparer = new SongLyricsAssetPreparer(
    async (query) => {
      seenQueries.push(query)
      return foundLyrics()
    },
    async () => ({
      title: '错误标签',
      artist: '错误歌手',
      durationSeconds: 203.75,
    }),
  )
  try {
    const result = await preparer.prepare({
      jobDirectory: fixture.jobDirectory,
      sourcePath: path.join(fixture.root, 'private', 'ScreenRecording.wav'),
      sourceName: 'ScreenRecording.wav',
      ffprobePath: path.join(fixture.root, 'private', 'ffprobe'),
      signal: uncancelledSignal(),
      metadataOverride: {
        title: '  手动歌名  ',
        artist: ' 手动歌手 ',
      },
    })
    assert.equal(result.status, 'ready')
    assert.deepEqual(seenQueries, [{
      title: '手动歌名',
      artist: '手动歌手',
      durationSeconds: 203.75,
    }])
    const manifestText = await readFile(
      path.join(fixture.jobDirectory, 'lyrics-manifest.json'),
      'utf8',
    )
    assert.equal(manifestText.includes('手动歌名'), false)
    assert.equal(manifestText.includes('手动歌手'), false)
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('manual metadata cannot bypass 12-minute FFprobe admission', async () => {
  for (const durationSeconds of [Number.NaN, 0, 720.01]) {
    const fixture = await createJobFixture()
    let lookupCalls = 0
    const preparer = new SongLyricsAssetPreparer(
      async () => {
        lookupCalls += 1
        return foundLyrics()
      },
      async () => ({ title: null, artist: null, durationSeconds }),
    )
    try {
      const result = await preparer.prepare({
        jobDirectory: fixture.jobDirectory,
        sourcePath: path.join(fixture.root, 'private', 'generic.wav'),
        sourceName: 'generic.wav',
        ffprobePath: path.join(fixture.root, 'private', 'ffprobe'),
        signal: uncancelledSignal(),
        metadataOverride: { title: '歌名', artist: '歌手' },
      })
      assert.deepEqual(result, { status: 'invalid-audio' })
      assert.equal(lookupCalls, 0)
      assert.deepEqual(await readdir(fixture.jobDirectory), [])
    } finally {
      await rm(fixture.root, { recursive: true, force: true })
    }
  }
})

test('propagates one AbortSignal through metadata probing and stops promptly', async () => {
  const fixture = await createJobFixture()
  const controller = new AbortController()
  let observedSignal
  let markProbeStarted
  const probeStarted = new Promise((resolve) => { markProbeStarted = resolve })
  const preparer = new SongLyricsAssetPreparer(
    async () => foundLyrics(),
    async (_sourcePath, _ffprobePath, signal) => {
      observedSignal = signal
      markProbeStarted()
      return new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => {
          reject(signal.reason)
        }, { once: true })
      })
    },
  )
  try {
    const preparation = preparer.prepare({
      jobDirectory: fixture.jobDirectory,
      sourcePath: path.join(fixture.root, 'private', 'song.mp3'),
      sourceName: 'song.mp3',
      ffprobePath: path.join(fixture.root, 'private', 'ffprobe'),
      signal: controller.signal,
      metadataOverride: null,
    })
    await probeStarted
    controller.abort()
    await assert.rejects(preparation, { name: 'AbortError' })
    assert.equal(observedSignal, controller.signal)
    assert.deepEqual(await readdir(fixture.jobDirectory), [])
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('writes whichever lyrics representation is actually available', async () => {
  const fixture = await createJobFixture()
  const preparer = new SongLyricsAssetPreparer(
    async () => foundLyrics({ syncedLyrics: null }),
    async () => ({
      title: '测试歌曲',
      artist: '测试歌手',
      durationSeconds: 198.25,
    }),
  )
  try {
    const result = await preparer.prepare({
      jobDirectory: fixture.jobDirectory,
      sourcePath: path.join(fixture.root, 'private', 'song.wav'),
      sourceName: 'song.wav',
      ffprobePath: path.join(fixture.root, 'private', 'ffprobe'),
      signal: uncancelledSignal(),
      metadataOverride: null,
    })
    assert.equal(result.status, 'ready')
    assert.deepEqual(await readdir(fixture.jobDirectory), [
      'lyrics-manifest.json',
      'lyrics.txt',
    ])
    assert.equal(result.manifest.hasSyncedLyrics, false)
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('fails closed before lookup when trustworthy metadata is unavailable', async () => {
  const fixture = await createJobFixture()
  let lookupCalls = 0
  const preparer = new SongLyricsAssetPreparer(
    async () => {
      lookupCalls += 1
      return foundLyrics()
    },
    async () => ({ title: null, artist: null, durationSeconds: 200 }),
  )
  try {
    const result = await preparer.prepare({
      jobDirectory: fixture.jobDirectory,
      sourcePath: path.join(fixture.root, 'private', 'vocals.wav'),
      sourceName: 'vocals.wav',
      ffprobePath: path.join(fixture.root, 'private', 'ffprobe'),
      signal: uncancelledSignal(),
      metadataOverride: null,
    })
    assert.deepEqual(result, { status: 'needs-metadata' })
    assert.equal(lookupCalls, 0)
    assert.deepEqual(await readdir(fixture.jobDirectory), [])
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('uses online results to disambiguate an untagged two-part filename', async () => {
  const fixture = await createJobFixture()
  const seenQueries = []
  const preparer = new SongLyricsAssetPreparer(
    async (query) => {
      seenQueries.push(query)
      if (query.artist === '歌手' && query.title === '歌名') {
        return foundLyrics()
      }
      return Object.freeze({
        status: 'not-found',
        plainLyrics: null,
        syncedLyrics: null,
        providerRecord: null,
      })
    },
    async () => ({ title: null, artist: null, durationSeconds: 180 }),
  )
  try {
    const result = await preparer.prepare({
      jobDirectory: fixture.jobDirectory,
      sourcePath: path.join(fixture.root, 'private', 'song.flac'),
      sourceName: '歌手 - 歌名.flac',
      ffprobePath: path.join(fixture.root, 'private', 'ffprobe'),
      signal: uncancelledSignal(),
      metadataOverride: null,
    })
    assert.equal(result.status, 'ready')
    assert.deepEqual(seenQueries, [
      { title: '歌名', artist: '歌手', durationSeconds: 180 },
      { title: '歌手', artist: '歌名', durationSeconds: 180 },
    ])
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('disambiguates a lyric-video underscore filename through online results', async () => {
  const fixture = await createJobFixture()
  const seenQueries = []
  const preparer = new SongLyricsAssetPreparer(
    async (query) => {
      seenQueries.push(query)
      if (query.artist === '要不要买菜' && query.title === '勿忘我') {
        return foundLyrics()
      }
      return Object.freeze({
        status: 'not-found',
        plainLyrics: null,
        syncedLyrics: null,
        providerRecord: null,
      })
    },
    async () => ({ title: null, artist: null, durationSeconds: 215.4 }),
  )
  try {
    const result = await preparer.prepare({
      jobDirectory: fixture.jobDirectory,
      sourcePath: path.join(fixture.root, 'private', 'song.mp3'),
      sourceName: '要不要买菜_勿忘我【動態歌詞_Lyrics_Video】.mp3',
      ffprobePath: path.join(fixture.root, 'private', 'ffprobe'),
      signal: uncancelledSignal(),
      metadataOverride: null,
    })
    assert.equal(result.status, 'ready')
    assert.deepEqual(seenQueries, [
      { title: '勿忘我', artist: '要不要买菜', durationSeconds: 215.4 },
      { title: '要不要买菜', artist: '勿忘我', durationSeconds: 215.4 },
    ])
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('requires confirmation when both filename directions match different songs', async () => {
  const fixture = await createJobFixture()
  let recordId = 9000
  const preparer = new SongLyricsAssetPreparer(
    async () => foundLyrics({
      providerRecord: Object.freeze({
        ...foundLyrics().providerRecord,
        recordId: recordId += 1,
      }),
    }),
    async () => ({ title: null, artist: null, durationSeconds: 180 }),
  )
  try {
    const result = await preparer.prepare({
      jobDirectory: fixture.jobDirectory,
      sourcePath: path.join(fixture.root, 'private', 'song.flac'),
      sourceName: '甲 - 乙.flac',
      ffprobePath: path.join(fixture.root, 'private', 'ffprobe'),
      signal: uncancelledSignal(),
      metadataOverride: null,
    })
    assert.deepEqual(result, { status: 'needs-metadata' })
    assert.deepEqual(await readdir(fixture.jobDirectory), [])
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('preserves provider miss status without materializing near-match lyrics', async () => {
  for (const status of ['not-found', 'low-confidence']) {
    const fixture = await createJobFixture()
    const preparer = new SongLyricsAssetPreparer(
      async () => ({
        status,
        plainLyrics: null,
        syncedLyrics: null,
        providerRecord: null,
      }),
      async () => ({
        title: '测试歌曲',
        artist: '测试歌手',
        durationSeconds: 198.25,
      }),
    )
    try {
      const result = await preparer.prepare({
        jobDirectory: fixture.jobDirectory,
        sourcePath: path.join(fixture.root, 'private', 'song.mp3'),
        sourceName: 'song.mp3',
        ffprobePath: path.join(fixture.root, 'private', 'ffprobe'),
        signal: uncancelledSignal(),
        metadataOverride: null,
      })
      assert.deepEqual(result, { status })
      assert.deepEqual(await readdir(fixture.jobDirectory), [])
    } finally {
      await rm(fixture.root, { recursive: true, force: true })
    }
  }
})

test('removes newly written lyrics when atomic asset-set creation fails', async () => {
  const fixture = await createJobFixture()
  const manifestPath = path.join(
    fixture.jobDirectory,
    'lyrics-manifest.json',
  )
  await writeFile(manifestPath, 'pre-existing', 'utf8')
  const preparer = new SongLyricsAssetPreparer(
    async () => foundLyrics(),
    async () => ({
      title: '测试歌曲',
      artist: '测试歌手',
      durationSeconds: 198.25,
    }),
  )
  try {
    await assert.rejects(
      preparer.prepare({
        jobDirectory: fixture.jobDirectory,
        sourcePath: path.join(fixture.root, 'private', 'song.mp3'),
        sourceName: 'song.mp3',
        ffprobePath: path.join(fixture.root, 'private', 'ffprobe'),
        signal: uncancelledSignal(),
        metadataOverride: null,
      }),
      /Private song lyrics could not be prepared/u,
    )
    assert.deepEqual(await readdir(fixture.jobDirectory), [
      'lyrics-manifest.json',
    ])
    assert.equal(await readFile(manifestPath, 'utf8'), 'pre-existing')
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('maps provider failures to one fixed error without native metadata', async () => {
  const fixture = await createJobFixture()
  const preparer = new SongLyricsAssetPreparer(
    async () => {
      throw new Error(`token and ${fixture.root}`)
    },
    async () => ({
      title: '测试歌曲',
      artist: '测试歌手',
      durationSeconds: 198.25,
    }),
  )
  try {
    await assert.rejects(
      preparer.prepare({
        jobDirectory: fixture.jobDirectory,
        sourcePath: path.join(fixture.root, 'private', 'song.mp3'),
        sourceName: 'song.mp3',
        ffprobePath: path.join(fixture.root, 'private', 'ffprobe'),
        signal: uncancelledSignal(),
        metadataOverride: null,
      }),
      (error) => {
        assert.equal(error.message, 'Online lyrics could not be retrieved safely.')
        assert.equal(error.message.includes(fixture.root), false)
        return true
      },
    )
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('maps metadata-probe failures without exposing the source path', async () => {
  const fixture = await createJobFixture()
  const preparer = new SongLyricsAssetPreparer(
    async () => foundLyrics(),
    async () => { throw new Error(`failed at ${fixture.root}`) },
  )
  try {
    await assert.rejects(
      preparer.prepare({
        jobDirectory: fixture.jobDirectory,
        sourcePath: path.join(fixture.root, 'private', 'song.mp3'),
        sourceName: 'song.mp3',
        ffprobePath: path.join(fixture.root, 'private', 'ffprobe'),
        signal: uncancelledSignal(),
        metadataOverride: null,
      }),
      (error) => {
        assert.equal(error.message, 'Song lyrics metadata could not be read.')
        assert.equal(error.message.includes(fixture.root), false)
        return true
      },
    )
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})
