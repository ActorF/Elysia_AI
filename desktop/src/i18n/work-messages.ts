/** Bilingual copy for Work plans, scoped tools, native approvals, and permission controls. */

import { settingsRuntimeMessages } from './settings-runtime-messages.ts'
import { workActivityMessages } from './work-activity-messages.ts'
import { runtimeMessages } from './runtime-messages.ts'
import type { MessageCatalog } from './types.ts'

export const workMessages: MessageCatalog = {
  ...runtimeMessages,
  ...workActivityMessages,
  ...settingsRuntimeMessages,
  "Work Workspace": {
    "en": "Work Workspace",
    "zh-CN": "工作区"
  },
  "Working folder": {
    "en": "Working folder",
    "zh-CN": "工作文件夹"
  },
  "Plan active": {
    "en": "Plan active",
    "zh-CN": "计划执行中"
  },
  "Working": {
    "en": "Working",
    "zh-CN": "处理中"
  },
  "Loading Workspace…": {
    "en": "Loading Workspace…",
    "zh-CN": "正在加载工作区…"
  },
  "No Workspace selected": {
    "en": "No Workspace selected",
    "zh-CN": "尚未选择工作区"
  },
  "Select a folder to enable file tools.": {
    "en": "Select a folder to enable file tools.",
    "zh-CN": "选择文件夹后即可使用文件工具。"
  },
  "File access is limited to this folder.": {
    "en": "File access is limited to this folder.",
    "zh-CN": "仅允许访问此文件夹内的文件。"
  },
  "Change Workspace": {
    "en": "Change Workspace",
    "zh-CN": "更换工作区"
  },
  "Choose Workspace": {
    "en": "Choose Workspace",
    "zh-CN": "选择工作区"
  },
  "Change": {
    "en": "Change",
    "zh-CN": "更换"
  },
  "Choose": {
    "en": "Choose",
    "zh-CN": "选择"
  },
  "Clear Workspace": {
    "en": "Clear Workspace",
    "zh-CN": "清除工作区"
  },
  "A plan is active · View plan": {
    "en": "A plan is active · View plan",
    "zh-CN": "计划正在执行 · 查看计划"
  },
  "Reload Workspace": {
    "en": "Reload Workspace",
    "zh-CN": "重新加载工作区"
  },
  "Workspace tools": {
    "en": "Workspace tools",
    "zh-CN": "工作区工具"
  },
  "Plan": {
    "en": "Plan",
    "zh-CN": "计划"
  },
  "Files": {
    "en": "Files",
    "zh-CN": "文件"
  },
  "History": {
    "en": "History",
    "zh-CN": "历史"
  },
  "Desktop": {
    "en": "Desktop",
    "zh-CN": "桌面"
  },
  "Could not load this Chat’s Workspace. Try again.": {
    "en": "Could not load this Chat’s Workspace. Try again.",
    "zh-CN": "无法加载此聊天的工作区，请重试。"
  },
  "Could not clear this Workspace. Try again.": {
    "en": "Could not clear this Workspace. Try again.",
    "zh-CN": "无法清除此工作区，请重试。"
  },
  "Could not bind the selected Workspace. Choose an accessible local directory.": {
    "en": "Could not bind the selected Workspace. Choose an accessible local directory.",
    "zh-CN": "无法关联所选工作区，请选择可访问的本地文件夹。"
  },
  "Modify waiting step": {
    "en": "Modify waiting step",
    "zh-CN": "修改待确认步骤"
  },
  "Use valid relative Workspace paths and bounded UTF-8 text.": {
    "en": "Use valid relative Workspace paths and bounded UTF-8 text.",
    "zh-CN": "请使用有效的工作区相对路径，并确保 UTF-8 文本未超过大小限制。"
  },
  "Revised relative path": {
    "en": "Revised relative path",
    "zh-CN": "修改后的相对路径"
  },
  "Revised destination": {
    "en": "Revised destination",
    "zh-CN": "修改后的目标路径"
  },
  "Revised content": {
    "en": "Revised content",
    "zh-CN": "修改后的内容"
  },
  "Create revised plan": {
    "en": "Create revised plan",
    "zh-CN": "创建修改后的计划"
  },
  "Keep original step": {
    "en": "Keep original step",
    "zh-CN": "保留原步骤"
  },
  "Could not load this Work plan. Reload to continue.": {
    "en": "Could not load this Work plan. Reload to continue.",
    "zh-CN": "无法加载此工作计划，请重新加载后继续。"
  },
  "The plan action could not complete. Reload the plan and try again.": {
    "en": "The plan action could not complete. Reload the plan and try again.",
    "zh-CN": "无法完成计划操作，请重新加载计划后重试。"
  },
  "Work plan": {
    "en": "Work plan",
    "zh-CN": "工作计划"
  },
  "Draft plan": {
    "en": "Draft plan",
    "zh-CN": "计划草稿"
  },
  "Your plan": {
    "en": "Your plan",
    "zh-CN": "当前计划"
  },
  "Review each step. File changes always need your approval.": {
    "en": "Review each step. File changes always need your approval.",
    "zh-CN": "请检查每个步骤，所有文件更改都需要你的批准。"
  },
  "Recovered paused. Resume rechecks the selected Workspace and prepares fresh approvals. Project Sources remain off after recovery; image context is not restored.": {
    "en": "Recovered paused. Resume rechecks the selected Workspace and prepares fresh approvals. Project Sources remain off after recovery; image context is not restored.",
    "zh-CN": "已恢复计划并保持暂停。继续时会重新检查工作区并请求新的批准。恢复后项目资料保持关闭，图像上下文不会恢复。"
  },
  "An interrupted write has an uncertain outcome. Check Work history and recovery copies before creating a new plan. It will not be replayed.": {
    "en": "An interrupted write has an uncertain outcome. Check Work history and recovery copies before creating a new plan. It will not be replayed.",
    "zh-CN": "中断的写入结果尚不确定。创建新计划前，请检查操作历史和恢复副本；此写入不会自动重试。"
  },
  "The latest checkpoint could not be saved. The step results below remain the actual local outcomes; check Work history before restarting.": {
    "en": "The latest checkpoint could not be saved. The step results below remain the actual local outcomes; check Work history before restarting.",
    "zh-CN": "无法保存最新检查点。下方步骤结果仍是本地实际执行结果，重新开始前请检查操作历史。"
  },
  "Selected visual context": {
    "en": "Selected visual context",
    "zh-CN": "已选图像上下文"
  },
  "Review the image context for this plan": {
    "en": "Review the image context for this plan",
    "zh-CN": "查看此计划的图像上下文"
  },
  "OCR text": {
    "en": "OCR text",
    "zh-CN": "文字识别结果"
  },
  "Model inference": {
    "en": "Model inference",
    "zh-CN": "模型推断"
  },
  "Your description": {
    "en": "Your description",
    "zh-CN": "你的描述"
  },
  "No OCR text selected.": {
    "en": "No OCR text selected.",
    "zh-CN": "未选择文字识别结果。"
  },
  "No model inference selected.": {
    "en": "No model inference selected.",
    "zh-CN": "未选择模型分析。"
  },
  "No description entered.": {
    "en": "No description entered.",
    "zh-CN": "尚未输入描述。"
  },
  "Remove image context from plan": {
    "en": "Remove image context from plan",
    "zh-CN": "从计划中移除图像上下文"
  },
  "No plan yet": {
    "en": "No plan yet",
    "zh-CN": "尚无计划"
  },
  "Add file actions to review and run together.": {
    "en": "Add file actions to review and run together.",
    "zh-CN": "添加文件操作，即可统一检查并执行。"
  },
  "Add a step": {
    "en": "Add a step",
    "zh-CN": "添加步骤"
  },
  "Every file change needs your approval.": {
    "en": "Every file change needs your approval.",
    "zh-CN": "每次文件更改都需要你的批准。"
  },
  "Plan title": {
    "en": "Plan title",
    "zh-CN": "计划名称"
  },
  "Consult this Chat’s Project Sources for advisory context": {
    "en": "Consult this Chat’s Project Sources for advisory context",
    "zh-CN": "参考此聊天的项目资料"
  },
  "Use Project Sources as reference": {
    "en": "Use Project Sources as reference",
    "zh-CN": "参考项目资料"
  },
  "Complete parameters": {
    "en": "Complete parameters",
    "zh-CN": "完整参数"
  },
  "Remove": {
    "en": "Remove",
    "zh-CN": "移除"
  },
  "Create plan": {
    "en": "Create plan",
    "zh-CN": "创建计划"
  },
  "Clear draft plan": {
    "en": "Clear draft plan",
    "zh-CN": "清空计划草稿"
  },
  " · local operation active": {
    "en": " · local operation active",
    "zh-CN": " · 本地操作正在执行"
  },
  "Advisory context cannot change these steps or grant approval.": {
    "en": "Advisory context cannot change these steps or grant approval.",
    "zh-CN": "参考上下文无法修改这些步骤，也不能代你批准。"
  },
  "Start plan": {
    "en": "Start plan",
    "zh-CN": "开始计划"
  },
  "Pause plan": {
    "en": "Pause plan",
    "zh-CN": "暂停计划"
  },
  "Resume plan": {
    "en": "Resume plan",
    "zh-CN": "继续计划"
  },
  "Cancel plan": {
    "en": "Cancel plan",
    "zh-CN": "取消计划"
  },
  "Work step approval": {
    "en": "Work step approval",
    "zh-CN": "工作步骤批准"
  },
  "Modify step": {
    "en": "Modify step",
    "zh-CN": "修改步骤"
  },
  "Waiting for the local operation to finish.": {
    "en": "Waiting for the local operation to finish.",
    "zh-CN": "正在等待本地操作结束。"
  },
  "Reload plan": {
    "en": "Reload plan",
    "zh-CN": "重新加载计划"
  },
  "Saved privately. Recovered plans stay paused until you resume; old approvals expire.": {
    "en": "Saved privately. Recovered plans stay paused until you resume; old approvals expire.",
    "zh-CN": "计划私密保存。恢复后保持暂停，需由你继续；旧批准将失效。"
  },
  "Workspace root": {
    "en": "Workspace root",
    "zh-CN": "工作区根目录"
  },
  "Workspace task": {
    "en": "Workspace task",
    "zh-CN": "工作区任务"
  },
  "Use valid tool parameters and a path relative to the selected Workspace.": {
    "en": "Use valid tool parameters and a path relative to the selected Workspace.",
    "zh-CN": "请使用有效的工具参数，以及相对于所选工作区的路径。"
  },
  "The change preview could not be prepared. Check the parameters and try again.": {
    "en": "The change preview could not be prepared. Check the parameters and try again.",
    "zh-CN": "无法准备更改预览，请检查参数后重试。"
  },
  "The tool could not run. Try again.": {
    "en": "The tool could not run. Try again.",
    "zh-CN": "无法执行工具，请重试。"
  },
  "This preview expired. Prepare the change again.": {
    "en": "This preview expired. Prepare the change again.",
    "zh-CN": "此预览已过期，请重新准备更改。"
  },
  "The change could not be completed. Prepare a new preview to try again.": {
    "en": "The change could not be completed. Prepare a new preview to try again.",
    "zh-CN": "无法完成更改，请准备新的预览后重试。"
  },
  "File tools": {
    "en": "File tools",
    "zh-CN": "文件工具"
  },
  "Read files, preview changes, or add a plan step.": {
    "en": "Read files, preview changes, or add a plan step.",
    "zh-CN": "读取文件、预览更改，或添加计划步骤。"
  },
  "Choose a Workspace to enable file tools.": {
    "en": "Choose a Workspace to enable file tools.",
    "zh-CN": "选择工作区后即可使用文件工具。"
  },
  "Work tool": {
    "en": "Work tool",
    "zh-CN": "工作工具"
  },
  "Relative path": {
    "en": "Relative path",
    "zh-CN": "相对路径"
  },
  "Filename contains": {
    "en": "Filename contains",
    "zh-CN": "文件名包含"
  },
  "Destination path": {
    "en": "Destination path",
    "zh-CN": "目标路径"
  },
  "Complete file content": {
    "en": "Complete file content",
    "zh-CN": "完整文件内容"
  },
  "Working…": {
    "en": "Working…",
    "zh-CN": "正在处理…"
  },
  "Prepare change": {
    "en": "Prepare change",
    "zh-CN": "预览更改"
  },
  "Run tool": {
    "en": "Run tool",
    "zh-CN": "执行工具"
  },
  "Use valid tool parameters before adding a plan step.": {
    "en": "Use valid tool parameters before adding a plan step.",
    "zh-CN": "添加计划步骤前，请填写有效的工具参数。"
  },
  "Add to plan": {
    "en": "Add to plan",
    "zh-CN": "添加到计划"
  },
  "Leave the path empty to use the Workspace root.": {
    "en": "Leave the path empty to use the Workspace root.",
    "zh-CN": "路径留空时使用工作区根目录。"
  },
  "Work tool result": {
    "en": "Work tool result",
    "zh-CN": "工具执行结果"
  },
  "Completed": {
    "en": "Completed",
    "zh-CN": "已完成"
  },
  "Available tools": {
    "en": "Available tools",
    "zh-CN": "可用工具"
  },
  "Reconnect to inspect Work tools.": {
    "en": "Reconnect to inspect Work tools.",
    "zh-CN": "重新连接后即可查看工作工具。"
  },
  "Could not load available tools.": {
    "en": "Could not load available tools.",
    "zh-CN": "无法加载可用工具。"
  },
  "Reload Tools": {
    "en": "Reload Tools",
    "zh-CN": "重新加载工具"
  },
  "Loading available tools…": {
    "en": "Loading available tools…",
    "zh-CN": "正在加载可用工具…"
  },
  "limit": {
    "en": "limit",
    "zh-CN": "执行时限"
  },
  "admission budget": {
    "en": "admission budget",
    "zh-CN": "写入准入时限"
  },
  "Could not load Work history. Refresh to try again.": {
    "en": "Could not load Work history. Refresh to try again.",
    "zh-CN": "无法加载操作历史，请刷新后重试。"
  },
  "Undo could not complete. Refresh history before trying again.": {
    "en": "Undo could not complete. Refresh history before trying again.",
    "zh-CN": "无法完成撤销，请刷新历史后重试。"
  },
  "Could not open the data folder. Open it from Settings to review recovery files.": {
    "en": "Could not open the data folder. Open it from Settings to review recovery files.",
    "zh-CN": "无法打开数据文件夹，请从设置中打开以查看恢复文件。"
  },
  "Work history": {
    "en": "Work history",
    "zh-CN": "操作历史"
  },
  "Activity": {
    "en": "Activity",
    "zh-CN": "活动"
  },
  "A record of your actions and approvals.": {
    "en": "A record of your actions and approvals.",
    "zh-CN": "记录你的操作与批准决定。"
  },
  "Refresh history": {
    "en": "Refresh history",
    "zh-CN": "刷新历史"
  },
  "No activity yet": {
    "en": "No activity yet",
    "zh-CN": "暂无活动"
  },
  "No recorded actions for this Chat.": {
    "en": "No recorded actions for this Chat.",
    "zh-CN": "此聊天暂无操作记录。"
  },
  "Showing the newest available records. Older history exceeds this view’s limit.": {
    "en": "Showing the newest available records. Older history exceeds this view’s limit.",
    "zh-CN": "正在显示最新可用记录，更早的历史超出此视图的显示范围。"
  },
  "Approved": {
    "en": "Approved",
    "zh-CN": "已批准"
  },
  "Rejected": {
    "en": "Rejected",
    "zh-CN": "已拒绝"
  },
  "Modification requested": {
    "en": "Modification requested",
    "zh-CN": "已请求修改"
  },
  "Approval expired": {
    "en": "Approval expired",
    "zh-CN": "批准已过期"
  },
  "Explicit read": {
    "en": "Explicit read",
    "zh-CN": "主动读取"
  },
  "Not recorded": {
    "en": "Not recorded",
    "zh-CN": "未记录"
  },
  "Parameter summary": {
    "en": "Parameter summary",
    "zh-CN": "参数摘要"
  },
  "None": {
    "en": "None",
    "zh-CN": "无"
  },
  "Interrupted operation. Review recovery files before changing the Workspace.": {
    "en": "Interrupted operation. Review recovery files before changing the Workspace.",
    "zh-CN": "操作已中断，更改工作区前请检查恢复文件。"
  },
  "Transaction journal folder:": {
    "en": "Transaction journal folder:",
    "zh-CN": "事务日志文件夹："
  },
  "Verified recovery copy:": {
    "en": "Verified recovery copy:",
    "zh-CN": "已验证的恢复副本："
  },
  "No verified recovery copy is available. The journal folder records the interruption.": {
    "en": "No verified recovery copy is available. The journal folder records the interruption.",
    "zh-CN": "没有可用的已验证恢复副本，中断情况已记录在日志文件夹中。"
  },
  "Open data folder": {
    "en": "Open data folder",
    "zh-CN": "打开数据文件夹"
  },
  "Confirm Work Undo": {
    "en": "Confirm Work Undo",
    "zh-CN": "确认撤销工作操作"
  },
  "Restore the recorded previous state only if the current file still matches this action. External changes will be kept.": {
    "en": "Restore the recorded previous state only if the current file still matches this action. External changes will be kept.",
    "zh-CN": "仅当当前文件仍与此操作结果一致时，才恢复记录中的原始状态。外部更改将被保留。"
  },
  "Confirm Undo": {
    "en": "Confirm Undo",
    "zh-CN": "确认撤销"
  },
  "Cancel Undo": {
    "en": "Cancel Undo",
    "zh-CN": "取消撤销"
  },
  "Undo completed and verified.": {
    "en": "Undo completed and verified.",
    "zh-CN": "撤销已完成并通过验证。"
  },
  "No change was applied.": {
    "en": "No change was applied.",
    "zh-CN": "未应用任何更改。"
  },
  "File contents and search terms are never shown in history.": {
    "en": "File contents and search terms are never shown in history.",
    "zh-CN": "历史记录不显示文件内容和搜索词。"
  },
  "Work job progress": {
    "en": "Work job progress",
    "zh-CN": "任务进度"
  },
  "Time limit reached.": {
    "en": "Time limit reached.",
    "zh-CN": "已达到时间限制。"
  },
  "Stop requested.": {
    "en": "Stop requested.",
    "zh-CN": "已请求停止。"
  },
  "Prepare read retry": {
    "en": "Prepare read retry",
    "zh-CN": "准备重新读取"
  },
  "Work change preview": {
    "en": "Work change preview",
    "zh-CN": "工作更改预览"
  },
  "Recoverable deletion preview": {
    "en": "Recoverable deletion preview",
    "zh-CN": "可恢复删除预览"
  },
  "Change preview": {
    "en": "Change preview",
    "zh-CN": "更改预览"
  },
  "Change and impact": {
    "en": "Change and impact",
    "zh-CN": "更改及其影响"
  },
  "Approval controls are unavailable in this version.": {
    "en": "Approval controls are unavailable in this version.",
    "zh-CN": "当前版本不支持批准操作。"
  },
  "Confirm recoverable deletion": {
    "en": "Confirm recoverable deletion",
    "zh-CN": "确认可恢复删除"
  },
  "Approve once": {
    "en": "Approve once",
    "zh-CN": "仅批准这一次"
  },
  "Deny": {
    "en": "Deny",
    "zh-CN": "拒绝"
  },
  "Edit parameters": {
    "en": "Edit parameters",
    "zh-CN": "编辑参数"
  },
  "Could not load desktop tools. Reload after the current action finishes.": {
    "en": "Could not load desktop tools. Reload after the current action finishes.",
    "zh-CN": "无法加载桌面工具，请在当前操作结束后重新加载。"
  },
  "Desktop action was refused or could not complete. Reload and prepare a fresh review before retrying.": {
    "en": "Desktop action was refused or could not complete. Reload and prepare a fresh review before retrying.",
    "zh-CN": "桌面操作被拒绝或未能完成，请重新加载并准备新的确认预览后重试。"
  },
  "Desktop history could not be read. Existing actions were not repeated.": {
    "en": "Desktop history could not be read. Existing actions were not repeated.",
    "zh-CN": "无法读取桌面操作历史，已有操作未被重复执行。"
  },
  "Desktop action completed.": {
    "en": "Desktop action completed.",
    "zh-CN": "桌面操作已完成。"
  },
  "Desktop action denied. No native change was made.": {
    "en": "Desktop action denied. No native change was made.",
    "zh-CN": "已拒绝桌面操作，未对本机作出更改。"
  },
  "The native action completed, but its final audit record could not be saved.": {
    "en": "The native action completed, but its final audit record could not be saved.",
    "zh-CN": "本机操作已完成，但无法保存最终审计记录。"
  },
  "The native app reported an error after starting. Check its state before retrying.": {
    "en": "The native app reported an error after starting. Check its state before retrying.",
    "zh-CN": "本机应用启动后报告错误，重试前请检查其状态。"
  },
  "Desktop action failed before completion.": {
    "en": "Desktop action failed before completion.",
    "zh-CN": "桌面操作未完成。"
  },
  "Desktop tools": {
    "en": "Desktop tools",
    "zh-CN": "桌面工具"
  },
  "Each approval applies to one action only.": {
    "en": "Each approval applies to one action only.",
    "zh-CN": "每次批准仅适用于一个操作。"
  },
  "Apps and windows": {
    "en": "Apps and windows",
    "zh-CN": "应用与窗口"
  },
  "Apps & windows": {
    "en": "Apps & windows",
    "zh-CN": "应用与窗口"
  },
  "Choose document to review": {
    "en": "Choose document to review",
    "zh-CN": "选择文档并预览"
  },
  "Review opening Notepad": {
    "en": "Review opening Notepad",
    "zh-CN": "预览打开记事本"
  },
  "List window names": {
    "en": "List window names",
    "zh-CN": "列出窗口名称"
  },
  "Clipboard actions": {
    "en": "Clipboard actions",
    "zh-CN": "剪贴板操作"
  },
  "Clipboard": {
    "en": "Clipboard",
    "zh-CN": "剪贴板"
  },
  "Read clipboard text": {
    "en": "Read clipboard text",
    "zh-CN": "读取剪贴板文字"
  },
  "Clipboard was empty on your last click.": {
    "en": "Clipboard was empty on your last click.",
    "zh-CN": "上次点击时剪贴板为空。"
  },
  "Clipboard text read on your last click": {
    "en": "Clipboard text read on your last click",
    "zh-CN": "上次点击读取的剪贴板文字"
  },
  "Text to put on clipboard": {
    "en": "Text to put on clipboard",
    "zh-CN": "写入剪贴板的文字"
  },
  "Review clipboard replacement": {
    "en": "Review clipboard replacement",
    "zh-CN": "预览替换剪贴板内容"
  },
  "Desktop action review": {
    "en": "Desktop action review",
    "zh-CN": "桌面操作预览"
  },
  "Exact desktop action": {
    "en": "Exact desktop action",
    "zh-CN": "完整桌面操作"
  },
  "Approve desktop action once": {
    "en": "Approve desktop action once",
    "zh-CN": "仅批准这一次桌面操作"
  },
  "Deny desktop action": {
    "en": "Deny desktop action",
    "zh-CN": "拒绝桌面操作"
  },
  "Edit and prepare a new review": {
    "en": "Edit and prepare a new review",
    "zh-CN": "编辑并重新预览"
  },
  "Select one window name": {
    "en": "Select one window name",
    "zh-CN": "选择一个窗口"
  },
  "Selection records metadata only. It does not capture the window.": {
    "en": "Selection records metadata only. It does not capture the window.",
    "zh-CN": "选择操作仅记录元数据，不会截取窗口画面。"
  },
  "Load desktop decision history": {
    "en": "Load desktop decision history",
    "zh-CN": "加载桌面操作批准历史"
  },
  "Reload desktop tools": {
    "en": "Reload desktop tools",
    "zh-CN": "重新加载桌面工具"
  },
  "Desktop decision history": {
    "en": "Desktop decision history",
    "zh-CN": "桌面操作批准历史"
  },
  "History records metadata; clipboard and document contents are omitted.": {
    "en": "History records metadata; clipboard and document contents are omitted.",
    "zh-CN": "历史仅记录元数据，不保存剪贴板和文档内容。"
  },
  "Older records are outside the retained history.": {
    "en": "Older records are outside the retained history.",
    "zh-CN": "更早的记录已超出保留范围。"
  },
  "No desktop decisions recorded.": {
    "en": "No desktop decisions recorded.",
    "zh-CN": "暂无桌面操作批准记录。"
  },
  "Work activity": {
    "en": "Work activity",
    "zh-CN": "工作活动"
  },
  "Work tool results": {
    "en": "Work tool results",
    "zh-CN": "工作工具结果"
  },
  "Recent file changes": {
    "en": "Recent file changes",
    "zh-CN": "最近文件变更"
  },
  "moved to": {
    "en": "moved to",
    "zh-CN": "移动到"
  },
  "Work permissions": {
    "en": "Work permissions",
    "zh-CN": "工作权限"
  },
  "Work permission levels": {
    "en": "Work permission levels",
    "zh-CN": "工作权限级别"
  },
  "pending": {
    "en": "Pending",
    "zh-CN": "待执行"
  },
  "running": {
    "en": "Running",
    "zh-CN": "执行中"
  },
  "paused": {
    "en": "Paused",
    "zh-CN": "已暂停"
  },
  "waiting": {
    "en": "Waiting",
    "zh-CN": "等待批准"
  },
  "succeeded": {
    "en": "Succeeded",
    "zh-CN": "已完成"
  },
  "failed": {
    "en": "Failed",
    "zh-CN": "失败"
  },
  "cancelled": {
    "en": "Cancelled",
    "zh-CN": "已取消"
  },
  "partial": {
    "en": "Partial",
    "zh-CN": "部分完成"
  },
  "denied": {
    "en": "Denied",
    "zh-CN": "已拒绝"
  },
  "undone": {
    "en": "Undone",
    "zh-CN": "已撤销"
  },
  "low": {
    "en": "Low",
    "zh-CN": "低"
  },
  "medium": {
    "en": "Medium",
    "zh-CN": "中"
  },
  "high": {
    "en": "High",
    "zh-CN": "高"
  },
  "critical": {
    "en": "Critical",
    "zh-CN": "极高"
  },
  "read": {
    "en": "Read",
    "zh-CN": "读取"
  },
  "preview": {
    "en": "Preview",
    "zh-CN": "预览"
  },
  "mutation": {
    "en": "Mutation",
    "zh-CN": "写入"
  },
  "idle": {
    "en": "Idle",
    "zh-CN": "空闲"
  },
  "admission": {
    "en": "Admission",
    "zh-CN": "准入检查"
  },
  "memory": {
    "en": "Memory",
    "zh-CN": "记忆"
  },
  "rag": {
    "en": "Rag",
    "zh-CN": "项目资料"
  },
  "brain": {
    "en": "Brain",
    "zh-CN": "模型处理"
  },
  "approval": {
    "en": "Approval",
    "zh-CN": "批准"
  },
  "tool": {
    "en": "Tool",
    "zh-CN": "工具执行"
  },
  "verification": {
    "en": "Verification",
    "zh-CN": "结果验证"
  },
  "end": {
    "en": "End",
    "zh-CN": "结束"
  },
  "unavailable": {
    "en": "Unavailable",
    "zh-CN": "不可用"
  },
  "prepared": {
    "en": "Prepared",
    "zh-CN": "已准备"
  },
  "admitted": {
    "en": "Admitted",
    "zh-CN": "已准入"
  },
  "file.open": {
    "en": "Open file",
    "zh-CN": "打开文件"
  },
  "app.open": {
    "en": "Open application",
    "zh-CN": "打开应用"
  },
  "clipboard.write": {
    "en": "Write clipboard",
    "zh-CN": "写入剪贴板"
  },
  "list_directory": {
    "en": "List directory",
    "zh-CN": "列出文件夹"
  },
  "read_text": {
    "en": "Read text",
    "zh-CN": "读取文本"
  },
  "search_files": {
    "en": "Search files",
    "zh-CN": "搜索文件"
  },
  "metadata": {
    "en": "File information",
    "zh-CN": "文件信息"
  },
  "create": {
    "en": "Create file",
    "zh-CN": "创建文件"
  },
  "edit": {
    "en": "Edit file",
    "zh-CN": "编辑文件"
  },
  "move": {
    "en": "Move file",
    "zh-CN": "移动文件"
  },
  "delete": {
    "en": "Delete file",
    "zh-CN": "删除文件"
  },
  "Modify {tool}. Completed steps stay completed. A new plan must be started and reviewed again.": {
    "en": "Modify {tool}. Completed steps stay completed. A new plan must be started and reviewed again.",
    "zh-CN": "修改{tool}。已完成步骤保持完成，新的计划需要重新开始并审核。"
  },
  "Image context: {name}": {
    "en": "Image context: {name}",
    "zh-CN": "图像上下文：{name}"
  },
  "{width} × {height}. This review adds advice only. The exact steps below still require their own approvals. Image context is not restored after restart.": {
    "en": "{width} × {height}. This review adds advice only. The exact steps below still require their own approvals. Image context is not restored after restart.",
    "zh-CN": "{width} × {height}。此预览仅提供参考，以下具体步骤仍需分别批准。重启后不会恢复图像上下文。"
  },
  "Remove plan step {number}": {
    "en": "Remove plan step {number}",
    "zh-CN": "移除计划步骤 {number}"
  },
  "Workflow: {state}": {
    "en": "Workflow: {state}",
    "zh-CN": "工作流程：{state}"
  },
  "Memory: {memory} · Project Sources: {sources}": {
    "en": "Memory: {memory} · Project Sources: {sources}",
    "zh-CN": "记忆：{memory} · 项目资料：{sources}"
  },
  "{risk} risk · {seconds}s {limit}": {
    "en": "{risk} risk · {seconds}s {limit}",
    "zh-CN": "{risk}风险 · {limit} {seconds} 秒"
  },
  "Decision: {decision}": {
    "en": "Decision: {decision}",
    "zh-CN": "决定：{decision}"
  },
  "Chat: {chat} · Project: {project}": {
    "en": "Chat: {chat} · Project: {project}",
    "zh-CN": "聊天：{chat} · 项目：{project}"
  },
  "Undo {tool}": {
    "en": "Undo {tool}",
    "zh-CN": "撤销{tool}"
  },
  "Undo {status}: {message}": {
    "en": "Undo {status}: {message}",
    "zh-CN": "撤销{status}：{message}"
  },
  "Completed steps: {completed} / {total}": {
    "en": "Completed steps: {completed} / {total}",
    "zh-CN": "已完成步骤：{completed} / {total}"
  },
  "Job time remaining: {remaining} seconds of {total}. Pauses and approval waits count toward this limit.": {
    "en": "Job time remaining: {remaining} seconds of {total}. Pauses and approval waits count toward this limit.",
    "zh-CN": "任务剩余时间：{remaining} 秒，总时限 {total} 秒。暂停和等待批准也计入时限。"
  },
  "Step {number}: {phase} · attempt {attempt}": {
    "en": "Step {number}: {phase} · attempt {attempt}",
    "zh-CN": "步骤 {number}：{phase} · 第 {attempt} 次尝试"
  },
  " · {seconds} seconds remaining": {
    "en": " · {seconds} seconds remaining",
    "zh-CN": " · 剩余 {seconds} 秒"
  },
  "Reads and previews: {readSeconds} seconds. Write admission: {writeSeconds} seconds. An admitted native write reports its actual result when it finishes.": {
    "en": "Reads and previews: {readSeconds} seconds. Write admission: {writeSeconds} seconds. An admitted native write reports its actual result when it finishes.",
    "zh-CN": "读取及预览时限为 {readSeconds} 秒，写入准入时限为 {writeSeconds} 秒。已获准的本机写入会在结束后报告实际结果。"
  },
  "This read failed temporarily. A retry creates a new pending plan with this read and the remaining steps. Completed steps stay completed; the original time budget and {retries}-retry limit still apply.": {
    "en": "This read failed temporarily. A retry creates a new pending plan with this read and the remaining steps. Completed steps stay completed; the original time budget and {retries}-retry limit still apply.",
    "zh-CN": "此次读取暂时失败。重试会使用此读取及剩余步骤创建新的待执行计划。已完成步骤保持完成，仍适用原有时间预算和 {retries} 次重试上限。"
  },
  "Risk: {risk} · Expires: {expires}": {
    "en": "Risk: {risk} · Expires: {expires}",
    "zh-CN": "风险：{risk} · 到期时间：{expires}"
  },
  "{action} · {risk} risk": {
    "en": "{action} · {risk} risk",
    "zh-CN": "{action} · {risk}风险"
  },
  "Expires {expires}. Approve once or deny.": {
    "en": "Expires {expires}. Approve once or deny.",
    "zh-CN": "到期时间：{expires}。请选择仅批准这一次或拒绝。"
  },
  "Selected window name: {name}. No capture permission was granted.": {
    "en": "Selected window name: {name}. No capture permission was granted.",
    "zh-CN": "已选窗口：{name}。未授予截图权限。"
  },
  "正在处理": {
    "en": "Working",
    "zh-CN": "正在处理"
  },
  "已完成": {
    "en": "Completed",
    "zh-CN": "已完成"
  },
  "已停止": {
    "en": "Stopped",
    "zh-CN": "已停止"
  },
  "未完成": {
    "en": "Incomplete",
    "zh-CN": "未完成"
  },
  "最近文件变更": {
    "en": "Recent file changes",
    "zh-CN": "最近文件变更"
  },
  "已撤销": {
    "en": "Undone",
    "zh-CN": "已撤销"
  },
  "Created": {
    "en": "Created",
    "zh-CN": "新建"
  },
  "Modified": {
    "en": "Modified",
    "zh-CN": "修改"
  },
  "Moved": {
    "en": "Moved",
    "zh-CN": "移动"
  },
  "Deleted": {
    "en": "Deleted",
    "zh-CN": "删除"
  },
  "工具权限": {
    "en": "Tool permissions",
    "zh-CN": "工具权限"
  },
  "仅对话": {
    "en": "Chat only",
    "zh-CN": "仅对话"
  },
  "不使用工具": {
    "en": "No tools",
    "zh-CN": "不使用工具"
  },
  "只读": {
    "en": "Read only",
    "zh-CN": "只读"
  },
  "查找文件、查看应用和音量": {
    "en": "Find files, inspect apps and volume",
    "zh-CN": "查找文件、查看应用和音量"
  },
  "操作前询问": {
    "en": "Ask before acting",
    "zh-CN": "操作前询问"
  },
  "每条 CMD 命令和桌面操作前由你确认": {
    "en": "Confirm each CMD command and desktop action",
    "zh-CN": "每条 CMD 命令和桌面操作前由你确认"
  },
  "允许 CMD 命令": {
    "en": "Allow CMD commands",
    "zh-CN": "允许 CMD 命令"
  },
  "按当前 Windows 用户权限执行命令，可读写文件、运行程序和联网": {
    "en": "Run as your Windows user: read or change files, run programs, and access the network",
    "zh-CN": "按当前 Windows 用户权限执行命令，可读写文件、运行程序和联网"
  },
  "CMD 命令结果": { "en": "CMD command results", "zh-CN": "CMD 命令结果" },
  "CMD 命令 {number} · {status}": { "en": "CMD command {number} · {status}", "zh-CN": "CMD 命令 {number} · {status}" },
  "工作目录：{cwd}": { "en": "Working directory: {cwd}", "zh-CN": "工作目录：{cwd}" },
  "进程已退出": { "en": "Process exited", "zh-CN": "进程已退出" },
  "命令执行超时": { "en": "Command timed out", "zh-CN": "命令执行超时" },
  "命令执行已取消": { "en": "Command cancelled", "zh-CN": "命令执行已取消" },
  "命令未能启动": { "en": "Command could not start", "zh-CN": "命令未能启动" },
  "未返回退出码": { "en": "No exit code returned", "zh-CN": "未返回退出码" },
  "退出码：{code}": { "en": "Exit code: {code}", "zh-CN": "退出码：{code}" },
  "标准输出": { "en": "Standard output", "zh-CN": "标准输出" },
  "标准错误": { "en": "Standard error", "zh-CN": "标准错误" },
  "没有命令输出": { "en": "No command output", "zh-CN": "没有命令输出" },
  "命令输出已截断，最多显示 16 KiB。": { "en": "Command output was truncated to 16 KiB.", "zh-CN": "命令输出已截断，最多显示 16 KiB。" },
  "桌面操作前由你确认": {
    "en": "Confirm before desktop actions",
    "zh-CN": "桌面操作前由你确认"
  },
  "允许桌面操作": {
    "en": "Allow desktop actions",
    "zh-CN": "允许桌面操作"
  },
  "直接执行已支持的桌面工具": {
    "en": "Run supported desktop tools directly",
    "zh-CN": "直接执行已支持的桌面工具"
  },
  "列出工作区目录的有界项目和元数据。": {
    "en": "List bounded entries and metadata within a Workspace directory.",
    "zh-CN": "列出工作区目录的有界项目和元数据。"
  },
  "读取有界 UTF-8 文本，不修改文件。": {
    "en": "Read bounded UTF-8 text without changing files.",
    "zh-CN": "读取有界 UTF-8 文本，不修改文件。"
  },
  "按字面名称搜索有界工作区路径，不读取文件正文。": {
    "en": "Search bounded Workspace paths by literal name without reading file contents.",
    "zh-CN": "按字面名称搜索有界工作区路径，不读取文件正文。"
  },
  "查看文件或目录的安全元数据，不读取正文。": {
    "en": "Inspect safe file or directory metadata without reading its contents.",
    "zh-CN": "查看文件或目录的安全元数据，不读取正文。"
  },
  "预览并批准后创建一个 UTF-8 文本文件。": {
    "en": "Create one UTF-8 text file after preview and approval.",
    "zh-CN": "预览并批准后创建一个 UTF-8 文本文件。"
  },
  "预览并批准后替换文本，保留可恢复的旧版本。": {
    "en": "Replace text after preview and approval, keeping a recoverable previous version.",
    "zh-CN": "预览并批准后替换文本，保留可恢复的旧版本。"
  },
  "预览并批准后移动一个文件，不覆盖已有目标。": {
    "en": "Move one file after preview and approval without overwriting an existing destination.",
    "zh-CN": "预览并批准后移动一个文件，不覆盖已有目标。"
  },
  "明确确认后将一个文件移入可恢复区域。": {
    "en": "Move one file to a recovery area after explicit confirmation.",
    "zh-CN": "明确确认后将一个文件移入可恢复区域。"
  },
  "The tool request is invalid.": {
    "en": "The tool request is invalid.",
    "zh-CN": "工具请求无效。"
  },
  "The requested tool is unavailable.": {
    "en": "The requested tool is unavailable.",
    "zh-CN": "请求的工具不可用。"
  },
  "Workspace tools require explicit Work mode.": {
    "en": "Workspace tools require explicit Work mode.",
    "zh-CN": "使用工作区工具需要明确启用工作模式。"
  },
  "Choose a Workspace before using tools.": {
    "en": "Choose a Workspace before using tools.",
    "zh-CN": "请先选择工作区，再使用工具。"
  },
  "The requested path is outside the permitted scope or unsafe.": {
    "en": "The requested path is outside the permitted scope or unsafe.",
    "zh-CN": "请求的路径超出允许范围或不安全。"
  },
  "The requested file is restricted.": {
    "en": "The requested file is restricted.",
    "zh-CN": "请求的文件受限。"
  },
  "The file exceeds the permitted size.": {
    "en": "The file exceeds the permitted size.",
    "zh-CN": "文件超出允许大小。"
  },
  "Only UTF-8 text files are supported.": {
    "en": "Only UTF-8 text files are supported.",
    "zh-CN": "仅支持 UTF-8 文本文件。"
  },
  "The requested file does not exist.": {
    "en": "The requested file does not exist.",
    "zh-CN": "请求的文件不存在。"
  },
  "The destination already exists.": {
    "en": "The destination already exists.",
    "zh-CN": "目标已存在。"
  },
  "The action requires explicit approval.": {
    "en": "The action requires explicit approval.",
    "zh-CN": "此操作需要明确批准。"
  },
  "The action was declined.": {
    "en": "The action was declined.",
    "zh-CN": "操作已拒绝。"
  },
  "The action was cancelled.": {
    "en": "The action was cancelled.",
    "zh-CN": "操作已取消。"
  },
  "The tool exceeded its time limit.": {
    "en": "The tool exceeded its time limit.",
    "zh-CN": "工具运行超时。"
  },
  "The local file operation failed.": {
    "en": "The local file operation failed.",
    "zh-CN": "本地文件操作失败。"
  },
  "The file changed; prepare the action again.": {
    "en": "The file changed; prepare the action again.",
    "zh-CN": "文件已更改，请重新准备操作。"
  },
  "The action can no longer be undone safely.": {
    "en": "The action can no longer be undone safely.",
    "zh-CN": "此操作已无法安全撤销。"
  },
  "The written result could not be verified.": {
    "en": "The written result could not be verified.",
    "zh-CN": "无法验证写入结果。"
  },
  "Open a verified review copy. Edits in its app do not change the Workspace original.": {
    "en": "Open a verified review copy. Edits in its app do not change the Workspace original.",
    "zh-CN": "打开已验证的审阅副本。在应用中编辑此副本不会更改工作区中的原文件。"
  },
  "Open Windows Notepad without a file or command arguments.": {
    "en": "Open Windows Notepad without a file or command arguments.",
    "zh-CN": "打开 Windows 记事本，不传入文件或命令参数。"
  },
  "Replace the system clipboard text with exactly the text shown. Previous clipboard text is not saved.": {
    "en": "Replace the system clipboard text with exactly the text shown. Previous clipboard text is not saved.",
    "zh-CN": "将系统剪贴板文本替换为此处显示的完整文本。不会保存原剪贴板文本。"
  },
  "invalid_request": {
    "en": "Invalid request",
    "zh-CN": "请求无效"
  },
  "unsupported_tool": {
    "en": "Tool unavailable",
    "zh-CN": "工具不可用"
  },
  "work_mode_required": {
    "en": "Work mode required",
    "zh-CN": "需要工作模式"
  },
  "workspace_required": {
    "en": "Workspace required",
    "zh-CN": "需要工作区"
  },
  "path_denied": {
    "en": "Path denied",
    "zh-CN": "路径被拒绝"
  },
  "sensitive_file": {
    "en": "Restricted file",
    "zh-CN": "受限文件"
  },
  "file_too_large": {
    "en": "File too large",
    "zh-CN": "文件过大"
  },
  "binary_file": {
    "en": "Unsupported binary file",
    "zh-CN": "不支持二进制文件"
  },
  "not_found": {
    "en": "Not found",
    "zh-CN": "未找到"
  },
  "already_exists": {
    "en": "Already exists",
    "zh-CN": "已存在"
  },
  "permission_required": {
    "en": "Approval required",
    "zh-CN": "需要批准"
  },
  "permission_denied": {
    "en": "Permission denied",
    "zh-CN": "权限被拒绝"
  },
  "timeout": {
    "en": "Timed out",
    "zh-CN": "已超时"
  },
  "io_error": {
    "en": "File operation failed",
    "zh-CN": "文件操作失败"
  },
  "conflict": {
    "en": "File changed",
    "zh-CN": "文件已更改"
  },
  "undo_unavailable": {
    "en": "Undo unavailable",
    "zh-CN": "无法撤销"
  },
  "verification_failed": {
    "en": "Verification failed",
    "zh-CN": "验证失败"
  },
  "busy": {
    "en": "Busy",
    "zh-CN": "忙碌"
  },
  "context_changed": {
    "en": "Context changed",
    "zh-CN": "上下文已更改"
  },
  "file_changed": {
    "en": "File changed",
    "zh-CN": "文件已更改"
  },
  "invalid_text": {
    "en": "Invalid text",
    "zh-CN": "文本无效"
  },
  "expired": {
    "en": "Expired",
    "zh-CN": "已过期"
  },
  "approval_required": {
    "en": "Approval required",
    "zh-CN": "需要批准"
  },
  "native_failed": {
    "en": "Native action failed",
    "zh-CN": "原生操作失败"
  },
  "audit_failed": {
    "en": "Audit could not be saved",
    "zh-CN": "无法保存审计记录"
  }
}
