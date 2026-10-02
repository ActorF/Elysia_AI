# Elysia Live2D 对齐源层

本目录保存当前应用内 Cubism-compatible 模型的可审阅源层。所有 PNG 都使用同一张 `1024 × 1024` 透明画布和统一锚点；文件名的数字前缀同时表示默认绘制顺序。它们来自已确认的 `08-layer-separation-guide.png`，并以 `01-character-turnaround.png` 与 `09-color-and-detail-master.png` 校对角色比例。

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
| `40_blush.png` | 仅在用户选择 `happy` 表情时使用的淡腮红 |
| `45_eye_l.png`, `46_eye_r.png` | 左、右睁眼层 |
| `45_eye_closed_l.png`, `46_eye_closed_r.png` | 左、右闭眼层 |
| `47_eyebrow_l.png`, `48_eyebrow_r.png` | 独立眉毛 |
| `49_nose.png` | 鼻部细节 |
| `50_mouth.png`, `50_mouth_cavity.png` | 闭合嘴线与张嘴口腔 |
| `60_hair_front.png` | 面部前方头发 |
| `70_accessory.png` | 发饰与最前方装饰 |

## 运行时关系

发布包只携带 `desktop/public/character/live2d/elysia/` 下的固定模型、纹理和最小清单，不携带这些制作源层。运行时通过封闭的 Character State、`neutral / happy / sad` 情绪和可信音频 RMS 嘴型提示修改参数；模型或回复文本都不能提供任意文件路径、参数名或动作脚本。

静态状态、表情、嘴型图集和立绘继续作为 Still、系统 Reduced Motion、WebGL/WASM 失败以及模型校验失败时的降级路径。
