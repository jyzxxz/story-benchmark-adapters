# TTS 服务部署检查清单

## ✅ 讯飞超拟人 TTS 部署检查

### 1. 讯飞服务开通
- [ ] 拥有讯飞开放平台账号（https://www.xfyun.cn/）
- [ ] 完成实名认证
- [ ] 开通「超拟人语音合成」服务
- [ ] 创建应用，获得 **APPID / APIKey / APISecret**
- [ ] 应用绑定「超拟人语音合成」服务
- [ ] 「发音人管理」开通 cbm01 OPPO oral 系列 8 个 vcn 权限（清单见 `docs/Xunfei_TTS_Setup.md` 第 1.4 节）
- [ ] 联系讯飞商务确认 cbm01 接入点 OPPO oral 系列授权已开通（避免 11200/11201）

### 2. 环境配置
- [ ] 复制 `.env.example` 到 `.env`
- [ ] 填写 `XUNFEI_TTS_APPID`
- [ ] 填写 `XUNFEI_TTS_API_KEY`
- [ ] 填写 `XUNFEI_TTS_API_SECRET`
- [ ] 设置 `TTS_ENGINE=xunfei`
- [ ] 设置 `TTS_ENABLED=true`
- [ ] 确认 `XUNFEI_TTS_DOMAIN` / `XUNFEI_TTS_PATH` 与讯飞控制台给的接入点一致
- [ ] 确认 `XUNFEI_TTS_PARAM_KEY=tts`（旧值 `ora12` 已废弃）
- [ ] 确认 `TTS_DEFAULT_SPEAKER=x4_lingxiaoxuan_oral`（必须是 cbm01 白名单内 vcn）
- [ ] **没有遗留** `ALIYUN_ACCESS_KEY_ID` 等阿里云变量

### 3. 依赖与系统
- [ ] Python 依赖已装：`pip install -r backend/requirements.txt`（含 aiohttp）
- [ ] **系统时间已校准**（讯飞签名要求 ≤5min 偏差）：
  ```bash
  sudo ntpdate ntp.aliyun.com && date
  ```

### 4. 缓存目录
- [ ] `static/tts_cache/` 目录存在（应用启动会自动创建）
- [ ] **从阿里云迁移**：旧 mp3 已清空 `rm -f static/tts_cache/*.mp3`

### 5. 自检
- [ ] `python test_tts_config.py` 退出码 0
- [ ] 日志可见 `合成成功` 且音频文件落盘

### 6. API 端到端验证
- [ ] `GET /api/tts/status` 返回 `{"enabled": true, "engine": "xunfei"}`
- [ ] `POST /api/tts/synthesize` body `{"text":"你好","speaker":"xiaoyan"}` 返回 `success=true` + `audio_url`
- [ ] 浏览器访问 audio_url 能播放
- [ ] 同请求第二次 `cached=true`
- [ ] `POST /api/tts/chapters/<pid>/<cid>/generate-batch` 返回 `failed=0`，所有 voice_lines[].audio_url 非空

### 7. 错误码排查参考

| 错误 | 原因 |
|------|------|
| 10043 鉴权失败 | APISecret 错；或系统时间漂移 |
| 10010/10019 vcn 错 | 该 vcn 未在讯飞控制台开通（注意 cbm01 接入点只支持 8 个 x4_*_oral vcn） |
| 10014 余额不足 | 讯飞账户需充值 |
| 10065 QPS 超限 | 降低 `_tts_semaphore` 或 chapter batch_size |
| 10163 协议错 | 请求帧字段缺失或值非法（parameter 子 key 必须是 tts；status 必须 0/1/2） |
| 11200 功能未授权 | AppID 未开通 cbm01 超拟人 OPPO oral 系列授权 → 联系讯飞商务 |
| 11201 licc failed | 已开通但日流控/总量超限 → 联系讯飞商务提升配额 |
| 401（HTTP 握手层） | APPID/APIKey 错，或被讯飞 WAF 拒 |

### 8. 监控
- [ ] 日志关键字 `[tts] synthesize service ok` 频率正常
- [ ] 日志关键字 `[tts] synthesize xunfei business error` 报警
- [ ] `static/tts_cache/` 磁盘占用监控（建议周期清理 >7 天文件）
