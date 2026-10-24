/** Reviewed native settings, recovery, device, and notification copy displayed at renderer boundaries. */

import type { MessageCatalog } from './types.ts'

export const storageWarningMessages: MessageCatalog = {
  "Some linked, special, or unreadable entries were not scanned.": {
    "en": "Some linked, special, or unreadable entries were not scanned.",
    "zh-CN": "部分链接、特殊条目或不可读条目未被扫描。"
  },
  "The capacity scan reached its safety limit.": {
    "en": "The capacity scan reached its safety limit.",
    "zh-CN": "容量扫描已达到安全上限。"
  },
  "Free disk capacity is unavailable.": {
    "en": "Free disk capacity is unavailable.",
    "zh-CN": "无法获取可用磁盘容量。"
  },
  "One or more recorded recovery copies are currently unavailable.": {
    "en": "One or more recorded recovery copies are currently unavailable.",
    "zh-CN": "一个或多个已记录的恢复副本当前不可用。"
  },
  "A recovery-copy path could not be inspected and remains recorded.": {
    "en": "A recovery-copy path could not be inspected and remains recorded.",
    "zh-CN": "无法检查某个恢复副本路径，相关记录已保留。"
  },
  "Recovery-copy records could not be finalized and will be checked again next launch.": {
    "en": "Recovery-copy records could not be finalized and will be checked again next launch.",
    "zh-CN": "无法完成恢复副本记录的更新，将在下次启动时再次检查。"
  },
  "A managed root changed or contains unknown or blocked entries and was retained.": {
    "en": "A managed root changed or contains unknown or blocked entries and was retained.",
    "zh-CN": "受管理的根目录发生了变化，或包含未知、受阻条目，因此已保留。"
  },
  "A managed root changed while being retired and was retained.": {
    "en": "A managed root changed while being retired and was retained.",
    "zh-CN": "受管理的根目录在移除期间发生了变化，因此已保留。"
  },
  "New entries appeared at a retired data path and were retained.": {
    "en": "New entries appeared at a retired data path and were retained.",
    "zh-CN": "原数据路径出现了新条目，因此已保留。"
  },
  "The previous managed root could not be verified or removed and was retained.": {
    "en": "The previous managed root could not be verified or removed and was retained.",
    "zh-CN": "无法验证或移除原受管理根目录，因此已保留。"
  },
  "The interrupted data move requires recovery before another storage action can run.": {
    "en": "The interrupted data move requires recovery before another storage action can run.",
    "zh-CN": "中断的数据迁移需要先恢复，才能执行其他存储操作。"
  },
  "The data move was rolled back.": {
    "en": "The data move was rolled back.",
    "zh-CN": "数据迁移已回滚。"
  },
  "The data move was rolled back, but the Python Backend could not restart.": {
    "en": "The data move was rolled back, but the Python Backend could not restart.",
    "zh-CN": "数据迁移已回滚，但 Python 本地服务无法重新启动。"
  },
  "The data move did not start, and the Python Backend could not restart.": {
    "en": "The data move did not start, and the Python Backend could not restart.",
    "zh-CN": "数据迁移未开始，且 Python 本地服务无法重新启动。"
  },
  "A failed move left recovery state that will be checked next launch.": {
    "en": "A failed move left recovery state that will be checked next launch.",
    "zh-CN": "失败的迁移留下了恢复状态，将在下次启动时检查。"
  },
  "Temporary data was cleared, but the Python Backend could not restart.": {
    "en": "Temporary data was cleared, but the Python Backend could not restart.",
    "zh-CN": "临时数据已清理，但 Python 本地服务无法重新启动。"
  },
  "Temporary data was cleared, but capacity could not be measured.": {
    "en": "Temporary data was cleared, but capacity could not be measured.",
    "zh-CN": "临时数据已清理，但无法统计容量。"
  },
  "The active data directory could not be opened.": {
    "en": "The active data directory could not be opened.",
    "zh-CN": "无法打开当前数据目录。"
  },
  "An interrupted data move was rolled back safely.": {
    "en": "An interrupted data move was rolled back safely.",
    "zh-CN": "中断的数据迁移已安全回滚。"
  }
}

export const settingsRuntimeMessages: MessageCatalog = {
  ...storageWarningMessages,
  "Voice capture requires a non-empty session identifier.": {
    "en": "Voice capture requires a non-empty session identifier.",
    "zh-CN": "语音采集需要非空的会话标识。"
  },
  "Voice activity detection has already settled.": {
    "en": "Voice activity detection has already settled.",
    "zh-CN": "语音活动检测已经结束。"
  },
  "Data cleanup request is invalid.": {
    "en": "Data cleanup request is invalid.",
    "zh-CN": "数据清理请求无效。"
  },
  "Data cleanup request has invalid fields.": {
    "en": "Data cleanup request has invalid fields.",
    "zh-CN": "数据清理请求包含无效字段。"
  },
  "Data cleanup revision is invalid.": {
    "en": "Data cleanup revision is invalid.",
    "zh-CN": "数据清理版本无效。"
  },
  "Data cleanup token is invalid.": {
    "en": "Data cleanup token is invalid.",
    "zh-CN": "数据清理凭据无效。"
  },
  "Data cleanup categories are invalid.": {
    "en": "Data cleanup categories are invalid.",
    "zh-CN": "数据清理类别无效。"
  },
  "Data move transaction is invalid.": {
    "en": "Data move transaction is invalid.",
    "zh-CN": "数据迁移事务无效。"
  },
  "Data move roots overlap.": {
    "en": "Data move roots overlap.",
    "zh-CN": "数据迁移的源目录与目标目录重叠。"
  },
  "Data root manifest is invalid.": {
    "en": "Data root manifest is invalid.",
    "zh-CN": "数据根目录清单无效。"
  },
  "Data storage bootstrap is invalid.": {
    "en": "Data storage bootstrap is invalid.",
    "zh-CN": "数据存储启动记录无效。"
  },
  "Pending data move is inconsistent.": {
    "en": "Pending data move is inconsistent.",
    "zh-CN": "待处理的数据迁移记录不一致。"
  },
  "Retained data roots are invalid.": {
    "en": "Retained data roots are invalid.",
    "zh-CN": "保留的数据根目录无效。"
  },
  "Data storage metadata could not be read.": {
    "en": "Data storage metadata could not be read.",
    "zh-CN": "无法读取数据存储元数据。"
  },
  "Data storage metadata is invalid.": {
    "en": "Data storage metadata is invalid.",
    "zh-CN": "数据存储元数据无效。"
  },
  "Data storage metadata is too large.": {
    "en": "Data storage metadata is too large.",
    "zh-CN": "数据存储元数据过大。"
  },
  "Data storage metadata could not be saved.": {
    "en": "Data storage metadata could not be saved.",
    "zh-CN": "无法保存数据存储元数据。"
  },
  "Data storage clock is unavailable.": {
    "en": "Data storage clock is unavailable.",
    "zh-CN": "数据存储时钟不可用。"
  },
  "A data storage path could not be inspected.": {
    "en": "A data storage path could not be inspected.",
    "zh-CN": "无法检查数据存储路径。"
  },
  "Data storage directory is invalid.": {
    "en": "Data storage directory is invalid.",
    "zh-CN": "数据存储目录无效。"
  },
  "Data storage directory cannot use links or junctions.": {
    "en": "Data storage directory cannot use links or junctions.",
    "zh-CN": "数据存储目录不能使用符号链接或目录联接。"
  },
  "Data storage directory could not be resolved.": {
    "en": "Data storage directory could not be resolved.",
    "zh-CN": "无法解析数据存储目录。"
  },
  "Data storage category is unavailable.": {
    "en": "Data storage category is unavailable.",
    "zh-CN": "数据存储类别不可用。"
  },
  "A data file changed during scanning.": {
    "en": "A data file changed during scanning.",
    "zh-CN": "数据文件在扫描期间发生了变化。"
  },
  "Data storage options are invalid.": {
    "en": "Data storage options are invalid.",
    "zh-CN": "数据存储选项无效。"
  },
  "Data storage revision is stale.": {
    "en": "Data storage revision is stale.",
    "zh-CN": "数据存储版本已过期。"
  },
  "A data move is already pending.": {
    "en": "A data move is already pending.",
    "zh-CN": "已有数据迁移正在等待处理。"
  },
  "The active data root cannot be copied safely.": {
    "en": "The active data root cannot be copied safely.",
    "zh-CN": "无法安全复制当前数据根目录。"
  },
  "Staged data did not match its SHA-256 inventory.": {
    "en": "Staged data did not match its SHA-256 inventory.",
    "zh-CN": "暂存数据与其 SHA-256 清单不匹配。"
  },
  "Data storage revision is exhausted.": {
    "en": "Data storage revision is exhausted.",
    "zh-CN": "数据存储版本编号已耗尽。"
  },
  "The uncommitted destination changed and was retained for recovery.": {
    "en": "The uncommitted destination changed and was retained for recovery.",
    "zh-CN": "尚未提交的目标目录发生了变化，已保留以便恢复。"
  },
  "Data root could not be moved.": {
    "en": "Data root could not be moved.",
    "zh-CN": "无法迁移数据根目录。"
  },
  "Cleanup cannot run during a data move.": {
    "en": "Cleanup cannot run during a data move.",
    "zh-CN": "数据迁移期间无法执行清理。"
  },
  "Data cleanup token is stale.": {
    "en": "Data cleanup token is stale.",
    "zh-CN": "数据清理凭据已过期。"
  },
  "Temporary data changed after it was measured.": {
    "en": "Temporary data changed after it was measured.",
    "zh-CN": "临时数据在统计后发生了变化。"
  },
  "The default data directory contains unmanaged files.": {
    "en": "The default data directory contains unmanaged files.",
    "zh-CN": "默认数据目录包含不受管理的文件。"
  },
  "Legacy and new data roots overlap.": {
    "en": "Legacy and new data roots overlap.",
    "zh-CN": "旧数据根目录与新数据根目录重叠。"
  },
  "Legacy workspace cannot be copied safely.": {
    "en": "Legacy workspace cannot be copied safely.",
    "zh-CN": "无法安全复制旧工作区。"
  },
  "Legacy workspace copy failed SHA-256 verification.": {
    "en": "Legacy workspace copy failed SHA-256 verification.",
    "zh-CN": "旧工作区副本未通过 SHA-256 验证。"
  },
  "Data storage could not be initialized.": {
    "en": "Data storage could not be initialized.",
    "zh-CN": "无法初始化数据存储。"
  },
  "Data storage bootstrap is missing.": {
    "en": "Data storage bootstrap is missing.",
    "zh-CN": "缺少数据存储启动记录。"
  },
  "Managed data root identity is invalid.": {
    "en": "Managed data root identity is invalid.",
    "zh-CN": "受管理数据根目录的标识无效。"
  },
  "Filesystem roots cannot store app data.": {
    "en": "Filesystem roots cannot store app data.",
    "zh-CN": "不能将应用数据存储在文件系统根目录。"
  },
  "Data move roots cannot overlap.": {
    "en": "Data move roots cannot overlap.",
    "zh-CN": "数据迁移的源目录与目标目录不能重叠。"
  },
  "Data storage cannot overlap application resources.": {
    "en": "Data storage cannot overlap application resources.",
    "zh-CN": "数据存储目录不能与应用资源目录重叠。"
  },
  "Destination data directory must be empty.": {
    "en": "Destination data directory must be empty.",
    "zh-CN": "目标数据目录必须为空。"
  },
  "Data move transaction is stale.": {
    "en": "Data move transaction is stale.",
    "zh-CN": "数据迁移事务已过期。"
  },
  "Managed data changed before conservative removal.": {
    "en": "Managed data changed before conservative removal.",
    "zh-CN": "受管理的数据在安全移除前发生了变化。"
  },
  "Managed root identity changed before conservative removal.": {
    "en": "Managed root identity changed before conservative removal.",
    "zh-CN": "受管理根目录的标识在安全移除前发生了变化。"
  },
  "Cleanup path escaped the data root.": {
    "en": "Cleanup path escaped the data root.",
    "zh-CN": "清理路径超出了数据根目录。"
  },
  "Cleanup directory is invalid.": {
    "en": "Cleanup directory is invalid.",
    "zh-CN": "清理目录无效。"
  },
  "Cleanup cannot follow links or junctions.": {
    "en": "Cleanup cannot follow links or junctions.",
    "zh-CN": "清理不能沿符号链接或目录联接进行。"
  },
  "Desktop pet update is invalid.": {
    "en": "Desktop pet update is invalid.",
    "zh-CN": "桌宠更新请求无效。"
  },
  "Desktop pet update has invalid fields.": {
    "en": "Desktop pet update has invalid fields.",
    "zh-CN": "桌宠更新请求包含无效字段。"
  },
  "Desktop pet revision is invalid.": {
    "en": "Desktop pet revision is invalid.",
    "zh-CN": "桌宠版本无效。"
  },
  "Desktop pet mode is invalid.": {
    "en": "Desktop pet mode is invalid.",
    "zh-CN": "桌宠模式无效。"
  },
  "Desktop pet model selection is invalid.": {
    "en": "Desktop pet model selection is invalid.",
    "zh-CN": "桌宠模型选择无效。"
  },
  "The native picker did not return one absolute directory.": {
    "en": "The native picker did not return one absolute directory.",
    "zh-CN": "系统选择器未返回唯一的绝对目录路径。"
  },
  "Desktop Pet shutdown deadline is invalid.": {
    "en": "Desktop Pet shutdown deadline is invalid.",
    "zh-CN": "桌宠关闭期限无效。"
  },
  "Desktop pet preferences could not be loaded safely. The pet remains off.": {
    "en": "Desktop pet preferences could not be loaded safely. The pet remains off.",
    "zh-CN": "无法安全加载桌宠偏好设置。桌宠将保持关闭。"
  },
  "The previous bundled Desktop Pet was removed. Choose a local companion program folder before turning the pet on.": {
    "en": "The previous bundled Desktop Pet was removed. Choose a local companion program folder before turning the pet on.",
    "zh-CN": "原内置桌宠已移除。开启桌宠前，请选择本地桌宠程序文件夹。"
  },
  "Desktop pet program folder is invalid.": {
    "en": "Desktop pet program folder is invalid.",
    "zh-CN": "桌宠程序文件夹无效。"
  },
  "Desktop pet program selection is invalid.": {
    "en": "Desktop pet program selection is invalid.",
    "zh-CN": "桌宠程序选择无效。"
  },
  "Desktop pet preference document is invalid.": {
    "en": "Desktop pet preference document is invalid.",
    "zh-CN": "桌宠偏好设置文件无效。"
  },
  "Desktop pet program selection has no configured folder.": {
    "en": "Desktop pet program selection has no configured folder.",
    "zh-CN": "所选桌宠程序尚未配置文件夹。"
  },
  "Visible desktop pet preferences require a selected program.": {
    "en": "Visible desktop pet preferences require a selected program.",
    "zh-CN": "显示桌宠前需要选择一个程序。"
  },
  "Desktop pet runtime state is invalid.": {
    "en": "Desktop pet runtime state is invalid.",
    "zh-CN": "桌宠运行状态无效。"
  },
  "Desktop pet program-library status is invalid.": {
    "en": "Desktop pet program-library status is invalid.",
    "zh-CN": "桌宠程序库状态无效。"
  },
  "Desktop pet program-folder name is invalid.": {
    "en": "Desktop pet program-folder name is invalid.",
    "zh-CN": "桌宠程序文件夹名称无效。"
  },
  "Desktop pet program summary is invalid.": {
    "en": "Desktop pet program summary is invalid.",
    "zh-CN": "桌宠程序摘要无效。"
  },
  "Desktop pet preferences could not be read.": {
    "en": "Desktop pet preferences could not be read.",
    "zh-CN": "无法读取桌宠偏好设置。"
  },
  "Desktop pet preferences could not be saved.": {
    "en": "Desktop pet preferences could not be saved.",
    "zh-CN": "无法保存桌宠偏好设置。"
  },
  "Desktop pet preference path must be absolute.": {
    "en": "Desktop pet preference path must be absolute.",
    "zh-CN": "桌宠偏好设置必须使用绝对路径。"
  },
  "Desktop pet preferences changed elsewhere. Reload before saving.": {
    "en": "Desktop pet preferences changed elsewhere. Reload before saving.",
    "zh-CN": "桌宠偏好设置已在其他位置更改。请重新加载后再保存。"
  },
  "Choose a Desktop Pet program folder before selecting a program.": {
    "en": "Choose a Desktop Pet program folder before selecting a program.",
    "zh-CN": "选择桌宠程序前，请先选择程序文件夹。"
  },
  "Choose a Desktop Pet program before turning it on.": {
    "en": "Choose a Desktop Pet program before turning it on.",
    "zh-CN": "开启桌宠前，请先选择程序。"
  },
  "Desktop pet program-folder update is invalid.": {
    "en": "Desktop pet program-folder update is invalid.",
    "zh-CN": "桌宠程序文件夹更新请求无效。"
  },
  "Desktop Pet program": {
    "en": "Desktop Pet program",
    "zh-CN": "桌宠程序"
  },
  "A Desktop Pet file changed while it was scanned.": {
    "en": "A Desktop Pet file changed while it was scanned.",
    "zh-CN": "桌宠文件在扫描期间发生了变化。"
  },
  "A trusted Desktop Pet program file could not be opened.": {
    "en": "A trusted Desktop Pet program file could not be opened.",
    "zh-CN": "无法打开受信任的桌宠程序文件。"
  },
  "A trusted Desktop Pet program file has an unexpected size.": {
    "en": "A trusted Desktop Pet program file has an unexpected size.",
    "zh-CN": "受信任的桌宠程序文件大小与预期不符。"
  },
  "A trusted Desktop Pet program file changed while it was scanned.": {
    "en": "A trusted Desktop Pet program file changed while it was scanned.",
    "zh-CN": "受信任的桌宠程序文件在扫描期间发生了变化。"
  },
  "A Desktop Pet program directory could not be read.": {
    "en": "A Desktop Pet program directory could not be read.",
    "zh-CN": "无法读取桌宠程序目录。"
  },
  "A Desktop Pet program contains too many top-level entries.": {
    "en": "A Desktop Pet program contains too many top-level entries.",
    "zh-CN": "桌宠程序的顶层条目过多。"
  },
  "A Desktop Pet program contains a top-level link.": {
    "en": "A Desktop Pet program contains a top-level link.",
    "zh-CN": "桌宠程序的顶层目录包含链接。"
  },
  "A Desktop Pet program contains unreviewed executable code.": {
    "en": "A Desktop Pet program contains unreviewed executable code.",
    "zh-CN": "桌宠程序包含未经审阅的可执行代码。"
  },
  "The Desktop Pet configuration must be a JSON object.": {
    "en": "The Desktop Pet configuration must be a JSON object.",
    "zh-CN": "桌宠配置必须为 JSON 对象。"
  },
  "The Desktop Pet configuration is missing a required section.": {
    "en": "The Desktop Pet configuration is missing a required section.",
    "zh-CN": "桌宠配置缺少必需部分。"
  },
  "The Desktop Pet configuration has an invalid input mode.": {
    "en": "The Desktop Pet configuration has an invalid input mode.",
    "zh-CN": "桌宠配置的输入模式无效。"
  },
  "A Desktop Pet model manifest must be a JSON object.": {
    "en": "A Desktop Pet model manifest must be a JSON object.",
    "zh-CN": "桌宠模型清单必须为 JSON 对象。"
  },
  "A Desktop Pet model does not use the supported Cubism 3 format.": {
    "en": "A Desktop Pet model does not use the supported Cubism 3 format.",
    "zh-CN": "桌宠模型未使用受支持的 Cubism 3 格式。"
  },
  "A Desktop Pet model has an unsupported texture list.": {
    "en": "A Desktop Pet model has an unsupported texture list.",
    "zh-CN": "桌宠模型的纹理列表不受支持。"
  },
  "A Desktop Pet model has an unsupported expression list.": {
    "en": "A Desktop Pet model has an unsupported expression list.",
    "zh-CN": "桌宠模型的表情列表不受支持。"
  },
  "A Desktop Pet expression entry is invalid.": {
    "en": "A Desktop Pet expression entry is invalid.",
    "zh-CN": "桌宠表情条目无效。"
  },
  "A Desktop Pet model has an unsupported motion table.": {
    "en": "A Desktop Pet model has an unsupported motion table.",
    "zh-CN": "桌宠模型的动作表不受支持。"
  },
  "A Desktop Pet motion group is invalid.": {
    "en": "A Desktop Pet motion group is invalid.",
    "zh-CN": "桌宠动作组无效。"
  },
  "A Desktop Pet motion entry is invalid.": {
    "en": "A Desktop Pet motion entry is invalid.",
    "zh-CN": "桌宠动作条目无效。"
  },
  "A Desktop Pet model references too many files.": {
    "en": "A Desktop Pet model references too many files.",
    "zh-CN": "桌宠模型引用的文件过多。"
  },
  "The Desktop Pet image directory leaves its program folder.": {
    "en": "The Desktop Pet image directory leaves its program folder.",
    "zh-CN": "桌宠图像目录超出了程序文件夹。"
  },
  "A Desktop Pet model directory leaves its program folder.": {
    "en": "A Desktop Pet model directory leaves its program folder.",
    "zh-CN": "桌宠模型目录超出了程序文件夹。"
  },
  "A Desktop Pet model reference leaves its model folder.": {
    "en": "A Desktop Pet model reference leaves its model folder.",
    "zh-CN": "桌宠模型引用超出了模型文件夹。"
  },
  "A Desktop Pet program directory has an invalid name.": {
    "en": "A Desktop Pet program directory has an invalid name.",
    "zh-CN": "桌宠程序目录名称无效。"
  },
  "A directory inside the Desktop Pet folder could not be read.": {
    "en": "A directory inside the Desktop Pet folder could not be read.",
    "zh-CN": "无法读取桌宠文件夹中的某个目录。"
  },
  "The selected Desktop Pet folder contains too many entries.": {
    "en": "The selected Desktop Pet folder contains too many entries.",
    "zh-CN": "所选桌宠文件夹包含的条目过多。"
  },
  "The selected Desktop Pet folder contains too many programs.": {
    "en": "The selected Desktop Pet folder contains too many programs.",
    "zh-CN": "所选桌宠文件夹包含的程序过多。"
  },
  "The selected Desktop Pet folder is nested too deeply.": {
    "en": "The selected Desktop Pet folder is nested too deeply.",
    "zh-CN": "所选桌宠文件夹的嵌套层级过深。"
  },
  "Desktop Pet program folder must be an absolute path.": {
    "en": "Desktop Pet program folder must be an absolute path.",
    "zh-CN": "桌宠程序文件夹必须使用绝对路径。"
  },
  "The selected Desktop Pet program folder is unavailable.": {
    "en": "The selected Desktop Pet program folder is unavailable.",
    "zh-CN": "所选桌宠程序文件夹不可用。"
  },
  "Desktop Pet program folder must be a real directory.": {
    "en": "Desktop Pet program folder must be a real directory.",
    "zh-CN": "桌宠程序文件夹必须为实际目录。"
  },
  "Desktop Pet program folder must not be redirected.": {
    "en": "Desktop Pet program folder must not be redirected.",
    "zh-CN": "桌宠程序文件夹不能被重定向。"
  },
  "No trusted, complete Desktop Pet program was found in the selected folder.": {
    "en": "No trusted, complete Desktop Pet program was found in the selected folder.",
    "zh-CN": "所选文件夹中未找到完整且受信任的桌宠程序。"
  },
  "The selected folder contains ambiguous Desktop Pet program identities.": {
    "en": "The selected folder contains ambiguous Desktop Pet program identities.",
    "zh-CN": "所选文件夹中的桌宠程序标识存在歧义。"
  },
  "Desktop Pet program root is no longer valid.": {
    "en": "Desktop Pet program root is no longer valid.",
    "zh-CN": "桌宠程序根目录已失效。"
  },
  "Desktop Pet program root is no longer a real directory.": {
    "en": "Desktop Pet program root is no longer a real directory.",
    "zh-CN": "桌宠程序根目录已不再是实际目录。"
  },
  "Desktop Pet program root changed after it was scanned.": {
    "en": "Desktop Pet program root changed after it was scanned.",
    "zh-CN": "桌宠程序根目录在扫描后发生了变化。"
  },
  "Desktop Pet program selection does not belong to this scan.": {
    "en": "Desktop Pet program selection does not belong to this scan.",
    "zh-CN": "所选桌宠程序不属于本次扫描结果。"
  },
  "Desktop Pet program selection leaves its selected folder.": {
    "en": "Desktop Pet program selection leaves its selected folder.",
    "zh-CN": "所选桌宠程序超出了选定文件夹。"
  },
  "Desktop Pet launcher path changed after it was scanned.": {
    "en": "Desktop Pet launcher path changed after it was scanned.",
    "zh-CN": "桌宠启动程序路径在扫描后发生了变化。"
  },
  "Desktop Pet program identity changed after it was scanned.": {
    "en": "Desktop Pet program identity changed after it was scanned.",
    "zh-CN": "桌宠程序标识在扫描后发生了变化。"
  },
  "Desktop Pet program could not be started.": {
    "en": "Desktop Pet program could not be started.",
    "zh-CN": "无法启动桌宠程序。"
  },
  "Desktop Pet program did not start in time.": {
    "en": "Desktop Pet program did not start in time.",
    "zh-CN": "桌宠程序未能及时启动。"
  },
  "Desktop Pet program could not be closed safely.": {
    "en": "Desktop Pet program could not be closed safely.",
    "zh-CN": "无法安全关闭桌宠程序。"
  },
  "Desktop Pet program did not close in time.": {
    "en": "Desktop Pet program did not close in time.",
    "zh-CN": "桌宠程序未能及时关闭。"
  },
  "Choose a detected Desktop Pet program first.": {
    "en": "Choose a detected Desktop Pet program first.",
    "zh-CN": "请先选择已检测到的桌宠程序。"
  },
  "Desktop Pet program closed unexpectedly.": {
    "en": "Desktop Pet program closed unexpectedly.",
    "zh-CN": "桌宠程序意外关闭。"
  },
  "Windows system root is unavailable.": {
    "en": "Windows system root is unavailable.",
    "zh-CN": "Windows 系统根目录不可用。"
  },
  "Desktop Pet process PID is invalid.": {
    "en": "Desktop Pet process PID is invalid.",
    "zh-CN": "桌宠进程标识无效。"
  },
  "Desktop Pet process PID is unavailable.": {
    "en": "Desktop Pet process PID is unavailable.",
    "zh-CN": "桌宠进程标识不可用。"
  },
  "Desktop Pet process deadline is invalid.": {
    "en": "Desktop Pet process deadline is invalid.",
    "zh-CN": "桌宠进程期限无效。"
  },
  "Desktop Pet window could not be prepared.": {
    "en": "Desktop Pet window could not be prepared.",
    "zh-CN": "无法准备桌宠窗口。"
  },
  "Presence notification update is invalid.": {
    "en": "Presence notification update is invalid.",
    "zh-CN": "通知更新请求无效。"
  },
  "Presence notification update has invalid fields.": {
    "en": "Presence notification update has invalid fields.",
    "zh-CN": "通知更新请求包含无效字段。"
  },
  "Presence notification revision is invalid.": {
    "en": "Presence notification revision is invalid.",
    "zh-CN": "通知版本无效。"
  },
  "Reply notification preference is invalid.": {
    "en": "Reply notification preference is invalid.",
    "zh-CN": "回复通知偏好设置无效。"
  },
  "Presence reminder frequency is invalid.": {
    "en": "Presence reminder frequency is invalid.",
    "zh-CN": "陪伴提醒频率无效。"
  },
  "Notification preferences could not be loaded safely. All notifications remain off.": {
    "en": "Notification preferences could not be loaded safely. All notifications remain off.",
    "zh-CN": "无法安全加载通知偏好设置。所有通知将保持关闭。"
  },
  "Presence notification preference document is invalid.": {
    "en": "Presence notification preference document is invalid.",
    "zh-CN": "通知偏好设置文件无效。"
  },
  "Presence notification runtime is invalid.": {
    "en": "Presence notification runtime is invalid.",
    "zh-CN": "通知运行状态无效。"
  },
  "Notification preferences could not be read.": {
    "en": "Notification preferences could not be read.",
    "zh-CN": "无法读取通知偏好设置。"
  },
  "Notification preferences could not be saved.": {
    "en": "Notification preferences could not be saved.",
    "zh-CN": "无法保存通知偏好设置。"
  },
  "Notification preference path must be absolute.": {
    "en": "Notification preference path must be absolute.",
    "zh-CN": "通知偏好设置必须使用绝对路径。"
  },
  "Notification preferences changed elsewhere. Reload before saving.": {
    "en": "Notification preferences changed elsewhere. Reload before saving.",
    "zh-CN": "通知偏好设置已在其他位置更改。请重新加载后再保存。"
  },
  "Notification preference revision is invalid.": {
    "en": "Notification preference revision is invalid.",
    "zh-CN": "通知偏好设置版本无效。"
  },
  "Presence reminder cadence anchor is invalid.": {
    "en": "Presence reminder cadence anchor is invalid.",
    "zh-CN": "陪伴提醒的计时起点无效。"
  },
  "Notification preferences changed before the reminder was delivered.": {
    "en": "Notification preferences changed before the reminder was delivered.",
    "zh-CN": "通知偏好设置在提醒送达前发生了变化。"
  },
  "Desktop speech audio acknowledgement is invalid.": {
    "en": "Desktop speech audio acknowledgement is invalid.",
    "zh-CN": "桌面语音音频确认无效。"
  },
  "Trusted desktop speech playback failed.": {
    "en": "Trusted desktop speech playback failed.",
    "zh-CN": "受信任的桌面语音播放失败。"
  },
  "Voice Call request is invalid.": {
    "en": "Voice Call request is invalid.",
    "zh-CN": "语音通话请求无效。"
  },
  "Voice Call request has invalid fields.": {
    "en": "Voice Call request has invalid fields.",
    "zh-CN": "语音通话请求包含无效字段。"
  },
  "Voice Call identity is invalid.": {
    "en": "Voice Call identity is invalid.",
    "zh-CN": "语音通话标识无效。"
  },
  "Voice Call presentation is invalid.": {
    "en": "Voice Call presentation is invalid.",
    "zh-CN": "语音通话显示状态无效。"
  },
  "Voice Call action is invalid.": {
    "en": "Voice Call action is invalid.",
    "zh-CN": "语音通话操作无效。"
  },
  "Elysia is shutting down. Voice Call cannot open yet.": {
    "en": "Elysia is shutting down. Voice Call cannot open yet.",
    "zh-CN": "Elysia 正在关闭，暂时无法打开语音通话。"
  },
  "Voice Call main window is not available.": {
    "en": "Voice Call main window is not available.",
    "zh-CN": "语音通话主窗口不可用。"
  },
  "Voice Call window closed before it was ready.": {
    "en": "Voice Call window closed before it was ready.",
    "zh-CN": "语音通话窗口在就绪前关闭了。"
  },
  "Voice Call controls did not become ready.": {
    "en": "Voice Call controls did not become ready.",
    "zh-CN": "语音通话控件未能就绪。"
  },
  "Voice Call window could not open. Please try again.": {
    "en": "Voice Call window could not open. Please try again.",
    "zh-CN": "无法打开语音通话窗口，请重试。"
  },
  "Voice Call start time cannot change during a call.": {
    "en": "Voice Call start time cannot change during a call.",
    "zh-CN": "通话期间不能更改语音通话开始时间。"
  },
  "Voice Call control does not match the active call.": {
    "en": "Voice Call control does not match the active call.",
    "zh-CN": "语音通话控制与当前通话不匹配。"
  },
  "Voice Call control could not reach the main window.": {
    "en": "Voice Call control could not reach the main window.",
    "zh-CN": "语音通话控制无法连接主窗口。"
  },
  "Voice Call IPC rejected an untrusted renderer.": {
    "en": "Voice Call IPC rejected an untrusted renderer.",
    "zh-CN": "语音通话进程通信拒绝了不受信任的界面。"
  }
}
