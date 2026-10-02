# Elysia Live2D 对齐源层

本目录保存当前应用内 Cubism-compatible 模型的可审阅源层。全部 21 张 PNG 都使用同一张 `2048 × 2048` 透明画布和统一锚点。身体、服装和头发来自已确认的 `08-layer-separation-guide.png`；活动五官从 `../live2d-face-master.png` 的同一张中性正脸母版提取，避免独立生成部件造成眼睛、鼻子或嘴巴漂移。

这不是原始绘师 PSD，也不是可以反推出完整遮挡区域的官方工程。为了让模型能够实际眨眼、说话和显示克制的脸红，制作过程补充了闭眼、口腔和腮红层；这些补充层只能视为当前应用模型的一部分，不应被描述为官方角色原画。

## 层级

| 文件 | 用途 |
|---|---|
| `00_hair_back.png` | 身体后的长发 |
| `10_leg_l.png`, `11_leg_r.png` | 左、右腿 |
| `20_torso.png` | 躯干与主要服装 |
| `30_arm_l.png`, `31_hand_l.png` | 左臂与左手 |
| `32_arm_r.png`, `33_hand_r.png` | 右臂与右手 |
| `40_face_base.png` | 不含活动五官的面部底层 |
| `41_blush.png` | 仅在用户选择 `happy` 表情时使用的淡腮红 |
| `45_eye_l.png`, `46_eye_r.png` | 左、右睁眼层 |
| `45_eye_closed_l.png`, `46_eye_closed_r.png` | 左、右闭眼层 |
| `47_eyebrow_l.png`, `48_eyebrow_r.png` | 独立眉毛 |
| `49_nose.png` | 鼻部细节 |
| `50_mouth_cavity.png`, `51_mouth.png` | 张嘴口腔与绘制在其上方的闭合嘴线 |
| `60_hair_front.png` | 面部前方头发 |
| `70_accessory.png` | 发饰与最前方装饰 |

## 运行时关系

发布包只携带 `desktop/public/character/live2d/elysia/` 下的固定模型、纹理和最小清单，不携带这些制作源层。运行时通过封闭的 Character State、`neutral / happy / sad` 情绪和可信音频 RMS 嘴型提示修改参数；模型或回复文本都不能提供任意文件路径、参数名或动作脚本。

静态状态、表情、嘴型图集和立绘继续作为 Still、系统 Reduced Motion、WebGL/WASM 失败以及模型校验失败时的降级路径。

`scripts/build_elysia_live2d_face_layers.py` 可从审核母版确定性重建五官层，`scripts/check_live2d_face_alignment.py` 在 CI 中验证左右眼/眉高度、面部中轴、纵向顺序、嘴线与口腔关系以及全部统一画布尺寸。`scripts/build_elysia_live2d_bundle.py` 再通过固定提交的外部 `image2live2d` 编译器生成运行时三文件，并显式保证腮红位于脸之上、嘴线位于口腔之上、前发位于五官之上。
