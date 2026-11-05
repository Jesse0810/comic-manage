# NAS 元数据规则与导出映射

Electron 1.3.0 按用户提供的《NAS 漫画元数据规范 v1.1》实现。成品仍是 CBZ，根目录包含 ComicInfo.xml；五类自定义分类统一使用 Tags 命名空间，不生成自定义 XML 元素或 metadata.json。

## 编辑字段

| 分组 | 可编辑信息 / XML 字段 |
| --- | --- |
| 名称与系列 | 作品名称 Title、系列 Series、系列译名 LocalizedSeries、期号 Number、总数 Count、卷号 Volume |
| 创作与出版 | 作者 Writer、独立绘画者 Penciller、译者 / 汉化组 Translator、发行方 Publisher |
| 内容与分类 | 内容类型 Genre、普通 Tags、AgeRating、原作、角色、发行性质、出版形态、R18 修正状态 |
| 发行与阅读 | Year / Month / Day、LanguageISO、BlackAndWhite、Manga |
| 简介与来源 | Summary、多条来源 Web |
| 自动计算 | PageCount 为实际图片数量，仅展示，不可手工覆盖 |

共 21 个标准字段、1 个 Kavita 扩展字段、5 个自定义分类。独立作品的 Series 留空时仅在 XML 导出中回退到自己的 Title，不使用原作 IP 合并不同同人志。Number 和 Volume 不自动互相复制，Count 未知留空。LocalizedSeries 必须是已填写系列的真实译名且与 Series 不同。

## 自定义分类

| 编辑值 | 导出 Tags |
| --- | --- |
| 原作 东方Project | 原作:东方Project |
| 角色 博丽灵梦、雾雨魔理沙 | 角色:博丽灵梦, 角色:雾雨魔理沙 |
| 发行性质 同人 | 发行性质:同人 |
| 出版形态 独立短篇 | 出版形态:独立短篇 |
| R18 无修正 | 修正状态:无修正 |

同一属性只有一个权威字段。普通 Tags 不重复生成汉化、生肉、全彩、黑白、内容类型、年龄或普通无修正标签。R18 默认有修正，省略修正状态标签；全年龄、R15、未确认年龄不导出修正状态标签。题材如恋爱、奇幻、日常、悬疑归入普通 Tags。

## 标准值与审核

- 全年龄 → Everyone；R15 → MA15+；R18 → R18+。Unknown 只用于待确认草稿，不猜测分级。
- BlackAndWhite：黑白 Yes、彩色 / 非黑白 No、未知 Unknown，不把未知推断为黑白。
- Manga：YesAndRightToLeft、Yes、No、Unknown。
- Penciller 只记录与 Writer 不同的独立绘画者。
- 日期可只填已知部分；检查实际日历日期，缺失值不自动补齐。
- Web 在 XML 的同一个元素内以空格分隔多个 HTTP(S) 来源链接。
- 已知翻译版本需核实 Translator；未知汉化保留为草稿提示，不当作真实团队。中文原创可留空，不能仅凭当前语言判断是否经过翻译。

现有 XML 中未管理信息尽量保留；旧 Characters 归入角色标签，SeriesSort 清除以免覆盖新 Series。旧库升级前自动备份，迁移后重新审核。生成流程继续核对源文件、页数、CRC 和 SHA-256；原图片和既有成品不删除、不覆盖。

官方对照：[ComicInfo 2.1 schema](https://anansi-project.github.io/docs/comicinfo/schemas/v2.1)、[ComicInfo 字段说明](https://anansi-project.github.io/docs/comicinfo/documentation)、[Kavita 漫画元数据](https://wiki.kavitareader.com/guides/metadata/comics/)。
