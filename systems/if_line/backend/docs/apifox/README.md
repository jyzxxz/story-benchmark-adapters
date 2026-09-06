# Apifox 文档拆分维护

主文件：

- `ifline_product.openapi.json`：Apifox 导入用的完整 OpenAPI 文件。

拆分源文件：

- `source/base.json`：OpenAPI 根信息，以及除 `components.schemas` 之外的公共配置。
- `source/_order.json`：原始顺序清单，用于组装时避免大 JSON 因排序变化产生无意义 diff。
- `source/catalog.json`：接口目录大纲，用于人工查看每个分类下有哪些 API，并跳转到对应小文件。
- `source/paths/*.json`：每个接口一个文件。
- `source/components/schemas/*.json`：每个数据模型一个文件。

分类说明：

- `operation.x-apifox-folder`：Apifox 导入后左侧目录树使用的分类。
- `operation.tags`：OpenAPI 通用标签。当前会保留中文分类标签，方便非 Apifox 工具阅读。
- `source/catalog.json`：人工维护视角的总目录，不直接参与 OpenAPI 标准，但用于 review 和快速搜索。

接口文件命名规则：

```text
{method}__{path 去掉开头 / 后把 / 换成 _}.json
```

例如：

```text
GET /api/story-paths/{story_path_id}/chapters
=> source/paths/get__api_story-paths_{story_path_id}_chapters.json
```

文件名只是为了搜索和 review，真实接口以文件内容里的 `method` 和 `path` 为准。

常用命令：

```bash
# 从当前完整 JSON 拆分到 source/
backend/venv/bin/python backend/docs/apifox/split_openapi.py split \
  --input backend/docs/apifox/ifline_product.openapi.json \
  --source backend/docs/apifox/source \
  --force

# 从 source/ 组装出完整 JSON
backend/venv/bin/python backend/docs/apifox/split_openapi.py build \
  --source backend/docs/apifox/source \
  --output backend/docs/apifox/ifline_product.openapi.json

# 重新生成接口目录大纲
backend/venv/bin/python backend/docs/apifox/split_openapi.py catalog \
  --source backend/docs/apifox/source \
  --output backend/docs/apifox/source/catalog.json

# 先组装到临时文件，再和主文件比较
backend/venv/bin/python backend/docs/apifox/split_openapi.py build \
  --source backend/docs/apifox/source \
  --output /tmp/ifline_product.rebuilt.openapi.json
cmp backend/docs/apifox/ifline_product.openapi.json /tmp/ifline_product.rebuilt.openapi.json

# 覆盖式同步到 Apifox
# 说明：apifox import 对已有接口/模型可能只返回 ignore，不会覆盖更新。
# 这个脚本会用 schema update / endpoint update 按资源逐项覆盖。
backend/venv/bin/python backend/docs/apifox/sync_apifox_overwrite.py
```
