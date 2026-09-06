"""
AutoCreator 可调参数。

未来想接入 asset / TTS 时,只要把对应开关从 False 改 True 即可,
对应 _generate_assets / _generate_tts 方法在 auto_creator.py 里实现。
"""

DEFAULT_WORD_COUNT_MIN = 1500
DEFAULT_WORD_COUNT_MAX = 3000

MAX_CHAPTER_FAILURES = 3
"""单章生成连续失败 N 次后放弃整篇,避免死循环烧 token。"""

MAX_LLM_RETRIES = 3
"""LLM 单次调用的最大尝试次数 (含首次)。3 = 首次 + 2 次重试。"""

LLM_RETRY_BASE_DELAY = 1.0
"""LLM 重试的指数退避基数 (秒)。第 i 次失败后 sleep base * 2^(i-1)。
3 次重试的延迟序列: 1s → 2s → 4s。"""

# 一致性检查 (quality_check) 参数 —— "中等严格度"
QUALITY_MIN_LENGTH_RATIO = 0.7
"""章节正文字数低于 word_min * 0.7 视为质量不合格。"""

QUALITY_MIN_LENGTH_ABSOLUTE = 300
"""章节正文绝对下限, 低于此值直接判废 (即便没设 word_min)。"""

QUALITY_ENDING_HOOK_SEARCH_WINDOW = 300
"""检查 ending_hook 是否呼应章节末尾时, 只看最后 N 个字。"""

AI_FLAVOR_MIN_SCORE = 70
"""AI 味评审最低通过分 (0-100)。章节正文 ai_flavor_check.score 低于此值视为不合格,触发重写。"""

AI_FLAVOR_MAX_RETRIES = 2
"""AI 味评审不通过时, 章节正文最多重写次数。超过则放弃重写、保留当前版本 (避免死循环)。"""

ENABLE_ASSETS = False
ENABLE_TTS = False
"""后期接入开关。当前默认全部关闭,主链路只跑到 ChapterContent。"""

BRAINSTORM_TEMPERATURE = 1.1
"""选题步骤鼓励发散,温度调高。"""
