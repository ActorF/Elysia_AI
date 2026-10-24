/** Bilingual Internet search, source review, cache, policy, and GitHub publishing interface copy. */

import type { MessageCatalog } from './types.ts'

export const internetFeatureMessages: MessageCatalog = {
  'Deciding whether to look online…': { en: 'Deciding whether to look online…', 'zh-CN': '正在判断是否需要联网查询…' },
  'Searching the web…': { en: 'Searching the web…', 'zh-CN': '正在搜索网上资料…' },
  'Checking the weather…': { en: 'Checking the weather…', 'zh-CN': '正在查询天气…' },
  'Reading allowed web pages…': { en: 'Reading allowed web pages…', 'zh-CN': '正在阅读已允许网站的正文…' },
  'Preparing your answer…': { en: 'Preparing your answer…', 'zh-CN': '正在整理回答…' },
  'Live information could not be verified.': { en: 'Live information could not be verified.', 'zh-CN': '未能核实实时信息。' },
  'Open Internet settings': { en: 'Open Internet settings', 'zh-CN': '打开联网设置' },
  'Internet on': { en: 'Internet on', 'zh-CN': '联网已开启' },
  'Internet off': { en: 'Internet off', 'zh-CN': '联网已关闭' },
  'Internet unavailable': { en: 'Internet unavailable', 'zh-CN': '联网状态未知' },
  'Internet on. Open Internet settings.': { en: 'Internet on. Open Internet settings.', 'zh-CN': '联网已开启，打开联网设置。' },
  'Internet off. Open Internet settings.': { en: 'Internet off. Open Internet settings.', 'zh-CN': '联网已关闭，打开联网设置。' },
  'Internet status unavailable. Open Internet settings.': { en: 'Internet status unavailable. Open Internet settings.', 'zh-CN': '联网状态未知，打开联网设置。' },
  "The preview expired. Review a fresh preview before creating an Issue.": {
    "en": "The preview expired. Review a fresh preview before creating an Issue.",
    "zh-CN": "预览已过期，创建议题前请检查新的预览。"
  },
  "The preview changed or was already consumed. Review a fresh preview.": {
    "en": "The preview changed or was already consumed. Review a fresh preview.",
    "zh-CN": "预览已更改或已使用，请检查新的预览。"
  },
  "A GitHub token is not configured for this app session.": {
    "en": "A GitHub token is not configured for this app session.",
    "zh-CN": "本次应用会话尚未配置 GitHub 令牌。"
  },
  "Internet is off. Enable it before preparing a new Issue.": {
    "en": "Internet is off. Enable it before preparing a new Issue.",
    "zh-CN": "联网已关闭，请先开启再准备新的议题。"
  },
  "Allow api.github.com in Internet settings before preparing a new Issue.": {
    "en": "Allow api.github.com in Internet settings before preparing a new Issue.",
    "zh-CN": "准备新议题前，请在联网设置中允许 api.github.com。"
  },
  "GitHub rejected the request. Check repository access and token permissions before preparing another preview.": {
    "en": "GitHub rejected the request. Check repository access and token permissions before preparing another preview.",
    "zh-CN": "GitHub 拒绝了请求，请检查仓库访问权限和令牌权限后再准备新的预览。"
  },
  "The request was not sent. Check the connection before preparing another preview.": {
    "en": "The request was not sent. Check the connection before preparing another preview.",
    "zh-CN": "请求尚未发送，请检查连接后再准备新的预览。"
  },
  "Wait for the current action to finish before preparing another preview.": {
    "en": "Wait for the current action to finish before preparing another preview.",
    "zh-CN": "请等待当前操作完成后再准备新的预览。"
  },
  "Could not check GitHub write configuration.": {
    "en": "Could not check GitHub write configuration.",
    "zh-CN": "无法检查 GitHub 写入配置。"
  },
  "This Issue preview expired. Review a new preview before continuing.": {
    "en": "This Issue preview expired. Review a new preview before continuing.",
    "zh-CN": "此议题预览已过期，请检查新的预览后继续。"
  },
  "Issue preview changed or expired.": {
    "en": "Issue preview changed or expired.",
    "zh-CN": "议题预览已更改或过期。"
  },
  "Could not prepare this Issue. Check the current inputs, Internet policy and token configuration.": {
    "en": "Could not prepare this Issue. Check the current inputs, Internet policy and token configuration.",
    "zh-CN": "无法准备此议题，请检查当前输入、联网策略和令牌配置。"
  },
  "This Issue preview expired. Review a new preview.": {
    "en": "This Issue preview expired. Review a new preview.",
    "zh-CN": "此议题预览已过期，请检查新的预览。"
  },
  "Issue identity changed.": {
    "en": "Issue identity changed.",
    "zh-CN": "议题标识已更改。"
  },
  "Create GitHub Issue": {
    "en": "Create GitHub Issue",
    "zh-CN": "创建 GitHub 议题"
  },
  "Review before publishing": {
    "en": "Review before publishing",
    "zh-CN": "发布前检查"
  },
  "Previewing sends nothing. Creating an Issue requires a separate confirmation window and may notify repository participants.": {
    "en": "Previewing sends nothing. Creating an Issue requires a separate confirmation window and may notify repository participants.",
    "zh-CN": "预览不会发送任何内容。创建议题需要在独立窗口中确认，并可能通知仓库参与者。"
  },
  "A GitHub write token is configured for this app session.": {
    "en": "A GitHub write token is configured for this app session.",
    "zh-CN": "本次应用会话已配置 GitHub 写入令牌。"
  },
  "GitHub Issue creation is unavailable until a write token is configured. Public repository queries remain available.": {
    "en": "GitHub Issue creation is unavailable until a write token is configured. Public repository queries remain available.",
    "zh-CN": "配置写入令牌后才能创建 GitHub 议题，公共仓库查询仍可使用。"
  },
  "Token setup help": {
    "en": "Token setup help",
    "zh-CN": "令牌配置帮助"
  },
  "Before starting Elysia, set ELYSIA_GITHUB_TOKEN in its launch environment to a GitHub token with access to the chosen repository and Issues write permission. Restart Elysia after changing it. The token is kept in the main process and is not entered here or sent to the AI. See the project documentation, docs/19-INTERNET-AND-SERVICES.md, for setup details.": {
    "en": "Before starting Elysia, set ELYSIA_GITHUB_TOKEN in its launch environment to a GitHub token with access to the chosen repository and Issues write permission. Restart Elysia after changing it. The token is kept in the main process and is not entered here or sent to the AI. See the project documentation, docs/19-INTERNET-AND-SERVICES.md, for setup details.",
    "zh-CN": "启动 Elysia 前，请在启动环境中将 ELYSIA_GITHUB_TOKEN 设为拥有所选仓库访问权限和议题写入权限的 GitHub 令牌。更改后需要重启 Elysia。令牌仅保存在主进程中，无需在此输入，也不会发送给 AI。详细配置方法请参阅项目文档 docs/19-INTERNET-AND-SERVICES.md。"
  },
  "Enable Internet and allow api.github.com in Internet settings before previewing.": {
    "en": "Enable Internet and allow api.github.com in Internet settings before previewing.",
    "zh-CN": "预览前，请开启联网并在联网设置中允许 api.github.com。"
  },
  "Issue repository owner": {
    "en": "Issue repository owner",
    "zh-CN": "议题所在仓库的所有者"
  },
  "Issue repository name": {
    "en": "Issue repository name",
    "zh-CN": "议题所在仓库的名称"
  },
  "Issue title": {
    "en": "Issue title",
    "zh-CN": "议题标题"
  },
  "Issue body (optional)": {
    "en": "Issue body (optional)",
    "zh-CN": "议题正文（可选）"
  },
  "Title ": {
    "en": "Title ",
    "zh-CN": "标题 "
  },
  "/256 · Body ": {
    "en": "/256 · Body ",
    "zh-CN": "/256 · 正文 "
  },
  "/4,000 UTF-8 bytes. Empty bodies are allowed.": {
    "en": "/4,000 UTF-8 bytes. Empty bodies are allowed.",
    "zh-CN": "/4,000 UTF-8 字节。正文可以留空。"
  },
  "Waiting for the Issue action…": {
    "en": "Waiting for the Issue action…",
    "zh-CN": "正在等待议题操作…"
  },
  "Preview Issue": {
    "en": "Preview Issue",
    "zh-CN": "预览议题"
  },
  "GitHub Issue preview": {
    "en": "GitHub Issue preview",
    "zh-CN": "GitHub 议题预览"
  },
  "Title": {
    "en": "Title",
    "zh-CN": "标题"
  },
  "Body": {
    "en": "Body",
    "zh-CN": "正文"
  },
  "(Empty body)": {
    "en": "(Empty body)",
    "zh-CN": "（正文为空）"
  },
  "Expires: ": {
    "en": "Expires: ",
    "zh-CN": "到期时间："
  },
  ". The confirmation window will show this exact repository, title and body again.": {
    "en": ". The confirmation window will show this exact repository, title and body again.",
    "zh-CN": "。确认窗口将再次显示此处的仓库、标题和正文。"
  },
  "Create Issue…": {
    "en": "Create Issue…",
    "zh-CN": "创建议题…"
  },
  "GitHub Issue outcome": {
    "en": "GitHub Issue outcome",
    "zh-CN": "GitHub 议题操作结果"
  },
  "GitHub confirmed Issue #": {
    "en": "GitHub confirmed Issue #",
    "zh-CN": "GitHub 已确认议题 #"
  },
  " was created. ": {
    "en": " was created. ",
    "zh-CN": " 创建成功。"
  },
  "Open created Issue": {
    "en": "Open created Issue",
    "zh-CN": "打开已创建的议题"
  },
  "Cancelled before the Issue request was sent. No Issue was created by this attempt.": {
    "en": "Cancelled before the Issue request was sent. No Issue was created by this attempt.",
    "zh-CN": "已在发送请求前取消，本次尝试未创建任何议题。"
  },
  "The outcome is unknown. GitHub may already have created the Issue. Inspect the repository before preparing any new attempt to avoid duplicates. Elysia will not retry automatically.": {
    "en": "The outcome is unknown. GitHub may already have created the Issue. Inspect the repository before preparing any new attempt to avoid duplicates. Elysia will not retry automatically.",
    "zh-CN": "结果尚不确定，GitHub 可能已经创建了议题。再次尝试前请检查仓库，避免重复创建。Elysia 不会自动重试。"
  },
  "The Issue could not be created. Review the current Chat, inputs and Internet settings before preparing another preview.": {
    "en": "The Issue could not be created. Review the current Chat, inputs and Internet settings before preparing another preview.",
    "zh-CN": "无法创建议题，请检查当前聊天、输入内容和联网设置后再准备新的预览。"
  },
  "The cache owner changed.": {
    "en": "The cache owner changed.",
    "zh-CN": "缓存所属对象已更改。"
  },
  "Could not list cached results for this Chat. Try listing again when the Chat is ready.": {
    "en": "Could not list cached results for this Chat. Try listing again when the Chat is ready.",
    "zh-CN": "无法列出此聊天的缓存结果，请在聊天就绪后重试。"
  },
  "Offline Internet cache": {
    "en": "Offline Internet cache",
    "zh-CN": "离线联网缓存"
  },
  "Saved for this session": {
    "en": "Saved for this session",
    "zh-CN": "本次会话已保存的内容"
  },
  "Restore sources offline, with their original retrieval times.": {
    "en": "Restore sources offline, with their original retrieval times.",
    "zh-CN": "离线恢复来源，并保留原始获取时间。"
  },
  "Availability and freshness": {
    "en": "Availability and freshness",
    "zh-CN": "可用性与时效"
  },
  "Up to 20 results stay in memory for this app session and are cleared when the Chat or Project changes. Search, weather and repository results have a five-minute freshness window; page extracts have thirty minutes. Expired content requires explicit confirmation and may be outdated. Restoring does not contact the provider.": {
    "en": "Up to 20 results stay in memory for this app session and are cleared when the Chat or Project changes. Search, weather and repository results have a five-minute freshness window; page extracts have thirty minutes. Expired content requires explicit confirmation and may be outdated. Restoring does not contact the provider.",
    "zh-CN": "本次应用会话最多在内存中保留 20 条结果，切换聊天或项目后清除。搜索、天气和仓库结果的有效期为 5 分钟，网页摘录为 30 分钟。过期内容可能已不准确，需要明确确认后使用。恢复缓存不会联系服务商。"
  },
  "Listing cached results…": {
    "en": "Listing cached results…",
    "zh-CN": "正在列出缓存结果…"
  },
  "List cached results": {
    "en": "List cached results",
    "zh-CN": "列出缓存结果"
  },
  "Clear cache and research": {
    "en": "Clear cache and research",
    "zh-CN": "清除缓存和检索记录"
  },
  "No cached results are available for this Chat.": {
    "en": "No cached results are available for this Chat.",
    "zh-CN": "此聊天没有可用的缓存结果。"
  },
  "Cached result {label}": {
    "en": "Cached result {label}",
    "zh-CN": "缓存结果 {label}"
  },
  " source": {
    "en": " source",
    "zh-CN": " 个来源"
  },
  "Expired": {
    "en": "Expired",
    "zh-CN": "已过期"
  },
  "Within freshness window at last listing": {
    "en": "Within freshness window at last listing",
    "zh-CN": "上次列出时仍在有效期内"
  },
  "Originally retrieved": {
    "en": "Originally retrieved",
    "zh-CN": "原始获取时间"
  },
  "Fresh until": {
    "en": "Fresh until",
    "zh-CN": "有效期至"
  },
  "Use this expired cached content; I understand it may be outdated.": {
    "en": "Use this expired cached content; I understand it may be outdated.",
    "zh-CN": "使用此过期缓存；我了解内容可能已不准确。"
  },
  "Restore cached result": {
    "en": "Restore cached result",
    "zh-CN": "恢复缓存结果"
  },
  "Internet is off. Enable it and allow the selected provider domain in Internet settings.": {
    "en": "Internet is off. Enable it and allow the selected provider domain in Internet settings.",
    "zh-CN": "联网已关闭，请先开启，并在联网设置中允许所选服务商的域名。"
  },
  "This domain is not allowed. Add the exact provider domain in Internet settings.": {
    "en": "This domain is not allowed. Add the exact provider domain in Internet settings.",
    "zh-CN": "此域名未获允许，请在联网设置中添加服务商的完整域名。"
  },
  "Wait for the current action to finish before starting another request.": {
    "en": "Wait for the current action to finish before starting another request.",
    "zh-CN": "请等待当前操作完成后再开始新的请求。"
  },
  "The active Chat or Project changed. Search again in the current Chat.": {
    "en": "The active Chat or Project changed. Search again in the current Chat.",
    "zh-CN": "当前聊天或项目已更改，请在当前聊天中重新搜索。"
  },
  "The request was cancelled.": {
    "en": "The request was cancelled.",
    "zh-CN": "请求已取消。"
  },
  "The request timed out. You can try again explicitly.": {
    "en": "The request timed out. You can try again explicitly.",
    "zh-CN": "请求已超时，你可以手动重试。"
  },
  "The online service could not be reached. Check your connection or configured proxy.": {
    "en": "The online service could not be reached. Check your connection or configured proxy.",
    "zh-CN": "无法连接在线服务，请检查网络连接或已配置的代理。"
  },
  "The service returned an unsupported response.": {
    "en": "The service returned an unsupported response.",
    "zh-CN": "服务返回了不支持的响应。"
  },
  "The service response exceeded the allowed size.": {
    "en": "The service response exceeded the allowed size.",
    "zh-CN": "服务响应超过了允许的大小。"
  },
  "Enter a valid query, page URL, coordinates or repository name.": {
    "en": "Enter a valid query, page URL, coordinates or repository name.",
    "zh-CN": "请输入有效的搜索词、网页地址、坐标或仓库名称。"
  },
  "The requested Internet source is unavailable. Check the input and try again explicitly.": {
    "en": "The requested Internet source is unavailable. Check the input and try again explicitly.",
    "zh-CN": "请求的网络来源不可用，请检查输入后手动重试。"
  },
  "This response is not a supported HTML or plain-text page.": {
    "en": "This response is not a supported HTML or plain-text page.",
    "zh-CN": "此响应不是受支持的 HTML 或纯文本网页。"
  },
  "This page is not valid UTF-8 text.": {
    "en": "This page is not valid UTF-8 text.",
    "zh-CN": "此网页不是有效的 UTF-8 文本。"
  },
  "The page could not be read as a supported document.": {
    "en": "The page could not be read as a supported document.",
    "zh-CN": "无法将此网页读取为受支持的文档。"
  },
  "This page did not contain readable text.": {
    "en": "This page did not contain readable text.",
    "zh-CN": "此网页不包含可读文本。"
  },
  "This cached result expired. List cached results again and explicitly allow the expired content to restore it offline.": {
    "en": "This cached result expired. List cached results again and explicitly allow the expired content to restore it offline.",
    "zh-CN": "此缓存结果已过期。请重新列出缓存结果，并明确允许使用过期内容后离线恢复。"
  },
  "This cached result is no longer available. List cached results again to choose another local entry.": {
    "en": "This cached result is no longer available. List cached results again to choose another local entry.",
    "zh-CN": "此缓存结果已不可用，请重新列出缓存结果并选择其他本地条目。"
  },
  "Unknown": {
    "en": "Unknown",
    "zh-CN": "未知"
  },
  "Wikipedia encyclopedia search result": {
    "en": "Wikipedia encyclopedia search result",
    "zh-CN": "维基百科搜索结果"
  },
  "Web page · plain text": {
    "en": "Web page · plain text",
    "zh-CN": "网页 · 纯文本"
  },
  "Open-Meteo weather · model-derived current conditions": {
    "en": "Open-Meteo weather · model-derived current conditions",
    "zh-CN": "Open-Meteo 天气 · 基于模型的当前天气"
  },
  "GitHub · public repository metadata": {
    "en": "GitHub · public repository metadata",
    "zh-CN": "GitHub · 公共仓库元数据"
  },
  "Could not load research for this Chat.": {
    "en": "Could not load research for this Chat.",
    "zh-CN": "无法加载此聊天的检索记录。"
  },
  "The research action could not complete. Check Internet settings and try again.": {
    "en": "The research action could not complete. Check Internet settings and try again.",
    "zh-CN": "检索操作未能完成，请检查联网设置后重试。"
  },
  "Cancellation requested. Waiting for the network request to finish closing.": {
    "en": "Cancellation requested. Waiting for the network request to finish closing.",
    "zh-CN": "已请求取消，正在等待网络请求关闭。"
  },
  "Could not cancel this request. Turn Internet off to revoke access.": {
    "en": "Could not cancel this request. Turn Internet off to revoke access.",
    "zh-CN": "无法取消此请求，可关闭联网以撤销访问权限。"
  },
  "Internet is off. Active in-app requests are being cancelled.": {
    "en": "Internet is off. Active in-app requests are being cancelled.",
    "zh-CN": "联网已关闭，正在取消应用内的活动请求。"
  },
  "Could not save Internet preferences. Review the current policy in Settings.": {
    "en": "Could not save Internet preferences. Review the current policy in Settings.",
    "zh-CN": "无法保存联网偏好，请在设置中检查当前策略。"
  },
  "The reviewed source changed.": {
    "en": "The reviewed source changed.",
    "zh-CN": "已检查的来源发生了更改。"
  },
  "Reviewed source added to your Chat draft. Review or edit the message before sending.": {
    "en": "Reviewed source added to your Chat draft. Review or edit the message before sending.",
    "zh-CN": "已检查的来源已添加到聊天草稿，发送前请检查或编辑消息。"
  },
  "This source could not be added. Review it again in the current Chat.": {
    "en": "This source could not be added. Review it again in the current Chat.",
    "zh-CN": "无法添加此来源，请在当前聊天中重新检查。"
  },
  "Reviewing sources…": {
    "en": "Reviewing sources…",
    "zh-CN": "正在检查来源…"
  },
  "Loading saved sources…": {
    "en": "Loading saved sources…",
    "zh-CN": "正在加载已保存的来源…"
  },
  "Internet": {
    "en": "Internet",
    "zh-CN": "联网"
  },
  "Find sources. Review before adding to your Chat.": {
    "en": "Find sources. Review before adding to your Chat.",
    "zh-CN": "查找来源，检查后添加到聊天。"
  },
  "Internet settings": {
    "en": "Internet settings",
    "zh-CN": "联网设置"
  },
  "Turn Internet off": {
    "en": "Turn Internet off",
    "zh-CN": "关闭联网"
  },
  "Internet is off. Enable access in Settings, or use saved sources offline.": {
    "en": "Internet is off. Enable access in Settings, or use saved sources offline.",
    "zh-CN": "联网已关闭，可在设置中开启，或离线使用已保存的来源。"
  },
  "Internet tools": {
    "en": "Internet tools",
    "zh-CN": "联网工具"
  },
  "Search Wikipedia in English or Chinese. Encyclopedia sources, without an API key.": {
    "en": "Search Wikipedia in English or Chinese. Encyclopedia sources, without an API key.",
    "zh-CN": "搜索中文或英文维基百科，无需 API 密钥即可查找百科来源。"
  },
  "Search query": {
    "en": "Search query",
    "zh-CN": "搜索词"
  },
  "What would you like to find?": {
    "en": "What would you like to find?",
    "zh-CN": "你想查找什么？"
  },
  "Wikipedia language": {
    "en": "Wikipedia language",
    "zh-CN": "维基百科语言"
  },
  "English": {
    "en": "English",
    "zh-CN": "英语"
  },
  "中文": {
    "en": "Chinese",
    "zh-CN": "中文"
  },
  "Allow ": {
    "en": "Allow ",
    "zh-CN": "搜索前请在联网设置中允许 "
  },
  ".wikipedia.org in Internet settings before searching.": {
    "en": ".wikipedia.org in Internet settings before searching.",
    "zh-CN": ".wikipedia.org。"
  },
  "Researching…": {
    "en": "Researching…",
    "zh-CN": "正在检索…"
  },
  "Search Wikipedia": {
    "en": "Search Wikipedia",
    "zh-CN": "搜索维基百科"
  },
  "About these sources": {
    "en": "About these sources",
    "zh-CN": "关于这些来源"
  },
  "These are encyclopedia results; they do not cover the whole web and may not include recent events. Sources are added to your draft only after review.": {
    "en": "These are encyclopedia results; they do not cover the whole web and may not include recent events. Sources are added to your draft only after review.",
    "zh-CN": "这些是百科结果，不覆盖整个互联网，也可能不包含最新事件。只有经过检查的来源才会添加到草稿。"
  },
  "Web page reader": {
    "en": "Web page reader",
    "zh-CN": "网页阅读"
  },
  "Read a public web page as plain text.": {
    "en": "Read a public web page as plain text.",
    "zh-CN": "以纯文本读取公开网页。"
  },
  "Page URL": {
    "en": "Page URL",
    "zh-CN": "网页地址"
  },
  "Enter a public HTTPS URL without credentials or a fragment.": {
    "en": "Enter a public HTTPS URL without credentials or a fragment.",
    "zh-CN": "请输入公开的 HTTPS 地址，不含凭证或片段标识。"
  },
  "Allow this exact domain in Internet settings before reading the page.": {
    "en": "Allow this exact domain in Internet settings before reading the page.",
    "zh-CN": "读取网页前，请在联网设置中允许此完整域名。"
  },
  "Read page": {
    "en": "Read page",
    "zh-CN": "读取网页"
  },
  "Supported pages": {
    "en": "Supported pages",
    "zh-CN": "支持的网页"
  },
  "Public HTTPS HTML and text only. Responses are limited to 1 MiB and extracted text to 32 KiB. Every redirected domain must also be allowed.": {
    "en": "Public HTTPS HTML and text only. Responses are limited to 1 MiB and extracted text to 32 KiB. Every redirected domain must also be allowed.",
    "zh-CN": "仅支持通过 HTTPS 访问的公开 HTML 和文本。响应上限为 1 MiB，提取文本上限为 32 KiB。所有重定向域名也必须获得允许。"
  },
  "Create a GitHub Issue ": {
    "en": "Create a GitHub Issue ",
    "zh-CN": "创建 GitHub 议题 "
  },
  "Optional · requires a token": {
    "en": "Optional · requires a token",
    "zh-CN": "可选 · 需要令牌"
  },
  "Recent searches ": {
    "en": "Recent searches ",
    "zh-CN": "最近搜索 "
  },
  "Internet query history": {
    "en": "Internet query history",
    "zh-CN": "联网查询历史"
  },
  "Up to 20 queries from this session. Changing Chat or Project clears this review.": {
    "en": "Up to 20 queries from this session. Changing Chat or Project clears this review.",
    "zh-CN": "最多显示本次会话的 20 条查询，切换聊天或项目后清除。"
  },
  " results · ": {
    "en": " results · ",
    "zh-CN": " 条结果 · "
  },
  "Clear research history": {
    "en": "Clear research history",
    "zh-CN": "清除检索历史"
  },
  "Request in progress…": {
    "en": "Request in progress…",
    "zh-CN": "请求进行中…"
  },
  "Cancelling…": {
    "en": "Cancelling…",
    "zh-CN": "正在取消…"
  },
  "Cancel research": {
    "en": "Cancel research",
    "zh-CN": "取消检索"
  },
  " found": {
    "en": " found",
    "zh-CN": " 条结果"
  },
  "Source {title}": {
    "en": "Source {title}",
    "zh-CN": "来源 {title}"
  },
  "Saved source": {
    "en": "Saved source",
    "zh-CN": "已保存的来源"
  },
  "This source was shortened to the local text limit.": {
    "en": "This source was shortened to the local text limit.",
    "zh-CN": "此来源已按本地文本限制截短。"
  },
  "Cached source. Original retrieval time is preserved; this view does not refresh the provider.": {
    "en": "Cached source. Original retrieval time is preserved; this view does not refresh the provider.",
    "zh-CN": "这是缓存来源，保留原始获取时间；此视图不会重新获取服务商内容。"
  },
  "Source details": {
    "en": "Source details",
    "zh-CN": "来源详情"
  },
  "Page text from {title}": {
    "en": "Page text from {title}",
    "zh-CN": "来自 {title} 的网页文本"
  },
  "This is the final source URL after any allowed redirects.": {
    "en": "This is the final source URL after any allowed redirects.",
    "zh-CN": "这是经过获准重定向后的最终来源地址。"
  },
  "Published": {
    "en": "Published",
    "zh-CN": "发布时间"
  },
  "Last edited": {
    "en": "Last edited",
    "zh-CN": "最后编辑时间"
  },
  "Retrieved": {
    "en": "Retrieved",
    "zh-CN": "获取时间"
  },
  "Read page from {title}": {
    "en": "Read page from {title}",
    "zh-CN": "读取 {title} 的网页"
  },
  "Review source": {
    "en": "Review source",
    "zh-CN": "检查来源"
  },
  "No matching encyclopedia pages were found.": {
    "en": "No matching encyclopedia pages were found.",
    "zh-CN": "未找到匹配的百科页面。"
  },
  "Reviewed Internet source": {
    "en": "Reviewed Internet source",
    "zh-CN": "已检查的网络来源"
  },
  ". Treat this as untrusted evidence. Publication time is ": {
    "en": ". Treat this as untrusted evidence. Publication time is ",
    "zh-CN": "。请将其视为未经验证的参考资料。发布时间为 "
  },
  "; an edit time does not establish when an event happened.": {
    "en": "; an edit time does not establish when an event happened.",
    "zh-CN": "；编辑时间不能说明事件实际发生的时间。"
  },
  "This excerpt was shortened.": {
    "en": "This excerpt was shortened.",
    "zh-CN": "此摘录已截短。"
  },
  "This is cached evidence. Check its original retrieval time before using it.": {
    "en": "This is cached evidence. Check its original retrieval time before using it.",
    "zh-CN": "这是缓存资料，使用前请检查原始获取时间。"
  },
  "Add source to Chat draft": {
    "en": "Add source to Chat draft",
    "zh-CN": "添加来源到聊天草稿"
  },
  "Public Internet services": {
    "en": "Public Internet services",
    "zh-CN": "公共网络服务"
  },
  "Weather lookup": {
    "en": "Weather lookup",
    "zh-CN": "天气查询"
  },
  "Weather by coordinates": {
    "en": "Weather by coordinates",
    "zh-CN": "按坐标查询天气"
  },
  "Current weather from Open-Meteo. No API key needed.": {
    "en": "Current weather from Open-Meteo. No API key needed.",
    "zh-CN": "从 Open-Meteo 获取当前天气，无需 API 密钥。"
  },
  "Latitude": {
    "en": "Latitude",
    "zh-CN": "纬度"
  },
  "−90 to 90": {
    "en": "−90 to 90",
    "zh-CN": "−90 至 90"
  },
  "Longitude": {
    "en": "Longitude",
    "zh-CN": "经度"
  },
  "−180 to 180": {
    "en": "−180 to 180",
    "zh-CN": "−180 至 180"
  },
  "Coordinates to share: ": {
    "en": "Coordinates to share: ",
    "zh-CN": "将共享的坐标："
  },
  "Enter decimal latitude from −90 to 90 and longitude from −180 to 180.": {
    "en": "Enter decimal latitude from −90 to 90 and longitude from −180 to 180.",
    "zh-CN": "请输入十进制坐标：纬度范围为 −90 至 90，经度范围为 −180 至 180。"
  },
  "Enable Internet and allow api.open-meteo.com in Internet settings.": {
    "en": "Enable Internet and allow api.open-meteo.com in Internet settings.",
    "zh-CN": "请开启联网，并在联网设置中允许 api.open-meteo.com。"
  },
  "Get weather": {
    "en": "Get weather",
    "zh-CN": "获取天气"
  },
  "Location and weather data": {
    "en": "Location and weather data",
    "zh-CN": "位置与天气数据"
  },
  "Open-Meteo receives coordinates rounded to four decimal places only when you click Get weather. No location is detected automatically. Current conditions come from weather models, rather than a direct local station observation. Provider time, retrieval time and units accompany the result.": {
    "en": "Open-Meteo receives coordinates rounded to four decimal places only when you click Get weather. No location is detected automatically. Current conditions come from weather models, rather than a direct local station observation. Provider time, retrieval time and units accompany the result.",
    "zh-CN": "仅当你点击“获取天气”时，Open-Meteo 才会收到四舍五入到小数点后四位的坐标。应用不会自动检测位置。当前天气来自气象模型，而非当地气象站的直接观测。结果附有服务商时间、获取时间和单位。"
  },
  "Public GitHub repository lookup": {
    "en": "Public GitHub repository lookup",
    "zh-CN": "查询 GitHub 公共仓库"
  },
  "Public GitHub repository": {
    "en": "Public GitHub repository",
    "zh-CN": "GitHub 公共仓库"
  },
  "Repository details, activity and counts. No token needed.": {
    "en": "Repository details, activity and counts. No token needed.",
    "zh-CN": "查看仓库详情、活动和统计数据，无需令牌。"
  },
  "GitHub owner": {
    "en": "GitHub owner",
    "zh-CN": "GitHub 所有者"
  },
  "Repository name": {
    "en": "Repository name",
    "zh-CN": "仓库名称"
  },
  "Enter valid owner and repository names, without a URL or slash.": {
    "en": "Enter valid owner and repository names, without a URL or slash.",
    "zh-CN": "请输入有效的所有者和仓库名称，不含网址或斜杠。"
  },
  "Enable Internet and allow api.github.com in Internet settings.": {
    "en": "Enable Internet and allow api.github.com in Internet settings.",
    "zh-CN": "请开启联网，并在联网设置中允许 api.github.com。"
  },
  "Get repository": {
    "en": "Get repository",
    "zh-CN": "获取仓库信息"
  },
  "Use up to 32 exact domain names, a timeout from 1 to 60 seconds, and a proxy port from 1 to 65535.": {
    "en": "Use up to 32 exact domain names, a timeout from 1 to 60 seconds, and a proxy port from 1 to 65535.",
    "zh-CN": "最多可设置 32 个完整域名，超时范围为 1 至 60 秒，代理端口范围为 1 至 65535。"
  },
  "Internet preferences could not be loaded. Internet access remains unavailable.": {
    "en": "Internet preferences could not be loaded. Internet access remains unavailable.",
    "zh-CN": "无法加载联网偏好，联网访问仍不可用。"
  },
  "Could not reload Internet preferences. Try again.": {
    "en": "Could not reload Internet preferences. Try again.",
    "zh-CN": "无法重新加载联网偏好，请重试。"
  },
  "Internet preferences saved.": {
    "en": "Internet preferences saved.",
    "zh-CN": "联网偏好已保存。"
  },
  "Internet preferences could not be saved. Reload the current policy before trying again.": {
    "en": "Internet preferences could not be saved. Reload the current policy before trying again.",
    "zh-CN": "无法保存联网偏好，请重新加载当前策略后重试。"
  },
  "Elysia can now search when your message needs online information.": {
    "en": "Elysia can now search when your message needs online information.",
    "zh-CN": "现在，当你的消息需要在线信息时，Elysia 可以进行搜索。"
  },
  "Internet could not be enabled. Reload preferences and try again.": {
    "en": "Internet could not be enabled. Reload preferences and try again.",
    "zh-CN": "无法开启联网，请重新加载偏好后重试。"
  },
  "Internet is off. In-app requests have been cancelled.": {
    "en": "Internet is off. In-app requests have been cancelled.",
    "zh-CN": "联网已关闭，应用内请求已取消。"
  },
  "Could not save the off preference. Reload to check the current state before restarting Elysia.": {
    "en": "Could not save the off preference. Reload to check the current state before restarting Elysia.",
    "zh-CN": "无法保存关闭联网的偏好。重启 Elysia 前，请重新加载并检查当前状态。"
  },
  "Internet access": {
    "en": "Internet access",
    "zh-CN": "联网访问"
  },
  "Let Elysia look up current information while you chat.": {
    "en": "Let Elysia look up current information while you chat.",
    "zh-CN": "让 Elysia 在聊天时查找最新信息。"
  },
  "Internet policy is unavailable.": {
    "en": "Internet policy is unavailable.",
    "zh-CN": "联网策略不可用。"
  },
  "Internet is {value1}.": {
    "en": "Internet is {value1}.",
    "zh-CN": "联网{value1}。"
  },
  "Internet settings require the desktop application.": {
    "en": "Internet settings require the desktop application.",
    "zh-CN": "联网设置需要使用桌面应用。"
  },
  "Internet is off because its preferences could not be saved or read. Check local storage, then reload before enabling it.": {
    "en": "Internet is off because its preferences could not be saved or read. Check local storage, then reload before enabling it.",
    "zh-CN": "由于无法保存或读取偏好，联网已关闭。请检查本地存储，重新加载后再开启。"
  },
  "Reload Internet preferences": {
    "en": "Reload Internet preferences",
    "zh-CN": "重新加载联网偏好"
  },
  "Allow Internet access": {
    "en": "Allow Internet access",
    "zh-CN": "允许联网访问"
  },
  "Turning Internet off…": {
    "en": "Turning Internet off…",
    "zh-CN": "正在关闭联网…"
  },
  "No API key or domain setup is needed. Web search, Wikipedia, weather, public GitHub and lyrics are included.": {
    "en": "No API key or domain setup is needed. Web search, Wikipedia, weather, public GitHub and lyrics are included.",
    "zh-CN": "无需配置 API 密钥或域名，已包含网页搜索、维基百科、天气、GitHub 公共仓库和歌词查询。"
  },
  "Advanced connection settings": {
    "en": "Advanced connection settings",
    "zh-CN": "高级连接设置"
  },
  "Allowed domains": {
    "en": "Allowed domains",
    "zh-CN": "允许的域名"
  },
  "Optional extra websites for manual reading. Built-in sources are already allowed. One exact domain per line; URLs, wildcards, IP addresses and local addresses are not allowed.": {
    "en": "Optional extra websites for manual reading. Built-in sources are already allowed. One exact domain per line; URLs, wildcards, IP addresses and local addresses are not allowed.",
    "zh-CN": "可选填用于手动阅读的其他网站；内置来源已获允许。每行填写一个完整域名，不支持网址、通配符、IP 地址或本地地址。"
  },
  "Connection": {
    "en": "Connection",
    "zh-CN": "连接方式"
  },
  "Direct connection": {
    "en": "Direct connection",
    "zh-CN": "直接连接"
  },
  "Local HTTP proxy": {
    "en": "Local HTTP proxy",
    "zh-CN": "本地 HTTP 代理"
  },
  "Proxy port at 127.0.0.1": {
    "en": "Proxy port at 127.0.0.1",
    "zh-CN": "127.0.0.1 代理端口"
  },
  "Request timeout (seconds)": {
    "en": "Request timeout (seconds)",
    "zh-CN": "请求超时（秒）"
  },
  "Elysia ignores system and environment proxy settings. A local HTTP proxy uses 127.0.0.1 and the port above; if it fails, Elysia does not fall back to a direct connection.": {
    "en": "Elysia ignores system and environment proxy settings. A local HTTP proxy uses 127.0.0.1 and the port above; if it fails, Elysia does not fall back to a direct connection.",
    "zh-CN": "Elysia 不使用系统和环境变量中的代理设置。本地 HTTP 代理使用 127.0.0.1 及上方端口；代理失败时不会自动改用直接连接。"
  },
  "The active policy changed or a save failed. Reload to review the current policy before saving this draft.": {
    "en": "The active policy changed or a save failed. Reload to review the current policy before saving this draft.",
    "zh-CN": "当前策略已更改或保存失败。保存此草稿前，请重新加载并检查当前策略。"
  },
  "Saving Internet preferences…": {
    "en": "Saving Internet preferences…",
    "zh-CN": "正在保存联网偏好…"
  },
  "Save Internet preferences": {
    "en": "Save Internet preferences",
    "zh-CN": "保存联网偏好"
  },
  "Privacy and sources": {
    "en": "Privacy and sources",
    "zh-CN": "隐私与来源"
  },
  "Internet starts off each time Elysia opens; domain, proxy and timeout choices are saved.": {
    "en": "Internet starts off each time Elysia opens; domain, proxy and timeout choices are saved.",
    "zh-CN": "每次打开 Elysia 时联网默认为关闭；域名、代理和超时设置会保留。"
  },
  "When your message needs online information, Elysia sends a short AI-generated search query to its search provider. It does not upload the full conversation, images, files or local memories.": {
    "en": "When your message needs online information, Elysia sends a short AI-generated search query to its search provider. It does not upload the full conversation, images, files or local memories.",
    "zh-CN": "当你的消息需要在线信息时，Elysia 会向搜索服务商发送一段由 AI 生成的简短搜索词，不会上传完整聊天、图像、文件或本地记忆。"
  },
  "Built-in domains: ": {
    "en": "Built-in domains: ",
    "zh-CN": "内置域名："
  },
  ". Search results do not grant access to additional websites.": {
    "en": ". Search results do not grant access to additional websites.",
    "zh-CN": "。搜索结果不会授予对其他网站的访问权限。"
  },
  "Changing this policy cancels active in-app Internet requests. External browsers control their own traffic after a link opens.": {
    "en": "Changing this policy cancels active in-app Internet requests. External browsers control their own traffic after a link opens.",
    "zh-CN": "更改此策略会取消应用内正在进行的联网请求。打开链接后，外部浏览器自行管理其网络流量。"
  },
  "Local Ollama and other local model services remain available when Internet access is off.": {
    "en": "Local Ollama and other local model services remain available when Internet access is off.",
    "zh-CN": "关闭联网后，本地 Ollama 和其他本地模型服务仍可使用。"
  },
  "Online content is untrusted reference material. Sending, uploading, publishing, purchasing, or logging in requires separate confirmation.": {
    "en": "Online content is untrusted reference material. Sending, uploading, publishing, purchasing, or logging in requires separate confirmation.",
    "zh-CN": "在线内容仅作为未经验证的参考资料。发送、上传、发布、购买或登录需要另行确认。"
  },
  "This comparison expired. Compare the sources again before adding them to a draft.": {
    "en": "This comparison expired. Compare the sources again before adding them to a draft.",
    "zh-CN": "此对比已过期，添加到草稿前请重新对比来源。"
  },
  "Review provenance changed.": {
    "en": "Review provenance changed.",
    "zh-CN": "审核的来源信息已更改。"
  },
  "This comparison expired. Compare the sources again.": {
    "en": "This comparison expired. Compare the sources again.",
    "zh-CN": "此对比已过期，请重新对比来源。"
  },
  "Could not compare these sources. Review the current selection and try again.": {
    "en": "Could not compare these sources. Review the current selection and try again.",
    "zh-CN": "无法对比这些来源，请检查当前选择后重试。"
  },
  "The comparison expired or changed.": {
    "en": "The comparison expired or changed.",
    "zh-CN": "对比已过期或更改。"
  },
  "Source comparison added to your Chat draft. Review the message before sending.": {
    "en": "Source comparison added to your Chat draft. Review the message before sending.",
    "zh-CN": "来源对比已添加到聊天草稿，发送前请检查消息。"
  },
  "This comparison expired or changed. Compare the sources again before adding them to a draft.": {
    "en": "This comparison expired or changed. Compare the sources again before adding them to a draft.",
    "zh-CN": "此对比已过期或更改，添加到草稿前请重新对比来源。"
  },
  "Internet source comparison": {
    "en": "Internet source comparison",
    "zh-CN": "网络来源对比"
  },
  "Compare sources": {
    "en": "Compare sources",
    "zh-CN": "对比来源"
  },
  "Select up to five sources and a question. Review their provenance before adding them to your draft.": {
    "en": "Select up to five sources and a question. Review their provenance before adding them to your draft.",
    "zh-CN": "最多选择五个来源并填写问题，检查来源信息后再添加到草稿。"
  },
  "Search or read a page to add sources for comparison.": {
    "en": "Search or read a page to add sources for comparison.",
    "zh-CN": "先搜索或读取网页，以添加待对比的来源。"
  },
  "Question or claim": {
    "en": "Question or claim",
    "zh-CN": "问题或主张"
  },
  "Interpretation": {
    "en": "Interpretation",
    "zh-CN": "解读方式"
  },
  "What the sources report": {
    "en": "What the sources report",
    "zh-CN": "来源报告的内容"
  },
  "My inference": {
    "en": "My inference",
    "zh-CN": "我的推断"
  },
  "Event date you supplied (optional)": {
    "en": "Event date you supplied (optional)",
    "zh-CN": "你提供的事件日期（可选）"
  },
  "Uncertainty or conflicting information (optional)": {
    "en": "Uncertainty or conflicting information (optional)",
    "zh-CN": "不确定或相互冲突的信息（可选）"
  },
  "About this comparison": {
    "en": "About this comparison",
    "zh-CN": "关于此对比"
  },
  "This comparison checks provenance; it does not verify that a claim is true. Question: up to 2,000 UTF-8 bytes. Uncertainty: up to 1,000 bytes. Event dates are your input; publication and edit dates describe the source.": {
    "en": "This comparison checks provenance; it does not verify that a claim is true. Question: up to 2,000 UTF-8 bytes. Uncertainty: up to 1,000 bytes. Event dates are your input; publication and edit dates describe the source.",
    "zh-CN": "此对比检查来源信息，不验证主张是否真实。问题最多 2,000 UTF-8 字节，不确定信息最多 1,000 字节。事件日期由你提供，发布和编辑日期描述的是来源本身。"
  },
  "Reviewing comparison…": {
    "en": "Reviewing comparison…",
    "zh-CN": "正在检查对比…"
  },
  "Compare selected sources": {
    "en": "Compare selected sources",
    "zh-CN": "对比所选来源"
  },
  "Reviewed source comparison": {
    "en": "Reviewed source comparison",
    "zh-CN": "已检查的来源对比"
  },
  "Interpretation: ": {
    "en": "Interpretation: ",
    "zh-CN": "解读方式："
  },
  "Inference — not a directly established source fact": {
    "en": "Inference — not a directly established source fact",
    "zh-CN": "推断——并非来源直接确立的事实"
  },
  "Source report — not independently verified": {
    "en": "Source report — not independently verified",
    "zh-CN": "来源报告——未经独立验证"
  },
  "User-supplied event date: ": {
    "en": "User-supplied event date: ",
    "zh-CN": "用户提供的事件日期："
  },
  "Uncertainty: ": {
    "en": "Uncertainty: ",
    "zh-CN": "不确定信息："
  },
  "Not independently verified.": {
    "en": "Not independently verified.",
    "zh-CN": "未经独立验证。"
  },
  "Source comparison warnings": {
    "en": "Source comparison warnings",
    "zh-CN": "来源对比提醒"
  },
  "Compared source {title}": {
    "en": "Compared source {title}",
    "zh-CN": "已对比的来源 {title}"
  },
  "Cached source; its retrieval time is preserved.": {
    "en": "Cached source; its retrieval time is preserved.",
    "zh-CN": "这是缓存来源，保留了原始获取时间。"
  },
  "This source was shortened.": {
    "en": "This source was shortened.",
    "zh-CN": "此来源已截短。"
  },
  "This review expires at ": {
    "en": "This review expires at ",
    "zh-CN": "此检查结果到期时间为 "
  },
  ". Draft exports include bounded excerpts and direct source URLs.": {
    "en": ". Draft exports include bounded excerpts and direct source URLs.",
    "zh-CN": "。导出的草稿包含限定长度的摘录及来源直达网址。"
  },
  "Add comparison to Chat draft": {
    "en": "Add comparison to Chat draft",
    "zh-CN": "添加对比到聊天草稿"
  },
  "Simplified Chinese": {
    "en": "Simplified Chinese",
    "zh-CN": "简体中文"
  },
  "Internet research": {
    "en": "Internet research",
    "zh-CN": "联网检索"
  },
  "On": {
    "en": "On",
    "zh-CN": "开启"
  },
  "Off": {
    "en": "Off",
    "zh-CN": "关闭"
  },
  "Turning off…": {
    "en": "Turning off…",
    "zh-CN": "正在关闭…"
  },
  "Turn off": {
    "en": "Turn off",
    "zh-CN": "关闭"
  },
  "Search": {
    "en": "Search",
    "zh-CN": "搜索"
  },
  "Pages": {
    "en": "Pages",
    "zh-CN": "网页"
  },
  "Services": {
    "en": "Services",
    "zh-CN": "服务"
  },
  "Compare": {
    "en": "Compare",
    "zh-CN": "对比"
  },
  "Saved": {
    "en": "Saved",
    "zh-CN": "已保存"
  },
  "Chinese": {
    "en": "Chinese",
    "zh-CN": "中文"
  },
  "Failed": {
    "en": "Failed",
    "zh-CN": "失败"
  },
  "Cancelled": {
    "en": "Cancelled",
    "zh-CN": "已取消"
  },
  "Allow {domain} in Internet settings before searching.": {
    "en": "Allow {domain} in Internet settings before searching.",
    "zh-CN": "搜索前，请在联网设置中允许 {domain}。"
  },
  "{count} results": {
    "en": "{count} results",
    "zh-CN": "{count} 条结果"
  },
  "{count} found": {
    "en": "{count} found",
    "zh-CN": "找到 {count} 条结果"
  },
  "{count} source": {
    "en": "{count} source",
    "zh-CN": "{count} 个来源"
  },
  "{count} sources": {
    "en": "{count} sources",
    "zh-CN": "{count} 个来源"
  },
  "Internet is on.": {
    "en": "Internet is on.",
    "zh-CN": "联网已开启。"
  },
  "Internet is off.": {
    "en": "Internet is off.",
    "zh-CN": "联网已关闭。"
  },
  "Could not open the Issue. Check Internet settings.": {
    "en": "Could not open the Issue. Check Internet settings.",
    "zh-CN": "无法打开议题，请检查联网设置。"
  },
  "Could not open this source. Check Internet settings.": {
    "en": "Could not open this source. Check Internet settings.",
    "zh-CN": "无法打开此来源，请检查联网设置。"
  },
  "Source agreement has not been independently verified.": {
    "en": "Source agreement has not been independently verified.",
    "zh-CN": "来源之间的一致性尚未经过独立验证。"
  },
  "Only one source is selected; independent corroboration is missing.": {
    "en": "Only one source is selected; independent corroboration is missing.",
    "zh-CN": "仅选择了一个来源，缺少独立佐证。"
  },
  "Some sources share a domain and may not be independent.": {
    "en": "Some sources share a domain and may not be independent.",
    "zh-CN": "部分来源来自同一域名，可能并不独立。"
  },
  "At least one publication date is unknown.": {
    "en": "At least one publication date is unknown.",
    "zh-CN": "至少一个来源的发布日期未知。"
  },
  "A claimed publication date is later than retrieval; check the website metadata and clock.": {
    "en": "A claimed publication date is later than retrieval; check the website metadata and clock.",
    "zh-CN": "某个标注的发布日期晚于获取时间，请检查网站元数据和时钟。"
  },
  "Some sources predate the supplied event date and may describe expectations rather than the completed event.": {
    "en": "Some sources predate the supplied event date and may describe expectations rather than the completed event.",
    "zh-CN": "部分来源早于你提供的事件日期，内容可能是预期而非已发生的事件。"
  },
  "Cached evidence may be outdated; retrieval time is preserved.": {
    "en": "Cached evidence may be outdated; retrieval time is preserved.",
    "zh-CN": "缓存资料可能已不准确，已保留原始获取时间。"
  },
  "A retrieval timestamp is ahead of the local clock.": {
    "en": "A retrieval timestamp is ahead of the local clock.",
    "zh-CN": "某个获取时间晚于本地当前时间。"
  },
  "Some evidence was retrieved more than one day ago.": {
    "en": "Some evidence was retrieved more than one day ago.",
    "zh-CN": "部分资料是在一天前获取的。"
  },
  "The interpretation is an inference, not a directly established source fact.": {
    "en": "The interpretation is an inference, not a directly established source fact.",
    "zh-CN": "此解读属于推断，并非来源直接确立的事实。"
  },
  "Event date is unknown; publication or edit dates do not establish it.": {
    "en": "Event date is unknown; publication or edit dates do not establish it.",
    "zh-CN": "事件日期未知，不能根据发布或编辑日期确定。"
  }
}
