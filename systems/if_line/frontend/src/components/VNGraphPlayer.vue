<!--
  VNGraphPlayer.vue — VNNodeLibrary.txt 剧情流播放器

  加载一张 VNGraph（{ Version, StartNodeIndex, Nodes[] }），用 vnGraphPlayer.ts
  的 buildPlaybackPlan 编译成线性 beat 序列后逐句播放。

  画面层复用 VisualNovelStage.vue（背景 + 立绘槽 + grade 滤镜），在其上叠加：
    * 对白框（speaker + text，旁白标 NARRATION）
    * Choice 选项按钮
    * 控制条（上一句 / 下一句 / 重置 / 自动播放 / 节点调试）

  资源：
    * 从后端 API 拉到的 graph_json 里已是真实 media_url（HTTP），直接用。
    * 本地样例 / 手写图若含 res://，由 resBaseUrl 拼接；未配置时占位显示。
-->
<template>
  <div class="vn-player" :class="{ 'is-autoplay': autoplay }">
    <!-- 舞台层（背景 + 立绘）—— 复用 VisualNovelStage -->
    <VisualNovelStage
      :background-url="displayBackgroundUrl"
      :portrait-assets="stagePortraits"
      grade="neutral"
    />

    <!-- 立绘占位标签层：当立绘 url 为空时在对应位置显示角色名色块 -->
    <div class="vn-player-placeholders">
      <div
        v-for="p in placeholderPortraits"
        :key="`ph-${p.id}`"
        class="vn-player-portrait-ph"
        :style="placeholderStyle(p)"
      >
        <span class="vn-player-portrait-ph-name">{{ p.id }}</span>
      </div>
    </div>

    <!-- 插画（关键帧 CG）层：独立于背景，叠加在舞台之上、对白框之下。
         VNNodeLibrary §五.5：插画用于关键场面 CG，显示时清空立绘。
         插画与背景是两层，不再互相覆盖（同 beat 可同时显示背景在下、CG 在上）。 -->
    <transition name="vn-cg">
      <div
        v-if="currentBeat?.stage.illustrationUrl"
        :key="currentBeat.stage.illustrationUrl"
        class="vn-player-cg"
        :style="{ backgroundImage: `url(${currentBeat.stage.illustrationUrl})` }"
      ></div>
    </transition>

    <!-- 空状态：图未加载或无 beat -->
    <div v-if="!currentBeat" class="vn-player-empty">
      <span v-if="!graph">未加载 VNGraph</span>
      <span v-else>该图无可播放的剧情节点</span>
    </div>

    <!-- 对白框 -->
    <transition name="vn-dialogue">
      <div
        v-if="currentBeat"
        :key="`dlg-${currentBeat.beatIndex}`"
        class="vn-player-dialogue"
        @click="onDialogueClick"
      >
        <div class="vn-player-nameplate">
          <span class="vn-player-speaker" :class="{ 'is-narration': isNarration(currentBeat.speaker) }">
            {{ currentBeat.speaker || '旁白' }}
          </span>
          <span v-if="isNarration(currentBeat.speaker)" class="vn-player-tag">NARRATION</span>
        </div>
        <p class="vn-player-text">{{ currentBeat.text }}</p>

        <!-- Choice 选项 -->
        <div v-if="currentBeat.options.length > 0" class="vn-player-options" @click.stop>
          <button
            v-for="(opt, i) in currentBeat.options"
            :key="i"
            class="vn-player-option"
            @click="chooseOption(opt)"
          >
            {{ opt.text }}
          </button>
        </div>

        <!-- 继续提示（非 Choice beat） -->
        <span v-else-if="!isLastBeat" class="vn-player-cue">▾</span>
      </div>
    </transition>

    <!-- 控制条 -->
    <div class="vn-player-controls">
      <div class="vn-player-controls-left">
        <span class="vn-player-progress">{{ beatIndex + 1 }} / {{ totalBeats }}</span>
        <button
          class="vn-player-btn"
          :disabled="beatIndex <= 0"
          title="上一句（←）"
          @click="rewind"
        >← 上一句</button>
        <button
          class="vn-player-btn vn-player-btn--primary"
          :disabled="isLastBeat || (currentBeat?.options.length ?? 0) > 0"
          :title="isLastBeat ? '已是结尾' : '下一句（→ / 空格）'"
          @click="advance"
        >下一句 →</button>
        <button class="vn-player-btn" title="重置" @click="reset">重置</button>
      </div>
      <div class="vn-player-controls-right">
        <label class="vn-player-toggle">
          <input type="checkbox" v-model="autoplay" /> 自动播放
        </label>
        <label class="vn-player-toggle">
          <input type="checkbox" v-model="showDebug" /> 节点调试
        </label>
      </div>
    </div>

    <!-- 节点调试浮层 -->
    <div v-if="showDebug && currentBeat" class="vn-player-debug">
      <dl>
        <dt>beat</dt><dd>{{ currentBeat.beatIndex }}</dd>
        <dt>node</dt><dd>{{ currentBeat.nodeIndex }}</dd>
        <dt>type</dt><dd>{{ currentBeat.nodeType }}/{{ currentBeat.subType }}</dd>
        <dt>next</dt><dd>{{ currentBeat.nextNodeIndex }}</dd>
        <dt>portraits</dt><dd>{{ currentBeat.stage.portraits.length }}</dd>
        <dt>bg</dt><dd class="vn-player-debug-url">{{ currentBeat.stage.backgroundUrl || '(空/占位)' }}</dd>
        <dt>cg</dt><dd class="vn-player-debug-url">{{ currentBeat.stage.illustrationUrl || '(无)' }}</dd>
      </dl>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch, onMounted, onUnmounted } from 'vue'
import VisualNovelStage, { type PortraitSlot } from './VisualNovelStage.vue'
import type { VNGraph } from '@/data/threeKingdoms'
import {
  buildPlaybackPlan,
  isNarration,
  type ChoiceOption,
  type PlaybackBeat,
  type PortraitState,
} from '@/utils/vnGraphPlayer'

interface Props {
  graph: VNGraph | null
  /** 把 res:// 资源映射到该 base URL；未配置时 res:// 解析为空（占位） */
  resBaseUrl?: string
  /** 自动播放：到达一个 beat 后定时推进 */
  autoplay?: boolean
  /** 自动播放时每句停留秒数 */
  autoplayIntervalMs?: number
}

const props = withDefaults(defineProps<Props>(), {
  resBaseUrl: '',
  autoplay: false,
  autoplayIntervalMs: 2500,
})

const emit = defineEmits<{
  /** 播放结束（到达最后 beat） */
  ended: []
  /** beat 切换 */
  beatChange: [beat: PlaybackBeat]
}>()

// ─────────────────────────────────────────────────────────────
// 编译播放计划
// ─────────────────────────────────────────────────────────────
const plan = ref(buildPlaybackPlanSafe(props.graph, props.resBaseUrl))
const beatIndex = ref(0)

function buildPlaybackPlanSafe(graph: VNGraph | null, resBaseUrl: string) {
  if (!graph) return { beats: [] as PlaybackBeat[], nodeToFirstBeat: new Map<number, number>() }
  return buildPlaybackPlan(graph, { resBaseUrl: resBaseUrl || undefined })
}

watch(
  () => [props.graph, props.resBaseUrl] as const,
  ([g, base]) => {
    plan.value = buildPlaybackPlanSafe(g, base)
    beatIndex.value = 0
    emitBeatChange()
  },
)

const beats = computed(() => plan.value.beats)
const totalBeats = computed(() => beats.value.length)
const currentBeat = computed<PlaybackBeat | null>(() => beats.value[beatIndex.value] ?? null)
const isLastBeat = computed(() => beatIndex.value >= totalBeats.value - 1)

function emitBeatChange() {
  if (currentBeat.value) emit('beatChange', currentBeat.value)
  if (isLastBeat.value && totalBeats.value > 0) emit('ended')
}

// ─────────────────────────────────────────────────────────────
// 立绘 → VisualNovelStage 槽位映射
// ─────────────────────────────────────────────────────────────
// 样例画布约 1280 宽（立绘 x 在 520-720）。用中心 640 判定左右。
const CANVAS_CENTER_X = 640

// 背景层 URL：只用 backgroundUrl。插画（CG）有独立的图层（.vn-player-cg），
// 不再用插画覆盖背景——同 beat 同时有背景+插画时，背景在下、CG 叠加上方，两者都可见。
const displayBackgroundUrl = computed(() => {
  const beat = currentBeat.value
  if (!beat) return ''
  return beat.stage.backgroundUrl || ''
})

const stagePortraits = computed<PortraitSlot[]>(() => {
  const beat = currentBeat.value
  if (!beat) return []
  // 有插画时不显示立绘（VNNodeLibrary §五.5：插画清空立绘）
  if (beat.stage.illustrationUrl) return []
  return beat.stage.portraits
    .filter((p) => p.url) // 只把有 url 的交给 stage；无 url 的走占位层
    .map((p) => ({
      id: p.id,
      url: p.url,
      position: positionOf(p),
      slotStyle: portraitSlotStyle(p),
      alt: p.id,
    }))
})

// 占位立绘：url 为空的角色，在播放器自己的层上画色块
const placeholderPortraits = computed<PortraitState[]>(() => {
  const beat = currentBeat.value
  if (!beat || beat.stage.illustrationUrl) return []
  return beat.stage.portraits.filter((p) => !p.url)
})

function positionOf(p: PortraitState): 'single' | 'left' | 'right' {
  const visible = currentBeat.value!.stage.portraits.filter((x) => x.url)
  if (visible.length <= 1) return 'single'
  // 多立绘：优先按编译器 TargetPosition 的实际 x 坐标分槽（x 为 1920 设计空间
  // 立绘左上角，有效宽 612），坐标不可用时回退按 id 奇偶分左右。
  const width = visible.map((v) => v.x).sort((a, b) => a - b)
  const min = width[0]
  const max = width[width.length - 1]
  const spread = max - min
  if (spread > 100) {
    const ratio = spread > 0 ? (p.x - min) / spread : 0.5
    return ratio < 0.34 ? 'left' : ratio > 0.66 ? 'right' : 'single'
  }
  const sorted = [...visible].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
  const idx = sorted.findIndex((x) => x.id === p.id)
  return idx % 2 === 0 ? 'left' : 'right'
}

function portraitSlotStyle(p: PortraitState): Record<string, string> {
  const style: Record<string, string> = { transform: `scale(${p.scale})` }
  const visible = currentBeat.value!.stage.portraits.filter((x) => x.url)
  // 多立绘时用实际坐标把立绘视觉中心映射到舞台百分比（1920 设计空间，
  // 立绘左上角 x + 半宽 306 = 中心），三人队形中间位借此错开左右槽。
  if (visible.length > 1) {
    const centerPct = Math.max(8, Math.min(92, ((p.x + 306) / 1920) * 100))
    style.left = `${centerPct}%`
    style.right = 'auto'
    style.transform = `translateX(-50%) scale(${p.scale})`
  }
  // TachiShaderEffect 近似（CSS）
  if (p.effect === 'Blur') style.filter = 'blur(3px)'
  if (p.effect === 'Outline') style.filter = 'drop-shadow(0 0 3px #fff)'
  if (p.effect === 'Dissolve') style.opacity = '0.4'
  return style
}

function placeholderStyle(p: PortraitState): Record<string, string> {
  // 把画布坐标(0-1280 x, 0-720 y)近似映射到舞台百分比
  const leftPct = Math.max(8, Math.min(88, (p.x / 1280) * 100))
  const style: Record<string, string> = {
    left: `${leftPct}%`,
    transform: `translateX(-50%) scale(${p.scale})`,
  }
  return style
}

// ─────────────────────────────────────────────────────────────
// 推进控制
// ─────────────────────────────────────────────────────────────
function advance() {
  const beat = currentBeat.value
  if (!beat) return
  // Choice beat 必须选选项，不能直接 advance
  if (beat.options.length > 0) return
  if (isLastBeat.value) {
    emit('ended')
    return
  }
  beatIndex.value++
  emitBeatChange()
}

function rewind() {
  if (beatIndex.value <= 0) return
  beatIndex.value--
  emitBeatChange()
}

function chooseOption(opt: ChoiceOption) {
  if (opt.targetBeatIndex >= 0 && opt.targetBeatIndex < totalBeats.value) {
    beatIndex.value = opt.targetBeatIndex
    emitBeatChange()
  }
}

function reset() {
  beatIndex.value = 0
  autoplay.value = false
  emitBeatChange()
}

function onDialogueClick() {
  const beat = currentBeat.value
  if (!beat) return
  // 对白框点击 = 下一句（Choice beat 除外，选项有自己的点击处理）
  if (beat.options.length > 0) return
  advance()
}

// ─────────────────────────────────────────────────────────────
// 键盘控制
// ─────────────────────────────────────────────────────────────
function onKeydown(e: KeyboardEvent) {
  const beat = currentBeat.value
  if (!beat) return
  if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'Enter') {
    e.preventDefault()
    if (beat.options.length === 0) advance()
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault()
    rewind()
  }
}

// ─────────────────────────────────────────────────────────────
// 自动播放
// ─────────────────────────────────────────────────────────────
// 用本地 ref 镜像 prop，使其可写（reset / 到结尾时关闭）。
const autoplay = ref(props.autoplay)
watch(() => props.autoplay, (v) => { autoplay.value = v })

const showDebug = ref(false)
let autoplayTimer: ReturnType<typeof setInterval> | null = null

function clearAutoplayTimer() {
  if (autoplayTimer) {
    clearInterval(autoplayTimer)
    autoplayTimer = null
  }
}

watch(
  autoplay,
  (on) => {
    clearAutoplayTimer()
    if (on) {
      autoplayTimer = setInterval(() => {
        const beat = currentBeat.value
        if (!beat) return
        // Choice beat 暂停自动推进，等玩家选
        if (beat.options.length > 0) return
        if (isLastBeat.value) {
          autoplay.value = false
          return
        }
        advance()
      }, props.autoplayIntervalMs)
    }
  },
)

// ─────────────────────────────────────────────────────────────
// 生命周期
// ─────────────────────────────────────────────────────────────
onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  emitBeatChange()
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  clearAutoplayTimer()
})
</script>

<style scoped>
.vn-player {
  position: relative;
  width: 100%;
  min-height: 610px;
  user-select: none;
  outline: none;
}

/* 占位立绘层 */
.vn-player-placeholders {
  position: absolute;
  inset: 0;
  z-index: 3;
  pointer-events: none;
}
.vn-player-portrait-ph {
  position: absolute;
  bottom: -6%;
  width: min(42%, 410px);
  height: 78%;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  background: linear-gradient(180deg, rgba(80, 90, 110, 0.35), rgba(40, 45, 60, 0.55));
  border: 1px dashed rgba(239, 222, 190, 0.5);
  border-radius: 6px 6px 0 0;
  aspect-ratio: 3 / 5;
  transform-origin: bottom center;
}
.vn-player-portrait-ph-name {
  margin-bottom: 12%;
  padding: 4px 12px;
  background: rgba(8, 8, 7, 0.7);
  color: #fbf8f1;
  font-size: 14px;
  border-radius: 3px;
  letter-spacing: 0.05em;
}

/* 插画（关键帧 CG）层：叠加在背景舞台之上，独立于背景图。
   z-index 5：高于占位立绘层(3)和空状态(4)，低于对白框(6)与控制条(8)。 */
.vn-player-cg {
  position: absolute;
  inset: 0;
  z-index: 5;
  background-position: center;
  background-size: cover;
  background-repeat: no-repeat;
  pointer-events: none;
  box-shadow: inset 0 0 120px rgba(0, 0, 0, 0.35);
}
.vn-cg-enter-active,
.vn-cg-leave-active {
  transition: opacity 0.45s ease;
}
.vn-cg-enter-from,
.vn-cg-leave-to {
  opacity: 0;
}

/* 空状态 */
.vn-player-empty {
  position: absolute;
  inset: 0;
  z-index: 4;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(251, 248, 241, 0.7);
  font-size: 15px;
  background: rgba(8, 8, 7, 0.4);
}

/* 对白框 */
.vn-player-dialogue {
  position: absolute;
  left: 4%;
  right: 4%;
  bottom: 64px;
  z-index: 6;
  padding: 14px 20px 18px;
  background: linear-gradient(180deg, rgba(12, 12, 14, 0.86), rgba(8, 8, 10, 0.92));
  border: 1px solid rgba(239, 222, 190, 0.35);
  border-radius: 4px;
  color: #fbf8f1;
  cursor: pointer;
  box-shadow: 0 -8px 30px rgba(0, 0, 0, 0.35);
}
.vn-player-nameplate {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.vn-player-speaker {
  font-size: 15px;
  font-weight: 600;
  color: #f3e4c0;
  letter-spacing: 0.04em;
}
.vn-player-speaker.is-narration {
  color: rgba(239, 222, 190, 0.7);
  font-weight: 500;
  font-style: italic;
}
.vn-player-tag {
  font-size: 9px;
  padding: 1px 5px;
  background: rgba(239, 222, 190, 0.15);
  color: rgba(239, 222, 190, 0.8);
  border-radius: 2px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
.vn-player-text {
  font-size: 16px;
  line-height: 1.65;
  margin: 0;
  color: #f5ede0;
}
.vn-player-cue {
  position: absolute;
  right: 16px;
  bottom: 8px;
  color: rgba(239, 222, 190, 0.6);
  font-size: 14px;
  animation: vn-player-blink 1.2s ease-in-out infinite;
}
@keyframes vn-player-blink {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 0.9; }
}

/* 选项 */
.vn-player-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
  cursor: default;
}
.vn-player-option {
  text-align: left;
  padding: 8px 14px;
  background: rgba(239, 222, 190, 0.08);
  border: 1px solid rgba(239, 222, 190, 0.3);
  color: #fbf8f1;
  font-size: 15px;
  border-radius: 3px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.vn-player-option:hover {
  background: rgba(239, 222, 190, 0.18);
  border-color: rgba(239, 222, 190, 0.6);
}

/* 控制条 */
.vn-player-controls {
  position: absolute;
  left: 4%;
  right: 4%;
  bottom: 14px;
  z-index: 8;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.vn-player-controls-left,
.vn-player-controls-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.vn-player-progress {
  font-size: 12px;
  color: rgba(251, 248, 241, 0.75);
  font-variant-numeric: tabular-nums;
  min-width: 48px;
}
.vn-player-btn {
  padding: 4px 12px;
  font-size: 13px;
  background: rgba(239, 222, 190, 0.1);
  border: 1px solid rgba(239, 222, 190, 0.3);
  color: #fbf8f1;
  border-radius: 3px;
  cursor: pointer;
  transition: background 0.15s;
}
.vn-player-btn:hover:not(:disabled) {
  background: rgba(239, 222, 190, 0.2);
}
.vn-player-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.vn-player-btn--primary {
  background: rgba(102, 126, 234, 0.35);
  border-color: rgba(102, 126, 234, 0.6);
}
.vn-player-btn--primary:hover:not(:disabled) {
  background: rgba(102, 126, 234, 0.55);
}
.vn-player-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: rgba(251, 248, 241, 0.8);
  cursor: pointer;
  padding: 2px 4px;
}
.vn-player-toggle input {
  /* 显式尺寸，确保 label 嵌套关联可点（否则默认渲染框在部分环境不可命中） */
  appearance: auto;
  width: 14px;
  height: 14px;
  margin: 0;
  cursor: pointer;
  accent-color: #667eea;
}

/* 调试浮层 */
.vn-player-debug {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 7;
  max-width: 340px;
  padding: 8px 10px;
  background: rgba(8, 8, 7, 0.85);
  color: #fbf8f1;
  font: 10px/1.4 Consolas, monospace;
  border: 1px solid rgba(239, 222, 190, 0.4);
  border-radius: 2px;
  pointer-events: none;
}
.vn-player-debug dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 2px 8px;
  margin: 0;
}
.vn-player-debug dt {
  color: rgba(239, 222, 190, 0.7);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.vn-player-debug dd {
  margin: 0;
  word-break: break-all;
}

/* 对白框过渡 */
.vn-dialogue-enter-active,
.vn-dialogue-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}
.vn-dialogue-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.vn-dialogue-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@media (max-width: 768px) {
  .vn-player { min-height: 480px; }
  .vn-player-dialogue { bottom: 60px; padding: 10px 14px 14px; }
  .vn-player-text { font-size: 15px; }
}
</style>
