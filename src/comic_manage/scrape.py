"""Preview text metadata from a specific, user-supplied work link."""
from __future__ import annotations

from datetime import date
from functools import partial
import re
from urllib.parse import SplitResult, urlsplit

from .metadata import AGE_RATINGS, GENRES, RELEASE_PLATFORMS, RELEASE_TYPES, Metadata, unique
from .scrape_galleries import fetch_gallery
from .scrape_http import ScrapeError, request_source
from .scrape_storefronts import fetch_storefront

SOURCES = {
    "e-hentai.org": "ehentai", "www.e-hentai.org": "ehentai",
    "nhentai.net": "nhentai", "www.nhentai.net": "nhentai",
    "dlsite.com": "dlsite", "www.dlsite.com": "dlsite",
    "dmm.com": "dmm", "dmm.co.jp": "dmm",
    "www.dmm.com": "dmm", "www.dmm.co.jp": "dmm",
    "book.dmm.com": "dmm", "book.dmm.co.jp": "dmm",
}
TEXT_FIELDS = {"title": 500, "publisher": 500, "summary": 8000, "language": 35}
LIST_FIELDS = {"authors": 30, "pencillers": 30, "original_works": 1, "characters": 50,
               "tags": 80, "release_platforms": 5}
ENUM_FIELDS = {"genre": GENRES, "release_type": RELEASE_TYPES, "age_rating": AGE_RATINGS[1:]}
MANAGED_TAGS = frozenset({
    "原作", "角色", "作品组", "文字版本", "内容类型", "发行性质", "出版形态", "发布方式", "发布平台",
    "色彩", "修正状态", "汉化状态", "年龄", "年龄等级", "年龄分级", "agerating", "彩色状态",
})


def work_link(value: str) -> tuple[str, SplitResult]:
    try:
        if not isinstance(value, str) or not value or len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
            raise ValueError
        url = urlsplit(value)
        provider = SOURCES.get(url.hostname)
        if url.scheme not in ("https", "http") or not provider or url.netloc.lower() != url.hostname:
            raise ValueError
    except ValueError:
        raise ScrapeError("请粘贴 E-Hentai、nhentai、DLsite 或 DMM/FANZA 的作品链接。") from None
    return provider, url._replace(scheme="https")


def _plain_text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    # Strip XML-incompatible control characters before metadata will be exported.
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff\ufffe\uffff]", "", value)
    return value.strip()[:limit].rstrip()


def _fields(raw: dict) -> dict:
    result = {key: text for key, limit in TEXT_FIELDS.items() if (text := _plain_text(raw.get(key), limit))}
    if "language" in result and not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*", result["language"]):
        del result["language"]
    result.update({key: raw[key] for key, values in ENUM_FIELDS.items() if raw.get(key) in values})
    for key, maximum in LIST_FIELDS.items():
        values = raw.get(key)
        if not isinstance(values, list):
            continue
        cleaned = unique([text for value in values[:maximum * 4] if (text := _plain_text(value, 500))])
        cleaned = [text for text in cleaned if not re.search(r"[,，;；\r\n]", text)]
        if key == "original_works" and len(cleaned) != 1:
            continue
        if key == "tags":
            # Tags must not set implicit metadata fields when the draft is saved.
            cleaned = [text for text in cleaned if text.replace("：", ":").split(":", 1)[0].strip().casefold() not in MANAGED_TAGS]
            cleaned = Metadata(tags=cleaned).tags
        if key == "release_platforms":
            cleaned = [text for text in cleaned if text in RELEASE_PLATFORMS]
        if cleaned:
            result[key] = cleaned[:maximum]
    year, month, day = (raw.get(key) or None for key in ("year", "month", "day"))
    if (isinstance(year, str) and re.fullmatch(r"[0-9]{4}", year)
            and (month is None or isinstance(month, str) and re.fullmatch(r"[0-9]{1,2}", month))
            and (day is None or month and isinstance(day, str) and re.fullmatch(r"[0-9]{1,2}", day))):
        try:
            parsed = date(int(year), int(month or 1), int(day or 1))
        except ValueError:
            pass
        else:
            result["year"] = str(parsed.year).zfill(4)
            if month:
                result["month"] = str(parsed.month)
            if day:
                result["day"] = str(parsed.day)
    return result


def scrape_preview(url: str, *, api_key: str | None = None, affiliate_id: str | None = None, cancel=None) -> dict:
    provider, parsed = work_link(url)
    credentials = {}
    for key, value in (("apiKey", api_key), ("affiliateId", affiliate_id)):
        if value is not None:
            if (not isinstance(value, str) or not 1 <= len(value) <= 512
                    or not re.fullmatch(r"[A-Za-z0-9_.=+/~-]+", value)):
                raise ScrapeError("站点 API 凭据格式无效。", "SCRAPE_CREDENTIALS")
            credentials[key] = value
    if (credentials and provider not in {"nhentai", "dmm"}
            or provider == "nhentai" and "affiliateId" in credentials):
        raise ScrapeError("请只填写当前站点需要的 API 凭据。", "SCRAPE_CREDENTIALS")
    if provider == "dmm" and set(credentials) != {"apiKey", "affiliateId"}:
        raise ScrapeError("请填写 DMM Web Service 的 API ID 和 Affiliate ID。", "SCRAPE_CREDENTIALS")
    handler = fetch_gallery if provider in {"ehentai", "nhentai"} else fetch_storefront
    result = handler(provider, parsed, credentials, partial(request_source, cancel=cancel))
    matched_provider, canonical = work_link(result["sourceUrl"])
    if matched_provider != provider:
        raise ScrapeError("返回的作品链接不属于当前站点。", "SCRAPE_RESPONSE")
    fields = _fields(result["fields"])
    if not fields.get("title"):
        raise ScrapeError("站点没有返回可确认的作品标题，请核对作品链接。", "SCRAPE_RESPONSE")
    warnings = [text for value in result["warnings"][:10] if (text := _plain_text(value, 400))]
    return {"provider": provider, "sourceUrl": canonical.geturl(), "fields": fields, "warnings": warnings}
