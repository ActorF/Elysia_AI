# Work 工作流与桌面能力

Stage 10 在 Stage 9 的显式计划、目录边界和单次写入批准之上加入工作流。现有 `work.plan` 仍是界面显示的执行事实；版本化工作流记录补充请求来源、节点位置和恢复信息。人格 Markdown、图片生成、Chat 消息与私有 Work 数据各自保留原有职责。

## 版本化状态

`agent/workflow_state.py` 定义版本 1 的严格 JSON 合同。未知版本、额外字段、重复步骤、越界内容、不匹配的工具结果或改变原始调用的快照均被拒绝。状态上限为 768 KiB，计划最多 16 步。

| 数据 | 含义与边界 |
| --- | --- |
| Request | 唯一请求、Chat、可空 Project、Work 模式、标题、时间、目录身份摘要和有序工具调用摘要。 |
| Plan / Step | 沿用 Stage 9 的计划、步骤、调用、结果和当前待确认预览。 |
| Cursor | 源码定义的节点名称与当前步骤，不能由模型生成任意跳转。 |
| Result / Error | 终态摘要和固定错误词表，不保存原始异常或重复文件正文。 |
| Admitted step | 区分正在生成预览与已进入本地写入的步骤。取消不能把已经执行的写入伪装成未执行。 |
| Checkpoint | 唯一记录、父记录、保存时间、恢复处置和强制重新验证标志。 |

目录身份摘要包含受信边界的规范路径及原生目录身份。原生 ID 以十进制字符串参与摘要，避免 JavaScript 数字精度损失。摘要只能用来比较；它不是路径，也不能恢复文件系统权限。进程内的线程、锁、取消函数和物理忙碌状态不进入持久化合同。

## 恢复处置

安全的未开始步骤、被打断的读取和待批准预览可以转为暂停记录。恢复仍须重新核对当前 Chat、Project、Work 模式、目录身份和文件状态，并重新生成需要用户确认的写入预览。批准令牌不能跨重启成为权限。

已接纳、但结果尚未确认的写入标记为 `uncertain_write`。此类记录必须先检查原有事务审计及恢复副本，不能直接重放。已完成步骤保留其真实结果；终态不能被普通状态更新改回可执行状态。

这里的 JSON 合同是恢复的数据基础。磁盘保存、恢复入口和桌面交互随对应 Module 接入，不通过反序列化任意 Python 对象恢复执行。

## 参考

- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)：状态、节点、固定及条件边、START / END。
- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：恢复会重新进入被中断的节点，因此副作用不能依赖隐式重放。
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：进程内保存器不提供跨重启恢复。
