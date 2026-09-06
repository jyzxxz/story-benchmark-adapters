#!/bin/bash

# 启动前端服务
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
