"""if_line ServerAgent 评测(evals)。

分层:
- types.py    纯数据类型, 不 import app(环境配置前可安全导入)
- seeders.py  造"已知状态"的项目/用户, 让指令可回答
- replay.py   cassette 录制 + 回放(只换 LLM, 工具仍真实执行)
- checkers.py 确定性 checker(工具序列 / 参数 / DB 状态 / 答案)
- judge.py    LLM-as-judge rubric(大纲 / 正文 / 反幻觉)
- mocks.py    mock 工具内部的 LLM 调用(replay 用)
- runner.py   驱动 ServerAgent 跑 task, 采集 tool_trace + 跑断言 + judge
- tasks.py    任务集定义(query / planning / constraint / generate)
- run_eval.py CLI 入口

用法(在 backend 目录下):
  python -m evals.run_eval --list
  python -m evals.run_eval --replay        # 全量回放, 自动按 task id 找 cassette
  python -m evals.run_eval --live          # 全量, 真实 LLM + judge(需 key)
  python -m evals.run_eval --task query.view_project --live --record
"""
