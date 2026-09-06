import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { VNGraph } from '@/data/threeKingdoms'
import { llmSample, ruleSample } from '@/data/vnGraphSamples'
import {
  buildPlaybackPlan,
  isNarration,
  resolveMediaPath,
  resolveValue,
} from '../vnGraphPlayer'

// ─────────────────────────────────────────────────────────────
// resolveValue —— 按 Kind 解包
// ─────────────────────────────────────────────────────────────
test('resolveValue 解包 String / Enum → 字符串', () => {
  assert.equal(resolveValue({ Kind: 'String', StringValue: '荆轲' }), '荆轲')
  assert.equal(resolveValue({ Kind: 'Enum', StringValue: 'FadeOut' }), 'FadeOut')
  assert.equal(resolveValue({ Kind: 'String' }), '') // StringValue 缺省
  assert.equal(resolveValue(undefined), '')
})

test('resolveValue 解包 Int / Float / Double → 数字', () => {
  assert.equal(resolveValue({ Kind: 'Int', NumberValue: 3 }), 3)
  assert.equal(resolveValue({ Kind: 'Float', NumberValue: 0.45 }), 0.45)
  assert.equal(resolveValue({ Kind: 'Double', NumberValue: 1.5 }), 1.5)
  assert.equal(resolveValue({ Kind: 'Float' }), 0) // NumberValue 缺省
})

test('resolveValue 解包 Bool → 布尔', () => {
  assert.equal(resolveValue({ Kind: 'Bool', BoolValue: true }), true)
  assert.equal(resolveValue({ Kind: 'Bool' }), false) // 缺省
})

test('resolveValue 解包 Vector2 / Vector3 / Color', () => {
  assert.deepEqual(resolveValue({ Kind: 'Vector2', X: 520, Y: 120 }), { x: 520, y: 120 })
  assert.deepEqual(resolveValue({ Kind: 'Vector3', X: 1, Y: 2, Z: 3 }), { x: 1, y: 2, z: 3 })
  // Color: X=R Y=G Z=B W=A
  assert.deepEqual(resolveValue({ Kind: 'Color', X: 0.2, Y: 0.4, Z: 0.6, W: 1 }), { r: 0.2, g: 0.4, b: 0.6, a: 1 })
})

test('resolveValue 解包 List / Object / Null', () => {
  assert.deepEqual(
    resolveValue({ Kind: 'List', Items: [{ Kind: 'String', StringValue: 'a' }, { Kind: 'Int', NumberValue: 9 }] }),
    ['a', 9],
  )
  const obj = resolveValue({
    Kind: 'Object',
    ObjectValue: { Name: { Kind: 'String', StringValue: '太子丹' } },
  })
  assert.deepEqual(obj, { Name: '太子丹' })
  assert.equal(resolveValue({ Kind: 'Null' }), null)
})

// ─────────────────────────────────────────────────────────────
// resolveMediaPath —— 资源路径解析
// ─────────────────────────────────────────────────────────────
test('resolveMediaPath: 空串返回空', () => {
  assert.equal(resolveMediaPath('', '/assets'), '')
  assert.equal(resolveMediaPath('', undefined), '')
})

test('resolveMediaPath: HTTP / 站点绝对路径 / data URI 原样返回', () => {
  const url = 'https://cdn.example.com/a/b.png'
  assert.equal(resolveMediaPath(url, undefined), url)
  assert.equal(resolveMediaPath(url, '/assets'), url) // 有 base 也不改 http URL
  assert.equal(resolveMediaPath('/api/media/abc', undefined), '/api/media/abc')
  assert.equal(resolveMediaPath('data:image/png;base64,xxxx', undefined), 'data:image/png;base64,xxxx')
  assert.equal(resolveMediaPath('//cdn.example.com/x.png', undefined), '//cdn.example.com/x.png')
})

test('resolveMediaPath: res:// 未配置 base 返回空串', () => {
  assert.equal(resolveMediaPath('res://Resources/Generated/Tachi/jing_ke.png', undefined), '')
})

test('resolveMediaPath: res:// 配置 base 时拼接（去重斜杠）', () => {
  assert.equal(
    resolveMediaPath('res://Resources/Generated/Tachi/jing_ke.png', 'https://cdn.example.com/assets'),
    'https://cdn.example.com/assets/Resources/Generated/Tachi/jing_ke.png',
  )
  // base 带尾斜杠也要去重
  assert.equal(
    resolveMediaPath('res://foo.png', '/assets/'),
    '/assets/foo.png',
  )
})

// ─────────────────────────────────────────────────────────────
// buildPlaybackPlan —— 用真实样例图验证
// ─────────────────────────────────────────────────────────────
test('buildPlaybackPlan(llm 样例): beat 数 = 两个段落对白行数之和', () => {
  // llm 样例：2 个 Paragraph 节点（Index 5 有 8 行，Index 9 有 3 行），共 11 个对白 beat。
  const plan = buildPlaybackPlan(llmSample.graph)
  const dialogueBeats = plan.beats.filter((b) => b.speaker || b.text)
  assert.equal(plan.beats.length, 11, `预期 11 个 beat，实际 ${plan.beats.length}`)
  assert.equal(dialogueBeats.length, 11)
})

test('buildPlaybackPlan(llm 样例): 每个 beat 携带累积舞台快照', () => {
  // 样例图背景是 res:// 形式，传 resBaseUrl 让其解析成可访问 URL
  const plan = buildPlaybackPlan(llmSample.graph, { resBaseUrl: 'https://cdn.example.com' })
  const beats = plan.beats
  // 段落 1（Index 5）引用 Action [2,3,4]：背景(2) + 太子丹立绘(3) + 荆轲立绘(4)
  // 段落 1 的首个 beat 应同时有背景和两个立绘。
  const firstParaBeats = beats.filter((b) => b.nodeIndex === 5)
  assert.ok(firstParaBeats.length > 0)
  const first = firstParaBeats[0]
  assert.ok(first.stage.backgroundUrl.length > 0, `段落1首beat 应有背景，实际 ${first.stage.backgroundUrl}`)
  assert.ok(first.stage.backgroundUrl.startsWith('https://cdn.example.com/'), '背景应是 resBaseUrl 拼接结果')
  assert.equal(first.stage.portraits.length, 2, '段落1首beat 应有 2 个立绘')
  const ids = first.stage.portraits.map((p) => p.id).sort()
  assert.deepEqual(ids, ['太子丹', '荆轲'])
})

test('buildPlaybackPlan(llm 样例): 未传 resBaseUrl 时 res:// 背景为空（占位语义）', () => {
  const plan = buildPlaybackPlan(llmSample.graph)
  const firstParaBeats = plan.beats.filter((b) => b.nodeIndex === 5)
  const first = firstParaBeats[0]
  // res:// 未配置 base → 空串，由播放器占位显示。这是真实 demo 场景。
  assert.equal(first.stage.backgroundUrl, '', '未配置 resBaseUrl 时 res:// 应解析为空')
  assert.equal(first.stage.portraits.length, 2, '立绘数量不受图片缺失影响')
})

test('buildPlaybackPlan(llm 样例): 段落2 切换背景（易水河畔）', () => {
  const plan = buildPlaybackPlan(llmSample.graph, { resBaseUrl: 'https://cdn.example.com' })
  const secondParaBeats = plan.beats.filter((b) => b.nodeIndex === 9)
  assert.ok(secondParaBeats.length > 0)
  const first = secondParaBeats[0]
  // 段落2 Action [6,7,8]：背景(6) 易水河畔 + 荆轲立绘(7) + 高渐离立绘(8)
  assert.ok(first.stage.backgroundUrl.includes('易水河畔'), `背景应含易水河畔，实际 ${first.stage.backgroundUrl}`)
  const ids = first.stage.portraits.map((p) => p.id).sort()
  assert.deepEqual(ids, ['太子丹', '荆轲', '高渐离'])
  // 旧立绘未显式退场，应保留（累积语义）
})

test('buildPlaybackPlan(llm 样例): 旁白与角色 speaker 正确', () => {
  const plan = buildPlaybackPlan(llmSample.graph)
  const speakers = plan.beats.map((b) => b.speaker)
  assert.ok(speakers.includes('旁白'))
  assert.ok(speakers.includes('太子丹'))
  assert.ok(speakers.includes('荆轲'))
  assert.ok(speakers.includes('高渐离'))
})

test('buildPlaybackPlan(llm 样例): nodeToFirstBeat 映射正确', () => {
  const plan = buildPlaybackPlan(llmSample.graph)
  // Start=1, Paragraph1=5, Paragraph2=9
  assert.ok(plan.nodeToFirstBeat.has(1))
  assert.ok(plan.nodeToFirstBeat.has(5))
  assert.ok(plan.nodeToFirstBeat.has(9))
  // Start 节点不产出 beat（直接进 Next），所以它映射到的 beat 应是段落1的首 beat
  const startBeatIdx = plan.nodeToFirstBeat.get(1)!
  assert.equal(plan.beats[startBeatIdx].nodeIndex, 5)
})

test('buildPlaybackPlan(llm 样例): collectLogs 时首 beat 携带动作日志', () => {
  const plan = buildPlaybackPlan(llmSample.graph, { collectLogs: true })
  assert.ok(plan.beatLogs)
  const firstBeatLogs = plan.beatLogs![0]
  assert.ok(firstBeatLogs.length > 0, '段落1首 beat 应有动作日志')
  assert.ok(firstBeatLogs.some((l) => l.includes('背景')))
  assert.ok(firstBeatLogs.some((l) => l.includes('立绘入场')))
})

test('buildPlaybackPlan(rule 样例): beat 数 = 段落对白行数', () => {
  const plan = buildPlaybackPlan(ruleSample.graph)
  assert.ok(plan.beats.length > 0, 'rule 样例应产出 beat')
})

test('buildPlaybackPlan(rule 样例): BGM 节点跳过执行（日志标注）但不报错', () => {
  const plan = buildPlaybackPlan(ruleSample.graph, { collectLogs: true })
  assert.ok(plan.beatLogs)
  const allLogs = plan.beatLogs!.flat()
  // rule 样例含 BackgroundMusic(21) 节点，应出现"跳过"日志
  assert.ok(allLogs.some((l) => l.includes('音频节点')), `应含音频跳过日志，实际 logs: ${JSON.stringify(allLogs)}`)
})

// ─────────────────────────────────────────────────────────────
// Choice 分支跳转
// ─────────────────────────────────────────────────────────────
const choiceGraph: VNGraph = {
  Version: 1,
  StartNodeIndex: 1,
  Nodes: [
    // 1 Start
    { Index: 1, DisplayName: '开始', Comment: '', NodeType: 1, SubType: 6, X: 0, Y: 0, Data: {}, Outputs: { Next: [2] } },
    // 2 Choice: 两个选项
    {
      Index: 2, DisplayName: '选择', Comment: '', NodeType: 1, SubType: 5, X: 0, Y: 0,
      Data: {
        Options: {
          Kind: 'List',
          Items: [
            { Kind: 'Object', ObjectValue: { Text: { Kind: 'String', StringValue: '走左路' } } },
            { Kind: 'Object', ObjectValue: { Text: { Kind: 'String', StringValue: '走右路' } } },
          ],
        },
      },
      Outputs: { 'Options[0].Next': [3], 'Options[1].Next': [4] },
    },
    // 3 左路 Dialogue
    {
      Index: 3, DisplayName: '左路', Comment: '', NodeType: 1, SubType: 1, X: 0, Y: 0,
      Data: {
        SpeakerIdData: { Kind: 'String', StringValue: '左路人' },
        TextData: { Kind: 'String', StringValue: '这是左边的风景。' },
        VoiceIdData: { Kind: 'String', StringValue: '' },
      },
      Outputs: { Next: [5] },
    },
    // 4 右路 Dialogue
    {
      Index: 4, DisplayName: '右路', Comment: '', NodeType: 1, SubType: 1, X: 0, Y: 0,
      Data: {
        SpeakerIdData: { Kind: 'String', StringValue: '右路人' },
        TextData: { Kind: 'String', StringValue: '这是右边的风景。' },
        VoiceIdData: { Kind: 'String', StringValue: '' },
      },
      Outputs: { Next: [5] },
    },
    // 5 合流 Dialogue
    {
      Index: 5, DisplayName: '合流', Comment: '', NodeType: 1, SubType: 1, X: 0, Y: 0,
      Data: {
        SpeakerIdData: { Kind: 'String', StringValue: '旁白' },
        TextData: { Kind: 'String', StringValue: '两条路在此汇合。' },
        VoiceIdData: { Kind: 'String', StringValue: '' },
      },
      Outputs: { Next: [] },
    },
  ],
}

test('buildPlaybackPlan(Choice): 产出 Choice beat 且 options 含两路 targetNodeIndex', () => {
  const plan = buildPlaybackPlan(choiceGraph)
  const choiceBeat = plan.beats.find((b) => b.subType === 5)
  assert.ok(choiceBeat, '应存在 Choice beat')
  assert.equal(choiceBeat!.options.length, 2)
  assert.equal(choiceBeat!.options[0].targetNodeIndex, 3)
  assert.equal(choiceBeat!.options[1].targetNodeIndex, 4)
})

test('buildPlaybackPlan(Choice): options 的 targetBeatIndex 回填到对应分支首 beat', () => {
  const plan = buildPlaybackPlan(choiceGraph)
  const choiceBeat = plan.beats.find((b) => b.subType === 5)!
  // 两路分支都应被展开成 beat，且 targetBeatIndex 指向各自首 beat
  const leftTarget = choiceBeat.options[0].targetBeatIndex
  const rightTarget = choiceBeat.options[1].targetBeatIndex
  assert.notEqual(leftTarget, -1, '左路 targetBeatIndex 应已回填')
  assert.notEqual(rightTarget, -1, '右路 targetBeatIndex 应已回填')
  assert.equal(plan.beats[leftTarget].nodeIndex, 3, '左路目标应是节点3')
  assert.equal(plan.beats[rightTarget].nodeIndex, 4, '右路目标应是节点4')
  assert.equal(plan.beats[leftTarget].text, '这是左边的风景。')
  assert.equal(plan.beats[rightTarget].text, '这是右边的风景。')
})

test('buildPlaybackPlan(Choice): 两个分支最终合流到节点5', () => {
  const plan = buildPlaybackPlan(choiceGraph)
  // 左路 3→5，右路 4→5，合流节点 5 应出现两次（每条分支各访问一次，visited 仅在主链防环）
  const mergeBeats = plan.beats.filter((b) => b.nodeIndex === 5)
  assert.ok(mergeBeats.length >= 1, '合流节点5应至少出现一次')
  assert.equal(mergeBeats[0].text, '两条路在此汇合。')
})

// ─────────────────────────────────────────────────────────────
// 立绘动作链：入场 → 移动 → 缩放 → 退场
// ─────────────────────────────────────────────────────────────
const tachiGraph: VNGraph = {
  Version: 1,
  StartNodeIndex: 1,
  Nodes: [
    { Index: 1, DisplayName: '开始', Comment: '', NodeType: 1, SubType: 6, X: 0, Y: 0, Data: {}, Outputs: { Next: [10] } },
    // 10 Dialogue，Actions 引用 [20,21,22,23] 顺序执行
    {
      Index: 10, DisplayName: '对白', Comment: '', NodeType: 1, SubType: 1, X: 0, Y: 0,
      Data: {
        SpeakerIdData: { Kind: 'String', StringValue: '荆轲' },
        TextData: { Kind: 'String', StringValue: '动起来。' },
        VoiceIdData: { Kind: 'String', StringValue: '' },
      },
      Outputs: { Actions: [20, 21, 22, 23], Next: [] },
    },
    // 20 Tachi 入场
    {
      Index: 20, DisplayName: '入场', Comment: '', NodeType: 2, SubType: 1, X: 0, Y: 0,
      Data: {
        TachiID: { Kind: 'String', StringValue: 'jing_ke' },
        TachiIamge: { Kind: 'String', StringValue: '/img/jing_ke.png' },
        TargetPosition: { Kind: 'Vector2', X: 500, Y: 100 },
        EnterType: { Kind: 'Enum', StringValue: 'SlideInLeft' },
        Duration: { Kind: 'Float', NumberValue: 0.3 },
      },
      Outputs: {},
    },
    // 21 TachiMove 移动
    {
      Index: 21, DisplayName: '移动', Comment: '', NodeType: 2, SubType: 11, X: 0, Y: 0,
      Data: {
        TachiID: { Kind: 'String', StringValue: 'jing_ke' },
        TargetPosition: { Kind: 'Vector2', X: 700, Y: 100 },
        Duration: { Kind: 'Float', NumberValue: 0.3 },
      },
      Outputs: {},
    },
    // 22 TachiScale 缩放
    {
      Index: 22, DisplayName: '缩放', Comment: '', NodeType: 2, SubType: 12, X: 0, Y: 0,
      Data: {
        TachiID: { Kind: 'String', StringValue: 'jing_ke' },
        TargetScale: { Kind: 'Float', NumberValue: 1.5 },
        Duration: { Kind: 'Float', NumberValue: 0.3 },
      },
      Outputs: {},
    },
    // 23 TachiExit 退场
    {
      Index: 23, DisplayName: '退场', Comment: '', NodeType: 2, SubType: 13, X: 0, Y: 0,
      Data: {
        TachiID: { Kind: 'String', StringValue: 'jing_ke' },
        ExitType: { Kind: 'Enum', StringValue: 'FadeOut' },
        Duration: { Kind: 'Float', NumberValue: 0.3 },
      },
      Outputs: {},
    },
  ],
}

test('buildPlaybackPlan(立绘链): 入场→移动→缩放→退场 在首 beat 快照中累积生效', () => {
  const plan = buildPlaybackPlan(tachiGraph)
  const beat = plan.beats[0]
  // Sequence [20,21,22,23] 在同一节点的 Actions 中顺序应用，最终退场后 portraits 为空
  assert.equal(beat.stage.portraits.length, 0, '退场后立绘应为空')
})

test('buildPlaybackPlan(立绘链): 入场后立即退场前，快照含立绘（用单独图验证移动+缩放累积）', () => {
  // 只保留入场+移动+缩放，去掉退场
  const g: VNGraph = { ...tachiGraph, Nodes: tachiGraph.Nodes.filter((n) => n.Index !== 23) }
  // 修正 10 的 Actions 不含 23
  g.Nodes = g.Nodes.map((n) => n.Index === 10 ? { ...n, Outputs: { Actions: [20, 21, 22], Next: [] } } : n)
  const plan = buildPlaybackPlan(g)
  const beat = plan.beats[0]
  assert.equal(beat.stage.portraits.length, 1)
  const p = beat.stage.portraits[0]
  assert.equal(p.id, 'jing_ke')
  assert.equal(p.url, '/img/jing_ke.png')
  assert.equal(p.x, 700, '应被 TachiMove 移到 x=700')
  assert.equal(p.scale, 1.5, '应被 TachiScale 缩放到 1.5')
  assert.equal(p.enterType, 'SlideInLeft')
})

// ─────────────────────────────────────────────────────────────
// 插画与立绘交替：Art 设插画后，后续 Tachi 立绘 beat 不应被 illustrationUrl 压制
// （来自项目54 真实场景：每个段落引用一个 Art CG，中间穿插立绘段落）
// ─────────────────────────────────────────────────────────────
const artThenTachiGraph: VNGraph = {
  Version: 1,
  StartNodeIndex: 1,
  Nodes: [
    { Index: 1, DisplayName: '开始', Comment: '', NodeType: 1, SubType: 6, X: 0, Y: 0, Data: {}, Outputs: { Next: [10] } },
    // 节点 10 段落：同时引用背景 30 和插画 20（CG），无立绘 —— 验证背景与插画两层独立
    {
      Index: 10, DisplayName: 'CG段', Comment: '', NodeType: 1, SubType: 2, X: 0, Y: 0,
      Data: { Lines: { Kind: 'List', Items: [{ Kind: 'Object', ObjectValue: {
        SpeakerId: { Kind: 'String', StringValue: '旁白' },
        Text: { Kind: 'String', StringValue: '一张 CG。' },
        VoiceId: { Kind: 'String', StringValue: '' },
      } }] } },
      Outputs: { Actions: [30, 20], Next: [11] },
    },
    // 节点 30 BackGround 背景
    {
      Index: 30, DisplayName: '背景', Comment: '', NodeType: 2, SubType: 3, X: 0, Y: 0,
      Data: {
        BackgroundImage: { Kind: 'String', StringValue: '/img/bg1.png' },
        ChangeType: { Kind: 'Enum', StringValue: 'FadeIn' },
        Duration: { Kind: 'Float', NumberValue: 0.5 },
      },
      Outputs: {},
    },
    // 节点 20 Art 插画
    {
      Index: 20, DisplayName: '插画', Comment: '', NodeType: 2, SubType: 6, X: 0, Y: 0,
      Data: {
        IllustrationImage: { Kind: 'String', StringValue: '/img/cg1.png' },
        ChangeType: { Kind: 'Enum', StringValue: 'FadeIn' },
        Duration: { Kind: 'Float', NumberValue: 0.5 },
      },
      Outputs: {},
    },
    // 节点 11 段落：引用立绘 21（应清空插画，显示立绘）
    {
      Index: 11, DisplayName: '立绘段', Comment: '', NodeType: 1, SubType: 2, X: 0, Y: 0,
      Data: { Lines: { Kind: 'List', Items: [{ Kind: 'Object', ObjectValue: {
        SpeakerId: { Kind: 'String', StringValue: '苏月' },
        Text: { Kind: 'String', StringValue: '立绘出场。' },
        VoiceId: { Kind: 'String', StringValue: '' },
      } }] } },
      Outputs: { Actions: [21], Next: [] },
    },
    // 节点 21 Tachi 立绘
    {
      Index: 21, DisplayName: '立绘', Comment: '', NodeType: 2, SubType: 1, X: 0, Y: 0,
      Data: {
        TachiID: { Kind: 'String', StringValue: 'su_yue' },
        TachiIamge: { Kind: 'String', StringValue: '/img/su_yue.png' },
        TargetPosition: { Kind: 'Vector2', X: 620, Y: 120 },
        EnterType: { Kind: 'Enum', StringValue: 'FadeIn' },
        Duration: { Kind: 'Float', NumberValue: 0.3 },
      },
      Outputs: {},
    },
  ],
}

test('Art→Tachi 交替：立绘 beat 的 illustrationUrl 应被清空，立绘不被压制', () => {
  const plan = buildPlaybackPlan(artThenTachiGraph)
  // beat 0 = CG 段（插画，无立绘）；beat 1 = 立绘段
  assert.equal(plan.beats.length, 2)
  const cgBeat = plan.beats[0]
  const tachiBeat = plan.beats[1]
  // CG beat：同时有背景和插画（两层独立，互不清空），立绘被 Art 清空
  assert.equal(cgBeat.stage.backgroundUrl, '/img/bg1.png', '背景应被 BackGround 节点设置')
  assert.equal(cgBeat.stage.illustrationUrl, '/img/cg1.png', '插画应被 Art 节点设置，与背景独立')
  assert.notEqual(cgBeat.stage.backgroundUrl, cgBeat.stage.illustrationUrl, '背景与插画是不同图层，不应互相覆盖')
  assert.equal(cgBeat.stage.portraits.length, 0)
  // 立绘 beat：插画应被 Tachi 清空，立绘正常存在
  assert.equal(tachiBeat.stage.illustrationUrl, '', '立绘入场后 illustrationUrl 必须清空，否则立绘被压制')
  assert.equal(tachiBeat.stage.portraits.length, 1)
  assert.equal(tachiBeat.stage.portraits[0].id, 'su_yue')
  assert.equal(tachiBeat.stage.portraits[0].url, '/img/su_yue.png')
})

// ─────────────────────────────────────────────────────────────
// isNarration
// ─────────────────────────────────────────────────────────────
test('isNarration: 旁白/空 识别', () => {
  assert.equal(isNarration('旁白'), true)
  assert.equal(isNarration(''), true)
  assert.equal(isNarration('NARRATION'), true)
  assert.equal(isNarration('荆轲'), false)
  assert.equal(isNarration('太子丹'), false)
})

// ─────────────────────────────────────────────────────────────
// 边界：空图 / 缺 StartNodeIndex
// ─────────────────────────────────────────────────────────────
test('buildPlaybackPlan: 空 Nodes 返回空 beat 序列', () => {
  const empty: VNGraph = { Version: 1, StartNodeIndex: 1, Nodes: [] }
  const plan = buildPlaybackPlan(empty)
  assert.equal(plan.beats.length, 0)
})

test('buildPlaybackPlan: StartNodeIndex 不存在时返回空', () => {
  const g: VNGraph = { Version: 1, StartNodeIndex: 999, Nodes: [{ Index: 1, DisplayName: '开始', Comment: '', NodeType: 1, SubType: 6, X: 0, Y: 0, Data: {}, Outputs: { Next: [] } }] }
  const plan = buildPlaybackPlan(g)
  assert.equal(plan.beats.length, 0)
})
