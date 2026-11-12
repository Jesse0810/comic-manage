# Komga 元数据规则 v1.1-K

Comic Manage 1.6.0 以用户的 Komga 专用模板为准，对照 Komga 1.30.1 的官方文档和实现。Notable Entities 自动导入需要 Komga 1.29.0 或以上。

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

## 软件内一键同步

1.6.0 在侧栏“Komga 连接设置”中保存服务器地址、API Key 和目标书库。支持部署子路径。测试连接成功后选择书库并保存。API Key 使用系统加密保存在本机，界面只显示已保存状态。更换地址时需重新填写新服务器密钥。

漫画须已被目标 Komga 书库扫描。选中本机已审核作品，点击“同步到 Komga”。支持直接使用本机已审核的当前资料更新标题、作者、简介、册号及数值排序、完整发行日、标签、链接、角色和分类实体。已生成的 CBZ 路径或导入 CBZ 的路径用于精确匹配，之后保存服务器与书库隔离的 Book ID 映射，本地改名也可同步。

本机没有管理的作者职责与实体类别会保留。本机空的简介、标签和链接会清空远端对应字段。同步字段设置对应元数据锁，避免旧 CBZ 的自动刷新覆盖直接同步的值。

只有本次成功选中的漫画覆盖远端整个系列，才更新系列聚合字段。年龄取最高值，类型取并集，总册数取已知最大值，出版方、语言和阅读方向取非空众数。平票或别名冲突时保留该字段并报告提示，其余明确字段继续更新。One-Shot 系列从本册派生标题、简介和总数。

真实系列的同标签别名以本机为准，可以更新或清除，同时保留其他标签的别名。API 无法移动 Book 到其他系列或改变 One-Shot 组织方式，这类变更需要先整理文件再扫描。

Komga API 只能表达完整发行日期。本机只知道部分年月日时，不更新远端日期并在结果中提示。全部日期为空时清空远端日期。非数值册号同步文本，但远端原有数值排序无法用 null 清除，结果会说明。

同步结果分别显示作品与系列的成功、跳过、失败及字段提示。可以取消，已完成的更新保留，未完成项可重新选择后重试。连接与元数据请求不跟随重定向，API Key 不写入本机资料库、同步清单或结果。

## 独立同步清单

工作区信息中保留“导出 Komga 同步清单”，可用于独立 PowerShell 工作流。清单依据当前已生成 CBZ 的 XML 校对后生成。便携包附 resources/scripts/sync-komga.ps1，支持 -ManifestPath 与 -WhatIf。该脚本保留保守的别名合并规则，已有同标签别名的改名或删除需在 Komga 核对处理。

## 官方参考

- [Komga 1.30.1](https://github.com/gotson/komga/releases/tag/1.30.1)
- [ComicInfo 导入](https://komga.org/docs/guides/scan-analysis-refresh/)
- [One-Shots](https://komga.org/docs/guides/oneshots/)
- [Notable Entities](https://komga.org/docs/openapi/referential-metadata/)
- [Book metadata API](https://komga.org/docs/openapi/update-book-metadata/)
- [Series metadata API](https://komga.org/docs/openapi/update-series-metadata/)
