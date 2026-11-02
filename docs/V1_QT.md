# Comic Manage · v1 初始版

Windows 本地漫画整理软件：浏览本机 / NAS 图片文件夹与已有 CBZ，人工编辑和审核元数据，生成经过校验、可交给 Kavita 扫描的 CBZ。

本分支用 **Python + PySide6 + SQLite** 重建原 Tauri/Vue 骨架。v1 已实现实际文件处理流程，不包含网络刮削。软件不发送漫画、目录或元数据到服务器。

## 启动

**Windows 便携版**：解压 `ComicManage-v1-windows-x64.zip`，双击目录内 `ComicManage.exe`。保留整个目录，不要单独移动 exe。便携包不需要安装 Python；资料库默认仍保存在本机用户数据目录。

**源码版**：安装 64 位 Python 3.11 或更新版本，解压本仓库，双击 `start.bat`。首次启动会联网安装固定版本的 PySide6。也可以执行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

除 Qt 外，核心文件处理使用 Python 标准库。SQLite 资料库不应放在 NAS 上；漫画源目录和成品目录可以使用映射盘或 `\\NAS\share\漫画`。共享目录的 Windows 访问权限需事先配置好。

## 使用流程

1. **添加目录**：递归扫描。每个含直接图片的文件夹视为一本作品；嵌套文件夹若也含图片，会作为另外一本作品。一个现有 CBZ 视为一本作品。扫描不会移动或改写源文件。
2. **选择成品目录**：使用独立目录，建议与原始漫画目录并列。重新扫描时会跳过当前成品目录和本机资料库目录。
3. **编辑资料**：填完整原名、中文名、作者、社团、汉化组、类型、标签、语言、年份、简介、年龄分级。多作者用逗号分隔。预览各页，并选择封面。可查看将写入的 XML。
4. **保存草稿**：人工修改保存在 SQLite，不会写回原始 ComicInfo。任何修改都撤销先前审核状态。
5. **人工审核**：核对标题、作者、页序和封面，勾选确认后点击“审核通过”。尚未审核的作品不能生成 CBZ。
6. **生成所选 CBZ**：Ctrl / Shift 多选已审核作品，查看路径计划，确认后生成。每本独立完成、独立校验；错误和取消不会回滚此前已成功的作品。全部源文件保留。
7. 把**成品目录**添加到 Kavita 的 Manga 书库并扫描。请先用几本作品核对实际显示、页序和双标题搜索。

支持按中文名、原名、作者、标签和汉化组搜索，按作者和处理状态筛选。v1 的审核为逐本确认，打包支持多选。

## 元数据规则

| 内容 | ComicInfo 写法 |
| --- | --- |
| 已汉化 | `Series` / `Title` 为完整中文名；`LocalizedSeries` 为完整日文原名 |
| 未汉化 | `Series` / `Title` 为完整原名；不生成 `LocalizedSeries` |
| 多位作者 | `Writer` 为逗号分隔作者列表，不复制多份漫画 |
| 正向标记 | 只为勾选项生成 `汉化`、`无修正`、`全彩` 标签 |
| 默认情况 | 不生成 `生肉`、`有修正`、`黑白` 等反向标签 |
| 语言 | 汉化切换时默认 `zh-Hans`；未汉化默认 `ja`，可手动填写 `zh-Hant` 等 |
| 封面 | `Pages/Page` 的 `Image` 为从 0 开始的页序，`Type="FrontCover"` |

`ComicInfo.xml` 写在 CBZ 根目录。已有 ComicInfo 的其他字段（例如 Number、Volume、Web）保留；本软件管理的字段和封面信息按当前审核数据重写。v1 只读取 UTF-8 XML；无效或不支持的原 XML 会显示警告，并改为人工录入。原 XML 始终留在源文件中。未填写的年龄分级为 Unknown，不推断作品是否成人向。

Kavita 官方说明 `LocalizedSeries` 支持第二名称搜索，也会参与合并系列。因此，其他作者的已审核同名作品会阻止审核，需人工调整标题或使用独立资料库管理；这只能检查**本资料库**，不能识别 Kavita 内已有的全部同名作品。

参考：[Kavita 漫画元数据](https://wiki.kavitareader.com/guides/metadata/comics/)、[Kavita 扫描规则](https://wiki.kavitareader.com/guides/scanner/)。Kavita 的 `LocalizedSeries` 属于其扩展字段，不能据此保证其他阅读器完全兼容。

## 文件与校验规则

- 图片扩展名：JPG / JPEG / PNG / WebP / GIF / BMP / TIFF / AVIF。预览由本机 Qt 图片插件决定，不支持预览的图片仍可按原字节打包。v1 校验字节完整性，不保证源图片可成功解码。
- 页序按源文件名自然排序，例如 `1, 2, 10`；输出为 `pages/00001.jpg` 等。嵌套 CBZ 按完整相对路径排序。若需要自定义页序，应先整理源文件名，重新扫描再审核。
- 图片**不解码重编码**，使用 ZIP_STORED 避免重复压缩。现有 CBZ 的非图片附件保留到 `extras/`；图片文件夹只打包本层图片和新 ComicInfo，其他文件及子目录留在原处。
- 成品路径：`成品目录/作者A & 作者B/作品标题 [来源ID].cbz`。文件名会替换 Windows 禁用字符并限制长度；XML 中保留完整标题。来源 ID 基于源路径，相同标题的不同来源不会直接相互覆盖。
- 每本先写成品目录下的随机 `.partial` 文件，对每张图片和每个附件重新读取，验证 ZIP CRC、SHA-256、清单、页数及 XML。通过后才原子发布为正式 CBZ；Windows 重命名拒绝已存在目标。
- 目标已存在时拒绝生成；源文件在预览后或打包中变化时拒绝发布。普通取消和异常会清理当次临时文件。断电 / 强制结束后可能留下 `.partial`，确认程序未运行后可手动清理。
- 扫描签名依据路径、大小、修改时间，以及 CBZ 成员 CRC / XML 摘要。它用于检测常规源变化，不是内容去重工具；源文件大小和修改时间被人为同时恢复时，文件夹签名可能无法识别变化。打包的 SHA-256 用于证明此次读取与成品字节一致。
- 单条目上限 512 MiB、单本解压总量 64 GiB、10 万条目；拒绝加密 CBZ、危险内部路径、重复条目和符号链接。不支持 CBR、RAR、7z、PDF 或 ZIP 后缀导入；可先在外部转换为 CBZ。
- NAS 离线或扫描中断不会删除数据库记录。缺失源文件会在预览 / 审核 / 生成时报告。网络阻塞时取消需等待系统文件操作返回。
- **v1 不执行源文件删除、替换现有 CBZ、批量覆盖或自动刮削。**

## 本机资料与备份

默认资料库由 Qt 的 `AppLocalDataLocation` 决定，Windows 通常位于 `%LOCALAPPDATA%\ComicManage\ComicManage\catalog.sqlite3`。可用 `--data-dir` 指定独立目录，例如：

```powershell
.\ComicManage.exe --data-dir D:\ComicManageData
```

“备份资料库”使用 SQLite 备份 API，备份元数据、来源路径、审核和校验记录，不包含漫画。恢复时关闭软件，将备份复制为数据目录下的 `catalog.sqlite3`，或放入新的 `--data-dir` 目录。请保留原库备份。源路径变化后会视为新来源，v1 不自动合并移动后的记录，也不自动清理旧索引。

## 开发与测试

```powershell
$env:PYTHONPATH = "$PWD\src"
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用临时合成文件与 Qt 测试图片，包括编辑 → 审核 → 预览计划 → 打包的实际界面流程、双标题搜索、校验失败、源变化、重名、取消、CBZ 附件和备份。尚未在用户实际 NAS 与 Kavita 实例上验收。

构建 Windows 64 位便携包：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe scripts\build_windows.py
```

结果位于 `dist/ComicManage/` 与 `dist/ComicManage-v1-windows-x64.zip`。使用 `--onedir` 保留 Qt 插件及第三方许可，避免单文件解包启动。首次构建的 exe 未进行代码签名。

## 后续范围

- v2：可插拔来源刮削、候选对照、原始返回与人工字段分离。
- v3：作者别名、来源 ID、内容去重、增量导入、替换与源清理策略。

当前没有假刮削结果或自动通过机制。后续接入来源必须遵循相同的人工确认规则。
