'use strict'

// Integration test of our own Electron window, real preload and real file service.
// Native pickers select synthetic fixtures; the IPC/file-processing path is unchanged.
const { app, dialog } = require('electron')
const { createApplication } = require('./main.cjs')
app.on('window-all-closed', () => {}) // Keep the test alive until service shutdown is verified.
const fs = require('node:fs/promises')
const path = require('node:path')
const assert = require('node:assert/strict')
const { deflateSync } = require('node:zlib')

const root = path.resolve(__dirname, '..')
let desktop
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
  let picking = sourceRoot
  dialog.showOpenDialog = async () => ({ canceled: false, filePaths: Array.isArray(picking) ? picking : [picking] })
  dialog.showSaveDialog = async () => ({ canceled: false, filePath: path.join(fixture, 'backup.sqlite3') })
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
  const click = text => evaluate(`(() => { const item = [...document.querySelectorAll('button')].find(b => b.getClientRects().length && !b.disabled && b.textContent.trim() === ${JSON.stringify(text)}); if (!item) throw new Error('Button missing: '+${JSON.stringify(text)}); item.click() })()`)
  const capture = async name => {
    await delay(250) // Let transitions and Chromium paint catch up with Vue state.
    await fs.writeFile(path.join(screenshots, name), (await window.webContents.capturePage()).toPNG())
  }
  const dismissResult = async () => {
    await click('知道了')
    await waitFor("![...document.querySelectorAll('.el-overlay')].some(e => e.getClientRects().length && getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden')", 'result dialog dismissed')
  }
  const metadata = { original_title: '春の旅', chinese_title: '春日旅途', authors: ['森野'], publisher: 'pink-noise', translator: '示例汉化组', genre: '同人志', tags: ['日常'], translated: true, uncensored: false, full_color: true, language: 'zh-Hans', age_rating: 'Unknown', summary: '用于验证新界面的合成样本。', year: '2019', month: '10', cover_index: 0, original_xml: '' }
  const call = (method, params = {}) => evaluate(`window.comic.invoke(${JSON.stringify(method)}, ${JSON.stringify(params)})`)
  const setInput = (placeholder, value) => evaluate(`(() => { const item = [...document.querySelectorAll('input')].find(x=>x.placeholder === ${JSON.stringify(placeholder)}); if(!item) throw new Error('Input missing'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(item,${JSON.stringify(value)}); item.dispatchEvent(new Event('input',{bubbles:true})); })()`)
  const toggle = testId => evaluate(`document.querySelector('[data-testid="'+${JSON.stringify(testId)}+'"]').click()`)
  const selectValue = async (testId, value) => {
    await evaluate(`(() => { const field=document.querySelector('[data-testid="'+${JSON.stringify(testId)}+'"] input'); field.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(field,${JSON.stringify(value)}); field.dispatchEvent(new Event('input',{bubbles:true})); field.click(); })()`)
    await waitFor(`[...document.querySelectorAll('.el-select-dropdown__item')].some(item=>item.getClientRects().length && item.textContent.trim()===${JSON.stringify(value)})`, `select option ${value}`)
    await evaluate(`[...document.querySelectorAll('.el-select-dropdown__item')].find(item=>item.getClientRects().length && item.textContent.trim()===${JSON.stringify(value)}).click()`)
    await evaluate(`document.querySelector('.el-dialog__header').click()`)
  }
  const folderCheckbox = folder => evaluate(`(() => { const item=[...document.querySelectorAll('.folder-tree-node')].find(item=>item.title===${JSON.stringify(folder)}); if(!item) throw new Error('Missing folder'); item.closest('.el-tree-node__content').querySelector('.el-checkbox').click(); })()`)

  await waitFor("document.body.innerText.includes('从第一本漫画开始')", 'empty state')
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
  await selectValue('import-genre', '同人志')
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
  const imported = catalog.comics.find(book => book.metadata.original_title === '春の旅')
  const id = imported.id
  const secondId = catalog.comics.find(book => book.id !== id).id
  assert.deepEqual(imported.metadata.authors, ['统一作者'])
  assert.equal(imported.metadata.genre, '同人志')
  assert.equal(imported.metadata.publisher, 'pink-noise')
  assert.equal(imported.metadata.translator, '未知汉化')
  assert.equal(imported.metadata.year, '2019')
  assert.equal(imported.metadata.month, '10')
  assert.equal(imported.metadata.uncensored && imported.metadata.full_color, true)
  await call('save', { id, metadata })
  await click('重新扫描')
  await waitFor("document.body.innerText.includes('已扫描 2 本作品')", 'rescan')
  await dismissResult()
  await waitFor("!document.querySelector('.table-disabled')", 'scan task released')
  await evaluate("[...document.querySelectorAll('.comic-cell')].find(item=>item.textContent.includes('春の旅')).click()")
  await waitFor("document.querySelector('.metadata-form input')?.value === '春の旅'", 'details')
  await setInput('汉化作品填写中文名', '春日旅途·校订')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '未保存'", 'dirty status')
  await click('保存草稿')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '待审核'", 'saved draft')
  catalog = await call('bootstrap')
  assert.equal(catalog.comics.find(book => book.id === id).metadata.chinese_title, '春日旅途·校订')
  await evaluate("document.querySelector('.review-checkbox').click()")
  await click('审核通过')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '已审核'", 'approval')
  await waitFor("document.querySelector('.mini-cover img')?.complete", 'image preview')
  await capture('ComicManage-Electron-details.png')
  await evaluate("[...document.querySelectorAll('.el-tabs__item')].find(x=>x.textContent.includes('页面预览')).click()")
  await waitFor("document.querySelector('.large-page img')?.complete", 'large preview')
  await evaluate("document.querySelector('[aria-label=下一页]').click()")
  await waitFor("document.querySelector('.page-counter input')?.value === '2'", 'page navigation')
  await delay(150)
  await click('将此页设为封面')
  await evaluate("document.querySelector('.review-checkbox').click()")
  await evaluate("[...document.querySelectorAll('.el-tabs__item')].find(x=>x.textContent === '基本资料').click()")
  await setInput('汉化作品填写中文名', '春日旅途·封面校订')
  await delay(100)
  assert.equal(await evaluate("document.querySelector('.review-checkbox input').checked"), false, 'Every metadata edit revokes the review checkbox')
  await click('保存草稿')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '待审核'", 'cover draft')
  await evaluate("document.querySelector('.review-checkbox').click()")
  await click('审核通过')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '已审核'", 'cover reapproval')
  await evaluate("[...document.querySelectorAll('.el-tabs__item')].find(x=>x.textContent === 'ComicInfo.xml').click()")
  await waitFor("document.querySelector('.xml-tab pre')?.textContent.includes('Image=\"1\"')", 'ComicInfo cover index')
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

  await evaluate("[...document.querySelectorAll('.table-area .el-table__header .el-checkbox')].find(item=>item.getClientRects().length).click()")
  await waitFor("document.querySelector('.selected-count')?.textContent.includes('2')", 'two comics checked')
  await setInput('搜索中日文标题、作者或标签', '不存在的筛选结果')
  await waitFor("document.body.innerText.includes('没有找到匹配的作品')", 'empty filter with reserved selection')
  await click('清除筛选')
  await waitFor("document.querySelectorAll('.table-area .el-table__body .el-checkbox input:checked').length === 2", 'checkboxes restored after empty filter')
  await click('批量编辑')
  await toggle('batch-enable-authors')
  await selectValue('batch-authors', '批量校订作者')
  await toggle('batch-enable-genre')
  await selectValue('batch-genre', '单行本')
  await click('保存 2 本作品')
  await waitFor("document.body.innerText.includes('已更新 2 本作品')", 'batch metadata edit')
  await waitFor("!document.querySelector('.table-disabled') && !document.querySelector('.batch-edit-dialog')", 'batch dialog released')
  catalog = await call('bootstrap')
  assert.equal(catalog.comics.every(book => book.status === 'draft' && book.metadata.authors[0] === '批量校订作者' && book.metadata.genre === '单行本'), true)
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
  assert.deepEqual(catalog.comics.find(book => book.id === secondId).metadata.authors, ['批量校订作者'])
  await click('备份资料库')
  await waitFor("document.body.innerText.includes('已备份目录索引')", 'catalog backup')
  await dismissResult()
  assert.ok((await fs.stat(path.join(fixture, 'backup.sqlite3'))).size > 0)
  window.setSize(1180, 800)
  await delay(200)
  assert.equal(await evaluate("document.querySelector('.review-footer').getBoundingClientRect().bottom <= window.innerHeight"), true)
  await capture('ComicManage-Electron-compact.png')
  assert.equal(javascriptErrors.filter(x => /Uncaught|TypeError|ReferenceError/.test(x)).length, 0, javascriptErrors.join('\n'))
  window.destroy()
  await desktop.bridge.stop()
  const result = { passed: true, checks: 'isolated renderer, lazy multi-folder checks, read-only parsed preview, import defaults, unchecked siblings excluded, edit, review, preview, cover, XML/month, verified export, batch edit revokes review, remove without file deletion, rescan skips removed, restore preserves metadata, backup, compact layout', fixture, screenshots }
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
