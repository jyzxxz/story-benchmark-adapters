export interface SemanticVisualBeat {
  speaker: string
  text: string
  direction?: string
  sourceIndex: number
  sourceBeatIndex: number
  source: string
}

export interface SemanticBackgroundPlanItem {
  backgroundKey: string
  sceneAnchorIndex: number
  explicit: boolean
  cue: string
  timeOfDay: 'day' | 'dawn' | 'dusk' | 'night' | 'interior'
  mood: 'calm' | 'tense' | 'battle' | 'storm' | 'fire' | 'sorrow' | 'ceremony'
  sceneTake: 0 | 1 | 2
}

interface BackgroundCandidate {
  backgroundKey: string
  priority: number
  cue: string
}

interface BackgroundRule {
  pattern: RegExp
  backgroundKey: string
  priority: number
  cue: string
}

/*
 * Rules describe visual locations, not story plots. A precise physical place
 * always outranks generic political or military vocabulary. This prevents a
 * sentence mentioning "the emperor" inside a cave or battlefield from being
 * rendered as the palace.
 */
const BACKGROUND_RULES: BackgroundRule[] = [
  {
    pattern: /桃园|桃林|桃花盛开|桃树/,
    backgroundKey: 'bg_three_kingdoms_peach_orchard',
    priority: 150,
    cue: 'peach-orchard',
  },
  {
    pattern: /草庐|茅庐|茅屋|农舍|楼桑村|桑树下/,
    backgroundKey: 'bg_three_kingdoms_rural_village',
    priority: 145,
    cue: 'rural-home',
  },
  {
    pattern: /山洞|洞中|石洞|石室|洞口|洞穴/,
    backgroundKey: 'bg_three_kingdoms_cave',
    priority: 145,
    cue: 'mountain-cave',
  },
  {
    pattern: /温德殿|金殿|宫殿|朝堂|御座|宫中|禁中|宫门|玉堂|内廷/,
    backgroundKey: 'bg_historical_palace_hall',
    priority: 140,
    cue: 'imperial-palace',
  },
  {
    pattern: /中军帐|军帐|大帐|帐中|帐内|入帐|幕府|帅帐/,
    backgroundKey: 'bg_three_kingdoms_command_tent',
    priority: 140,
    cue: 'command-tent',
  },
  {
    pattern: /村店|客店|酒店|酒肆|店中|店门|客栈|驿馆|旅店/,
    backgroundKey: 'bg_historical_inn_hall',
    priority: 140,
    cue: 'roadside-inn',
  },
  {
    pattern: /牢中|牢狱|监牢|狱中|囚室|下狱|槛车/,
    backgroundKey: 'bg_historical_prison',
    priority: 138,
    cue: 'prison',
  },
  {
    pattern: /公堂|衙门|官府|县衙|堂上|堂下|审案|太守府/,
    backgroundKey: 'bg_historical_courtroom_yamen',
    priority: 136,
    cue: 'government-office',
  },
  {
    pattern: /祖庙|宗庙|祠堂|寺院|寺中|庙中|道观|祭坛|祭告天地|焚香祭告/,
    backgroundKey: 'bg_three_kingdoms_temple_courtyard',
    priority: 135,
    cue: 'temple',
  },
  {
    pattern: /书房|书斋|竹简|书案|奏章|密信|天书|地图前/,
    backgroundKey: 'bg_historical_study',
    priority: 132,
    cue: 'study',
  },
  {
    pattern: /卧房|寝宫|寝室|房中|房内|内室|榻上|床前|更衣/,
    backgroundKey: 'bg_historical_inn_room',
    priority: 130,
    cue: 'private-room',
  },
  {
    pattern: /厨房|灶房|灶台|炊烟|做饭|烹煮|备膳/,
    backgroundKey: 'bg_historical_kitchen',
    priority: 128,
    cue: 'kitchen',
  },
  {
    pattern: /花园|后园|园中|园内|庭园|亭中|池边|竹园/,
    backgroundKey: 'bg_historical_garden',
    priority: 128,
    cue: 'garden',
  },
  {
    pattern: /庄上|庄园|府邸|宅院|宅中|家中|院中|庭院|大院/,
    backgroundKey: 'bg_historical_courtyard',
    priority: 125,
    cue: 'residence-courtyard',
  },
  {
    pattern: /水寨|水军|战船|楼船|江面|河面|江口|渡口|码头|岸边|江岸|河岸|赤壁|舟中|船上/,
    backgroundKey: 'bg_three_kingdoms_riverbank',
    priority: 124,
    cue: 'military-riverbank',
  },
  {
    pattern: /桥上|桥边|过桥|木桥|浮桥|河桥/,
    backgroundKey: 'bg_historical_bridge_river',
    priority: 122,
    cue: 'river-crossing',
  },
  {
    pattern: /山岭|山谷|山下|山上|高冈|山冈|山坡|峰顶|悬崖|崖边|关隘|隘口|栈道|谷口/,
    backgroundKey: 'bg_three_kingdoms_mountain_pass',
    priority: 122,
    cue: 'mountain-pass',
  },
  {
    pattern: /密林|树林|林中|松林|竹林|森林|樵径|林间|木道/,
    backgroundKey: 'bg_three_kingdoms_forest_path',
    priority: 120,
    cue: 'forest-road',
  },
  {
    pattern: /田野|田间|乡野|村庄|村中|乡村|农田|麦田|稻田|井边/,
    backgroundKey: 'bg_three_kingdoms_rural_village',
    priority: 118,
    cue: 'rural-village',
  },
  {
    pattern: /营寨|军营|扎营|下寨|寨中|营中|辕门|大营|营垒|贼寨|长社/,
    backgroundKey: 'bg_three_kingdoms_fortress_camp',
    priority: 118,
    cue: 'army-camp',
  },
  {
    pattern: /城门|城楼|城下|城墙|城池|城郭|围城|攻城|守城|城头/,
    backgroundKey: 'bg_historical_city_gate',
    priority: 116,
    cue: 'fortified-city',
  },
  {
    pattern: /宴会|筵席|酒宴|设宴|摆酒|大宴|宴请|酒席|饮宴/,
    backgroundKey: 'bg_three_kingdoms_banquet_hall',
    priority: 114,
    cue: 'banquet',
  },
  {
    pattern: /榜文|告示|市镇|市集|街市|街上|县城|城中|洛阳城|长安城|许都|涿县/,
    backgroundKey: 'bg_historical_street_day',
    priority: 112,
    cue: 'city-street',
  },
  {
    pattern: /两军相对|阵前|战场|出阵|出战|迎战|交战|交锋|厮杀|大战|混战|冲杀|伏兵|埋伏|追杀|火攻|败阵|连夜起兵|发动起义|举兵造反|放火抢劫|喊杀|火光烛天/,
    backgroundKey: 'bg_historical_battlefield',
    priority: 108,
    cue: 'battlefield',
  },
  {
    pattern: /赶路|途中|半路|路上|官道|驿道|北行|南下|东进|西行|连夜赶|投.*而来/,
    backgroundKey: 'bg_three_kingdoms_forest_path',
    priority: 92,
    cue: 'travel-road',
  },
  {
    pattern: /发布诏令|皇帝召|召集百官|朝廷议事|上朝|退朝|天子召见/,
    backgroundKey: 'bg_historical_palace_hall',
    priority: 90,
    cue: 'court-session',
  },
  {
    pattern: /挥军|率军|领兵|军队|兵马|士兵|叛军|官军|讨伐|进犯|追击|战败|大获全胜/,
    backgroundKey: 'bg_historical_battlefield',
    priority: 78,
    cue: 'military-action',
  },
  {
    pattern: /皇帝|天子|百官|宦官|朝政|朝廷|大臣|上奏|诏书/,
    backgroundKey: 'bg_historical_palace_hall',
    priority: 54,
    cue: 'court-politics',
  },
]

const SCENE_TRANSITION_PATTERN = /^(?:却说|且说|再说|话说|第二天|次日|当日|当天|当夜|次夜|翌日|后来|随后|不久|没过几天|到了|来到|回到|返回|行至|忽见|忽闻|转眼|另一边)/
const NIGHT_PATTERN = /夜|半夜|二更|三更|月色|月光|星光|更鼓|宵禁/
const DAWN_PATTERN = /黎明|拂晓|破晓|天刚亮|东方发白/
const DUSK_PATTERN = /黄昏|傍晚|日暮|夕阳|暮色/
const INTERIOR_PATTERN = /殿|堂|帐|房|室|店中|狱|衙|书房|宫中|厅|庙中|祠堂/

const moodForText = (text: string): SemanticBackgroundPlanItem['mood'] => {
  if (/雷|暴雨|狂风|冰雹|风雪|大雪|洪水|巨浪|地震|山崩/.test(text)) return 'storm'
  if (/纵火|火攻|火焰|火光|焚烧|大火|烧毁/.test(text)) return 'fire'
  if (/战|杀|攻|守|兵|军|阵|追|围城|埋伏|刀|枪|箭/.test(text)) return 'battle'
  if (/祭|誓|拜天地|登基|即位|大典|婚礼|丧礼/.test(text)) return 'ceremony'
  if (/哭|泣|死|亡|败|忧|悲|哀|诀别/.test(text)) return 'sorrow'
  if (/惊|怒|恐|密谋|告密|危|逃|囚|叛/.test(text)) return 'tense'
  return 'calm'
}

const timeForText = (
  text: string,
  backgroundKey: string,
): SemanticBackgroundPlanItem['timeOfDay'] => {
  if (DAWN_PATTERN.test(text)) return 'dawn'
  if (DUSK_PATTERN.test(text)) return 'dusk'
  if (NIGHT_PATTERN.test(text)) return 'night'
  if (INTERIOR_PATTERN.test(text) || /hall|room|tent|prison|study|kitchen|yamen/.test(backgroundKey)) {
    return 'interior'
  }
  return 'day'
}

const candidateForText = (text: string): BackgroundCandidate | null => {
  let best: BackgroundCandidate | null = null
  for (const rule of BACKGROUND_RULES) {
    const match = rule.pattern.exec(text)
    if (!match) continue
    const specificityBonus = Math.min(8, match[0].length)
    const candidate = {
      backgroundKey: rule.backgroundKey,
      priority: rule.priority + specificityBonus,
      cue: rule.cue,
    }
    if (!best || candidate.priority > best.priority) best = candidate
  }
  return best
}

const candidateForBeat = (beat: SemanticVisualBeat): BackgroundCandidate | null => {
  const textCandidate = candidateForText(beat.text)
  if (beat.source === 'chapter' || !beat.direction) return textCandidate
  const directionCandidate = candidateForText(beat.direction)
  if (!textCandidate) return directionCandidate
  if (!directionCandidate) return textCandidate
  // The concrete current beat wins once it contains a strong location/event.
  // The user's direction only resolves an otherwise generic or ambiguous beat.
  if (textCandidate.priority >= 100) return textCandidate
  return directionCandidate.priority > textCandidate.priority
    ? directionCandidate
    : textCandidate
}

const defaultCandidateForText = (text: string): BackgroundCandidate => {
  if (NIGHT_PATTERN.test(text)) {
    return {
      backgroundKey: 'bg_historical_street_night',
      priority: 20,
      cue: 'night-fallback',
    }
  }
  if (/野外|荒野|平原|原野|天地|天下形势/.test(text)) {
    return {
      backgroundKey: 'bg_historical_battlefield',
      priority: 18,
      cue: 'open-land-fallback',
    }
  }
  return {
    backgroundKey: 'bg_historical_courtyard',
    priority: 10,
    cue: 'historical-fallback',
  }
}

/**
 * Build one deterministic visual plan for the entire chapter. The plan keeps
 * locations through dialogue, but starts a new content scene when the source
 * paragraph or a real narrative transition changes.
 */
export const buildSemanticBackgroundPlan = (
  beats: SemanticVisualBeat[],
): SemanticBackgroundPlanItem[] => {
  if (!beats.length) return []

  const directCandidates = beats.map(candidateForBeat)
  const bestBySource = new Map<number, BackgroundCandidate>()
  directCandidates.forEach((candidate, index) => {
    if (!candidate) return
    const sourceIndex = beats[index].sourceIndex
    const existing = bestBySource.get(sourceIndex)
    if (!existing || candidate.priority > existing.priority) bestBySource.set(sourceIndex, candidate)
  })

  const plan: SemanticBackgroundPlanItem[] = []
  let activeCandidate = defaultCandidateForText(beats[0].text)
  let activeSourceIndex = -1
  let sceneAnchorIndex = 0
  let sceneTake: 0 | 1 | 2 = 0
  const sceneSignatureCounts = new Map<string, number>()

  beats.forEach((beat, index) => {
    const direct = directCandidates[index]
    const sourceBest = bestBySource.get(beat.sourceIndex)
    const sourceChanged = beat.sourceIndex !== activeSourceIndex
    const isVerse = beat.speaker === '诗赞'
    const hasTransition = SCENE_TRANSITION_PATTERN.test(beat.text.trim())

    let selected = activeCandidate
    if (direct && direct.priority >= 100) {
      selected = direct
    } else if (direct && (sourceChanged || hasTransition || direct.priority >= 82)) {
      selected = direct
    } else if (sourceChanged && sourceBest && sourceBest.priority >= 100 && !isVerse) {
      selected = sourceBest
    } else if (sourceChanged && direct && !isVerse) {
      selected = direct
    } else if (index === 0) {
      selected = direct || sourceBest || defaultCandidateForText(beat.text)
    }

    if (NIGHT_PATTERN.test(beat.text) && selected.backgroundKey === 'bg_historical_street_day') {
      selected = {
        ...selected,
        backgroundKey: 'bg_historical_street_night',
        cue: `${selected.cue}-night`,
      }
    }

    const keyChanged = selected.backgroundKey !== activeCandidate.backgroundKey
    const startsContentScene = index === 0
      || keyChanged
      || (!isVerse && sourceChanged)
      || (!isVerse && hasTransition)
    const textTime = timeForText(beat.text, selected.backgroundKey)
    const directionTime = beat.direction
      ? timeForText(beat.direction, selected.backgroundKey)
      : textTime
    const timeOfDay = beat.source !== 'chapter'
      && textTime === 'day'
      && directionTime !== 'day'
      ? directionTime
      : textTime
    const textMood = moodForText(beat.text)
    const mood = beat.source !== 'chapter'
      && textMood === 'calm'
      && beat.direction
      ? moodForText(beat.direction)
      : textMood
    if (startsContentScene) {
      sceneAnchorIndex = index
      const signature = [
        selected.backgroundKey,
        selected.cue,
        timeOfDay,
        mood,
      ].join('|')
      const occurrence = sceneSignatureCounts.get(signature) || 0
      sceneTake = (occurrence % 3) as 0 | 1 | 2
      sceneSignatureCounts.set(signature, occurrence + 1)
    }

    activeCandidate = selected
    activeSourceIndex = beat.sourceIndex
    plan.push({
      backgroundKey: selected.backgroundKey,
      sceneAnchorIndex,
      explicit: Boolean(direct),
      cue: selected.cue,
      timeOfDay,
      mood,
      sceneTake,
    })
  })

  return plan
}

export const semanticSceneRequestKey = (
  plan: SemanticBackgroundPlanItem,
  beat: SemanticVisualBeat,
) => [
  'semantic-scene',
  `source-${beat.sourceIndex}`,
  `beat-${beat.sourceBeatIndex}`,
  plan.backgroundKey,
  plan.cue,
  plan.timeOfDay,
  plan.mood,
  `take-${plan.sceneTake}`,
].join(':')
