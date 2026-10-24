/** Define the closed interface languages and complete bilingual message entries. */

export type Locale = 'zh-CN' | 'en'
export type TranslationValues = Readonly<Record<string, string | number>>
export type MessageCatalog = Readonly<Record<string, Readonly<Record<Locale, string>>>>
