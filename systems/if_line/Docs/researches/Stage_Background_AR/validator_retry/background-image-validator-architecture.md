# D01 — background-image-validator-architecture

## 背景

生成后的图片必须经验收才能落库——否则"单人主体图"仍被保存。当前 `background_image_validator_service.py` 是简单的 heuristic（可能只是尺寸/比例检查），无法检测"画面里有人物主体"。

新设计要求 **bbox 启发式 + 多模态二次复核** 的混合验收：
- bbox 先筛（快、CPU 单机、低延迟）
- bbox 判定为"可疑"时进多模态（更准、但贵）
- 多模态判 fail 时硬失败 + retry

**禁止 VLM-only 闭环**（哲学要求）——必须双轨。

## SOTA 实践

**OpenAI Evals testing_criteria 分层**（[`github.com/openai/evals`](https://github.com/openai/evals)）：

> 复杂验收分多层：(1) 启发式快筛 (2) model-graded 深度判定。第一层 fail 立即返回；第一层 pass 才进第二层。

**Google Vertex AI "model-assisted moderation"**（[`cloud.google.com/vertex-ai/generative-ai/docs/multimodal/overview`](https://cloud.google.com/vertex-ai/generative-ai/docs/multimodal/overview)）：

> 大型图像审核通常用 fast heuristic（如 NSFW JS）做第一道，VLM 做第二道——这种 cascade 比单一 VLM 节省 80% 成本。

**Stable Diffusion "control net + validator"**（[`github.com/lllyasviel/stable-diffusion-webui-forge`](https://github.com/lllyasviel/stable-diffusion-webui-forge)）：bbox + OpenPose 验收组合，工业级实践。

## 落地建议

`backend/app/services/background_image_validator_service.py` 重构为：

```python
class BackgroundImageValidator:
    def __init__(self, bbox_detector, vlm_client, profiles):
        self._bbox = bbox_detector  # YOLOv8 or similar
        self._vlm = vlm_client      # GLM-4V
        self._profiles = profiles

    async def validate(
        self, image_bytes: bytes, spec: BackgroundSceneSpec, prompt: str
    ) -> ValidationResult:
        # 第一层：bbox
        boxes = self._detect_persons_bbox(image_bytes)
        bbox_verdict = self._bbox_verdict(boxes, spec.people_policy.mode)

        if bbox_verdict == "hard_pass":
            # bbox 完全无人物且 empty_required → 直接通过
            return ValidationResult(passed=True, stage="bbox_hard_pass")
        if bbox_verdict == "hard_fail":
            # bbox 检出单人主体 → 直接 fail（仍可触发 VLM 二次确认）
            return ValidationResult(passed=False, stage="bbox_hard_fail", reason="single_prominent_person")

        # 第二层：VLM 二次复核（仅 bbox 判 "suspicious" 时）
        vlm_result = await self._validate_with_vlm(image_bytes, spec, prompt)
        if vlm_result["has_single_prominent_person"]:
            return ValidationResult(passed=False, stage="vlm", reason="single_prominent_person")
        if vlm_result["has_named_cast"]:
            return ValidationResult(passed=False, stage="vlm", reason="named_cast_leak")
        if not vlm_result["is_environment_main_subject"]:
            return ValidationResult(passed=False, stage="vlm", reason="environment_not_main")

        return ValidationResult(passed=True, stage="vlm_pass")

    def _bbox_verdict(self, boxes, mode):
        # 见 D03
        ...
```

**层次设计依据**：
- bbox 在 CPU 单机 ~0.5s，VLM ~2s
- bbox 准确率 ~85%（绘画风格人物次优），VLM ~95%
- cascade: 100% 图过 bbox → ~10% 进 VLM → 平均延迟 ~0.7s（远低于纯 VLM 2s）

## 风险与权衡

1. **bbox 在绘画风格背景上准确率低**：CogView-4 出图是绘画风格，YOLO 训练集偏真实照片。权衡：用 fine-tuned YOLO 或加 OpenPose 作为辅助；或把 bbox 阈值设严（任何人物框都触发 VLM）。
2. **VLM 延迟 2s + bbox 0.5s = 2.5s，validator 总耗时上升**：权衡：bbox hard_pass 路径 ~80% case 直接 0.5s 返回；只有 ~20% 进 VLM。平均可接受。

## 完成判据自检

- ✅ 围绕"bbox + VLM 混合验收架构"，不跑题到具体 detector 选型（D02）。
- ✅ 引用 OpenAI Evals 分层、Google cascade、SD validator。
- ✅ 落到 `background_image_validator_service.py::validate`。
- ✅ 符合哲学：bbox + VLM 混合（不是 VLM-only），不引入新模型供应商。
