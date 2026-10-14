/** Capture an optional Main-only GitHub credential and render an independent exact-content native write confirmation. */

import type { MessageBoxOptions } from 'electron'
import { parseGithubIssueApproval, type GithubIssueApproval } from './internet-write-protocol.js'

/** Remove the launch credential before any child is spawned, retaining only a bounded private Main value. */
export function takeInternetGithubToken(environment: NodeJS.ProcessEnv): string | null {
  const token = environment.ELYSIA_GITHUB_TOKEN
  // Child processes inherit process.env by default. Consuming the one supported
  // launch credential here keeps it out of Python, model, voice and tool workers.
  delete environment.ELYSIA_GITHUB_TOKEN
  return typeof token === 'string' && /^[\x21-\x7e]{1,4096}$/u.test(token) ? token : null
}

/** Confirm one exact GitHub Issue payload with Cancel as the default, without opening a browser or sending data. */
export async function confirmGithubIssueNative(value: GithubIssueApproval,
  show: (options: MessageBoxOptions) => Promise<{ response: number }>): Promise<boolean> {
  const preview = parseGithubIssueApproval(value)
  const payload = JSON.stringify({ title: preview.title, body: preview.body }, null, 2)
    // Direction controls can visually reorder labels. Show their literal escape
    // notation in the authoritative dialog while preserving the submitted text.
    .replace(/[\u202a-\u202e\u2066-\u2069]/gu, character => `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`)
  const choice = await show({ type: 'question', title: 'Publish GitHub issue',
    message: `Create an issue in ${preview.owner}/${preview.repo}?`,
    detail: `Destination: https://api.github.com/repos/${preview.owner}/${preview.repo}/issues\n\n${payload}\n\n`
      + 'This sends the displayed title and body using your configured GitHub token and may notify repository subscribers. '
      + 'Closing Internet after transmission cannot undo a published issue. This approval applies once to this exact issue.',
    buttons: ['Cancel', 'Create this issue'], defaultId: 0, cancelId: 0, noLink: true })
  return choice.response === 1
}
