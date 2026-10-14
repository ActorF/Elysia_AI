/** Compute responsive cover crops that keep the Voice Call artwork's face in view. */

/** Normalized point in the original artwork used as the crop's visual anchor. */
export interface VoiceCallBackgroundFocalPoint {
  readonly x: number
  readonly y: number
}

/** Pixel dimensions and offsets for an image that fully covers its viewport. */
export interface VoiceCallBackgroundCrop {
  readonly width: number
  readonly height: number
  readonly left: number
  readonly top: number
}

/** Face location in the bundled portrait, measured against its original dimensions. */
export const VOICE_CALL_BACKGROUND_FOCAL_POINT: VoiceCallBackgroundFocalPoint = {
  x: 0.63,
  y: 0.46,
}

/**
 * Return a cover crop anchored to a normalized face position without blank edges.
 *
 * A small window receives a modest extra zoom: an ordinary width-bound cover
 * cannot move a portrait horizontally, so that extra image area lets the face
 * approach the center. Offsets are clamped to the covered rectangle, preserving
 * full coverage even when a face is near an edge or the window is unusually tall.
 * Invalid dimensions yield a harmless empty crop while measurement is pending.
 */
export function computeVoiceCallBackgroundCrop(
  viewportWidth: number,
  viewportHeight: number,
  imageWidth: number,
  imageHeight: number,
  focalPoint: VoiceCallBackgroundFocalPoint = VOICE_CALL_BACKGROUND_FOCAL_POINT,
): VoiceCallBackgroundCrop {
  if (
    ![viewportWidth, viewportHeight, imageWidth, imageHeight]
      .every((value) => Number.isFinite(value) && value > 0)
  ) {
    return { width: 0, height: 0, left: 0, top: 0 }
  }

  const compactZoom = 1 + 0.36 * Math.max(
    0,
    Math.min(1, (800 - viewportWidth) / 280),
  )
  const scale = Math.max(
    viewportWidth / imageWidth,
    viewportHeight / imageHeight,
  ) * compactZoom
  const width = imageWidth * scale
  const height = imageHeight * scale
  const focalX = Number.isFinite(focalPoint.x)
    ? Math.max(0, Math.min(1, focalPoint.x))
    : 0.5
  const focalY = Number.isFinite(focalPoint.y)
    ? Math.max(0, Math.min(1, focalPoint.y))
    : 0.5

  return {
    width,
    height,
    left: Math.max(viewportWidth - width, Math.min(0, viewportWidth / 2 - width * focalX)),
    top: Math.max(viewportHeight - height, Math.min(0, viewportHeight / 2 - height * focalY)),
  }
}
