<!--
  VisualNovelStage.vue — project-visual-bible-v2 §E5

  Shared visual-novel stage component used by ThreeKingdomsPublicView,
  ImmersiveReaderView, and VNGraphPreviewView. Replaces three copies of
  inline stage CSS with one component that:

  * Locks the same slot geometry (height-driven, aspect-locked) so
    transparent-portrait whitespace and proportions are consistent
    regardless of the source canvas.
  * Applies the 4 grade classes (warm / cool / mist / ember) by updating
    BOTH scene and portrait palette CSS variables, so characters and
    backgrounds always shift color together (no more "two works" look).
  * Prefers ``presentation.url`` over ``image_url`` so normalized
    presentation canvases are served when available (decision 3).
  * Renders an optional ``?visualDebug=1`` overlay showing fingerprint /
    scene_treatment / segment_id for QA.

  This component renders the stage chrome only. Routing / state machines
  / TachiID entrance-exit logic stay in the parent view — the parent
  computes which portraits are visible and passes them via ``portraitAssets``.
-->
<template>
  <div
    class="vn-stage"
    :class="stageClass"
    :style="stageStyle"
  >
    <div
      v-if="backgroundUrl"
      :key="backgroundUrl"
      class="vn-backdrop"
      :style="{ backgroundImage: `url(${backgroundUrl})` }"
    ></div>
    <div class="vn-stage-shade"></div>
    <div class="vn-stage-grain"></div>

    <div
      v-for="(p, idx) in slots"
      :key="p.id ?? `${p.url}-${idx}`"
      class="vn-portrait-slot"
      :class="[`vn-portrait-slot--${p.position || 'single'}`, p.slotClass || '']"
      :style="p.slotStyle"
    >
      <img
        v-if="p.url"
        :src="p.url"
        :alt="p.alt || ''"
        class="vn-portrait-img"
        draggable="false"
      />
    </div>

    <div v-if="visualDebug" class="vn-visual-debug">
      <dl>
        <template v-for="(row, k) in debugRows" :key="k">
          <dt>{{ row.label }}</dt>
          <dd>{{ row.value }}</dd>
        </template>
      </dl>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'

type Grade = 'warm' | 'cool' | 'mist' | 'ember' | 'neutral'

interface StageAsset extends Record<string, any> {
  presentation?: Record<string, any>
  presentation_url?: string | null
  versions?: Array<Record<string, any>>
  latest_version_id?: string | number | null
  image_url?: string
  url?: string
}

interface SceneTreatment {
  camera?: string
  atmosphere?: string
  accent_palette?: string[]
  time_of_day?: string
}

export interface PortraitSlot {
  /** Optional stable id for :key reconciliation. */
  id?: string | number
  /** Asset or manifest item — anything with image_url / presentation. */
  asset?: StageAsset
  /** Force a specific URL (overrides asset). */
  url?: string
  /** Left / right slot when two characters share the stage. */
  position?: 'single' | 'left' | 'right'
  alt?: string
  /** Extra class for per-view customization. */
  slotClass?: string
  /** Inline style override for this slot only. */
  slotStyle?: CSSProperties
}

interface Props {
  backgroundAsset?: StageAsset | null
  /** If backgroundUrl is supplied directly it overrides ``backgroundAsset``. */
  backgroundUrl?: string
  portraitAssets?: PortraitSlot[]
  grade?: Grade
  /** Camera-shot modifier ('push' / 'drift-left' / 'drift-right'). */
  shot?: string
  /** Optional style_fingerprint / visual_bible_version / scene_treatment
   * for the visualDebug overlay. Defaults are read off the background asset. */
  styleFingerprint?: string
  visualBibleVersion?: string
  sceneTreatment?: SceneTreatment
  segmentId?: number | string
  /** Render the dev overlay (typically gated by ?visualDebug=1 in the parent). */
  visualDebug?: boolean
  /** CSS variable overrides applied to the stage root. */
  cssVarsOverride?: CSSProperties
}

const props = withDefaults(defineProps<Props>(), {
  backgroundAsset: null,
  backgroundUrl: '',
  portraitAssets: () => [],
  grade: 'neutral',
  shot: '',
  visualDebug: false,
})

// project-visual-bible-v2 §E1 — prefer presentation.url over image_url
function resolveUrl (asset?: StageAsset | null): string {
  if (!asset) return ''
  const a = asset as Record<string, any>
  const presentation = a.presentation as Record<string, any> | undefined
  if (presentation && typeof presentation.url === 'string' && presentation.url) {
    return presentation.url
  }
  if (typeof a.presentation_url === 'string' && a.presentation_url) {
    return a.presentation_url
  }
  if (Array.isArray(a.versions)) {
    const latest = a.versions.find((version: any) => version.id === a.latest_version_id) || a.versions[0]
    if (typeof latest?.media_url === 'string') return latest.media_url
  }
  return typeof a.image_url === 'string' ? a.image_url : (typeof a.url === 'string' ? a.url : '')
}

function resolveSourceUrl (asset?: StageAsset | null): string {
  if (!asset) return ''
  const a = asset as Record<string, any>
  const presentation = a.presentation as Record<string, any> | undefined
  if (presentation && typeof presentation.source_image_url === 'string') {
    return presentation.source_image_url
  }
  if (typeof a.source_image_url === 'string') return a.source_image_url
  return resolveUrl(asset)
}

const backgroundUrl = computed(() => {
  if (props.backgroundUrl) return props.backgroundUrl
  return resolveUrl(props.backgroundAsset)
})

const slots = computed<PortraitSlot[]>(() => {
  return (props.portraitAssets || []).map((s) => {
    const url = s.url || resolveUrl(s.asset) || ''
    return { ...s, url }
  })
})

const stageClass = computed(() => {
  const classes: string[] = []
  if (props.grade && props.grade !== 'neutral') {
    classes.push(`is-grade-${props.grade}`)
  }
  if (props.shot) {
    classes.push(`is-shot-${props.shot}`)
  }
  const hasDual = slots.value.some((s) => s.position === 'left' || s.position === 'right')
  if (hasDual) classes.push('is-layout-dual')
  return classes
})

const stageStyle = computed<CSSProperties>(() => {
  const style: CSSProperties = {}
  if (props.cssVarsOverride) {
    Object.assign(style, props.cssVarsOverride)
  }
  return style
})

const styleFingerprint = computed(() => {
  if (props.styleFingerprint) return props.styleFingerprint
  const a = props.backgroundAsset as Record<string, any> | null
  return a?.style_fingerprint || ''
})

const visualBibleVersion = computed(() => {
  if (props.visualBibleVersion) return props.visualBibleVersion
  const a = props.backgroundAsset as Record<string, any> | null
  return a?.visual_bible_version || ''
})

const sceneTreatment = computed<SceneTreatment | undefined>(() => {
  if (props.sceneTreatment) return props.sceneTreatment
  const a = props.backgroundAsset as Record<string, any> | null
  return a?.scene_treatment as SceneTreatment | undefined
})

const segmentId = computed(() => {
  if (props.segmentId !== undefined) return props.segmentId
  const a = props.backgroundAsset as Record<string, any> | null
  return a?.segment_id ?? ''
})

const debugRows = computed(() => {
  const rows: { label: string; value: string }[] = []
  rows.push({ label: 'fingerprint', value: styleFingerprint.value || '—' })
  rows.push({ label: 'bible_version', value: visualBibleVersion.value || '—' })
  rows.push({ label: 'segment_id', value: String(segmentId.value || '—') })
  if (sceneTreatment.value) {
    const t = sceneTreatment.value
    rows.push({ label: 'scene_camera', value: t.camera || '—' })
    rows.push({ label: 'scene_atmosphere', value: t.atmosphere || '—' })
    rows.push({ label: 'scene_time', value: t.time_of_day || '—' })
    rows.push({
      label: 'accent_palette',
      value: (t.accent_palette || []).join(' / ') || '—',
    })
  }
  const bgSource = resolveSourceUrl(props.backgroundAsset)
  if (bgSource && bgSource !== backgroundUrl.value) {
    rows.push({ label: 'bg_source', value: bgSource })
  }
  return rows
})
</script>

<style scoped>
.vn-stage {
  /* Shared CSS variables — every grade updates BOTH scene and portrait
     palette so characters and backgrounds always shift color together. */
  --scene-saturation: .9;
  --scene-brightness: .96;
  --scene-contrast: 1;
  --scene-sepia: 0;
  --scene-hue-rotate: 0deg;
  --portrait-saturation: .9;
  --portrait-brightness: .98;
  --portrait-contrast: 1.03;
  --portrait-sepia: 0;
  --portrait-hue-rotate: 0deg;
  /* Slot sizing — height-driven, aspect-locked. */
  --vn-slot-aspect: 3 / 5;
  --vn-slot-height-single: 88%;
  --vn-slot-height-dual: 80%;
  --vn-slot-width-single: min(42%, 410px);
  --vn-slot-width-dual: min(38%, 380px);
  --vn-slot-bottom: -6%;
  position: relative;
  isolation: isolate;
  overflow: hidden;
  min-height: 610px;
  background-color: #5f574d;
  border: 1px solid #3e3932;
  outline: none;
  box-shadow: 0 24px 55px rgba(35,29,22,.22);
}
.vn-backdrop {
  position: absolute; z-index: -2; inset: -3%;
  background-position: center; background-size: cover;
  transform: scale(1.02);
  transition: filter .5s ease, background-position .5s ease;
  will-change: transform, filter;
  filter:
    sepia(var(--scene-sepia))
    saturate(var(--scene-saturation))
    brightness(var(--scene-brightness))
    contrast(var(--scene-contrast))
    hue-rotate(var(--scene-hue-rotate));
}
.vn-stage.is-shot-push .vn-backdrop { animation: vn-background-push 13s cubic-bezier(.2,.65,.25,1) both; }
.vn-stage.is-shot-drift-left .vn-backdrop { animation: vn-background-drift-left 14s ease-out both; }
.vn-stage.is-shot-drift-right .vn-backdrop { animation: vn-background-drift-right 14s ease-out both; }
.vn-stage.is-grade-warm {
  --scene-saturation: .94; --scene-brightness: .95; --scene-sepia: .08;
  --portrait-saturation: .91; --portrait-brightness: .97; --portrait-sepia: .06;
}
.vn-stage.is-grade-cool {
  --scene-saturation: .9; --scene-brightness: .94; --scene-hue-rotate: 8deg;
  --portrait-saturation: .88; --portrait-brightness: .97; --portrait-hue-rotate: 4deg;
}
.vn-stage.is-grade-mist {
  --scene-saturation: .82; --scene-contrast: .92; --scene-brightness: 1.03;
  --portrait-saturation: .82; --portrait-contrast: .96; --portrait-brightness: 1.02;
}
.vn-stage.is-grade-ember {
  --scene-saturation: 1.06; --scene-contrast: 1.04; --scene-brightness: .91; --scene-sepia: .18;
  --portrait-saturation: 1.0; --portrait-contrast: 1.05; --portrait-brightness: .94; --portrait-sepia: .12;
}
@keyframes vn-background-push {
  from { transform: translate3d(0, 0, 0) scale(1.02); }
  to { transform: translate3d(1%, -1%, 0) scale(1.1); }
}
@keyframes vn-background-drift-left {
  from { transform: translate3d(3%, 0, 0) scale(1.08); }
  to { transform: translate3d(-3%, 0, 0) scale(1.08); }
}
@keyframes vn-background-drift-right {
  from { transform: translate3d(-3%, 0, 0) scale(1.08); }
  to { transform: translate3d(3%, 0, 0) scale(1.08); }
}
.vn-stage-shade {
  position: absolute; z-index: -1; inset: 0;
  background: linear-gradient(180deg, rgba(8,10,9,.04) 35%, rgba(9,10,9,.36) 68%, rgba(7,8,7,.9) 100%),
              linear-gradient(90deg, rgba(10,10,10,.18), transparent 35%, transparent 65%, rgba(10,10,10,.18));
}
.vn-stage.is-grade-warm .vn-stage-shade { background: linear-gradient(180deg, rgba(117,72,34,.08), rgba(18,15,12,.34) 68%, rgba(8,8,7,.9)), linear-gradient(90deg, rgba(58,34,20,.18), transparent 66%, rgba(25,17,12,.2)); }
.vn-stage.is-grade-cool .vn-stage-shade { background: linear-gradient(180deg, rgba(39,58,70,.12), rgba(10,17,21,.38) 68%, rgba(5,9,11,.91)), linear-gradient(90deg, rgba(14,27,34,.2), transparent 64%, rgba(10,21,28,.22)); }
.vn-stage.is-grade-mist .vn-stage-shade { background: linear-gradient(180deg, rgba(210,211,197,.13), rgba(25,27,24,.29) 62%, rgba(8,9,8,.88)), linear-gradient(90deg, rgba(27,29,26,.14), transparent 68%, rgba(27,29,26,.15)); }
.vn-stage.is-grade-ember .vn-stage-shade { background: radial-gradient(circle at 78% 18%, rgba(181,91,38,.17), transparent 32%), linear-gradient(180deg, rgba(93,43,21,.1), rgba(25,12,8,.4) 68%, rgba(8,6,5,.92)); }
.vn-stage-grain {
  position: absolute; z-index: 5; inset: 0; pointer-events: none;
  opacity: .12; mix-blend-mode: soft-light;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 160 160' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.82' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.75'/%3E%3C/svg%3E");
}
.vn-portrait-slot {
  position: absolute; z-index: 2; bottom: var(--vn-slot-bottom);
  display: flex; align-items: flex-end; justify-content: center;
  pointer-events: none;
}
.vn-portrait-slot--single {
  left: 50%; transform: translateX(-50%);
  width: var(--vn-slot-width-single); height: var(--vn-slot-height-single);
  aspect-ratio: var(--vn-slot-aspect);
}
.vn-portrait-slot--left {
  left: 6%;
  width: var(--vn-slot-width-dual); height: var(--vn-slot-height-dual);
  aspect-ratio: var(--vn-slot-aspect);
}
.vn-portrait-slot--right {
  right: 6%;
  width: var(--vn-slot-width-dual); height: var(--vn-slot-height-dual);
  aspect-ratio: var(--vn-slot-aspect);
}
.vn-portrait-img {
  width: 100%; height: 100%; object-fit: contain; object-position: bottom center;
  filter:
    sepia(var(--portrait-sepia))
    saturate(var(--portrait-saturation))
    brightness(var(--portrait-brightness))
    contrast(var(--portrait-contrast))
    hue-rotate(var(--portrait-hue-rotate));
  transition: filter .4s ease;
  user-select: none;
}
.vn-visual-debug {
  position: absolute; z-index: 9; top: 8px; right: 8px;
  max-width: 320px; padding: 8px 10px;
  background: rgba(8, 8, 7, .82); color: #fbf8f1;
  font: 9px/1.35 Consolas, monospace;
  border: 1px solid rgba(239,222,190,.4); border-radius: 2px;
  pointer-events: none;
}
.vn-visual-debug dl { display: grid; grid-template-columns: auto 1fr; gap: 2px 8px; margin: 0; }
.vn-visual-debug dt { color: rgba(239,222,190,.7); text-transform: uppercase; letter-spacing: .08em; }
.vn-visual-debug dd { margin: 0; word-break: break-all; }

/* Mobile breakpoint — smaller slots geometry under 768px. */
@media (max-width: 768px) {
  .vn-stage { min-height: 480px; }
  .vn-portrait-slot--single { width: min(60%, 280px); height: 80%; }
  .vn-portrait-slot--left, .vn-portrait-slot--right { width: min(48%, 240px); height: 72%; }
}
</style>
