#!/bin/bash

# 启动后端服务脚本（使用虚拟环境）

# 切换到后端目录
cd "$(dirname "$0")/backend"

# 激活虚拟环境
source venv/bin/activate

# 启动服务
python -m uvicorn app.main:app --host 0.0.0.0 --port 60002 --reload
