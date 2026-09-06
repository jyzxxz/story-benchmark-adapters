# TTS 服务配置指南

## 当前配置

项目当前默认使用**阿里云百炼 Model Studio TTS**，并保留讯飞旧实现作为回退路径。

### 阿里云百炼 Model Studio 非实时语音合成
- **优点**：CosyVoice / Qwen-TTS 音色丰富；HTTP 非实时接口适合章节批量配音；返回临时音频 URL 后可落本地缓存
- **缺点**：需联网；按量计费；CosyVoice 非实时合成主要在华北 2（北京）地域使用
- **当前默认音色**：`longshuo_v3`

### 科大讯飞超拟人语音合成（xtremer 2.0，在线 API）
- **状态**：代码保留，设置 `TTS_ENGINE=xunfei` 可回退
- **配置文档**：[`docs/Xunfei_TTS_Setup.md`](./Xunfei_TTS_Setup.md)

## 历史引擎

- **阿里云 NLS**：旧版 AccessKey/AppKey 接入已移除；当前阿里云实现使用百炼 `DASHSCOPE_API_KEY`
- **EmotiVoice**（本地部署）：`docs/EmotiVoice_TTS_Setup.md` 保留作为参考，但代码层无 EmotiVoice 实现，`start_tts.sh` 里的 emotivoice 分支仅为占位

## 引擎切换

`backend/app/services/tts_service.py` 按 `TTS_ENGINE` 分流，`tts_voice_profiles.json` 用 `engine` 字段区分音色池。项目其它模块仍统一调用 `tts_service.synthesize(...)`。

## 环境变量速查

| 变量 | 作用 | 默认值 |
|------|------|--------|
| `TTS_ENABLED` | 总开关 | `true` |
| `TTS_ENGINE` | 引擎名，支持 `aliyun` / `xunfei` | `aliyun` |
| `TTS_DEFAULT_SPEAKER` | 兜底音色 | `longshuo_v3` |
| `TTS_MAX_TEXT_LENGTH` | 单条文本字符上限 | `600` |
| `TTS_OUTPUT_DIR` | 音频缓存目录 | `static/tts_cache` |
| `TTS_AUDIO_BASE_URL` | 对外返回的 URL 前缀 | `/static/tts_cache` |
| `TTS_AUDIO_FORMAT` | 文件扩展名 | `mp3` |
| `DASHSCOPE_API_KEY` | 阿里云百炼 API Key | （必填） |
| `ALIYUN_TTS_BASE_URL` | 百炼 HTTP API Base URL | `https://dashscope.aliyuncs.com/api/v1` |
| `ALIYUN_TTS_WORKSPACE_ID` | 可选，配置后使用 Workspace 专属域名 | 空 |
| `ALIYUN_TTS_COSYVOICE_MODEL` | CosyVoice 默认模型 | `cosyvoice-v3-flash` |
| `ALIYUN_TTS_QWEN_MODEL` | Qwen-TTS 默认模型 | `qwen3-tts-flash` |
| `ALIYUN_TTS_SAMPLE_RATE` | CosyVoice 采样率 | `24000` |
| `ALIYUN_TTS_AUDIO_FORMAT` | CosyVoice 输出格式 | `mp3` |
| `ALIYUN_TTS_QWEN_AUDIO_FORMAT` | Qwen-TTS 本地缓存扩展名 | `wav` |
| `XUNFEI_TTS_APPID` | 讯飞应用 ID | （必填） |
| `XUNFEI_TTS_API_KEY` | 讯飞接口密钥 | （必填） |
| `XUNFEI_TTS_API_SECRET` | 讯飞签名密钥 | （必填） |
| `XUNFEI_TTS_DOMAIN` | WS 域名 | `cbm01.cn-huabei-1.xf-yun.com` |
| `XUNFEI_TTS_PATH` | WS path | `/v1/private/medd90fec` |
| `XUNFEI_TTS_PARAM_KEY` | parameter 子 key（已废弃，硬编码为 tts） | `tts` |
| `XUNFEI_TTS_AUE` | 音频编码（lame=mp3） | `lame` |
| `XUNFEI_TTS_TIMEOUT` | 单次合成整体超时 | `30` |
