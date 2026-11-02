"""ComicInfo mapping; no network calls or image conversion."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import unicodedata
import xml.etree.ElementTree as ET

MAX_XML_BYTES = 2 * 1024 * 1024
POSITIVE_TAGS = ("汉化", "无修正", "全彩")
AGE_RATINGS = ("Unknown", "Everyone", "Teen", "Mature 17+", "Adults Only 18+")


def split_values(value: str) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in re.split(r"[,，;；\n]", value) if v.strip()))


def title_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def parse_xml(data: bytes | str) -> ET.Element:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_XML_BYTES:
        raise ValueError("ComicInfo.xml 超过 2 MiB，未读取。")
    # Only accept UTF-8 XML. Reject DTDs/entities before handing data to the parser.
    text = raw.decode("utf-8-sig")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        raise ValueError("ComicInfo.xml 包含不支持的 DTD 或实体声明。")
    root = ET.fromstring(text)
    if root.tag != "ComicInfo":
        raise ValueError("XML 根节点必须为 ComicInfo。")
    return root


@dataclass
class Metadata:
    original_title: str = ""
    chinese_title: str = ""
    authors: list[str] = field(default_factory=list)
    publisher: str = ""
    translator: str = ""
    genre: str = ""
    tags: list[str] = field(default_factory=list)
    translated: bool = False
    uncensored: bool = False
    full_color: bool = False
    language: str = "ja"
    age_rating: str = "Unknown"
    summary: str = ""
    year: str = ""
    cover_index: int = 0
    original_xml: str = ""

    @property
    def display_title(self) -> str:
        return (self.chinese_title if self.translated else self.original_title).strip()

    @property
    def all_tags(self) -> list[str]:
        custom = [t.strip() for t in self.tags if t.strip() and t.strip() not in (*POSITIVE_TAGS, "生肉", "有修正", "黑白")]
        flags = [v for v, enabled in zip(POSITIVE_TAGS, (self.translated, self.uncensored, self.full_color)) if enabled]
        return list(dict.fromkeys(custom + flags))

    def validate(self, page_count: int) -> None:
        if not self.original_title.strip():
            raise ValueError("请填写完整日文原名（原作非日文时填写原名）。")
        if self.translated and not self.chinese_title.strip():
            raise ValueError("已汉化作品需要填写完整中文名。")
        if not any(a.strip() for a in self.authors):
            raise ValueError("请填写至少一位作者。")
        if self.year and (not re.fullmatch(r"\d{4}", self.year) or not 1000 <= int(self.year) <= 9999):
            raise ValueError("年份请填写四位数字，或留空。")
        if not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*", self.language):
            raise ValueError("语言请填写 ja、zh-Hans、zh-Hant 等语言代码。")
        if page_count < 1 or not 0 <= self.cover_index < page_count:
            raise ValueError("封面页码超出图片范围，请重新选择。")
        values = asdict(self)
        values.pop("original_xml")
        if any(re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", str(v)) for v in values.values()):
            raise ValueError("元数据中含有 XML 不支持的控制字符。")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> Metadata:
        allowed = cls.__dataclass_fields__
        return cls(**{k: v for k, v in value.items() if k in allowed})


def from_comicinfo(data: bytes, fallback_title: str) -> Metadata:
    root = parse_xml(data)
    get = lambda tag: (root.findtext(tag) or "").strip()
    tags = split_values(get("Tags"))
    language = get("LanguageISO") or "ja"
    translated = "汉化" in tags or language.lower().startswith("zh")
    series = get("Series") or get("Title") or fallback_title
    localized = get("LocalizedSeries")
    cover = next((p.get("Image", "0") for p in root.findall("./Pages/Page") if p.get("Type") == "FrontCover"), "0")
    return Metadata(
        original_title=(localized if translated else series) or ("" if translated else fallback_title),
        chinese_title=series if translated else "",
        authors=split_values(get("Writer")), publisher=get("Publisher"),
        translator=get("Translator"), genre=get("Genre"),
        tags=[t for t in tags if t not in POSITIVE_TAGS], translated=translated,
        uncensored="无修正" in tags, full_color="全彩" in tags,
        language=language, age_rating=get("AgeRating") or "Unknown",
        summary=get("Summary"), year=get("Year"),
        cover_index=int(cover) if cover.isdecimal() else 0,
        original_xml=data.decode("utf-8-sig"),
    )


def to_comicinfo(metadata: Metadata, page_count: int) -> bytes:
    metadata.validate(page_count)
    root = parse_xml(metadata.original_xml) if metadata.original_xml else ET.Element("ComicInfo")
    fields = {
        "Title": metadata.display_title, "Series": metadata.display_title,
        "LocalizedSeries": metadata.original_title.strip() if metadata.translated else "",
        "Summary": metadata.summary.strip(), "Year": metadata.year,
        "Writer": ", ".join(a.strip() for a in metadata.authors if a.strip()),
        "Translator": metadata.translator.strip() if metadata.translated else "",
        "Publisher": metadata.publisher.strip(), "Genre": metadata.genre.strip(),
        "Tags": ", ".join(metadata.all_tags), "PageCount": str(page_count),
        "LanguageISO": metadata.language, "AgeRating": metadata.age_rating,
        "BlackAndWhite": "",
    }
    for name, value in fields.items():
        for previous in list(root.findall(name)):
            root.remove(previous)
        if value:
            ET.SubElement(root, name).text = value
    for old_pages in list(root.findall("Pages")):
        root.remove(old_pages)
    pages = ET.SubElement(root, "Pages")
    ET.SubElement(pages, "Page", Image=str(metadata.cover_index), Type="FrontCover")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
