/**
 * @fileoverview Classify Song Cover lifecycle stages for renderer controls.
 */

import type { SongCoverStage } from '../../electron/song-cover-contracts.ts'

const BUSY_SONG_COVER_STAGES: ReadonlySet<SongCoverStage> = new Set([
  'validating',
  'separating',
  'transcribing',
  'aligning',
  'synthesizing',
  'converting',
  'mixing',
])

/** Return whether a Song Cover stage owns the singing-generation pipeline. */
export function isSongCoverBusyStage(
  stage: SongCoverStage | null | undefined,
): boolean {
  return stage !== null
    && stage !== undefined
    && BUSY_SONG_COVER_STAGES.has(stage)
}
