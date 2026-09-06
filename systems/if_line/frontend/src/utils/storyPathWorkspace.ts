import type { OutlineChapterInput, PathChapter, StoryPath } from '@/api/storyPathTypes'

export interface StoryPathTreeNode extends StoryPath {
  children: StoryPathTreeNode[]
}

function canAttachToParent(
  node: StoryPathTreeNode,
  parent: StoryPathTreeNode,
  byId: Map<string, StoryPathTreeNode>,
): boolean {
  const visited = new Set<string>()
  let cursor: StoryPathTreeNode | undefined = parent

  while (cursor && !visited.has(cursor.id)) {
    if (cursor.id === node.id) return false
    visited.add(cursor.id)
    cursor = cursor.parent_path_id ? byId.get(cursor.parent_path_id) : undefined
  }
  return true
}

function sortTree(nodes: StoryPathTreeNode[]): void {
  nodes.sort((left, right) => {
    if (left.status !== right.status) return left.status === 'active' ? -1 : 1
    return left.title.localeCompare(right.title, 'zh-CN')
  })
  for (const node of nodes) sortTree(node.children)
}

export function buildStoryPathTree(paths: StoryPath[]): StoryPathTreeNode[] {
  const byId = new Map(
    paths.map((path) => [path.id, { ...path, children: [] } satisfies StoryPathTreeNode]),
  )
  const roots: StoryPathTreeNode[] = []

  for (const path of paths) {
    const node = byId.get(path.id)
    if (!node) continue
    const parent = path.parent_path_id ? byId.get(path.parent_path_id) : undefined
    if (parent && canAttachToParent(node, parent, byId)) parent.children.push(node)
    else roots.push(node)
  }

  sortTree(roots)
  return roots
}

export function shortId(value: string | null | undefined, length = 8): string {
  if (!value) return '-'
  return value.length <= length ? value : value.slice(0, length)
}

export function titleForPathChapter(
  chapter: Pick<PathChapter, 'id' | 'display_index'>,
  outlineChapters: OutlineChapterInput[] = [],
): string {
  const outlineChapter = outlineChapters.find(
    (item) => item.story_path_chapter_id === chapter.id,
  )
  return outlineChapter?.title || `第 ${chapter.display_index} 章`
}

export interface RevisionLike {
  id: string
  revision_no?: number
  content_hash?: string
  script_hash?: string
  graph_hash?: string
  created_at?: string
}

export interface RevisionOption {
  id: string
  label: string
  detail: string
}

export function toRevisionOptions(revisions: RevisionLike[], noun = '版本'): RevisionOption[] {
  return revisions.map((revision, index) => {
    const number = revision.revision_no ?? revisions.length - index
    const hash = revision.content_hash ?? revision.script_hash ?? revision.graph_hash
    const createdAt = revision.created_at
      ? new Date(revision.created_at).toLocaleString('zh-CN')
      : ''
    return {
      id: revision.id,
      label: `${noun} r${number}`,
      detail: createdAt || (hash ? shortId(hash, 12) : shortId(revision.id, 12)),
    }
  })
}
