/** Adapt explicit desktop gestures to Electron without exposing native paths, commands, or capture handles. */

import { clipboard, desktopCapturer, dialog, shell, type BrowserWindow } from 'electron'
import { spawn } from 'node:child_process'
import { lstat, realpath } from 'node:fs/promises'
import path from 'node:path'
import type { WorkDesktopDependencies, WorkDesktopOwner } from './work-desktop-tools.js'
import type { WorkDesktopStorage } from './work-desktop-storage.js'

/** Main authority is consulted again after disk or picker work, rather than trusting a renderer context. */
export interface WorkDesktopNativeHost {
  /** Read the current canonical Work owner and competing physical work. */
  getOwner(): WorkDesktopOwner
  /** Resolve storage from the application's current managed data root. */
  getStorage(): WorkDesktopStorage
  /** Return the trusted parent for native file selection. */
  getWindow(): BrowserWindow
}

function authority(host: WorkDesktopNativeHost): WorkDesktopOwner {
  const owner = host.getOwner()
  if (!owner.ready || owner.mode !== 'work' || owner.busy) throw new Error('Desktop authority is unavailable.')
  return { ...owner }
}

function recheck(host: WorkDesktopNativeHost, expected: WorkDesktopOwner): void {
  const owner = authority(host)
  if (owner.chatId !== expected.chatId || owner.projectId !== expected.projectId
    || owner.workspacePath !== expected.workspacePath) throw new Error('Desktop authority changed.')
}

function systemRoot(): string | null {
  if (process.platform !== 'win32') return null
  const root = process.env.SystemRoot
  if (root === undefined || !/^[A-Za-z]:\\/u.test(root) || path.win32.normalize(root) !== root
    || /[<>"|?*]/u.test(root) || Array.from(root).some((character) => (character.codePointAt(0) ?? 0) < 32)) return null
  return root
}

/** Create fixed native callbacks; callers cannot choose executable paths, arguments, URLs, or capture APIs. */
export function createWorkDesktopNativeCallbacks(host: WorkDesktopNativeHost): WorkDesktopDependencies {
  return {
    getOwner: host.getOwner,
    platform: process.platform,
    chooseFile: async () => {
      const selected = await dialog.showOpenDialog(host.getWindow(), {
        title: 'Select a Workspace document to review',
        properties: ['openFile', 'dontAddToRecent'],
        filters: [{ name: 'Review documents', extensions: ['txt', 'md', 'csv', 'json', 'pdf', 'docx'] }],
      })
      return !selected.canceled && selected.filePaths.length === 1 ? selected.filePaths[0]! : null
    },
    openDocument: async (document) => {
      const owner = authority(host)
      const storage = host.getStorage()
      const copy = await storage.saveReview(document)
      await storage.verifyReview(copy)
      // Disk verification can outlive a Backend disconnect or application quit.
      // The copy may remain for recovery, but an outdated owner cannot launch it.
      recheck(host, owner)
      // The opened path belongs to Main's identity-pinned private data tree.
      // Shared Workspace bytes no longer influence the default-app launch.
      if (await shell.openPath(copy) !== '') throw new Error('The review app refused this document.')
    },
    trustedSystemRoot: systemRoot,
    launchNotepad: async (executable, args) => {
      const owner = authority(host)
      const root = systemRoot()
      if (root === null || args.length !== 0 || executable !== path.win32.join(root, 'System32', 'notepad.exe')) {
        throw new Error('The fixed desktop application is unavailable.')
      }
      for (const target of [root, path.win32.join(root, 'System32'), executable]) {
        const stats = await lstat(target)
        if (stats.isSymbolicLink() || (target === executable ? !stats.isFile() : !stats.isDirectory())
          || (await realpath(target)).toLowerCase() !== path.win32.resolve(target).toLowerCase()) {
          throw new Error('The fixed desktop application is unavailable.')
        }
      }
      recheck(host, owner)
      await new Promise<void>((resolve, reject) => {
        // This visible app is explicitly requested by the user. The fixed
        // system executable receives no file, command, URL, or shell parsing.
        const child = spawn(executable, [], { shell: false, detached: true, stdio: 'ignore' })
        child.once('error', reject)
        child.once('spawn', () => { child.unref(); resolve() })
      })
    },
    readClipboard: () => clipboard.readText(),
    writeClipboard: (text) => clipboard.writeText(text),
    listWindows: async () => {
      const sources = await desktopCapturer.getSources({
        types: ['window'], thumbnailSize: { width: 0, height: 0 }, fetchWindowIcons: false,
      })
      // A zero-size thumbnail explicitly skips thumbnail capture processing.
      // Native images, icons, and source IDs never enter the renderer DTO.
      return sources.map((source) => ({ sourceId: source.id, title: source.name }))
    },
    audit: (entry) => host.getStorage().append(entry),
  }
}
