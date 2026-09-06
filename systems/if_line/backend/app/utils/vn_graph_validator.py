"""
VNGraph 校验器。

规则来源是 aivn/Scripts/Logic/VNNode 下的编辑器节点定义：
- Progress/Action/Condition 三类节点及其 SubType。
- Auto 生成文件里的输出端口和输入容量。
- NodeView.CollectIssues 里的节点配置问题。
"""
from dataclasses import dataclass, field
import math
import re
from typing import Any, Dict, List, Optional, Tuple


NODE_TYPE_PROGRESS = 1
NODE_TYPE_ACTION = 2
NODE_TYPE_CONDITION = 3

PROGRESS_START = 6
PROGRESS_CHOICE = 5
PROGRESS_DIALOGUE = 1
PROGRESS_PARAGRAPH = 2
PROGRESS_CHAPTER = 3
PROGRESS_TRANSITION = 4
PROGRESS_CONDITION_BRANCH = 7
PROGRESS_VARIABLE_INPUT = 8
PROGRESS_VARIABLE_TEXT_REPLACE = 9
PROGRESS_PERFORMANCE_SEGMENT = 10
PROGRESS_END = 11
PROGRESS_WAIT_CLICK = 12
PROGRESS_HOTSPOT_CLICK = 13
PROGRESS_SOFT_TRANSITION = 14
PROGRESS_SOFT_MERGE = 15

ACTION_TACHI = 1
ACTION_BACKGROUND = 3
ACTION_VOICE = 4
ACTION_AUDIO = 5
ACTION_ART = 6
ACTION_SEQUENCE = 7
ACTION_PARALLEL = 8
ACTION_DELAY = 9
ACTION_TACHI_MOVE = 11
ACTION_TACHI_SCALE = 12
ACTION_TACHI_EXIT = 13
ACTION_TACHI_MOVE_X = 14
ACTION_TACHI_MOVE_BESIDE = 15
ACTION_TACHI_EFFECT = 16
ACTION_MEMORY_EFFECT = 17
ACTION_BLINK_EFFECT = 18
ACTION_BLUR_EFFECT = 19
ACTION_CLEAR_SCREEN_EFFECT = 20
ACTION_BACKGROUND_MUSIC = 21
ACTION_ATTRIBUTE_CHANGE = 22
ACTION_CONDITIONAL = 23
ACTION_TACHI_SHAKE = 24
ACTION_VARIABLE_SET = 25
ACTION_TACHI_CHANGE_IMAGE = 26
ACTION_TACHI_OPACITY = 27
ACTION_TACHI_FLIP = 28
ACTION_SCREEN_SHAKE = 29
ACTION_TACHI_ROTATE = 30
ACTION_STOP_VOICE = 31
ACTION_CLEAR_DIALOGUE = 32
ACTION_CLEAR_ALL_TACHIS = 33
ACTION_BACKGROUND_BLACK = 34
ACTION_ILLUSTRATION_PAN = 35
ACTION_SCREEN_SPARKLE_EFFECT = 36
ACTION_CLEAR_ILLUSTRATION = 37
ACTION_TACHI_HIGHLIGHT = 38
ACTION_STAGE_CAMERA = 39
ACTION_STOP_BACKGROUND_MUSIC = 40
ACTION_BACKGROUND_PAN = 41
ACTION_STAGE_CAMERA_RESTORE = 42

CONDITION_ATTRIBUTE_COMPARE = 1
CONDITION_VARIABLE_COMPARE = 2
CONDITION_GROUP = 3
CONDITION_TACHI_PRESENCE = 4

VALID_SUBTYPES = {
    NODE_TYPE_PROGRESS: {
        PROGRESS_DIALOGUE,
        PROGRESS_PARAGRAPH,
        PROGRESS_CHAPTER,
        PROGRESS_TRANSITION,
        PROGRESS_CHOICE,
        PROGRESS_START,
        PROGRESS_CONDITION_BRANCH,
        PROGRESS_VARIABLE_INPUT,
        PROGRESS_VARIABLE_TEXT_REPLACE,
        PROGRESS_PERFORMANCE_SEGMENT,
        PROGRESS_END,
        PROGRESS_WAIT_CLICK,
        PROGRESS_HOTSPOT_CLICK,
        PROGRESS_SOFT_TRANSITION,
        PROGRESS_SOFT_MERGE,
    },
    NODE_TYPE_ACTION: {
        ACTION_TACHI,
        ACTION_BACKGROUND,
        ACTION_VOICE,
        ACTION_AUDIO,
        ACTION_ART,
        ACTION_SEQUENCE,
        ACTION_PARALLEL,
        ACTION_DELAY,
        ACTION_TACHI_MOVE,
        ACTION_TACHI_SCALE,
        ACTION_TACHI_EXIT,
        ACTION_TACHI_MOVE_X,
        ACTION_TACHI_MOVE_BESIDE,
        ACTION_TACHI_EFFECT,
        ACTION_MEMORY_EFFECT,
        ACTION_BLINK_EFFECT,
        ACTION_BLUR_EFFECT,
        ACTION_CLEAR_SCREEN_EFFECT,
        ACTION_BACKGROUND_MUSIC,
        ACTION_ATTRIBUTE_CHANGE,
        ACTION_CONDITIONAL,
        ACTION_TACHI_SHAKE,
        ACTION_VARIABLE_SET,
        ACTION_TACHI_CHANGE_IMAGE,
        ACTION_TACHI_OPACITY,
        ACTION_TACHI_FLIP,
        ACTION_SCREEN_SHAKE,
        ACTION_TACHI_ROTATE,
        ACTION_STOP_VOICE,
        ACTION_CLEAR_DIALOGUE,
        ACTION_CLEAR_ALL_TACHIS,
        ACTION_BACKGROUND_BLACK,
        ACTION_ILLUSTRATION_PAN,
        ACTION_SCREEN_SPARKLE_EFFECT,
        ACTION_CLEAR_ILLUSTRATION,
        ACTION_TACHI_HIGHLIGHT,
        ACTION_STAGE_CAMERA,
        ACTION_STOP_BACKGROUND_MUSIC,
        ACTION_BACKGROUND_PAN,
        ACTION_STAGE_CAMERA_RESTORE,
    },
    NODE_TYPE_CONDITION: {
        CONDITION_ATTRIBUTE_COMPARE,
        CONDITION_VARIABLE_COMPARE,
        CONDITION_GROUP,
        CONDITION_TACHI_PRESENCE,
    },
}

OUTPUT_TARGET_TYPES = {
    (NODE_TYPE_PROGRESS, PROGRESS_DIALOGUE): {
        "Actions": NODE_TYPE_ACTION,
        "Next": NODE_TYPE_PROGRESS,
    },
    (NODE_TYPE_PROGRESS, PROGRESS_PARAGRAPH): {
        "Actions": NODE_TYPE_ACTION,
        "Next": NODE_TYPE_PROGRESS,
    },
    (NODE_TYPE_PROGRESS, PROGRESS_CHAPTER): {
        "Next": NODE_TYPE_PROGRESS,
    },
    (NODE_TYPE_PROGRESS, PROGRESS_TRANSITION): {
        "Next": NODE_TYPE_PROGRESS,
    },
    (NODE_TYPE_PROGRESS, PROGRESS_START): {
        "Next": NODE_TYPE_PROGRESS,
    },
    (NODE_TYPE_PROGRESS, PROGRESS_CONDITION_BRANCH): {
        "Condition": NODE_TYPE_CONDITION,
        "TrueNext": NODE_TYPE_PROGRESS,
        "FalseNext": NODE_TYPE_PROGRESS,
    },
    (NODE_TYPE_PROGRESS, PROGRESS_VARIABLE_INPUT): {
        "Next": NODE_TYPE_PROGRESS,
    },
    (NODE_TYPE_PROGRESS, PROGRESS_VARIABLE_TEXT_REPLACE): {
        "Next": NODE_TYPE_PROGRESS,
    },
    (NODE_TYPE_PROGRESS, PROGRESS_PERFORMANCE_SEGMENT): {
        "Actions": NODE_TYPE_ACTION,
        "Next": NODE_TYPE_PROGRESS,
    },
    (NODE_TYPE_PROGRESS, PROGRESS_WAIT_CLICK): {
        "Next": NODE_TYPE_PROGRESS,
    },
    (NODE_TYPE_PROGRESS, PROGRESS_HOTSPOT_CLICK): {
        "Success": NODE_TYPE_PROGRESS,
        "Failure": NODE_TYPE_PROGRESS,
    },
    (NODE_TYPE_PROGRESS, PROGRESS_SOFT_TRANSITION): {
        "Next": NODE_TYPE_PROGRESS,
    },
    (NODE_TYPE_PROGRESS, PROGRESS_SOFT_MERGE): {
        "Next": NODE_TYPE_PROGRESS,
    },
    (NODE_TYPE_ACTION, ACTION_SEQUENCE): {
        "Actions": NODE_TYPE_ACTION,
    },
    (NODE_TYPE_ACTION, ACTION_PARALLEL): {
        "Actions": NODE_TYPE_ACTION,
    },
    (NODE_TYPE_ACTION, ACTION_CONDITIONAL): {
        "Condition": NODE_TYPE_CONDITION,
        "TrueActions": NODE_TYPE_ACTION,
        "FalseActions": NODE_TYPE_ACTION,
    },
    (NODE_TYPE_CONDITION, CONDITION_GROUP): {
        "Conditions": NODE_TYPE_CONDITION,
    },
}

SINGLE_OUTPUT_KEYS = {
    "Next",
    "Condition",
    "TrueNext",
    "FalseNext",
    "Success",
    "Failure",
}

MULTI_INPUT_NODES = {
    (NODE_TYPE_PROGRESS, PROGRESS_CHAPTER),
    (NODE_TYPE_PROGRESS, PROGRESS_TRANSITION),
    (NODE_TYPE_PROGRESS, PROGRESS_END),
    (NODE_TYPE_PROGRESS, PROGRESS_WAIT_CLICK),
    (NODE_TYPE_PROGRESS, PROGRESS_HOTSPOT_CLICK),
    (NODE_TYPE_PROGRESS, PROGRESS_SOFT_TRANSITION),
    (NODE_TYPE_PROGRESS, PROGRESS_SOFT_MERGE),
    (NODE_TYPE_ACTION, ACTION_ART),
}

NO_INPUT_NODES = {
    (NODE_TYPE_PROGRESS, PROGRESS_START),
}

CHOICE_OUTPUT_RE = re.compile(r"^Options\[(\d+)\]\.(Condition|Next)$")

DATA_FIELD_ALIASES = {
    (NODE_TYPE_PROGRESS, PROGRESS_DIALOGUE): {
        "SpeakerId": "SpeakerIdData",
        "SpeakerID": "SpeakerIdData",
        "Text": "TextData",
        "DialogueText": "TextData",
        "VoiceId": "VoiceIdData",
        "VoiceID": "VoiceIdData",
        "AudioPath": "VoiceIdData",
    },
    (NODE_TYPE_PROGRESS, PROGRESS_PARAGRAPH): {
        "TextData": "Lines",
        "Text": "Lines",
        "Dialogues": "Lines",
        "DialogueLines": "Lines",
    },
    (NODE_TYPE_PROGRESS, PROGRESS_CHOICE): {
        "Choices": "Options",
    },
    (NODE_TYPE_PROGRESS, PROGRESS_VARIABLE_INPUT): {
        "VariableID": "VariableId",
    },
    (NODE_TYPE_PROGRESS, PROGRESS_VARIABLE_TEXT_REPLACE): {
        "VariableID": "VariableId",
    },
    (NODE_TYPE_ACTION, ACTION_TACHI): {
        "TachiId": "TachiID",
        "TachiImage": "TachiIamge",
        "TachiImagePath": "TachiIamge",
        "Image": "TachiIamge",
    },
    (NODE_TYPE_ACTION, ACTION_TACHI_MOVE): {
        "TachiId": "TachiID",
    },
    (NODE_TYPE_ACTION, ACTION_TACHI_SCALE): {
        "TachiId": "TachiID",
    },
    (NODE_TYPE_ACTION, ACTION_TACHI_EXIT): {
        "TachiId": "TachiID",
    },
    (NODE_TYPE_ACTION, ACTION_TACHI_MOVE_X): {
        "TachiId": "TachiID",
    },
    (NODE_TYPE_ACTION, ACTION_TACHI_MOVE_BESIDE): {
        "TachiId": "TachiID",
        "ReferenceTachiId": "ReferenceTachiID",
    },
    (NODE_TYPE_ACTION, ACTION_TACHI_EFFECT): {
        "TachiId": "TachiID",
    },
    (NODE_TYPE_ACTION, ACTION_TACHI_SHAKE): {
        "TachiId": "TachiID",
    },
    (NODE_TYPE_ACTION, ACTION_TACHI_CHANGE_IMAGE): {
        "TachiId": "TachiID",
        "TachiImage": "TachiIamge",
        "TachiImagePath": "TachiIamge",
        "Image": "TachiIamge",
    },
    (NODE_TYPE_ACTION, ACTION_TACHI_OPACITY): {
        "TachiId": "TachiID",
    },
    (NODE_TYPE_ACTION, ACTION_TACHI_FLIP): {
        "TachiId": "TachiID",
    },
    (NODE_TYPE_ACTION, ACTION_TACHI_ROTATE): {
        "TachiId": "TachiID",
    },
    (NODE_TYPE_ACTION, ACTION_TACHI_HIGHLIGHT): {
        "TachiId": "TachiID",
    },
    (NODE_TYPE_ACTION, ACTION_BACKGROUND): {
        "Background": "BackgroundImage",
        "BackgroundPath": "BackgroundImage",
        "Image": "BackgroundImage",
    },
    (NODE_TYPE_ACTION, ACTION_VOICE): {
        "VoiceId": "AudioPath",
        "VoiceID": "AudioPath",
        "VoicePath": "AudioPath",
        "AudioId": "AudioPath",
        "AudioID": "AudioPath",
    },
    (NODE_TYPE_ACTION, ACTION_AUDIO): {
        "SoundId": "AudioPath",
        "SoundID": "AudioPath",
        "SoundPath": "AudioPath",
        "AudioId": "AudioPath",
        "AudioID": "AudioPath",
    },
    (NODE_TYPE_ACTION, ACTION_BACKGROUND_MUSIC): {
        "BgmPath": "AudioPath",
        "MusicPath": "AudioPath",
        "BackgroundMusic": "AudioPath",
        "AudioId": "AudioPath",
        "AudioID": "AudioPath",
    },
    (NODE_TYPE_ACTION, ACTION_ART): {
        "Illustration": "IllustrationImage",
        "IllustrationPath": "IllustrationImage",
        "Image": "IllustrationImage",
    },
    (NODE_TYPE_ACTION, ACTION_ILLUSTRATION_PAN): {
        "Illustration": "IllustrationImage",
        "IllustrationPath": "IllustrationImage",
        "Image": "IllustrationImage",
    },
    (NODE_TYPE_ACTION, ACTION_ATTRIBUTE_CHANGE): {
        "AttributeID": "AttributeId",
    },
    (NODE_TYPE_ACTION, ACTION_VARIABLE_SET): {
        "VariableID": "VariableId",
    },
    (NODE_TYPE_PROGRESS, PROGRESS_SOFT_TRANSITION): {
        "Background": "BackgroundImage",
        "BackgroundPath": "BackgroundImage",
        "Image": "BackgroundImage",
    },
    (NODE_TYPE_CONDITION, CONDITION_ATTRIBUTE_COMPARE): {
        "AttributeID": "AttributeId",
    },
    (NODE_TYPE_CONDITION, CONDITION_VARIABLE_COMPARE): {
        "VariableID": "VariableId",
    },
    (NODE_TYPE_CONDITION, CONDITION_TACHI_PRESENCE): {
        "TachiId": "TachiID",
    },
}


@dataclass
class VNGraphValidationResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


class VNGraphValidator:
    """VNGraph 校验器。"""

    def validate(self, vn_graph: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        兼容旧调用方的入口。

        返回值保持为 (是否有效, 错误列表)，warning 不影响 is_valid。
        """
        result = self.validate_detailed(vn_graph)
        return result.is_valid, result.errors

    def validate_detailed(self, vn_graph: Dict[str, Any]) -> VNGraphValidationResult:
        """返回完整错误和提示。"""
        result = VNGraphValidationResult()

        if not self._validate_root_object(vn_graph, result):
            return result

        nodes = vn_graph.get("Nodes", [])
        if not self._validate_nodes(nodes, result):
            return result

        nodes_by_index = self._validate_unique_indices(nodes, result)
        self._validate_start_node(vn_graph, nodes, nodes_by_index, result)
        self._validate_data_has_kind(nodes, result)
        self._validate_outputs(nodes, nodes_by_index, result)
        self._validate_no_duplicate_tachi_positions_in_action_groups(nodes, result.errors)
        self._validate_common_model_mistakes(nodes, result)
        self._validate_node_issues(nodes, result)

        return result

    def _validate_root_object(self, vn_graph: Any, result: VNGraphValidationResult) -> bool:
        if not isinstance(vn_graph, dict):
            result.errors.append("VNGraph 根对象必须是对象。")
            return False

        for field_name in ["Version", "StartNodeIndex", "Nodes"]:
            if field_name not in vn_graph:
                result.errors.append(f"根对象缺少必需字段: {field_name}")

        if "Version" in vn_graph and vn_graph["Version"] != 1:
            result.errors.append(f"Version 必须为 1，当前为 {vn_graph['Version']}")

        if "Nodes" in vn_graph and not isinstance(vn_graph["Nodes"], list):
            result.errors.append("Nodes 必须是数组。")

        return len(result.errors) == 0

    def _validate_nodes(self, nodes: Any, result: VNGraphValidationResult) -> bool:
        if not isinstance(nodes, list):
            result.errors.append("Nodes 必须是数组。")
            return False

        if len(nodes) == 0:
            result.errors.append("Nodes 不能为空。")
            return False

        required_fields = ["Index", "DisplayName", "NodeType", "SubType", "X", "Y", "Data", "Outputs"]
        for node_offset, node in enumerate(nodes):
            if not isinstance(node, dict):
                result.errors.append(f"节点 {node_offset} 必须是对象。")
                continue

            for field_name in required_fields:
                if field_name not in node:
                    result.errors.append(f"节点 {node_offset} 缺少必需字段: {field_name}")

            index = node.get("Index")
            if not isinstance(index, int) or isinstance(index, bool):
                result.errors.append(f"节点 {node_offset} 的 Index 必须是整数。")

            node_type = node.get("NodeType")
            if node_type not in VALID_SUBTYPES:
                result.errors.append(f"节点 {self._node_label(node, node_offset)} 的 NodeType 必须是 1、2 或 3。")
            else:
                subtype = node.get("SubType")
                if subtype not in VALID_SUBTYPES[node_type]:
                    result.errors.append(
                        f"节点 {self._node_label(node, node_offset)} 的 SubType {subtype} 对于 NodeType {node_type} 无效。"
                    )

            if "Data" in node and not isinstance(node.get("Data"), dict):
                result.errors.append(f"节点 {self._node_label(node, node_offset)} 的 Data 必须是对象。")

            if "Outputs" in node and not isinstance(node.get("Outputs"), dict):
                result.errors.append(f"节点 {self._node_label(node, node_offset)} 的 Outputs 必须是对象。")

        return len(result.errors) == 0

    def _validate_unique_indices(
        self,
        nodes: List[Dict[str, Any]],
        result: VNGraphValidationResult,
    ) -> Dict[int, Dict[str, Any]]:
        nodes_by_index: Dict[int, Dict[str, Any]] = {}
        for node in nodes:
            index = node.get("Index")
            if not isinstance(index, int) or isinstance(index, bool):
                continue

            if index in nodes_by_index:
                result.errors.append(f"Index {index} 重复。")
                continue

            nodes_by_index[index] = node

        return nodes_by_index

    def _validate_start_node(
        self,
        vn_graph: Dict[str, Any],
        nodes: List[Dict[str, Any]],
        nodes_by_index: Dict[int, Dict[str, Any]],
        result: VNGraphValidationResult,
    ) -> None:
        start_index = vn_graph.get("StartNodeIndex")
        if not isinstance(start_index, int) or isinstance(start_index, bool):
            result.errors.append("StartNodeIndex 必须是整数。")
            return

        start_nodes = [
            node for node in nodes
            if node.get("NodeType") == NODE_TYPE_PROGRESS and node.get("SubType") == PROGRESS_START
        ]
        if len(start_nodes) == 0:
            result.errors.append("节点图必须包含一个开始节点。")
        elif len(start_nodes) > 1:
            result.errors.append(f"节点图只能包含一个开始节点，当前有 {len(start_nodes)} 个。")

        start_node = nodes_by_index.get(start_index)
        if start_node is None:
            result.errors.append(f"StartNodeIndex {start_index} 在 Nodes 中不存在。")
            return

        if start_node.get("NodeType") != NODE_TYPE_PROGRESS or start_node.get("SubType") != PROGRESS_START:
            result.errors.append(f"StartNodeIndex {start_index} 必须指向开始节点。")

    def _validate_data_has_kind(
        self,
        nodes: List[Dict[str, Any]],
        result: VNGraphValidationResult,
    ) -> None:
        valid_kinds = {
            "String",
            "Int",
            "Float",
            "Double",
            "Bool",
            "Enum",
            "Vector2",
            "Vector3",
            "Color",
            "List",
            "Object",
            "Null",
        }

        for node in nodes:
            data = node.get("Data", {})
            if not isinstance(data, dict):
                continue

            for field_name, value in data.items():
                self._validate_serialized_value(
                    value,
                    f"节点 #{node.get('Index')} 的 Data['{field_name}']",
                    valid_kinds,
                    result,
                )

    def _validate_serialized_value(
        self,
        value: Any,
        path: str,
        valid_kinds: set,
        result: VNGraphValidationResult,
    ) -> None:
        if not isinstance(value, dict):
            result.errors.append(f"{path} 应该是对象，当前为 {type(value).__name__}。")
            return

        kind = value.get("Kind")
        if not kind:
            result.errors.append(f"{path} 缺少 Kind 字段。")
            return

        if kind not in valid_kinds:
            result.errors.append(f"{path} 的 Kind 值 '{kind}' 无效。")
            return

        if kind == "List":
            items = value.get("Items")
            if items is None:
                result.errors.append(f"{path} 是 List 类型但缺少 Items 字段。")
                return
            if not isinstance(items, list):
                result.errors.append(f"{path}.Items 必须是数组。")
                return
            for index, item in enumerate(items):
                if item is None:
                    continue
                self._validate_serialized_value(item, f"{path}.Items[{index}]", valid_kinds, result)

        if kind == "Object":
            object_value = value.get("ObjectValue")
            if object_value is None:
                result.errors.append(f"{path} 是 Object 类型但缺少 ObjectValue 字段。")
                return
            if not isinstance(object_value, dict):
                result.errors.append(f"{path}.ObjectValue 必须是对象。")
                return
            for field_name, field_value in object_value.items():
                self._validate_serialized_value(
                    field_value,
                    f"{path}.ObjectValue['{field_name}']",
                    valid_kinds,
                    result,
                )

    def _validate_outputs(
        self,
        nodes: List[Dict[str, Any]],
        nodes_by_index: Dict[int, Dict[str, Any]],
        result: VNGraphValidationResult,
    ) -> None:
        incoming: Dict[int, List[Tuple[int, str]]] = {}

        for node in nodes:
            outputs = node.get("Outputs", {})
            if not isinstance(outputs, dict):
                continue

            for output_key, target_indexes in outputs.items():
                target_type = self._get_output_target_type(node, output_key, result)
                if target_type is None:
                    continue

                if not isinstance(target_indexes, list):
                    result.errors.append(
                        f"节点 #{node.get('Index')} 的 Outputs['{output_key}'] 必须是数组，当前为 {type(target_indexes).__name__}。"
                    )
                    continue

                if self._is_single_output_key(output_key) and len(target_indexes) > 1:
                    result.errors.append(f"节点 #{node.get('Index')} 的 {output_key} 输出端口最多只能连接 1 个节点。")

                for target_index in target_indexes:
                    if not isinstance(target_index, int) or isinstance(target_index, bool):
                        result.errors.append(f"节点 #{node.get('Index')} 的 {output_key} 输出目标必须是整数。")
                        continue

                    if target_index == node.get("Index"):
                        result.errors.append(f"节点 #{node.get('Index')} 的 {output_key} 输出不能连接到自己。")
                        continue

                    target_node = nodes_by_index.get(target_index)
                    if target_node is None:
                        result.errors.append(f"节点 #{node.get('Index')} 的 {output_key} 指向不存在的 #{target_index}。")
                        continue

                    if target_node.get("NodeType") != target_type:
                        result.errors.append(
                            f"节点 #{node.get('Index')} 的 {output_key} 只能连接到"
                            f"{self._format_node_type(target_type)}节点，不能连接到 #{target_index}。"
                        )
                        continue

                    incoming.setdefault(target_index, []).append((node.get("Index"), output_key))

        self._validate_input_capacity(nodes_by_index, incoming, result)

    def _get_output_target_type(
        self,
        node: Dict[str, Any],
        output_key: str,
        result: VNGraphValidationResult,
    ) -> Optional[int]:
        node_type = node.get("NodeType")
        subtype = node.get("SubType")

        if node_type == NODE_TYPE_PROGRESS and subtype == PROGRESS_CHOICE:
            match = CHOICE_OUTPUT_RE.match(output_key or "")
            if not match:
                result.errors.append(f"节点 #{node.get('Index')} 的 Outputs 包含非法 key: {output_key}")
                return None

            option_index = int(match.group(1))
            options = self._data_value(node, "Options")
            if isinstance(options, list) and option_index >= len(options):
                result.errors.append(f"节点 #{node.get('Index')} 的 {output_key} 超出选项数量。")
                return None

            output_name = match.group(2)
            if output_name == "Condition":
                return NODE_TYPE_CONDITION
            return NODE_TYPE_PROGRESS

        rule = OUTPUT_TARGET_TYPES.get((node_type, subtype), {})
        if output_key not in rule:
            result.errors.append(f"节点 #{node.get('Index')} 的 Outputs 包含非法 key: {output_key}")
            return None

        return rule[output_key]

    def _validate_input_capacity(
        self,
        nodes_by_index: Dict[int, Dict[str, Any]],
        incoming: Dict[int, List[Tuple[int, str]]],
        result: VNGraphValidationResult,
    ) -> None:
        for target_index, connections in incoming.items():
            target_node = nodes_by_index.get(target_index)
            if target_node is None:
                continue

            if (target_node.get("NodeType"), target_node.get("SubType")) in NO_INPUT_NODES:
                result.errors.append(f"节点 #{target_index} 没有输入端口，不能接收连线。")
                continue

            if (target_node.get("NodeType"), target_node.get("SubType")) in MULTI_INPUT_NODES:
                continue

            if len(connections) <= 1:
                continue

            source_indexes = {source_index for source_index, _ in connections}
            if len(source_indexes) == 1:
                source_node = nodes_by_index.get(next(iter(source_indexes)))
                if source_node and source_node.get("NodeType") == NODE_TYPE_PROGRESS and source_node.get("SubType") == PROGRESS_CHOICE:
                    continue

            result.errors.append(f"节点 #{target_index} 的输入端口已有连接，不能同时接收 {len(connections)} 条连线。")

    def _validate_node_issues(
        self,
        nodes: List[Dict[str, Any]],
        result: VNGraphValidationResult,
    ) -> None:
        for node in nodes:
            node_type = node.get("NodeType")
            subtype = node.get("SubType")

            if node_type == NODE_TYPE_PROGRESS:
                self._validate_progress_node_issues(node, subtype, result)
            elif node_type == NODE_TYPE_ACTION:
                self._validate_action_node_issues(node, subtype, result)
            elif node_type == NODE_TYPE_CONDITION:
                self._validate_condition_node_issues(node, subtype, result)

    def _validate_common_model_mistakes(
        self,
        nodes: List[Dict[str, Any]],
        result: VNGraphValidationResult,
    ) -> None:
        for node in nodes:
            data = node.get("Data")
            if not isinstance(data, dict):
                continue

            node_type = node.get("NodeType")
            subtype = node.get("SubType")
            aliases = DATA_FIELD_ALIASES.get((node_type, subtype), {})
            for wrong_name, right_name in aliases.items():
                if wrong_name not in data:
                    continue
                if right_name in data:
                    result.warnings.append(
                        f"节点 #{node.get('Index')}: Data['{wrong_name}'] 不是当前节点的保存字段；"
                        f"已存在 Data['{right_name}']，请移除多余字段。"
                    )
                else:
                    result.errors.append(
                        f"节点 #{node.get('Index')}: Data['{wrong_name}'] 不是当前节点的保存字段，"
                        f"请改用 Data['{right_name}']。"
                    )

            self._validate_common_node_type_mistake(node, data, result)

    def _validate_common_node_type_mistake(
        self,
        node: Dict[str, Any],
        data: Dict[str, Any],
        result: VNGraphValidationResult,
    ) -> None:
        node_type = node.get("NodeType")
        subtype = node.get("SubType")
        index = node.get("Index")

        if node_type != NODE_TYPE_PROGRESS:
            return

        if subtype == PROGRESS_DIALOGUE and self._has_any_data(data, {"TachiID", "TachiId", "TachiIamge", "TachiImage", "EnterType"}):
            result.errors.append(f"节点 #{index}: NodeType=1/SubType=1 是对白节点；立绘入场请使用 NodeType=2/SubType=1。")
        elif subtype == PROGRESS_CHAPTER and self._has_any_data(data, {"BackgroundImage", "Background", "BackgroundPath", "ChangeType"}):
            result.errors.append(f"节点 #{index}: NodeType=1/SubType=3 是章节跳转节点；背景切换请使用 NodeType=2/SubType=3。")
        elif subtype == PROGRESS_TRANSITION and self._has_any_data(data, {"AudioPath", "VoiceId", "VoiceID", "Volume", "PitchScale"}):
            result.errors.append(f"节点 #{index}: NodeType=1/SubType=4 是转场流程节点；配音动作请使用 NodeType=2/SubType=4。")
        elif subtype == PROGRESS_CHOICE and self._has_any_data(data, {"AudioPath", "SoundId", "SoundPath", "Volume", "PitchScale"}):
            result.errors.append(f"节点 #{index}: NodeType=1/SubType=5 是选择节点；音效动作请使用 NodeType=2/SubType=5。")

    def _validate_progress_node_issues(
        self,
        node: Dict[str, Any],
        subtype: int,
        result: VNGraphValidationResult,
    ) -> None:
        if subtype == PROGRESS_DIALOGUE:
            self._require_text(node, "TextData", "对话节点缺少台词内容。", result)
        elif subtype == PROGRESS_PARAGRAPH:
            self._validate_paragraph(node, result)
        elif subtype == PROGRESS_CHOICE:
            self._validate_choice(node, result)
        elif subtype == PROGRESS_END:
            self._validate_end(node, result)
        elif subtype == PROGRESS_CONDITION_BRANCH:
            self._require_output(node, "Condition", "条件分叉缺少条件节点。", result)
            self._warn_missing_output(node, "TrueNext", "条件满足分支没有连接下一流程。", result)
            self._warn_missing_output(node, "FalseNext", "条件不满足分支没有连接下一流程。", result)
        elif subtype == PROGRESS_VARIABLE_INPUT:
            self._require_text(node, "VariableId", "变量输入节点缺少变量 ID。", result)
        elif subtype == PROGRESS_VARIABLE_TEXT_REPLACE:
            self._require_text(node, "VariableId", "变量文本替换节点缺少变量 ID。", result)
            self._require_text(node, "SourceText", "变量文本替换节点缺少原文本。", result)
        elif subtype == PROGRESS_TRANSITION:
            self._warn_invalid_duration(node, "Duration", "转场持续时间小于 0，会被当作立即完成。", result)
        elif subtype == PROGRESS_SOFT_TRANSITION:
            self._require_text(node, "BackgroundImage", "软转场缺少背景资源。", result)
        elif subtype == PROGRESS_SOFT_MERGE:
            self._warn_missing_output(node, "Next", "软合流还没有连接下一流程。", result)
            result.warnings.append(f"节点 #{node.get('Index')}: 软合流只合并流程，不改变舞台；请保证所有上游线路进入时表现一致。")

    def _validate_action_node_issues(
        self,
        node: Dict[str, Any],
        subtype: int,
        result: VNGraphValidationResult,
    ) -> None:
        if subtype == ACTION_TACHI:
            self._require_text(node, "TachiID", "立绘入场缺少立绘 ID。", result)
            self._require_text(node, "TachiIamge", "立绘入场缺少立绘资源。", result)
            self._warn_invalid_duration(node, "Duration", "立绘入场时间小于 0，会被当作立即完成。", result)
        elif subtype == ACTION_TACHI_MOVE:
            self._require_text(node, "TachiID", "立绘移动缺少立绘 ID。", result)
            self._warn_invalid_duration(node, "Duration", "立绘移动时间小于 0，会被当作立即完成。", result)
        elif subtype == ACTION_TACHI_MOVE_X:
            self._require_text(node, "TachiID", "水平移动缺少立绘 ID。", result)
            self._warn_invalid_duration(node, "Duration", "水平移动时间小于 0，会被当作立即完成。", result)
        elif subtype == ACTION_TACHI_MOVE_BESIDE:
            self._require_text(node, "TachiID", "移动到旁边缺少移动目标立绘 ID。", result)
            self._require_text(node, "ReferenceTachiID", "移动到旁边缺少参照立绘 ID。", result)
            self._warn_invalid_duration(node, "Duration", "移动到旁边时间小于 0，会被当作立即完成。", result)
        elif subtype == ACTION_TACHI_SCALE:
            self._require_text(node, "TachiID", "立绘缩放缺少立绘 ID。", result)
            self._warn_invalid_duration(node, "Duration", "立绘缩放时间小于 0，会被当作立即完成。", result)
            target_scale = self._number_data_value(node, "TargetScale")
            if target_scale is not None and target_scale <= 0:
                result.warnings.append(f"节点 #{node.get('Index')}: 目标缩放小于等于 0，立绘可能不可见。")
        elif subtype == ACTION_TACHI_CHANGE_IMAGE:
            self._require_text(node, "TachiID", "立绘换图缺少立绘 ID。", result)
            self._require_text(node, "TachiIamge", "立绘换图缺少立绘资源。", result)
        elif subtype == ACTION_TACHI_OPACITY:
            self._require_text(node, "TachiID", "立绘透明度缺少立绘 ID。", result)
            self._warn_invalid_duration(node, "Duration", "立绘透明度时间小于 0，会被当作立即完成。", result)
            self._warn_invalid_alpha(node, "TargetAlpha", "目标透明度建议在 0 到 1 之间，运行时会夹到有效范围。", result)
        elif subtype == ACTION_TACHI_HIGHLIGHT:
            self._require_text(node, "TachiID", "角色高亮缺少立绘 ID。", result)
            self._warn_invalid_duration(node, "Duration", "角色高亮压暗时间小于 0，会被当作立即完成。", result)
            self._warn_invalid_alpha(node, "HighlightAlpha", "高亮透明度建议在 0 到 1 之间，运行时会夹到有效范围。", result)
            self._warn_invalid_alpha(node, "DimAlpha", "压暗透明度建议在 0 到 1 之间，运行时会夹到有效范围。", result)
        elif subtype == ACTION_TACHI_FLIP:
            self._require_text(node, "TachiID", "立绘翻转缺少立绘 ID。", result)
        elif subtype == ACTION_TACHI_ROTATE:
            self._require_text(node, "TachiID", "立绘旋转缺少立绘 ID。", result)
            self._warn_invalid_duration(node, "Duration", "立绘旋转时间小于 0，会被当作立即完成。", result)
            self._warn_invalid_number(node, "TargetRotationDegrees", "立绘旋转角度无效。", result)
        elif subtype == ACTION_TACHI_EXIT:
            self._require_text(node, "TachiID", "立绘退场缺少立绘 ID。", result)
            self._warn_invalid_duration(node, "Duration", "立绘退场时间小于 0，会被当作立即完成。", result)
        elif subtype == ACTION_TACHI_EFFECT:
            self._require_text(node, "TachiID", "立绘效果缺少立绘 ID。", result)
        elif subtype == ACTION_TACHI_SHAKE:
            self._require_text(node, "TachiID", "立绘抖动缺少立绘 ID。", result)
            self._warn_invalid_duration(node, "Duration", "立绘抖动时间小于 0，会被当作立即完成。", result)
            self._warn_invalid_nonnegative(node, "Amplitude", "立绘抖动振幅无效，会被当作 0。", result)
            self._warn_invalid_nonnegative(node, "Frequency", "立绘抖动频率无效，会被当作 0。", result)
        elif subtype == ACTION_SCREEN_SHAKE:
            self._warn_invalid_duration(node, "Duration", "屏幕震动时间小于 0，会被当作立即完成。", result)
            self._warn_invalid_nonnegative(node, "Amplitude", "屏幕震动振幅无效，会被当作 0。", result)
            self._warn_invalid_nonnegative(node, "Frequency", "屏幕震动频率无效，会被当作 0。", result)
        elif subtype == ACTION_BACKGROUND:
            self._require_text(node, "BackgroundImage", "背景节点缺少背景资源。", result)
            self._warn_invalid_duration(node, "Duration", "背景切换时间小于 0，会被当作立即完成。", result)
        elif subtype == ACTION_BACKGROUND_BLACK:
            self._warn_invalid_duration(node, "Duration", "背景变黑时间小于 0，会被当作立即完成。", result)
        elif subtype == ACTION_ART:
            self._warn_invalid_duration(node, "Duration", "插画切换时间小于 0，会被当作立即完成。", result)
        elif subtype == ACTION_ILLUSTRATION_PAN:
            self._require_text(node, "IllustrationImage", "插画镜头移动缺少插画资源。", result)
            self._warn_invalid_duration(node, "Duration", "插画镜头移动时间小于 0，会被当作立即完成。", result)
            self._warn_minimum_number(node, "FocusScale", 1, "插画镜头倍率建议不小于 1，运行时会夹到有效范围。", result)
        elif subtype == ACTION_STAGE_CAMERA:
            self._warn_invalid_duration(node, "Duration", "舞台镜头时间小于 0，会被当作立即完成。", result)
            self._warn_minimum_number(node, "Zoom", 1, "舞台镜头倍率建议不小于 1，运行时会夹到有效范围。", result)
        elif subtype == ACTION_BACKGROUND_PAN:
            self._warn_invalid_duration(node, "Duration", "背景镜头移动时间小于 0，会被当作立即完成。", result)
            self._warn_minimum_number(node, "Zoom", 1, "背景镜头倍率建议不小于 1，运行时会夹到有效范围。", result)
        elif subtype == ACTION_STAGE_CAMERA_RESTORE:
            self._warn_invalid_duration(node, "Duration", "舞台镜头恢复时间小于 0，会被当作立即完成。", result)
        elif subtype == ACTION_BACKGROUND_MUSIC:
            self._warn_invalid_duration(node, "FadeTime", "BGM 淡入淡出时间无效，会被当作立即完成。", result)
            volume = self._number_data_value(node, "Volume")
            if volume is not None and volume < 0:
                result.warnings.append(f"节点 #{node.get('Index')}: BGM 音量小于 0，播放效果可能异常。")
        elif subtype == ACTION_AUDIO:
            self._require_text(node, "AudioPath", "音效节点缺少音频资源。", result)
            self._warn_invalid_audio_value(node, "Volume", "音效音量无效。", result)
            self._warn_invalid_audio_value(node, "PitchScale", "音效音调无效。", result)
        elif subtype == ACTION_VOICE:
            self._require_text(node, "AudioPath", "配音节点缺少音频资源。", result)
            self._warn_invalid_audio_value(node, "Volume", "配音音量无效。", result)
            self._warn_invalid_audio_value(node, "PitchScale", "配音音调无效。", result)
        elif subtype == ACTION_STOP_VOICE:
            self._warn_invalid_duration(node, "FadeTime", "停止配音淡出时间小于 0，会被当作立即完成。", result)
        elif subtype == ACTION_DELAY:
            self._warn_invalid_duration(node, "Duration", "等待时间小于 0，会被当作立即完成。", result)
        elif subtype == ACTION_SEQUENCE:
            self._warn_empty_output(node, "Actions", "顺序动作没有连接任何动作。", result)
        elif subtype == ACTION_PARALLEL:
            self._warn_empty_output(node, "Actions", "并行动作没有连接任何动作。", result)
        elif subtype == ACTION_CONDITIONAL:
            self._require_output(node, "Condition", "条件行为缺少条件节点。", result)
            if not self._has_output(node, "TrueActions") and not self._has_output(node, "FalseActions"):
                result.warnings.append(f"节点 #{node.get('Index')}: 条件行为没有连接任何分支动作。")
        elif subtype == ACTION_ATTRIBUTE_CHANGE:
            self._require_text(node, "AttributeId", "属性变化缺少属性 ID。", result)
        elif subtype == ACTION_VARIABLE_SET:
            self._require_text(node, "VariableId", "变量赋值节点缺少变量 ID。", result)

    def _validate_condition_node_issues(
        self,
        node: Dict[str, Any],
        subtype: int,
        result: VNGraphValidationResult,
    ) -> None:
        if subtype == CONDITION_ATTRIBUTE_COMPARE:
            self._require_text(node, "AttributeId", "属性比较缺少属性 ID。", result)
        elif subtype == CONDITION_VARIABLE_COMPARE:
            self._require_text(node, "VariableId", "变量比较缺少变量 ID。", result)
            compare_operator = self._data_value(node, "Operator")
            if self._requires_compare_value(compare_operator):
                self._require_text(node, "CompareValue", "变量比较缺少比较值。", result)
        elif subtype == CONDITION_GROUP:
            self._warn_empty_output(node, "Conditions", "条件组还没有连接任何条件，会按“空列表结果”返回。", result)

    def _validate_paragraph(self, node: Dict[str, Any], result: VNGraphValidationResult) -> None:
        lines = self._data_value(node, "Lines")
        if not isinstance(lines, list) or len(lines) == 0:
            result.errors.append(f"节点 #{node.get('Index')}: 段落节点没有对白行。")
            return

        has_text = False
        for index, line in enumerate(lines):
            if isinstance(line, dict) and not self._is_blank_text(line.get("Text")):
                has_text = True
                continue

            result.warnings.append(f"节点 #{node.get('Index')}: 第 {index + 1} 行对白为空。")

        if not has_text:
            result.errors.append(f"节点 #{node.get('Index')}: 段落节点没有有效台词。")

    def _validate_choice(self, node: Dict[str, Any], result: VNGraphValidationResult) -> None:
        data = node.get("Data", {})
        if isinstance(data, dict) and "Choices" in data:
            result.errors.append(
                f"节点 #{node.get('Index')}: ChoiceNode 的字段名是 Options，不是 Choices；"
                "选项跳转要写在 Outputs['Options[i].Next']，不要写 TargetNodeIndex。"
            )

        options = self._data_value(node, "Options")
        if not isinstance(options, list) or len(options) == 0:
            result.errors.append(f"节点 #{node.get('Index')}: 选择节点没有选项。")
            return

        valid_text_count = 0
        for index, option in enumerate(options):
            if not isinstance(option, dict):
                result.warnings.append(f"节点 #{node.get('Index')}: 第 {index + 1} 个选项为空。")
                continue

            if self._is_blank_text(option.get("Text")):
                result.warnings.append(f"节点 #{node.get('Index')}: 第 {index + 1} 个选项缺少文本。")
            else:
                valid_text_count += 1

            output_key = f"Options[{index}].Next"
            if not self._has_output(node, output_key):
                result.warnings.append(f"节点 #{node.get('Index')}: 第 {index + 1} 个选项没有连接下一流程。")

        if valid_text_count == 0:
            result.errors.append(f"节点 #{node.get('Index')}: 选择节点没有可显示的选项文本。")

    def _validate_end(self, node: Dict[str, Any], result: VNGraphValidationResult) -> None:
        data = node.get("Data", {})
        if isinstance(data, dict) and ("Choices" in data or "Options" in data):
            result.errors.append(
                f"节点 #{node.get('Index')}: SubType=11 是章节结束节点，不是选择节点；"
                "选择节点请使用 NodeType=1、SubType=5、Data.Options 和 Outputs['Options[i].Next']。"
            )

    def _require_text(
        self,
        node: Dict[str, Any],
        field_name: str,
        message: str,
        result: VNGraphValidationResult,
    ) -> None:
        if self._is_blank_text(self._data_value(node, field_name)):
            result.errors.append(f"节点 #{node.get('Index')}: {message}")

    def _require_output(
        self,
        node: Dict[str, Any],
        output_key: str,
        message: str,
        result: VNGraphValidationResult,
    ) -> None:
        if not self._has_output(node, output_key):
            result.errors.append(f"节点 #{node.get('Index')}: {message}")

    def _warn_missing_output(
        self,
        node: Dict[str, Any],
        output_key: str,
        message: str,
        result: VNGraphValidationResult,
    ) -> None:
        if not self._has_output(node, output_key):
            result.warnings.append(f"节点 #{node.get('Index')}: {message}")

    def _warn_empty_output(
        self,
        node: Dict[str, Any],
        output_key: str,
        message: str,
        result: VNGraphValidationResult,
    ) -> None:
        if not self._has_output(node, output_key):
            result.warnings.append(f"节点 #{node.get('Index')}: {message}")

    def _warn_invalid_duration(
        self,
        node: Dict[str, Any],
        field_name: str,
        message: str,
        result: VNGraphValidationResult,
    ) -> None:
        self._warn_invalid_nonnegative(node, field_name, message, result)

    def _warn_invalid_audio_value(
        self,
        node: Dict[str, Any],
        field_name: str,
        message: str,
        result: VNGraphValidationResult,
    ) -> None:
        self._warn_invalid_nonnegative(node, field_name, message, result)

    def _warn_invalid_nonnegative(
        self,
        node: Dict[str, Any],
        field_name: str,
        message: str,
        result: VNGraphValidationResult,
    ) -> None:
        value = self._number_data_value(node, field_name)
        if value is not None and (value < 0 or not math.isfinite(value)):
            result.warnings.append(f"节点 #{node.get('Index')}: {message}")

    def _warn_invalid_number(
        self,
        node: Dict[str, Any],
        field_name: str,
        message: str,
        result: VNGraphValidationResult,
    ) -> None:
        value = self._number_data_value(node, field_name)
        if value is not None and not math.isfinite(value):
            result.warnings.append(f"节点 #{node.get('Index')}: {message}")

    def _warn_invalid_alpha(
        self,
        node: Dict[str, Any],
        field_name: str,
        message: str,
        result: VNGraphValidationResult,
    ) -> None:
        value = self._number_data_value(node, field_name)
        if value is not None and (value < 0 or value > 1 or not math.isfinite(value)):
            result.warnings.append(f"节点 #{node.get('Index')}: {message}")

    def _warn_minimum_number(
        self,
        node: Dict[str, Any],
        field_name: str,
        minimum: float,
        message: str,
        result: VNGraphValidationResult,
    ) -> None:
        value = self._number_data_value(node, field_name)
        if value is not None and (value < minimum or not math.isfinite(value)):
            result.warnings.append(f"节点 #{node.get('Index')}: {message}")

    def _has_output(self, node: Dict[str, Any], output_key: str) -> bool:
        outputs = node.get("Outputs", {})
        value = outputs.get(output_key) if isinstance(outputs, dict) else None
        return isinstance(value, list) and len(value) > 0

    def _has_any_data(self, data: Dict[str, Any], field_names: set) -> bool:
        return any(field_name in data for field_name in field_names)

    def _is_single_output_key(self, output_key: str) -> bool:
        if output_key in SINGLE_OUTPUT_KEYS:
            return True
        match = CHOICE_OUTPUT_RE.match(output_key or "")
        return bool(match)

    def _data_value(self, node: Dict[str, Any], field_name: str) -> Any:
        data = node.get("Data", {})
        if not isinstance(data, dict) or field_name not in data:
            return None
        return self._unwrap_serialized_value(data.get(field_name))

    def _unwrap_serialized_value(self, value: Any) -> Any:
        if value is None:
            return None

        if not isinstance(value, dict) or "Kind" not in value:
            return value

        kind = value.get("Kind")
        if kind in {"String", "Enum"}:
            return value.get("StringValue", "")
        if kind in {"Int", "Float", "Double"}:
            return value.get("NumberValue", 0)
        if kind == "Bool":
            return bool(value.get("BoolValue", False))
        if kind == "Vector2":
            return {"x": value.get("X", 0), "y": value.get("Y", 0)}
        if kind == "Vector3":
            return {"x": value.get("X", 0), "y": value.get("Y", 0), "z": value.get("Z", 0)}
        if kind == "Color":
            return {
                "r": value.get("X", 0),
                "g": value.get("Y", 0),
                "b": value.get("Z", 0),
                "a": value.get("W", 1),
            }
        if kind == "List":
            return [self._unwrap_serialized_value(item) for item in value.get("Items") or []]
        if kind == "Object":
            object_value = value.get("ObjectValue") or {}
            return {
                key: self._unwrap_serialized_value(item)
                for key, item in object_value.items()
            }
        if kind == "Null":
            return None
        return value

    def _number_data_value(self, node: Dict[str, Any], field_name: str) -> Optional[float]:
        value = self._data_value(node, field_name)
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _requires_compare_value(self, compare_operator: Any) -> bool:
        return compare_operator not in {"IsEmpty", "IsNotEmpty"}

    def _is_blank_text(self, value: Any) -> bool:
        return value is None or str(value).strip() == ""

    def _node_label(self, node: Dict[str, Any], node_offset: int) -> str:
        index = node.get("Index")
        if isinstance(index, int) and not isinstance(index, bool):
            return f"#{index}"
        return str(node_offset)

    def _format_node_type(self, node_type: int) -> str:
        if node_type == NODE_TYPE_PROGRESS:
            return "流程"
        if node_type == NODE_TYPE_ACTION:
            return "动作"
        if node_type == NODE_TYPE_CONDITION:
            return "条件"
        return "未知"

    def _validate_no_duplicate_tachi_positions_in_action_groups(self, nodes: List[Dict[str, Any]], errors: List[str]) -> bool:
        """验证一个 Progress 节点 Outputs.Actions 内的 TachiNode 不共享坐标。"""
        node_by_index = {
            node.get("Index"): node
            for node in nodes
            if isinstance(node, dict) and isinstance(node.get("Index"), int)
        }

        for node in nodes:
            if not isinstance(node, dict):
                continue
            outputs = node.get("Outputs", {})
            if not isinstance(outputs, dict):
                continue
            actions = outputs.get("Actions")
            if not isinstance(actions, list):
                continue

            seen_positions = {}
            for action_index in actions:
                action = node_by_index.get(action_index)
                if not isinstance(action, dict):
                    continue
                if action.get("NodeType") != 2 or action.get("SubType") != 1:
                    continue
                data = action.get("Data", {})
                target = data.get("TargetPosition") if isinstance(data, dict) else None
                if not isinstance(target, dict) or target.get("Kind") != "Vector2":
                    continue
                x = target.get("X", 0)
                y = target.get("Y", 0)
                if not x and not y:
                    continue
                position_key = (x, y)
                if position_key in seen_positions:
                    errors.append(
                        f"节点 {node.get('Index')} 的 Actions 内 TachiNode {seen_positions[position_key]} "
                        f"和 {action_index} 共享 TargetPosition {position_key}"
                    )
                else:
                    seen_positions[position_key] = action_index

        return len(errors) == 0


vn_graph_validator = VNGraphValidator()
