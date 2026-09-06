#!/bin/bash

# 启动后端服务
cd backend

# 仅在 venv 不存在时创建（已存在则跳过，避免跨 Python 版本污染）
# 如需重建：cd backend && rm -rf venv && conda create -p venv python=3.12 -y && ./venv/bin/pip install -r requirements.txt
if [ ! -d venv ]; then
    python -m venv venv
fi

# 直接用 venv 内的解释器（兼容 conda env，conda env 没有 bin/activate）
if [ ! -x venv/bin/python ]; then
    echo "❌ venv/bin/python 不存在，请用以下命令重建："
    echo "   cd backend && rm -rf venv && conda create -p venv python=3.12 -y && ./venv/bin/pip install -r requirements.txt"
    exit 1
fi

venv/bin/python -m pip install -r requirements.txt
exec venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 60002