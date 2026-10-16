/** @fileoverview Define the path-free image preview capability exposed by Main. */

import type { ImageGenerateParams, ImageGenerationResult, ImageGalleryListParams,
  ImageGalleryListResult, ImageGalleryDeleteResult, ImageGalleryGetResult } from './protocol.js'

/** Path-free model readiness and reviewed preset choices. */
export interface ImageSettingsSummary {
  configured: boolean
  ready: boolean
  state: 'unconfigured' | 'missing_model' | 'hash_mismatch' | 'ready' | 'invalid'
  message: string
  revision: number
  defaultProfileId: string | null
  profiles: Array<{
    profileId: string
    displayName: string
    architecture: string
    promptDialect: string
    defaultSizePresetId: string
    sizePresets: Array<{ presetId: string; width: number; height: number }>
  }>
}

/** A short-lived, single-use image URL; pixels stay outside Renderer IPC. */
export interface ImagePreviewCapability {
  artifactId: string
  url: string
  width: number
  height: number
  expiresAt: number
}

/** Request previews using opaque Gallery identities only. */
export interface ImageClient {
  /** Read reviewed model choices and current local runtime readiness. */
  getImageSettings(): Promise<ImageSettingsSummary>
  /** Generate and commit one structured original-character request. */
  generateImage(params: ImageGenerateParams): Promise<ImageGenerationResult>
  /** Cancel the current image task without releasing its physical admission early. */
  cancelImageGeneration(): Promise<void>
  /** Read one bounded Gallery page without image pixels or storage paths. */
  getImageGallery(params: ImageGalleryListParams): Promise<ImageGalleryListResult>
  /** Read one opaque artifact's public metadata. */
  getImageArtifact(artifactId: string): Promise<ImageGalleryGetResult>
  /** Delete an opaque Gallery artifact after the Renderer confirms its selection. */
  deleteImageArtifact(artifactId: string): Promise<ImageGalleryDeleteResult>
  /** Revalidate a Gallery artifact and authorize one short-lived preview URL. */
  openImagePreview(artifactId: string): Promise<ImagePreviewCapability>
  /** Open a native Save dialog and export reverified pixels without disclosing paths. */
  exportImageArtifact(artifactId: string): Promise<{ artifactId: string; exported: boolean }>
}
