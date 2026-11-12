# Komga 元数据规则 v1.1-K

Comic Manage 1.5.0 以用户的 Komga 专用模板为准，对照 Komga 1.30.1 的官方文档和实现。Notable Entities 自动导入需要 Komga 1.29.0 或以上。

## 作品组织

每本作品明确选择“独立作品 One-Shot”或“多卷 / 连续系列”。

| 编辑信息 | 独立作品 | 系列作品 |
| --- | --- | --- |
| 成品目录 | _oneshots/ | 同一 Series 的共同目录 |
| Title | 作品名称 | 本册或章节名称 |
| Series | XML 省略 | 实际系列名称 |
| Number | XML 省略 | 本册 / 章节编号，整卷也使用此字段 |
| Count | XML 省略 | 已知系列总册数 |
| Volume | 本机旧值供核对 | 本机旧值供核对，导出省略 |
| 系列别名与标签 | 暂存旧值 | 通过 API alternateTitles 补充 |

Komga 按父文件夹组织系列。系列目录使用稳定标识区分清洗文件名后可能同名的系列，不按作者分别建立目录。独立作品需要在 Komga Library 的 One-Shots directory 设置为 /_oneshots。单独省略 XML Series 不会启用 One-Shot。

切换组织方式保留本机原值。批量编辑只写勾选项，切换模式后需人工核对编号、系列与别名。旧库首次升级先自动备份，再撤销旧审核。

## 字段映射

| 信息 | ComicInfo.xml | Komga 行为 |
| --- | --- | --- |
| 作者、译者、独立绘画者 | Writer、Translator、Penciller | Book 的对应作者角色 |
| 发售日期 | Year、Month、Day | Book releaseDate，缺月日时 Komga 展示可能补为 1 |
| 简介 | Summary | Book 简介 |
| 出版方、内容类型、当前语言 | Publisher、Genre、LanguageISO | Series 汇总信息 |
| 年龄分级 | Everyone、MA15+、R18+ | Series 的 0、15、18，同系列取最高值 |
| 阅读方向 | Manga | YesAndRightToLeft 表示右向左 |
| 多条来源 | 同一 Web 内用空格分隔 | Book links |
| 页数 | PageCount 为实际图片数 | Komga 自行分析 media.pagesCount |
| 黑白 / 彩色 | BlackAndWhite | 通过补充实体建立色彩筛选 |
| 角色 | Characters 与独立的 角色: 标签 | Characters 自动进入 character 实体 |
| 原作、发行性质、出版形态、修正状态 | Tags 命名空间 | 通过补充清单建立实体 |

日期只填写已知部分，软件不在 XML 中虚构月日。BlackAndWhite=No 表示彩色或非黑白，不推断为全彩。未知色彩保持 Unknown。作者兼任绘画时只填 Writer。中文原创可以不填译者，未知汉化占位仍需人工核实。

Genre 保存内容类型，题材归入 Tags。汉化、全彩、年龄和普通无修正不重复生成标签。修正状态只用于 R18，默认有修正，只有无修正写入 修正状态:无修正。

ComicInfo 中的封面标记继续保存，Komga 默认封面来自实际首图。改变该标记不会改变 Komga 的封面或图片顺序。旧 SeriesSort、SeriesGroup、StoryArc、AlternateSeries 及 Kavita 扩展 LocalizedSeries 在生成时清理，避免额外系列、合集或阅读清单。

## API 补充清单

详情的“Komga 补充资料”显示实体与系列别名的 JSON。选中已生成的作品，点击“Komga 同步清单”可以保存 komga_notable_entities_manifest.json。清单依据当前成品 XML 校对后生成。

清单为每册保留年龄分级和内容类型辅助实体，用于同系列混合分级、混合类型的逐册筛选。还包括原作、发行性质、出版形态、修正状态及已知色彩。角色由 Characters 自动导入，脚本保留已有 character 及其他未受管实体。

将成品加入 Komga 书库，扫描分析完成后运行便携包内的 scripts/sync-komga.ps1。脚本在本机输入地址和管理员 API Key，选择书库，先预览匹配与更新计划，确认后写入。-WhatIf 只预览。每次同步替换受管实体的当前值，读取并保留其他类别。系列别名先读取合并，同标签不同名称会报告冲突。

同步清单是 API 的输入文件，Komga 扫描 CBZ 时不会自动处理这个 JSON。Notable Entities 与 alternateTitles 存储在 Komga 数据库。新增系列别名可同步；已有同标签别名的改名或清除需在 Komga 核对处理，本地清空不会自动删除服务器别名。

## 官方参考

- [Komga 1.30.1](https://github.com/gotson/komga/releases/tag/1.30.1)
- [ComicInfo 导入](https://komga.org/docs/guides/scan-analysis-refresh/)
- [One-Shots](https://komga.org/docs/guides/oneshots/)
- [Notable Entities](https://komga.org/docs/openapi/referential-metadata/)
- [Book metadata API](https://komga.org/docs/openapi/update-book-metadata/)
- [Series metadata API](https://komga.org/docs/openapi/update-series-metadata/)
