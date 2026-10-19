# 本地图片运行环境审计

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
