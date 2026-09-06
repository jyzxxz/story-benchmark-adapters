"""前端播放语义探针：用 Godot 真前端同款规则回放 graph_json。

模拟的规则均来自 /home/workspace/aivn 源码核对：
- VNNodeType: 1=Progress, 2=Action; ProgressNodeType: Start=6, Paragraph=2, Dialogue=1, End=11。
- TachiManager.AddTachi: TargetPosition==(0,0) 时按 TachiID 后缀 a/b/c→25%/50%/75% 自动站位；
  否则 TargetPosition 为左上角坐标（设计空间 1920×1080）。
- TachiManager.GetDefaultPosition: x = 1920*pct - size.X*0.5, y = 1080 - size.Y（底对齐）。
- ExitTachi/ChangeTachiImage/MoveTachi: TachiID 不在场时 PushWarning。
- VisualLayerManager: ART 插画(ActionNodeType 6) 隐式清空全部立绘。
- ActionNodeType: TACHI=1 BACKGROUND=3 ART=6 TACHI_MOVE=11 TACHI_EXIT=13 CHANGE_IMAGE=26
  CLEAR_ALL_TACHIS=33 CLEAR_ILLUSTRATION=37 OPACITY=21 HIGHLIGHT=38。

用法: venv/bin/python tests/_probe_frontend_playback.py <graph_json_path> [--portrait-w 1024 --portrait-h 1536]
"""
from __future__ import annotations

import json
import sys

DESIGN_W, DESIGN_H = 1920.0, 1080.0

PROGRESS, ACTION = 1, 2
P_PARAGRAPH, P_START, P_END = 2, 6, 11
A_TACHI, A_BG, A_ART = 1, 3, 6
A_MOVE, A_EXIT, A_CHANGE, A_CLEAR_ALL, A_CLEAR_ILU, A_OPACITY, A_HIGHLIGHT = 11, 13, 26, 33, 37, 21, 38

KNOWN_ACTIONS = {A_TACHI, A_BG, A_ART, A_MOVE, A_EXIT, A_CHANGE, A_CLEAR_ALL, A_CLEAR_ILU, A_OPACITY, A_HIGHLIGHT}
KNOWN_PROGRESS = {P_START, P_PARAGRAPH, P_END, 1, 5, 10}


def auto_position(tachi_id: str, size: tuple[float, float]) -> tuple[float, float]:
    idl = (tachi_id or "").lower()
    pct = 0.5
    if idl.endswith("a") or "left" in idl:
        pct = 0.25
    elif idl.endswith("b") or "center" in idl:
        pct = 0.5
    elif idl.endswith("c") or "right" in idl:
        pct = 0.75
    w, h = size
    return (DESIGN_W * pct - w * 0.5, DESIGN_H - h)


def vec2(raw) -> tuple[float, float]:
    if isinstance(raw, dict):
        try:
            return (float(raw.get("X", 0)), float(raw.get("Y", 0)))
        except (TypeError, ValueError):
            return (0.0, 0.0)
    if isinstance(raw, list) and len(raw) == 2:
        try:
            return (float(raw[0]), float(raw[1]))
        except (TypeError, ValueError):
            return (0.0, 0.0)
    return (0.0, 0.0)


def sval(v):
    return (v or {}).get("StringValue") if isinstance(v, dict) else v


def main() -> None:
    path = sys.argv[1]
    portrait_size = (1024.0, 1536.0)
    if "--portrait-w" in sys.argv:
        portrait_size = (float(sys.argv[sys.argv.index("--portrait-w") + 1]), portrait_size[1])
    if "--portrait-h" in sys.argv:
        portrait_size = (portrait_size[0], float(sys.argv[sys.argv.index("--portrait-h") + 1]))
    graph = json.load(open(path))
    nodes = graph.get("Nodes") or []
    by_index = {n["Index"]: n for n in nodes}

    def next_index(node):
        outputs = node.get("Outputs")
        if isinstance(outputs, dict):
            for v in outputs.get("Next") or []:
                if isinstance(v, int) and v >= 0:
                    return v
        return None

    order: list[dict] = []
    seen: set[int] = set()
    cur = by_index.get(graph.get("StartNodeIndex"))
    while cur is not None and cur["Index"] not in seen:
        seen.add(cur["Index"])
        order.append(cur)
        # 段落的 Actions 同帧按列表序触发（ParagraphNode 执行模型）
        if cur.get("NodeType") == PROGRESS:
            outputs = cur.get("Outputs") or {}
            for aidx in (outputs.get("Actions") or []):
                if isinstance(aidx, int) and aidx in by_index and aidx not in seen:
                    seen.add(aidx)
                    order.append(by_index[aidx])
        cur = by_index.get(next_index(cur))

    on_stage: dict[str, dict] = {}
    warnings: list[str] = []
    unknown: list[str] = []
    events: list[str] = []
    frames: list[dict] = []
    last_paragraph = None

    def snapshot(node_idx: int) -> None:
        if len(on_stage) < 1:
            return
        items = sorted(on_stage.items(), key=lambda kv: kv[1]["pos"][0])
        centers = {tid: v["pos"][0] + v["size"][0] / 2 for tid, v in items}
        dup = []
        tids = list(centers)
        for i in range(len(tids)):
            for j in range(i + 1, len(tids)):
                if abs(centers[tids[i]] - centers[tids[j]]) < 1.0:
                    dup.append((tids[i], tids[j]))
        frames.append(
            {
                "node": node_idx,
                "para": last_paragraph,
                "actors": [(t, round(centers[t], 1), round(on_stage[t]["pos"][1], 1)) for t in tids],
                "same_center": dup,
            }
        )

    for node in order:
        ntype, sub = node.get("NodeType"), node.get("SubType")
        data = node.get("Data") or {}
        if ntype == PROGRESS:
            if sub not in KNOWN_PROGRESS:
                unknown.append(f"Progress/{sub}")
            if sub == P_PARAGRAPH or sub == 1:
                if on_stage or frames:
                    snapshot(node["Index"])
                last_paragraph = node["Index"]
        elif ntype == ACTION:
            if sub not in KNOWN_ACTIONS:
                unknown.append(f"Action/{sub}")
                continue
            tid = sval(data.get("TachiID"))
            if sub == A_TACHI:
                pos = vec2(data.get("TargetPosition"))
                if pos == (0.0, 0.0):
                    pos = auto_position(tid, portrait_size)
                on_stage[tid] = {"pos": pos, "img": sval(data.get("TachiIamge")), "size": portrait_size}
                events.append(f"TACHI {tid} at {pos}")
            elif sub == A_MOVE:
                if tid not in on_stage:
                    warnings.append(f"#{node['Index']} MoveTachi failed: {tid} not on stage")
                else:
                    on_stage[tid]["pos"] = vec2(data.get("TargetPosition"))
                    events.append(f"MOVE {tid} -> {on_stage[tid]['pos']}")
            elif sub == A_CHANGE:
                if tid not in on_stage:
                    warnings.append(f"#{node['Index']} ChangeTachiImage failed: {tid} not on stage")
                else:
                    on_stage[tid]["img"] = sval(data.get("TachiIamge"))
                    events.append(f"CHANGE_IMAGE {tid}")
            elif sub in (A_OPACITY, A_HIGHLIGHT):
                if tid and tid not in on_stage:
                    warnings.append(f"#{node['Index']} opacity/highlight failed: {tid} not on stage")
            elif sub == A_EXIT:
                if tid not in on_stage:
                    warnings.append(f"#{node['Index']} ExitTachi failed: {tid} not on stage")
                else:
                    del on_stage[tid]
                    events.append(f"EXIT {tid}")
            elif sub == A_CLEAR_ALL:
                on_stage.clear()
                events.append("CLEAR_ALL_TACHIS")
            elif sub == A_ART:
                events.append(f"ART (implicit clear {len(on_stage)})")
                on_stage.clear()
            elif sub == A_CLEAR_ILU:
                events.append("CLEAR_ILLUSTRATION")
        else:
            unknown.append(f"{ntype}/{sub}")
    if on_stage:
        snapshot(-1)

    print(f"nodes={len(nodes)} walked={len(order)} start={graph.get('StartNodeIndex')}")
    print(f"unknown node types: {unknown or 'none'}")
    print(f"TryGetTachi-style warnings: {len(warnings)}")
    for w in warnings[:10]:
        print("  ", w)
    print(f"--- stage frames ({len(frames)}, sampled at paragraph boundaries) ---")
    shown: set[tuple] = set()
    for f in frames:
        key = tuple(f["actors"])
        if key in shown:
            continue
        shown.add(key)
        flag = " <<< SAME CENTER" if f["same_center"] else ""
        print(f"  para@{f['para']}: {f['actors']}{flag}")
    bad_y = [f for f in frames if any(a[2] != -456.0 for a in f["actors"])]
    print(f"frames with non-bottom-aligned y: {len(bad_y)}")
    stacked = [f for f in frames if f["same_center"]]
    print(f"distinct stage layouts: {len(shown)}")
    print(f"RESULT: {'STACKING DETECTED in ' + str(len(stacked)) + ' frames' if stacked else 'no stacking'}")
    print(f"--- events ({len(events)}) ---")
    for e in events:
        print("  ", e)


if __name__ == "__main__":
    main()
