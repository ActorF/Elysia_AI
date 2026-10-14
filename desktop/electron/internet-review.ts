/** Retain one expiring native source comparison and reject changed evidence before manual Chat draft export. */

import { randomUUID } from 'node:crypto'
import type { InternetSource, InternetResearchScope } from './internet-protocol.js'
import { formatInternetReview, parseInternetReview, parseInternetReviewLookup, parseInternetReviewRequest,
  type InternetReview, type InternetReviewLookup, type InternetReviewRequest } from './internet-review-protocol.js'

/** Compare provenance without claiming automated truth verification or independent corroboration. */
export class InternetReviewStore {
  private review: InternetReview | null = null
  /** Use a private clock for expiry and deterministic regression fixtures. */
  constructor(private readonly now: () => number = Date.now) {}

  /** Resolve source identities from Main and retain one five-minute, scope-bound comparison. */
  prepare(owner: InternetResearchScope, value: InternetReviewRequest, lookup: (id: string) => InternetSource): InternetReview {
    const request = parseInternetReviewRequest(value)
    if (owner.chatId !== request.chatId) throw new Error('Source review owner changed.')
    const sources = request.sourceIds.map(lookup), warnings = ['Source agreement has not been independently verified.']
    const domains = new Set(sources.map(source => new URL(source.url).hostname))
    if (sources.length === 1) warnings.push('Only one source is selected; independent corroboration is missing.')
    else if (domains.size < sources.length) warnings.push('Some sources share a domain and may not be independent.')
    if (sources.some(source => source.publishedAt === null)) warnings.push('At least one publication date is unknown.')
    if (sources.some(source => source.publishedAt !== null && Date.parse(source.publishedAt) > Date.parse(source.fetchedAt))) {
      warnings.push('A claimed publication date is later than retrieval; check the website metadata and clock.')
    }
    if (request.eventDate !== null && sources.some(source => source.publishedAt !== null
      && source.publishedAt.slice(0, 10) < request.eventDate!)) {
      warnings.push('Some sources predate the supplied event date and may describe expectations rather than the completed event.')
    }
    if (sources.some(source => source.fromCache)) warnings.push('Cached evidence may be outdated; retrieval time is preserved.')
    if (sources.some(source => Date.parse(source.fetchedAt) > this.now() + 60_000)) warnings.push('A retrieval timestamp is ahead of the local clock.')
    if (sources.some(source => this.now() - Date.parse(source.fetchedAt) > 86_400_000)) warnings.push('Some evidence was retrieved more than one day ago.')
    if (request.interpretation === 'inference') warnings.push('The interpretation is an inference, not a directly established source fact.')
    if (request.eventDate === null) warnings.push('Event date is unknown; publication or edit dates do not establish it.')
    this.review = parseInternetReview({ id: `review_${randomUUID().replaceAll('-', '')}`, owner, request, sources, warnings,
      expiresAt: new Date(this.now() + 300_000).toISOString() })
    return parseInternetReview(this.review)
  }

  /** Recheck scope, expiry and exact native source identities/content before producing draft text. */
  resolve(owner: InternetResearchScope, value: InternetReviewLookup, lookup: (id: string) => InternetSource): { review: InternetReview; text: string } {
    const request = parseInternetReviewLookup(value), review = this.review
    if (review === null || review.id !== request.reviewId || request.chatId !== owner.chatId
      || review.owner.chatId !== owner.chatId || review.owner.projectId !== owner.projectId || Date.parse(review.expiresAt) <= this.now()) {
      throw new Error('Source review expired or its owner changed.')
    }
    if (review.sources.some(source => JSON.stringify(lookup(source.id)) !== JSON.stringify(source))) throw new Error('Reviewed evidence changed.')
    const detached = parseInternetReview(review)
    return { review: detached, text: formatInternetReview(detached) }
  }

  /** Forget retained excerpts on explicit clear, renderer replacement or shutdown. */
  clear(): void { this.review = null }
}
