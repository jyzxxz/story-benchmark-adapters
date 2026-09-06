"""
章节/项目生成流水线事件名。

这些值会经由 agent 的 EEvent_tool_progress.stage 推给前端。这里集中定义，
前端对接时只需要看这个文件，不需要在各个 service 里搜索字符串。
"""

EEvent_bible_start = "EEvent_bible_start"
EEvent_bible_done = "EEvent_bible_done"

EEvent_outline_start = "EEvent_outline_start"
EEvent_outline_done = "EEvent_outline_done"
EEvent_outline_revise = "EEvent_outline_revise"
EEvent_outline_revised = "EEvent_outline_revised"
EEvent_outline_approved = "EEvent_outline_approved"
EEvent_single_outline_start = "EEvent_single_outline_start"
EEvent_single_outline_done = "EEvent_single_outline_done"
EEvent_single_outline_approved = "EEvent_single_outline_approved"

EEvent_content_skip = "EEvent_content_skip"
EEvent_content_start = "EEvent_content_start"
EEvent_content_done = "EEvent_content_done"
EEvent_batch_start = "EEvent_batch_start"
EEvent_batch_done = "EEvent_batch_done"

EEvent_assets_start = "EEvent_assets_start"
EEvent_assets_done = "EEvent_assets_done"

EEvent_resources_start = "EEvent_resources_start"
EEvent_resources_done = "EEvent_resources_done"
EEvent_resource_start = "EEvent_resource_start"
EEvent_resource_done = "EEvent_resource_done"

EEvent_graph_start = "EEvent_graph_start"
EEvent_graph_done = "EEvent_graph_done"
EEvent_assemble_start = "EEvent_assemble_start"
EEvent_assemble_done = "EEvent_assemble_done"

EEvent_workflow_start = "EEvent_workflow_start"
EEvent_workflow_done = "EEvent_workflow_done"
EEvent_workspace_done = "EEvent_workspace_done"
