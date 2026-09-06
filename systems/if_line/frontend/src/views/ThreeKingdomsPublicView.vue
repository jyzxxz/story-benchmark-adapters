<template>
  <div ref="storyPageRef" class="story-page">
    <section class="hero">
      <div class="hero-pattern" aria-hidden="true"></div>
      <div class="hero-inner">
        <div class="hero-copy">
          <div class="eyebrow"><span></span> PUBLIC STORY · 无需登录</div>
          <p class="hero-author">{{ storyMeta.author }}</p>
          <h1>{{ storyMeta.title }}</h1>
          <p class="hero-subtitle">{{ storyMeta.subtitle }}</p>
          <p class="hero-logline">{{ storyMeta.logline }}</p>
          <div class="hero-actions">
            <button class="primary-action" type="button" @click="openChapter(1)">从第一回开始</button>
            <button class="secondary-action" type="button" @click="setSection('bible')">查看 Story Bible</button>
            <button class="secondary-action" type="button" @click="router.push('/create')">游客直接创作新故事</button>
          </div>
          <p class="public-notice"><span>✓</span>{{ storyMeta.publicNotice }}</p>
        </div>
        <div class="hero-seal" aria-hidden="true">
          <span class="seal-top">演义</span>
          <strong>三<br>国</strong>
          <span class="seal-bottom">一百二十回</span>
        </div>
      </div>
    </section>

    <nav class="story-nav" aria-label="《三国演义》公开内容导航">
      <div class="story-nav-inner">
        <button
          v-for="item in navigation"
          :key="item.id"
          type="button"
          :class="{ active: activeSection === item.id || (item.id === 'chapters' && activeSection === 'reader') }"
          @click="item.id === 'reader' ? openChapter(currentChapterNumber || 1) : setSection(item.id)"
        >
          <span>{{ item.index }}</span>{{ item.label }}
        </button>
      </div>
    </nav>

    <main class="story-main">
      <section v-if="activeSection === 'overview'" class="content-section overview-section">
        <header class="section-heading">
          <p>PUBLIC EDITION</p>
          <h2>把一部长篇史诗，展开成可阅读、可检索、可进入 VN 的公开作品</h2>
          <span>原著正文与结构化创作资料同处一个无需账号的入口。</span>
        </header>

        <div class="stat-grid">
          <article v-for="item in publicContent" :key="item.label" class="stat-card">
            <strong>{{ item.value }}</strong>
            <h3>{{ item.label }}</h3>
            <p>{{ item.detail }}</p>
          </article>
        </div>

        <div class="overview-grid">
          <article class="paper-card synopsis-card">
            <p class="card-kicker">STORY SYNOPSIS</p>
            <h3>故事梗概</h3>
            <p>{{ storyMeta.synopsis }}</p>
            <div class="edition-note">
              <span>版本说明</span>
              <p>{{ storyMeta.edition }}。已移除 EPUB 脚注跳转、现代注音与出版专有版式；页面不使用原书封面。</p>
            </div>
          </article>
          <aside class="quote-card">
            <p>“说起天下形势，<br>分久必合，合久必分。”</p>
            <span>第一回 · 开篇</span>
          </aside>
        </div>

        <header class="subsection-heading">
          <p>THEMATIC PILLARS</p>
          <h2>六根主题支柱</h2>
        </header>
        <div class="theme-grid">
          <article v-for="(theme, index) in themes" :key="theme.title" class="theme-card">
            <span>0{{ index + 1 }}</span>
            <h3>{{ theme.title }}</h3>
            <p>{{ theme.text }}</p>
          </article>
        </div>

        <div class="open-callout">
          <div>
            <p>不用登录，也不会跳转到创作后台</p>
            <h3>现在就读完整 120 回</h3>
          </div>
          <button type="button" @click="setSection('chapters')">打开章回目录 →</button>
        </div>
      </section>

      <section v-else-if="activeSection === 'bible'" class="content-section">
        <header class="section-heading compact">
          <p>STORY BIBLE</p>
          <h2>故事总设定</h2>
          <span>用于阅读理解、视觉小说改编与后续内容生产的统一事实层。</span>
        </header>

        <div class="bible-grid">
          <article v-for="section in bibleSections" :key="section.title" class="paper-card bible-card">
            <p class="card-kicker">{{ section.kicker }}</p>
            <h3>{{ section.title }}</h3>
            <dl>
              <template v-for="item in section.items" :key="item[0]">
                <dt>{{ item[0] }}</dt>
                <dd>{{ item[1] }}</dd>
              </template>
            </dl>
          </article>
        </div>

        <div class="guardrail-panel">
          <div class="guardrail-title">
            <span>ADAPTATION<br>GUARDRAILS</span>
            <h3>改编边界</h3>
          </div>
          <ol>
            <li v-for="rule in adaptationGuardrails" :key="rule">{{ rule }}</li>
          </ol>
        </div>

        <article class="relations-card">
          <div>
            <p class="card-kicker">RELATIONSHIP AXES</p>
            <h3>关系主轴</h3>
          </div>
          <div class="relation-list">
            <p><strong>刘备 · 关羽 · 张飞</strong><span>兄弟誓言 → 共同创业 → 荆州之失 → 复仇与崩解</span></p>
            <p><strong>刘备 · 诸葛亮</strong><span>三顾知遇 → 隆中共识 → 白帝托孤 → 理想由后继者承担</span></p>
            <p><strong>曹操 · 刘备</strong><span>英雄相识 → 合作与猜疑 → 国家道路的长期竞争</span></p>
            <p><strong>诸葛亮 · 司马懿</strong><span>进攻与防守 → 才智相持 → 国力、时间与继承的较量</span></p>
            <p><strong>孙权 · 周瑜 · 鲁肃 · 陆逊</strong><span>江东四代战略核心，各以不同方式处理守土、联盟与进取</span></p>
          </div>
        </article>
      </section>

      <section v-else-if="activeSection === 'characters'" class="content-section">
        <header class="section-heading compact">
          <p>CHARACTER & FACTION BIBLE</p>
          <h2>人物与势力</h2>
          <span>每个核心人物都包含动机、内在矛盾、人物弧线与稳定 VN 立绘 ID。</span>
        </header>

        <div class="faction-filter" role="group" aria-label="按势力筛选人物">
          <button type="button" :class="{ active: selectedFaction === 'all' }" @click="selectedFaction = 'all'">全部人物</button>
          <button
            v-for="faction in factions"
            :key="faction.id"
            type="button"
            :class="{ active: selectedFaction === faction.id }"
            :style="{ '--faction-color': faction.color }"
            @click="selectedFaction = faction.id"
          >{{ faction.name }}</button>
        </div>

        <div class="faction-strip">
          <article v-for="faction in factions" :key="faction.id" :style="{ '--faction-color': faction.color }">
            <span></span>
            <div><h3>{{ faction.name }}</h3><p>{{ faction.summary }}</p><small>{{ faction.leaders }}</small></div>
          </article>
        </div>

        <div class="character-grid">
          <article v-for="character in filteredCharacters" :key="character.name" class="character-card">
            <header>
              <div class="character-monogram">{{ character.name.slice(0, 1) }}</div>
              <div><h3>{{ character.name }}</h3><p>{{ character.courtesy }} · {{ character.role }}</p></div>
              <span class="vn-id">{{ character.vnId }}</span>
            </header>
            <dl>
              <div><dt>性格锚点</dt><dd>{{ character.traits }}</dd></div>
              <div><dt>核心欲望</dt><dd>{{ character.desire }}</dd></div>
              <div><dt>内在矛盾</dt><dd>{{ character.contradiction }}</dd></div>
              <div><dt>人物弧线</dt><dd>{{ character.arc }}</dd></div>
            </dl>
          </article>
        </div>
      </section>

      <section v-else-if="activeSection === 'timeline'" class="content-section">
        <header class="section-heading compact">
          <p>TWELVE-ACT TIMELINE</p>
          <h2>十二幕历史进程</h2>
          <span>每十回为一幕，用转折而非人物名单组织九十余年的叙事。</span>
        </header>
        <div class="timeline">
          <article v-for="arc in storyArcs" :key="arc.id" class="timeline-item">
            <div class="timeline-marker"><strong>{{ String(arc.id).padStart(2, '0') }}</strong><span></span></div>
            <div class="timeline-years">{{ arc.years }}<small>{{ arc.chapters }}</small></div>
            <div class="timeline-copy">
              <h3>{{ arc.title }}</h3>
              <p>{{ arc.summary }}</p>
              <div><span>结构转折</span>{{ arc.turning }}</div>
            </div>
            <button type="button" @click="openArc(arc.id)">查看本幕章节</button>
          </article>
        </div>
      </section>

      <section v-else-if="activeSection === 'chapters'" class="content-section chapter-section">
        <header class="section-heading compact">
          <p>COMPLETE TEXT · 120 CHAPTERS</p>
          <h2>完整章回目录</h2>
          <span>共 {{ totalCharacterCount.toLocaleString('zh-CN') }} 字可读正文；每回同时是一份符合 VNNodeLibrary 的公开节点图。</span>
        </header>

        <section class="my-branch-directory" aria-labelledby="my-branch-directory-title">
          <header>
            <div>
              <p>MY STORY BRANCHES</p>
              <h3 id="my-branch-directory-title">我的续写分支</h3>
            </div>
            <span>{{ myContinuationBranches.length }} 条</span>
          </header>
          <p class="my-branch-intro">退出后再回来，生成中的任务、待采用预览与已经公开的分支都会保留在这里。</p>
          <div v-if="myContinuationBranchesLoading" class="my-branch-empty" aria-live="polite">正在找回你的故事分支…</div>
          <div v-else-if="myContinuationBranchesError" class="my-branch-empty is-error" role="alert">{{ myContinuationBranchesError }}</div>
          <div v-else-if="!myContinuationBranches.length" class="my-branch-empty">你还没有续写分支。进入任一回正文即可开始共创。</div>
          <div v-else class="my-branch-list">
            <article v-for="branch in myContinuationBranches" :key="branch.continuationId">
              <div class="my-branch-chapter"><small>CHAPTER</small>{{ String(branch.chapterNumber).padStart(3, '0') }}</div>
              <div class="my-branch-copy">
                <header><strong>{{ branch.chapterTitle }}</strong><span :class="`status-${branch.status}`">{{ myBranchStatusLabel(branch.status) }}</span></header>
                <p>{{ branch.direction }}</p>
                <small v-if="branch.continuationText">{{ branch.continuationText }}</small>
              </div>
              <button type="button" @click="openMyContinuationBranch(branch)">继续此分支 →</button>
            </article>
          </div>
        </section>

        <div class="chapter-toolbar">
          <label class="search-box">
            <span aria-hidden="true">⌕</span>
            <input v-model.trim="chapterSearch" type="search" name="chapter-search" autocomplete="off" aria-label="搜索回目、人物或正文摘要" placeholder="搜索回目、人物或正文摘要…" />
          </label>
          <select v-model.number="selectedArc" aria-label="按叙事幕筛选">
            <option :value="0">全部十二幕</option>
            <option v-for="arc in storyArcs" :key="arc.id" :value="arc.id">第 {{ arc.id }} 幕 · {{ arc.title }}</option>
          </select>
          <span class="result-count">{{ filteredChapters.length }} 回</span>
        </div>

        <div v-if="catalogLoading" class="loading-state">正在载入 120 回公开目录…</div>
        <div v-else-if="catalogError" class="error-state">{{ catalogError }}</div>
        <div v-else class="chapter-list-grid">
          <article
            v-for="chapter in filteredChapters"
            :key="chapter.number"
            role="link"
            tabindex="0"
            :aria-label="`阅读${chapter.title}`"
            @click="openChapter(chapter.number)"
            @keydown.enter.prevent="openChapter(chapter.number)"
            @keydown.space.prevent="openChapter(chapter.number)"
          >
            <div class="chapter-number"><small>CHAPTER</small>{{ String(chapter.number).padStart(3, '0') }}</div>
            <div class="chapter-copy">
              <span>第 {{ chapter.arc }} 幕 · {{ storyArcs[chapter.arc - 1]?.title }}</span>
              <h3>{{ chapter.title }}</h3>
              <p>{{ chapter.excerpt }}</p>
              <footer>{{ chapter.paragraphCount }} 个 ParagraphNode · {{ chapter.characterCount.toLocaleString('zh-CN') }} 字</footer>
            </div>
            <span class="chapter-open-cue" aria-hidden="true">→</span>
          </article>
        </div>
        <div v-if="!catalogLoading && !catalogError && filteredChapters.length === 0" class="empty-results">没有找到匹配的回目。</div>
      </section>

      <section v-else-if="activeSection === 'vn'" class="content-section">
        <header class="section-heading compact">
          <p>VN NODE BLUEPRINT</p>
          <h2>按照 VNNodeLibrary 构建</h2>
          <span>不是借用“节点”概念做装饰；公开 JSON 直接采用文件规定的字段、数字枚举和 VNSerializedValue 包装。</span>
        </header>

        <div class="reference-banner">
          <div class="reference-icon">{ }</div>
          <div>
            <p>唯一节点规范来源</p>
            <h3>/home/workspace/fengbohan/if_line/VNNodeLibrary.txt</h3>
            <span>Version 1 · Progress / Action 双层结构 · Outputs 端口数组 · Data Kind 包装</span>
          </div>
        </div>

        <div class="node-flow" aria-label="VN 主流程与动作节点关系">
          <div class="flow-main">
            <div><small>Progress 1 / 6</small><strong>开始</strong><span>StartNodeIndex = 1</span></div><i>→</i>
            <div><small>Progress 1 / 4</small><strong>章回转场</strong><span>FadeIn · 0.45s</span></div><i>→</i>
            <div class="flow-wide"><small>Progress 1 / 2</small><strong>正文段落 × N</strong><span>Lines → 旁白 / 诗赞</span></div><i>→</i>
            <div><small>Progress</small><strong>下一回</strong><span>独立 Graph</span></div>
          </div>
          <div class="flow-action"><span>Actions 引用（不进入 Progress 主链）</span><b>Sequence</b><b>Parallel</b><b>Tachi</b><b>Background</b><b>CV / Audio</b><b>Effects</b></div>
        </div>

        <div class="node-mapping-grid">
          <article v-for="node in vnNodeMappings" :key="node.name">
            <p>{{ node.kind }}</p>
            <h3>{{ node.name }}</h3>
            <span>{{ node.use }}</span>
            <code>{{ node.example }}</code>
          </article>
        </div>

        <div class="contract-grid">
          <article class="paper-card">
            <p class="card-kicker">SERIALIZATION CONTRACT</p>
            <h3>序列化硬规则</h3>
            <ul>
              <li>NodeType / SubType 写数字，不写枚举名。</li>
              <li>枚举 Data 使用 Kind: Enum 与字符串枚举值。</li>
              <li>Outputs 的目标即便只有一个，也必须写成数组。</li>
              <li>Choice 的 Options 数量必须与 Options[i].Next 一致。</li>
              <li>Action 节点由 Progress 的 Actions 或动作组引用。</li>
              <li>正式素材统一使用 Godot res:// 路径。</li>
            </ul>
          </article>
          <article class="json-preview">
            <div class="code-header"><span>001.json</span><a :href="chapterAssetUrl(1)" target="_blank" rel="noopener">打开原始 JSON ↗</a></div>
            <pre>{
  "Version": 1,
  "StartNodeIndex": 1,
  "Nodes": [
    {
      "Index": 1,
      "NodeType": 1,
      "SubType": 6,
      "Outputs": { "Next": [2] }
    }
  ]
}</pre>
          </article>
        </div>
      </section>

      <section v-else-if="activeSection === 'reader'" class="reader-section">
        <div class="reader-toolbar">
          <button type="button" @click="setSection('chapters')">← 目录</button>
          <select v-model.number="currentChapterNumber" aria-label="选择章回" @change="openChapter(currentChapterNumber)">
            <option v-for="chapter in catalog?.chapters || []" :key="chapter.number" :value="chapter.number">{{ chapter.title }}</option>
          </select>
          <div>
            <button type="button" :disabled="currentChapterNumber <= 1" @click="navigateChapter(-1)">上一回</button>
            <button type="button" :disabled="currentChapterNumber >= 120" @click="navigateChapter(1)">下一回</button>
          </div>
        </div>

        <div v-if="chapterLoading" class="loading-state reader-loading">
          <strong>正在准备第 {{ currentChapterNumber }} 回</strong>
          <span v-if="vnSemanticVisualsTotal && !vnPreloadTotal">
            {{ vnSemanticVisualsPhase }} {{ vnSemanticVisualsCompleted }} / {{ vnSemanticVisualsTotal }}
          </span>
          <span v-else-if="vnPreloadTotal && !vnChapterAssetsReady">预载开场与后续画面 {{ vnPreloadLoaded }} / {{ vnPreloadTotal }}</span>
          <span v-else>正在整理视觉小说节点与图库素材…</span>
          <div
            class="reader-preload-progress"
            :class="{ 'is-indeterminate': !vnPreloadTotal && !vnSemanticVisualsTotal }"
          >
            <div
              class="reader-preload-track"
              role="progressbar"
              aria-label="视觉小说素材预载进度"
              aria-valuemin="0"
              aria-valuemax="100"
              :aria-valuenow="vnSemanticVisualsTotal && !vnPreloadTotal ? vnSemanticVisualsPercent : vnPreloadTotal ? vnPreloadPercent : undefined"
              :aria-valuetext="vnSemanticVisualsTotal && !vnPreloadTotal
                ? `已完成 ${vnSemanticVisualsCompleted} / ${vnSemanticVisualsTotal}`
                : vnPreloadTotal
                  ? `已完成 ${vnPreloadLoaded} / ${vnPreloadTotal}`
                  : '正在统计所需素材'"
            >
              <i :style="{ width: `${vnSemanticVisualsTotal && !vnPreloadTotal ? vnSemanticVisualsPercent : vnPreloadPercent}%` }"></i>
            </div>
            <output>
              {{ vnSemanticVisualsTotal && !vnPreloadTotal
                ? `${vnSemanticVisualsPercent}%`
                : vnPreloadTotal
                  ? `${vnPreloadPercent}%`
                  : '准备中' }}
            </output>
          </div>
          <small>系统先按正文准备本回场景和人物状态，再一次性预载；进入舞台后翻页不会临时生图。</small>
        </div>
        <div v-else-if="chapterError" class="error-state">{{ chapterError }}</div>
        <article v-else-if="selectedChapter" class="reader-paper">
          <header>
            <p>第 {{ selectedChapter.arc }} 幕 · {{ storyArcs[selectedChapter.arc - 1]?.title }}</p>
            <h1>{{ selectedChapter.title }}</h1>
            <div><span>{{ selectedChapter.characterCount.toLocaleString('zh-CN') }} 字</span><span>{{ selectedChapter.paragraphCount }} 个 ParagraphNode</span><span>公开阅读 · 游客可续写</span></div>
          </header>

          <section class="vn-player" aria-labelledby="vn-player-title">
            <header class="vn-player-bar">
              <div>
                <small>VISUAL NOVEL · SCENE {{ String(vnBeatIndex + 1).padStart(3, '0') }}</small>
                <strong id="vn-player-title">{{ vnSceneLabel }}</strong>
              </div>
              <div class="vn-progress-copy"><span>{{ vnBeatIndex + 1 }} / {{ vnBeats.length }}</span><b>{{ vnProgress }}%</b></div>
            </header>

            <div
              class="vn-stage"
              :class="[
                {
                  'is-poem': currentVnBeat.speaker === '诗赞',
                  'is-ending': vnAtEnd,
                  'has-soft-variation': vnVisualPresentation.softVariation,
                  'is-single-cast': activeVnCharacters.length === 1,
                  'is-dual-cast': activeVnCharacters.length === 2,
                  'is-visual-debug': isVisualDebugEnabled,
                },
                `is-shot-${vnVisualPresentation.backgroundShot}`,
                `is-composition-${vnVisualPresentation.composition}`,
                `is-grade-${vnVisualPresentation.backgroundGrade}`,
                `is-background-take-${currentVnBackgroundTake}`,
              ]"
              role="button"
              tabindex="0"
              :aria-label="vnStageAriaLabel"
              @click="handleVnStageClick"
              @keydown.enter.prevent="advanceVn"
              @keydown.space.prevent="advanceVn"
              @keydown.right.prevent="advanceVn"
              @keydown.left.prevent="rewindVn"
            >
              <transition name="vn-background">
                <div
                  v-if="currentVnBackgroundUrl"
                  :key="currentVnBackgroundUrl"
                  class="vn-backdrop"
                  :style="{
                    backgroundImage: `url(${currentVnBackgroundUrl})`,
                    backgroundPosition: vnVisualPresentation.backgroundPosition,
                  }"
                  aria-hidden="true"
                ></div>
              </transition>
              <div class="vn-stage-shade"></div>
              <div class="vn-stage-grain"></div>
              <div v-if="vnBeatIndex > 0" class="vn-side-cue is-prev" aria-hidden="true">
                <i>‹</i><span>上一幕</span>
              </div>
              <div v-if="!vnAtEnd" class="vn-side-cue is-next" aria-hidden="true">
                <span>下一幕</span><i>›</i>
              </div>
              <transition-group name="vn-character">
                <div
                  v-for="character in activeVnCharacters"
                  :key="`${character.name}-${character.side}`"
                  class="vn-character-slot"
                  :class="[
                    `is-${character.side}`,
                    `motion-${character.motion}`,
                    `is-take-${character.take}`,
                    {
                      'is-generating': character.generating,
                      'is-generated': character.portraitSource === 'generated',
                      'is-speaker': character.isSpeaker,
                      'is-observer': !character.isSpeaker && activeVnCharacters.length > 1,
                    },
                  ]"
                >
                  <span class="vn-character-ground" aria-hidden="true"></span>
                  <transition v-if="character.asset" name="vn-expression" mode="out-in">
                    <img
                      :key="characterPortraitSrc(character.asset)"
                      class="vn-character"
                      :src="characterPortraitSrc(character.asset)"
                      :alt="`${character.name}·${character.variantLabel}·镜头${character.take + 1}立绘`"
                      :width="characterPortraitWidth(character.asset)"
                      :height="characterPortraitHeight(character.asset)"
                      @load="onVnPortraitImageLoad(character.asset)"
                      @error="onVnPortraitImageError(character.asset)"
                      draggable="false"
                    />
                  </transition>
                  <div v-else class="vn-character-silhouette" aria-hidden="true"><span>{{ character.name }}</span></div>
                  <span v-if="character.generating" class="vn-character-forge">AI 正在绘制 {{ character.name }}·{{ character.variantLabel }}镜头 {{ character.requestedTake + 1 }}</span>
                  <span v-else-if="character.generationFailed && character.portraitSource === 'library'" class="vn-character-forge is-fallback">暂用素材库立绘</span>
                </div>
              </transition-group>

              <div v-if="vnNavigationBusy" class="vn-asset-status">正在准备下一幕的对应画面…</div>
              <div v-else-if="vnAssetsLoading" class="vn-asset-status">正在调度历史场景与人物立绘…</div>
              <div v-else-if="vnAssetsError" class="vn-asset-status is-error">{{ vnAssetsError }}</div>

              <div v-if="isVisualDebugEnabled" class="vn-visual-debug" aria-hidden="true">
                <dl>
                  <dt>style_pack</dt><dd>{{ visualDebug.stylePackVersion }}</dd>
                  <dt>normalization</dt><dd>{{ visualDebug.normalizationVersion }}</dd>
                  <dt>grade</dt><dd>{{ vnVisualPresentation.backgroundGrade }}</dd>
                  <dt>composition</dt><dd>{{ vnVisualPresentation.composition }}</dd>
                  <dt>cast</dt><dd>{{ activeVnCharacters.length }} ({{ activeVnCharacters.map(c => c.name).join(' / ') }})</dd>
                  <dt v-for="(c, i) in activeVnCharacters" :key="i" class="vn-visual-debug-char">
                    {{ c.name }}
                  </dt>
                  <dd v-for="(c, i) in activeVnCharacters" :key="`d${i}`" class="vn-visual-debug-char">
                    src={{ c.portraitSource }} take={{ c.take }}<br />
                    h_ratio={{ characterPresentation(c.asset)?.foreground_height_ratio ?? '—' }}<br />
                    w_ratio={{ characterPresentation(c.asset)?.foreground_width_ratio ?? '—' }}<br />
                    bottom={{ characterPresentation(c.asset)?.bottom_ratio ?? '—' }}<br />
                    axis={{ characterPresentation(c.asset)?.limiting_axis ?? '—' }}
                  </dd>
                </dl>
              </div>

              <transition name="vn-dialogue" mode="out-in">
                <div :key="vnBeatIndex" class="vn-dialogue-box">
                  <div class="vn-nameplate">
                    <span>{{ currentVnBeat.speaker }}</span>
                    <small>{{ currentVnBeat.speaker === '旁白' ? 'NARRATION' : currentVnBeat.speaker === '诗赞' ? 'VERSE' : 'CHARACTER' }}</small>
                  </div>
                  <p>{{ currentVnBeat.text }}</p>
                  <span class="vn-next-cue">{{ vnEndCueText }} <i>◆</i></span>
                </div>
              </transition>
            </div>

            <footer class="vn-controls">
              <button type="button" :disabled="vnBeatIndex <= 0" @click="rewindVn">← 上一句</button>
              <div class="vn-progress-track" aria-hidden="true"><span :style="{ width: `${vnProgress}%` }"></span></div>
              <button v-if="!vnAtEnd" type="button" @click="advanceVn">下一句 →</button>
              <button v-else-if="vnAtDraftEnd" type="button" @click="scrollToContinuationReview">查看并采用续写 ↓</button>
              <button v-else type="button" @click="openReaderEndMode('choices')">选择下一步 →</button>
            </footer>
          </section>

          <section
            v-if="vnAtDraftEnd"
            id="continuation-vn-review"
            class="continuation-vn-review"
            aria-labelledby="continuation-vn-review-title"
          >
            <header>
              <div>
                <small>AI CONTINUATION · VN PREVIEW COMPLETE</small>
                <h2 id="continuation-vn-review-title">这段续写已经完整融入正文舞台</h2>
              </div>
              <span :class="`status-${generatedContinuation?.status}`">{{ continuationStatusLabel }}</span>
            </header>
            <p>你刚读完的每一句文字和场景图就是待采用的分支。采用后，它会固定到本回正文末尾，所有读者都能从目录进入并在视觉小说中游玩。</p>
            <div class="continuation-vn-review-actions">
              <button type="button" @click="rewindVn">← 返回上一幕检查</button>
              <button
                type="button"
                :disabled="confirmBusy || imageAction?.status === 'processing' || imageDisplayPending"
                @click="confirmContinuation"
              >{{ confirmBusy ? '正在采用…' : '采用这段续写并公开分支 →' }}</button>
            </div>
            <details v-if="readerVisualMode === 'system_generate'" class="redraw-details">
              <summary>画面不合适？重绘后仍在正文舞台内预览</summary>
              <label for="image-prompt">场景图提示词</label>
              <textarea
                id="image-prompt"
                v-model.trim="imagePrompt"
                rows="4"
                maxlength="4000"
                placeholder="描述要重绘的古典场景…"
              ></textarea>
              <button
                class="studio-secondary"
                type="button"
                :disabled="imageBusy || !imagePrompt"
                @click="generateImage"
              >{{ imageBusy ? imagePhase : 'Qwen AI 重绘场景图' }}</button>
            </details>
          </section>

          <section
            v-else-if="vnAtEnd && readerEndMode === 'choices'"
            id="reader-end-flow"
            class="chapter-end-flow"
            aria-labelledby="chapter-end-title"
          >
            <header>
              <div>
                <small>CHAPTER {{ String(currentChapterNumber).padStart(3, '0') }} · COMPLETE</small>
                <h2 id="chapter-end-title">本回已完，你想怎样继续？</h2>
              </div>
              <span v-if="parentContinuationId">当前已选择一条公共分支</span>
            </header>
            <div class="chapter-end-actions">
              <button
                type="button"
                :disabled="currentChapterNumber >= 120"
                @click="navigateChapter(1)"
              >
                <i>01</i>
                <strong>{{ currentChapterNumber < 120 ? '继续阅读下一回' : '全书已经读完' }}</strong>
                <span>{{ nextChapterTitle || '返回前文或开始续写自己的结局' }}</span>
              </button>
              <button type="button" @click="openReaderEndMode('continue')">
                <i>02</i>
                <strong>从这里开始续写</strong>
                <span>让 AI 沿当前正文或已选分支生成新的文字与场景图</span>
              </button>
              <button type="button" @click="openReaderEndMode('community')">
                <i>03</i>
                <strong>查看其他人的续写</strong>
                <span>{{ publicContinuations.length ? `${publicContinuations.length} 条公开走向，先选方向再查看正文` : '这一回还没有公开分支' }}</span>
              </button>
            </div>
          </section>

          <section
            v-else-if="vnAtEnd && readerEndMode === 'community'"
            id="reader-end-flow"
            class="community-story"
            aria-labelledby="community-story-title"
          >
            <header>
              <div>
                <button class="reader-flow-back" type="button" @click="openReaderEndMode('choices')">← 返回本回选择</button>
                <p>COMMUNITY BRANCHES</p>
                <h2 id="community-story-title">选择一条故事走向</h2>
              </div>
              <span>{{ publicContinuations.length }} 个公开分支</span>
            </header>
            <p class="community-intro">先选择方向，再查看该分支的正文和场景图。只有点击“从这条分支继续”才会改变你的后续创作上下文。</p>
            <p v-if="studioError" class="community-selection-error" role="alert">{{ studioError }}</p>
            <div v-if="publicContinuationsLoading" class="community-empty">正在展开公共故事树…</div>
            <div v-else-if="publicContinuationsError" class="community-empty is-error">{{ publicContinuationsError }}</div>
            <div v-else-if="!publicContinuations.length" class="community-empty">
              <p>这一回还没有公开续写。</p>
              <button type="button" @click="openReaderEndMode('continue')">写下第一个故事分支 →</button>
            </div>
            <div v-else class="community-branch-browser">
              <nav class="community-branch-list" aria-label="公开续写方向">
                <button
                  v-for="(node, index) in publicContinuations"
                  :key="node.id"
                  type="button"
                  :class="{ active: communityPreviewNode?.id === node.id, selected: parentContinuationId === node.id }"
                  @click="communityPreviewId = node.id"
                >
                  <i>{{ String(index + 1).padStart(2, '0') }}</i>
                  <span><strong>{{ node.direction }}</strong><small>{{ node.selection_count }} 人选择 · 第 {{ node.depth + 1 }} 层分支</small></span>
                  <b aria-hidden="true">›</b>
                </button>
              </nav>
              <article v-if="communityPreviewNode" class="community-branch-preview" aria-live="polite">
                <header>
                  <div><small>当前预览</small><strong>{{ communityPreviewNode.direction }}</strong></div>
                  <span v-if="parentContinuationId === communityPreviewNode.id">已选分支</span>
                </header>
                <div class="community-branch-story">
                  <p>{{ communityPreviewNode.continuation_text }}</p>
                  <img
                    v-if="communityPreviewNode.assets[0]"
                    :src="communityPreviewNode.assets[0].media_url"
                    :alt="`${communityPreviewNode.direction}的公开场景图`"
                    :width="communityPreviewNode.assets[0].width || 1024"
                    :height="communityPreviewNode.assets[0].height || 1024"
                    loading="lazy"
                  />
                </div>
                <footer>
                  <span>{{ communityPreviewNode.selection_count }} 位读者从这里继续</span>
                  <button
                    type="button"
                    :disabled="selectingContinuationId !== null"
                    @click.stop="selectPublicContinuation(communityPreviewNode)"
                  >{{ selectingContinuationId === communityPreviewNode.id ? '正在准备视觉小说…' : parentContinuationId === communityPreviewNode.id ? '重新游玩这条分支 →' : '在视觉小说中阅读这条分支 →' }}</button>
                </footer>
              </article>
            </div>
          </section>

          <section
            v-if="vnAtEnd && readerEndMode === 'continue' && !vnAtDraftEnd"
            id="reader-end-flow"
            class="ai-studio"
            aria-labelledby="ai-studio-title"
          >
            <header>
              <div>
                <button class="reader-flow-back" type="button" @click="openReaderEndMode('choices')">← 返回本回选择</button>
                <p>GUEST AI STUDIO</p>
                <h2 id="ai-studio-title">从本回继续写，并生成完整场景素材</h2>
              </div>
              <span>无需注册 · 公开共创</span>
            </header>

            <div class="studio-grid">
              <div class="studio-panel">
                <div class="reader-source-choice" role="radiogroup" aria-label="续写图片来源">
                  <button
                    type="button"
                    role="radio"
                    :disabled="continuationBusy"
                    :aria-checked="readerVisualMode === 'user_upload'"
                    :class="{ active: readerVisualMode === 'user_upload' }"
                    @click="readerVisualMode = 'user_upload'"
                  >
                    <small>入口一</small><strong>上传自己的图片</strong><span>当前游客会话私有 · 隔离检查</span>
                  </button>
                  <button
                    type="button"
                    role="radio"
                    :disabled="continuationBusy"
                    :aria-checked="readerVisualMode === 'system_generate'"
                    :class="{ active: readerVisualMode === 'system_generate' }"
                    @click="readerVisualMode = 'system_generate'"
                  >
                    <small>入口二</small><strong>系统生成续写</strong><span>完整素材包 · 全部完成后展示</span>
                  </button>
                </div>
                <label for="continuation-direction">你希望故事接下来怎样发展？</label>
                <textarea
                  id="continuation-direction"
                  v-model.trim="continuationDirection"
                  rows="5"
                  maxlength="4000"
                  :placeholder="continuationPlaceholder"
                ></textarea>
                <p class="studio-hint" aria-live="polite">
                  {{ continuationSuggestionBusy
                    ? 'AI 正在分析前文中的人物、冲突与未解线索…'
                    : continuationSuggestion
                      ? '以上方向由 AI 根据当前前文生成，可直接修改。'
                      : continuationSuggestionError || 'AI 将根据当前前文生成一个续写方向。' }}
                </p>
                <button
                  class="studio-primary"
                  type="button"
                  :disabled="continuationBusy || !continuationDirection || (readerVisualMode === 'user_upload' && (!readerUploadFile || !readerRightsAttested))"
                  @click="generateContinuation"
                >{{ continuationBusy ? continuationPhase : readerVisualMode === 'user_upload' ? '使用我的图片生成续写' : '系统生成完整续写包' }}</button>
                <p class="studio-hint">当前第 {{ currentChapterNumber }} 回正文与所选公共节点会作为冻结上下文；采用后正文和场景图将进入所有人可见的故事树。</p>
              </div>

              <div v-if="readerVisualMode === 'user_upload'" class="studio-panel upload-panel">
                <label for="reader-image-upload">上传续写场景图</label>
                <label class="reader-file-picker" for="reader-image-upload">
                  <input id="reader-image-upload" type="file" accept="image/png,image/jpeg,image/webp" @change="onReaderUploadChange" />
                  <span>{{ readerUploadFile ? readerUploadFile.name : '选择 PNG、JPEG 或 WebP' }}</span>
                </label>
                <label class="reader-rights"><input v-model="readerRightsAttested" type="checkbox" /> 我确认拥有此图的使用权</label>
                <p class="studio-hint">图片生成预览期间仅当前会话可见；采用续写后会随公共故事节点一并公开。</p>
                <div v-if="readerUploadResult" class="reader-upload-result">
                  <img :src="readerUploadResult.media_url" :alt="readerUploadResult.target_name" :width="readerUploadResult.width" :height="readerUploadResult.height" />
                  <span><strong>已加入当前游客会话</strong><small>{{ readerUploadResult.width }} × {{ readerUploadResult.height }} · {{ readerUploadResult.status }}</small></span>
                </div>
              </div>

              <div v-else class="studio-panel system-mode-panel">
                <p class="system-mode-index">SYSTEM GENERATE</p>
                <h3>系统会自动完成三件事</h3>
                <ol>
                  <li>根据公开 Release 与当前会话状态生成正文</li>
                  <li>为续写节点准备背景与所需人物立绘</li>
                  <li>全部图片生成、匹配并加载完成后，正文与画面一次显示</li>
                </ol>
                <p class="studio-hint">不会先展示临时黑舞台或半成品。只有完整素材包就绪并由你点击“采用”，才会固定到本回的公共故事节点。</p>
              </div>
            </div>

            <p v-if="studioError" class="studio-error" role="alert">{{ studioError }}</p>

            <div v-if="generatedContinuation && draftContinuationAssets.length" class="generated-bundle-preloader" aria-hidden="true">
              <img
                v-for="asset in draftContinuationAssets"
                :key="asset.asset_version_id"
                :src="asset.media_url"
                alt=""
                decoding="sync"
                @load="onGeneratedImageLoad(asset.media_url)"
                @error="onGeneratedImageError(asset.media_url)"
              />
            </div>

            <div v-if="generatedContinuation && !continuationBundleReady" class="continuation-generation-gate" aria-live="polite">
              <strong>正在完成完整续写包</strong>
              <span>{{ continuationBusy ? continuationPhase : imageBusy ? imagePhase : '正在装配文字与场景图…' }}</span>
              <small>文字和图片全部生成并加载完成后才会一起展示。</small>
            </div>

            <footer>
              <span>还想从零创作？Story Bible、章节、大纲、配音与 VNGraph 也已向游客开放。</span>
              <button type="button" @click="router.push('/create')">进入完整创作台 →</button>
            </footer>
          </section>

          <details class="reader-full-text">
            <summary>展开本回完整原文</summary>
            <div class="reader-body">
              <template v-for="(line, index) in readerLines" :key="index">
                <blockquote v-if="line.speaker === '诗赞'">{{ line.text }}</blockquote>
                <p v-else>{{ line.text }}</p>
              </template>
            </div>
          </details>

          <details class="graph-details">
            <summary>查看本回 VN 图信息</summary>
            <div>
              <p><strong>Version</strong><span>{{ chapterGraph?.Version }}</span></p>
              <p><strong>StartNodeIndex</strong><span>{{ chapterGraph?.StartNodeIndex }}</span></p>
              <p><strong>Nodes</strong><span>{{ chapterGraph?.Nodes.length }}</span></p>
              <p><strong>主链</strong><span>Start → Transition → Paragraph × {{ readerLines.length }}</span></p>
              <a :href="chapterAssetUrl(currentChapterNumber)" target="_blank" rel="noopener">查看公开节点 JSON ↗</a>
            </div>
          </details>

          <footer class="reader-footer">
            <button type="button" :disabled="currentChapterNumber <= 1" @click="navigateChapter(-1)">← 上一回</button>
            <div><span>三国演义</span><small>{{ currentChapterNumber }} / 120</small></div>
            <button type="button" :disabled="!vnAtEnd" @click="openReaderEndMode('choices')">{{ vnAtEnd ? '选择下一步 →' : '读完本回后继续' }}</button>
          </footer>
        </article>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  guestStoryApi,
  type AssetAction,
  type GeneratedAsset,
  type GuestVisualUpload,
  type PublicContinuationNode,
  type PublicReleaseProject,
  type ReadingContinuation,
  type ReadingSession,
  type VnGeneratedBackground,
  type VnGeneratedPortrait,
  type VnPortraitVariant,
  type VnVisualTake,
} from '@/api/guestStoryApi'
import { libraryAssetApi, type VnLibraryAsset } from '@/api/libraryAssetApi'
import { useUserStore } from '@/stores/user'
import {
  buildSemanticBackgroundPlan,
  semanticSceneRequestKey,
} from '@/utils/threeKingdomsVisualMatcher'
import {
  adaptationGuardrails,
  bibleSections,
  characters,
  factions,
  publicContent,
  storyArcs,
  storyMeta,
  themes,
  vnNodeMappings,
  type ChapterCatalog,
  type StorySection,
  type VNGraph,
  type VNSerializedValue,
} from '@/data/threeKingdoms'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const storyPageRef = ref<HTMLElement | null>(null)
const assetRoot = `${import.meta.env.BASE_URL}three-kingdoms`
const storyContentVersion = 'vernacular-20260717'

const navigation: Array<{ id: StorySection; index: string; label: string }> = [
  { id: 'overview', index: '序', label: '作品总览' },
  { id: 'bible', index: '壹', label: 'Story Bible' },
  { id: 'characters', index: '贰', label: '人物势力' },
  { id: 'timeline', index: '叁', label: '十二幕' },
  { id: 'chapters', index: '肆', label: '120 回目录' },
  { id: 'vn', index: '伍', label: 'VN 节点' },
  { id: 'reader', index: '读', label: '阅读正文' },
]

type ContinuationStatus = ReadingContinuation['status']

interface PendingContinuationRecord {
  continuationId: string
  direction: string
  startedAt: number
  visualMode?: 'user_upload' | 'system_generate'
  upload?: GuestVisualUpload
}

interface MyContinuationBranch {
  chapterNumber: number
  chapterTitle: string
  sessionId: string
  continuationId: string
  direction: string
  continuationText: string
  status: ContinuationStatus
}

type ReaderEndMode = 'choices' | 'community' | 'continue'

const validSections = new Set<StorySection>(navigation.map((item) => item.id))
const initialSection = String(route.query.section || 'overview') as StorySection
const activeSection = ref<StorySection>(validSections.has(initialSection) ? initialSection : 'overview')
const catalog = ref<ChapterCatalog | null>(null)
const catalogLoading = ref(false)
const catalogError = ref('')
const chapterGraph = ref<VNGraph | null>(null)
const chapterLoading = ref(false)
const chapterError = ref('')
const vnBeatIndex = ref(0)
const vnBackgrounds = ref<VnLibraryAsset[]>([])
const vnPortraits = ref<VnLibraryAsset[]>([])
const vnGeneratedBackgrounds = ref<Record<string, VnGeneratedBackground>>({})
const vnGeneratedSceneBackgrounds = ref<Record<string, VnGeneratedBackground>>({})
const vnGeneratedPortraits = ref<Record<string, VnGeneratedPortrait>>({})
const vnAssetsLoading = ref(false)
const vnAssetsError = ref('')
const vnCachedVisualsLoaded = ref(false)
const vnChapterAssetsReady = ref(false)
const vnSemanticVisualsCompleted = ref(0)
const vnSemanticVisualsTotal = ref(0)
const vnSemanticVisualsFailed = ref(0)
const vnSemanticVisualsPhase = ref('')
const vnPreloadLoaded = ref(0)
const vnPreloadTotal = ref(0)
const vnNavigationBusy = ref(false)
const vnFailedMediaUrls = ref<string[]>([])
const vnDecodedMediaUrls = ref<string[]>([])
const currentChapterNumber = ref(Math.min(120, Math.max(1, Number(route.query.chapter) || 1)))
const chapterSearch = ref('')
const selectedArc = ref(0)
const selectedFaction = ref('all')
const runtimeProject = ref<PublicReleaseProject | null>(null)
const readingSession = ref<ReadingSession | null>(null)
const publicContinuations = ref<PublicContinuationNode[]>([])
const publicContinuationsLoading = ref(false)
const publicContinuationsError = ref('')
const myContinuationBranches = ref<MyContinuationBranch[]>([])
const myContinuationBranchesLoading = ref(false)
const myContinuationBranchesError = ref('')
const selectingContinuationId = ref<string | null>(null)
const generatedContinuation = ref<ReadingContinuation | null>(null)
const continuationDirection = ref('')
const continuationSuggestion = ref('')
const continuationSuggestionBusy = ref(false)
const continuationSuggestionError = ref('')
const continuationBusy = ref(false)
const continuationPhase = ref('正在准备游客空间…')
const confirmBusy = ref(false)
const parentContinuationId = ref<string | null>(null)
const readerEndMode = ref<ReaderEndMode>('choices')
const communityPreviewId = ref<string | null>(null)
const readerVisualMode = ref<'user_upload' | 'system_generate'>('system_generate')
const readerUploadFile = ref<File | null>(null)
const readerRightsAttested = ref(false)
const readerUploadResult = ref<GuestVisualUpload | null>(null)
const imagePrompt = ref('')
const imageAction = ref<AssetAction | null>(null)
const imageBusy = ref(false)
const imagePhase = ref('正在提交生图任务…')
const generatedImageLoadedUrls = ref<string[]>([])
const generatedImageFailedUrls = ref<string[]>([])
const studioError = ref('')
const lastAutoEnteredContinuationId = ref('')
let continuationSuggestionSerial = 0
let readingSessionPromise: Promise<ReadingSession> | null = null

const filteredCharacters = computed(() => selectedFaction.value === 'all'
  ? characters
  : characters.filter((character) => character.faction === selectedFaction.value))

const totalCharacterCount = computed(() => catalog.value?.chapters.reduce((sum, chapter) => sum + chapter.characterCount, 0) || 0)

const nextChapterTitle = computed(() => catalog.value?.chapters.find(
  (chapter) => chapter.number === currentChapterNumber.value + 1
)?.title || '')

const communityPreviewNode = computed(() => publicContinuations.value.find(
  (node) => node.id === communityPreviewId.value
) || publicContinuations.value[0] || null)

const filteredChapters = computed(() => {
  const query = chapterSearch.value.toLocaleLowerCase('zh-CN')
  return (catalog.value?.chapters || []).filter((chapter) => {
    const matchesArc = selectedArc.value === 0 || chapter.arc === selectedArc.value
    const haystack = `${chapter.title} ${chapter.excerpt}`.toLocaleLowerCase('zh-CN')
    return matchesArc && (!query || haystack.includes(query))
  })
})

const selectedChapter = computed(() => catalog.value?.chapters.find((chapter) => chapter.number === currentChapterNumber.value))

const readerLines = computed(() => {
  const result: Array<{ speaker: string; text: string }> = []
  for (const node of chapterGraph.value?.Nodes || []) {
    if (node.NodeType !== 1 || node.SubType !== 2) continue
    const items = node.Data.Lines?.Items || []
    for (const item of items) {
      const object = item.ObjectValue || {}
      const speaker = object.SpeakerId?.StringValue || '旁白'
      const text = object.Text?.StringValue || ''
      if (text) result.push({ speaker, text })
    }
  }
  return result
})

type VnBeatSource = 'chapter' | 'public_continuation' | 'draft_continuation'

interface VnBeat {
  speaker: string
  text: string
  sourceIndex: number
  sourceBeatIndex: number
  source: VnBeatSource
  visibleCharacters?: string[]
  continuationId?: string
  direction?: string
  backgroundUrl?: string
  backgroundLabel?: string
}

type ContinuationVisualAsset = Pick<
  GeneratedAsset,
  'asset_version_id' | 'media_url' | 'asset_type' | 'width' | 'height' | 'description_cn'
>

const draftContinuationAssets = computed<ContinuationVisualAsset[]>(() => {
  if (readerVisualMode.value === 'user_upload') {
    const upload = readerUploadResult.value
    return upload
      ? [{
          asset_version_id: upload.asset_version_id,
          media_url: upload.media_url,
          asset_type: upload.asset_type,
          width: upload.width,
          height: upload.height,
          description_cn: upload.target_name,
        }]
      : []
  }
  return (imageAction.value?.assets || []).filter((asset) => Boolean(asset.media_url))
})

const draftContinuationImagesReady = computed(() => Boolean(
  draftContinuationAssets.value.length
    && draftContinuationAssets.value.every((asset) => (
      generatedImageLoadedUrls.value.includes(asset.media_url)
    ))
))

const continuationBundleReady = computed(() => {
  const status = generatedContinuation.value?.status
  if (!status || status === 'processing') return false
  if (status === 'failed' || status === 'cancelled') return true
  if (readerVisualMode.value === 'user_upload') {
    return Boolean(
      (status === 'preview_ready' || status === 'confirmed')
      && draftContinuationImagesReady.value
    )
  }
  return Boolean(
    (status === 'preview_ready' || status === 'confirmed')
    && imageAction.value
    && ['awaiting_confirmation', 'ready', 'applied'].includes(imageAction.value.status)
    && draftContinuationImagesReady.value
  )
})

const splitVnText = (text: string, maxLength = 145) => {
  const sentences = text.match(/[^。！？；…]+(?:[。！？；…]+|$)/g) || [text]
  const chunks: string[] = []
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
    for (let offset = 0; offset < clean.length; offset += maxLength) {
      chunks.push(clean.slice(offset, offset + maxLength))
    }
  }
  if (current) chunks.push(current)
  return chunks
}

const vnCharacterAliases: Record<string, string[]> = {
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

const genericVnCharacterLabels = new Set([
  '', '旁白', '诗赞', 'NPC', '路人', '路人甲', '路人乙', '士兵', '守卫',
  '侍卫', '军士', '官兵', '群众', '众人', '旁人', '百姓', '村民',
  '店员', '侍者', '仆人', '丫鬟', '老人', '男人', '女人', '某人',
  '庄主', '主人', '大人', '主公',
])
// 仅在身世/背景介绍里被提及的历史人物与单字简称，本身不是登场角色，
// 不应被 specificVnCharacterName 识别为可立绘角色。例如第一章正文里
// 「叫来校尉邹靖商量。靖说：……」中的“靖”是邹靖的单字简称，
// 「是中山靖王刘胜和汉景帝的后代」中的“中山靖王刘胜/刘胜/汉景帝”
// 是刘备的祖先。它们一旦被识别成角色，就会显示莫名其妙的“靖曰”立绘。
const backgroundOnlyCharacterLabels = new Set([
  '靖', '邹靖', '中山', '中山靖', '山靖', '靖王', '中山靖王', '靖王刘胜',
  '中山靖王刘胜', '刘胜', '王刘胜',
  '汉景帝', '景帝', '刘贞', '贞', '鲁恭王', '恭王',
  '汉武帝', '武帝', '光武帝',
  '汉高祖', '高祖', '献帝', '灵帝', '桓帝', '汉室',
])
const vnCharacterLeadWords = /^(?:只见|忽见|原来|这时|此时|随后|于是|却见|又见|那位|这位|一名|一位|一个)+/
const vnCharacterSpeechAdverbs = /(?:忍不住|不由得|急忙上前|快步上前|赶上前|先下拜|翻身下马|急进|趋前|上前|下拜|下马|起身|拱手|抱拳|跪下|回身|转身|缓缓|忽然|突然|猛然|悄然|立即|马上|径直|慢慢|快步|连忙|大声|高声|低声|轻声|厉声|冷冷|沉声|朗声|平静地|笑着|叹息着|怒声)*$/
const vnCharacterSpeechVerbTail = /(?:说道|问道|答道|喝道|叫道|喊道|骂道|赞道|劝道|笑道|叹道|怒道|道|曰|说|问|答|喝|叫|喊|骂|赞|劝|笑|叹)+$/
const vnCharacterCueActionSuffix = /(?:劝谏|进谏|劝阻|劝说|劝解|阻拦|迎出|引见|引入|先下拜|急忙上前|快步上前|赶上前|翻身下马|急进|趋前|上前|下拜|下马|起身|拱手|抱拳|跪下|回身|转身)+$/
const vnCharacterTitlePrefix = /^(?:大将军|前将军|后将军|左将军|右将军|司徒|太尉|太傅|丞相|军师|太守|刺史|校尉|尚书|侍中|议郎|主簿|将军)+/
const commonChineseSurnames = '赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝安常乐于傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫房裘缪干解应宗丁宣邓郁单杭洪包左石崔吉龚程邢裴陆荣翁荀羊甄曲封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶黎薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴苍闻党翟谭贡劳逄姬申扶堵冉宰郦雍郤璩桑桂濮牛寿边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权盖益桓公'
const compoundChineseSurnames = [
  '欧阳', '司马', '上官', '诸葛', '东方', '皇甫', '尉迟', '公孙',
  '慕容', '宇文', '司徒', '司空', '令狐', '夏侯', '长孙', '端木',
]

const looksLikePersonalName = (value: string) => {
  if (/^[A-Z][A-Za-z0-9·・.' -]{1,31}$/.test(value)) return true
  if (!/^[\p{Script=Han}·・]{2,4}$/u.test(value)) return false
  return compoundChineseSurnames.some((surname) => value.startsWith(surname))
    || commonChineseSurnames.includes(value[0])
}

const specificVnCharacterName = (value: string) => {
  const clean = value
    .trim()
    .replace(vnCharacterLeadWords, '')
    .replace(vnCharacterSpeechAdverbs, '')
    .replace(vnCharacterTitlePrefix, '')
    .trim()
  if (genericVnCharacterLabels.has(clean)) return ''
  if (backgroundOnlyCharacterLabels.has(clean)) return ''
  if (
    /^(?:那人|其人|一人|大汉|老人|男子|女子|此人)(?:说|问|答|喝|叫|喊|骂|笑|叹|道|曰)?$/u.test(clean)
    || (clean.length >= 3 && /(?:说道|问道|答道|喝道|叫道|喊道|骂道|笑道|叹道|怒道|说|问|答|喝|叫|喊|骂|笑|叹|曰)$/u.test(clean))
  ) return ''
  if (clean.length < 2 || clean.length > 32) return ''
  if (!/^[\p{L}\p{N}·・.' -]+$/u.test(clean)) return ''
  return looksLikePersonalName(clean) ? clean : ''
}

const canonicalVnCharacterName = (value: string) => {
  const clean = value.trim()
  return Object.entries(vnCharacterAliases).find(([, aliases]) => aliases.includes(clean))?.[0]
    || specificVnCharacterName(clean)
}

const introducedVnCharacterName = (text: string) => {
  const familyAndGiven = /我姓([\p{Script=Han}]{1,2})[，,\s]*(?:名|叫)([\p{Script=Han}]{1,2})/u.exec(text)
  if (familyAndGiven) {
    const name = canonicalVnCharacterName(`${familyAndGiven[1]}${familyAndGiven[2]}`)
    if (name) return name
  }
  const direct = /我(?:叫|名叫|名为|是)[：，,\s]*([\p{Script=Han}·・]{2,4}|[A-Z][A-Za-z0-9·・.' -]{1,31})/u.exec(text)
  if (direct) return canonicalVnCharacterName(direct[1])
  const relational = /(?:我|臣)(?:是|乃)?[^。！？“”「」]{0,36}(?:弟弟|兄长|儿子|女儿|名叫|叫做)[：，,\s]*([\p{Script=Han}·・]{2,4})/u.exec(text)
  return relational ? canonicalVnCharacterName(relational[1]) : ''
}

const anonymousVnCharacterCue = /(?:一人|一位[\p{Script=Han}]{0,4}|一个[\p{Script=Han}]{0,4}|那人|其人|大汉|老人|男子|女子|庄主|店主|主人)(?:[^。！？；“”「」]{0,18})(?:说道|问道|答道|喝道|叫道|喊道|骂道|赞道|劝道|笑道|叹道|怒道|说|曰)[：，,:]?[“「]/u
const anonymousVnCharacterEntry = /(?:一人|一位[\p{Script=Han}]{0,4}|一个[\p{Script=Han}]{0,4}|那人|其人|大汉|老人|男子|女子|庄主|店主|主人)(?:[^。！？；]{0,24})(?:走进|走来|来到|赶来|奔来|推门|入店|入席|坐下|下马|出现|现身|说道|问道|喝道|叫道|说)/u

const nearestFullNameForShortCue = (sourceText: string, shortName: string) => {
  if (!/^[\p{Script=Han}]{1,2}$/u.test(shortName)) return ''
  // 防御:单字"靖"是中山靖王刘胜的核心字、邹靖的单字简称,
  // 不是任何核心角色的合法简称。直接拒绝,避免滑动窗口切出
  // "山靖"/"靖王"等子串被 specificVnCharacterName 误判为人名。
  if (backgroundOnlyCharacterLabels.has(shortName)) return ''
  // Prefer the shortest valid full name. This prevents a sentence fragment
  // such as “崔毅引贡” from outranking the earlier explicit name “闵贡”.
  for (let length = 2; length <= 4; length += 1) {
    let nearest = ''
    let nearestIndex = -1
    for (let start = 0; start + length <= sourceText.length; start += 1) {
      const candidate = sourceText.slice(start, start + length)
      if (!candidate.endsWith(shortName)) continue
      // 防御:滑动窗口切出的子串也必须不是祖先/历史人物黑名单的一员
      if (backgroundOnlyCharacterLabels.has(candidate)) continue
      if (
        start > 0
        && compoundChineseSurnames.includes(sourceText.slice(start - 1, start + 1))
      ) continue
      const name = canonicalVnCharacterName(candidate)
      if (name && start >= nearestIndex) {
        nearest = name
        nearestIndex = start
      }
    }
    if (nearest) return nearest
  }
  return ''
}

const cueSubjectVnCharacterName = (value: string, sourceText: string) => {
  const normalized = value
    .trim()
    .replace(vnCharacterSpeechVerbTail, '')
    .replace(vnCharacterLeadWords, '')
    .replace(vnCharacterSpeechAdverbs, '')
    .replace(vnCharacterCueActionSuffix, '')
    .replace(vnCharacterTitlePrefix, '')
    .trim()
  if (normalized.endsWith('大')) {
    const expanded = nearestFullNameForShortCue(sourceText, normalized.slice(0, -1))
    if (expanded) return expanded
  }
  const direct = canonicalVnCharacterName(normalized)
  if (direct) return direct
  const contextual = nearestFullNameForShortCue(sourceText, normalized)
  if (contextual) return contextual
  const uniqueKnownAbbreviation = Object.keys(vnCharacterAliases)
    .filter((name) => name.endsWith(normalized))
  return uniqueKnownAbbreviation.length === 1 ? uniqueKnownAbbreviation[0] : ''
}

const spokenVnCharacterName = (text: string, sourceText = text) => {
  const speechCue = /(?:说道|问道|答道|喝道|叫道|喊道|骂道|赞道|劝道|笑道|叹道|怒道|道|曰|说|问|答|喝|叫|喊|骂|赞|劝|笑|叹)[：，,:]?[“「]/g
  for (const cue of text.matchAll(speechCue)) {
    const cueIndex = cue.index || 0
    const contextStart = Math.max(0, cueIndex - 64)
    const context = text.slice(contextStart, cueIndex)
    let nearest: { name: string; index: number; aliasLength: number } | null = null
    for (const [name, aliases] of Object.entries(vnCharacterAliases)) {
      for (const alias of aliases) {
        const aliasIndex = context.lastIndexOf(alias)
        if (aliasIndex >= 0 && (!nearest || aliasIndex > nearest.index)) {
          nearest = { name, index: aliasIndex, aliasLength: alias.length }
        }
      }
    }
    if (nearest) {
      const afterKnownName = context.slice(nearest.index + nearest.aliasLength)
      if (/(?:一人|一位|一个|那人|其人|大汉|老人|男子|女子)/u.test(afterKnownName)) {
        return ''
      }
      return nearest.name
    }
    if (anonymousVnCharacterCue.test(`${context}${cue[0]}`)) return ''
  }

  let direct: { name: string; index: number } | null = null
  for (const [name, aliases] of Object.entries(vnCharacterAliases)) {
    for (const alias of aliases) {
      const match = new RegExp(`${alias}[：:][“「]`).exec(text)
      if (match && (!direct || match.index < direct.index)) direct = { name, index: match.index }
    }
  }
  if (direct) return direct.name

  // Compatibility fallback for any story character not present in the
  // curated cast. The source text remains the authority: only a concrete
  // subject directly attached to a speech cue or Name:“...” form is used.
  const directNamed = /(?:^|[，。！？；\s])([\p{L}\p{N}·・.' -]{2,32})[：:][“「]/u.exec(text)
  if (directNamed) {
    const named = cueSubjectVnCharacterName(directNamed[1], sourceText)
    if (named) return named
  }
  const cueNamed = /(?:^|[，。！？；\s])([\p{Script=Han}·・]{2,4}|[A-Z][A-Za-z0-9·・.' -]{1,31})(?:说道|问道|答道|喝道|叫道|喊道|骂道|赞道|劝道|笑道|叹道|怒道|说|曰)[：，,:]?[“「]/u.exec(text)
  return cueNamed ? cueSubjectVnCharacterName(cueNamed[1], sourceText) : ''
}

const buildChapterVnBeats = () => {
  const beats: VnBeat[] = []
  readerLines.value.forEach((line, sourceIndex) => {
    let quotedSpeaker = ''
    let quoteDepth = 0
    const chunks = splitVnText(line.text)
    const introducedNames = chunks.map(introducedVnCharacterName)
    chunks.forEach((text, sourceBeatIndex) => {
      const nearbyIntroducedName = introducedNames
        .slice(sourceBeatIndex, sourceBeatIndex + 2)
        .find(Boolean) || ''
      const inferredAnonymousSpeaker = (
        anonymousVnCharacterCue.test(text)
        ? nearbyIntroducedName
        : ''
      )
      const explicitSpeaker = line.speaker === '诗赞'
        ? '诗赞'
        : spokenVnCharacterName(text, line.text)
          || introducedNames[sourceBeatIndex]
          || inferredAnonymousSpeaker
      const speaker = explicitSpeaker || (quoteDepth > 0 && quotedSpeaker ? quotedSpeaker : line.speaker)
      const visibleCharacters = (
        nearbyIntroducedName
        && anonymousVnCharacterEntry.test(text)
        ? [nearbyIntroducedName]
        : []
      )
      beats.push({
        speaker,
        text,
        sourceIndex,
        sourceBeatIndex,
        source: 'chapter',
        visibleCharacters,
      })
      if (explicitSpeaker && explicitSpeaker !== '诗赞') quotedSpeaker = explicitSpeaker
      const opens = (text.match(/[“「]/g) || []).length
      const closes = (text.match(/[”」]/g) || []).length
      quoteDepth = Math.max(0, quoteDepth + opens - closes)
      if (quoteDepth === 0 && closes > 0) quotedSpeaker = ''
    })
  })
  return beats
}

const continuationBackgroundAssets = (assets: ContinuationVisualAsset[]) => {
  const backgrounds = assets.filter((asset) => asset.asset_type === 'background')
  if (backgrounds.length) return backgrounds
  // Older confirmed nodes predate asset_type in the response. Their single
  // frozen visual was the continuation background.
  return assets.length === 1 && !assets[0].asset_type ? assets : []
}

const buildContinuationVnBeats = (
  text: string,
  source: Exclude<VnBeatSource, 'chapter'>,
  continuationId: string,
  direction: string,
  assets: ContinuationVisualAsset[],
  sourceIndexStart: number,
) => {
  const paragraphs = text
    .replace(/\r/g, '')
    .split(/\n{2,}|\n(?=(?:#{1,6}\s*)?(?:[\u3400-\u9fffA-Za-z]{1,12}[：:]|[-*]\s))/)
    .map((paragraph) => paragraph
      .replace(/^#{1,6}\s*/, '')
      .replace(/^[-*]\s+/, '')
      .trim())
    .filter(Boolean)
  const beats: VnBeat[] = []

  paragraphs.forEach((paragraph, paragraphIndex) => {
    const speakerMatch = paragraph.match(/^([\u3400-\u9fffA-Za-z]{1,12})[：:]\s*([\s\S]+)$/)
    const paragraphSpeaker = speakerMatch?.[1]?.trim() || '旁白'
    const paragraphText = speakerMatch?.[2]?.trim() || paragraph
    let quotedSpeaker = paragraphSpeaker === '旁白' ? '' : paragraphSpeaker
    let quoteDepth = 0
    splitVnText(paragraphText, 118).forEach((chunk, sourceBeatIndex) => {
      const explicitSpeaker = spokenVnCharacterName(chunk, paragraphText)
      const speaker = explicitSpeaker || (quoteDepth > 0 && quotedSpeaker ? quotedSpeaker : paragraphSpeaker)
      beats.push({
        speaker,
        text: chunk,
        sourceIndex: sourceIndexStart + paragraphIndex,
        sourceBeatIndex,
        source,
        continuationId,
        direction,
      })
      if (explicitSpeaker) quotedSpeaker = explicitSpeaker
      const opens = (chunk.match(/[“「]/g) || []).length
      const closes = (chunk.match(/[”」]/g) || []).length
      quoteDepth = Math.max(0, quoteDepth + opens - closes)
      if (quoteDepth === 0 && closes > 0 && paragraphSpeaker === '旁白') quotedSpeaker = ''
    })
  })

  const backgrounds = continuationBackgroundAssets(assets)
  if (!backgrounds.length || !beats.length) return beats
  return beats.map((beat, index) => {
    // A one-image continuation uses the generated scene as an opening anchor,
    // then returns to semantic library matching instead of stretching one
    // background across the entire generated chapter.
    if (backgrounds.length === 1 && index >= Math.min(3, beats.length)) return beat
    const assetIndex = Math.min(
      backgrounds.length - 1,
      Math.floor((index * backgrounds.length) / beats.length),
    )
    const background = backgrounds[assetIndex]
    return {
      ...beat,
      backgroundUrl: background.media_url,
      backgroundLabel: background.description_cn || direction,
    }
  })
}

const chapterVnBeats = computed<VnBeat[]>(buildChapterVnBeats)
const chapterVnBeatCount = computed(() => chapterVnBeats.value.length)

const publicContinuationPathFor = (selectedId: string | null) => {
  if (!selectedId) return []
  const byId = new Map(publicContinuations.value.map((node) => [node.id, node]))
  const path: PublicContinuationNode[] = []
  const visited = new Set<string>()
  let continuationId: string | null = selectedId
  while (continuationId && !visited.has(continuationId)) {
    visited.add(continuationId)
    const node = byId.get(continuationId)
    if (!node) break
    path.push(node)
    continuationId = node.parent_continuation_id
  }
  return path.reverse()
}

const selectedPublicContinuationPath = computed(() => (
  publicContinuationPathFor(parentContinuationId.value)
))

const publicContinuationVnBeats = computed(() => selectedPublicContinuationPath.value.flatMap(
  (node, pathIndex) => buildContinuationVnBeats(
    node.continuation_text,
    'public_continuation',
    node.id,
    node.direction,
    node.assets,
    readerLines.value.length + pathIndex,
  )
))

const draftContinuationVnBeats = computed(() => {
  const continuation = generatedContinuation.value
  if (
    !continuation
    || !continuation.continuation_text
    || !continuationBundleReady.value
    || !['preview_ready', 'confirmed'].includes(continuation.status)
    || selectedPublicContinuationPath.value.some((node) => node.id === continuation.id)
  ) return []
  return buildContinuationVnBeats(
    continuation.continuation_text,
    'draft_continuation',
    continuation.id,
    continuation.direction || continuationDirection.value,
    draftContinuationAssets.value,
    readerLines.value.length + selectedPublicContinuationPath.value.length,
  )
})

const vnBeats = computed<VnBeat[]>(() => [
  ...chapterVnBeats.value,
  ...publicContinuationVnBeats.value,
  ...draftContinuationVnBeats.value,
])

const currentVnBeat = computed(() => vnBeats.value[vnBeatIndex.value] || {
  speaker: '旁白',
  text: '本回尚未载入。',
  sourceIndex: 0,
  sourceBeatIndex: 0,
  source: 'chapter' as const,
})
const vnProgress = computed(() => vnBeats.value.length
  ? Math.round(((vnBeatIndex.value + 1) / vnBeats.value.length) * 100)
  : 0)
const vnPreloadPercent = computed(() => (
  vnPreloadTotal.value
    ? Math.min(100, Math.round((vnPreloadLoaded.value / vnPreloadTotal.value) * 100))
    : 0
))
const vnSemanticVisualsPercent = computed(() => (
  vnSemanticVisualsTotal.value
    ? Math.min(100, Math.round((vnSemanticVisualsCompleted.value / vnSemanticVisualsTotal.value) * 100))
    : 0
))
const vnAtEnd = computed(() => vnBeats.value.length > 0 && vnBeatIndex.value >= vnBeats.value.length - 1)
const vnAtDraftEnd = computed(() => (
  vnAtEnd.value
  && currentVnBeat.value.source === 'draft_continuation'
  && generatedContinuation.value?.status === 'preview_ready'
))
const vnEndCueText = computed(() => {
  if (!vnAtEnd.value) return '左侧返回 · 右侧继续'
  if (vnAtDraftEnd.value) return '续写预览结束 · 点击左侧返回'
  if (currentVnBeat.value.source === 'public_continuation') return '分支终点 · 点击左侧返回'
  return '本回终 · 点击左侧返回'
})
const vnStageAriaLabel = computed(() => {
  if (vnBeatIndex.value <= 0) return '点击画面右侧进入下一幕'
  if (vnAtEnd.value) return '点击画面左侧返回上一幕'
  return '点击画面左侧返回上一幕，右侧进入下一幕'
})

const vnAssetMap = computed(() => new Map(
  [...vnBackgrounds.value, ...vnPortraits.value].map((asset) => [asset.stable_key, asset])
))

const vnSemanticBackgroundPlan = computed(() => buildSemanticBackgroundPlan(vnBeats.value))

const backgroundPlanForBeat = (index: number) => vnSemanticBackgroundPlan.value[index]

const backgroundFallbackKeys: Record<string, string> = {
  bg_three_kingdoms_peach_orchard: 'bg_historical_garden',
  bg_three_kingdoms_cave: 'bg_three_kingdoms_mountain_pass',
  bg_three_kingdoms_command_tent: 'bg_historical_inn_room',
  bg_three_kingdoms_fortress_camp: 'bg_historical_battlefield',
  bg_three_kingdoms_mountain_pass: 'bg_historical_city_gate',
  bg_three_kingdoms_forest_path: 'bg_historical_garden',
  bg_three_kingdoms_temple_courtyard: 'bg_historical_courtyard',
  bg_three_kingdoms_rural_village: 'bg_historical_street_day',
  bg_three_kingdoms_banquet_hall: 'bg_historical_inn_hall',
  bg_three_kingdoms_riverbank: 'bg_historical_bridge_river',
}

const backgroundKeyForBeat = (index: number) => {
  return backgroundPlanForBeat(index)?.backgroundKey || 'bg_historical_courtyard'
}

const semanticSceneRequestKeyForBeat = (index: number) => {
  const plan = backgroundPlanForBeat(index)
  const beat = vnBeats.value[index]
  if (!plan || !beat || beat.source !== 'chapter') return ''
  const anchorBeat = vnBeats.value[plan.sceneAnchorIndex]
  const anchorPlan = backgroundPlanForBeat(plan.sceneAnchorIndex)
  if (!anchorBeat || !anchorPlan || anchorBeat.source !== 'chapter') return ''
  return semanticSceneRequestKey(anchorPlan, anchorBeat)
}

const currentVnBackgroundKey = computed(() => backgroundKeyForBeat(vnBeatIndex.value))

const backgroundRunDepthForBeat = (index: number) => {
  const backgroundKey = backgroundKeyForBeat(index)
  let depth = 1
  for (let candidate = index - 1; candidate >= 0; candidate -= 1) {
    if (backgroundKeyForBeat(candidate) !== backgroundKey) break
    depth += 1
  }
  return depth
}

const backgroundSegmentOrdinalForBeat = (index: number) => {
  const backgroundKey = backgroundKeyForBeat(index)
  let ordinal = -1
  let previousKey = ''
  for (let candidate = 0; candidate <= index; candidate += 1) {
    const candidateKey = backgroundKeyForBeat(candidate)
    if (candidateKey === backgroundKey && candidateKey !== previousKey) ordinal += 1
    previousKey = candidateKey
  }
  return Math.max(0, ordinal)
}

const vnBackgroundTakeForBeat = (index: number): VnVisualTake => {
  const runBand = Math.floor((backgroundRunDepthForBeat(index) - 1) / 6)
  const segmentOrdinal = backgroundSegmentOrdinalForBeat(index)
  return ((segmentOrdinal + runBand) % 3) as VnVisualTake
}

const backgroundRequestKey = (backgroundKey: string, take: VnVisualTake) => `${backgroundKey}:take:${take}`
const currentVnBackgroundTake = computed(() => {
  const semanticSceneKey = semanticSceneRequestKeyForBeat(vnBeatIndex.value)
  if (semanticSceneKey && vnGeneratedSceneBackgrounds.value[semanticSceneKey]) {
    return backgroundPlanForBeat(vnBeatIndex.value)?.sceneTake || 0
  }
  return vnBackgroundTakeForBeat(vnBeatIndex.value)
})
const vnMediaIsUsable = (mediaUrl: string | undefined) => Boolean(
  mediaUrl && !vnFailedMediaUrls.value.includes(mediaUrl)
)
const vnMediaIsDecoded = (mediaUrl: string | undefined) => Boolean(
  mediaUrl && vnDecodedMediaUrls.value.includes(mediaUrl)
)

const vnBackgroundAssetForBeat = (index: number) => {
  const backgroundKey = backgroundKeyForBeat(index)
  const semanticSceneKey = semanticSceneRequestKeyForBeat(index)
  const semanticScene = semanticSceneKey
    ? vnGeneratedSceneBackgrounds.value[semanticSceneKey]
    : undefined
  if (semanticScene && vnMediaIsUsable(semanticScene.media_url)) return semanticScene
  const take = vnBackgroundTakeForBeat(index)
  const generated = vnGeneratedBackgrounds.value[backgroundRequestKey(backgroundKey, take)]
  if (generated && vnMediaIsUsable(generated.media_url)) return generated
  const library = vnAssetMap.value.get(backgroundKey)
  if (library && vnMediaIsUsable(library.media_url)) return library
  const fallbackKey = backgroundFallbackKeys[backgroundKey] || backgroundKey
  const fallback = vnAssetMap.value.get(fallbackKey)
  if (fallback && vnMediaIsUsable(fallback.media_url)) return fallback
  // Never substitute an arbitrary decoded background. That made a fast reader
  // see the same prison/courtyard image across unrelated semantic scenes.
  const neutralHistoricalFallback = vnAssetMap.value.get('bg_historical_courtyard')
  return neutralHistoricalFallback && vnMediaIsUsable(neutralHistoricalFallback.media_url)
    ? neutralHistoricalFallback
    : undefined
}

const vnReadyBackgroundAssetForBeat = (index: number) => {
  const preferred = vnBackgroundAssetForBeat(index)
  if (preferred && vnMediaIsDecoded(preferred.media_url)) return preferred

  const backgroundKey = backgroundKeyForBeat(index)
  const fallbackKey = backgroundFallbackKeys[backgroundKey] || backgroundKey
  const semanticSceneKey = semanticSceneRequestKeyForBeat(index)
  const candidates = [
    semanticSceneKey ? vnGeneratedSceneBackgrounds.value[semanticSceneKey] : undefined,
    vnAssetMap.value.get(backgroundKey),
    vnAssetMap.value.get(fallbackKey),
    ...Object.values(vnGeneratedBackgrounds.value)
      .filter((asset) => asset.background_key === backgroundKey),
    vnAssetMap.value.get('bg_historical_courtyard'),
  ]
  return candidates.find((asset) => (
    asset && vnMediaIsUsable(asset.media_url) && vnMediaIsDecoded(asset.media_url)
  )) || preferred
}

const currentVnBackground = computed(() => vnReadyBackgroundAssetForBeat(vnBeatIndex.value))
const currentVnBackgroundUrl = computed(() => {
  const continuationUrl = currentVnBeat.value.backgroundUrl
  if (
    continuationUrl
    && vnMediaIsUsable(continuationUrl)
    && vnMediaIsDecoded(continuationUrl)
  ) return continuationUrl
  return currentVnBackground.value?.media_url || ''
})
const portraitKeyByCharacter: Record<string, string> = {
  刘备: 'portrait_xianxia_young_scholar__serious',
  关羽: 'portrait_historical_guard__alert',
  张飞: 'portrait_historical_guard__neutral',
  诸葛亮: 'portrait_xianxia_young_scholar__neutral',
  赵云: 'portrait_historical_guard__alert',
  庞统: 'portrait_historical_official__serious',
  姜维: 'portrait_historical_guard__neutral',
  曹操: 'portrait_historical_official__serious',
  郭嘉: 'portrait_xianxia_young_scholar__neutral',
  荀彧: 'portrait_historical_official__neutral',
  司马懿: 'portrait_xianxia_sect_master__stern',
  曹丕: 'portrait_historical_emperor__stern',
  孙策: 'portrait_historical_guard__alert',
  孙权: 'portrait_historical_emperor__neutral',
  周瑜: 'portrait_xianxia_sword_master__determined',
  鲁肃: 'portrait_historical_official__neutral',
  陆逊: 'portrait_xianxia_young_scholar__serious',
  吕布: 'portrait_xianxia_sword_master__determined',
  貂蝉: 'portrait_historical_princess__serious',
  董卓: 'portrait_historical_emperor__stern',
  袁绍: 'portrait_historical_official__serious',
}

// Portraits are reserved for an actual speaker or a character performing a
// visible action. Single-character verbs such as “来 / 去 / 回 / 看” are too
// ambiguous in Chinese prose: “董卓后来……” used to match “来” and display a
// portrait even though the sentence only mentioned his name.
const vnCharacterActionPhrase = /(?:起身|站起|下马|上马|翻身上马|拔刀|拔剑|挥刀|挥剑|挥军|举刀|举剑|举枪|举杯|拜见|拜谢|下拜|跪下|跪拜|坐下|落座|奔来|奔出|赶来|赶到|追上|追赶|跃马|纵马|冲出|冲向|杀来|杀出|斩杀|交战|迎战|厮杀|大战|射箭|放箭|刺向|砍向|救下|救出|扶起|搀扶|持枪|执剑|提刀|舞刀|取出|拿出|放下|看见|望见|回头|回身|转身|退后|撤退|进城|入城|出城|走进|走出|卧倒|醒来|写下|写信|读罢|拆开|收起|藏起|献上|投降|攻打|攻城|守住|下令|传令|命令|吩咐|率领|引兵|领兵|迎上|送行|大笑|哭泣|怒喝|惊叫|叹息|喝道|叫道|问道|答道|倒地|受伤|中箭|中刀|战死|身亡|被杀)/

const vnCharacterPerformsAction = (text: string, aliases: string[]) => aliases.some((alias) => {
  let searchFrom = 0
  while (searchFrom < text.length) {
    const aliasIndex = text.indexOf(alias, searchFrom)
    if (aliasIndex < 0) return false
    const nearbyClause = text
      .slice(aliasIndex + alias.length)
      .split(/[。！？；“”「」]/, 1)[0]
      .slice(0, 24)
    if (vnCharacterActionPhrase.test(nearbyClause)) return true
    searchFrom = aliasIndex + alias.length
  }
  return false
})

const vnCharacterNamesForBeat = (beat: VnBeat) => {
  const found: string[] = []
  const speaker = canonicalVnCharacterName(beat.speaker)
  if (speaker && !backgroundOnlyCharacterLabels.has(speaker)) found.push(speaker)
  for (const visibleCharacter of beat.visibleCharacters || []) {
    const characterName = canonicalVnCharacterName(visibleCharacter)
    if (!characterName || backgroundOnlyCharacterLabels.has(characterName)) continue
    if (!found.includes(characterName)) found.push(characterName)
    if (found.length >= 2) return found
  }
  for (const character of characters) {
    if (found.includes(character.name)) continue
    if (backgroundOnlyCharacterLabels.has(character.name)) continue
    const aliases = vnCharacterAliases[character.name] || [character.name]
    const performsAction = vnCharacterPerformsAction(beat.text, aliases)
    if (performsAction) found.push(character.name)
    if (found.length >= 2) break
  }
  return found.slice(0, 2)
}

const activeVnCharacterNames = computed(() => vnCharacterNamesForBeat(currentVnBeat.value))

type VnCharacterMotion = 'breathe' | 'warm' | 'tense' | 'startled' | 'guarded' | 'combat'
interface VnExpressionChoice {
  variant: VnPortraitVariant
  label: string
  motion: VnCharacterMotion
  semantic: boolean
}

const vnVariantLabels: Record<VnPortraitVariant, string> = {
  neutral: '平静',
  smile: '微笑',
  angry: '愤怒',
  sad: '悲伤',
  surprise: '惊讶',
  fear: '警惕',
  combat: '迎战',
  command: '号令',
  thinking: '沉思',
  respect: '行礼',
  injured: '负伤',
}

const vnExpressionRules: Array<{
  pattern: RegExp
  variant: VnPortraitVariant
  motion: VnCharacterMotion
}> = [
  { pattern: /大惊|惊曰|惊问|惊叫|愕然|骇然|失色|吃惊|不意|忽见/, variant: 'surprise', motion: 'startled' },
  { pattern: /大怒|怒曰|怒喝|怒视|愤怒|厉声|叱曰|骂曰|咬牙|愤然|暴怒/, variant: 'angry', motion: 'tense' },
  { pattern: /大哭|哭曰|泣曰|流泪|垂泪|悲曰|哀叹|痛哭|伤感/, variant: 'sad', motion: 'guarded' },
  { pattern: /大恐|恐曰|惧曰|慌忙|胆寒|战栗|惊慌|惶恐|紧张|戒备/, variant: 'fear', motion: 'guarded' },
  { pattern: /大笑|笑曰|微笑|欣然|喜曰|喜道|称善|大喜/, variant: 'smile', motion: 'warm' },
  { pattern: /受伤|负伤|中箭|中刀|流血|吐血|病倒|昏倒|跌下马|倒在地上|力竭/, variant: 'injured', motion: 'guarded' },
  { pattern: /拔刀|拔剑|挥刀|挥剑|举枪|挺枪|冲杀|杀来|杀将来|杀出|混杀|迎战|交战|交锋|厮杀|大战|追杀|攻城|斩将|射箭/, variant: 'combat', motion: 'combat' },
  { pattern: /下令|传令|命令|吩咐|号令|挥军|指挥|点兵|调兵|喝令|令军士/, variant: 'command', motion: 'tense' },
  { pattern: /沉吟|思量|寻思|暗想|细想|谋划|商议|定计|献计|思索|踌躇/, variant: 'thinking', motion: 'breathe' },
  { pattern: /行礼|拜见|拜谢|作揖|拱手|跪拜|下拜|施礼|叩首/, variant: 'respect', motion: 'warm' },
]

const vnCharacterContext = (beat: VnBeat, characterName: string) => {
  const aliases = vnCharacterAliases[characterName] || [characterName]
  let localContext = beat.text
  const match = aliases
    .map((alias) => ({ alias, index: beat.text.indexOf(alias) }))
    .find((item) => item.index >= 0)
  if (match) {
    // Always read the clause around this exact character. A beat may mention
    // several people, and using the whole paragraph lets one person's injury
    // or anger leak onto another person's portrait.
    localContext = beat.text.slice(
      Math.max(0, match.index - 6),
      match.index + match.alias.length + 34,
    )
  }
  // The direction controls what prose and scenes get generated, but it is not
  // a per-frame expression label. Reading it here made a future "袁绍负伤"
  // direction show every earlier 袁绍 — and even other speakers — as injured.
  // Once prose exists, the current beat is the authoritative expression cue.
  return localContext
}

const vnExpressionForBeat = (beat: VnBeat, characterName: string): VnExpressionChoice => {
  const context = vnCharacterContext(beat, characterName)
  const matched = vnExpressionRules.find((rule) => rule.pattern.test(context))
  if (matched) {
    return {
      variant: matched.variant,
      label: vnVariantLabels[matched.variant],
      motion: matched.motion,
      semantic: true,
    }
  }
  return { variant: 'neutral', label: vnVariantLabels.neutral, motion: 'breathe', semantic: false }
}

const stableVisualHash = (value: string) => {
  let hash = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

const vnVisualSignatureForBeat = (index: number) => {
  const beat = vnBeats.value[index]
  if (!beat) return ''
  const cast = vnCharacterNamesForBeat(beat)
    .map((name) => `${name}:${vnExpressionForBeat(beat, name).variant}`)
    .join(',')
  return `${backgroundKeyForBeat(index)}|${cast}`
}

const vnVisualPresentation = computed(() => {
  const currentIndex = vnBeatIndex.value
  const signature = vnVisualSignatureForBeat(currentIndex)
  const backgroundKey = backgroundKeyForBeat(currentIndex)
  const recentIndices = Array.from({ length: Math.min(3, currentIndex) }, (_, index) => currentIndex - index - 1)
  const exactRepeats = recentIndices.filter((index) => vnVisualSignatureForBeat(index) === signature).length
  const backgroundRepeats = recentIndices.filter((index) => backgroundKeyForBeat(index) === backgroundKey).length
  let backgroundRunDepth = 1
  for (let index = currentIndex - 1; index >= 0 && backgroundRunDepth < 12; index -= 1) {
    if (backgroundKeyForBeat(index) !== backgroundKey) break
    backgroundRunDepth += 1
  }
  const hash = stableVisualHash(`${currentChapterNumber.value}:${currentIndex}:${signature}`)
  const semanticPlan = backgroundPlanForBeat(currentIndex)

  // Soft de-duplication: repeated frames are only down-ranked, never forbidden.
  const softVariation = exactRepeats >= 2
    || (exactRepeats === 1 && hash % 5 === 0)
    || (backgroundRepeats === 3 && hash % 3 === 0)
    || (backgroundRunDepth >= 5 && backgroundRunDepth % 2 === 0)
  const shots = ['push', 'drift-left', 'drift-right'] as const
  const compositions = ['close', 'offset', 'wide'] as const
  const grades = ['warm', 'cool', 'mist', 'ember'] as const
  const positions = ['center', '43% 48%', '57% 46%', '50% 42%'] as const
  const variationIndex = (hash + Math.floor(backgroundRunDepth / 2)) >>> 0
  const semanticMotion = semanticPlan?.mood === 'battle'
    ? 'push'
    : semanticPlan?.mood === 'tense'
      ? 'drift-right'
      : semanticPlan?.mood === 'storm' || semanticPlan?.mood === 'sorrow'
        ? 'drift-left'
        : 'still'
  const semanticComposition = semanticPlan?.mood === 'battle'
    ? 'wide'
    : semanticPlan?.mood === 'tense' || semanticPlan?.mood === 'sorrow'
      ? 'offset'
      : semanticPlan?.mood === 'ceremony'
        ? 'wide'
        : 'standard'
  const semanticGrade = semanticPlan?.mood === 'fire'
    ? 'ember'
    : semanticPlan?.mood === 'storm'
      ? 'mist'
      : semanticPlan?.mood === 'sorrow' || semanticPlan?.timeOfDay === 'night'
        ? 'cool'
        : semanticPlan?.mood === 'ceremony' || semanticPlan?.timeOfDay === 'dawn'
          ? 'warm'
          : 'natural'
  return {
    softVariation,
    backgroundShot: semanticMotion !== 'still'
      ? semanticMotion
      : softVariation
        ? shots[hash % shots.length]
        : 'still',
    composition: semanticComposition !== 'standard'
      ? semanticComposition
      : softVariation
        ? compositions[(hash >>> 3) % compositions.length]
        : 'standard',
    backgroundGrade: semanticGrade !== 'natural'
      ? semanticGrade
      : softVariation
        ? grades[variationIndex % grades.length]
        : 'natural',
    backgroundPosition: softVariation ? positions[(variationIndex >>> 2) % positions.length] : 'center',
  }
})

const portraitRequestKey = (
  characterName: string,
  variant: VnPortraitVariant,
  take: VnVisualTake = 0
) => `${characterName}:${variant}:take:${take}`

const portraitAssetDisplayUrl = (
  asset: VnGeneratedPortrait | VnLibraryAsset | undefined
) => {
  if (!asset) return ''
  if (asset.presentation_url && vnMediaIsUsable(asset.presentation_url)) {
    return asset.presentation_url
  }
  return vnMediaIsUsable(asset.media_url) ? asset.media_url : ''
}

const vnPortraitTakeForBeat = (
  beatIndex: number,
  characterName: string,
  variant: VnPortraitVariant
): VnVisualTake => {
  // Strong semantic expressions use the canonical camera. The backend rejects
  // expression + alternate-take combinations, and requesting them left a
  // visible silhouette when the 422 response arrived during chapter prep.
  if (variant !== 'neutral') return 0

  let runDepth = 1
  for (let candidate = beatIndex - 1; candidate >= 0; candidate -= 1) {
    const beat = vnBeats.value[candidate]
    const isSameCharacterState = vnCharacterNamesForBeat(beat).includes(characterName)
      && vnExpressionForBeat(beat, characterName).variant === variant
    if (!isSameCharacterState) break
    runDepth += 1
  }

  let segmentOrdinal = -1
  let wasActive = false
  for (let candidate = 0; candidate <= beatIndex; candidate += 1) {
    const beat = vnBeats.value[candidate]
    const isActive = vnCharacterNamesForBeat(beat).includes(characterName)
      && vnExpressionForBeat(beat, characterName).variant === variant
    if (isActive && !wasActive) segmentOrdinal += 1
    wasActive = isActive
  }
  const runBand = Math.floor((runDepth - 1) / 4)
  return ((Math.max(0, segmentOrdinal) + runBand) % 3) as VnVisualTake
}

const vnPortraitAssetForBeat = (
  characterName: string,
  variant: VnPortraitVariant,
  take: VnVisualTake
) => {
  const generated = vnGeneratedPortraits.value[portraitRequestKey(characterName, variant, take)]
  if (generated && portraitAssetDisplayUrl(generated)) return { asset: generated, exactTake: true }
  const neutralTake = vnGeneratedPortraits.value[portraitRequestKey(characterName, 'neutral', take)]
  if (neutralTake && portraitAssetDisplayUrl(neutralTake)) return { asset: neutralTake, exactTake: true }
  const neutralBase = vnGeneratedPortraits.value[portraitRequestKey(characterName, 'neutral', 0)]
  if (neutralBase && portraitAssetDisplayUrl(neutralBase)) return { asset: neutralBase, exactTake: false }
  const fallback = vnAssetMap.value.get(portraitKeyByCharacter[characterName])
  if (fallback && portraitAssetDisplayUrl(fallback)) return { asset: fallback, exactTake: false }
  // A missing identity must not silently become somebody else's portrait.
  // Preparation will generate the requested character or leave it absent.
  return { asset: undefined, exactTake: false }
}

const vnReadyPortraitAssetForDisplay = (
  characterName: string,
  preferred: ReturnType<typeof vnPortraitAssetForBeat>
) => {
  if (preferred.asset && vnMediaIsDecoded(portraitAssetDisplayUrl(preferred.asset))) return preferred
  const cachedForCharacter = Object.values(vnGeneratedPortraits.value).find((asset) => (
    asset.character_name === characterName
      && Boolean(portraitAssetDisplayUrl(asset))
      && vnMediaIsDecoded(portraitAssetDisplayUrl(asset))
  ))
  if (cachedForCharacter) return { asset: cachedForCharacter, exactTake: false }
  const library = vnAssetMap.value.get(portraitKeyByCharacter[characterName])
  if (library && portraitAssetDisplayUrl(library) && vnMediaIsDecoded(portraitAssetDisplayUrl(library))) {
    return { asset: library, exactTake: false }
  }
  return { asset: undefined, exactTake: false }
}

const activeVnCharacters = computed(() => activeVnCharacterNames.value.map((name, index) => {
  const character = characters.find((item) => item.name === name) || {
    name,
    courtesy: '正文新登场',
    faction: 'lords',
    vnId: `source_${stableVisualHash(name).toString(16)}`,
    role: '正文命名人物',
    traits: '由正文说话、入场或可见动作触发',
    desire: '',
    contradiction: '',
    arc: '',
  }
  const expression = vnExpressionForBeat(currentVnBeat.value, name)
  const take = vnPortraitTakeForBeat(vnBeatIndex.value, name, expression.variant)
  const resolved = vnReadyPortraitAssetForDisplay(
    name,
    vnPortraitAssetForBeat(name, expression.variant, take)
  )
  const canonicalSpeaker = canonicalVnCharacterName(currentVnBeat.value.speaker)
  return {
    ...character,
    side: index === 0 ? 'left' : 'right',
    asset: resolved.asset,
    portraitSource: resolved.asset && 'character_name' in resolved.asset ? 'generated' : resolved.asset ? 'library' : 'pending',
    variant: expression.variant,
    variantLabel: expression.label,
    take: resolved.exactTake ? take : 0,
    requestedTake: take,
    motion: expression.motion,
    isSpeaker: canonicalSpeaker === name || (!canonicalSpeaker && index === 0),
    generating: false,
    generationFailed: false,
  }
}))

const vnSceneLabel = computed(() => {
  if (currentVnBeat.value.source === 'draft_continuation') return 'AI CONTINUATION · 待采用分支'
  if (currentVnBeat.value.source === 'public_continuation') {
    return `PUBLIC BRANCH · ${currentVnBeat.value.direction || '读者续写'}`
  }
  const plan = backgroundPlanForBeat(vnBeatIndex.value)
  if (plan) return `${plan.cue} · ${plan.timeOfDay} · ${plan.mood}`.toUpperCase()
  return 'HISTORICAL SCENE'
})

// ============================================================
// Visual presentation: prefer normalized ``presentation_url`` over raw media_url
// ============================================================

const CANONICAL_PORTRAIT_WIDTH = 768
const CANONICAL_PORTRAIT_HEIGHT = 1280

type PortraitAsset = VnGeneratedPortrait | VnLibraryAsset | undefined

interface PresentationMeta {
  normalization_version?: string
  canvas_width?: number
  canvas_height?: number
  foreground_height_ratio?: number
  foreground_width_ratio?: number
  bottom_ratio?: number
  limiting_axis?: string
  scale_factor?: number
}

const characterPresentation = (asset: PortraitAsset): PresentationMeta | null => {
  if (!asset) return null
  if ('presentation' in asset && asset.presentation) {
    return asset.presentation as PresentationMeta
  }
  return null
}

const characterPortraitSrc = (asset: PortraitAsset): string => {
  return portraitAssetDisplayUrl(asset)
}

const onVnPortraitImageLoad = (asset: PortraitAsset) => {
  const mediaUrl = portraitAssetDisplayUrl(asset)
  if (mediaUrl && !vnDecodedMediaUrls.value.includes(mediaUrl)) {
    vnDecodedMediaUrls.value = [...vnDecodedMediaUrls.value, mediaUrl]
  }
  if (mediaUrl) {
    vnFailedMediaUrls.value = vnFailedMediaUrls.value.filter((url) => url !== mediaUrl)
  }
}

const onVnPortraitImageError = (asset: PortraitAsset) => {
  const failedUrl = portraitAssetDisplayUrl(asset)
  if (!failedUrl) return
  vnImagePreloadTasks.delete(failedUrl)
  if (!vnFailedMediaUrls.value.includes(failedUrl)) {
    vnFailedMediaUrls.value = [...vnFailedMediaUrls.value, failedUrl]
  }
  // If a normalized presentation file fails, the computed src immediately
  // falls back to the already versioned raw cutout instead of a silhouette.
  const fallbackUrl = asset?.media_url
  if (fallbackUrl && fallbackUrl !== failedUrl) void preloadVnImage(fallbackUrl)
}

const characterPortraitWidth = (asset: PortraitAsset): number => {
  const meta = characterPresentation(asset)
  return meta?.canvas_width || CANONICAL_PORTRAIT_WIDTH
}

const characterPortraitHeight = (asset: PortraitAsset): number => {
  const meta = characterPresentation(asset)
  return meta?.canvas_height || CANONICAL_PORTRAIT_HEIGHT
}

// ============================================================
// Visual debug overlay — dev only, gated by ?visualDebug=1
// ============================================================

const isVisualDebugEnabled = computed(() => {
  const raw = String(route.query.visualDebug || '').toLowerCase()
  return raw === '1' || raw === 'true' || raw === 'on'
})

const visualDebug = computed(() => ({
  stylePackVersion: 'three-kingdoms-ink-v2',
  normalizationVersion: 'canonical-canvas-v1',
}))


const vnProgressKey = () => `ifline:three-kingdoms:vn-progress:${currentChapterNumber.value}`
const saveVnProgress = () => window.localStorage.setItem(vnProgressKey(), String(vnBeatIndex.value))
const savedVnProgress = () => Number(window.localStorage.getItem(vnProgressKey()) || 0)
const restoreVnProgress = () => {
  const saved = savedVnProgress()
  vnBeatIndex.value = Number.isFinite(saved)
    ? Math.min(Math.max(0, saved), Math.max(0, vnBeats.value.length - 1))
    : 0
}
watch(
  () => `${parentContinuationId.value || ''}:${publicContinuationVnBeats.value.length}`,
  () => {
    if (!parentContinuationId.value || !publicContinuationVnBeats.value.length) return
    const saved = savedVnProgress()
    if (!Number.isFinite(saved) || saved < chapterVnBeatCount.value) return
    vnBeatIndex.value = Math.min(saved, Math.max(0, vnBeats.value.length - 1))
  },
)
let vnNavigationSerial = 0
const navigateVnTo = async (requestedIndex: number) => {
  const targetIndex = Math.min(
    Math.max(0, requestedIndex),
    Math.max(0, vnBeats.value.length - 1)
  )
  if (targetIndex === vnBeatIndex.value) return

  const serial = ++vnNavigationSerial
  const preloadSerial = vnPreloadSerial
  const targetMediaUrls = collectVnChapterMediaUrls(targetIndex, targetIndex + 1)
  const targetAlreadyDecoded = targetMediaUrls.length > 0
    && targetMediaUrls.every((mediaUrl) => vnMediaIsDecoded(mediaUrl))

  if (!targetAlreadyDecoded) {
    // Keep the current fully painted frame visible. Switch only after the
    // semantically matched target background and portrait have decoded.
    vnNavigationBusy.value = true
    await preloadVnRangeWithFallbacks(targetIndex, targetIndex + 1, preloadSerial, false)
    if (serial !== vnNavigationSerial || preloadSerial !== vnPreloadSerial) return
    const resolvedTargetUrls = collectVnChapterMediaUrls(targetIndex, targetIndex + 1)
    const targetResolved = resolvedTargetUrls.length > 0
      && resolvedTargetUrls.every((mediaUrl) => vnMediaIsDecoded(mediaUrl))
    if (!targetResolved) {
      vnNavigationBusy.value = false
      vnAssetsError.value = '下一幕的对应画面加载失败，已保留当前幕，请稍后重试。'
      return
    }
  }

  vnBeatIndex.value = targetIndex
  vnNavigationBusy.value = false
  vnAssetsError.value = ''
  saveVnProgress()

  // Refill a rolling window after each navigation. Normal reading stays
  // instant, while unusually fast skipping cannot expose an unrelated frame.
  void preloadVnRangeWithFallbacks(
    targetIndex + 1,
    Math.min(vnBeats.value.length, targetIndex + 9),
    preloadSerial,
    false,
  )
}
const advanceVn = () => {
  if (!vnAtEnd.value && !vnNavigationBusy.value) void navigateVnTo(vnBeatIndex.value + 1)
}
const rewindVn = () => {
  if (!vnNavigationBusy.value) void navigateVnTo(vnBeatIndex.value - 1)
}
const handleVnStageClick = (event: MouseEvent) => {
  const stage = event.currentTarget as HTMLElement | null
  if (!stage) return
  const bounds = stage.getBoundingClientRect()
  const clickedLeftHalf = event.clientX - bounds.left < bounds.width / 2
  if (clickedLeftHalf) rewindVn()
  else advanceVn()
}
const restartVn = () => {
  vnBeatIndex.value = 0
  saveVnProgress()
}

const scrollToVnPlayer = () => window.requestAnimationFrame(() => {
  document.querySelector('.vn-player')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
})

const scrollToContinuationReview = () => window.requestAnimationFrame(() => {
  document.querySelector('#continuation-vn-review')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
})

const openReaderEndMode = (mode: ReaderEndMode) => {
  readerEndMode.value = mode
  if (mode !== 'community') return
  if (!communityPreviewId.value && publicContinuations.value.length) {
    communityPreviewId.value = publicContinuations.value[0].id
  }
  if (!publicContinuations.value.length && !publicContinuationsLoading.value) {
    void loadPublicContinuations()
  }
}

const withVnRequestRetry = async <T>(request: () => Promise<T>, attempts = 2): Promise<T> => {
  let lastError: unknown
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await request()
    } catch (error: any) {
      lastError = error
      const status = Number(error?.response?.status || 0)
      const retryable = !status || status === 408 || status === 429 || status >= 500
      if (!retryable || attempt >= attempts - 1) throw error
      await new Promise((resolve) => window.setTimeout(resolve, 350 * (attempt + 1)))
    }
  }
  throw lastError
}

const loadVnAssets = async () => {
  if (vnBackgrounds.value.length && vnPortraits.value.length && vnCachedVisualsLoaded.value) return
  vnAssetsLoading.value = true
  vnAssetsError.value = ''
  try {
    await userStore.ensureGuest()
    const cachedVisualsPromise = withVnRequestRetry(
      () => guestStoryApi.listThreeKingdomsCachedVisuals()
    ).catch(() => null)
    const [backgrounds, historicalPortraits, xianxiaPortraits, cachedVisuals] = await Promise.all([
      withVnRequestRetry(
        () => libraryAssetApi.listByFacetValue({
          assetType: 'background', category: 'style', value: 'historical', limit: 100,
        }),
        3,
      ),
      withVnRequestRetry(
        () => libraryAssetApi.listByFacetValue({
          assetType: 'portrait', category: 'style', value: 'historical', limit: 100,
        }),
        3,
      ),
      withVnRequestRetry(
        () => libraryAssetApi.listByFacetValue({
          assetType: 'portrait', category: 'style', value: 'xianxia', limit: 100,
        }),
        3,
      ),
      cachedVisualsPromise,
    ])
    vnBackgrounds.value = backgrounds.data.items
    vnPortraits.value = [...historicalPortraits.data.items, ...xianxiaPortraits.data.items]
    vnGeneratedBackgrounds.value = Object.fromEntries(
      (cachedVisuals?.data.backgrounds || []).map((asset) => [
        backgroundRequestKey(asset.background_key, asset.take),
        asset,
      ])
    )
    vnGeneratedPortraits.value = Object.fromEntries(
      (cachedVisuals?.data.portraits || []).map((asset) => [
        portraitRequestKey(asset.character_name, asset.variant, asset.take),
        asset,
      ])
    )
    // Keep the library available even when the optional cache catalog is down,
    // but retry that catalog on the next chapter load instead of freezing an
    // incomplete cache view for the whole session.
    vnCachedVisualsLoaded.value = Boolean(cachedVisuals)
  } catch (error: any) {
    vnAssetsError.value = apiErrorMessage(error, '视觉素材载入失败，请稍后重试。')
    throw error
  } finally {
    vnAssetsLoading.value = false
  }
}

interface SemanticScenePreparationRequest {
  requestKey: string
  beatIndex: number
  sourceIndex: number
  sourceBeatIndex: number
  backgroundKey: string
  cue: string
  timeOfDay: string
  mood: string
  take: VnVisualTake
}

interface SemanticPortraitPreparationRequest {
  requestKey: string
  beatIndex: number
  characterName: string
  variant: VnPortraitVariant
  take: VnVisualTake
}

const semanticScenePreparationRequests = () => {
  const requests = new Map<string, SemanticScenePreparationRequest>()
  vnSemanticBackgroundPlan.value.forEach((plan, beatIndex) => {
    if (plan.sceneAnchorIndex !== beatIndex) return
    const beat = vnBeats.value[beatIndex]
    if (!beat || beat.source !== 'chapter' || beat.speaker === '诗赞') return
    const requestKey = semanticSceneRequestKey(plan, beat)
    requests.set(requestKey, {
      requestKey,
      beatIndex,
      sourceIndex: beat.sourceIndex,
      sourceBeatIndex: beat.sourceBeatIndex,
      backgroundKey: plan.backgroundKey,
      cue: plan.cue,
      timeOfDay: plan.timeOfDay,
      mood: plan.mood,
      take: plan.sceneTake,
    })
  })
  return [...requests.values()].sort((left, right) => left.beatIndex - right.beatIndex)
}

const semanticPortraitPreparationRequests = () => {
  const requests = new Map<string, SemanticPortraitPreparationRequest>()
  vnBeats.value.forEach((beat, beatIndex) => {
    vnCharacterNamesForBeat(beat).forEach((characterName) => {
      const expression = vnExpressionForBeat(beat, characterName)
      const take = vnPortraitTakeForBeat(beatIndex, characterName, expression.variant)
      const requestKey = portraitRequestKey(characterName, expression.variant, take)
      if (!requests.has(requestKey)) {
        requests.set(requestKey, {
          requestKey,
          beatIndex,
          characterName,
          variant: expression.variant,
          take,
        })
      }
    })
  })
  return [...requests.values()].sort((left, right) => {
    if (left.beatIndex !== right.beatIndex) return left.beatIndex - right.beatIndex
    if (left.variant === 'neutral' && right.variant !== 'neutral') return -1
    if (left.variant !== 'neutral' && right.variant === 'neutral') return 1
    return left.take - right.take
  })
}

let vnSemanticPreparationSerial = 0

const prepareVnSemanticVisuals = async () => {
  const serial = ++vnSemanticPreparationSerial
  const chapterNumber = currentChapterNumber.value
  const sceneRequests = semanticScenePreparationRequests()
  const portraitRequests = semanticPortraitPreparationRequests()

  // A completed chapter should reopen without replaying dozens of individual
  // cache probes. Fetch its validated content-scene catalog once, then only
  // generate the genuinely missing coordinates.
  try {
    const cachedSceneResponse = await guestStoryApi.listThreeKingdomsChapterSceneVisuals(chapterNumber)
    if (serial !== vnSemanticPreparationSerial || chapterNumber !== currentChapterNumber.value) return
    const requestKeyByCoordinate = new Map(
      sceneRequests.map((request) => [
        `${request.sourceIndex}:${request.sourceBeatIndex}`,
        request.requestKey,
      ])
    )
    const cachedScenes = Object.fromEntries(
      cachedSceneResponse.data.scenes.flatMap((asset) => {
        const requestKey = requestKeyByCoordinate.get(
          `${asset.source_index}:${asset.source_beat_index}`
        )
        return requestKey ? [[requestKey, asset]] : []
      })
    )
    vnGeneratedSceneBackgrounds.value = {
      ...vnGeneratedSceneBackgrounds.value,
      ...cachedScenes,
    }
  } catch {
    // Older deployments do not expose the chapter catalog. Individual ensure
    // requests below remain the compatibility path and still validate cache.
  }

  const pendingSceneRequests = sceneRequests.filter(
    (request) => !vnGeneratedSceneBackgrounds.value[request.requestKey]
  )
  const pendingPortraitRequests = portraitRequests.filter(
    (request) => !vnGeneratedPortraits.value[request.requestKey]
  )
  const tasks = [
    ...pendingSceneRequests.map((request) => async () => {
      if (vnGeneratedSceneBackgrounds.value[request.requestKey]) return
      const response = await withVnRequestRetry(
        () => guestStoryApi.ensureThreeKingdomsSceneVisual(
          chapterNumber,
          request.sourceIndex,
          request.sourceBeatIndex,
          request.backgroundKey,
          request.cue,
          request.timeOfDay,
          request.mood,
          request.take,
        )
      )
      if (serial !== vnSemanticPreparationSerial || chapterNumber !== currentChapterNumber.value) return
      vnGeneratedSceneBackgrounds.value = {
        ...vnGeneratedSceneBackgrounds.value,
        [request.requestKey]: response.data,
      }
    }),
    ...pendingPortraitRequests.map((request) => async () => {
      if (vnGeneratedPortraits.value[request.requestKey]) return
      const response = await withVnRequestRetry(
        () => guestStoryApi.ensureThreeKingdomsPortrait(
          request.characterName,
          request.variant,
          request.take,
        )
      )
      if (serial !== vnSemanticPreparationSerial || chapterNumber !== currentChapterNumber.value) return
      vnGeneratedPortraits.value = {
        ...vnGeneratedPortraits.value,
        [request.requestKey]: response.data,
      }
      const displayUrl = portraitAssetDisplayUrl(response.data)
      if (displayUrl) await preloadVnImage(displayUrl)
    }),
  ]

  vnSemanticVisualsCompleted.value = 0
  vnSemanticVisualsFailed.value = 0
  vnSemanticVisualsTotal.value = tasks.length
  vnSemanticVisualsPhase.value = tasks.length
    ? `正在补齐 ${pendingSceneRequests.length} 个正文场景和 ${pendingPortraitRequests.length} 个人物状态`
    : ''
  if (!tasks.length) return

  let cursor = 0
  const workerCount = Math.min(2, tasks.length)
  const workers = Array.from({ length: workerCount }, async () => {
    while (cursor < tasks.length && serial === vnSemanticPreparationSerial) {
      const taskIndex = cursor
      cursor += 1
      try {
        await tasks[taskIndex]()
      } catch {
        if (serial === vnSemanticPreparationSerial) vnSemanticVisualsFailed.value += 1
      } finally {
        if (serial === vnSemanticPreparationSerial) vnSemanticVisualsCompleted.value += 1
      }
    }
  })
  await Promise.all(workers)
  if (serial !== vnSemanticPreparationSerial) return
  vnSemanticVisualsPhase.value = vnSemanticVisualsFailed.value
    ? `${vnSemanticVisualsFailed.value} 个素材暂时使用图库兜底`
    : '正文场景和人物状态已全部准备完成'
}

const publicContinuationPortraitSignature = computed(() => (
  vnBeats.value
    .filter((beat) => beat.source === 'public_continuation')
    .flatMap((beat) => vnCharacterNamesForBeat(beat).map((characterName) => (
      `${characterName}:${vnExpressionForBeat(beat, characterName).variant}`
    )))
    .join('|')
))

watch(publicContinuationPortraitSignature, (signature, previous) => {
  if (!signature || signature === previous) return
  void prepareVnSemanticVisuals()
})

const vnImagePreloadTasks = new Map<string, Promise<boolean>>()
let vnPreloadSerial = 0
const VN_IMAGE_PRELOAD_TIMEOUT_MS = 25_000

const preloadVnImage = (mediaUrl: string): Promise<boolean> => {
  const existing = vnImagePreloadTasks.get(mediaUrl)
  if (existing) return existing
  const task = new Promise<boolean>((resolve) => {
    const image = new Image()
    let settled = false
    const finish = (loaded: boolean) => {
      if (settled) return
      settled = true
      window.clearTimeout(timeoutId)
      resolve(loaded)
    }
    const timeoutId = window.setTimeout(() => {
      image.src = ''
      finish(false)
    }, VN_IMAGE_PRELOAD_TIMEOUT_MS)
    image.decoding = 'async'
    image.onload = async () => {
      // A completed network request is not enough: wait for decode too, so the
      // first frame that uses this asset does not pause on a blank paint.
      await image.decode?.().catch(() => undefined)
      if (!vnDecodedMediaUrls.value.includes(mediaUrl)) {
        vnDecodedMediaUrls.value = [...vnDecodedMediaUrls.value, mediaUrl]
      }
      // A transient timeout must not poison this URL for the whole chapter.
      // Once a later request succeeds, semantic selection can use it again.
      vnFailedMediaUrls.value = vnFailedMediaUrls.value.filter((url) => url !== mediaUrl)
      finish(true)
    }
    image.onerror = () => finish(false)
    image.src = mediaUrl
  }).then((loaded) => {
    if (!loaded) vnImagePreloadTasks.delete(mediaUrl)
    return loaded
  })
  vnImagePreloadTasks.set(mediaUrl, task)
  return task
}

const collectVnChapterMediaUrls = (fromIndex = 0, toIndex = vnBeats.value.length) => {
  const mediaUrls = new Set<string>()
  vnBeats.value.slice(fromIndex, toIndex).forEach((beat, offset) => {
    const index = fromIndex + offset
    if (beat.backgroundUrl) mediaUrls.add(beat.backgroundUrl)
    const background = vnBackgroundAssetForBeat(index)
    if (background?.media_url) mediaUrls.add(background.media_url)
    vnCharacterNamesForBeat(beat).forEach((characterName) => {
      const expression = vnExpressionForBeat(beat, characterName)
      const take = vnPortraitTakeForBeat(index, characterName, expression.variant)
      const portrait = vnPortraitAssetForBeat(characterName, expression.variant, take).asset
      const displayUrl = portraitAssetDisplayUrl(portrait)
      if (displayUrl) mediaUrls.add(displayUrl)
    })
  })
  return [...mediaUrls]
}

const preloadVnMediaBatch = async (
  mediaUrls: string[],
  serial: number,
  trackProgress: boolean
) => {
  let cursor = 0
  const workerCount = Math.min(6, Math.max(1, mediaUrls.length))
  const workers = Array.from({ length: workerCount }, async () => {
    while (cursor < mediaUrls.length && serial === vnPreloadSerial) {
      const mediaUrl = mediaUrls[cursor]
      cursor += 1
      let loaded = await preloadVnImage(mediaUrl)
      if (!loaded) loaded = await preloadVnImage(mediaUrl)
      if (serial !== vnPreloadSerial) return
      if (loaded) {
        vnFailedMediaUrls.value = vnFailedMediaUrls.value.filter((url) => url !== mediaUrl)
      } else if (!vnFailedMediaUrls.value.includes(mediaUrl)) {
        vnFailedMediaUrls.value = [...vnFailedMediaUrls.value, mediaUrl]
      }
      if (trackProgress) vnPreloadLoaded.value += 1
    }
  })
  await Promise.all(workers)
}

const preloadVnRangeWithFallbacks = async (
  fromIndex: number,
  toIndex: number,
  serial: number,
  trackProgress: boolean
) => {
  const attemptedUrls = new Set<string>()
  while (serial === vnPreloadSerial) {
    const mediaUrls = collectVnChapterMediaUrls(fromIndex, toIndex)
      .filter((mediaUrl) => !attemptedUrls.has(mediaUrl))
    if (!mediaUrls.length) break
    mediaUrls.forEach((mediaUrl) => attemptedUrls.add(mediaUrl))
    if (trackProgress) vnPreloadTotal.value += mediaUrls.length
    await preloadVnMediaBatch(mediaUrls, serial, trackProgress)
  }
}

const prepareVnChapterAssets = async () => {
  const serial = ++vnPreloadSerial
  vnChapterAssetsReady.value = false
  vnFailedMediaUrls.value = []
  vnPreloadLoaded.value = 0
  vnPreloadTotal.value = 0

  const preloadStart = Math.max(0, vnBeatIndex.value - 3)
  const preloadEnd = Math.min(vnBeats.value.length, vnBeatIndex.value + 13)
  await preloadVnRangeWithFallbacks(preloadStart, preloadEnd, serial, true)
  if (serial !== vnPreloadSerial) return
  vnChapterAssetsReady.value = true

  // Continue in reading order without blocking the stage. These requests only
  // fetch/decode existing assets; they never invoke an AI generation endpoint.
  void (async () => {
    await preloadVnRangeWithFallbacks(preloadEnd, vnBeats.value.length, serial, false)
    if (serial === vnPreloadSerial && preloadStart > 0) {
      await preloadVnRangeWithFallbacks(0, preloadStart, serial, false)
    }
  })()
}

const continuationPlaceholder = computed(() => {
  if (continuationSuggestionBusy.value) return 'AI 正在分析前文并生成续写方向…'
  return continuationSuggestion.value || 'AI 将根据当前前文生成一个具体的续写方向。'
})

const continuationStatusLabel = computed(() => ({
  processing: '生成中',
  preview_ready: '待采用',
  confirmed: '已采用',
  failed: '生成失败',
  cancelled: '已取消'
}[generatedContinuation.value?.status || 'processing']))

const generatedImageUrl = computed(() => (
  draftContinuationAssets.value.find((asset) => asset.asset_type === 'background')?.media_url
  || draftContinuationAssets.value[0]?.media_url
  || ''
))
const generatedImageLoaded = computed(() => draftContinuationImagesReady.value)
const imageDisplayPending = computed(() => Boolean(generatedImageUrl.value && !generatedImageLoaded.value))

watch(() => draftContinuationAssets.value.map((asset) => asset.media_url).join('|'), () => {
  const activeUrls = new Set(draftContinuationAssets.value.map((asset) => asset.media_url))
  generatedImageLoadedUrls.value = generatedImageLoadedUrls.value.filter((url) => activeUrls.has(url))
  generatedImageFailedUrls.value = generatedImageFailedUrls.value.filter((url) => activeUrls.has(url))
}, { immediate: true })

const onGeneratedImageLoad = (mediaUrl: string) => {
  if (mediaUrl && !generatedImageLoadedUrls.value.includes(mediaUrl)) {
    generatedImageLoadedUrls.value = [...generatedImageLoadedUrls.value, mediaUrl]
  }
  if (mediaUrl && !vnDecodedMediaUrls.value.includes(mediaUrl)) {
    vnDecodedMediaUrls.value = [...vnDecodedMediaUrls.value, mediaUrl]
  }
  generatedImageFailedUrls.value = generatedImageFailedUrls.value.filter((url) => url !== mediaUrl)
}

const onGeneratedImageError = (mediaUrl: string) => {
  generatedImageLoadedUrls.value = generatedImageLoadedUrls.value.filter((url) => url !== mediaUrl)
  if (mediaUrl && !generatedImageFailedUrls.value.includes(mediaUrl)) {
    generatedImageFailedUrls.value = [...generatedImageFailedUrls.value, mediaUrl]
  }
}

const enterDraftContinuationInVn = async (continuationId: string) => {
  // Continuation portraits are derived from its actual prose and user
  // direction. Prepare and decode those expression variants before opening
  // the first draft frame, just like the generated continuation backgrounds.
  await prepareVnSemanticVisuals()
  const startIndex = chapterVnBeatCount.value + publicContinuationVnBeats.value.length
  const draftEndIndex = startIndex + draftContinuationVnBeats.value.length
  const mediaUrls = collectVnChapterMediaUrls(startIndex, draftEndIndex)
  await Promise.all(mediaUrls.map((mediaUrl) => preloadVnImage(mediaUrl)))
  if (
    generatedContinuation.value?.id !== continuationId
    || !continuationBundleReady.value
    || !draftContinuationVnBeats.value.length
  ) return
  lastAutoEnteredContinuationId.value = continuationId
  readerEndMode.value = 'continue'
  vnBeatIndex.value = startIndex
  saveVnProgress()
  scrollToVnPlayer()
}

watch(
  () => ({
    ready: continuationBundleReady.value,
    continuationId: generatedContinuation.value?.id || '',
    beatCount: draftContinuationVnBeats.value.length,
  }),
  ({ ready, continuationId, beatCount }) => {
    if (
      !ready
      || !continuationId
      || !beatCount
      || lastAutoEnteredContinuationId.value === continuationId
    ) return
    void enterDraftContinuationInVn(continuationId)
  },
)

watch(
  () => selectedPublicContinuationPath.value
    .flatMap((node) => node.assets.map((asset) => asset.media_url))
    .join('|'),
  (mediaUrlList) => {
    mediaUrlList.split('|').filter(Boolean).forEach((mediaUrl) => {
      void preloadVnImage(mediaUrl)
    })
  },
  { immediate: true },
)

const delay = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds))
const idempotencyKey = (prefix: string) => `${prefix}-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`}`

const apiErrorMessage = (error: any, fallback: string) => {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') return detail.message
  return fallback
}

const loadRuntimeProject = async () => {
  if (runtimeProject.value) return runtimeProject.value
  const response = await guestStoryApi.findThreeKingdomsRelease()
  const project = response.data.find((item) => item.title === '三国演义·公开互动版')
  if (!project) throw new Error('公开互动 Release 尚未部署')
  runtimeProject.value = project
  return project
}

const loadPublicContinuations = async () => {
  publicContinuationsLoading.value = true
  publicContinuationsError.value = ''
  try {
    const project = await loadRuntimeProject()
    const response = await guestStoryApi.listPublicContinuations(
      project.id,
      project.release_id,
      currentChapterNumber.value
    )
    publicContinuations.value = response.data.nodes
    if (!communityPreviewId.value || !response.data.nodes.some((node) => node.id === communityPreviewId.value)) {
      communityPreviewId.value = response.data.nodes[0]?.id || null
    }
  } catch (error: any) {
    publicContinuationsError.value = apiErrorMessage(error, '公共故事树暂时无法载入。')
  } finally {
    publicContinuationsLoading.value = false
  }
}

const chapterSessionPrefix = (releaseId: string) => `ifline:three-kingdoms:${releaseId}:chapter:`
const chapterSessionKey = (releaseId: string, chapterNumber = currentChapterNumber.value) => `${chapterSessionPrefix(releaseId)}${chapterNumber}`
const chapterSessionIndexKey = (releaseId: string) => `ifline:three-kingdoms:${releaseId}:chapter-sessions`
const pendingContinuationKey = (sessionId: string) => `ifline:three-kingdoms:pending-continuation:${sessionId}`

const rememberChapterSession = (releaseId: string, chapterNumber: number, sessionId: string) => {
  window.localStorage.setItem(chapterSessionKey(releaseId, chapterNumber), sessionId)
  let index: Array<{ chapterNumber: number; sessionId: string }> = []
  try {
    const parsed = JSON.parse(window.localStorage.getItem(chapterSessionIndexKey(releaseId)) || '[]')
    if (Array.isArray(parsed)) index = parsed
  } catch {
    index = []
  }
  const next = index
    .filter((entry) => Number(entry?.chapterNumber) !== chapterNumber)
    .concat({ chapterNumber, sessionId })
    .filter((entry) => Number.isInteger(Number(entry.chapterNumber)) && entry.sessionId)
    .slice(-120)
  window.localStorage.setItem(chapterSessionIndexKey(releaseId), JSON.stringify(next))
}

const storedChapterSessions = (releaseId: string) => {
  const prefix = chapterSessionPrefix(releaseId)
  const sessions = new Map<number, string>()
  for (let index = 0; index < window.localStorage.length; index += 1) {
    const key = window.localStorage.key(index)
    if (!key?.startsWith(prefix) || key === chapterSessionIndexKey(releaseId)) continue
    const chapterNumber = Number(key.slice(prefix.length))
    const sessionId = window.localStorage.getItem(key) || ''
    if (Number.isInteger(chapterNumber) && chapterNumber >= 1 && chapterNumber <= 120 && sessionId) {
      sessions.set(chapterNumber, sessionId)
    }
  }
  try {
    const indexed = JSON.parse(window.localStorage.getItem(chapterSessionIndexKey(releaseId)) || '[]')
    if (Array.isArray(indexed)) {
      for (const entry of indexed) {
        const chapterNumber = Number(entry?.chapterNumber)
        const sessionId = String(entry?.sessionId || '')
        if (Number.isInteger(chapterNumber) && chapterNumber >= 1 && chapterNumber <= 120 && sessionId) {
          sessions.set(chapterNumber, sessionId)
        }
      }
    }
  } catch {
    // Per-chapter keys remain authoritative if the optional index is malformed.
  }
  if (readingSession.value?.release_id === releaseId) {
    sessions.set(currentChapterNumber.value, readingSession.value.id)
  }
  return [...sessions].map(([chapterNumber, sessionId]) => ({ chapterNumber, sessionId }))
}

const readPendingContinuation = (sessionId: string): PendingContinuationRecord | null => {
  try {
    const raw = window.localStorage.getItem(pendingContinuationKey(sessionId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<PendingContinuationRecord>
    if (!parsed.continuationId || typeof parsed.direction !== 'string') return null
    return {
      continuationId: parsed.continuationId,
      direction: parsed.direction,
      startedAt: Number(parsed.startedAt) || Date.now(),
      visualMode: parsed.visualMode === 'user_upload' ? 'user_upload' : 'system_generate',
      upload: parsed.upload,
    }
  } catch {
    return null
  }
}

const savePendingContinuation = (sessionId: string, record: PendingContinuationRecord) => {
  window.localStorage.setItem(pendingContinuationKey(sessionId), JSON.stringify(record))
}

const clearPendingContinuation = (sessionId: string) => {
  window.localStorage.removeItem(pendingContinuationKey(sessionId))
}

const myBranchStatusLabel = (status: ContinuationStatus) => ({
  processing: '生成中',
  preview_ready: '待采用',
  confirmed: '已公开',
  failed: '生成失败',
  cancelled: '已取消',
}[status])

const loadMyContinuationBranches = async () => {
  if (myContinuationBranchesLoading.value) return
  myContinuationBranchesLoading.value = true
  myContinuationBranchesError.value = ''
  try {
    await userStore.ensureGuest()
    const project = await loadRuntimeProject()
    const storedSessions = storedChapterSessions(project.release_id)

    const branchGroups = await Promise.all(storedSessions.map(async ({ chapterNumber, sessionId }) => {
      try {
        const sessionResponse = await guestStoryApi.readSession(sessionId)
        const session = sessionResponse.data
        const chapterTitle = catalog.value?.chapters.find((chapter) => chapter.number === chapterNumber)?.title || `第 ${chapterNumber} 回`
        const entries: MyContinuationBranch[] = []
        const pending = readPendingContinuation(sessionId)
        if (pending) {
          try {
            const continuation = (await guestStoryApi.readContinuation(sessionId, pending.continuationId)).data
            entries.push({
              chapterNumber,
              chapterTitle,
              sessionId,
              continuationId: continuation.id,
              direction: continuation.direction || pending.direction,
              continuationText: continuation.continuation_text,
              status: continuation.status,
            })
          } catch {
            // Keep the session itself; a transient continuation read must not hide confirmed branches.
          }
        }
        if (session.selected_continuation_id && !entries.some((entry) => entry.continuationId === session.selected_continuation_id)) {
          const tree = await guestStoryApi.listPublicContinuations(project.id, project.release_id, chapterNumber)
          const selected = tree.data.nodes.find((node) => node.id === session.selected_continuation_id)
          if (selected) {
            entries.push({
              chapterNumber,
              chapterTitle,
              sessionId,
              continuationId: selected.id,
              direction: selected.direction,
              continuationText: selected.continuation_text,
              status: 'confirmed',
            })
          }
        }
        return entries
      } catch {
        return []
      }
    }))
    myContinuationBranches.value = branchGroups.flat().sort((a, b) => b.chapterNumber - a.chapterNumber)
  } catch (error: any) {
    myContinuationBranchesError.value = apiErrorMessage(error, '暂时无法找回你的续写分支。')
  } finally {
    myContinuationBranchesLoading.value = false
  }
}

const openMyContinuationBranch = async (branch: MyContinuationBranch) => {
  await openChapter(branch.chapterNumber)
}

const ensureReadingSession = async (): Promise<ReadingSession> => {
  await userStore.ensureGuest()
  const project = await loadRuntimeProject()
  if (readingSession.value?.project_id === project.id) return readingSession.value
  if (readingSessionPromise) return readingSessionPromise

  readingSessionPromise = (async () => {
    const storageKey = chapterSessionKey(project.release_id)
    const storedId = window.localStorage.getItem(storageKey)
    if (storedId) {
      try {
        const existing = await guestStoryApi.readSession(storedId)
        readingSession.value = existing.data
        parentContinuationId.value = existing.data.selected_continuation_id
        rememberChapterSession(project.release_id, currentChapterNumber.value, existing.data.id)
        return existing.data
      } catch {
        window.localStorage.removeItem(storageKey)
      }
    }

    const narrativeLines = readerLines.value.filter((line) => !/^〚\d+〛/.test(line.text.trim()))
    const chapterText = narrativeLines
      .map((line) => `${line.speaker === '旁白' ? '' : `${line.speaker}：`}${line.text}`)
      .join('\n\n')
      .slice(-12000)
    const chapterContinuationPoint = narrativeLines
      .slice(-6)
      .map((line) => `${line.speaker === '旁白' ? '' : `${line.speaker}：`}${line.text}`)
      .join('\n\n')
    const created = await guestStoryApi.startReadingSession(project.id, project.release_id, {
      source: '三国演义公开 120 回 VN JSON',
      chapter_number: currentChapterNumber.value,
      chapter_title: selectedChapter.value?.title || '',
      chapter_excerpt: selectedChapter.value?.excerpt || '',
      chapter_context: chapterText,
      chapter_continuation_point: chapterContinuationPoint,
    })
    readingSession.value = created.data
    parentContinuationId.value = created.data.selected_continuation_id
    rememberChapterSession(project.release_id, currentChapterNumber.value, created.data.id)
    return created.data
  })()

  try {
    return await readingSessionPromise
  } finally {
    readingSessionPromise = null
  }
}


const selectPublicContinuation = async (node: PublicContinuationNode) => {
  if (selectingContinuationId.value) return
  selectingContinuationId.value = node.id
  studioError.value = ''
  try {
    const path = publicContinuationPathFor(node.id)
    const pathMediaUrls = path.flatMap((item) => item.assets.map((asset) => asset.media_url))
    await Promise.all(pathMediaUrls.map((mediaUrl) => preloadVnImage(mediaUrl)))

    if (parentContinuationId.value !== node.id) {
      const session = await ensureReadingSession()
      const response = await guestStoryApi.selectContinuation(session.id, node.id)
      readingSession.value = response.data
    }
    parentContinuationId.value = node.id
    generatedContinuation.value = null
    imageAction.value = null
    continuationDirection.value = ''
    continuationSuggestion.value = ''
    readerEndMode.value = 'choices'
    communityPreviewId.value = node.id
    vnBeatIndex.value = Math.min(
      chapterVnBeatCount.value,
      Math.max(0, vnBeats.value.length - 1),
    )
    saveVnProgress()
    scrollToVnPlayer()
    void refreshContinuationSuggestion()
    void loadPublicContinuations()
  } catch (error: any) {
    studioError.value = apiErrorMessage(error, '选择故事节点失败，请刷新后重试。')
  } finally {
    selectingContinuationId.value = null
  }
}

const refreshContinuationSuggestion = async () => {
  const serial = ++continuationSuggestionSerial
  const previousSuggestion = continuationSuggestion.value
  continuationSuggestionBusy.value = true
  continuationSuggestionError.value = ''
  try {
    const session = await ensureReadingSession()
    const response = await guestStoryApi.suggestContinuationDirection(session.id)
    if (serial !== continuationSuggestionSerial) return
    const direction = response.data.direction.trim()
    continuationSuggestion.value = direction
    if (!continuationDirection.value || continuationDirection.value === previousSuggestion) {
      continuationDirection.value = direction
    }
  } catch (error: any) {
    if (serial !== continuationSuggestionSerial) return
    continuationSuggestionError.value = apiErrorMessage(error, 'AI 续写方向生成失败，请自行输入方向。')
  } finally {
    if (serial === continuationSuggestionSerial) continuationSuggestionBusy.value = false
  }
}

const pollContinuation = async (sessionId: string, continuationId: string) => {
  const startedAt = Date.now()
  const deadline = startedAt + 12 * 60 * 1000
  while (Date.now() < deadline) {
    const response = await guestStoryApi.readContinuation(sessionId, continuationId)
    generatedContinuation.value = response.data
    if (response.data.status !== 'processing') return response.data
    const elapsedRatio = (Date.now() - startedAt) / (deadline - startedAt)
    continuationPhase.value = `AI 正在续写… ${Math.min(95, Math.max(15, Math.round(15 + elapsedRatio * 80)))}%`
    await delay(document.hidden ? 3000 : 1500)
  }
  throw new Error('续写仍在后台生成。你可以离开页面，稍后从“我的续写分支”自动恢复。')
}

const generateContinuation = async () => {
  if (!continuationDirection.value || continuationBusy.value) return
  if (generatedContinuation.value?.status === 'preview_ready') {
    studioError.value = '请先采用当前续写，再生成下一段。'
    return
  }
  continuationBusy.value = true
  continuationPhase.value = '正在准备游客空间…'
  studioError.value = ''
  imageAction.value = null
  try {
    const session = await ensureReadingSession()
    const project = await loadRuntimeProject()
    let uploadedAssetVersionIds: string[] = []
    if (readerVisualMode.value === 'user_upload') {
      if (!readerUploadFile.value || !readerRightsAttested.value) {
        throw new Error('请选择图片并确认拥有使用权')
      }
      continuationPhase.value = '正在上传到会话私有隔离区…'
      if (!readerUploadResult.value) {
        const upload = await guestStoryApi.uploadReadingImage(
          project.id,
          session.id,
          readerUploadFile.value,
          continuationDirection.value,
          idempotencyKey('guest-reading-upload')
        )
        readerUploadResult.value = upload.data
      }
      uploadedAssetVersionIds = [readerUploadResult.value.asset_version_id]
    }
    continuationPhase.value = '正在提交续写任务…'
    const response = await guestStoryApi.continueStory(
      session.id,
      continuationDirection.value,
      parentContinuationId.value,
      readerVisualMode.value,
      uploadedAssetVersionIds,
      readerVisualMode.value === 'system_generate' ? 3 : 0,
      idempotencyKey('guest-continuation')
    )
    generatedContinuation.value = response.data
    savePendingContinuation(session.id, {
      continuationId: response.data.id,
      direction: continuationDirection.value,
      startedAt: Date.now(),
      visualMode: readerVisualMode.value,
      upload: readerUploadResult.value || undefined,
    })
    const result = await pollContinuation(session.id, response.data.id)
    if (result.status === 'failed' || result.status === 'cancelled') {
      clearPendingContinuation(session.id)
      throw new Error(result.error?.message || (result.status === 'failed' ? '续写生成失败' : '续写生成已取消'))
    }
    imagePrompt.value ||= `${selectedChapter.value?.title || '三国故事'}，${continuationDirection.value}，中国古典历史演义风格，电影感构图，无现代人物，无文字水印`
    if (result.asset_action_id) {
      imageBusy.value = true
      imagePhase.value = '正在取得系统场景图…'
      try {
        const imageResult = await pollImage(project.id, result.asset_action_id)
        if (imageResult.status === 'failed') throw new Error(imageResult.error?.message || 'AI 生图失败')
      } finally {
        imageBusy.value = false
      }
    }
  } catch (error: any) {
    if (readingSession.value && ['failed', 'cancelled'].includes(generatedContinuation.value?.status || '')) {
      clearPendingContinuation(readingSession.value.id)
    }
    studioError.value = apiErrorMessage(error, error?.message || '续写生成失败，请重试')
  } finally {
    continuationBusy.value = false
    continuationPhase.value = '正在准备游客空间…'
  }
}

const onReaderUploadChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  readerUploadFile.value = input.files?.[0] || null
  readerUploadResult.value = null
}

const confirmContinuation = async () => {
  if (!readingSession.value || !generatedContinuation.value || confirmBusy.value) return
  confirmBusy.value = true
  studioError.value = ''
  try {
    const response = await guestStoryApi.confirmContinuation(
      readingSession.value.id,
      generatedContinuation.value.id,
      readingSession.value.lock_version
    )
    generatedContinuation.value = response.data.continuation
    readingSession.value.lock_version = response.data.session_lock_version
    parentContinuationId.value = response.data.continuation.id
    continuationDirection.value = ''
    continuationSuggestion.value = ''
    clearPendingContinuation(readingSession.value.id)
    await loadPublicContinuations()
    vnBeatIndex.value = Math.max(0, vnBeats.value.length - 1)
    readerEndMode.value = 'choices'
    saveVnProgress()
    void loadMyContinuationBranches()
    void refreshContinuationSuggestion()
  } catch (error: any) {
    studioError.value = apiErrorMessage(error, '采用续写失败，请刷新后重试')
  } finally {
    confirmBusy.value = false
  }
}

const pollImage = async (projectId: number, actionId: string) => {
  const deadline = Date.now() + 15 * 60 * 1000
  while (Date.now() < deadline) {
    const response = await guestStoryApi.readAssetAction(projectId, actionId)
    imageAction.value = response.data
    imagePhase.value = `Qwen 正在生图… ${Math.round(response.data.progress || 0)}%`
    if (['awaiting_confirmation', 'ready', 'applied', 'failed', 'cancelled'].includes(response.data.status)) {
      return response.data
    }
    await delay(document.hidden ? 4000 : 2000)
  }
  throw new Error('场景图仍在后台生成。你可以离开页面，稍后从“我的续写分支”自动恢复。')
}

const generateImage = async () => {
  if (!generatedContinuation.value || !imagePrompt.value || imageBusy.value) return
  imageBusy.value = true
  imagePhase.value = '正在提交生图任务…'
  studioError.value = ''
  try {
    const session = await ensureReadingSession()
    const project = await loadRuntimeProject()
    const response = await guestStoryApi.generateContinuationImage(
      session.id,
      generatedContinuation.value.id,
      imagePrompt.value,
      idempotencyKey('guest-image')
    )
    imageAction.value = response.data
    const result = await pollImage(project.id, response.data.action_id)
    if (result.status === 'failed') throw new Error(result.error?.message || 'AI 生图失败')
    const refreshed = await guestStoryApi.readContinuation(
      session.id,
      generatedContinuation.value.id
    )
    generatedContinuation.value = refreshed.data
    if (refreshed.data.status === 'confirmed') await loadPublicContinuations()
  } catch (error: any) {
    studioError.value = apiErrorMessage(error, error?.message || 'AI 生图失败，请重试')
  } finally {
    imageBusy.value = false
    imagePhase.value = '正在提交生图任务…'
  }
}

const resumePendingContinuation = async () => {
  if (continuationBusy.value) return false
  const session = await ensureReadingSession()
  const pending = readPendingContinuation(session.id)
  if (!pending) return false

  continuationBusy.value = true
  continuationPhase.value = '正在恢复上次的续写任务…'
  studioError.value = ''
  continuationDirection.value ||= pending.direction
  readerVisualMode.value = pending.visualMode || 'system_generate'
  if (pending.upload) readerUploadResult.value = pending.upload
  try {
    const project = await loadRuntimeProject()
    const result = await pollContinuation(session.id, pending.continuationId)
    if (result.status === 'failed' || result.status === 'cancelled') {
      clearPendingContinuation(session.id)
      throw new Error(result.error?.message || (result.status === 'failed' ? '续写生成失败' : '续写生成已取消'))
    }
    if (result.status === 'confirmed') clearPendingContinuation(session.id)
    imagePrompt.value ||= `${selectedChapter.value?.title || '三国故事'}，${result.direction || pending.direction}，中国古典历史演义风格，电影感构图，无现代人物，无文字水印`
    if (result.asset_action_id) {
      imageBusy.value = true
      imagePhase.value = '正在恢复上次的场景图任务…'
      try {
        const imageResult = await pollImage(project.id, result.asset_action_id)
        if (imageResult.status === 'failed') throw new Error(imageResult.error?.message || 'AI 生图失败')
      } finally {
        imageBusy.value = false
      }
    }
    return true
  } catch (error: any) {
    if (['failed', 'cancelled'].includes(generatedContinuation.value?.status || '')) {
      clearPendingContinuation(session.id)
    }
    studioError.value = apiErrorMessage(error, error?.message || '恢复续写任务失败，请稍后重试')
    return false
  } finally {
    continuationBusy.value = false
    continuationPhase.value = '正在准备游客空间…'
  }
}

const resetStudioForChapter = () => {
  vnBeatIndex.value = 0
  vnNavigationSerial += 1
  vnNavigationBusy.value = false
  vnPreloadSerial += 1
  vnSemanticPreparationSerial += 1
  vnChapterAssetsReady.value = false
  vnGeneratedSceneBackgrounds.value = {}
  vnSemanticVisualsCompleted.value = 0
  vnSemanticVisualsTotal.value = 0
  vnSemanticVisualsFailed.value = 0
  vnSemanticVisualsPhase.value = ''
  vnPreloadLoaded.value = 0
  vnPreloadTotal.value = 0
  vnFailedMediaUrls.value = []
  continuationSuggestionSerial += 1
  readingSessionPromise = null
  readingSession.value = null
  publicContinuations.value = []
  publicContinuationsLoading.value = false
  publicContinuationsError.value = ''
  selectingContinuationId.value = null
  generatedContinuation.value = null
  lastAutoEnteredContinuationId.value = ''
  continuationDirection.value = ''
  continuationSuggestion.value = ''
  continuationSuggestionBusy.value = false
  continuationSuggestionError.value = ''
  parentContinuationId.value = null
  readerEndMode.value = 'choices'
  communityPreviewId.value = null
  imageAction.value = null
  readerUploadFile.value = null
  readerUploadResult.value = null
  readerRightsAttested.value = false
  studioError.value = ''
}

const loadCatalog = async () => {
  if (catalog.value) return
  catalogLoading.value = true
  catalogError.value = ''
  try {
    const response = await fetch(`${assetRoot}/catalog.json?v=${storyContentVersion}`, { cache: 'no-cache' })
    if (!response.ok) throw new Error(`目录请求失败（${response.status}）`)
    const data = await response.json() as ChapterCatalog
    if (data.chapterCount !== 120 || data.chapters.length !== 120) throw new Error('公开目录不是完整 120 回')
    catalog.value = data
  } catch (error) {
    catalogError.value = error instanceof Error ? error.message : '无法载入公开目录'
  } finally {
    catalogLoading.value = false
  }
}

const updateRoute = (section: StorySection, chapter?: number) => {
  const query: Record<string, string> = { section }
  if (chapter) query.chapter = String(chapter)
  router.replace({ path: route.path, query })
}

const scrollStoryToTop = () => {
  const scroller = storyPageRef.value
  if (scroller) {
    scroller.scrollTo({ top: 0, behavior: 'smooth' })
    return
  }
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

const setSection = async (section: StorySection) => {
  if (section === 'reader') {
    await openChapter(currentChapterNumber.value || 1)
    return
  }
  activeSection.value = section
  updateRoute(section)
  scrollStoryToTop()
  if (section === 'chapters') void loadMyContinuationBranches()
}

const chapterAssetUrl = (number: number) => (
  `${assetRoot}/chapters/${String(number).padStart(3, '0')}.json?v=${storyContentVersion}`
)

const openChapter = async (number: number) => {
  const safeNumber = Math.min(120, Math.max(1, Number(number) || 1))
  await loadCatalog()
  currentChapterNumber.value = safeNumber
  activeSection.value = 'reader'
  chapterLoading.value = true
  chapterError.value = ''
  chapterGraph.value = null
  resetStudioForChapter()
  updateRoute('reader', safeNumber)
  scrollStoryToTop()
  try {
    const response = await fetch(chapterAssetUrl(safeNumber), { cache: 'no-cache' })
    if (!response.ok) throw new Error(`第 ${safeNumber} 回请求失败（${response.status}）`)
    const graph = await response.json() as VNGraph
    if (graph.Version !== 1 || graph.StartNodeIndex !== 1 || !Array.isArray(graph.Nodes)) {
      throw new Error('本回 VN 图结构不完整')
    }
    chapterGraph.value = graph
    restoreVnProgress()
    await loadVnAssets()
    await prepareVnSemanticVisuals()
    await prepareVnChapterAssets()
    void loadPublicContinuations()
    void refreshContinuationSuggestion()
    void resumePendingContinuation()
  } catch (error) {
    chapterError.value = error instanceof Error ? error.message : '无法载入本回正文'
  } finally {
    chapterLoading.value = false
  }
}

const navigateChapter = (offset: number) => openChapter(currentChapterNumber.value + offset)

const openArc = (arc: number) => {
  selectedArc.value = arc
  setSection('chapters')
}

onMounted(async () => {
  await loadCatalog()
  if (activeSection.value === 'reader') await openChapter(currentChapterNumber.value)
  if (activeSection.value === 'chapters') void loadMyContinuationBranches()
})

// Keep this import type visibly exercised so graph Data remains checked if the
// VNSerializedValue contract evolves in VNNodeLibrary.txt.
const _serializedValueContract: VNSerializedValue['Kind'] = 'String'
void _serializedValueContract
</script>

<style scoped>
:global(body) {
  background: #eeeae1;
  color: #26231f;
}

.story-page {
  --ink: #22201d;
  --muted: #777066;
  --paper: #f8f5ee;
  --line: #d8d0c2;
  --red: #8f2f2b;
  --gold: #b9904a;
  height: calc(100dvh - 56px);
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior-y: contain;
  scrollbar-gutter: stable;
  background: #eeeae1;
  font-family: 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

button, input, select { font: inherit; }
button { cursor: pointer; }

.hero {
  position: relative;
  overflow: hidden;
  color: #f5efe4;
  background: #181a18;
  border-bottom: 1px solid #433d35;
}

.hero::before {
  position: absolute;
  inset: 0;
  content: '';
  background:
    radial-gradient(circle at 78% 35%, rgba(157, 75, 52, .19), transparent 26%),
    radial-gradient(circle at 16% 80%, rgba(185, 144, 74, .1), transparent 30%),
    linear-gradient(112deg, transparent 58%, rgba(255,255,255,.025) 58% 59%, transparent 59%);
}

.hero-pattern {
  position: absolute;
  width: 700px;
  height: 700px;
  right: -220px;
  top: -280px;
  border: 1px solid rgba(185,144,74,.16);
  border-radius: 50%;
  box-shadow: 0 0 0 72px rgba(185,144,74,.025), 0 0 0 144px rgba(185,144,74,.018);
}

.hero-inner {
  position: relative;
  z-index: 1;
  max-width: 1180px;
  min-height: 560px;
  margin: 0 auto;
  padding: 70px 34px 66px;
  display: grid;
  grid-template-columns: 1fr 280px;
  align-items: center;
  gap: 80px;
}

.eyebrow, .card-kicker, .section-heading > p, .subsection-heading > p {
  color: var(--gold);
  letter-spacing: .17em;
  font-size: 12px;
  font-weight: 700;
}

.eyebrow { display: flex; align-items: center; gap: 12px; }
.eyebrow span { width: 34px; height: 1px; background: var(--gold); }
.hero-author { margin-top: 38px; color: #a9a295; font: 14px/1.5 Georgia, serif; }
.hero h1 { margin: 2px 0 12px; font: 700 82px/1.08 'STKaiti', 'KaiTi', serif; letter-spacing: .12em; }
.hero-subtitle { color: #d4c6ad; font: 21px/1.65 'STKaiti', 'KaiTi', serif; }
.hero-logline { max-width: 760px; margin-top: 22px; color: #aaa49a; font-size: 15px; line-height: 1.9; }
.hero-actions { display: flex; gap: 12px; margin-top: 30px; }
.hero-actions button { padding: 12px 20px; border-radius: 2px; border: 1px solid var(--gold); transition: .2s ease; }
.primary-action { color: #1d1a16; background: var(--gold); }
.secondary-action { color: #e5dbc8; background: transparent; }
.hero-actions button:hover { transform: translateY(-2px); filter: brightness(1.08); }
.public-notice { margin-top: 20px; color: #8f998d; font-size: 12px; }
.public-notice span { margin-right: 8px; color: #a4bf9f; }

.hero-seal {
  width: 218px;
  height: 350px;
  padding: 23px 18px;
  justify-self: center;
  border: 1px solid rgba(185,144,74,.46);
  outline: 1px solid rgba(185,144,74,.2);
  outline-offset: 9px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: space-between;
  background: rgba(0,0,0,.12);
}
.hero-seal::before, .hero-seal::after { content: '◆'; color: var(--gold); font-size: 9px; }
.hero-seal strong { color: #c94c40; font: 700 70px/.95 'STKaiti', 'KaiTi', serif; text-align: center; text-shadow: 0 2px 20px rgba(0,0,0,.5); }
.hero-seal span { writing-mode: vertical-rl; letter-spacing: .18em; font: 13px/1 'STKaiti', 'KaiTi', serif; color: #b8ad9a; }
.hero-seal .seal-top, .hero-seal .seal-bottom { position: absolute; }
.hero-seal .seal-top { transform: translate(-84px, 20px); }
.hero-seal .seal-bottom { transform: translate(84px, 185px); }

.story-nav { position: sticky; top: 0; z-index: 50; background: rgba(248,245,238,.97); border-bottom: 1px solid var(--line); backdrop-filter: blur(10px); }
.story-nav-inner { max-width: 1180px; margin: 0 auto; padding: 0 24px; display: flex; overflow-x: auto; }
.story-nav button { flex: 0 0 auto; padding: 17px 18px 15px; color: #686158; background: none; border: 0; border-bottom: 2px solid transparent; font-size: 13px; }
.story-nav button span { margin-right: 7px; color: #aaa095; font-family: 'STKaiti', 'KaiTi', serif; }
.story-nav button:hover, .story-nav button.active { color: var(--red); border-bottom-color: var(--red); }

.story-main { min-height: 600px; }
.content-section, .reader-section { max-width: 1180px; margin: 0 auto; padding: 66px 34px 90px; }
.section-heading { max-width: 870px; margin-bottom: 46px; }
.section-heading.compact { margin-bottom: 38px; }
.section-heading h2 { margin: 10px 0 12px; color: var(--ink); font: 700 34px/1.35 'STKaiti', 'KaiTi', serif; }
.section-heading > span { color: var(--muted); font-size: 14px; line-height: 1.8; }
.subsection-heading { margin: 64px 0 26px; }
.subsection-heading h2 { margin-top: 8px; font: 700 28px/1.4 'STKaiti', 'KaiTi', serif; }

.stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); border: 1px solid var(--line); background: var(--paper); }
.stat-card { padding: 28px; border-right: 1px solid var(--line); }
.stat-card:last-child { border-right: 0; }
.stat-card strong { color: var(--red); font: 700 38px/1 Georgia, serif; }
.stat-card h3 { margin-top: 10px; font-size: 14px; }
.stat-card p { margin-top: 5px; color: var(--muted); font-size: 12px; line-height: 1.6; }

.overview-grid { margin-top: 26px; display: grid; grid-template-columns: 1.6fr .8fr; gap: 24px; }
.paper-card { background: var(--paper); border: 1px solid var(--line); }
.synopsis-card { padding: 36px; }
.paper-card h3 { margin: 8px 0 16px; font: 700 25px/1.4 'STKaiti', 'KaiTi', serif; }
.synopsis-card > p:not(.card-kicker) { color: #514b43; font-size: 15px; line-height: 2; }
.edition-note { margin-top: 28px; padding-top: 18px; border-top: 1px solid var(--line); display: grid; grid-template-columns: 80px 1fr; gap: 14px; }
.edition-note span { color: var(--red); font-size: 12px; font-weight: 700; }
.edition-note p { color: var(--muted); font-size: 12px; line-height: 1.7; }
.quote-card { padding: 36px; color: #eee5d5; background: #30332f; display: flex; flex-direction: column; justify-content: space-between; }
.quote-card p { margin: auto 0; font: 24px/1.9 'STKaiti', 'KaiTi', serif; }
.quote-card span { color: #91897d; font-size: 12px; }

.theme-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; border: 1px solid var(--line); background: var(--line); }
.theme-card { min-height: 220px; padding: 30px; background: var(--paper); }
.theme-card > span { color: #b9afa1; font: 12px Georgia, serif; }
.theme-card h3 { margin: 30px 0 12px; font: 700 21px 'STKaiti', 'KaiTi', serif; }
.theme-card p { color: var(--muted); font-size: 13px; line-height: 1.8; }
.open-callout { margin-top: 30px; padding: 28px 32px; color: #f2e9dc; background: var(--red); display: flex; align-items: center; justify-content: space-between; }
.open-callout p { color: #d7aaa4; font-size: 12px; }
.open-callout h3 { margin-top: 4px; font: 700 22px 'STKaiti', 'KaiTi', serif; }
.open-callout button { color: #fff; background: none; border: 0; }

.bible-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; }
.bible-card { padding: 30px; }
.bible-card dl { border-top: 1px solid var(--line); }
.bible-card dl > * { padding-top: 13px; }
.bible-card dt { color: var(--red); font-size: 12px; font-weight: 700; }
.bible-card dd { margin: 5px 0 0; padding-bottom: 13px; color: #5f584f; border-bottom: 1px solid #e7e0d5; font-size: 13px; line-height: 1.75; }
.guardrail-panel { margin-top: 24px; padding: 34px; color: #e7ded0; background: #2c2e2b; display: grid; grid-template-columns: 190px 1fr; gap: 38px; }
.guardrail-title span { color: var(--gold); font: 10px/1.6 Georgia, serif; letter-spacing: .13em; }
.guardrail-title h3 { margin-top: 18px; font: 700 28px 'STKaiti', 'KaiTi', serif; }
.guardrail-panel ol { counter-reset: item; list-style: none; display: grid; grid-template-columns: 1fr 1fr; gap: 14px 28px; }
.guardrail-panel li { color: #bdb6aa; font-size: 13px; line-height: 1.75; }
.guardrail-panel li::before { counter-increment: item; content: counter(item, decimal-leading-zero); margin-right: 10px; color: #9e4b45; font: 11px Georgia, serif; }
.relations-card { margin-top: 24px; padding: 32px; background: #e5ded1; display: grid; grid-template-columns: 220px 1fr; gap: 30px; }
.relations-card h3 { margin-top: 8px; font: 700 24px 'STKaiti', 'KaiTi', serif; }
.relation-list p { padding: 12px 0; border-bottom: 1px solid #ccc1b2; display: grid; grid-template-columns: 220px 1fr; gap: 20px; }
.relation-list p:last-child { border-bottom: 0; }
.relation-list strong { font-size: 13px; }
.relation-list span { color: #6e665d; font-size: 12px; line-height: 1.6; }

.faction-filter { margin-bottom: 20px; display: flex; gap: 8px; flex-wrap: wrap; }
.faction-filter button { padding: 8px 14px; color: #655e56; border: 1px solid var(--line); background: var(--paper); border-radius: 20px; font-size: 12px; }
.faction-filter button.active { color: #fff; background: var(--faction-color, var(--red)); border-color: var(--faction-color, var(--red)); }
.faction-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 24px; }
.faction-strip article { padding: 18px; background: var(--paper); border: 1px solid var(--line); display: flex; gap: 13px; }
.faction-strip article > span { width: 4px; flex: 0 0 4px; background: var(--faction-color); }
.faction-strip h3 { font: 700 18px 'STKaiti', 'KaiTi', serif; }
.faction-strip p { margin-top: 5px; color: var(--muted); font-size: 11px; line-height: 1.6; }
.faction-strip small { display: block; margin-top: 8px; color: var(--faction-color); font-size: 10px; }
.character-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
.character-card { padding: 24px; background: var(--paper); border: 1px solid var(--line); }
.character-card header { display: grid; grid-template-columns: 48px 1fr auto; gap: 12px; align-items: center; padding-bottom: 16px; border-bottom: 1px solid var(--line); }
.character-monogram { width: 48px; height: 48px; color: #f4eee3; background: var(--red); display: grid; place-items: center; font: 700 23px 'STKaiti', 'KaiTi', serif; }
.character-card h3 { font: 700 21px 'STKaiti', 'KaiTi', serif; }
.character-card header p { margin-top: 3px; color: var(--muted); font-size: 11px; }
.vn-id { color: #8b8277; font: 10px Consolas, monospace; }
.character-card dl { margin-top: 14px; display: grid; gap: 9px; }
.character-card dl div { display: grid; grid-template-columns: 72px 1fr; gap: 10px; }
.character-card dt { color: #9a4e47; font-size: 11px; font-weight: 700; }
.character-card dd { color: #5c554d; font-size: 12px; line-height: 1.65; }

.timeline { position: relative; }
.timeline::before { content: ''; position: absolute; left: 26px; top: 30px; bottom: 30px; width: 1px; background: #c9bfb1; }
.timeline-item { position: relative; display: grid; grid-template-columns: 54px 110px 1fr auto; gap: 22px; align-items: start; padding: 24px 0; border-bottom: 1px solid var(--line); }
.timeline-marker { position: relative; z-index: 1; width: 54px; height: 54px; color: #fff; background: var(--red); display: grid; place-items: center; font: 14px Georgia, serif; }
.timeline-years { padding-top: 4px; color: var(--red); font: 13px Georgia, serif; }
.timeline-years small { display: block; margin-top: 8px; color: var(--muted); font-family: inherit; }
.timeline-copy h3 { font: 700 22px 'STKaiti', 'KaiTi', serif; }
.timeline-copy > p { margin-top: 7px; color: #5c554d; font-size: 13px; line-height: 1.75; }
.timeline-copy > div { margin-top: 9px; color: #766e64; font-size: 11px; }
.timeline-copy > div span { margin-right: 8px; color: var(--gold); font-weight: 700; }
.timeline-item > button { margin-top: 8px; color: var(--red); background: none; border: 0; font-size: 11px; }

.my-branch-directory { margin-bottom: 18px; padding: 20px; background: #282b28; border: 1px solid #454942; color: #e9dfd0; }
.my-branch-directory > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.my-branch-directory > header p { color: var(--gold); font: 8px/1 Consolas, monospace; letter-spacing: .14em; }
.my-branch-directory > header h3 { margin-top: 5px; font: 700 21px/1.35 'STKaiti', 'KaiTi', serif; }
.my-branch-directory > header > span { color: #a8aea4; font-size: 10px; }
.my-branch-intro { margin: 10px 0 14px; color: #969d93; font-size: 10px; line-height: 1.65; }
.my-branch-empty { padding: 18px; color: #aab0a6; background: #202320; border: 1px solid #3d413b; font-size: 11px; }
.my-branch-empty.is-error { color: #f1c4be; }
.my-branch-list { display: grid; gap: 9px; }
.my-branch-list > article { padding: 14px; display: grid; grid-template-columns: 64px 1fr auto; gap: 14px; align-items: center; background: #202320; border: 1px solid #3d413b; }
.my-branch-chapter { color: var(--gold); font: 18px/1 Georgia, serif; }
.my-branch-chapter small { display: block; margin-bottom: 5px; color: #7f867d; font: 7px/1 Consolas, monospace; letter-spacing: .12em; }
.my-branch-copy { min-width: 0; }
.my-branch-copy header { display: flex; justify-content: space-between; gap: 12px; }
.my-branch-copy header strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 600 13px/1.4 'STKaiti', 'KaiTi', serif; }
.my-branch-copy header span { padding: 3px 6px; color: #cfc3b2; background: #454038; font-size: 8px; white-space: nowrap; }
.my-branch-copy header .status-confirmed { color: #eef5eb; background: #577153; }
.my-branch-copy header .status-failed, .my-branch-copy header .status-cancelled { color: #fff; background: #8f2f2b; }
.my-branch-copy p { margin-top: 6px; color: #d7cbb9; font-size: 11px; line-height: 1.55; }
.my-branch-copy > small { margin-top: 5px; color: #888f86; font-size: 9px; line-height: 1.55; display: -webkit-box; overflow: hidden; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.my-branch-list button { padding: 9px 11px; color: #1e1c18; background: var(--gold); border: 1px solid var(--gold); font-size: 10px; white-space: nowrap; }
.my-branch-list button:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }
.chapter-toolbar { position: sticky; top: 52px; z-index: 20; margin-bottom: 18px; padding: 12px; background: rgba(238,234,225,.96); border: 1px solid var(--line); display: grid; grid-template-columns: 1fr 220px auto; gap: 10px; }
.search-box { padding: 0 13px; background: var(--paper); border: 1px solid #ccc3b6; display: flex; align-items: center; gap: 10px; }
.search-box span { color: #999083; font-size: 20px; }
.search-box input { width: 100%; padding: 11px 0; color: #38332d; background: transparent; border: 0; outline: none; }
.chapter-toolbar select, .reader-toolbar select { padding: 10px 12px; color: #4e4841; background: var(--paper); border: 1px solid #ccc3b6; outline: none; }
.search-box:focus-within { border-color: var(--red); box-shadow: 0 0 0 2px rgba(142, 43, 37, .16); }
.chapter-toolbar select:focus-visible, .reader-toolbar select:focus-visible { border-color: var(--red); box-shadow: 0 0 0 2px rgba(142, 43, 37, .16); }
.result-count { padding: 12px 8px; color: var(--muted); font-size: 12px; white-space: nowrap; }
.chapter-list-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
.chapter-list-grid > article { position: relative; min-height: 190px; padding: 22px; background: var(--paper); border: 1px solid var(--line); display: grid; grid-template-columns: 76px 1fr 24px; gap: 18px; transition: transform .2s ease, border-color .2s ease, box-shadow .2s ease; cursor: pointer; content-visibility: auto; contain-intrinsic-size: 190px; }
.chapter-list-grid > article:hover { transform: translateY(-2px); border-color: #aa8d66; box-shadow: 0 10px 22px rgba(54,44,34,.08); }
.chapter-list-grid > article:focus-visible { outline: 3px solid var(--red); outline-offset: 2px; }
.chapter-number { color: var(--red); font: 22px Georgia, serif; }
.chapter-number small { display: block; margin-bottom: 5px; color: #a99f92; font-size: 8px; letter-spacing: .12em; }
.chapter-copy > span { color: #a07749; font-size: 10px; }
.chapter-copy h3 { margin: 5px 0 8px; font: 700 17px/1.5 'STKaiti', 'KaiTi', serif; }
.chapter-copy p { color: var(--muted); font-size: 11px; line-height: 1.65; display: -webkit-box; overflow: hidden; -webkit-line-clamp: 3; -webkit-box-orient: vertical; }
.chapter-copy footer { margin-top: 10px; color: #9c9387; font-size: 9px; }
.chapter-open-cue { color: var(--red); align-self: center; font-size: 18px; }
.loading-state, .error-state, .empty-results { padding: 70px 20px; text-align: center; color: var(--muted); background: var(--paper); border: 1px solid var(--line); }
.error-state { color: #9a3934; }

.reference-banner { margin-bottom: 24px; padding: 26px 30px; color: #e9e1d5; background: #292c29; display: flex; align-items: center; gap: 22px; }
.reference-icon { width: 58px; height: 58px; color: var(--gold); border: 1px solid #5f594f; display: grid; place-items: center; font: 16px Consolas, monospace; }
.reference-banner p { color: #a49b8e; font-size: 10px; letter-spacing: .12em; }
.reference-banner h3 { margin: 5px 0; color: #d2ad6e; font: 13px Consolas, monospace; word-break: break-all; }
.reference-banner span { color: #9c958b; font-size: 11px; }
.node-flow { padding: 28px; background: #f8f5ee; border: 1px solid var(--line); overflow-x: auto; }
.flow-main { min-width: 850px; display: flex; align-items: center; gap: 12px; }
.flow-main > div { min-width: 150px; padding: 16px; border: 1px solid #cbbda9; background: #fffdfa; }
.flow-main .flow-wide { min-width: 210px; }
.flow-main small { display: block; color: #9a7961; font: 9px Consolas, monospace; }
.flow-main strong { display: block; margin: 7px 0; font-size: 13px; }
.flow-main span { color: var(--muted); font-size: 9px; }
.flow-main i { color: var(--red); font-style: normal; }
.flow-action { margin-top: 22px; padding-left: 174px; display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.flow-action span { margin-right: 10px; color: var(--muted); font-size: 10px; }
.flow-action b { padding: 6px 9px; color: #756d63; background: #e9e2d7; font: 9px Consolas, monospace; }
.node-mapping-grid { margin-top: 20px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.node-mapping-grid article { padding: 20px; background: var(--paper); border: 1px solid var(--line); }
.node-mapping-grid article > p { color: #9c4a43; font: 9px Consolas, monospace; }
.node-mapping-grid h3 { margin: 12px 0 9px; font: 700 16px Consolas, monospace; }
.node-mapping-grid span { min-height: 60px; display: block; color: var(--muted); font-size: 11px; line-height: 1.7; }
.node-mapping-grid code { display: block; margin-top: 12px; color: #776c5f; font-size: 9px; }
.contract-grid { margin-top: 20px; display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.contract-grid .paper-card { padding: 28px; }
.contract-grid ul { padding-left: 18px; color: #5f584f; font-size: 12px; line-height: 2; }
.json-preview { color: #ded8cd; background: #202320; border: 1px solid #343932; }
.code-header { padding: 12px 16px; border-bottom: 1px solid #3b3e39; display: flex; justify-content: space-between; }
.code-header span, .code-header a { color: #a7a196; font: 10px Consolas, monospace; text-decoration: none; }
.code-header a { color: #caa66d; }
.json-preview pre { padding: 24px; color: #b8c9ab; font: 12px/1.7 Consolas, monospace; white-space: pre-wrap; }

.reader-section { max-width: 1000px; }
.reader-toolbar { position: sticky; top: 52px; z-index: 20; padding: 10px; background: rgba(238,234,225,.97); border: 1px solid var(--line); display: grid; grid-template-columns: auto 1fr auto; gap: 10px; }
.reader-toolbar button { padding: 9px 12px; color: #5f574e; background: var(--paper); border: 1px solid #ccc3b6; }
.reader-toolbar div { display: flex; gap: 6px; }
.reader-toolbar button:disabled, .reader-footer button:disabled { opacity: .38; cursor: not-allowed; }
.reader-loading { min-height: 360px; margin-top: 18px; padding: 48px; display: grid; place-content: center; gap: 12px; text-align: center; color: #5f574e; background: #e6ded1; border: 1px solid var(--line); }
.reader-loading strong { font: 700 24px/1.45 'STKaiti', 'KaiTi', serif; }
.reader-loading span { color: var(--red); font-size: 12px; }
.reader-loading small { max-width: 420px; color: #82786b; font-size: 10px; line-height: 1.7; }
.reader-preload-progress { width: min(420px, 72vw); margin: 4px auto 2px; display: grid; grid-template-columns: minmax(0, 1fr) 48px; align-items: center; gap: 12px; }
.reader-preload-track { position: relative; height: 8px; overflow: hidden; background: rgba(94,83,69,.12); border: 1px solid rgba(111,95,75,.24); box-shadow: inset 0 1px 2px rgba(61,50,38,.08); }
.reader-preload-track::after { position: absolute; inset: 1px; content: ''; opacity: .38; background: repeating-linear-gradient(90deg, transparent 0, transparent calc(25% - 1px), rgba(85,71,55,.24) calc(25% - 1px), rgba(85,71,55,.24) 25%); pointer-events: none; }
.reader-preload-track i { position: absolute; z-index: 1; inset: 0 auto 0 0; display: block; min-width: 0; background: linear-gradient(90deg, #7d2b28, var(--red) 62%, #bd7b49); box-shadow: 0 0 12px rgba(143,47,43,.24); transition: width .28s cubic-bezier(.22,.8,.28,1); }
.reader-preload-progress output { color: #74695c; font: 10px/1 Consolas, monospace; text-align: right; }
.reader-preload-progress.is-indeterminate .reader-preload-track i { width: 34% !important; animation: reader-preload-search 1.15s ease-in-out infinite; }
.reader-preload-progress.is-indeterminate output { font-family: 'STKaiti', 'KaiTi', serif; }
@keyframes reader-preload-search {
  0% { transform: translateX(-110%); }
  55% { transform: translateX(115%); }
  100% { transform: translateX(310%); }
}
.reader-paper { margin-top: 18px; background: #fbf8f1; border: 1px solid var(--line); box-shadow: 0 18px 50px rgba(71,59,44,.1); }
.reader-paper > header { padding: 58px 70px 38px; text-align: center; border-bottom: 1px solid #e2dbd0; }
.reader-paper > header > p { color: #9c4a43; font-size: 11px; letter-spacing: .12em; }
.reader-paper > header h1 { max-width: 720px; margin: 14px auto 18px; font: 700 32px/1.55 'STKaiti', 'KaiTi', serif; }
.reader-paper > header div { display: flex; justify-content: center; gap: 16px; color: #999083; font-size: 10px; }
.vn-player { max-width: 1040px; margin: 34px auto 36px; padding: 0 28px; }
.vn-player-bar { padding: 12px 2px; display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; color: #605a51; }
.vn-player-bar > div:first-child { display: grid; gap: 4px; }
.vn-player-bar small { color: var(--red); font: 9px/1.3 Consolas, monospace; letter-spacing: .15em; }
.vn-player-bar strong { font: 600 12px/1.3 'STKaiti', 'KaiTi', serif; letter-spacing: .08em; }
.vn-progress-copy { display: flex; align-items: center; gap: 12px; font: 10px/1 Consolas, monospace; }
.vn-progress-copy b { color: var(--red); font-size: 15px; }
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
  /* Slot sizing — height-driven, aspect-locked, so different source canvases
     no longer make characters appear different sizes. */
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
  cursor: pointer;
  box-shadow: 0 24px 55px rgba(35,29,22,.22);
}
.vn-backdrop { position: absolute; z-index: -2; inset: -3%; background-position: center; background-size: cover; transform: scale(1.02); transition: filter .5s ease, background-position .5s ease; will-change: transform, filter;
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
/* Grades — same color temperature on backdrop AND character. */
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
.vn-background-enter-active, .vn-background-leave-active { transition: opacity .55s cubic-bezier(.22,.8,.28,1), filter .55s ease; }
.vn-background-enter-from, .vn-background-leave-to { opacity: 0; filter: saturate(.72) blur(5px); }
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
.vn-stage:focus-visible { box-shadow: 0 0 0 3px #fbf8f1, 0 0 0 5px var(--red), 0 24px 55px rgba(35,29,22,.22); }
.vn-stage-shade { position: absolute; z-index: -1; inset: 0; background: linear-gradient(180deg, rgba(8,10,9,.04) 35%, rgba(9,10,9,.36) 68%, rgba(7,8,7,.9) 100%), linear-gradient(90deg, rgba(10,10,10,.18), transparent 35%, transparent 65%, rgba(10,10,10,.18)); }
.vn-stage.is-grade-warm .vn-stage-shade { background: linear-gradient(180deg, rgba(117,72,34,.08), rgba(18,15,12,.34) 68%, rgba(8,8,7,.9)), linear-gradient(90deg, rgba(58,34,20,.18), transparent 66%, rgba(25,17,12,.2)); }
.vn-stage.is-grade-cool .vn-stage-shade { background: linear-gradient(180deg, rgba(39,58,70,.12), rgba(10,17,21,.38) 68%, rgba(5,9,11,.91)), linear-gradient(90deg, rgba(14,27,34,.2), transparent 64%, rgba(10,21,28,.22)); }
.vn-stage.is-grade-mist .vn-stage-shade { background: linear-gradient(180deg, rgba(210,211,197,.13), rgba(25,27,24,.29) 62%, rgba(8,9,8,.88)), linear-gradient(90deg, rgba(27,29,26,.14), transparent 68%, rgba(27,29,26,.15)); }
.vn-stage.is-grade-ember .vn-stage-shade { background: radial-gradient(circle at 78% 18%, rgba(181,91,38,.17), transparent 32%), linear-gradient(180deg, rgba(93,43,21,.1), rgba(25,12,8,.4) 68%, rgba(8,6,5,.92)); }
.vn-stage-grain { position: absolute; z-index: 5; inset: 0; pointer-events: none; opacity: .12; mix-blend-mode: soft-light; background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 160 160' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.82' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.75'/%3E%3C/svg%3E"); }
.vn-visual-debug { position: absolute; z-index: 9; top: 8px; right: 8px; max-width: 320px; padding: 8px 10px; background: rgba(8, 8, 7, .82); color: #fbf8f1; font: 9px/1.35 Consolas, monospace; border: 1px solid rgba(239,222,190,.4); border-radius: 2px; pointer-events: none; }
.vn-visual-debug dl { display: grid; grid-template-columns: auto 1fr; gap: 2px 8px; margin: 0; }
.vn-visual-debug dt { color: rgba(239,222,190,.7); text-transform: uppercase; letter-spacing: .08em; }
.vn-visual-debug dd { margin: 0; word-break: break-all; }
.vn-visual-debug .vn-visual-debug-char { grid-column: 1 / -1; color: rgba(239,222,190,.55); padding-top: 4px; border-top: 1px dashed rgba(239,222,190,.2); }
.vn-side-cue { position: absolute; z-index: 7; top: 42%; display: flex; align-items: center; gap: 7px; color: rgba(239,222,190,.72); opacity: .16; pointer-events: none; transition: opacity .2s ease, transform .2s ease; text-shadow: 0 2px 8px rgba(0,0,0,.8); }
.vn-side-cue.is-prev { left: 16px; transform: translateX(-4px); }
.vn-side-cue.is-next { right: 16px; transform: translateX(4px); }
.vn-side-cue i { font: 300 30px/1 Georgia, serif; }
.vn-side-cue span { font: 10px/1 'STKaiti', 'KaiTi', serif; letter-spacing: .12em; writing-mode: vertical-rl; }
.vn-stage:hover .vn-side-cue { opacity: .76; transform: translateX(0); }
.vn-character-slot {
  position: absolute;
  z-index: 2;
  bottom: var(--vn-slot-bottom);
  width: var(--vn-slot-width-dual);
  height: var(--vn-slot-height-dual);
  aspect-ratio: var(--vn-slot-aspect);
  user-select: none;
  pointer-events: none;
}
.vn-character-slot.is-left { left: 2%; }
.vn-character-slot.is-right { right: 2%; }
/* Single cast — taller and slightly wider than dual */
.vn-stage.is-single-cast {
  --vn-slot-height-single: 88%;
  --vn-slot-width-single: min(42%, 410px);
}
.vn-stage.is-single-cast .vn-character-slot {
  width: var(--vn-slot-width-single);
  height: var(--vn-slot-height-single);
}
.vn-stage.is-dual-cast .vn-character-slot {
  width: var(--vn-slot-width-dual);
  height: var(--vn-slot-height-dual);
}
/* Composition overrides — bounded to ±6% of base, never a step change. */
.vn-stage.is-composition-close {
  --vn-slot-height-dual: 84%;
  --vn-slot-height-single: 92%;
}
.vn-stage.is-composition-close .vn-character-slot { width: min(43%, 425px); }
.vn-stage.is-composition-offset .vn-character-slot.is-left { left: -1%; }
.vn-stage.is-composition-offset .vn-character-slot.is-right { right: 5%; }
.vn-stage.is-composition-wide {
  --vn-slot-bottom: -4%;
  --vn-slot-height-dual: 76%;
  --vn-slot-height-single: 84%;
}
.vn-stage.is-composition-wide .vn-character-slot { width: min(38%, 370px); }
.vn-character-slot.is-speaker { z-index: 3; }
.vn-character-slot.is-observer { opacity: .72; }
/* take 0 — explicit rule (previously fell through to base, causing the
   'character scaling inconsistency' between takes 0/1/2). */
.vn-character-slot.is-take-0 { width: var(--vn-slot-width-dual); height: var(--vn-slot-height-dual); }
.vn-character-slot.is-take-0 .vn-character { object-position: 50% bottom; }
.vn-character-slot.is-take-1.is-left { left: 4%; }
.vn-character-slot.is-take-1.is-right { right: 4%; }
.vn-character-slot.is-take-2 { bottom: -4%; width: min(41%, 390px); }
.vn-character-slot.is-take-1 .vn-character { object-position: 46% bottom; }
.vn-character-slot.is-take-2 .vn-character { object-position: 54% bottom; }
.vn-character {
  --vn-facing: 1;
  position: relative;
  z-index: 2;
  width: 100%;
  height: 100%;
  object-fit: contain;
  object-position: center bottom;
  transform: scaleX(var(--vn-facing));
  transform-origin: 50% 88%;
  /* Filter reads from the stage CSS variables so grade affects BOTH the
     backdrop and the character with the same color temperature. */
  filter:
    sepia(var(--portrait-sepia))
    saturate(var(--portrait-saturation))
    brightness(var(--portrait-brightness))
    contrast(var(--portrait-contrast))
    hue-rotate(var(--portrait-hue-rotate))
    drop-shadow(0 22px 18px rgba(0,0,0,.52));
  mask-image: linear-gradient(to bottom, #000 0%, #000 91%, transparent 100%);
}
.vn-character-slot.is-right .vn-character { --vn-facing: -1; }
.vn-character-slot.is-generated .vn-character {
  filter:
    sepia(var(--portrait-sepia))
    saturate(calc(var(--portrait-saturation) + .02))
    brightness(var(--portrait-brightness))
    contrast(calc(var(--portrait-contrast) + .01))
    hue-rotate(var(--portrait-hue-rotate))
    drop-shadow(0 24px 19px rgba(0,0,0,.55));
}
.vn-character-slot.motion-breathe .vn-character { animation: vn-character-breathe 5.8s ease-in-out infinite; }
.vn-character-slot.motion-warm .vn-character { animation: vn-character-warm 4.8s ease-in-out infinite; }
.vn-character-slot.motion-tense .vn-character { animation: vn-character-tense 2.8s ease-in-out infinite; }
.vn-character-slot.motion-startled .vn-character { animation: vn-character-startled .48s cubic-bezier(.18,.84,.28,1) both; }
.vn-character-slot.motion-guarded .vn-character { animation: vn-character-guarded 5.6s ease-in-out infinite; }
.vn-character-slot.motion-combat .vn-character { animation: vn-character-combat 2.4s ease-in-out infinite; }
@keyframes vn-character-breathe {
  0%, 100% { transform: translateY(0) scaleX(var(--vn-facing)) scale(1); }
  50% { transform: translateY(-3px) scaleX(var(--vn-facing)) scale(1.006); }
}
@keyframes vn-character-warm {
  0%, 100% { transform: translateY(0) rotate(0) scaleX(var(--vn-facing)); }
  50% { transform: translateY(-4px) rotate(.25deg) scaleX(var(--vn-facing)) scale(1.008); }
}
@keyframes vn-character-tense {
  0%, 100% { transform: translate(0, 0) scaleX(var(--vn-facing)) scale(1); }
  45% { transform: translate(-1px, -2px) scaleX(var(--vn-facing)) scale(1.009); }
  50% { transform: translate(1px, -2px) scaleX(var(--vn-facing)) scale(1.009); }
}
@keyframes vn-character-startled {
  0% { transform: translateY(8px) scaleX(var(--vn-facing)) scale(.97); }
  55% { transform: translateY(-7px) scaleX(var(--vn-facing)) scale(1.018); }
  100% { transform: translateY(-2px) scaleX(var(--vn-facing)) scale(1); }
}
@keyframes vn-character-guarded {
  0%, 100% { transform: translateY(1px) scaleX(var(--vn-facing)) scale(.995); }
  50% { transform: translateY(-2px) scaleX(var(--vn-facing)) scale(1.004); }
}
@keyframes vn-character-combat {
  0%, 100% { transform: translateY(0) scaleX(var(--vn-facing)) scale(1); }
  40% { transform: translateY(-5px) scaleX(var(--vn-facing)) scale(1.012); }
  52% { transform: translateY(-3px) scaleX(var(--vn-facing)) scale(1.006); }
}
.vn-expression-enter-active, .vn-expression-leave-active { transition: opacity .2s ease, filter .24s ease; }
.vn-expression-enter-from, .vn-expression-leave-to { opacity: 0; filter: saturate(.55) blur(3px); }
.vn-character-ground { position: absolute; z-index: 1; right: 12%; bottom: 4%; left: 12%; height: 8%; border-radius: 50%; background: radial-gradient(ellipse, rgba(0,0,0,.58), transparent 70%); filter: blur(8px); }
.vn-character-silhouette { position: absolute; z-index: 2; right: 16%; bottom: 6%; left: 16%; height: 78%; opacity: .52; background: linear-gradient(160deg, #363833, #111311); border-radius: 45% 45% 12% 12%; filter: blur(1px) drop-shadow(0 20px 20px rgba(0,0,0,.5)); }
.vn-character-silhouette::before { position: absolute; top: -13%; left: 30%; width: 40%; aspect-ratio: 1; content: ''; background: #292b27; border-radius: 50%; }
.vn-character-silhouette span { position: absolute; right: 0; bottom: 28%; left: 0; color: rgba(235,219,191,.6); text-align: center; font: 22px 'STKaiti', 'KaiTi', serif; }
.vn-character-forge { position: absolute; z-index: 4; bottom: 27%; left: 50%; padding: 6px 10px; color: #ead6b2; background: rgba(24,25,22,.8); border: 1px solid rgba(207,170,111,.52); font: 8px/1.2 Consolas, monospace; letter-spacing: .08em; white-space: nowrap; transform: translateX(-50%); backdrop-filter: blur(8px); }
.vn-character-forge.is-fallback { color: #c7bca9; border-color: rgba(199,188,169,.32); }
.vn-character-enter-active { transition: opacity .38s ease, transform .48s cubic-bezier(.2,.8,.2,1); }
.vn-character-leave-active { transition: opacity .16s ease; }
.vn-character-enter-from, .vn-character-leave-to { opacity: 0; }
.vn-character-enter-from.is-left { transform: translateX(-35px); }
.vn-character-enter-from.is-right { transform: translateX(35px); }
.vn-asset-status { position: absolute; z-index: 6; top: 18px; left: 18px; padding: 8px 11px; color: #e8ddca; background: rgba(28,29,27,.76); border: 1px solid rgba(216,196,161,.32); font-size: 10px; backdrop-filter: blur(8px); }
.vn-asset-status.is-error { color: #f1b2a9; }
.vn-dialogue-box { position: absolute; z-index: 8; right: 5%; bottom: 5%; left: 5%; min-height: 155px; padding: 35px 42px 28px; color: #f6eee0; background: linear-gradient(105deg, rgba(18,19,18,.94), rgba(31,31,28,.9)); border: 1px solid rgba(209,181,133,.62); box-shadow: 0 15px 35px rgba(0,0,0,.36); backdrop-filter: blur(11px); }
.vn-dialogue-box::before, .vn-dialogue-box::after { position: absolute; width: 28px; height: 28px; content: ''; border-color: #bb8f4b; }
.vn-dialogue-box::before { top: 8px; left: 8px; border-top: 1px solid; border-left: 1px solid; }
.vn-dialogue-box::after { right: 8px; bottom: 8px; border-right: 1px solid; border-bottom: 1px solid; }
.vn-nameplate { position: absolute; top: -17px; left: 28px; min-width: 150px; padding: 9px 18px 8px; color: #fff; background: var(--red); border-left: 3px solid var(--gold); display: flex; align-items: center; justify-content: space-between; gap: 18px; box-shadow: 0 7px 14px rgba(0,0,0,.25); }
.vn-nameplate span { font: 700 17px/1 'STKaiti', 'KaiTi', serif; }
.vn-nameplate small { color: #dba9a4; font: 8px/1 Consolas, monospace; letter-spacing: .12em; }
.vn-dialogue-box > p { max-width: 850px; margin: 0; text-align: justify; text-indent: 0; font: 18px/1.85 'STSong', 'SimSun', serif; text-shadow: 0 1px 3px #000; }
.vn-stage.is-poem .vn-dialogue-box > p { color: #ead4ad; text-align: center; font-family: 'STKaiti', 'KaiTi', serif; }
.vn-next-cue { position: absolute; right: 22px; bottom: 12px; color: #9f998f; font-size: 9px; letter-spacing: .08em; }
.vn-next-cue i { margin-left: 6px; color: var(--gold); font-style: normal; animation: vn-cue 1.2s ease-in-out infinite; }
@keyframes vn-cue { 50% { opacity: .25; transform: translateY(2px); } }
.vn-dialogue-enter-active, .vn-dialogue-leave-active { transition: opacity .18s ease, transform .18s ease; }
.vn-dialogue-enter-from { opacity: 0; transform: translateY(8px); }
.vn-dialogue-leave-to { opacity: 0; transform: translateY(-4px); }
.vn-controls { padding: 14px 0 0; display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 14px; }
.vn-controls button { min-width: 112px; padding: 9px 13px; color: #62594f; background: transparent; border: 1px solid #c8bcac; font-size: 11px; }
.vn-controls button:hover:not(:disabled) { color: #fff; background: var(--red); border-color: var(--red); }
.vn-controls button:disabled { opacity: .35; cursor: not-allowed; }
.vn-progress-track { overflow: hidden; height: 2px; background: #d6ccbd; }
.vn-progress-track span { display: block; height: 100%; background: var(--red); transition: width .24s ease; }
.reader-full-text { max-width: 820px; margin: 0 auto 30px; border-top: 1px solid #ded5c8; border-bottom: 1px solid #ded5c8; }
.reader-full-text > summary { padding: 15px 4px; color: #796f63; cursor: pointer; font-size: 11px; letter-spacing: .08em; }
.reader-full-text[open] > summary { color: var(--red); border-bottom: 1px solid #ded5c8; }
.reader-body { max-width: 760px; margin: 0 auto; padding: 54px 74px; color: #312d28; font-family: 'STSong', 'SimSun', serif; }
.reader-body p { margin-bottom: 1.2em; text-align: justify; text-indent: 2em; font-size: 18px; line-height: 2.05; }
.reader-body blockquote { margin: 28px 30px; padding: 18px 24px; color: #765047; background: #f1ebe1; border-left: 2px solid var(--red); white-space: pre-line; font: 17px/2 'STKaiti', 'KaiTi', serif; }
.chapter-end-flow { max-width: 820px; margin: 0 auto 28px; padding: 28px; color: #eee7dc; background: #252825; border: 1px solid #3d423c; box-shadow: 0 16px 36px rgba(32,29,25,.14); }
.chapter-end-flow > header { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; padding-bottom: 20px; border-bottom: 1px solid #41443f; }
.chapter-end-flow > header small { color: var(--gold); font: 9px/1.4 Consolas, monospace; letter-spacing: .15em; }
.chapter-end-flow > header h2 { margin-top: 7px; color: #f2eadc; font: 700 24px/1.4 'STKaiti', 'KaiTi', serif; }
.chapter-end-flow > header > span { color: #aab7a6; font-size: 10px; }
.chapter-end-actions { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 20px; }
.chapter-end-actions button { min-height: 150px; padding: 18px; text-align: left; color: #d9d0c3; background: #202320; border: 1px solid #464b44; transition: background .18s cubic-bezier(.22,1,.36,1), border-color .18s cubic-bezier(.22,1,.36,1); }
.chapter-end-actions button:hover:not(:disabled), .chapter-end-actions button:focus-visible { color: #f7efe2; background: #373027; border-color: var(--gold); outline: none; }
.chapter-end-actions button:disabled { opacity: .38; cursor: not-allowed; }
.chapter-end-actions i, .chapter-end-actions strong, .chapter-end-actions span { display: block; }
.chapter-end-actions i { color: var(--gold); font: normal 10px/1 Consolas, monospace; letter-spacing: .12em; }
.chapter-end-actions strong { margin-top: 25px; color: inherit; font: 700 17px/1.45 'STKaiti', 'KaiTi', serif; }
.chapter-end-actions span { margin-top: 8px; color: #949b91; font-size: 10px; line-height: 1.65; }
.continuation-vn-review { max-width: 820px; margin: 0 auto 28px; padding: 26px 28px; color: #eee7dc; background: #252825; border: 1px solid #66563f; box-shadow: 0 16px 36px rgba(32,29,25,.14); }
.continuation-vn-review > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; padding-bottom: 16px; border-bottom: 1px solid #41443f; }
.continuation-vn-review > header small { color: var(--gold); font: 9px/1.4 Consolas, monospace; letter-spacing: .14em; }
.continuation-vn-review > header h2 { margin-top: 6px; color: #f2eadc; font: 700 22px/1.45 'STKaiti', 'KaiTi', serif; }
.continuation-vn-review > header > span { padding: 5px 8px; color: #211f1b; background: var(--gold); font-size: 9px; white-space: nowrap; }
.continuation-vn-review > p { max-width: 680px; margin-top: 16px; color: #aaa297; font-size: 11px; line-height: 1.8; }
.continuation-vn-review-actions { display: grid; grid-template-columns: 1fr 1.45fr; gap: 10px; margin-top: 18px; }
.continuation-vn-review-actions button { padding: 12px 14px; color: #eee7dc; background: transparent; border: 1px solid #69645b; font-size: 11px; }
.continuation-vn-review-actions button:last-child { color: #211f1b; background: var(--gold); border-color: var(--gold); font-weight: 700; }
.continuation-vn-review-actions button:disabled { opacity: .45; cursor: not-allowed; }
.continuation-vn-review .redraw-details { border-color: #4a4d48; }
.continuation-vn-review .redraw-details summary, .continuation-vn-review .redraw-details label { color: #c9b087; }
.continuation-vn-review .redraw-details textarea { color: #ece5da; background: #1f211f; border-color: #4c514a; }
.reader-flow-back { margin-bottom: 13px; padding: 0; color: #7f7466; background: transparent; border: 0; font-size: 10px; }
.reader-flow-back:hover, .reader-flow-back:focus-visible { color: var(--red); outline: none; text-decoration: underline; text-underline-offset: 3px; }
.ai-studio .reader-flow-back { color: #b7a68c; }
.ai-studio .reader-flow-back:hover, .ai-studio .reader-flow-back:focus-visible { color: var(--gold); }
.community-story { max-width: 820px; margin: 0 auto 28px; padding: 30px; background: #eee7dc; border: 1px solid #d5cab8; box-shadow: 0 14px 34px rgba(45,39,31,.08); }
.community-story > header { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; padding-bottom: 17px; border-bottom: 1px solid #cfc2ae; }
.community-story > header p { color: var(--red); font: 9px/1.4 Consolas, monospace; letter-spacing: .16em; }
.community-story > header h2 { margin-top: 5px; font: 700 24px/1.35 'STKaiti', 'KaiTi', serif; }
.community-story > header > span { color: #766c5d; font-size: 11px; white-space: nowrap; }
.community-intro { max-width: 680px; margin: 17px 0 22px; color: #6e655a; font-size: 12px; line-height: 1.8; }
.community-selection-error { margin: -8px 0 18px; padding: 10px 12px; color: #7e2724; background: #f1ded8; border: 1px solid #d8aaa0; font-size: 11px; line-height: 1.6; }
.community-empty { padding: 30px 20px; color: #756b5e; text-align: center; border: 1px dashed #c8baa5; font: 14px/1.8 'STSong', 'SimSun', serif; }
.community-empty.is-error { color: var(--red); }
.community-empty button { margin-top: 12px; padding: 9px 13px; color: var(--red); background: transparent; border: 1px solid #ba9f87; }
.community-branch-browser { display: grid; grid-template-columns: minmax(220px, 280px) minmax(0, 1fr); gap: 16px; align-items: start; }
.community-branch-list { max-height: 570px; overflow-y: auto; display: grid; align-content: start; gap: 7px; padding-right: 3px; }
.community-branch-list button { width: 100%; min-width: 0; padding: 12px; display: grid; grid-template-columns: 28px minmax(0, 1fr) 12px; align-items: center; gap: 9px; text-align: left; color: #51483e; background: #e7dfd2; border: 1px solid #d1c4b2; }
.community-branch-list button:hover, .community-branch-list button:focus-visible { border-color: #a98f6c; outline: none; }
.community-branch-list button.active { color: #2f2923; background: #faf6ee; border-color: var(--red); box-shadow: inset 0 0 0 1px rgba(143,47,43,.13); }
.community-branch-list button.selected i { color: #f6ede0; background: var(--red); }
.community-branch-list i { width: 28px; height: 28px; display: grid; place-items: center; color: #817461; background: #d8cdbc; font: normal 9px/1 Consolas, monospace; }
.community-branch-list span, .community-branch-list strong, .community-branch-list small { min-width: 0; display: block; }
.community-branch-list strong { overflow: hidden; color: inherit; font: 600 12px/1.55 'STKaiti', 'KaiTi', serif; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.community-branch-list small { margin-top: 4px; color: #8a7f70; font-size: 8px; }
.community-branch-list b { color: #9a8a74; font: 18px/1 Georgia, serif; }
.community-branch-preview { min-width: 0; background: #f8f4eb; border: 1px solid #d8cdbd; }
.community-branch-preview > header { padding: 16px 18px; display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; border-bottom: 1px solid #ddd2c2; }
.community-branch-preview > header small, .community-branch-preview > header strong { display: block; }
.community-branch-preview > header small { color: var(--red); font-size: 8px; letter-spacing: .12em; }
.community-branch-preview > header strong { margin-top: 5px; color: #383128; font: 600 14px/1.55 'STKaiti', 'KaiTi', serif; }
.community-branch-preview > header > span { padding: 4px 7px; color: #f6ede0; background: var(--red); font-size: 8px; white-space: nowrap; }
.community-branch-story { max-height: 470px; overflow-y: auto; }
.community-branch-story > p { padding: 20px; color: #3c352d; white-space: pre-wrap; font: 15px/1.9 'STSong', 'SimSun', serif; }
.community-branch-story > img { width: 100%; max-height: 310px; object-fit: cover; border-top: 1px solid #d8cdbd; }
.community-branch-preview > footer { padding: 14px 18px; display: flex; align-items: center; justify-content: space-between; gap: 14px; border-top: 1px solid #ddd2c2; }
.community-branch-preview > footer > span { color: #8b7f6e; font-size: 9px; }
.community-branch-preview > footer button { padding: 9px 12px; color: #f7efe2; background: var(--red); border: 1px solid var(--red); font-size: 10px; }
.community-branch-preview > footer button:disabled { color: #887d70; background: transparent; border-color: #ba9f87; cursor: default; opacity: .7; }

.ai-studio { max-width: 820px; margin: 0 auto 42px; padding: 30px; color: #eee7dc; background: #252825; border: 1px solid #3d423c; box-shadow: 0 16px 36px rgba(32,29,25,.14); }
.ai-studio > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding-bottom: 22px; border-bottom: 1px solid #41443f; }
.ai-studio > header p, .preview-heading small { color: var(--gold); font: 9px/1.4 Consolas, monospace; letter-spacing: .15em; }
.ai-studio > header h2 { margin-top: 6px; color: #f2eadc; font: 700 22px/1.45 'STKaiti', 'KaiTi', serif; }
.ai-studio > header > span { padding: 7px 10px; color: #aab7a6; border: 1px solid #586456; font-size: 10px; white-space: nowrap; }
.studio-grid { margin-top: 22px; display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.studio-panel { padding: 18px; background: #2d302d; border: 1px solid #444942; }
.studio-panel label { display: block; margin-bottom: 9px; color: #d9cdbb; font-size: 12px; font-weight: 700; }
.studio-panel textarea { width: 100%; resize: vertical; padding: 12px; color: #ece5da; background: #1f211f; border: 1px solid #4c514a; outline: none; font-size: 12px; line-height: 1.7; }
.studio-panel textarea:focus { border-color: var(--gold); }
.studio-panel button, .continuation-preview button { width: 100%; margin-top: 10px; padding: 11px 14px; border: 1px solid var(--gold); font-size: 12px; }
.studio-primary { color: #1e1c18; background: var(--gold); }
.studio-secondary, .continuation-preview button { color: #efe5d5; background: transparent; }
.studio-panel button:disabled, .continuation-preview button:disabled { opacity: .45; cursor: not-allowed; }
.studio-hint { margin-top: 9px; color: #8f958b; font-size: 9px; line-height: 1.6; }
.reader-source-choice { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 18px; }
.studio-panel .reader-source-choice button { width: auto; min-height: 82px; margin: 0; padding: 11px; text-align: left; color: #aaa297; background: #222522; border: 1px solid #4c514a; }
.studio-panel .reader-source-choice button.active { color: #f5ead8; background: #3a3328; border-color: var(--gold); }
.reader-source-choice small, .reader-source-choice strong, .reader-source-choice span { display: block; }
.reader-source-choice small { color: var(--gold); font: 8px Consolas, monospace; letter-spacing: .12em; }
.reader-source-choice strong { margin: 6px 0; font-size: 12px; }
.reader-source-choice span { font-size: 8px; line-height: 1.5; }
.reader-file-picker { padding: 24px 12px; text-align: center; color: #d8b274 !important; background: #222522; border: 1px dashed #8e7957; cursor: pointer; }
.reader-file-picker input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.studio-panel .reader-rights { margin-top: 12px; color: #aaa297; font-weight: 400; }
.reader-rights input { margin-right: 6px; }
.reader-upload-result { display: grid; grid-template-columns: 92px 1fr; gap: 10px; margin-top: 13px; padding: 9px; background: #202320; }
.reader-upload-result img { width: 92px; height: 68px; object-fit: cover; }
.reader-upload-result span, .reader-upload-result strong, .reader-upload-result small { display: block; }
.reader-upload-result strong { color: #d8cbb9; font-size: 10px; }
.reader-upload-result small { margin-top: 6px; color: #8f958b; font-size: 8px; }
.system-mode-index { color: var(--gold); font: 9px Consolas, monospace; letter-spacing: .14em; }
.system-mode-panel h3 { margin: 8px 0 12px; color: #e9dfd0; font: 18px 'STKaiti', 'KaiTi', serif; }
.system-mode-panel ol { padding-left: 20px; color: #b8b0a5; font-size: 11px; line-height: 2; }
.studio-error { margin-top: 16px; padding: 11px 13px; color: #f1c4be; background: #4a2927; border: 1px solid #73413d; font-size: 11px; }
.continuation-generation-gate { min-height: 180px; margin-top: 18px; padding: 28px; display: grid; place-content: center; gap: 9px; text-align: center; color: #ddd2c1; background: #202320; border: 1px solid #4a5048; }
.continuation-generation-gate strong { color: #f1e7d7; font: 700 18px/1.45 'STKaiti', 'KaiTi', serif; }
.continuation-generation-gate span { color: var(--gold); font-size: 11px; }
.continuation-generation-gate small { color: #8f958b; font-size: 9px; }
.continuation-preview, .image-preview { margin-top: 18px; padding: 22px; color: #302c27; background: #f8f3e9; border: 1px solid #cdbb9b; }
.preview-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.preview-heading h3 { margin-top: 4px; font: 700 20px 'STKaiti', 'KaiTi', serif; }
.preview-heading > span { padding: 5px 8px; color: #6d6359; background: #e8dfd1; font-size: 9px; }
.preview-heading .status-confirmed { color: #eef5eb; background: #577153; }
.preview-heading .status-failed { color: #fff; background: #8f2f2b; }
.continuation-preview > p, .image-preview > p { margin-top: 16px; white-space: pre-wrap; font: 16px/1.95 'STSong', 'SimSun', serif; }
.continuation-preview button { color: #fff; background: var(--red); border-color: var(--red); }
.generated-image-frame { position: relative; display: grid; min-height: 180px; margin-top: 16px; overflow: hidden; place-items: center; background: #eee6d8; }
.generated-image-frame.is-ready { min-height: 0; background: transparent; }
.generated-image-frame img { grid-area: 1 / 1; width: 100%; height: auto; max-height: none; display: block; opacity: 0; transition: opacity .18s ease; }
.generated-image-frame img.is-loaded { opacity: 1; }
.generated-image-loading { grid-area: 1 / 1; z-index: 1; padding: 24px; color: #746a5f; font-size: 11px; letter-spacing: .08em; }
.generated-bundle-preloader { position: absolute; width: 1px; height: 1px; overflow: hidden; opacity: 0; pointer-events: none; }
.generated-bundle-preloader img { width: 1px; height: 1px; }
.redraw-details { margin-top: 16px; padding-top: 14px; border-top: 1px solid #ddd1c0; }
.redraw-details summary { color: #7c583c; font-size: 11px; cursor: pointer; }
.redraw-details label { display: block; margin: 12px 0 7px; color: #6d6359; font-size: 11px; font-weight: 700; }
.redraw-details textarea { width: 100%; padding: 10px; resize: vertical; color: #403a34; background: #fffdf8; border: 1px solid #cbbba5; line-height: 1.6; }
.redraw-details button { width: 100%; margin-top: 8px; padding: 10px; color: #fff; background: var(--red); border: 1px solid var(--red); }
.redraw-details button:disabled { opacity: .45; }
.ai-studio > footer { margin-top: 20px; padding-top: 18px; border-top: 1px solid #41443f; display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.ai-studio > footer span { color: #999f96; font-size: 10px; line-height: 1.6; }
.ai-studio > footer button { color: #d8b274; background: none; border: 0; white-space: nowrap; }
.graph-details { max-width: 760px; margin: 0 auto 42px; border: 1px solid var(--line); }
.graph-details summary { padding: 13px 16px; color: #6c645b; background: #f0eadf; font-size: 12px; cursor: pointer; }
.graph-details > div { padding: 18px; display: grid; grid-template-columns: repeat(4, 1fr) auto; gap: 12px; align-items: center; }
.graph-details p strong, .graph-details p span { display: block; font-size: 9px; }
.graph-details p strong { color: #999083; }
.graph-details p span { margin-top: 4px; color: #47413a; }
.graph-details a { color: var(--red); font-size: 10px; text-decoration: none; }
.reader-footer { padding: 24px 32px; background: #efeadf; border-top: 1px solid var(--line); display: flex; justify-content: space-between; align-items: center; }
.reader-footer button { color: var(--red); background: none; border: 0; }
.reader-footer div { text-align: center; }
.reader-footer span { display: block; font: 16px 'STKaiti', 'KaiTi', serif; }
.reader-footer small { color: #9c9387; font: 9px Georgia, serif; }

@media (max-width: 900px) {
  .hero-inner { grid-template-columns: 1fr; min-height: auto; gap: 40px; }
  .hero-seal { display: none; }
  .hero h1 { font-size: 64px; }
  .stat-grid, .faction-strip { grid-template-columns: repeat(2, 1fr); }
  .stat-card:nth-child(2) { border-right: 0; }
  .stat-card:nth-child(-n+2) { border-bottom: 1px solid var(--line); }
  .overview-grid, .bible-grid, .character-grid, .chapter-list-grid, .contract-grid { grid-template-columns: 1fr; }
  .theme-grid { grid-template-columns: repeat(2, 1fr); }
  .node-mapping-grid { grid-template-columns: repeat(2, 1fr); }
  .timeline-item { grid-template-columns: 54px 92px 1fr; }
  .timeline-item > button { display: none; }
  .guardrail-panel, .relations-card { grid-template-columns: 1fr; }
  .graph-details > div { grid-template-columns: repeat(2, 1fr); }
  .vn-stage { min-height: 560px; }
  .vn-character-slot { width: min(52%, 370px); }
  .studio-grid { grid-template-columns: 1fr; }
}

@media (max-width: 620px) {
  .hero-inner { padding: 52px 22px; }
  .hero h1 { font-size: 49px; }
  .hero-subtitle { font-size: 17px; }
  .hero-actions { flex-direction: column; align-items: stretch; }
  .content-section, .reader-section { padding: 44px 16px 70px; }
  .section-heading h2 { font-size: 28px; }
  .stat-grid, .theme-grid, .faction-strip, .node-mapping-grid { grid-template-columns: 1fr; }
  .stat-card { border-right: 0; border-bottom: 1px solid var(--line); }
  .stat-card:last-child { border-bottom: 0; }
  .open-callout { align-items: flex-start; gap: 20px; flex-direction: column; }
  .guardrail-panel { padding: 24px; }
  .guardrail-panel ol { grid-template-columns: 1fr; }
  .relation-list p { grid-template-columns: 1fr; gap: 4px; }
  .character-card header { grid-template-columns: 48px 1fr; }
  .vn-id { grid-column: 2; }
  .timeline-item { grid-template-columns: 45px 1fr; gap: 14px; }
  .timeline::before { left: 22px; }
  .timeline-marker { width: 45px; height: 45px; }
  .timeline-years { grid-column: 2; }
  .timeline-copy { grid-column: 2; }
  .chapter-toolbar { top: 52px; grid-template-columns: 1fr; }
  .chapter-list-grid > article { grid-template-columns: 62px 1fr; padding: 18px; }
  .chapter-open-cue { display: none; }
  .my-branch-list > article { grid-template-columns: 52px 1fr; }
  .my-branch-list button { grid-column: 1 / -1; width: 100%; }
  .reader-toolbar { grid-template-columns: auto 1fr; }
  .reader-toolbar > div { grid-column: 1 / -1; }
  .reader-toolbar > div button { flex: 1; }
  .reader-paper > header { padding: 38px 22px 28px; }
  .reader-paper > header h1 { font-size: 25px; }
  .reader-paper > header div { flex-wrap: wrap; }
  .vn-player { margin: 22px auto 28px; padding: 0 10px; }
  .continuation-vn-review { padding: 22px 18px; }
  .continuation-vn-review > header { display: block; }
  .continuation-vn-review > header > span { display: inline-block; margin-top: 12px; }
  .continuation-vn-review-actions { grid-template-columns: 1fr; }
  .vn-player-bar { align-items: flex-start; }
  .vn-player-bar strong { max-width: 210px; }
  .vn-stage { min-height: 520px; }
  .vn-side-cue { top: 39%; opacity: .42; }
  .vn-side-cue.is-prev { left: 8px; }
  .vn-side-cue.is-next { right: 8px; }
  .vn-side-cue span { display: none; }
  .vn-character-slot { bottom: 19%; width: 64%; height: 72%; }
  .vn-character-slot.is-left { left: -13%; }
  .vn-character-slot.is-right { right: -13%; }
  .vn-stage.is-composition-close .vn-character-slot { bottom: 18%; width: 68%; height: 76%; }
  .vn-stage.is-composition-offset .vn-character-slot.is-left { left: -17%; }
  .vn-stage.is-composition-offset .vn-character-slot.is-right { right: -8%; }
  .vn-stage.is-composition-wide .vn-character-slot { bottom: 20%; width: 58%; height: 68%; }
  .vn-dialogue-box { right: 3%; bottom: 3%; left: 3%; min-height: 180px; padding: 32px 22px 28px; }
  .vn-dialogue-box > p { font-size: 16px; line-height: 1.75; }
  .vn-nameplate { left: 16px; min-width: 128px; padding-right: 12px; padding-left: 12px; }
  .vn-next-cue { right: 14px; bottom: 9px; }
  .vn-controls { grid-template-columns: 1fr 1fr; }
  .vn-controls button { min-width: 0; }
  .vn-progress-track { grid-column: 1 / -1; grid-row: 1; }
  .reader-full-text { margin: 0 18px 28px; }
  .reader-body { padding: 38px 22px; }
  .reader-body p { font-size: 17px; line-height: 2; }
  .reader-body blockquote { margin: 22px 0; }
  .chapter-end-flow { margin: 0 18px 24px; padding: 20px; }
  .chapter-end-flow > header { align-items: flex-start; flex-direction: column; }
  .chapter-end-actions { grid-template-columns: 1fr; }
  .chapter-end-actions button { min-height: 112px; }
  .chapter-end-actions strong { margin-top: 16px; }
  .community-story { margin: 0 18px 24px; padding: 20px; }
  .community-story > header { align-items: flex-start; flex-direction: column; }
  .community-branch-browser { grid-template-columns: 1fr; }
  .community-branch-list { max-height: 300px; }
  .community-branch-story { max-height: 430px; }
  .community-branch-preview > footer { align-items: stretch; flex-direction: column; }
  .community-branch-preview > footer button { width: 100%; }
  .ai-studio { margin: 0 18px 30px; padding: 20px; }
  .ai-studio > header, .ai-studio > footer { flex-direction: column; }
  .ai-studio > header > span { white-space: normal; }
  .graph-details { margin: 0 18px 30px; }
  .reader-footer { padding: 18px; }
}

@media (prefers-reduced-motion: reduce) {
  .vn-stage,
  .vn-backdrop,
  .vn-character,
  .vn-next-cue i { animation: none !important; }
  .vn-character-enter-active,
  .vn-character-leave-active,
  .vn-expression-enter-active,
  .vn-expression-leave-active,
  .vn-dialogue-enter-active,
  .vn-dialogue-leave-active { transition-duration: .01ms !important; }
}
</style>
