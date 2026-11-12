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
GENRES = ("同人志", "CG集", "画集", "设定集", "漫画", "插画集")
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
    uncensored: bool = False
    language: str = "ja"
    age_rating: str = "Unknown"
    black_and_white: str = "Unknown"
    manga: str = "Unknown"
    web: list[str] = field(default_factory=list)
    summary: str = ""
    year: str = ""
    month: str = ""
    day: str = ""
    cover_index: int = 0
    original_xml: str = ""

    def __post_init__(self):
        if self.title is None:
            self.title = (self.chinese_title if self.translated and self.chinese_title.strip() else self.original_title).strip()
            if self.full_color and self.black_and_white == "Unknown":
                self.black_and_white = "No"
        if self.collection_mode is None:
            self.collection_mode = "series" if self.series.strip() else "oneshot"
        self.normalize()

    def normalize(self) -> None:
        """Resolve authority once, retaining only ordinary themes in tags."""
        self.age_rating = AGE_ALIASES.get(self.age_rating, self.age_rating)
        if self.age_rating not in AGE_RATINGS:
            self.age_rating = "Unknown"
        self.authors = unique(self.authors)
        writers = {title_key(v) for v in self.authors}
        self.pencillers = [v for v in unique(self.pencillers) if title_key(v) not in writers]
        self.original_works = classification_values(self.original_works)
        self.characters = classification_values(self.characters)
        # URL path and query strings are case-sensitive; normalize whitespace only.
        self.web = list(dict.fromkeys(value.strip() for value in self.web if value.strip()))
        ordinary = []
        for tag in self.tags:
            tag = tag.strip()
            prefix, separator, value = tag.replace("：", ":", 1).partition(":")
            prefix, value = prefix.strip(), value.strip()
            if separator and prefix in ("原作", "角色"):
                if value:
                    target = self.original_works if prefix == "原作" else self.characters
                    target.extend(v.strip() for v in value.split("、") if v.strip())
            elif separator and prefix == "发行性质":
                if value and not self.release_type:
                    self.release_type = value
            elif separator and prefix == "出版形态":
                if value and not self.publication_format:
                    self.publication_format = value
            elif separator and prefix == "修正状态":
                if value == "无修正":
                    self.uncensored = True
            elif separator and prefix == "内容类型":
                if value and not self.genre:
                    self.genre = value
            elif separator and prefix in ("汉化状态", "年龄", "年龄等级", "年龄分级", "AgeRating", "彩色状态"):
                continue
            elif tag in (*POSITIVE_TAGS, "生肉", "有修正", "黑白", "全年龄", "R15", "R18", "R18+", "MA15+", "Everyone"):
                # Historical flags are imported by from_dict/from_comicinfo, not inferred when edited.
                continue
            elif tag:
                ordinary.append(tag)
        self.original_works = classification_values(self.original_works)
        self.characters = classification_values(self.characters)
        genres = []
        for genre in split_values(self.genre):
            if genre in THEMES:
                ordinary.append(genre)
            elif genre in ("商业志", "商業誌", "商業志"):
                genres.append("漫画")
                self.release_type = self.release_type or "商业"
            elif genre in ("单行本", "單行本"):
                genres.append("漫画")
                self.publication_format = self.publication_format or "单行本"
            else:
                genres.append(genre)
        self.genre = ", ".join(unique(genres))
        self.tags = unique(ordinary)
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
        if value.age_rating == "R18+" and value.uncensored:
            custom.append("修正状态:无修正")
        return unique(custom + value.tags)

    def validate(self, page_count: int) -> None:
        if not self.display_title:
            raise ValueError("请填写作品名称。")
        if not self.authors:
            raise ValueError("请填写至少一位作者。")
        if self.age_rating not in AGE_RATINGS[1:]:
            raise ValueError("请人工确认年龄分级：全年龄、MA15+ 或 R18。")
        unknown_translator = any(t in UNKNOWN_TRANSLATORS for t in split_values(self.translator))
        if unknown_translator:
            raise ValueError("已确认有翻译的作品请补录实际译者或汉化组；未知汉化信息仍需人工确认。")
        if self.collection_mode == "series" and (not self.series.strip() or not self.number.strip()):
            raise ValueError("系列作品请填写共同系列名称和本册编号 Number。完整一卷也使用 Number 排序。")
        fields = set(self.__dataclass_fields__) - {"original_xml", "volume"}
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
        if "black_and_white" in fields and self.black_and_white not in ("Unknown", "Yes", "No"):
            raise ValueError("黑白 / 彩色状态无效。")
        if "manga" in fields and self.manga not in ("Unknown", "Yes", "No", "YesAndRightToLeft"):
            raise ValueError("阅读方向无效。")
        if "language" in fields and not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*", self.language):
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
            if field_name in {"original_works", "characters", "release_type", "publication_format", "tags"}:
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
        values = dict(value)
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
    language = get("LanguageISO") or "ja"
    title = get("Title") or get("Series") or fallback_title
    cover = next((p.get("Image", "0") for p in root.findall("./Pages/Page") if p.get("Type") == "FrontCover"), "0")
    black_and_white = get("BlackAndWhite") or ("No" if "全彩" in tags else "Unknown")
    translator = get("Translator")
    translation_evidence = bool(translator) or has_translation_tag(tags)
    if translation_evidence and not translator:
        translator = "未知汉化"
    return Metadata(
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
        full_color=black_and_white == "No", black_and_white=black_and_white,
        manga=get("Manga") or "Unknown", web=get("Web").split(),
        language=language, age_rating=get("AgeRating") or "Unknown",
        summary=get("Summary"), year=get("Year") if get("Year") != "-1" else "",
        month=get("Month") if get("Month") != "-1" else "", day=get("Day") if get("Day") != "-1" else "",
        cover_index=int(cover) if cover.isdecimal() else 0, original_xml=data.decode("utf-8-sig"),
    )


def to_comicinfo(metadata: Metadata, page_count: int) -> bytes:
    value = Metadata.from_dict(metadata.to_dict())
    value.validate(page_count)
    root = parse_xml(value.original_xml) if value.original_xml else ET.Element("ComicInfo")
    fields = {
        "Title": value.display_title, "Series": value.series.strip() if value.collection_mode == "series" else "",
        "Number": value.number.strip() if value.collection_mode == "series" else "",
        "Count": value.count if value.collection_mode == "series" else "", "Volume": "",
        "LocalizedSeries": "",  # Komga alternateTitles are supplied through its API.
        "Summary": value.summary.strip(), "Year": value.year, "Month": value.month, "Day": value.day,
        "Writer": ", ".join(value.authors), "Penciller": ", ".join(value.pencillers),
        "Translator": value.translator.strip(), "Publisher": value.publisher.strip(), "Genre": value.genre,
        "Tags": ", ".join(value.all_tags), "AgeRating": value.age_rating, "Web": " ".join(value.web),
        "PageCount": str(page_count), "LanguageISO": value.language,
        "BlackAndWhite": value.black_and_white, "Manga": value.manga,
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
