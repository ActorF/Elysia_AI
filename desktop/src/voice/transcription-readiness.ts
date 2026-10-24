/** Convert sanitized STT readiness enums into actionable renderer copy. */

import { t } from '../i18n/locale.ts'
import type { VoiceTranscriptionStatus } from '../../electron/protocol.ts'

/** Presentation data shared by Voice capture and the Settings status card. */
export interface TranscriptionReadinessPresentation {
  label: string
  summary: string
  blockingMessage: string | null
  tone: 'success' | 'warning' | 'error'
}

/**
 * Describe local recognition without exposing model paths or native errors.
 *
 * Python sends only closed enums across the trust boundary. Keeping the copy
 * here gives every renderer surface the same recovery action while preventing
 * an adapter diagnostic from being rendered accidentally.
 */
export function describeTranscriptionReadiness(
  status: VoiceTranscriptionStatus,
): TranscriptionReadinessPresentation {
  if (status.state === 'ready') {
    const fallback = status.reason === 'cuda_initialization_failed'
      ? t(" CUDA initialization failed, so the active model fell back safely.")
      : status.reason === 'cuda_unavailable'
        ? t(" CUDA is unavailable, so Auto loaded the model on CPU.")
        : ''
    return {
      label: t("Loaded"),
      summary: t('The {model} model is loaded on {device}.{fallback}', { model: status.model, device: deviceLabel(status), fallback }),
      blockingMessage: null,
      tone: fallback ? 'warning' : 'success',
    }
  }

  if (status.state === 'available') {
    const fallback = status.reason === 'cuda_unavailable'
      ? t(" CUDA is unavailable; Auto will use the CPU fallback.")
      : ''
    return {
      label: t("Available"),
      summary: t('The {model} model is installed and will use {device}.{fallback}', { model: status.model, device: deviceLabel(status), fallback }),
      blockingMessage: null,
      tone: fallback ? 'warning' : 'success',
    }
  }

  switch (status.reason) {
    case 'model_missing': {
      const message = t('Install the selected {model} model in models/weights/faster-whisper/{model}, then restart the Backend.', { model: status.model })
      return unavailable(message)
    }
    case 'dependencies_missing':
      return unavailable(
        t("Install the optional local speech-recognition dependencies, then restart the Backend."),
      )
    case 'device_unavailable':
      if (status.requestedDevice === 'cuda') {
        return unavailable(
          t("The selected CUDA transcription device is unavailable. Choose Auto or CPU, save, and restart the Backend."),
        )
      }
      if (status.requestedDevice === 'cpu') {
        return unavailable(
          t("The selected CPU transcription device is unavailable. Reinstall the optional runtime or choose Auto, then restart the Backend."),
        )
      }
      return unavailable(
        t("No supported local transcription device is available. Reinstall the optional runtime, then restart the Backend."),
      )
    case 'initialization_failed':
      return unavailable(
        t("The local speech model could not initialize. Choose Auto or a smaller installed model, then restart the Backend."),
      )
    case 'runtime_probe_failed':
      return unavailable(
        t("The local speech runtime could not be inspected. Reinstall its optional dependencies and restart the Backend."),
      )
    case 'cuda_unavailable':
      return unavailable(
        t("CUDA transcription is unavailable. Choose Auto or CPU, save, and restart the Backend."),
      )
    case 'cuda_initialization_failed':
      return unavailable(
        t("CUDA transcription could not initialize. Choose Auto or CPU, save, and restart the Backend."),
      )
    case null:
      return unavailable(
        t("Local speech recognition is unavailable. Review its Settings and restart the Backend."),
      )
  }
}

function deviceLabel(status: VoiceTranscriptionStatus): string {
  const device = status.resolvedDevice ?? status.requestedDevice
  const compute = status.computeType === null
    ? ''
    : ` (${status.computeType.replace('_', ' ')})`
  return `${device === 'auto' ? t('Auto') : device.toUpperCase()}${compute}`
}

function unavailable(message: string): TranscriptionReadinessPresentation {
  return {
    label: t("Unavailable"),
    summary: message,
    blockingMessage: message,
    tone: 'error',
  }
}
