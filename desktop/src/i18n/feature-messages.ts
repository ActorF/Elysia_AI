/** Reviewed interface translations for Projects, knowledge, image generation, Vision and Song Cover. */

import type { MessageCatalog } from './types.ts'
import { internetFeatureMessages } from './internet-feature-messages.ts'

export const featureMessages: MessageCatalog = {
  ...internetFeatureMessages,
  "本地 SDXL": { "en": "Local SDXL", "zh-CN": "本地 SDXL" },
  "Current": {
    "en": "Current",
    "zh-CN": "当前"
  },
  "Archived": {
    "en": "Archived",
    "zh-CN": "已归档"
  },
  "Work": {
    "en": "Work",
    "zh-CN": "工作"
  },
  "Chat": {
    "en": "Chat",
    "zh-CN": "聊天"
  },
  " messages": {
    "en": " messages",
    "zh-CN": " 条消息"
  },
  "Open Chat {title}": {
    "en": "Open Chat {title}",
    "zh-CN": "打开聊天「{title}」"
  },
  "Archived chats are read-only": {
    "en": "Archived chats are read-only",
    "zh-CN": "已归档的聊天为只读状态"
  },
  "Open": {
    "en": "Open",
    "zh-CN": "打开"
  },
  "Project Settings": {
    "en": "Project Settings",
    "zh-CN": "项目设置"
  },
  "Name and instructions are shared by every Chat in this Project.": {
    "en": "Name and instructions are shared by every Chat in this Project.",
    "zh-CN": "项目内的所有聊天共用名称和指令。"
  },
  "Project name": {
    "en": "Project name",
    "zh-CN": "项目名称"
  },
  " characters": {
    "en": " characters",
    "zh-CN": " 个字符"
  },
  "Custom instructions": {
    "en": "Custom instructions",
    "zh-CN": "自定义指令"
  },
  "How should Elysia work inside this Project?": {
    "en": "How should Elysia work inside this Project?",
    "zh-CN": "你希望 Elysia 如何在此项目中工作？"
  },
  "Applied on future turns. Leave blank to clear.": {
    "en": "Applied on future turns. Leave blank to clear.",
    "zh-CN": "应用于后续对话。留空即可清除。"
  },
  "Save Settings": {
    "en": "Save Settings",
    "zh-CN": "保存设置"
  },
  "Workspace": {
    "en": "Workspace",
    "zh-CN": "工作区"
  },
  "Bind one directory for future Project-aware Work tools.": {
    "en": "Bind one directory for future Project-aware Work tools.",
    "zh-CN": "绑定一个目录，供此项目的工作工具使用。"
  },
  "No workspace is bound.": {
    "en": "No workspace is bound.",
    "zh-CN": "尚未绑定工作区。"
  },
  "Bind Workspace": {
    "en": "Bind Workspace",
    "zh-CN": "绑定工作区"
  },
  "Replace Workspace": {
    "en": "Replace Workspace",
    "zh-CN": "更换工作区"
  },
  "Unbind Workspace": {
    "en": "Unbind Workspace",
    "zh-CN": "解绑工作区"
  },
  "Enter a name for this Project.": {
    "en": "Enter a name for this Project.",
    "zh-CN": "请输入项目名称。"
  },
  "Project names can use at most {MAX_PROJECT_NAME_LENGTH} characters.": {
    "en": "Project names can use at most {MAX_PROJECT_NAME_LENGTH} characters.",
    "zh-CN": "项目名称最多可使用 {MAX_PROJECT_NAME_LENGTH} 个字符。"
  },
  "Project instructions are too long to save.": {
    "en": "Project instructions are too long to save.",
    "zh-CN": "项目指令过长，无法保存。"
  },
  "Created Project {name}.": {
    "en": "Created Project {name}.",
    "zh-CN": "已创建项目「{name}」。"
  },
  "Could not create the Project.": {
    "en": "Could not create the Project.",
    "zh-CN": "无法创建项目。"
  },
  "Archived Project {name}.": {
    "en": "Archived Project {name}.",
    "zh-CN": "已归档项目「{name}」。"
  },
  "Unbound the workspace from {name}.": {
    "en": "Unbound the workspace from {name}.",
    "zh-CN": "已解绑「{name}」的工作区。"
  },
  "Could not archive the Project.": {
    "en": "Could not archive the Project.",
    "zh-CN": "无法归档项目。"
  },
  "Could not unbind the workspace.": {
    "en": "Could not unbind the workspace.",
    "zh-CN": "无法解绑工作区。"
  },
  "Opening Project…": {
    "en": "Opening Project…",
    "zh-CN": "正在打开项目…"
  },
  "Project opened.": {
    "en": "Project opened.",
    "zh-CN": "项目已打开。"
  },
  "Could not open the Project.": {
    "en": "Could not open the Project.",
    "zh-CN": "无法打开项目。"
  },
  "Saving Project settings…": {
    "en": "Saving Project settings…",
    "zh-CN": "正在保存项目设置…"
  },
  "Project settings saved.": {
    "en": "Project settings saved.",
    "zh-CN": "项目设置已保存。"
  },
  "Could not save the Project settings.": {
    "en": "Could not save the Project settings.",
    "zh-CN": "无法保存项目设置。"
  },
  "Choosing a workspace…": {
    "en": "Choosing a workspace…",
    "zh-CN": "正在选择工作区…"
  },
  "Workspace selection canceled. Nothing changed.": {
    "en": "Workspace selection canceled. Nothing changed.",
    "zh-CN": "已取消选择工作区，未做更改。"
  },
  "Workspace bound to the Project.": {
    "en": "Workspace bound to the Project.",
    "zh-CN": "工作区已绑定到项目。"
  },
  "Project workspace replaced.": {
    "en": "Project workspace replaced.",
    "zh-CN": "已更换项目工作区。"
  },
  "Could not bind the workspace.": {
    "en": "Could not bind the workspace.",
    "zh-CN": "无法绑定工作区。"
  },
  "Restoring Project…": {
    "en": "Restoring Project…",
    "zh-CN": "正在恢复项目…"
  },
  "Restored Project {name}.": {
    "en": "Restored Project {name}.",
    "zh-CN": "已恢复项目「{name}」。"
  },
  "Could not restore the Project.": {
    "en": "Could not restore the Project.",
    "zh-CN": "无法恢复项目。"
  },
  "Removing Chat from Project…": {
    "en": "Removing Chat from Project…",
    "zh-CN": "正在将聊天移出项目…"
  },
  "Moving Chat into Project…": {
    "en": "Moving Chat into Project…",
    "zh-CN": "正在将聊天移入项目…"
  },
  "Chat removed from the Project.": {
    "en": "Chat removed from the Project.",
    "zh-CN": "聊天已移出项目。"
  },
  "Chat moved into the Project.": {
    "en": "Chat moved into the Project.",
    "zh-CN": "聊天已移入项目。"
  },
  "Could not update the Chat Project.": {
    "en": "Could not update the Chat Project.",
    "zh-CN": "无法更改聊天所属项目。"
  },
  "Opening Chat…": {
    "en": "Opening Chat…",
    "zh-CN": "正在打开聊天…"
  },
  "Chat opened.": {
    "en": "Chat opened.",
    "zh-CN": "聊天已打开。"
  },
  "Could not open the Chat.": {
    "en": "Could not open the Chat.",
    "zh-CN": "无法打开聊天。"
  },
  "Create Project": {
    "en": "Create Project",
    "zh-CN": "创建项目"
  },
  "Give the Project a stable name and optional shared instructions.": {
    "en": "Give the Project a stable name and optional shared instructions.",
    "zh-CN": "为项目设置名称，也可添加共用指令。"
  },
  "Archive “{name}”?": {
    "en": "Archive “{name}”?",
    "zh-CN": "归档「{name}」？"
  },
  "The Project and its Chats stay stored, but the Project becomes read-only until it is restored.": {
    "en": "The Project and its Chats stay stored, but the Project becomes read-only until it is restored.",
    "zh-CN": "项目及其聊天将保留，项目在恢复前为只读状态。"
  },
  "Archive Project": {
    "en": "Archive Project",
    "zh-CN": "归档项目"
  },
  "Unbind workspace from “{name}”?": {
    "en": "Unbind workspace from “{name}”?",
    "zh-CN": "解绑「{name}」的工作区？"
  },
  "This removes only the Project association. Files in the directory are not changed or deleted.": {
    "en": "This removes only the Project association. Files in the directory are not changed or deleted.",
    "zh-CN": "此操作仅移除项目关联，不会更改或删除目录内的文件。"
  },
  "Hide navigation": {
    "en": "Hide navigation",
    "zh-CN": "隐藏导航"
  },
  "Show navigation": {
    "en": "Show navigation",
    "zh-CN": "显示导航"
  },
  "Toggle navigation (Ctrl+B)": {
    "en": "Toggle navigation (Ctrl+B)",
    "zh-CN": "切换导航（Ctrl+B）"
  },
  "Projects": {
    "en": "Projects",
    "zh-CN": "项目"
  },
  "Shared context, Chats, and workspaces": {
    "en": "Shared context, Chats, and workspaces",
    "zh-CN": "共用上下文、聊天和工作区"
  },
  "New Project": {
    "en": "New Project",
    "zh-CN": "新建项目"
  },
  "Project action failed": {
    "en": "Project action failed",
    "zh-CN": "项目操作失败"
  },
  "Dismiss Project error": {
    "en": "Dismiss Project error",
    "zh-CN": "关闭项目错误提示"
  },
  "Loading Projects": {
    "en": "Loading Projects",
    "zh-CN": "正在加载项目"
  },
  "Reading canonical Project and Chat relationships from the local Backend.": {
    "en": "Reading canonical Project and Chat relationships from the local Backend.",
    "zh-CN": "正在从本地服务读取项目与聊天的关联。"
  },
  "No Projects yet": {
    "en": "No Projects yet",
    "zh-CN": "还没有项目"
  },
  "Create a Project to share instructions and a workspace across multiple Chats.": {
    "en": "Create a Project to share instructions and a workspace across multiple Chats.",
    "zh-CN": "创建项目，让多个聊天共用指令和工作区。"
  },
  "All Projects": {
    "en": "All Projects",
    "zh-CN": "所有项目"
  },
  "Open project {name}": {
    "en": "Open project {name}",
    "zh-CN": "打开项目「{name}」"
  },
  "Chats": {
    "en": "Chats",
    "zh-CN": "聊天"
  },
  "No workspace": {
    "en": "No workspace",
    "zh-CN": "无工作区"
  },
  "Workspace bound": {
    "en": "Workspace bound",
    "zh-CN": "已绑定工作区"
  },
  "Choose a Project": {
    "en": "Choose a Project",
    "zh-CN": "选择项目"
  },
  "Select a Project from the list to see its Chats and shared context.": {
    "en": "Select a Project from the list to see its Chats and shared context.",
    "zh-CN": "从列表中选择项目，查看聊天和共用上下文。"
  },
  "Archived Project": {
    "en": "Archived Project",
    "zh-CN": "已归档项目"
  },
  "Active Project": {
    "en": "Active Project",
    "zh-CN": "当前项目"
  },
  " shared ": {
    "en": " shared ",
    "zh-CN": " 个共用"
  },
  "Updated ": {
    "en": "Updated ",
    "zh-CN": "更新于 "
  },
  "Restore Project": {
    "en": "Restore Project",
    "zh-CN": "恢复项目"
  },
  "Read-only Project": {
    "en": "Read-only Project",
    "zh-CN": "只读项目"
  },
  "Restore this Project before editing settings, changing its workspace, or moving Chats.": {
    "en": "Restore this Project before editing settings, changing its workspace, or moving Chats.",
    "zh-CN": "恢复项目后，即可编辑设置、更换工作区或移动聊天。"
  },
  "Project sections": {
    "en": "Project sections",
    "zh-CN": "项目分区"
  },
  "Sources": {
    "en": "Sources",
    "zh-CN": "来源"
  },
  "Memory": {
    "en": "Memory",
    "zh-CN": "记忆"
  },
  "Settings": {
    "en": "Settings",
    "zh-CN": "设置"
  },
  "Project Chats": {
    "en": "Project Chats",
    "zh-CN": "项目聊天"
  },
  "Chats that share this Project's context.": {
    "en": "Chats that share this Project's context.",
    "zh-CN": "共用此项目上下文的聊天。"
  },
  "No Project Chats": {
    "en": "No Project Chats",
    "zh-CN": "没有项目聊天"
  },
  "Move an unassigned Chat into this Project to start sharing context.": {
    "en": "Move an unassigned Chat into this Project to start sharing context.",
    "zh-CN": "将未归属项目的聊天移入此项目，即可共用上下文。"
  },
  "Remove": {
    "en": "Remove",
    "zh-CN": "移除"
  },
  "Unassigned Chats": {
    "en": "Unassigned Chats",
    "zh-CN": "未归属项目的聊天"
  },
  "Move Chat": {
    "en": "Move Chat",
    "zh-CN": "移动对话"
  },
  "Move a Chat here without changing its messages.": {
    "en": "Move a Chat here without changing its messages.",
    "zh-CN": "将聊天移入此处，消息内容不会更改。"
  },
  "No Unassigned Chats": {
    "en": "No Unassigned Chats",
    "zh-CN": "没有未归属项目的聊天"
  },
  "Every active Chat already belongs to a Project.": {
    "en": "Every active Chat already belongs to a Project.",
    "zh-CN": "每个未归档的聊天均已归属项目。"
  },
  "Move here": {
    "en": "Move here",
    "zh-CN": "移入此处"
  },
  "Project Memory isn't connected yet": {
    "en": "Project Memory isn't connected yet",
    "zh-CN": "项目记忆尚未连接"
  },
  "This entry will show only memory scoped to this Project. No global or other-Project memory is exposed here.": {
    "en": "This entry will show only memory scoped to this Project. No global or other-Project memory is exposed here.",
    "zh-CN": "此处只显示当前项目的记忆，不会显示全局记忆或其他项目的记忆。"
  },
  "Custom instructions ": {
    "en": "Custom instructions ",
    "zh-CN": "自定义指令 "
  },
  "Optional": {
    "en": "Optional",
    "zh-CN": "可选"
  },
  "Shared guidance for Chats in this Project": {
    "en": "Shared guidance for Chats in this Project",
    "zh-CN": "项目内聊天共用的指令"
  },
  "{sizeBytes} B": {
    "en": "{sizeBytes} B",
    "zh-CN": "{sizeBytes} B"
  },
  "{value1} KB": {
    "en": "{value1} KB",
    "zh-CN": "{value1} KB"
  },
  "{value1} MB": {
    "en": "{value1} MB",
    "zh-CN": "{value1} MB"
  },
  "Ready": {
    "en": "Ready",
    "zh-CN": "就绪"
  },
  "Not indexed": {
    "en": "Not indexed",
    "zh-CN": "未索引"
  },
  "Needs reindex": {
    "en": "Needs reindex",
    "zh-CN": "需要重新索引"
  },
  "Revoked": {
    "en": "Revoked",
    "zh-CN": "已撤销"
  },
  "Processing": {
    "en": "Processing",
    "zh-CN": "处理中"
  },
  "Adding source": {
    "en": "Adding source",
    "zh-CN": "正在添加资料"
  },
  "Replacing source": {
    "en": "Replacing source",
    "zh-CN": "正在替换资料"
  },
  "Reindexing source": {
    "en": "Reindexing source",
    "zh-CN": "正在重新索引资料"
  },
  "Rebuilding Project knowledge": {
    "en": "Rebuilding Project knowledge",
    "zh-CN": "正在重建项目知识库"
  },
  "Revoking Project knowledge": {
    "en": "Revoking Project knowledge",
    "zh-CN": "正在撤销项目知识库"
  },
  "Deleting source": {
    "en": "Deleting source",
    "zh-CN": "正在删除资料"
  },
  "Running": {
    "en": "Running",
    "zh-CN": "运行中"
  },
  "Stopping safely": {
    "en": "Stopping safely",
    "zh-CN": "正在安全停止"
  },
  "Recovery required": {
    "en": "Recovery required",
    "zh-CN": "需要恢复"
  },
  "Completed": {
    "en": "Completed",
    "zh-CN": "已完成"
  },
  "Cancelled": {
    "en": "Cancelled",
    "zh-CN": "已取消"
  },
  "Failed": {
    "en": "Failed",
    "zh-CN": "失败"
  },
  "The local index configuration changed. Rebuild the Project corpus before using it.": {
    "en": "The local index configuration changed. Rebuild the Project corpus before using it.",
    "zh-CN": "本地索引配置已更改，请重建项目资料库后再使用。"
  },
  "The app restarted before the selected source could be recorded. Add the file again.": {
    "en": "The app restarted before the selected source could be recorded. Add the file again.",
    "zh-CN": "所选资料尚未记录时应用已重启，请重新添加文件。"
  },
  "Temporary indexed data still needs safe cleanup. Run recovery before another change.": {
    "en": "Temporary indexed data still needs safe cleanup. Run recovery before another change.",
    "zh-CN": "临时索引数据仍需安全清理，请先恢复，再进行其他更改。"
  },
  "Automatic recovery reached its safe retry limit. Review the local Backend logs before retrying.": {
    "en": "Automatic recovery reached its safe retry limit. Review the local Backend logs before retrying.",
    "zh-CN": "自动恢复已达到安全重试上限，请检查本地服务日志后重试。"
  },
  "The source could not be added without weakening Project authorization.": {
    "en": "The source could not be added without weakening Project authorization.",
    "zh-CN": "无法在维持项目授权边界的前提下添加此资料。"
  },
  "The replacement did not reach a safe published state. Run recovery.": {
    "en": "The replacement did not reach a safe published state. Run recovery.",
    "zh-CN": "替换未能安全发布，请运行恢复。"
  },
  "The source could not be reindexed. Its previous authorization was not silently replaced.": {
    "en": "The source could not be reindexed. Its previous authorization was not silently replaced.",
    "zh-CN": "无法重新索引资料，原有授权未被替换。"
  },
  "The complete Project corpus could not be rebuilt. Run recovery before answering from it.": {
    "en": "The complete Project corpus could not be rebuilt. Run recovery before answering from it.",
    "zh-CN": "无法重建完整的项目资料库，请先恢复，再用于回答。"
  },
  "The Project corpus could not reach a durable revoked state. Run recovery.": {
    "en": "The Project corpus could not reach a durable revoked state. Run recovery.",
    "zh-CN": "未能持久保存项目资料库的撤销状态，请运行恢复。"
  },
  "Deletion did not finish across every local store. The source remains unavailable while recovery is required.": {
    "en": "Deletion did not finish across every local store. The source remains unavailable while recovery is required.",
    "zh-CN": "尚未从所有本地存储完成删除。恢复完成前，此资料仍不可用。"
  },
  "The operation stopped at a safe boundary. Run recovery or retry the requested action.": {
    "en": "The operation stopped at a safe boundary. Run recovery or retry the requested action.",
    "zh-CN": "操作已在安全边界停止，请恢复或重试操作。"
  },
  "Indexed ": {
    "en": "Indexed ",
    "zh-CN": "索引于 "
  },
  "Replace": {
    "en": "Replace",
    "zh-CN": "替换"
  },
  "Reindex": {
    "en": "Reindex",
    "zh-CN": "重新索引"
  },
  "Export original": {
    "en": "Export original",
    "zh-CN": "导出原文件"
  },
  "Delete": {
    "en": "Delete",
    "zh-CN": "删除"
  },
  "Phase: ": {
    "en": "Phase: ",
    "zh-CN": "阶段："
  },
  "Recovery attempt ": {
    "en": "Recovery attempt ",
    "zh-CN": "恢复尝试次数："
  },
  "{value1} progress": {
    "en": "{value1} progress",
    "zh-CN": "{value1}进度"
  },
  "Could not {value1}.": {
    "en": "Could not {value1}.",
    "zh-CN": "无法完成操作：{value1}。"
  },
  "Delete {fileName} from {projectName}? It will be revoked before local index and original data are removed.": {
    "en": "Delete {fileName} from {projectName}? It will be revoked before local index and original data are removed.",
    "zh-CN": "从「{projectName}」删除「{fileName}」？资料将先被撤销，再删除本地索引与原文件。"
  },
  "Delete source": {
    "en": "Delete source",
    "zh-CN": "删除资料"
  },
  "Revoke all Project Sources in {projectName}? Owned originals remain, but Chats cannot use them until you rebuild.": {
    "en": "Revoke all Project Sources in {projectName}? Owned originals remain, but Chats cannot use them until you rebuild.",
    "zh-CN": "撤销「{projectName}」的所有项目资料？原文件将保留，但重建前聊天无法使用这些资料。"
  },
  "Revoke Project knowledge": {
    "en": "Revoke Project knowledge",
    "zh-CN": "撤销项目知识库"
  },
  "Project Sources for {projectName}": {
    "en": "Project Sources for {projectName}",
    "zh-CN": "「{projectName}」的项目资料"
  },
  "Project Sources": {
    "en": "Project Sources",
    "zh-CN": "项目资料"
  },
  "Indexed locally for Chats in this Project. Other Projects cannot use this corpus.": {
    "en": "Indexed locally for Chats in this Project. Other Projects cannot use this corpus.",
    "zh-CN": "在本地建立索引，供此项目内的聊天使用，其他项目无法使用此资料库。"
  },
  "Project Sources action failed": {
    "en": "Project Sources action failed",
    "zh-CN": "项目资料操作失败"
  },
  "This Project is archived. Sources and history are visible, but lifecycle actions are read-only.": {
    "en": "This Project is archived. Sources and history are visible, but lifecycle actions are read-only.",
    "zh-CN": "此项目已归档，资料和历史记录仍可查看，维护操作为只读状态。"
  },
  "Project Sources are unavailable until the local Backend reconnects with knowledge management support.": {
    "en": "Project Sources are unavailable until the local Backend reconnects with knowledge management support.",
    "zh-CN": "本地服务重新连接并支持知识库管理后，项目资料才可使用。"
  },
  "Project knowledge actions": {
    "en": "Project knowledge actions",
    "zh-CN": "项目知识库操作"
  },
  "Rebuild the revoked corpus before adding another source.": {
    "en": "Rebuild the revoked corpus before adding another source.",
    "zh-CN": "请先重建已撤销的资料库，再添加新资料。"
  },
  "Adding…": {
    "en": "Adding…",
    "zh-CN": "正在添加…"
  },
  "Add sources": {
    "en": "Add sources",
    "zh-CN": "添加资料"
  },
  "Rebuild all": {
    "en": "Rebuild all",
    "zh-CN": "全部重建"
  },
  "Revoke all": {
    "en": "Revoke all",
    "zh-CN": "全部撤销"
  },
  "Recover": {
    "en": "Recover",
    "zh-CN": "恢复"
  },
  "Stopping…": {
    "en": "Stopping…",
    "zh-CN": "正在停止…"
  },
  "Stop current": {
    "en": "Stop current",
    "zh-CN": "停止当前操作"
  },
  "Loading Project Sources": {
    "en": "Loading Project Sources",
    "zh-CN": "正在加载项目资料"
  },
  "Reading canonical source and recovery state from the local Backend.": {
    "en": "Reading canonical source and recovery state from the local Backend.",
    "zh-CN": "正在从本地服务读取资料和恢复状态。"
  },
  "No Project Sources yet. Add a supported document to index it locally.": {
    "en": "No Project Sources yet. Add a supported document to index it locally.",
    "zh-CN": "还没有项目资料。添加支持的文档，即可在本地建立索引。"
  },
  "Recent operations": {
    "en": "Recent operations",
    "zh-CN": "最近的操作"
  },
  "服装": {
    "en": "Outfit",
    "zh-CN": "服装"
  },
  "例如：白色长裙与银色耳饰": {
    "en": "For example: a white dress and silver earrings",
    "zh-CN": "例如：白色长裙与银色耳饰"
  },
  "姿势": {
    "en": "Pose",
    "zh-CN": "姿势"
  },
  "例如：自然站姿，轻轻侧身": {
    "en": "For example: standing naturally, turned slightly to one side",
    "zh-CN": "例如：自然站姿，轻轻侧身"
  },
  "风格": {
    "en": "Style",
    "zh-CN": "风格"
  },
  "例如：柔和的数字插画": {
    "en": "For example: a soft digital illustration",
    "zh-CN": "例如：柔和的数字插画"
  },
  "镜头": {
    "en": "Camera",
    "zh-CN": "镜头"
  },
  "例如：半身肖像，平视": {
    "en": "For example: an eye-level, waist-up portrait",
    "zh-CN": "例如：半身肖像，平视"
  },
  "光线": {
    "en": "Lighting",
    "zh-CN": "光线"
  },
  "例如：温暖逆光": {
    "en": "For example: warm backlighting",
    "zh-CN": "例如：温暖逆光"
  },
  "负面词": {
    "en": "Negative prompt",
    "zh-CN": "负面词"
  },
  "例如：模糊，畸形的手": {
    "en": "For example: blurry, malformed hands",
    "zh-CN": "例如：模糊，畸形的手"
  },
  "检查生成描述": {
    "en": "Checking the description",
    "zh-CN": "检查生成描述"
  },
  "检查本地模型": {
    "en": "Checking local models",
    "zh-CN": "检查本地模型"
  },
  "准备生成": {
    "en": "Preparing generation",
    "zh-CN": "准备生成"
  },
  "正在生成图片": {
    "en": "Generating the image",
    "zh-CN": "正在生成图片"
  },
  "验证图片并保存": {
    "en": "Verifying and saving the image",
    "zh-CN": "验证图片并保存"
  },
  "图片已保存": {
    "en": "Image saved",
    "zh-CN": "图片已保存"
  },
  "尚未配置本地图片模型": {
    "en": "No local image model configured",
    "zh-CN": "尚未配置本地图片模型"
  },
  "缺少已配置的模型文件": {
    "en": "Configured model file is missing",
    "zh-CN": "缺少已配置的模型文件"
  },
  "模型或运行文件校验不一致": {
    "en": "Model or runtime verification does not match",
    "zh-CN": "模型或运行文件校验不一致"
  },
  "本地图片运行环境需要检查": {
    "en": "The local image runtime needs attention",
    "zh-CN": "本地图片运行环境需要检查"
  },
  "请到图片设置恢复未完成任务": {
    "en": "Recover the unfinished task in image settings",
    "zh-CN": "请到图片设置恢复未完成任务"
  },
  "本地图片模型已就绪": {
    "en": "Local image model ready",
    "zh-CN": "本地图片模型已就绪"
  },
  "请在 Elysia 桌面应用中使用图片生成。": {
    "en": "Use image generation in the Elysia desktop app.",
    "zh-CN": "请在 Elysia 桌面应用中使用图片生成。"
  },
  "无法读取本地图片设置。": {
    "en": "Could not read local image settings.",
    "zh-CN": "无法读取本地图片设置。"
  },
  "无法加载画廊。": {
    "en": "Could not load the gallery.",
    "zh-CN": "无法加载画廊。"
  },
  "无法检查本地模型。": {
    "en": "Could not check the local model.",
    "zh-CN": "无法检查本地模型。"
  },
  "无法打开图片预览。": {
    "en": "Could not open the image preview.",
    "zh-CN": "无法打开图片预览。"
  },
  "图片已生成并保存到画廊。": {
    "en": "Image generated and saved to the gallery.",
    "zh-CN": "图片已生成并保存到画廊。"
  },
  "图片生成未完成，请检查模型后重试。": {
    "en": "Image generation did not complete. Check the model and try again.",
    "zh-CN": "图片生成未完成，请检查模型后重试。"
  },
  "无法取消图片任务。": {
    "en": "Could not cancel the image task.",
    "zh-CN": "无法取消图片任务。"
  },
  "图片已导出。": {
    "en": "Image exported.",
    "zh-CN": "图片已导出。"
  },
  "无法导出图片。": {
    "en": "Could not export the image.",
    "zh-CN": "无法导出图片。"
  },
  "图片已从画廊删除。": {
    "en": "Image deleted from the gallery.",
    "zh-CN": "图片已从画廊删除。"
  },
  "无法删除图片。": {
    "en": "Could not delete the image.",
    "zh-CN": "无法删除图片。"
  },
  "图片生成器": {
    "en": "Image generator",
    "zh-CN": "图片生成器"
  },
  "Open navigation": {
    "en": "Open navigation",
    "zh-CN": "打开导航"
  },
  "图片生成": {
    "en": "Image generation",
    "zh-CN": "图片生成"
  },
  "爱莉希雅": {
    "en": "Elysia",
    "zh-CN": "爱莉希雅"
  },
  "本地图片生成": {
    "en": "Local image generation",
    "zh-CN": "本地图片生成"
  },
  "图片运行环境": {
    "en": "Image runtime",
    "zh-CN": "图片运行环境"
  },
  "检查中…": {
    "en": "Checking…",
    "zh-CN": "检查中…"
  },
  "模型已就绪": {
    "en": "Model ready",
    "zh-CN": "模型已就绪"
  },
  "无法读取模型": {
    "en": "Could not read the model",
    "zh-CN": "无法读取模型"
  },
  "重新检查": {
    "en": "Check again",
    "zh-CN": "重新检查"
  },
  "模型设置": {
    "en": "Model settings",
    "zh-CN": "模型设置"
  },
  "图片生成描述": {
    "en": "Image description",
    "zh-CN": "图片生成描述"
  },
  "画面描述": {
    "en": "Scene description",
    "zh-CN": "画面描述"
  },
  "例如：爱莉希雅穿着白色长裙，站在黄昏的花园中，微笑着望向镜头。": {
    "en": "For example: Elysia in a white dress, standing in a garden at dusk and smiling toward the camera.",
    "zh-CN": "例如：爱莉希雅穿着白色长裙，站在黄昏的花园中，微笑着望向镜头。"
  },
  "固定角色：成年虚构爱莉希雅 · 粉色长发 · 紫色眼睛": {
    "en": "Fixed character: fictional adult Elysia · long pink hair · violet eyes",
    "zh-CN": "固定角色：成年虚构爱莉希雅 · 粉色长发 · 紫色眼睛"
  },
  "尺寸": {
    "en": "Size",
    "zh-CN": "尺寸"
  },
  "图片尺寸": {
    "en": "Image size",
    "zh-CN": "图片尺寸"
  },
  "请先选择模型": {
    "en": "Choose a model first",
    "zh-CN": "请先选择模型"
  },
  "LoRA 选择": {
    "en": "Choose LoRAs",
    "zh-CN": "LoRA 选择"
  },
  "LoRA": {
    "en": "LoRA",
    "zh-CN": "LoRA"
  },
  "已选 {length} / 4": {
    "en": "{length} / 4 selected",
    "zh-CN": "已选 {length} / 4"
  },
  "不使用 LoRA": {
    "en": "Use no LoRAs",
    "zh-CN": "不使用 LoRA"
  },
  "尚未导入 LoRA，可在模型设置中添加。": {
    "en": "No LoRAs imported. Add one in model settings.",
    "zh-CN": "尚未导入 LoRA，可在模型设置中添加。"
  },
  "强度": {
    "en": "Strength",
    "zh-CN": "强度"
  },
  "{displayName} 强度": {
    "en": "{displayName} strength",
    "zh-CN": "{displayName} 强度"
  },
  "请输入 0 至 2 的数值。": {
    "en": "Enter a number from 0 to 2.",
    "zh-CN": "请输入 0 至 2 的数值。"
  },
  "更多参数": {
    "en": "More options",
    "zh-CN": "更多参数"
  },
  "模型": {
    "en": "Model",
    "zh-CN": "模型"
  },
  "图片模型": {
    "en": "Image model",
    "zh-CN": "图片模型"
  },
  "Seed": {
    "en": "Seed",
    "zh-CN": "随机种子"
  },
  "随机 Seed": {
    "en": "Random seed",
    "zh-CN": "随机种子"
  },
  "请输入 0 至 9007199254740991 的整数。": {
    "en": "Enter an integer from 0 to 9007199254740991.",
    "zh-CN": "请输入 0 至 9007199254740991 的整数。"
  },
  "保留相同 Seed 与描述，便于再次生成。": {
    "en": "Keep the same seed and description to reproduce the image.",
    "zh-CN": "保留相同 随机种子 与描述，便于再次生成。"
  },
  "正在生成…": {
    "en": "Generating…",
    "zh-CN": "正在生成…"
  },
  "生成图片": {
    "en": "Generate image",
    "zh-CN": "生成图片"
  },
  "取消中…": {
    "en": "Cancelling…",
    "zh-CN": "取消中…"
  },
  "取消生成": {
    "en": "Cancel generation",
    "zh-CN": "取消生成"
  },
  "正在停止本地图片任务…": {
    "en": "Stopping the local image task…",
    "zh-CN": "正在停止本地图片任务…"
  },
  "正在准备图片任务…": {
    "en": "Preparing the image task…",
    "zh-CN": "正在准备图片任务…"
  },
  "图片生成进度": {
    "en": "Image generation progress",
    "zh-CN": "图片生成进度"
  },
  "进度按实际阶段更新，生成阶段可能需要较长时间。": {
    "en": "Progress reflects the actual processing stages. Generation may take some time.",
    "zh-CN": "进度按实际阶段更新，生成阶段可能需要较长时间。"
  },
  "图片画廊": {
    "en": "Image gallery",
    "zh-CN": "图片画廊"
  },
  "画廊": {
    "en": "Gallery",
    "zh-CN": "画廊"
  },
  "你的生成作品，保存在本机。": {
    "en": "Your generated images, saved on this device.",
    "zh-CN": "你的生成作品，保存在本机。"
  },
  "刷新画廊": {
    "en": "Refresh gallery",
    "zh-CN": "刷新画廊"
  },
  "正在打开预览…": {
    "en": "Opening preview…",
    "zh-CN": "正在打开预览…"
  },
  "关闭预览": {
    "en": "Close preview",
    "zh-CN": "关闭预览"
  },
  "正在加载画廊…": {
    "en": "Loading gallery…",
    "zh-CN": "正在加载画廊…"
  },
  "从第一张作品开始": {
    "en": "Start with your first image",
    "zh-CN": "从第一张作品开始"
  },
  "完成生成后，图片会出现在这里。": {
    "en": "Your image will appear here after generation completes.",
    "zh-CN": "完成生成后，图片会出现在这里。"
  },
  "图片 Seed {seed}": {
    "en": "Image seed {seed}",
    "zh-CN": "图片 随机种子 {seed}"
  },
  "预览图片 Seed {seed}": {
    "en": "Preview image with seed {seed}",
    "zh-CN": "预览图片 随机种子 {seed}"
  },
  "打开预览": {
    "en": "Open preview",
    "zh-CN": "打开预览"
  },
  "Seed ": {
    "en": "Seed ",
    "zh-CN": "随机种子 "
  },
  "本地生成": {
    "en": "Generated locally",
    "zh-CN": "本地生成"
  },
  "导出": {
    "en": "Export",
    "zh-CN": "导出"
  },
  "删除": {
    "en": "Delete",
    "zh-CN": "删除"
  },
  "确认删除图片": {
    "en": "Confirm image deletion",
    "zh-CN": "确认删除图片"
  },
  "从画廊永久删除这张图片？已导出的副本会保留。": {
    "en": "Permanently delete this image from the gallery? Exported copies will remain.",
    "zh-CN": "从画廊永久删除这张图片？已导出的副本会保留。"
  },
  "保留图片": {
    "en": "Keep image",
    "zh-CN": "保留图片"
  },
  "确认删除": {
    "en": "Confirm deletion",
    "zh-CN": "确认删除"
  },
  "画廊分页": {
    "en": "Gallery pages",
    "zh-CN": "画廊分页"
  },
  "上一页": {
    "en": "Previous page",
    "zh-CN": "上一页"
  },
  "第 ": {
    "en": "Page ",
    "zh-CN": "第 "
  },
  " 页": {
    "en": " ",
    "zh-CN": " 页"
  },
  "下一页": {
    "en": "Next page",
    "zh-CN": "下一页"
  },
  "先添加一个图片模型": {
    "en": "Add an image model first",
    "zh-CN": "先添加一个图片模型"
  },
  "可以开始生成了": {
    "en": "Ready to generate",
    "zh-CN": "可以开始生成了"
  },
  "模型文件缺失": {
    "en": "Model file missing",
    "zh-CN": "模型文件缺失"
  },
  "模型文件发生了变化": {
    "en": "Model file changed",
    "zh-CN": "模型文件发生了变化"
  },
  "需要修复图片设置": {
    "en": "Image settings need repair",
    "zh-CN": "需要修复图片设置"
  },
  "需要恢复上次任务": {
    "en": "Previous task needs recovery",
    "zh-CN": "需要恢复上次任务"
  },
  "点击导入 Checkpoint，选择本机模型。首次使用会引导你选择运行环境。": {
    "en": "Import a checkpoint to choose a local model. On first use, you will also choose a runtime.",
    "zh-CN": "点击导入 基础模型，选择本机模型。首次使用会引导你选择运行环境。"
  },
  "已连接本地运行环境，生成的图片会保存到画廊。": {
    "en": "The local runtime is connected. Generated images will be saved to the gallery.",
    "zh-CN": "已连接本地运行环境，生成的图片会保存到画廊。"
  },
  "请恢复原模型文件，或通过高级安装修复重新选择模型。": {
    "en": "Restore the original model file, or choose it again through advanced installation repair.",
    "zh-CN": "请恢复原模型文件，或通过高级安装修复重新选择模型。"
  },
  "请确认文件来源，再通过高级安装修复重新选择并确认安装。": {
    "en": "Verify the file source, then choose and confirm the installation through advanced repair.",
    "zh-CN": "请确认文件来源，再通过高级安装修复重新选择并确认安装。"
  },
  "请通过高级安装修复重新选择本地安装，原配置会保留备份。": {
    "en": "Choose a local installation through advanced repair. A backup of the original configuration will be kept.",
    "zh-CN": "请通过高级安装修复重新选择本地安装，原配置会保留备份。"
  },
  "保留未完成任务的文件并恢复运行环境，之后即可继续生成。": {
    "en": "Preserve the unfinished task files and recover the runtime before generating again.",
    "zh-CN": "保留未完成任务的文件并恢复运行环境，之后即可继续生成。"
  },
  "无法检查或保存本地图片设置，请重试。": {
    "en": "Could not check or save local image settings. Try again.",
    "zh-CN": "无法检查或保存本地图片设置，请重试。"
  },
  "导入已取消，图片配置未更改。": {
    "en": "Import cancelled. Image settings were not changed.",
    "zh-CN": "导入已取消，图片配置未更改。"
  },
  "LoRA 已添加，可在生成时选择。": {
    "en": "LoRA added. You can select it when generating an image.",
    "zh-CN": "LoRA 已添加，可在生成时选择。"
  },
  "图片模型已添加。": {
    "en": "Image model added.",
    "zh-CN": "图片模型已添加。"
  },
  "安装配置未更改。": {
    "en": "Installation settings were not changed.",
    "zh-CN": "安装配置未更改。"
  },
  "配置已保存，本地图片模型已就绪。": {
    "en": "Settings saved. The local image model is ready.",
    "zh-CN": "配置已保存，本地图片模型已就绪。"
  },
  "配置已保存，请检查模型与运行环境。": {
    "en": "Settings saved. Check the model and runtime.",
    "zh-CN": "配置已保存，请检查模型与运行环境。"
  },
  "恢复尚未完成，临时文件继续保留。": {
    "en": "Recovery has not completed. Temporary files are being retained.",
    "zh-CN": "恢复尚未完成，临时文件继续保留。"
  },
  "恢复检查完成，未完成任务的文件已保留。": {
    "en": "Recovery check complete. Unfinished task files have been preserved.",
    "zh-CN": "恢复检查完成，未完成任务的文件已保留。"
  },
  "为爱莉希雅选择主模型与 LoRA，作品保存在本机。": {
    "en": "Choose a base model and LoRAs for Elysia. Images stay on this device.",
    "zh-CN": "为爱莉希雅选择主模型与 LoRA，作品保存在本机。"
  },
  "请在 Elysia 桌面应用中配置本地图片生成。": {
    "en": "Configure local image generation in the Elysia desktop app.",
    "zh-CN": "请在 Elysia 桌面应用中配置本地图片生成。"
  },
  "本地图片模型状态": {
    "en": "Local image model status",
    "zh-CN": "本地图片模型状态"
  },
  "正在检查模型…": {
    "en": "Checking models…",
    "zh-CN": "正在检查模型…"
  },
  "尚未读取图片配置": {
    "en": "Image settings have not been loaded",
    "zh-CN": "尚未读取图片配置"
  },
  "正在恢复…": {
    "en": "Recovering…",
    "zh-CN": "正在恢复…"
  },
  "保留文件并恢复": {
    "en": "Preserve files and recover",
    "zh-CN": "保留文件并恢复"
  },
  "正在导入 Checkpoint…": {
    "en": "Importing checkpoint…",
    "zh-CN": "正在导入 基础模型…"
  },
  "导入 Checkpoint": {
    "en": "Import checkpoint",
    "zh-CN": "导入 基础模型"
  },
  "正在导入 LoRA…": {
    "en": "Importing LoRA…",
    "zh-CN": "正在导入 LoRA…"
  },
  "导入 LoRA": {
    "en": "Import LoRA",
    "zh-CN": "导入 LoRA"
  },
  "LoRA 适用模型": {
    "en": "LoRA target model",
    "zh-CN": "LoRA 适用模型"
  },
  "选择文件后确认即可添加，无需填写模型资料。": {
    "en": "Choose and confirm a file to add it. No model details are required.",
    "zh-CN": "选择文件后确认即可添加，无需填写模型资料。"
  },
  "已配置图片模型": {
    "en": "Configured image models",
    "zh-CN": "已配置图片模型"
  },
  "主模型": {
    "en": "Base model",
    "zh-CN": "主模型"
  },
  "默认模型": {
    "en": "Default model",
    "zh-CN": "默认模型"
  },
  "{displayName} 已配置 LoRA": {
    "en": "Configured LoRAs for {displayName}",
    "zh-CN": "{displayName} 已配置 LoRA"
  },
  "已就绪": {
    "en": "Ready",
    "zh-CN": "已就绪"
  },
  "需要检查": {
    "en": "Needs attention",
    "zh-CN": "需要检查"
  },
  "尚未添加 LoRA，也可以直接使用主模型生成。": {
    "en": "No LoRAs added. You can generate images using just the base model.",
    "zh-CN": "尚未添加 LoRA，也可以直接使用主模型生成。"
  },
  "高级安装修复": {
    "en": "Advanced installation repair",
    "zh-CN": "高级安装修复"
  },
  "仅在需要替换或修复运行环境时使用。重新选择安装会替换当前图片配置，请填写实际适用的资料。": {
    "en": "Use only to replace or repair the runtime. Choosing another installation replaces the current image configuration. Enter the applicable details.",
    "zh-CN": "仅在需要替换或修复运行环境时使用。重新选择安装会替换当前图片配置，请填写实际适用的资料。"
  },
  "图片模型名称": {
    "en": "Image model name",
    "zh-CN": "图片模型名称"
  },
  "图片模型 ID": {
    "en": "Image model ID",
    "zh-CN": "图片模型 ID"
  },
  "模型提示词格式": {
    "en": "Model prompt format",
    "zh-CN": "模型提示词格式"
  },
  "SDXL 自然语言": {
    "en": "SDXL natural language",
    "zh-CN": "SDXL 自然语言"
  },
  "Pony 标签": {
    "en": "Pony tags",
    "zh-CN": "Pony 标签"
  },
  "Illustrious 标签": {
    "en": "Illustrious tags",
    "zh-CN": "Illustrious 标签"
  },
  "默认图片尺寸": {
    "en": "Default image size",
    "zh-CN": "默认图片尺寸"
  },
  "方形 · 1024 × 1024": {
    "en": "Square · 1024 × 1024",
    "zh-CN": "方形 · 1024 × 1024"
  },
  "竖图 · 832 × 1216": {
    "en": "Portrait · 832 × 1216",
    "zh-CN": "竖图 · 832 × 1216"
  },
  "横图 · 1216 × 832": {
    "en": "Landscape · 1216 × 832",
    "zh-CN": "横图 · 1216 × 832"
  },
  "模型来源": {
    "en": "Model source",
    "zh-CN": "模型来源"
  },
  "模型许可证": {
    "en": "Model license",
    "zh-CN": "模型许可证"
  },
  "正在选择与校验安装…": {
    "en": "Choosing and verifying the installation…",
    "zh-CN": "正在选择与校验安装…"
  },
  "重新选择并确认安装": {
    "en": "Choose and confirm installation again",
    "zh-CN": "重新选择并确认安装"
  },
  "请完成系统选择与确认。文件校验可能需要一段时间。": {
    "en": "Complete the system file selection and confirmation. Verification may take a moment.",
    "zh-CN": "请完成系统选择与确认。文件校验可能需要一段时间。"
  },
  "Traditional Chinese": {
    "en": "Traditional Chinese",
    "zh-CN": "繁体中文"
  },
  "Describe this image.": {
    "en": "Describe this image.",
    "zh-CN": "描述这张图片。"
  },
  "Could not load local OCR state. Check availability after reconnecting.": {
    "en": "Could not load local OCR state. Check availability after reconnecting.",
    "zh-CN": "无法加载本地文字识别状态，请重新连接后检查可用性。"
  },
  "Local analysis could not complete. Check the local provider before retrying.": {
    "en": "Local analysis could not complete. Check the local provider before retrying.",
    "zh-CN": "本地分析未能完成，请检查本地服务后重试。"
  },
  "Cancellation could not be requested. Wait for the local operation to settle.": {
    "en": "Cancellation could not be requested. Wait for the local operation to settle.",
    "zh-CN": "无法请求取消，请等待本地操作结束。"
  },
  "Visual analysis": {
    "en": "Visual analysis",
    "zh-CN": "视觉分析"
  },
  "Image analysis": {
    "en": "Image analysis",
    "zh-CN": "图片分析"
  },
  "Cancel analysis": {
    "en": "Cancel analysis",
    "zh-CN": "取消分析"
  },
  "Cancellation requested. Waiting for the local analysis operation to finish.": {
    "en": "Cancellation requested. Waiting for the local analysis operation to finish.",
    "zh-CN": "已请求取消，正在等待本地分析结束。"
  },
  "Checking installed local vision models…": {
    "en": "Checking installed local vision models…",
    "zh-CN": "正在检查已安装的本地视觉模型…"
  },
  "The local vision model is analyzing the selected image…": {
    "en": "The local vision model is analyzing the selected image…",
    "zh-CN": "本地视觉模型正在分析所选图片…"
  },
  "Checking local Tesseract and language data…": {
    "en": "Checking local Tesseract and language data…",
    "zh-CN": "正在检查本地 Tesseract 和语言数据…"
  },
  "Local OCR is running…": {
    "en": "Local OCR is running…",
    "zh-CN": "本地文字识别正在运行…"
  },
  "Local OCR": {
    "en": "Local OCR",
    "zh-CN": "本地文字识别"
  },
  "Extract text from your image, locally.": {
    "en": "Extract text from your image, locally.",
    "zh-CN": "在本地提取图片中的文字。"
  },
  "Check OCR": {
    "en": "Check OCR",
    "zh-CN": "检查文字识别"
  },
  "OCR language": {
    "en": "OCR language",
    "zh-CN": "识别语言"
  },
  "Simplified Chinese + English": {
    "en": "Simplified Chinese + English",
    "zh-CN": "简体中文和英语"
  },
  "Traditional Chinese + English": {
    "en": "Traditional Chinese + English",
    "zh-CN": "繁体中文和英语"
  },
  "Run OCR": {
    "en": "Run OCR",
    "zh-CN": "识别文字"
  },
  "Check OCR to connect your local text reader.": {
    "en": "Check OCR to connect your local text reader.",
    "zh-CN": "检查文字识别功能，连接本地识别引擎。"
  },
  "Tesseract {value1} is ready.": {
    "en": "Tesseract {value1} is ready.",
    "zh-CN": "Tesseract {value1} 已就绪。"
  },
  "Tesseract is missing or not configured. Install a local engine before checking again.": {
    "en": "Tesseract is missing or not configured. Install a local engine before checking again.",
    "zh-CN": "未安装或配置 Tesseract，请安装本地引擎后重新检查。"
  },
  "Required Tesseract language data is missing. Install English and the Chinese data you need.": {
    "en": "Required Tesseract language data is missing. Install English and the Chinese data you need.",
    "zh-CN": "缺少所需的 Tesseract 语言数据，请安装英语和需要的中文数据。"
  },
  "Local Tesseract is unavailable. Check the local installation before retrying.": {
    "en": "Local Tesseract is unavailable. Check the local installation before retrying.",
    "zh-CN": "本地 Tesseract 不可用，请检查安装后重试。"
  },
  "Language availability": {
    "en": "Language availability",
    "zh-CN": "语言可用性"
  },
  "Installed language data: ": {
    "en": "Installed language data: ",
    "zh-CN": "已安装的语言数据："
  },
  "The selected language requires additional local Tesseract language data.": {
    "en": "The selected language requires additional local Tesseract language data.",
    "zh-CN": "所选语言需要额外的本地 Tesseract 语言数据。"
  },
  "Local OCR ended: ": {
    "en": "Local OCR ended: ",
    "zh-CN": "本地文字识别已结束："
  },
  "OCR evidence": {
    "en": "OCR evidence",
    "zh-CN": "文字识别结果"
  },
  "OCR extraction · ": {
    "en": "OCR extraction · ",
    "zh-CN": "文字提取 · "
  },
  "Extracted OCR text": {
    "en": "Extracted OCR text",
    "zh-CN": "提取的文字"
  },
  "No text was recognized.": {
    "en": "No text was recognized.",
    "zh-CN": "未识别到文字。"
  },
  "OCR word positions": {
    "en": "OCR word positions",
    "zh-CN": "识别文字的位置"
  },
  "Word positions and confidence (": {
    "en": "Word positions and confidence (",
    "zh-CN": "文字位置和置信度（"
  },
  "% · x ": {
    "en": "% · x ",
    "zh-CN": "% · 横坐标 "
  },
  ", y ": {
    "en": ", y ",
    "zh-CN": "，纵坐标 "
  },
  ", width ": {
    "en": ", width ",
    "zh-CN": "，宽度 "
  },
  ", height ": {
    "en": ", height ",
    "zh-CN": "，高度 "
  },
  "Local vision model": {
    "en": "Local vision model",
    "zh-CN": "本地视觉模型"
  },
  "Ask a question. Explore the details.": {
    "en": "Ask a question. Explore the details.",
    "zh-CN": "提出问题，探索图片细节。"
  },
  "Check vision models": {
    "en": "Check vision models",
    "zh-CN": "检查视觉模型"
  },
  "Vision model": {
    "en": "Vision model",
    "zh-CN": "视觉模型"
  },
  "Select a vision model": {
    "en": "Select a vision model",
    "zh-CN": "选择视觉模型"
  },
  "Check vision models to get started.": {
    "en": "Check vision models to get started.",
    "zh-CN": "先检查视觉模型，即可开始。"
  },
  "No installed model advertises vision support. Configure a local Ollama vision model and check again.": {
    "en": "No installed model advertises vision support. Configure a local Ollama vision model and check again.",
    "zh-CN": "已安装的模型均未声明支持视觉。请配置本地 Ollama 视觉模型后重新检查。"
  },
  "Local vision models are unavailable. Start local Ollama and check again.": {
    "en": "Local vision models are unavailable. Start local Ollama and check again.",
    "zh-CN": "本地视觉模型不可用，请启动本地 Ollama 后重新检查。"
  },
  "Question about image": {
    "en": "Question about image",
    "zh-CN": "关于图片的问题"
  },
  " / 4096 UTF-8 bytes.": {
    "en": " / 4096 UTF-8 bytes.",
    "zh-CN": " / 4096 个 UTF-8 字节。"
  },
  "Enter a question within the byte limit before asking.": {
    "en": "Enter a question within the byte limit before asking.",
    "zh-CN": "请输入字节数在限制内的问题。"
  },
  "Ask about image": {
    "en": "Ask about image",
    "zh-CN": "询问图片内容"
  },
  "Runs locally. Review model answers for accuracy.": {
    "en": "Runs locally. Review model answers for accuracy.",
    "zh-CN": "在本地运行，请核实模型回答的准确性。"
  },
  "Local vision model ended: ": {
    "en": "Local vision model ended: ",
    "zh-CN": "本地视觉模型已结束："
  },
  "Model inference": {
    "en": "Model inference",
    "zh-CN": "模型推断"
  },
  "Model inference · ": {
    "en": "Model inference · ",
    "zh-CN": "模型推断 · "
  },
  "Question: ": {
    "en": "Question: ",
    "zh-CN": "问题："
  },
  "Local model inference": {
    "en": "Local model inference",
    "zh-CN": "本地模型推断"
  },
  "The local model returned no description.": {
    "en": "The local model returned no description.",
    "zh-CN": "本地模型未返回描述。"
  },
  "Review this interpretation against the selected image and OCR evidence.": {
    "en": "Review this interpretation against the selected image and OCR evidence.",
    "zh-CN": "请对照所选图片和文字识别结果核实此解读。"
  },
  "The model response was truncated. Review it as incomplete.": {
    "en": "The model response was truncated. Review it as incomplete.",
    "zh-CN": "模型回答已被截断，请按不完整内容查看。"
  },
  "Could not check native capture capacity. Retry when the desktop service is available.": {
    "en": "Could not check native capture capacity. Retry when the desktop service is available.",
    "zh-CN": "无法检查本机截图能力，请在桌面服务可用时重试。"
  },
  "Capture owner changed.": {
    "en": "Capture owner changed.",
    "zh-CN": "截图所属会话已更改。"
  },
  "Capture scope changed.": {
    "en": "Capture scope changed.",
    "zh-CN": "截图范围已更改。"
  },
  "Capture cancelled. No image was added.": {
    "en": "Capture cancelled. No image was added.",
    "zh-CN": "已取消截图，未添加图片。"
  },
  "One selected frame was added for review.": {
    "en": "One selected frame was added for review.",
    "zh-CN": "已添加一帧所选画面，等待查看。"
  },
  "Capture could not complete. List sources and prepare a fresh scope before trying again.": {
    "en": "Capture could not complete. List sources and prepare a fresh scope before trying again.",
    "zh-CN": "截图未能完成，请重新列出来源并确认新的范围后重试。"
  },
  "Cancellation requested. Waiting for capture resources to close.": {
    "en": "Cancellation requested. Waiting for capture resources to close.",
    "zh-CN": "已请求取消，正在等待截图资源关闭。"
  },
  "Capture cancellation requested.": {
    "en": "Capture cancellation requested.",
    "zh-CN": "已请求取消截图。"
  },
  "Capture cancellation could not be requested. Wait for the native action to settle.": {
    "en": "Capture cancellation could not be requested. Wait for the native action to settle.",
    "zh-CN": "无法请求取消截图，请等待本机操作结束。"
  },
  "Screenshot permission": {
    "en": "Screenshot permission",
    "zh-CN": "截图权限"
  },
  "Capture a screenshot": {
    "en": "Capture a screenshot",
    "zh-CN": "截取屏幕"
  },
  "Choose a display or window, then confirm one screenshot. Listing sources does not capture pixels.": {
    "en": "Choose a display or window, then confirm one screenshot. Listing sources does not capture pixels.",
    "zh-CN": "选择显示器或窗口，再确认截取一张图片。列出来源时不会捕获画面。"
  },
  "List displays and windows": {
    "en": "List displays and windows",
    "zh-CN": "列出显示器和窗口"
  },
  "Capture source": {
    "en": "Capture source",
    "zh-CN": "截图来源"
  },
  "Select a display or window": {
    "en": "Select a display or window",
    "zh-CN": "选择显示器或窗口"
  },
  "Capture scope": {
    "en": "Capture scope",
    "zh-CN": "截图范围"
  },
  "One full selected source frame": {
    "en": "One full selected source frame",
    "zh-CN": "所选来源的完整一帧"
  },
  "Region of selected display": {
    "en": "Region of selected display",
    "zh-CN": "所选显示器的部分区域"
  },
  "A region capture temporarily reads one full frame of this selected display, then keeps the crop and discards pixels outside it.": {
    "en": "A region capture temporarily reads one full frame of this selected display, then keeps the crop and discards pixels outside it.",
    "zh-CN": "区域截图会临时读取所选显示器的一整帧，只保留裁剪区域并丢弃其余像素。"
  },
  "Left (%)": {
    "en": "Left (%)",
    "zh-CN": "左侧（%）"
  },
  "Top (%)": {
    "en": "Top (%)",
    "zh-CN": "顶部（%）"
  },
  "Width (%)": {
    "en": "Width (%)",
    "zh-CN": "宽度（%）"
  },
  "Height (%)": {
    "en": "Height (%)",
    "zh-CN": "高度（%）"
  },
  "Region percentages must stay inside this selected display.": {
    "en": "Region percentages must stay inside this selected display.",
    "zh-CN": "区域百分比必须位于所选显示器范围内。"
  },
  "Review capture scope": {
    "en": "Review capture scope",
    "zh-CN": "检查截图范围"
  },
  "Remove an image before adding a screenshot.": {
    "en": "Remove an image before adding a screenshot.",
    "zh-CN": "请先移除一张图片，再添加截图。"
  },
  "Capture scope review": {
    "en": "Capture scope review",
    "zh-CN": "截图范围确认"
  },
  "One selected source frame": {
    "en": "One selected source frame",
    "zh-CN": "所选来源的完整一帧"
  },
  "One selected display region": {
    "en": "One selected display region",
    "zh-CN": "所选显示器的一个区域"
  },
  "Source kind: ": {
    "en": "Source kind: ",
    "zh-CN": "来源类型："
  },
  ". Expires: ": {
    "en": ". Expires: ",
    "zh-CN": "。到期时间："
  },
  "Normalized crop: left ": {
    "en": "Normalized crop: left ",
    "zh-CN": "归一化裁剪范围：左侧 "
  },
  ", top ": {
    "en": ", top ",
    "zh-CN": "，顶部 "
  },
  ". One full selected-display frame is temporarily captured before the crop is kept.": {
    "en": ". One full selected-display frame is temporarily captured before the crop is kept.",
    "zh-CN": "。将临时截取所选显示器的一整帧，然后只保留裁剪区域。"
  },
  "This capture review expired. Edit the scope and prepare a fresh review.": {
    "en": "This capture review expired. Edit the scope and prepare a fresh review.",
    "zh-CN": "此截图确认已过期，请编辑范围并重新确认。"
  },
  "Capture this source once": {
    "en": "Capture this source once",
    "zh-CN": "截取此来源一次"
  },
  "Deny capture": {
    "en": "Deny capture",
    "zh-CN": "拒绝截图"
  },
  "Edit capture scope": {
    "en": "Edit capture scope",
    "zh-CN": "编辑截图范围"
  },
  "Cancel capture": {
    "en": "Cancel capture",
    "zh-CN": "取消截图"
  },
  "A native capture is still active. Cancel it or wait for its resources to close.": {
    "en": "A native capture is still active. Cancel it or wait for its resources to close.",
    "zh-CN": "本机截图仍在进行，请取消或等待其资源关闭。"
  },
  "Waiting for the one-frame capture and native resources to close…": {
    "en": "Waiting for the one-frame capture and native resources to close…",
    "zh-CN": "正在等待单帧截图完成及本机资源关闭…"
  },
  "Preparing capture metadata…": {
    "en": "Preparing capture metadata…",
    "zh-CN": "正在准备截图信息…"
  },
  "Review owner changed.": {
    "en": "Review owner changed.",
    "zh-CN": "确认内容所属会话已更改。"
  },
  "The review is unavailable. Select the image and review its current analysis again.": {
    "en": "The review is unavailable. Select the image and review its current analysis again.",
    "zh-CN": "确认内容不可用，请重新选择图片并查看当前分析结果。"
  },
  "Review expired.": {
    "en": "Review expired.",
    "zh-CN": "确认已过期。"
  },
  "This context could not be used. Review the current image again.": {
    "en": "This context could not be used. Review the current image again.",
    "zh-CN": "无法使用此上下文，请重新检查当前图片。"
  },
  "Vision context review": {
    "en": "Vision context review",
    "zh-CN": "视觉上下文确认"
  },
  "Continue with this image": {
    "en": "Continue with this image",
    "zh-CN": "继续使用这张图片"
  },
  "Add reviewed text to your conversation. Nothing is sent automatically.": {
    "en": "Add reviewed text to your conversation. Nothing is sent automatically.",
    "zh-CN": "将已确认的文字添加到对话中，内容不会自动发送。"
  },
  "Your description": {
    "en": "Your description",
    "zh-CN": "你的描述"
  },
  "Add a note or explain what matters…": {
    "en": "Add a note or explain what matters…",
    "zh-CN": "添加备注，或说明需要关注的内容…"
  },
  "Shorten your description to at most 4 KiB of text.": {
    "en": "Shorten your description to at most 4 KiB of text.",
    "zh-CN": "请将描述缩短至最多 4 KiB。"
  },
  "Working…": {
    "en": "Working…",
    "zh-CN": "正在处理…"
  },
  "Review context": {
    "en": "Review context",
    "zh-CN": "检查上下文"
  },
  "What this review includes": {
    "en": "What this review includes",
    "zh-CN": "此确认包含的内容"
  },
  "Review the source, extracted text, model interpretation, and your description separately. This does not send a message or approve a task.": {
    "en": "Review the source, extracted text, model interpretation, and your description separately. This does not send a message or approve a task.",
    "zh-CN": "分别检查来源、提取文字、模型解读和你的描述。此操作不会发送消息或批准任务。"
  },
  "Reviewed image context": {
    "en": "Reviewed image context",
    "zh-CN": "已确认的图片上下文"
  },
  "Image source": {
    "en": "Image source",
    "zh-CN": "图片来源"
  },
  "The source identifies selected pixels; it does not verify a scene interpretation.": {
    "en": "The source identifies selected pixels; it does not verify a scene interpretation.",
    "zh-CN": "来源只表明所选像素的出处，不代表图片解读已经验证。"
  },
  "OCR extraction": {
    "en": "OCR extraction",
    "zh-CN": "文字提取"
  },
  "No OCR text is included.": {
    "en": "No OCR text is included.",
    "zh-CN": "未包含识别文字。"
  },
  "OCR text was shortened for this review.": {
    "en": "OCR text was shortened for this review.",
    "zh-CN": "此次确认中的识别文字已缩短。"
  },
  "Model inference — may be wrong": {
    "en": "Model inference — may be wrong",
    "zh-CN": "模型推断——可能有误"
  },
  "No model inference is included.": {
    "en": "No model inference is included.",
    "zh-CN": "未包含模型推断。"
  },
  "The model answer is incomplete or was shortened for this review.": {
    "en": "The model answer is incomplete or was shortened for this review.",
    "zh-CN": "模型回答不完整，或已为此次确认缩短。"
  },
  "No description was entered.": {
    "en": "No description was entered.",
    "zh-CN": "未输入描述。"
  },
  "This review expires after five minutes. Image text and model answers cannot authorize actions.": {
    "en": "This review expires after five minutes. Image text and model answers cannot authorize actions.",
    "zh-CN": "此次确认五分钟后到期，图片文字和模型回答不能授权任何操作。"
  },
  "Add to Chat draft": {
    "en": "Add to Chat draft",
    "zh-CN": "添加到聊天草稿"
  },
  "Use in Work plan": {
    "en": "Use in Work plan",
    "zh-CN": "用于工作计划"
  },
  "How this context is used": {
    "en": "How this context is used",
    "zh-CN": "此上下文的使用方式"
  },
  "Only text is added; image pixels are not attached. Text follows your configured Chat or Work model connection. Chat drafts and sent messages use normal Chat storage. Work evidence stays in memory, and each planned action still needs its own review.": {
    "en": "Only text is added; image pixels are not attached. Text follows your configured Chat or Work model connection. Chat drafts and sent messages use normal Chat storage. Work evidence stays in memory, and each planned action still needs its own review.",
    "zh-CN": "仅添加文字，不附带图片像素。文字使用已配置的聊天或工作模型连接。聊天草稿和已发送消息采用正常聊天存储；工作证据保留在内存中，每个计划操作仍需单独确认。"
  },
  "This review expired. Review the current image again before using it.": {
    "en": "This review expired. Review the current image again before using it.",
    "zh-CN": "此次确认已过期，请重新检查当前图片后再使用。"
  },
  "Vision": {
    "en": "Vision",
    "zh-CN": "视觉理解"
  },
  "Look closer. Make sense of it.": {
    "en": "Look closer. Make sense of it.",
    "zh-CN": "细看图片，理解内容。"
  },
  "Close Vision": {
    "en": "Close Vision",
    "zh-CN": "关闭视觉理解"
  },
  "History owner changed.": {
    "en": "History owner changed.",
    "zh-CN": "历史记录所属会话已更改。"
  },
  "The saved image action could not complete. Reload the list or retry deletion after checking local storage.": {
    "en": "The saved image action could not complete. Reload the list or retry deletion after checking local storage.",
    "zh-CN": "已保存图片的操作未能完成，请检查本地存储后重新加载列表或重试删除。"
  },
  "Saved Vision images": {
    "en": "Saved Vision images",
    "zh-CN": "已保存的视觉图片"
  },
  "Your saved images": {
    "en": "Your saved images",
    "zh-CN": "你保存的图片"
  },
  "Saved locally without encryption. OCR and model answers are excluded.": {
    "en": "Saved locally without encryption. OCR and model answers are excluded.",
    "zh-CN": "在本地以未加密形式保存，不包含识别文字和模型回答。"
  },
  "Allow saving selected images": {
    "en": "Allow saving selected images",
    "zh-CN": "允许保存所选图片"
  },
  "Off in each Chat. Images are saved only when you choose.": {
    "en": "Off in each Chat. Images are saved only when you choose.",
    "zh-CN": "每个聊天默认关闭，仅在你主动选择时保存图片。"
  },
  "Selected image is saved": {
    "en": "Selected image is saved",
    "zh-CN": "所选图片已保存"
  },
  "Save selected image": {
    "en": "Save selected image",
    "zh-CN": "保存所选图片"
  },
  "Load saved images": {
    "en": "Load saved images",
    "zh-CN": "加载已保存图片"
  },
  "Storage and deletion": {
    "en": "Storage and deletion",
    "zh-CN": "存储与删除"
  },
  "Saved copies contain image pixels and a source label in the local application data folder. Saving is off on each new session or Chat.": {
    "en": "Saved copies contain image pixels and a source label in the local application data folder. Saving is off on each new session or Chat.",
    "zh-CN": "副本包含图片像素和来源标签，位于应用本地数据目录。每个新会话或聊天默认关闭保存。"
  },
  "Turning saving off keeps existing copies. Deleting saved copies leaves original files, selected session images, and Chat messages intact.": {
    "en": "Turning saving off keeps existing copies. Deleting saved copies leaves original files, selected session images, and Chat messages intact.",
    "zh-CN": "关闭保存不会移除现有副本。删除副本不会影响原文件、会话中所选图片或聊天消息。"
  },
  "Deletion applies to the current application data folder. Recovery folders retained during a failed move are listed separately in Settings.": {
    "en": "Deletion applies to the current application data folder. Recovery folders retained during a failed move are listed separately in Settings.",
    "zh-CN": "删除只适用于当前应用数据目录。移动失败时保留的恢复文件夹会在设置中单独列出。"
  },
  "Clear this Chat’s saved images": {
    "en": "Clear this Chat’s saved images",
    "zh-CN": "清除此聊天保存的图片"
  },
  "Clear all saved Vision images": {
    "en": "Clear all saved Vision images",
    "zh-CN": "清除所有保存的视觉图片"
  },
  "Load the saved list to review this Chat’s image copies.": {
    "en": "Load the saved list to review this Chat’s image copies.",
    "zh-CN": "加载保存列表，查看此聊天的图片副本。"
  },
  "No saved images for this Chat.": {
    "en": "No saved images for this Chat.",
    "zh-CN": "此聊天没有保存的图片。"
  },
  "Restore saved image {fileName}": {
    "en": "Restore saved image {fileName}",
    "zh-CN": "恢复已保存图片「{fileName}」"
  },
  "Restore to selected images": {
    "en": "Restore to selected images",
    "zh-CN": "恢复到所选图片"
  },
  "Delete saved image {fileName}": {
    "en": "Delete saved image {fileName}",
    "zh-CN": "删除已保存图片「{fileName}」"
  },
  "Delete saved copy": {
    "en": "Delete saved copy",
    "zh-CN": "删除保存的副本"
  },
  "Confirm saved image deletion": {
    "en": "Confirm saved image deletion",
    "zh-CN": "确认删除已保存图片"
  },
  "Delete all saved Vision images, including copies belonging to other or deleted Chats?": {
    "en": "Delete all saved Vision images, including copies belonging to other or deleted Chats?",
    "zh-CN": "删除所有已保存的视觉图片，包括其他聊天或已删除聊天的副本？"
  },
  "Delete all saved Vision copies for this Chat and Project?": {
    "en": "Delete all saved Vision copies for this Chat and Project?",
    "zh-CN": "删除此聊天与项目的所有视觉图片副本？"
  },
  "Delete the saved copy of {label}?": {
    "en": "Delete the saved copy of {label}?",
    "zh-CN": "删除「{label}」的保存副本？"
  },
  "Confirm deletion": {
    "en": "Confirm deletion",
    "zh-CN": "确认删除"
  },
  "Keep saved images": {
    "en": "Keep saved images",
    "zh-CN": "保留已保存图片"
  },
  "Updating saved images…": {
    "en": "Updating saved images…",
    "zh-CN": "正在更新已保存图片…"
  },
  "{value1} KiB": {
    "en": "{value1} KiB",
    "zh-CN": "{value1} KiB"
  },
  "{value1} MiB": {
    "en": "{value1} MiB",
    "zh-CN": "{value1} MiB"
  },
  "Image state belongs to a different Chat. Reload images after navigation finishes.": {
    "en": "Image state belongs to a different Chat. Reload images after navigation finishes.",
    "zh-CN": "图片状态属于其他聊天，请等待导航完成后重新加载图片。"
  },
  "Could not load selected images. Reload images when the local service is ready.": {
    "en": "Could not load selected images. Reload images when the local service is ready.",
    "zh-CN": "无法加载所选图片，请在本地服务就绪后重新加载。"
  },
  "Vision owner changed.": {
    "en": "Vision owner changed.",
    "zh-CN": "视觉理解所属会话已更改。"
  },
  "The image action could not complete. Review a supported PNG or JPEG attachment, or reload images before retrying.": {
    "en": "The image action could not complete. Review a supported PNG or JPEG attachment, or reload images before retrying.",
    "zh-CN": "图片操作未能完成，请检查支持的 PNG 或 JPEG 附件，或重新加载图片后重试。"
  },
  "Vision images": {
    "en": "Vision images",
    "zh-CN": "视觉图片"
  },
  "Image workspace": {
    "en": "Image workspace",
    "zh-CN": "图片工作区"
  },
  "View active task": {
    "en": "View active task",
    "zh-CN": "查看进行中的任务"
  },
  "Screenshot": {
    "en": "Screenshot",
    "zh-CN": "截图"
  },
  "Image options": {
    "en": "Image options",
    "zh-CN": "图片选项"
  },
  "Images stay in this session and clear when the Chat or Project changes.": {
    "en": "Images stay in this session and clear when the Chat or Project changes.",
    "zh-CN": "图片仅保留在此会话中，切换聊天或项目后会清除。"
  },
  "Reload images": {
    "en": "Reload images",
    "zh-CN": "重新加载图片"
  },
  "Clear selected images": {
    "en": "Clear selected images",
    "zh-CN": "清除所选图片"
  },
  "Vision image": {
    "en": "Vision image",
    "zh-CN": "视觉图片"
  },
  "Select an image": {
    "en": "Select an image",
    "zh-CN": "选择图片"
  },
  "Loading selected images…": {
    "en": "Loading selected images…",
    "zh-CN": "正在加载所选图片…"
  },
  "A closer look starts here": {
    "en": "A closer look starts here",
    "zh-CN": "从这里开始细看"
  },
  "Choose an image to explore": {
    "en": "Choose an image to explore",
    "zh-CN": "选择一张图片开始探索"
  },
  "Take a screenshot, or review a PNG or JPEG from your message attachments.": {
    "en": "Take a screenshot, or review a PNG or JPEG from your message attachments.",
    "zh-CN": "截取屏幕，或查看消息附件中的 PNG 或 JPEG 图片。"
  },
  "Select a thumbnail below to preview and analyze it.": {
    "en": "Select a thumbnail below to preview and analyze it.",
    "zh-CN": "选择下方缩略图，预览和分析图片。"
  },
  "Take a screenshot": {
    "en": "Take a screenshot",
    "zh-CN": "截取屏幕"
  },
  "This image preview could not be displayed.": {
    "en": "This image preview could not be displayed.",
    "zh-CN": "无法显示此图片预览。"
  },
  "Selected image thumbnails": {
    "en": "Selected image thumbnails",
    "zh-CN": "所选图片的缩略图"
  },
  "Selected image {fileName}": {
    "en": "Selected image {fileName}",
    "zh-CN": "所选图片「{fileName}」"
  },
  "Review selected image {fileName}": {
    "en": "Review selected image {fileName}",
    "zh-CN": "查看所选图片「{fileName}」"
  },
  "Remove selected image {fileName}": {
    "en": "Remove selected image {fileName}",
    "zh-CN": "移除所选图片「{fileName}」"
  },
  "Remove an image before reviewing another. Up to eight images can be selected.": {
    "en": "Remove an image before reviewing another. Up to eight images can be selected.",
    "zh-CN": "请先移除一张图片，再查看其他图片。最多可选择八张。"
  },
  "Image tools": {
    "en": "Image tools",
    "zh-CN": "图片工具"
  },
  "Vision tools": {
    "en": "Vision tools",
    "zh-CN": "视觉工具"
  },
  "Key pending": {
    "en": "Key pending",
    "zh-CN": "调性待定"
  },
  "Original key": {
    "en": "Original key",
    "zh-CN": "原调"
  },
  "Lyrics-driven singing": {
    "en": "Lyrics-driven singing",
    "zh-CN": "歌词驱动演唱"
  },
  "Voice conversion": {
    "en": "Voice conversion",
    "zh-CN": "音色转换"
  },
  "Legacy RVC": {
    "en": "Legacy RVC",
    "zh-CN": "旧版 RVC"
  },
  "Singing method pending": {
    "en": "Singing method pending",
    "zh-CN": "演唱方式待定"
  },
  "Song Cover": {
    "en": "Song Cover",
    "zh-CN": "歌曲翻唱"
  },
  "Preparing audio": {
    "en": "Preparing audio",
    "zh-CN": "正在准备音频"
  },
  "Separating vocals": {
    "en": "Separating vocals",
    "zh-CN": "正在分离人声"
  },
  "Finding lyrics": {
    "en": "Finding lyrics",
    "zh-CN": "正在查找歌词"
  },
  "Aligning lyrics": {
    "en": "Aligning lyrics",
    "zh-CN": "正在对齐歌词"
  },
  "Singing": {
    "en": "Singing",
    "zh-CN": "正在演唱"
  },
  "Converting voice": {
    "en": "Converting voice",
    "zh-CN": "正在转换音色"
  },
  "Mixing your cover": {
    "en": "Mixing your cover",
    "zh-CN": "正在混音"
  },
  "Ready to play": {
    "en": "Ready to play",
    "zh-CN": "可播放"
  },
  "Now playing": {
    "en": "Now playing",
    "zh-CN": "正在播放"
  },
  "Needs attention": {
    "en": "Needs attention",
    "zh-CN": "需要处理"
  },
  "Hide Song Cover": {
    "en": "Hide Song Cover",
    "zh-CN": "隐藏歌曲翻唱"
  },
  "Hide player": {
    "en": "Hide player",
    "zh-CN": "隐藏播放器"
  },
  "Elysia · Song Cover": {
    "en": "Elysia · Song Cover",
    "zh-CN": "Elysia · 歌曲翻唱"
  },
  "Song Cover settings": {
    "en": "Song Cover settings",
    "zh-CN": "歌曲翻唱设置"
  },
  "Vocal + accompaniment shift": {
    "en": "Vocal + accompaniment shift",
    "zh-CN": "人声与伴奏一同移调"
  },
  "Vocal-only shift": {
    "en": "Vocal-only shift",
    "zh-CN": "仅人声移调"
  },
  "Accompaniment · ": {
    "en": "Accompaniment · ",
    "zh-CN": "伴奏 · "
  },
  "Prepare": {
    "en": "Prepare",
    "zh-CN": "准备"
  },
  "Sing": {
    "en": "Sing",
    "zh-CN": "演唱"
  },
  "Mix": {
    "en": "Mix",
    "zh-CN": "混音"
  },
  "Song Cover progress": {
    "en": "Song Cover progress",
    "zh-CN": "歌曲翻唱进度"
  },
  "Seek Song Cover": {
    "en": "Seek Song Cover",
    "zh-CN": "跳转翻唱播放位置"
  },
  "Playback time": {
    "en": "Playback time",
    "zh-CN": "播放时间"
  },
  "Choose another song to try again.": {
    "en": "Choose another song to try again.",
    "zh-CN": "选择另一首歌曲重试。"
  },
  "Cancel Song Cover": {
    "en": "Cancel Song Cover",
    "zh-CN": "取消歌曲翻唱"
  },
  "Cancel": {
    "en": "Cancel",
    "zh-CN": "取消"
  },
  "Stop Song Cover": {
    "en": "Stop Song Cover",
    "zh-CN": "停止歌曲翻唱"
  },
  "Play Song Cover": {
    "en": "Play Song Cover",
    "zh-CN": "播放歌曲翻唱"
  },
  "Stop": {
    "en": "Stop",
    "zh-CN": "停止"
  },
  "Play cover": {
    "en": "Play cover",
    "zh-CN": "播放翻唱"
  },
  "Export Song Cover": {
    "en": "Export Song Cover",
    "zh-CN": "导出歌曲翻唱"
  },
  "Export lossless WAV": {
    "en": "Export lossless WAV",
    "zh-CN": "导出无损 WAV"
  },
  "Save WAV": {
    "en": "Save WAV",
    "zh-CN": "保存 WAV"
  },
  "Enter both song title and artist, or leave both fields blank.": {
    "en": "Enter both song title and artist, or leave both fields blank.",
    "zh-CN": "请同时填写歌曲名和歌手，或将两项均留空。"
  },
  "Choose audio": {
    "en": "Choose audio",
    "zh-CN": "选择音频"
  },
  "A song you love, in Elysia’s voice.": {
    "en": "A song you love, in Elysia’s voice.",
    "zh-CN": "让 Elysia 唱一首你喜欢的歌。"
  },
  "Create Song Cover": {
    "en": "Create Song Cover",
    "zh-CN": "创建歌曲翻唱"
  },
  "Singing method": {
    "en": "Singing method",
    "zh-CN": "演唱方式"
  },
  "Follow the original words, melody and timing.": {
    "en": "Follow the original words, melody and timing.",
    "zh-CN": "跟随原曲的歌词、旋律和节奏。"
  },
  "Sing from synced Mandarin lyrics.": {
    "en": "Sing from synced Mandarin lyrics.",
    "zh-CN": "根据同步的普通话歌词演唱。"
  },
  "Use the previous local voice model.": {
    "en": "Use the previous local voice model.",
    "zh-CN": "使用之前的本地音色模型。"
  },
  "About voice quality": {
    "en": "About voice quality",
    "zh-CN": "关于音质"
  },
  "Offline conversion using the source vocal as a pronunciation and timing guide. Quality depends on the local private model; sustained low or high notes may sound hoarse or electronic.": {
    "en": "Offline conversion using the source vocal as a pronunciation and timing guide. Quality depends on the local private model; sustained low or high notes may sound hoarse or electronic.",
    "zh-CN": "以原始人声作为发音与节奏参考，在本地离线转换。音质取决于本地私有模型；持续的低音或高音可能沙哑或带有电子感。"
  },
  "Singing reference": {
    "en": "Singing reference",
    "zh-CN": "演唱参考"
  },
  "妖精小姐的魔法邀约": {
    "en": "The Fairy's Magical Invitation",
    "zh-CN": "妖精小姐的魔法邀约"
  },
  "Vocal arrangement": {
    "en": "Vocal arrangement",
    "zh-CN": "人声处理范围"
  },
  "Entire vocal track": {
    "en": "Entire vocal track",
    "zh-CN": "完整人声音轨"
  },
  "Convert the full vocal track. Overlapping singers can blend.": {
    "en": "Convert the full vocal track. Overlapping singers can blend.",
    "zh-CN": "转换完整人声音轨，重叠的歌声可能混合。"
  },
  "Lead only, keep other singers": {
    "en": "Lead only, keep other singers",
    "zh-CN": "仅主唱，保留其他歌手"
  },
  "Use prepared stems to keep the original voices in your backing mix.": {
    "en": "Use prepared stems to keep the original voices in your backing mix.",
    "zh-CN": "使用预先分离的音轨，保留背景混音中的原始声音。"
  },
  "Audio source": {
    "en": "Audio source",
    "zh-CN": "音频来源"
  },
  "Complete song": {
    "en": "Complete song",
    "zh-CN": "完整歌曲"
  },
  "Isolated lead + backing mix": {
    "en": "Isolated lead + backing mix",
    "zh-CN": "独立主唱与背景混音"
  },
  "Vocals + accompaniment": {
    "en": "Vocals + accompaniment",
    "zh-CN": "人声与伴奏"
  },
  "Choose an isolated lead vocal first, then a time-aligned backing mix containing the instruments, original harmonies and other singers, with the lead removed.": {
    "en": "Choose an isolated lead vocal first, then a time-aligned backing mix containing the instruments, original harmonies and other singers, with the lead removed.",
    "zh-CN": "先选择独立主唱，再选择时间对齐的背景混音。背景混音应包含乐器、原始和声及其他歌手，并已移除主唱。"
  },
  "Choose one audio file. Elysia separates its vocal and music.": {
    "en": "Choose one audio file. Elysia separates its vocal and music.",
    "zh-CN": "选择一个音频文件，Elysia 将分离人声与伴奏。"
  },
  "Choose isolated vocals first, then the accompaniment file.": {
    "en": "Choose isolated vocals first, then the accompaniment file.",
    "zh-CN": "先选择独立人声，再选择伴奏文件。"
  },
  "Song key": {
    "en": "Song key",
    "zh-CN": "歌曲调性"
  },
  "Lower song key one semitone": {
    "en": "Lower song key one semitone",
    "zh-CN": "歌曲降一个半音"
  },
  "Follow the source melody": {
    "en": "Follow the source melody",
    "zh-CN": "跟随原曲旋律"
  },
  "Whole-song key adjustment": {
    "en": "Whole-song key adjustment",
    "zh-CN": "整曲移调"
  },
  "Raise song key one semitone": {
    "en": "Raise song key one semitone",
    "zh-CN": "歌曲升一个半音"
  },
  "Song key adjustment": {
    "en": "Song key adjustment",
    "zh-CN": "歌曲移调"
  },
  "Reset to original": {
    "en": "Reset to original",
    "zh-CN": "恢复原调"
  },
  "Shift accompaniment with vocal": {
    "en": "Shift accompaniment with vocal",
    "zh-CN": "伴奏随人声一同移调"
  },
  "Keep the vocal and music in the same key.": {
    "en": "Keep the vocal and music in the same key.",
    "zh-CN": "保持人声与伴奏处于同一调性。"
  },
  "Use Original key to keep the other singers at their original pitch. Shifting the accompaniment also shifts the voices in the backing mix.": {
    "en": "Use Original key to keep the other singers at their original pitch. Shifting the accompaniment also shifts the voices in the backing mix.",
    "zh-CN": "使用原调可让其他歌手保持原来的音高。伴奏移调也会改变背景混音中其他人声的音高。"
  },
  "About key changes": {
    "en": "About key changes",
    "zh-CN": "关于移调"
  },
  "Original key follows the source melody most closely. A key shift always moves the lead; the option above decides whether the accompaniment follows it. Use the smallest shift that fits; larger shifts, especially the 12-semitone octave, can increase vocal artifacts. It is a range tradeoff, not an artifact repair": {
    "en": "Original key follows the source melody most closely. A key shift always moves the lead; the option above decides whether the accompaniment follows it. Use the smallest shift that fits; larger shifts, especially the 12-semitone octave, can increase vocal artifacts. It is a range tradeoff, not an artifact repair",
    "zh-CN": "原调最贴近源旋律。移调始终会改变主唱音高，上方选项决定伴奏是否一同移调。建议使用适合音域的最小幅度；较大的移调，尤其是跨越 12 个半音的八度移调，可能增加人声音质瑕疵。这是音域上的取舍，不是修复音质的方法"
  },
  ", and may soften transients": {
    "en": ", and may soften transients",
    "zh-CN": "，也可能减弱瞬态表现"
  },
  "Turn off accompaniment shifting to keep the music at its original key. Non-octave vocal shifts may then sound out of key.": {
    "en": "Turn off accompaniment shifting to keep the music at its original key. Non-octave vocal shifts may then sound out of key.",
    "zh-CN": "关闭伴奏移调可保留原始伴奏调性。此时，人声进行非整八度移调可能导致调性不合。"
  },
  "Lyrics lookup ": {
    "en": "Lyrics lookup ",
    "zh-CN": "歌词查找 "
  },
  "Song title": {
    "en": "Song title",
    "zh-CN": "歌曲名称"
  },
  "From the audio file": {
    "en": "From the audio file",
    "zh-CN": "从音频文件读取"
  },
  "Artist": {
    "en": "Artist",
    "zh-CN": "歌手"
  },
  "Mandarin lyrics only. Leave both blank to use audio tags.": {
    "en": "Mandarin lyrics only. Leave both blank to use audio tags.",
    "zh-CN": "仅支持普通话歌词。两项都留空时将使用音频标签。"
  },
  "LRCLIB receives the title, artist, and rounded duration to find lyrics; your audio is never uploaded.": {
    "en": "LRCLIB receives the title, artist, and rounded duration to find lyrics; your audio is never uploaded.",
    "zh-CN": "LRCLIB 会接收歌曲名、歌手及取整后的时长以查找歌词，不会上传音频。"
  },
  "When to add a title and artist": {
    "en": "When to add a title and artist",
    "zh-CN": "何时填写歌曲名和歌手"
  },
  "A recognized Artist/Title filename can also identify a song. Generic or ambiguous names need both fields.": {
    "en": "A recognized Artist/Title filename can also identify a song. Generic or ambiguous names need both fields.",
    "zh-CN": "可识别的“歌手/歌曲名”文件名也能用于识别歌曲。名称含糊或过于通用时，请同时填写两项。"
  },
  "{count} messages": {
    "en": "{count} messages",
    "zh-CN": "{count} 条消息"
  },
  "{count} shared chats": {
    "en": "{count} shared chats",
    "zh-CN": "{count} 个共用聊天"
  },
  "Preparing": {
    "en": "Preparing",
    "zh-CN": "准备中"
  },
  "Importing": {
    "en": "Importing",
    "zh-CN": "导入中"
  },
  "Indexing": {
    "en": "Indexing",
    "zh-CN": "索引中"
  },
  "Revoking": {
    "en": "Revoking",
    "zh-CN": "撤销中"
  },
  "Cleaning up": {
    "en": "Cleaning up",
    "zh-CN": "清理中"
  },
  "Publishing": {
    "en": "Publishing",
    "zh-CN": "发布中"
  },
  "Rebuild Project knowledge": {
    "en": "Rebuild Project knowledge",
    "zh-CN": "重建项目知识库"
  },
  "Recover Project knowledge": {
    "en": "Recover Project knowledge",
    "zh-CN": "恢复项目知识库"
  },
  "Stop Project knowledge operation": {
    "en": "Stop Project knowledge operation",
    "zh-CN": "停止项目知识库操作"
  },
  "Reindex source": {
    "en": "Reindex source",
    "zh-CN": "重新索引资料"
  },
  "Replace source": {
    "en": "Replace source",
    "zh-CN": "替换资料"
  },
  "Square": {
    "en": "Square",
    "zh-CN": "方形"
  },
  "Portrait": {
    "en": "Portrait",
    "zh-CN": "竖图"
  },
  "Landscape": {
    "en": "Landscape",
    "zh-CN": "横图"
  },
  "生成图片预览": {
    "en": "Generated image preview",
    "zh-CN": "生成图片预览"
  },
  "预览已失效，请重新打开图片。": {
    "en": "The preview expired. Open the image again.",
    "zh-CN": "预览已失效，请重新打开图片。"
  },
  "Page {page}": {
    "en": "Page {page}",
    "zh-CN": "第 {page} 页"
  },
  "Analyze": {
    "en": "Analyze",
    "zh-CN": "分析"
  },
  "Use in Chat": {
    "en": "Use in Chat",
    "zh-CN": "用于聊天"
  },
  "Use in Work": {
    "en": "Use in Work",
    "zh-CN": "用于工作"
  },
  "Saved images": {
    "en": "Saved images",
    "zh-CN": "已保存图片"
  },
  "Describe image": {
    "en": "Describe image",
    "zh-CN": "描述图片"
  },
  "Read text": {
    "en": "Read text",
    "zh-CN": "识别文字"
  },
  "None": {
    "en": "None",
    "zh-CN": "无"
  },
  "OCR positions in {fileName}": {
    "en": "OCR positions in {fileName}",
    "zh-CN": "「{fileName}」中的识别文字位置"
  },
  "Display": {
    "en": "Display",
    "zh-CN": "显示器"
  },
  "Window": {
    "en": "Window",
    "zh-CN": "窗口"
  },
  "Raised {count} semitone": {
    "en": "Raised {count} semitone",
    "zh-CN": "升高 {count} 个半音"
  },
  "Raised {count} semitones": {
    "en": "Raised {count} semitones",
    "zh-CN": "升高 {count} 个半音"
  },
  "Lowered {count} semitone": {
    "en": "Lowered {count} semitone",
    "zh-CN": "降低 {count} 个半音"
  },
  "Lowered {count} semitones": {
    "en": "Lowered {count} semitones",
    "zh-CN": "降低 {count} 个半音"
  },
  "Full preview of {fileName}": {
    "en": "Full preview of {fileName}",
    "zh-CN": "「{fileName}」的完整预览"
  },
  "Preview of {fileName}": {
    "en": "Preview of {fileName}",
    "zh-CN": "「{fileName}」的预览"
  },
  "{position} of {duration}": {
    "en": "{position} of {duration}",
    "zh-CN": "{position}，共 {duration}"
  },
  "Local analysis is unavailable.": {
    "en": "Local analysis is unavailable.",
    "zh-CN": "本地分析不可用。"
  },
  "Wait for the current local analysis.": {
    "en": "Wait for the current local analysis.",
    "zh-CN": "请等待当前本地分析完成。"
  },
  "Local analysis was cancelled.": {
    "en": "Local analysis was cancelled.",
    "zh-CN": "本地分析已取消。"
  },
  "Local analysis exceeded its time limit.": {
    "en": "Local analysis exceeded its time limit.",
    "zh-CN": "本地分析超过时间限制。"
  },
  "The selected image is unavailable or invalid.": {
    "en": "The selected image is unavailable or invalid.",
    "zh-CN": "所选图片不可用或无效。"
  },
  "The OCR language is invalid.": {
    "en": "The OCR language is invalid.",
    "zh-CN": "文字识别语言无效。"
  },
  "Local analysis returned invalid evidence.": {
    "en": "Local analysis returned invalid evidence.",
    "zh-CN": "本地分析返回的结果无效。"
  },
  "Local analysis exceeded its safe limits.": {
    "en": "Local analysis exceeded its safe limits.",
    "zh-CN": "本地分析超出安全限制。"
  },
  "Local analysis could not complete.": {
    "en": "Local analysis could not complete.",
    "zh-CN": "本地分析无法完成。"
  },
  "The selected image or owner changed.": {
    "en": "The selected image or owner changed.",
    "zh-CN": "所选图片或所属会话已更改。"
  },
  "The selected local vision model is unavailable.": {
    "en": "The selected local vision model is unavailable.",
    "zh-CN": "所选本地视觉模型不可用。"
  },
  "The image question is invalid.": {
    "en": "The image question is invalid.",
    "zh-CN": "图片问题无效。"
  },
  "The selected local model does not support image questions.": {
    "en": "The selected local model does not support image questions.",
    "zh-CN": "所选本地模型不支持图片提问。"
  },
  "Choose and confirm a trusted ComfyUI installation.": {
    "en": "Choose and confirm a trusted ComfyUI installation.",
    "zh-CN": "请选择并确认可信的 ComfyUI 安装。"
  },
  "A configured checkpoint is missing. Install the reviewed model before generating.": {
    "en": "A configured checkpoint is missing. Install the reviewed model before generating.",
    "zh-CN": "已配置的基础模型缺失，请安装确认过的模型后再生成。"
  },
  "A reviewed model or runtime SHA-256 no longer matches. Review the installation.": {
    "en": "A reviewed model or runtime SHA-256 no longer matches. Review the installation.",
    "zh-CN": "已确认模型或运行环境的 SHA-256 已发生变化，请检查安装。"
  },
  "The trusted local image installation is ready.": {
    "en": "The trusted local image installation is ready.",
    "zh-CN": "可信的本地图片运行环境已就绪。"
  },
  "Local image settings or runtime readiness checks failed. Review the installation.": {
    "en": "Local image settings or runtime readiness checks failed. Review the installation.",
    "zh-CN": "本地图片设置或运行环境检查失败，请检查安装。"
  },
  "图片临时目录有未完成任务，请确认隔离保留这些文件后恢复生成。": {
    "en": "Unfinished tasks remain in the image temporary directory. Confirm that their files should be preserved in isolation before resuming generation.",
    "zh-CN": "图片临时目录有未完成任务，请确认隔离保留这些文件后恢复生成。"
  },
  "便携 Python 需要先配置依赖路径。请按本地图片文档运行 prepare_image_python.py，再重新检查。": {
    "en": "The portable Python installation needs its dependency paths configured. Follow the local image documentation to run prepare_image_python.py, then check again.",
    "zh-CN": "便携 Python 需要先配置依赖路径。请按本地图片文档运行 prepare_image_python.py，再重新检查。"
  },
  "请选择完整的 Python 解释器或已审查的便携安装；Windows venv 重定向器暂不支持。": {
    "en": "Choose a complete Python interpreter or a reviewed portable installation. Windows venv redirectors are not supported.",
    "zh-CN": "请选择完整的 Python 解释器或已审查的便携安装；Windows venv 重定向器暂不支持。"
  },
  "LoRA 文件缺失": {
    "en": "LoRA file missing",
    "zh-CN": "LoRA 文件缺失"
  },
  "LoRA 文件校验不一致": {
    "en": "LoRA file verification does not match",
    "zh-CN": "LoRA 文件校验不一致"
  },
  "LoRA 暂不可用": {
    "en": "LoRA is currently unavailable",
    "zh-CN": "LoRA 暂不可用"
  },
  "LoRA 已就绪。": {
    "en": "LoRA is ready.",
    "zh-CN": "LoRA 已就绪。"
  },
  "LoRA 文件缺失，请重新导入。": {
    "en": "The LoRA file is missing. Import it again.",
    "zh-CN": "LoRA 文件缺失，请重新导入。"
  },
  "图片设置正在处理，请稍后重试。": {
    "en": "Image settings are busy. Try again shortly.",
    "zh-CN": "图片设置正在处理，请稍后重试。"
  },
  "图片设置已关闭。": {
    "en": "Image settings are closed.",
    "zh-CN": "图片设置已关闭。"
  },
  "操作已取消。": {
    "en": "Operation cancelled.",
    "zh-CN": "操作已取消。"
  },
  "无法识别导入资料，请重新选择文件。": {
    "en": "Could not recognize the import details. Choose the file again.",
    "zh-CN": "无法识别导入资料，请重新选择文件。"
  },
  "无法校验文件或本地运行环境，请检查安装后重试。": {
    "en": "Could not verify the file or local runtime. Check the installation and try again.",
    "zh-CN": "无法校验文件或本地运行环境，请检查安装后重试。"
  },
  "文件检查超时，请稍后重试。": {
    "en": "File verification timed out. Try again shortly.",
    "zh-CN": "文件检查超时，请稍后重试。"
  },
  "这个文件已经导入，无需重复添加。": {
    "en": "This file has already been imported.",
    "zh-CN": "这个文件已经导入，无需重复添加。"
  },
  "The Song Cover worker returned an invalid response.": {
    "en": "The Song Cover worker returned an invalid response.",
    "zh-CN": "歌曲翻唱进程返回了无效响应。"
  },
  "The Song Cover worker returned no usable audio.": {
    "en": "The Song Cover worker returned no usable audio.",
    "zh-CN": "歌曲翻唱进程未返回可用音频。"
  },
  "The Song Cover is ready, but playback could not start.": {
    "en": "The Song Cover is ready, but playback could not start.",
    "zh-CN": "歌曲翻唱已完成，但无法开始播放。"
  },
  "Song Cover ran out of GPU memory. Close other GPU apps and try again.": {
    "en": "Song Cover ran out of GPU memory. Close other GPU apps and try again.",
    "zh-CN": "歌曲翻唱的显存不足，请关闭其他使用显卡的应用后重试。"
  },
  "Song Cover could not be created. Check the local singing runtime and source audio.": {
    "en": "Song Cover could not be created. Check the local singing runtime and source audio.",
    "zh-CN": "无法创建歌曲翻唱，请检查本地演唱运行环境和源音频。"
  },
  "One installed vision model is available.": {
    "en": "One installed vision model is available.",
    "zh-CN": "已有一个可用的视觉模型。"
  },
  "{count} installed vision models are available.": {
    "en": "{count} installed vision models are available.",
    "zh-CN": "已有 {count} 个可用的视觉模型。"
  },
  "1 image": {
    "en": "1 image",
    "zh-CN": "1 张图片"
  },
  "{count} images": {
    "en": "{count} images",
    "zh-CN": "{count} 张图片"
  },
  "{direction}{count} semitone": {
    "en": "{direction}{count} semitone",
    "zh-CN": "{direction}{count} 个半音"
  },
  "{direction}{count} semitones": {
    "en": "{direction}{count} semitones",
    "zh-CN": "{direction}{count} 个半音"
  }
}
