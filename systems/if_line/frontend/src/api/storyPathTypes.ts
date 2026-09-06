export type UUID = string
export type ISODateTime = string

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonObject
  | JsonValue[]

export interface JsonObject {
  [key: string]: JsonValue
}

export interface TaskAccepted {
  task_id: UUID
  status: string
  events_url: string
  created: boolean
}

export interface GenerationRequest {
  instructions?: string | null
  parameters?: JsonObject
}

export interface ChapterGenerationRequest extends GenerationRequest {
  /** 草稿物化锚点：bible/outline 必须成对出现，不传沿用 head。 */
  bible_revision_id?: UUID | null
  outline_revision_id?: UUID | null
  ancestor_revision_overrides?: Record<string, UUID> | null
}

export interface BlockingItem {
  code: string
  story_path_id?: UUID | null
  path_chapter_id?: UUID | null
  detail?: string | null
}

export type AuthoringStage =
  | 'bible'
  | 'story_paths'
  | 'outlines'
  | 'chapters'
  | 'scripts'
  | 'vn_graphs'
  | 'ready'
  | 'blocked'

export interface AuthoringProjection {
  stage: AuthoringStage
  blocking_items: BlockingItem[]
  running_task_count: number
  has_unpublished_changes: boolean
}

export type PublicationState = 'unpublished' | 'published' | 'changes_pending'

export interface PublicationProjection {
  state: PublicationState
  active_release_id: UUID | null
  published_at: ISODateTime | null
  lock_version: number
}

export interface ProjectCreate {
  title: string
  characters?: JsonObject[]
  story_start?: string
  story_end?: string
  style?: string
  source_work?: string | null
  pace?: string | null
  extra_requirements?: string | null
}

export interface ProjectPatch {
  title?: string | null
  characters?: JsonObject[] | null
  story_start?: string | null
  story_end?: string | null
  style?: string | null
  source_work?: string | null
  pace?: string | null
  extra_requirements?: string | null
}

export interface ProjectRead {
  id: number
  root_story_path_id: UUID | null
  title: string
  characters: JsonObject[]
  story_start: string
  story_end: string
  style: string
  source_work: string | null
  pace: string | null
  extra_requirements: string | null
  authoring: AuthoringProjection
  publication: PublicationProjection
}

export interface PublicationReadiness {
  ready: boolean
  authoring_fingerprint: string
  blocking_items: BlockingItem[]
}

export interface ProjectPathMetrics {
  status: 'active' | 'archived'
  parent_path_id: UUID | null
  path_chapter_ids: UUID[]
}

export interface ProjectMetrics {
  project_id: number
  story_paths_by_id: Record<UUID, ProjectPathMetrics>
  selected_path_chapter_count: number
  revision_counts: {
    bible: number
    outline: number
    chapter: number
    script: number
    vn_graph: number
  }
  task_counts: {
    total: number
    by_status: Record<string, number>
    by_kind: Record<string, number>
  }
}

export interface GenerationTaskMetrics {
  kind: string
  status: string
  path_chapter_id: UUID | null
  duration_seconds: number | null
}

export interface AssetVersionMetrics {
  source_kind: string | null
  source_revision_id: UUID | null
  generation_task_id: UUID | null
  storage_object_id: UUID | null
  quality_score: number | null
  safety_status: string
}

export interface ProjectArtifactMetrics {
  project_id: number
  generation_tasks_by_id: Record<UUID, GenerationTaskMetrics>
  asset_versions_by_id: Record<UUID, AssetVersionMetrics>
}

export interface BibleRevisionCreate {
  parent_revision_id?: UUID | null
  content_json: JsonObject
}

export interface BibleRevision {
  id: UUID
  project_id: number
  parent_revision_id: UUID | null
  revision_no: number
  source_hash: string
  content_hash: string
  content_json: JsonObject
  created_at: ISODateTime
}

export interface RevisionHead {
  revision_id: UUID | null
  lock_version: number
  updated_at: ISODateTime
}

export interface StoryPathCreate {
  candidate_id: UUID
  title?: string | null
}

export interface StoryPathPromotion {
  title?: string | null
}

export interface StoryPathPatch {
  title?: string | null
  status?: 'active' | 'archived' | null
}

export interface StoryPath {
  id: UUID
  project_id: number
  parent_path_id: UUID | null
  fork_path_chapter_id: UUID | null
  fork_checkpoint_node_id: UUID | null
  fork_candidate_id: UUID | null
  base_state_snapshot_id: UUID | null
  title: string
  status: 'active' | 'archived'
  lock_version: number
}

export interface OutlineGenerationRequest {
  chapter_count: number
  instructions?: string | null
  /** 草稿物化锚点：不传沿用 bible head。 */
  bible_revision_id?: UUID | null
}

export interface OutlineChapterInput {
  story_path_chapter_id?: UUID | null
  display_index: number
  title: string
  summary: string
  conflict?: string | null
  characters?: string[]
  scene?: string | null
  emotion?: string | null
  visual_keywords?: string[]
}

export interface OutlineRevisionCreate {
  bible_revision_id: UUID
  parent_revision_id?: UUID | null
  chapters: OutlineChapterInput[]
}

export interface OutlineRevision extends OutlineRevisionCreate {
  id: UUID
  story_path_id: UUID
  revision_no: number
  source_hash: string
  content_hash: string
}

export interface PathChapterCreate {
  after_path_chapter_id?: UUID | null
}

export interface PathChapter {
  id: UUID
  story_path_id: UUID
  chapter_slot_id: UUID
  display_index: number
  predecessor_path_chapter_id: UUID | null
  inherited_from_path_chapter_id: UUID | null
  current_revision_id: UUID | null
  lock_version: number
}

export interface ChapterBatchRequest {
  path_chapter_ids: UUID[]
  instructions?: string | null
  /** 可选物化草稿锚点，与单章生成同契约：成对出现；不传沿用 head。 */
  bible_revision_id?: UUID | null
  outline_revision_id?: UUID | null
}

export interface ChapterRevisionCreate {
  parent_revision_id?: UUID | null
  content: string
}

export interface ChapterRevision {
  id: UUID
  chapter_slot_id: UUID
  created_for_story_path_id: UUID
  parent_revision_id: UUID | null
  revision_no: number
  context_manifest: JsonObject
  context_hash: string
  content_hash: string
  content: string
}

export interface CandidateSetGenerationRequest {
  chapter_revision_id: UUID
  state_snapshot_id: UUID
  candidate_count: number
  instructions?: string | null
}

export interface CandidateSetRevision {
  id: UUID
  story_path_id: UUID
  checkpoint_node_id: UUID
  chapter_revision_id: UUID
  state_snapshot_id: UUID
  revision_no: number
  source_hash: string
  content_hash: string
}

export interface BranchCandidate {
  id: UUID
  candidate_set_revision_id: UUID
  option_key: string
  preview_text: string
  state_delta: JsonObject
}

export interface ScriptRevision {
  id: UUID
  chapter_revision_id: UUID
  revision_no: number
  source_hash: string
  script_hash: string
  script_json: JsonObject
}

export interface ScriptRevisionCreate {
  script_json: JsonObject
  parent_revision_id?: UUID | null
  auto_register_characters?: boolean
}

export interface ScriptRevisionCreated extends ScriptRevision {
  appended_character_names: string[] | null
}

export type ResourceRole = 'portrait' | 'background' | 'keyframe'

export interface ResourceSlot {
  id: UUID
  script_revision_id: UUID
  role: ResourceRole
  asset_version_id: UUID | null
  lock_version: number
}

export interface VNGraphCompileRequest {
  compiler_version?: string | null
  schema_version?: string | null
}

export interface VNGraphRevision {
  id: UUID
  script_revision_id: UUID
  binding_manifest: JsonObject
  binding_manifest_hash: string
  graph_hash: string
  graph_json: JsonObject
}

export interface VNGraphRevisionCreate {
  graph_json: JsonObject
  parent_revision_id?: UUID | null
}

export interface FinalizePublishRequest {
  bible_revision_id?: UUID | null
  outline_revision_ids?: Record<string, UUID>
  chapter_revision_ids?: Record<string, UUID>
  script_revision_ids?: Record<string, UUID>
  graph_revision_ids?: Record<string, UUID>
  release_notes?: string | null
}

export interface PublishRequest {
  expected_authoring_fingerprint: string
  release_notes?: string | null
}

export type ReleaseStatus = 'published' | 'superseded' | 'withdrawn'

export interface Release {
  id: UUID
  project_id: number
  version: number
  status: ReleaseStatus
  manifest_hash: string
  authoring_fingerprint: string
  release_notes: string | null
  published_at: ISODateTime
  withdrawn_at: ISODateTime | null
}

export interface ReleaseManifest {
  release_id: UUID
  version: number
  manifest_hash: string
  manifest: JsonObject
}

export interface PublicProject {
  id: number
  title: string
  summary: string
  cover_url: string | null
  release_id: UUID
  release_version: number
  manifest_hash: string
  published_at: ISODateTime
}

export type PathChapterManifest = JsonObject
export type PublicVNGraph = JsonObject

export interface VoiceLineItem {
  id: UUID
  chapter_revision_id: UUID
  occurrence_id: string
  order_index: number
  kind: string
  text: string
  speaker_character_id?: string | null
  speaker_name?: string | null
  emotion?: string | null
  voice_profile_id?: string | null
  audio_asset_version_id?: string | null
  status: string
}

export interface VoiceManifest {
  chapter_revision_id: UUID
  count: number
  items: VoiceLineItem[]
}
