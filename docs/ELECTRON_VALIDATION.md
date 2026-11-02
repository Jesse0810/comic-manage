# Electron 1.1.0 验证记录

日期：2026-11-02。验证使用 Windows 11 x64、Node.js 24.16、Electron 45.2.1、Vue 3.5.44、Element Plus 2.14.7 和 Python 3.12。

## 已验证

- 22 项原文件处理核心测试通过。
- 17 项 JSON-lines 服务测试通过：旧库结构、人工字段保留、审核、生成计划失效、取消、备份、UTF-8 子进程协议与资料库锁。
- 8 项 Node 进程通信测试通过：非法参数、UTF-8 分片、请求关联、进度、取消、异常 stdout 和后台退出。
- Vue TypeScript 检查与 Vite 生产构建通过。
- 实际 Windows Electron 便携程序启动成功，preload、Vue、Element Plus、内置后台和原资料目录连接正常。
- Electron 窗口完整流程通过：空状态 → 扫描合成漫画 → 修改中文名 → 保存草稿 → 人工确认 → 审核 → 预览翻页 → 换封面 → 再次修改撤销确认 → 保存并重新审核 → 查看 XML 封面索引 → 选择成品目录 → 确认生成计划 → 生成并校验 CBZ → 备份资料库。
- 流程结束确认源图片原字节保留、成品存在、数据库状态为 packaged、封面页索引正确、备份存在。
- 隔离渲染进程内没有 window.require / window.process，任意方法请求被拒绝。
- 1180×800 小窗口下审核栏保持可见，页面、资料和 XML 分区独立滚动。

核心与服务使用 Python 标准库；Electron 便携包未包含 Qt。内置后台为 PyInstaller console 模式，由 Electron 隐藏启动，不依赖系统 Python。

本次命令执行环境限制 Chromium 直接启动，桌面集成测试通过原生 Windows 启动的同版本隔离 Electron 测试程序执行，启用正常进程隔离与渲染沙箱，没有关闭这些保护。测试入口为 electron/smoke.cjs，样本与结果位于忽略的 build/ 目录；正常 Windows 开发环境使用 npm run test:desktop。

## 待实际环境验收

- 用户的 NAS / SMB 读写、长时间断网、空间不足与海量库性能。
- 实际 Kavita 版本对双标题、封面、阅读顺序和类型的显示。
- Windows 10 与其他 DPI / 语言环境、未签名程序的分发体验。

本次为桌面框架与界面重构，不增加网络刮削、自动删除或覆盖替换。
