import api from './index'

export interface PublicReleaseProject {
  id: number
  title: string
  release_id: string
  release_version: number
  chapter_count: number
}

export interface ReadingSession {
  id: string
  project_id: number
  release_id: string
  selected_continuation_id: string | null
  lock_version: number
  status: string
}

export interface ReadingDirectionSuggestion {
  direction: string
  prompt_version: string
}

export interface ReadingContinuation {
  id: string
  session_id: string
  parent_continuation_id: string | null
  direction: string
  visual_mode: string
  status: 'processing' | 'preview_ready' | 'confirmed' | 'failed' | 'cancelled'
  continuation_text: string
  state_delta: Record<string, unknown>
  scene_manifest?: Record<string, unknown> | null
  vngraph_patch?: Array<Record<string, unknown>>
  uploaded_asset_version_ids?: string[]
  frozen_asset_version_ids?: string[]
  task_id: string | null
  asset_action_id: string | null
  base_session_lock_version: number
  error: { code?: string; message?: string } | null
}

export interface PublicContinuationNode {
  id: string
  parent_continuation_id: string | null
  direction: string
  continuation_text: string
  chapter_number: number
  depth: number
  selection_count: number
  assets: GeneratedAsset[]
  confirmed_at: string
}

export interface PublicContinuationTree {
  project_id: number
  release_id: string
  chapter_number: number
  nodes: PublicContinuationNode[]
}

export interface VnPortraitPresentation {
  normalization_version?: string
  canvas_width?: number
  canvas_height?: number
  foreground_height_ratio?: number
  foreground_width_ratio?: number
  bottom_ratio?: number
  limiting_axis?: string
  scale_factor?: number
}

export interface VnGeneratedPortrait {
  character_name: string
  stable_key: string
  media_url: string
  cached: boolean
  variant: VnPortraitVariant
  take: VnVisualTake
  variant_label: string
  style_pack_version: string
  identity_version: string
  /** Normalized 768×1280 canvas URL; preferred by the VN stage when present. */
  presentation_url?: string | null
  /** Geometry metadata describing the normalized canvas layout. */
  presentation?: VnPortraitPresentation | null
  quality?: {
    passed: boolean
    reason: string
    metrics: Record<string, unknown>
  } | null
}

export type VnPortraitVariant =
  | 'neutral'
  | 'smile'
  | 'angry'
  | 'sad'
  | 'surprise'
  | 'fear'
  | 'combat'
  | 'command'
  | 'thinking'
  | 'respect'
  | 'injured'
export type VnVisualTake = 0 | 1 | 2

export interface VnGeneratedBackground {
  stable_key: string
  background_key: string
  scene_key?: string
  base_background_key?: string
  chapter_number?: number
  source_index?: number
  source_beat_index?: number
  media_url: string
  cached: boolean
  take: VnVisualTake
  style_pack_version: string
  identity_version: string
  quality?: {
    passed: boolean
    reason: string
    metrics: Record<string, unknown>
  } | null
}

export interface VnCachedVisualCatalog {
  style_pack_version: string
  portraits: VnGeneratedPortrait[]
  backgrounds: VnGeneratedBackground[]
}

export interface VnChapterSceneCatalog {
  chapter_number: number
  style_pack_version: string
  scenes: VnGeneratedBackground[]
}

export interface GuestVisualUpload {
  action_id: string
  asset_id: number
  asset_version_id: string
  media_url: string
  asset_type: 'portrait' | 'background' | 'keyframe'
  target_name: string
  ownership_scope: 'session' | 'personal' | 'project'
  width: number
  height: number
  status: string
}

export interface GeneratedAsset {
  asset_id: number
  asset_version_id: string
  storage_object_id?: string
  asset_type?: 'portrait' | 'background' | 'keyframe'
  media_url: string
  sha256?: string
  description_cn?: string
  tags?: string[]
  taxonomy?: Record<string, unknown>
  width: number | null
  height: number | null
}

export interface AssetAction {
  action_id: string
  status: 'processing' | 'awaiting_confirmation' | 'ready' | 'applied' | 'failed' | 'cancelled'
  stage: string
  progress: number
  origin: string | null
  task_id: string | null
  assets: GeneratedAsset[]
  scene_manifest?: Record<string, unknown> | null
  vngraph_patch?: Array<Record<string, unknown>>
  error: { code?: string; message?: string } | null
}

export const guestStoryApi = {
  findThreeKingdomsRelease: () =>
    api.get<PublicReleaseProject[]>('/public/projects', {
      params: { q: '三国演义·公开互动版', limit: 10 }
    }),

  listPublicContinuations: (projectId: number, releaseId: string, chapterNumber: number) =>
    api.get<PublicContinuationTree>(
      `/public/projects/${projectId}/releases/${releaseId}/continuations`,
      { params: { chapter_number: chapterNumber } }
    ),

  listThreeKingdomsCachedVisuals: () =>
    api.get<VnCachedVisualCatalog>('/three-kingdoms/cached-visuals'),

  listThreeKingdomsChapterSceneVisuals: (chapterNumber: number) =>
    api.get<VnChapterSceneCatalog>(
      `/three-kingdoms/chapter-scene-visuals/${chapterNumber}`
    ),

  ensureThreeKingdomsPortrait: (
    characterName: string,
    variant: VnPortraitVariant = 'neutral',
    take: VnVisualTake = 0
  ) =>
    api.post<VnGeneratedPortrait>(
      `/three-kingdoms/portraits/${encodeURIComponent(characterName)}`,
      undefined,
      { params: { variant, take } }
    ),

  ensureThreeKingdomsSceneVisual: (
    chapterNumber: number,
    sourceIndex: number,
    sourceBeatIndex: number,
    backgroundKey: string,
    cue: string,
    timeOfDay: string,
    mood: string,
    take: VnVisualTake = 0
  ) =>
    api.post<VnGeneratedBackground>('/three-kingdoms/scene-visuals', {
      chapter_number: chapterNumber,
      source_index: sourceIndex,
      source_beat_index: sourceBeatIndex,
      background_key: backgroundKey,
      cue,
      time_of_day: timeOfDay,
      mood,
      take
    }),

  startReadingSession: (
    projectId: number,
    releaseId: string,
    initialState: Record<string, unknown>
  ) => api.post<ReadingSession>('/reading-sessions', {
    project_id: projectId,
    release_id: releaseId,
    initial_state: initialState
  }),

  readSession: (sessionId: string) =>
    api.get<ReadingSession>(`/reading-sessions/${sessionId}`),

  selectContinuation: (sessionId: string, continuationId: string) =>
    api.post<ReadingSession>(
      `/reading-sessions/${sessionId}/continuations/${continuationId}/select`
    ),

  suggestContinuationDirection: (sessionId: string) =>
    api.post<ReadingDirectionSuggestion>(
      `/reading-sessions/${sessionId}/direction-suggestion`
    ),

  continueStory: (
    sessionId: string,
    direction: string,
    parentContinuationId: string | null,
    visualMode: 'user_upload' | 'system_generate',
    uploadedAssetVersionIds: string[],
    maxGeneratedAssets: number,
    idempotencyKey: string
  ) => api.post<ReadingContinuation>(
    `/reading-sessions/${sessionId}/directions`,
    {
      direction,
      visual_mode: visualMode,
      uploaded_asset_version_ids: uploadedAssetVersionIds,
      max_generated_assets: maxGeneratedAssets,
      parent_continuation_id: parentContinuationId
    },
    { headers: { 'Idempotency-Key': idempotencyKey } }
  ),

  uploadReadingImage: (
    projectId: number,
    sessionId: string,
    file: File,
    description: string,
    idempotencyKey: string
  ) => {
    const form = new FormData()
    form.append('file', file)
    form.append('asset_type', 'background')
    form.append('target_name', file.name || '游客续写场景图')
    form.append('description', description)
    form.append('ownership_scope', 'session')
    form.append('reading_session_id', sessionId)
    form.append('rights_attested', 'true')
    form.append('target_json', JSON.stringify({
      source_kind: 'reading_continuation',
      source_id: sessionId,
      node_key: 'continuation-preview',
      asset_slot: 'BackgroundImage',
      role: 'background'
    }))
    return api.post<GuestVisualUpload>(
      `/projects/${projectId}/visual-assets/uploads`,
      form,
      {
        headers: {
          'Content-Type': 'multipart/form-data',
          'Idempotency-Key': idempotencyKey
        }
      }
    )
  },

  readContinuation: (sessionId: string, continuationId: string) =>
    api.get<ReadingContinuation>(
      `/reading-sessions/${sessionId}/continuations/${continuationId}`
    ),

  confirmContinuation: (
    sessionId: string,
    continuationId: string,
    lockVersion: number
  ) => api.post<{ continuation: ReadingContinuation; session_lock_version: number }>(
    `/reading-sessions/${sessionId}/continuations/${continuationId}/confirm`,
    {},
    { headers: { 'If-Match': String(lockVersion) } }
  ),

  generateContinuationImage: (
    sessionId: string,
    continuationId: string,
    prompt: string,
    idempotencyKey: string
  ) => api.post<AssetAction>(
    `/reading-sessions/${sessionId}/continuations/${continuationId}/images`,
    { prompt, width: 1024, height: 1024 },
    { headers: { 'Idempotency-Key': idempotencyKey } }
  ),

  readAssetAction: (projectId: number, actionId: string) =>
    api.get<AssetAction>(`/projects/${projectId}/asset-actions/${actionId}`)
}
