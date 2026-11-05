"""Conservative metadata hints from comic folder names and CBZ stems.

Only a leading date and explicit labels are authoritative. A leading plain
bracket group is an author hint; unknown groups within the title stay intact.
The result is a draft: age and translation credits still require confirmation,
and source ComicInfo metadata takes precedence over these hints.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .metadata import Metadata, split_values


@dataclass(frozen=True)
class _Group:
    start: int
    end: int
    value: str
    nested: bool = False


_BRACKETS = {"[": "]", "［": "］"}
_GENRES = {
    "同人志": "同人志", "同人誌": "同人志",
    "商业志": "商业志", "商業志": "商业志", "商业誌": "商业志", "商業誌": "商业志",
    "单行本": "单行本", "單行本": "单行本",
}
_OTHER_LABELS = {"digital", "dl版", "dl", "電子版", "电子版", "掃圖", "扫图", "scan", "raw", "生肉", "有修正", "黑白"}
_DATE = re.compile(r"(\d{4})[.-](\d{1,2})")
_DATE_LIKE = re.compile(r"\d{4}(?:[.-].*)?")


def _groups(name: str, warnings: list[str]) -> list[_Group]:
    groups: list[_Group] = []
    index = 0
    while index < len(name):
        if name[index] not in _BRACKETS:
            if name[index] in _BRACKETS.values():
                warnings.append("名称中有不完整的方括号，已保留原文，请确认标题。")
            index += 1
            continue
        start = index
        stack = [_BRACKETS[name[index]]]
        nested = False
        index += 1
        while index < len(name) and stack:
            char = name[index]
            if char in _BRACKETS:
                stack.append(_BRACKETS[char])
                nested = True
            elif char in _BRACKETS.values():
                if char != stack[-1]:
                    break
                stack.pop()
            index += 1
        if stack:
            warnings.append("名称中有不完整的方括号，已保留原文，请确认标题。")
            # Do not consume the title after a broken opening bracket as a label.
            break
        groups.append(_Group(start, index, name[start + 1:index - 1].strip(), nested))
    return groups


def _outside_text(name: str, start: int, end: int, groups: list[_Group]) -> str:
    cursor = start
    pieces = []
    for group in groups:
        if group.start < start or group.end > end:
            continue
        pieces.append(name[cursor:group.start])
        cursor = group.end
    pieces.append(name[cursor:end])
    return "".join(pieces).strip()


def _balanced_parentheses(value: str) -> bool:
    pairs = {"(": ")", "（": "）"}
    stack: list[str] = []
    for char in value:
        if char in pairs:
            stack.append(pairs[char])
        elif char in pairs.values():
            if not stack or stack.pop() != char:
                return False
    return not stack


def _circle_author(value: str) -> tuple[str, str] | None:
    """Return circle + full author text, retaining nested aliases in the latter."""
    opening = next((i for i, char in enumerate(value) if char in "(（"), -1)
    if opening <= 0 or not _balanced_parentheses(value):
        return None
    closing = ")" if value[opening] == "(" else "）"
    if not value.endswith(closing):
        return None
    # The first opening must close at the end, rather than before another group.
    depth = 0
    for i, char in enumerate(value[opening:], opening):
        if char in "(（":
            depth += 1
        elif char in ")）":
            depth -= 1
            if depth == 0 and i != len(value) - 1:
                return None
    circle, author = value[:opening].strip(), value[opening + 1:-1].strip()
    return (circle, author) if circle and author else None


def parse_filename(name: str) -> tuple[Metadata, list[str]]:
    """Parse one folder name or CBZ stem without changing any source files.

    Date values use numeric strings (e.g. ``2019`` and ``1``). Explicit labels
    may occur at either end; ambiguous groups inside title text remain there.
    Unknown boundary labels are retained in tags with a review warning.
    """
    metadata = Metadata()
    warnings: list[str] = []
    name = name.strip()
    groups = _groups(name, warnings)
    removed: list[_Group] = []
    publishers: list[str] = []
    translators: list[str] = []
    plain_author_seen = False

    for group in groups:
        value = group.value
        label = unicodedata.normalize("NFKC", value).strip()
        prefix = not _outside_text(name, 0, group.start, groups)
        suffix = not _outside_text(name, group.end, len(name), groups)
        boundary = prefix or suffix

        if group.nested:
            warnings.append(f"方括号嵌套，已保留原文：{value}")
            continue
        if not value:
            warnings.append("空方括号已保留，请确认标题。")
            continue
        if not _balanced_parentheses(value):
            warnings.append(f"括号不完整，已保留原文：{value}")
            continue

        date = _DATE.fullmatch(label)
        if _DATE_LIKE.fullmatch(label):
            if group.start == len(name) - len(name.lstrip()) and date:
                year, month = int(date[1]), int(date[2])
                if 1000 <= year <= 9999 and 1 <= month <= 12:
                    metadata.year, metadata.month = str(year), str(month)
                    removed.append(group)
                else:
                    warnings.append(f"发售年月不合法，已保留原文：{value}")
            else:
                warnings.append(f"发售年月仅从名称开头的 [YYYY.MM] 提取，已保留原文：{value}")
            continue

        if label in ("无修正", "無修正", "无修正版", "無修正版"):
            metadata.uncensored = True
        elif label == "全彩":
            metadata.full_color = True
            metadata.black_and_white = "No"
        elif label in _GENRES:
            genre = _GENRES[label]
            if metadata.genre and metadata.genre != genre:
                metadata.tags.append(value)
                warnings.append(f"发现多个作品类型，请确认：{metadata.genre} / {value}")
            else:
                metadata.genre = genre
        elif any(token in label for token in ("汉化", "漢化", "翻译", "翻譯", "翻訳")):
            normalized = label.translate(str.maketrans({"國": "国", "漢": "汉", "譯": "译", "訳": "译"}))
            translator = "未知汉化" if normalized in ("中国翻译", "中国汉化", "未知汉化", "未知翻译", "汉化", "翻译") else value
            if translator not in translators:
                translators.append(translator)
            metadata.translated = True
            metadata.language = "zh-Hans"
        elif boundary and (circle_author := _circle_author(value)):
            circle, author = circle_author
            if circle not in publishers:
                publishers.append(circle)
            metadata.authors.extend(split_values(author))
            plain_author_seen = True
        elif prefix and not plain_author_seen and label.casefold() not in _OTHER_LABELS:
            metadata.authors.extend(split_values(value))
            plain_author_seen = True
            warnings.append(f"已按前置作者标签识别“{value}”，请确认。")
        elif boundary:
            metadata.tags.append(value)
            warnings.append(f"未识别的标签已保留到其他标签：{value}")
        else:
            warnings.append(f"标题中的方括号内容已保留：{value}")
            continue
        removed.append(group)

    cursor = 0
    pieces = []
    for group in removed:
        pieces.append(name[cursor:group.start])
        cursor = group.end
    pieces.append(name[cursor:])
    metadata.original_title = re.sub(r"[ \t]{2,}", " ", "".join(pieces)).strip()
    metadata.title = metadata.original_title
    metadata.authors = list(dict.fromkeys(metadata.authors))
    metadata.tags = list(dict.fromkeys(metadata.tags))
    metadata.publisher = ", ".join(publishers)
    metadata.translator = ", ".join(translators)
    if len(publishers) > 1:
        warnings.append("发现多个社团 / 出版社，已全部保留，请确认。")
    if len(translators) > 1:
        warnings.append("发现多个汉化组，已全部保留，请确认。")
    if not metadata.original_title:
        warnings.append("未识别到作品标题，请手动填写完整原名。")
    metadata.normalize()
    if "未知汉化" in translators:
        warnings.append("尚未确认实际译者或汉化组，请补录后审核。")
    return metadata, list(dict.fromkeys(warnings))
