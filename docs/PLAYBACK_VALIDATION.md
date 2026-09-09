# 离线回放验证记录 · 2026-09-09

基于 `f7ac54dc28895bbb3451aaf1f5fa508d4767f5d7` 增加外置回放。三个 `systems/` 原生源码目录没有变化；公共输入、原生生成与八项指标计算代码没有改变。自动导出在封存及校验完成后执行。

| 检查 | 结果 |
|---|---|
| 新回放合同测试 | 25 项通过：正文、选择、图片引用、指标原值、旧记录、目录冲突、失败隔离及不可信文本/路径 |
| 记录器和批次回归 | 23 项通过；涵盖三个系统共用的封存后自动导出入口 |
| 30 题与实验入口回归 | 121 项运行，120 通过、1 项环境条件跳过 |
| 新 AI4VisualNovel 双进程原生夹具 | 1 项通过，两个独立根运行自动产生播放器与 ZIP；resume 新增请求为 0 |
| 浏览器实际离线回放 | Chromium `file://` 检查 6 个样本、243 页、226 个带图页面；桌面与 390px 视口、逐页文字/图片/选择、目录、键盘、只读选择和末页检查通过 |
| 浏览器网络与脚本 | 0 个远程请求、0 个控制台错误；含脚本/外链图片字符串的测试正文保持为文字，没有执行 |
| 原始证据 | 历史 5 个样本的 4,794 个封存文件集合与 SHA-256 通过；新 AI4 两根 403/408 个文件通过 |
| 八项指标 | 以上每个样本的 M1–M8 与原 `metrics.json` 值完全一致；原来的 unknown/null 保留 |
| 分发包 | ZIP 与 `review/` 内容逐项相符；组织者映射、私有日志及模型配置不进入 ZIP |

浏览器和原生夹具在本地 macOS 运行；本次未重新执行 Windows/Linux 整套安装，也未重跑三个系统的付费生成。浏览器导出采用标准静态文件，接收者解压后用浏览器打开。此次测试证明导出及播放链路，不证明各项目生成内容的质量或原生生成的稳定成功率。

## 使用的故事证据

| 样本 | 来源 | 正文段 / 画面 / 选择记录 | 回放页 |
|---|---|---|---|
| IF Line | 以前保存的真实模型运行 | 104 / 104 / 2 | 108 |
| InfiPlot | 以前保存的真实模型运行 | 75 / 75 / 3 | 80 |
| AI4VisualNovel × 2 | 以前保存的成功工程夹具 | 各 22 / 23 / 1 | 各 25 |
| AI4VisualNovel 失败样本 | 以前保存的真实模型运行，未生成正文 | 0 / 0 / 0 | 2：公共开头与失败说明 |
| 不可信文字夹具 | 本次本地构造 | 1 / 1 / 1（未执行） | 3 |

另一次新 AI4VisualNovel 并行夹具实际执行原生设计、脚本和 Pygame 渲染，每根 352 字、22 段、23 帧、1 次已执行选择、25 页，自动导出状态均为 `ready`。供应商由 localhost 固定响应服务替代，rembg 为明确标注的测试替身，付费请求为 0。播放器醒目标明 `fixture`，不能将其作为真实故事质量结果。

独立内容核对覆盖历史样本的 223 段正文、225 幅画面、7 次选择，以及两类视觉评审包中 450 个画面链接和 19 个角色参考图链接。图像文件按内容复用只减小包体，不改变原生成数量，也不合并正文页或原始计费记录。

## 复现离线检查

在已配置的仓库环境中运行：

```bash
PYTHONPATH=benchmark python3 -m unittest discover -s benchmark/tests -p 'test_playback.py' -v
PYTHONPATH=benchmark python3 -m unittest discover -s benchmark/tests -p 'test_batch_recording.py' -v
bash experiment.sh test
bash experiment.sh playback --input PATH_TO_RUN_OR_BATCH --out NEW_EXPORT_DIRECTORY
```

浏览器回归需要本地 Playwright 与 Chromium，仅开发检查需要，接收者阅读不需要：

```bash
PLAYWRIGHT_MODULE=/path/to/node_modules/playwright node tools/test_playback_browser.cjs \
  --out work/playback-browser-report NEW_EXPORT_DIRECTORY/review
```

查看方式和失败处理见 [回放使用说明](PLAYBACK_REVIEW.md)。原证据规范逐字保留在 `benchmark/source/recording_spec.v0.1.md`，实现对照见 [八项指标采集](BATCH_RECORDING.md)。
