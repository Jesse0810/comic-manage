# 漫画资源元数据规范 v1.3 · Comic Manage

Comic Manage 1.10 按《漫画资源元数据规范 v1.3》组织编辑字段。ComicInfo.xml 和 Tags 保存可移植资料；Komga API 提供服务器索引与系列信息。继续使用 Komga 连接，不新增 Kavita 模式或页面版式字段。

## 编辑分组与取值

| 分组 | 字段与规则 |
| --- | --- |
| 名称与系列 | Title 必填，优先原文；Series 在属于系列时填写；Number 条件填写；Count 只填明确总数；Volume 只填来源明确的独立卷号，不复制 Number；系列别名另存 Komga alternateTitles。 |
| 作者与出版 | Writer 必填；Penciller 仅在绘画者与作者分离时填写；Publisher 可填社团或出版社；Translator 只填实际译者，空值表示没有翻译资料。 |
| 内容与分级 | Genre 为漫画、小说、CG集、画集、设定集、插画集；AgeRating 为 Everyone、MA15+、R18+；题材放普通 Tags。原作单值，角色多值。 |
| 发行与平台 | 发行性质为同人发行、商业出版、个人独立发布；出版形态建议独立作品、单行本、合集、分卷、连载，可扩展。发布方式和发布平台都可多值。 |
| 日期与阅读 | Year / Month / Day 按已知资料填写，语言可空；Manga 保留原生字段；Komga 阅读方向支持 LEFT_TO_RIGHT、RIGHT_TO_LEFT、VERTICAL、WEBTOON。 |
| 简介与来源 | Summary 可选；Web 可多条完整链接；PageCount 以实际文件或服务器分析为准。 |

发布方式只使用公开发布、单次购买、会员订阅、免费与付费并行。平台词库包含 Fantia、pixivFANBOX、Ci-en、Patreon、pixiv、BOOTH、DLsite、FANZA同人、DiGiket、Gumroad、Ko-fi、Melonbooks、Toranoana，也可新增。选择平台不会推断发行性质、获取方式、语言或年龄。

## 作品组与文字版本

作品组是可选的单值名称，用于关联同一作品的不同文件版本。使用稳定的原名或通用名称，同组填写完全一致的值；同名但不同作者或社团的作品应加作者、社团或来源标识。不从 Title、Series 或作者自动生成、合并作品组，不忽略名称大小写来归并作品。

文字版本为可空的“有文字 / 无文字”。只在确有版本差异或关联需求时填写，普通作品不默认有文字。日文原版和汉化版都可能有文字，文字版本不会改动 Translator、LanguageISO、作者、年龄、色彩或修正状态。

单本的“版本关联”区域和批量编辑均提供两个字段。两项分开勾选，填写作品组不会复制文字版本或其他资料；勾选后留空表示明确清空。列表、导入预览会显示版本标记，搜索可使用作品组与文字版本。

两个版本保持独立 Book、文件和阅读进度。作品组不改变 collection_mode、Series、Number、Count、Volume；独立版本使用 One-Shot，不伪造第 1 / 2 卷。现有带来源标识的成品路径可容纳相同 Title 的两个 One-Shot，不按作品组归入系列目录。

仅通过 Tags 的 `作品组:名称`、`文字版本:有文字` / `文字版本:无文字` 保存，XML 不新增对应元素。Komga 同步同名 Book-level Notable Entities；不自动建 Collection、不移动或合并 Book。Collection 属于可选服务器组织方式，不能替代可移植的作品组。

明确文字版本为无文字时，不再额外导出或同步重复普通标签“无字版”。未勾选的普通标签在本机编辑数据中保留，不据此反推或复活已清空的文字版本。

## 色彩与修正状态

色彩为可空的独立分类：黑白、全彩、部分彩色。按正文判断，不计封面；未知时留空，不从页面缩略图或旧“非黑白”字段推断全彩。

新生成 XML 不包含 BlackAndWhite。色彩保存在 `色彩:值` 标签，并可同步为 Komga `色彩` 实体。

只有 R18+ 显示修正状态。默认有修正不生成记录，明确无修正才输出 `修正状态:无修正`。其他分级不输出该标签或实体。批量仅修改勾选字段；改年龄时保留未勾选的历史布尔值，但导出与同步副本按最终分级生成。

## ComicInfo、Tags 与 Komga 的对应

原生 XML 字段包括 Title、Series、Number、Count、Volume、Summary、Year、Month、Day、Writer、Translator、Penciller、Publisher、Genre、Tags、AgeRating、Web、PageCount、LanguageISO、Manga。独立作品省略不适用的 Series / Number / Count；有明确资料的 Volume 可以单独填写。

自定义字段不增加 XML 子元素，统一生成以下前缀：

| 字段 | Tags 前缀 | Komga 实体 kind |
| --- | --- | --- |
| 原作 | 原作: | 原作 |
| 作品组 | 作品组: | 作品组 |
| 文字版本 | 文字版本: | 文字版本 |
| 角色 | 角色: | character |
| 发行性质 | 发行性质: | 发行性质 |
| 出版形态 | 出版形态: | 出版形态 |
| 发布方式 | 发布方式: | 发布方式 |
| 发布平台 | 发布平台: | 发布平台 |
| 色彩 | 色彩: | 色彩 |
| 修正状态 | 修正状态: | 修正状态 |

多值逐条生成标签与实体，去除重复项。角色同时写入 ComicInfo Characters。Genre 与 AgeRating 的逐册 Komga 辅助实体仍可使用 `内容类型`、`年龄分级`，但它们不是新的核心自定义字段，也不生成同名前缀 Tags。

不输出普通“汉化、全彩、黑白、无修正、同人志”等重复标签，不记录“生肉、有修正”默认状态；翻译只由 Translator 表达。

Komga 不会把 API 修改自动写回 CBZ。远端直接编辑更新服务器资料；本地生成 CBZ 才会把 Tags 和 ComicInfo 写入文件。

## 审核与批量编辑

审核需要真实 Title、Writer、Genre、AgeRating，以及至少一条已确认的普通或自定义标签。没有可靠资料时保存草稿，不生成“无”之类占位标签。旧原作多值、旧固定词表不符合新规则时需要核对。

单本和批量都可编辑新字段；多值批量替换列表，勾选后留空表示清空。未勾选字段保持不变，实际改动撤销旧审核。题材属于普通 Tags，不作为 Genre。

### 批量审核并同步

勾选作品后点击“批量审核并同步”。先检查 规范必填、来源指纹、当前连接与远端快照，并列出每本通过或不通过的原因；检查阶段不改变审核状态或服务器资料。核对完整名单并勾选确认后，仅对通过项执行审核，再一次同步整组作品。未通过项保持待处理，可修改后重新检查。

确认提交会再次核对冻结的资料，期间被编辑、移除、发生源或远端变化的记录会跳过。检查凭证只能使用一次、15 分钟后失效，且绑定本次服务器与书库。可取消；已完成的审核不会回滚，尚未同步的作品可稍后使用“同步到 Komga”。

结果分别列出审核状态、作品同步状态和系列共享字段结果；审核通过不代表已经同步成功。部分系列选择、权限或网络问题会在对应结果中说明。原来已审核或已生成的记录保持原状态；没有可唯一匹配的 Komga 作品时不会执行本组合操作。

## Komga 远端工作副本

从左下角“Komga 连接设置”进入，配置并点击“保存并读取书库”。远端记录按服务器、书库、Book ID 隔离；刷新保留草稿，冲突需要选择保留本机修改或采用远端资料，再重新审核。

Series 的 Genre、AgeRating、Publisher、Language 等共享资料单独展示。不能把共享类型或分级当成每册已经确认的事实；缺少逐册信息的作品保留待审核。

远端只 PATCH 实际修改字段，无修改不写入；未编辑的原始角色、链接标签、锁和未知值保留。系列共享字段要求选全所有有效、已审核成员。Manga 和 Volume 没有对应的 Komga 元数据 API 编辑字段，远端视图只读；阅读方向通过原生 Series readingDirection 写入，不伪造 XML 字段或新标签。

本地 XML 的 Manga 与 Komga 阅读方向分别保留，纵向和 Webtoon 不伪装成左右翻页。Volume 会进入本地 XML；Komga 库请关闭“将卷号附加到系列标题”，避免误建系列。

## 旧资料迁移

升级前自动备份本机资料库。v1.2 升级只补两个可选空字段时，保留既有审核、成品状态、成品路径、指纹与远端待同步修改，不再统一撤销审核。已有明确版本标签可读取；存在冲突或实质资料变化的记录会单独提示核对。保留人工标题与历史名称，不用中文名覆盖原文；旧成品文件不自动改写。

对于更早版本，仍执行已有 v1.2 实质迁移并重新核对。本地旧“同人志”内容类型转为“漫画”和“同人发行”；旧“同人 / 商业”性质转为“同人发行 / 商业出版”；“独立短篇”转为“独立作品”。旧 BlackAndWhite=Yes 可转换为黑白；No 不能区分全彩和部分彩色，除非另有明确全彩证据，否则留空并提示确认。旧原作多值保留以供核对，不截断丢失。

远端的旧词和原始快照保留为待核对资料，不因升级自动产生同步请求。选择新词、审核并同步后，才更新对应服务器字段。

## 官方参考

- [Komga 元数据与刷新](https://komga.org/docs/guides/scan-analysis-refresh/)
- [Komga Book API 数据字段](https://github.com/gotson/komga/blob/1.30.1/komga/src/main/kotlin/org/gotson/komga/interfaces/api/rest/dto/BookDto.kt)
- [Komga Series API 数据字段](https://github.com/gotson/komga/blob/1.30.1/komga/src/main/kotlin/org/gotson/komga/interfaces/api/rest/dto/SeriesDto.kt)
