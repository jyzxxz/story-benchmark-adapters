"""Validation helpers for immutable VNGraph revisions derived from asset actions."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from app.utils.vn_graph_validator import VNGraphValidator


class VNGraphDerivationError(ValueError):
    pass


_PATCHABLE_ASSET_FIELDS = frozenset(
    {"BackgroundImage", "TachiIamge", "IllustrationImage"}
)
_PATCH_FIELD_NODE_TYPES = {
    "BackgroundImage": frozenset({(2, 3), (1, 14)}),
    "TachiIamge": frozenset({(2, 1), (2, 26)}),
    "IllustrationImage": frozenset({(2, 6)}),
}
_RESOURCE_FIELDS = frozenset(
    {
        *_PATCHABLE_ASSET_FIELDS,
        "AudioPath",
        "AudioUrl",
        "audio_url",
        "media_url",
        "resolved_media_url",
    }
)


def _serialized_string(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, Mapping) or value.get("Kind") != "String":
        return None
    string_value = value.get("StringValue")
    if not isinstance(string_value, str):
        return None
    return string_value.strip() or None


def _node_key_values(node: Mapping[str, Any]) -> set[str]:
    result = {
        str(value)
        for value in (
            node.get("node_key"),
            node.get("scene_id"),
            node.get("paragraph_id"),
        )
        if value is not None and str(value)
    }
    data = node.get("Data")
    if isinstance(data, Mapping):
        for field in ("SceneId", "ParagraphId"):
            value = _serialized_string(data.get(field))
            if value:
                result.add(value)
    return result


def _patch_node(graph: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    nodes = graph.get("Nodes")
    if not isinstance(nodes, list):
        raise VNGraphDerivationError("VNGraph patch base has no Nodes array")

    node_index = item.get("node_index")
    node_key = item.get("node_key")
    matches: list[dict[str, Any]] = []
    if isinstance(node_index, int) and not isinstance(node_index, bool):
        matches = [
            node
            for node in nodes
            if isinstance(node, dict) and node.get("Index") == node_index
        ]
    elif isinstance(node_key, str) and node_key:
        matches = [
            node
            for node in nodes
            if isinstance(node, dict) and node_key in _node_key_values(node)
        ]
    else:
        raise VNGraphDerivationError(
            "VNGraph patch requires a node_index or resolvable node_key"
        )
    if len(matches) != 1:
        raise VNGraphDerivationError("VNGraph patch target does not resolve uniquely")
    if isinstance(node_key, str) and node_key not in _node_key_values(matches[0]):
        raise VNGraphDerivationError("VNGraph patch node_index and node_key disagree")
    return matches[0]


def replay_asset_action_patch(
    base_graph: Mapping[str, Any],
    patch: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply the deliberately small AssetAction patch language server-side."""

    if not isinstance(base_graph, Mapping):
        raise VNGraphDerivationError("VNGraph patch base must be an object")
    result = deepcopy(dict(base_graph))
    items = list(patch)
    if not items:
        raise VNGraphDerivationError("VNGraph asset patch must not be empty")
    patched_fields: set[tuple[int, str]] = set()
    for item in items:
        if not isinstance(item, Mapping) or item.get("op") != "set_node_data":
            raise VNGraphDerivationError("Unsupported VNGraph asset patch operation")
        path = item.get("path")
        if (
            not isinstance(path, list)
            or len(path) != 1
            or path[0] not in _PATCHABLE_ASSET_FIELDS
        ):
            raise VNGraphDerivationError("VNGraph asset patch path is not allowed")
        value = item.get("value")
        if _serialized_string(value) is None or not isinstance(value, Mapping):
            raise VNGraphDerivationError(
                "VNGraph asset patch value must be a serialized String"
            )
        node = _patch_node(result, item)
        node_type = (node.get("NodeType"), node.get("SubType"))
        if node_type not in _PATCH_FIELD_NODE_TYPES[path[0]]:
            raise VNGraphDerivationError(
                "VNGraph asset patch field does not match the target node type"
            )
        data = node.get("Data")
        if not isinstance(data, dict):
            raise VNGraphDerivationError("VNGraph patch target Data must be an object")
        resolved_index = node.get("Index")
        if not isinstance(resolved_index, int) or isinstance(resolved_index, bool):
            raise VNGraphDerivationError("VNGraph patch target Index must be an integer")
        target_key = (resolved_index, path[0])
        if target_key in patched_fields:
            raise VNGraphDerivationError(
                "VNGraph asset patch cannot overwrite the same node field twice"
            )
        patched_fields.add(target_key)
        data[path[0]] = deepcopy(dict(value))
    return result


def validate_vn_graph_schema(graph: Mapping[str, Any]) -> None:
    result = VNGraphValidator().validate_detailed(dict(graph))
    if result.errors:
        summary = "; ".join(result.errors[:5])
        raise VNGraphDerivationError(f"VNGraph schema validation failed: {summary}")


def _resource_references(value: Any, *, field_name: str | None = None) -> set[str]:
    result: set[str] = set()
    if field_name in _RESOURCE_FIELDS:
        reference = _serialized_string(value)
        if reference:
            result.add(reference)
        return result
    if isinstance(value, Mapping):
        for key, item in value.items():
            result.update(_resource_references(item, field_name=str(key)))
    elif isinstance(value, (list, tuple)):
        for item in value:
            result.update(_resource_references(item))
    return result


def _binding_references(item: Mapping[str, Any]) -> set[str]:
    references = {
        str(value)
        for value in (item.get("media_url"), item.get("legacy_url"))
        if isinstance(value, str) and value.strip()
    }
    storage_id = item.get("storage_object_id")
    if isinstance(storage_id, str) and storage_id:
        references.add(f"/api/media/{storage_id}")
    return references


def validate_graph_resource_closure(
    graph: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    """Ensure every graph resource is frozen and this derivation's assets are used."""

    assets = manifest.get("asset_bindings")
    voices = manifest.get("voice_line_versions")
    if not isinstance(assets, list) or not isinstance(voices, list):
        raise VNGraphDerivationError("VNGraph resource manifest is incomplete")

    allowed: set[str] = set()
    current_action_groups: list[tuple[int, set[str]]] = []
    derivation = manifest.get("derivation")
    action_id = derivation.get("asset_action_id") if isinstance(derivation, Mapping) else None
    action_prefix = f"asset-action:{action_id}:" if action_id else None
    for item in assets:
        if not isinstance(item, Mapping):
            raise VNGraphDerivationError("VNGraph asset binding is invalid")
        references = _binding_references(item)
        allowed.update(references)
        slot_id = item.get("resource_slot_id")
        if action_prefix and isinstance(slot_id, str) and slot_id.startswith(action_prefix):
            if not references:
                raise VNGraphDerivationError(
                    "Derived VNGraph asset binding has no frozen graph reference"
                )
            order_index = item.get("order_index")
            if not isinstance(order_index, int) or isinstance(order_index, bool):
                raise VNGraphDerivationError(
                    "Derived VNGraph asset binding order is invalid"
                )
            current_action_groups.append((order_index, references))

    for item in voices:
        if not isinstance(item, Mapping):
            raise VNGraphDerivationError("VNGraph voice binding is invalid")
        audio = item.get("audio_asset_version")
        if isinstance(audio, Mapping):
            allowed.update(_binding_references(audio))
        audio_url = item.get("audio_url")
        if isinstance(audio_url, str) and audio_url.strip():
            allowed.add(audio_url)

    used = _resource_references(graph)
    unknown = sorted(used - allowed)
    if unknown:
        raise VNGraphDerivationError(
            "VNGraph references resources outside its frozen manifest: "
            + ", ".join(unknown[:5])
        )
    if any(not (references & used) for _order, references in current_action_groups):
        raise VNGraphDerivationError(
            "Derived VNGraph does not reference every AssetAction result"
        )
    patch = derivation.get("patch") if isinstance(derivation, Mapping) else None
    if not isinstance(patch, list) or len(patch) != len(current_action_groups):
        raise VNGraphDerivationError(
            "Derived VNGraph patch and AssetAction results do not match"
        )
    sorted_groups = sorted(current_action_groups, key=lambda item: item[0])
    if [order for order, _references in sorted_groups] != list(range(len(patch))):
        raise VNGraphDerivationError(
            "Derived VNGraph asset binding order is incomplete"
        )
    ordered_groups = [references for _order, references in sorted_groups]
    for patch_item, references in zip(patch, ordered_groups):
        patch_reference = (
            _serialized_string(patch_item.get("value"))
            if isinstance(patch_item, Mapping)
            else None
        )
        if patch_reference not in references:
            raise VNGraphDerivationError(
                "Derived VNGraph patch does not use its matching frozen asset"
            )


def validate_derived_vn_graph(
    *,
    graph: Mapping[str, Any],
    manifest: Mapping[str, Any],
    parent_graph: Mapping[str, Any],
    patch: Iterable[Mapping[str, Any]],
) -> None:
    replayed = replay_asset_action_patch(parent_graph, patch)
    if replayed != graph:
        raise VNGraphDerivationError(
            "Derived VNGraph does not equal the server-replayed patch result"
        )
    validate_vn_graph_schema(graph)
    validate_graph_resource_closure(graph, manifest)


__all__ = [
    "VNGraphDerivationError",
    "replay_asset_action_patch",
    "validate_derived_vn_graph",
    "validate_graph_resource_closure",
    "validate_vn_graph_schema",
]
