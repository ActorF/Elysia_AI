/** @fileoverview Define the path-free image preview capability exposed by Main. */

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
  /** Revalidate a Gallery artifact and authorize one short-lived preview URL. */
  openImagePreview(artifactId: string): Promise<ImagePreviewCapability>
  /** Open a native Save dialog and export reverified pixels without disclosing paths. */
  exportImageArtifact(artifactId: string): Promise<{ artifactId: string; exported: boolean }>
}
