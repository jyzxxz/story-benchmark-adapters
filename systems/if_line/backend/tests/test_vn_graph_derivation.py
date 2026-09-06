from __future__ import annotations

import pytest

from app.application.vn_graph_derivation import (
    VNGraphDerivationError,
    replay_asset_action_patch,
    validate_derived_vn_graph,
)


def _graph(background: str, *, include_version: bool = True) -> dict:
    graph = {
        "StartNodeIndex": 0,
        "Nodes": [
            {
                "Index": 0,
                "DisplayName": "Start",
                "NodeType": 1,
                "SubType": 6,
                "X": 0,
                "Y": 0,
                "Data": {},
                "Outputs": {},
            },
            {
                "Index": 3,
                "DisplayName": "Background",
                "NodeType": 2,
                "SubType": 3,
                "X": 100,
                "Y": 0,
                "Data": {
                    "BackgroundImage": {
                        "Kind": "String",
                        "StringValue": background,
                    },
                    "SceneId": {
                        "Kind": "String",
                        "StringValue": "station",
                    },
                },
                "Outputs": {},
            },
        ],
    }
    if include_version:
        graph["Version"] = 1
    return graph


def _patch(media_url: str) -> list[dict]:
    return [
        {
            "op": "set_node_data",
            "path": ["BackgroundImage"],
            "value": {"Kind": "String", "StringValue": media_url},
            "node_index": 3,
            "node_key": "station",
        }
    ]


def _manifest(media_url: str, patch: list[dict]) -> dict:
    return {
        "asset_bindings": [
            {
                "resource_slot_id": "asset-action:action-1:0",
                "order_index": 0,
                "storage_object_id": "storage-1",
                "media_url": media_url,
            }
        ],
        "voice_line_versions": [],
        "derivation": {
            "asset_action_id": "action-1",
            "patch": patch,
        },
    }


def test_derived_graph_validation_rejects_resource_outside_frozen_manifest():
    base = _graph("")
    patch = _patch("/api/media/not-frozen")
    derived = replay_asset_action_patch(base, patch)

    with pytest.raises(VNGraphDerivationError, match="outside its frozen manifest"):
        validate_derived_vn_graph(
            graph=derived,
            manifest=_manifest("/api/media/storage-1", patch),
            parent_graph=base,
            patch=patch,
        )


def test_derived_graph_validation_rejects_schema_invalid_replay_result():
    base = _graph("", include_version=False)
    patch = _patch("/api/media/storage-1")
    derived = replay_asset_action_patch(base, patch)

    with pytest.raises(VNGraphDerivationError, match="schema validation failed"):
        validate_derived_vn_graph(
            graph=derived,
            manifest=_manifest("/api/media/storage-1", patch),
            parent_graph=base,
            patch=patch,
        )


def test_asset_patch_rejects_duplicate_node_field_overwrite():
    patch = [*_patch("/api/media/storage-1"), *_patch("/api/media/storage-2")]

    with pytest.raises(VNGraphDerivationError, match="overwrite"):
        replay_asset_action_patch(_graph(""), patch)


def test_asset_patch_rejects_field_on_wrong_node_type():
    patch = _patch("/api/media/storage-1")
    patch[0]["node_index"] = 0
    patch[0].pop("node_key")

    with pytest.raises(VNGraphDerivationError, match="target node type"):
        replay_asset_action_patch(_graph(""), patch)
