# 科大讯飞超拟人 TTS 配置指南

本项目 TTS 后端使用**科大讯飞超拟人语音合成（xtremer 2.0）**，通过 WebSocket API 调用。

## 1. 开通讯飞服务

### 1.1 注册与实名
1. 访问 [讯飞开放平台](https://www.xfyun.cn/)
2. 注册账号 → 完成实名认证（个人/企业）

### 1.2 开通超拟人语音合成
1. 控制台 → 「语音合成」→ 「**超拟人语音合成 / 超拟人发音人 2.0**」
2. 点击「立即开通」（按量计费，有免费试用额度）
3. 服务有效期与余额在控制台首页可见

### 1.3 创建应用获取凭据
1. 控制台 → 「我的应用」→ 「创建应用」
2. 应用创建后获得三件套：
   - **APPID**（应用 ID，形如 `abc12345`）
   - **APIKey**（接口密钥）
   - **APISecret**（接口签名密钥，**严禁泄露**）
3. 给该应用**绑定「超拟人语音合成」服务**

### 1.4 开通发音人权限
本项目使用的接入点 `cbm01.cn-huabei-1.xf-yun.com/v1/private/medd90fec` 是讯飞超拟人 OPPO oral 系列服务，**仅接受以下 8 个 vcn**（其它 vcn 会报 10163 protocol error 或 10019 vcn not found）：

| vcn | 推测性别 | 适用 profile_id |
|-----|---------|-----------------|
| `x4_lingxiaoxuan_oral` | 女 | 默认女声（TTS_DEFAULT_SPEAKER）/ female_gentle_young |
| `x4_lingxiaoyue_oral` | 女 | female_lively_young / female_angry_young |
| `x4_lingxiaoqi_oral` | 女 | female_sad_young |
| `x4_lingyuyan_oral` | 女 | female_elegant_middle / female_elderly_kind |
| `x4_lingfeiyi_oral` | 男 | male_calm_young |
| `x4_lingfeizhe_oral` | 男 | male_heroic_middle / male_cold_middle |
| `x4_lingyuzhao_oral` | 男 | male_gentle_young / male_elderly_wise |
| `x4_oppo_oral` | 中性 | male_angry_middle |

> **重要**：
> 1. 以上 vcn 的实际性别以讯飞控制台「发音人管理」页为准；如性别不符，可在 `backend/app/config/tts_voice_profiles.json` 调整 profile 与 vcn 的映射。
> 2. 该 cbm01 接入点需要在讯飞控制台单独**申请 OPPO oral 系列授权**。如返回 `11200 auth no license` 或 `11201 licc failed`，说明授权未开通或日流控超限，**必须联系讯飞商务开通/提升配额**，不是代码问题。
> 3. 如已开通其他接入点（如 `tts-api.xfyun.io`），需要相应改 `XUNFEI_TTS_DOMAIN` / `XUNFEI_TTS_PATH` 以及 vcn 列表。

## 2. 配置 .env

编辑 `backend/.env`：

```bash
# TTS 总开关
TTS_ENABLED=true
TTS_ENGINE=xunfei

# 讯飞三件套（必填）
XUNFEI_TTS_APPID=你的APPID
XUNFEI_TTS_API_KEY=你的APIKey
XUNFEI_TTS_API_SECRET=你的APISecret

# 接入点（控制台给的域名，默认值通常正确，如不一致再覆盖）
XUNFEI_TTS_DOMAIN=cbm01.cn-huabei-1.xf-yun.com
XUNFEI_TTS_PATH=/v1/private/medd90fec
XUNFEI_TTS_PARAM_KEY=tts

# 合成参数（可选，不填用默认）
XUNFEI_TTS_AUE=lame                # mp3
XUNFEI_TTS_AUF=audio/L16;rate=16000
XUNFEI_TTS_SPEED=50                # 0-100，50 默认
XUNFEI_TTS_VOLUME=50
XUNFEI_TTS_PITCH=50
XUNFEI_TTS_TIMEOUT=30              # 单次合成超时秒
XUNFEI_TTS_WS_CONNECT_TIMEOUT=10

# 缓存与默认音色
TTS_OUTPUT_DIR=static/tts_cache
TTS_AUDIO_BASE_URL=/static/tts_cache
TTS_DEFAULT_SPEAKER=x4_lingxiaoxuan_oral
TTS_MAX_TEXT_LENGTH=200
TTS_AUDIO_FORMAT=mp3
```

## 3. 自检命令

```bash
# 在项目根目录
python test_tts_config.py
```

预期输出：
- 配置加载 ✓
- 音色匹配 4 个 case 全部输出 vcn
- 合成测试：成功落盘 mp3

## 4. 错误码对照

| code | 含义 | 排查方向 |
|------|------|----------|
| 10005 | APPID 无效 | 检查 XUNFEI_TTS_APPID 拼写 |
| 10006 | 参数错误 | 检查 vcn、aue、tte 字段 |
| 10010 | 引擎拒绝 | vcn 未开通超拟人 → 控制台开通 |
| 10014 | 余额不足 | 控制台充值 |
| 10019 | 音色不存在 | vcn 名拼错，或该 vcn 未在控制台开通 |
| 10043 | 鉴权失败 | APISecret 错；或服务器时间偏移 >5min（用 `ntpdate` 校时） |
| 10065 | QPS 超限 | 并发太高，调低 `_tts_semaphore` |
| 10163 | 协议错 | 请求帧字段缺失或值非法（如 status=3 应为 2，parameter 子 key 应为 tts） |
| 11200 | 功能未授权 | 该 AppID 未开通 cbm01 超拟人 OPPO oral 系列授权 → **联系讯飞商务开通** |
| 11201 | licc failed / 流控超限 | 已开通但日调用次数超限，或服务授权总量耗尽 → **联系讯飞商务提升配额** |

## 5. 时钟同步（关键）

讯飞签名校验要求客户端时间与服务端偏差 ≤ 5 分钟。Linux 部署机务必开启 NTP：

```bash
# Ubuntu/Debian
sudo apt install ntpdate -y
sudo ntpdate ntp.aliyun.com
sudo timedatectl set-ntp true

# 验证
date  # 应为当前 UTC 时间
```

## 6. 旧缓存清理（迁移自阿里云时）

如从阿里云 TTS 迁移过来，旧 `static/tts_cache/*.mp3` 是阿里云音色，**必须清空**避免新旧音色混播：

```bash
rm -f static/tts_cache/*.mp3
```

## 7. 性能与并发

- 单次合成 RTT ≈ 0.5-2s（短文本）
- 模块级信号量 `_tts_semaphore = asyncio.Semaphore(2)` 限制单进程最多 2 个并发 WS
- 章节批量配音 `CHAPTER_VOICE_BATCH_SIZE=5` + 信号量双重限流
- 缓存命中不调 WS（key = md5(text|character_name|character_voice|speaker|emotion)）

## 8. 调试

启用 DEBUG 日志：

```bash
DEBUG=True
```

观察以下日志关键字：
- `[tts] synthesize service start` — 合成开始
- `[tts] xunfei ws start vcn=... text_chars=...` — WS 调用
- `[tts] synthesize service ok ... bytes=... file=...` — 成功
- `[tts] synthesize xunfei business error code=...` — 业务错（参考错误码表）
- `[tts] synthesize xunfei timeout` — 超时
