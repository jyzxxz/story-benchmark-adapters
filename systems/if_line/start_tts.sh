#!/bin/bash
# TTS 服务快速启动脚本

set -e

echo "================================"
echo "TTS 服务启动脚本"
echo "================================"

# 检查环境变量
if [ ! -f "backend/.env" ]; then
    echo "❌ 未找到 .env 文件"
    echo "请复制 backend/.env.example 到 backend/.env 并填写配置"
    exit 1
fi

# 加载环境变量
source backend/.env 2>/dev/null || true

# 检查 TTS 引擎配置
echo ""
echo "当前 TTS 配置:"
echo "  引擎: $TTS_ENGINE"
echo "  状态: $TTS_ENABLED"

if [ "$TTS_ENGINE" == "aliyun" ]; then
    echo ""
    echo "使用阿里云百炼 Model Studio TTS API"
    echo "  区域: ${ALIYUN_TTS_REGION:-cn-beijing}"
    if [ -n "$ALIYUN_TTS_WORKSPACE_ID" ]; then
        echo "  Workspace: $ALIYUN_TTS_WORKSPACE_ID"
    else
        echo "  Endpoint: ${ALIYUN_TTS_BASE_URL:-https://dashscope.aliyuncs.com/api/v1}"
    fi

    # 检查必需配置
    if [ -z "$DASHSCOPE_API_KEY" ] && [ -z "$ALIYUN_DASHSCOPE_API_KEY" ] && [ -z "$ALIYUN_API_KEY" ]; then
        echo ""
        echo "❌ 阿里云配置不完整，请检查以下环境变量:"
        echo "  - DASHSCOPE_API_KEY"
        exit 1
    fi

elif [ "$TTS_ENGINE" == "emotivoice" ]; then
    echo ""
    echo "使用 EmotiVoice 本地服务"
    echo "  地址: $EMOTIVOICE_BASE_URL"

    # 检查 EmotiVoice 服务
    if curl -s "$EMOTIVOICE_BASE_URL" > /dev/null 2>&1; then
        echo "  ✓ EmotiVoice 服务已启动"
    else
        echo "  ✗ EmotiVoice 服务未启动"
        echo ""
        echo "请先启动 EmotiVoice 服务:"
        echo "  docker run -dp 127.0.0.1:5011:8501 -p 127.0.0.1:5010:8000 syq163/emoti-voice:latest"
        exit 1
    fi
fi

echo ""
echo "================================"
echo "启动后端服务..."
echo "================================"

# 使用唯一的后端启动入口和端口默认值，避免 TTS 启动方式形成独立部署配置。
exec env PORT="${PORT:-60002}" bash backend/start.sh
