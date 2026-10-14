/** Expose only job-bound configuration and one-frame completion inside the private sandbox capture helper. */

import { contextBridge, ipcRenderer } from 'electron'

const argument = process.argv.find(value => /^--elysia-vision-capture-job=[0-9a-f]{32}$/u.test(value))
const jobId = argument?.slice('--elysia-vision-capture-job='.length)
if (jobId !== undefined) {
  const prefix = `vision-capture-private:${jobId}:`
  contextBridge.exposeInMainWorld('elysiaPrivateVisionCapture', Object.freeze({
    /** Fetch only the native-approved normalized crop under an exact helper-frame check. */
    configuration: (): unknown => ipcRenderer.sendSync(`${prefix}configuration`),
    /** Publish one bounded frame after every local media track has stopped; Main validates the bytes again. */
    finish: (value: unknown): Promise<unknown> => ipcRenderer.invoke(`${prefix}finish`, value),
    /** Report a fixed native failure without exposing browser exceptions or captured data. */
    failure: (): Promise<unknown> => ipcRenderer.invoke(`${prefix}failure`),
  }))
}
