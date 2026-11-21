'use strict'

// Keep the data model independently testable without mounting a renderer or
// teaching Node how to execute the entire Vue application.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require(path.resolve(process.cwd(), 'node_modules/typescript'))

const frontend = path.resolve(process.env.COMIC_MANAGE_FIELD_SRC || path.resolve(__dirname, '../../frontend/src'))
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

const { activeLibraryConditions, matchesLibraryConditions, sortLibraryEntries, nextLibrarySort, validLibraryCondition } = loadTypeScript(path.join(frontend, 'library-fields.ts'))
const { buildLibraryEntries, flattenLibraryRows, visibleSelectableIds } = loadTypeScript(path.join(frontend, 'library-groups.ts'))

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

const query = (field, operator, value = '', upper = '') => ({ field, operator, value, upper })
const testFields = (books, ...conditions) => books.filter(book => matchesLibraryConditions(book, activeLibraryConditions(conditions))).map(book => book.id)
const entriesFor = books => buildLibraryEntries(books, books)
const sortedIds = (books, key, direction) => sortLibraryEntries(entriesFor(books), { key, direction }).map(row => row.comic.id)

test('AND conditions apply for exact and normalized per-value searches', () => {
  const books = [comic('a', { metadata: { title: 'Ａｒｃ １', authors: ['Zeta', '作者甲'], tags: ['测试'] } }),
    comic('b', { metadata: { title: 'Arc 2', authors: ['zeta'], tags: ['后记'] } }),
    comic('c', { metadata: { title: 'Arc 3', authors: ['Zeta Jr.'], tags: ['测试'] } })]
  assert.deepEqual(testFields(books, query('title','contains','arc'), query('authors','eq','zeta'), query('tags','eq','测试')), ['a'])
  assert.deepEqual(testFields(books, query('authors','neq','zeta')), ['c'])
})

test('aliases and generated classification tags can be filtered', () => {
  const books = [comic('a', { metadata: { title: '静かな春', chinese_title: '安静的春', release_type: '同人发行', series: '春', localized_series: '春日系列' } }), comic('b')]
  assert.deepEqual(testFields(books, query('title','contains','安静'), query('series','eq','春日系列'), query('tags','contains','发行性质:同人')), ['a'])
})

test('numeric operations are inclusive, normalize full-width digits and reject bad ranges', () => {
  const books = [
    comic('a',{ page_count: 12, metadata: { year: '2021' } }), comic('b',{ page_count: 129, metadata: { year: '2020' } }),
    comic('c',{ page_count: 300, metadata: { year: '2023' } }), comic('d',{ page_count: 0, metadata: { year: '' } }),
  ]
  assert.deepEqual(testFields(books, query('pages','between','１２','１２９'), query('year','gte','2020')), ['a','b'])
  assert.deepEqual(testFields(books, query('pages','lte','129'), query('year','eq','2020')), ['b'])
  assert.deepEqual(testFields(books, query('year','missing')), ['d'])
  assert.equal(validLibraryCondition(query('pages','between','17','3')), false)
  assert.equal(validLibraryCondition(query('year','eq','1e3')), false)
  assert.equal(validLibraryCondition(query('year','eq','10000')), false)
  assert.equal(validLibraryCondition(query('status','contains','draft')), false)
})

test('unknown rating may be filtered by value or as unconfirmed, blanks do not satisfy neq', () => {
  const books = [comic('a'), comic('b', { metadata: { age_rating:'Everyone', language:'ja' } })]
  assert.deepEqual(testFields(books, query('age_rating','missing')), ['a'])
  assert.deepEqual(testFields(books, query('age_rating','eq','Unknown')), ['a'])
  assert.deepEqual(testFields(books, query('age_rating','present')), ['b'])
  assert.deepEqual(testFields(books, query('language','neq','ja')), [])
})

test('sort on headers cycles ascending, descending, then existing order with stable ties', () => {
  const asc = nextLibrarySort(null, 'pages')
  const desc = nextLibrarySort(asc, 'pages')
  assert.deepEqual(asc, {key:'pages',direction:'asc'})
  assert.deepEqual(desc, {key:'pages',direction:'desc'})
  assert.equal(nextLibrarySort(desc, 'pages'), null)
  assert.deepEqual(nextLibrarySort(desc, 'title'), {key:'title',direction:'asc'})
  const books = [
    comic('a', { page_count: 11, metadata: { title:'卷 11' } }), comic('b', { page_count: 2, metadata: { title:'卷 2' } }),
    comic('c', { page_count: 2, metadata: { title:'卷 2' } }), comic('d', { page_count: 0, metadata: { title:'', original_title:'' } }),
  ]
  assert.deepEqual(sortedIds(books, 'title', 'asc'), ['b','c','a','d'])
  assert.deepEqual(sortedIds(books, 'pages', 'desc'), ['a','b','c','d'])
  assert.deepEqual(books.map(book=>book.id), ['a','b','c','d'])
})

test('dates and state sort with natural missing-last behavior', () => {
  const books = [
    comic('a', { status:'approved', metadata:{year:'2022',month:'01',day:'12'} }),
    comic('b', { status:'packaged', metadata:{year:'2022',month:'12',day:'01'} }),
    comic('c', { status:'draft', metadata:{year:'2020',month:'11'} }),
    comic('d', { status:'draft' }),
  ]
  assert.deepEqual(sortedIds(books, 'releaseDate', 'desc'), ['b','a','c','d'])
  assert.deepEqual(sortedIds(books, 'status', 'asc'), ['c','d','a','b'])
})

test('group parent remains with all visible children sorted, selection never includes header', () => {
  const all = [
    comic('group-large', { page_count: 31, metadata:{work_group:'双版本',text_version:'有文字'} }),
    comic('single', { page_count: 20 }),
    comic('group-small', { page_count: 14, metadata:{work_group:'双版本',text_version:'无文字'} }),
  ]
  const entries = entriesFor(all)
  const groupKey = entries[0].key
  const sorted = sortLibraryEntries(entries, { key:'pages', direction:'asc' })
  assert.equal(sorted[0].kind, 'group')
  assert.deepEqual(sorted[0].visibleMembers.map(book => book.id), ['group-small','group-large'])
  assert.deepEqual(visibleSelectableIds(flattenLibraryRows(sorted, new Set()), () => true), ['single'])
  assert.deepEqual(visibleSelectableIds(flattenLibraryRows(sorted, new Set([groupKey])), () => true), ['group-small','group-large','single'])
  assert.deepEqual(entries[0].visibleMembers.map(book => book.id), ['group-large','group-small'])
  const filtered = buildLibraryEntries([all[0], all[1]], all)
  const [first, second] = sortLibraryEntries(filtered, { key:'pages', direction:'asc' })
  assert.equal(first.comic.id, 'single')
  assert.deepEqual(second.visibleMembers.map(book => book.id), ['group-large'])
  assert.equal(second.members.length, 2)
})
