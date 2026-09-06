# TTS 服务集成完成报告

## 概述

TTS 服务当前使用**科大讯飞超拟人语音合成（xtremer 2.0）**，通过 WebSocket API 调用。整体架构保留原有的「角色音色匹配 + 音频缓存 + 章节批量编排」三层结构，仅替换底层网络调用层。

## 架构

```
前端 ChapterVoicePlayer.vue
    ↓ HTTP
后端路由层 (routers/tts.py + routers/voice.py，挂 /api/tts)
    ↓
服务层
    ├─ ChapterVoiceService  ← 章节级编排（LLM 切句 + 并发 + 落库）
    └─ TTSService           ← 单条合成（缓存 + 讯飞 WS 调用）
```

数据最终落 `Asset(asset_type='voice_line')` 表，VN Graph 装配时按 `Asset.prompt == dialogue.text` 匹配回填 `audio_url`。

## 完成的工作

### 1. 后端服务层
- `backend/app/services/tts_service.py`
  - 删除阿里云 Token + HTTP GET 流式调用
  - 新增讯飞 WS 鉴权（HMAC-SHA256 签名）+ 双向 WS 流
  - 新增 `_build_auth_url` / `_build_request_frame` / `_xunfei_ws_run` / `_call_xunfei_tts_api` / `_lookup_profile_extras` / `_map_xunfei_error`
  - 业务错误码映射（10005/10006/10010/10014/10019/10043/10065）
  - 保留 `synthesize` 对外契约与缓存机制

### 2. API 路由
- `backend/app/routers/tts.py` 单条合成路由：契约不变
- `backend/app/routers/voice.py` 章节批量配音路由：契约不变

### 3. 音色配置
- `backend/app/config/tts_voice_profiles.json`
  - 12 个 profile 的 `speaker` 字段从阿里云音色改为讯飞超拟人 cbm01 OPPO oral 系列 vcn
  - 实测可用 vcn 仅 8 个（`x4_lingxiaoxuan_oral` / `x4_lingxiaoyue_oral` / `x4_lingxiaoqi_oral` / `x4_lingyuyan_oral` / `x4_lingfeiyi_oral` / `x4_lingfeizhe_oral` / `x4_lingyuzhao_oral` / `x4_oppo_oral`），多 profile 复用同一 vcn
  - 每条 profile 保留 `tte`（讯飞 emotion tag）/ `speed` / `volume` / `pitch` 扩展字段

### 4. 环境变量
- 删除 `ALIYUN_ACCESS_KEY_ID` / `ALIYUN_ACCESS_KEY_SECRET` / `ALIYUN_TTS_APP_KEY` / `ALIYUN_TTS_REGION`
- 新增 `XUNFEI_TTS_APPID` / `API_KEY` / `API_SECRET` / `DOMAIN` / `PATH` / `PARAM_KEY` / `AUE` / `AUF` / `SPEED` / `VOLUME` / `PITCH` / `TIMEOUT` / `WS_CONNECT_TIMEOUT`
- 默认值：`TTS_ENGINE=xunfei`，`TTS_DEFAULT_SPEAKER=x4_lingxiaoxuan_oral`，`XUNFEI_TTS_PARAM_KEY=tts`

### 5. 测试
- 新增 `backend/tests/test_tts_xunfei_signing.py`（17 个单元测试，mock WS，无需真实凭据）
- 新增 `backend/tests/test_tts_xunfei_integration.py`（3 个集成测试，需真实凭据，默认跳过）
- 现有 `backend/tests/test_chapter_voice_service.py`（13 个契约测试）零改动通过，证明对外契约不变

### 6. 前端
- `frontend/src/api/ttsApi.ts` / `voiceApi.ts` / `ChapterVoicePlayer.vue`：零改动
- 类型定义、API 调用、播放器消费 audio_url 均与引擎无关

### 7. 文档
- 删除 `docs/Aliyun_TTS_Setup.md`
- 新增 `docs/Xunfei_TTS_Setup.md`
- 重写 `docs/TTS_Integration_Report.md` / `TTS_Deployment_Checklist.md` / `TTS_Switching_Guide.md`

## 鉴权机制

讯飞 WS 鉴权用 HMAC-SHA256 签名：

```
date = RFC1123 GMT 时间
signature_origin = f"host: {DOMAIN}\ndate: {date}\nGET {PATH} HTTP/1.1"
signature = base64(hmac_sha256(APISecret, signature_origin))
authorization = base64('api_key="...", algorithm="hmac-sha256", headers="host date request-line", signature="..."')
auth_url = f"wss://{DOMAIN}{PATH}?authorization=...&date=...&host=..."
```

服务器时钟偏移 >5 分钟会 10043 鉴权失败，部署机必须开 NTP。

## API 接口

### 单条合成
```
POST /api/tts/synthesize
```
请求：
```json
{
  "text": "要合成的文本",
  "character_name": "角色名",
  "character_voice": "说话风格描述",
  "gender": "male/female",
  "age": "young/middle/elderly",
  "emotion": "calm/happy/sad/angry",
  "speaker": "直接指定讯飞 vcn（可选）"
}
```
响应：
```json
{
  "success": true,
  "audio_url": "/static/tts_cache/xxx.mp3",
  "cached": false,
  "speaker": "xiaoyan",
  "emotion_prompt": "calm"
}
```

### 状态查询
```
GET /api/tts/status → {"enabled": true, "engine": "xunfei"}
```

### 章节批量配音
```
POST /api/tts/chapters/{project_id}/{chapter_index}/generate-batch
GET  /api/tts/chapters/{project_id}/{chapter_index}/manifest
```

## 智能音色匹配算法

```
匹配分数 = 性别匹配(30) + 年龄匹配(20) + 风格关键词匹配(10/词) + 情绪匹配(15)
```

12 个预设 profile 覆盖「性别 × 年龄 × 风格 × 情绪」常见组合，命中后从 profile 反查 tte/speed/volume/pitch 扩展参数。

## 缓存机制

- 缓存键：`md5(text|character_name|character_voice|speaker|emotion_prompt)`
- 缓存路径：`backend/static/tts_cache/`
- 自动清理：`TTSService.clear_cache(max_age_hours=168)` 清 7 天以上文件

## 验收

| 项 | 状态 |
|----|------|
| 单元测试 17 个 | ✅ 全绿 |
| chapter_voice 契约测试 13 个 | ✅ 全绿（零改动通过） |
| 配置自检脚本 | ✅ 跑通到 WS 握手 + 协议帧正确 |
| 鉴权（HMAC-SHA256 签名） | ✅ 通过（无 10043） |
| 协议帧（parameter.tts / status=2） | ✅ 通过（无 10163） |
| 端到端合成 | ⏳ 卡在 `11201 licc failed`（cbm01 接入点授权未开通或日流控超限）|

## 已知阻塞：cbm01 接入点授权

实测用真实凭据调通到讯飞引擎，返回 `11201 licc failed`。该错误含义（讯飞官方错误码表）：
- **11200 auth no license** — 功能未授权或授权到期
- **11201 auth no enough license** — 业务会话总量超限或日流控超限

`cbm01.cn-huabei-1.xf-yun.com/v1/private/medd90fec` 是讯飞超拟人 OPPO oral 系列服务，需在讯飞控制台单独申请授权并通过商务开通。代码层已全部就绪，**解除阻塞只能由用户联系讯飞商务**：
1. 确认 AppID 是否已开通 cbm01 接入点 + OPPO oral 8 vcn 授权
2. 确认日调用配额是否充足
3. 如未开通，可改用其他已开通的讯飞超拟人接入点（同时改 `XUNFEI_TTS_DOMAIN` / `XUNFEI_TTS_PATH` 和 `tts_voice_profiles.json` 中 vcn）

## 下一步

1. 在 `backend/.env` 填真实 `XUNFEI_TTS_APPID/API_KEY/API_SECRET`（已填）
2. 讯飞控制台开通 cbm01 OPPO oral 系列 8 个 vcn 权限 + 申请接入点授权
3. `python test_tts_config.py` 验证（期待 `✅ 合成成功`）
4. 启动后端 `./start_tts.sh`（统一调用 `backend/start.sh`，默认端口 `60002`）
5. `curl -X POST http://localhost:60002/api/tts/synthesize -H 'Content-Type: application/json' -d '{"text":"你好","speaker":"x4_lingxiaoxuan_oral"}'` 测试合成
6. 前端 `ChapterVoicePlayer` 试播

## 文档索引

- 讯飞配置：`docs/Xunfei_TTS_Setup.md`
- 配置速查：`docs/TTS_Switching_Guide.md`
- 部署检查清单：`docs/TTS_Deployment_Checklist.md`
- EmotiVoice 参考：`docs/EmotiVoice_TTS_Setup.md`（注：代码层无实现，仅文档）

## 注意事项

1. **安全**：`XUNFEI_TTS_API_SECRET` 严禁泄露或提交到代码仓库
2. **时钟**：部署机必须 NTP 校时（≤5min 偏差），否则 10043 鉴权失败
3. **缓存迁移**：从阿里云迁来时清空 `static/tts_cache/*.mp3` 避免新旧音色混播
4. **vcn 权限**：讯飞超拟人发音人需逐一开通，未开通会 10010/10019
5. **成本**：监控讯飞控制台用量，善用本地缓存减少重复请求
