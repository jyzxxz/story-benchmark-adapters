# 🎮 大模型生成 VNNode 详细讲解

## 📖 目录

1. [方案概述](#方案概述)
2. [核心架构](#核心架构)
3. [实现细节](#实现细节)
4. [使用方法](#使用方法)
5. [对比分析](#对比分析)
6. [扩展建议](#扩展建议)

---

## 方案概述

### 问题背景

你的项目原本使用**硬编码规则**在 `vn_graph_generator.py` 中生成 VNGraph，存在以下问题：

1. **对话解析过于简单**：使用正则表达式匹配 `角色名：对话` 格式
   ```python
   dialogue_match = re.match(r'^["「]?([^"「」：:\n]{1,10})[」：:]\s*["「]?([^"「」]*)["」]?$', line)
   ```
   - 无法处理复杂对话格式（如：带动作的对话、心理独白）
   - 无法识别说话人的情绪和表情变化

2. **缺乏语义理解**：
   - 无法识别场景转换（"夜幕降临，他们来到了城门..."）
   - 无法识别情绪变化（"他的眼神变得锐利"）
   - 无法识别动作描写（"她猛地站起身"）

3. **动作节点固定**：
   - 每个章节都是固定的背景+BGM+立绘
   - 无法根据剧情动态调整（如：战斗场景需要切换背景）

4. **选择点检测粗糙**：
   - 仅通过关键词检测：`["是否", "要不要", "选择", "决定", "是否要"]`
   - 无法生成有意义的选择分支

### 解决方案

使用**两阶段生成法**：

```
章节正文 → [LLM语义分析] → 结构化场景数据 → [规则生成器] → VNGraph JSON
```

**第一阶段：LLM 语义分析**
- 使用大模型理解章节内容的语义
- 提取场景、对话、旁白、动作、情绪、特效等
- 识别选择分支点

**第二阶段：规则生成器**
- 根据结构化数据，按照 VNNodeLibrary.txt 规范生成节点
- 确保节点连接正确、Index 唯一、格式合法

---

## 核心架构

### 文件结构

```
backend/app/services/
├── llm_vn_graph_generator.py  # 新增：LLM 驱动的 VNGraph 生成器
├── vn_graph_generator.py      # 原有：硬编码生成器（保留作为对比）
└── llm_service.py             # LLM 服务基础类

backend/app/utils/
└── vn_graph_validator.py      # VNGraph 校验器
```

### 类设计

```python
class LLMVNGraphGenerator:
    """基于大模型的 VNGraph 生成器"""

    async def generate_vn_graph(
        self,
        chapter_content: str,      # 章节正文
        chapter_outline: Dict,     # 章节大纲
        story_bible: Dict,         # Story Bible
        characters: List[Dict],    # 角色列表
        visual_assets: List[Dict], # 视觉素材
        chapter_index: int         # 章节索引
    ) -> Dict[str, Any]:
        """主入口：生成 VNGraph"""

    async def _analyze_chapter_content(self, ...) -> Dict[str, Any]:
        """第一步：使用 LLM 分析章节内容"""

    async def _build_vn_graph(self, ...) -> Dict[str, Any]:
        """第二步：根据分析结果构建 VNGraph"""
```

---

## 实现细节

### 第一步：LLM 语义分析

#### Prompt 设计

```python
prompt = f"""请分析以下小说章节内容，提取视觉小说所需的结构化场景数据。

【章节大纲】
{json.dumps(chapter_outline, ensure_ascii=False, indent=2)}

【故事设定】
世界观: {story_bible.get('worldview', '未知')}
主题: {story_bible.get('theme_and_tone', '未知')}

【出场角色】
{char_info}

【章节正文】
{chapter_content}

【分析要求】

1. **场景划分**：
   - 根据地点变化、时间跳跃、情绪转折划分场景
   - 每个场景应包含：位置、背景描述、在场角色、内容序列

2. **内容类型识别**：
   - dialogue: 对话（需识别说话人和情绪）
   - narration: 旁白/心理描写
   - action: 动作描写（可能需要特效）
   - transition: 场景转换

3. **角色状态追踪**：
   - 记录每个角色的入场/退场方式
   - 追踪角色位置变化（左/中/右）
   - 识别表情/情绪变化

4. **选择分支识别**：
   - 识别剧情中的关键决策点
   - 为每个选择生成2-4个选项
   - 选项应影响后续剧情走向

5. **视听效果建议**：
   - 背景音乐变化点
   - 屏幕特效需求（回忆、模糊、闪烁等）
   - 立绘动画需求
"""
```

#### 输出格式

LLM 返回的结构化数据：

```json
{
  "scenes": [
    {
      "scene_id": 1,
      "scene_type": "dialogue",
      "location": "燕国宫殿",
      "background": "夜晚的燕宫大殿，烛火摇曳",
      "characters_present": ["荆轲", "太子丹"],
      "content": [
        {
          "type": "dialogue",
          "speaker": "太子丹",
          "text": "荆卿，秦兵不会等我们。",
          "emotion": "焦虑",
          "actions": ["站起", "踱步"]
        },
        {
          "type": "narration",
          "text": "太子丹的声音在空旷的大殿中回荡。"
        },
        {
          "type": "dialogue",
          "speaker": "荆轲",
          "text": "臣明白。但此行凶险，需万全准备。",
          "emotion": "冷静",
          "actions": ["拱手"]
        }
      ],
      "visual_effects": [],
      "bgm_change": "紧张",
      "emotion_tone": "压抑"
    },
    {
      "scene_id": 2,
      "scene_type": "transition",
      "location": "易水河畔",
      "background": "易水河畔，秋风萧瑟，枯叶纷飞",
      "characters_present": ["荆轲", "高渐离"],
      "content": [
        {
          "type": "narration",
          "text": "数日后，易水河畔。"
        },
        {
          "type": "dialogue",
          "speaker": "高渐离",
          "text": "风萧萧兮易水寒，壮士一去兮不复还！",
          "emotion": "悲壮",
          "actions": ["击筑"]
        }
      ],
      "visual_effects": ["回忆泛黄"],
      "bgm_change": "悲壮",
      "emotion_tone": "悲凉"
    }
  ],
  "choices": [
    {
      "choice_id": 1,
      "prompt": "荆轲面临抉择...",
      "context": "太子丹催促出发，但准备尚未万全",
      "options": [
        {
          "text": "立即启程，以速取胜",
          "consequence_hint": "可能准备不足，但能抓住时机"
        },
        {
          "text": "坚持准备，等待时机",
          "consequence_hint": "准备充分，但可能错失良机"
        }
      ]
    }
  ],
  "character_states": {
    "荆轲": {
      "initial_position": "left",
      "enter_animation": "SlideInLeft",
      "exit_animation": "FadeOut",
      "expressions": ["冷静", "决绝", "悲壮"]
    },
    "太子丹": {
      "initial_position": "right",
      "enter_animation": "FadeIn",
      "exit_animation": "SlideOutRight",
      "expressions": ["焦虑", "期待", "失望"]
    }
  },
  "transitions": [
    {
      "from_scene": 1,
      "to_scene": 2,
      "transition_type": "FadeOut",
      "duration": 0.8
    }
  ]
}
```

### 第二步：规则生成器

根据结构化数据生成 VNGraph JSON：

#### 生成流程

```python
async def _build_vn_graph(self, scene_data, visual_assets, chapter_index):
    nodes = []
    node_index = 1

    # 1. 创建 StartNode
    start_node = self._create_node(
        node_index=1,
        display_name="开始",
        node_type=1,  # Progress
        sub_type=6,   # Start
        data={},
        outputs={"Next": []}
    )
    nodes.append(start_node)

    # 2. 处理每个场景
    for scene in scene_data["scenes"]:
        # 2.1 创建动作组（背景、立绘）
        action_nodes = []

        # 背景切换
        bg_node = self._create_background_node(...)
        action_nodes.append(bg_node["Index"])

        # 角色入场
        for char_name in scene["characters_present"]:
            tachi_node = self._create_tachi_node(...)
            action_nodes.append(tachi_node["Index"])

        # 2.2 创建内容节点（对话/旁白）
        content_nodes = self._create_content_nodes(scene["content"])

        # 第一个内容节点连接动作组
        content_nodes[0]["Outputs"]["Actions"] = action_nodes

        # 2.3 处理特效
        for effect in scene["visual_effects"]:
            effect_node = self._create_effect_node(effect)

    # 3. 处理选择分支
    for choice in scene_data["choices"]:
        choice_node = self._create_choice_node(choice)

    # 4. 构建完整 VNGraph
    return {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": nodes
    }
```

#### 节点创建示例

**背景节点：**

```python
def _create_background_node(self, background_desc, asset_map, chapter_index):
    return {
        "Index": node_index,
        "DisplayName": f"背景: {background_desc[:20]}",
        "NodeType": 2,  # Action
        "SubType": 3,   # BackGround
        "Data": {
            "BackgroundImage": {"Kind": "String", "StringValue": bg_path},
            "ChangeType": {"Kind": "Enum", "StringValue": "FadeIn"},
            "Duration": {"Kind": "Float", "NumberValue": 0.5}
        },
        "Outputs": {}
    }
```

**立绘节点：**

```python
def _create_tachi_node(self, char_name, char_state, asset_map):
    position = char_state.get("initial_position", "left")
    x_positions = {"left": 520, "center": 620, "right": 720}
    target_x = x_positions.get(position, 520)

    return {
        "Index": node_index,
        "DisplayName": f"{char_name}立绘",
        "NodeType": 2,  # Action
        "SubType": 1,   # Tachi
        "Data": {
            "TachiID": {"Kind": "String", "StringValue": char_id},
            "TachiIamge": {"Kind": "String", "StringValue": tachi_path},
            "TargetPosition": {"Kind": "Vector2", "X": target_x, "Y": 120},
            "EnterType": {"Kind": "Enum", "StringValue": enter_type},
            "Duration": {"Kind": "Float", "NumberValue": 0.3}
        },
        "Outputs": {}
    }
```

**段落节点：**

```python
def _create_content_nodes(self, content_list):
    lines = []
    for content in content_list:
        if content["type"] == "dialogue":
            lines.append({
                "SpeakerId": content["speaker"],
                "Text": content["text"],
                "VoiceId": ""
            })
        elif content["type"] == "narration":
            lines.append({
                "SpeakerId": "旁白",
                "Text": content["text"],
                "VoiceId": ""
            })

    return {
        "Index": node_index,
        "DisplayName": "段落",
        "NodeType": 1,  # Progress
        "SubType": 2,   # Paragraph
        "Data": {
            "Lines": {"Kind": "List", "Items": lines_data}
        },
        "Outputs": {"Next": []}
    }
```

**选择节点：**

```python
def _create_choice_node(self, choice_data):
    options = choice_data["options"]
    options_data = [
        {"Kind": "Object", "ObjectValue": {"Text": {"Kind": "String", "StringValue": opt["text"]}}}
        for opt in options
    ]

    outputs = {}
    for i in range(len(options)):
        outputs[f"Options[{i}].Next"] = []

    return {
        "Index": node_index,
        "DisplayName": "选择",
        "NodeType": 1,  # Progress
        "SubType": 5,   # Choice
        "Data": {
            "Options": {"Kind": "List", "Items": options_data}
        },
        "Outputs": outputs
    }
```

---

## 使用方法

### 1. 基本使用

```python
from app.services.llm_vn_graph_generator import llm_vn_graph_generator

# 准备数据
chapter_content = """
夜色压在燕宫的铜灯上。

太子丹：荆卿，秦兵不会等我们。

荆轲：臣明白。但此行凶险，需万全准备。

太子丹踱步于殿中，烛火在他脸上投下摇曳的光影。

太子丹：我已等了太久。燕国的存亡，全系于你此行。
"""

chapter_outline = {
    "chapter_index": 1,
    "title": "易水寒",
    "summary": "太子丹催促荆轲出发，荆轲面临抉择"
}

story_bible = {
    "worldview": "战国末期，秦灭六国",
    "theme_and_tone": "悲壮、压抑、决绝"
}

characters = [
    {"name": "荆轲", "role": "主角", "personality": "冷静、果决"},
    {"name": "太子丹", "role": "重要配角", "personality": "焦虑、急切"}
]

visual_assets = []

# 生成 VNGraph
vn_graph = await llm_vn_graph_generator.generate_vn_graph(
    chapter_content=chapter_content,
    chapter_outline=chapter_outline,
    story_bible=story_bible,
    characters=characters,
    visual_assets=visual_assets,
    chapter_index=1
)

# 输出结果
print(json.dumps(vn_graph, ensure_ascii=False, indent=2))
```

### 2. 集成到现有流程

修改 `backend/app/routers/vn_graph.py`：

```python
from app.services.llm_vn_graph_generator import llm_vn_graph_generator

@router.post("/projects/{project_id}/chapters/{chapter_index}/generate-llm")
async def generate_vn_graph_with_llm(
    project_id: int,
    chapter_index: int,
    db: AsyncSession = Depends(get_db)
):
    """使用 LLM 生成 VNGraph"""

    # 获取数据
    project = await get_project(db, project_id)
    chapter_content = await get_chapter_content(db, project_id, chapter_index)
    chapter_outline = await get_chapter_outline(db, project_id, chapter_index)
    story_bible = await get_story_bible(db, project_id)
    characters = story_bible.characters
    visual_assets = await get_visual_assets(db, project_id, chapter_index)

    # 使用 LLM 生成
    vn_graph = await llm_vn_graph_generator.generate_vn_graph(
        chapter_content=chapter_content.content,
        chapter_outline=chapter_outline.dict(),
        story_bible=story_bible.dict(),
        characters=characters,
        visual_assets=visual_assets,
        chapter_index=chapter_index
    )

    # 保存到数据库
    db_vn_graph = VNGraph(
        project_id=project_id,
        chapter_index=chapter_index,
        graph_json=vn_graph,
        status="generated"
    )
    db.add(db_vn_graph)
    await db.commit()

    return {"message": "VNGraph generated successfully", "vn_graph": vn_graph}
```

---

## 对比分析

### 硬编码 vs LLM 生成

| 维度 | 硬编码方式 | LLM 生成方式 |
|------|-----------|-------------|
| **对话识别** | 正则表达式匹配 `角色：对话` | LLM 理解语义，识别情绪、动作 |
| **场景转换** | 无法识别 | LLM 识别地点、时间、情绪变化 |
| **角色状态** | 固定位置、固定动画 | LLM 推断位置、动画、表情 |
| **选择分支** | 关键词检测 | LLM 识别决策点，生成有意义选项 |
| **特效识别** | 无 | LLM 识别回忆、模糊、闪烁等 |
| **灵活性** | 低（需修改代码） | 高（修改 prompt 即可） |
| **成本** | 无（纯规则） | 有（LLM API 调用） |
| **速度** | 快（毫秒级） | 慢（秒级） |
| **准确性** | 低（格式依赖） | 高（语义理解） |

### 示例对比

**输入文本：**

```
夜色压在燕宫的铜灯上。

太子丹踱步于殿中，烛火在他脸上投下摇曳的光影。

太子丹：荆卿，秦兵不会等我们。

荆轲沉默片刻，缓缓开口。

荆轲：臣明白。但此行凶险，需万全准备。
```

**硬编码输出：**

```json
{
  "Nodes": [
    {
      "Index": 1,
      "DisplayName": "开始",
      "NodeType": 1,
      "SubType": 6,
      "Data": {}
    },
    {
      "Index": 2,
      "DisplayName": "段落",
      "NodeType": 1,
      "SubType": 2,
      "Data": {
        "Lines": {
          "Kind": "List",
          "Items": [
            {
              "SpeakerId": "旁白",
              "Text": "夜色压在燕宫的铜灯上。"
            },
            {
              "SpeakerId": "旁白",
              "Text": "太子丹踱步于殿中，烛火在他脸上投下摇曳的光影。"
            },
            {
              "SpeakerId": "太子丹",
              "Text": "荆卿，秦兵不会等我们。"
            },
            {
              "SpeakerId": "旁白",
              "Text": "荆轲沉默片刻，缓缓开口。"
            },
            {
              "SpeakerId": "荆轲",
              "Text": "臣明白。但此行凶险，需万全准备。"
            }
          ]
        }
      }
    }
  ]
}
```

**LLM 生成输出：**

```json
{
  "Nodes": [
    {
      "Index": 1,
      "DisplayName": "开始",
      "NodeType": 1,
      "SubType": 6,
      "Data": {}
    },
    {
      "Index": 2,
      "DisplayName": "背景: 夜晚的燕宫大殿",
      "NodeType": 2,
      "SubType": 3,
      "Data": {
        "BackgroundImage": "res://Resources/Backgrounds/yan_palace_night.png",
        "ChangeType": "FadeIn",
        "Duration": 0.5
      }
    },
    {
      "Index": 3,
      "DisplayName": "太子丹立绘",
      "NodeType": 2,
      "SubType": 1,
      "Data": {
        "TachiID": "prince_dan",
        "TachiIamge": "res://Resources/Tachi/prince_dan.png",
        "TargetPosition": {"X": 720, "Y": 120},
        "EnterType": "SlideInRight",
        "Duration": 0.3
      }
    },
    {
      "Index": 4,
      "DisplayName": "荆轲立绘",
      "NodeType": 2,
      "SubType": 1,
      "Data": {
        "TachiID": "jing_ke",
        "TachiIamge": "res://Resources/Tachi/jing_ke.png",
        "TargetPosition": {"X": 520, "Y": 120},
        "EnterType": "SlideInLeft",
        "Duration": 0.3
      }
    },
    {
      "Index": 5,
      "DisplayName": "段落",
      "NodeType": 1,
      "SubType": 2,
      "Data": {
        "Lines": {
          "Kind": "List",
          "Items": [
            {
              "SpeakerId": "旁白",
              "Text": "夜色压在燕宫的铜灯上。太子丹踱步于殿中，烛火在他脸上投下摇曳的光影。"
            },
            {
              "SpeakerId": "太子丹",
              "Text": "荆卿，秦兵不会等我们。"
            },
            {
              "SpeakerId": "旁白",
              "Text": "荆轲沉默片刻，缓缓开口。"
            },
            {
              "SpeakerId": "荆轲",
              "Text": "臣明白。但此行凶险，需万全准备。"
            }
          ]
        }
      },
      "Outputs": {
        "Actions": [2, 3, 4],
        "Next": []
      }
    }
  ]
}
```

**关键差异：**

1. **LLM 识别了场景**：夜晚的燕宫大殿
2. **LLM 识别了角色入场**：太子丹从右侧入场，荆轲从左侧入场
3. **LLM 合并了旁白**：将连续的旁白合并，减少节点数量
4. **LLM 推断了动画**：根据角色性格和场景氛围选择入场动画

---

## 扩展建议

### 1. 添加更多节点类型

当前实现支持：
- StartNode
- ParagraphNode
- ChoiceNode
- BackGroundNode
- TachiNode
- EffectNode

可以扩展支持：
- **DialogueNode**：单句对话节点（适合重要台词）
- **TransitionNode**：转场节点（场景切换）
- **ActionSequenceNode**：顺序动作组
- **ActionParallelNode**：并行动作组
- **TachiMoveNode**：立绘移动
- **TachiEffectNode**：立绘特效
- **BackgroundMusicNode**：背景音乐

### 2. 优化 Prompt

可以添加更多约束：

```python
【额外约束】
1. 每个场景的对话数量不超过 10 句
2. 选择分支至少 2 个选项，最多 4 个
3. 特效使用要克制，避免滥用
4. 背景音乐变化要平滑，避免突兀
5. 角色位置要合理，避免重叠
```

### 3. 添加校验和修正

```python
async def generate_vn_graph(self, ...):
    # 生成
    vn_graph = await self._build_vn_graph(...)

    # 校验
    is_valid, errors = self.validator.validate(vn_graph)

    if not is_valid:
        # 使用 LLM 修正错误
        vn_graph = await self._fix_errors(vn_graph, errors)

    return vn_graph
```

### 4. 添加缓存

```python
from functools import lru_cache

@lru_cache(maxsize=100)
async def _analyze_chapter_content_cached(
    self,
    chapter_content_hash: str,
    ...
):
    """带缓存的分析"""
    return await self._analyze_chapter_content(...)
```

### 5. 添加流式生成

```python
async def generate_vn_graph_streaming(self, ...):
    """流式生成 VNGraph"""

    # 第一阶段：分析
    scene_data = await self._analyze_chapter_content(...)

    # 第二阶段：逐场景生成
    for scene in scene_data["scenes"]:
        nodes = self._create_nodes_for_scene(scene)
        yield nodes  # 逐步返回节点
```

---

## 总结

### 优势

1. **语义理解**：LLM 能理解文本的深层含义，而非简单的模式匹配
2. **灵活性**：通过修改 prompt 即可调整生成策略，无需修改代码
3. **准确性**：能识别场景转换、情绪变化、动作描写等复杂语义
4. **可扩展**：易于添加新的节点类型和生成规则

### 劣势

1. **成本**：需要调用 LLM API，有费用成本
2. **速度**：生成速度较慢（秒级 vs 毫秒级）
3. **不确定性**：LLM 输出可能不稳定，需要校验和修正

### 建议

- **开发阶段**：使用 LLM 生成，快速迭代
- **生产阶段**：根据成本和速度要求，选择合适的方式
- **混合方案**：关键场景使用 LLM，普通场景使用硬编码

---

## 附录：完整示例

### 输入

```text
【章节正文】

夜色压在燕宫的铜灯上。

太子丹踱步于殿中，烛火在他脸上投下摇曳的光影。

太子丹：荆卿，秦兵不会等我们。

荆轲沉默片刻，缓缓开口。

荆轲：臣明白。但此行凶险，需万全准备。

太子丹停下脚步，目光灼灼地看向荆轲。

太子丹：我已等了太久。燕国的存亡，全系于你此行。

荆轲抬起头，眼中闪过一丝决绝。

荆轲：臣，愿往。

数日后，易水河畔。

秋风萧瑟，枯叶纷飞。高渐离击筑而歌。

高渐离：风萧萧兮易水寒，壮士一去兮不复还！

荆轲转身，向燕宫方向深深一拜，随后头也不回地踏上征程。
```

### 输出

```json
{
  "Version": 1,
  "StartNodeIndex": 1,
  "Nodes": [
    {
      "Index": 1,
      "DisplayName": "开始",
      "NodeType": 1,
      "SubType": 6,
      "X": 80,
      "Y": 80,
      "Data": {},
      "Outputs": {"Next": [5]}
    },
    {
      "Index": 2,
      "DisplayName": "背景: 夜晚的燕宫大殿",
      "NodeType": 2,
      "SubType": 3,
      "X": 80,
      "Y": 430,
      "Data": {
        "BackgroundImage": {"Kind": "String", "StringValue": "res://Resources/Backgrounds/yan_palace_night.png"},
        "ChangeType": {"Kind": "Enum", "StringValue": "FadeIn"},
        "Duration": {"Kind": "Float", "NumberValue": 0.5}
      },
      "Outputs": {}
    },
    {
      "Index": 3,
      "DisplayName": "太子丹立绘",
      "NodeType": 2,
      "SubType": 1,
      "X": 280,
      "Y": 430,
      "Data": {
        "TachiID": {"Kind": "String", "StringValue": "prince_dan"},
        "TachiIamge": {"Kind": "String", "StringValue": "res://Resources/Tachi/prince_dan.png"},
        "TargetPosition": {"Kind": "Vector2", "X": 720, "Y": 120},
        "EnterType": {"Kind": "Enum", "StringValue": "SlideInRight"},
        "Duration": {"Kind": "Float", "NumberValue": 0.3}
      },
      "Outputs": {}
    },
    {
      "Index": 4,
      "DisplayName": "荆轲立绘",
      "NodeType": 2,
      "SubType": 1,
      "X": 480,
      "Y": 430,
      "Data": {
        "TachiID": {"Kind": "String", "StringValue": "jing_ke"},
        "TachiIamge": {"Kind": "String", "StringValue": "res://Resources/Tachi/jing_ke.png"},
        "TargetPosition": {"Kind": "Vector2", "X": 520, "Y": 120},
        "EnterType": {"Kind": "Enum", "StringValue": "SlideInLeft"},
        "Duration": {"Kind": "Float", "NumberValue": 0.3}
      },
      "Outputs": {}
    },
    {
      "Index": 5,
      "DisplayName": "段落",
      "NodeType": 1,
      "SubType": 2,
      "X": 430,
      "Y": 80,
      "Data": {
        "Lines": {
          "Kind": "List",
          "Items": [
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {"Kind": "String", "StringValue": "旁白"},
                "Text": {"Kind": "String", "StringValue": "夜色压在燕宫的铜灯上。太子丹踱步于殿中，烛火在他脸上投下摇曳的光影。"},
                "VoiceId": {"Kind": "String", "StringValue": ""}
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {"Kind": "String", "StringValue": "太子丹"},
                "Text": {"Kind": "String", "StringValue": "荆卿，秦兵不会等我们。"},
                "VoiceId": {"Kind": "String", "StringValue": ""}
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {"Kind": "String", "StringValue": "旁白"},
                "Text": {"Kind": "String", "StringValue": "荆轲沉默片刻，缓缓开口。"},
                "VoiceId": {"Kind": "String", "StringValue": ""}
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {"Kind": "String", "StringValue": "荆轲"},
                "Text": {"Kind": "String", "StringValue": "臣明白。但此行凶险，需万全准备。"},
                "VoiceId": {"Kind": "String", "StringValue": ""}
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {"Kind": "String", "StringValue": "旁白"},
                "Text": {"Kind": "String", "StringValue": "太子丹停下脚步，目光灼灼地看向荆轲。"},
                "VoiceId": {"Kind": "String", "StringValue": ""}
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {"Kind": "String", "StringValue": "太子丹"},
                "Text": {"Kind": "String", "StringValue": "我已等了太久。燕国的存亡，全系于你此行。"},
                "VoiceId": {"Kind": "String", "StringValue": ""}
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {"Kind": "String", "StringValue": "旁白"},
                "Text": {"Kind": "String", "StringValue": "荆轲抬起头，眼中闪过一丝决绝。"},
                "VoiceId": {"Kind": "String", "StringValue": ""}
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {"Kind": "String", "StringValue": "荆轲"},
                "Text": {"Kind": "String", "StringValue": "臣，愿往。"},
                "VoiceId": {"Kind": "String", "StringValue": ""}
              }
            }
          ]
        }
      },
      "Outputs": {
        "Actions": [2, 3, 4],
        "Next": [7]
      }
    },
    {
      "Index": 6,
      "DisplayName": "特效: 回忆泛黄",
      "NodeType": 2,
      "SubType": 17,
      "X": 780,
      "Y": 80,
      "Data": {
        "Intensity": {"Kind": "Float", "NumberValue": 0.75},
        "Duration": {"Kind": "Float", "NumberValue": 0.5}
      },
      "Outputs": {}
    },
    {
      "Index": 7,
      "DisplayName": "背景: 易水河畔",
      "NodeType": 2,
      "SubType": 3,
      "X": 780,
      "Y": 430,
      "Data": {
        "BackgroundImage": {"Kind": "String", "StringValue": "res://Resources/Backgrounds/yishui_river.png"},
        "ChangeType": {"Kind": "Enum", "StringValue": "FadeIn"},
        "Duration": {"Kind": "Float", "NumberValue": 0.8}
      },
      "Outputs": {}
    },
    {
      "Index": 8,
      "DisplayName": "高渐离立绘",
      "NodeType": 2,
      "SubType": 1,
      "X": 980,
      "Y": 430,
      "Data": {
        "TachiID": {"Kind": "String", "StringValue": "gao_jianli"},
        "TachiIamge": {"Kind": "String", "StringValue": "res://Resources/Tachi/gao_jianli.png"},
        "TargetPosition": {"Kind": "Vector2", "X": 620, "Y": 120},
        "TargetPosition": {"Kind": "Vector2", "X": 620, "Y": 120},
        "EnterType": {"Kind": "Enum", "StringValue": "FadeIn"},
        "Duration": {"Kind": "Float", "NumberValue": 0.3}
      },
      "Outputs": {}
    },
    {
      "Index": 9,
      "DisplayName": "段落",
      "NodeType": 1,
      "SubType": 2,
      "X": 780,
      "Y": 80,
      "Data": {
        "Lines": {
          "Kind": "List",
          "Items": [
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {"Kind": "String", "StringValue": "旁白"},
                "Text": {"Kind": "String", "StringValue": "数日后，易水河畔。秋风萧瑟，枯叶纷飞。高渐离击筑而歌。"},
                "VoiceId": {"Kind": "String", "StringValue": ""}
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {"Kind": "String", "StringValue": "高渐离"},
                "Text": {"Kind": "String", "StringValue": "风萧萧兮易水寒，壮士一去兮不复还！"},
                "VoiceId": {"Kind": "String", "StringValue": ""}
              }
            },
            {
              "Kind": "Object",
              "ObjectValue": {
                "SpeakerId": {"Kind": "String", "StringValue": "旁白"},
                "Text": {"Kind": "String", "StringValue": "荆轲转身，向燕宫方向深深一拜，随后头也不回地踏上征程。"},
                "VoiceId": {"Kind": "String", "StringValue": ""}
              }
            }
          ]
        }
      },
      "Outputs": {
        "Actions": [6, 7, 8],
        "Next": []
      }
    }
  ]
}
```

---

**文档版本**: v1.0
**最后更新**: 2024-05-18
**作者**: Claude Code
