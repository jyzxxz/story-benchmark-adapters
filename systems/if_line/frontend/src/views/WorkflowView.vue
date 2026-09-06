<template>
  <div class="authoring-page">
    <header class="workspace-header">
      <div class="header-primary">
        <el-tooltip content="返回项目列表" placement="bottom">
          <el-button :icon="ArrowLeft" circle aria-label="返回项目列表" @click="router.push('/')" />
        </el-tooltip>
        <div class="project-heading">
          <span class="project-kicker">StoryPath 工作台</span>
          <h1>{{ project?.title || '加载项目' }}</h1>
        </div>
        <el-tag v-if="project" :type="stageTagType" effect="plain">
          {{ authoringStageLabel }}
        </el-tag>
        <el-tag v-if="selectedPath" :type="selectedPath.status === 'active' ? 'success' : 'info'" effect="plain">
          {{ selectedPath.status === 'active' ? '当前路径' : '归档路径' }}
        </el-tag>
        <el-tag v-if="project" :type="publicationTagType" effect="plain">
          {{ publicationStateLabel }}
        </el-tag>
      </div>

      <div class="header-actions">
        <div v-if="project" class="header-metric">
          <span>后台任务</span>
          <strong>{{ project.authoring.running_task_count }}</strong>
        </div>
        <el-tooltip content="刷新工作台" placement="bottom">
          <el-button
            :icon="Refresh"
            circle
            :loading="initialLoading"
            aria-label="刷新工作台"
            @click="refreshWorkspace"
          />
        </el-tooltip>
        <el-button v-if="project" :icon="Promotion" @click="setPublicationPanelVisible(true)">
          发布管理
        </el-button>
      </div>
    </header>

    <div v-if="activeTask" class="task-strip" :class="`task-${activeTask.status}`">
      <div class="task-copy">
        <el-icon :class="{ spinning: activeTaskBusy }"><Loading /></el-icon>
        <span>{{ activeTask.label }}</span>
        <code>{{ shortId(activeTask.id, 12) }}</code>
      </div>
      <el-progress
        :percentage="activeTask.progress"
        :indeterminate="activeTaskBusy && activeTask.progress === 0"
        :show-text="false"
        :stroke-width="5"
      />
      <span class="task-status">{{ taskStatusLabel }}</span>
      <el-button
        v-if="!activeTaskBusy"
        :icon="Close"
        text
        circle
        aria-label="关闭任务状态"
        @click="activeTask = null"
      />
    </div>

    <div v-if="fatalError" class="fatal-state">
      <el-result icon="error" title="项目无法加载" :sub-title="fatalError">
        <template #extra>
          <el-button type="primary" @click="refreshWorkspace">重试</el-button>
          <el-button @click="router.push('/')">返回项目列表</el-button>
        </template>
      </el-result>
    </div>

    <div
      v-else
      class="workspace-grid"
    >
      <StoryPathTree
        :paths="paths"
        :selected-id="selectedPathId"
        :loading="initialLoading || pathLoading"
        :updating="pathUpdating"
        @select="(path) => selectPath(path, true)"
        @refresh="refreshPaths"
        @toggle-status="togglePathStatus"
        @open-checkpoint="openSiblingCheckpoint"
      />

      <main class="artifact-workspace">
        <el-tabs v-model="activeArea" class="artifact-tabs">
          <el-tab-pane label="故事设定" name="bible" />
          <el-tab-pane label="章节大纲" name="outline" />
          <el-tab-pane label="路径章节" name="chapters" />
          <el-tab-pane label="分支候选" name="branches" />
        </el-tabs>

        <div v-if="initialLoading" class="main-loading">
          <el-skeleton :rows="10" animated />
        </div>

        <section v-else-if="activeArea === 'bible'" class="artifact-section">
          <header class="section-toolbar">
            <div>
              <span class="section-kicker">项目级</span>
              <h2>故事设定</h2>
            </div>
            <div class="toolbar-actions">
              <el-input
                v-model="bibleInstructions"
                maxlength="12000"
                clearable
                placeholder="本次生成要求（可选）"
                class="instruction-input"
              />
              <el-button
                type="primary"
                :icon="MagicStick"
                :disabled="activeTaskBusy || generationSubmitting"
                @click="generateBible"
              >
                生成设定
              </el-button>
            </div>
          </header>

          <DraftDocumentCard
            title="故事设定"
            kicker="当前版本只读，编辑即复制进本地草稿"
            :drafting="Boolean(bibleDraft)"
            :dirty="bibleSaveState === 'dirty'"
            :saved-at="bibleDraft?.updatedAt ?? null"
            :memory-fallback="draftStore.usingMemoryFallback"
            :suggestion="bibleSuggestion ? { revisionId: bibleSuggestion.id, revisionNo: bibleSuggestion.revision_no } : null"
            @preview-suggestion="previewBibleSuggestion"
            @adopt-suggestion="adoptBibleSuggestion"
            @dismiss-suggestion="dismissBibleSuggestion"
          >
            <template #actions>
              <el-button
                v-if="!bibleDraft"
                size="small"
                :icon="EditPen"
                :disabled="activeTaskBusy || generationSubmitting"
                @click="startBibleDraft"
              >
                编辑（复制到草稿）
              </el-button>
              <template v-else>
                <el-button size="small" type="primary" plain @click="saveBibleDraft(true)">
                  保存
                </el-button>
                <el-button size="small" type="danger" plain @click="discardBibleDraft">
                  丢弃草稿
                </el-button>
              </template>
            </template>

            <el-input
              v-if="bibleDraft"
              v-model="bibleDraftText"
              type="textarea"
              :rows="18"
              resize="vertical"
              class="draft-editor"
              aria-label="故事设定草稿编辑器"
              @input="onBibleDraftInput"
            />
            <template v-else-if="selectedBibleRevision">
              <div class="document-meta">
                <span>r{{ selectedBibleRevision.revision_no }}</span>
                <span>{{ shortId(selectedBibleRevision.content_hash, 12) }}</span>
                <el-tag
                  :type="selectedBibleRevision.id === bibleHead?.revision_id ? 'success' : 'warning'"
                  size="small"
                  effect="plain"
                >
                  {{ selectedBibleRevision.id === bibleHead?.revision_id ? '当前版本' : '历史版本' }}
                </el-tag>
              </div>
              <BibleContentView :content="selectedBibleRevision.content_json" />
            </template>
            <el-empty v-else description="尚未生成故事设定" />
          </DraftDocumentCard>
        </section>

        <section v-else-if="activeArea === 'outline'" class="artifact-section">
          <header class="section-toolbar outline-toolbar">
            <div>
              <span class="section-kicker">{{ selectedPath?.title || '未选择路径' }}</span>
              <h2>章节大纲</h2>
            </div>
            <div class="toolbar-actions">
              <el-input-number
                v-model="outlineChapterCount"
                :min="1"
                :max="500"
                :step="1"
                step-strictly
                controls-position="right"
                aria-label="大纲章节数"
              />
              <el-input
                v-model="outlineInstructions"
                maxlength="12000"
                clearable
                placeholder="本次生成要求（可选）"
                class="instruction-input"
              />
              <el-button
                type="primary"
                :icon="MagicStick"
                :disabled="!pathEditable || (!bibleHead?.revision_id && !bibleDraft) || activeTaskBusy || generationSubmitting"
                @click="generateOutline"
              >
                生成大纲
              </el-button>
            </div>
          </header>

          <DraftDocumentCard
            title="章节大纲"
            kicker="有设定草稿时，生成会自动锚定物化后的设定"
            :drafting="Boolean(outlineDraft)"
            :dirty="outlineSaveState === 'dirty'"
            :saved-at="outlineDraft?.updatedAt ?? null"
            :stale-message="outlineStaleMessage"
            :memory-fallback="draftStore.usingMemoryFallback"
            :suggestion="outlineSuggestion ? { revisionId: outlineSuggestion.id, revisionNo: outlineSuggestion.revision_no } : null"
            @preview-suggestion="previewOutlineSuggestion"
            @adopt-suggestion="adoptOutlineSuggestion"
            @dismiss-suggestion="dismissOutlineSuggestion"
          >
            <template #actions>
              <el-button
                v-if="!outlineDraft"
                size="small"
                :icon="EditPen"
                :disabled="!pathEditable || activeTaskBusy || generationSubmitting"
                @click="startOutlineDraft"
              >
                编辑（复制到草稿）
              </el-button>
              <template v-else>
                <el-button size="small" type="primary" plain @click="saveOutlineDraft(true)">
                  保存
                </el-button>
                <el-button size="small" type="danger" plain @click="discardOutlineDraft">
                  丢弃草稿
                </el-button>
              </template>
            </template>

            <el-input
              v-if="outlineDraft"
              v-model="outlineDraftText"
              type="textarea"
              :rows="18"
              resize="vertical"
              class="draft-editor"
              aria-label="章节大纲草稿编辑器"
              @input="onOutlineDraftInput"
            />
            <template v-else-if="selectedOutlineRevision">
              <div class="document-meta">
                <span>r{{ selectedOutlineRevision.revision_no }}</span>
                <span>{{ selectedOutlineRevision.chapters.length }} 章</span>
                <span>设定 {{ shortId(selectedOutlineRevision.bible_revision_id, 10) }}</span>
                <el-tag
                  :type="selectedOutlineRevision.id === outlineHead?.revision_id ? 'success' : 'warning'"
                  size="small"
                  effect="plain"
                >
                  {{ selectedOutlineRevision.id === outlineHead?.revision_id ? '当前版本' : '历史版本' }}
                </el-tag>
              </div>
              <ol class="outline-list">
                <li v-for="chapter in selectedOutlineRevision.chapters" :key="`${selectedOutlineRevision.id}:${chapter.display_index}`">
                  <span class="outline-index">{{ chapter.display_index }}</span>
                  <div class="outline-copy">
                    <h3>{{ chapter.title || `第 ${chapter.display_index} 章` }}</h3>
                    <p>{{ chapter.summary }}</p>
                    <div v-if="chapter.scene || chapter.emotion || chapter.conflict" class="outline-tags">
                      <el-tag v-if="chapter.scene" size="small" effect="plain">{{ chapter.scene }}</el-tag>
                      <el-tag v-if="chapter.emotion" size="small" type="success" effect="plain">{{ chapter.emotion }}</el-tag>
                      <el-tag v-if="chapter.conflict" size="small" type="warning" effect="plain">{{ chapter.conflict }}</el-tag>
                    </div>
                  </div>
                </li>
              </ol>
            </template>
            <el-empty v-else description="当前路径尚无大纲版本" />
          </DraftDocumentCard>
        </section>

        <section v-else-if="activeArea === 'chapters'" class="artifact-section chapter-section">
          <header class="section-toolbar chapter-toolbar">
            <div>
              <span class="section-kicker">{{ selectedPath?.title || '未选择路径' }}</span>
              <h2>路径章节</h2>
            </div>
            <div class="toolbar-actions">
              <el-button
                :icon="Plus"
                :disabled="!pathEditable || activeTaskBusy || generationSubmitting"
                @click="appendChapter"
              >
                追加章节
              </el-button>
              <el-button
                type="primary"
                :icon="MagicStick"
                :disabled="!pathEditable || selectedBatchIds.length === 0 || activeTaskBusy || generationSubmitting"
                @click="generateChapterBatch"
              >
                生成所选章节
              </el-button>
            </div>
          </header>

          <div v-if="pathChapters.length > 0" class="chapter-workbench">
            <aside class="chapter-order" aria-label="章节顺序">
              <div class="chapter-order-tools">
                <el-checkbox
                  :model-value="allChaptersSelected"
                  :indeterminate="someChaptersSelected"
                  @change="toggleAllChapters"
                >
                  全选
                </el-checkbox>
                <span>{{ selectedBatchIds.length }}/{{ pathChapters.length }}</span>
              </div>
              <div class="chapter-list">
                <div
                  v-for="chapter in pathChapters"
                  :key="chapter.id"
                  class="chapter-row"
                  :class="{ selected: chapter.id === selectedChapterId }"
                >
                  <el-checkbox-group v-model="selectedBatchIds">
                    <el-checkbox :value="chapter.id" :aria-label="`选择第 ${chapter.display_index} 章`" />
                  </el-checkbox-group>
                  <button type="button" @click="selectChapter(chapter.id, true)">
                    <span class="chapter-number">{{ chapter.display_index }}</span>
                    <span class="chapter-title">{{ chapterTitle(chapter) }}</span>
                    <el-tag
                      v-if="chapter.inherited_from_path_chapter_id"
                      size="small"
                      type="info"
                      effect="plain"
                    >
                      继承
                    </el-tag>
                    <el-icon v-if="chapter.current_revision_id" class="chapter-ready"><CircleCheck /></el-icon>
                  </button>
                </div>
              </div>
            </aside>

            <div class="chapter-detail">
              <div class="chapter-detail-header">
                <div>
                  <span class="section-kicker">第 {{ selectedChapter?.display_index || '-' }} 章</span>
                  <h3>{{ selectedChapter ? chapterTitle(selectedChapter) : '选择章节' }}</h3>
                </div>
                <el-radio-group v-model="chapterStage" size="small">
                  <el-radio-button value="chapter">正文</el-radio-button>
                  <el-radio-button value="script">脚本</el-radio-button>
                  <el-radio-button value="graph">VNGraph</el-radio-button>
                </el-radio-group>
              </div>

              <template v-if="chapterStage === 'chapter'">
                <div class="generation-bar">
                  <el-input
                    v-model="chapterInstructions"
                    maxlength="12000"
                    clearable
                    placeholder="本次正文要求（可选）"
                  />
                  <el-button
                    type="primary"
                    :icon="MagicStick"
                    :disabled="!pathEditable || !selectedChapter || activeTaskBusy || generationSubmitting"
                    @click="generateChapter"
                  >
                    生成正文
                  </el-button>
                </div>
                <DraftDocumentCard
                  title="章节正文"
                  kicker="改正文 = 分叉：脚本与 VNGraph 需重做"
                  :drafting="Boolean(chapterDraft)"
                  :dirty="chapterSaveState === 'dirty'"
                  :saved-at="chapterDraft?.updatedAt ?? null"
                  :stale-message="chapterStaleMessage"
                  :memory-fallback="draftStore.usingMemoryFallback"
                  :suggestion="chapterSuggestion ? { revisionId: chapterSuggestion.id, revisionNo: chapterSuggestion.revision_no } : null"
                  @preview-suggestion="previewChapterSuggestion"
                  @adopt-suggestion="adoptChapterSuggestion"
                  @dismiss-suggestion="dismissChapterSuggestion"
                >
                  <template #actions>
                    <el-button
                      v-if="!chapterDraft"
                      size="small"
                      :icon="EditPen"
                      :disabled="!pathEditable || !selectedChapter || activeTaskBusy || generationSubmitting"
                      @click="startChapterDraft"
                    >
                      编辑（复制到草稿）
                    </el-button>
                    <template v-else>
                      <el-button size="small" type="primary" plain @click="saveChapterDraft(true)">
                        保存
                      </el-button>
                      <el-button size="small" type="danger" plain @click="discardChapterDraft">
                        丢弃草稿
                      </el-button>
                    </template>
                  </template>

                  <el-input
                    v-if="chapterDraft"
                    v-model="chapterDraftText"
                    type="textarea"
                    :rows="14"
                    resize="vertical"
                    class="draft-editor draft-editor-prose"
                    aria-label="章节正文草稿编辑器"
                    @input="onChapterDraftInput"
                  />
                  <article v-else-if="selectedChapterRevision" class="prose-preview">
                    {{ selectedChapterRevision.content }}
                  </article>
                  <el-empty v-else description="该章节尚无正文版本" />
                </DraftDocumentCard>
              </template>

              <template v-else-if="chapterStage === 'script'">
                <div class="generation-bar">
                  <el-input
                    v-model="scriptInstructions"
                    maxlength="12000"
                    clearable
                    placeholder="本次脚本要求（可选）"
                  />
                  <el-tooltip
                    :disabled="Boolean(chapterDraft) || Boolean(chapterHead?.revision_id) || Boolean(selectedChapterRevisionId)"
                    content="需要正文草稿或正文版本"
                    placement="top"
                  >
                    <span>
                      <el-button
                        type="primary"
                        :icon="MagicStick"
                        :disabled="!pathEditable || !selectedChapter || activeTaskBusy || (!chapterDraft && !chapterHead?.revision_id && !selectedChapterRevisionId) || generationSubmitting"
                        @click="generateScript"
                      >
                        生成脚本
                      </el-button>
                    </span>
                  </el-tooltip>
                  <el-tooltip
                    :disabled="Boolean(chapterHead?.revision_id) || Boolean(chapterDraft) || Boolean(selectedChapterRevisionId)"
                    content="需要正文版本后才能切分语音行"
                    placement="top"
                  >
                    <span>
                      <el-button
                        :icon="Headset"
                        :disabled="!pathEditable || !selectedChapter || activeTaskBusy || (!chapterDraft && !chapterHead?.revision_id && !selectedChapterRevisionId) || generationSubmitting"
                        @click="generateVoiceLines"
                      >
                        生成全章语音
                      </el-button>
                    </span>
                  </el-tooltip>
                </div>
                <DraftDocumentCard
                  title="章节脚本"
                  kicker="spans 只读（与正文逐字锁定），仅演出字段可编辑"
                  :drafting="Boolean(scriptDraft)"
                  :dirty="scriptSaveState === 'dirty'"
                  :saved-at="scriptDraft?.updatedAt ?? null"
                  :stale-message="scriptStaleMessage"
                  :memory-fallback="draftStore.usingMemoryFallback"
                  :suggestion="scriptSuggestion ? { revisionId: scriptSuggestion.id, revisionNo: scriptSuggestion.revision_no } : null"
                  @preview-suggestion="previewScriptSuggestion"
                  @adopt-suggestion="adoptScriptSuggestion"
                  @dismiss-suggestion="dismissScriptSuggestion"
                >
                  <template #actions>
                    <el-button
                      v-if="!scriptDraft"
                      size="small"
                      :icon="EditPen"
                      :disabled="!pathEditable || !selectedScriptRevision || activeTaskBusy || generationSubmitting"
                      @click="startScriptDraft"
                    >
                      编辑（复制到草稿）
                    </el-button>
                    <template v-else>
                      <el-button size="small" type="primary" plain @click="saveScriptDraft()">
                        保存
                      </el-button>
                      <el-button size="small" type="danger" plain @click="discardScriptDraft">
                        丢弃草稿
                      </el-button>
                    </template>
                  </template>

                  <div v-if="scriptDraft" class="script-draft-editor">
                    <el-alert
                      type="info"
                      :closable="false"
                      title="段落切分（spans）必须与正文逐字一致，手改会在保存时报 422；合法途径是重新生成脚本后采用。"
                      class="draft-banner"
                    />
                    <div
                      v-for="(paragraph, index) in scriptDraftParagraphs"
                      :key="`script-draft-paragraph:${index}`"
                      class="script-paragraph-row"
                    >
                      <div class="script-paragraph-head">
                        <span class="script-paragraph-index">#{{ index + 1 }}</span>
                        <el-tag size="small" effect="plain">{{ String(paragraph.kind ?? '段落') }}</el-tag>
                        <span class="script-paragraph-speaker">
                          {{ String(paragraph.speaker_display_name || paragraph.speaker_name || '旁白') }}
                        </span>
                      </div>
                      <div class="script-paragraph-fields">
                        <el-input
                          :model-value="String(paragraph.emotion ?? '')"
                          size="small"
                          placeholder="情绪，如 neutral / sad"
                          aria-label="段落情绪"
                          @update:model-value="(value: string) => setScriptParagraphField(paragraph, 'emotion', value)"
                        />
                        <el-input
                          :model-value="String(paragraph.keyframe_prompt ?? '')"
                          size="small"
                          placeholder="关键帧画面提示（可选）"
                          aria-label="关键帧提示"
                          @update:model-value="(value: string) => setScriptParagraphField(paragraph, 'keyframe_prompt', value)"
                        />
                        <el-checkbox
                          :model-value="Boolean(paragraph.keyframe_required)"
                          @change="(value: string | number | boolean) => setScriptParagraphField(paragraph, 'keyframe_required', Boolean(value))"
                        >
                          需要关键帧
                        </el-checkbox>
                      </div>
                    </div>
                    <details class="script-draft-raw">
                      <summary>查看完整脚本 JSON（只读）</summary>
                      <pre class="json-preview">{{ prettyJson(scriptDraft.payload.script_json) }}</pre>
                    </details>
                  </div>
                  <template v-else-if="selectedScriptRevision">
                    <pre class="json-preview">{{ prettyJson(selectedScriptRevision.script_json) }}</pre>
                    <div class="resource-section">
                      <div class="subsection-header">
                        <h4>资源槽</h4>
                        <div class="resource-actions">
                          <el-button
                            v-for="role in resourceRoles"
                            :key="role"
                            size="small"
                            :disabled="!pathEditable || activeTaskBusy || generationSubmitting"
                            @click="renderResources(role)"
                          >
                            {{ resourceRoleLabel(role) }}
                          </el-button>
                        </div>
                      </div>
                      <el-table :data="resourceSlots" size="small" empty-text="暂无资源槽">
                        <el-table-column label="类型" width="100">
                          <template #default="scope">{{ resourceRoleLabel(scope.row.role) }}</template>
                        </el-table-column>
                        <el-table-column prop="id" label="槽位">
                          <template #default="scope"><code>{{ shortId(scope.row.id, 12) }}</code></template>
                        </el-table-column>
                        <el-table-column label="资源版本">
                          <template #default="scope">
                            <el-tag :type="scope.row.asset_version_id ? 'success' : 'warning'" size="small" effect="plain">
                              {{ scope.row.asset_version_id ? shortId(scope.row.asset_version_id, 12) : '待生成' }}
                            </el-tag>
                          </template>
                        </el-table-column>
                      </el-table>
                    </div>
                  </template>
                  <el-empty v-else description="该正文版本尚无脚本" />
                </DraftDocumentCard>

                <div v-if="selectedChapterRevision" class="voice-lines-panel">
                  <header class="voice-lines-head">
                    <h4>章节语音</h4>
                    <span v-if="voiceLines.length" class="voice-lines-summary">
                      {{ voiceRenderedCount }}/{{ voiceLines.length }} 行已渲染
                    </span>
                    <el-button
                      size="small"
                      :icon="Refresh"
                      :loading="voiceLinesLoading"
                      @click="loadVoiceLines(true)"
                    >
                      刷新清单
                    </el-button>
                  </header>
                  <p v-if="!voiceLines.length" class="voice-lines-empty">
                    尚未生成语音。点击「生成全章语音」按正文逐行合成配音，完成后编译 VNGraph 即可在播放器中连播。
                  </p>
                  <ul v-else class="voice-lines-list">
                    <li
                      v-for="line in voiceLines"
                      :key="line.id"
                      class="voice-line-row"
                      :class="{ 'voice-line-pending': !line.audio_asset_version_id }"
                    >
                      <span class="voice-line-index">#{{ line.order_index + 1 }}</span>
                      <el-tag size="small" effect="plain">
                        {{ line.speaker_name || '旁白' }}
                      </el-tag>
                      <span v-if="line.emotion" class="voice-line-emotion">{{ line.emotion }}</span>
                      <span class="voice-line-text">{{ line.text }}</span>
                      <el-tag
                        size="small"
                        :type="line.audio_asset_version_id ? 'success' : 'info'"
                        effect="light"
                      >
                        {{ line.audio_asset_version_id ? '已渲染' : line.status }}
                      </el-tag>
                    </li>
                  </ul>
                </div>
              </template>

              <template v-else>
                <div class="generation-bar graph-bar">
                  <div class="compile-options">
                    <el-input v-model="graphSchemaVersion" clearable placeholder="Schema 版本（可选）" />
                    <el-input v-model="graphCompilerVersion" clearable placeholder="Compiler 版本（可选）" />
                  </div>
                  <el-tooltip
                    :disabled="Boolean(scriptDraft) || Boolean(scriptHead?.revision_id) || Boolean(selectedScriptRevisionId)"
                    content="需要脚本草稿或脚本版本"
                    placement="top"
                  >
                    <span>
                      <el-button
                        type="primary"
                        :icon="Connection"
                        :disabled="!pathEditable || !selectedChapter || activeTaskBusy || (!scriptDraft && !scriptHead?.revision_id && !selectedScriptRevisionId) || generationSubmitting"
                        @click="compileGraph"
                      >
                        编译 VNGraph
                      </el-button>
                    </span>
                  </el-tooltip>
                  <el-tooltip
                    :disabled="hasAnyLocalDraft"
                    content="没有本地草稿；将按服务端当前版本发布，若内容未就绪会被校验拦下"
                    placement="top"
                  >
                    <span>
                      <el-button
                        type="success"
                        :icon="Promotion"
                        :loading="publishingDrafts"
                        :disabled="!pathEditable || activeTaskBusy || generationSubmitting"
                        @click="publishDraftsAsBook"
                      >
                        确认发布成书
                      </el-button>
                    </span>
                  </el-tooltip>
                </div>
                <DraftDocumentCard
                  title="VNGraph"
                  kicker="发布成书时，草稿会连同全链一起固化为新版本"
                  :drafting="Boolean(graphDraft)"
                  :dirty="graphSaveState === 'dirty'"
                  :saved-at="graphDraft?.updatedAt ?? null"
                  :stale-message="graphStaleMessage"
                  :memory-fallback="draftStore.usingMemoryFallback"
                  :suggestion="graphSuggestion ? { revisionId: graphSuggestion.id, revisionNo: graphSuggestion.revision_no } : null"
                  @preview-suggestion="previewGraphSuggestion"
                  @adopt-suggestion="adoptGraphSuggestion"
                  @dismiss-suggestion="dismissGraphSuggestion"
                >
                  <template #actions>
                    <el-button
                      v-if="!graphDraft"
                      size="small"
                      :icon="EditPen"
                      :disabled="!pathEditable || !selectedGraphRevision || activeTaskBusy || generationSubmitting"
                      @click="startGraphDraft"
                    >
                      编辑（复制到草稿）
                    </el-button>
                    <template v-else>
                      <el-button size="small" type="primary" plain @click="saveGraphDraft(true)">
                        保存
                      </el-button>
                      <el-button size="small" type="danger" plain @click="discardGraphDraft">
                        丢弃草稿
                      </el-button>
                    </template>
                  </template>

                  <el-input
                    v-if="graphDraft"
                    v-model="graphDraftText"
                    type="textarea"
                    :rows="16"
                    resize="vertical"
                    class="draft-editor"
                    aria-label="VNGraph 草稿编辑器"
                    @input="onGraphDraftInput"
                  />
                  <template v-else-if="selectedGraphRevision">
                    <div class="document-meta">
                      <span>图 {{ shortId(selectedGraphRevision.id, 12) }}</span>
                      <span>绑定 {{ shortId(selectedGraphRevision.binding_manifest_hash, 12) }}</span>
                      <el-radio-group v-model="graphViewMode" size="small" class="graph-view-toggle">
                        <el-radio-button value="json">JSON</el-radio-button>
                        <el-radio-button value="play">播放</el-radio-button>
                      </el-radio-group>
                    </div>
                    <pre v-if="graphViewMode === 'json'" class="json-preview graph-preview">{{ prettyJson(selectedGraphRevision.graph_json) }}</pre>
                    <div v-else class="graph-player-wrap">
                      <VNGraphPlayer :graph="selectedGraphRevision.graph_json as unknown as VNGraph" />
                    </div>
                  </template>
                  <el-empty v-else description="该脚本版本尚无 VNGraph" />
                </DraftDocumentCard>
              </template>
            </div>
          </div>
          <el-empty v-else description="审阅大纲后将建立稳定章节顺序" />
        </section>

        <section v-else class="artifact-section branch-section">
          <header class="section-toolbar branch-toolbar">
            <div>
              <span class="section-kicker">{{ selectedPath?.title || '未选择路径' }}</span>
              <h2>分支候选</h2>
            </div>
            <div class="toolbar-actions branch-actions">
              <el-input v-model="checkpointId" clearable placeholder="检查点 UUID">
                <template #prepend>检查点</template>
              </el-input>
              <el-input v-model="candidateStateSnapshotId" clearable placeholder="状态快照 UUID">
                <template #prepend>状态</template>
              </el-input>
              <el-input-number
                v-model="candidateCount"
                :min="2"
                :max="4"
                :step="1"
                step-strictly
                controls-position="right"
                aria-label="候选数量"
              />
              <el-button
                :icon="Refresh"
                :disabled="!checkpointId || !selectedPath"
                @click="loadCandidateFamily(false, true)"
              >
                读取候选
              </el-button>
            </div>
          </header>

          <div class="branch-generation-bar">
            <div class="branch-source">
              <span>章节</span>
              <strong>{{ selectedChapter ? `第 ${selectedChapter.display_index} 章` : '未选择' }}</strong>
              <span>正文</span>
              <strong>{{ shortId(chapterHead?.revision_id, 12) }}</strong>
            </div>
            <el-input
              v-model="candidateInstructions"
              maxlength="12000"
              clearable
              placeholder="本次分支方向要求（可选）"
            />
            <el-button
              type="primary"
              :icon="MagicStick"
              :disabled="!canGenerateCandidates || activeTaskBusy || generationSubmitting"
              @click="generateCandidates"
            >
              生成候选
            </el-button>
          </div>

          <div v-if="branchCandidates.length > 0" class="candidate-list">
            <article v-for="candidate in branchCandidates" :key="candidate.id" class="candidate-item">
              <div class="candidate-key">{{ candidate.option_key }}</div>
              <div class="candidate-copy">
                <p>{{ candidate.preview_text }}</p>
                <pre v-if="Object.keys(candidate.state_delta).length > 0">{{ prettyJson(candidate.state_delta) }}</pre>
              </div>
              <el-tooltip
                :disabled="selectedCandidateRevisionId === candidateHead?.revision_id"
                content="先将候选集设为当前版本"
                placement="top"
              >
                <span>
                  <el-button
                    type="primary"
                    plain
                    :icon="Guide"
                    :disabled="!pathEditable || selectedCandidateRevisionId !== candidateHead?.revision_id || pathUpdating"
                    @click="promoteCandidate(candidate)"
                  >
                    创建路径
                  </el-button>
                </span>
              </el-tooltip>
            </article>
          </div>
          <el-empty v-else :description="checkpointId ? '当前候选集没有可提升选项' : '尚未选择章节检查点'" />
        </section>
      </main>

      <RevisionReviewPane
        :title="reviewTitle"
        :revisions="reviewOptions"
        :selected-id="reviewSelectedId"
        :head-id="reviewHeadId"
        :loading="reviewLoading"
        :activating="activatingRevision"
        :disabled="reviewActivationDisabled"
        :empty-label="reviewEmptyLabel"
        @select="selectReviewRevision"
        @activate="activateReviewRevision"
        @refresh="refreshReview"
      />
    </div>

    <el-dialog
      :model-value="Boolean(suggestionPreview)"
      :title="suggestionPreview?.title || '建议预览'"
      width="min(760px, 92vw)"
      @update:model-value="(visible: boolean) => { if (!visible) closeSuggestionPreview() }"
    >
      <BibleContentView
        v-if="suggestionPreview?.bibleContent"
        :content="suggestionPreview.bibleContent"
      />
      <pre v-else class="manual-editor suggestion-preview-body">{{ suggestionPreview?.body }}</pre>
      <template #footer>
        <el-button @click="closeSuggestionPreview">关闭</el-button>
      </template>
    </el-dialog>

    <PublicationPanel
      :model-value="publicationPanelVisible"
      :project="project"
      @update:model-value="setPublicationPanelVisible"
      @updated="project = $event"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft,
  CircleCheck,
  Close,
  Connection,
  EditPen,
  Loading,
  MagicStick,
  Guide,
  Headset,
  Plus,
  Promotion,
  Refresh,
} from '@element-plus/icons-vue'
import api from '@/api/index'
import PublicationPanel from '@/components/PublicationPanel.vue'
import StoryPathTree from '@/components/StoryPathTree.vue'
import RevisionReviewPane from '@/components/RevisionReviewPane.vue'
import DraftDocumentCard from '@/components/DraftDocumentCard.vue'
import VNGraphPlayer from '@/components/VNGraphPlayer.vue'
import BibleContentView from '@/components/BibleContentView.vue'
import type { VNGraph } from '@/data/threeKingdoms'
import { createIdempotencyKey, storyPathApi } from '@/api/storyPathApi'
import type {
  BibleRevision,
  BranchCandidate,
  CandidateSetRevision,
  ChapterRevision,
  FinalizePublishRequest,
  JsonObject,
  OutlineChapterInput,
  OutlineRevision,
  PathChapter,
  ProjectRead,
  ResourceRole,
  ResourceSlot,
  RevisionHead,
  ScriptRevision,
  StoryPath,
  TaskAccepted,
  UUID,
  VoiceLineItem,
  VNGraphRevision,
} from '@/api/storyPathTypes'
import { describeTaskFailure, pollTaskUntilTerminal } from '@/utils/taskPolling'
import { getSafeErrorSummary } from '@/security/safeError'
import { shortId, titleForPathChapter, toRevisionOptions } from '@/utils/storyPathWorkspace'
import {
  buildUpstreamHashes,
  createDebouncedSaver,
  draftFingerprint,
  findPendingSuggestion,
  isDraftStale,
  useArtifactDraft,
  type ArtifactDraftRecord,
} from '@/composables/useArtifactDraft'

type WorkspaceArea = 'bible' | 'outline' | 'chapters' | 'branches'
type ChapterStage = 'chapter' | 'script' | 'graph'

/** 大纲草稿载荷：chapters + 记录锚定的 bible 修订（物化时可被覆盖）。 */
interface OutlineDraftPayload {
  bible_revision_id: UUID | null
  chapters: OutlineChapterInput[]
}

/** 脚本草稿载荷：script_json + 复制来源的正文修订（跨 family 时 parent 作废）。 */
interface ScriptDraftPayload {
  script_json: JsonObject
  source_chapter_revision_id: UUID | null
}

/** 图草稿载荷：graph_json + 复制来源的脚本修订（跨 family 时 parent 作废）。 */
interface GraphDraftPayload {
  graph_json: JsonObject
  source_script_revision_id: UUID | null
}

type DraftSaveState = 'clean' | 'dirty' | 'saving'

interface TaskEventLogEntry {
  seq: number
  event_type: string
  payload: Record<string, unknown>
}

interface TrackedTask {
  id: string
  label: string
  status: string
  progress: number
}

const route = useRoute()
const router = useRouter()
const projectId = Number(route.params.id)

const project = ref<ProjectRead | null>(null)
const paths = ref<StoryPath[]>([])
const selectedPathId = ref('')
const pathChapters = ref<PathChapter[]>([])
const selectedChapterId = ref('')

const bibleRevisions = ref<BibleRevision[]>([])
const bibleHead = ref<RevisionHead | null>(null)
const selectedBibleRevisionId = ref('')

const outlineRevisions = ref<OutlineRevision[]>([])
const outlineHead = ref<RevisionHead | null>(null)
const selectedOutlineRevisionId = ref('')

const chapterRevisions = ref<ChapterRevision[]>([])
const chapterHead = ref<RevisionHead | null>(null)
const selectedChapterRevisionId = ref('')

const scriptRevisions = ref<ScriptRevision[]>([])
const scriptHead = ref<RevisionHead | null>(null)
const selectedScriptRevisionId = ref('')

const resourceSlots = ref<ResourceSlot[]>([])
const graphRevisions = ref<VNGraphRevision[]>([])
const graphHead = ref<RevisionHead | null>(null)
const selectedGraphRevisionId = ref('')
// VNGraph tab：JSON / 播放 双模式切换
const graphViewMode = ref<'json' | 'play'>('json')

const candidateRevisions = ref<CandidateSetRevision[]>([])
const candidateHead = ref<RevisionHead | null>(null)
const selectedCandidateRevisionId = ref('')
const branchCandidates = ref<BranchCandidate[]>([])

const activeArea = ref<WorkspaceArea>('bible')
const chapterStage = ref<ChapterStage>('chapter')
const selectedBatchIds = ref<string[]>([])
const initialLoading = ref(false)
const pathLoading = ref(false)
const reviewLoading = ref(false)
const pathUpdating = ref(false)
const activatingRevision = ref(false)
const fatalError = ref('')
const publicationPanelVisible = ref(queryString(route.query.publication) === '1')

const bibleInstructions = ref('')
const outlineInstructions = ref('')
const outlineChapterCount = ref(13)
const chapterInstructions = ref('')
const scriptInstructions = ref('')
const graphSchemaVersion = ref('')
const graphCompilerVersion = ref('')
const checkpointId = ref(queryString(route.query.checkpoint))
const candidateStateSnapshotId = ref('')
const candidateCount = ref(3)
const candidateInstructions = ref('')

/* ------------------------------ 客户端草稿区 ------------------------------ */

const draftStore = useArtifactDraft()
const draftSaver = createDebouncedSaver(800)

const bibleDraft = ref<ArtifactDraftRecord<JsonObject> | null>(null)
const outlineDraft = ref<ArtifactDraftRecord<OutlineDraftPayload> | null>(null)
const chapterDraft = ref<ArtifactDraftRecord<string> | null>(null)
const scriptDraft = ref<ArtifactDraftRecord<ScriptDraftPayload> | null>(null)
const graphDraft = ref<ArtifactDraftRecord<GraphDraftPayload> | null>(null)

const bibleDraftText = ref('')
const outlineDraftText = ref('')
const chapterDraftText = ref('')
const graphDraftText = ref('')

const bibleSaveState = ref<DraftSaveState>('clean')
const outlineSaveState = ref<DraftSaveState>('clean')
const chapterSaveState = ref<DraftSaveState>('clean')
const scriptSaveState = ref<DraftSaveState>('clean')
const graphSaveState = ref<DraftSaveState>('clean')

const bibleSuggestion = ref<BibleRevision | null>(null)
const outlineSuggestion = ref<OutlineRevision | null>(null)
const chapterSuggestion = ref<ChapterRevision | null>(null)
const scriptSuggestion = ref<ScriptRevision | null>(null)
const graphSuggestion = ref<(VNGraphRevision & { revision_no: number }) | null>(null)

const publishingDrafts = ref(false)
const suggestionPreview = ref<{
  title: string
  body: string
  bibleContent?: JsonObject
} | null>(null)

const activeTask = ref<TrackedTask | null>(null)
const resourceRoles: ResourceRole[] = ['portrait', 'background', 'keyframe']
const taskStorageKey = `if-line:story-path-task:${projectId}`

let pathRequestToken = 0
let chapterRequestToken = 0
let scriptRequestToken = 0
let candidateRequestToken = 0

const selectedPath = computed(() => paths.value.find((path) => path.id === selectedPathId.value) ?? null)
const selectedChapter = computed(() => pathChapters.value.find((chapter) => chapter.id === selectedChapterId.value) ?? null)
const selectedBibleRevision = computed(() => bibleRevisions.value.find((item) => item.id === selectedBibleRevisionId.value) ?? null)
const selectedOutlineRevision = computed(() => outlineRevisions.value.find((item) => item.id === selectedOutlineRevisionId.value) ?? null)
const selectedChapterRevision = computed(() => chapterRevisions.value.find((item) => item.id === selectedChapterRevisionId.value) ?? null)
const selectedScriptRevision = computed(() => scriptRevisions.value.find((item) => item.id === selectedScriptRevisionId.value) ?? null)
const selectedGraphRevision = computed(() => graphRevisions.value.find((item) => item.id === selectedGraphRevisionId.value) ?? null)
const selectedCandidateRevision = computed(() => candidateRevisions.value.find((item) => item.id === selectedCandidateRevisionId.value) ?? null)
const pathEditable = computed(() => selectedPath.value?.status === 'active')
const canGenerateCandidates = computed(() => Boolean(
  pathEditable.value
  && selectedPath.value
  && selectedChapter.value
  && (chapterHead.value?.revision_id || selectedChapterRevisionId.value)
  && checkpointId.value.trim()
  && candidateStateSnapshotId.value.trim()
  && Number.isInteger(candidateCount.value)
  && candidateCount.value >= 2
  && candidateCount.value <= 4,
))

const activeTaskBusy = computed(() => Boolean(
  activeTask.value && ['queued', 'pending', 'running', 'accepted'].includes(activeTask.value.status),
))

// 提交期防双发：activeTask 要等 POST 返回 202 才置位，点击到响应之间存在
// 空窗（幂等键每次点击都是新 UUID，服务端不会去重），用独立 ref 盖住空窗。
const generationSubmitting = ref(false)

const taskStatusLabel = computed(() => {
  const labels: Record<string, string> = {
    queued: '排队中',
    pending: '等待中',
    running: '生成中',
    accepted: '已受理',
    succeeded: '已完成',
    failed: '失败',
    cancelled: '已取消',
    background: '后台继续',
  }
  return labels[activeTask.value?.status || ''] || activeTask.value?.status || ''
})

const authoringStageLabel = computed(() => {
  const labels: Record<string, string> = {
    bible: '等待故事设定',
    story_paths: '等待剧情路径',
    outlines: '等待章节大纲',
    chapters: '等待章节正文',
    scripts: '等待章节脚本',
    vn_graphs: '等待 VNGraph',
    ready: '创作就绪',
    blocked: '存在阻塞项',
  }
  return labels[project.value?.authoring.stage || ''] || '加载中'
})

const stageTagType = computed<'success' | 'warning' | 'info' | 'danger'>(() => {
  if (project.value?.authoring.stage === 'ready') return 'success'
  if (project.value?.authoring.stage === 'blocked') return 'danger'
  if ((project.value?.authoring.running_task_count || 0) > 0) return 'warning'
  return 'info'
})

const publicationStateLabel = computed(() => {
  if (project.value?.publication.state === 'published') return '已公开'
  if (project.value?.publication.state === 'changes_pending') return '有未发布修改'
  return '未发布'
})

const publicationTagType = computed<'success' | 'warning' | 'info'>(() => {
  if (project.value?.publication.state === 'published') return 'success'
  if (project.value?.publication.state === 'changes_pending') return 'warning'
  return 'info'
})

const allChaptersSelected = computed(() => (
  pathChapters.value.length > 0 && selectedBatchIds.value.length === pathChapters.value.length
))
const someChaptersSelected = computed(() => (
  selectedBatchIds.value.length > 0 && !allChaptersSelected.value
))

/* ------------------------- 草稿：上游有效 hash / stale -------------------------
 * 配方（保存与校验一致）：上游有草稿 → 草稿载荷指纹；无草稿 → head 修订的
 * content_hash（来自服务端 API）。两套哈希空间不互相比较。 */

function headRevisionOf<T extends { id: string }>(
  revisions: readonly T[],
  head: RevisionHead | null,
): T | null {
  if (!head?.revision_id) return null
  return revisions.find((item) => item.id === head.revision_id) ?? null
}

function bibleEffectiveHash(): string | null {
  if (bibleDraft.value) return draftFingerprint(bibleDraft.value.payload)
  return headRevisionOf(bibleRevisions.value, bibleHead.value)?.content_hash ?? null
}

function syncOutlineChapterCount(): void {
  const headRevision = headRevisionOf(outlineRevisions.value, outlineHead.value)
  const count = headRevision?.chapters?.length
  if (Number.isInteger(count) && count >= 1 && count <= 500) {
    outlineChapterCount.value = count
  }
}

function outlineEffectiveHash(): string | null {
  if (outlineDraft.value) return draftFingerprint(outlineDraft.value.payload.chapters)
  return headRevisionOf(outlineRevisions.value, outlineHead.value)?.content_hash ?? null
}

function chapterEffectiveHash(): string | null {
  if (chapterDraft.value) return draftFingerprint(chapterDraft.value.payload)
  return headRevisionOf(chapterRevisions.value, chapterHead.value)?.content_hash ?? null
}

function scriptEffectiveHash(): string | null {
  if (scriptDraft.value) return draftFingerprint(scriptDraft.value.payload.script_json)
  return headRevisionOf(scriptRevisions.value, scriptHead.value)?.script_hash ?? null
}

const outlineStaleMessage = computed(() => {
  if (!outlineDraft.value) return null
  return isDraftStale(outlineDraft.value, { bible: bibleEffectiveHash() })
    ? '故事设定在保存大纲草稿后已变化：请重新生成大纲或重新采用结果'
    : null
})

const chapterStaleMessage = computed(() => {
  if (!chapterDraft.value) return null
  return isDraftStale(chapterDraft.value, {
    bible: bibleEffectiveHash(),
    outline: outlineEffectiveHash(),
  })
    ? '设定或大纲在保存正文草稿后已变化：正文可能需要重写'
    : null
})

const scriptStaleMessage = computed(() => {
  if (!scriptDraft.value) return null
  return isDraftStale(scriptDraft.value, { chapter: chapterEffectiveHash() })
    ? '正文在保存脚本草稿后已变化：脚本需重新生成（spans 必须与正文逐字一致）'
    : null
})

const graphStaleMessage = computed(() => {
  if (!graphDraft.value) return null
  return isDraftStale(graphDraft.value, { script: scriptEffectiveHash() })
    ? '脚本在保存图草稿后已变化：VNGraph 需重新编译'
    : null
})

const scriptDraftParagraphs = computed<JsonObject[]>(() => {
  const paragraphs = scriptDraft.value?.payload.script_json?.paragraphs
  return Array.isArray(paragraphs)
    ? paragraphs.filter((item): item is JsonObject => Boolean(item) && typeof item === 'object')
    : []
})

const hasAnyLocalDraft = computed(() => Boolean(
  bibleDraft.value || outlineDraft.value || chapterDraft.value || scriptDraft.value || graphDraft.value,
))

const reviewTitle = computed(() => {
  if (activeArea.value === 'bible') return '故事设定'
  if (activeArea.value === 'outline') return '章节大纲'
  if (activeArea.value === 'branches') return '候选集'
  if (chapterStage.value === 'chapter') return '章节正文'
  if (chapterStage.value === 'script') return '章节脚本'
  return 'VNGraph'
})

const reviewOptions = computed(() => {
  if (activeArea.value === 'bible') return toRevisionOptions(bibleRevisions.value, '设定')
  if (activeArea.value === 'outline') return toRevisionOptions(outlineRevisions.value, '大纲')
  if (activeArea.value === 'branches') return toRevisionOptions(candidateRevisions.value, '候选')
  if (chapterStage.value === 'chapter') return toRevisionOptions(chapterRevisions.value, '正文')
  if (chapterStage.value === 'script') return toRevisionOptions(scriptRevisions.value, '脚本')
  return toRevisionOptions(graphRevisions.value, '图')
})

const reviewSelectedId = computed(() => {
  if (activeArea.value === 'bible') return selectedBibleRevisionId.value
  if (activeArea.value === 'outline') return selectedOutlineRevisionId.value
  if (activeArea.value === 'branches') return selectedCandidateRevisionId.value
  if (chapterStage.value === 'chapter') return selectedChapterRevisionId.value
  if (chapterStage.value === 'script') return selectedScriptRevisionId.value
  return selectedGraphRevisionId.value
})

const reviewHeadId = computed(() => {
  if (activeArea.value === 'bible') return bibleHead.value?.revision_id ?? null
  if (activeArea.value === 'outline') return outlineHead.value?.revision_id ?? null
  if (activeArea.value === 'branches') return candidateHead.value?.revision_id ?? null
  if (chapterStage.value === 'chapter') return chapterHead.value?.revision_id ?? null
  if (chapterStage.value === 'script') return scriptHead.value?.revision_id ?? null
  return graphHead.value?.revision_id ?? null
})

const reviewEmptyLabel = computed(() => {
  if (activeArea.value === 'branches' && !checkpointId.value) return '请先选择章节检查点'
  if (activeArea.value === 'chapters' && !selectedChapter.value) return '请先选择路径章节'
  if (activeArea.value === 'chapters' && chapterStage.value !== 'chapter' && !selectedChapterRevision.value) {
    return '请先选择正文版本'
  }
  return '暂无可审阅版本'
})

const reviewActivationDisabled = computed(() => {
  if (activeArea.value === 'bible') return false
  if (!pathEditable.value) return true
  return activeArea.value === 'branches' && !checkpointId.value
})

function queryString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function prettyJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2)
}

function resourceRoleLabel(role: ResourceRole): string {
  return { portrait: '角色立绘', background: '背景', keyframe: '关键帧' }[role]
}

function chapterTitle(chapter: PathChapter): string {
  return titleForPathChapter(chapter, selectedOutlineRevision.value?.chapters)
}

function reportError(context: string, error: unknown, fallback: string): void {
  const summary = getSafeErrorSummary(error)
  console.error(context, summary)
  if (summary.status === 409) {
    const body = (error as {
      response?: { data?: { detail?: unknown; error?: { code?: unknown; message?: unknown } } }
    })?.response?.data
    const errorCode = typeof body?.error?.code === 'string' ? body.error.code : undefined
    const legacyDetail = typeof body?.detail === 'string' ? body.detail : undefined
    const structuredMessage =
      typeof body?.error?.message === 'string' ? body.error.message : undefined
    if (errorCode === 'head.version_conflict') {
      ElMessage.warning('内容已被其他操作更新，已刷新当前版本')
    } else {
      ElMessage.warning(legacyDetail ?? structuredMessage ?? '操作冲突，请刷新后重试')
    }
  } else if (summary.status === 404) {
    ElMessage.error('目标内容不存在或已不可访问')
  } else {
    ElMessage.error(fallback)
  }
}

function replaceSelectionQuery(pathId: string, chapterId = ''): void {
  const query = { ...route.query, path: pathId } as Record<string, string | string[] | null | undefined>
  if (chapterId) query.chapter = chapterId
  else delete query.chapter
  if (checkpointId.value) query.checkpoint = checkpointId.value
  else delete query.checkpoint
  router.replace({ name: 'Project', params: { id: String(projectId) }, query }).catch(() => {})
}

function syncCheckpointQuery(): void {
  if (!selectedPathId.value) return
  replaceSelectionQuery(selectedPathId.value, selectedChapterId.value)
}

function setPublicationPanelVisible(visible: boolean): void {
  publicationPanelVisible.value = visible
  const query = { ...route.query } as Record<string, string | string[] | null | undefined>
  if (visible) query.publication = '1'
  else delete query.publication
  router.replace({ name: 'Project', params: { id: String(projectId) }, query }).catch(() => {})
}

function stateSnapshotFromRevision(revision: ChapterRevision | null): string {
  if (!revision) return ''
  const direct = revision.context_manifest.state_snapshot_id
  if (typeof direct === 'string') return direct
  const state = revision.context_manifest.state
  if (state && typeof state === 'object' && !Array.isArray(state)) {
    const nested = state.id ?? state.state_snapshot_id
    if (typeof nested === 'string') return nested
  }
  return ''
}

function chooseRevision(
  currentId: string,
  head: RevisionHead | null,
  revisions: Array<{ id: string }>,
  preferLatest: boolean,
): string {
  if (preferLatest && revisions[0]) return revisions[0].id
  if (currentId && revisions.some((item) => item.id === currentId)) return currentId
  if (head?.revision_id && revisions.some((item) => item.id === head.revision_id)) return head.revision_id
  return revisions[0]?.id || ''
}

async function loadProject(): Promise<void> {
  const response = await storyPathApi.projects.get(projectId)
  project.value = response.data
}

async function loadPaths(): Promise<void> {
  const response = await storyPathApi.storyPaths.list(projectId)
  paths.value = response.data
}

async function loadBible(preferLatest = false): Promise<void> {
  reviewLoading.value = true
  try {
    const [revisionsResponse, headResponse] = await Promise.all([
      storyPathApi.bible.listRevisions(projectId),
      storyPathApi.bible.getHead(projectId),
    ])
    bibleRevisions.value = revisionsResponse.data
    bibleHead.value = headResponse.data
    selectedBibleRevisionId.value = chooseRevision(
      selectedBibleRevisionId.value,
      bibleHead.value,
      bibleRevisions.value,
      preferLatest,
    )
    reloadBibleDraft()
    refreshBibleSuggestion()
  } finally {
    reviewLoading.value = false
  }
}

async function selectPath(path: StoryPath, syncRoute: boolean, preferredChapterId = ''): Promise<void> {
  const token = ++pathRequestToken
  const pathChanged = Boolean(selectedPathId.value && selectedPathId.value !== path.id)
  if (pathChanged) {
    checkpointId.value = ''
    candidateStateSnapshotId.value = ''
    clearCandidateFamily()
  }
  selectedPathId.value = path.id
  selectedChapterId.value = ''
  pathChapters.value = []
  outlineRevisions.value = []
  outlineHead.value = null
  clearChapterFamily()
  if (syncRoute) replaceSelectionQuery(path.id)
  pathLoading.value = true
  reviewLoading.value = true

  try {
    const [revisionsResponse, headResponse, chaptersResponse] = await Promise.all([
      storyPathApi.outlines.listRevisions(path.id),
      storyPathApi.outlines.getHead(path.id),
      storyPathApi.chapters.list(path.id),
    ])
    if (token !== pathRequestToken) return
    outlineRevisions.value = revisionsResponse.data
    outlineHead.value = headResponse.data
    selectedOutlineRevisionId.value = chooseRevision(
      selectedOutlineRevisionId.value,
      outlineHead.value,
      outlineRevisions.value,
      false,
    )
    // 章节数随当前 head 大纲初始化，避免默认 13 与实际章节数脱节误导再生成
    syncOutlineChapterCount()
    reloadOutlineDraft()
    refreshOutlineSuggestion()
    pathChapters.value = chaptersResponse.data
    selectedBatchIds.value = []

    const routeChapterId = preferredChapterId || queryString(route.query.chapter)
    const nextChapter = pathChapters.value.find((item) => item.id === routeChapterId)
      ?? pathChapters.value[0]
    if (nextChapter) await selectChapter(nextChapter.id, syncRoute)
    else if (syncRoute) replaceSelectionQuery(path.id)
  } catch (error) {
    if (token === pathRequestToken) reportError('load StoryPath artifacts', error, '路径内容加载失败')
  } finally {
    if (token === pathRequestToken) {
      pathLoading.value = false
      reviewLoading.value = false
    }
  }
}

function clearChapterFamily(): void {
  chapterRevisions.value = []
  chapterHead.value = null
  selectedChapterRevisionId.value = ''
  chapterDraft.value = null
  chapterDraftText.value = ''
  chapterSaveState.value = 'clean'
  chapterSuggestion.value = null
  clearScriptFamily()
}

function clearScriptFamily(): void {
  scriptRevisions.value = []
  scriptHead.value = null
  selectedScriptRevisionId.value = ''
  scriptDraft.value = null
  scriptSaveState.value = 'clean'
  scriptSuggestion.value = null
  resourceSlots.value = []
  graphRevisions.value = []
  graphHead.value = null
  selectedGraphRevisionId.value = ''
  graphDraft.value = null
  graphDraftText.value = ''
  graphSaveState.value = 'clean'
  graphSuggestion.value = null
}

function clearCandidateFamily(): void {
  candidateRequestToken += 1
  candidateRevisions.value = []
  candidateHead.value = null
  selectedCandidateRevisionId.value = ''
  branchCandidates.value = []
}

async function selectChapter(chapterId: string, syncRoute: boolean, preferLatest = false): Promise<void> {
  const chapter = pathChapters.value.find((item) => item.id === chapterId)
  if (!chapter) return
  const token = ++chapterRequestToken
  const chapterChanged = Boolean(selectedChapterId.value && selectedChapterId.value !== chapter.id)
  if (chapterChanged) {
    checkpointId.value = ''
    candidateStateSnapshotId.value = ''
    clearCandidateFamily()
  }
  selectedChapterId.value = chapter.id
  clearChapterFamily()
  if (syncRoute) replaceSelectionQuery(selectedPathId.value, chapter.id)
  reviewLoading.value = true

  try {
    const [revisionsResponse, headResponse] = await Promise.all([
      storyPathApi.chapters.listRevisions(chapter.id),
      storyPathApi.chapters.getHead(chapter.id),
    ])
    if (token !== chapterRequestToken) return
    chapterRevisions.value = revisionsResponse.data
    chapterHead.value = headResponse.data
    selectedChapterRevisionId.value = chooseRevision(
      selectedChapterRevisionId.value,
      chapterHead.value,
      chapterRevisions.value,
      preferLatest,
    )
    reloadChapterDraft()
    refreshChapterSuggestion()
    const headRevision = chapterRevisions.value.find((item) => item.id === chapterHead.value?.revision_id) ?? null
    if (!candidateStateSnapshotId.value) {
      candidateStateSnapshotId.value = stateSnapshotFromRevision(headRevision)
    }
    if (selectedChapterRevisionId.value) {
      await loadScriptFamily(selectedChapterRevisionId.value, false)
    }
  } catch (error) {
    if (token === chapterRequestToken) reportError('load chapter revisions', error, '章节版本加载失败')
  } finally {
    if (token === chapterRequestToken) reviewLoading.value = false
  }
}

async function loadScriptFamily(chapterRevisionId: string, preferLatest = false): Promise<void> {
  const token = ++scriptRequestToken
  clearScriptFamily()
  reviewLoading.value = true
  try {
    const [revisionsResponse, headResponse] = await Promise.all([
      storyPathApi.scripts.listRevisions(chapterRevisionId),
      storyPathApi.scripts.getHead(chapterRevisionId),
    ])
    if (token !== scriptRequestToken) return
    scriptRevisions.value = revisionsResponse.data
    scriptHead.value = headResponse.data
    selectedScriptRevisionId.value = chooseRevision(
      selectedScriptRevisionId.value,
      scriptHead.value,
      scriptRevisions.value,
      preferLatest,
    )
    reloadScriptDraft()
    refreshScriptSuggestion()
    if (selectedScriptRevisionId.value) await loadScriptArtifacts(selectedScriptRevisionId.value)
  } catch (error) {
    if (token === scriptRequestToken) reportError('load script revisions', error, '脚本版本加载失败')
  } finally {
    if (token === scriptRequestToken) reviewLoading.value = false
  }
}

async function loadScriptArtifacts(scriptRevisionId: string, preferLatestGraph = false): Promise<void> {
  const token = scriptRequestToken
  resourceSlots.value = []
  graphRevisions.value = []
  graphHead.value = null
  selectedGraphRevisionId.value = ''
  try {
    const [slotsResponse, revisionsResponse, headResponse] = await Promise.all([
      storyPathApi.resources.list(scriptRevisionId),
      storyPathApi.vnGraphs.listRevisions(scriptRevisionId),
      storyPathApi.vnGraphs.getHead(scriptRevisionId),
    ])
    if (token !== scriptRequestToken || selectedScriptRevisionId.value !== scriptRevisionId) return
    resourceSlots.value = slotsResponse.data
    graphRevisions.value = revisionsResponse.data
    graphHead.value = headResponse.data
    selectedGraphRevisionId.value = chooseRevision(
      selectedGraphRevisionId.value,
      graphHead.value,
      graphRevisions.value,
      preferLatestGraph,
    )
    reloadGraphDraft()
    refreshGraphSuggestion()
  } catch (error) {
    if (token === scriptRequestToken) reportError('load script resources', error, '脚本资源加载失败')
  }
}

async function loadCandidateOptions(candidateSetRevisionId: string): Promise<void> {
  const token = candidateRequestToken
  const response = await storyPathApi.candidates.listCandidates(candidateSetRevisionId)
  if (token !== candidateRequestToken || selectedCandidateRevisionId.value !== candidateSetRevisionId) return
  branchCandidates.value = response.data
}

async function loadCandidateFamily(preferLatest = false, syncRoute = false): Promise<void> {
  const path = selectedPath.value
  const nodeId = checkpointId.value.trim()
  if (!path || !nodeId) {
    clearCandidateFamily()
    return
  }
  const token = ++candidateRequestToken
  reviewLoading.value = true
  branchCandidates.value = []
  if (syncRoute) syncCheckpointQuery()
  try {
    const [revisionsResponse, headResponse] = await Promise.all([
      storyPathApi.candidates.listRevisions(path.id, nodeId),
      storyPathApi.candidates.getHead(path.id, nodeId),
    ])
    if (token !== candidateRequestToken) return
    candidateRevisions.value = revisionsResponse.data
    candidateHead.value = headResponse.data
    selectedCandidateRevisionId.value = chooseRevision(
      selectedCandidateRevisionId.value,
      candidateHead.value,
      candidateRevisions.value,
      preferLatest,
    )
    const selected = candidateRevisions.value.find(
      (item) => item.id === selectedCandidateRevisionId.value,
    )
    if (selected) {
      candidateStateSnapshotId.value = selected.state_snapshot_id
      await loadCandidateOptions(selected.id)
    }
  } catch (error) {
    if (token === candidateRequestToken) {
      reportError('load candidate revisions', error, '候选集加载失败')
    }
  } finally {
    if (token === candidateRequestToken) reviewLoading.value = false
  }
}

async function refreshPaths(): Promise<void> {
  pathLoading.value = true
  try {
    await loadPaths()
    const current = paths.value.find((item) => item.id === selectedPathId.value)
      ?? paths.value.find((item) => item.id === project.value?.root_story_path_id)
      ?? paths.value[0]
    if (current) await selectPath(current, false, selectedChapterId.value)
  } catch (error) {
    reportError('refresh paths', error, '路径刷新失败')
  } finally {
    pathLoading.value = false
  }
}

async function refreshWorkspace(): Promise<void> {
  if (!Number.isSafeInteger(projectId) || projectId < 1) {
    fatalError.value = '项目 ID 无效'
    return
  }
  initialLoading.value = true
  fatalError.value = ''
  try {
    await Promise.all([loadProject(), loadPaths(), loadBible(false)])
    const requestedPathId = queryString(route.query.path)
    const requestedPath = paths.value.find((item) => item.id === requestedPathId)
    const rootPath = paths.value.find((item) => item.id === project.value?.root_story_path_id)
      ?? paths.value.find((item) => !item.parent_path_id)
      ?? paths.value[0]
    if (requestedPath || rootPath) {
      await selectPath(requestedPath ?? rootPath, true, queryString(route.query.chapter))
    }
    if (checkpointId.value) await loadCandidateFamily(false, true)
    resumeStoredTask()
  } catch (error) {
    const summary = getSafeErrorSummary(error)
    console.error('load StoryPath workspace', summary)
    fatalError.value = summary.status === 404
      ? '项目不存在或当前账号无权访问'
      : '无法读取项目创作数据'
  } finally {
    initialLoading.value = false
  }
}

function persistTask(task: TrackedTask | null): void {
  if (typeof window === 'undefined') return
  try {
    if (task) window.sessionStorage.setItem(taskStorageKey, JSON.stringify(task))
    else window.sessionStorage.removeItem(taskStorageKey)
  } catch {
    // Task recovery is best effort; request idempotency remains server-side.
  }
}

async function trackTask(
  accepted: TaskAccepted,
  label: string,
  onSuccess: () => Promise<void>,
  deadlineMs = 15 * 60 * 1000,
): Promise<void> {
  activeTask.value = {
    id: accepted.task_id,
    label,
    status: accepted.status || 'accepted',
    progress: 0,
  }
  persistTask(activeTask.value)
  const result = await pollTaskUntilTerminal(accepted.task_id, {
    deadlineMs,
    onProgress(task) {
      activeTask.value = {
        id: task.id,
        label,
        status: task.status,
        progress: Math.max(0, Math.min(100, Math.round(task.progress || 0))),
      }
      persistTask(activeTask.value)
    },
  })

  if (result.succeeded) {
    activeTask.value = { id: accepted.task_id, label, status: 'succeeded', progress: 100 }
    persistTask(null)
    await onSuccess()
    await loadProject()
    ElMessage.success(`${label}已完成，请审阅新版本`)
  } else if (result.timedOut) {
    activeTask.value = { id: accepted.task_id, label, status: 'background', progress: result.task.progress || 0 }
    persistTask(activeTask.value)
    ElMessage.warning(`${label}仍在后台执行`)
  } else {
    activeTask.value = { id: accepted.task_id, label, status: result.task.status, progress: result.task.progress || 0 }
    persistTask(null)
    ElMessage.error(describeTaskFailure(result.task, `${label}失败`) || `${label}失败`)
  }
}

async function runGeneration(
  label: string,
  request: () => Promise<{ data: TaskAccepted }>,
  onSuccess: () => Promise<void>,
  deadlineMs?: number,
): Promise<void> {
  if (activeTaskBusy.value || generationSubmitting.value) return
  generationSubmitting.value = true
  try {
    const response = await request()
    await trackTask(response.data, label, onSuccess, deadlineMs)
  } catch (error) {
    reportError(`start ${label}`, error, `${label}提交失败`)
  } finally {
    generationSubmitting.value = false
  }
}

function resumeStoredTask(): void {
  if (typeof window === 'undefined' || activeTaskBusy.value) return
  try {
    const raw = window.sessionStorage.getItem(taskStorageKey)
    if (!raw) return
    const stored = JSON.parse(raw) as Partial<TrackedTask>
    if (!stored.id || !stored.label) return
    void trackTask(
      { task_id: stored.id, status: stored.status || 'running', events_url: '', created: false },
      stored.label,
      async () => {
        await Promise.all([loadBible(true), refreshPaths()])
        if (checkpointId.value) await loadCandidateFamily(true, true)
      },
    ).catch((error) => reportError('resume generation task', error, '后台任务状态恢复失败'))
  } catch {
    persistTask(null)
  }
}

async function generateBible(): Promise<void> {
  // 存在未采用的 AI 建议时先确认，避免建议越积越多无人处理
  //（深海信标曾 35 秒双发产生 r1/r2 两版，r2 永久悬挂）。
  if (bibleSuggestion.value) {
    try {
      await ElMessageBox.confirm(
        `存在尚未处理的 AI 建议版本 r${bibleSuggestion.value.revision_no}。建议先「预览」并采用或丢弃；继续生成会再产出一版新建议。`,
        '仍有未采用的建议',
        { type: 'warning', confirmButtonText: '仍要生成', cancelButtonText: '先去看看' },
      )
    } catch {
      return
    }
  }
  await runGeneration(
    '故事设定生成',
    () => storyPathApi.bible.generate(
      projectId,
      { instructions: bibleInstructions.value || null },
      createIdempotencyKey('bible'),
    ),
    () => loadBible(true),
  )
}

async function generateOutline(): Promise<void> {
  if (!selectedPath.value || !pathEditable.value) return
  const count = outlineChapterCount.value
  if (!Number.isInteger(count) || count < 1 || count > 500) {
    ElMessage.warning('章节数必须是 1 到 500 的整数')
    return
  }
  const pathId = selectedPath.value.id
  try {
    // 有设定草稿 → 先物化，锚定生成（无草稿时不传锚点，行为与现状一致）。
    const bibleAnchor = bibleDraft.value ? await materializeBibleDraft() : null
    await runGeneration(
      '章节大纲生成',
      () => storyPathApi.outlines.generate(
        pathId,
        {
          chapter_count: count,
          instructions: outlineInstructions.value || null,
          bible_revision_id: bibleAnchor,
        },
        createIdempotencyKey(`outline:${pathId}`),
      ),
      async () => {
        if (selectedPathId.value !== pathId) return
        const [revisionsResponse, headResponse] = await Promise.all([
          storyPathApi.outlines.listRevisions(pathId),
          storyPathApi.outlines.getHead(pathId),
        ])
        if (selectedPathId.value !== pathId) return
        outlineRevisions.value = revisionsResponse.data
        outlineHead.value = headResponse.data
        selectedOutlineRevisionId.value = outlineRevisions.value[0]?.id || ''
        syncOutlineChapterCount()
        refreshOutlineSuggestion()
      },
    )
  } catch (error) {
    if (error instanceof TypeError || error instanceof SyntaxError) ElMessage.error(error.message)
    else reportError('materialize bible draft for outline', error, '设定草稿物化失败')
  }
}

async function generateChapter(): Promise<void> {
  const chapter = selectedChapter.value
  if (!chapter || !pathEditable.value) return
  const pathChapterId = chapter.id
  try {
    // 草稿物化锚点：bible/outline 必须成对出现（解析器契约）。
    let bibleAnchor: string | null = null
    let outlineAnchor: string | null = null
    if (bibleDraft.value || outlineDraft.value) {
      if (bibleDraft.value && !outlineDraft.value) {
        ElMessage.warning('设定有草稿时，大纲必须先重做（生成或采用进草稿）才能续写正文')
        return
      }
      bibleAnchor = bibleDraft.value
        ? await materializeBibleDraft()
        : bibleHead.value?.revision_id ?? null
      if (!bibleAnchor) {
        ElMessage.warning('尚无可用的故事设定版本')
        return
      }
      outlineAnchor = outlineDraft.value
        ? await materializeOutlineDraft(selectedPathId.value, bibleAnchor)
        : outlineHead.value?.revision_id ?? null
      if (!outlineAnchor) {
        ElMessage.warning('尚无可用的大纲版本')
        return
      }
    }
    // 前章草稿 → 物化后经 ancestor overrides 进续写上下文。
    const overrides: Record<string, string> = {}
    for (const previous of pathChapters.value) {
      if (previous.display_index >= chapter.display_index) continue
      if (!draftStore.readDraft('chapter', previous.id)) continue
      overrides[previous.id] = await materializeChapterDraft(previous.id)
    }
    await runGeneration(
      '章节正文生成',
      () => storyPathApi.chapters.generate(
        pathChapterId,
        {
          instructions: chapterInstructions.value || null,
          bible_revision_id: bibleAnchor,
          outline_revision_id: outlineAnchor,
          ancestor_revision_overrides: Object.keys(overrides).length > 0 ? overrides : null,
        },
        createIdempotencyKey(`chapter:${pathChapterId}`),
      ),
      async () => {
        if (selectedChapterId.value === pathChapterId) {
          await selectChapter(pathChapterId, false, true)
        }
      },
    )
  } catch (error) {
    if (error instanceof TypeError || error instanceof SyntaxError) ElMessage.error(error.message)
    else reportError('materialize drafts for chapter', error, '草稿物化失败')
  }
}

async function generateChapterBatch(): Promise<void> {
  if (!selectedPath.value || !pathEditable.value || selectedBatchIds.value.length === 0) return
  const pathId = selectedPath.value.id
  const ids = [...selectedBatchIds.value]
  try {
    // 草稿物化锚点：与单章同契约，bible/outline 成对（批量内部按 provisional 链自管前章）。
    let bibleAnchor: string | null = null
    let outlineAnchor: string | null = null
    if (bibleDraft.value || outlineDraft.value) {
      if (bibleDraft.value && !outlineDraft.value) {
        ElMessage.warning('设定有草稿时，大纲必须先重做（生成或采用进草稿）才能批量续写')
        return
      }
      bibleAnchor = bibleDraft.value
        ? await materializeBibleDraft()
        : bibleHead.value?.revision_id ?? null
      if (!bibleAnchor) {
        ElMessage.warning('尚无可用的故事设定版本')
        return
      }
      outlineAnchor = outlineDraft.value
        ? await materializeOutlineDraft(pathId, bibleAnchor)
        : outlineHead.value?.revision_id ?? null
      if (!outlineAnchor) {
        ElMessage.warning('尚无可用的大纲版本')
        return
      }
    }
    await runGeneration(
      '批量正文生成',
      () => storyPathApi.chapters.generateBatch(
        pathId,
        {
          path_chapter_ids: ids,
          instructions: chapterInstructions.value || null,
          bible_revision_id: bibleAnchor,
          outline_revision_id: outlineAnchor,
        },
        createIdempotencyKey(`chapter-batch:${pathId}`),
      ),
      async () => {
        if (selectedPathId.value !== pathId || !selectedChapterId.value) return
        await selectChapter(selectedChapterId.value, false, true)
      },
      2 * 60 * 60 * 1000,
    )
  } catch (error) {
    reportError('materialize chapter batch anchors', error, '物化草稿锚点失败')
  }
}

// ===== 章节语音（TTS）：后端 voice-lines 契约的入口，逐行渲染后由
// VN 图编译把 AudioUrl 织入播放器 =====
const voiceLines = ref<VoiceLineItem[]>([])
const voiceLinesRevisionId = ref('')
const voiceLinesLoading = ref(false)
const voiceRenderedCount = computed(() =>
  voiceLines.value.filter(line => Boolean(line.audio_asset_version_id)).length)

function resetVoiceLines(): void {
  voiceLines.value = []
  voiceLinesRevisionId.value = ''
}

async function loadVoiceLines(force = false): Promise<void> {
  const revisionId = selectedChapterRevisionId.value
  if (!revisionId) return
  if (!force && voiceLinesRevisionId.value === revisionId) return
  voiceLinesLoading.value = true
  try {
    const response = await storyPathApi.voiceLines.list(projectId, revisionId)
    if (selectedChapterRevisionId.value !== revisionId) return
    voiceLines.value = response.data.items
    voiceLinesRevisionId.value = revisionId
  } catch (error) {
    reportError('load voice lines', error, '语音清单加载失败')
  } finally {
    voiceLinesLoading.value = false
  }
}

function generateVoiceLines(): void {
  const revisionId = selectedChapterRevisionId.value
  if (!revisionId) {
    ElMessage.warning('该章节尚无正文版本，请先生成正文')
    return
  }
  void runGeneration(
    '章节语音生成',
    () => storyPathApi.voiceLines.generate(
      projectId,
      revisionId,
      createIdempotencyKey(`voice:${revisionId}`),
    ),
    async () => {
      resetVoiceLines()
      await loadVoiceLines(true)
      const total = voiceLines.value.length
      const done = voiceRenderedCount.value
      ElMessage.success(`语音生成完成：${done}/${total} 行已渲染`)
    },
  )
}

// 切章清空语音清单；进入脚本视图时懒加载当前正文修订的语音状态
watch(selectedChapterRevisionId, () => resetVoiceLines())
watch(chapterStage, (stage) => {
  if (stage === 'script' && selectedChapterRevisionId.value) {
    void loadVoiceLines()
  }
})

async function generateScript(): Promise<void> {
  if (!selectedChapter.value || !pathEditable.value) return
  const pathChapterId = selectedChapter.value.id
  try {
    // 有正文草稿 → 先物化，脚本生成锚定到物化修订；否则优先正文 head，
    // head 为空（新会话、修订未激活）时回退到当前选中的正文修订。
    let chapterRevisionId = selectedChapterRevisionId.value
    if (chapterDraft.value) {
      chapterRevisionId = await materializeChapterDraft(pathChapterId)
      const revisionsResponse = await storyPathApi.chapters.listRevisions(pathChapterId)
      chapterRevisions.value = revisionsResponse.data
      selectedChapterRevisionId.value = chapterRevisionId
      refreshChapterSuggestion()
    } else {
      chapterRevisionId = chapterHead.value?.revision_id || selectedChapterRevisionId.value
      if (!chapterRevisionId) {
        ElMessage.warning('该章节尚无正文版本，请先生成或编辑正文')
        return
      }
    }
    const targetRevisionId = chapterRevisionId
    let taskId = ''
    await runGeneration(
      '章节脚本生成',
      async () => {
        const response = await storyPathApi.scripts.generate(
          targetRevisionId,
          { instructions: scriptInstructions.value || null },
          createIdempotencyKey(`script:${targetRevisionId}`),
        )
        taskId = response.data.task_id
        return response
      },
      async () => {
        await mergeScriptWritebackIntoBibleDraft(taskId)
        if (selectedChapterId.value === pathChapterId) {
          await loadScriptFamily(targetRevisionId, true)
        }
      },
    )
  } catch (error) {
    if (error instanceof TypeError || error instanceof SyntaxError) ElMessage.error(error.message)
    else reportError('materialize chapter draft for script', error, '正文草稿物化失败')
  }
}

async function renderResources(role: ResourceRole): Promise<void> {
  if (!selectedScriptRevision.value || !pathEditable.value) return
  const revisionId = selectedScriptRevision.value.id
  await runGeneration(
    `${resourceRoleLabel(role)}生成`,
    () => storyPathApi.resources.render(
      revisionId,
      role,
      createIdempotencyKey(`resource:${revisionId}:${role}`),
    ),
    () => loadScriptArtifacts(revisionId, false),
  )
}

async function compileGraph(): Promise<void> {
  if (!selectedChapter.value || !pathEditable.value) return
  const pathChapterId = selectedChapter.value.id
  try {
    // 有脚本草稿 → 先物化，编译锚定到物化修订；否则优先脚本 head，
    // head 为空时回退到当前选中的脚本修订。
    let scriptRevisionId = selectedScriptRevisionId.value
    if (scriptDraft.value) {
      if (!selectedChapterRevisionId.value) {
        ElMessage.warning('缺少正文版本，无法物化脚本草稿')
        return
      }
      scriptRevisionId = await materializeScriptDraft(pathChapterId, selectedChapterRevisionId.value)
      const revisionsResponse = await storyPathApi.scripts.listRevisions(selectedChapterRevisionId.value)
      scriptRevisions.value = revisionsResponse.data
      selectedScriptRevisionId.value = scriptRevisionId
      refreshScriptSuggestion()
    } else {
      scriptRevisionId = scriptHead.value?.revision_id || selectedScriptRevisionId.value
      if (!scriptRevisionId) {
        ElMessage.warning('该正文版本尚无脚本，请先生成脚本')
        return
      }
    }
    const targetRevisionId = scriptRevisionId
    await runGeneration(
      'VNGraph 编译',
      () => storyPathApi.vnGraphs.compile(
        targetRevisionId,
        {
          schema_version: graphSchemaVersion.value || null,
          compiler_version: graphCompilerVersion.value || null,
        },
        createIdempotencyKey(`vn-graph:${targetRevisionId}`),
      ),
      () => loadScriptArtifacts(targetRevisionId, true),
    )
  } catch (error) {
    if (error instanceof TypeError || error instanceof SyntaxError) ElMessage.error(error.message)
    else reportError('materialize script draft for graph', error, '脚本草稿物化失败')
  }
}

async function generateCandidates(): Promise<void> {
  if (!canGenerateCandidates.value || !selectedPath.value || !selectedChapter.value) return
  const pathId = selectedPath.value.id
  const nodeId = checkpointId.value.trim()
  const stateSnapshotId = candidateStateSnapshotId.value.trim()
  syncCheckpointQuery()
  try {
    // 后端按"章节 head"校验候选来源修订；新会话里 head 可能为空，
    // 先把当前选中的正文修订激活为 head（幂等），再发起候选生成。
    const chapterRevisionId = chapterHead.value?.revision_id || selectedChapterRevisionId.value
    if (!chapterRevisionId) {
      ElMessage.warning('该章节尚无正文版本，请先生成正文')
      return
    }
    if (chapterHead.value?.revision_id !== chapterRevisionId) {
      const headResponse = await storyPathApi.chapters.updateHead(
        selectedChapter.value.id,
        chapterRevisionId,
        chapterHead.value?.lock_version ?? 1,
      )
      chapterHead.value = headResponse.data
      selectedChapter.value.current_revision_id = headResponse.data.revision_id
    }
    const targetRevisionId = chapterRevisionId
    await runGeneration(
      '分支候选生成',
      () => storyPathApi.candidates.generate(
        pathId,
        nodeId,
        {
          chapter_revision_id: targetRevisionId,
          state_snapshot_id: stateSnapshotId,
          candidate_count: candidateCount.value,
          instructions: candidateInstructions.value || null,
        },
        createIdempotencyKey(`candidates:${pathId}:${nodeId}`),
      ),
      async () => {
        if (selectedPathId.value === pathId && checkpointId.value.trim() === nodeId) {
          await loadCandidateFamily(true, true)
        }
      },
    )
  } catch (error) {
    reportError('activate chapter head for candidates', error, '章节版本激活失败，无法生成分支候选')
  }
}

async function promoteCandidate(candidate: BranchCandidate): Promise<void> {
  if (!selectedCandidateRevision.value || selectedCandidateRevision.value.id !== candidateHead.value?.revision_id) {
    return
  }
  let title = ''
  try {
    const response = await ElMessageBox.prompt(
      candidate.preview_text,
      '创建剧情路径',
      {
        confirmButtonText: '创建路径',
        cancelButtonText: '取消',
        inputPlaceholder: '路径名称（可选）',
        inputValue: candidate.preview_text.slice(0, 40),
        inputValidator: (value) => value.length <= 200 || '路径名称不能超过 200 个字符',
      },
    )
    title = response.value.trim()
  } catch {
    return
  }

  pathUpdating.value = true
  try {
    const response = await storyPathApi.candidates.promote(
      candidate.id,
      { title: title || null },
      createIdempotencyKey(`promote:${candidate.id}`),
    )
    await loadPaths()
    const childPath = paths.value.find((path) => path.id === response.data.id) ?? response.data
    checkpointId.value = ''
    candidateStateSnapshotId.value = ''
    clearCandidateFamily()
    await selectPath(childPath, true)
    activeArea.value = 'chapters'
    ElMessage.success('子路径已创建')
  } catch (error) {
    reportError('promote branch candidate', error, '剧情路径创建失败')
  } finally {
    pathUpdating.value = false
  }
}

async function openSiblingCheckpoint(sourcePath: StoryPath): Promise<void> {
  if (!sourcePath.parent_path_id || !sourcePath.fork_checkpoint_node_id) return
  const parentPath = paths.value.find((path) => path.id === sourcePath.parent_path_id)
  if (!parentPath) {
    ElMessage.error('父路径不可访问')
    return
  }
  await selectPath(parentPath, true, sourcePath.fork_path_chapter_id || '')
  checkpointId.value = sourcePath.fork_checkpoint_node_id
  activeArea.value = 'branches'
  await loadCandidateFamily(false, true)
}

async function appendChapter(): Promise<void> {
  if (!selectedPath.value || !pathEditable.value) return
  pathUpdating.value = true
  try {
    const tail = pathChapters.value.at(-1)
    const response = await storyPathApi.chapters.append(
      selectedPath.value.id,
      { after_path_chapter_id: tail?.id ?? null },
      selectedPath.value.lock_version,
    )
    await loadPaths()
    const updatedPath = paths.value.find((item) => item.id === selectedPathId.value)
    if (updatedPath) await selectPath(updatedPath, true, response.data.id)
    ElMessage.success('已追加路径章节')
  } catch (error) {
    reportError('append path chapter', error, '追加章节失败')
    await refreshPaths()
  } finally {
    pathUpdating.value = false
  }
}

async function togglePathStatus(path: StoryPath): Promise<void> {
  const nextStatus = path.status === 'active' ? 'archived' : 'active'
  try {
    await ElMessageBox.confirm(
      nextStatus === 'archived'
        ? `归档“${path.title}”及其后续公开范围？`
        : `恢复“${path.title}”到创作范围？`,
      nextStatus === 'archived' ? '归档剧情路径' : '恢复剧情路径',
      { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  pathUpdating.value = true
  try {
    await storyPathApi.storyPaths.update(path.id, { status: nextStatus }, path.lock_version)
    await loadPaths()
    ElMessage.success(nextStatus === 'archived' ? '路径已归档' : '路径已恢复')
  } catch (error) {
    reportError('update StoryPath', error, '路径状态更新失败')
    await loadPaths()
  } finally {
    pathUpdating.value = false
  }
}

function toggleAllChapters(checked: string | number | boolean): void {
  selectedBatchIds.value = checked ? pathChapters.value.map((item) => item.id) : []
}

function selectReviewRevision(revisionId: string): void {
  if (activeArea.value === 'bible') selectedBibleRevisionId.value = revisionId
  else if (activeArea.value === 'outline') selectedOutlineRevisionId.value = revisionId
  else if (activeArea.value === 'branches') {
    selectedCandidateRevisionId.value = revisionId
    void loadCandidateOptions(revisionId)
  }
  else if (chapterStage.value === 'chapter') {
    selectedChapterRevisionId.value = revisionId
    void loadScriptFamily(revisionId, false)
  } else if (chapterStage.value === 'script') {
    selectedScriptRevisionId.value = revisionId
    void loadScriptArtifacts(revisionId, false)
  } else selectedGraphRevisionId.value = revisionId
}

async function activateReviewRevision(): Promise<void> {
  const revisionId = reviewSelectedId.value
  if (!revisionId) return
  activatingRevision.value = true
  try {
    if (activeArea.value === 'bible' && bibleHead.value) {
      const response = await storyPathApi.bible.updateHead(projectId, revisionId, bibleHead.value.lock_version)
      bibleHead.value = response.data
    } else if (activeArea.value === 'outline' && selectedPath.value && outlineHead.value) {
      const pathId = selectedPath.value.id
      const response = await storyPathApi.outlines.updateHead(pathId, revisionId, outlineHead.value.lock_version)
      outlineHead.value = response.data
      await loadPaths()
      const updatedPath = paths.value.find((item) => item.id === pathId)
      if (updatedPath) await selectPath(updatedPath, true, selectedChapterId.value)
    } else if (activeArea.value === 'branches' && selectedPath.value && candidateHead.value) {
      const response = await storyPathApi.candidates.updateHead(
        selectedPath.value.id,
        checkpointId.value,
        revisionId,
        candidateHead.value.lock_version,
      )
      candidateHead.value = response.data
    } else if (chapterStage.value === 'chapter' && selectedChapter.value && chapterHead.value) {
      const response = await storyPathApi.chapters.updateHead(
        selectedChapter.value.id,
        revisionId,
        chapterHead.value.lock_version,
      )
      chapterHead.value = response.data
      selectedChapter.value.current_revision_id = response.data.revision_id
      await loadScriptFamily(revisionId, false)
    } else if (chapterStage.value === 'script' && selectedChapterRevision.value && scriptHead.value) {
      const response = await storyPathApi.scripts.updateHead(
        selectedChapterRevision.value.id,
        revisionId,
        scriptHead.value.lock_version,
      )
      scriptHead.value = response.data
    } else if (chapterStage.value === 'graph' && selectedScriptRevision.value && graphHead.value) {
      const response = await storyPathApi.vnGraphs.updateHead(
        selectedScriptRevision.value.id,
        revisionId,
        graphHead.value.lock_version,
      )
      graphHead.value = response.data
    }
    await loadProject()
    ElMessage.success('当前版本已更新')
  } catch (error) {
    reportError('activate revision Head', error, '版本激活失败')
    await refreshReview()
  } finally {
    activatingRevision.value = false
  }
}

async function refreshReview(): Promise<void> {
  try {
    if (activeArea.value === 'bible') await loadBible(false)
    else if (activeArea.value === 'outline' && selectedPath.value) {
      await selectPath(selectedPath.value, false, selectedChapterId.value)
    } else if (activeArea.value === 'branches') {
      await loadCandidateFamily(false, true)
    } else if (chapterStage.value === 'chapter' && selectedChapter.value) {
      await selectChapter(selectedChapter.value.id, false)
    } else if (chapterStage.value === 'script' && selectedChapterRevision.value) {
      await loadScriptFamily(selectedChapterRevision.value.id, false)
    } else if (chapterStage.value === 'graph' && selectedScriptRevision.value) {
      await loadScriptArtifacts(selectedScriptRevision.value.id, false)
    }
  } catch (error) {
    reportError('refresh revisions', error, '版本刷新失败')
  }
}

/* ------------------------------ 草稿：读取/重置 ------------------------------ */

function reloadBibleDraft(): void {
  draftSaver.cancel()
  bibleDraft.value = draftStore.readDraft<JsonObject>('bible', projectId)
  bibleDraftText.value = bibleDraft.value ? prettyJson(bibleDraft.value.payload) : ''
  bibleSaveState.value = 'clean'
}

function reloadOutlineDraft(): void {
  draftSaver.cancel()
  outlineDraft.value = selectedPathId.value
    ? draftStore.readDraft<OutlineDraftPayload>('outline', selectedPathId.value)
    : null
  outlineDraftText.value = outlineDraft.value
    ? prettyJson(outlineDraft.value.payload.chapters)
    : ''
  outlineSaveState.value = 'clean'
}

function reloadChapterDraft(): void {
  draftSaver.cancel()
  chapterDraft.value = selectedChapterId.value
    ? draftStore.readDraft<string>('chapter', selectedChapterId.value)
    : null
  chapterDraftText.value = chapterDraft.value?.payload ?? ''
  chapterSaveState.value = 'clean'
}

function reloadScriptDraft(): void {
  draftSaver.cancel()
  scriptDraft.value = selectedChapterId.value
    ? draftStore.readDraft<ScriptDraftPayload>('script', selectedChapterId.value)
    : null
  scriptSaveState.value = 'clean'
}

function reloadGraphDraft(): void {
  draftSaver.cancel()
  graphDraft.value = selectedChapterId.value
    ? draftStore.readDraft<GraphDraftPayload>('graph', selectedChapterId.value)
    : null
  graphDraftText.value = graphDraft.value ? prettyJson(graphDraft.value.payload.graph_json) : ''
  graphSaveState.value = 'clean'
}

/* ------------------------------ 草稿：建议（待选 AI 结果） ------------------------------ */

function refreshBibleSuggestion(): void {
  bibleSuggestion.value = findPendingSuggestion(bibleRevisions.value, {
    headRevisionId: bibleHead.value?.revision_id,
    baseRevisionId: bibleDraft.value?.baseRevisionId ?? null,
    dismissedRevisionId: draftStore.dismissedSuggestion(projectId, 'bible', projectId),
    minRevisionNo: headRevisionOf(bibleRevisions.value, bibleHead.value)?.revision_no ?? null,
  })
}

function refreshOutlineSuggestion(): void {
  outlineSuggestion.value = findPendingSuggestion(outlineRevisions.value, {
    headRevisionId: outlineHead.value?.revision_id,
    baseRevisionId: outlineDraft.value?.baseRevisionId ?? null,
    dismissedRevisionId: selectedPathId.value
      ? draftStore.dismissedSuggestion(projectId, 'outline', selectedPathId.value)
      : null,
    minRevisionNo: headRevisionOf(outlineRevisions.value, outlineHead.value)?.revision_no ?? null,
  })
}

function refreshChapterSuggestion(): void {
  chapterSuggestion.value = findPendingSuggestion(chapterRevisions.value, {
    headRevisionId: chapterHead.value?.revision_id,
    baseRevisionId: chapterDraft.value?.baseRevisionId ?? null,
    dismissedRevisionId: selectedChapterId.value
      ? draftStore.dismissedSuggestion(projectId, 'chapter', selectedChapterId.value)
      : null,
    minRevisionNo: headRevisionOf(chapterRevisions.value, chapterHead.value)?.revision_no ?? null,
  })
}

function refreshScriptSuggestion(): void {
  scriptSuggestion.value = findPendingSuggestion(scriptRevisions.value, {
    headRevisionId: scriptHead.value?.revision_id,
    baseRevisionId: scriptDraft.value?.baseRevisionId ?? null,
    dismissedRevisionId: selectedChapterId.value
      ? draftStore.dismissedSuggestion(projectId, 'script', selectedChapterId.value)
      : null,
    minRevisionNo: headRevisionOf(scriptRevisions.value, scriptHead.value)?.revision_no ?? null,
  })
}

function refreshGraphSuggestion(): void {
  // VNGraphRevision 无 revision_no 字段，以列表顺序（新→旧）映射展示序号。
  const total = graphRevisions.value.length
  const candidates = graphRevisions.value.map((revision, index) => ({
    ...revision,
    revision_no: total - index,
  }))
  const headRevisionNo = graphHead.value?.revision_id
    ? (candidates.find((revision) => revision.id === graphHead.value?.revision_id)?.revision_no ?? null)
    : null
  graphSuggestion.value = findPendingSuggestion(candidates, {
    headRevisionId: graphHead.value?.revision_id,
    baseRevisionId: graphDraft.value?.baseRevisionId ?? null,
    dismissedRevisionId: selectedChapterId.value
      ? draftStore.dismissedSuggestion(projectId, 'graph', selectedChapterId.value)
      : null,
    minRevisionNo: headRevisionNo,
  })
}

/* ------------------------------ 草稿：开始编辑（复制到草稿） ------------------------------ */

function startBibleDraft(): void {
  const headRevision = headRevisionOf(bibleRevisions.value, bibleHead.value)
  const sourceRevision = selectedBibleRevision.value ?? headRevision
  draftStore.writeDraft<JsonObject>('bible', projectId, {
    baseRevisionId: headRevision?.id ?? null,
    payload: (sourceRevision?.content_json ?? {}) as JsonObject,
    upstreamHashes: {},
  })
  reloadBibleDraft()
  refreshBibleSuggestion()
}

function startOutlineDraft(): void {
  if (!selectedPath.value) return
  const headRevision = headRevisionOf(outlineRevisions.value, outlineHead.value)
  const sourceRevision = selectedOutlineRevision.value ?? headRevision
  draftStore.writeDraft<OutlineDraftPayload>('outline', selectedPath.value.id, {
    baseRevisionId: headRevision?.id ?? null,
    payload: {
      bible_revision_id:
        sourceRevision?.bible_revision_id ?? bibleHead.value?.revision_id ?? null,
      chapters: ((sourceRevision?.chapters ?? []) as OutlineChapterInput[]).map(
        (chapter) => ({ ...chapter }),
      ),
    },
    upstreamHashes: buildUpstreamHashes({ bible: bibleEffectiveHash() }),
  })
  reloadOutlineDraft()
  refreshOutlineSuggestion()
}

function startChapterDraft(): void {
  if (!selectedChapter.value) return
  const headRevision = headRevisionOf(chapterRevisions.value, chapterHead.value)
  const sourceRevision = selectedChapterRevision.value ?? headRevision
  draftStore.writeDraft<string>('chapter', selectedChapter.value.id, {
    baseRevisionId: headRevision?.id ?? null,
    payload: sourceRevision?.content ?? '',
    upstreamHashes: buildUpstreamHashes({
      bible: bibleEffectiveHash(),
      outline: outlineEffectiveHash(),
    }),
  })
  reloadChapterDraft()
  refreshChapterSuggestion()
}

function startScriptDraft(): void {
  if (!selectedChapterId.value) return
  const headRevision = headRevisionOf(scriptRevisions.value, scriptHead.value)
  const sourceRevision = selectedScriptRevision.value ?? headRevision
  if (!sourceRevision) {
    ElMessage.warning('尚无可复制的脚本版本，请先生成脚本')
    return
  }
  draftStore.writeDraft<ScriptDraftPayload>('script', selectedChapterId.value, {
    baseRevisionId: headRevision?.id ?? sourceRevision.id,
    payload: {
      script_json: sourceRevision.script_json as JsonObject,
      source_chapter_revision_id: selectedChapterRevisionId.value || null,
    },
    upstreamHashes: buildUpstreamHashes({ chapter: chapterEffectiveHash() }),
  })
  reloadScriptDraft()
  refreshScriptSuggestion()
}

function startGraphDraft(): void {
  if (!selectedChapterId.value) return
  const headRevision = headRevisionOf(graphRevisions.value, graphHead.value)
  const sourceRevision = selectedGraphRevision.value ?? headRevision
  if (!sourceRevision) {
    ElMessage.warning('尚无可复制的 VNGraph 版本，请先编译')
    return
  }
  draftStore.writeDraft<GraphDraftPayload>('graph', selectedChapterId.value, {
    baseRevisionId: headRevision?.id ?? sourceRevision.id,
    payload: {
      graph_json: sourceRevision.graph_json as JsonObject,
      source_script_revision_id: selectedScriptRevisionId.value || null,
    },
    upstreamHashes: buildUpstreamHashes({ script: scriptEffectiveHash() }),
  })
  reloadGraphDraft()
  refreshGraphSuggestion()
}

/* ------------------------------ 草稿：编辑/保存/丢弃 ------------------------------ */

function onBibleDraftInput(): void {
  if (!bibleDraft.value) return
  bibleSaveState.value = 'dirty'
  draftSaver.schedule(() => saveBibleDraft(false))
}

function saveBibleDraft(explicit: boolean): boolean {
  if (!bibleDraft.value || bibleSaveState.value !== 'dirty') return bibleDraft.value !== null
  let parsed: unknown
  try {
    parsed = JSON.parse(bibleDraftText.value)
  } catch {
    if (explicit) ElMessage.error('故事设定必须是合法 JSON')
    return false
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    if (explicit) ElMessage.error('故事设定必须是 JSON 对象')
    return false
  }
  bibleSaveState.value = 'saving'
  draftStore.writeDraft<JsonObject>('bible', projectId, { payload: parsed as JsonObject })
  bibleDraft.value = draftStore.readDraft<JsonObject>('bible', projectId)
  bibleSaveState.value = 'clean'
  refreshBibleSuggestion()
  return true
}

function onOutlineDraftInput(): void {
  if (!outlineDraft.value || !selectedPathId.value) return
  const pathId = selectedPathId.value
  outlineSaveState.value = 'dirty'
  draftSaver.schedule(() => {
    if (selectedPathId.value === pathId) saveOutlineDraft(false)
  })
}

function saveOutlineDraft(explicit: boolean): boolean {
  if (!outlineDraft.value || !selectedPathId.value) return false
  if (outlineSaveState.value !== 'dirty') return true
  let parsed: unknown
  try {
    parsed = JSON.parse(outlineDraftText.value)
  } catch {
    if (explicit) ElMessage.error('章节大纲必须是合法 JSON')
    return false
  }
  if (!Array.isArray(parsed) || parsed.length === 0) {
    if (explicit) ElMessage.error('章节大纲必须是非空 JSON 数组')
    return false
  }
  outlineSaveState.value = 'saving'
  draftStore.writeDraft<OutlineDraftPayload>('outline', selectedPathId.value, {
    payload: {
      bible_revision_id: outlineDraft.value.payload.bible_revision_id,
      chapters: parsed as OutlineChapterInput[],
    },
  })
  outlineDraft.value = draftStore.readDraft<OutlineDraftPayload>('outline', selectedPathId.value)
  outlineSaveState.value = 'clean'
  refreshOutlineSuggestion()
  return true
}

function onChapterDraftInput(): void {
  if (!chapterDraft.value || !selectedChapterId.value) return
  const chapterId = selectedChapterId.value
  chapterSaveState.value = 'dirty'
  draftSaver.schedule(() => {
    if (selectedChapterId.value === chapterId) saveChapterDraft(false)
  })
}

function saveChapterDraft(explicit: boolean): boolean {
  if (!chapterDraft.value || !selectedChapterId.value) return false
  if (chapterSaveState.value !== 'dirty') return true
  if (!chapterDraftText.value.trim()) {
    if (explicit) ElMessage.error('章节正文不能为空')
    return false
  }
  chapterSaveState.value = 'saving'
  draftStore.writeDraft<string>('chapter', selectedChapterId.value, {
    payload: chapterDraftText.value,
  })
  chapterDraft.value = draftStore.readDraft<string>('chapter', selectedChapterId.value)
  chapterSaveState.value = 'clean'
  refreshChapterSuggestion()
  return true
}

function onScriptDraftEdit(): void {
  if (!scriptDraft.value || !selectedChapterId.value) return
  const chapterId = selectedChapterId.value
  scriptSaveState.value = 'dirty'
  draftSaver.schedule(() => {
    if (selectedChapterId.value === chapterId) saveScriptDraft()
  })
}

function saveScriptDraft(): boolean {
  if (!scriptDraft.value || !selectedChapterId.value) return false
  if (scriptSaveState.value !== 'dirty') return true
  scriptSaveState.value = 'saving'
  draftStore.writeDraft<ScriptDraftPayload>('script', selectedChapterId.value, {
    payload: scriptDraft.value.payload,
  })
  scriptDraft.value = draftStore.readDraft<ScriptDraftPayload>('script', selectedChapterId.value)
  scriptSaveState.value = 'clean'
  return true
}

function setScriptParagraphField(paragraph: JsonObject, field: string, value: string | boolean): void {
  paragraph[field] = value
  onScriptDraftEdit()
}

function onGraphDraftInput(): void {
  if (!graphDraft.value || !selectedChapterId.value) return
  const chapterId = selectedChapterId.value
  graphSaveState.value = 'dirty'
  draftSaver.schedule(() => {
    if (selectedChapterId.value === chapterId) saveGraphDraft(false)
  })
}

function saveGraphDraft(explicit: boolean): boolean {
  if (!graphDraft.value || !selectedChapterId.value) return false
  if (graphSaveState.value !== 'dirty') return true
  let parsed: unknown
  try {
    parsed = JSON.parse(graphDraftText.value)
  } catch {
    if (explicit) ElMessage.error('VNGraph 必须是合法 JSON')
    return false
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    if (explicit) ElMessage.error('VNGraph 必须是 JSON 对象')
    return false
  }
  graphSaveState.value = 'saving'
  draftStore.writeDraft<GraphDraftPayload>('graph', selectedChapterId.value, {
    payload: {
      graph_json: parsed as JsonObject,
      source_script_revision_id: graphDraft.value.payload.source_script_revision_id,
    },
  })
  graphDraft.value = draftStore.readDraft<GraphDraftPayload>('graph', selectedChapterId.value)
  graphSaveState.value = 'clean'
  return true
}

async function discardBibleDraft(): Promise<void> {
  try {
    await ElMessageBox.confirm('丢弃本地设定草稿？草稿不会进入版本库。', '丢弃草稿', {
      type: 'warning',
      confirmButtonText: '丢弃',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  draftStore.clearDraft('bible', projectId)
  reloadBibleDraft()
  refreshBibleSuggestion()
  ElMessage.success('设定草稿已丢弃')
}

async function discardOutlineDraft(): Promise<void> {
  if (!selectedPathId.value) return
  try {
    await ElMessageBox.confirm('丢弃本地大纲草稿？草稿不会进入版本库。', '丢弃草稿', {
      type: 'warning',
      confirmButtonText: '丢弃',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  draftStore.clearDraft('outline', selectedPathId.value)
  reloadOutlineDraft()
  refreshOutlineSuggestion()
  ElMessage.success('大纲草稿已丢弃')
}

async function discardChapterDraft(): Promise<void> {
  if (!selectedChapterId.value) return
  try {
    await ElMessageBox.confirm('丢弃本地正文草稿？草稿不会进入版本库。', '丢弃草稿', {
      type: 'warning',
      confirmButtonText: '丢弃',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  draftStore.clearDraft('chapter', selectedChapterId.value)
  reloadChapterDraft()
  refreshChapterSuggestion()
  ElMessage.success('正文草稿已丢弃')
}

async function discardScriptDraft(): Promise<void> {
  if (!selectedChapterId.value) return
  try {
    await ElMessageBox.confirm('丢弃本地脚本草稿？草稿不会进入版本库。', '丢弃草稿', {
      type: 'warning',
      confirmButtonText: '丢弃',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  draftStore.clearDraft('script', selectedChapterId.value)
  reloadScriptDraft()
  refreshScriptSuggestion()
  ElMessage.success('脚本草稿已丢弃')
}

async function discardGraphDraft(): Promise<void> {
  if (!selectedChapterId.value) return
  try {
    await ElMessageBox.confirm('丢弃本地 VNGraph 草稿？草稿不会进入版本库。', '丢弃草稿', {
      type: 'warning',
      confirmButtonText: '丢弃',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  draftStore.clearDraft('graph', selectedChapterId.value)
  reloadGraphDraft()
  refreshGraphSuggestion()
  ElMessage.success('VNGraph 草稿已丢弃')
}

/* ------------------------------ 草稿：建议 采用/丢弃/预览 ------------------------------ */

function adoptBibleSuggestion(): void {
  const suggestion = bibleSuggestion.value
  if (!suggestion) return
  draftSaver.cancel()
  draftStore.writeDraft<JsonObject>('bible', projectId, {
    baseRevisionId: suggestion.id,
    payload: suggestion.content_json as JsonObject,
    upstreamHashes: {},
  })
  reloadBibleDraft()
  refreshBibleSuggestion()
  ElMessage.success(`已采用设定 r${suggestion.revision_no} 进草稿，可继续编辑`)
}

function dismissBibleSuggestion(): void {
  const suggestion = bibleSuggestion.value
  if (!suggestion) return
  draftStore.dismissSuggestion(projectId, 'bible', projectId, suggestion.id)
  refreshBibleSuggestion()
}

function previewBibleSuggestion(): void {
  const suggestion = bibleSuggestion.value
  if (!suggestion) return
  suggestionPreview.value = {
    title: `故事设定建议 r${suggestion.revision_no}`,
    body: prettyJson(suggestion.content_json),
    bibleContent: suggestion.content_json,
  }
}

function adoptOutlineSuggestion(): void {
  const suggestion = outlineSuggestion.value
  if (!suggestion || !selectedPathId.value) return
  draftSaver.cancel()
  draftStore.writeDraft<OutlineDraftPayload>('outline', selectedPathId.value, {
    baseRevisionId: suggestion.id,
    payload: {
      bible_revision_id: suggestion.bible_revision_id,
      chapters: suggestion.chapters.map((chapter) => ({ ...chapter })),
    },
    upstreamHashes: buildUpstreamHashes({ bible: bibleEffectiveHash() }),
  })
  reloadOutlineDraft()
  refreshOutlineSuggestion()
  ElMessage.success(`已采用大纲 r${suggestion.revision_no} 进草稿，可继续编辑`)
}

function dismissOutlineSuggestion(): void {
  const suggestion = outlineSuggestion.value
  if (!suggestion || !selectedPathId.value) return
  draftStore.dismissSuggestion(projectId, 'outline', selectedPathId.value, suggestion.id)
  refreshOutlineSuggestion()
}

function previewOutlineSuggestion(): void {
  const suggestion = outlineSuggestion.value
  if (!suggestion) return
  suggestionPreview.value = {
    title: `章节大纲建议 r${suggestion.revision_no}`,
    body: prettyJson(suggestion.chapters),
  }
}

function adoptChapterSuggestion(): void {
  const suggestion = chapterSuggestion.value
  if (!suggestion || !selectedChapterId.value) return
  draftSaver.cancel()
  draftStore.writeDraft<string>('chapter', selectedChapterId.value, {
    baseRevisionId: suggestion.id,
    payload: suggestion.content,
    upstreamHashes: buildUpstreamHashes({
      bible: bibleEffectiveHash(),
      outline: outlineEffectiveHash(),
    }),
  })
  reloadChapterDraft()
  refreshChapterSuggestion()
  ElMessage.success(`已采用正文 r${suggestion.revision_no} 进草稿，可继续编辑`)
}

function dismissChapterSuggestion(): void {
  const suggestion = chapterSuggestion.value
  if (!suggestion || !selectedChapterId.value) return
  draftStore.dismissSuggestion(projectId, 'chapter', selectedChapterId.value, suggestion.id)
  refreshChapterSuggestion()
}

function previewChapterSuggestion(): void {
  const suggestion = chapterSuggestion.value
  if (!suggestion) return
  suggestionPreview.value = {
    title: `章节正文建议 r${suggestion.revision_no}`,
    body: suggestion.content,
  }
}

function adoptScriptSuggestion(): void {
  const suggestion = scriptSuggestion.value
  if (!suggestion || !selectedChapterId.value) return
  draftSaver.cancel()
  draftStore.writeDraft<ScriptDraftPayload>('script', selectedChapterId.value, {
    baseRevisionId: suggestion.id,
    payload: {
      script_json: suggestion.script_json as JsonObject,
      source_chapter_revision_id: selectedChapterRevisionId.value || null,
    },
    upstreamHashes: buildUpstreamHashes({ chapter: chapterEffectiveHash() }),
  })
  reloadScriptDraft()
  refreshScriptSuggestion()
  ElMessage.success(`已采用脚本 r${suggestion.revision_no} 进草稿，可调整演出字段`)
}

function dismissScriptSuggestion(): void {
  const suggestion = scriptSuggestion.value
  if (!suggestion || !selectedChapterId.value) return
  draftStore.dismissSuggestion(projectId, 'script', selectedChapterId.value, suggestion.id)
  refreshScriptSuggestion()
}

function previewScriptSuggestion(): void {
  const suggestion = scriptSuggestion.value
  if (!suggestion) return
  suggestionPreview.value = {
    title: `章节脚本建议 r${suggestion.revision_no}`,
    body: prettyJson(suggestion.script_json),
  }
}

function adoptGraphSuggestion(): void {
  const suggestion = graphSuggestion.value
  if (!suggestion || !selectedChapterId.value) return
  draftSaver.cancel()
  draftStore.writeDraft<GraphDraftPayload>('graph', selectedChapterId.value, {
    baseRevisionId: suggestion.id,
    payload: {
      graph_json: suggestion.graph_json as JsonObject,
      source_script_revision_id: selectedScriptRevisionId.value || null,
    },
    upstreamHashes: buildUpstreamHashes({ script: scriptEffectiveHash() }),
  })
  reloadGraphDraft()
  refreshGraphSuggestion()
  ElMessage.success('已采用 VNGraph 建议进草稿')
}

function dismissGraphSuggestion(): void {
  const suggestion = graphSuggestion.value
  if (!suggestion || !selectedChapterId.value) return
  draftStore.dismissSuggestion(projectId, 'graph', selectedChapterId.value, suggestion.id)
  refreshGraphSuggestion()
}

function previewGraphSuggestion(): void {
  const suggestion = graphSuggestion.value
  if (!suggestion) return
  suggestionPreview.value = {
    title: 'VNGraph 建议预览',
    body: prettyJson(suggestion.graph_json),
  }
}

function closeSuggestionPreview(): void {
  suggestionPreview.value = null
}

/* ------------------------------ 草稿：物化（草稿 → 未激活修订） ------------------------------ */

async function materializeBibleDraft(): Promise<string> {
  if (bibleDraft.value && bibleSaveState.value === 'dirty' && !saveBibleDraft(true)) {
    throw new TypeError('故事设定草稿不是合法 JSON，无法物化')
  }
  const draft = draftStore.readDraft<JsonObject>('bible', projectId)
  if (!draft) throw new TypeError('故事设定草稿不存在')
  const response = await storyPathApi.bible.createRevision(projectId, {
    parent_revision_id: draft.baseRevisionId,
    content_json: draft.payload,
  })
  return response.data.id
}

async function materializeOutlineDraft(pathId: string, bibleRevisionId?: string): Promise<string> {
  if (selectedPathId.value === pathId && outlineDraft.value
    && outlineSaveState.value === 'dirty' && !saveOutlineDraft(true)) {
    throw new TypeError('大纲草稿不是合法 JSON，无法物化')
  }
  const draft = draftStore.readDraft<OutlineDraftPayload>('outline', pathId)
  if (!draft) throw new TypeError('大纲草稿不存在')
  const anchor = bibleRevisionId
    ?? draft.payload.bible_revision_id
    ?? bibleHead.value?.revision_id
  if (!anchor) throw new TypeError('大纲缺少可用的设定锚点')
  // AI 大纲条目与现有 PathChapter 互不绑定（落章推迟到发布激活），而正文生成的
  // 解析器要求大纲包含目标章——物化时按 display_index 与现有章节配对回填绑定。
  const chaptersByIndex = new Map(
    pathChapters.value.map((chapter) => [chapter.display_index, chapter.id]),
  )
  const chapters = draft.payload.chapters.map((chapter) => ({
    ...chapter,
    story_path_chapter_id:
      chapter.story_path_chapter_id ?? chaptersByIndex.get(chapter.display_index) ?? null,
  }))
  const response = await storyPathApi.outlines.createRevision(pathId, {
    bible_revision_id: anchor,
    parent_revision_id: draft.baseRevisionId,
    chapters,
  })
  return response.data.id
}

async function materializeChapterDraft(pathChapterId: string): Promise<string> {
  if (selectedChapterId.value === pathChapterId && chapterDraft.value
    && chapterSaveState.value === 'dirty' && !saveChapterDraft(true)) {
    throw new TypeError('正文草稿尚未完成，无法物化')
  }
  const draft = draftStore.readDraft<string>('chapter', pathChapterId)
  if (!draft || !draft.payload.trim()) throw new TypeError('正文草稿为空')
  const response = await storyPathApi.chapters.createRevision(pathChapterId, {
    parent_revision_id: draft.baseRevisionId,
    content: draft.payload.trim(),
  })
  return response.data.id
}

async function materializeScriptDraft(
  pathChapterId: string,
  chapterRevisionId: string,
): Promise<string> {
  if (selectedChapterId.value === pathChapterId && scriptDraft.value) saveScriptDraft()
  const draft = draftStore.readDraft<ScriptDraftPayload>('script', pathChapterId)
  if (!draft) throw new TypeError('脚本草稿不存在')
  // parent 必须与目标正文同 family：跨 family 时置空从头建链。
  const parentId = draft.payload.source_chapter_revision_id === chapterRevisionId
    ? draft.baseRevisionId
    : null
  const response = await storyPathApi.scripts.createRevision(chapterRevisionId, {
    script_json: draft.payload.script_json,
    parent_revision_id: parentId,
  })
  return response.data.id
}

async function materializeGraphDraft(
  pathChapterId: string,
  scriptRevisionId: string,
): Promise<string> {
  if (selectedChapterId.value === pathChapterId && graphDraft.value) saveGraphDraft(true)
  const draft = draftStore.readDraft<GraphDraftPayload>('graph', pathChapterId)
  if (!draft) throw new TypeError('VNGraph 草稿不存在')
  const parentId = draft.payload.source_script_revision_id === scriptRevisionId
    ? draft.baseRevisionId
    : null
  const response = await storyPathApi.vnGraphs.createRevision(scriptRevisionId, {
    graph_json: draft.payload.graph_json,
    parent_revision_id: parentId,
  })
  return response.data.id
}

/* ------------------------------ 脚本生成 writeback 合并 ------------------------------
 * 脚本生成会把新说话人占位角色补录进 bible（并激活新 bible 修订）。若本地有设定
 * 草稿，必须按 name 合并这些角色，否则物化发布设定草稿会覆盖增量角色。 */

async function mergeScriptWritebackIntoBibleDraft(taskId: string): Promise<void> {
  try {
    const response = await api.get<TaskEventLogEntry[]>(`/tasks/${taskId}/event-log`)
    const names = new Set<string>()
    for (const entry of response.data) {
      if (entry.event_type !== 'bible.characters_augmented') continue
      const appended = entry.payload?.appended_character_names
      if (!Array.isArray(appended)) continue
      for (const name of appended) {
        if (typeof name === 'string' && name.trim()) names.add(name.trim())
      }
    }
    await loadBible(true)
    if (names.size === 0 || !bibleDraft.value) return
    const headRevision = headRevisionOf(bibleRevisions.value, bibleHead.value)
    const headCharacters = Array.isArray(
      (headRevision?.content_json as JsonObject | undefined)?.characters,
    )
      ? ((headRevision?.content_json as JsonObject).characters as JsonObject[])
      : []
    const payload = (bibleDraft.value.payload ?? {}) as JsonObject
    const draftCharacters = Array.isArray(payload.characters)
      ? [...(payload.characters as JsonObject[])]
      : []
    const knownNames = new Set(draftCharacters.map((item) => String(item?.name ?? '')))
    let changed = false
    for (const name of names) {
      if (knownNames.has(name)) continue
      const source = headCharacters.find((item) => String(item?.name ?? '') === name)
      if (!source) continue
      draftCharacters.push(source)
      knownNames.add(name)
      changed = true
    }
    if (!changed) return
    draftStore.writeDraft<JsonObject>('bible', projectId, {
      payload: { ...payload, characters: draftCharacters },
    })
    reloadBibleDraft()
    refreshBibleSuggestion()
    ElMessage.success(`新增配角已合并进设定草稿：${[...names].join('、')}`)
  } catch (error) {
    console.warn('merge script writeback into bible draft failed', error)
  }
}

/* ------------------------------ 固化发布（确认发布成书） ------------------------------ */

function collectDraftSubjects(): {
  hasBibleDraft: boolean
  outline: string[]
  chapter: string[]
  script: string[]
  graph: string[]
} {
  const subjects = {
    hasBibleDraft: draftStore.readDraft('bible', projectId) !== null,
    outline: [] as string[],
    chapter: [] as string[],
    script: [] as string[],
    graph: [] as string[],
  }
  if (typeof window === 'undefined') return subjects
  const prefix = 'if-line:draft:v1:'
  for (let index = 0; index < window.localStorage.length; index += 1) {
    const key = window.localStorage.key(index)
    if (!key || !key.startsWith(prefix)) continue
    const parts = key.slice(prefix.length).split(':')
    const kind = parts[0]
    const subjectId = parts.slice(1).join(':')
    if (!subjectId) continue
    if (kind === 'bible' && subjectId === String(projectId)) subjects.hasBibleDraft = true
    else if (kind === 'outline' || kind === 'chapter' || kind === 'script' || kind === 'graph') {
      subjects[kind].push(subjectId)
    }
  }
  return subjects
}

async function publishDraftsAsBook(): Promise<void> {
  if (!project.value) return
  if (publishingDrafts.value) return
  if (activeTaskBusy.value) {
    ElMessage.warning('后台任务还在收尾，请等任务状态刷新后再发布')
    return
  }
  const subjects = collectDraftSubjects()
  const hasAnyDraft = subjects.hasBibleDraft
    || subjects.outline.length > 0
    || subjects.chapter.length > 0
    || subjects.script.length > 0
    || subjects.graph.length > 0
  let releaseNotes = ''
  try {
    const response = await ElMessageBox.prompt(
      hasAnyDraft
        ? '将物化全部本地草稿并原子发布为同项目的新书版本（release）。发布前会做全链就绪校验，失败时草稿保留。'
        : '当前没有本地草稿，将按服务端当前版本（已激活版本或最新可用修订）原子发布为同项目的新书版本。发布前会做全链就绪校验。',
      '确认发布成书',
      {
        confirmButtonText: '物化并发布',
        cancelButtonText: '取消',
        inputPlaceholder: '版本说明（可选）',
        inputValidator: (value) => value.length <= 2000 || '版本说明不能超过 2000 个字符',
      },
    )
    releaseNotes = (response.value || '').trim()
  } catch {
    return
  }

  publishingDrafts.value = true
  try {
    const pathIdSet = new Set(paths.value.map((path) => path.id))
    const chaptersByPath = await Promise.all(
      paths.value.map((path) => storyPathApi.chapters.list(path.id)),
    )
    const allChapters = chaptersByPath.flatMap((item) => item.data)
    const chapterIdSet = new Set(allChapters.map((chapter) => chapter.id))
    const chapterById = new Map(allChapters.map((chapter) => [chapter.id, chapter]))

    const outlinePathIds = subjects.outline.filter((pathId) => pathIdSet.has(pathId))
    const chapterIds = subjects.chapter.filter((id) => chapterIdSet.has(id))
    const scriptIds = subjects.script.filter((id) => chapterIdSet.has(id))
    const graphIds = subjects.graph.filter((id) => chapterIdSet.has(id))

    if (!subjects.hasBibleDraft && outlinePathIds.length === 0 && chapterIds.length === 0
      && scriptIds.length === 0 && graphIds.length === 0) {
      // 无本地草稿（换会话/换设备）：走服务端空 anchors 回退——后端按
      // head/最新可选修订自动收集并原子激活发布，UI 不再被草稿死锁。
      const result = await storyPathApi.releases.finalizePublish(
        projectId,
        { release_notes: releaseNotes || null },
        createIdempotencyKey(`finalize:${projectId}`),
      )
      ElMessage.success(`已发布新书版本 v${result.data.version}`)
      await refreshWorkspace()
      return
    }

    const anchors: FinalizePublishRequest = {}
    if (subjects.hasBibleDraft) {
      anchors.bible_revision_id = await materializeBibleDraft()
    }
    if (outlinePathIds.length > 0) {
      anchors.outline_revision_ids = {}
      for (const pathId of outlinePathIds) {
        anchors.outline_revision_ids[pathId] = await materializeOutlineDraft(
          pathId,
          anchors.bible_revision_id ?? undefined,
        )
      }
    }
    if (chapterIds.length > 0) {
      anchors.chapter_revision_ids = {}
      for (const chapterId of chapterIds) {
        anchors.chapter_revision_ids[chapterId] = await materializeChapterDraft(chapterId)
      }
    }
    if (scriptIds.length > 0) {
      anchors.script_revision_ids = {}
      for (const chapterId of scriptIds) {
        const chapterRevisionId = anchors.chapter_revision_ids?.[chapterId]
          ?? chapterById.get(chapterId)?.current_revision_id
          ?? null
        if (!chapterRevisionId) {
          throw new TypeError('存在脚本草稿的章节尚无正文版本，请先生成正文')
        }
        anchors.script_revision_ids[chapterId] = await materializeScriptDraft(
          chapterId,
          chapterRevisionId,
        )
      }
    }
    if (graphIds.length > 0) {
      anchors.graph_revision_ids = {}
      for (const chapterId of graphIds) {
        const chapterRevisionId = anchors.chapter_revision_ids?.[chapterId]
          ?? chapterById.get(chapterId)?.current_revision_id
          ?? null
        if (!chapterRevisionId) {
          throw new TypeError('存在图草稿的章节尚无正文版本，请先生成正文')
        }
        let scriptRevisionId = anchors.script_revision_ids?.[chapterId] ?? null
        if (!scriptRevisionId) {
          const headResponse = await storyPathApi.scripts.getHead(chapterRevisionId)
          scriptRevisionId = headResponse.data.revision_id
        }
        if (!scriptRevisionId) {
          throw new TypeError('存在图草稿的章节尚无脚本版本，请先生成脚本')
        }
        anchors.graph_revision_ids[chapterId] = await materializeGraphDraft(
          chapterId,
          scriptRevisionId,
        )
      }
    }

    const result = await storyPathApi.releases.finalizePublish(
      projectId,
      { ...anchors, release_notes: releaseNotes || null },
      createIdempotencyKey(`finalize:${projectId}`),
    )

    draftStore.clearDraft('bible', projectId)
    for (const pathId of outlinePathIds) draftStore.clearDraft('outline', pathId)
    for (const chapterId of [...chapterIds, ...scriptIds, ...graphIds]) {
      draftStore.clearDraft('chapter', chapterId)
      draftStore.clearDraft('script', chapterId)
      draftStore.clearDraft('graph', chapterId)
    }
    ElMessage.success(`已发布新书版本 v${result.data.version}，本地草稿已清空`)
    await refreshWorkspace()
  } catch (error) {
    const details = (error as { response?: { data?: { error?: { details?: { blocking_items?: Array<{ code?: string; detail?: string }> } } } } })
      ?.response?.data?.error?.details
    if (details && Array.isArray(details.blocking_items) && details.blocking_items.length > 0) {
      const lines = details.blocking_items
        .map((item) => item.detail || item.code || '未知阻塞项')
      await ElMessageBox.alert(
        `发布被就绪校验阻塞，草稿已保留。请按提示重做下游环节后重试：\n${lines.join('\n')}`,
        '发布阻塞',
        { type: 'warning', confirmButtonText: '知道了' },
      ).catch(() => {})
    } else if (error instanceof TypeError || error instanceof SyntaxError) {
      ElMessage.error(error.message)
    } else {
      reportError('finalize publish drafts', error, '发布失败，草稿已保留')
    }
  } finally {
    publishingDrafts.value = false
  }
}

watch(
  () => route.query.path,
  (pathId) => {
    const id = queryString(pathId)
    if (!id || id === selectedPathId.value) return
    const path = paths.value.find((item) => item.id === id)
    if (path) void selectPath(path, false, queryString(route.query.chapter))
  },
)

watch(
  () => route.query.chapter,
  (chapterId) => {
    const id = queryString(chapterId)
    if (!id || id === selectedChapterId.value) return
    if (pathChapters.value.some((item) => item.id === id)) void selectChapter(id, false)
  },
)

watch(
  () => route.query.publication,
  (value) => {
    publicationPanelVisible.value = queryString(value) === '1'
  },
)

onMounted(refreshWorkspace)
</script>

<style scoped>
.authoring-page {
  min-height: calc(100vh - 56px);
  background: #eef1f4;
  color: #242b33;
}

.workspace-header {
  display: flex;
  min-height: 76px;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 12px 20px;
  border-bottom: 1px solid #d8dde3;
  background: #fff;
}

.header-primary,
.header-actions,
.toolbar-actions,
.generation-bar,
.resource-actions,
.compile-options {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-primary {
  min-width: 0;
  flex-wrap: wrap;
}

.project-heading {
  min-width: 0;
  margin-right: 8px;
}

.project-kicker,
.section-kicker {
  display: block;
  margin-bottom: 2px;
  color: #7b8490;
  font-size: 11px;
}

.project-heading h1 {
  max-width: 520px;
  margin: 0;
  overflow: hidden;
  color: #1f252c;
  font-size: 20px;
  font-weight: 680;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-actions {
  flex: 0 0 auto;
}

.header-metric {
  display: flex;
  align-items: baseline;
  gap: 6px;
  color: #727b86;
  font-size: 12px;
}

.header-metric strong {
  color: #26313c;
  font-size: 16px;
}

.task-strip {
  display: grid;
  grid-template-columns: minmax(180px, 300px) minmax(160px, 1fr) auto auto;
  min-height: 38px;
  align-items: center;
  gap: 12px;
  padding: 6px 20px;
  border-bottom: 1px solid #bed0df;
  background: #e8f2fa;
}

.task-succeeded {
  border-bottom-color: #b9d8c5;
  background: #edf7f1;
}

.task-failed,
.task-cancelled {
  border-bottom-color: #ecc4c4;
  background: #fbefef;
}

.task-copy {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 7px;
  font-size: 12px;
}

.task-copy span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-copy code,
.document-meta code {
  color: #6f7883;
  font-size: 11px;
}

.task-status {
  color: #44515d;
  font-size: 12px;
  white-space: nowrap;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.fatal-state {
  min-height: 520px;
  padding-top: 70px;
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(220px, 260px) minmax(480px, 1fr) minmax(240px, 290px);
  min-height: calc(100vh - 132px);
  background: #fff;
}

.artifact-workspace {
  min-width: 0;
  min-height: 0;
  background: #fff;
}

.artifact-tabs {
  height: auto;
}

.artifact-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: 0 18px;
  border-bottom: 1px solid #dfe3e8;
}

.artifact-tabs :deep(.el-tabs__nav-wrap::after) {
  display: none;
}

.artifact-tabs :deep(.el-tabs__content) {
  display: none;
}

.main-loading {
  padding: 28px;
}

.artifact-section {
  min-height: calc(100vh - 187px);
  padding: 0 22px 28px;
}

.section-toolbar {
  display: flex;
  min-height: 76px;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin: 0 -22px 20px;
  padding: 12px 22px;
  border-bottom: 1px solid #edf0f2;
}

.section-toolbar h2,
.chapter-detail-header h3,
.subsection-header h4 {
  margin: 0;
  color: #222930;
  font-weight: 650;
}

.section-toolbar h2 {
  font-size: 17px;
}

.toolbar-actions {
  min-width: 0;
  justify-content: flex-end;
}

.instruction-input {
  width: min(320px, 30vw);
}

.document-preview,
.outline-preview,
.prose-preview,
.json-preview {
  border: 1px solid #dfe3e8;
  border-radius: 6px;
  background: #fbfcfd;
}

.document-meta {
  display: flex;
  min-height: 38px;
  align-items: center;
  gap: 12px;
  padding: 7px 12px;
  border-bottom: 1px solid #e4e8ec;
  color: #68727d;
  font-size: 12px;
}

.document-preview pre,
.json-preview {
  margin: 0;
  overflow: auto;
  color: #2d3740;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.document-preview pre {
  max-height: calc(100vh - 300px);
  padding: 18px;
}

.outline-list {
  display: flex;
  flex-direction: column;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}

.outline-list li {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  gap: 12px;
  padding: 16px 14px;
  border-bottom: 1px solid #e7eaee;
}

.outline-list li:last-child {
  border-bottom: 0;
}

.outline-index {
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  border: 1px solid #bdc8d2;
  border-radius: 50%;
  color: #536271;
  font-size: 12px;
  font-weight: 650;
}

.outline-copy {
  min-width: 0;
}

.outline-copy h3 {
  margin: 2px 0 6px;
  font-size: 14px;
}

.outline-copy p {
  margin: 0;
  color: #59636e;
  font-size: 13px;
  line-height: 1.65;
  overflow-wrap: anywhere;
}

.outline-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 9px;
}

.chapter-section {
  padding-bottom: 0;
}

.chapter-workbench {
  display: grid;
  grid-template-columns: minmax(190px, 230px) minmax(0, 1fr);
  min-height: calc(100vh - 284px);
  margin: 0 -22px;
  border-top: 1px solid #dfe3e8;
}

.chapter-order {
  min-width: 0;
  border-right: 1px solid #dfe3e8;
  background: #f8f9fa;
}

.chapter-order-tools {
  display: flex;
  min-height: 42px;
  align-items: center;
  justify-content: space-between;
  padding: 7px 12px;
  border-bottom: 1px solid #dfe3e8;
  color: #77818c;
  font-size: 11px;
}

.chapter-list {
  max-height: calc(100vh - 326px);
  overflow: auto;
  padding: 7px;
}

.chapter-row {
  display: flex;
  align-items: center;
  border: 1px solid transparent;
  border-radius: 5px;
}

.chapter-row:hover {
  background: #edf1f4;
}

.chapter-row.selected {
  border-color: #9dbbd4;
  background: #e8f1f8;
}

.chapter-row > .el-checkbox-group {
  flex: 0 0 auto;
  padding-left: 8px;
}

.chapter-row > button {
  display: grid;
  min-width: 0;
  min-height: 44px;
  flex: 1;
  grid-template-columns: 24px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 6px;
  padding: 6px 8px 6px 2px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font: inherit;
  text-align: left;
}

.chapter-number {
  color: #6e7883;
  font-size: 11px;
  font-weight: 650;
  text-align: center;
}

.chapter-title {
  min-width: 0;
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chapter-ready {
  color: #2e8b57;
}

.chapter-detail {
  min-width: 0;
  padding: 0 18px 22px;
}

.chapter-detail-header,
.subsection-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}

.chapter-detail-header {
  min-height: 68px;
  border-bottom: 1px solid #edf0f2;
}

.chapter-detail-header h3 {
  max-width: 420px;
  overflow: hidden;
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.generation-bar {
  min-height: 62px;
  padding: 10px 0;
}

.generation-bar .el-input {
  min-width: 140px;
  flex: 1;
}

.prose-preview {
  max-height: calc(100vh - 412px);
  min-height: 260px;
  overflow: auto;
  padding: 22px;
  color: #303841;
  font-size: 14px;
  line-height: 1.9;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.json-preview {
  max-height: 360px;
  padding: 16px;
}

.resource-section {
  margin-top: 18px;
  border-top: 1px solid #dfe3e8;
  padding-top: 14px;
}

.subsection-header {
  margin-bottom: 10px;
}

.subsection-header h4 {
  font-size: 14px;
}

.compile-options {
  min-width: 260px;
  flex: 1;
}

.graph-preview {
  max-height: calc(100vh - 410px);
}

.graph-view-toggle {
  margin-left: auto;
}

.graph-player-wrap {
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  overflow: hidden;
}

.branch-actions {
  flex: 1;
}

.branch-actions > .el-input {
  min-width: 220px;
  flex: 1;
}

.branch-generation-bar {
  display: grid;
  grid-template-columns: auto minmax(180px, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid #dfe3e8;
  border-radius: 6px;
  background: #f8fafb;
}

.branch-source {
  display: grid;
  grid-template-columns: auto auto;
  gap: 3px 8px;
  color: #7a8490;
  font-size: 11px;
}

.branch-source strong {
  max-width: 120px;
  overflow: hidden;
  color: #35404a;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.candidate-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 16px;
}

.candidate-item {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) auto;
  align-items: start;
  gap: 12px;
  padding: 14px;
  border: 1px solid #dfe3e8;
  border-radius: 6px;
  background: #fff;
}

.candidate-key {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border-radius: 50%;
  background: #e6f0f7;
  color: #245d86;
  font-size: 12px;
  font-weight: 700;
}

.candidate-copy {
  min-width: 0;
}

.candidate-copy p {
  margin: 2px 0 0;
  color: #303943;
  font-size: 13px;
  line-height: 1.65;
  overflow-wrap: anywhere;
}

.candidate-copy pre {
  max-height: 140px;
  margin: 10px 0 0;
  overflow: auto;
  padding: 9px;
  border-radius: 4px;
  background: #f4f6f8;
  color: #59636e;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 11px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.manual-editor :deep(textarea) {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.55;
}

@media (max-width: 1180px) {
  .workspace-grid {
    grid-template-columns: minmax(220px, 250px) minmax(0, 1fr);
  }

  .workspace-grid > :last-child {
    grid-column: 1 / -1;
  }
}

@media (max-width: 980px) {
  .workspace-header,
  .section-toolbar,
  .chapter-detail-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .header-actions,
  .toolbar-actions {
    width: 100%;
    flex-wrap: wrap;
    justify-content: flex-start;
  }

  .instruction-input {
    width: min(100%, 420px);
  }

  .branch-actions,
  .branch-generation-bar {
    width: 100%;
  }

  .branch-generation-bar {
    grid-template-columns: minmax(0, 1fr);
  }

  .workspace-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .chapter-workbench {
    grid-template-columns: minmax(0, 1fr);
  }

  .chapter-order {
    border-right: 0;
    border-bottom: 1px solid #dfe3e8;
  }

  .chapter-list {
    max-height: 280px;
  }
}

@media (max-width: 640px) {
  .workspace-header {
    padding: 12px;
  }

  .project-heading h1 {
    max-width: calc(100vw - 92px);
    font-size: 18px;
    white-space: normal;
  }

  .task-strip {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .task-strip > .el-progress {
    grid-column: 1 / -1;
    grid-row: 2;
  }

  .artifact-section {
    padding-right: 12px;
    padding-left: 12px;
  }

  .section-toolbar,
  .chapter-workbench {
    margin-right: -12px;
    margin-left: -12px;
  }

  .section-toolbar {
    padding-right: 12px;
    padding-left: 12px;
  }

  .toolbar-actions > .el-button,
  .toolbar-actions > .el-input-number,
  .instruction-input,
  .generation-bar > .el-button,
  .generation-bar > span {
    width: 100%;
  }

  .generation-bar,
  .compile-options {
    align-items: stretch;
    flex-direction: column;
  }

  .document-meta {
    flex-wrap: wrap;
  }

  .resource-actions {
    flex-wrap: wrap;
  }

  .branch-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .branch-actions > .el-input,
  .branch-actions > .el-input-number,
  .branch-actions > .el-button {
    width: 100%;
    min-width: 0;
  }

  .candidate-item {
    grid-template-columns: 32px minmax(0, 1fr);
  }

  .candidate-item > span {
    grid-column: 1 / -1;
  }

  .candidate-item > span .el-button {
    width: 100%;
  }
}

/* ------------------------------ 草稿区 ------------------------------ */

.draft-card {
  margin-top: 4px;
}

.draft-editor :deep(textarea) {
  color: #2d3740;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.65;
}

.draft-editor-prose :deep(textarea) {
  color: #303841;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.9;
}

.draft-card .draft-editor {
  margin: 12px;
  width: calc(100% - 24px);
}

.draft-card .el-empty {
  padding: 24px 0;
}

.draft-card .prose-preview {
  margin: 12px;
}

.draft-card .json-preview {
  margin: 12px;
  max-height: 360px;
}

.draft-card .document-meta {
  border-bottom: 1px solid #e4e8ec;
}

.draft-preview-body {
  max-height: calc(100vh - 300px);
  margin: 0;
  overflow: auto;
  padding: 18px;
  color: #2d3740;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.script-draft-editor {
  padding: 12px;
}

.script-draft-editor .draft-banner {
  margin: 0 0 12px;
}

.script-paragraph-row {
  padding: 10px 12px;
  border: 1px solid #e4e8ec;
  border-radius: 5px;
  background: #fff;
}

.script-paragraph-row + .script-paragraph-row {
  margin-top: 8px;
}

.script-paragraph-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  color: #536271;
  font-size: 12px;
}

.script-paragraph-index {
  color: #7b8490;
  font-size: 11px;
  font-weight: 650;
}

.script-paragraph-speaker {
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.script-paragraph-fields {
  display: grid;
  grid-template-columns: minmax(160px, 1fr) minmax(220px, 2fr) auto;
  align-items: center;
  gap: 8px;
}

.script-draft-raw {
  margin-top: 12px;
}

.script-draft-raw summary {
  color: #5b8db8;
  cursor: pointer;
  font-size: 12px;
}

.suggestion-preview-body {
  max-height: 60vh;
  margin: 0;
  overflow: auto;
  color: #2d3740;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 640px) {
  .script-paragraph-fields {
    grid-template-columns: minmax(0, 1fr);
  }
}

.voice-lines-panel {
  margin-top: 14px;
  border: 1px solid #e4e8ec;
  border-radius: 10px;
  padding: 12px 14px;
  background: #fafbfc;
}

.voice-lines-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.voice-lines-head h4 {
  margin: 0;
  font-size: 13px;
  color: #1f2d3d;
}

.voice-lines-summary {
  color: #8492a6;
  font-size: 12.5px;
}

.voice-lines-empty {
  margin: 8px 0 0;
  color: #8492a6;
  font-size: 12.5px;
  line-height: 1.6;
}

.voice-lines-list {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
  max-height: 320px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.voice-line-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  background: #fff;
  border: 1px solid #eef1f5;
}

.voice-line-pending {
  opacity: 0.75;
}

.voice-line-index {
  color: #8492a6;
  font-size: 12px;
  min-width: 32px;
}

.voice-line-emotion {
  color: #409eff;
  font-size: 12px;
}

.voice-line-text {
  flex: 1;
  color: #2d3740;
  font-size: 12.5px;
  line-height: 1.6;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
