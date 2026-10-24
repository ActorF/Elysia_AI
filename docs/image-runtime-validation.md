# 图片运行环境与验收记录

此页统一保存图片运行环境的来源审计与实际生成证据。记录日期为 2026-10-16 至 2026-10-18，只代表当时的模型、硬件与版本；其中的本机路径是历史验收位置，不是安装 EXE 后自动具备的资源。部分旧模型和临时输出已清理，见下文对应记录。

日常安装与使用请先阅读[本地图片生成指南](16-LOCAL-IMAGE-GENERATION.md)。原始审计 JSON、来源清单与测试摘要继续单独保存，便于机器核验。

## 运行环境审计

本次审计针对独立的 `models/cache/image-acceptance` 安装，不修改项目 Python 环境，不导入 Torch/ComfyUI、不启动推理，也不把依赖静态检查当成 GPU 验收。目标解释器的启动仍依赖其已经审核的 `_pth`/`site` 配置。

## 本次结果

完整快照保存在 [image-runtime-audit-2026-10-16.json](image-runtime-audit-2026-10-16.json)，生成时间为 `2026-10-16T19:38:14.774406+00:00`。

| 检查 | 结果 |
| --- | --- |
| Python / pip | 3.13.14 / 26.2.1 |
| Distribution | 89 个；`pip inspect` 与独立 metadata 清单一致 |
| `pip check` | `No broken requirements found.` |
| RECORD SHA-256 | 45,733 个唯一文件、4,334,040,326 字节通过；无 Hash 或尺寸不匹配 |
| 预期空摘要 | 15,369 个 RECORD 自身或字节码条目；其他空摘要文件为 0 |
| 未记录文件 | 0；未记录字节码不纳入该数字 |
| 来源 Pin | 8 个 Archive/模型/许可/Wheel/本机配置摘要全部匹配；ComfyUI Revision 匹配且受版本控制文件无修改 |
| OSV | 92/92 个精确/公开版本查询完成；命中包 0 |
| Manifest 发现 | `torchvision==0.29.0+cu130` 的 RECORD 有 198 个规范化重复路径 |

本次命令返回 `1`，保留上述 Manifest 发现。重复来自 `/` 与 `\` 两种分隔符：197 个非 RECORD 文件同时存在空摘要行与带摘要行，所有带摘要行均已独立验证，没有互相冲突的摘要；另一个重复是 RECORD 自身。工具没有修改、去重或重新安装 torchvision，也没有把这份安装称为没有审计发现的完整认证。

## 可复现输入

固定来源、模型许可和本机修改记录在 [image-runtime-provenance.json](image-runtime-provenance.json)。源码与依赖树审计和真实生成测试分别记录，互不替代。

| 对象 | 复核输入 |
| --- | --- |
| ComfyUI | 官方 `v0.41.0`，Commit `98cc0bf84b7cb1383654c5b402e7612d875d6d22` |
| NVIDIA Portable Archive | SHA-256 `7ff446d6904a9eecdfa07870a06238cc33a6204dcd31726edc6783efd080ac34` |
| SDXL Base 1.0 | 官方仓库 Revision `76d28af79639c28a79fa5c6c6468febd3490a37e`，Checkpoint SHA-256 `31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b` |
| Embedded Python | 安装随附 Python 3.13.14；具体版本字符串写入审计 JSON |
| 本机 `_pth` 调整 | 原始 SHA-256 `5caee11a202b73328941593a3bea1a0540ddafc15a2fafc939be4520dd96e381`；审核后 `2d517fda409ddcaa916ffb15d73ec87161aa39a5512609aba85eab45a81895b9` |

`_pth` 仅改为准确的绝对 ComfyUI、Python Zip、解释器和 site-packages 根，唯一执行行仍是 `import site`。这是本机受信安装调整，不把这些依赖路径当成每次生成请求的输入，也不声称官方 Archive 原样包含这一修改。

Portable 原始安装的 `pip check` 缺少三个已经声明的模板资源依赖：`comfyui-workflow-templates-media-image==0.3.160`、`…-other==0.3.229`、`…-video==0.3.101`。本次只向该独立安装补齐三份 universal wheel；每份先核对官方 PyPI JSON SHA-256，再以 `--no-index --no-deps` 安装。精确 Wheel 摘要、版本和来源保存在 provenance 文件；项目 `.venv` 未安装或升级依赖。

官方来源：[ComfyUI Release](https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.41.0)、[SDXL Checkpoint](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/76d28af79639c28a79fa5c6c6468febd3490a37e/sd_xl_base_1.0.safetensors)、[Python 模块路径说明](https://docs.python.org/3.13/using/windows.html#finding-modules)。

## 复现命令

在 CMD 中从仓库根运行：

```bat
.venv\Scripts\python.exe scripts\audit_image_runtime.py ^
  --python models\cache\image-acceptance\ComfyUI_windows_portable\python_embeded\python.exe ^
  --acceptance-root models\cache\image-acceptance ^
  --provenance docs\image-runtime-provenance.json ^
  --output docs\image-runtime-audit-2026-10-16.json ^
  --query-osv
```

工具执行有界的 `pip check`/`pip inspect`，交叉核对安装身份，读取每个 `.dist-info` 的许可与依赖声明，并核验 `RECORD` 中的 SHA-256、尺寸、文件身份及目录身份。合法的 Scripts/share 相对条目可在解释器根内读取；逃逸、静态链接、reparse、多硬链接、文件变化、未知摘要与未记录文件均有可见结果。正常的空摘要 RECORD 自身和字节码单独计数。目录检查按身份缓存并在结束时复核，审计要求安装在检查期间保持稳定，不提供抵抗恶意并发替换的原子文件系统快照。

默认每次文件清单最多 512 个 Distribution、500,000 个清单/扫描条目、单文件 8 GiB、累计读取 64 GiB、20 分钟；Provenance 哈希另有相同的有界预算。子命令有输出与时间预算。`pip inspect` 显式使用 UTF-8，避免 Windows ANSI 编码截断非 ASCII 元数据。工具不安装、升级或修复运行环境。

返回码 `0` 要求依赖关系、身份交叉检查、文件和来源摘要全部通过，OSV 全量查询完成且没有命中；`1` 表示报告已写入但存在发现、遗漏或未请求/未完成的漏洞查询；`2` 表示无法完整审计。离线执行可省略 `--query-osv`，报告会明确记录 `not-requested`。

## 许可与覆盖范围

ComfyUI 随附 [GPL v3 文本](https://github.com/Comfy-Org/ComfyUI/blob/98cc0bf84b7cb1383654c5b402e7612d875d6d22/LICENSE)；SDXL 的固定版本使用 [CreativeML Open RAIL++-M](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/76d28af79639c28a79fa5c6c6468febd3490a37e/LICENSE.md)，附有使用限制和分发条件。包的 License/License-Expression、Classifier、License-File 与核验过的随附许可文件均进入完整 JSON 清单。元数据缺失、长许可文本和混合许可不能被简化成统一的“允许分发”。当前仓库不分发该 Portable、Checkpoint 或运行时依赖。

本次两个包没有 License/License-Expression 或许可 Classifier：`comfy-aimdo` 和 `comfyui_frontend_package`。`comfy-aimdo` 的随附 LICENSE 已通过 RECORD 核验，正文声明 GPL v3；[对应版本上游文件](https://github.com/Comfy-Org/comfy-aimdo/blob/v0.5.6/LICENSE) 可独立复查。Frontend Wheel 的 [PyPI 元数据](https://pypi.org/pypi/comfyui-frontend-package/1.55.17/json) 也未声明许可；[同版本上游 LICENSE](https://github.com/Comfy-Org/ComfyUI_frontend/blob/v1.55.17/LICENSE) 是 GPL v3 文本。这些人工补充没有改写安装元数据，也不认证 Wheel 所有前端第三方组件。另有 15 个包没有在已核验的 `.dist-info` 条目中检出许可文件，完整清单仍保留其实际声明；不能由此推导统一的分发许可。

`RECORD` 是安装器提供的本地清单，能够发现清单与文件不一致，不能证明清单没有与文件一同被篡改，也不等同于发布者签名。它不认证未记录文件、空摘要字节码、ComfyUI 的任意未来修改或所有 CPython/原生 DLL 的组成与许可。Archive 与模型摘要只把本次文件关联到审核过的来源。

漏洞查询使用 [OSV 官方 Batch API](https://google.github.io/osv.dev/post-v1-querybatch/)，提交包名和精确版本，处理分页；带 CUDA 本地版本标签的包同时查询其公开版本。服务不可用、形状错误、超时或分页超过预算都明确记为 `incomplete`。命中 ID 保留到报告并链接原始记录；查询结果只是生成时点的 PyPI 数据库视图，不能覆盖未知漏洞、驱动、CPython 与所有 bundled CUDA/FFmpeg/OpenSSL 等原生组件。

`pip check` 通过只证明已声明的 Python 版本约束当前可满足。实际 ComfyUI 启动、生成、取消、失败、关闭、显存峰值、残留和持续运行由独立真实验收报告记录。

## 2026-10-18 用户指定的本地资产

本次根据用户明确指定的两个本机文件导入，不从仓库分发或重新发布模型。完整 SHA-256 与发布者版本 API 的文件摘要逐项匹配；安全格式检查确认第一个是 SDXL Checkpoint，第二个是 SDXL LoRA。原文件保留，私有副本以摘要命名；既有 SDXL Profile 和默认模型不变。

| 资产 | 发布者版本与 SHA-256 |
| --- | --- |
| WAI Illustrious SDXL v8.0，6,938,040,682 字节 | [WAI0731，版本 1183765](https://civitai.com/models/827184?modelVersionId=1183765)；`63e5c28bf8ae348136c9f9cdbfde14d8a4a5daa13306e09a7752104b2fae2006` |
| Hikari Elysia IL v1，28,947,460 字节 | [hi_ka_ri，版本 1318587](https://civitai.com/models/774891?modelVersionId=1318587)；`06ba4cd1cfb9b2e9ce181299f2546fc1db4cb64bab8a3c8ceb0aa108075a9e18` |

发布页在复核时声明：WAI 允许 Image 与 RentCivit 范围的商业使用，允许衍生；Hikari 要求署名，商业许可仅列 RentCivit、不允许衍生，说明禁止转载。两个来源声明分别保存在其受信 Profile/LoRA 中，没有套用 SDXL Base 的统一许可。上述为发布者当时的条款记录，使用与分发仍应核对对应版本页面；摘要关联文件，不证明发布者拥有全部底层权利。原始 API 快照只保留在本机忽略目录。

该 LoRA 使用 ComfyUI 内置 [LoraLoader](https://github.com/Comfy-Org/docs/blob/main/built-in-nodes/LoraLoader.mdx)，未增加 Custom Node、依赖包或元数据触发词。导入与选择不改变现有原创、虚构、明确成年、单人生成合同；没有新增内容模式。

## Elysia 专用配置与风格 LoRA

用户随后明确改为仅使用成年虚构 Elysia，主模型只保留 WAI，默认 Profile 改为 `wai-illustrious-v80`；Hikari 作为角色 LoRA，新增 `honkai-starrail-style-v1` 风格 LoRA，可单独或同时选择。Elysia 使用独立的固定身份合同，不再以原创角色确认代表该身份，仍没有内容模式。

新增本机 `Honkai_Starrail_Style_Lora.safetensors` 为 SDXL/Illustrious LoRA，57,447,636 字节，完整 SHA-256 `0b1876635cfc93bbab11731a224472a5316b05cc4dd3213370a868d0e944c18b` 与 [FlyingOyster 发布版本 1852115](https://civitai.com/models/1636274?modelVersionId=1852115) 的文件记录完全匹配。发布页复核时允许不署名、商业图像/托管/模型销售及衍生；记录保存对应版本来源和条款摘要，不将其推导成对底层全部权利的认证。它使用相同内置 Loader，不增加安装依赖，也不读取元数据中的训练标签作为指令。

用户已逐项批准清理旧 SDXL Base、已安装运行时的下载包和五个旧临时验收目录，共 332 个文件、9,314,432,892 字节。当前唯一使用中的 ComfyUI/Python、WAI、两个 LoRA 私有副本与三个原文件保留。正式 Gallery 四份文件在操作前后逐字节摘要一致。旧下载包与验收原始 PNG 已删除，因此上文审计/旧 Soak 为历史记录；复现其完整来源核验需恢复对应固定摘要的输入。清理回执及变更前配置备份保留在本机忽略目录 `tmp/elysia-studio-acceptance/operator-bda887d6610149609c82f87455b07a7d`。

## 真实环境验收

2026-10-16 基线验收使用 Windows、RTX 4070 SUPER 12 GiB、官方 ComfyUI v0.41.0
便携运行时和官方 SDXL Base 1.0。安装、模型、运行目录和 Gallery 均放在
忽略的验收目录，未覆盖用户的正式图片配置。来源、固定摘要、许可证和
依赖清单见 [运行时审计](#运行环境审计)。

## 历史基线安装位置

以下是 2026-10-16 基线验收所使用的位置，旧 SDXL Checkpoint 已在后续清理中删除。新安装应在设置页选择自己的 ComfyUI、Python 和模型：

| 项目 | 路径 |
| --- | --- |
| ComfyUI 根目录 | `D:\Elysia_AI\models\cache\image-acceptance\ComfyUI_windows_portable\ComfyUI` |
| Python 解释器 | `D:\Elysia_AI\models\cache\image-acceptance\ComfyUI_windows_portable\python_embeded\python.exe` |
| 模型根目录 | `D:\Elysia_AI\models\cache\image-acceptance\models` |
| Checkpoint | `D:\Elysia_AI\models\cache\image-acceptance\models\checkpoints\sd_xl_base_1.0.safetensors` |

该 Checkpoint 的来源填写官方
[SDXL Base 1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0)，
许可证声明为 `CreativeML Open RAIL++-M`，Prompt Dialect 使用 SDXL Natural。
正式应用仍通过设置页的文件选择与原生确认流程保存受信安装配置。

## 实际发现并修复的兼容性问题

Windows `CreateProcessW` 拒绝 `\\?\Volume{GUID}\...` 形式的可执行文件名，
返回错误 87。共享 Windows launcher 仅把经过严格规范检查的可执行文件前缀
转换成指向同一卷的 `\\.\Volume{GUID}\...`，同时同步命令行首项。
转换不经过盘符；解释器、ComfyUI 入口和 Checkpoint 仍通过原有文件 Guard
保持固定文件身份。脚本参数和模型路径继续使用受保护的卷 GUID 路径。
含 `..`、空分量、尾部空格/句点、ADS 或 DOS 保留名的转换请求会被拒绝。

CPython 3.13 的现代 DLL 搜索标志 `0x1100` 不接受卷 GUID 形式的 `.pyd`
依赖路径。便携版原有的相对 `._pth` 声明因此无法直接配合卷 GUID 解释器启动。
明确的安装准备只将已审核的 Python 根目录及同一便携安装中的 ComfyUI 目录
写成普通绝对依赖路径，并显式列出 `Lib/site-packages`；唯一允许的执行指令
是原有的 `import site`。原始文件与准备后的文件摘要均已记录在来源审计中。
静态 readiness 会拒绝尚未准备的嵌入环境及 Windows venv redirector，并给出
路径不可见的操作说明。

这是安装依赖的信任边界：依赖树由安装者审核，并由正常的 Windows/CPython
加载机制解析，DLL 搜索标志保持 `0x1100`。原生 Guard 的固定文件身份保证
覆盖解释器、入口和 Checkpoint 三项；依赖 DLL、包文件及其盘符映射属于
上述受信安装边界。

ComfyUI 的内部根相对路径规范化不能以卷 GUID 目录作为 Windows 当前目录。
进程当前目录改成此次任务拥有并重新检查过的私有运行目录，与输入、输出及
缓存目录使用同一普通路径约定。Windows 子进程在运行时持有其当前目录。
`PrivateRunDirectory` 沿用普通目录和所有权检查；三个核心文件的原生 Guard
保持上述规则。

## 已完成的真实 Smoke

第一张图使用原创、纯虚构、明确 30 岁的单人角色描述：银发女性、白色连衣裙、
安静花园、柔和插画。固定 Workflow、1024 × 1024、Seed 42、8 Steps。
生成总计 61.210 秒，经过 `sampling → verifying → completed`，最终 PNG
1,714,939 字节，SHA-256：

```text
f3d4c2f9a63621f5c3f015ac9b5cfaabc52428976d5c47aa24fb1b50323ae873
```

已目视检查输出。Gallery 提交后才发布 100%，重新打开的规范 PNG 通过尺寸与
摘要检查。任务结束后私有运行根为空，原有无关 GPU 进程保留。

最终执行了真实 `BackendProcess` 桥接验收，耗时 130.976 秒，覆盖：

- 正式 Backend 启动、图片 capability 和 Main GPU 准入。
- `image.generate`、Gallery 提交及关联进度。
- 私有 fd4 二进制 PNG、摘要校验和一次性 Preview URL。
- Main 持有的原子导出，Renderer 仅提交 Artifact ID。
- 应用服务报告 `sampling` 后取消，确认收到取消请求 ACK 和明确的取消领域错误，未增加 Gallery 记录。
- Gallery 删除、Backend 关闭和全局 GPU Lease 释放。

桥接报告记录导出 PNG 为 1024 × 1024、1,626,886 字节，SHA-256：

```text
dffe0af67aabb39e9b820910bc9b89574e2026860803f72cb9c6da0dfaebabeb
```

## 实际 Electron 图片显示

另外启动隔离的 Electron 43.7.7 Renderer，使用生产协议 Handler、生产 CSP、
二进制 Reader 和 Preview Owner，实际将前述 1024 × 1024 PNG 绘制到 `<img>`。
检查了分片 `ELYSIMG1` 帧、原生 PNG 解码、响应摘要、首读成功、重读/刷新后
失效、撤销、过期，以及 POST 请求拒绝且保留可用能力。上下文隔离与 Sandbox
开启，Node Integration 关闭；硬件加速关闭，因此该步骤未使用推理 GPU。

该步骤验证独立 Renderer 的真实自定义协议和图片绘制。完整 React 页面交互
由 UI 测试覆盖，完整 Backend/模型链路由桥接验收覆盖。原始报告和窗口截图
保留于 `tmp/image-preview-acceptance/real-render-005/report.json` 与 `preview.png`；
最终报告核对了桥接报告中的显式尺寸及当前生产源码摘要。

## 重复生成与恢复验收工具

2026-10-16 的实际长跑全部通过，摘要保存在
[image-runtime-smoke-2026-10-16.json](image-runtime-smoke-2026-10-16.json)。

| 项目 | 实测结果 |
| --- | --- |
| 生成窗口 | 1,823.349 秒（30 分 23.349 秒） |
| 全套验收 | 1,971.305 秒（32 分 51.305 秒） |
| 输出 | 连续 34 张，1024 × 1024，28 Steps，Seed 42–75 |
| 每张生成耗时 | 最短 52.455 秒，最长 58.835 秒，平均 53.628 秒 |
| PNG 校验 | 34 个不同摘要，全部通过 Gallery 重新读取校验 |
| 取消 | 应用服务报告 sampling 后等待 4 秒请求取消，结果 requested，无 Gallery 提交 |
| 损坏模型 | 真实子进程产生 ImageGenerationFailedError，私有目录清理完成，无 Gallery 提交 |
| 关闭 | 跨线程取消并排空，排空耗时 0.110 秒，私有目录清理完成 |
| 保留与恢复 | 未知运行目录被拒绝并保留，精确清理工具自己的合成夹具后，34 份 Gallery 结果完整保留 |
| 后代进程 | 独立观察到 74 个后代 PID，结束时残留 0，观察无失败 |
| 全卡显存 | 基线 2,486 MiB，峰值 11,380 MiB，末次 2,479 MiB，总量 12,282 MiB |

全套时间为 `2026-10-16T19:32:59.865272Z` 至
`2026-10-16T20:05:51.167514Z`。原始报告、逐张 PNG 和私有 Gallery 保留于
`tmp/image-real-acceptance/58dbf4cfc9c14b829a2b31d12187c302`。
表中的显存是全卡数据，含验收之前已存在的其他应用；3,572 次采样用于报告
该观察区间的峰值，不表示图片进程独占的显存用量。

在已验证并完成显式 Python 环境准备的安装上运行：

```bat
.venv\Scripts\python.exe scripts\smoke_local_image_generation.py --cycles 3 --soak-seconds 1800 --steps 28
```

工具每次在 `tmp/image-real-acceptance/<uuid>` 建立独立数据根，记录逐张生成耗时、
完整 Gallery PNG 校验、应用服务报告 `sampling` 后等待 4 秒并提交取消、损坏
Checkpoint 的真实执行失败、
跨线程关闭与排空。另用明确属于工具的合成孤留目录检查运行时拒绝未知私有状态，
确认保留内容后仅删除该工具自己创建的精确标记文件和空目录，再验证 Gallery
重新打开后的持久化数据。

此命令使用生产 Application Service 组合；Main 的跨进程 GPU 准入和二进制
桌面协议已由前述 Backend 桥接验收另行覆盖。

未知真实运行目录不会被自动接管或清空。对真实硬崩溃留下的目录，需要先确认
没有存活的任务，再走原生确认的隔离恢复流程；本工具的合成恢复检查不等同于
自动恢复。

## 原生隔离恢复的真实 Windows 验收

使用已关闭的桥接验收数据根另行执行真实 `ImageSettingsManager`、真实 Python
Worker 和原生 `MoveFileExW` 隔离恢复，未启动推理。测试拥有的一个 UUID 运行目录
含两份文件、共 1,626,950 字节，其中包含前述真实导出的 PNG。
隔离后两个文件逐字节保留，PNG 摘要仍为 `dffe0af6…faebabeb`，Gallery Index
逐字节保持一致，readiness 从 `recovery_required` 变成 `ready`。

产品设置页提供“隔离保留并恢复图片生成”入口。Main 先预览遗留状态，获得原生
确认后停止并等待 Backend 退出，再由 Worker 取得 Gallery Lease，将固定私有
运行根中的遗留任务移动到固定隔离目录。它保留图片和其他私有文件，
不会删除 Gallery，也不允许 Renderer 提交恢复路径。并发进程持有 Gallery Lease
时，恢复会被拒绝。

将 `ELYSIA_IMAGE_ACCEPTANCE_ROOT` 设为已经完成桥接验收且带专用标记的独立
数据根后，可重复执行元数据恢复验收：

```bat
cd desktop
npm run smoke:image-recovery -- --data-root "%ELYSIA_IMAGE_ACCEPTANCE_ROOT%"
```

该命令由操作员给生产 Main Manager 提供确认适配器。确认与取消的调用分支由
UI/Manager 测试覆盖；本次未自动点击真实操作系统对话框。验收报告位于该数据根的
`backend-smoke/recovery-report.json`，明确记录这个确认边界。

## 采样和时长边界

GPU 采样报告全卡已用显存的基线、峰值和末次值，其中包含原有其他应用；
不能将它解释为图片任务独占的峰值。独立 Windows 进程快照记录本工具观察到的
后代进程，即使中间父进程已结束，仍检查已观察到的后代是否残留。真正的所有权
与停止证明仍来自原生 Job Object，而非 PID 名称匹配。

`--soak-seconds` 的范围为 0–3600，最多生成 50 张；达到数量上限但未达到请求
时长会报告失败。实际耗时和全部结果写入验收数据根中的 `acceptance.json`。
验收不得被描述成 24 小时压力测试或对其他 GPU/驱动/模型的全面保证。

## 2026-10-18 WAI 与可选 LoRA 桥接验收

用户明确指定的 Checkpoint/LoRA 已导入正式配置的已有模型根，保留两份原文件、原 SDXL Profile 和默认模型。正式配置仅增加 `wai-illustrious-v80` 与其 `hikari-elysia-il-v1` 目录项，Revision 1→2。来源、精确摘要和各自条款见[资产补充审计](#2026-10-18-用户指定的本地资产)。此导入采用用户对给定文件的明确授权，未伪称自动点击原生确认窗口；日常产品流程由 Main 原生选择和确认，相关分支由 Manager/UI 测试覆盖。

真实生成仍使用独立的 `tmp/image-lora-acceptance/3014d71d3d4d42b1b3f9fda5ee19a13c` 数据根，没有向正式 Gallery 添加或删除图片。沿用同一 ComfyUI 安装、1024×1024、28 Steps、Seed 420042，提示词为原创虚构、明确 28 岁、穿完整外套/衬衫/长裙的花园人物，没有角色触发词。分别运行生产 Backend/Main GPU Broker/私有 fd4/原生导出链路：

| 用例 | 结果 | 全套耗时 | 规范 PNG |
| --- | --- | --- | --- |
| WAI，不使用 LoRA（v1） | 生成、提交、预览摘要、一次性 URL、原子导出、取消无提交、删除与关闭通过 | 216.282 秒 | 1,479,664 字节；`be797f20b2c893c0e6b7512d576189161eeac81b9c903aedb3b4b1c2c96ef37a` |
| WAI + Hikari（v2，模型/CLIP 强度各 1） | 同一套链路全部通过 | 546.319 秒 | 1,414,759 字节；`5047bf510f312b68a639ff598f7e93d258cc1be21540211314909320b7e525a2` |

两个 PNG 均已目视检查。LoRA 开启样本出现重复人物构图：技术验收验证了所选资产进入实际推理和图片完整性，并不证明模型会严格满足人物数量、年龄或身份语义。现有文本合同不包含输出图像分类器；导入 LoRA 不应被解释为已实现角色锁定或输出语义认证。

另外通过生产 `ImageSettingsManager` 检查正式双模型配置，并仅启动/关闭正式 Backend，不生成或改动画廊：Readiness 71.330 秒、Backend Ready 111.805 秒，均在现有 120/180 秒期限内；两份 Profile、LoRA 可用状态、Revision 2 和图片 Capability 均匹配。检查后已排空 Backend 和 GPU Coordinator。

启用 LoRA 的整段观察得到 303 次全卡显存采样：基线 3,177 MiB、峰值 11,196 MiB、末次 2,438 MiB，总量 12,282 MiB，包含其他应用。独立观察到 9 个本次后代 PID，结束时残留 0、观察无失败；两个独立运行根均为空，Main GPU Lease 均释放。原始证据分别为 `off/backend-smoke/report.json`、`on/backend-smoke/report.json` 和 `on/gpu-report.json`。全套耗时包括启动检查、成功生成和另一次取消用例，不是单张推理耗时；本次没有重复长时间 Soak，也没有重新认证全部依赖。

操作员可在明确拥有 `.image-acceptance-root` 标记、已配置的独立数据根复现：

```bat
cd desktop
npm run smoke:image-backend -- --data-root <独立验收根> --profile wai-illustrious-v80 --preset square
npm run smoke:image-backend -- --data-root <另一个独立验收根> --profile wai-illustrious-v80 --preset square --lora hikari-elysia-il-v1
```

## Elysia 专用界面与双 LoRA 实测

用户随后选择只保留 WAI 主模型，以 Hikari 作为角色 LoRA、Honkai Star Rail 作为风格 LoRA。正式配置现为 Revision 3；旧 SDXL、运行时下载包和五个旧验收目录已按逐项确认的清单删除，正式画廊及正在使用的 ComfyUI 保留。清理范围和来源见[运行时审计补充](#elysia-专用配置与风格-lora)。上文旧报告路径为历史位置，不再承诺原始临时 PNG 仍存在。

新生成页固定成年虚构 Elysia，主要显示画面描述、尺寸和 LoRA 多选/强度，其他参数折叠；设置页正常导入仅需选择文件。截图检查了桌面与窄窗口布局，完整 Playwright 346 项全部通过，Python 全套 5,022 项通过；后续缺失文件恢复的独立测试另行覆盖。普通导入没有 ID/来源/许可表单，高级安装修复仍折叠保留原入口。

真实双 LoRA 通过生产 Backend、Main GPU Coordinator、固定 v3 Loader 串联和 fd4。独立根为 `tmp/elysia-studio-acceptance/real-168b085346aa438083bdc3f2ebfc44d2`，与正式 Gallery 分离。首轮模型/CLIP 强度各 1 的完整链路通过，194.151 秒；独立进程观察记录 9 个后代 PID、结束残留 0。GPU 仅测了全卡首末值 2,579/2,927 MiB，没有采样本轮峰值，不能推算单进程显存。

首轮及一次构图调整样本出现重复人物；最终增加仅针对 Elysia 的单画面/数量标签，角色强度 0.8、风格强度 0.7，以同一 Seed 420042、1024×1024、28 Steps 再验证。最终样图目视为一个粉发紫眼角色，规范 PNG 1,599,162 字节，SHA-256 `9ab124706af889b491de81342804f9c68f2ea082803125693e2b2dbb6b0d1d79`。一次样本不证明随机输出总能满足人物数量、身份或年龄语义，也不能将前后差异只归因于某一项参数。

最终完整链路 139.244 秒，覆盖生成/提交、PNG 重验、一次性预览、原子原生导出、另一任务取消无提交、Gallery 删除、GPU Lease 释放与 Backend 关闭。导出 PNG 与 `backend-smoke/report.json` 保留于该独立根；早期独立进程观察在 `ownership-report.json`，其明确属于强度 1/1 的首轮。当前私有运行根为空，未向正式 Gallery 添加或删除图片。

```bat
cd desktop
npm run smoke:image-backend -- --data-root <独立验收根> --profile wai-illustrious-v80 --preset square --character elysia --loras hikari-elysia-il-v1,honkai-starrail-style-v1 --lora-strengths 0.8,0.7
```

此命令仍要求显式隔离标记，会修改所给验收 Gallery；不能指向正式用户数据根。本次没有重新执行长期 Soak，也未引入输出图像分类器。
