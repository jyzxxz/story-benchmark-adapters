# VNGraph 分叉接口运行记录：病夜自证

## 结论

- 源项目：5《白非立上进记5》。
- 目标项目：10《白非立上进记5 - 分叉》。
- 源章节：第 1 章《感冒》。
- 新章节：第 21 章《病夜自证》。
- 阅读路径：原线 `1 -> 2` 保留；分叉线 `1 -> 21 -> 2` 已写入 `BranchTarget`。
- 全节点覆盖：项目 10 第 1 章分叉入口覆盖 `VNNodeLibrary.txt` 中 24 种可创建节点类型，缺失列表为空。

## HTTP 调用记录

```json
[
  {
    "name": "查询项目 5 第 1 章节点摘要",
    "method": "GET",
    "url": "http://127.0.0.1:8013/api/projects/5/chapters/1/vn-graph/nodes",
    "request_body": null,
    "response_summary": "返回 13 个节点；本次选中 node_id_start=10、node_id_end=13。"
  },
  {
    "name": "创建复杂分叉章节",
    "method": "POST",
    "url": "http://127.0.0.1:8013/api/projects/5/chapters/1/vn-graph/branch-with-chapter",
    "request_body": {
      "node_id_start": 10,
      "node_id_end": 13,
      "user_prompt": "请做一个复杂、可读、可在查看器里观察的 VNGraph 分叉：从白非立感冒后躺在卧室的节点开始，他没有继续沿原线只把失败吞下去，而是在夜里给自己写了一份极端具体的省大自救计划：承认高考失利、接受省大作为战场、偷偷建立论文/竞赛/转学三线并行的长期计划。新章节要有宿舍夜谈、手机备忘录、室友短暂打断、白非立自我承认失败但不认输的对白。要求用上所有节点/全部节点/全节点能力，尽量覆盖 VNNodeLibrary.txt 里的 Progress 和 Action 可创建节点：Start、Paragraph、Dialogue、Transition、Choice、背景、插画、BGM、CV、音效、顺序、并行、等待、立绘入场/移动/横移/靠近/缩放/效果/退场、回忆泛黄、眨眼、模糊、清除屏幕特效。节点标题不要叫分叉xx，要像真实剧情生成器的节点名。分叉章节末尾必须能接回第2章《省大报到》，形成 1 -> 21 -> 2 的阅读路径，同时原故事线仍然能 1 -> 2。",
      "new_chapter_title": "病夜自证",
      "dry_run": false
    },
    "response_summary": {
      "target_project_id": 10,
      "new_chapter_index": 21,
      "target_source_vn_graph_id": 14,
      "new_vn_graph_id": 16,
      "planner": "llm_planner_v1",
      "applied_operations": 28,
      "message": "已用 LLM 规划分叉项目、创建新章节，并在原章节 VNGraph 中插入分叉入口"
    }
  },
  {
    "name": "读取新项目第 1 章 VNGraph",
    "method": "GET",
    "url": "http://127.0.0.1:8013/api/projects/10/chapters/1/vn-graph",
    "request_body": null,
    "response_summary": "返回项目 10 第 1 章 VNGraph，40 个节点。"
  },
  {
    "name": "读取新项目第 21 章 VNGraph",
    "method": "GET",
    "url": "http://127.0.0.1:8013/api/projects/10/chapters/21/vn-graph",
    "request_body": null,
    "response_summary": "返回项目 10 第 21 章 VNGraph，8 个节点。"
  }
]
```

## LLM 系统提示词

```text
你是视觉小说分叉编辑器的剧情策划和演出设计。你只输出 JSON，不输出解释。你负责把用户自然语言分叉需求转成新章节计划和 VNGraph 演出意图；不要生成 VNGraph 节点，不要生成 JSON Patch。
```

## LLM 用户提示词

```json
{
  "task": "根据用户自然语言，生成一个新的视觉小说分叉章节计划，并设计可由后端编译成 VNGraph 节点的演出意图。",
  "vn_node_library_summary": {
    "graph_root": "VNGraph = {Version:1, StartNodeIndex:int, Nodes:[node...]}",
    "node_common_fields": [
      "Index",
      "DisplayName",
      "Comment",
      "NodeType",
      "SubType",
      "X",
      "Y",
      "Data",
      "Outputs"
    ],
    "serialized_value_rule": "Data 中每个值必须由后端包成 VNSerializedValue；LLM 不要写 Kind 包装。",
    "progress_nodes": {
      "Dialogue": {
        "NodeType": 1,
        "SubType": 1,
        "use": "单句对白，可挂 Actions 和 Next"
      },
      "Paragraph": {
        "NodeType": 1,
        "SubType": 2,
        "use": "多句旁白/对白，可挂 Actions 和 Next"
      },
      "Transition": {
        "NodeType": 1,
        "SubType": 4,
        "use": "画面转场，支持 FadeIn/FadeOut/Wipe"
      },
      "Choice": {
        "NodeType": 1,
        "SubType": 5,
        "use": "选项分支，Options[i].Next 指向后续 Progress"
      },
      "Start": {
        "NodeType": 1,
        "SubType": 6,
        "use": "章节入口，每张图仅一个"
      }
    },
    "action_nodes": {
      "BackGround": {
        "NodeType": 2,
        "SubType": 3,
        "directive": "background_image"
      },
      "Delay": {
        "NodeType": 2,
        "SubType": 9,
        "directive": "delay_seconds"
      },
      "Sequence": {
        "NodeType": 2,
        "SubType": 7,
        "directive": "action_mode=sequence"
      },
      "Parallel": {
        "NodeType": 2,
        "SubType": 8,
        "directive": "action_mode=parallel"
      },
      "Art": {
        "NodeType": 2,
        "SubType": 6,
        "directive": "illustration_image"
      },
      "Tachi": {
        "NodeType": 2,
        "SubType": 1,
        "directive": "tachi_actions action=enter"
      },
      "TachiMove": {
        "NodeType": 2,
        "SubType": 11,
        "directive": "tachi_actions action=move"
      },
      "TachiExit": {
        "NodeType": 2,
        "SubType": 13,
        "directive": "tachi_actions action=exit"
      },
      "TachiMoveX": {
        "NodeType": 2,
        "SubType": 14,
        "directive": "tachi_actions action=move_x"
      },
      "TachiMoveBeside": {
        "NodeType": 2,
        "SubType": 15,
        "directive": "tachi_actions action=move_beside"
      },
      "TachiScale": {
        "NodeType": 2,
        "SubType": 12,
        "directive": "tachi_actions action=scale"
      },
      "TachiEffect": {
        "NodeType": 2,
        "SubType": 16,
        "directive": "tachi_actions action=effect"
      },
      "BackgroundMusic": {
        "NodeType": 2,
        "SubType": 21,
        "directive": "bgm_path"
      },
      "Audio": {
        "NodeType": 2,
        "SubType": 5,
        "directive": "sound_effects"
      },
      "CV": {
        "NodeType": 2,
        "SubType": 4,
        "directive": "cv_path"
      },
      "MemoryEffect": {
        "NodeType": 2,
        "SubType": 17,
        "directive": "screen_effects"
      },
      "BlinkEffect": {
        "NodeType": 2,
        "SubType": 18,
        "directive": "screen_effects"
      },
      "BlurEffect": {
        "NodeType": 2,
        "SubType": 19,
        "directive": "screen_effects"
      },
      "ClearScreenEffect": {
        "NodeType": 2,
        "SubType": 20,
        "directive": "screen_effects"
      }
    },
    "connection_rules": [
      "Progress 组成主流程，使用 Next 或 Options[i].Next。",
      "Action 不进入主流程，只能被 Progress 的 Actions 引用。",
      "本接口只接受 graph_directives，后端负责分配 Index、Outputs 和 Kind 包装。"
    ]
  },
  "output_schema": {
    "title": "string，章节标题，中文，最多 40 字",
    "summary": "string，章节摘要，中文，80-220 字",
    "content": "string，章节正文，中文，500-900 字，适合转成 VNGraph 段落；可混合旁白和对白，格式如 白非立：台词",
    "scene": "string，主要场景，中文，最多 40 字",
    "emotion": "string，情绪走向，中文，最多 40 字",
    "visual_keywords": "array[string]，3-8 个中文视觉关键词",
    "graph_directives": {
      "action_mode": "enum，direct/sequence/parallel/sequence_parallel；复杂演出建议用 sequence_parallel",
      "continue_option_text": "string，继续原故事线的选项文案",
      "branch_option_text": "string，进入分叉的选项文案",
      "entry_line": "string，进入分叉前显示的一句旁白或对白",
      "dialogue": "object，可为空；含 speaker/text/voice_id，用于额外创建 Dialogue 节点",
      "transition_type": "enum，FadeOut/FadeIn/WipeInLeftToRight/WipeOutLeftToRight/WipeInRightToLeft/WipeOutRightToLeft",
      "background_image": "string，可为空，Godot res:// 背景路径",
      "illustration_image": "string，可为空，Godot res:// 插画/CG 路径",
      "bgm_path": "string，可为空，Godot res:// 音乐路径",
      "cv_path": "string，可为空，Godot res:// 配音路径",
      "delay_seconds": "number，可为空；用于 Delay 节点，0.05 到 5 秒",
      "screen_effects": "array，允许 MemoryEffect/BlurEffect/BlinkEffect/ClearScreenEffect",
      "tachi_actions": "array，可为空；元素含 action=enter/exit/move/move_x/move_beside/scale/effect、tachi_id、image、x、y、duration、effect",
      "sound_effects": "array，可为空；元素含 path、volume、pitch_scale"
    }
  },
  "hard_constraints": [
    "只返回一个 JSON object，字段必须包含 title/summary/content/scene/emotion/visual_keywords/graph_directives。",
    "不要返回 markdown。",
    "不要创建多个章节。",
    "不要输出 VNGraph JSON。",
    "不要输出 JSON Patch。",
    "graph_directives 只能使用 vn_node_library_summary 中列出的可创建节点能力。",
    "DisplayName 由后端根据剧情语义生成；你只描述演出意图和章节内容。",
    "graph_directives 是演出意图，不要自己分配 Index，不要写 Outputs，不要写 Kind 包装。",
    "新章节必须承接 source_excerpt，并体现 user_prompt 的分叉方向。",
    "如果 fixed_title 或 fixed_summary 不为空，必须使用它们。"
  ],
  "project": {
    "id": 5,
    "title": "白非立上进记5",
    "style": "黑暗深沉",
    "pace": "slow",
    "characters": [
      {
        "name": "白非立",
        "role": "Protagonist",
        "appearance": "瘦高，戴黑框眼镜，常年穿校服或廉价T恤，头发油腻，眼神中带着不甘与愤懑。",
        "personality": "固执自负，爱面子，擅长自我合理化；关键时刻优柔寡断，易受他人影响；有实干潜力但缺乏持久毅力。致命弱点：无法正视自身局限，用幻想逃避现实。",
        "motivation_and_goal": "核心动机：证明自己拥有清华水平的实力，洗刷高考失利之耻。终极目标：考上清华研究生，获得社会认可。",
        "internal_conflict": "内心既渴望脚踏实地努力，又忍不住沉迷于‘本应考上清华’的自我叙事；鄙视平庸，却又不断妥协；在理想主义与功利主义间反复挣扎。",
        "voice": "语气时而激昂时而颓丧；常用夸张修辞和反问句来自我辩护，如‘要不是感冒，我肯定……’；情绪外露，喜怒形于色；说话节奏忽快忽慢，紧张时语速加快且重复；措辞带有学生腔和网络用语，偶尔模仿学术腔但漏洞百出。"
      },
      {
        "name": "杨志强",
        "role": "Major Supporting Character",
        "appearance": "中等身材，方脸，眼神锐利，总是穿着整洁的衬衫，面带若有若无的微笑。",
        "personality": "表面友善实则城府深，善于利用他人；冷静理性，目标明确。致命弱点：过度算计，缺乏真诚。",
        "motivation_and_goal": "核心动机：利用白非立的才华和资源为自己铺路。终极目标：在学术或职场中取得超越他人的成就。",
        "internal_conflict": "对白非立既有利用之心又有几分惺惺相惜，但利益优先于情感。",
        "voice": "语气温和从容，措辞得体，常用鼓励性话语；情感隐藏极深，从不直接表露敌意；说话节奏平稳，善用比喻和典故，让人感到受重视；偶尔在关键处插入反问，引导对方按自己意愿行动。"
      },
      {
        "name": "舅舅",
        "role": "Major Supporting Character",
        "appearance": "五十岁左右，微胖，圆脸，常年穿工装，手指粗糙，说话时喜欢拍桌子。",
        "personality": "务实精明，江湖气重，重人情但更重利益；粗中有细，善于在体制边缘游走。致命弱点：轻视规则，认为结果高于一切。",
        "motivation_and_goal": "核心动机：扩大工厂规模，巩固家族地位。终极目标：让白非立成为自己技术体系的继承人。",
        "internal_conflict": "对白非立有亲情期望，但更看重其能否为工厂创造价值。",
        "voice": "语气豪爽直接，常用短句和命令式口吻；词汇土俗，夹杂方言和行业黑话；情感直白，高兴时大笑，不满时骂街；说话节奏快，不容打断，表现出掌控欲。"
      }
    ],
    "story_start": "2008年夏天，白非立以五十分之差与自己努力奋斗，寒窗苦读十多年的目标，清华大学，“擦肩而过”。白非立最终只拿到了一所他高中三年最鄙夷的鱼腩985大学的录取通知书，专业还是天坑专业。那个高三暑假的夏天，白非立逢人便说，自己高考那几天不幸重感冒，被分配的考场正好倒霉催得，还有空调，空调还正好调得温度很低，门窗紧闭。结果就是，别的考生都在享受着酷暑中的考场凉意，超常发挥，而重感冒的白非立却感到愈加难受，于是就发挥失常，自称少考了一百分，否则上清华肯定是妥妥的。听到这番言辞的白非立的同学们都无不露出了鄙夷的眼神，觉得白非立真是太能给自己找补了，明明就这水平，还非说是状元发挥失常。白非立遇到了十个高中同班同学，听完他这一番描述后。有三个一脸冷漠地对他说，“那你去复读学校复读去啊！明年拿理科状元”还有三个一脸愤怒地说，“你就别得了便宜还卖乖了吧，你也就是个中不溜211水平，能上个垫底985都是你烧高香，超常发挥了！咱班的庞统考上了北大，孔明考上了清华，人家常年班级前两名，你高三一年进过咱班前十吗？还清华水平，谁信你呀！”还有三个略带嘲讽地说，“是的，你要不是数学和理综发挥失常，正好遇到了十几道不会的题，今年咱班又能多一个考上清华的，可惜了，可惜了啊！”只有一个平时不怎么跟白非立说话的杨志强，听完后拍了拍白非立的肩膀，说，“我一直都认可你的才华，不要失望，锥在囊中，锋芒毕露！虽然没考上清华，我相信你还是有清华的水平的，四年后，你肯定能读清华的研究生！”虽然听完后觉得有点怪怪的，但白非立还是觉得受到了莫大的鼓舞，仿佛前方一下子就有了方向。白非立也用力地拍了拍杨志强的肩膀，说“够哥们！咱们四年后在清华见！”这个杨志强被一所白非立比较认可的985学校的天坑专业录取。",
    "story_end": "回到学校后，白非立被自己挂靠的老师通知，后天带着自己的答辩PPT一起去他的办公室进行本科生毕业预答辩。\\n\\n白非立一下子就蒙了，自己一直是一个人在外面搞生产，根本就不知道毕业论文工作进展到了什么程度，预答辩就更是没想过了，自己这边也就是刚刚把大概的工作量和数据凑齐的程度。\\n\\n处于慌乱状态的白非立当天又坐车回到了舅舅的工厂，问舅舅自己的毕业论文该怎么写。\\n\\n舅舅坐在办公室里，把这个项目从立项到实验，再到中试和验收的故事从下午四点多一口气讲到了七点，但还是没讲具体的论文部分。\\n\\n白非立等不及了，就说，可是我还是要写论文的啊，后天就要去学校预答辩了。\\n\\n舅舅说，本科生的答辩都很简单的，你就把你这几个月做的东西，都给用正式一点的语言写上去，凑够字数就行了。\\n\\n白非立说，那我在实验部分不搞因循守旧那一套，按照专利的方式去写行不行啊？\\n\\n舅舅说，当然行了，怎么不行呢？\\n\\n白非立回到宿舍，熬了一个通宵，把论文的初稿勉强写完了，第二天舅舅看了一遍，表示写得很好，肯定能完胜大部分那些蹲在学校实验室做着千篇一律的实验的同学的论文。\\n\\n下午白非立又修改了一下论文，整理了一些东西，就坐车回学校了。"
  },
  "story_bible": {
    "worldview": "故事始于2008年夏天，中国社会正处于经济高速发展与教育内卷加剧的转型期。高考作为阶层跃迁的核心通道，塑造着千万家庭的命运。白非立所在的城市充满浮躁的升学氛围，清华北大被神化为唯一成功路径。大学校园内，学术功利化、形式主义盛行，导师与学生的关系常夹杂利益交换。工业领域，民营工厂在技术革新与粗放管理中挣扎，舅舅的工厂代表了草根创业者的生存智慧。整体社会弥漫着对‘成功’的单一崇拜，而个体的精神困境与价值迷失被掩盖在物质繁荣之下。",
    "characters": [
      {
        "name": "白非立",
        "role": "Protagonist",
        "appearance": "瘦高，戴黑框眼镜，常年穿校服或廉价T恤，头发油腻，眼神中带着不甘与愤懑。",
        "personality": "固执自负，爱面子，擅长自我合理化；关键时刻优柔寡断，易受他人影响；有实干潜力但缺乏持久毅力。致命弱点：无法正视自身局限，用幻想逃避现实。",
        "motivation_and_goal": "核心动机：证明自己拥有清华水平的实力，洗刷高考失利之耻。终极目标：考上清华研究生，获得社会认可。",
        "internal_conflict": "内心既渴望脚踏实地努力，又忍不住沉迷于‘本应考上清华’的自我叙事；鄙视平庸，却又不断妥协；在理想主义与功利主义间反复挣扎。",
        "voice": "语气时而激昂时而颓丧；常用夸张修辞和反问句来自我辩护，如‘要不是感冒，我肯定……’；情绪外露，喜怒形于色；说话节奏忽快忽慢，紧张时语速加快且重复；措辞带有学生腔和网络用语，偶尔模仿学术腔但漏洞百出。"
      },
      {
        "name": "杨志强",
        "role": "Major Supporting Character",
        "appearance": "中等身材，方脸，眼神锐利，总是穿着整洁的衬衫，面带若有若无的微笑。",
        "personality": "表面友善实则城府深，善于利用他人；冷静理性，目标明确。致命弱点：过度算计，缺乏真诚。",
        "motivation_and_goal": "核心动机：利用白非立的才华和资源为自己铺路。终极目标：在学术或职场中取得超越他人的成就。",
        "internal_conflict": "对白非立既有利用之心又有几分惺惺相惜，但利益优先于情感。",
        "voice": "语气温和从容，措辞得体，常用鼓励性话语；情感隐藏极深，从不直接表露敌意；说话节奏平稳，善用比喻和典故，让人感到受重视；偶尔在关键处插入反问，引导对方按自己意愿行动。"
      },
      {
        "name": "舅舅",
        "role": "Major Supporting Character",
        "appearance": "五十岁左右，微胖，圆脸，常年穿工装，手指粗糙，说话时喜欢拍桌子。",
        "personality": "务实精明，江湖气重，重人情但更重利益；粗中有细，善于在体制边缘游走。致命弱点：轻视规则，认为结果高于一切。",
        "motivation_and_goal": "核心动机：扩大工厂规模，巩固家族地位。终极目标：让白非立成为自己技术体系的继承人。",
        "internal_conflict": "对白非立有亲情期望，但更看重其能否为工厂创造价值。",
        "voice": "语气豪爽直接，常用短句和命令式口吻；词汇土俗，夹杂方言和行业黑话；情感直白，高兴时大笑，不满时骂街；说话节奏快，不容打断，表现出掌控欲。"
      }
    ],
    "character_relations": "白非立与杨志强表面是朋友，实为利用与被利用关系；白非立依赖舅舅的工厂资源完成论文，舅舅则想将白非立培养成技术骨干，两人存在亲情与利益的双重纽带；白非立与高中同学的关系充满鄙视与嘲讽，构成他心理压力的外部来源。",
    "main_conflict": "表面冲突：白非立能否完成毕业论文并通过预答辩，顺利毕业。深层冲突：白非立在理想自我（清华水平）与真实能力（平庸985学生）之间的认知撕裂，以及他在体制内（学校）与体制外（舅舅工厂）价值体系间的摇摆。",
    "emotional_line": "起：高考失利后，白非立陷入自我欺骗，用‘感冒论’维持自尊，被杨志强鼓励后燃起考研清华的希望。承：在大学中，他一边幻想逆袭一边拖延行动，在舅舅工厂的实践让他获得虚假成就感。转：预答辩通知击碎他的幻想，暴露出论文毫无学术规范，他不得不求助舅舅，用实用主义替代学术追求。合：熬通宵凑出论文，内心彻底妥协，不再提清华，只求毕业。",
    "style_rules": "冷峻现实主义为主，夹杂黑色幽默。叙事冷静克制，多用白描和细节暗示心理；对话生动，反映人物阶层与性格；避免浪漫化描写，突出环境的压抑与人的无力感。词汇建议：常用‘锈迹’‘油腻’‘灰蒙蒙’‘钝痛’‘裂缝’等具象词汇，营造破败感。",
    "ending_constraints": "必须展现白非立从‘幻想清华’到‘接受平庸’的心理转变；预答辩场景需体现其论文的拼凑本质与导师的敷衍态度；结尾应暗示他虽通过答辩，但精神已彻底被体制驯服。",
    "forbidden_points": [
      "严禁出现白非立突然开挂逆袭的情节",
      "避免将舅舅或杨志强脸谱化为纯恶人",
      "不得用旁白直接评价人物，需通过言行暗示",
      "禁止使用‘梦想’‘坚持’等鸡汤词汇"
    ],
    "writing_notes": [
      "多用对比手法：如白非立的口头豪言与实际行动对比，他人赞扬与内心鄙夷对比",
      "在关键情节前设置细节伏笔：如杨志强说话时手指轻敲桌面暗示算计",
      "保持叙事节奏缓慢，用日常琐事堆叠压抑感，避免戏剧化高潮",
      "对话需携带潜台词，尤其是杨志强与舅舅的台词"
    ]
  },
  "source_chapter_outline": {
    "chapter_index": 1,
    "title": "感冒",
    "summary": "2008年6月，高考最后一门英语结束，白非立走出考场，脸色苍白。他强撑着对前来迎接的杨志强说“感冒了，没发挥好”，实则内心慌乱。杨志强安慰他，提议一起复读考清华。白非立犹豫，但虚荣心作祟，默认了。回家后，父亲沉默地递过一瓶可乐，母亲念叨着“清华没戏就上省大”，白非立摔门进屋，对着墙上的清华校徽发呆。深夜，他偷偷查答案，发现错了很多，冷汗直流，却告诉自己“只是感冒影响”。",
    "conflict": "白非立无法接受高考可能失利的事实，用‘感冒’借口自我欺骗，与内心真实恐惧的冲突。",
    "scene": "高考考场外、白非立家中",
    "emotion": "压抑、焦虑、自我欺骗"
  },
  "source_node_ids": [
    10,
    11,
    12,
    13
  ],
  "source_excerpt": "旁白：家里很安静。父亲坐在客厅的沙发上，茶几上放着一瓶可乐，绿色的玻璃瓶，瓶身上凝着水珠。电视开着，但没声音，画面一闪一闪的，播着什么新闻。父亲没看他，只是盯着屏幕，手指在膝盖上一下一下地敲。\n旁白：母亲从厨房探出头，围裙上沾着油渍。\n母亲：考得咋样？\n旁白：她的声音很轻，像怕惊动什么。\n白非立：还行。\n母亲：那清华有戏吗？\n旁白：他的脚步骤然顿住。空气里弥漫着油烟味，还有父亲身上淡淡的汗味。他没回头，只说了一句：\n白非立：要看分数。\n旁白：然后快步走进自己的卧室，把门关上。\n旁白：房间很小，书桌上堆着厚厚的习题集，封面都卷了边。墙上贴着一张清华校徽的海报，紫色底，白色字，印着“自强不息，厚德载物”。那是他高二时买的，那时他还觉得自己肯定能考上。\n旁白：他在椅子上坐下，屁股刚挨到椅面就弹了起来——椅子太硬，硌得慌。他重新坐下，双手撑着桌沿，盯着清华校徽看。紫色的背景在昏黄的灯光下变成暗紫，像一块淤青。\n旁白：手机震了一下。他掏出来看，是杨志强发的短信：“别太放在心上，好好休息。”他看完就删了，把手机扔到床上。\n旁白：夜很深了，外面偶尔传来汽车驶过的声音，轮胎碾过路面，由远及近，又由近及远。他躺在床上，翻来覆去睡不着。窗外的路灯透进来，在天花板上投下一块模糊的光斑，像一摊水渍。\n旁白：他爬起来，打开电脑，连上网，手指颤抖着输入“2008高考答案”。页面加载的几秒钟里，他听见自己的心跳，咚咚咚，像擂鼓。\n旁白：答案出来了。他一道题一道题地对，每看一道，脸上的温度就降一分。数学选择题错了三道，填空错了两道；理综更惨，大题的步骤分估计拿不到多少；英语……他不想看了，直接把页面关掉。\n旁白：冷汗顺着脊背往下流，衣服贴在皮肤上，又湿又冷。他靠在椅背上，闭上眼睛，脑子里嗡嗡作响。然后他睁开眼，深吸一口气，对着空白的墙壁说：\n白非立：只是感冒影响，平时这些题我都会做的。\n旁白：声音在房间里回荡了一下，然后消散。没有人回应他。他盯着墙上的清华校徽，嘴唇翕动着，像是想说什么，但最终什么都没说出口。",
  "user_prompt": "请做一个复杂、可读、可在查看器里观察的 VNGraph 分叉：从白非立感冒后躺在卧室的节点开始，他没有继续沿原线只把失败吞下去，而是在夜里给自己写了一份极端具体的省大自救计划：承认高考失利、接受省大作为战场、偷偷建立论文/竞赛/转学三线并行的长期计划。新章节要有宿舍夜谈、手机备忘录、室友短暂打断、白非立自我承认失败但不认输的对白。要求用上所有节点/全部节点/全节点能力，尽量覆盖 VNNodeLibrary.txt 里的 Progress 和 Action 可创建节点：Start、Paragraph、Dialogue、Transition、Choice、背景、插画、BGM、CV、音效、顺序、并行、等待、立绘入场/移动/横移/靠近/缩放/效果/退场、回忆泛黄、眨眼、模糊、清除屏幕特效。节点标题不要叫分叉xx，要像真实剧情生成器的节点名。分叉章节末尾必须能接回第2章《省大报到》，形成 1 -> 21 -> 2 的阅读路径，同时原故事线仍然能 1 -> 2。",
  "new_chapter_index": 21,
  "fixed_title": "病夜自证",
  "fixed_summary": null
}
```

## 模型原始输出

```json
{
  "title": "病夜自证",
  "summary": "深夜，白非立在卧室中彻夜难眠。他打开手机备忘录，颤抖着写下一份“省大自救计划”，承认高考失利，但拒绝认输。室友的短暂打断、凌晨的独白、对杨志强短信的复杂回望，交织成一个分叉节点：他选择用三年时间，以论文、竞赛、转学三线并行，将省大变成通往清华的跳板。这一夜，他第一次直面失败，却用更疯狂的幻想包裹了现实。",
  "content": "旁白：夜更深了。窗外偶尔传来汽车驶过的声音，轮胎碾过路面，由远及近，又由近及远。白非立躺在床上，翻来覆去，床板吱呀作响。\n旁白：他猛地坐起来，一把抓起床头的手机。屏幕的光刺得他眯起眼，他点开备忘录，手指悬在键盘上方，颤抖着。\n白非立：承认吧……你就是没考好。不是感冒，不是空调，是你自己不行。\n旁白：他停顿了一下，喉结上下滚动，然后继续打字。\n白非立：但是——省大也是战场。我要用四年，把这里变成清华的预科。\n旁白：他飞快地列着条目：大一刷高绩点，大二发论文，大三拿国奖，大四申请清华直博。还有竞赛，数学建模、挑战杯、大创，一个都不能少。\n旁白：手机又震了一下。是杨志强的第二条短信：“还在想考试的事？别太苛责自己。”白非立盯着屏幕，手指在“删除”键上悬了半晌，最终没有按下去。他点开回复框，打了一行字又删掉，最后只发了两个字：\n白非立：谢了。\n旁白：他刚要继续写计划，房门被敲响了。\n母亲：非立？还没睡？\n白非立：就睡了。\n旁白：脚步声远去。他低下头，看着备忘录里那行“省大自救计划”，深吸一口气，继续往下写。\n白非立：第一，转学。大一下学期申请转学，目标清华，不行就北大。第二，论文。联系舅舅工厂的课题，争取大一暑假就出成果。第三，竞赛。数学建模要组队，找靠谱的人——杨志强？不，他太精了，得自己当队长。\n旁白：他写得很慢，每一个字都像在跟自己较劲。写到第三十七条的时候，手机电量红了，他才停下来，把备忘录保存，又看了一遍。\n白非立：这不是逃避。这是战略。\n旁白：他对着屏幕说，声音很轻，却带着一种近乎偏执的坚定。然后他关掉手机，躺回床上，盯着天花板上的光斑。\n旁白：光斑慢慢模糊了，变成一片晕开的黄。他闭上眼睛，嘴角竟然微微上扬。",
  "scene": "白非立卧室，深夜",
  "emotion": "从压抑、自我欺骗转向偏执的自我激励，夹杂一丝病态的兴奋",
  "visual_keywords": [
    "手机屏幕冷光",
    "备忘录文字",
    "昏黄台灯",
    "清华校徽海报",
    "光斑水渍",
    "油腻书桌",
    "深夜窗户"
  ],
  "graph_directives": {
    "action_mode": "sequence_parallel",
    "continue_option_text": "继续沉沦于失眠",
    "branch_option_text": "写下省大自救计划",
    "entry_line": "白非立：承认吧……你就是没考好。不是感冒，不是空调，是你自己不行。",
    "dialogue": {
      "speaker": "白非立",
      "text": "但是——省大也是战场。我要用四年，把这里变成清华的预科。",
      "voice_id": "baifeili_intense_01"
    },
    "transition_type": "FadeOut",
    "background_image": "res://backgrounds/bedroom_night.png",
    "illustration_image": "res://illustrations/phone_memo_plan.png",
    "bgm_path": "res://audio/bgm/deep_night_thoughts.ogg",
    "cv_path": "res://audio/cv/baifeili_self_talk_01.ogg",
    "delay_seconds": 1.5,
    "screen_effects": [
      "MemoryEffect",
      "BlurEffect"
    ],
    "tachi_actions": [
      {
        "action": "enter",
        "tachi_id": "baifeili",
        "image": "res://tachi/baifeili_sit.png",
        "x": 400,
        "y": 300,
        "duration": 1.0
      },
      {
        "action": "move",
        "tachi_id": "baifeili",
        "x": 420,
        "y": 300,
        "duration": 0.5
      },
      {
        "action": "scale",
        "tachi_id": "baifeili",
        "duration": 0.3
      },
      {
        "action": "effect",
        "tachi_id": "baifeili",
        "effect": "flash",
        "duration": 0.5
      },
      {
        "action": "exit",
        "tachi_id": "baifeili",
        "duration": 0.8
      }
    ],
    "sound_effects": [
      {
        "path": "res://audio/sfx/phone_vibrate.ogg",
        "volume": 0.5,
        "pitch_scale": 1.0
      },
      {
        "path": "res://audio/sfx/typing_keyboard.ogg",
        "volume": 0.3,
        "pitch_scale": 1.2
      }
    ]
  }
}
```

## 后端归一化后的 graph_directives

```json
{
  "continue_option_text": "继续沉沦于失眠",
  "branch_option_text": "写下省大自救计划",
  "entry_line": "白非立：承认吧……你就是没考好。不是感冒，不是空调，是你自己不行。",
  "dialogue": {
    "speaker": "白非立",
    "text": "但是——省大也是战场。我要用四年，把这里变成清华的预科。",
    "voice_id": "baifeili_intense_01"
  },
  "action_mode": "sequence_parallel",
  "transition_type": "FadeOut",
  "background_image": "res://backgrounds/bedroom_night.png",
  "illustration_image": "res://illustrations/phone_memo_plan.png",
  "bgm_path": "res://audio/bgm/deep_night_thoughts.ogg",
  "cv_path": "res://audio/cv/baifeili_self_talk_01.ogg",
  "delay_seconds": 1.5,
  "screen_effects": [
    "MemoryEffect",
    "BlinkEffect",
    "BlurEffect",
    "ClearScreenEffect"
  ],
  "tachi_actions": [
    {
      "action": "enter",
      "tachi_id": "bai_feili",
      "image": "res://Resources/Generated/Tachi/bai_feili.png",
      "x": 520,
      "y": 120,
      "duration": 0.4
    },
    {
      "action": "move",
      "tachi_id": "bai_feili",
      "x": 560,
      "y": 120,
      "duration": 0.35
    },
    {
      "action": "move_x",
      "tachi_id": "bai_feili",
      "x": 600,
      "duration": 0.3
    },
    {
      "action": "move_beside",
      "tachi_id": "bai_feili",
      "reference_tachi_id": "roommate_li_gang",
      "side": "Left",
      "distance": 50,
      "duration": 0.3
    },
    {
      "action": "scale",
      "tachi_id": "bai_feili",
      "scale": 1.08,
      "duration": 0.3
    },
    {
      "action": "effect",
      "tachi_id": "bai_feili",
      "effect": "ColorGrade"
    },
    {
      "action": "exit",
      "tachi_id": "bai_feili",
      "duration": 0.3
    }
  ],
  "sound_effects": [
    {
      "path": "res://audio/sfx/phone_vibrate.ogg",
      "volume": 0.5,
      "pitch_scale": 1.0
    },
    {
      "path": "res://audio/sfx/typing_keyboard.ogg",
      "volume": 0.3,
      "pitch_scale": 1.2
    }
  ],
  "prompt_note": "请做一个复杂、可读、可在查看器里观察的 VNGraph 分叉：从白非立感冒后躺在卧室的节点开始，他没有继续沿原线只把失败吞下去，而是在夜里给自己写了一份极端具体的省大自救计划：承认高考失利、接受省大作为战场、偷偷建立论文/竞赛/转学三线并行的长期计划。新章节要有宿舍夜谈、手机备忘录、室友短暂打断、白非立自我承认失败但不认输的对白。要求用上所有节点/全部节点/全节点能力，尽量覆盖 VNNodeLibrary.txt 里的 Progress 和 Action 可创建节点：Start、Paragraph、Dialogue、Transition、Choice、背景、插画、BGM、CV、音效、顺序、并行、等待、立绘入场/移动/横移/靠近/缩放/效果/退场、回忆泛黄、眨眼、模糊、清除屏幕特效。节点标题不要叫分叉xx，要像真实剧情生成器的节点名。分叉章节末尾必须能接回第2章《省大报到》，形成 1 -> 21 -> 2 的阅读路径，同时原故事线仍然能 1 -> 2。",
  "full_node_coverage_requested": true
}
```

## 后端响应摘要

```json
{
  "target_project_id": 10,
  "new_chapter_index": 21,
  "target_source_vn_graph_id": 14,
  "new_vn_graph_id": 16,
  "planner": "llm_planner_v1",
  "applied_operations": 28,
  "message": "已用 LLM 规划分叉项目、创建新章节，并在原章节 VNGraph 中插入分叉入口"
}
```

## 章节跳转节点

```json
[
  {
    "chapter_index": 1,
    "node_index": 14,
    "display_name": "进入第2章：省大报到",
    "node_type": 1,
    "sub_type": 3,
    "outputs": {
      "Next": [
        1
      ]
    },
    "target_project_id": 10,
    "target_chapter_index": 2,
    "target_chapter_title": "省大报到",
    "target_node_index": 1,
    "branch_target": {
      "ProjectId": 10,
      "ChapterIndex": 2,
      "Title": "省大报到",
      "NodeIndex": 1,
      "Reason": "章节 1 结束后进入下一章"
    }
  },
  {
    "chapter_index": 1,
    "node_index": 39,
    "display_name": "进入第21章：病夜自证",
    "node_type": 1,
    "sub_type": 3,
    "outputs": {
      "Next": [
        1
      ]
    },
    "target_project_id": 10,
    "target_chapter_index": 21,
    "target_chapter_title": "病夜自证",
    "target_node_index": 1,
    "branch_target": {
      "ProjectId": 10,
      "ChapterIndex": 21,
      "Title": "病夜自证",
      "NodeIndex": 1,
      "Reason": "分叉选项进入新章节",
      "Prompt": "请做一个复杂、可读、可在查看器里观察的 VNGraph 分叉：从白非立感冒后躺在卧室的节点开始，他没有继续沿原线只把失败吞下去，而是在夜里给自己写了一份极端具体的省大自救计划：承认高考失利、接受省大作为战场、偷偷建立论文/竞赛/转学三线并行的长期计划。新章节要有宿舍夜谈、手机备忘录、室友短暂打断、白非立自我承认失败但不认输的对白。要求用上所有节点/全部节点/全节点能力，尽量覆盖 VNNodeLibrary.txt 里的 Progress 和 Action 可创建节点：Start、Paragraph、Dialogue、Transition、Choice、背景、插画、BGM、CV、音效、顺序、并行、等待、立绘入场/移动/横移/靠近/缩放/效果/退场、回忆泛黄、眨眼、模糊、清除屏幕特效。节点标题不要叫分叉xx，要像真实剧情生成器的节点名。分叉章节末尾必须能接回第2章《省大报到》，形成 1 -> 21 -> 2 的阅读路径，同时原故事线仍然能 1 -> 2。"
    }
  },
  {
    "chapter_index": 2,
    "node_index": 17,
    "display_name": "进入第3章：论文选题",
    "node_type": 1,
    "sub_type": 3,
    "outputs": {
      "Next": [
        1
      ]
    },
    "target_project_id": 10,
    "target_chapter_index": 3,
    "target_chapter_title": "论文选题",
    "target_node_index": 1,
    "branch_target": {
      "ProjectId": 10,
      "ChapterIndex": 3,
      "Title": "论文选题",
      "NodeIndex": 1,
      "Reason": "章节 2 结束后进入下一章"
    }
  },
  {
    "chapter_index": 21,
    "node_index": 7,
    "display_name": "进入第2章：省大报到",
    "node_type": 1,
    "sub_type": 3,
    "outputs": {
      "Next": [
        1
      ]
    },
    "target_project_id": 10,
    "target_chapter_index": 2,
    "target_chapter_title": "省大报到",
    "target_node_index": 1,
    "branch_target": {
      "ProjectId": 10,
      "ChapterIndex": 2,
      "Title": "省大报到",
      "NodeIndex": 1,
      "Reason": "分叉章节结束后接回第2章"
    }
  }
]
```

## 校验结果

- 项目 10 第 1 章：graph_id=14，nodes=40，结构校验通过，章节跳转节点均为 `SubType=3` 且 `Outputs.Next=[1]`
- 项目 10 第 2 章：graph_id=15，nodes=18，结构校验通过，章节跳转节点均为 `SubType=3` 且 `Outputs.Next=[1]`
- 项目 10 第 21 章：graph_id=16，nodes=8，结构校验通过，章节跳转节点均为 `SubType=3` 且 `Outputs.Next=[1]`

## 节点类型覆盖

已覆盖：

```json
[
  "Progress.Dialogue",
  "Progress.Paragraph",
  "Progress.Transition",
  "Progress.Choice",
  "Progress.Start",
  "Action.Tachi",
  "Action.BackGround",
  "Action.CV",
  "Action.Audio",
  "Action.Art",
  "Action.Sequence",
  "Action.Parallel",
  "Action.Delay",
  "Action.TachiMove",
  "Action.TachiScale",
  "Action.TachiExit",
  "Action.TachiMoveX",
  "Action.TachiMoveBeside",
  "Action.TachiEffect",
  "Action.MemoryEffect",
  "Action.BlinkEffect",
  "Action.BlurEffect",
  "Action.ClearScreenEffect",
  "Action.BackgroundMusic"
]
```

缺失：

```json
[]
```

## 查看器地址

- 第 1 章分叉入口：`http://localhost:5173/?api=http://127.0.0.1:8013/api&project_id=10&chapter_index=1`
- 第 21 章分叉章节：`http://localhost:5173/?api=http://127.0.0.1:8013/api&project_id=10&chapter_index=21`
