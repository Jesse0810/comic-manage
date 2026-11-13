'use strict'

// Integration test of our own Electron window, real preload and real file service.
// Native pickers select synthetic fixtures; the IPC/file-processing path is unchanged.
const { app, dialog } = require('electron')
const { createApplication } = require('./main.cjs')
app.on('window-all-closed', () => {}) // Keep the test alive until service shutdown is verified.
const fs = require('node:fs/promises')
const path = require('node:path')
const assert = require('node:assert/strict')
const { createKomgaFixture } = require('./tests/komga-http-fixture.cjs')
const { deflateSync } = require('node:zlib')

const root = path.resolve(__dirname, '..')
let desktop
let komgaFixture
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))
function crc(buffer) {
  let value = 0xffffffff
  for (const byte of buffer) {
    value ^= byte
    for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ (0xedb88320 & -(value & 1))
  }
  return (value ^ 0xffffffff) >>> 0
}
function png() {
  const width = 360, height = 500
  const raw = Buffer.alloc((width * 3 + 1) * height)
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const offset = y * (width * 3 + 1) + 1 + x * 3
    const panel = x > 25 && x < 335 && ((y > 70 && y < 220) || (y > 245 && y < 450))
    const color = panel ? [153, 182, 164] : [233, 223, 198]
    raw.set(color, offset)
  }
  const chunk = (name, value) => {
    const type = Buffer.from(name), length = Buffer.alloc(4), digest = Buffer.alloc(4)
    length.writeUInt32BE(value.length); digest.writeUInt32BE(crc(Buffer.concat([type, value])))
    return Buffer.concat([length, type, value, digest])
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 2
  return Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), chunk('IHDR', header), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))])
}

async function run() {
  const build = path.join(root, 'build')
  await fs.mkdir(build, { recursive: true })
  const fixture = await fs.mkdtemp(path.join(build, 'desktop-smoke-'))
  const dataDir = path.join(fixture, 'data')
  const sourceRoot = path.join(fixture, '漫画源')
  const source = path.join(sourceRoot, '[2019.10] [pink-noise (みずいろめがね)] 春の旅 [中国翻訳] [无修正] [全彩]')
  const secondSource = path.join(sourceRoot, '[作者乙] 夏の旅 [商业志]')
  const excludedSource = path.join(sourceRoot, '[作者丙] 未选作品')
  const output = path.join(fixture, '成品')
  const screenshots = process.env.COMIC_MANAGE_SCREENSHOTS || path.join(fixture, 'screenshots')
  await Promise.all([fs.mkdir(source, { recursive: true }), fs.mkdir(output, { recursive: true }), fs.mkdir(screenshots, { recursive: true }), fs.mkdir(path.join(dataDir, 'electron'), { recursive: true })])
  const page = png()
  for (let i = 1; i <= 3; i++) await fs.writeFile(path.join(source, `${i}.png`), page)
  for (const folder of [secondSource, excludedSource]) {
    await fs.mkdir(folder, { recursive: true })
    await fs.writeFile(path.join(folder, '1.png'), page)
  }
  app.setPath('userData', path.join(dataDir, 'electron'))
  await app.whenReady()
  const syntheticKey = 'synthetic-komga-test-key'
  komgaFixture = await createKomgaFixture(syntheticKey)
  let picking = sourceRoot
  dialog.showOpenDialog = async () => ({ canceled: false, filePaths: Array.isArray(picking) ? picking : [picking] })
  dialog.showSaveDialog = async (_window, options) => ({ canceled: false, filePath: path.join(fixture, options.title.includes('Komga') ? 'komga_notable_entities_manifest.json' : 'backup.sqlite3') })
  desktop = await createApplication({ dataDir, show: process.env.COMIC_MANAGE_TEST_SHOW === '1' })
  const window = desktop.window
  window.webContents.setBackgroundThrottling(false)
  const javascriptErrors = []
  window.webContents.on('console-message', (_event, level, message) => { if (level === 3) javascriptErrors.push(message) })
  const evaluate = async code => {
    try { return await window.webContents.executeJavaScript(code, true) }
    catch (error) { throw new Error(`${error.message}\nScript: ${code}\nConsole: ${javascriptErrors.join('\n')}\nBody: ${await window.webContents.executeJavaScript('document.body.innerText')}`, { cause: error }) }
  }
  const waitFor = async (expression, label, timeout = 20000) => {
    const deadline = Date.now() + timeout
    while (Date.now() < deadline) {
      if (window.isDestroyed()) throw new Error(`Window closed while waiting for ${label}`)
      if (await evaluate(expression)) return
      await delay(50)
    }
    throw new Error(`Timed out: ${label}\n${await evaluate('document.body.innerText')}`)
  }
  const click = async text => {
    // Page loads and service requests may outlive a render on slower CI machines.
    await waitFor(`[...document.querySelectorAll('button')].some(b => b.getClientRects().length && !b.disabled && b.textContent.trim() === ${JSON.stringify(text)})`, `enabled button ${text}`)
    await evaluate(`(() => { const item = [...document.querySelectorAll('button')].find(b => b.getClientRects().length && !b.disabled && b.textContent.trim() === ${JSON.stringify(text)}); if (!item) throw new Error('Button missing: '+${JSON.stringify(text)}); item.click() })()`)
  }
  const capture = async name => {
    await delay(250) // Let transitions and Chromium paint catch up with Vue state.
    await fs.writeFile(path.join(screenshots, name), (await window.webContents.capturePage()).toPNG())
  }
  const dismissResult = async () => {
    await click('知道了')
    await waitFor("![...document.querySelectorAll('.el-overlay')].some(e => e.getClientRects().length && getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden')", 'result dialog dismissed')
  }
  const metadata = { collection_mode: 'series', localized_series_label: 'zh-Hans', title: '春日旅途', series: '春の旅シリーズ', localized_series: '春日旅途系列', number: '1.5', count: '3', volume: '1', authors: ['森野'], pencillers: ['独立画师'], publisher: 'pink-noise', translator: '示例汉化组', genre: '漫画', tags: ['日常'], original_works: ['东方Project'], characters: ['博丽灵梦', '雾雨魔理沙'], release_type: '同人发行', publication_format: '独立作品', uncensored: true, color: '黑白', release_methods: [], release_platforms: [], reading_direction: '', manga: 'YesAndRightToLeft', language: 'zh-Hans', age_rating: 'R18+', summary: '用于验证新界面的合成样本。', year: '2024', month: '2', day: '29', web: ['https://example.com/comic/A,B', 'https://example.org/book'], cover_index: 0, original_xml: '' }
  const call = (method, params = {}) => evaluate(`window.comic.invoke(${JSON.stringify(method)}, ${JSON.stringify(params)})`)
  const setInput = (placeholder, value) => evaluate(`(() => { const item = [...document.querySelectorAll('input')].find(x=>x.placeholder === ${JSON.stringify(placeholder)}); if(!item) throw new Error('Input missing'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(item,${JSON.stringify(value)}); item.dispatchEvent(new Event('input',{bubbles:true})); })()`)
  const toggle = testId => evaluate(`document.querySelector('[data-testid="'+${JSON.stringify(testId)}+'"]').click()`)
  const setField = (testId, value) => evaluate(`(() => { const root=document.querySelector('[data-testid="'+${JSON.stringify(testId)}+'"]'); const item=root.matches('input,textarea')?root:root.querySelector('input,textarea'); const prototype=item.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(prototype,'value').set.call(item,${JSON.stringify(value)}); item.dispatchEvent(new Event('input',{bubbles:true})); })()`)
  const selectValue = async (testId, value) => {
    await evaluate(`(() => { const field=document.querySelector('[data-testid="'+${JSON.stringify(testId)}+'"] input'); field.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(field,${JSON.stringify(value)}); field.dispatchEvent(new Event('input',{bubbles:true})); field.click(); })()`)
    await waitFor(`[...document.querySelectorAll('.el-select-dropdown__item')].some(item=>item.getClientRects().length && item.textContent.trim()===${JSON.stringify(value)})`, `select option ${value}`)
    await evaluate(`[...document.querySelectorAll('.el-select-dropdown__item')].find(item=>item.getClientRects().length && item.textContent.trim()===${JSON.stringify(value)}).click()`)
    await evaluate(`document.querySelector('.el-dialog__header').click()`)
  }
  const folderCheckbox = folder => evaluate(`(() => { const item=[...document.querySelectorAll('.folder-tree-node')].find(item=>item.title===${JSON.stringify(folder)}); if(!item) throw new Error('Missing folder'); item.closest('.el-tree-node__content').querySelector('.el-checkbox').click(); })()`)
  const chooseOption = async (testId, label) => {
    await evaluate(`(() => { const item=document.querySelector('[data-testid="'+${JSON.stringify(testId)}+'"] .el-select__wrapper'); item.scrollIntoView({block:'center'}); item.click(); })()`)
    await waitFor(`[...document.querySelectorAll('.el-select-dropdown__item')].some(item=>item.getClientRects().length && item.textContent.trim()===${JSON.stringify(label)})`, `option ${label}`)
    await evaluate(`[...document.querySelectorAll('.el-select-dropdown__item')].find(item=>item.getClientRects().length && item.textContent.trim()===${JSON.stringify(label)}).click()`)
  }
  const chooseMany = async (testId, labels) => {
    await evaluate(`(() => { const item=document.querySelector('[data-testid="'+${JSON.stringify(testId)}+'"] .el-select__wrapper'); item.scrollIntoView({block:'center'}); item.click() })()`)
    for (const label of labels) {
      await waitFor(`[...document.querySelectorAll('.el-select-dropdown__item')].some(item=>item.getClientRects().length && item.textContent.trim()===${JSON.stringify(label)})`, 'multi option ' + label)
      await evaluate(`[...document.querySelectorAll('.el-select-dropdown__item')].find(item=>item.getClientRects().length && item.textContent.trim()===${JSON.stringify(label)}).click()`)
    }
    await evaluate("document.body.dispatchEvent(new MouseEvent('mousedown',{bubbles:true})); document.body.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}))")
    await waitFor("![...document.querySelectorAll('.el-select__popper')].some(item=>item.getClientRects().length)", 'multi dropdown closed')
  }

  await waitFor("document.body.innerText.includes('从第一本漫画开始')", 'empty state')
  assert.equal(await evaluate("document.querySelectorAll('[data-testid=komga-settings]').length"), 1)
  assert.equal(await evaluate("document.querySelector('[data-testid=komga-pull]')"), null)
  assert.equal(await evaluate('typeof window.require'), 'undefined')
  assert.equal(await evaluate('typeof window.process'), 'undefined')
  assert.equal(await evaluate("window.comic.invoke('arbitrary-shell',{}).then(()=>false,()=>true)"), true)
  await capture('ComicManage-Electron-empty.png')
  await click('添加漫画目录')
  await click('选择多个文件夹')
  await waitFor("document.querySelectorAll('.folder-tree-node').length === 1", 'directory selection')
  await folderCheckbox(sourceRoot) // Choose two child folders, leaving their sibling unchecked.
  await evaluate("document.querySelector('[data-testid=import-tree] .el-tree-node__expand-icon').click()")
  await waitFor("document.querySelectorAll('.folder-tree-node').length === 4", 'lazy child folders')
  await folderCheckbox(source)
  await folderCheckbox(secondSource)
  await click('读取并预览')
  await waitFor("document.body.innerText.includes('发现 2 本漫画')", 'import preview')
  assert.equal((await call('bootstrap')).comics.length, 0, 'Preview must not mutate the catalog')
  assert.equal(await evaluate("document.querySelector('[data-testid=import-table]').textContent.includes('未知汉化')"), true)
  await toggle('import-enable-authors')
  await selectValue('import-authors', '统一作者')
  await toggle('import-enable-genre')
  await chooseOption('import-genre', '漫画')
  await capture('ComicManage-Electron-import.png')
  window.setSize(1180, 800)
  await delay(200)
  assert.equal(await evaluate("document.querySelector('.import-dialog .el-dialog__footer').getBoundingClientRect().bottom <= window.innerHeight"), true, 'Import confirmation stays visible at compact size')
  await capture('ComicManage-Electron-import-compact.png')
  window.setSize(1500, 980)
  await delay(100)
  await click('导入选中 2 本')
  await waitFor("document.body.innerText.includes('已导入 2 本作品')", 'selected import')
  await dismissResult()
  let catalog = await call('bootstrap')
  assert.equal(catalog.comics.length, 2)
  const imported = catalog.comics.find(book => book.metadata.title === '春の旅')
  const id = imported.id
  const secondId = catalog.comics.find(book => book.id !== id).id
  assert.deepEqual(imported.metadata.authors, ['统一作者'])
  assert.equal(imported.metadata.genre, '漫画')
  assert.equal(imported.metadata.publisher, 'pink-noise')
  assert.equal(imported.metadata.translator, '未知汉化')
  assert.equal(imported.metadata.year, '2019')
  assert.equal(imported.metadata.month, '10')
  assert.equal(imported.metadata.color, '全彩')
  await call('save', { id, metadata })
  await click('重新扫描')
  await waitFor("document.body.innerText.includes('已扫描 2 本作品')", 'rescan')
  await dismissResult()
  await waitFor("!document.querySelector('.table-disabled')", 'scan task released')
  await evaluate("[...document.querySelectorAll('.comic-cell')].find(item=>item.textContent.includes('春日旅途')).click()")
  await waitFor("[...document.querySelectorAll('input')].some(item=>item.dataset.testid === 'title' && item.value === '春日旅途') || document.querySelector('[data-testid=title] input')?.value === '春日旅途'", 'details')
  await toggle('expand-metadata')
  await setField('title', '春日旅途·校订')
  await setField('series', '春の旅シリーズ改訂')
  await setField('localized-series', '春日旅途系列校订')
  await setField('number', '2.5')
  await setField('count', '4')
  await setField('volume', '7')
  await chooseOption('color', '部分彩色')
  await chooseMany('release-methods', ['会员订阅', '单次购买'])
  await chooseMany('release-platforms', ['Fantia', 'pixivFANBOX'])
  await chooseOption('reading-direction', '纵向')
  await chooseOption('day', '28 日')
  await setField('web', 'https://example.com/comic/A,B\nhttps://example.org/book')
  await chooseOption('age-rating', '全年龄')
  assert.equal(await evaluate("!!document.querySelector('[data-testid=correction-field]')"), false)
  await chooseOption('age-rating', 'R18')
  assert.equal(await evaluate("document.querySelector('[data-testid=uncensored] input').checked"), true, 'Changing age keeps the unchecked historical correction flag')
  assert.equal(await evaluate("document.querySelector('.custom-tag-preview').textContent.includes('修正状态:无修正')"), true)
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '未保存'", 'dirty status')
  await click('保存草稿')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '待审核'", 'saved draft')
  catalog = await call('bootstrap')
  assert.equal(catalog.comics.find(book => book.id === id).metadata.title, '春日旅途·校订')
  assert.equal(catalog.comics.find(book => book.id === id).metadata.number, '2.5')
  await evaluate("document.querySelector('.review-checkbox').click()")
  await click('审核通过')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '已审核'", 'approval')
  await waitFor("document.querySelector('.mini-cover img')?.complete", 'image preview')
  await capture('ComicManage-Electron-details.png')
  await evaluate("document.querySelector('.metadata-scroll').scrollTop = 0")
  await capture('ComicManage-Electron-metadata.png')
  await evaluate("[...document.querySelectorAll('.el-tabs__item')].find(x=>x.textContent.includes('页面预览')).click()")
  await waitFor("document.querySelector('.large-page img')?.complete", 'large preview')
  await evaluate("document.querySelector('[aria-label=下一页]').click()")
  await waitFor("document.querySelector('.page-counter input')?.value === '2'", 'page navigation')
  await click('将此页设为封面')
  await evaluate("document.querySelector('.review-checkbox').click()")
  await evaluate("[...document.querySelectorAll('.el-tabs__item')].find(x=>x.textContent === '基本资料').click()")
  await setField('title', '春日旅途·封面校订')
  await delay(100)
  assert.equal(await evaluate("document.querySelector('.review-checkbox input').checked"), false, 'Every metadata edit revokes the review checkbox')
  await click('保存草稿')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '待审核'", 'cover draft')
  await evaluate("document.querySelector('.review-checkbox').click()")
  await click('审核通过')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '已审核'", 'cover reapproval')
  await evaluate("[...document.querySelectorAll('.el-tabs__item')].find(x=>x.textContent === 'ComicInfo.xml').click()")
  await waitFor("document.querySelector('.xml-tab pre')?.textContent.includes('Image=\"1\"')", 'ComicInfo cover index')
  const exportedXml = await evaluate("document.querySelector('.xml-tab pre').textContent")
  for (const fragment of ['<Title>春日旅途·封面校订</Title>', '<Series>春の旅シリーズ改訂</Series>', '<Number>2.5</Number>', '<Volume>7</Volume>', '<Characters>博丽灵梦, 雾雨魔理沙</Characters>', '<Count>4</Count>', '<Day>28</Day>', '<Penciller>独立画师</Penciller>', '<AgeRating>R18+</AgeRating>', '<Manga>YesAndRightToLeft</Manga>', '<PageCount>3</PageCount>', '角色:博丽灵梦', '角色:雾雨魔理沙', '原作:东方Project', '发行性质:同人发行', '出版形态:独立作品', '色彩:部分彩色', '发布方式:会员订阅', '发布方式:单次购买', '发布平台:Fantia', '发布平台:pixivFANBOX', '修正状态:无修正', '<Web>https://example.com/comic/A,B https://example.org/book</Web>']) assert.ok(exportedXml.includes(fragment), fragment)
  assert.equal(/<Tags>[^<]*(?:^|, )(?:汉化|全彩|无修正)(?:,|<)/.test(exportedXml), false)
  assert.equal(exportedXml.includes('<BlackAndWhite>') || exportedXml.includes('<LocalizedSeries>') || exportedXml.includes('<原作>') || exportedXml.includes('<色彩>'), false)
  picking = output
  await click('选择')
  await click('生成 CBZ')
  await waitFor("document.body.innerText.includes('即将生成 1 本漫画')", 'export plan')
  await click('确认生成并校验')
  await waitFor("document.body.innerText.includes('1 本已生成并通过校验')", 'verified CBZ export')
  await dismissResult()
  catalog = await call('bootstrap')
  const packaged = catalog.comics.find(book => book.id === id)
  assert.equal(packaged.status, 'packaged')
  assert.equal(packaged.metadata.cover_index, 1)
  assert.equal((await fs.readFile(path.join(source, '1.png'))).equals(page), true)
  assert.equal((await fs.stat(packaged.output)).isFile(), true)
  await evaluate("[...document.querySelectorAll('.el-tabs__item')].find(x=>x.textContent === 'Komga 补充资料').click()")
  await waitFor("document.querySelector('[data-testid=komga-payload]')?.textContent.includes('春日旅途系列校订')", 'Komga supplementary preview')
  assert.equal(await evaluate("document.querySelector('[data-testid=komga-payload]').textContent.includes('notableEntities')"), true)
  await call('export_komga', { ids: [id], path: path.join(fixture, 'komga_notable_entities_manifest.json') })
  const manifest = JSON.parse(await fs.readFile(path.join(fixture, 'komga_notable_entities_manifest.json'), 'utf8'))
  assert.equal(manifest.books.length, 1)
  assert.equal(manifest.series[0].alternateTitles[0].title, '春日旅途系列校订')
  assert.ok(manifest.books[0].notableEntities.some(entity => entity.kind === '原作' && entity.name === '东方Project'))
  const relativeOutput = path.basename(path.dirname(packaged.output)) + '/' + path.basename(packaged.output)
  komgaFixture.books.set('remote-book', {
    id: 'remote-book', libraryId: 'test-library', seriesId: 'remote-series',
    name: path.basename(packaged.output), url: '/remote/library/' + relativeOutput,
    metadata: {
      title: '服务器旧标题', number: '1', numberSort: 1, summary: '旧简介', tags: ['旧标签'],
      authors: [{ name: '保留墨线人员', role: 'inker' }, { name: '旧作者', role: 'writer' }],
      notableEntities: [{ kind: 'team', name: '保留队伍' }, { kind: 'character', name: '旧角色' }],
      links: [],
    },
  })
  komgaFixture.series.set('remote-series', {
    id: 'remote-series', libraryId: 'test-library', oneshot: false, booksCount: 1,
    url: '/remote/library/' + path.basename(path.dirname(packaged.output)),
    metadata: {
      title: packaged.metadata.series, titleSort: '手工排序标题', publisher: '旧出版方',
      language: 'ja', ageRating: 0, genres: ['旧类型'], readingDirection: 'LEFT_TO_RIGHT',
      alternateTitles: [{ label: 'en', title: 'Existing alias' }, { label: 'zh-Hans', title: '旧译名' }],
    },
  })
  await evaluate("[...document.querySelectorAll('.el-tabs__item')].find(x=>x.textContent === '基本资料').click()")
  await setField('title', '春日旅途·同步校订')
  await click('保存草稿')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '待审核'", 'sync title draft')
  await evaluate("document.querySelector('.review-checkbox').click()")
  await click('审核通过')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '已审核'", 'reviewed metadata ready without repackaging')
  await click('同步到 Komga')
  await waitFor("document.querySelector('[data-testid=komga-url]')", 'connection settings opened automatically')
  await waitFor("document.querySelector('[data-testid=komga-test]')?.disabled === false", 'connection configuration loaded')
  await setField('komga-url', komgaFixture.url)
  await setField('komga-api-key', syntheticKey)
  await click('测试连接并读取书库')
  await waitFor("document.querySelector('[data-testid=komga-test-success]')", 'authenticated Komga connection')
  await capture('ComicManage-Komga-connection.png')
  await click('保存并同步')
  await waitFor("document.querySelector('[data-testid=komga-sync-summary]')", 'direct Komga synchronization result')
  assert.equal(await evaluate("document.querySelector('[data-testid=komga-sync-results]').textContent.includes('成功')"), true)
  const remoteBook = komgaFixture.books.get('remote-book').metadata
  assert.equal(remoteBook.title, '春日旅途·同步校订')
  assert.equal(remoteBook.numberSort, 2.5)
  assert.equal(remoteBook.releaseDate, '2024-02-28')
  assert.ok(remoteBook.authors.some(author => author.name === '保留墨线人员' && author.role === 'inker'))
  assert.ok(remoteBook.authors.some(author => author.name === '森野' && author.role === 'writer'))
  assert.ok(remoteBook.notableEntities.some(entity => entity.kind === 'character' && entity.name === '博丽灵梦'))
  assert.ok(remoteBook.notableEntities.some(entity => entity.kind === 'team' && entity.name === '保留队伍'))
  assert.equal(remoteBook.notableEntities.some(entity => entity.name === '旧角色'), false)
  const remoteSeries = komgaFixture.series.get('remote-series').metadata
  assert.equal(remoteSeries.readingDirection, 'VERTICAL')
  assert.equal(remoteSeries.publisher, 'pink-noise')
  assert.equal(remoteSeries.ageRating, 18)
  assert.ok(remoteSeries.alternateTitles.some(alias => alias.label === 'zh-Hans' && alias.title === '春日旅途系列校订'))
  assert.ok(remoteSeries.alternateTitles.some(alias => alias.label === 'en' && alias.title === 'Existing alias'))
  assert.equal(remoteSeries.titleSort, '手工排序标题')
  assert.equal((await fs.readFile(path.join(dataDir, 'komga-connection.json'), 'utf8')).includes(syntheticKey), false)
  const savedConnection = await evaluate('window.comic.komgaConfig()')
  assert.equal(savedConnection.hasApiKey, true)
  assert.equal(JSON.stringify(savedConnection).includes(syntheticKey), false)
  await capture('ComicManage-Komga-result.png')
  await click('完成')
  await waitFor("!document.querySelector('.komga-sync-result-dialog')", 'sync result closed')
  await toggle('komga-settings')
  await waitFor("document.querySelector('[data-testid=komga-url] input')?.value || document.querySelector('input[data-testid=komga-url]')?.value", 'saved connection reopened')
  assert.equal(await evaluate("document.querySelector('input[data-testid=komga-api-key]')?.value || document.querySelector('[data-testid=komga-api-key] input')?.value || ''"), '')
  await click('取消')
  await call('approve', { id: secondId, metadata: { age_rating: 'Everyone', genre: '漫画', tags: ['日常'], collection_mode: 'oneshot', series: '本地旧系列', number: '8', count: '9', volume: '3' } })
  const oneShotPlan = await call('plan', { ids: [secondId], outputRoot: output })
  assert.equal(path.basename(path.dirname(oneShotPlan.items[0].destination)), '_oneshots')
  const oneShotResult = await call('export', { token: oneShotPlan.token })
  assert.deepEqual(oneShotResult.errors, [])
  const oneShotXml = (await call('xml', { id: secondId, metadata: {}, pageCount: 1 })).xml
  for (const tag of ['Series','Number','Count','LocalizedSeries','BlackAndWhite']) assert.equal(oneShotXml.includes('<'+tag+'>'), false, tag)
  assert.ok(oneShotXml.includes('<Volume>3</Volume>'))
  catalog = await call('bootstrap')

  await evaluate("[...document.querySelectorAll('.table-area .el-table__header .el-checkbox')].find(item=>item.getClientRects().length).click()")
  await waitFor("document.querySelector('.selected-count')?.textContent.includes('2')", 'two comics checked')
  await setInput('搜索作品、系列、作者或分类', '不存在的筛选结果')
  await waitFor("document.body.innerText.includes('没有找到匹配的作品')", 'empty filter with reserved selection')
  await click('清除筛选')
  await waitFor("document.querySelectorAll('.table-area .el-table__body .el-checkbox input:checked').length === 2", 'checkboxes restored after empty filter')
  const beforeBatch = new Map(catalog.comics.map(book => [book.id, book.metadata]))
  const batchChanges = {
    collection_mode: 'series',
    series: '旅途系列',
    release_type: '商业出版',
    publication_format: '分卷',
    age_rating: 'MA15+',
    color: '全彩',
    release_methods: ['公开发布'],
    release_platforms: ['BOOTH', 'DLsite'],
    reading_direction: 'WEBTOON',
  }
  window.setSize(1180, 800)
  await click('批量编辑')
  await toggle('batch-expand-all')
  assert.equal(await evaluate("[...document.querySelectorAll('.batch-edit-dialog .el-dialog__footer button')].find(b => b.textContent.trim() === '保存 2 本作品')?.disabled"), true)
  await toggle('batch-enable-collection-mode')
  await chooseOption('batch-collection-mode', '多卷系列 · Series')
  await toggle('batch-enable-series')
  await setField('batch-series', batchChanges.series)
  await toggle('batch-enable-release-type')
  await chooseOption('batch-release-type', batchChanges.release_type)
  await toggle('batch-enable-publication-format')
  await chooseOption('batch-publication-format', batchChanges.publication_format)
  await toggle('batch-enable-age-rating')
  await chooseOption('batch-age-rating', 'MA15+')
  await toggle('batch-enable-color')
  await chooseOption('batch-color', '全彩')
  await toggle('batch-enable-release-methods')
  await chooseMany('batch-release-methods', ['公开发布'])
  await toggle('batch-enable-release-platforms')
  await chooseMany('batch-release-platforms', ['BOOTH', 'DLsite'])
  await toggle('batch-enable-reading-direction')
  await chooseOption('batch-reading-direction', 'Webtoon')
  assert.equal(await evaluate("document.querySelector('.batch-edit-dialog .el-dialog__footer').getBoundingClientRect().bottom <= window.innerHeight"), true)
  await capture('ComicManage-Electron-batch.png')
  await click('保存 2 本作品')
  await waitFor("document.body.innerText.includes('已更新 2 本作品')", 'batch metadata edit')
  await waitFor("!document.querySelector('.table-disabled') && !document.querySelector('.batch-edit-dialog')", 'batch dialog released')
  catalog = await call('bootstrap')
  for (const book of catalog.comics) {
    assert.equal(book.status, 'draft')
    assert.deepEqual(book.metadata, { ...beforeBatch.get(book.id), ...batchChanges }, 'Only the checked metadata fields change')
  }
  assert.equal(catalog.comics.find(book => book.id === id).output, packaged.output)
  await evaluate("[...document.querySelectorAll('.table-area .el-table__header .el-checkbox')].find(item=>item.getClientRects().length).click()")
  await waitFor("!document.querySelector('.selected-count')", 'batch selection cleared')
  await evaluate("[...document.querySelectorAll('.table-area .el-table__row')].find(row=>row.textContent.includes('夏の旅')).querySelector('.el-checkbox').click()")
  await waitFor("document.querySelector('.selected-count')?.textContent.includes('1')", 'one comic checked for removal')
  await click('移出列表')
  await evaluate("[...document.querySelectorAll('.el-message-box__btns button')].find(item=>item.textContent.trim()==='移出列表').click()")
  await waitFor("document.body.innerText.includes('已移出 1 本作品')", 'remove from catalog')
  assert.equal((await call('bootstrap')).comics.length, 1)
  assert.equal((await call('bootstrap')).comics[0].id, id)
  assert.equal((await fs.readFile(path.join(secondSource, '1.png'))).equals(page), true)
  await click('重新扫描')
  await waitFor("document.body.innerText.includes('已扫描 1 本作品')", 'removed record skipped')
  await dismissResult()
  assert.equal((await call('bootstrap')).comics.length, 1, 'Rescan must not add unchecked or removed comics')
  picking = secondSource
  await click('添加漫画')
  await evaluate("[...document.querySelectorAll('.el-dropdown-menu__item')].find(item=>item.textContent.includes('选择图片文件夹')).click()")
  await click('选择多个文件夹')
  await click('读取并预览')
  await waitFor("document.querySelector('[data-testid=import-table]')?.textContent.includes('已移出')", 'removed import preview')
  await evaluate("document.querySelector('[data-testid=import-table] .el-table__body .el-checkbox').click()")
  await click('导入选中 1 本')
  await waitFor("document.body.innerText.includes('已导入 1 本作品')", 'explicit restore')
  await dismissResult()
  catalog = await call('bootstrap')
  assert.equal(catalog.comics.length, 2)
  assert.deepEqual(catalog.comics.find(book => book.id === secondId).metadata, { ...beforeBatch.get(secondId), ...batchChanges })
  await click('备份资料库')
  await waitFor("document.body.innerText.includes('已备份目录索引')", 'catalog backup')
  await dismissResult()
  assert.ok((await fs.stat(path.join(fixture, 'backup.sqlite3'))).size > 0)

  // Remote-only books use the real renderer, credential vault and HTTP backend.
  komgaFixture.setPage(page)
  for (const [bookId, title, number] of [['cloud-a', '云端卷一', '1'], ['cloud-b', '云端卷二', '2']]) {
    komgaFixture.books.set(bookId, {
      id: bookId, libraryId: 'test-library', seriesId: 'cloud-series', oneshot: false,
      name: title + '.cbz', url: '/remote/cloud/' + title + '.cbz', media: { pagesCount: 1 },
      metadata: { title, number, numberSort: 42, summary: '云端原始简介', isbn: '9780000000000',
        titleLock: false, tags: ['保留自定义标签'],
        authors: [{ name: '云端作者', role: 'writer' }, { name: '保留墨线人员', role: 'inker' }],
        notableEntities: [{ kind: 'team', name: '保留远端队伍' }, { kind: '内容类型', name: '漫画' }, { kind: '年龄分级', name: 'Everyone' }],
        links: [{ label: '作者主页', url: 'https://example.org/cloud' }] },
    })
  }
  komgaFixture.series.set('cloud-series', {
    id: 'cloud-series', libraryId: 'test-library', oneshot: false, booksCount: 2, url: '/remote/cloud',
    metadata: { title: '云端系列', titleSort: 'Cloud manual sort', publisher: '原出版方', language: 'ja',
      ageRating: 17, genres: ['奇幻'], readingDirection: 'WEBTOON',
      alternateTitles: [{ label: 'custom-label', title: '保留服务器别名' }] },
  })
  const selectRemote = async title => {
    await evaluate(`[...document.querySelectorAll('.table-area .el-table__row')].find(row=>row.textContent.includes(${JSON.stringify(title)})).querySelector('.comic-titles').click()`)
    await waitFor(`document.querySelector('.selected-summary h2')?.textContent === ${JSON.stringify(title)}`, 'selected remote book ' + title)
  }
  const approveRemote = async () => {
    await evaluate("document.querySelector('.review-checkbox').click()")
    await click('审核通过')
    await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '已审核'", 'remote review')
  }
  const syncRemote = async () => {
    await click('同步到 Komga')
    await waitFor("document.querySelector('[data-testid=komga-sync-summary]')", 'remote sync result')
    assert.equal(await evaluate("document.querySelector('[data-testid=komga-sync-results]').textContent.includes('失败')"), false)
    await click('完成')
    await waitFor("!document.querySelector('.komga-sync-result-dialog')", 'remote sync result closed')
  }
  const readKomgaLibrary = async () => {
    await toggle('komga-settings')
    await waitFor("document.querySelector('[data-testid=komga-save]')?.disabled === false && document.querySelector('[data-testid=komga-save]')?.textContent.includes('保存并读取书库')", 'sidebar connection ready to read')
    await toggle('komga-save')
    await waitFor("!document.querySelector('.komga-settings-dialog')", 'sidebar connection saved')
  }
  await readKomgaLibrary()
  await waitFor("document.querySelector('.table-area')?.textContent.includes('云端卷一') && !document.querySelector('.table-disabled')", 'remote library without import')
  catalog = await call('bootstrap')
  const cloudA = catalog.comics.find(book => book.remote?.bookId === 'cloud-a')
  const cloudB = catalog.comics.find(book => book.remote?.bookId === 'cloud-b')
  assert.ok(cloudA && cloudB)
  assert.equal(cloudA.kind, 'komga')
  assert.equal(cloudA.metadata.age_rating, 'Everyone')
  assert.equal(cloudA.metadata.genre, '漫画')
  assert.equal(cloudA.remote.sharedMetadata.ageRating, 17)
  assert.equal(catalog.comics.filter(book => book.kind === 'komga').length, 3)
  await selectRemote('云端卷一')
  await waitFor("document.querySelector('.selected-summary img')?.src.startsWith('data:image/png;base64,')", 'authenticated remote page')
  const beforeNoop = komgaFixture.patches.length
  const rawCloud = structuredClone(komgaFixture.books.get('cloud-a').metadata)
  const rawShared = structuredClone(komgaFixture.series.get('cloud-series').metadata)
  await approveRemote()
  await syncRemote()
  assert.equal(komgaFixture.patches.length, beforeNoop, 'Unchanged remote review sends no PATCH or lock changes')
  assert.deepEqual(komgaFixture.books.get('cloud-a').metadata, rawCloud)
  assert.deepEqual(komgaFixture.series.get('cloud-series').metadata, rawShared)
  await setField('title', '云端卷一·校订')
  await click('保存草稿')
  await waitFor("document.querySelector('[data-testid=komga-local-changes]')", 'saved remote draft')
  komgaFixture.books.get('cloud-a').metadata.summary = '服务器上的新简介'
  await readKomgaLibrary()
  await waitFor("!document.querySelector('.table-disabled')", 'refreshed remote library')
  await selectRemote('云端卷一·校订')
  await waitFor("document.querySelector('[data-testid=komga-conflict-actions]')", 'remote conflict keeps draft')
  await toggle('komga-keep-local')
  await waitFor("!document.querySelector('[data-testid=komga-conflict-actions]') && document.querySelector('[data-testid=komga-local-changes]')", 'remote conflict rebased')
  catalog = await call('bootstrap')
  assert.equal(catalog.comics.find(book => book.id === cloudA.id).metadata.summary, '服务器上的新简介')
  assert.equal(catalog.comics.find(book => book.id === cloudA.id).metadata.title, '云端卷一·校订')
  await approveRemote()
  const beforeTitle = komgaFixture.patches.length
  await syncRemote()
  assert.deepEqual(komgaFixture.patches.slice(beforeTitle).map(item => [item.kind, item.id, Object.keys(item.body).sort()]), [['books', 'cloud-a', ['title', 'titleLock']]])
  assert.equal(komgaFixture.books.get('cloud-a').metadata.summary, '服务器上的新简介')
  assert.equal(komgaFixture.books.get('cloud-a').metadata.numberSort, 42)
  await capture('ComicManage-Komga-library.png')
  for (const title of ['云端卷一·校订', '云端卷二']) {
    await evaluate(`[...document.querySelectorAll('.table-area .el-table__row')].find(row=>row.textContent.includes(${JSON.stringify(title)})).querySelector('.el-checkbox').click()`)
  }
  await waitFor("document.querySelector('.selected-count')?.textContent.includes('2')", 'two remote books selected')
  await click('批量编辑')
  assert.equal(await evaluate("document.querySelector('[data-testid=batch-enable-collection-mode] input').disabled"), true)
  await evaluate("[...document.querySelectorAll('.batch-edit-dialog button')].find(item=>item.textContent.trim()==='展开全部').click()")
  await waitFor("document.querySelector('[data-testid=batch-enable-publisher]')", 'expanded remote batch fields')
  await toggle('batch-enable-publisher')
  await setField('batch-publisher', '云端批量出版方')
  await toggle('batch-enable-publication-format')
  await chooseOption('batch-publication-format', '分卷')
  await click('保存 2 本作品')
  await waitFor("!document.querySelector('.batch-edit-dialog') && !document.querySelector('.table-disabled')", 'remote batch saved')
  catalog = await call('bootstrap')
  for (const identity of [cloudA.id, cloudB.id]) {
    const edited = catalog.comics.find(book => book.id === identity)
    assert.equal(edited.status, 'draft')
    assert.equal(edited.metadata.publisher, '云端批量出版方')
    assert.equal(edited.metadata.publication_format, '分卷')
    assert.equal(edited.metadata.age_rating, 'Everyone')
    assert.equal(edited.metadata.genre, '漫画')
  }
  const beforeBatchSync = komgaFixture.patches.length
  await toggle('batch-review-sync')
  await waitFor("document.querySelector('[data-testid=batch-review-preview-table]')?.textContent.includes('云端卷二')", 'batch review frozen preview')
  assert.equal(await evaluate("document.querySelector('[data-testid=batch-review-submit]').disabled"), true)
  assert.equal(komgaFixture.patches.length, beforeBatchSync, 'Preview must not PATCH')
  catalog = await call('bootstrap')
  for (const identity of [cloudA.id, cloudB.id]) assert.equal(catalog.comics.find(book => book.id === identity).status, 'draft', 'Preview must not approve')
  await capture('ComicManage-batch-review-preview.png')
  await toggle('batch-review-confirm')
  await waitFor("document.querySelector('[data-testid=batch-review-submit]')?.disabled === false", 'batch review confirmation')
  await toggle('batch-review-submit')
  await waitFor("document.querySelector('[data-testid=batch-review-results]')", 'batch review and sync result')
  await capture('ComicManage-batch-review-results.png')
  catalog = await call('bootstrap')
  for (const identity of [cloudA.id, cloudB.id]) assert.equal(catalog.comics.find(book => book.id === identity).status, 'approved')
  await toggle('batch-review-done')
  await waitFor("!document.querySelector('[data-testid=batch-review-results]')", 'batch review result closed')
  const batchPatches = komgaFixture.patches.slice(beforeBatchSync)
  assert.equal(batchPatches.filter(item => item.kind === 'books').length, 2)
  assert.equal(batchPatches.filter(item => item.kind === 'series').length, 1)
  assert.equal(komgaFixture.series.get('cloud-series').metadata.publisher, '云端批量出版方')
  assert.equal(komgaFixture.series.get('cloud-series').metadata.ageRating, 17)
  assert.equal(komgaFixture.series.get('cloud-series').metadata.readingDirection, 'WEBTOON')
  assert.deepEqual(komgaFixture.series.get('cloud-series').metadata.genres, ['奇幻'])
  assert.equal(komgaFixture.books.get('cloud-a').metadata.links[0].label, '作者主页')
  assert.ok(komgaFixture.books.get('cloud-b').metadata.notableEntities.some(entity => entity.kind === 'team' && entity.name === '保留远端队伍'))
  await capture('ComicManage-Komga-library-batch.png')

  const selectCloudPair = async () => {
    await readKomgaLibrary()
    await waitFor("!document.querySelector('.table-disabled')", 'library refreshed for batch review')
    for (const title of ['云端卷一·校订', '云端卷二']) {
      await evaluate(`[...document.querySelectorAll('.table-area .el-table__row')].find(row=>row.textContent.includes(${JSON.stringify(title)})).querySelector('.el-checkbox').click()`)
    }
    await waitFor("document.querySelector('.selected-count')?.textContent.includes('2')", 'mixed review selection')
  }
  await call('save', {id:cloudA.id,metadata:{summary:'批量确认后的有效修改'}})
  await call('save', {id:cloudB.id,metadata:{authors:[]}})
  await selectCloudPair()
  const beforePartialReview = komgaFixture.patches.length
  await toggle('batch-review-sync')
  await waitFor("document.querySelector('[data-testid=batch-review-preview-table]')?.textContent.includes('请填写至少一位作者')", 'invalid book visibly blocked')
  await toggle('batch-review-confirm')
  await waitFor("document.querySelector('[data-testid=batch-review-submit]')?.disabled === false", 'ready subset confirmed')
  await toggle('batch-review-submit')
  await waitFor("document.querySelector('[data-testid=batch-review-results]')", 'partial batch result')
  catalog = await call('bootstrap')
  assert.equal(catalog.comics.find(book => book.id === cloudA.id).status,'approved')
  assert.equal(catalog.comics.find(book => book.id === cloudB.id).status,'draft')
  assert.deepEqual(komgaFixture.patches.slice(beforePartialReview).map(item=>[item.kind,item.id,Object.keys(item.body).sort()]),[['books','cloud-a',['summary','summaryLock']]])
  assert.ok(komgaFixture.books.get('cloud-b').metadata.authors.some(author=>author.role==='writer'&&author.name==='云端作者'))
  await toggle('batch-review-done')
  await waitFor("!document.querySelector('[data-testid=batch-review-results]')", 'partial result closed')

  await call('save', {id:cloudA.id,metadata:{summary:'确认时的本机草稿'}})
  await selectCloudPair()
  await toggle('batch-review-sync')
  await waitFor("document.querySelector('[data-testid=batch-review-preview-table]')?.textContent.includes('请填写至少一位作者')", 'review prepared before remote change')
  komgaFixture.books.get('cloud-a').metadata.summary='检查后服务器上的并发修改'
  const beforeStaleReview = komgaFixture.patches.length
  await toggle('batch-review-confirm')
  await waitFor("document.querySelector('[data-testid=batch-review-submit]')?.disabled === false", 'stale review confirmation')
  await toggle('batch-review-submit')
  await waitFor("document.querySelector('[data-testid=batch-review-results]')", 'stale review skipped result')
  catalog = await call('bootstrap')
  assert.equal(catalog.comics.find(book=>book.id===cloudA.id).status,'draft')
  assert.equal(catalog.comics.find(book=>book.id===cloudA.id).metadata.summary,'确认时的本机草稿')
  assert.equal(komgaFixture.patches.length,beforeStaleReview,'A remote change after preview cannot be approved or overwritten')
  await toggle('batch-review-done')
  await waitFor("!document.querySelector('[data-testid=batch-review-results]')", 'stale review result closed')
  window.setSize(1180, 800)
  await delay(200)
  assert.equal(await evaluate("document.querySelector('.review-footer').getBoundingClientRect().bottom <= window.innerHeight"), true)
  await capture('ComicManage-Electron-compact.png')
  assert.equal(javascriptErrors.filter(x => /Uncaught|TypeError|ReferenceError/.test(x)).length, 0, javascriptErrors.join('\n'))
  window.destroy()
  await desktop.bridge.stop()
  await komgaFixture.close()
  const result = { passed: true, checks: 'isolated renderer, lazy multi-folder checks, read-only parsed preview, import defaults, unchecked siblings excluded, expanded metadata editor, independent title and series, volume/number/count/day, conditional R18 correction, source URLs, namespaced custom tags, Komga Characters and tags, encrypted Komga configuration, connection test, reviewed native metadata direct sync without repackaging, partial field preservation, supplementary payload and manifest, One-Shot path and XML, review, preview, cover markers, verified export, batch edit changes only checked metadata and revokes review, remove without file deletion, rescan skips removed, restore preserves metadata, backup, remote-only library read and authenticated preview, no-change review sends zero PATCH, remote conflict rebase preserves edits, title-only differential sync, checked remote batch fields and shared publisher sync preserve native age/genre/direction, compact layout', fixture, screenshots }
  await fs.writeFile(path.join(root, 'build', 'desktop-last-result.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result))
  app.exit(0)
}

const timeout = setTimeout(() => { console.error('Desktop integration test timed out'); desktop?.window.destroy(); desktop?.bridge.child.kill(); app.exit(1) }, 180000)
timeout.unref()
run().catch(async error => {
  await fs.writeFile(path.join(root, 'build', 'desktop-last-result.json'), JSON.stringify({ passed: false, error: error.stack }, null, 2))
  console.error(error); desktop?.window.destroy(); await desktop?.bridge.stop(); app.exit(1)
})
