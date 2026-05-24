#!/usr/bin/env python3
"""学术文献搜索与 BibTeX 获取工具 — Semantic Scholar + CrossRef + DBLP 三级 fallback。

用法：
    python scholar_fetch.py search "attention mechanism" --max 5
    python scholar_fetch.py search "Vaswani attention is all you need" --max 3
    python scholar_fetch.py bibtex "attention is all you need" --max 5
    python scholar_fetch.py bibtex-doi "10.1145/3292500.3330919"
    python scholar_fetch.py bibtex-id "S2:204e3073870fae3d05bcbc2f6a8e263d9b72e776"

三个 API 均为免费公开接口，无需 API Key：
- Semantic Scholar：学术搜索，返回标题/作者/年份/DOI/摘要/引用数
- CrossRef：通过 DOI 获取权威 BibTeX
- DBLP：CS 领域最权威的 BibTeX 来源

搜索策略：
1. 先用 Semantic Scholar 搜索（覆盖面最广）
2. 对每个结果，优先从 DBLP 获取 BibTeX（最干净）
3. DBLP 失败则用 CrossRef DOI 获取 BibTeX
4. 都失败则从 Semantic Scholar 元数据自动生成 BibTeX
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

_USER_AGENT = "MHAgent-ScholarFetch/1.0 (academic research tool)"
_TIMEOUT = 15  # 单次请求超时秒数
_RATE_DELAY = 0.3  # 请求间隔（秒），避免被限流


# ============================================================
# 通用 HTTP 工具
# ============================================================

def _http_get(url: str, headers: Optional[dict] = None, timeout: int = _TIMEOUT) -> Optional[str]:
    """发送 GET 请求，返回响应文本。失败返回 None。"""
    hdrs = {"User-Agent": _USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as e:
        _log(f"HTTP GET failed: {url} -> {e}")
        return None


def _log(msg: str):
    """输出日志到 stderr（不干扰 stdout 的 JSON 输出）。"""
    print(f"[scholar_fetch] {msg}", file=sys.stderr)


# ============================================================
# Semantic Scholar API
# ============================================================

def semantic_scholar_search(query: str, max_results: int = 5) -> list[dict]:
    """搜索 Semantic Scholar，返回论文列表。
    
    API 文档: https://api.semanticscholar.org/api-docs/graph
    免费限额: 100 次/5分钟（无 key），1000 次/5分钟（有 key）
    """
    fields = "title,authors,year,externalIds,abstract,citationCount,venue,publicationTypes,journal"
    params = urllib.parse.urlencode({
        "query": query,
        "limit": min(max_results, 20),
        "fields": fields,
    })
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?{params}"
    
    text = _http_get(url)
    if not text:
        return []
    
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        _log(f"S2 JSON parse error: {text[:200]}")
        return []
    
    papers = data.get("data", [])
    results = []
    for p in papers:
        ext_ids = p.get("externalIds") or {}
        authors_raw = p.get("authors") or []
        authors = [a.get("name", "") for a in authors_raw if a.get("name")]
        
        result = {
            "source": "semantic_scholar",
            "s2_id": p.get("paperId", ""),
            "title": (p.get("title") or "").strip(),
            "authors": authors,
            "year": p.get("year"),
            "doi": ext_ids.get("DOI", ""),
            "arxiv_id": ext_ids.get("ArXiv", ""),
            "dblp_id": ext_ids.get("DBLP", ""),
            "venue": (p.get("venue") or "").strip(),
            "citation_count": p.get("citationCount", 0),
            "abstract": (p.get("abstract") or "")[:500],
        }
        # 从 journal 字段补充 venue
        if not result["venue"] and p.get("journal"):
            result["venue"] = (p["journal"].get("name") or "").strip()
        results.append(result)
    
    return results


def semantic_scholar_by_id(paper_id: str) -> Optional[dict]:
    """通过 Semantic Scholar Paper ID / DOI / ArXiv ID 获取单篇论文详情。
    
    支持的 ID 格式:
    - S2 Paper ID: "204e3073870fae3d05bcbc2f6a8e263d9b72e776"
    - DOI: "DOI:10.1145/xxx" 或 "10.1145/xxx"
    - ArXiv: "ArXiv:2301.07041" 或 "ARXIV:2301.07041"
    - DBLP: "DBLP:conf/nips/VaswaniSPUJGKP17"
    """
    fields = "title,authors,year,externalIds,abstract,citationCount,venue,journal,publicationTypes"
    # 自动加前缀
    if re.match(r"^10\.\d{4,}/", paper_id):
        paper_id = f"DOI:{paper_id}"
    elif re.match(r"^\d{4}\.\d{4,5}", paper_id):
        paper_id = f"ARXIV:{paper_id}"
    
    url = f"https://api.semanticscholar.org/graph/v1/paper/{urllib.parse.quote(paper_id, safe='')}?fields={fields}"
    text = _http_get(url)
    if not text:
        return None
    
    try:
        p = json.loads(text)
    except json.JSONDecodeError:
        return None
    
    if "error" in p or not p.get("paperId"):
        return None
    
    ext_ids = p.get("externalIds") or {}
    authors_raw = p.get("authors") or []
    authors = [a.get("name", "") for a in authors_raw if a.get("name")]
    
    result = {
        "source": "semantic_scholar",
        "s2_id": p.get("paperId", ""),
        "title": (p.get("title") or "").strip(),
        "authors": authors,
        "year": p.get("year"),
        "doi": ext_ids.get("DOI", ""),
        "arxiv_id": ext_ids.get("ArXiv", ""),
        "dblp_id": ext_ids.get("DBLP", ""),
        "venue": (p.get("venue") or "").strip(),
        "citation_count": p.get("citationCount", 0),
        "abstract": (p.get("abstract") or "")[:500],
    }
    if not result["venue"] and p.get("journal"):
        result["venue"] = (p["journal"].get("name") or "").strip()
    return result



# ============================================================
# CrossRef API（通过 DOI 获取 BibTeX）
# ============================================================

def crossref_bibtex_by_doi(doi: str) -> Optional[str]:
    """通过 DOI 从 CrossRef 获取 BibTeX。这是最权威的 BibTeX 来源之一。
    
    使用 content negotiation: Accept: application/x-bibtex
    """
    if not doi:
        return None
    
    url = f"https://doi.org/{urllib.parse.quote(doi, safe='/:')}"
    text = _http_get(url, headers={"Accept": "application/x-bibtex"})
    if text and text.strip().startswith("@"):
        return text.strip()
    return None


def crossref_search(query: str, max_results: int = 5) -> list[dict]:
    """搜索 CrossRef，返回论文列表。
    
    API 文档: https://api.crossref.org/swagger-ui/index.html
    """
    params = urllib.parse.urlencode({
        "query": query,
        "rows": min(max_results, 20),
        "select": "DOI,title,author,published-print,published-online,container-title,type",
    })
    url = f"https://api.crossref.org/works?{params}"
    
    text = _http_get(url)
    if not text:
        return []
    
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    
    items = data.get("message", {}).get("items", [])
    results = []
    for item in items:
        title_list = item.get("title", [])
        title = title_list[0] if title_list else ""
        
        authors_raw = item.get("author", [])
        authors = []
        for a in authors_raw:
            given = a.get("given", "")
            family = a.get("family", "")
            if family:
                authors.append(f"{given} {family}".strip())
        
        # 提取年份
        year = None
        for date_field in ["published-print", "published-online"]:
            date_parts = item.get(date_field, {}).get("date-parts", [[]])
            if date_parts and date_parts[0] and date_parts[0][0]:
                year = date_parts[0][0]
                break
        
        venue_list = item.get("container-title", [])
        venue = venue_list[0] if venue_list else ""
        
        results.append({
            "source": "crossref",
            "doi": item.get("DOI", ""),
            "title": title.strip(),
            "authors": authors,
            "year": year,
            "venue": venue.strip(),
            "type": item.get("type", ""),
        })
    
    return results


# ============================================================
# DBLP API（CS 领域最权威的 BibTeX）
# ============================================================

def dblp_search(query: str, max_results: int = 5) -> list[dict]:
    """搜索 DBLP，返回论文列表。
    
    API 文档: https://dblp.org/faq/How+to+use+the+dblp+search+API.html
    """
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "h": min(max_results, 20),
    })
    url = f"https://dblp.org/search/publ/api?{params}"
    
    text = _http_get(url)
    if not text:
        return []
    
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    
    hits = data.get("result", {}).get("hits", {}).get("hit", [])
    results = []
    for hit in hits:
        info = hit.get("info", {})
        
        # DBLP 的 authors 可能是 dict 或 list
        authors_raw = info.get("authors", {}).get("author", [])
        if isinstance(authors_raw, dict):
            authors_raw = [authors_raw]
        authors = []
        for a in authors_raw:
            if isinstance(a, dict):
                authors.append(a.get("text", ""))
            elif isinstance(a, str):
                authors.append(a)
        
        results.append({
            "source": "dblp",
            "dblp_key": info.get("key", ""),
            "title": (info.get("title") or "").strip().rstrip("."),
            "authors": authors,
            "year": int(info["year"]) if info.get("year", "").isdigit() else None,
            "venue": (info.get("venue") or "").strip(),
            "doi": (info.get("doi") or "").strip(),
            "url": (info.get("url") or "").strip(),
            "type": (info.get("type") or "").strip(),
        })
    
    return results


def dblp_bibtex(dblp_key: str) -> Optional[str]:
    """通过 DBLP key 获取 BibTeX。
    
    例如: dblp_key = "conf/nips/VaswaniSPUJGKP17"
    """
    if not dblp_key:
        return None
    
    # 确保 key 格式正确
    key = dblp_key.strip()
    if key.startswith("DBLP:"):
        key = key[5:]
    
    url = f"https://dblp.org/rec/{key}.bib"
    text = _http_get(url)
    if text and text.strip().startswith("@"):
        return text.strip()
    return None


def dblp_search_bibtex(query: str) -> Optional[str]:
    """搜索 DBLP 并返回第一个匹配结果的 BibTeX。"""
    results = dblp_search(query, max_results=1)
    if not results:
        return None
    
    key = results[0].get("dblp_key", "")
    if key:
        return dblp_bibtex(key)
    return None


# ============================================================
# 智能 BibTeX 获取（三级 fallback）
# ============================================================

def _make_citation_key(authors: list[str], year, title: str) -> str:
    """生成 BibTeX citation key: firstauthor_year_keyword"""
    # 第一作者姓氏
    first_author = ""
    if authors:
        name = authors[0]
        # 处理 "Last, First" 和 "First Last" 两种格式
        if "," in name:
            first_author = name.split(",")[0].strip()
        else:
            parts = name.split()
            first_author = parts[-1] if parts else "unknown"
    first_author = re.sub(r"[^a-zA-Z]", "", first_author).lower() or "unknown"
    
    # 年份
    yr = str(year) if year else "xxxx"
    
    # 标题关键词（取第一个有意义的词）
    stop_words = {"a", "an", "the", "of", "for", "and", "in", "on", "to", "with", "is", "are", "by"}
    words = re.findall(r"[a-zA-Z]+", title.lower())
    keyword = ""
    for w in words:
        if w not in stop_words and len(w) > 2:
            keyword = w
            break
    keyword = keyword or "paper"
    
    return f"{first_author}_{yr}_{keyword}"


def _compute_match_score(query: str, title: str, authors: list[str] = None) -> float:
    """计算搜索查询和返回结果的匹配度（0.0-1.0）。
    
    用于检测搜索结果是否真的是用户想要的论文，防止"标题相近但实际不同"的假匹配。
    
    策略：
    - 将 query 和 title 都转小写，分词
    - 计算词级别的 Jaccard 相似度
    - 如果 query 中包含作者姓氏且 authors 中也有，加分
    - 如果 query 中包含年份且 title/authors 匹配，加分
    """
    import re
    
    if not query or not title:
        return 0.0
    
    # 清理和分词
    def tokenize(text: str) -> set[str]:
        text = text.lower().strip()
        # 移除标点，按空格和下划线分词
        tokens = re.split(r'[\s_\-:,;.(){}]+', text)
        # 过滤掉太短的词和纯数字
        return {t for t in tokens if len(t) >= 2 and not t.isdigit()}
    
    q_tokens = tokenize(query)
    t_tokens = tokenize(title)
    
    if not q_tokens or not t_tokens:
        return 0.0
    
    # Jaccard 相似度
    intersection = q_tokens & t_tokens
    union = q_tokens | t_tokens
    jaccard = len(intersection) / len(union) if union else 0.0
    
    # 标题包含度：query 中的词有多少出现在 title 中
    query_coverage = len(intersection) / len(q_tokens) if q_tokens else 0.0
    
    # 综合分数（标题包含度权重更高，因为 query 通常比 title 短）
    score = 0.4 * jaccard + 0.6 * query_coverage
    
    # 作者匹配加分
    if authors:
        q_lower = query.lower()
        for author in authors:
            # 取姓氏（最后一个词）
            surname = author.strip().split()[-1].lower() if author.strip() else ""
            if surname and len(surname) >= 2 and surname in q_lower:
                score = min(1.0, score + 0.15)
                break
    
    return round(score, 3)


def _match_label(score: float) -> str:
    """根据匹配度分数返回标签。"""
    if score >= 0.6:
        return "good"
    elif score >= 0.3:
        return "partial"
    else:
        return "low"


def _escape_bibtex(text: str) -> str:
    """转义 BibTeX 特殊字符。"""
    # 保留已有的 LaTeX 命令（如 {\"u}）
    text = text.replace("&", r"\&")
    text = text.replace("%", r"\%")
    text = text.replace("#", r"\#")
    return text


def _generate_bibtex_from_metadata(paper: dict) -> str:
    """从元数据自动生成 BibTeX（最后的 fallback）。"""
    authors = paper.get("authors", [])
    title = paper.get("title", "Unknown Title")
    year = paper.get("year", "")
    doi = paper.get("doi", "")
    venue = paper.get("venue", "")
    arxiv_id = paper.get("arxiv_id", "")
    
    key = _make_citation_key(authors, year, title)
    author_str = " and ".join(authors) if authors else "Unknown"
    
    # 判断类型
    if arxiv_id and not venue:
        # arXiv 预印本
        lines = [
            f"@misc{{{key},",
            f"  title = {{{_escape_bibtex(title)}}},",
            f"  author = {{{_escape_bibtex(author_str)}}},",
            f"  year = {{{year}}},",
            f"  eprint = {{{arxiv_id}}},",
            f"  archiveprefix = {{arXiv}},",
        ]
        if doi:
            lines.append(f"  doi = {{{doi}}},")
        lines.append(f"  note = {{[AUTO-GENERATED] Verify before submission}}")
        lines.append("}")
    elif venue:
        # 有 venue，判断是会议还是期刊
        conf_keywords = {"conference", "proceedings", "workshop", "symposium",
                         "ICML", "NeurIPS", "ICLR", "AAAI", "IJCAI", "ACL",
                         "EMNLP", "CVPR", "ICCV", "ECCV", "KDD", "WWW",
                         "SIGIR", "SIGMOD", "VLDB", "ICDE", "CIKM"}
        is_conf = any(kw.lower() in venue.lower() for kw in conf_keywords)
        
        if is_conf:
            lines = [
                f"@inproceedings{{{key},",
                f"  title = {{{_escape_bibtex(title)}}},",
                f"  author = {{{_escape_bibtex(author_str)}}},",
                f"  booktitle = {{{_escape_bibtex(venue)}}},",
                f"  year = {{{year}}},",
            ]
        else:
            lines = [
                f"@article{{{key},",
                f"  title = {{{_escape_bibtex(title)}}},",
                f"  author = {{{_escape_bibtex(author_str)}}},",
                f"  journal = {{{_escape_bibtex(venue)}}},",
                f"  year = {{{year}}},",
            ]
        if doi:
            lines.append(f"  doi = {{{doi}}},")
        lines.append(f"  note = {{[AUTO-GENERATED] Verify before submission}}")
        lines.append("}")
    else:
        # 最简格式
        lines = [
            f"@misc{{{key},",
            f"  title = {{{_escape_bibtex(title)}}},",
            f"  author = {{{_escape_bibtex(author_str)}}},",
            f"  year = {{{year}}},",
        ]
        if doi:
            lines.append(f"  doi = {{{doi}}},")
        lines.append(f"  note = {{[AUTO-GENERATED] Verify before submission}}")
        lines.append("}")
    
    return "\n".join(lines)


def fetch_bibtex(query: str, max_results: int = 5) -> list[dict]:
    """智能获取 BibTeX：搜索论文 + 三级 fallback 获取 BibTeX。
    
    返回格式: [{"title": ..., "authors": [...], "year": ..., "bibtex": "...", "source": "dblp/crossref/auto", ...}]
    """
    # Step 1: 用 Semantic Scholar 搜索
    _log(f"Searching Semantic Scholar: {query}")
    papers = semantic_scholar_search(query, max_results=max_results)
    
    # 如果 S2 没结果，尝试 DBLP
    if not papers:
        _log("S2 returned no results, trying DBLP...")
        dblp_results = dblp_search(query, max_results=max_results)
        for dr in dblp_results:
            papers.append({
                "title": dr["title"],
                "authors": dr["authors"],
                "year": dr["year"],
                "doi": dr.get("doi", ""),
                "dblp_id": dr.get("dblp_key", ""),
                "arxiv_id": "",
                "venue": dr.get("venue", ""),
                "citation_count": 0,
                "abstract": "",
                "source": "dblp",
            })
    
    # 如果还没结果，尝试 CrossRef
    if not papers:
        _log("DBLP returned no results, trying CrossRef...")
        cr_results = crossref_search(query, max_results=max_results)
        for cr in cr_results:
            papers.append({
                "title": cr["title"],
                "authors": cr["authors"],
                "year": cr["year"],
                "doi": cr.get("doi", ""),
                "dblp_id": "",
                "arxiv_id": "",
                "venue": cr.get("venue", ""),
                "citation_count": 0,
                "abstract": "",
                "source": "crossref",
            })
    
    if not papers:
        _log("No results from any source")
        return []
    
    # Step 2: 对每篇论文获取 BibTeX（三级 fallback）
    results = []
    for paper in papers:
        bibtex = None
        bib_source = "auto"
        
        # 尝试 1: DBLP（最干净的 BibTeX）
        dblp_id = paper.get("dblp_id", "")
        if dblp_id:
            _log(f"  Trying DBLP BibTeX: {dblp_id}")
            bibtex = dblp_bibtex(dblp_id)
            if bibtex:
                bib_source = "dblp"
                _log(f"  ✓ Got BibTeX from DBLP")
            time.sleep(_RATE_DELAY)
        
        # 尝试 1b: DBLP 搜索（如果没有 dblp_id）
        if not bibtex and paper.get("title"):
            title = paper["title"]
            first_author = paper["authors"][0].split()[-1] if paper.get("authors") else ""
            dblp_query = f"{title} {first_author}".strip()
            _log(f"  Trying DBLP search: {dblp_query[:60]}...")
            dblp_bib = dblp_search_bibtex(dblp_query)
            if dblp_bib:
                bibtex = dblp_bib
                bib_source = "dblp"
                _log(f"  ✓ Got BibTeX from DBLP search")
            time.sleep(_RATE_DELAY)
        
        # 尝试 2: CrossRef DOI
        if not bibtex and paper.get("doi"):
            _log(f"  Trying CrossRef DOI: {paper['doi']}")
            bibtex = crossref_bibtex_by_doi(paper["doi"])
            if bibtex:
                bib_source = "crossref"
                _log(f"  ✓ Got BibTeX from CrossRef")
            time.sleep(_RATE_DELAY)
        
        # 尝试 3: 自动生成
        if not bibtex:
            _log(f"  Generating BibTeX from metadata")
            bibtex = _generate_bibtex_from_metadata(paper)
            bib_source = "auto"
        
        results.append({
            "title": paper.get("title", ""),
            "authors": paper.get("authors", []),
            "year": paper.get("year"),
            "doi": paper.get("doi", ""),
            "arxiv_id": paper.get("arxiv_id", ""),
            "venue": paper.get("venue", ""),
            "citation_count": paper.get("citation_count", 0),
            "abstract": paper.get("abstract", ""),
            "bibtex": bibtex,
            "bibtex_source": bib_source,
            "match_score": _compute_match_score(query, paper.get("title", ""), paper.get("authors", [])),
            "match_label": _match_label(_compute_match_score(query, paper.get("title", ""), paper.get("authors", []))),
        })
    
    return results


def fetch_bibtex_by_doi(doi: str) -> Optional[dict]:
    """通过 DOI 获取单篇论文的 BibTeX。"""
    # 先从 CrossRef 获取 BibTeX
    bibtex = crossref_bibtex_by_doi(doi)
    bib_source = "crossref" if bibtex else None
    
    # 获取元数据
    paper = semantic_scholar_by_id(doi)
    
    if not bibtex and paper:
        # 尝试 DBLP
        dblp_id = paper.get("dblp_id", "")
        if dblp_id:
            bibtex = dblp_bibtex(dblp_id)
            if bibtex:
                bib_source = "dblp"
    
    if not bibtex and paper:
        bibtex = _generate_bibtex_from_metadata(paper)
        bib_source = "auto"
    
    if not bibtex:
        return None
    
    return {
        "title": paper.get("title", "") if paper else "",
        "authors": paper.get("authors", []) if paper else [],
        "year": paper.get("year") if paper else None,
        "doi": doi,
        "bibtex": bibtex,
        "bibtex_source": bib_source,
    }


# ============================================================
# CLI 入口
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="学术文献搜索与 BibTeX 获取工具（Semantic Scholar + CrossRef + DBLP）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scholar_fetch.py search "transformer attention mechanism" --max 5
  python scholar_fetch.py bibtex "attention is all you need" --max 3
  python scholar_fetch.py bibtex-doi "10.1145/3292500.3330919"
  python scholar_fetch.py bibtex-id "ARXIV:2301.07041"
        """,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # search: 搜索论文（返回元数据，不含 BibTeX）
    sp_search = subparsers.add_parser("search", help="搜索论文（返回元数据）")
    sp_search.add_argument("query", help="搜索关键词或论文标题")
    sp_search.add_argument("--max", type=int, default=5, help="最大结果数（默认 5）")
    
    # bibtex: 搜索论文并获取 BibTeX
    sp_bib = subparsers.add_parser("bibtex", help="搜索论文并获取 BibTeX（三级 fallback）")
    sp_bib.add_argument("query", help="搜索关键词或论文标题")
    sp_bib.add_argument("--max", type=int, default=5, help="最大结果数（默认 5）")
    
    # bibtex-doi: 通过 DOI 获取 BibTeX
    sp_doi = subparsers.add_parser("bibtex-doi", help="通过 DOI 获取 BibTeX")
    sp_doi.add_argument("doi", help="DOI 标识符，如 10.1145/3292500.3330919")
    
    # bibtex-id: 通过 Semantic Scholar / ArXiv / DBLP ID 获取 BibTeX
    sp_id = subparsers.add_parser("bibtex-id", help="通过论文 ID 获取 BibTeX")
    sp_id.add_argument("paper_id", help="论文 ID（S2 ID / DOI:xxx / ARXIV:xxx / DBLP:xxx）")
    
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    
    if args.command == "search":
        results = semantic_scholar_search(args.query, max_results=args.max)
        if not results:
            # fallback to DBLP
            results = dblp_search(args.query, max_results=args.max)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    
    elif args.command == "bibtex":
        results = fetch_bibtex(args.query, max_results=args.max)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    
    elif args.command == "bibtex-doi":
        result = fetch_bibtex_by_doi(args.doi)
        if result:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        else:
            print(json.dumps({"error": f"No BibTeX found for DOI: {args.doi}"}))
            return 1
    
    elif args.command == "bibtex-id":
        paper = semantic_scholar_by_id(args.paper_id)
        if not paper:
            print(json.dumps({"error": f"Paper not found: {args.paper_id}"}))
            return 1
        
        # 获取 BibTeX
        bibtex = None
        bib_source = "auto"
        
        if paper.get("dblp_id"):
            bibtex = dblp_bibtex(paper["dblp_id"])
            if bibtex:
                bib_source = "dblp"
        
        if not bibtex and paper.get("doi"):
            bibtex = crossref_bibtex_by_doi(paper["doi"])
            if bibtex:
                bib_source = "crossref"
        
        if not bibtex:
            bibtex = _generate_bibtex_from_metadata(paper)
            bib_source = "auto"
        
        paper["bibtex"] = bibtex
        paper["bibtex_source"] = bib_source
        print(json.dumps(paper, ensure_ascii=False, indent=2))
        return 0
    
    return 1


if __name__ == "__main__":
    sys.exit(main())
