# AI4VisualNovel v4 图文批量接入

> **现行 v4 驱动的开发补充参考。**安装、配置、试跑、规模调整和实验交付以[操作者交接说明](../../../docs/OPERATOR_HANDOFF.md)为统一入口；下面手工安装依赖和直接调用单项目脚本的示例供维护驱动使用，不替代带依赖锁的安装器。八项记录见[采集说明](../../../docs/BATCH_RECORDING.md)，生成后的本地播放器和评阅包见[回放说明](../../../docs/PLAYBACK_REVIEW.md)。

入口是 `benchmark/run_ai4visualnovel_batch.py`。每个独立 OS worker 将冻结的
35 个原生文件复制到本次 `native/ai4vn/source/`，再由外置
`batch_launcher.py` 执行原生 `main()` 的 `design → script → render`，最后
使用原生 `GameManager`、`DialogueScene`、`StoryParser` 和 pygame 渲染并执行
实际选项。`systems/AI4VisualNovel` 不修改，不读取其中的旧 `.env` 或产物。

原生设计图、内部审核、演绎、脚本、图片生成和视觉审核均保留。原生可能预先生成
未访问分支；这些调用照常计入本次根运行成本，只有实际访问路径进入阅读样本。
原生 12 节点校验、模型解析失败、审核后保留最后一张失败图片等行为照实记录，
不会删节点、补剧情、添加故事提醒或额外重试。

## 配置与运行

先在独立 Python 环境安装原生依赖：

```sh
python -m pip install -r systems/AI4VisualNovel/requirements.txt
```

公共配置格式见 `benchmark/configs/batch.example.json`。只在
`systems.ai4visualnovel` 指定 `repo_path`、`source_lock` 和
`python_executable`；如需指定 rembg 模型缓存，添加 `rembg_model_dir`。
路径相对于配置文件解析。原生抠图使用 `isnet-anime`，缓存缺失时保留 rembg
自身的下载及失败行为。预检只检查依赖可导入，不下载或执行图像模型。

三种模型的 endpoint、model、凭证环境变量名由公共 `providers.text`、
`providers.vision`、`providers.image` 配置。当前共同图像模型是
`gpt-image-2`，视觉审核模型是 `gpt-5.4-mini`。凭证只放环境变量；原生客户端
仅收到本地网关地址和本次临时 token。网关读取真实凭证，分开记录文字、视觉审核
及图片请求。模型控制参数只能来自共同配置，不改原生消息或图片参数。

在仓库根目录运行（替换配置和输出路径）：

```sh
python benchmark/run_ai4visualnovel_batch.py --config /absolute/path/batch.json --preflight
python benchmark/run_ai4visualnovel_batch.py --config /absolute/path/batch.json --out /absolute/path/new-batch --count 10 --concurrency 2
python benchmark/run_ai4visualnovel_batch.py --out /absolute/path/new-batch --resume --concurrency 2
python benchmark/run_ai4visualnovel_batch.py --out /absolute/path/new-batch --verify
```

批量模式不施加生成次数、输出 token、输入长度或生成阶段时限；原生 SDK 和供应商
限制保留。`count` 是独立根运行数量，`concurrency` 是同时运行的 worker 数量。
`--resume` 只启动尚未开始的任务，不重发失败、已完成或可能已送达的运行。

阅读截止遵循共同 v4 bundle 的 `window_chars` 和同一句界规则。每次遇到原生菜单，
按公共 `choice_indices` 执行零基下标，序列用完后重复最后一个。先真实执行原生
`make_choice`，再记录所选路径；越界、缺失目标和停滞控制流明确失败。原生菜单不
自动等同公共 C1/C2。达到窗口即停止读取；未达到窗口而原生结束时保留 `native_end`，
不补正文、不强制生成完整结局。

## 证据范围

- `native/ai4vn/observations.jsonl`：真实解码收件原文、原生阶段、方法和 HTTP
  关联、脚本可用时点、图片参考锚点、原生选择与异常。首创作请求须完整含共同任务一次。
- `trajectories/main/story.jsonl`：实际路径正文、原生节点和脚本修订、原物理行及
  parsed JSON pointer、Writer 请求 call IDs。原生脚本及每次脚本修订另存，裁判只
  读取已观察版本。固定开头作为上下文，不计为新增正文。
- `images/requests.jsonl`、`outputs.jsonl`：网关的每次实际 HTTP 尝试和全部返回候选。
  两个候选即使字节相同也保留两个身份。原生选中候选由响应头 call ID 和原生
  `data[0]` 下标关联，不能单凭像素相同推断来源。
- `images/assets.jsonl`：原始图片、抠图后图片与 parent asset 关联；转换不新增模型候选。
  未选候选、原生拒绝后重生、最后拒绝但仍使用、引用资产及图库来源分别保留。
- `visuals/frame_map.jsonl`：每段实际正文对应的图层、角色候选 ID、原生 UI 截图，
  以及使用同一组原生已加载、缩放图层确定性合成的干净图。缺图和占位明确标记。
  原生菜单也先真实 draw/capture，再执行选择；菜单帧的 `segment_ids=[]`，即使
  开场直接出现菜单也不制造前置正文。每个离屏观测事件带实际 `frame_id`，菜单和
  占位帧不作为首个新增正文对应图片的时延。
- `characters/versions.jsonl`：原生人物定义版本、参考资产及原生出处。它是原生
  身份候选，不擅自创造题目未规定的公共外貌标准。

渲染模式为 SDL dummy 的真实 pygame 离屏运行。它证明原生渲染表面及实际素材
关联，不证明图片已显示到用户桌面；`desktop_presented=false`，不能把后端
可获得时间叫作用户屏幕显示时间。音频没有启用或评测。

## 本地验证

`benchmark/tests/test_ai4vn_batch.py` 使用本地文字、视觉与图像 HTTP 服务，实际
运行原生 CLI 和 pygame。rembg 的重型分割依赖使用明确标注的测试替身；原生
`Artist._remove_background` 方法仍执行。该测试不证明真实分割质量或供应商可用性。
必须显式设置 `AI4VN_TEST_PYTHON`；未指定时跳过原生夹具，不依赖开发者机器路径。

```sh
AI4VN_TEST_PYTHON=/absolute/path/native-python python -m unittest discover -s benchmark/tests -p test_ai4vn_batch.py -v
```

覆盖原生全链与实际选择、同像素多候选、图片失败与占位、三次视觉审核失败后原生
保留最后图片、设计失败停止、两独立 OS worker 并行隔离、每个 root 封存验证，以及
恢复不增加请求；另外验证开场只有原生菜单的真截图，以及有正文的原生回环只按
共同字数窗口停止。测试显式标记 `fixture` 和零付费调用，不能用作故事质量成绩。
