#!/usr/bin/env node

import fs from 'node:fs'
import path from 'node:path'

const [epubDirectoryArg, targetDirectoryArg] = process.argv.slice(2)

if (!epubDirectoryArg || !targetDirectoryArg) {
  console.error(
    'Usage: node import_three_kingdoms_vernacular.mjs <expanded-epub-directory> <frontend/public/three-kingdoms>',
  )
  process.exit(1)
}

const epubDirectory = path.resolve(epubDirectoryArg)
const targetDirectory = path.resolve(targetDirectoryArg)
const textDirectory = path.join(epubDirectory, 'text')
const tocPath = path.join(textDirectory, 'part0000_split_000.html')
const catalogPath = path.join(targetDirectory, 'catalog.json')
const chaptersDirectory = path.join(targetDirectory, 'chapters')

const decodeHtmlEntities = (value) => value
  .replace(/&#x([0-9a-f]+);/gi, (_, digits) => String.fromCodePoint(Number.parseInt(digits, 16)))
  .replace(/&#([0-9]+);/g, (_, digits) => String.fromCodePoint(Number.parseInt(digits, 10)))
  .replace(/&nbsp;/gi, ' ')
  .replace(/&amp;/gi, '&')
  .replace(/&lt;/gi, '<')
  .replace(/&gt;/gi, '>')
  .replace(/&quot;/gi, '"')
  .replace(/&apos;/gi, "'")

const htmlToText = (value) => decodeHtmlEntities(value
  .replace(/<br\s*\/?>/gi, '\n')
  .replace(/<[^>]+>/g, '')
  .replace(/[\t\r\n\u00a0\u3000 ]+/g, ' ')
  .trim())

const stripReadingAids = (value) => value
  .replace(/<!--[\s\S]*?-->/g, '')
  .replace(/<script\b[\s\S]*?<\/script>/gi, '')
  .replace(/<style\b[\s\S]*?<\/style>/gi, '')
  .replace(/<sup\b[\s\S]*?<\/sup>/gi, '')
  .replace(/<span\b[^>]*class="c"[^>]*>[\s\S]*?<\/span>/gi, '')

const isNonNarrativePromotion = (text) => (
  /更多好书.*公众号|sanqiujun/i.test(text)
)

const extractChapterLines = (html) => {
  const bodyAfterTitle = html.replace(/^[\s\S]*?<\/h1>/i, '')
  const narrativeOnly = bodyAfterTitle.split(/<div\b[^>]*class="fnote\d?"[^>]*>/i)[0]
  const cleaned = stripReadingAids(narrativeOnly)
  const lines = []
  const blockPattern = /<blockquote\b[^>]*>([\s\S]*?)<\/blockquote>|<p\b[^>]*>([\s\S]*?)<\/p>/gi

  for (const match of cleaned.matchAll(blockPattern)) {
    if (match[1] !== undefined) {
      for (const poemLine of match[1].matchAll(/<p\b[^>]*>([\s\S]*?)<\/p>/gi)) {
        const text = htmlToText(poemLine[1])
        if (text && !isNonNarrativePromotion(text)) lines.push({ speaker: '诗赞', text })
      }
      continue
    }

    const text = htmlToText(match[2])
    if (text && !isNonNarrativePromotion(text)) lines.push({ speaker: '旁白', text })
  }

  return lines
}

const chapterNode = (chapterNumber, line, lineIndex, lineCount) => {
  const index = lineIndex + 10
  const outputs = lineIndex + 1 < lineCount ? { Next: [index + 1] } : {}

  return {
    Index: index,
    DisplayName: `正文 ${String(lineIndex + 1).padStart(3, '0')}`,
    Comment: `《三国演义》第${chapterNumber}回，第${lineIndex + 1}段`,
    NodeType: 1,
    SubType: 2,
    X: 800 + lineIndex * 370,
    Y: 120,
    Data: {
      Lines: {
        Kind: 'List',
        Items: [
          {
            Kind: 'Object',
            ObjectValue: {
              SpeakerId: { Kind: 'String', StringValue: line.speaker },
              Text: { Kind: 'String', StringValue: line.text },
              VoiceId: { Kind: 'String', StringValue: '' },
            },
          },
        ],
      },
    },
    Outputs: outputs,
  }
}

const tocHtml = fs.readFileSync(tocPath, 'utf8')
const tocEntries = [...tocHtml.matchAll(/<a\b[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi)]
  .map((match) => ({
    href: match[1],
    title: htmlToText(match[2]).replace(/\s*([；;])\s*/g, '；'),
  }))
  .filter((entry) => /^第.+回(?:\s|　)/.test(entry.title))

if (tocEntries.length !== 120) {
  throw new Error(`Expected 120 chapter entries in the EPUB table of contents, found ${tocEntries.length}`)
}

const catalog = JSON.parse(fs.readFileSync(catalogPath, 'utf8'))
if (catalog.chapterCount !== 120 || catalog.chapters?.length !== 120) {
  throw new Error('Target catalog is not a complete 120-chapter catalog')
}

let totalCharacters = 0
let totalParagraphs = 0
let poemLines = 0
const chapterSummaries = []

for (const [chapterOffset, entry] of tocEntries.entries()) {
  const chapterNumber = chapterOffset + 1
  const sourceFile = entry.href.split('#')[0]
  const sourcePath = path.join(textDirectory, sourceFile)
  const chapterPath = path.join(chaptersDirectory, `${String(chapterNumber).padStart(3, '0')}.json`)
  const sourceHtml = fs.readFileSync(sourcePath, 'utf8')
  const lines = extractChapterLines(sourceHtml)

  if (lines.length === 0) {
    throw new Error(`No narrative paragraphs found for chapter ${chapterNumber} in ${sourceFile}`)
  }
  if (lines.some((line) => /<[^>]+>|〚\d+〛|\[\d+\]/.test(line.text))) {
    throw new Error(`Footnote or HTML residue remains in chapter ${chapterNumber}`)
  }

  const currentGraph = JSON.parse(fs.readFileSync(chapterPath, 'utf8'))
  const startNode = structuredClone(currentGraph.Nodes.find((node) => node.Index === 1))
  const transitionNode = structuredClone(currentGraph.Nodes.find((node) => node.Index === 2))
  if (!startNode || !transitionNode) {
    throw new Error(`Chapter ${chapterNumber} is missing its start or transition node`)
  }

  transitionNode.Outputs = { Next: [10] }
  const graph = {
    Version: 1,
    StartNodeIndex: 1,
    Nodes: [
      startNode,
      transitionNode,
      ...lines.map((line, lineIndex) => chapterNode(chapterNumber, line, lineIndex, lines.length)),
    ],
  }
  fs.writeFileSync(chapterPath, JSON.stringify(graph))

  const characterCount = lines.reduce((sum, line) => sum + Array.from(line.text).length, 0)
  const excerptCharacters = Array.from(lines[0].text)
  const excerpt = excerptCharacters.length > 180
    ? `${excerptCharacters.slice(0, 180).join('')}…`
    : excerptCharacters.join('')
  const catalogEntry = catalog.chapters.find((chapter) => chapter.number === chapterNumber)
  if (!catalogEntry) throw new Error(`Catalog entry ${chapterNumber} is missing`)

  catalogEntry.title = entry.title
  catalogEntry.paragraphCount = lines.length
  catalogEntry.characterCount = characterCount
  catalogEntry.excerpt = excerpt

  totalCharacters += characterCount
  totalParagraphs += lines.length
  poemLines += lines.filter((line) => line.speaker === '诗赞').length
  chapterSummaries.push({
    chapterNumber,
    sourceFile,
    title: entry.title,
    paragraphCount: lines.length,
    characterCount,
  })
}

fs.writeFileSync(catalogPath, `${JSON.stringify(catalog, null, 2)}\n`)

console.log(JSON.stringify({
  source: epubDirectory,
  target: targetDirectory,
  chapterCount: chapterSummaries.length,
  paragraphCount: totalParagraphs,
  poemLineCount: poemLines,
  characterCount: totalCharacters,
  firstChapter: chapterSummaries[0],
  lastChapter: chapterSummaries.at(-1),
}, null, 2))
