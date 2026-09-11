/** Keep renderer audio permissions narrow and independently testable. */

export interface AudioPermissionContext {
  isMainFrame: boolean
  isMainWindow: boolean
  requestingUrlTrusted: boolean
  currentUrlTrusted: boolean
}

function isTrustedContext(context: AudioPermissionContext): boolean {
  return context.isMainFrame
    && context.isMainWindow
    && context.requestingUrlTrusted
    && context.currentUrlTrusted
}

export function allowAudioPermissionCheck(
  permission: string,
  mediaType: string | undefined,
  context: AudioPermissionContext,
): boolean {
  return isTrustedContext(context)
    && permission === 'media'
    && mediaType === 'audio'
}

export function allowAudioPermissionRequest(
  permission: string,
  mediaTypes: readonly string[] | undefined,
  context: AudioPermissionContext,
): boolean {
  if (!isTrustedContext(context)) {
    return false
  }
  if (permission === 'speaker-selection') {
    return true
  }
  return permission === 'media'
    && mediaTypes?.length === 1
    && mediaTypes[0] === 'audio'
}
