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
const { createServer } = require('node:http')

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
  let pickerOptions
  let pickerOpened
  dialog.showOpenDialog = async (_window, options) => {
    pickerOptions = options
    pickerOpened?.()
    return { canceled: picking === null, filePaths: picking === null ? [] : [].concat(picking) }
  }
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
  const groupExpression = name => `[...document.querySelectorAll('[data-testid=work-group-row]')].find(row=>row.textContent.includes(${JSON.stringify(name)}))`
  const toggleGroup = async name => {
    await waitFor(`!!(${groupExpression(name)})`, 'work group ' + name)
    await evaluate(`${groupExpression(name)}.querySelector('[data-testid=work-group-toggle]').click()`)
  }
  const comicExpression = id => `document.querySelector('[data-testid="comic-row"][data-comic-id="'+${JSON.stringify(id)}+'"]')`
  const selectComicId = id => evaluate(`${comicExpression(id)}.querySelector('.comic-titles').click()`)
  const checkComicId = id => evaluate(`document.querySelector('[data-testid="library-select-comic"][data-comic-id="'+${JSON.stringify(id)}+'"]').click()`)
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

  async function verifyModelAssist() {
    const received = []
    const testKey = 'synthetic-model-test-key'
    const modelServer = createServer(async (req, res) => {
      const body = []
      for await (const chunk of req) body.push(chunk)
      const input = JSON.parse(Buffer.concat(body).toString('utf8'))
      received.push({ path: req.url, authorization: req.headers.authorization, input })
      const isProbe = received.length === 1
      const content = JSON.stringify(isProbe ? { status: 'ok' } : { fields: { title: '春日旅途' } })
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ choices: [{ finish_reason: 'stop', message: { content } }] }))
    })
    await new Promise((resolve, reject) => { modelServer.once('error', reject); modelServer.listen(0, '127.0.0.1', resolve) })
    try {
      await toggle('model-settings-open')
      await waitFor("document.querySelector('[data-testid=model-profile-new]')", 'model configuration')
      const name = '测试本机模型'
      await toggle('model-profile-new')
      await setField('model-profile-name', name)
      const base = 'http://127.0.0.1:' + modelServer.address().port + '/v1'
      await setField('model-profile-endpoint', base)
      await setField('model-profile-model', 'fixture-model')
      await setField('model-profile-key', testKey)
      await toggle('model-profile-test')
      await waitFor("document.querySelector('[data-testid=model-profile-result]')?.textContent.includes('连接成功')", 'model request to a real local server')
      assert.deepEqual(await call('model_config_get'), { profiles: [], default_id: '' })
      await toggle('model-profile-save')
      await waitFor("document.querySelector('[data-testid=model-profile-option]')?.textContent.includes('测试本机模型')", 'saved model profile')
      const saved = await call('model_config_get')
      assert.equal(saved.profiles[0].id, saved.default_id)
      assert.equal(saved.profiles[0].has_key, true)
      assert(!JSON.stringify(saved).includes(testKey))
      const onDisk = await fs.readFile(path.join(dataDir, 'model-connections.json'), 'utf8')
      assert(!onDisk.includes(testKey))
      assert.match(onDisk, /encrypted_key/)
      await delay(300)
      await capture('ComicManage-Electron-model-management.png')
      await toggle('model-settings-close')
      await waitFor("!document.querySelector('[data-testid=model-profile-test]')?.getClientRects().length", 'model settings closed')
      await toggle('scrape-open')
      await waitFor("document.querySelector('[data-testid=scrape-model-run]')?.getClientRects().length", 'assist available without a selected link')
      await toggle('scrape-model-run')
      await waitFor("document.querySelector('[data-testid=scrape-field-title]')?.getClientRects().length", 'model candidates')
      assert.equal(received.length, 2)
      assert(received.every(item => item.path === '/v1/chat/completions' && item.authorization === 'Bearer ' + testKey))
      const sent = received[1].input.messages.at(-1).content
      assert(sent.includes('春日旅途·校订'))
      assert(!sent.includes('用于验证新界面的合成样本'))
      assert(!sent.includes('example.com'))
      assert(!sent.includes('漫画源'))
      assert(!sent.includes(id))
      assert.equal(await evaluate("!!document.querySelector('[data-testid=scrape-field-title] input:checked')"), false)
      await toggle('scrape-field-title')
      await waitFor("document.querySelector('[data-testid=scrape-confirm-overwrite]')", 'model overwrite confirmation')
      assert.equal(await evaluate("document.querySelector('[data-testid=scrape-apply]').disabled"), true)
      await toggle('scrape-confirm-overwrite')
      await delay(300)
      await capture('ComicManage-Electron-model-candidates.png')
      await toggle('scrape-apply')
      await waitFor("!document.querySelector('[data-testid=scrape-apply]')?.getClientRects().length", 'model candidates applied to the draft')
      assert.equal(await evaluate("(document.querySelector('input[data-testid=title]') || document.querySelector('[data-testid=title] input')).value"), '春日旅途')
      assert.equal((await call('bootstrap')).comics.find(book => book.id === id).metadata.title, '春日旅途')
      await setField('title', '春日旅途·校订')
    } finally {
      await new Promise(resolve => modelServer.close(resolve))
    }
  }

  await waitFor("document.body.innerText.includes('从第一本漫画开始')", 'empty state')
  assert.equal(await evaluate("document.querySelectorAll('[data-testid=komga-settings]').length"), 1)
  assert.equal(await evaluate("document.querySelector('[data-testid=komga-pull]')"), null)
  assert.equal(await evaluate('typeof window.require'), 'undefined')
  assert.equal(await evaluate('typeof window.process'), 'undefined')
  assert.equal(await evaluate("window.comic.invoke('arbitrary-shell',{}).then(()=>false,()=>true)"), true)
  await capture('ComicManage-Electron-empty.png')
  await click('添加漫画目录')
  await waitFor("document.querySelector('[data-testid=import-pick-folders]')", 'single folder picker entry')
  assert.equal(await evaluate("document.querySelectorAll('[data-testid=import-pick-folders]').length"), 1)
  assert.equal(await evaluate("typeof window.comic.chooseCbz"), 'undefined')
  await click('选择文件夹')
  await waitFor("document.querySelectorAll('.folder-tree-node').length === 1", 'directory selection')
  assert.deepEqual(pickerOptions.properties, ['openDirectory', 'multiSelections'])
  picking = null
  const cancelledPicker = new Promise(resolve => { pickerOpened = resolve })
  await click('选择文件夹')
  await cancelledPicker
  pickerOpened = undefined
  assert.equal(await evaluate("document.querySelectorAll('.folder-tree-node').length"), 1, 'Cancel keeps the selected folder')
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
  const fixtureLink = 'https://www.dlsite.com/home/work/=/product_id/RJ123456.html'
  const originalBridgeCall = desktop.bridge.call
  let requestedMetadata
  desktop.bridge.call = function (method, params) {
    if (method !== 'scrape_preview') return originalBridgeCall.call(this, method, params)
    requestedMetadata = params
    return Promise.resolve({
      provider: 'dlsite', sourceUrl: fixtureLink, warnings: [],
      fields: { title: '站点作品标题', publisher: '站点出版方', release_platforms: ['DLsite'] },
    })
  }
  try {
    await toggle('scrape-open')
    await waitFor("document.querySelector('[data-testid=scrape-url]')", 'metadata lookup form')
    await setField('scrape-url', fixtureLink)
    await toggle('scrape-read')
    await waitFor("document.querySelector('[data-testid=scrape-field-title]')", 'metadata differences')
    assert.deepEqual(requestedMetadata, { url: fixtureLink })
    assert.equal(await evaluate("!!document.querySelector('[data-testid=scrape-field-title] input:checked')"), false)
    await toggle('scrape-select-empty')
    const sourceChecked = await evaluate("!!document.querySelector('[data-testid=scrape-append-source] input:checked')")
    if (!sourceChecked) await toggle('scrape-append-source')
    await toggle('scrape-apply')
    await waitFor("!document.querySelector('[data-testid=scrape-apply]')?.getClientRects().length", 'scrape preview applied to draft')
    assert.equal(await evaluate("(document.querySelector('input[data-testid=title]') || document.querySelector('[data-testid=title] input')).value"), '春日旅途·校订')
    assert.equal(await evaluate("document.querySelector('[data-testid=release-platforms]').textContent.includes('DLsite')"), true)
    assert.equal(await evaluate(`(document.querySelector('textarea[data-testid=web]') || document.querySelector('[data-testid=web] textarea')).value.includes(${JSON.stringify(fixtureLink)})`), true)
    assert.equal((await call('bootstrap')).comics.find(book => book.id === id).metadata.title, '春日旅途')
  } finally {
    desktop.bridge.call = originalBridgeCall
  }
  await verifyModelAssist()
  await toggle('editor-layout-toggle')
  await waitFor("document.querySelector('.workspace-editor-expanded') && !document.querySelector('.library-panel').getClientRects().length", 'expanded editor hides the list')
  assert.equal(await evaluate("(document.querySelector('input[data-testid=title]') || document.querySelector('[data-testid=title] input')).value"), '春日旅途·校订')
  await toggle('editor-layout-toggle')
  await waitFor("document.querySelector('.library-panel').getClientRects().length && document.querySelector('.details-panel .status-pill')?.textContent === '未保存'", 'return to list preserves unsaved draft')
  await setField('series', '春の旅シリーズ改訂')
  await setField('localized-series', '春日旅途系列校订')
  await setField('number', '2.5')
  await setField('count', '4')
  await setField('volume', '7')
  await setField('work-group', 'Main Variant Group（森野）')
  await chooseOption('text-version', '无文字')
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
  await toggle('page-viewer-open')
  await waitFor("document.querySelector('[data-testid=page-viewer-image]')?.naturalWidth > 0", 'window-sized preview')
  await toggle('viewer-page-next')
  await waitFor("document.querySelector('[data-testid=page-viewer-image]')?.alt.endsWith('第 2 页')", 'page navigation in large preview')
  await toggle('page-viewer-close')
  await waitFor("document.querySelector('.page-counter input')?.value === '2' && document.querySelector('.large-page img')?.complete", 'preview returns to the same page')
  await click('设为封面')
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
  assert.ok(exportedXml.includes('作品组:Main Variant Group（森野）'))
  assert.ok(exportedXml.includes('文字版本:无文字'))
  assert.equal(exportedXml.includes('<BlackAndWhite>') || exportedXml.includes('<LocalizedSeries>') || exportedXml.includes('<原作>') || exportedXml.includes('<色彩>') || exportedXml.includes('<作品组>') || exportedXml.includes('<文字版本>'), false)
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
  assert.equal(savedConnection.autoSyncEnabled, true)
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
  assert.equal(oneShotPlan.komga.state, 'checked')
  assert.equal(oneShotPlan.items[0].komga.status, 'new')
  const oneShotResult = await call('export', { token: oneShotPlan.token })
  assert.deepEqual(oneShotResult.errors, [])
  const oneShotXml = (await call('xml', { id: secondId, metadata: {}, pageCount: 1 })).xml
  for (const tag of ['Series','Number','Count','LocalizedSeries','BlackAndWhite']) assert.equal(oneShotXml.includes('<'+tag+'>'), false, tag)
  assert.ok(oneShotXml.includes('<Volume>3</Volume>'))
  catalog = await call('bootstrap')

  const indexedOneShot = catalog.comics.find(book => book.id === secondId)
  const automaticJob = catalog.autoSyncJobs.find(job => job.comicId === secondId)
  assert.ok(automaticJob && automaticJob.status === 'PendingIndex')
  assert.equal(automaticJob.libraryId, 'test-library')
  assert.equal(automaticJob.relativePath, '_oneshots/' + path.basename(indexedOneShot.output))
  const automaticBook = {
    id:'automatic-book', libraryId:'test-library', seriesId:'automatic-series', oneshot:true,
    name:path.basename(indexedOneShot.output, '.cbz'), url:'/remote/library/' + automaticJob.relativePath,
    media:{pagesCount:1}, readProgress:{page:1,completed:false},
    metadata:{title:indexedOneShot.metadata.title,number:'1',numberSort:1,
      summary:'已从 CBZ 读取的简介',authors:[{name:'CBZ 作者',role:'writer'}],tags:['CBZ 标签'],
      notableEntities:[{kind:'character',name:'待清理的旧角色'},{kind:'team',name:'第三方队伍'}],
      notableEntitiesLock:false,links:[]},
  }
  komgaFixture.books.set('automatic-book',structuredClone(automaticBook))
  const automaticSeries={id:'automatic-series',libraryId:'test-library',oneshot:true,booksCount:1,
    url:automaticBook.url,metadata:{title:indexedOneShot.metadata.title,ageRating:0,genres:['漫画'],language:'ja',alternateTitles:[]}}
  komgaFixture.series.set('automatic-series',structuredClone(automaticSeries))
  const beforeAutomatic=komgaFixture.patches.length
  await toggle('komga-settings')
  await waitFor("document.querySelector('[data-testid=komga-auto-sync]')?.getAttribute('aria-checked') === 'true' || document.querySelector('[data-testid=komga-auto-sync] input')?.checked === true",'automatic synchronization enabled')
  await toggle('komga-auto-jobs')
  await waitFor("!!document.querySelector('[data-testid=komga-auto-jobs-table]')",'durable automatic task list')
  await call('komga_jobs_retry',{ids:[automaticJob.id]})
  await waitFor(`document.querySelector('[data-testid=komga-auto-jobs-table]')?.textContent.includes('已同步')`,'automatic scheduled entity synchronization',45000)
  const automaticPatches=komgaFixture.patches.slice(beforeAutomatic)
  assert.deepEqual(automaticPatches.map(item=>[item.kind,item.id,Object.keys(item.body).sort()]),[['books','automatic-book',['notableEntities','notableEntitiesLock']]])
  const finalAutomatic=structuredClone(komgaFixture.books.get('automatic-book'))
  assert.equal(finalAutomatic.metadata.notableEntitiesLock,true)
  assert.ok(finalAutomatic.metadata.notableEntities.some(item=>item.kind==='team'&&item.name==='第三方队伍'))
  assert.equal(finalAutomatic.metadata.notableEntities.some(item=>item.name==='待清理的旧角色'),false)
  delete finalAutomatic.metadata.notableEntities; delete finalAutomatic.metadata.notableEntitiesLock
  const unchangedNative=structuredClone(automaticBook)
  delete unchangedNative.metadata.notableEntities; delete unchangedNative.metadata.notableEntitiesLock
  assert.deepEqual(finalAutomatic,unchangedNative)
  assert.deepEqual(komgaFixture.series.get('automatic-series'),automaticSeries)
  await capture('ComicManage-automatic-sync-jobs.png')
  await click('关闭')
  await waitFor("!document.querySelector('[data-testid=komga-auto-jobs-table]')",'automatic task dialog closed')
  await evaluate(`window.comic.saveKomgaConfig({url:${JSON.stringify(komgaFixture.url)},autoSyncEnabled:false})`)
  assert.equal((await evaluate('window.comic.komgaConfig()')).autoSyncEnabled,false)
  komgaFixture.books.delete('automatic-book');komgaFixture.series.delete('automatic-series')

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
    work_group: '批量作品组（统一作者）',
    text_version: '有文字',
  }
  window.setSize(1180, 800)
  await click('批量编辑')
  await toggle('batch-expand-all')
  assert.equal(await evaluate("[...document.querySelectorAll('.batch-edit-dialog .el-dialog__footer button')].find(b => b.textContent.trim() === '保存 2 本作品')?.disabled"), true)
  await toggle('batch-enable-collection-mode')
  await chooseOption('batch-collection-mode', '多卷系列 · Series')
  await toggle('batch-enable-series')
  await setField('batch-series', batchChanges.series)
  await toggle('batch-enable-work-group')
  await setField('batch-work-group', batchChanges.work_group)
  await toggle('batch-enable-text-version')
  await chooseOption('batch-text-version', '有文字')
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
  await waitFor(`!!(${groupExpression(batchChanges.work_group)})`, 'new local group is collapsed after batch edit')
  assert.equal(await evaluate(`(${groupExpression(batchChanges.work_group)}).querySelector('[data-testid=work-group-toggle]').getAttribute('aria-expanded')`),'false')
  assert.equal(await evaluate(`!!(${comicExpression(secondId)})`),false)
  await toggle('clear-library-selection')
  await waitFor("!document.querySelector('.selected-count')", 'grouped batch selection cleared explicitly')
  await toggleGroup(batchChanges.work_group)
  await waitFor(`!!(${comicExpression(secondId)})`, 'local group expanded for individual removal')
  await checkComicId(secondId)
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
  await click('选择文件夹')
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
  for (const [bookId, version] of [['variant-with', '有文字'], ['variant-without', '无文字']]) {
    const seriesId = bookId + '-series'
    const remotePath = '/remote/_oneshots/' + bookId + '.cbz'
    komgaFixture.books.set(bookId, {
      id: bookId, libraryId: 'test-library', seriesId, oneshot: true, name: bookId + '.cbz',
      url: remotePath, media: { pagesCount: 1 }, readProgress: { page: 1, completed: false },
      metadata: { title: '同一作品', number: '1', numberSort: 1, summary: '各版本独立资料',
        authors: [{ name: '作者甲', role: 'writer' }, { name: '保留职责', role: 'inker' }],
        tags: version === '有文字' ? ['日常', '无字版'] : ['日常'],
        notableEntities: [{ kind: '内容类型', name: '漫画' }, { kind: '年龄分级', name: 'Everyone' },
          { kind: '作品组', name: '版本作品（作者甲）' }, { kind: '文字版本', name: version }, { kind: 'team', name: '保留队伍' }],
        links: [] },
    })
    komgaFixture.series.set(seriesId, {
      id: seriesId, libraryId: 'test-library', oneshot: true, booksCount: 1, url: remotePath,
      metadata: { title: '同一作品', ageRating: 0, genres: ['漫画'], language: '', readingDirection: 'LEFT_TO_RIGHT', alternateTitles: [] },
    })
  }
  const originalOtherVersion = structuredClone(komgaFixture.books.get('variant-without'))
  const originalVariantSeries = structuredClone(komgaFixture.series.get('variant-with-series'))
  await readKomgaLibrary()
  await waitFor(`!!(${groupExpression('版本作品（作者甲）')}) && !document.querySelector('.table-disabled')`, 'versions share a collapsed work group')
  catalog = await call('bootstrap')
  const variants = catalog.comics.filter(book => ['variant-with', 'variant-without'].includes(book.remote?.bookId))
  const variantWith = variants.find(book=>book.remote.bookId==='variant-with')
  const variantWithout = variants.find(book=>book.remote.bookId==='variant-without')
  assert.equal(variants.length, 2)
  assert.notEqual(variants[0].id, variants[1].id)
  for (const book of variants) {
    assert.equal(book.metadata.collection_mode, 'oneshot')
    assert.equal(book.metadata.series, '')
    assert.equal(book.metadata.number, '')
    assert.equal(book.metadata.work_group, '版本作品（作者甲）')
  }
  assert.equal(await evaluate(`(${groupExpression('版本作品（作者甲）')}).querySelector('[data-testid=work-group-toggle]').getAttribute('aria-expanded')`),'false')
  assert.equal(await evaluate(`!!(${comicExpression(variantWith.id)}) || !!(${comicExpression(variantWithout.id)})`),false)
  const visibleSingles = await evaluate("document.querySelectorAll('[data-testid=library-select-comic] input:not(:disabled)').length")
  await toggle('library-select-all')
  await waitFor(`document.querySelector('.selected-count')?.textContent.includes(${JSON.stringify(String(visibleSingles))})`, 'select all includes only visible real books')
  assert.equal(await evaluate(`!!(${groupExpression('版本作品（作者甲）')}).querySelector('[data-testid=work-group-selected-count]')`),false,'Collapsed versions cannot be selected by the global header')
  await toggle('clear-library-selection')
  await waitFor("!document.querySelector('.selected-count')",'all visible selections cleared')
  await setInput('搜索作品、系列、作者或分类', '有文字')
  await waitFor(`!!(${comicExpression(variantWith.id)}) && !(${comicExpression(variantWithout.id)})`, 'search reveals only the matching version')
  assert.equal(await evaluate(`(${groupExpression('版本作品（作者甲）')}).querySelector('[data-testid=work-group-version-count]').dataset.visibleCount`),'1')
  assert.equal(await evaluate(`(${groupExpression('版本作品（作者甲）')}).querySelector('[data-testid=work-group-version-count]').dataset.totalCount`),'2')
  await setInput('搜索作品、系列、作者或分类', '')
  await waitFor(`!(${comicExpression(variantWith.id)}) && !(${comicExpression(variantWithout.id)})`, 'clearing search restores default collapse')
  const detailsBeforeExpand = await evaluate("document.querySelector('.selected-summary h2')?.textContent || ''")
  await toggleGroup('版本作品（作者甲）')
  await waitFor(`!!(${comicExpression(variantWith.id)}) && !!(${comicExpression(variantWithout.id)})`, 'remote group expanded')
  assert.equal(await evaluate("document.querySelector('.selected-summary h2')?.textContent || ''"),detailsBeforeExpand,'Expanding a group never loads a synthetic editable record')
  await readKomgaLibrary()
  await waitFor(`!!(${comicExpression(variantWith.id)}) && !document.querySelector('.table-disabled')`, 'explicit expansion survives library refresh')
  await checkComicId(variantWith.id)
  await selectComicId(variantWith.id)
  await waitFor("document.querySelector('.selected-summary h2')?.textContent === '同一作品'", 'independent version selected')
  await toggleGroup('版本作品（作者甲）')
  await waitFor(`!(${comicExpression(variantWith.id)})`, 'checked version folded without losing selection')
  assert.equal(await evaluate(`(${groupExpression('版本作品（作者甲）')}).querySelector('[data-testid=work-group-selected-count]').textContent.includes('1')`),true)
  assert.equal(await evaluate("document.querySelector('.selected-count').textContent.includes('1')"),true)
  await toggle('clear-library-selection')
  await waitFor("!document.querySelector('.selected-count')",'collapsed selection cleared from toolbar')
  assert.equal(await evaluate("document.querySelector('[data-testid=batch-review-sync]').disabled"),true)
  await toggleGroup('版本作品（作者甲）')
  await checkComicId(variantWith.id)
  await selectComicId(variantWith.id)
  assert.equal(await evaluate("document.querySelector('[data-testid=text-version]').textContent.includes('有文字')"),true)
  await chooseOption('text-version', '无文字')
  await click('保存草稿')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '待审核'", 'version draft saved')
  await toggleGroup('版本作品（作者甲）')
  await waitFor(`!(${comicExpression(variantWith.id)})`, 'edited checked book can be reviewed while its group is folded')
  const beforeVersionSync = komgaFixture.patches.length
  await toggle('batch-review-sync')
  await waitFor("document.querySelector('[data-testid=batch-review-preview-table]')?.textContent.includes('文字版本')", 'version field review label')
  assert.equal(await evaluate("document.querySelectorAll('[data-testid=batch-review-preview-table] .el-table__body .el-table__row').length"),1,'Only the selected version enters batch review')
  await toggle('batch-review-confirm')
  await waitFor("document.querySelector('[data-testid=batch-review-submit]')?.disabled === false", 'version review confirmed')
  await toggle('batch-review-submit')
  await waitFor("document.querySelector('[data-testid=batch-review-results]')", 'version review sync result')
  const versionPatches = komgaFixture.patches.slice(beforeVersionSync)
  assert.deepEqual(versionPatches.map(item=>[item.kind,item.id,Object.keys(item.body).sort()]), [['books','variant-with',['notableEntities','notableEntitiesLock','tags','tagsLock']]])
  assert.ok(komgaFixture.books.get('variant-with').metadata.notableEntities.some(item=>item.kind==='文字版本' && item.name==='无文字'))
  assert.ok(komgaFixture.books.get('variant-with').metadata.tags.includes('文字版本:无文字'))
  assert.equal(komgaFixture.books.get('variant-with').metadata.tags.includes('无字版'), false)
  assert.equal(komgaFixture.books.get('variant-with').metadata.number, '1')
  assert.deepEqual(komgaFixture.books.get('variant-without'), originalOtherVersion)
  assert.deepEqual(komgaFixture.series.get('variant-with-series'), originalVariantSeries)
  assert.deepEqual(komgaFixture.books.get('variant-with').readProgress, { page: 1, completed: false })
  catalog = await call('bootstrap')
  assert.equal(catalog.comics.find(book=>book.id===variantWith.id).status,'approved')
  assert.equal(catalog.comics.find(book=>book.id===variantWithout.id).status,'draft')
  await toggle('batch-review-done')
  await waitFor("!document.querySelector('[data-testid=batch-review-results]')", 'version sync result closed')
  if (await evaluate("!!document.querySelector('.selected-count')")) await toggle('clear-library-selection')
  const folderVariantWork = path.join(fixture, '版本来源', '[pink-noise (みずいろめがね)] 合集', '[2011.08] Cattleya & Lana [穿刺個人漢化]')
  for (const [version, count] of [['有文字', 2], ['无文字', 3]]) {
    const folder = path.join(folderVariantWork, version)
    await fs.mkdir(folder, {recursive:true})
    for (let index=1;index<=count;index++) await fs.writeFile(path.join(folder,index+'.png'),page)
  }
  picking = [folderVariantWork, source, folderVariantWork]
  await click('添加漫画')
  await click('选择文件夹')
  await waitFor("document.querySelectorAll('.folder-tree-node').length === 2", 'same picker accepts multiple folders and deduplicates locations')
  await click('读取并预览')
  const editionRows = "[...document.querySelectorAll('[data-testid=import-table] tr')].filter(row => row.querySelector('.import-title')?.textContent === 'Cattleya & Lana')"
  await waitFor(`${editionRows}.length === 2`, 'both editions appear in the multi-folder preview')
  assert.equal(await evaluate("document.querySelector('[data-testid=import-table]').textContent.includes('Cattleya & Lana')"),true)
  assert.equal(await evaluate("document.querySelector('[data-testid=import-table]').textContent.includes('みずいろめがね')"),true)
  for (const version of ['有文字','无文字']) {
    assert.equal(await evaluate(`${editionRows}.some(row => [...row.querySelectorAll('[data-testid=import-version-labels] span')].some(label => label.textContent === ${JSON.stringify(version)}))`), true)
  }
  await capture('ComicManage-version-folders-preview.png')
  await click('导入选中 2 本')
  await waitFor("document.body.innerText.includes('已导入 2 本作品')",'folder editions imported')
  await dismissResult()
  catalog = await call('bootstrap')
  const folderEditions = catalog.comics.filter(book=>book.source.startsWith(folderVariantWork+path.sep))
  assert.equal(folderEditions.length,2)
  assert.equal(new Set(folderEditions.map(book=>book.metadata.work_group)).size,1)
  assert.ok(folderEditions[0].metadata.work_group)
  assert.deepEqual(folderEditions.map(book=>book.page_count).sort(),[2,3])
  for (const book of folderEditions) {
    assert.equal(book.metadata.title,'Cattleya & Lana')
    assert.equal(book.metadata.collection_mode,'oneshot')
    assert.equal(book.metadata.series,'')
    assert.equal(book.metadata.number,'')
  }
  await chooseOption('source-filter','全部来源')
  await waitFor(`!!(${groupExpression('Cattleya & Lana')})`, 'folder editions start in one collapsed group')
  assert.equal(await evaluate(`(${groupExpression('Cattleya & Lana')}).querySelector('[data-testid=work-group-toggle]').getAttribute('aria-expanded')`),'false')
  for (const book of folderEditions) assert.equal(await evaluate(`!!(${comicExpression(book.id)})`),false)
  await capture('ComicManage-grouped-versions-collapsed.png')
  await setInput('搜索作品、系列、作者或分类','Cattleya & Lana')
  await waitFor("document.querySelectorAll('[data-testid=comic-row]').length===2",'search reveals both folder editions under their group')
  assert.equal(await evaluate("document.querySelectorAll('[data-testid=work-group-row]').length"),1)
  await setInput('搜索作品、系列、作者或分类','')
  await waitFor(`!(${comicExpression(folderEditions[0].id)}) && !(${comicExpression(folderEditions[1].id)})`,'clearing folder search folds the previously untouched group')
  await toggleGroup('Cattleya & Lana')
  const folderWithText=folderEditions.find(book=>book.metadata.text_version==='有文字')
  const folderWithoutText=folderEditions.find(book=>book.metadata.text_version==='无文字')
  const otherFolderMetadata=structuredClone(folderWithoutText.metadata)
  await checkComicId(folderWithText.id)
  await selectComicId(folderWithText.id)
  await waitFor("document.querySelector('.selected-summary h2')?.textContent==='Cattleya & Lana'",'parent work title in editor')
  assert.equal(await evaluate("document.querySelector('[data-testid=text-version]').textContent.includes('有文字')"),true)
  if (await evaluate("document.querySelector('[data-testid=expand-metadata]').textContent.includes('展开')")) await toggle('expand-metadata')
  await chooseOption('genre','漫画')
  await chooseOption('age-rating','全年龄')
  await setField('summary','只编辑并审核有文字版本')
  await click('保存草稿')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent==='待审核'",'selected folder edition draft saved')
  await evaluate("document.querySelector('.review-checkbox').click()")
  await click('审核通过')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent==='已审核'",'only selected folder edition approved')
  catalog=await call('bootstrap')
  assert.equal(catalog.comics.find(book=>book.id===folderWithText.id).metadata.summary,'只编辑并审核有文字版本')
  assert.equal(catalog.comics.find(book=>book.id===folderWithText.id).status,'approved')
  assert.equal(catalog.comics.find(book=>book.id===folderWithoutText.id).status,'draft')
  assert.deepEqual(catalog.comics.find(book=>book.id===folderWithoutText.id).metadata,otherFolderMetadata,'Editing and reviewing one child must preserve its sibling')
  await setInput('搜索作品、系列、作者或分类','Cattleya & Lana')
  await evaluate("[...document.querySelectorAll('.navigation button')].find(button=>button.querySelector('span')?.textContent==='待审核').click()")
  await waitFor(`!!(${comicExpression(folderWithoutText.id)}) && !(${comicExpression(folderWithText.id)})`,'status filter reveals only the pending version')
  assert.equal(await evaluate(`(${groupExpression('Cattleya & Lana')}).querySelector('[data-testid=work-group-version-count]').dataset.visibleCount`),'1')
  assert.equal(await evaluate(`(${groupExpression('Cattleya & Lana')}).querySelector('[data-testid=work-group-version-count]').dataset.totalCount`),'2')
  assert.equal(await evaluate(`(${groupExpression('Cattleya & Lana')}).querySelector('[data-testid=work-group-selected-count]').textContent.includes('1')`),true,'Selection remains explicit even when the selected sibling is filtered out')
  await toggle('clear-library-selection')
  await waitFor("!document.querySelector('.selected-count')",'filtered hidden child selection cleared')
  assert.equal(await evaluate("document.querySelector('[data-testid=batch-review-sync]').disabled"),true)
  await evaluate("[...document.querySelectorAll('.navigation button')].find(button=>button.querySelector('span')?.textContent==='全部漫画').click()")
  await waitFor("document.querySelectorAll('[data-testid=comic-row]').length===2",'all independent versions restored')
  await selectComicId(folderWithoutText.id)
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent==='待审核'",'unreviewed sibling remains individually editable')
  await capture('ComicManage-version-folders-library.png')
  await capture('ComicManage-grouped-versions-expanded.png')
  window.setSize(1180, 800)
  await delay(200)
  assert.equal(await evaluate("document.querySelector('.review-footer').getBoundingClientRect().bottom <= window.innerHeight"), true)
  await capture('ComicManage-Electron-compact.png')
  assert.equal(javascriptErrors.filter(x => /Uncaught|TypeError|ReferenceError/.test(x)).length, 0, javascriptErrors.join('\n'))
  window.destroy()
  await desktop.bridge.stop()
  await komgaFixture.close()
  const result = { passed: true, checks: 'isolated renderer, lazy multi-folder checks, read-only parsed preview, import defaults, unchecked siblings excluded, expanded metadata editor, independent title and series, volume/number/count/day, conditional R18 correction, source URLs, namespaced custom tags, Komga Characters and tags, encrypted Komga configuration, connection test, reviewed native metadata direct sync without repackaging, partial field preservation, supplementary payload and manifest, One-Shot path and XML, review, preview, cover markers, verified export, batch edit changes only checked metadata and revokes review, remove without file deletion, rescan skips removed, restore preserves metadata, backup, remote-only library read and authenticated preview, no-change review sends zero PATCH, remote conflict rebase preserves edits, title-only differential sync, checked remote batch fields and shared publisher sync preserve native age/genre/direction, v1.3 optional work-group and text-version fields, checked-only batch version edits, independent same-title One-Shots, version tags and Book entities, no series or read-progress edits, durable automatic entity task UI, real idle scheduler, entity-only reconciliation and lock verification, current-connection retry without repackaging, parent work titles and authors inherited for version subfolders, distinct edition badges and page counts, collapsed work groups, visible-only header selection, independent child selection editing and review, hidden checked child badges and explicit clear, search and status reveal only matching versions, expansion survives refresh, website metadata preview, saved model settings with encrypted key, real local model request and draft-only candidates, compact layout', fixture, screenshots }
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
