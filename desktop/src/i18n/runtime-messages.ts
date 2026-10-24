/** Bilingual local settings recovery, bounded audio errors, transcription readiness, and character presence copy. */

import type { MessageCatalog } from './types.ts'
import { backendStatusMessages } from './backend-status-messages.ts'

export const runtimeMessages: MessageCatalog = {
  ...backendStatusMessages,
  "The audio resampler must be reset after finishing.": {
    "en": "The audio resampler must be reset after finishing.",
    "zh-CN": "音频重采样器结束后需要重置才能再次使用。"
  },
  " CUDA initialization failed, so the active model fell back safely.": {
    "en": " CUDA initialization failed, so the active model fell back safely.",
    "zh-CN": " CUDA 初始化失败，当前模型已安全切换到备用设备。"
  },
  " CUDA is unavailable, so Auto loaded the model on CPU.": {
    "en": " CUDA is unavailable, so Auto loaded the model on CPU.",
    "zh-CN": " CUDA 不可用，自动模式已将模型加载到 CPU。"
  },
  "Loaded": {
    "en": "Loaded",
    "zh-CN": "已加载"
  },
  " CUDA is unavailable; Auto will use the CPU fallback.": {
    "en": " CUDA is unavailable; Auto will use the CPU fallback.",
    "zh-CN": " CUDA 不可用，自动模式将改用 CPU。"
  },
  "Available": {
    "en": "Available",
    "zh-CN": "可用"
  },
  "Install the optional local speech-recognition dependencies, then restart the Backend.": {
    "en": "Install the optional local speech-recognition dependencies, then restart the Backend.",
    "zh-CN": "请安装可选的本地语音识别依赖，然后重启本地服务。"
  },
  "The selected CUDA transcription device is unavailable. Choose Auto or CPU, save, and restart the Backend.": {
    "en": "The selected CUDA transcription device is unavailable. Choose Auto or CPU, save, and restart the Backend.",
    "zh-CN": "所选 CUDA 转写设备不可用。请选择自动或 CPU，保存后重启本地服务。"
  },
  "The selected CPU transcription device is unavailable. Reinstall the optional runtime or choose Auto, then restart the Backend.": {
    "en": "The selected CPU transcription device is unavailable. Reinstall the optional runtime or choose Auto, then restart the Backend.",
    "zh-CN": "所选 CPU 转写设备不可用。请重新安装可选运行环境，或选择自动，然后重启本地服务。"
  },
  "No supported local transcription device is available. Reinstall the optional runtime, then restart the Backend.": {
    "en": "No supported local transcription device is available. Reinstall the optional runtime, then restart the Backend.",
    "zh-CN": "没有可用的本地转写设备，请重新安装可选运行环境后重启本地服务。"
  },
  "The local speech model could not initialize. Choose Auto or a smaller installed model, then restart the Backend.": {
    "en": "The local speech model could not initialize. Choose Auto or a smaller installed model, then restart the Backend.",
    "zh-CN": "本地语音模型无法初始化。请选择自动或已安装的较小模型，然后重启本地服务。"
  },
  "The local speech runtime could not be inspected. Reinstall its optional dependencies and restart the Backend.": {
    "en": "The local speech runtime could not be inspected. Reinstall its optional dependencies and restart the Backend.",
    "zh-CN": "无法检查本地语音运行环境，请重新安装可选依赖并重启本地服务。"
  },
  "CUDA transcription is unavailable. Choose Auto or CPU, save, and restart the Backend.": {
    "en": "CUDA transcription is unavailable. Choose Auto or CPU, save, and restart the Backend.",
    "zh-CN": "CUDA 转写不可用。请选择自动或 CPU，保存后重启本地服务。"
  },
  "CUDA transcription could not initialize. Choose Auto or CPU, save, and restart the Backend.": {
    "en": "CUDA transcription could not initialize. Choose Auto or CPU, save, and restart the Backend.",
    "zh-CN": "CUDA 转写无法初始化。请选择自动或 CPU，保存后重启本地服务。"
  },
  "Local speech recognition is unavailable. Review its Settings and restart the Backend.": {
    "en": "Local speech recognition is unavailable. Review its Settings and restart the Backend.",
    "zh-CN": "本地语音识别不可用，请检查设置后重启本地服务。"
  },
  "Unavailable": {
    "en": "Unavailable",
    "zh-CN": "不可用"
  },
  "AudioContext is unavailable.": {
    "en": "AudioContext is unavailable.",
    "zh-CN": "音频处理环境不可用。"
  },
  "Secure voice session identifiers are unavailable.": {
    "en": "Secure voice session identifiers are unavailable.",
    "zh-CN": "无法生成安全的语音会话标识。"
  },
  "EchoCancellationUnavailableError": {
    "en": "EchoCancellationUnavailableError",
    "zh-CN": "回声消除不可用"
  },
  "OverconstrainedError": {
    "en": "OverconstrainedError",
    "zh-CN": "设备约束不满足"
  },
  "ConstraintNotSatisfiedError": {
    "en": "ConstraintNotSatisfiedError",
    "zh-CN": "设备约束不满足"
  },
  "Barge-in requires microphone echo cancellation that can be verified.": {
    "en": "Barge-in requires microphone echo cancellation that can be verified.",
    "zh-CN": "打断功能需要经过验证的麦克风回声消除。"
  },
  "NotAllowedError": {
    "en": "NotAllowedError",
    "zh-CN": "访问被拒绝"
  },
  "SecurityError": {
    "en": "SecurityError",
    "zh-CN": "安全错误"
  },
  "Microphone access was denied by Windows or Electron.": {
    "en": "Microphone access was denied by Windows or Electron.",
    "zh-CN": "Windows 或 Electron 拒绝了麦克风访问。"
  },
  "NotFoundError": {
    "en": "NotFoundError",
    "zh-CN": "未找到设备"
  },
  "The selected microphone is unavailable.": {
    "en": "The selected microphone is unavailable.",
    "zh-CN": "所选麦克风不可用。"
  },
  "NotReadableError": {
    "en": "NotReadableError",
    "zh-CN": "无法读取设备"
  },
  "AbortError": {
    "en": "AbortError",
    "zh-CN": "操作已中止"
  },
  "The microphone could not be opened. Another application may be using it.": {
    "en": "The microphone could not be opened. Another application may be using it.",
    "zh-CN": "无法打开麦克风，可能正被其他应用使用。"
  },
  "Voice capture could not start.": {
    "en": "Voice capture could not start.",
    "zh-CN": "无法开始语音采集。"
  },
  "The source audio sample rate must be positive.": {
    "en": "The source audio sample rate must be positive.",
    "zh-CN": "源音频采样率必须大于零。"
  },
  "The selected microphone identifier is invalid.": {
    "en": "The selected microphone identifier is invalid.",
    "zh-CN": "所选麦克风标识无效。"
  },
  "This device does not provide microphone capture.": {
    "en": "This device does not provide microphone capture.",
    "zh-CN": "此设备不支持麦克风采集。"
  },
  "Voice capture requires a voice_<id> session identifier.": {
    "en": "Voice capture requires a voice_<id> session identifier.",
    "zh-CN": "语音采集需要 voice_<id> 格式的会话标识。"
  },
  "Microphone stream contains no audio track.": {
    "en": "Microphone stream contains no audio track.",
    "zh-CN": "麦克风媒体流中没有音轨。"
  },
  "Verified echo cancellation is unavailable.": {
    "en": "Verified echo cancellation is unavailable.",
    "zh-CN": "经过验证的回声消除不可用。"
  },
  "The microphone disconnected during voice capture.": {
    "en": "The microphone disconnected during voice capture.",
    "zh-CN": "语音采集期间麦克风已断开。"
  },
  "Voice resampling is unavailable.": {
    "en": "Voice resampling is unavailable.",
    "zh-CN": "语音重采样不可用。"
  },
  "Voice activity detection is unavailable.": {
    "en": "Voice activity detection is unavailable.",
    "zh-CN": "语音活动检测不可用。"
  },
  "Dictation requires a fresh voice capture identifier.": {
    "en": "Dictation requires a fresh voice capture identifier.",
    "zh-CN": "听写需要新的语音采集标识。"
  },
  "Microphone access was denied by Windows or Elysia.": {
    "en": "Microphone access was denied by Windows or Elysia.",
    "zh-CN": "Windows 或 Elysia 拒绝了麦克风访问。"
  },
  "The selected microphone is not available.": {
    "en": "The selected microphone is not available.",
    "zh-CN": "所选麦克风不可用。"
  },
  "TrackStartError": {
    "en": "TrackStartError",
    "zh-CN": "无法启动音轨"
  },
  "The microphone is busy or could not be opened by Windows.": {
    "en": "The microphone is busy or could not be opened by Windows.",
    "zh-CN": "麦克风正忙，或 Windows 无法打开它。"
  },
  "The microphone test could not start.": {
    "en": "The microphone test could not start.",
    "zh-CN": "无法开始麦克风测试。"
  },
  "The selected speaker is not available.": {
    "en": "The selected speaker is not available.",
    "zh-CN": "所选扬声器不可用。"
  },
  "Windows did not allow audio to use the selected speaker.": {
    "en": "Windows did not allow audio to use the selected speaker.",
    "zh-CN": "Windows 未允许通过所选扬声器播放音频。"
  },
  "The speaker test could not play.": {
    "en": "The speaker test could not play.",
    "zh-CN": "无法播放扬声器测试音。"
  },
  "Microphone": {
    "en": "Microphone",
    "zh-CN": "麦克风"
  },
  "Speaker": {
    "en": "Speaker",
    "zh-CN": "扬声器"
  },
  "Windows did not allow Elysia to inspect audio devices.": {
    "en": "Windows did not allow Elysia to inspect audio devices.",
    "zh-CN": "Windows 未允许 Elysia 检查音频设备。"
  },
  "Audio devices could not be refreshed.": {
    "en": "Audio devices could not be refreshed.",
    "zh-CN": "无法刷新音频设备。"
  },
  "This device does not provide the browser audio device API.": {
    "en": "This device does not provide the browser audio device API.",
    "zh-CN": "此设备不支持浏览器音频设备接口。"
  },
  "The microphone disconnected during the test.": {
    "en": "The microphone disconnected during the test.",
    "zh-CN": "测试期间麦克风已断开。"
  },
  "The selected speaker identifier is invalid.": {
    "en": "The selected speaker identifier is invalid.",
    "zh-CN": "所选扬声器标识无效。"
  },
  "This device cannot route a test to a selected speaker.": {
    "en": "This device cannot route a test to a selected speaker.",
    "zh-CN": "此设备无法将测试音路由至所选扬声器。"
  },
  "The speaker disconnected during the test.": {
    "en": "The speaker disconnected during the test.",
    "zh-CN": "测试期间扬声器已断开。"
  },
  "Voice action failed": {
    "en": "Voice action failed",
    "zh-CN": "语音操作失败"
  },
  "Interrupting Elysia": {
    "en": "Interrupting Elysia",
    "zh-CN": "正在打断 Elysia"
  },
  "The previous reply is stopping. Keep speaking; this new utterance remains temporary until local transcription begins.": {
    "en": "The previous reply is stopping. Keep speaking; this new utterance remains temporary until local transcription begins.",
    "zh-CN": "上一条回复正在停止。请继续说话；新的语音会暂存至本地转写开始。"
  },
  "Elysia is speaking": {
    "en": "Elysia is speaking",
    "zh-CN": "Elysia 正在说话"
  },
  "Trusted desktop playback is speaking the current Chat reply.": {
    "en": "Trusted desktop playback is speaking the current Chat reply.",
    "zh-CN": "受信任的桌面播放正在朗读当前聊天回复。"
  },
  "Elysia is thinking": {
    "en": "Elysia is thinking",
    "zh-CN": "Elysia 正在思考"
  },
  "The completed transcript is using the normal Chat reply path.": {
    "en": "The completed transcript is using the normal Chat reply path.",
    "zh-CN": "完整转写文本正通过正常聊天流程生成回复。"
  },
  "Microphone muted": {
    "en": "Microphone muted",
    "zh-CN": "麦克风已静音"
  },
  "Unmute to resume continuous listening in this Voice Call.": {
    "en": "Unmute to resume continuous listening in this Voice Call.",
    "zh-CN": "取消静音即可继续此语音通话的持续聆听。"
  },
  "Microphone unavailable": {
    "en": "Microphone unavailable",
    "zh-CN": "麦克风不可用"
  },
  "Transcription failed": {
    "en": "Transcription failed",
    "zh-CN": "转写失败"
  },
  "Local transcription could not finish this utterance.": {
    "en": "Local transcription could not finish this utterance.",
    "zh-CN": "本地转写未能完成这段语音。"
  },
  "Preparing transcription": {
    "en": "Preparing transcription",
    "zh-CN": "正在准备转写"
  },
  "Valid speech was found. Temporary audio is entering the local transcription boundary.": {
    "en": "Valid speech was found. Temporary audio is entering the local transcription boundary.",
    "zh-CN": "已检测到有效语音，临时音频正在交给本地转写。"
  },
  "Transcribing locally": {
    "en": "Transcribing locally",
    "zh-CN": "正在本地转写"
  },
  "Faster-Whisper is processing this utterance locally. No Chat message has been created.": {
    "en": "Faster-Whisper is processing this utterance locally. No Chat message has been created.",
    "zh-CN": "Faster-Whisper 正在本地处理这段语音，尚未创建聊天消息。"
  },
  "Cancelling transcription": {
    "en": "Cancelling transcription",
    "zh-CN": "正在取消转写"
  },
  "Stopping this transcription and discarding any result that arrives late.": {
    "en": "Stopping this transcription and discarding any result that arrives late.",
    "zh-CN": "正在停止此转写，稍后到达的结果将被丢弃。"
  },
  "Sending transcript": {
    "en": "Sending transcript",
    "zh-CN": "正在发送转写文本"
  },
  "The complete local transcript is entering the normal Chat path for this Voice Call.": {
    "en": "The complete local transcript is entering the normal Chat path for this Voice Call.",
    "zh-CN": "完整的本地转写文本正在进入此语音通话的正常聊天流程。"
  },
  "Transcription cancelled": {
    "en": "Transcription cancelled",
    "zh-CN": "转写已取消"
  },
  "The transcript was discarded. Record again when the local speech engine is ready.": {
    "en": "The transcript was discarded. Record again when the local speech engine is ready.",
    "zh-CN": "转写文本已丢弃，本地语音引擎就绪后可重新录制。"
  },
  "Opening microphone": {
    "en": "Opening microphone",
    "zh-CN": "正在打开麦克风"
  },
  "Waiting for the selected microphone and local audio graph.": {
    "en": "Waiting for the selected microphone and local audio graph.",
    "zh-CN": "正在等待所选麦克风和本地音频处理就绪。"
  },
  "Listening for speech": {
    "en": "Listening for speech",
    "zh-CN": "正在聆听"
  },
  "Speak naturally. Silence and short noise are discarded locally.": {
    "en": "Speak naturally. Silence and short noise are discarded locally.",
    "zh-CN": "请自然说话，静音和短暂噪声会在本地丢弃。"
  },
  "Speech detected": {
    "en": "Speech detected",
    "zh-CN": "已检测到语音"
  },
  "Keep speaking; capture ends after about 0.6 seconds of silence.": {
    "en": "Keep speaking; capture ends after about 0.6 seconds of silence.",
    "zh-CN": "请继续说话，静音约 0.6 秒后将结束采集。"
  },
  "Preparing capture": {
    "en": "Preparing capture",
    "zh-CN": "正在准备采集"
  },
  "Captured speech remains temporary while local transcription starts.": {
    "en": "Captured speech remains temporary while local transcription starts.",
    "zh-CN": "正在启动本地转写，已采集的语音暂时保留。"
  },
  "No speech detected": {
    "en": "No speech detected",
    "zh-CN": "未检测到语音"
  },
  "No usable speech was captured. Nothing was sent or added to Chat.": {
    "en": "No usable speech was captured. Nothing was sent or added to Chat.",
    "zh-CN": "未采集到可用语音，没有发送或添加任何聊天内容。"
  },
  "Capture cancelled": {
    "en": "Capture cancelled",
    "zh-CN": "采集已取消"
  },
  "Temporary audio was discarded. Nothing was sent or added to Chat.": {
    "en": "Temporary audio was discarded. Nothing was sent or added to Chat.",
    "zh-CN": "临时音频已丢弃，没有发送或添加任何聊天内容。"
  },
  "Ready to listen": {
    "en": "Ready to listen",
    "zh-CN": "可以开始说话"
  },
  "The microphone listens while this Voice Call is unmuted. Audio remains temporary and is transcribed locally.": {
    "en": "The microphone listens while this Voice Call is unmuted. Audio remains temporary and is transcribed locally.",
    "zh-CN": "此语音通话取消静音后，麦克风会持续聆听。音频仅暂时保留，并在本地转写。"
  },
  "No microphone audio is being captured or monitored.": {
    "en": "No microphone audio is being captured or monitored.",
    "zh-CN": "目前没有采集或监听麦克风音频。"
  },
  "Microphone active": {
    "en": "Microphone active",
    "zh-CN": "麦克风正在采集"
  },
  "Monitoring interruptions": {
    "en": "Monitoring interruptions",
    "zh-CN": "正在检测打断"
  },
  "Confirmed speech is being held temporarily while the previous reply stops.": {
    "en": "Confirmed speech is being held temporarily while the previous reply stops.",
    "zh-CN": "正在等待上一条回复停止，已确认的语音暂时保留。"
  },
  "Verified echo cancellation is monitoring for sustained speech without changing the primary reply state.": {
    "en": "Verified echo cancellation is monitoring for sustained speech without changing the primary reply state.",
    "zh-CN": "经过验证的回声消除正在检测持续语音，主回复状态保持不变。"
  },
  "Microphone on": {
    "en": "Microphone on",
    "zh-CN": "麦克风已开启"
  },
  "Temporary microphone capture is active.": {
    "en": "Temporary microphone capture is active.",
    "zh-CN": "正在临时采集麦克风音频。"
  },
  "Microphone off": {
    "en": "Microphone off",
    "zh-CN": "麦克风已关闭"
  },
  "The microphone is not capturing audio.": {
    "en": "The microphone is not capturing audio.",
    "zh-CN": "麦克风未在采集音频。"
  },
  "Ready": {
    "en": "Ready",
    "zh-CN": "就绪"
  },
  "Elysia is here when you need her.": {
    "en": "Elysia is here when you need her.",
    "zh-CN": "需要的时候，Elysia 就在这里。"
  },
  "Listening": {
    "en": "Listening",
    "zh-CN": "正在聆听"
  },
  "Elysia is listening to this local Voice session.": {
    "en": "Elysia is listening to this local Voice session.",
    "zh-CN": "Elysia 正在聆听此本地语音会话。"
  },
  "Thinking": {
    "en": "Thinking",
    "zh-CN": "正在思考"
  },
  "Elysia is preparing a response.": {
    "en": "Elysia is preparing a response.",
    "zh-CN": "Elysia 正在准备回复。"
  },
  "Speaking": {
    "en": "Speaking",
    "zh-CN": "正在说话"
  },
  "Elysia is speaking the current response.": {
    "en": "Elysia is speaking the current response.",
    "zh-CN": "Elysia 正在朗读当前回复。"
  },
  "Working": {
    "en": "Working",
    "zh-CN": "处理中"
  },
  "Elysia is completing the current local task.": {
    "en": "Elysia is completing the current local task.",
    "zh-CN": "Elysia 正在完成当前本地任务。"
  },
  "Waiting for approval": {
    "en": "Waiting for approval",
    "zh-CN": "等待批准"
  },
  "Elysia is waiting for your decision before continuing.": {
    "en": "Elysia is waiting for your decision before continuing.",
    "zh-CN": "Elysia 正在等待你的决定，之后才会继续。"
  },
  "Needs attention": {
    "en": "Needs attention",
    "zh-CN": "需要处理"
  },
  "A local action needs your attention before Elysia can continue.": {
    "en": "A local action needs your attention before Elysia can continue.",
    "zh-CN": "需要先处理一项本地操作，Elysia 才能继续。"
  },
  "Dictation stopped because transcription could not keep up. Previously saved draft text was kept.": {
    "en": "Dictation stopped because transcription could not keep up. Previously saved draft text was kept.",
    "zh-CN": "转写无法跟上，听写已停止。此前保存的草稿文字已保留。"
  },
  "Local transcription could not start. Dictation stopped; previously saved draft text was kept.": {
    "en": "Local transcription could not start. Dictation stopped; previously saved draft text was kept.",
    "zh-CN": "本地转写无法启动，听写已停止。此前保存的草稿文字已保留。"
  },
  "Local transcription could not be correlated safely. Dictation stopped; previously saved draft text was kept.": {
    "en": "Local transcription could not be correlated safely. Dictation stopped; previously saved draft text was kept.",
    "zh-CN": "无法安全关联本地转写，听写已停止。此前保存的草稿文字已保留。"
  },
  "Dictation stopped because the transcript could not be saved to the message draft.": {
    "en": "Dictation stopped because the transcript could not be saved to the message draft.",
    "zh-CN": "无法将转写内容保存到消息草稿，听写已停止。"
  },
  "Local transcription failed. Dictation stopped; previously saved draft text was kept.": {
    "en": "Local transcription failed. Dictation stopped; previously saved draft text was kept.",
    "zh-CN": "本地转写失败，听写已停止。此前保存的草稿文字已保留。"
  },
  "Dictation requires a valid conversation binding.": {
    "en": "Dictation requires a valid conversation binding.",
    "zh-CN": "听写需要关联有效的聊天。"
  },
  "Dictation received an invalid or repeated audio segment. Previously saved draft text was kept.": {
    "en": "Dictation received an invalid or repeated audio segment. Previously saved draft text was kept.",
    "zh-CN": "听写收到无效或重复的音频片段。此前保存的草稿文字已保留。"
  },
  "Local transcription returned an invalid final transcript. Previously saved draft text was kept.": {
    "en": "Local transcription returned an invalid final transcript. Previously saved draft text was kept.",
    "zh-CN": "本地转写返回了无效的最终文本。此前保存的草稿文字已保留。"
  },
  "VoiceSessionTransitionError": {
    "en": "VoiceSessionTransitionError",
    "zh-CN": "语音会话状态转换错误"
  },
  "Cancel the active Voice operation before rebinding its Session.": {
    "en": "Cancel the active Voice operation before rebinding its Session.",
    "zh-CN": "重新关联语音会话前，请先取消正在进行的语音操作。"
  },
  "Voice Session Chat identifier": {
    "en": "Voice Session Chat identifier",
    "zh-CN": "语音会话聊天标识"
  },
  "Voice Session Project identifier": {
    "en": "Voice Session Project identifier",
    "zh-CN": "语音会话项目标识"
  },
  "Voice capture session identifier is invalid.": {
    "en": "Voice capture session identifier is invalid.",
    "zh-CN": "语音采集会话标识无效。"
  },
  "Only a final Voice transcript can be edited.": {
    "en": "Only a final Voice transcript can be edited.",
    "zh-CN": "只有最终语音转写文本可以编辑。"
  },
  "Voice transcript exceeds its maximum length.": {
    "en": "Voice transcript exceeds its maximum length.",
    "zh-CN": "语音转写文本超过了长度上限。"
  },
  "Voice Chat operation identifier": {
    "en": "Voice Chat operation identifier",
    "zh-CN": "语音聊天操作标识"
  },
  "A non-blank final transcript is required before Chat can begin.": {
    "en": "A non-blank final transcript is required before Chat can begin.",
    "zh-CN": "开始聊天前需要非空的最终转写文本。"
  },
  "Voice interruption capture identifier is invalid.": {
    "en": "Voice interruption capture identifier is invalid.",
    "zh-CN": "语音打断采集标识无效。"
  },
  "Voice Session is not active.": {
    "en": "Voice Session is not active.",
    "zh-CN": "语音会话未激活。"
  },
  "Voice Session correlation space is exhausted.": {
    "en": "Voice Session correlation space is exhausted.",
    "zh-CN": "语音会话关联标识已耗尽。"
  },
  "Voice Session controller has been disposed.": {
    "en": "Voice Session controller has been disposed.",
    "zh-CN": "语音会话控制器已释放。"
  },
  "The {model} model is loaded on {device}.{fallback}": {
    "en": "The {model} model is loaded on {device}.{fallback}",
    "zh-CN": "{model} 模型已加载到 {device}。{fallback}"
  },
  "The {model} model is installed and will use {device}.{fallback}": {
    "en": "The {model} model is installed and will use {device}.{fallback}",
    "zh-CN": "{model} 模型已安装，将使用 {device}。{fallback}"
  },
  "Install the selected {model} model in models/weights/faster-whisper/{model}, then restart the Backend.": {
    "en": "Install the selected {model} model in models/weights/faster-whisper/{model}, then restart the Backend.",
    "zh-CN": "请将所选 {model} 模型安装到 models/weights/faster-whisper/{model}，然后重启本地服务。"
  },
  "Auto": {
    "en": "Auto",
    "zh-CN": "自动"
  },
  "Microphone {number}": {
    "en": "Microphone {number}",
    "zh-CN": "麦克风 {number}"
  },
  "Speaker {number}": {
    "en": "Speaker {number}",
    "zh-CN": "扬声器 {number}"
  },
  "Bind a Voice Session before attempting to start listening.": {
    "en": "Bind a Voice Session before attempting to start listening.",
    "zh-CN": "开始聆听前，请先关联语音会话。"
  },
  "Cannot start listening while Voice Session is idle.": {
    "en": "Cannot start listening while Voice Session is idle.",
    "zh-CN": "语音会话处于空闲状态，无法开始聆听。"
  },
  "Cannot start listening while Voice Session is listening.": {
    "en": "Cannot start listening while Voice Session is listening.",
    "zh-CN": "语音会话处于聆听状态，无法开始聆听。"
  },
  "Cannot start listening while Voice Session is transcribing.": {
    "en": "Cannot start listening while Voice Session is transcribing.",
    "zh-CN": "语音会话处于转写状态，无法开始聆听。"
  },
  "Cannot start listening while Voice Session is reviewing.": {
    "en": "Cannot start listening while Voice Session is reviewing.",
    "zh-CN": "语音会话处于检查状态，无法开始聆听。"
  },
  "Cannot start listening while Voice Session is thinking.": {
    "en": "Cannot start listening while Voice Session is thinking.",
    "zh-CN": "语音会话处于思考状态，无法开始聆听。"
  },
  "Cannot start listening while Voice Session is speaking.": {
    "en": "Cannot start listening while Voice Session is speaking.",
    "zh-CN": "语音会话处于播放状态，无法开始聆听。"
  },
  "Cannot start listening while Voice Session is cancelling.": {
    "en": "Cannot start listening while Voice Session is cancelling.",
    "zh-CN": "语音会话处于取消状态，无法开始聆听。"
  },
  "Voice Session Chat identifier is invalid.": {
    "en": "Voice Session Chat identifier is invalid.",
    "zh-CN": "语音会话聊天标识无效。"
  },
  "Voice Session Project identifier is invalid.": {
    "en": "Voice Session Project identifier is invalid.",
    "zh-CN": "语音会话项目标识无效。"
  },
  "Voice Chat operation identifier is invalid.": {
    "en": "Voice Chat operation identifier is invalid.",
    "zh-CN": "语音聊天操作标识无效。"
  }
}
