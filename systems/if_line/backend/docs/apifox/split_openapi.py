#!/usr/bin/env python3
"""拆分/组装 Apifox OpenAPI 文档。

目标是让人工 review 看小文件，而不是每次都看一个巨大的 JSON diff。

目录约定：
- source/base.json：除 paths 和 components.schemas 之外的根文档。
- source/paths/*.json：每个接口一个文件，文件名用于搜索，真实 path/method 以文件内容为准。
- source/components/schemas/*.json：每个数据模型一个文件。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import OrderedDict
from pathlib import Path
from typing import Any


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(value, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def _path_filename(method: str, path: str) -> str:
    raw_path = path.strip("/") or "root"
    safe = raw_path.replace("/", "_")
    safe = re.sub(r"[^A-Za-z0-9_.{}-]+", "_", safe)
    return f"{method.lower()}__{safe}.json"


def _schema_filename(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.{}-]+", "_", name)
    return f"{safe}.json"


def split_openapi(input_path: Path, source_dir: Path, *, force: bool) -> None:
    spec = _read_json(input_path)
    if source_dir.exists():
        if not force:
            raise SystemExit(f"{source_dir} 已存在；如需覆盖请加 --force")
        shutil.rmtree(source_dir)

    order: dict[str, Any] = {
        "root_keys": list(spec.keys()),
        "component_keys": list(spec.get("components", {}).keys()),
        "paths": [],
        "schemas": list(spec.get("components", {}).get("schemas", {}).keys()),
    }

    base: dict[str, Any] = {}
    for key, value in spec.items():
        if key == "paths":
            continue
        if key == "components":
            components = dict(value)
            components.pop("schemas", None)
            base[key] = components
            continue
        base[key] = value
    _write_json(source_dir / "base.json", base)
    _write_json(source_dir / "_order.json", order)

    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS:
                continue
            order["paths"].append({"method": method.lower(), "path": path})
            item = {
                "method": method.lower(),
                "path": path,
                "operation": operation,
            }
            _write_json(source_dir / "paths" / _path_filename(method, path), item)
    _write_json(source_dir / "_order.json", order)

    schemas = spec.get("components", {}).get("schemas", {})
    for name, schema in schemas.items():
        _write_json(
            source_dir / "components" / "schemas" / _schema_filename(name),
            {
                "name": name,
                "schema": schema,
            },
        )


def build_openapi(source_dir: Path, output_path: Path) -> None:
    base = _read_json(source_dir / "base.json")
    order_path = source_dir / "_order.json"
    order = _read_json(order_path) if order_path.exists() else {}

    operation_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for path_file in sorted((source_dir / "paths").glob("*.json")):
        item = _read_json(path_file)
        method = item["method"].lower()
        path = item["path"]
        operation_by_key[(method, path)] = item["operation"]

    ordered_ops = [
        (item["method"].lower(), item["path"])
        for item in order.get("paths", [])
        if (item["method"].lower(), item["path"]) in operation_by_key
    ]
    known = set(ordered_ops)
    for key in sorted(operation_by_key):
        if key not in known:
            ordered_ops.append(key)

    paths: dict[str, dict[str, Any]] = {}
    for method, path in ordered_ops:
        paths.setdefault(path, {})[method] = operation_by_key[(method, path)]

    schema_by_name: dict[str, Any] = {}
    schema_dir = source_dir / "components" / "schemas"
    if schema_dir.exists():
        for schema_file in sorted(schema_dir.glob("*.json")):
            item = _read_json(schema_file)
            schema_by_name[item["name"]] = item["schema"]

    schema_names = [
        name for name in order.get("schemas", []) if name in schema_by_name
    ]
    known_schemas = set(schema_names)
    for name in sorted(schema_by_name):
        if name not in known_schemas:
            schema_names.append(name)
    schemas = {name: schema_by_name[name] for name in schema_names}

    components_base = base.get("components", {})
    components: dict[str, Any] = {}
    component_keys = order.get("component_keys") or list(components_base.keys())
    for key in component_keys:
        if key == "schemas":
            if schemas:
                components["schemas"] = schemas
        elif key in components_base:
            components[key] = components_base[key]
    for key, value in components_base.items():
        if key not in components:
            components[key] = value
    if schemas and "schemas" not in components:
        components["schemas"] = schemas

    spec: dict[str, Any] = {}
    root_keys = order.get("root_keys") or list(base.keys()) + ["paths"]
    for key in root_keys:
        if key == "paths":
            spec["paths"] = paths
        elif key == "components":
            spec["components"] = components
        elif key in base:
            spec[key] = base[key]
    for key, value in base.items():
        if key not in spec and key != "components":
            spec[key] = value
    if "paths" not in spec:
        spec["paths"] = paths
    if components and "components" not in spec:
        spec["components"] = components

    _write_json(output_path, spec)


def write_catalog(source_dir: Path, output_path: Path) -> None:
    """按当前 operation 分类生成一个便于人工阅读的接口大纲。"""

    base = _read_json(source_dir / "base.json")
    order_path = source_dir / "_order.json"
    order = _read_json(order_path) if order_path.exists() else {}
    operation_order = {
        (item["method"].upper(), item["path"]): index
        for index, item in enumerate(order.get("paths", []))
    }

    categories: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for path_file in sorted((source_dir / "paths").glob("*.json")):
        item = _read_json(path_file)
        operation = item["operation"]
        folder = (
            operation.get("x-apifox-folder")
            or (operation.get("tags") or ["未分类"])[0]
        )
        categories.setdefault(folder, [])
        categories[folder].append(
            {
                "method": item["method"].upper(),
                "path": item["path"],
                "summary": operation.get("summary", ""),
                "file": str(path_file.relative_to(source_dir)),
            }
        )

    for operations in categories.values():
        operations.sort(
            key=lambda value: operation_order.get(
                (value["method"], value["path"]),
                10**9,
            )
        )

    tag_order = {
        tag["name"]: index
        for index, tag in enumerate(base.get("tags", []))
        if "name" in tag
    }
    category_items = [
        {"name": name, "operations": operations}
        for name, operations in categories.items()
    ]
    category_items.sort(key=lambda item: tag_order.get(item["name"], 10**9))

    _write_json(
        output_path,
        {
            "description": (
                "Apifox 接口目录大纲。用于人工查看分类和快速跳转；"
                "真实接口定义在 operations[].file 指向的小文件里。"
            ),
            "categories": category_items,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="拆分/组装 Apifox OpenAPI 文档")
    subparsers = parser.add_subparsers(dest="command", required=True)

    split_parser = subparsers.add_parser("split", help="把单个 OpenAPI JSON 拆成小文件")
    split_parser.add_argument("--input", type=Path, required=True)
    split_parser.add_argument("--source", type=Path, required=True)
    split_parser.add_argument("--force", action="store_true")

    build_parser = subparsers.add_parser("build", help="把拆分目录组装成 OpenAPI JSON")
    build_parser.add_argument("--source", type=Path, required=True)
    build_parser.add_argument("--output", type=Path, required=True)

    catalog_parser = subparsers.add_parser("catalog", help="生成接口目录大纲")
    catalog_parser.add_argument("--source", type=Path, required=True)
    catalog_parser.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "split":
        split_openapi(args.input, args.source, force=args.force)
    elif args.command == "build":
        build_openapi(args.source, args.output)
    elif args.command == "catalog":
        write_catalog(args.source, args.output)


if __name__ == "__main__":
    main()
