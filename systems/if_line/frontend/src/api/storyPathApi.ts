import type { AxiosInstance, AxiosRequestConfig } from 'axios'
import api from './index'
import type {
  BibleRevision,
  BibleRevisionCreate,
  BranchCandidate,
  CandidateSetGenerationRequest,
  CandidateSetRevision,
  ChapterBatchRequest,
  ChapterGenerationRequest,
  ChapterRevision,
  ChapterRevisionCreate,
  FinalizePublishRequest,
  GenerationRequest,
  JsonObject,
  OutlineGenerationRequest,
  OutlineRevision,
  OutlineRevisionCreate,
  PathChapter,
  PathChapterCreate,
  PathChapterManifest,
  ProjectArtifactMetrics,
  ProjectCreate,
  ProjectMetrics,
  ProjectPatch,
  ProjectRead,
  PublicProject,
  PublicationReadiness,
  PublicationState,
  PublicVNGraph,
  PublishRequest,
  Release,
  ReleaseManifest,
  ResourceRole,
  ResourceSlot,
  RevisionHead,
  ScriptRevision,
  ScriptRevisionCreate,
  ScriptRevisionCreated,
  StoryPath,
  StoryPathCreate,
  StoryPathPatch,
  StoryPathPromotion,
  TaskAccepted,
  UUID,
  VNGraphCompileRequest,
  VNGraphRevision,
  VNGraphRevisionCreate,
  VoiceManifest,
} from './storyPathTypes'

export type StoryPathHttpClient = Pick<AxiosInstance, 'get' | 'post' | 'put' | 'patch' | 'delete'>

const segment = (value: string | number): string => encodeURIComponent(String(value))

export const storyPathRoutes = {
  projects: '/projects',
  project: (projectId: number) => `/projects/${segment(projectId)}`,
  publicationReadiness: (projectId: number) =>
    `/projects/${segment(projectId)}/publication-readiness`,
  projectMetrics: (projectId: number) => `/projects/${segment(projectId)}/metrics`,
  projectArtifactMetrics: (projectId: number) =>
    `/projects/${segment(projectId)}/metrics/artifacts`,
  bibleGenerations: (projectId: number) => `/projects/${segment(projectId)}/bible-generations`,
  bibleRevisions: (projectId: number) => `/projects/${segment(projectId)}/bible-revisions`,
  bibleHead: (projectId: number) => `/projects/${segment(projectId)}/bible-head`,
  projectStoryPaths: (projectId: number) => `/projects/${segment(projectId)}/story-paths`,
  storyPath: (storyPathId: UUID) => `/story-paths/${segment(storyPathId)}`,
  outlineGenerations: (storyPathId: UUID) =>
    `/story-paths/${segment(storyPathId)}/outline-generations`,
  outlineRevisions: (storyPathId: UUID) =>
    `/story-paths/${segment(storyPathId)}/outline-revisions`,
  outlineHead: (storyPathId: UUID) => `/story-paths/${segment(storyPathId)}/outline-head`,
  pathChapters: (storyPathId: UUID) => `/story-paths/${segment(storyPathId)}/chapters`,
  chapterGenerationBatches: (storyPathId: UUID) =>
    `/story-paths/${segment(storyPathId)}/chapter-generation-batches`,
  pathChapterGenerations: (pathChapterId: UUID) =>
    `/path-chapters/${segment(pathChapterId)}/generations`,
  pathChapterRevisions: (pathChapterId: UUID) =>
    `/path-chapters/${segment(pathChapterId)}/revisions`,
  pathChapterHead: (pathChapterId: UUID) => `/path-chapters/${segment(pathChapterId)}/head`,
  chapterRevision: (chapterRevisionId: UUID) =>
    `/chapter-revisions/${segment(chapterRevisionId)}`,
  voiceLinesGenerate: (projectId: number, chapterRevisionId: UUID) =>
    `/projects/${segment(projectId)}/chapter-revisions/${segment(chapterRevisionId)}/voice-lines/generations`,
  voiceLines: (projectId: number, chapterRevisionId: UUID) =>
    `/projects/${segment(projectId)}/chapter-revisions/${segment(chapterRevisionId)}/voice-lines`,
  scriptGenerations: (chapterRevisionId: UUID) =>
    `/chapter-revisions/${segment(chapterRevisionId)}/script-generations`,
  scriptRevisions: (chapterRevisionId: UUID) =>
    `/chapter-revisions/${segment(chapterRevisionId)}/script-revisions`,
  scriptHead: (chapterRevisionId: UUID) =>
    `/chapter-revisions/${segment(chapterRevisionId)}/script-head`,
  candidateSetGenerations: (storyPathId: UUID, nodeId: UUID) =>
    `/story-paths/${segment(storyPathId)}/checkpoints/${segment(nodeId)}/candidate-set-generations`,
  candidateSetRevisions: (storyPathId: UUID, nodeId: UUID) =>
    `/story-paths/${segment(storyPathId)}/checkpoints/${segment(nodeId)}/candidate-set-revisions`,
  candidateSetHead: (storyPathId: UUID, nodeId: UUID) =>
    `/story-paths/${segment(storyPathId)}/checkpoints/${segment(nodeId)}/candidate-set-head`,
  branchCandidates: (candidateSetRevisionId: UUID) =>
    `/candidate-set-revisions/${segment(candidateSetRevisionId)}/candidates`,
  candidateStoryPaths: (candidateId: UUID) =>
    `/branch-candidates/${segment(candidateId)}/story-paths`,
  resourceSlots: (scriptRevisionId: UUID) =>
    `/chapter-script-revisions/${segment(scriptRevisionId)}/resource-slots`,
  resourceRenders: (scriptRevisionId: UUID) =>
    `/chapter-script-revisions/${segment(scriptRevisionId)}/resource-renders`,
  resourceSlot: (scriptRevisionId: UUID, slotId: UUID) =>
    `/chapter-script-revisions/${segment(scriptRevisionId)}/resource-slots/${segment(slotId)}`,
  vnGraphCompilations: (scriptRevisionId: UUID) =>
    `/chapter-script-revisions/${segment(scriptRevisionId)}/vn-graph-compilations`,
  vnGraphRevisions: (scriptRevisionId: UUID) =>
    `/chapter-script-revisions/${segment(scriptRevisionId)}/vn-graph-revisions`,
  vnGraphHead: (scriptRevisionId: UUID) =>
    `/chapter-script-revisions/${segment(scriptRevisionId)}/vn-graph-head`,
  vnGraphRevision: (vnGraphRevisionId: UUID) =>
    `/vn-graph-revisions/${segment(vnGraphRevisionId)}`,
  publish: (projectId: number) => `/projects/${segment(projectId)}/publish`,
  finalizePublish: (projectId: number) =>
    `/projects/${segment(projectId)}/finalize-publish`,
  releases: (projectId: number) => `/projects/${segment(projectId)}/releases`,
  release: (releaseId: UUID) => `/releases/${segment(releaseId)}`,
  unpublish: (projectId: number) => `/projects/${segment(projectId)}/unpublish`,
  publicProjects: '/public/projects',
  publicProject: (projectId: number) => `/public/projects/${segment(projectId)}`,
  publicReleaseManifest: (releaseId: UUID) =>
    `/public/releases/${segment(releaseId)}/manifest`,
  publicPathChapterManifest: (releaseId: UUID, pathChapterId: UUID) =>
    `/public/releases/${segment(releaseId)}/path-chapters/${segment(pathChapterId)}/manifest`,
  publicPathChapterVNGraph: (releaseId: UUID, pathChapterId: UUID) =>
    `/public/releases/${segment(releaseId)}/path-chapters/${segment(pathChapterId)}/vn-graph`,
} as const

const commandConfig = (idempotencyKey: string): AxiosRequestConfig => {
  const key = idempotencyKey.trim()
  if (!key || key.length > 255) {
    throw new TypeError('Idempotency-Key must contain 1 to 255 characters')
  }
  return { headers: { 'Idempotency-Key': key } }
}

const matchConfig = (lockVersion: number): AxiosRequestConfig => {
  if (!Number.isSafeInteger(lockVersion) || lockVersion < 1) {
    throw new TypeError('lockVersion must be a positive integer')
  }
  return { headers: { 'If-Match': String(lockVersion) } }
}

export const createIdempotencyKey = (scope: string): string => {
  const randomId = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  const normalizedScope = scope.trim() || 'authoring'
  const maxScopeLength = 255 - randomId.length - 1
  return `${normalizedScope.slice(0, maxScopeLength)}:${randomId}`
}

export const createStoryPathApi = (client: StoryPathHttpClient) => ({
  projects: {
    create: (body: ProjectCreate) => client.post<ProjectRead>(storyPathRoutes.projects, body),
    list: (publicationState: PublicationState | 'all' = 'all') =>
      client.get<ProjectRead[]>(storyPathRoutes.projects, {
        params: { publication_state: publicationState },
      }),
    get: (projectId: number) => client.get<ProjectRead>(storyPathRoutes.project(projectId)),
    update: (projectId: number, body: ProjectPatch) =>
      client.patch<ProjectRead>(storyPathRoutes.project(projectId), body),
    delete: (projectId: number) => client.delete<void>(storyPathRoutes.project(projectId)),
    readiness: (projectId: number) =>
      client.get<PublicationReadiness>(storyPathRoutes.publicationReadiness(projectId)),
    metrics: (projectId: number) =>
      client.get<ProjectMetrics>(storyPathRoutes.projectMetrics(projectId)),
    artifactMetrics: (projectId: number) =>
      client.get<ProjectArtifactMetrics>(storyPathRoutes.projectArtifactMetrics(projectId)),
  },
  bible: {
    generate: (projectId: number, body: GenerationRequest, idempotencyKey: string) =>
      client.post<TaskAccepted>(
        storyPathRoutes.bibleGenerations(projectId),
        body,
        commandConfig(idempotencyKey),
      ),
    listRevisions: (projectId: number) =>
      client.get<BibleRevision[]>(storyPathRoutes.bibleRevisions(projectId)),
    createRevision: (projectId: number, body: BibleRevisionCreate) =>
      client.post<BibleRevision>(storyPathRoutes.bibleRevisions(projectId), body),
    getHead: (projectId: number) =>
      client.get<RevisionHead>(storyPathRoutes.bibleHead(projectId)),
    updateHead: (projectId: number, revisionId: UUID, lockVersion: number) =>
      client.put<RevisionHead>(
        storyPathRoutes.bibleHead(projectId),
        { revision_id: revisionId },
        matchConfig(lockVersion),
      ),
  },
  storyPaths: {
    list: (projectId: number) =>
      client.get<StoryPath[]>(storyPathRoutes.projectStoryPaths(projectId)),
    createFromCandidate: (
      projectId: number,
      body: StoryPathCreate,
      idempotencyKey: string,
    ) => client.post<StoryPath>(
      storyPathRoutes.projectStoryPaths(projectId),
      body,
      commandConfig(idempotencyKey),
    ),
    get: (storyPathId: UUID) => client.get<StoryPath>(storyPathRoutes.storyPath(storyPathId)),
    update: (storyPathId: UUID, body: StoryPathPatch, lockVersion: number) =>
      client.patch<StoryPath>(
        storyPathRoutes.storyPath(storyPathId),
        body,
        matchConfig(lockVersion),
      ),
  },
  outlines: {
    generate: (
      storyPathId: UUID,
      body: OutlineGenerationRequest,
      idempotencyKey: string,
    ) => client.post<TaskAccepted>(
      storyPathRoutes.outlineGenerations(storyPathId),
      body,
      commandConfig(idempotencyKey),
    ),
    listRevisions: (storyPathId: UUID) =>
      client.get<OutlineRevision[]>(storyPathRoutes.outlineRevisions(storyPathId)),
    createRevision: (storyPathId: UUID, body: OutlineRevisionCreate) =>
      client.post<OutlineRevision>(storyPathRoutes.outlineRevisions(storyPathId), body),
    getHead: (storyPathId: UUID) =>
      client.get<RevisionHead>(storyPathRoutes.outlineHead(storyPathId)),
    updateHead: (storyPathId: UUID, revisionId: UUID, lockVersion: number) =>
      client.put<RevisionHead>(
        storyPathRoutes.outlineHead(storyPathId),
        { revision_id: revisionId },
        matchConfig(lockVersion),
      ),
  },
  chapters: {
    list: (storyPathId: UUID) =>
      client.get<PathChapter[]>(storyPathRoutes.pathChapters(storyPathId)),
    append: (storyPathId: UUID, body: PathChapterCreate, pathLockVersion: number) =>
      client.post<PathChapter>(
        storyPathRoutes.pathChapters(storyPathId),
        body,
        matchConfig(pathLockVersion),
      ),
    generateBatch: (
      storyPathId: UUID,
      body: ChapterBatchRequest,
      idempotencyKey: string,
    ) => client.post<TaskAccepted>(
      storyPathRoutes.chapterGenerationBatches(storyPathId),
      body,
      commandConfig(idempotencyKey),
    ),
    generate: (pathChapterId: UUID, body: ChapterGenerationRequest, idempotencyKey: string) =>
      client.post<TaskAccepted>(
        storyPathRoutes.pathChapterGenerations(pathChapterId),
        body,
        commandConfig(idempotencyKey),
      ),
    listRevisions: (pathChapterId: UUID) =>
      client.get<ChapterRevision[]>(storyPathRoutes.pathChapterRevisions(pathChapterId)),
    createRevision: (pathChapterId: UUID, body: ChapterRevisionCreate) =>
      client.post<ChapterRevision>(storyPathRoutes.pathChapterRevisions(pathChapterId), body),
    getHead: (pathChapterId: UUID) =>
      client.get<RevisionHead>(storyPathRoutes.pathChapterHead(pathChapterId)),
    updateHead: (pathChapterId: UUID, revisionId: UUID, lockVersion: number) =>
      client.put<RevisionHead>(
        storyPathRoutes.pathChapterHead(pathChapterId),
        { revision_id: revisionId },
        matchConfig(lockVersion),
      ),
    getRevision: (chapterRevisionId: UUID) =>
      client.get<ChapterRevision>(storyPathRoutes.chapterRevision(chapterRevisionId)),
  },
  candidates: {
    generate: (
      storyPathId: UUID,
      nodeId: UUID,
      body: CandidateSetGenerationRequest,
      idempotencyKey: string,
    ) => client.post<TaskAccepted>(
      storyPathRoutes.candidateSetGenerations(storyPathId, nodeId),
      body,
      commandConfig(idempotencyKey),
    ),
    listRevisions: (storyPathId: UUID, nodeId: UUID) =>
      client.get<CandidateSetRevision[]>(
        storyPathRoutes.candidateSetRevisions(storyPathId, nodeId),
      ),
    getHead: (storyPathId: UUID, nodeId: UUID) =>
      client.get<RevisionHead>(storyPathRoutes.candidateSetHead(storyPathId, nodeId)),
    updateHead: (
      storyPathId: UUID,
      nodeId: UUID,
      revisionId: UUID,
      lockVersion: number,
    ) => client.put<RevisionHead>(
      storyPathRoutes.candidateSetHead(storyPathId, nodeId),
      { revision_id: revisionId },
      matchConfig(lockVersion),
    ),
    listCandidates: (candidateSetRevisionId: UUID) =>
      client.get<BranchCandidate[]>(storyPathRoutes.branchCandidates(candidateSetRevisionId)),
    promote: (candidateId: UUID, body: StoryPathPromotion, idempotencyKey: string) =>
      client.post<StoryPath>(
        storyPathRoutes.candidateStoryPaths(candidateId),
        body,
        commandConfig(idempotencyKey),
      ),
  },
  voiceLines: {
    generate: (projectId: number, chapterRevisionId: UUID, idempotencyKey: string) =>
      client.post<TaskAccepted>(
        storyPathRoutes.voiceLinesGenerate(projectId, chapterRevisionId),
        { render_audio: true },
        commandConfig(idempotencyKey),
      ),
    list: (projectId: number, chapterRevisionId: UUID) =>
      client.get<VoiceManifest>(storyPathRoutes.voiceLines(projectId, chapterRevisionId)),
  },
  scripts: {
    generate: (chapterRevisionId: UUID, body: GenerationRequest, idempotencyKey: string) =>
      client.post<TaskAccepted>(
        storyPathRoutes.scriptGenerations(chapterRevisionId),
        body,
        commandConfig(idempotencyKey),
      ),
    listRevisions: (chapterRevisionId: UUID) =>
      client.get<ScriptRevision[]>(storyPathRoutes.scriptRevisions(chapterRevisionId)),
    createRevision: (chapterRevisionId: UUID, body: ScriptRevisionCreate) =>
      client.post<ScriptRevisionCreated>(
        storyPathRoutes.scriptRevisions(chapterRevisionId),
        body,
      ),
    getHead: (chapterRevisionId: UUID) =>
      client.get<RevisionHead>(storyPathRoutes.scriptHead(chapterRevisionId)),
    updateHead: (chapterRevisionId: UUID, revisionId: UUID, lockVersion: number) =>
      client.put<RevisionHead>(
        storyPathRoutes.scriptHead(chapterRevisionId),
        { revision_id: revisionId },
        matchConfig(lockVersion),
      ),
  },
  resources: {
    list: (scriptRevisionId: UUID) =>
      client.get<ResourceSlot[]>(storyPathRoutes.resourceSlots(scriptRevisionId)),
    render: (scriptRevisionId: UUID, role: ResourceRole, idempotencyKey: string) =>
      client.post<TaskAccepted>(
        storyPathRoutes.resourceRenders(scriptRevisionId),
        { role },
        commandConfig(idempotencyKey),
      ),
    bind: (
      scriptRevisionId: UUID,
      slotId: UUID,
      assetVersionId: UUID,
      lockVersion: number,
    ) => client.put<ResourceSlot>(
      storyPathRoutes.resourceSlot(scriptRevisionId, slotId),
      { asset_version_id: assetVersionId },
      matchConfig(lockVersion),
    ),
  },
  vnGraphs: {
    compile: (
      scriptRevisionId: UUID,
      body: VNGraphCompileRequest,
      idempotencyKey: string,
    ) => client.post<TaskAccepted>(
      storyPathRoutes.vnGraphCompilations(scriptRevisionId),
      body,
      commandConfig(idempotencyKey),
    ),
    listRevisions: (scriptRevisionId: UUID) =>
      client.get<VNGraphRevision[]>(storyPathRoutes.vnGraphRevisions(scriptRevisionId)),
    createRevision: (scriptRevisionId: UUID, body: VNGraphRevisionCreate) =>
      client.post<VNGraphRevision>(
        storyPathRoutes.vnGraphRevisions(scriptRevisionId),
        body,
      ),
    getHead: (scriptRevisionId: UUID) =>
      client.get<RevisionHead>(storyPathRoutes.vnGraphHead(scriptRevisionId)),
    updateHead: (scriptRevisionId: UUID, revisionId: UUID, lockVersion: number) =>
      client.put<RevisionHead>(
        storyPathRoutes.vnGraphHead(scriptRevisionId),
        { revision_id: revisionId },
        matchConfig(lockVersion),
      ),
    getRevision: (vnGraphRevisionId: UUID) =>
      client.get<VNGraphRevision>(storyPathRoutes.vnGraphRevision(vnGraphRevisionId)),
  },
  releases: {
    publish: (projectId: number, body: PublishRequest, idempotencyKey: string) =>
      client.post<Release>(
        storyPathRoutes.publish(projectId),
        body,
        commandConfig(idempotencyKey),
      ),
    finalizePublish: (projectId: number, body: FinalizePublishRequest, idempotencyKey: string) =>
      client.post<Release>(
        storyPathRoutes.finalizePublish(projectId),
        body,
        commandConfig(idempotencyKey),
      ),
    list: (projectId: number) => client.get<Release[]>(storyPathRoutes.releases(projectId)),
    get: (releaseId: UUID) => client.get<Release>(storyPathRoutes.release(releaseId)),
    unpublish: (projectId: number, publicationLockVersion: number) =>
      client.post<Release>(
        storyPathRoutes.unpublish(projectId),
        undefined,
        matchConfig(publicationLockVersion),
      ),
  },
  public: {
    listProjects: () => client.get<PublicProject[]>(storyPathRoutes.publicProjects),
    getProject: (projectId: number) =>
      client.get<PublicProject>(storyPathRoutes.publicProject(projectId)),
    getReleaseManifest: (releaseId: UUID) =>
      client.get<ReleaseManifest>(storyPathRoutes.publicReleaseManifest(releaseId)),
    getPathChapterManifest: (releaseId: UUID, pathChapterId: UUID) =>
      client.get<PathChapterManifest>(
        storyPathRoutes.publicPathChapterManifest(releaseId, pathChapterId),
      ),
    getPathChapterVNGraph: (releaseId: UUID, pathChapterId: UUID, etag?: string) => {
      const config: AxiosRequestConfig = {
        validateStatus: (status) => status === 200 || status === 304,
      }
      if (etag) config.headers = { 'If-None-Match': etag }
      return client.get<PublicVNGraph>(
        storyPathRoutes.publicPathChapterVNGraph(releaseId, pathChapterId),
        config,
      )
    },
  },
})

export const storyPathApi = createStoryPathApi(api)

export type StoryPathApi = ReturnType<typeof createStoryPathApi>
export type { JsonObject }
