"""ComicInfo / namespaced Tags mapping for the NAS metadata rules."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import calendar
import re
import unicodedata
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

MAX_XML_BYTES = 2 * 1024 * 1024
POSITIVE_TAGS = ("汉化", "无修正", "全彩")  # Legacy import aliases only.
AGE_RATINGS = ("Unknown", "Everyone", "MA15+", "R18+")
GENRES = ("漫画", "小说", "CG集", "画集", "设定集", "插画集")
COLORS = ("", "黑白", "全彩", "部分彩色")
RELEASE_TYPES = ("", "同人发行", "商业出版", "个人独立发布")
RELEASE_METHODS = ("公开发布", "单次购买", "会员订阅", "免费与付费并行")
READING_DIRECTIONS = ("", "LEFT_TO_RIGHT", "RIGHT_TO_LEFT", "VERTICAL", "WEBTOON")
RELEASE_PLATFORMS = ("Fantia", "pixivFANBOX", "Ci-en", "Patreon", "pixiv", "BOOTH", "DLsite", "FANZA同人", "DiGiket", "Gumroad", "Ko-fi", "Melonbooks", "Toranoana")
V12_FIELDS = frozenset({"color", "release_methods", "release_platforms", "reading_direction"})
V13_FIELDS = frozenset({"work_group", "text_version"})
TEXT_VERSIONS = ("", "有文字", "无文字")
THEMES = {"恋爱", "奇幻", "日常", "悬疑", "纯爱", "猎奇", "NTR"}
UNKNOWN_TRANSLATORS = {"未知汉化", "未知漢化", "中国翻訳", "中国翻译", "中國翻譯", "未知翻译"}
AGE_ALIASES = {"全年龄": "Everyone", "R15": "MA15+", "R18": "R18+", "Adults Only 18+": "R18+"}


def split_values(value: str) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in re.split(r"[,，;；\n]", value) if v.strip()))


def title_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def unique(values: list[str]) -> list[str]:
    result, seen = [], set()
    for value in values:
        value = value.strip()
        key = title_key(value)
        if value and key not in seen:
            result.append(value)
            seen.add(key)
    return result


def unique_tags(values: list[str]) -> list[str]:
    """Work-group keys are exact names, unlike ordinary case-insensitive tags."""
    result, seen = [], set()
    for value in values:
        value = value.strip()
        prefix = value.replace("：", ":", 1).partition(":")[0].strip()
        key = ("exact", value) if prefix == "作品组" else ("ordinary", title_key(value))
        if value and key not in seen:
            result.append(value)
            seen.add(key)
    return result


def read_variant_fields(entities: list[dict], tags: list[str]) -> tuple[dict[str, str], list[str]]:
    """Read explicit v1.3 evidence without deriving grouping or version defaults."""
    result, warnings = {}, []
    for kind, field_name in (("作品组", "work_group"), ("文字版本", "text_version")):
        matching_entities = [item for item in entities if isinstance(item, dict) and isinstance(item.get("kind"), str) and item["kind"].strip() == kind]
        entity_values = [item["name"].strip() if isinstance(item.get("name"), str) else "" for item in matching_entities]
        tag_values = [tag.replace("：", ":", 1).partition(":")[2].strip() for tag in tags
                      if isinstance(tag, str) and tag.replace("：", ":", 1).partition(":")[0].strip() == kind]
        candidates = list(dict.fromkeys(entity_values if matching_entities else tag_values))
        if len(candidates) == 1 and candidates[0] and (field_name != "text_version" or candidates[0] in TEXT_VERSIONS[1:]):
            result[field_name] = candidates[0]
        else:
            result[field_name] = ""
            if candidates:
                warnings.append(f"{kind}存在多个值或无效值，原始证据已保留，请人工核对：{' / '.join(candidates)}。")
        if matching_entities and tag_values and any(value not in entity_values and value not in [name.lower() for name in entity_values] for value in tag_values):
            warnings.append(f"{kind}实体与标签副本不一致，按明确实体读取，原始标签仍保留供核对。")
    return result, warnings


def classification_values(values: list[str]) -> list[str]:
    """Both dedicated chips and namespaced tags use independent classification values."""
    return unique([part for value in values for part in value.split("、")])


def has_translation_tag(tags: list[str]) -> bool:
    return any(tag.strip().replace("：", ":") in ("汉化", "汉化状态:汉化") for tag in tags)


def parse_xml(data: bytes | str) -> ET.Element:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_XML_BYTES:
        raise ValueError("ComicInfo.xml 超过 2 MiB，未读取。")
    text = raw.decode("utf-8-sig")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        raise ValueError("ComicInfo.xml 包含不支持的 DTD 或实体声明。")
    root = ET.fromstring(text)
    if root.tag != "ComicInfo":
        raise ValueError("XML 根节点必须为 ComicInfo。")
    return root


def legacy_color(value: dict) -> str:
    tags = value.get("tags", [])
    explicit = [tag.replace("：", ":", 1).removeprefix("色彩:").strip()
                for tag in tags if tag.replace("：", ":", 1).startswith("色彩:")]
    explicit.extend(tag for tag in tags if tag in COLORS[1:])
    xml = value.get("original_xml")
    xml_color = ""
    if xml:
        try:
            root = parse_xml(xml)
            xml_tags = split_values(root.findtext("Tags") or "")
            explicit.extend(tag.replace("：", ":", 1).removeprefix("色彩:").strip()
                            for tag in xml_tags if tag.replace("：", ":", 1).startswith("色彩:"))
            explicit.extend(tag for tag in xml_tags if tag in COLORS[1:])
            xml_color = (root.findtext("BlackAndWhite") or "").strip()
        except (ValueError, UnicodeError, ET.ParseError):
            pass
    explicit = list(dict.fromkeys(color for color in explicit if color in COLORS[1:]))
    if len(explicit) == 1:
        return explicit[0]
    if len(explicit) > 1:
        return ""
    if value.get("full_color") and xml_color != "No":
        return "全彩"
    if value.get("black_and_white") == "Yes" or xml_color == "Yes":
        return "黑白"
    return ""


def migrate_legacy_values(value: dict) -> dict:
    """Convert explicit older local facts once. Remote working copies may retain original vocabulary."""
    result = dict(value)
    tags = result.get("tags", [])
    for prefix, field_name in (("发行性质", "release_type"), ("出版形态", "publication_format"), ("内容类型", "genre")):
        if not result.get(field_name):
            found = [tag.replace("：", ":", 1).partition(":")[2].strip() for tag in tags
                     if tag.replace("：", ":", 1).partition(":")[0].strip() == prefix]
            if found:
                result[field_name] = found[0]
    if "color" not in result or result["color"] is None:
        result["color"] = legacy_color(result)
    genres = []
    for genre in split_values(result.get("genre", "")):
        if genre in ("同人志", "同人誌"):
            genre = "漫画"
            result["release_type"] = result.get("release_type") or "同人发行"
        elif genre in ("商业志", "商業誌", "商業志"):
            genre = "漫画"
            result["release_type"] = result.get("release_type") or "商业出版"
        elif genre in ("单行本", "單行本"):
            genre = "漫画"
            result["publication_format"] = result.get("publication_format") or "单行本"
        genres.append(genre)
    result["genre"] = ", ".join(unique(genres))
    result["release_type"] = {"同人": "同人发行", "商业": "商业出版", "商業": "商业出版"}.get(result.get("release_type"), result.get("release_type", ""))
    if result.get("publication_format") == "独立短篇":
        result["publication_format"] = "独立作品"
    result.setdefault("release_methods", [])
    result.setdefault("release_platforms", [])
    result.setdefault("reading_direction", {"No": "LEFT_TO_RIGHT", "YesAndRightToLeft": "RIGHT_TO_LEFT"}.get(result.get("manga"), ""))
    return result


def migration_warnings(value: dict) -> list[str]:
    warnings = []
    _, variant_warnings = read_variant_fields([], value.get("tags", []))
    warnings.extend(variant_warnings)
    converted = migrate_legacy_values(value)
    if len(value.get("original_works", [])) > 1:
        warnings.append("旧记录包含多个原作，值已完整保留，请核对后只选择一个原作。")
    if not converted.get("color") and value.get("black_and_white") == "No":
        warnings.append("旧 BlackAndWhite=No 只表示非黑白，无法确认全彩或部分彩色，请人工核对。")
    original, chinese = value.get("original_title", ""), value.get("chinese_title", "")
    if original and chinese and original != chinese and value.get("title", chinese) == chinese:
        warnings.append("旧记录包含不同的原名和中文名，名称均已保留，请核对主标题；已有人工标题不自动改写。")
    if value.get("genre") and value["genre"] not in GENRES:
        warnings.append("旧内容类型已保留或转换，请按 v1.2 六类内容类型重新核对。")
    if value.get("release_type") and value["release_type"] not in RELEASE_TYPES:
        warnings.append("旧发行性质用词需要核对，请选择同人发行、商业出版或个人独立发布。")
    if value.get("publication_format") == "独立短篇":
        warnings.append("旧出版形态“独立短篇”请核对是否使用“独立作品”。")
    return warnings


@dataclass
class Metadata:
    # Retained only to migrate and preserve earlier manual names, not XML authority.
    original_title: str = ""
    chinese_title: str = ""
    translated: bool = False
    full_color: bool = False
    title: str | None = None
    collection_mode: str | None = None
    series: str = ""
    localized_series: str = ""
    localized_series_label: str = "zh-Hans"
    number: str = ""
    count: str = ""
    volume: str = ""
    authors: list[str] = field(default_factory=list)
    pencillers: list[str] = field(default_factory=list)
    publisher: str = ""
    translator: str = ""
    genre: str = ""
    tags: list[str] = field(default_factory=list)
    original_works: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    release_type: str = ""
    publication_format: str = ""
    release_methods: list[str] = field(default_factory=list)
    release_platforms: list[str] = field(default_factory=list)
    work_group: str | None = None
    text_version: str | None = None
    color: str | None = None
    uncensored: bool = False
    language: str = ""
    age_rating: str = "Unknown"
    black_and_white: str = "Unknown"
    manga: str = "Unknown"
    reading_direction: str = ""
    web: list[str] = field(default_factory=list)
    summary: str = ""
    year: str = ""
    month: str = ""
    day: str = ""
    cover_index: int = 0
    original_xml: str = ""

    def __post_init__(self):
        variants, _ = read_variant_fields([], self.tags)
        if self.work_group is None:
            self.work_group = variants["work_group"]
        if self.text_version is None:
            self.text_version = variants["text_version"]
        if self.title is None:
            self.title = (self.original_title or self.chinese_title).strip()
        if self.color is None:
            self.color = legacy_color(asdict(self))
        if self.collection_mode is None:
            self.collection_mode = "series" if self.series.strip() else "oneshot"
        self.normalize()

    def normalize(self) -> None:
        """Resolve authority once, retaining only ordinary themes in tags."""
        self.work_group = self.work_group.strip()
        self.text_version = self.text_version.strip()
        self.age_rating = AGE_ALIASES.get(self.age_rating, self.age_rating)
        if self.age_rating not in AGE_RATINGS:
            self.age_rating = "Unknown"
        self.authors = unique(self.authors)
        writers = {title_key(v) for v in self.authors}
        self.pencillers = [v for v in unique(self.pencillers) if title_key(v) not in writers]
        self.original_works = unique(self.original_works)
        self.characters = classification_values(self.characters)
        self.release_methods = unique(self.release_methods)
        platforms = {value.casefold(): value for value in RELEASE_PLATFORMS}
        self.release_platforms = unique(platforms.get(value.strip().casefold(), value.strip()) for value in self.release_platforms)
        # URL path and query strings are case-sensitive; normalize whitespace only.
        self.web = list(dict.fromkeys(value.strip() for value in self.web if value.strip()))
        ordinary = []
        colors_from_tags = {tag.replace("：", ":", 1).partition(":")[2].strip()
                            for tag in self.tags if tag.replace("：", ":", 1).partition(":")[0].strip() == "色彩"}
        if not self.color and len(colors_from_tags) == 1:
            self.color = next(iter(colors_from_tags))
        for tag in self.tags:
            tag = tag.strip()
            prefix, separator, value = tag.replace("：", ":", 1).partition(":")
            prefix, value = prefix.strip(), value.strip()
            if separator and prefix in ("原作", "角色"):
                if value:
                    target = self.original_works if prefix == "原作" else self.characters
                    target.extend([value] if prefix == "原作" else [v.strip() for v in value.split("、") if v.strip()])
            elif separator and prefix == "发行性质":
                if value and not self.release_type:
                    self.release_type = value
            elif separator and prefix == "出版形态":
                if value and not self.publication_format:
                    self.publication_format = value
            elif separator and prefix in ("作品组", "文字版本"):
                # Retain source evidence. Only all_tags emits the canonical copy,
                # so changing a version field never changes unchecked raw tags.
                ordinary.append(tag)
            elif separator and prefix in ("发布方式", "发布平台"):
                target = self.release_methods if prefix == "发布方式" else self.release_platforms
                if value:
                    target.append(value)
            elif separator and prefix == "色彩":
                continue
            elif separator and prefix == "修正状态":
                if value == "无修正":
                    self.uncensored = True
            elif separator and prefix == "内容类型":
                if value and not self.genre:
                    self.genre = value
            elif separator and prefix in ("汉化状态", "年龄", "年龄等级", "年龄分级", "AgeRating", "彩色状态"):
                continue
            elif tag in (*POSITIVE_TAGS, "生肉", "有修正", "黑白", "部分彩色", "全年龄", "R15", "R18", "R18+", "MA15+", "Everyone"):
                # Historical flags are imported by from_dict/from_comicinfo, not inferred when edited.
                continue
            elif tag:
                ordinary.append(tag)
        self.original_works = unique(self.original_works)
        self.characters = classification_values(self.characters)
        self.release_methods = unique(self.release_methods)
        self.release_platforms = unique(platforms.get(value.strip().casefold(), value.strip()) for value in self.release_platforms)
        genres = []
        for genre in split_values(self.genre):
            if genre in THEMES:
                ordinary.append(genre)
            else:
                genres.append(genre)
        self.genre = ", ".join(unique(genres))
        self.tags = unique_tags(tag for tag in ordinary if tag != self.genre and not (tag in ("同人志", "同人誌") and self.release_type == "同人发行"))
        if self.localized_series.strip() == self.series.strip():
            self.localized_series = ""

    @property
    def display_title(self) -> str:
        return (self.title or "").strip()

    @property
    def all_tags(self) -> list[str]:
        value = Metadata.from_dict(asdict(self))
        custom = [f"原作:{v}" for v in value.original_works] + [f"角色:{v}" for v in value.characters]
        if value.release_type.strip():
            custom.append(f"发行性质:{value.release_type.strip()}")
        if value.publication_format.strip():
            custom.append(f"出版形态:{value.publication_format.strip()}")
        custom.extend(f"发布方式:{item}" for item in value.release_methods)
        custom.extend(f"发布平台:{item}" for item in value.release_platforms)
        if value.work_group:
            custom.append(f"作品组:{value.work_group}")
        if value.text_version:
            custom.append(f"文字版本:{value.text_version}")
        if value.color:
            custom.append(f"色彩:{value.color}")
        if value.age_rating == "R18+" and value.uncensored:
            custom.append("修正状态:无修正")
        ordinary = [tag for tag in value.tags
                    if tag.replace("：", ":", 1).partition(":")[0].strip() not in ("作品组", "文字版本")
                    and not (value.text_version == "无文字" and tag == "无字版")]
        return unique_tags(custom + ordinary)

    def validate_review(self) -> None:
        if not self.display_title:
            raise ValueError("请填写作品名称。")
        if not self.authors:
            raise ValueError("请填写至少一位作者。")
        if self.genre not in GENRES:
            raise ValueError("请确认内容类型：漫画、小说、CG集、画集、设定集或插画集。")
        if self.age_rating not in AGE_RATINGS[1:]:
            raise ValueError("请人工确认年龄分级：全年龄、MA15+ 或 R18。")
        unknown_translator = any(t in UNKNOWN_TRANSLATORS for t in split_values(self.translator))
        if unknown_translator:
            raise ValueError("已确认有翻译的作品请补录实际译者或汉化组；未知汉化信息仍需人工确认。")
        self.validate_fields({"original_works", "release_type", "release_methods", "color", "work_group", "text_version"})
        if not self.all_tags:
            raise ValueError("请填写至少一条真实已确认的题材或自定义标签，未知时先保存草稿。")

    def validate(self, page_count: int) -> None:
        self.validate_review()
        if self.collection_mode == "series" and not self.series.strip():
            raise ValueError("系列作品请填写共同系列名称。")
        fields = set(self.__dataclass_fields__) - {"original_xml", "black_and_white", "full_color"}
        if self.collection_mode == "oneshot":
            fields -= {"series", "number", "count", "localized_series", "localized_series_label"}
        self.validate_fields(fields)
        if page_count < 1 or not 0 <= self.cover_index < page_count:
            raise ValueError("封面页码超出图片范围，请重新选择。")

    def validate_fields(self, fields: set[str]) -> None:
        """Check the edited values and their dependencies without requiring a complete draft."""
        if "collection_mode" in fields and self.collection_mode not in ("oneshot", "series"):
            raise ValueError("作品组织方式请使用独立作品或真实系列。")
        if fields & {"year", "month", "day"}:
            self._validate_date()
        for field_name, label, minimum in (("count", "系列总数", 1), ("volume", "卷号", 0)):
            number = getattr(self, field_name)
            if field_name in fields and number and (not re.fullmatch(r"\d+", number) or not minimum <= int(number) <= 2147483647):
                raise ValueError(f"{label}请填写有效整数，或留空。")
        if fields & {"series", "localized_series", "collection_mode"} and self.collection_mode == "series" and self.localized_series.strip() and not self.series.strip():
            raise ValueError("系列别名需要对应的真实系列名称。")
        if fields & {"localized_series", "localized_series_label", "collection_mode"} and self.collection_mode == "series" and self.localized_series.strip() and not self.localized_series_label.strip():
            raise ValueError("请填写系列别名标签，如 zh-Hans 或原名。")
        if "original_works" in fields and len(self.original_works) > 1:
            raise ValueError("原作只允许一个值，旧多值已保留，请人工核对。")
        if "color" in fields and self.color not in COLORS:
            raise ValueError("色彩请填写黑白、全彩、部分彩色，未知时留空。")
        if "text_version" in fields and self.text_version not in TEXT_VERSIONS:
            raise ValueError("文字版本请使用有文字、无文字，未知或不适用时留空。")
        if "release_type" in fields and self.release_type not in RELEASE_TYPES:
            raise ValueError("发行性质请填写同人发行、商业出版或个人独立发布，未知时留空。")
        if "release_methods" in fields and any(item not in RELEASE_METHODS for item in self.release_methods):
            raise ValueError("发布方式请使用公开发布、单次购买、会员订阅或免费与付费并行。")
        if "reading_direction" in fields and self.reading_direction not in READING_DIRECTIONS:
            raise ValueError("阅读方向选项无效。")
        if "manga" in fields and self.manga not in ("Unknown", "Yes", "No", "YesAndRightToLeft"):
            raise ValueError("阅读方向无效。")
        if "language" in fields and self.language and not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*", self.language):
            raise ValueError("语言请填写 ja、zh-Hans、zh-Hant 等语言代码。")
        if "web" in fields:
            for url in self.web:
                parsed = urlsplit(url)
                if parsed.scheme not in ("http", "https") or not parsed.netloc or re.search(r"\s", url):
                    raise ValueError("来源链接请填写完整的 http 或 https 作品详情页地址。")
        for field_name in fields:
            value = getattr(self, field_name)
            values = value if isinstance(value, list) else [value]
            if any(isinstance(v, str) and re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", v) for v in values):
                raise ValueError("元数据中含有 XML 不支持的字符。")
            if field_name in {"original_works", "characters", "release_type", "publication_format", "release_methods", "release_platforms", "work_group", "text_version", "color", "tags"}:
                if any(re.search(r"[,，;；\n]", v) for v in values):
                    raise ValueError("每个标签值请单独填写，不能包含逗号、分号或换行。")

    def _validate_date(self) -> None:
        if self.year and (not re.fullmatch(r"\d{4}", self.year) or not 1000 <= int(self.year) <= 9999):
            raise ValueError("年份请填写四位数字，或留空。")
        if self.month and (not re.fullmatch(r"\d{1,2}", self.month) or not 1 <= int(self.month) <= 12):
            raise ValueError("月份请填写 1 到 12，或留空。")
        if self.day and (not re.fullmatch(r"\d{1,2}", self.day) or not 1 <= int(self.day) <= 31):
            raise ValueError("日期请填写 1 到 31，或留空。")
        if self.day and not self.month:
            raise ValueError("填写具体日期前请先填写月份。")
        if self.month and self.day:
            limit = calendar.monthrange(int(self.year) if self.year else 2000, int(self.month))[1]
            if int(self.day) > limit:
                raise ValueError("发售日期不是有效的日历日期。")

    def to_dict(self) -> dict:
        self.normalize()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> Metadata:
        values = dict(value) if V12_FIELDS.issubset(value) else migrate_legacy_values(value)
        legacy = "title" not in values
        if legacy:
            tags = values.get("tags", [])
            translation_evidence = bool(values.get("translated")) or has_translation_tag(tags)
            if values.get("full_color") or "全彩" in tags:
                values.setdefault("black_and_white", "No")
            if values.get("uncensored") or "无修正" in tags:
                values["uncensored"] = True
            # Previously hidden standard fields remain available after an upgrade.
            if values.get("original_xml"):
                try:
                    imported = from_comicinfo(values["original_xml"].encode("utf-8"), values.get("original_title", ""))
                    translation_evidence = translation_evidence or imported.translated
                    for key in ("series", "localized_series", "number", "count", "volume", "pencillers", "day", "web", "manga", "black_and_white", "original_works", "characters", "release_type", "publication_format"):
                        values.setdefault(key, getattr(imported, key))
                except (ValueError, UnicodeError, ET.ParseError):
                    pass
            # Make incomplete legacy evidence visible and editable once. Canonical
            # records must respect a user's later decision to clear Translator.
            if translation_evidence and not values.get("translator", "").strip():
                values["translator"] = "未知汉化"
        allowed = cls.__dataclass_fields__
        return cls(**{k: v for k, v in values.items() if k in allowed})


def from_comicinfo(data: bytes, fallback_title: str) -> Metadata:
    root = parse_xml(data)
    get = lambda tag: (root.findtext(tag) or "").strip()
    tags = split_values(get("Tags"))
    language = get("LanguageISO")
    title = get("Title") or get("Series") or fallback_title
    cover = next((p.get("Image", "0") for p in root.findall("./Pages/Page") if p.get("Type") == "FrontCover"), "0")
    black_and_white = get("BlackAndWhite") or ("No" if "全彩" in tags else "Unknown")
    translator = get("Translator")
    translation_evidence = bool(translator) or has_translation_tag(tags)
    if translation_evidence and not translator:
        translator = "未知汉化"
    values = dict(
        title=title, original_title=title, series=get("Series"), localized_series=get("LocalizedSeries"),
        collection_mode="series" if get("Series") else "oneshot",
        number=get("Number"), count=get("Count") if get("Count") != "-1" else "",
        volume=get("Volume") if get("Volume") != "-1" else "",
        authors=split_values(get("Writer")), pencillers=split_values(get("Penciller")), publisher=get("Publisher"),
        translator=translator, genre=get("Genre"), tags=tags,
        characters=split_values(get("Characters")),
        # Language describes this edition, not whether it was translated. Chinese originals are valid.
        translated=translation_evidence,
        uncensored="无修正" in tags or "修正状态:无修正" in tags,
        full_color=False, black_and_white=black_and_white,
        manga=get("Manga") or "Unknown", web=get("Web").split(),
        language=language, age_rating=get("AgeRating") or "Unknown",
        summary=get("Summary"), year=get("Year") if get("Year") != "-1" else "",
        month=get("Month") if get("Month") != "-1" else "", day=get("Day") if get("Day") != "-1" else "",
        cover_index=int(cover) if cover.isdecimal() else 0, original_xml=data.decode("utf-8-sig"),
    )
    return Metadata.from_dict(migrate_legacy_values(values))


def to_comicinfo(metadata: Metadata, page_count: int) -> bytes:
    value = Metadata.from_dict(metadata.to_dict())
    value.validate(page_count)
    root = parse_xml(value.original_xml) if value.original_xml else ET.Element("ComicInfo")
    fields = {
        "Title": value.display_title, "Series": value.series.strip() if value.collection_mode == "series" else "",
        "Number": value.number.strip() if value.collection_mode == "series" else "",
        "Count": value.count if value.collection_mode == "series" else "", "Volume": value.volume,
        "LocalizedSeries": "",  # Komga alternateTitles are supplied through its API.
        "Summary": value.summary.strip(), "Year": value.year, "Month": value.month, "Day": value.day,
        "Writer": ", ".join(value.authors), "Penciller": ", ".join(value.pencillers),
        "Translator": value.translator.strip(), "Publisher": value.publisher.strip(), "Genre": value.genre,
        "Tags": ", ".join(value.all_tags), "AgeRating": value.age_rating, "Web": " ".join(value.web),
        "PageCount": str(page_count), "LanguageISO": value.language,
        "BlackAndWhite": "", "Manga": value.manga,
        "Characters": ", ".join(value.characters),  # Komga character entities plus portable 角色: tag copies.
        "SeriesSort": "",
        "SeriesGroup": "", "StoryArc": "", "StoryArcNumber": "", "AlternateSeries": "", "AlternateNumber": "",
    }
    for name, content in fields.items():
        for previous in list(root.findall(name)):
            root.remove(previous)
        if content:
            ET.SubElement(root, name).text = content
    page_attributes = {}
    for old_page in root.findall("./Pages/Page"):
        raw_index = old_page.get("Image", "")
        if not re.fullmatch(r"\d{1,10}", raw_index) or int(raw_index) >= page_count:
            continue
        attributes = dict(old_page.attrib)
        if attributes.get("Type") == "FrontCover":
            attributes.pop("Type")
        page_attributes[int(raw_index)] = attributes
    for old_pages in list(root.findall("Pages")):
        root.remove(old_pages)
    pages = ET.SubElement(root, "Pages")
    cover_attributes = page_attributes.setdefault(value.cover_index, {"Image": str(value.cover_index)})
    cover_attributes["Type"] = "FrontCover"
    for index, attributes in sorted(page_attributes.items()):
        attributes["Image"] = str(index)
        ET.SubElement(pages, "Page", attributes)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
