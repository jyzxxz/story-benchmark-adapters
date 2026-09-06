# EmotiVoice TTS 接入说明

## 一、启动 EmotiVoice 服务

### 方式 1：Docker 启动（推荐）

```bash
# 拉取镜像
docker pull syq163/emoti-voice:latest

# 启动服务
# Web UI: 5011 端口，API: 5010 端口
docker run -dp 127.0.0.1:5011:8501 -p 127.0.0.1:5010:8000 syq163/emoti-voice:latest
```

启动后：
- Web UI: http://localhost:5011
- OpenAI 兼容 API: http://localhost:5010

### 方式 2：源码启动

```bash
# 克隆项目
git clone https://github.com/netease-youdao/EmotiVoice.git
cd EmotiVoice

# 安装依赖
conda create -n EmotiVoice python=3.8 -y
conda activate EmotiVoice
pip install torch torchaudio
pip install numpy numba scipy transformers soundfile yacs g2p_en jieba pypinyin pypinyin_dict
python -m nltk.downloader "averaged_perceptron_tagger_eng"

# 下载模型
git lfs install
git lfs clone https://huggingface.co/WangZeJun/simbert-base-chinese WangZeJun/simbert-base-chinese
git clone https://www.modelscope.cn/syq163/outputs.git

# 启动 API 服务（5010 端口）
pip install fastapi pydub uvicorn[standard] pyrubberband
uvicorn openaiapi:app --host 0.0.0.0 --port 5010
```

## 二、API 调用方式

### OpenAI 兼容 API（端口 5010）

```bash
curl -X POST http://127.0.0.1:5010/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "input": "你好，这是一段测试语音。",
    "voice": "8051",
    "prompt": "开心",
    "response_format": "mp3"
  }' \
  --output test.mp3
```

### 参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| input | string | 要合成的文本 |
| voice | string | speaker_id，如 "8051", "11614" 等 |
| prompt | string | 情绪提示，如 "开心", "悲伤", "愤怒", "平静" |
| response_format | string | 输出格式：mp3, wav |
| speed | float | 语速，默认 1.0 |

## 三、常用 Speaker ID

| Speaker ID | 性别 | 年龄 | 特点 |
|------------|------|------|------|
| 8051 | 男 | 年轻 | 冷静理性 |
| 11614 | 男 | 中年 | 豪迈激昂 |
| 9017 | 男 | 年轻 | 温柔细腻 |
| 6097 | 男 | 中年 | 冷峻严肃 |
| 6671 | 女 | 年轻 | 温柔婉约 |
| 6670 | 女 | 年轻 | 活泼开朗 |
| 9136 | 女 | 中年 | 优雅端庄 |
| 11697 | 女 | 年轻 | 悲伤忧郁 |
| 92 | 男 | 中年 | 愤怒激动 |
| 12787 | 女 | 年轻 | 愤怒尖锐 |

完整 speaker 列表见：`backend/app/config/tts_voice_profiles.json`

## 四、环境变量配置

在 `backend/.env` 中添加：

```env
TTS_ENABLED=true
TTS_ENGINE=emotivoice
EMOTIVOICE_BASE_URL=http://127.0.0.1:5010
TTS_OUTPUT_DIR=static/tts_cache
TTS_AUDIO_BASE_URL=/static/tts_cache
TTS_DEFAULT_SPEAKER_ID=8051
TTS_MAX_TEXT_LENGTH=200
TTS_AUDIO_FORMAT=mp3
```

## 五、端口分配

| 服务 | 端口 |
|------|------|
| 后端 API | 60002 |
| EmotiVoice API | 5010 |
| EmotiVoice Web UI | 5011 |

## 六、测试 TTS 功能

1. 确保 EmotiVoice 服务已启动
2. 启动后端服务：`./start_tts.sh`（默认端口 `60002`）
3. 打开前端，进入 Story Bible 页面
4. 在"人物设定"标签页，找到任意角色
5. 输入测试台词，点击"测试语音"按钮

## 七、注意事项

1. **带宽限制**：当前服务器 3M 带宽，音频文件已压缩为 64kbps mp3
2. **文本长度**：单次请求限制 200 字以内
3. **并发控制**：后端限制同时最多 2 个 TTS 请求
4. **缓存**：相同文本、角色、speaker_id 的语音会缓存
5. **GPU 需求**：EmotiVoice 最好使用 GPU，CPU 会较慢

## 八、故障排查

### 问题：EmotiVoice API 连接失败

```bash
# 检查服务是否启动
curl http://127.0.0.1:5010/v1/audio/speech -X POST -H "Content-Type: application/json" -d '{"input":"test"}'

# 检查端口是否被占用
lsof -i :5010
```

### 问题：TTS 功能未启用

检查环境变量 `TTS_ENABLED=true`

### 问题：音频无法播放

检查浏览器控制台是否有 CORS 错误，确认 `/static/tts_cache` 路径可访问
