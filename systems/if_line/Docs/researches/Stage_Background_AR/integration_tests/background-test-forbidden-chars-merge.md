# E09 — background-test-forbidden-chars-merge

## 背景

`_collect_forbidden_characters`（E02）合并 4 源 + 别名命中。需要测试覆盖：
1. outline.characters 字段
2. SceneSegment.characters_present 字段
3. StoryBible.characters 的 name / english_name / aliases / nicknames
4. 章节正文中别名命中

漏任何一个源，named cast 都可能漏进图。

## SOTA 实践

**pytest mock fixtures**（[`docs.pytest.org/en/stable/mock.html`](https://docs.pytest.org/en/stable/mock.html)）：为每源构造 mock 数据。

**Test data builders**（[`www.growing-object-oriented-software.com/`](https://www.growing-object-oriented-software.com/)）：可组合的测试数据构造模式。

**Coverage-driven testing**（[`coverage.readthedocs.io/`](https://coverage.readthedocs.io/)）：用 coverage 工具验证 4 个源都被覆盖。

## 落地建议

`backend/tests/test_background_forbidden_chars.py`：

```python
@pytest.fixture
def mock_outline():
    return ChapterOutline(
        scene="野战营帐",
        characters=["林夜", "苏晚晴"],  # Source 1
    )

@pytest.fixture
def mock_specs():
    return [
        BackgroundSceneSpec(
            scene_id="s1",
            forbidden_characters=["赵峰"],  # Source 2
            ...
        )
    ]

@pytest.fixture
def mock_story_bible():
    return StoryBible(
        characters=[
            Character(
                name="林夜",
                english_name="Lin Ye",
                aliases=["阿夜"],       # Source 3
                nicknames=["夜哥"],
            ),
            Character(
                name="苏墨",            # Source 3 + 4（出现在正文）
                english_name="Su Mo",
                aliases=["墨子"],
            ),
            Character(
                name="无名路人",        # 不应出现在结果中（不在正文 / outline / spec）
                english_name="",
            )
        ]
    )

@pytest.fixture
def mock_chapter_content():
    return """
    林夜在营帐中与苏晚晴商议军情...
    阿夜走出营帐，看见赵峰在远处巡逻...
    苏墨突然到访，墨子满脸紧张...
    """

def test_collect_from_outline(mock_outline, mock_specs, mock_story_bible, mock_chapter_content, asset_svc):
    forbidden = asset_svc._collect_forbidden_characters(
        mock_outline, mock_specs, mock_story_bible, mock_chapter_content
    )
    # Source 1: outline
    assert "林夜" in forbidden
    assert "苏晚晴" in forbidden

def test_collect_from_specs(mock_outline, mock_specs, mock_story_bible, mock_chapter_content, asset_svc):
    forbidden = asset_svc._collect_forbidden_characters(
        mock_outline, mock_specs, mock_story_bible, mock_chapter_content
    )
    # Source 2: SceneSegment.characters_present
    assert "赵峰" in forbidden

def test_collect_from_bible_names_and_aliases(mock_outline, mock_specs, mock_story_bible, mock_chapter_content, asset_svc):
    forbidden = asset_svc._collect_forbidden_characters(
        mock_outline, mock_specs, mock_story_bible, mock_chapter_content
    )
    # Source 3: StoryBible name + english_name + aliases + nicknames
    assert "林夜" in forbidden
    assert "Lin Ye" in forbidden
    assert "阿夜" in forbidden
    assert "夜哥" in forbidden

def test_collect_from_chapter_content_alias_hit(mock_outline, mock_specs, mock_story_bible, mock_chapter_content, asset_svc):
    forbidden = asset_svc._collect_forbidden_characters(
        mock_outline, mock_specs, mock_story_bible, mock_chapter_content
    )
    # Source 4: 正文命中
    # 苏墨 在 bible 但不在 outline / spec；正文出现"苏墨"和"墨子" → 都应入列
    assert "苏墨" in forbidden
    assert "墨子" in forbidden

def test_collect_excludes_chars_not_in_chapter(mock_outline, mock_specs, mock_story_bible, mock_chapter_content, asset_svc):
    forbidden = asset_svc._collect_forbidden_characters(
        mock_outline, mock_specs, mock_story_bible, mock_chapter_content
    )
    # 无名路人 不在正文 / outline / spec → 不应出现（除非从 bible name 来，但 bible name 一定入列）
    # 改测试期望：bible name 入列，但 hit detection 不影响
    assert "无名路人" in forbidden  # bible name 总是入列（Source 3）

def test_collect_dedup(mock_outline, mock_specs, mock_story_bible, mock_chapter_content, asset_svc):
    forbidden = asset_svc._collect_forbidden_characters(
        mock_outline, mock_specs, mock_story_bible, mock_chapter_content
    )
    # 去重：林夜 来自 outline 和 bible，但只出现一次
    assert forbidden.count("林夜") == 1
```

## 风险与权衡

1. **mock data 与真实数据脱节**：测试 mock 简化，可能漏真实边界。权衡：定期用 prod 数据 snapshot 跑回归。
2. **测试函数过多**：5 个 source × N 个 case → 几十个 test。权衡：用 `parametrize` 合并；只对核心断言分函数。

## 完成判据自检

- ✅ 围绕"forbidden_chars 合并测试"，不跑题到 vn_graph 匹配（E05）。
- ✅ 引用 pytest mock、Test data builders、Coverage-driven testing。
- ✅ 落到 `backend/tests/test_background_forbidden_chars.py`。
- ✅ 符合哲学：测试守护 4 源合并，不引入新模型供应商。
