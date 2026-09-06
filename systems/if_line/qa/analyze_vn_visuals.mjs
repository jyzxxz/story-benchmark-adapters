#!/usr/bin/env node

import fs from 'node:fs'
import path from 'node:path'

const root = process.argv[2]
if (!root) {
  console.error('Usage: node analyze_vn_visuals.mjs <three-kingdoms-data-dir>')
  process.exit(1)
}

const aliases = {
  刘备: ['刘备', '玄德', '刘玄德', '皇叔', '刘豫州', '先主'],
  关羽: ['关羽', '云长', '关云长', '关公', '美髯公'],
  张飞: ['张飞', '翼德', '张翼德'],
  诸葛亮: ['诸葛亮', '孔明', '卧龙', '武侯'],
  赵云: ['赵云', '子龙', '赵子龙'],
  庞统: ['庞统', '士元', '凤雏'],
  姜维: ['姜维', '伯约', '姜伯约'],
  曹操: ['曹操', '孟德', '曹孟德', '阿瞒'],
  郭嘉: ['郭嘉', '奉孝', '郭奉孝'],
  荀彧: ['荀彧', '文若', '荀文若'],
  司马懿: ['司马懿', '仲达', '司马仲达'],
  曹丕: ['曹丕', '子桓'],
  孙策: ['孙策', '伯符', '小霸王'],
  孙权: ['孙权', '仲谋', '孙仲谋'],
  周瑜: ['周瑜', '公瑾', '周公瑾'],
  鲁肃: ['鲁肃', '子敬', '鲁子敬'],
  陆逊: ['陆逊', '伯言', '陆伯言'],
  吕布: ['吕布', '奉先', '吕奉先', '温侯'],
  貂蝉: ['貂蝉'],
  董卓: ['董卓', '仲颖', '董太师'],
  袁绍: ['袁绍', '本初', '袁本初'],
}

const backgroundRules = [
  [/宫|殿|帝|朝廷|天子|百官/, 'palace'],
  [/城门|城下|关隘|城池/, 'city_gate'],
  [/江|河|水|桥|渡|船|湖/, 'bridge_river'],
  [/狱|牢|囚|下狱/, 'prison'],
  [/客店|酒肆|酒店|驿馆|店中/, 'inn'],
  [/房中|房内|卧房|寝|榻|床|帐中|帐内/, 'room'],
  [/衙|公堂|官府|堂上|堂下|审案/, 'yamen'],
  [/厨|灶|炊|饭|宴|酒食/, 'kitchen'],
  [/书房|读书|书案|密信|奏章/, 'study'],
  [/园|桃|花|竹|林|山中|洞中/, 'garden'],
  [/夜|月|更鼓|星/, 'night'],
  [/街|市|县|村|榜文/, 'street'],
  [/战|兵|军|阵|营|寨|讨贼|厮杀/, 'battlefield'],
]

const expressionRules = [
  [/大惊|惊曰|惊问|惊叫|愕然|骇然|失色|吃惊|不意|忽见/, 'surprise'],
  [/大怒|怒曰|怒喝|厉声|叱曰|骂曰|咬牙|愤然|暴怒/, 'angry'],
  [/大哭|哭曰|泣曰|流泪|垂泪|悲曰|哀叹|痛哭|伤感/, 'sad'],
  [/大恐|恐曰|惧曰|慌忙|胆寒|战栗|惊慌|惶恐/, 'fear'],
  [/大笑|笑曰|微笑|欣然|喜曰|喜道|称善|大喜/, 'smile'],
  [/拔刀|拔剑|挥刀|挥剑|举枪|挺枪|冲杀|杀来|杀将来|杀出|混杀|迎战|交战|交锋|厮杀|大战|追杀|攻城|斩将|射箭/, 'combat'],
]

const actionVerb = '(?:起身|下马|上马|拔|挥|举|拜|跪|坐|立|行|走|奔|赶|追|跃|冲|杀|斩|战|斗|射|刺|砍|救|扶|持|执|提|舞|取|放|纵|看|观|望|笑|哭|怒|惊|叹|喝|叫|问|答|命|令|引|领|迎|送|入|出|来|去|回|转|退|进|卧|醒|写|读|拆|开|收|藏|献|投|攻|守)'

function splitText(text, maxLength = 145) {
  const sentences = text.match(/[^。！？；…]+(?:[。！？；…]+|$)/g) || [text]
  const chunks = []
  let current = ''
  for (const sentence of sentences) {
    const clean = sentence.trim()
    if (!clean) continue
    if (current && current.length + clean.length > maxLength) {
      chunks.push(current)
      current = ''
    }
    if (clean.length <= maxLength) {
      current += clean
      continue
    }
    if (current) chunks.push(current)
    for (let offset = 0; offset < clean.length; offset += maxLength) chunks.push(clean.slice(offset, offset + maxLength))
  }
  if (current) chunks.push(current)
  return chunks
}

function canonical(value) {
  const clean = value.trim()
  return Object.entries(aliases).find(([, values]) => values.includes(clean))?.[0] || ''
}

function spoken(text) {
  for (const [name, values] of Object.entries(aliases)) {
    if (values.some((value) => new RegExp(`${value}(?:笑曰|问曰|答曰|喝曰|叹曰|怒曰|曰|说道|道|问|答|喝|叫道)[：，,]?[“「]`).test(text))) return name
  }
  return ''
}

function readLines(graph) {
  const lines = []
  for (const node of graph.Nodes || []) {
    if (node.NodeType !== 1 || node.SubType !== 2) continue
    for (const item of node.Data.Lines?.Items || []) {
      const value = item.ObjectValue || {}
      const text = value.Text?.StringValue || ''
      if (text) lines.push({ speaker: value.SpeakerId?.StringValue || '旁白', text })
    }
  }
  return lines
}

function makeBeats(lines) {
  const beats = []
  lines.forEach((line, sourceIndex) => {
    let quotedSpeaker = ''
    let quoteDepth = 0
    for (const text of splitText(line.text)) {
      const explicitSpeaker = line.speaker === '诗赞' ? '诗赞' : spoken(text)
      const speaker = explicitSpeaker || (quoteDepth > 0 && quotedSpeaker ? quotedSpeaker : line.speaker)
      beats.push({ speaker, text, sourceIndex })
      if (explicitSpeaker && explicitSpeaker !== '诗赞') quotedSpeaker = explicitSpeaker
      const opens = (text.match(/[“「]/g) || []).length
      const closes = (text.match(/[”」]/g) || []).length
      quoteDepth = Math.max(0, quoteDepth + opens - closes)
      if (quoteDepth === 0 && closes > 0) quotedSpeaker = ''
    }
  })
  return beats
}

function castForBeat(beat) {
  const found = []
  const speaker = canonical(beat.speaker)
  if (speaker) found.push(speaker)
  for (const [name, values] of Object.entries(aliases)) {
    if (found.includes(name)) continue
    if (values.some((value) => new RegExp(`${value}[^。！？；]{0,8}${actionVerb}`).test(beat.text))) found.push(name)
    if (found.length >= 2) break
  }
  return found.slice(0, 2)
}

function expressionFor(beat, character) {
  let context = beat.text
  if (canonical(beat.speaker) !== character) {
    const match = (aliases[character] || [character])
      .map((value) => ({ value, index: beat.text.indexOf(value) }))
      .find((item) => item.index >= 0)
    if (match) context = beat.text.slice(Math.max(0, match.index - 6), match.index + match.value.length + 34)
  }
  return expressionRules.find(([pattern]) => pattern.test(context))?.[1] || 'neutral'
}

function backgroundFor(beats, index) {
  const beat = beats[index]
  const direct = backgroundRules.find(([pattern]) => pattern.test(beat.text))?.[1]
  if (direct) return direct
  for (let offset = 1; offset <= 3; offset += 1) {
    const previous = beats[index - offset]
    if (!previous || previous.sourceIndex !== beat.sourceIndex) break
    const inherited = backgroundRules.find(([pattern]) => pattern.test(previous.text))?.[1]
    if (inherited) return inherited
  }
  return 'courtyard'
}

function hash(value) {
  let result = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index)
    result = Math.imul(result, 16777619)
  }
  return result >>> 0
}

function topEntries(map, count = 12) {
  return [...map.entries()].sort((a, b) => b[1] - a[1]).slice(0, count)
}

const files = fs.readdirSync(path.join(root, 'chapters'))
  .filter((name) => !name.startsWith('.') && name.endsWith('.json'))
  .sort()
const totals = {
  chapters: files.length,
  lines: 0,
  beats: 0,
  portraitBeats: 0,
  semanticExpressionBeats: 0,
  softVariations: 0,
  exactRepeatFromPrevious: 0,
  backgroundRepeatFromPrevious: 0,
  exactRepeatRuns4Plus: 0,
  backgroundRuns8Plus: 0,
}
const backgrounds = new Map()
const expressions = new Map()
const signatures = new Map()
let longestSignatureRun = { length: 0 }
let longestBackgroundRun = { length: 0 }
const chapterSummaries = []

for (const file of files) {
  const chapter = Number(file.slice(0, 3))
  const graph = JSON.parse(fs.readFileSync(path.join(root, 'chapters', file), 'utf8'))
  const lines = readLines(graph)
  const beats = makeBeats(lines)
  totals.lines += lines.length
  totals.beats += beats.length
  let signatureRun = 0
  let backgroundRun = 0
  let previousSignature = ''
  let previousBackground = ''
  let chapterSoft = 0
  let chapterPortraits = 0

  for (let index = 0; index < beats.length; index += 1) {
    const cast = castForBeat(beats[index])
    const variants = cast.map((name) => `${name}:${expressionFor(beats[index], name)}`)
    const background = backgroundFor(beats, index)
    const signature = `${background}|${variants.join(',')}`
    backgrounds.set(background, (backgrounds.get(background) || 0) + 1)
    signatures.set(signature, (signatures.get(signature) || 0) + 1)
    if (cast.length) {
      totals.portraitBeats += 1
      chapterPortraits += 1
    }
    if (variants.some((value) => !value.endsWith(':neutral'))) totals.semanticExpressionBeats += 1
    for (const value of variants) expressions.set(value, (expressions.get(value) || 0) + 1)

    signatureRun = signature === previousSignature ? signatureRun + 1 : 1
    backgroundRun = background === previousBackground ? backgroundRun + 1 : 1
    if (index > 0 && signature === previousSignature) totals.exactRepeatFromPrevious += 1
    if (index > 0 && background === previousBackground) totals.backgroundRepeatFromPrevious += 1
    if (signatureRun === 4) totals.exactRepeatRuns4Plus += 1
    if (backgroundRun === 8) totals.backgroundRuns8Plus += 1
    if (signatureRun > longestSignatureRun.length) longestSignatureRun = { length: signatureRun, chapter, start: index - signatureRun + 1, end: index, signature }
    if (backgroundRun > longestBackgroundRun.length) longestBackgroundRun = { length: backgroundRun, chapter, start: index - backgroundRun + 1, end: index, background }

    const recent = Array.from({ length: Math.min(3, index) }, (_, offset) => index - offset - 1)
    const exactRepeats = recent.filter((candidate) => {
      const candidateCast = castForBeat(beats[candidate]).map((name) => `${name}:${expressionFor(beats[candidate], name)}`)
      return `${backgroundFor(beats, candidate)}|${candidateCast.join(',')}` === signature
    }).length
    const backgroundRepeats = recent.filter((candidate) => backgroundFor(beats, candidate) === background).length
    let backgroundRunDepth = 1
    for (let candidate = index - 1; candidate >= 0 && backgroundRunDepth < 12; candidate -= 1) {
      if (backgroundFor(beats, candidate) !== background) break
      backgroundRunDepth += 1
    }
    const stableHash = hash(`${chapter}:${index}:${signature}`)
    const softVariation = exactRepeats >= 2
      || (exactRepeats === 1 && stableHash % 5 === 0)
      || (backgroundRepeats === 3 && stableHash % 3 === 0)
      || (backgroundRunDepth >= 5 && backgroundRunDepth % 2 === 0)
    if (softVariation) {
      totals.softVariations += 1
      chapterSoft += 1
    }
    previousSignature = signature
    previousBackground = background
  }

  chapterSummaries.push({ chapter, beats: beats.length, portraitPct: +(chapterPortraits / beats.length * 100).toFixed(1), softVariationPct: +(chapterSoft / beats.length * 100).toFixed(1) })
}

const pct = (value) => +(value / totals.beats * 100).toFixed(1)
console.log(JSON.stringify({
  totals: {
    ...totals,
    portraitBeatPct: pct(totals.portraitBeats),
    noPortraitBeatPct: +(100 - pct(totals.portraitBeats)).toFixed(1),
    semanticExpressionBeatPct: pct(totals.semanticExpressionBeats),
    softVariationPct: pct(totals.softVariations),
    exactRepeatFromPreviousPct: pct(totals.exactRepeatFromPrevious),
    backgroundRepeatFromPreviousPct: pct(totals.backgroundRepeatFromPrevious),
    uniqueSignatures: signatures.size,
  },
  longestSignatureRun,
  longestBackgroundRun,
  backgroundUsage: topEntries(backgrounds),
  portraitVariantUsage: topEntries(expressions, 24),
  chaptersWithLowestPortraitPct: [...chapterSummaries].sort((a, b) => a.portraitPct - b.portraitPct).slice(0, 8),
  chaptersWithHighestExactMitigation: [...chapterSummaries].sort((a, b) => b.softVariationPct - a.softVariationPct).slice(0, 8),
}, null, 2))
