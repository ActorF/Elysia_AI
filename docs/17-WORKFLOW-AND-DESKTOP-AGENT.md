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

## 只读执行图

`agent/work_graph.py` 的初始只读图已扩展为固定的 `START → admission → memory → rag → brain → approval → tool → verification → END` 路径。条件边只根据代码验证后的调用决定进入读取工具还是结束并要求批准。`WorkPlanRunner` 的实际读取分支使用这个图，写入先走建议和批准路由，再由原有预览与单次批准服务处理。

图只接收有界 JSON 调用和结果，取消函数放在进程内 context。读取前后都检查取消，验证节点再核对结果的路径与请求一致。递归上限、固定边和无自动重试限制执行；显式关闭 LangSmith tracing，避免环境变量意外把本地文件内容导出。依赖固定为 LangGraph 1.2.15。

Memory 使用当前 Chat/Project 范围，RAG 仅在创建计划时明确勾选 Project Sources 后运行，且仍使用原有来源权限与版本租约。两者每类最多八条记录，单条正文最多 4 KiB。Brain 只接收计划标题和有界上下文，生成最多 8 KiB 的只读建议，不写入 Chat 或长期记忆，也不把建议解析为调用。上下文按当前计划缓存，所有读取与缓存发布都重新检查所属范围和取消状态。

Plan 面板在原有执行事实之外展示当前节点、上下文数量和建议。模型不可用时明确显示未完成分析，显式工具仍由代码权限层处理；检索文本、模型回答和文件内容都不能更改工具调用或批准权。

## 恢复处置

安全的未开始步骤、被打断的读取和待批准预览可以转为暂停记录。恢复仍须重新核对当前 Chat、Project、Work 模式、目录身份和文件状态，并重新生成需要用户确认的写入预览。批准令牌不能跨重启成为权限。

已接纳、但结果尚未确认的写入标记为 `uncertain_write`。此类记录必须先检查原有事务审计及恢复副本，不能直接重放。已完成步骤保留其真实结果；终态不能被普通状态更新改回可执行状态。

这里的 JSON 合同是恢复的数据基础。磁盘保存、恢复入口和桌面交互随对应 Module 接入，不通过反序列化任意 Python 对象恢复执行。

## 参考

- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)：状态、节点、固定及条件边、START / END。
- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：恢复会重新进入被中断的节点，因此副作用不能依赖隐式重放。
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：进程内保存器不提供跨重启恢复。
