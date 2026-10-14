/**
 * Acquire one explicitly approved display/window frame inside the private sandbox helper.
 *
 * Main selects the native source; this page cannot choose handles or grant
 * permission. Display crops temporarily receive one complete selected-display
 * frame before copying only the normalized region. All tracks stop and local
 * pixel references are cleared before publishing the private PNG back to Main.
 */

(() => {
  let started = false

  /** Wait for one decoded video frame rather than serializing an uninitialized or stale canvas. */
  function frame(video) {
    return new Promise(resolve => video.requestVideoFrameCallback(() => resolve()))
  }

  /** Encode bounded owned canvas pixels without metadata from an original file. */
  function blob(canvas) {
    return new Promise((resolve, reject) => canvas.toBlob(value => value === null ? reject(new Error('invalid_image')) : resolve(value), 'image/png'))
  }

  /** Start at most once under Main's explicit transient user gesture; no background invocation exists. */
  globalThis.elysiaCaptureStart = async () => {
    if (started) return
    started = true
    const bridge = globalThis.elysiaPrivateVisionCapture
    let stream = null, video = null, canvas = null, pixels = null, result = null
    try {
      const configuration = bridge.configuration()
      if (configuration === null) throw new Error('unavailable')
      stream = await navigator.mediaDevices.getDisplayMedia({ audio: false, video: { frameRate: 1,
        width: { ideal: 8192, max: 8192 }, height: { ideal: 8192, max: 8192 } } })
      if (stream.getAudioTracks().length !== 0 || stream.getVideoTracks().length !== 1) throw new Error('unavailable')
      video = document.createElement('video')
      video.muted = true; video.srcObject = stream
      await video.play(); await frame(video)
      const width = video.videoWidth, height = video.videoHeight
      if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width < 1 || height < 1
        || width > 8192 || height > 8192 || width * height > 16 * 1024 * 1024) throw new Error('invalid_image')
      const region = configuration.region
      let x = 0, y = 0, outputWidth = width, outputHeight = height
      if (region !== null) {
        // Round inward so the published crop contains no pixels outside the
        // selected normalized rectangle. Sub-pixel selections fail explicitly.
        x = Math.ceil(region.x * width); y = Math.ceil(region.y * height)
        outputWidth = Math.floor((region.x + region.width) * width) - x
        outputHeight = Math.floor((region.y + region.height) * height) - y
        if (outputWidth < 1 || outputHeight < 1) throw new Error('invalid_image')
      }
      canvas = document.createElement('canvas'); canvas.width = outputWidth; canvas.height = outputHeight
      const context = canvas.getContext('2d', { alpha: false })
      if (context === null) throw new Error('invalid_image')
      context.drawImage(video, x, y, outputWidth, outputHeight, 0, 0, outputWidth, outputHeight)
      const encoded = await blob(canvas)
      if (encoded.size < 45 || encoded.size > 16 * 1024 * 1024) throw new Error('invalid_image')
      pixels = new Uint8Array(await encoded.arrayBuffer())
      result = { bytes: pixels, width: outputWidth, height: outputHeight }
    } catch { result = null }
    finally {
      // Track.stop is synchronous and changes readyState to ended. Main also
      // destroys this WebContents and waits for destruction before releasing
      // capacity, including browser/OS failures that bypass this finally block.
      if (stream !== null) for (const track of stream.getTracks()) track.stop()
      if (video !== null) { video.pause(); video.srcObject = null }
      if (canvas !== null) { canvas.width = 0; canvas.height = 0 }
      stream = null; video = null; canvas = null
    }
    try {
      if (result === null) await bridge.failure()
      else await bridge.finish(result)
    } finally {
      if (pixels !== null) pixels.fill(0)
      pixels = null; result = null
    }
  }
})()
