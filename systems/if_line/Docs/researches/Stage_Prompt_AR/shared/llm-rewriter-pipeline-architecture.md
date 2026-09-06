# D01 — llm-rewriter-pipeline-architecture

> 设计统一的 LLM Rewriter 接口，作为 prompt 优化路径的核心抽象。这是 A01/B01/C01 的共同依赖。

## 背景

当前 `prompt_builder_service.py` 三个 `build_*_prompts` 方法做的事是：
**取 StoryBible/ChapterOutline 字段 → 套模板槽 → 拼成长字符串 → 喂给 CogView-4**

这套流程的根本问题不在模板，而在"字段→槽位"是机械映射，没有语义合成：
- 中文 `appearance="身着素白丧服的年轻女子"` 被原样塞进英文模板
- 多个字段之间没有上下文关联（情绪不知道装束，装束不知道场景）
- 缺失字段用 `characters`、`standing pose` 占位

哲学主张在 builder 之后插一层 LLM Rewriter：把结构化字段一次性合成成一段**视觉可解析的英文 prompt**，而不是字段拼接。这层抽象必须统一——不能为 portrait/background/keyframe 各做一遍，否则三套 system prompt、三套 fallback、三套缓存逻辑，维护成本指数上升。

## SOTA 实践

### DALL-E 3 的 prompt rewriting
OpenAI 在 DALL-E 3 里**强制**用 GPT-4 重写用户的原始 prompt 再喂给图像模型，官方 blog ([Improving image generation with better prompts](https://openai.com/index/improving-image-generation-with-better-prompts/)) 解释：raw prompt 通常太短、太结构化、缺乏视觉细节，重写能显著提升图文一致性。

关键洞察：**重写发生在 API 服务端**，但同样的模式可以在客户端用 LLM 实现——这就是本项目要做的事。

### Stable Diffusion 的 structured prompts
SDXL 的 [SDXL Prompt Styler](https://github.com/twri/sdxl-prompt-styling) 工程化了一个模式：把 prompt 拆成 `subject + style + lighting + composition`，每段独立可控。本项目 D03 schema 沿用这个分解。

### LangChain LLMRouter / Output Parser
LangChain 的 [PydanticOutputParser](https://python.langchain.com/docs/modules/model_io/output_parsers/structured_output_parsers) 提供了"LLM 输出 JSON → Pydantic 校验 → 失败重试"的标准模式。本 Rewriter 应该用同样的模式。

## 落地建议

### 文件位置
**新建** `backend/app/services/prompt_rewriter_service.py`，**不**直接动 `llm_service.py`。理由：
- `llm_service.py` 是底层 SDK 封装（chat complete / embedding），职责单一
- Rewriter 是上层业务抽象，依赖 llm_service 但有自己的 schema、缓存、fallback 逻辑
- 后续如果换底层 LLM provider，rewriter 不动

### 接口
```python
# backend/app/services/prompt_rewriter_service.py

from typing import Literal, TypedDict
from pydantic import BaseModel, Field

AssetType = Literal["portrait", "background", "keyframe"]

class RewrittenPrompt(BaseModel):
    subject: str = Field(description="主体描述（人物、场景或事件），80-120 词，必须英文")
    details: list[str] = Field(default_factory=list, description="视觉细节列表，每个 5-15 词")
    lighting: str = Field(default="", description="光照与氛围")
    composition: str = Field(default="", description="构图与景别")
    style: str = Field(default="", description="画风与时代风格")
    negative_minimal: list[str] = Field(default_factory=list, description="最小化负面提示，<=5 词")

    def to_cogview_prompt(self) -> str:
        """序列化成 CogView-4 友好的单行 prompt"""
        ...

class PromptRewriterService:
    async def rewrite(
        self,
        asset_type: AssetType,
        fields: dict,
        force_refresh: bool = False,
    ) -> RewrittenPrompt:
        """
        将结构化字段重写为 CogView-4 友好的英文 prompt。
        失败时返回 None，调用方负责 fallback 到原 prompt_builder_service。
        """
        ...
```

### 调用点

每个 builder 方法尾部追加：
```python
# prompt_builder_service.py:23 build_portrait_prompts
# 末尾，构造完字段后
for prompt_data in prompts:
    rewritten = await rewriter.rewrite("portrait", prompt_data)
    if rewritten:
        prompt_data["final_prompt"] = rewritten.to_cogview_prompt()
    else:
        prompt_data["final_prompt"] = self._legacy_build(prompt_data)  # fallback
```

`image_generation_service._build_*_prompt` 改为：**如果 `final_prompt` 已存在则直接用，否则走旧逻辑**。这样改动可控、可回滚。

### 性能保护
- `asyncio.Semaphore(5)` 限并发（CogView-4 本身限制 3，但 LLM 调用可以更并发）
- LLM 输出按输入 hash 缓存（D06）
- 单次重写超时 8s，超时 fallback 到旧 builder（D04）

## 风险与权衡

1. **延迟翻倍**: 每张图增加一次 LLM round-trip（~1-3s）。**权衡**: 用户在 `image_generation_service.py:33` 已经把 CogView-4 调用限流到 3 并发，LLM 并发可以开到 5-10，端到端延迟不会 1:1 翻倍。
2. **LLM 成本**: 全项目假设 100 章 × 10 角色 × 3 变体 = 3000 次重写。用 GLM-4-flash 单次约 ¥0.001，总成本 ~¥3，可接受。如果改用 glm-4-plus 升到 ¥30，需要业务方确认（D05）。
3. **新增依赖**: 项目已用 openai SDK 调 CogView-4，rewriter 复用同一 SDK 调 GLM/DeepSeek，零新依赖。

## 完成判据自检

- [x] 文档完全围绕"统一 Rewriter 架构"，不跑题到具体 prompt 模板
- [x] 引用至少 1 个外部 SOTA：DALL-E 3 官方 blog、SDXL Prompt Styler、LangChain Output Parser
- [x] 给出具体文件路径（`prompt_rewriter_service.py`）和代码骨架
- [x] 推荐做法符合哲学：LLM 重写路径、不引入新图像供应商、不引入 VLM 校验

## 依赖项

本项是 A01/B01/C01 的硬依赖，必须最先完成。
后续 D02 (system prompt 模板)、D03 (JSON schema)、D04 (fallback)、D05 (成本)、D06 (缓存) 都在本项架构内展开。
