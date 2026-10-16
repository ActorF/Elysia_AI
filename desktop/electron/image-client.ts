/** @fileoverview Define the path-free image preview capability exposed by Main. */

import type { ImageGenerateParams, ImageGenerationResult, ImageGalleryListParams,
  ImageGalleryListResult, ImageGalleryDeleteResult, ImageGalleryGetResult } from './protocol.js'

/** Select the reviewed prompt dialect without changing the fixed node graph. */
export type ImagePromptDialect = 'sdxl-natural' | 'pony-tags' | 'illustrious-tags'

/** Describe model provenance and defaults while Main selects every native path. */
export interface ImageProfileMetadata {
  readonly displayName: string
  readonly profileId: string
  readonly promptDialect: ImagePromptDialect
  readonly checkpointSource: string
  readonly checkpointLicense: string
  readonly defaultSizePresetId: 'square' | 'portrait' | 'landscape'
}

/** Path-free model readiness and reviewed preset choices. */
export interface ImageSettingsSummary {
  configured: boolean
  ready: boolean
  state: 'unconfigured' | 'missing_model' | 'hash_mismatch' | 'ready' | 'invalid' | 'recovery_required'
  message: string
  revision: number
  defaultProfileId: string | null
  profiles: ReadonlyArray<{
    profileId: string
    displayName: string
    architecture: string
    promptDialect: string
    defaultSizePresetId: string
    sizePresets: ReadonlyArray<{ presetId: string; width: number; height: number }>
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
  /** Run native installation picks and explicit hash/provenance consent before saving. */
  configureImageInstallation(metadata: ImageProfileMetadata): Promise<ImageSettingsSummary>
  /** Quarantine incomplete private runs after Main's native confirmation, retaining their files. */
  recoverImageRuntime(): Promise<ImageSettingsSummary>
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
