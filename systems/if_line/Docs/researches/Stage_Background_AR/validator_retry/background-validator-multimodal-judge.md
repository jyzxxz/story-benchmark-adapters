# D04 — background-validator-multimodal-judge

## 背景

bbox 检测出 suspicious case 后，必须用 VLM（GLM-4V）二次复核。VLM 能理解：
- "环境是否为主体"（语义判断，bbox 无法判断）
- "是否有命名角色"（结合 forbidden_characters 列表）
- "是否有单人主体"（结合构图判断）

VLM 输出必须是结构化 JSON，避免自由文本判断。

## SOTA 实践

**OpenAI vision API with Structured Outputs**（[`platform.openai.com/docs/guides/vision`](https://platform.openai.com/docs/guides/vision)）：

> 把图作为 `input_image` 喂模型，配合 `response_format={"type": "json_object"}` 强制结构化输出。

**GLM-4V 官方文档**（[`open.bigmodel.cn/dev/api/normal-model/glm-4v`](https://open.bigmodel.cn/dev/api/normal-model/glm-4v)）：支持 image input + JSON output，对中文场景理解优于通用 VLM。

**Anthropic Claude vision**（[`docs.anthropic.com/en/docs/build-with-claude/vision`](https://docs.anthropic.com/en/docs/build-with-claude/vision)）：

> 多模态任务用结构化 prompt：image + textual checklist → JSON verdict。这种模式比 free-form description 更稳定。

## 落地建议

`background_image_validator_service.py::_validate_with_vlm`：

```python
async def _validate_with_vlm(
    self, image_bytes: bytes, spec: BackgroundSceneSpec, prompt: str
) -> dict:
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    sys_prompt = """你是背景图验收器。请按以下 JSON schema 输出验收结果：
{
  "is_environment_main_subject": bool,  // 环境是否为主体
  "has_named_cast": bool,               // 是否含命名角色（参考 forbidden_characters）
  "has_single_prominent_person": bool,  // 是否有单人主体
  "has_centered_lone_figure": bool,     // 是否有居中单人
  "has_large_foreground_person": bool,  // 是否有前景大人物
  "needs_retry": bool,                  // 综合判断：是否需要重试
  "reason": string                      // 简短理由（中文，<=80 字）
}

验收标准：
- environment_main_subject: 建筑、景观、室内陈设占画面 80%+，且无人物主体化
- single_prominent_person: 画面中有单一人物作为视觉中心
- centered_lone_figure: 中央位置有单人
- large_foreground_person: 前景有大比例人物

严禁自由发挥；只输出严格 JSON。"""

    user_prompt = f"""<ImageForValidation>（base64 image，见 input_image）</ImageForValidation>

<BackgroundSceneSpec>
{spec.model_dump_json()}
</BackgroundSceneSpec>

<ForbiddenCharacters>
{', '.join(spec.forbidden_characters)}
</ForbiddenCharacters>

<FinalPromptUsed>
{prompt[:500]}
</FinalPromptUsed>"""

    resp = await asyncio.wait_for(
        self._vlm.chat(
            model="glm-4v-plus",
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
                ]},
            ],
            temperature=0.1,
            max_tokens=400,
            response_format={"type": "json_object"},
        ),
        timeout=10.0,
    )
    return json.loads(resp["choices"][0]["message"]["content"])
```

**关键约束**：
- `temperature=0.1`：分类任务要稳定
- `max_tokens=400`：JSON 输出短
- `timeout=10s`：VLM 比 LLM 慢
- `response_format=json_object`：严格 JSON

## 风险与权衡

1. **GLM-4V 在绘画风格上准确率 < 真实照片**：可能漏判"绘画风格单人主体"。权衡：用 bbox 阈值把严，VLM 只做二次保险。
2. **VLM API 失败时怎么办**：超时 / 解析失败。权衡：失败时按"可疑"处理（needs_retry=true），让 retry 路径处理；不直接 pass。

## 完成判据自检

- ✅ 围绕"VLM 二次复核"，不跑题到 bbox（D02）或 retry（D06）。
- ✅ 引用 OpenAI vision、GLM-4V 文档、Anthropic Claude vision。
- ✅ 落到 `background_image_validator_service.py::_validate_with_vlm`。
- ✅ 符合哲学：bbox + VLM 混合（VLM 不是唯一闭环），复用现有 GLM 配置。
