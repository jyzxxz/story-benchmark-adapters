"""
测试脚本 - 验证系统核心功能
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.utils.vn_graph_validator import vn_graph_validator


def test_vn_graph_validator():
    """测试 VNGraph 校验器"""
    print("\n" + "=" * 60)
    print("测试 VNGraph 校验器")
    print("=" * 60)

    # 测试合法的 VNGraph
    valid_graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            {
                "Index": 1,
                "DisplayName": "开始",
                "Comment": "",
                "NodeType": 1,
                "SubType": 6,
                "X": 80,
                "Y": 120,
                "Data": {},
                "Outputs": {"Next": [2]}
            },
            {
                "Index": 2,
                "DisplayName": "对白",
                "Comment": "",
                "NodeType": 1,
                "SubType": 1,
                "X": 280,
                "Y": 120,
                "Data": {
                    "SpeakerIdData": 1,
                    "TextData": "你好",
                    "VoiceIdData": 0
                },
                "Outputs": {"Next": []}
            }
        ]
    }

    is_valid, errors = vn_graph_validator.validate(valid_graph)
    print(f"\n合法 VNGraph 校验结果: {'✅ 通过' if is_valid else '❌ 失败'}")
    if errors:
        for error in errors:
            print(f"  - {error}")

    # 测试非法的 VNGraph（Outputs 不是数组）
    invalid_graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            {
                "Index": 1,
                "DisplayName": "开始",
                "Comment": "",
                "NodeType": 1,
                "SubType": 6,
                "X": 80,
                "Y": 120,
                "Data": {},
                "Outputs": {"Next": 2}  # 错误：应该是数组
            }
        ]
    }

    is_valid, errors = vn_graph_validator.validate(invalid_graph)
    print(f"\n非法 VNGraph 校验结果: {'❌ 通过（不应该）' if is_valid else '✅ 正确识别错误'}")
    if errors:
        print("错误信息:")
        for error in errors:
            print(f"  - {error}")

    # 测试 ChoiceNode
    choice_graph = {
        "Version": 1,
        "StartNodeIndex": 1,
        "Nodes": [
            {
                "Index": 1,
                "DisplayName": "选择",
                "Comment": "",
                "NodeType": 1,
                "SubType": 5,
                "X": 80,
                "Y": 120,
                "Data": {
                    "Options": [
                        {"Text": "选项A", "Next": 0},
                        {"Text": "选项B", "Next": 0}
                    ]
                },
                "Outputs": {
                    "Options[0].Next": [],
                    "Options[1].Next": []
                }
            }
        ]
    }

    is_valid, errors = vn_graph_validator.validate(choice_graph)
    print(f"\nChoiceNode 校验结果: {'✅ 通过' if is_valid else '❌ 失败'}")
    if errors:
        for error in errors:
            print(f"  - {error}")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("AI 沉浸式小说续写与角色可视化 Agent 系统 - 测试")
    print("=" * 60)

    # 测试 VNGraph 校验器
    test_vn_graph_validator()

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
