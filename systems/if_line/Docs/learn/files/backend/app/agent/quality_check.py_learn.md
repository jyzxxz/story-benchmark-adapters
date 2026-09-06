# quality_check.py — learn note

> Source: `backend/app/agent/quality_check.py` (108 LOC)
> Route: `standard` | Reuse target: **P4 ai_flavor_check.py 与之同级，参考返回结构**
> Status: `[_]` → master-validated `[x]`

## 职责
章节正文一致性规则检查。**纯规则，不调 LLM**。

## ⭐ 返回结构（P4 直接复刻）
```python
@dataclass
class CheckResult:
    passed: bool
    reasons: List[str] = field(default_factory=list)
```

P4 `ai_flavor_check.py` 建议 dataclass：
```python
@dataclass
class AiFlavorReport:
    passed: bool               # score >= AI_FLAVOR_MIN_SCORE
    score: int                 # 0-100
    issues: List[str]          # 命中类型："套话:不禁"、"排比灌水"...
    rewrite_hint: str          # 给重写 prompt 的具体指令
```

## 现有 6 条检查（P4 不动，平行加）
1. content 空 → 失败
2. content < 300 字 → 失败
3. content < word_min × 70% → 失败
4. 标题不一致 → 失败
5. 章节未出现大纲角色名 → 失败
6. ending_hook 末尾未呼应 → 失败

P4 在这 6 条之外**平行加一层** LLM 评审（AI 味），不替换这 6 条。

## ⭐ auto_creator 接入点（L277-294）
```python
check = quality_check.check_chapter_content(candidate, chapter_outline, word_count_min)
if check.passed:
    content_output = candidate
else:
    # 重试
```

P4 改造：在 `check.passed` 之后再加一层：
```python
if check.passed:
    flavor = await ai_flavor_check.check(candidate.content, bible.style_rules)
    if not flavor.passed:
        # 把 flavor.rewrite_hint 注入下次 generate_chapter_content
        rewrite_hint = flavor.rewrite_hint
        # 共享 MAX_CHAPTER_FAILURES 配额，不叠加
    else:
        content_output = candidate
```

## worker 提示
- 不要把 AI 味检查合进 `quality_check.py`，保持职责单一。
- `_extract_keywords(text)` 用 `[一-龥]{2,4}` 正则抽中文 token，P4 可复用。
