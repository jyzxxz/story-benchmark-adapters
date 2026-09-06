# E11 — background-test-portrait-keyframe-unaffected

## 背景

新链路只动 background，不应影响 portrait 和 keyframe。哲学明确要求"不动 portrait / keyframe 链路"。

回归测试必须断言：重构前后 portrait / keyframe 的 prompt 输出**完全一致**。

## SOTA 实践

**Golden master testing / Characterization testing**（[`martinfowler.com/bliki/CharacterizationTesting.html`](https://martinfowler.com/bliki/CharacterizationTesting.html)）：把重构前的输出作为 golden，重构后必须保持一致。

**Snapshot testing (Jest snapshot pattern)**（[`jestjs.io/docs/snapshot-testing`](https://jestjs.io/docs/snapshot-testing)）：把输出存为 snapshot，diff 自动检测变化。

**Git diff regression tests**（[`git-scm.com/docs/git-diff`](https://git-scm.com/docs/git-diff)）：用 git 历史对比重构前后。

## 落地建议

`backend/tests/test_portrait_keyframe_regression.py`：

```python
import json
from pathlib import Path

PORTRAIT_GOLDEN_DIR = Path("tests/fixtures/portrait_golden_pre_refactor")
KEYFRAME_GOLDEN_DIR = Path("tests/fixtures/keyframe_golden_pre_refactor")


def load_golden_prompt(asset_type: str, case_id: str) -> str:
    """读取重构前的 golden prompt"""
    if asset_type == "portrait":
        path = PORTRAIT_GOLDEN_DIR / f"{case_id}.txt"
    else:
        path = KEYFRAME_GOLDEN_DIR / f"{case_id}.txt"
    return path.read_text()


@pytest.mark.parametrize("case_id", [
    "P01_historical_female_mourning",
    "P02_modern_male_detective",
    "P03_scifi_female_pilot",
    "P04_fantasy_male_mage",
    "P05_anime_female_student",
    # ... 10 个 portrait case
])
def test_portrait_prompt_unchanged(case_id, prompt_builder, image_gen):
    """portrait prompt 重构前后必须完全一致"""
    case = json.loads((PORTRAIT_GOLDEN_DIR / f"{case_id}.json").read_text())
    character = Character(**case["character"])

    prompts = prompt_builder.build_portrait_prompts(character, case["genre"])
    final_prompt = image_gen._build_portrait_prompt(prompts[0])

    golden = load_golden_prompt("portrait", case_id)
    assert final_prompt == golden, (
        f"portrait prompt changed for {case_id}!\n"
        f"Diff:\n{_unified_diff(golden, final_prompt)}"
    )


@pytest.mark.parametrize("case_id", [
    "K01_battlefield_clash",
    "K02_modern_interrogation",
    "K03_scifi_boarding",
    "K04_fantasy_spell_casting",
    "K05_anime_rooftop_confrontation",
    # ... 10 个 keyframe case
])
def test_keyframe_prompt_unchanged(case_id, prompt_builder, image_gen):
    """keyframe prompt 重构前后必须完全一致"""
    case = json.loads((KEYFRAME_GOLDEN_DIR / f"{case_id}.json").read_text())

    prompts = prompt_builder.build_keyframe_prompts(case["chapter_event"], case["genre"])
    final_prompt = image_gen._build_keyframe_prompt(prompts[0])

    golden = load_golden_prompt("keyframe", case_id)
    assert final_prompt == golden


def _unified_diff(a: str, b: str) -> str:
    import difflib
    return "\n".join(difflib.unified_diff(
        a.splitlines(), b.splitlines(),
        fromfile="golden", tofile="actual", lineterm=""
    ))


def test_portrait_cache_key_unchanged():
    """portrait cache 策略不应被重构影响"""
    # ... 验证 cache key 算法一致


def test_keyframe_failure_fallback_unchanged():
    """keyframe fallback 路径不变"""
    # ...
```

**Golden 生成脚本**（重构前一次性跑）：

```bash
# 在重构主分支前跑：
python backend/scripts/dump_golden_prompts.py --asset-type portrait --output tests/fixtures/portrait_golden_pre_refactor/
python backend/scripts/dump_golden_prompts.py --asset-type keyframe --output tests/fixtures/keyframe_golden_pre_refactor/
git add tests/fixtures/*_golden_pre_refactor/
git commit -m "Add golden prompts pre background AR refactor"
```

## 风险与权衡

1. **Golden 文件膨胀**：20 个 case × ~500 字 = 10KB，可接受。
2. **prompt_builder / image_gen 接口本身变化**：例如新增参数。权衡：测试用 default 参数调，避免参数变化影响 golden。

## 完成判据自检

- ✅ 围绕"portrait / keyframe 不受影响"，不跑题到 background 测试。
- ✅ 引用 Characterization testing、Jest snapshot、git diff regression。
- ✅ 落到 `backend/tests/test_portrait_keyframe_regression.py`。
- ✅ 符合哲学：守护哲学硬约束"不动 portrait/keyframe"。
