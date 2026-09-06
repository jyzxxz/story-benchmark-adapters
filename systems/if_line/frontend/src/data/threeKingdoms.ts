export type StorySection = 'overview' | 'bible' | 'characters' | 'timeline' | 'chapters' | 'vn' | 'reader'

export interface ChapterCatalogItem {
  number: number
  title: string
  file: string
  paragraphCount: number
  characterCount: number
  excerpt: string
  arc: number
}

export interface ChapterCatalog {
  title: string
  author: string
  chapterCount: number
  format: string
  chapters: ChapterCatalogItem[]
}

export interface VNSerializedValue {
  Kind: string
  StringValue?: string
  NumberValue?: number
  BoolValue?: boolean
  Items?: VNSerializedValue[]
  ObjectValue?: Record<string, VNSerializedValue>
  // Vector2 / Vector3 / Color fields. VNNodeLibrary.txt §二:
  // Vector2 uses X/Y; Vector3 uses X/Y/Z; Color uses X/Y/Z/W (RGBA).
  X?: number
  Y?: number
  Z?: number
  W?: number
}

export interface VNNode {
  Index: number
  DisplayName: string
  Comment: string
  NodeType: number
  SubType: number
  X: number
  Y: number
  Data: Record<string, VNSerializedValue>
  Outputs: Record<string, number[]>
}

export interface VNGraph {
  Version: number
  StartNodeIndex: number
  Nodes: VNNode[]
}

export const storyMeta = {
  title: '三国演义',
  subtitle: '一部关于秩序崩解、英雄选择与天下归一的章回史诗',
  author: '〔明〕罗贯中',
  edition: '正文采用《三国演义（纯白话易读版）》',
  logline: '东汉末年，旧秩序在战乱中崩塌；曹操、刘备、孙权及其文臣武将以不同道路争夺天下，最终三分归晋，留下忠义、权谋与兴亡的漫长回声。',
  synopsis: '故事从黄巾起义与桃园结义写起，历经董卓乱政、群雄逐鹿、官渡决战、三顾茅庐、赤壁鏖兵、三国鼎立、夷陵之战、诸葛亮北伐，直至司马氏代魏、蜀吴相继覆亡。它既写庙堂与战场，也写个人在时代洪流中的承诺、欲望、判断与代价。',
  publicNotice: '本页面无需登录。Story Bible、人物势力、十二幕年表、120 回目录、正文及每回 VN 节点 JSON 均为公开只读内容。',
}

export const themes = [
  { title: '天下大势', text: '分合不是背景板，而是推动每个角色选择的最高层力量；任何局部胜利都要放回秩序兴亡中衡量。' },
  { title: '忠义与代价', text: '誓言能凝聚共同体，也会把人物推向无法回头的选择。义不是口号，而是持续承担后果。' },
  { title: '权谋与人心', text: '计策只有在读懂人性、地势、时机与组织能力时才成立；聪明不等于必胜。' },
  { title: '天命与人事', text: '作品常借天象、谶言与神异谈天命，但真正改变局势的仍是人的判断、制度与行动。' },
  { title: '英雄的有限性', text: '几乎所有英雄都有鲜明长处和致命局限；人物魅力来自矛盾，而非单一善恶标签。' },
  { title: '成败兴亡', text: '胜负不断改写名分与叙事，最终却共同归入历史；结尾要保留“浪花淘尽英雄”的苍凉感。' },
]

export const bibleSections = [
  {
    title: '世界与时代',
    kicker: 'Worldview',
    items: [
      ['时间范围', '东汉末年至西晋统一，约公元 184—280 年。叙事允许章回小说的艺术虚构，但重大时代顺序保持清楚。'],
      ['地理舞台', '洛阳、长安、中原、河北、荆州、江东、益州、汉中、陇右与南中构成战略网络；江河、关隘、粮道和城池直接决定行动。'],
      ['权力规则', '皇权名分、州郡治理、世族关系、军队控制与粮草组织共同构成政治实力。挟天子、称王、受禅都必须有名分叙事。'],
      ['战争规则', '胜负由情报、联盟、后勤、地形、军心与将帅判断共同决定。个人武勇可以改变战术节点，却不能替代战略。'],
      ['神异尺度', '梦兆、天象、道术与显圣以古典叙事方式出现，既营造宿命感，也映照人物心理；不把它们改写成现代超能力系统。'],
    ],
  },
  {
    title: '核心冲突',
    kicker: 'Conflict',
    items: [
      ['宏观冲突', '汉室权威衰亡后，谁能重建可被承认的天下秩序。'],
      ['政治冲突', '名分与实力长期错位：拥汉、代汉、割据与统一分别争夺正当性。'],
      ['人物冲突', '忠于人、忠于理想、忠于政权往往不能同时成立，人物必须在承诺之间做选择。'],
      ['叙事张力', '读者知道“三分归晋”的结局，却仍被一次次可能改变历史的决策吸引。'],
    ],
  },
  {
    title: '情感主线',
    kicker: 'Emotional line',
    items: [
      ['桃园之义', '刘备、关羽、张飞以兄弟誓言建立全书最具辨识度的情感支点，兴衰皆与此相连。'],
      ['君臣知遇', '刘备与诸葛亮、曹操与谋臣集团、孙权与江东群臣呈现不同的信任结构。'],
      ['托孤与继承', '英雄离场后，理想能否被下一代和制度承接，成为后半部的持续追问。'],
      ['故国与归属', '流离、降服、易主与守节反复出现，使“我为谁而战”成为人物最深层的问题。'],
    ],
  },
  {
    title: '叙事与文风',
    kicker: 'Style rules',
    items: [
      ['叙述声音', '保留章回体全知叙述的简洁推进感，用“且说”“却说”自然切换战场与阵营。'],
      ['场面节奏', '大战前先交代局势与计策，临阵用短句加速，战后回到政治后果，避免只有动作没有因果。'],
      ['人物对白', '曹操敏锐果决，刘备克制含情，诸葛亮从容缜密，张飞直烈，关羽简傲，孙权善于权衡。'],
      ['章回钩子', '每回以局势未决、人物将遇或计策将发收束，让下一回承接悬念。'],
      ['诗赞功能', '诗词不是装饰，用于评论人物、压缩历史距离、制造兴亡回望。'],
    ],
  },
]

export const adaptationGuardrails = [
  '不把复杂人物压扁成“纯正派/纯反派”；同一行为同时呈现动机、收益与代价。',
  '不混淆《三国演义》的文学叙事与《三国志》等史料事实；需要说明时标注“演义叙事”。',
  '不随意改变重大事件顺序、人物生卒与势力消长，以免破坏长线因果。',
  '玩家选择用于观察立场、补充支线或改变演出顺序，不伪装成已被原著支持的历史结局。',
  '不使用现代网络语破坏时代氛围；必要的易读化以简洁释义完成。',
  '每个 VN 场景都要回答：谁想要什么、谁在阻止、局势因这一场发生了什么变化。',
]

export const factions = [
  { id: 'han', name: '汉室与朝廷', color: '#b68a35', summary: '名分仍是天下共用的政治语言，但中央控制力不断流失。', leaders: '汉献帝、何进、王允' },
  { id: 'wei', name: '曹魏', color: '#7f3035', summary: '以中原、制度与高效用人建立最强国家机器，最终权柄转入司马氏。', leaders: '曹操、曹丕、司马懿' },
  { id: 'shu', name: '蜀汉', color: '#3f6b4f', summary: '以复兴汉室为政治理想，从流离集团成长为据有益州的政权。', leaders: '刘备、诸葛亮、姜维' },
  { id: 'wu', name: '东吴', color: '#315f78', summary: '凭江东根基与长江天险立国，在守成、联盟与扩张之间求取空间。', leaders: '孙策、孙权、周瑜、陆逊' },
  { id: 'lords', name: '群雄', color: '#725b48', summary: '董卓、吕布、袁绍、袁术、刘表、马超等共同塑造三国形成前的多极乱局。', leaders: '董卓、吕布、袁绍' },
  { id: 'jin', name: '司马晋', color: '#575b66', summary: '在魏国制度内部累积权力，先代魏，再灭蜀、吴，完成形式上的统一。', leaders: '司马懿、司马昭、司马炎' },
]

export const characters = [
  { name: '刘备', courtesy: '字玄德', faction: 'shu', vnId: 'liu_bei', role: '仁德之主／蜀汉开创者', traits: '坚韧、善结人心、以汉室名分自任', desire: '在乱世中建立可以安民的秩序并延续汉统', contradiction: '仁德理想与生存扩张之间不断冲突', arc: '从织席贩履的流亡者成为昭烈帝，又因兄弟之仇在夷陵遭受重挫。' },
  { name: '关羽', courtesy: '字云长', faction: 'shu', vnId: 'guan_yu', role: '名将／桃园兄弟', traits: '忠义、骄傲、重然诺、威严', desire: '守住与刘备的誓言和个人荣誉', contradiction: '无可置疑的勇名与对同盟、部属的轻慢并存', arc: '从千里寻兄到威震华夏，最终在荆州的战略孤立中败走麦城。' },
  { name: '张飞', courtesy: '字益德', faction: 'shu', vnId: 'zhang_fei', role: '猛将／桃园兄弟', traits: '勇烈、率直、敬君子而轻士卒', desire: '以武力保护兄长并建立功业', contradiction: '战场上的敏锐与日常治军的暴烈形成反差', arc: '从怒鞭督邮到义释严颜，最终因急于复仇而死于部下之手。' },
  { name: '诸葛亮', courtesy: '字孔明', faction: 'shu', vnId: 'zhuge_liang', role: '军师／蜀汉丞相', traits: '缜密、克己、远见、责任感极强', desire: '实践隆中对，辅佐汉室恢复中原', contradiction: '超凡规划能力受限于国力、人才与时间', arc: '由隆中隐士成为托孤重臣，以六出祁山和五丈原之逝完成“鞠躬尽瘁”的弧线。' },
  { name: '赵云', courtesy: '字子龙', faction: 'shu', vnId: 'zhao_yun', role: '亲卫名将', traits: '勇而有节、冷静、忠诚、少有私欲', desire: '保护主君与百姓，完成职责', contradiction: '个人近乎完美，却常处于辅助位置', arc: '从长坂救主到汉水拒敌，以稳定可靠贯穿蜀汉早中期。' },
  { name: '庞统', courtesy: '字士元', faction: 'shu', vnId: 'pang_tong', role: '谋士／凤雏', traits: '旷达、敏锐、不拘形迹', desire: '让才能在夺取益州的关键事业中被证明', contradiction: '战略眼光超前，生命却在事业展开之初中断', arc: '由耒阳小吏转为刘备军师，最终在雒城攻势中身亡。' },
  { name: '姜维', courtesy: '字伯约', faction: 'shu', vnId: 'jiang_wei', role: '后期统帅／诸葛亮继志者', traits: '好学、坚毅、孤注一掷', desire: '延续北伐、保存蜀汉最后的主动权', contradiction: '忠于理想，却持续消耗已衰弱的国家', arc: '从魏将归蜀到国亡后的最后布局，成为“知其不可而为之”的尾声人物。' },
  { name: '曹操', courtesy: '字孟德', faction: 'wei', vnId: 'cao_cao', role: '政治家／军事家／曹魏奠基者', traits: '果决、多疑、识人、务实、富文学气质', desire: '在崩坏秩序中以自己的方法重建统一', contradiction: '能容天下之才，却难完全摆脱控制与猜疑', arc: '从献刀与起兵的行动者成长为魏王；统一北方，却未能跨过长江。' },
  { name: '郭嘉', courtesy: '字奉孝', faction: 'wei', vnId: 'guo_jia', role: '战略谋士', traits: '洞察人性、敢断、善把握时机', desire: '以判断帮助曹操消灭北方主要对手', contradiction: '智略锐利但生命短暂', arc: '在官渡前后屡定大局，遗计平辽东后成为曹操反复追忆的缺席者。' },
  { name: '荀彧', courtesy: '字文若', faction: 'wei', vnId: 'xun_yu', role: '王佐之才／内政核心', traits: '端方、深谋、重汉室名分', desire: '借曹操之力匡扶汉室、平定天下', contradiction: '辅佐的权力中心最终走向他无法接受的方向', arc: '从迎奉天子、经营后方到与曹操的政治目标分裂，体现名分与现实的裂缝。' },
  { name: '司马懿', courtesy: '字仲达', faction: 'jin', vnId: 'sima_yi', role: '魏国重臣／晋室权力奠基者', traits: '隐忍、审慎、善守、长于等待', desire: '保存家族并在权力结构中取得最终主动', contradiction: '常以退让示人，却拥有最持久的政治意志', arc: '从与诸葛亮长期对峙到高平陵政变，为司马氏代魏打开道路。' },
  { name: '曹丕', courtesy: '字子桓', faction: 'wei', vnId: 'cao_pi', role: '魏文帝', traits: '敏感、精于权力、文学修养深', desire: '赢得继承并把曹氏实力转化为皇位', contradiction: '建立新朝的理性与亲族猜忌并存', arc: '在世子之争中胜出，完成汉魏禅代，也开启新的正统争议。' },
  { name: '孙策', courtesy: '字伯符', faction: 'wu', vnId: 'sun_ce', role: '江东开拓者／小霸王', traits: '果敢、爽烈、善于迅速聚众', desire: '以父辈遗志开创江东基业', contradiction: '扩张速度惊人，政治安全却十分脆弱', arc: '短期席卷江东，在盛势中早逝，把尚未稳固的事业交给孙权。' },
  { name: '孙权', courtesy: '字仲谋', faction: 'wu', vnId: 'sun_quan', role: '东吴君主', traits: '善权衡、能纳谏、务实、后期多疑', desire: '守住江东并在魏蜀之间取得独立地位', contradiction: '长于平衡群臣与联盟，晚年却陷入继承内耗', arc: '从青年守成者到称帝，在赤壁、合肥与夷陵之间塑造东吴生存之道。' },
  { name: '周瑜', courtesy: '字公瑾', faction: 'wu', vnId: 'zhou_yu', role: '水军统帅／赤壁主将', traits: '英锐、雅量、果断、善统筹', desire: '击退曹操并为江东争取战略主动', contradiction: '宏大进取计划受生命长度与联盟牵制', arc: '以赤壁奠定三分格局，后在谋取益州前病逝。' },
  { name: '鲁肃', courtesy: '字子敬', faction: 'wu', vnId: 'lu_su', role: '战略家／联盟维护者', traits: '宽厚、务实、目光长远', desire: '以孙刘联盟抗衡北方强权', contradiction: '联盟符合大局，却长期承受荆州利益冲突', arc: '从榻上策到单刀会，持续为脆弱的战略合作争取时间。' },
  { name: '陆逊', courtesy: '字伯言', faction: 'wu', vnId: 'lu_xun', role: '东吴统帅', traits: '沉着、善忍、精于后发制人', desire: '保卫江东并证明年轻统帅的判断', contradiction: '军事上极有耐心，政治上仍受君权猜忌', arc: '以夷陵火攻击破刘备，晚年卷入东吴继承风波。' },
  { name: '吕布', courtesy: '字奉先', faction: 'lords', vnId: 'lv_bu', role: '无双猛将／反复易主者', traits: '勇武、冲动、重眼前利益、缺乏稳定判断', desire: '凭个人武力获得地位与安全', contradiction: '战术上几乎无人可当，政治信用却持续破产', arc: '从诛董卓到据徐州，最终在白门楼为自己反复无常的选择付出代价。' },
  { name: '貂蝉', courtesy: '演义人物', faction: 'han', vnId: 'diao_chan', role: '连环计核心人物', traits: '克制、勇敢、善于隐藏真实情绪', desire: '完成王允托付、除去董卓', contradiction: '个人情感被卷入国家与权谋的巨大目标', arc: '以自身处境撬动董卓与吕布关系，是少数直接改变天下局势的女性角色。' },
  { name: '董卓', courtesy: '字仲颖', faction: 'lords', vnId: 'dong_zhuo', role: '权臣／乱政者', traits: '强横、残暴、多疑、依赖武力', desire: '控制朝廷并将天下资源据为己有', contradiction: '军事威慑巨大，统治却缺少可持续的认同', arc: '由入京掌权、废立天子走向迁都与暴政，最终死于连环计。' },
  { name: '袁绍', courtesy: '字本初', faction: 'lords', vnId: 'yuan_shao', role: '河北霸主', traits: '门望高、能聚众、多谋少决、外宽内忌', desire: '凭家世与兵力成为北方共主', contradiction: '资源压倒性占优，决策体系却反复内耗', arc: '从盟军领袖到官渡败北，显示组织能力比声望与数量更重要。' },
]

export const storyArcs = [
  { id: 1, chapters: '1—10 回', years: '184—192', title: '汉末失序', summary: '黄巾起义引出桃园结义；董卓进京、废立天子，吕布与王允合谋诛卓。', turning: '个人豪杰登场，天下共同秩序第一次彻底破裂。' },
  { id: 2, chapters: '11—20 回', years: '193—199', title: '群雄逐鹿', summary: '曹操、吕布、刘备围绕兖州与徐州反复攻守，吕布最终殒命白门楼。', turning: '短期武勇让位于组织、信用与根据地竞争。' },
  { id: 3, chapters: '21—30 回', years: '199—200', title: '官渡与千里独行', summary: '刘备再度流离，关羽降汉不降曹、挂印封金；曹操于官渡击败袁绍。', turning: '北方争霸由袁强曹弱逆转为曹操占据主动。' },
  { id: 4, chapters: '31—40 回', years: '201—208', title: '隆中决策', summary: '刘备依附荆州，三顾茅庐得诸葛亮；隆中对提出跨有荆益、联吴抗曹的长期路线。', turning: '刘备集团从求生转向有明确国家战略。' },
  { id: 5, chapters: '41—50 回', years: '208', title: '赤壁烈火', summary: '长坂败走、舌战群儒、草船借箭、苦肉计与连环计汇成赤壁决战。', turning: '曹操统一天下的窗口关闭，三分格局获得生存空间。' },
  { id: 6, chapters: '51—60 回', years: '209—211', title: '荆州棋局', summary: '孙刘围绕荆州持续角力，周瑜三气而亡；马超起兵，西方战局打开。', turning: '昔日联盟开始被现实利益侵蚀。' },
  { id: 7, chapters: '61—70 回', years: '211—217', title: '入蜀与汉中', summary: '刘备进取益州，庞统殒命、张飞义释严颜、马超归降；曹操平汉中、张辽威震逍遥津。', turning: '魏、蜀、吴均形成更稳定的领土核心。' },
  { id: 8, chapters: '71—80 回', years: '218—220', title: '荆州覆亡', summary: '刘备进位汉中王，关羽水淹七军后失荆州、走麦城；曹操去世，曹丕代汉。', turning: '桃园理想与隆中战略同时遭受不可逆的损失。' },
  { id: 9, chapters: '81—90 回', years: '221—225', title: '夷陵与南征', summary: '刘备伐吴败于陆逊，白帝托孤；诸葛亮安定后方并七擒孟获。', turning: '蜀汉由开创者时代进入托孤与制度维持时代。' },
  { id: 10, chapters: '91—100 回', years: '225—230', title: '出师北伐', summary: '出师表、收姜维、失街亭、斩马谡与再出祁山，构成理想和现实的拉锯。', turning: '诸葛亮的责任伦理达到顶点，国力限制也越来越清楚。' },
  { id: 11, chapters: '101—110 回', years: '231—253', title: '五丈原之后', summary: '木牛流马、上方谷与五丈原之后，魏国权力转入司马氏；姜维接续北伐。', turning: '旧一代英雄离场，真正决定结局的是继承与组织。' },
  { id: 12, chapters: '111—120 回', years: '254—280', title: '三分归一', summary: '司马氏控制魏国，邓艾、钟会灭蜀；晋代魏后南下灭吴，天下复归一统。', turning: '所有阵营的胜负被更长的历史周期重新解释。' },
]

export const vnNodeMappings = [
  { name: 'StartNode', kind: 'Progress 1 / Start 6', use: '每回唯一入口；StartNodeIndex 固定指向它。', example: '开始 → 章回转场' },
  { name: 'TransitionNode', kind: 'Progress 1 / Transition 4', use: '章节、地点与时间切换；清空立绘并控制淡入淡出。', example: 'FadeIn · 0.45s' },
  { name: 'ParagraphNode', kind: 'Progress 1 / Paragraph 2', use: '承载原著连续叙述；当前 120 回正文均序列化为此节点。', example: 'Lines[旁白/诗赞]' },
  { name: 'DialogueNode', kind: 'Progress 1 / Dialogue 1', use: '正式 VN 改编时拆出单句对白，并可同步触发配音与立绘动作。', example: 'SpeakerIdData + TextData' },
  { name: 'ChoiceNode', kind: 'Progress 1 / Choice 5', use: '最多六项；用于观察视角或支线，不篡改原著既定史实。', example: 'Options[i].Next' },
  { name: 'Sequence / Parallel', kind: 'Action 2 / 7、8', use: '把按序或同步发生的演出从 Progress 主链中分离。', example: 'Outputs.Actions[]' },
  { name: 'Tachi 系列', kind: 'Action 2 / 1、11—16', use: '角色先入场，再移动、缩放、退场或施加效果；TachiID 全程稳定。', example: 'zhuge_liang · cao_cao' },
  { name: '音画与特效', kind: 'Action 2 / 3—6、17—21', use: '背景、插画、配音、音效、音乐与屏幕效果；资源统一使用 res://。', example: 'res://Resources/ThreeKingdoms/…' },
]

export const publicContent = [
  { value: '120', label: '回完整正文', detail: '逐回加载，保留段落与诗赞' },
  { value: '120', label: '份 VN 节点图', detail: '遵循 VNNodeLibrary 序列化结构' },
  { value: '21', label: '位核心人物', detail: '动机、矛盾、弧线与稳定 TachiID' },
  { value: '12', label: '幕历史进程', detail: '从黄巾起义到三分归晋' },
]
