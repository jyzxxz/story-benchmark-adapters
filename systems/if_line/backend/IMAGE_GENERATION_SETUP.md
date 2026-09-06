# 图像生成 / 立绘去背景 环境配置

本文件说明 if_line 后端图像生成（Qwen-Image）和立绘去背景（rembg）所需的
**运行时依赖、模型缓存、以及常见 500 错误的排查方法**。

---

## 1. 安装依赖

在 `backend/` 目录下，使用项目自带 venv 安装：

```bash
cd /home/workspace/fengbohan/if_line/backend
./venv/bin/pip install -r requirements.txt
```

关键依赖（`requirements.txt`）：

| 包 | 作用 | 备注 |
|----|------|------|
| `Pillow` | 图像读写 | |
| `rembg` | 立绘去背景（抠图） | |
| **`onnxruntime`** | rembg 的神经网络推理后端 | **必须显式安装**，见下方说明 |

### 为什么必须显式装 `onnxruntime`？

`rembg` 包本身**不会**自动拉取推理后端，需要用 `rembg[cpu]` 或 `rembg[gpu]`
这种 extras 才会带上。如果只是 `pip install rembg`，会缺少 `onnxruntime`，
导致 `rembg/bg.py` 在 import 时执行 `sys.exit(1)`：

```
ModuleNotFoundError: No module named 'onnxruntime'
  → rembg/bg.py:20: sys.exit(1)
  → SystemExit: 1  （在工作线程里触发，整个请求 500）
```

因此 `requirements.txt` 里把 `onnxruntime>=1.16.0` 单独列出来，避免漏装。

手动补装（如果 venv 已经创建、只缺这一个）：

```bash
./venv/bin/pip install onnxruntime          # CPU 版
# 或 NVIDIA GPU：
./venv/bin/pip install onnxruntime-gpu
```

---

## 2. 模型缓存（u2net）

rembg 默认使用 `u2net` 模型，**首次调用时会从 GitHub 下载 ~176MB**：

```
https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx
```

下载后缓存在 `~/.u2net/u2net.onnx`。

校验：

```bash
ls -la ~/.u2net/u2net.onnx
# 期望: 约 176MB (175997641 bytes)
```

> **离线 / 内网环境**：如果服务器无法访问 GitHub，需要从能联网的机器把
> `u2net.onnx` 拷贝到 `~/.u2net/u2net.onnx`，否则首次去背景会卡在下载或超时。

可通过环境变量改换缓存目录：

```bash
export U2NET_HOME=/data/models/u2net
```

---

## 3. 验证安装

跑一遍真实流程（用任意已有立绘图）：

```bash
cd /home/workspace/fengbohan/if_line/backend
./venv/bin/python -c "
from app.services.image_generation_service import image_generation_service
import asyncio
out = asyncio.run(image_generation_service.remove_background(
    'static/assets/portraits/<某张图>.png'))
print('ok ->', out)
"
```

期望输出：原路径（rembg 是**原地覆盖**写回 PNG），文件大小变化正常（通常比原图小，
因为去背景后大量透明像素压缩率高）。

---

## 4. 相关接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/image-generation/{project_id}/portraits/generate` | 生成立绘（含自动去背景） |
| POST | `/api/image-generation/{project_id}/backgrounds/generate` | 生成背景图 |
| GET  | `/api/image-generation/{project_id}/all` | 拉取该项目所有素材 |
| GET  | `/api/image-generation/status` | 服务状态 |

---

## 5. 常见 500 错误排查

### A. `ModuleNotFoundError: No module named 'onnxruntime'` / `SystemExit: 1`
→ venv 里漏装 `onnxruntime`，执行第 1 节的安装命令。

### B. `401 令牌已过期或验证不正确`
→ Qwen-Image 的 `AI_IMAGE_API_KEY` / `DASHSCOPE_API_KEY` 过期或地域 endpoint 不匹配。注意：
- `.env` 改完 key 后 **必须重启 uvicorn 进程**，`--reload` 不监听 `.env`；
- 模块级常量 `AI_IMAGE_API_KEY = os.getenv(...)` 在 import 时只读一次。

### C. `rewrite failed (portrait): ...`
→ prompt 重写服务（`prompt_rewriter_service.py`）调 LLM 失败，
fallback 到 `AI_IMAGE_API_KEY`，通常和 B 是同一个 key 过期问题。

---

## 6. 关键环境变量（`.env`）

```
# 图像生成（Qwen-Image）
# 默认可直接复用 DASHSCOPE_API_KEY
DASHSCOPE_API_KEY=<阿里云百炼 API Key>
AI_IMAGE_API_KEY=${DASHSCOPE_API_KEY}
AI_IMAGE_BASE_URL=https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
AI_IMAGE_MODEL=qwen-image-2.0
AI_IMAGE_PROMPT_EXTEND=true
AI_IMAGE_WATERMARK=false

# 去背景（可选，默认走本地 rembg）
# U2NET_HOME=/data/models/u2net
```

> 改完 `.env` **务必重启后端进程**，不要只依赖 `--reload`。
