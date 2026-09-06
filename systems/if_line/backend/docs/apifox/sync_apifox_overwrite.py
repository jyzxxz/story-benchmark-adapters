#!/usr/bin/env python3
"""覆盖式同步 Apifox 项目。

`apifox import --format openapi` 对已有接口/模型会出现 ignore，本脚本改用
`schema update` 和 `endpoint update` 逐项覆盖。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def _write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as fp:
        json.dump(value, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def _run_json(args: list[str], token: str) -> Any:
    command = [*args, "--access-token", token]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        safe_command = ["<TOKEN>" if part == token else part for part in command]
        raise SystemExit(
            "命令执行失败："
            + " ".join(safe_command)
            + f"\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return json.loads(result.stdout)


def _run(args: list[str], token: str) -> None:
    command = [*args, "--access-token", token]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        safe_command = ["<TOKEN>" if part == token else part for part in command]
        raise SystemExit(
            "命令执行失败："
            + " ".join(safe_command)
            + f"\nstdout={result.stdout}\nstderr={result.stderr}"
        )


def _export_remote_openapi(project: str, branch: str, output: Path, token: str) -> None:
    _run(
        [
            "apifox",
            "export",
            "--project",
            project,
            "--format",
            "openapi",
            "--oas-version",
            "3.0",
            "--include-extension-properties",
            "--add-folders-to-tags",
            "--branch",
            branch,
            "--output",
            str(output),
        ],
        token,
    )


def _operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() in HTTP_METHODS:
                result[(method.upper(), path)] = operation
    return result


def _collect_pages(base_args: list[str], token: str) -> list[dict[str, Any]]:
    page = 1
    items: list[dict[str, Any]] = []
    while True:
        payload = _run_json(
            [*base_args, "--page", str(page), "--page-size", "500"],
            token,
        )
        items.extend(payload.get("data") or [])
        meta = payload.get("meta") or {}
        if not meta.get("nextPage"):
            break
        page = int(meta["nextPage"])
    return items


def _collect_all(base_args: list[str], token: str) -> list[dict[str, Any]]:
    payload = _run_json(base_args, token)
    return payload.get("data") or []


def _schema_title(schema: dict[str, Any], name: str) -> str:
    title = schema.get("title")
    return title if isinstance(title, str) and title else name


def _schema_description(schema: dict[str, Any]) -> str:
    description = schema.get("description")
    return description if isinstance(description, str) else ""


def _convert_refs(value: Any, schema_id_by_name: dict[str, int]) -> Any:
    if isinstance(value, list):
        return [_convert_refs(item, schema_id_by_name) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "$ref" and isinstance(item, str):
            prefix = "#/components/schemas/"
            if item.startswith(prefix):
                name = item[len(prefix) :]
                schema_id = schema_id_by_name.get(name)
                if schema_id is not None:
                    result[key] = f"#/definitions/{schema_id}"
                    continue
        result[key] = _convert_refs(item, schema_id_by_name)
    return result


def _parameter_type(schema: dict[str, Any]) -> str:
    value = schema.get("type")
    if isinstance(value, str):
        if value in {"number", "string", "integer", "array", "boolean"}:
            return value
    if "items" in schema:
        return "array"
    return "string"


def _parameter(parameter: dict[str, Any], index: int, schema_id_by_name: dict[str, int]) -> dict[str, Any]:
    schema = _convert_refs(parameter.get("schema") or {}, schema_id_by_name)
    return {
        "id": f"{parameter.get('name', 'param')}#{index}",
        "name": parameter.get("name", ""),
        "required": bool(parameter.get("required")),
        "enable": True,
        "description": parameter.get("description") or "",
        "example": parameter.get("example") if parameter.get("example") is not None else "",
        "type": _parameter_type(schema),
        "schema": schema,
    }


def _parameters(operation: dict[str, Any], schema_id_by_name: dict[str, int]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {
        "path": [],
        "query": [],
        "header": [],
        "cookie": [],
    }
    counters = {key: 0 for key in grouped}
    for raw in operation.get("parameters") or []:
        location = raw.get("in")
        if location not in grouped:
            continue
        grouped[location].append(_parameter(raw, counters[location], schema_id_by_name))
        counters[location] += 1
    return grouped


def _media_type_to_apifox(media_type: str) -> str:
    known = {
        "application/json",
        "application/xml",
        "multipart/form-data",
        "application/x-www-form-urlencoded",
        "text/plain",
        "application/octet-stream",
    }
    return media_type if media_type in known else "application/json"


def _request_body(operation: dict[str, Any], schema_id_by_name: dict[str, int]) -> dict[str, Any]:
    body = operation.get("requestBody")
    if not body:
        return {"type": "none"}
    content = body.get("content") or {}
    if not content:
        return {"type": "none", "required": bool(body.get("required"))}
    media_type, media = next(iter(content.items()))
    schema = _convert_refs(media.get("schema") or {}, schema_id_by_name)
    result: dict[str, Any] = {
        "type": _media_type_to_apifox(media_type),
        "mediaType": media_type,
        "required": bool(body.get("required")),
        "parameters": [],
        "jsonSchema": schema,
    }
    example = media.get("example")
    if example is not None:
        result["examples"] = [
            {
                "value": json.dumps(example, ensure_ascii=False, indent=2),
                "mediaType": media_type,
                "description": "",
            }
        ]
    return result


def _responses(operation: dict[str, Any], schema_id_by_name: dict[str, int]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, (code, response) in enumerate((operation.get("responses") or {}).items()):
        content = response.get("content") or {}
        media_type = "application/json"
        media: dict[str, Any] = {}
        if content:
            media_type, media = next(iter(content.items()))
        schema = _convert_refs(media.get("schema") or {}, schema_id_by_name)
        result.append(
            {
                "id": f"{code}#{index}",
                "name": response.get("x-apifox-name") or ("成功" if str(code).startswith("2") else "响应"),
                "code": str(code),
                "contentType": "json" if media_type == "application/json" else "raw",
                "jsonSchema": schema,
                "itemSchema": {},
                "description": response.get("description") or "",
                "mediaType": media_type,
                "headers": [],
                "oasExtensions": json.dumps(
                    {
                        key: value
                        for key, value in response.items()
                        if key.startswith("x-")
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            }
        )
    return result


def _response_examples(operation: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, (code, response) in enumerate((operation.get("responses") or {}).items()):
        content = response.get("content") or {}
        if not content:
            continue
        media_type, media = next(iter(content.items()))
        example = media.get("example")
        if example is None:
            continue
        result.append(
            {
                "name": response.get("x-apifox-name") or f"{code} 示例",
                "responseId": f"{code}#{index}",
                "description": "",
                "data": json.dumps(example, ensure_ascii=False, indent=2),
            }
        )
    return result


def _endpoint_payload(
    method: str,
    path: str,
    operation: dict[str, Any],
    schema_id_by_name: dict[str, int],
) -> dict[str, Any]:
    extensions = {
        key: value
        for key, value in operation.items()
        if key.startswith("x-") and key != "x-apifox-folder"
    }
    return {
        "name": operation.get("summary") or f"{method} {path}",
        "type": "http",
        "method": method.lower(),
        "path": path,
        "status": operation.get("x-apifox-status") or "released",
        "description": operation.get("description") or "",
        "operationId": operation.get("operationId") or "",
        "tags": operation.get("tags") or [],
        "parameters": _parameters(operation, schema_id_by_name),
        "requestBody": _request_body(operation, schema_id_by_name),
        "responses": _responses(operation, schema_id_by_name),
        "responseExamples": _response_examples(operation),
        "oasExtensions": json.dumps(extensions, ensure_ascii=False, indent=2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="覆盖式同步 Apifox 项目")
    parser.add_argument("--project", default="8663103")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--openapi", type=Path, default=Path("backend/docs/apifox/ifline_product.openapi.json"))
    parser.add_argument("--token-file", type=Path, default=Path("backend/docs/apifox/ifline_apifox_token"))
    parser.add_argument("--remote-openapi", type=Path, default=None, help="可选。指定已有远端导出文件；不传则脚本自动导出。")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = args.token_file.read_text(encoding="utf-8").strip()
    with tempfile.TemporaryDirectory(prefix="ifline-apifox-sync-") as tmp_dir_raw:
        tmp_dir = Path(tmp_dir_raw)
        remote_openapi = args.remote_openapi or (tmp_dir / "remote.openapi.json")
        if args.remote_openapi is None:
            _export_remote_openapi(args.project, args.branch, remote_openapi, token)

        local = _load_json(args.openapi)
        remote = _load_json(remote_openapi)

        schema_items = _collect_all(
            ["apifox", "schema", "list", "--project", args.project, "--branch", args.branch],
            token,
        )
        schema_id_by_name = {item["name"]: item["id"] for item in schema_items}
        endpoint_items = _collect_pages(
            ["apifox", "endpoint", "list", "--project", args.project, "--branch", args.branch],
            token,
        )
        endpoint_id_by_key = {
            (item["method"].upper(), item["path"]): item["id"]
            for item in endpoint_items
        }

        local_schemas = local.get("components", {}).get("schemas", {})
        remote_schemas = remote.get("components", {}).get("schemas", {})
        schema_updates: list[str] = []
        endpoint_updates: list[tuple[str, str]] = []

        schema_creates: list[str] = []
        for name, schema in local_schemas.items():
            schema_id = schema_id_by_name.get(name)
            if schema_id is None:
                payload = {
                    "name": name,
                    "displayName": _schema_title(schema, name),
                    "description": _schema_description(schema),
                    "jsonSchema": _convert_refs(schema, schema_id_by_name),
                }
                file_path = tmp_dir / f"schema-create-{name}.json"
                _write_json(file_path, payload)
                schema_creates.append(name)
                if not args.dry_run:
                    created = _run_json(
                        [
                            "apifox",
                            "schema",
                            "create",
                            "--project",
                            args.project,
                            "--branch",
                            args.branch,
                            "--file",
                            str(file_path),
                        ],
                        token,
                    )
                    new_id = (
                        created.get("data", {}).get("id")
                        or created.get("data", {}).get("schemaId")
                        or created.get("id")
                    )
                    if new_id is not None:
                        schema_id_by_name[name] = int(new_id)
                continue
            if remote_schemas.get(name) == schema:
                continue
            payload = {
                "name": name,
                "displayName": _schema_title(schema, name),
                "description": _schema_description(schema),
                "jsonSchema": _convert_refs(schema, schema_id_by_name),
            }
            file_path = tmp_dir / f"schema-{schema_id}.json"
            _write_json(file_path, payload)
            schema_updates.append(name)
            if not args.dry_run:
                _run(
                    [
                        "apifox",
                        "schema",
                        "update",
                        str(schema_id),
                        "--project",
                        args.project,
                        "--branch",
                        args.branch,
                        "--file",
                        str(file_path),
                    ],
                    token,
                )

        local_ops = _operations(local)
        remote_ops = _operations(remote)
        endpoint_creates: list[tuple[str, str]] = []
        for key, operation in local_ops.items():
            endpoint_id = endpoint_id_by_key.get(key)
            method, path = key
            if endpoint_id is None:
                payload = _endpoint_payload(method, path, operation, schema_id_by_name)
                file_path = tmp_dir / f"endpoint-create-{method.lower()}-{path.replace('/', '_')}.json"
                _write_json(file_path, payload)
                endpoint_creates.append(key)
                if not args.dry_run:
                    created = _run_json(
                        [
                            "apifox",
                            "endpoint",
                            "create",
                            "--project",
                            args.project,
                            "--branch",
                            args.branch,
                            "--file",
                            str(file_path),
                        ],
                        token,
                    )
                    new_id = (
                        created.get("data", {}).get("id")
                        or created.get("data", {}).get("apiId")
                        or created.get("id")
                    )
                    if new_id is not None:
                        endpoint_id_by_key[key] = int(new_id)
                continue
            if remote_ops.get(key) == operation:
                continue
            payload = _endpoint_payload(method, path, operation, schema_id_by_name)
            file_path = tmp_dir / f"endpoint-{endpoint_id}.json"
            _write_json(file_path, payload)
            endpoint_updates.append(key)
            if not args.dry_run:
                _run(
                    [
                        "apifox",
                        "endpoint",
                        "update",
                        str(endpoint_id),
                        "--project",
                        args.project,
                        "--branch",
                        args.branch,
                        "--file",
                        str(file_path),
                    ],
                    token,
                )

    print(
        json.dumps(
            {
                "dry_run": args.dry_run,
                "schema_update_count": len(schema_updates),
                "schema_create_count": len(schema_creates),
                "endpoint_update_count": len(endpoint_updates),
                "endpoint_create_count": len(endpoint_creates),
                "schema_creates": schema_creates,
                "endpoint_creates": [
                    {"method": method, "path": path} for method, path in endpoint_creates
                ],
                "schema_updates": schema_updates,
                "endpoint_updates": [
                    {"method": method, "path": path} for method, path in endpoint_updates
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
