"""Search supported metadata sites, then prepare explicitly reviewed draft updates."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import re
import secrets
from threading import Event
import time
import unicodedata

from .library_agent import AgentError, LibraryAgent, MAX_AGENT_BOOKS, _date, _ids, _scope, _text
from .metadata import unique
from .model_assist import _safe_text
from .repository import Repository
from .scrape import SITE_PROPOSAL_FIELDS, _fields, scrape_preview, work_link
from .scrape_galleries import _EH_PATH, _NH_PATH
from .scrape_http import ScrapeError
from .scrape_storefronts import _dlsite_identity, _dmm_identity, _source_url
from .search_galleries import search_gallery
from .search_stores import search_store
from .sources import Cancelled, check_cancel


SEARCH_PROVIDERS = {"dlsite": "DLsite", "dmm": "DMM/FANZA", "ehentai": "E-Hentai", "nhentai": "nhentai"}
_SEARCH = {"dlsite": search_store, "dmm": search_store, "ehentai": search_gallery, "nhentai": search_gallery}
_MAX_CANDIDATES = 10
_TICKET_TTL = 45 * 60
_TICKET_LIMIT = 6
_DATE_FIELDS = ("year", "month", "day")
_ADDITIVE = {"tags", "release_platforms"}
_STOP_CODES = {"SCRAPE_AUTH", "SCRAPE_CREDENTIALS", "SCRAPE_RATE_LIMIT", "SCRAPE_RESTRICTED", "SCRAPE_NETWORK"}
_NOTICES = {
    "SCRAPE_AUTH": "站点拒绝访问，请核对权限或 API 凭据。",
    "SCRAPE_CREDENTIALS": "API 凭据无效，请重新填写。",
    "SCRAPE_RATE_LIMIT": "站点限制了请求频率，请稍后重试。",
    "SCRAPE_RESTRICTED": "站点要求登录、跳转或当前地区不可访问。",
    "SCRAPE_NETWORK": "无法连接站点，请检查网络。",
}
_KNOWN_VERSIONS = re.compile(r"(?:^|\s|[【\[(（])(?:有文字|無文字|无文字|無字版|无字版|textless|no text)(?:$|\s|[】\])）])", re.I)


@dataclass(frozen=True)
class _SearchTicket:
    expires: float
    scope: str
    # Only authoritative local revisions and sanitized candidates, with no keys.
    books: dict


def _credentials(value) -> dict:
    if not isinstance(value, dict) or set(value) - {"dmm", "nhentai"}:
        raise AgentError("站点凭据格式无效。")
    result = {}
    for provider, values in value.items():
        allowed = {"apiKey", "affiliateId"} if provider == "dmm" else {"apiKey"}
        if not isinstance(values, dict) or set(values) - allowed or any(
            not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_.=+/~-]{1,512}", item)
            for item in values.values()
        ):
            raise AgentError("站点 API 凭据格式无效。")
        result[provider] = values
    return result


def _identity(url: str) -> tuple[str, str, str]:
    provider, parsed = work_link(url)
    if provider == "dlsite":
        identity = _dlsite_identity(parsed)
        return provider, identity, _source_url(parsed)
    if provider == "dmm":
        identity = _dmm_identity(parsed)
        # DMM and FANZA can use different service identifiers for the same title.
        family = "FANZA" if ".co.jp" in parsed.hostname else "DMM"
        return provider, family + ":" + identity, _source_url(parsed, identity)
    match = (_EH_PATH if provider == "ehentai" else _NH_PATH).fullmatch(parsed.path)
    if not match:
        raise ScrapeError("来源没有提供有效的作品详情地址。", "SCRAPE_URL")
    if provider == "ehentai":
        return provider, match[1], f"https://e-hentai.org/g/{match[1]}/{match[2].lower()}/"
    return provider, match[1], f"https://nhentai.net/g/{match[1]}/"


def _key(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(char if char.isalnum() or unicodedata.category(char).startswith("M") else " "
                            for char in text).split())


def _names(values) -> list[str]:
    if not isinstance(values, list):
        return []
    return unique([text for name in values[:24] if (text := _safe_text(name, 240))])


def _name_keys(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value)
    variants = [normalized]
    if match := re.fullmatch(r"([^()]{1,180})\s*\(([^()]{1,180})\)", normalized):
        variants.extend(match.groups())
    return {key for item in variants if (key := _key(item))}


def _credit_match(local: list[str], found: list[str]) -> tuple[bool, bool]:
    left, right = [_name_keys(value) for value in local], [_name_keys(value) for value in found]
    edges = [[bool(name & other) for other in right] for name in left]
    any_match = any(any(row) for row in edges)
    exact = any_match and all(sum(row) == 1 for row in edges) and all(sum(column) == 1 for column in zip(*edges))
    return any_match, exact


def _language_diff(local: str, found: str) -> bool:
    if not local or not found:
        return False
    first, second = local.lower(), found.lower()
    return first != second and (first.split("-")[0] != second.split("-")[0]
                               or first.startswith("zh-") and second.startswith("zh-"))


def _rating(book: dict, local_title: str, authors: list[str], candidate: dict) -> tuple[int, bool, list[str]]:
    peer_titles = [key for value in [candidate["title"], *candidate["titles"]] if (key := _key(value))]
    local_key = _key(local_title)
    if not local_key or not peer_titles:
        return 0, False, []
    exact_title = local_key in peer_titles
    similarity = max(SequenceMatcher(None, local_key, title, autojunk=False).ratio() for title in peer_titles)
    credit, full_credit = _credit_match(authors, candidate["authors"])
    if not exact_title and similarity < (0.55 if credit else 0.73):
        return 0, False, []
    score = (66 if exact_title else int(42 * similarity)) + (30 if full_credit else 17 if credit else 0)
    reasons = ["原名一致" if exact_title else "名称相近"]
    if full_credit:
        reasons.append("作者一致")
    elif credit:
        reasons.append("部分作者一致")
    elif authors and candidate["authors"]:
        reasons.append("作者信息不同")
    else:
        reasons.append("作者待核对")
    metadata = book["metadata"]
    differs = False
    if metadata.get("year") and candidate.get("year") and metadata["year"] != candidate["year"]:
        reasons.append("发行年份不同")
        differs = True
    if book.get("page_count", 0) > 0 and candidate.get("pageCount", 0) > 0 and book["page_count"] != candidate["pageCount"]:
        reasons.append("页数不同")
        differs = True
    if _language_diff(metadata.get("language", ""), candidate.get("language", "")):
        reasons.append("语言版本不同")
        differs = True
    if metadata.get("text_version") or any(_KNOWN_VERSIONS.search(value) for value in peer_titles):
        reasons.append("请核对文字版本")
        differs = True
    substantial = sum(char.isalnum() for char in local_key) >= 2
    confident = exact_title and full_credit and not differs and substantial and candidate["searchComplete"]
    return score, confident, reasons


def _candidate(provider: str, raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    try:
        found, identity, url = _identity(raw.get("url"))
    except ScrapeError:
        return None
    title = _safe_text(raw.get("title"), 500)
    if not title or found != provider:
        return None
    result = {"id": hashlib.sha256((provider + ":" + identity).encode()).hexdigest()[:32],
              "provider": provider, "title": title, "url": url, "titles": _names(raw.get("titles")),
              "authors": _names(raw.get("authors")), "searchComplete": raw.get("searchComplete") is not False}
    if publisher := _safe_text(raw.get("publisher"), 240):
        result["publisher"] = publisher
    if isinstance(year := raw.get("year"), str) and re.fullmatch(r"[0-9]{4}", year):
        result["year"] = year
    if isinstance(language := raw.get("language"), str) and re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language):
        result["language"] = language
    if type(page_count := raw.get("pageCount")) is int and 0 < page_count <= 100000:
        result["pageCount"] = page_count
    return result


def _provider_notice(provider: str, code: str) -> str:
    return SEARCH_PROVIDERS[provider] + "：" + _NOTICES.get(code, "站点未返回可识别的资料，请稍后重试。")


def _selection(value) -> dict[str, str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_AGENT_BOOKS:
        raise AgentError("请先选择要读取的候选作品。")
    choices = {}
    for choice in value:
        if (not isinstance(choice, dict) or set(choice) != {"id", "candidateId"}
                or not _text(choice.get("id"), 128) or not _text(choice.get("candidateId"), 128)
                or choice["id"] in choices):
            raise AgentError("每本漫画只能选择一个当前搜索结果。")
        choices[choice["id"]] = choice["candidateId"]
    return choices


def _merge_source(before: list[str], source: str) -> list[str]:
    key = _identity(source)[:2]
    for previous in before:
        try:
            if _identity(previous)[:2] == key:
                return before
        except ScrapeError:
            continue
    return [*before, source]


def _patch(before: dict, fields: dict, url: str, warnings: list[str]) -> dict:
    values = _fields(fields)
    if "year" in values:
        values["release_date"] = _date(before, values)
    values["web"] = _merge_source(before["web"], url)
    changes = {}
    for field, after in values.items():
        if field not in SITE_PROPOSAL_FIELDS:
            continue
        if field in _ADDITIVE:
            after = unique(before[field] + after)
        patch = after if field == "release_date" else {field: after}
        try:
            if Repository.checked_agent_values(before, patch):
                changes[field] = after
        except ValueError:
            warnings.append("部分来源资料与当前字段有冲突，请回到作品资料中核对。")
    return changes


class LibrarySearch:
    def __init__(self, agent: LibraryAgent):
        self.agent = agent
        self.repo = agent.repo
        self.progress = agent.progress
        self.tickets: OrderedDict[str, _SearchTicket] = OrderedDict()

    def search(self, params: dict, cancel: Event) -> dict:
        if set(params) - {"ids", "providers", "credentials", "komga_scope"}:
            raise AgentError("站点搜索请求格式无效。")
        ids = _ids(params.get("ids"))
        providers = params.get("providers")
        if (not isinstance(providers, list) or not providers or len(providers) > len(SEARCH_PROVIDERS)
                or any(not isinstance(site, str) or site not in SEARCH_PROVIDERS for site in providers)
                or len(set(providers)) != len(providers)):
            raise AgentError("请选择要搜索的站点。")
        credentials = _credentials(params.get("credentials", {}))
        if "dmm" in providers and set(credentials.get("dmm", {})) != {"apiKey", "affiliateId"}:
            raise AgentError("请填写 DMM/FANZA 的 API ID 和 Affiliate ID。")
        scope = _scope(params.get("komga_scope", ""))
        rows, actionable, unavailable, cache = [], {}, {}, {}
        try:
            for index, identity in enumerate(ids, 1):
                check_cancel(cancel)
                self.progress(f"正在搜索第 {index}/{len(ids)} 本漫画…")
                try:
                    saved = self.repo.review_state(identity)
                    self.repo.check_agent_scope(saved, scope)
                except ValueError:
                    rows.append({"id": identity, "title": "当前不可编辑的作品", "status": "error",
                                 "candidates": [], "warnings": ["作品已移除，或不属于当前可编辑书库。"]})
                    continue
                data = saved["metadata"]
                title = _safe_text(data.get("original_title"), 300) or _safe_text(data.get("title"), 300)
                authors = _names(data.get("authors"))
                row = {"id": identity, "title": title or "未命名作品", "authorSummary": "、".join(authors),
                       "status": "unmatched", "candidates": [], "warnings": []}
                rows.append(row)
                if not title or title in ("未命名作品", "有文字", "无文字", "無文字"):
                    row["warnings"].append("请先填写作品原名，再试一次。")
                    continue
                errors, partial, collected = 0, False, {}
                for site in providers:
                    check_cancel(cancel)
                    if site in unavailable:
                        errors += 1
                        row["warnings"].append(unavailable[site])
                        continue
                    key = (site, title, tuple(authors))
                    if key not in cache:
                        self.progress(f"第 {index}/{len(ids)} 本 · 正在查询 {SEARCH_PROVIDERS[site]}…")
                        try:
                            hits = _SEARCH[site](site, title, authors, credentials.get(site, {}), cancel=cancel)
                            if not isinstance(hits, list):
                                raise ScrapeError("搜索响应无效。", "SCRAPE_RESPONSE")
                            safe_hits = [safe for item in hits[:80] if (safe := _candidate(site, item))]
                            if hits and not safe_hits:
                                raise ScrapeError("搜索结果缺少可确认的作品资料。", "SCRAPE_RESPONSE")
                            if len(safe_hits) != len(hits):
                                for candidate in safe_hits:
                                    candidate["searchComplete"] = False
                            cache[key] = safe_hits
                        except ScrapeError as error:
                            notice = _provider_notice(site, error.code)
                            cache[key] = notice
                            if error.code in _STOP_CODES:
                                unavailable[site] = notice
                    result = cache[key]
                    if isinstance(result, str):
                        errors += 1
                        row["warnings"].append(result)
                        continue
                    partial = partial or any(not candidate["searchComplete"] for candidate in result)
                    for candidate in result:
                        score, confident, reasons = _rating(saved, title, authors, candidate)
                        if score > 0:
                            if candidate["id"] not in collected or score > collected[candidate["id"]][0]:
                                collected[candidate["id"]] = (score, confident, {**candidate, "reasons": reasons})
                ranked = sorted(collected.values(), key=lambda hit: (-hit[0], hit[2]["provider"], hit[2]["id"]))
                if partial:
                    row["warnings"].append("至少一个站点返回了部分结果，可能还有其他候选。")
                if not ranked:
                    if errors or partial:
                        row["status"] = "error"
                    continue
                shown = ranked[:_MAX_CANDIDATES]
                row["candidates"] = [{key: value for key, value in candidate.items() if key not in {"titles", "searchComplete"}}
                                     for _, _, candidate in shown]
                if len(ranked) > len(shown):
                    row["warnings"].append(f"候选较多，仅显示最相关的 {len(shown)} 项，请核对后选择。")
                if len(ranked) == 1 and ranked[0][1] and not errors and not partial:
                    row.update(status="matched", recommendedId=ranked[0][2]["id"])
                else:
                    row["status"] = "ambiguous"
                actionable[identity] = (title, self.repo.agent_revision(saved),
                                        {candidate["id"]: candidate for _, _, candidate in shown})
            check_cancel(cancel)
        except Cancelled:
            return {"token": None, "books": rows, "cancelled": True}
        token = None
        if actionable:
            now = time.monotonic()
            self.tickets = OrderedDict((key, item) for key, item in self.tickets.items() if item.expires > now)
            token = secrets.token_urlsafe(24)
            self.tickets[token] = _SearchTicket(now + _TICKET_TTL, scope, actionable)
            while len(self.tickets) > _TICKET_LIMIT:
                self.tickets.popitem(last=False)
        return {"token": token, "books": rows, "cancelled": False}

    def preview(self, params: dict, cancel: Event) -> dict:
        if set(params) - {"token", "choices", "credentials", "komga_scope"} or not _text(params.get("token"), 128):
            raise AgentError("站点资料读取请求格式无效。")
        selections = _selection(params.get("choices"))
        scope = _scope(params.get("komga_scope", ""))
        credentials = _credentials(params.get("credentials", {}))
        ticket = self.tickets.get(params["token"])
        if not ticket or ticket.expires <= time.monotonic() or ticket.scope != scope:
            raise AgentError("搜索结果已过期或当前书库已切换，请重新搜索。", "AGENT_SEARCH_EXPIRED")
        for identity, candidate_id in selections.items():
            if identity not in ticket.books or candidate_id not in ticket.books[identity][2]:
                raise AgentError("只能选用本次搜索为该作品返回的候选。")
        rows, plans, cache = [], {}, {}
        try:
            for index, (identity, candidate_id) in enumerate(selections.items(), 1):
                check_cancel(cancel)
                self.progress(f"正在读取已选来源 {index}/{len(selections)}…")
                title, revision, candidates = ticket.books[identity]
                candidate = candidates[candidate_id]
                site, url = candidate["provider"], candidate["url"]
                row = {"id": identity, "title": title, "fields": [], "warnings": [], "source": SEARCH_PROVIDERS[site]}
                rows.append(row)
                try:
                    saved = self.repo.review_state(identity)
                    self.repo.check_agent_scope(saved, scope)
                    if self.repo.agent_revision(saved) != revision:
                        raise ValueError()
                except ValueError:
                    row["warnings"].append("作品资料已变化，请重新搜索后再选来源。")
                    continue
                key = _identity(url)[:2]
                if key not in cache:
                    cred = credentials.get(site, {})
                    try:
                        detail = scrape_preview(url, api_key=cred.get("apiKey"), affiliate_id=cred.get("affiliateId"),
                                                cancel=cancel)
                        if _identity(detail["sourceUrl"])[:2] != key:
                            raise ScrapeError("站点返回了不同的作品。", "SCRAPE_RESPONSE")
                        cache[key] = detail
                    except ScrapeError as error:
                        cache[key] = _provider_notice(site, error.code)
                detail = cache[key]
                if isinstance(detail, str):
                    row["warnings"].append(detail)
                    continue
                before = saved["metadata"]
                warnings = [text for value in detail["warnings"] if (text := _safe_text(value, 400))]
                changes = _patch(before, detail["fields"], detail["sourceUrl"], warnings)
                row["warnings"] = list(dict.fromkeys(warnings))
                if not changes:
                    row["warnings"].append("已读取资料，未发现可填入的变化。")
                    continue
                row["fields"] = [{"field": field,
                                  "before": {part: before[part] for part in _DATE_FIELDS} if field == "release_date" else before[field],
                                  "after": value} for field, value in changes.items()]
                plans[identity] = (title, revision, changes)
            check_cancel(cancel)
        except Cancelled:
            return {"token": None, "books": rows, "cancelled": True}
        return {"token": self.agent.remember_plan(scope, plans) if plans else None, "books": rows, "cancelled": False}
