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
  const source = path.join(fixture, '漫画源', '春の旅')
  const output = path.join(fixture, '成品')
  const screenshots = process.env.COMIC_MANAGE_SCREENSHOTS || path.join(fixture, 'screenshots')
  await Promise.all([fs.mkdir(source, { recursive: true }), fs.mkdir(output, { recursive: true }), fs.mkdir(screenshots, { recursive: true }), fs.mkdir(path.join(dataDir, 'electron'), { recursive: true })])
  const page = png()
  for (let i = 1; i <= 3; i++) await fs.writeFile(path.join(source, `${i}.png`), page)
  app.setPath('userData', path.join(dataDir, 'electron'))
  await app.whenReady()
  let picking = source
  dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [picking] })
  dialog.showSaveDialog = async () => ({ canceled: false, filePath: path.join(fixture, 'backup.sqlite3') })
  desktop = await createApplication({ dataDir, show: process.env.COMIC_MANAGE_TEST_SHOW === '1' })
  const window = desktop.window
  window.webContents.setBackgroundThrottling(false)
  const javascriptErrors = []
  window.webContents.on('console-message', (_event, level, message) => { if (level === 3) javascriptErrors.push(message) })
  const evaluate = code => window.webContents.executeJavaScript(code, true)
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
  const metadata = { original_title: '春の旅', chinese_title: '春日旅途', authors: ['森野'], publisher: '', translator: '示例汉化组', genre: '短篇漫画', tags: ['日常'], translated: true, uncensored: false, full_color: true, language: 'zh-Hans', age_rating: 'Unknown', summary: '用于验证新界面的合成样本。', year: '', cover_index: 0, original_xml: '' }
  const call = (method, params = {}) => evaluate(`window.comic.invoke(${JSON.stringify(method)}, ${JSON.stringify(params)})`)
  const setInput = (placeholder, value) => evaluate(`(() => { const item = [...document.querySelectorAll('input')].find(x=>x.placeholder === ${JSON.stringify(placeholder)}); if(!item) throw new Error('Input missing'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(item,${JSON.stringify(value)}); item.dispatchEvent(new Event('input',{bubbles:true})); })()`)

  await waitFor("document.body.innerText.includes('从第一本漫画开始')", 'empty state')
  assert.equal(await evaluate('typeof window.require'), 'undefined')
  assert.equal(await evaluate('typeof window.process'), 'undefined')
  assert.equal(await evaluate("window.comic.invoke('arbitrary-shell',{}).then(()=>false,()=>true)"), true)
  await capture('ComicManage-Electron-empty.png')
  await click('添加漫画目录')
  await waitFor("document.body.innerText.includes('已扫描 1 本作品')", 'real scan')
  await dismissResult()
  let catalog = await call('bootstrap')
  assert.equal(catalog.comics.length, 1)
  const id = catalog.comics[0].id
  await call('save', { id, metadata })
  await click('重新扫描')
  await waitFor("document.body.innerText.includes('已扫描 1 本作品')", 'rescan')
  await dismissResult()
  await waitFor("!document.querySelector('.table-disabled')", 'scan task released')
  await evaluate("document.querySelector('.el-table__body tr .comic-cell').click()")
  await waitFor("document.querySelector('.metadata-form input')?.value === '春の旅'", 'details')
  await setInput('汉化作品填写中文名', '春日旅途·校订')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '未保存'", 'dirty status')
  await click('保存草稿')
  await waitFor("document.querySelector('.details-panel .status-pill')?.textContent === '待审核'", 'saved draft')
  catalog = await call('bootstrap')
  assert.equal(catalog.comics[0].metadata.chinese_title, '春日旅途·校订')
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
  assert.equal(catalog.comics[0].status, 'packaged')
  assert.equal(catalog.comics[0].metadata.cover_index, 1)
  assert.equal((await fs.readFile(path.join(source, '1.png'))).equals(page), true)
  assert.equal((await fs.stat(catalog.comics[0].output)).isFile(), true)
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
  const result = { passed: true, checks: 'isolated renderer, empty state, scan, edit, review, preview, cover, XML, plan, verified export, backup, compact layout', fixture, screenshots }
  await fs.writeFile(path.join(root, 'build', 'desktop-last-result.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result))
  app.exit(0)
}

const timeout = setTimeout(() => { console.error('Desktop integration test timed out'); desktop?.window.destroy(); desktop?.bridge.child.kill(); app.exit(1) }, 90000)
timeout.unref()
run().catch(async error => {
  await fs.writeFile(path.join(root, 'build', 'desktop-last-result.json'), JSON.stringify({ passed: false, error: error.stack }, null, 2))
  console.error(error); desktop?.window.destroy(); await desktop?.bridge.stop(); app.exit(1)
})
