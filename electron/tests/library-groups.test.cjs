'use strict'

// Keep the data model independently testable without mounting a renderer or
// teaching Node how to execute the entire Vue application.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require(path.resolve(process.cwd(), 'node_modules/typescript'))

const sourceFile = process.env.COMIC_MANAGE_GROUPS_MODULE || path.resolve(__dirname, '../../frontend/src/library-groups.ts')
const loaded = new Map()
function loadTypeScript(file) {
  file = path.resolve(file)
  if (loaded.has(file)) return loaded.get(file).exports
  const module = { exports: {} }
  loaded.set(file, module)
  const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: file,
  }).outputText
  const localRequire = request => {
    if (request.startsWith('.')) {
      const candidate = path.resolve(path.dirname(file), request + '.ts')
      if (fs.existsSync(candidate)) return loadTypeScript(candidate)
    }
    return require(request)
  }
  new Function('require', 'module', 'exports', '__filename', '__dirname', compiled)(localRequire, module, module.exports, file, path.dirname(file))
  return module.exports
}

const {
  libraryGroupKey, buildLibraryGroups, flattenLibraryRows,
  visibleSelectableIds, updateVisibleSelection, toggleComicSelection,
} = loadTypeScript(sourceFile)

function comic(id, options = {}) {
  const metadata = {
    title: id, original_title: id, chinese_title: '', series: '', localized_series: '', localized_series_label: '',
    collection_mode: 'oneshot', number: '', count: '', volume: '', work_group: '', text_version: '',
    authors: [], pencillers: [], publisher: '', translator: '', genre: '漫画', tags: ['日常'],
    original_works: [], characters: [], release_type: '', publication_format: '', release_methods: [], release_platforms: [],
    color: '', year: '', month: '', day: '', language: '', age_rating: 'Unknown', summary: '', web: [],
    reading_direction: '', manga: 'Unknown', black_and_white: 'Unknown', full_color: false,
    uncensored: false, translated: false, cover_index: 0, original_xml: '',
  }
  return {
    id, source: 'C:/synthetic/' + id, kind: 'folder', fingerprint: id, page_count: 2,
    status: 'draft', output: '', warning: '', updated_at: '2026-11-17T00:00:00Z',
    ...options, metadata: { ...metadata, ...options.metadata },
  }
}

function pair(group = '共通作品（原文作者）') {
  return [
    comic('with-text', { metadata: { title: '共通作品', work_group: group, text_version: '有文字', authors: ['作者甲'] } }),
    comic('without-text', { metadata: { title: '共通作品', work_group: group, text_version: '无文字', authors: ['作者甲'] }, page_count: 3 }),
  ]
}

function remote(id, serverUrl, libraryId, options = {}) {
  return comic(id, {
    ...options, kind: 'komga',
    remote: { serverUrl, libraryId, scope: serverUrl + '/' + libraryId, bookId: id, seriesId: id + '-series', ...options.remote },
  })
}

function deepFreeze(value) {
  if (value && typeof value === 'object') {
    Object.freeze(value)
    Object.values(value).forEach(deepFreeze)
  }
  return value
}

const ids = rows => rows.filter(row => row.kind === 'comic').map(row => row.comic.id)

test('work groups use explicit exact names rather than title, letter case or compatibility normalization', () => {
  assert.equal(libraryGroupKey(comic('blank')), null)
  assert.equal(libraryGroupKey(comic('spaces', { metadata: { work_group: '   ' } })), null)
  const key = name => libraryGroupKey(comic('source', { metadata: { work_group: name } }))
  assert.equal(key('  Group Ａ  '), key('Group Ａ'))
  assert.notEqual(key('Group A'), key('Group a'))
  assert.notEqual(key('Group A'), key('Group Ａ'))
  assert.notEqual(key('Group A'), key('Group  A'))
  const sameTitles = [comic('a', { metadata: { title: '相同标题' } }), comic('b', { metadata: { title: '相同标题' } })]
  assert.equal(buildLibraryGroups(sameTitles, sameTitles).every(entry => entry.kind === 'comic'), true)
})

test('local sources group together while remote server and actual library scopes remain separate', () => {
  const localFolder = comic('local-folder', { metadata: { work_group: '作品组' } })
  const localArchive = comic('local-cbz', { kind: 'cbz', metadata: { work_group: '作品组' } })
  assert.equal(libraryGroupKey(localFolder), libraryGroupKey(localArchive))
  const a = remote('a', 'https://one.test/komga', 'library-A', { metadata: { work_group: '作品组' } })
  const b = remote('b', 'https://one.test/komga', 'library-A', { metadata: { work_group: '作品组' } })
  const otherLibrary = remote('c', 'https://one.test/komga', 'library-B', { metadata: { work_group: '作品组' } })
  const otherServer = remote('d', 'https://two.test/komga', 'library-A', { metadata: { work_group: '作品组' } })
  assert.equal(libraryGroupKey(a), libraryGroupKey(b))
  for (const other of [localFolder, otherLibrary, otherServer]) assert.notEqual(libraryGroupKey(a), libraryGroupKey(other))
  const all = [localFolder, a, localArchive, b, otherLibrary, otherServer]
  const entries = buildLibraryGroups(all, all)
  assert.equal(entries.filter(row => row.kind === 'group').length, 2)
  assert.equal(entries.filter(row => row.kind === 'comic').length, 2)
})

test('remote records without a complete scope never merge with an unrelated cached record', () => {
  const missing = [comic('a', { kind: 'komga', metadata: { work_group: '作品组' } }), comic('b', { kind: 'komga', metadata: { work_group: '作品组' } })]
  assert.notEqual(libraryGroupKey(missing[0]), libraryGroupKey(missing[1]))
  assert.equal(buildLibraryGroups(missing, missing).every(row => row.kind === 'comic'), true)
})

test('a real pair defaults to one collapsed header and retains independent records when expanded', () => {
  const books = pair()
  const entries = buildLibraryGroups(books, books)
  assert.equal(entries.length, 1)
  const group = entries[0]
  assert.equal(group.kind, 'group')
  assert.deepEqual(group.members.map(book => book.id).sort(), ['with-text', 'without-text'])
  assert.equal(flattenLibraryRows(entries, new Set()).length, 1)
  assert.deepEqual(visibleSelectableIds(flattenLibraryRows(entries, new Set()), () => true), [])
  const expanded = flattenLibraryRows(entries, new Set([group.key]))
  assert.equal(expanded[0].kind, 'group')
  assert.deepEqual(ids(expanded).sort(), ['with-text', 'without-text'])
  assert.deepEqual(visibleSelectableIds(expanded, () => true).sort(), ['with-text', 'without-text'])
  assert.notEqual(books[0].page_count, books[1].page_count)
})

test('a singleton named group stays a normal record and does not gain a meaningless empty wrapper', () => {
  const books = [comic('only', { metadata: { work_group: '作品组', text_version: '有文字' } })]
  const entries = buildLibraryGroups(books, books)
  assert.equal(entries.length, 1)
  assert.equal(entries[0].kind, 'comic')
  assert.deepEqual(ids(flattenLibraryRows(entries, new Set())), ['only'])
})

test('filtering to one edition keeps the same group identity and never pulls the other edition into selection', () => {
  const books = pair()
  books[1].status = 'approved'
  books[1].metadata.authors = ['另一位责任者']
  const full = buildLibraryGroups(books, books)[0]
  const entries = buildLibraryGroups([books[0]], books)
  assert.equal(entries.length, 1)
  assert.equal(entries[0].kind, 'group')
  assert.equal(entries[0].key, full.key)
  assert.equal(entries[0].members.length, 2)
  assert.equal(entries[0].visibleMembers.length, 1)
  const revealed = flattenLibraryRows(entries, new Set([entries[0].key]))
  assert.deepEqual(ids(revealed), ['with-text'])
  assert.deepEqual(visibleSelectableIds(revealed, () => true), ['with-text'])
  assert.deepEqual(updateVisibleSelection([], visibleSelectableIds(revealed, () => true), true), ['with-text'])
  assert.deepEqual(buildLibraryGroups([], books), [])
})

test('visible-only select all respects collapsed groups and read-only edition predicates', () => {
  const books = [...pair(), comic('standalone')]
  const entries = buildLibraryGroups(books, books)
  const group = entries.find(row => row.kind === 'group')
  assert.deepEqual(visibleSelectableIds(flattenLibraryRows(entries, new Set()), () => true), ['standalone'])
  const expanded = flattenLibraryRows(entries, new Set([group.key]))
  assert.deepEqual(visibleSelectableIds(expanded, book => book.id !== 'without-text').sort(), ['standalone', 'with-text'])
  assert.equal(visibleSelectableIds(expanded, () => false).length, 0)
})

test('collapse and filtering preserve explicit checked IDs without selecting headers or hidden siblings', () => {
  const books = pair()
  const entries = buildLibraryGroups(books, books)
  const checked = ['with-text']
  const collapsedVisible = visibleSelectableIds(flattenLibraryRows(entries, new Set()), () => true)
  assert.deepEqual(updateVisibleSelection(checked, collapsedVisible, true), ['with-text'])
  assert.deepEqual(updateVisibleSelection(checked, collapsedVisible, false), ['with-text'])
  const visibleFirst = visibleSelectableIds(flattenLibraryRows(buildLibraryGroups([books[0]], books), new Set([entries[0].key])), () => true)
  assert.deepEqual(updateVisibleSelection(['without-text'], visibleFirst, true).sort(), ['with-text', 'without-text'])
  assert.deepEqual(updateVisibleSelection(['without-text', 'with-text'], visibleFirst, false), ['without-text'])
})

test('selection operations are immutable and idempotent even with repeated IDs', () => {
  const checked = Object.freeze(['hidden', 'visible'])
  const visible = Object.freeze(['visible', 'other', 'other'])
  assert.deepEqual(updateVisibleSelection(checked, visible, true), ['hidden', 'visible', 'other'])
  assert.deepEqual(updateVisibleSelection(checked, visible, false), ['hidden'])
  assert.deepEqual(toggleComicSelection(checked, 'new', true), ['hidden', 'visible', 'new'])
  assert.deepEqual(toggleComicSelection(checked, 'visible', true), ['hidden', 'visible'])
  assert.deepEqual(toggleComicSelection(checked, 'visible', false), ['hidden'])
  assert.deepEqual(checked, ['hidden', 'visible'])
})

test('refreshes can replace record objects and statuses without changing group keys or editing data', () => {
  const original = pair()
  const before = JSON.stringify(original)
  const frozen = deepFreeze(original)
  const entries = buildLibraryGroups(frozen, frozen)
  const expandedKeys = new Set([entries[0].key])
  flattenLibraryRows(entries, expandedKeys)
  visibleSelectableIds(flattenLibraryRows(entries, expandedKeys), () => true)
  const refreshed = JSON.parse(before)
  refreshed[0].status = 'approved'
  refreshed[0].metadata.summary = '经人工审核的独立修改'
  assert.equal(buildLibraryGroups(refreshed, refreshed)[0].key, entries[0].key)
  assert.equal(JSON.stringify(original), before)
  assert.deepEqual([...expandedKeys], [entries[0].key])
})

test('when a peer is removed from the active collection the remaining edition becomes a standalone row', () => {
  const books = pair()
  const existingKey = buildLibraryGroups(books, books)[0].key
  const remaining = buildLibraryGroups([books[0]], [books[0]])
  assert.equal(remaining[0].kind, 'comic')
  assert.deepEqual(ids(flattenLibraryRows(remaining, new Set([existingKey]))), ['with-text'])
})
