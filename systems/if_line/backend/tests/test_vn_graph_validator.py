from app.utils.vn_graph_validator import VNGraphValidator


def s(value):
    return {"Kind": "String", "StringValue": value}


def e(value):
    return {"Kind": "Enum", "StringValue": value}


def f(value):
    return {"Kind": "Float", "NumberValue": value}


def l(items):
    return {"Kind": "List", "Items": items}


def o(value):
    return {"Kind": "Object", "ObjectValue": value}


def node(index, node_type, subtype, data=None, outputs=None):
    return {
        "Index": index,
        "DisplayName": "",
        "Comment": "",
        "NodeType": node_type,
        "SubType": subtype,
        "X": 0,
        "Y": 0,
        "Data": data or {},
        "Outputs": outputs or {},
    }


def valid_graph():
    return {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            node(1, 1, 6, outputs={"Next": [2]}),
            node(
                2,
                1,
                1,
                data={"TextData": s("你好")},
                outputs={"Actions": [3], "Next": [4]},
            ),
            node(
                3,
                2,
                3,
                data={"BackgroundImage": s("bg_room"), "Duration": f(0.3)},
            ),
            node(4, 1, 11),
        ],
    }


def test_vn_graph_validator_accepts_aivn_current_node_set():
    validator = VNGraphValidator()

    result = validator.validate_detailed(valid_graph())

    assert result.errors == []
    assert result.warnings == []
    assert result.is_valid


def test_vn_graph_validator_keeps_legacy_validate_signature():
    validator = VNGraphValidator()

    is_valid, errors = validator.validate(valid_graph())

    assert is_valid is True
    assert errors == []


def test_vn_graph_validator_reports_warnings_without_blocking():
    graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            node(1, 1, 6, outputs={"Next": [2]}),
            node(2, 1, 15),
        ],
    }

    result = VNGraphValidator().validate_detailed(graph)
    is_valid, errors = VNGraphValidator().validate(graph)

    assert result.errors == []
    assert any("软合流还没有连接下一流程" in warning for warning in result.warnings)
    assert is_valid is True
    assert errors == []


def test_vn_graph_validator_validates_condition_nodes_and_branch_outputs():
    graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            node(1, 1, 6, outputs={"Next": [2]}),
            node(2, 1, 7, outputs={"Condition": [3], "TrueNext": [4], "FalseNext": []}),
            node(
                3,
                3,
                2,
                data={
                    "VariableId": s("flag"),
                    "Operator": e("Equal"),
                    "CompareValue": s(""),
                },
            ),
            node(4, 1, 11),
        ],
    }

    result = VNGraphValidator().validate_detailed(graph)

    assert any("变量比较缺少比较值" in error for error in result.errors)
    assert any("条件不满足分支没有连接下一流程" in warning for warning in result.warnings)


def test_vn_graph_validator_reports_output_type_and_input_capacity():
    graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            node(1, 1, 6, outputs={"Next": [2]}),
            node(2, 1, 1, data={"TextData": s("A")}, outputs={"Next": [4]}),
            node(3, 1, 1, data={"TextData": s("B")}, outputs={"Next": [4]}),
            node(4, 1, 1, data={"TextData": s("C")}),
            node(5, 2, 3, data={"BackgroundImage": s("bg")}, outputs={"Next": [4]}),
        ],
    }

    result = VNGraphValidator().validate_detailed(graph)

    assert any("输入端口已有连接" in error for error in result.errors)
    assert any("Outputs 包含非法 key: Next" in error for error in result.errors)


def test_vn_graph_validator_reports_node_configuration_errors():
    graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            node(1, 1, 6, outputs={"Next": [2]}),
            node(2, 1, 2, data={"Lines": l([o({"Text": s("")})])}, outputs={"Next": [3]}),
            node(3, 1, 5, data={"Options": l([o({"Text": s("")})])}, outputs={"Options[0].Next": []}),
        ],
    }

    result = VNGraphValidator().validate_detailed(graph)

    assert any("段落节点没有有效台词" in error for error in result.errors)
    assert any("选择节点没有可显示的选项文本" in error for error in result.errors)
    assert any("第 1 行对白为空" in warning for warning in result.warnings)
    assert any("第 1 个选项没有连接下一流程" in warning for warning in result.warnings)


def test_vn_graph_validator_reports_common_wrong_choice_shape():
    graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            node(1, 1, 6, outputs={"Next": [2]}),
            node(
                2,
                1,
                11,
                data={
                    "Choices": l([
                        o({
                            "Text": s("直接复读"),
                            "TargetNodeIndex": f(3),
                        })
                    ]),
                },
            ),
            node(3, 1, 11),
        ],
    }

    result = VNGraphValidator().validate_detailed(graph)

    assert any("SubType=11 是章节结束节点" in error for error in result.errors)
    assert any("Data.Options" in error for error in result.errors)


def test_vn_graph_validator_reports_common_wrong_dialogue_data_fields():
    graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            node(1, 1, 6, outputs={"Next": [2]}),
            node(
                2,
                1,
                1,
                data={
                    "SpeakerId": s("白非立"),
                    "Text": s("我只是感冒影响了发挥。"),
                    "VoiceId": s("voice_001"),
                },
                outputs={"Next": [3]},
            ),
            node(3, 1, 11),
        ],
    }

    result = VNGraphValidator().validate_detailed(graph)

    assert any("Data['SpeakerId']" in error and "Data['SpeakerIdData']" in error for error in result.errors)
    assert any("Data['Text']" in error and "Data['TextData']" in error for error in result.errors)
    assert any("Data['VoiceId']" in error and "Data['VoiceIdData']" in error for error in result.errors)


def test_vn_graph_validator_reports_common_wrong_tachi_image_field():
    graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            node(1, 1, 6, outputs={"Next": [2]}),
            node(2, 1, 2, data={"Lines": l([o({"Text": s("白非立走进房间。")})])}, outputs={"Actions": [3], "Next": [4]}),
            node(
                3,
                2,
                1,
                data={
                    "TachiID": s("白非立"),
                    "TachiImage": s("res://tachis/baifeili.png"),
                    "Duration": f(0.3),
                },
            ),
            node(4, 1, 11),
        ],
    }

    result = VNGraphValidator().validate_detailed(graph)

    assert any("Data['TachiImage']" in error and "Data['TachiIamge']" in error for error in result.errors)


def test_vn_graph_validator_reports_common_wrong_asset_fields():
    graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            node(1, 1, 6, outputs={"Next": [2]}),
            node(2, 1, 2, data={"Lines": l([o({"Text": s("插画和音乐同时切入。")})])}, outputs={"Actions": [3, 4], "Next": [5]}),
            node(3, 2, 6, data={"Image": s("res://illustrations/cg.png")}),
            node(4, 2, 21, data={"MusicPath": s("res://audio/bgm.ogg")}),
            node(5, 1, 11),
        ],
    }

    result = VNGraphValidator().validate_detailed(graph)

    assert any("Data['Image']" in error and "Data['IllustrationImage']" in error for error in result.errors)
    assert any("Data['MusicPath']" in error and "Data['AudioPath']" in error for error in result.errors)


def test_vn_graph_validator_reports_common_wrong_tachi_id_fields():
    graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            node(1, 1, 6, outputs={"Next": [2]}),
            node(2, 1, 2, data={"Lines": l([o({"Text": s("白非立移动到杨志强旁边。")})])}, outputs={"Actions": [3], "Next": [4]}),
            node(
                3,
                2,
                15,
                data={
                    "TachiId": s("白非立"),
                    "ReferenceTachiId": s("杨志强"),
                },
            ),
            node(4, 1, 11),
        ],
    }

    result = VNGraphValidator().validate_detailed(graph)

    assert any("Data['TachiId']" in error and "Data['TachiID']" in error for error in result.errors)
    assert any("Data['ReferenceTachiId']" in error and "Data['ReferenceTachiID']" in error for error in result.errors)


def test_vn_graph_validator_reports_common_wrong_variable_and_attribute_ids():
    graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            node(1, 1, 6, outputs={"Next": [2]}),
            node(2, 1, 2, data={"Lines": l([o({"Text": s("根据分数分支。")})])}, outputs={"Actions": [3], "Next": [4]}),
            node(3, 2, 25, data={"VariableID": s("score")}),
            node(4, 1, 7, outputs={"Condition": [5], "TrueNext": [6], "FalseNext": [6]}),
            node(5, 3, 1, data={"AttributeID": s("rank"), "Operator": e("GreaterOrEqual"), "CompareValue": s("A")}),
            node(6, 1, 11),
        ],
    }

    result = VNGraphValidator().validate_detailed(graph)

    assert any("Data['VariableID']" in error and "Data['VariableId']" in error for error in result.errors)
    assert any("Data['AttributeID']" in error and "Data['AttributeId']" in error for error in result.errors)


def test_vn_graph_validator_reports_common_progress_action_subtype_confusion():
    graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            node(1, 1, 6, outputs={"Next": [2]}),
            node(
                2,
                1,
                3,
                data={
                    "BackgroundImage": s("res://backgrounds/classroom.png"),
                    "ChangeType": e("FadeIn"),
                    "Duration": f(0.5),
                },
            ),
        ],
    }

    result = VNGraphValidator().validate_detailed(graph)

    assert any("NodeType=1/SubType=3 是章节跳转节点" in error for error in result.errors)
    assert any("NodeType=2/SubType=3" in error for error in result.errors)


def test_vn_graph_validator_requires_exactly_one_start_node():
    graph = valid_graph()
    graph["Nodes"].append(node(9, 1, 6))

    result = VNGraphValidator().validate_detailed(graph)

    assert any("节点图只能包含一个开始节点" in error for error in result.errors)


def test_vn_graph_validator_rejects_incoming_connection_to_start_node():
    graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            node(1, 1, 6, outputs={"Next": [2]}),
            node(2, 1, 1, data={"TextData": s("回环")}, outputs={"Next": [1]}),
        ],
    }

    result = VNGraphValidator().validate_detailed(graph)

    assert any("没有输入端口" in error for error in result.errors)
