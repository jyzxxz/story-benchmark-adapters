# D02 — background-validator-bbox-detection

## 背景

bbox person detector 是 validator 第一层。选型决定整体性能：
- YOLOv8：实时、CPU 可跑、训练集偏真实照片
- RT-DETR：Transformer-based、准确率更高、但更慢
- OpenCV HOG：极轻量、但对绘画人物弱

背景图通常是绘画风格（CogView-4 产物），需要选对绘画友好的 detector。

## SOTA 实践

**Ultralytics YOLOv8 文档**（[`docs.ultralytics.com/models/yolov8/`](https://docs.ultralytics.com/models/yolov8/)）：

> YOLOv8n（nano）CPU 单机 ~50ms / image，mAP50 ~37（COCO）。

**RT-DETR 论文**（[`arxiv.org/abs/2304.08069`](https://arxiv.org/abs/2304.08069)）：real-time DETR，mAP53 ~53，但 latency 比 YOLOv8 高 2-3 倍。

**OpenCV HOG person detector**（[`docs.opencv.org/4.x/d5/d77/tutorial_person_detection.html`](https://docs.opencv.org/4.x/d5/d77/tutorial_person_detection.html)）：~10ms / image，但对绘画风格几乎无效。

**绘画风格 fine-tuned YOLO**（[`github.com/ultralytics/ultralytics/discussions/1517`](https://github.com/ultralytics/ultralytics/discussions/1517)）：社区报告 YOLOv8 在动漫人物上 mAP 下降 ~30%，但通过 fine-tune 可恢复。

## 落地建议

**主选 YOLOv8n**（CPU 友好 + 模型小）：

```python
from ultralytics import YOLO

class BBoxPersonDetector:
    def __init__(self, model_path="yolov8n.pt"):
        self._model = YOLO(model_path)
        self._person_class = 0  # COCO class 0 = person

    def detect(self, image_bytes: bytes) -> list[BBox]:
        import numpy as np
        from PIL import Image
        import io
        img = np.array(Image.open(io.BytesIO(image_bytes)))
        results = self._model(img, verbose=False)
        boxes = []
        for r in results:
            for box in r.boxes:
                if int(box.cls) == self._person_class and float(box.conf) >= 0.4:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    boxes.append(BBox(x1=x1, y1=y1, x2=x2, y2=y2, conf=float(box.conf)))
        return boxes
```

**配置化**：

```json
"background_validator": {
  "bbox": {
    "model": "yolov8n.pt",
    "confidence_threshold": 0.4,
    "person_class_id": 0
  }
}
```

**模型路径**：模型文件放 `backend/models/yolov8n.pt`（首次启动时自动下载）。

**绘画风格适应**：YOLOv8 在绘画人物上召回率低（漏检多），所以阈值要严（任何人物框都触发 VLM 复核）——宁可误进 VLM 也不能漏过主体人物。

## 风险与权衡

1. **YOLOv8 在绘画风格上漏检率高**：可能让"绘画风格单人主体图"被误判 pass。权衡：bbox 只做粗筛，所有 bbox 有任何 person box（哪怕 conf=0.3）都进 VLM；empty_required 模式任何 person box 都 fail。
2. **CPU 推理 ~50ms × 多图并发**：单机 10 章并发可接受；但 50 章并发可能 CPU 阻塞。权衡：用 `concurrent.futures.ThreadPoolExecutor` 限制并发数 4。

## 完成判据自检

- ✅ 围绕"bbox detector 选型"，不跑题到 VLM 实现（D04）或阈值（D03）。
- ✅ 引用 YOLOv8 / RT-DETR 论文、OpenCV HOG、绘画 fine-tune 讨论。
- ✅ 落到 `background_image_validator_service.py::_detect_persons_bbox` 和 `image_generation_profiles.json::background_validator.bbox`。
- ✅ 符合哲学：bbox 启发式先筛，不引入 VLM-only 闭环。
