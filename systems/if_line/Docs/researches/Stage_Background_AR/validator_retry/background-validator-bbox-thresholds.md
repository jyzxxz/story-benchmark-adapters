# D03 — background-validator-bbox-thresholds

## 背景

bbox 检测出 N 个 person box 后，需要判定：
- empty_required 模式：任何 person box 都 fail
- background_people_optional：单 person box 面积 > 1-3% 图像 → fail
- background_groups_required：单 person box 面积 > 1-3% 仍 fail（"groups"意味着多人，单人即问题）

阈值需要调参——调太严会误杀合理"远景人影"，调太松会放过"前景大人物"。

## SOTA 实践

**COCO eval metrics**（[`cocodataset.org/#detection-eval`](https://cocodataset.org/#detection-eval)）：用 AP / AR 评估 detector 阈值敏感性。

**A/B testing for thresholds**（[`medium.com/airbnb-engineering/designing-more-powerful-experiments-with-a-b-testing-and-machine-learning-df19b4dbd8a3`](https://medium.com/airbnb-engineering/designing-more-powerful-experiments-with-a-b-testing-and-machine-learning-df19b4dbd8a3)）：在 golden set 上扫不同阈值，选 precision/recall 综合最优点。

**人机协作 threshold tuning**（[`cloud.google.com/vertex-ai/docs/model-monitoring`](https://cloud.google.com/vertex-ai/docs/model-monitoring)）：先用规则化阈值，再根据 reviewer 反馈调整。

## 落地建议

`image_generation_profiles.json::background_validator_thresholds`：

```json
"background_validator_thresholds": {
  "empty_required": {
    "any_person_box_area_ratio": 0.0,
    "any_person_confidence": 0.3,
    "verdict": "hard_fail"
  },
  "background_people_optional": {
    "single_person_area_ratio_threshold": 0.03,
    "single_person_center_region_ratio": 0.30,
    "total_person_area_ratio_threshold": 0.15,
    "max_person_count": 8,
    "verdict_per_rule": "hard_fail"
  },
  "background_groups_required": {
    "single_person_area_ratio_threshold": 0.02,
    "single_person_center_region_ratio": 0.25,
    "total_person_area_ratio_threshold": 0.20,
    "min_person_count_for_groups": 3,
    "verdict_per_rule": "hard_fail"
  },
  "image_center_region": {
    "x_ratio": [0.25, 0.75],
    "y_ratio": [0.25, 0.75]
  }
}
```

`_bbox_verdict` 实现：

```python
def _bbox_verdict(self, boxes: list[BBox], mode: str, image_w: int, image_h: int) -> str:
    cfg = self._profiles["background_validator_thresholds"][mode]
    img_area = image_w * image_h

    if mode == "empty_required":
        # 任何置信度 > 0.3 的 box → fail
        for b in boxes:
            if b.conf >= cfg["any_person_confidence"]:
                return "hard_fail"
        return "hard_pass"

    # optional / required
    center_x_range = (image_w * 0.25, image_w * 0.75)
    center_y_range = (image_h * 0.25, image_h * 0.75)

    for b in boxes:
        box_area = (b.x2 - b.x1) * (b.y2 - b.y1)
        area_ratio = box_area / img_area

        # 单 box 面积过大
        if area_ratio > cfg["single_person_area_ratio_threshold"]:
            return "hard_fail"

        # box 中心在图像中心区域
        cx, cy = (b.x1 + b.x2) / 2, (b.y1 + b.y2) / 2
        if (center_x_range[0] <= cx <= center_x_range[1] and
            center_y_range[0] <= cy <= center_y_range[1]):
            return "hard_fail"

    total_person_area = sum((b.x2 - b.x1) * (b.y2 - b.y1) for b in boxes) / img_area
    if total_person_area > cfg["total_person_area_ratio_threshold"]:
        return "hard_fail"

    if mode == "background_groups_required" and len(boxes) > 0 and len(boxes) < cfg["min_person_count_for_groups"]:
        # 1-2 人不构成 "groups"，可能是误生成单人
        return "hard_fail"

    if len(boxes) > cfg.get("max_person_count", 8):
        return "hard_fail"

    return "suspicious"  # 让 VLM 复核
```

## 风险与权衡

1. **3% 阈值是经验值，需 golden set A/B 校准**：权衡：先上线保守阈值（1.5%），观察误杀率再放宽。
2. **center region 0.25-0.75 太严**：可能让"中心偏远的人物"漏过。权衡：保留 suspicious 进 VLM 复核，让 VLM 终判。

## 完成判据自检

- ✅ 围绕"bbox 阈值"，不跑题到 detector 选型（D02）或 VLM（D04）。
- ✅ 引用 COCO eval、A/B testing、人机 threshold tuning。
- ✅ 落到 `image_generation_profiles.json::background_validator_thresholds` 和 `_bbox_verdict`。
- ✅ 符合哲学：硬阈值 + VLM 兜底，不引入新模型供应商。
