<script setup lang="ts">
import { computed } from 'vue'
import type { JsonObject } from '@/api/storyPathTypes'

// 故事设定结构化只读视图：content_json 从裸 JSON 倾倒改为分区渲染。
// 所有字段逐项判空兜底，旧格式/缺字段的修订不会白屏；未知字段折叠进
// 「其他设定」，保证不丢信息。
const props = defineProps<{ content: JsonObject | null | undefined }>()

interface BibleCharacter {
  name: string
  role: string
  appearance: string
  personality: string
  motivation_and_goal: string
  internal_conflict: string
  voice: string
}

const KNOWN_KEYS = new Set([
  'worldview',
  'characters',
  'character_relations',
  'main_conflict',
  'emotional_line',
  'style_rules',
  'ending_constraints',
  'forbidden_points',
  'writing_notes',
  'character_visual_schema_version',
])

function textOf(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function stringArrayOf(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string' && item.trim() !== '')
}

const worldview = computed(() => textOf(props.content?.worldview))
const characters = computed<BibleCharacter[]>(() => {
  const raw: unknown = props.content?.characters
  if (!Array.isArray(raw)) return []
  const rows = raw.filter((item) => typeof item === 'object' && item !== null) as Record<string, unknown>[]
  return rows
    .map((item) => ({
      name: textOf(item.name),
      role: textOf(item.role),
      appearance: textOf(item.appearance),
      personality: textOf(item.personality),
      motivation_and_goal: textOf(item.motivation_and_goal),
      internal_conflict: textOf(item.internal_conflict),
      voice: textOf(item.voice),
    }))
    .filter((item) => item.name !== '')
})
const relations = computed(() => textOf(props.content?.character_relations))
const mainConflict = computed(() => textOf(props.content?.main_conflict))
const emotionalLine = computed(() => textOf(props.content?.emotional_line))
const styleRules = computed(() => textOf(props.content?.style_rules))
const endingConstraints = computed(() => textOf(props.content?.ending_constraints))
const forbiddenPoints = computed(() => stringArrayOf(props.content?.forbidden_points))
const writingNotes = computed(() => stringArrayOf(props.content?.writing_notes))

const extras = computed<{ key: string; json: string }[]>(() => {
  if (!props.content || typeof props.content !== 'object') return []
  return Object.entries(props.content)
    .filter(([key, value]) =>
      !KNOWN_KEYS.has(key)
      && value !== null
      && value !== ''
      && !(Array.isArray(value) && value.length === 0))
    .map(([key, value]) => ({ key, json: JSON.stringify(value, null, 2) }))
})

const characterFields = (character: BibleCharacter): { label: string; text: string }[] => [
  { label: '外貌', text: character.appearance },
  { label: '性格', text: character.personality },
  { label: '动机与目标', text: character.motivation_and_goal },
  { label: '内心冲突', text: character.internal_conflict },
  { label: '语言风格', text: character.voice },
].filter((row) => row.text !== '')

const isEmpty = computed(() =>
  !worldview.value
  && characters.value.length === 0
  && !relations.value
  && !mainConflict.value
  && !emotionalLine.value
  && !styleRules.value
  && !endingConstraints.value
  && forbiddenPoints.value.length === 0
  && writingNotes.value.length === 0)
</script>

<template>
  <div class="bible-view">
    <section v-if="worldview" class="bible-section">
      <h4 class="bible-section-title">世界观</h4>
      <p class="bible-text">{{ worldview }}</p>
    </section>

    <section v-if="characters.length" class="bible-section">
      <h4 class="bible-section-title">核心角色（{{ characters.length }}）</h4>
      <div class="bible-chars">
        <article v-for="character in characters" :key="character.name" class="bible-char">
          <header class="bible-char-header">
            <strong class="bible-char-name">{{ character.name }}</strong>
            <span v-if="character.role" class="bible-char-role">{{ character.role }}</span>
          </header>
          <dl class="bible-char-fields">
            <template v-for="field in characterFields(character)" :key="field.label">
              <dt>{{ field.label }}</dt>
              <dd>{{ field.text }}</dd>
            </template>
          </dl>
        </article>
      </div>
    </section>

    <section v-if="relations" class="bible-section">
      <h4 class="bible-section-title">角色关系</h4>
      <p class="bible-text">{{ relations }}</p>
    </section>

    <div class="bible-grid">
      <section v-if="mainConflict" class="bible-section">
        <h4 class="bible-section-title">主线冲突</h4>
        <p class="bible-text">{{ mainConflict }}</p>
      </section>
      <section v-if="emotionalLine" class="bible-section">
        <h4 class="bible-section-title">情感线</h4>
        <p class="bible-text">{{ emotionalLine }}</p>
      </section>
      <section v-if="styleRules" class="bible-section">
        <h4 class="bible-section-title">风格规则</h4>
        <p class="bible-text">{{ styleRules }}</p>
      </section>
      <section v-if="endingConstraints" class="bible-section">
        <h4 class="bible-section-title">结局约束</h4>
        <p class="bible-text">{{ endingConstraints }}</p>
      </section>
    </div>

    <section v-if="forbiddenPoints.length" class="bible-section">
      <h4 class="bible-section-title">禁则</h4>
      <ul class="bible-list">
        <li v-for="item in forbiddenPoints" :key="item">{{ item }}</li>
      </ul>
    </section>

    <section v-if="writingNotes.length" class="bible-section">
      <h4 class="bible-section-title">创作备注</h4>
      <ul class="bible-list">
        <li v-for="item in writingNotes" :key="item">{{ item }}</li>
      </ul>
    </section>

    <details v-if="extras.length" class="bible-section bible-extras">
      <summary>其他设定（{{ extras.length }}）</summary>
      <div v-for="extra in extras" :key="extra.key" class="bible-extra">
        <strong class="bible-extra-key">{{ extra.key }}</strong>
        <pre class="bible-extra-json">{{ extra.json }}</pre>
      </div>
    </details>

    <p v-if="isEmpty" class="bible-empty">该版本暂无可结构化展示的内容。</p>
  </div>
</template>

<style scoped>
.bible-view {
  max-height: calc(100vh - 300px);
  overflow: auto;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.bible-section-title {
  margin: 0 0 6px;
  color: #1f2d3d;
  font-size: 13px;
  font-weight: 600;
}

.bible-text {
  margin: 0;
  color: #2d3740;
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
}

.bible-chars {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 10px;
}

.bible-char {
  border: 1px solid #e4e8ec;
  border-radius: 8px;
  padding: 10px 12px;
  background: #fafbfc;
}

.bible-char-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.bible-char-name {
  font-size: 14px;
  color: #1f2d3d;
}

.bible-char-role {
  padding: 1px 8px;
  border-radius: 10px;
  background: #ecf5ff;
  color: #409eff;
  font-size: 12px;
}

.bible-char-fields {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bible-char-fields dt {
  color: #8492a6;
  font-size: 12px;
}

.bible-char-fields dd {
  margin: 0 0 4px;
  color: #2d3740;
  font-size: 12.5px;
  line-height: 1.6;
}

.bible-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 14px;
}

.bible-list {
  margin: 0;
  padding-left: 18px;
  color: #2d3740;
  font-size: 13px;
  line-height: 1.7;
}

.bible-extras summary {
  cursor: pointer;
  color: #8492a6;
  font-size: 12.5px;
}

.bible-extra {
  margin-top: 8px;
}

.bible-extra-key {
  color: #5e6d82;
  font-size: 12px;
}

.bible-extra-json {
  margin: 4px 0 0;
  padding: 10px;
  border-radius: 6px;
  background: #f5f7fa;
  color: #2d3740;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.bible-empty {
  margin: 24px 0;
  color: #8492a6;
  font-size: 13px;
  text-align: center;
}
</style>
