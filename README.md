# Comic Manage · Electron 版

本地漫画整理软件，采用 **Electron + Vue 3 + TypeScript + Element Plus**。浏览本机或 NAS 图片目录与 CBZ，编辑中日文资料、预览页面、人工审核，并生成经过完整性校验的 CBZ。

Electron 负责桌面窗口、系统对话框与进程管理，Vue / Element Plus 提供界面。已验证的 Python 扫描、SQLite、ComicInfo 和无损打包逻辑保留为独立本地服务，通过进程管道通信，**不开放 HTTP 端口**。便携包内置服务，无需用户安装 Python 或 Node.js。

## Windows 便携版

解压 `ComicManage-Electron-1.2.0-windows-x64.zip`，双击 `ComicManage.exe`，保留整个目录。

1. 添加一个或多个本地 / NAS 位置，在目录树中勾选需要的文件夹，或多选已有 CBZ。
2. 预览识别结果，勾选需要入库的作品，可统一填写作者和类型，再确认导入。
3. 选择独立成品目录。
4. 选择作品，编辑原名、中文名、作者、标签等信息，预览页面并选择封面。
5. 保存草稿；核对后勾选审核确认，再点击“审核通过”。
6. 多选已审核作品，查看生成计划，确认目标路径后生成。

图片不转码，逐图片与附件验证 ZIP CRC 和 SHA-256，校验成功后才发布正式 CBZ。不会覆盖已有目标，不会删除原始图片。修改元数据或扫描到源变化后需要重新审核。

已汉化作品使用中文 Series 和日文 LocalizedSeries；未汉化作品只保留原名。正向标签只生成“汉化”“无修正”“全彩”。完整规则见 [v1 元数据与文件规则](docs/V1_QT.md#元数据规则)，这些规则继续适用。

## 选择导入、批量编辑与移除

目录树支持跨位置勾选子文件夹，勾选一个文件夹会递归查找其中的漫画。预览阶段不写入资料库；确认时只导入勾选的作品。重新扫描只检查已导入的源目录，不会把本次未勾选的相邻漫画补进列表。旧版已登记的递归扫描根仍按原方式工作。

导入时可统一指定作者和类型（同人志、商业志、单行本，也可自行输入）。默认只应用于新作品，已有人工资料保持原样；明确开启覆盖已有作者 / 类型后才更新已有作品。列表中也可以多选作品进行批量编辑，编辑后撤销审核，需要重新确认。

“移出列表”保留源文件、成品和原有资料，重新扫描不会自动恢复。之后在导入预览中再次选中该作品，可恢复原有资料并重新审核。

## 文件夹名识别

新导入且没有有效 ComicInfo.xml 的作品会从文件夹名 / CBZ 文件名提取资料，例如：

```text
[2019.10] [pink-noise (みずいろめがね)] 漫画原名 [畜牧系學姊漢化組] [无修正] [全彩]
```

| 文件名内容 | 识别结果 |
| --- | --- |
| 开头的 `[2019.10]` | 发售年 2019、月 10，写入 ComicInfo 的 Year / Month |
| `[pink-noise (みずいろめがね)]` | 社团 / 出版社 pink-noise，作者 みずいろめがね |
| `[畜牧系學姊漢化組]` | 汉化组 畜牧系學姊漢化組，并标记汉化 |
| `[中国翻訳]`、`[未知汉化]` | 统一为汉化组 未知汉化 |
| `[无修正]`、`[全彩]` | 对应正向标记 |
| 剩余名称 | 漫画原名 |

录入值去除标签外层方括号。未识别或异常格式会保留供核对，不猜测中文译名；汉化作品仍需人工填写中文名后才能审核。有效的原有 ComicInfo.xml 优先，重新扫描和再次导入默认保留人工修改，不会自动覆盖已有原名。

## 原有资料库

Windows 默认仍使用 `%LOCALAPPDATA%\ComicManage\ComicManage\catalog.sqlite3`，与 Qt 版相同。不会另建空库覆盖旧资料。Chromium 缓存在该目录的 electron/ 子目录。

**切换前退出旧 Qt 版并备份资料库，不要同时操作同一个资料库。** 可在软件中备份，或关闭软件后复制 SQLite 文件。备份不含漫画图片。

可以指定独立本机数据目录：

```powershell
.\ComicManage.exe --data-dir D:\ComicManageData
```

漫画源目录与成品目录可位于 NAS，SQLite 资料目录应放本机。共享目录需已配置 Windows 访问权限。NAS 断开不会清理索引；源路径移动后仍视为新来源。

## 源码开发

需要 Node.js 22.12+（建议 24 LTS）与 Python 3.11+。在项目目录执行：

```powershell
py -3 -m venv .venv
npm ci
npm run dev
```

也可双击 start.bat，它会检查开发环境、安装依赖、构建并启动 Electron。`npm run dev` 同时启动 Vite 与 Electron，支持界面热更新。

默认使用项目 .venv 的 Python，可通过 `COMIC_MANAGE_PYTHON` 指定完整路径。文件服务只用标准库，不需要 PySide6。

```powershell
npm run build          # Vue 类型检查与生产构建
npm start              # 启动已构建界面
npm run test:core      # 核心与 JSON 服务测试
npm run test:bridge    # 进程通信边界测试
npm run test:desktop   # 实际 Electron 窗口与文件流程测试
```

开发命令默认读原资料库。测试使用临时合成样本；手工调试可通过 `npm run dev -- --data-dir D:\ComicManageTestData` 隔离资料。

## Windows 构建

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
npm run dist:win
```

结果在 release/，包括 Windows x64 ZIP。Electron 通过 extraResources 启动内置 ComicManageService.exe，不调用用户系统 Python。首次构建未进行代码签名。

## 项目结构

| 目录 | 用途 |
| --- | --- |
| frontend/ | Vue 3 + Element Plus 界面 |
| electron/ | 主进程、隔离 preload、受限 IPC 与服务生命周期 |
| src/comic_manage/service.py | 标准库 JSON-lines 服务、任务取消、一次性生成计划 |
| src/comic_manage/{sources,metadata,repository,packaging}.py | 已有文件与数据库核心 |
| tests/、electron/tests/ | 核心、服务与 IPC 测试 |
| scripts/ | 开发、构建和依赖许可复制 |

旧 Qt 界面源码暂留作历史参考，默认入口已改为 Electron。运行旧界面需单独安装 PySide6-Essentials==6.10.2，再运行 run.py；Electron 便携包不包含 Qt。旧版说明在 [docs/V1_QT.md](docs/V1_QT.md)。

## 当前范围

继续使用人工审核、计划预览与校验发布流程，不含网络刮削、内容去重、自动删除或覆盖替换。大于 32 MiB 的单张图片暂不能预览，仍可原字节打包。TIFF 等格式能否预览由 Chromium 解码支持决定。NAS 取消需等待系统文件读取返回。

实际 NAS、Kavita 入库与海量文件性能仍需用户环境验收。双标题规则参考 [Kavita 官方元数据说明](https://wiki.kavitareader.com/guides/metadata/comics/)。
