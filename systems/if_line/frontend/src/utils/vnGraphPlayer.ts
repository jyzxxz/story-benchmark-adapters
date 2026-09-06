// vnGraphPlayer.ts — VNNodeLibrary.txt 剧情流解释器（纯逻辑，不依赖 Vue）
//
// 把一张 VNGraph（{ Version, StartNodeIndex, Nodes[] }）编译成线性 PlaybackBeat
// 序列，供 VNGraphPlayer.vue 逐句播放。覆盖：
//   Progress: Start / Dialogue / Paragraph / Choice / Transition
//   Action 画面: 背景 / 插画
//   Action 立绘: 入场 / 退场 / 移动 / 水平移动 / 移到旁边 / 缩放 / 效果
//   Action 容器: Sequence（顺序）/ Parallel（并行）
// 音频 / 屏幕特效节点仅记日志、不执行（纯前端难还原音频与着色器）。
//
// 资源路径：后端编译器已把 res:// 剥成真实 media_url 写进 graph_json
// (backend/app/services/vn_graph_compiler.py:67-69)，所以从 API 拉到的图里是
// HTTP URL；只有本地样例/手写图才是 res:// 形式，由 resolveMediaPath 处理。

import type { VNGraph, VNNode, VNSerializedValue } from '@/data/threeKingdoms'

// ─────────────────────────────────────────────────────────────
// 节点枚举（VNNodeLibrary.txt §三）
// ─────────────────────────────────────────────────────────────
export const NodeType = { Progress: 1, Action: 2 } as const

export const ProgressSubType = {
  Dialogue: 1,
  Paragraph: 2,
  Chapter: 3,
  Transition: 4,
  Choice: 5,
  Start: 6,
} as const

export const ActionSubType = {
  Tachi: 1, // 立绘入场
  SE: 2,
  BackGround: 3, // 背景
  CV: 4, // 配音
  Audio: 5, // 音效
  Art: 6, // 插画
  Sequence: 7, // 顺序动作
  Parallel: 8, // 并行动作
  Delay: 9, // 等待
  Transition: 10, // 旧版动作转场
  TachiMove: 11, // 立绘移动
  TachiScale: 12, // 立绘缩放
  TachiExit: 13, // 立绘退场
  TachiMoveX: 14, // 水平移动
  TachiMoveBeside: 15, // 移动到旁边
  TachiEffect: 16, // 立绘效果
  MemoryEffect: 17, // 回忆泛黄
  BlinkEffect: 18, // 眨眼
  BlurEffect: 19, // 屏幕模糊
  ClearScreenEffect: 20, // 清除屏幕特效
  BackgroundMusic: 21, // 背景音乐
} as const

// ─────────────────────────────────────────────────────────────
// 序列化值解包（VNNodeLibrary.txt §二）
// ─────────────────────────────────────────────────────────────
export interface Vec2 { x: number; y: number }
export interface Vec3 { x: number; y: number; z: number }
export interface Color { r: number; g: number; b: number; a: number }

export function resolveValue(field: VNSerializedValue | undefined): unknown {
  if (!field) return ''
  switch (field.Kind) {
    case 'String':
    case 'Enum':
      return field.StringValue ?? ''
    case 'Int':
    case 'Float':
    case 'Double':
      return field.NumberValue ?? 0
    case 'Bool':
      return field.BoolValue ?? false
    case 'Vector2':
      return { x: field.X ?? 0, y: field.Y ?? 0 } satisfies Vec2
    case 'Vector3':
      return { x: field.X ?? 0, y: field.Y ?? 0, z: field.Z ?? 0 } satisfies Vec3
    case 'Color':
      // Godot Color: X=R, Y=G, Z=B, W=A
      return { r: field.X ?? 0, g: field.Y ?? 0, b: field.Z ?? 0, a: field.W ?? 1 } satisfies Color
    case 'List':
      return (field.Items ?? []).map(resolveValue)
    case 'Object': {
      const obj = field.ObjectValue ?? {}
      const out: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(obj)) out[k] = resolveValue(v)
      return out
    }
    case 'Null':
    default:
      return null
  }
}

export function stringValue(field: VNSerializedValue | undefined): string {
  const v = resolveValue(field)
  return typeof v === 'string' ? v : ''
}

export function numberValue(field: VNSerializedValue | undefined, fallback = 0): number {
  const v = resolveValue(field)
  return typeof v === 'number' ? v : fallback
}

export function vec2Value(field: VNSerializedValue | undefined, fallback: Vec2 = { x: 0, y: 0 }): Vec2 {
  const v = resolveValue(field)
  if (v && typeof v === 'object' && 'x' in v && 'y' in v) {
    return { x: Number((v as Vec2).x), y: Number((v as Vec2).y) }
  }
  return fallback
}

// ─────────────────────────────────────────────────────────────
// 资源路径解析
// ─────────────────────────────────────────────────────────────
// resBaseUrl 配置时：res://Resources/Generated/X.png + baseURL → baseURL + '/Resources/Generated/X.png'
// 未配置时返回 ''（由播放器占位显示）。
export function resolveMediaPath(raw: string, resBaseUrl?: string): string {
  if (!raw) return ''
  // 已是可用 URL：后端 API 拉到的 media_url（http/https）、站点绝对路径、data URI。
  if (/^(https?:|\/\/|\/|data:|blob:)/i.test(raw)) return raw
  if (raw.startsWith('res://')) {
    const rel = raw.slice('res://'.length)
    if (!resBaseUrl) return ''
    const base = resBaseUrl.replace(/\/+$/, '')
    return `${base}/${rel}`
  }
  // 其它未知协议原样返回（罕见）。
  return raw
}

// ─────────────────────────────────────────────────────────────
// 舞台状态 & 立绘
// ─────────────────────────────────────────────────────────────
export type TachiEnterType = 'None' | 'FadeIn' | 'SlideInLeft' | 'SlideInRight' | 'SlideInBottom' | 'PopIn'
export type TachiExitType = 'None' | 'FadeOut' | 'SlideOutLeft' | 'SlideOutRight' | 'SlideOutBottom' | 'PopOut'
export type TachiShaderEffect = 'None' | 'ColorGrade' | 'Outline' | 'Dissolve' | 'Glitch' | 'Blur'

export interface PortraitState {
  id: string
  url: string
  x: number
  y: number
  scale: number
  effect: TachiShaderEffect
  enterType: TachiEnterType
}

export interface StageSnapshot {
  backgroundUrl: string
  /** 插画层 url（非空时覆盖舞台，清空立绘） */
  illustrationUrl: string
  portraits: PortraitState[]
  /** 屏幕特效（仅记录，前端 CSS 近似或不渲染） */
  screenEffect: string
}

// ─────────────────────────────────────────────────────────────
// PlaybackBeat
// ─────────────────────────────────────────────────────────────
export interface ChoiceOption {
  text: string
  /** 玩家选此项后跳到的 beat 索引（由 buildPlaybackPlan 预扫描填充） */
  targetBeatIndex: number
  /** 原始目标节点 Index，便于调试 */
  targetNodeIndex: number
}

export interface PlaybackBeat {
  /** 该 beat 来自哪个 VNNode.Index */
  nodeIndex: number
  /** 该 beat 在序列里的序号 */
  beatIndex: number
  speaker: string
  text: string
  voiceUrl: string
  /** 该 beat 显示时的舞台快照（背景+立绘，累积态） */
  stage: StageSnapshot
  /** Choice 节点才有：选项列表；非 Choice 为空数组 */
  options: ChoiceOption[]
  /** 是否为该节点产出的首个 beat（用于 nodeIndex→beatIndex 映射） */
  isFirstOfNode: boolean
  /** 节点类型标记，便于 UI 调试 */
  nodeType: number
  subType: number
  /** 该节点完成后的下一节点 Index；无则 -1（结尾） */
  nextNodeIndex: number
}

// ─────────────────────────────────────────────────────────────
// 舞台快照复制（深拷贝 portraits 数组）
// ─────────────────────────────────────────────────────────────
function cloneStage(s: StageSnapshot): StageSnapshot {
  return {
    backgroundUrl: s.backgroundUrl,
    illustrationUrl: s.illustrationUrl,
    portraits: s.portraits.map((p) => ({ ...p })),
    screenEffect: s.screenEffect,
  }
}

// ─────────────────────────────────────────────────────────────
// 把单个 Action 节点的效果应用到舞台快照（原地修改）
// ─────────────────────────────────────────────────────────────
function applyActionNode(
  node: VNNode,
  stage: StageSnapshot,
  resBaseUrl?: string,
): string[] {
  // 返回该动作产生的日志（调试/未支持类型提示）
  const logs: string[] = []
  const data = node.Data || {}
  switch (node.SubType) {
    case ActionSubType.BackGround: {
      stage.backgroundUrl = resolveMediaPath(stringValue(data.BackgroundImage), resBaseUrl)
      stage.illustrationUrl = '' // 背景节点不插画，但清空插画层避免残留
      logs.push(`背景 → ${stage.backgroundUrl || '(空/占位)'}`)
      break
    }
    case ActionSubType.Art: {
      const url = resolveMediaPath(stringValue(data.IllustrationImage), resBaseUrl)
      stage.illustrationUrl = url
      stage.portraits = [] // 插画显示时清空立绘（VNNodeLibrary §五.5）
      logs.push(url ? `插画 → ${url}` : '清除插画')
      break
    }
    case ActionSubType.Tachi: {
      const id = stringValue(data.TachiID)
      const url = resolveMediaPath(stringValue(data.TachiIamge), resBaseUrl) // 注意源码拼写 TachiIamge
      const pos = vec2Value(data.TargetPosition, { x: 520, y: 120 })
      const enter = (stringValue(data.EnterType) || 'FadeIn') as TachiEnterType
      // 立绘与插画互斥（VNNodeLibrary：插画显示时清空立绘；反之立绘出场应收起插画）。
      // 不清空的话，先前的 illustrationUrl 会沿主链累积，导致后续立绘 beat 被错误压制。
      stage.illustrationUrl = ''
      const existing = stage.portraits.find((p) => p.id === id)
      if (existing) {
        existing.url = url
        existing.x = pos.x
        existing.y = pos.y
        existing.enterType = enter
      } else {
        stage.portraits.push({ id, url, x: pos.x, y: pos.y, scale: 1, effect: 'None', enterType: enter })
      }
      logs.push(`立绘入场 ${id}`)
      break
    }
    case ActionSubType.TachiExit: {
      const id = stringValue(data.TachiID)
      stage.portraits = stage.portraits.filter((p) => p.id !== id)
      logs.push(`立绘退场 ${id}`)
      break
    }
    case ActionSubType.TachiMove: {
      const id = stringValue(data.TachiID)
      const pos = vec2Value(data.TargetPosition)
      const p = stage.portraits.find((x) => x.id === id)
      if (p) { p.x = pos.x; p.y = pos.y }
      logs.push(`立绘移动 ${id}`)
      break
    }
    case ActionSubType.TachiMoveX: {
      const id = stringValue(data.TachiID)
      const tx = numberValue(data.TargetX)
      const p = stage.portraits.find((x) => x.id === id)
      if (p) p.x = tx
      logs.push(`立绘水平移动 ${id} → x=${tx}`)
      break
    }
    case ActionSubType.TachiMoveBeside: {
      const id = stringValue(data.TachiID)
      const ref = stringValue(data.ReferenceTachiID)
      const side = (stringValue(data.Side) || 'Right') === 'Left' ? 'Left' : 'Right' as 'Left' | 'Right'
      const dist = numberValue(data.Distance, 40)
      const refP = stage.portraits.find((x) => x.id === ref)
      const p = stage.portraits.find((x) => x.id === id)
      if (refP && p) {
        p.x = side === 'Left' ? refP.x - dist : refP.x + dist
        p.y = refP.y
      }
      logs.push(`立绘移到旁边 ${id} ↔ ${ref}`)
      break
    }
    case ActionSubType.TachiScale: {
      const id = stringValue(data.TachiID)
      const ts = numberValue(data.TargetScale, 1)
      const p = stage.portraits.find((x) => x.id === id)
      if (p) p.scale = ts
      logs.push(`立绘缩放 ${id} → ${ts}`)
      break
    }
    case ActionSubType.TachiEffect: {
      const id = stringValue(data.TachiID)
      const eff = (stringValue(data.Effect) || 'None') as TachiShaderEffect
      const p = stage.portraits.find((x) => x.id === id)
      if (p) p.effect = eff
      logs.push(`立绘效果 ${id} → ${eff}`)
      break
    }
    case ActionSubType.Delay:
      logs.push(`等待 ${numberValue(data.Duration, 0.3)}s`)
      break
    case ActionSubType.CV:
    case ActionSubType.Audio:
    case ActionSubType.BackgroundMusic:
      logs.push(`音频节点(${node.SubType}) 跳过执行（纯前端不播音频）`)
      break
    case ActionSubType.MemoryEffect:
      stage.screenEffect = 'Memory'
      logs.push('屏幕特效: 回忆泛黄（CSS 近似）')
      break
    case ActionSubType.BlinkEffect:
      logs.push('屏幕特效: 眨眼（跳过）')
      break
    case ActionSubType.BlurEffect:
      stage.screenEffect = 'Blur'
      logs.push('屏幕特效: 模糊（CSS 近似）')
      break
    case ActionSubType.ClearScreenEffect:
      stage.screenEffect = ''
      logs.push('清除屏幕特效')
      break
    default:
      logs.push(`未支持的动作节点 SubType=${node.SubType} 跳过`)
  }
  return logs
}

// 递归展开 Sequence/Parallel，返回叶子 Action 节点列表（保持顺序）。
// Parallel 在前端无法真并行执行，按列表顺序近似（日志会标注）。
function flattenActionIndices(node: VNNode, byIndex: Map<number, VNNode>): number[] {
  const actions = node.Outputs?.Actions ?? []
  const leaves: number[] = []
  for (const idx of actions) {
    const child = byIndex.get(idx)
    if (!child) continue
    if (child.NodeType === NodeType.Action && (child.SubType === ActionSubType.Sequence || child.SubType === ActionSubType.Parallel)) {
      leaves.push(...flattenActionIndices(child, byIndex))
    } else {
      leaves.push(idx)
    }
  }
  return leaves
}

// 把一个 Progress 节点的 Actions 输出全部应用到舞台快照（返回累积日志）。
function applyProgressActions(node: VNNode, stage: StageSnapshot, byIndex: Map<number, VNNode>, resBaseUrl?: string): string[] {
  const logs: string[] = []
  const leaves = flattenActionIndices(node, byIndex)
  for (const idx of leaves) {
    const child = byIndex.get(idx)
    if (child && child.NodeType === NodeType.Action) {
      logs.push(...applyActionNode(child, stage, resBaseUrl))
    }
  }
  return logs
}

// ─────────────────────────────────────────────────────────────
// buildPlaybackPlan 主流程
// ─────────────────────────────────────────────────────────────
export interface BuildPlanOptions {
  resBaseUrl?: string
  /** 收集每个 beat 产生的动作日志（调试用） */
  collectLogs?: boolean
}

export interface PlaybackPlan {
  beats: PlaybackBeat[]
  /** nodeIndex → 该节点首个 beat 的索引（Choice 跳转用） */
  nodeToFirstBeat: Map<number, number>
  /** 每个 beat 的动作日志（仅 collectLogs 时） */
  beatLogs?: string[][]
}

export interface DialogueLine { SpeakerId: string; Text: string; VoiceId: string }

function readDialogueLine(obj: unknown): DialogueLine {
  const o = (obj || {}) as Record<string, unknown>
  return {
    SpeakerId: typeof o.SpeakerId === 'string' ? o.SpeakerId : '',
    Text: typeof o.Text === 'string' ? o.Text : '',
    VoiceId: typeof o.VoiceId === 'string' ? o.VoiceId : '',
  }
}

export function buildPlaybackPlan(graph: VNGraph, opts: BuildPlanOptions = {}): PlaybackPlan {
  const { resBaseUrl, collectLogs = false } = opts
  const beats: PlaybackBeat[] = []
  const nodeToFirstBeat = new Map<number, number>()
  const beatLogs: string[][] | undefined = collectLogs ? [] : undefined

  const byIndex = new Map<number, VNNode>()
  for (const n of graph.Nodes) byIndex.set(n.Index, n)

  const startNode = byIndex.get(graph.StartNodeIndex)
  if (!startNode) {
    return { beats, nodeToFirstBeat }
  }

  // —— 第一遍：DFS 沿 Progress 主链产出 beat，记录 nextNodeIndex 与 Choice options 的 targetNodeIndex。
  // Choice 跳转目标是另一个 Progress 节点，需要 nodeToFirstBeat 映射，所以分两遍：
  // 第一遍产出所有 beat 并填 nodeToFirstBeat，第二遍把 options.targetNodeIndex 解析成 targetBeatIndex。
  interface PendingOption {
    beatIdx: number
    optionIdx: number
    targetNodeIndex: number
  }
  const pendingOptions: PendingOption[] = []

  const visited = new Set<number>()
  // 当前舞台快照（沿主链累积；分叉不回滚，简化处理——VN 主线通常线性 + 少量 Choice 合流）
  let stage: StageSnapshot = {
    backgroundUrl: '',
    illustrationUrl: '',
    portraits: [],
    screenEffect: '',
  }

  const pushBeat = (b: Omit<PlaybackBeat, 'beatIndex'>, logs: string[]): number => {
    const idx = beats.length
    beats.push({ ...b, beatIndex: idx })
    if (beatLogs) beatLogs.push(logs)
    return idx
  }

  const visitProgress = (nodeIndex: number, fromChoiceJump: boolean): void => {
    if (visited.has(nodeIndex)) return // 防环
    visited.add(nodeIndex)
    const node = byIndex.get(nodeIndex)
    if (!node || node.NodeType !== NodeType.Progress) return

    nodeToFirstBeat.set(nodeIndex, beats.length)
    const nextArr = node.Outputs?.Next ?? []
    const nextNodeIndex = nextArr.length > 0 ? nextArr[0] : -1

    if (node.SubType === ProgressSubType.Start) {
      // 开始节点：执行其 Actions（若有），直接进入下一节点。
      const logs = applyProgressActions(node, stage, byIndex, resBaseUrl)
      void fromChoiceJump
      if (nextNodeIndex !== -1) visitProgress(nextNodeIndex, false)
      return
    }

    if (node.SubType === ProgressSubType.Transition) {
      // 转场节点：清空立绘，若指定背景则切换。
      const logs = applyProgressActions(node, stage, byIndex, resBaseUrl)
      stage.portraits = [] // VNNodeLibrary §四.5：转场清空所有立绘
      const bg = resolveMediaPath(stringValue(node.Data?.BackgroundImage), resBaseUrl)
      if (bg) stage.backgroundUrl = bg
      logs.push(`转场（清空立绘，背景=${bg || '不变'}）`)
      if (nextNodeIndex !== -1) visitProgress(nextNodeIndex, false)
      return
    }

    // Dialogue / Paragraph / Choice：先执行节点的 Actions（段落开头演出），再产出对白 beat。
    const logs = applyProgressActions(node, stage, byIndex, resBaseUrl)

    if (node.SubType === ProgressSubType.Choice) {
      const optionsRaw = resolveValue(node.Data?.Options)
      const optionsArr = Array.isArray(optionsRaw) ? optionsRaw : []
      const options: ChoiceOption[] = []
      const beatIdx = pushBeat({
        nodeIndex,
        speaker: '',
        text: '（请选择）',
        voiceUrl: '',
        stage: cloneStage(stage),
        options: [], // 占位，下方用 pendingOptions 回填 targetBeatIndex
        isFirstOfNode: true,
        nodeType: node.NodeType,
        subType: node.SubType,
        nextNodeIndex,
      }, logs)
      optionsArr.forEach((opt, i) => {
        const o = (opt || {}) as Record<string, unknown>
        const text = typeof o.Text === 'string' ? o.Text : `选项 ${i + 1}`
        const key = `Options[${i}].Next`
        const targetNodeIndexArr = node.Outputs?.[key] ?? []
        const targetNodeIndex = targetNodeIndexArr.length > 0 ? targetNodeIndexArr[0] : -1
        options.push({ text, targetBeatIndex: -1, targetNodeIndex })
        if (targetNodeIndex !== -1) pendingOptions.push({ beatIdx, optionIdx: i, targetNodeIndex })
      })
      beats[beatIdx].options = options
      return // Choice 的分支由 pendingOptions 驱动继续遍历（见下方）
    }

    // 对白行收集
    let lines: DialogueLine[] = []
    if (node.SubType === ProgressSubType.Paragraph) {
      const items = resolveValue(node.Data?.Lines)
      if (Array.isArray(items)) lines = items.map(readDialogueLine)
    } else if (node.SubType === ProgressSubType.Dialogue) {
      lines = [{
        SpeakerId: stringValue(node.Data?.SpeakerIdData),
        Text: stringValue(node.Data?.TextData),
        VoiceId: stringValue(node.Data?.VoiceIdData),
      }]
    }
    if (lines.length === 0) {
      // 无对白、无选项的 Progress 节点（如编译器自定义的"章节结束"标记节点 SubType=11、
      // 或空 Dialogue）：不产出 beat（避免显示空对白框），只保留其 Actions 的副作用，
      // 继续沿 Next 推进。
    } else {
      lines.forEach((line, i) => {
        pushBeat({
          nodeIndex,
          speaker: line.SpeakerId,
          text: line.Text,
          voiceUrl: resolveMediaPath(line.VoiceId, resBaseUrl),
          stage: cloneStage(stage),
          options: [],
          isFirstOfNode: i === 0,
          nodeType: node.NodeType,
          subType: node.SubType,
          nextNodeIndex,
        }, i === 0 ? logs : []) // logs 只挂在节点首个 beat
      })
    }

    if (nextNodeIndex !== -1) visitProgress(nextNodeIndex, false)
  }

  visitProgress(graph.StartNodeIndex, false)

  // —— 第二遍：递归遍历 Choice 分支目标（第一遍 visited 没覆盖到的分支）。
  // 把每个 pendingOption 的目标节点也展开成 beat，再回填 targetBeatIndex。
  let pi = 0
  while (pi < pendingOptions.length) {
    const { beatIdx, optionIdx, targetNodeIndex } = pendingOptions[pi]
    pi++
    if (!nodeToFirstBeat.has(targetNodeIndex)) {
      // 记录当前 beat 栈顶快照作为分支起点（分支沿用主链累积态）
      visitProgress(targetNodeIndex, true)
    }
    const targetBeatIdx = nodeToFirstBeat.get(targetNodeIndex)
    if (targetBeatIdx !== undefined) {
      beats[beatIdx].options[optionIdx].targetBeatIndex = targetBeatIdx
    }
  }

  return { beats, nodeToFirstBeat, beatLogs }
}

// ─────────────────────────────────────────────────────────────
// 便捷：判断 speaker 是否为旁白
// ─────────────────────────────────────────────────────────────
const NARRATION_SPEAKERS = new Set(['旁白', 'narration', 'narrator', ''])
export function isNarration(speaker: string): boolean {
  return NARRATION_SPEAKERS.has(speaker.trim().toLowerCase())
}
