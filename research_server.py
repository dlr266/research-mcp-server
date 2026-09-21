"""
research_server.py - an MCP server for academic research.

Endpoints exposed to the MCP client (Claude Desktop):
  * tool      search_papers(query, limit)   - keyword search on OpenAlex
  * tool      cite_paper(paper_id, style)   - APA or BibTeX citation, saved to a bibliography
  * resource  bibliography://all            - the bibliography built so far
  * prompt    literature_review(topic, n)   - template that chains the pieces together

Run it with:   python research_server.py      (Claude Desktop does this for you)

Server skeleton (FastMCP object, @mcp.tool / @mcp.resource / @mcp.prompt
decorators, mcp.run()) follows the "Quickstart" example in the official
MCP Python SDK README: https://github.com/modelcontextprotocol/python-sdk
"""

import json
import logging
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values
from mcp.server.fastmcp import FastMCP

from safety import RateLimiter, check_paper_id, check_query

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# All file paths are built from this file's location with pathlib, so the
# server works no matter which directory Claude Desktop launches it from.
PROJECT_DIR = Path(__file__).resolve().parent
ENV_FILE = PROJECT_DIR / ".env"
BIBLIOGRAPHY_FILE = PROJECT_DIR / "bibliography.json"

# Secrets live in .env (never committed). dotenv_values() reads the file into
# a plain dictionary; it does NOT touch the system environment variables.
config = dotenv_values(ENV_FILE)
API_KEY = config.get("OPENALEX_API_KEY")                     # optional
RATE_LIMIT_CALLS = int(config.get("RATE_LIMIT_CALLS", 10))   # per tool
RATE_LIMIT_SECONDS = int(config.get("RATE_LIMIT_SECONDS", 60))

# OpenAlex REST API. `select` limits the response to the fields we use.
# Endpoint and field names follow the OpenAlex API reference:
# https://help.openalex.org/api/
API_BASE = "https://api.openalex.org"
WORK_FIELDS = "id,title,authorships,publication_year,primary_location,doi,cited_by_count,biblio"

# stdout is reserved for MCP protocol messages, so logs go to stderr.
logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("research")
logging.getLogger("httpx").setLevel(logging.WARNING)   # hide per-request noise

mcp = FastMCP("research")
limiter = RateLimiter(max_calls=RATE_LIMIT_CALLS, per_seconds=RATE_LIMIT_SECONDS)


# ---------------------------------------------------------------------------
# Helper functions (not exposed to the client)
# ---------------------------------------------------------------------------

def api_get(path: str, params: dict) -> dict:
    """Call one OpenAlex endpoint and return the JSON body.

    The key goes in an Authorization header, as described at
    https://help.openalex.org/api/authentication/
    """
    headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
    for attempt in range(3):
        response = httpx.get(f"{API_BASE}/{path}", params=params,
                             headers=headers, timeout=20)
        if response.status_code == 429:      # "too many requests": back off and retry
            time.sleep(1 + attempt)
            continue
        response.raise_for_status()          # turns other 4xx/5xx into an exception
        return response.json()
    response.raise_for_status()              # still 429 after 3 tries: give up


def summarize(work: dict) -> dict:
    """Keep only the fields a reader (or Claude) needs from a raw OpenAlex work."""
    source = (work.get("primary_location") or {}).get("source") or {}
    biblio = work.get("biblio") or {}
    doi = work.get("doi") or ""
    return {
        "paper_id": (work.get("id") or "").rsplit("/", 1)[-1],   # "https://openalex.org/W123" -> "W123"
        "title": work.get("title"),
        "authors": [a["author"]["display_name"] for a in work.get("authorships") or []],
        "year": work.get("publication_year"),
        "venue": source.get("display_name") or "",
        "volume": biblio.get("volume") or "",
        "issue": biblio.get("issue") or "",
        "pages": "-".join(p for p in (biblio.get("first_page"), biblio.get("last_page")) if p),
        "doi": doi.replace("https://doi.org/", ""),
        "citation_count": work.get("cited_by_count"),
    }


def apa_author(name: str) -> str:
    """'Xinyi Hou' -> 'Hou, X.'"""
    parts = name.split()
    if len(parts) == 1:
        return name
    initials = " ".join(p[0] + "." for p in parts[:-1])
    return f"{parts[-1]}, {initials}"


def format_apa(paper: dict) -> str:
    """Build an APA-style reference from a summarized paper."""
    authors = [apa_author(a) for a in paper["authors"]]
    if len(authors) > 7:                        # keep long author lists readable
        authors = authors[:6] + ["..."] + authors[-1:]
    if len(authors) > 1:
        author_text = ", ".join(authors[:-1]) + ", & " + authors[-1]
    else:
        author_text = authors[0] if authors else "Unknown"

    year = paper["year"] or "n.d."
    reference = f"{author_text} ({year}). {paper['title']}."
    if paper["venue"]:
        reference += f" {paper['venue']}"
        if paper["volume"]:
            reference += f", {paper['volume']}"
            if paper["issue"]:
                reference += f"({paper['issue']})"
        if paper["pages"]:
            reference += f", {paper['pages']}"
        reference += "."
    if paper["doi"]:
        reference += f" https://doi.org/{paper['doi']}"
    return reference


def format_bibtex(paper: dict) -> str:
    """Build a BibTeX entry from a summarized paper."""
    first_author = paper["authors"][0].split()[-1] if paper["authors"] else "unknown"
    key = f"{first_author}{paper['year'] or ''}".lower()
    fields = {
        "author": " and ".join(paper["authors"]),
        "title": paper["title"],
        "journal": paper["venue"],
        "year": paper["year"],
        "volume": paper["volume"],
        "number": paper["issue"],
        "pages": paper["pages"],
        "doi": paper["doi"],
    }
    lines = [f"  {name} = {{{value}}}," for name, value in fields.items() if value]
    return f"@article{{{key},\n" + "\n".join(lines) + "\n}"


def load_bibliography() -> list:
    if BIBLIOGRAPHY_FILE.exists():
        return json.loads(BIBLIOGRAPHY_FILE.read_text())
    return []


def save_bibliography(entries: list) -> None:
    BIBLIOGRAPHY_FILE.write_text(json.dumps(entries, indent=2))


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def search_papers(query: str, limit: int = 5) -> dict:
    """Search OpenAlex for academic papers matching a keyword query.

    Returns up to `limit` papers (max 20) with paper_id, title, authors,
    year, venue, DOI and citation count. Use the paper_id with cite_paper.
    """
    try:
        limiter.check("search_papers")          # best practice 2: rate limit
        query = check_query(query)              # best practice 1: sanitize
    except (ValueError, RuntimeError) as err:
        return {"error": str(err)}

    limit = max(1, min(int(limit), 20))
    try:
        data = api_get("works", {"search": query, "per-page": limit, "select": WORK_FIELDS})
    except httpx.HTTPError as err:
        log.warning("search_papers failed: %s", err)     # details stay in the log
        return {"error": "OpenAlex request failed. Please try again in a moment."}

    papers = [summarize(w) for w in data.get("results", [])]
    return {"query": query, "count": len(papers), "papers": papers}


@mcp.tool()
def cite_paper(paper_id: str, style: str = "apa") -> dict:
    """Get a citation for one paper and add it to the bibliography.

    `paper_id` is an OpenAlex work id such as 'W7129030749' or 'DOI:<doi>'.
    `style` is 'apa' (default) or 'bibtex'.
    """
    try:
        limiter.check("cite_paper")             # best practice 2: rate limit
        paper_id = check_paper_id(paper_id)     # best practice 1: sanitize
    except (ValueError, RuntimeError) as err:
        return {"error": str(err)}

    style = style.lower().strip()
    if style not in ("apa", "bibtex"):
        return {"error": "style must be 'apa' or 'bibtex'."}

    try:
        raw = api_get(f"works/{paper_id}", {"select": WORK_FIELDS})
    except httpx.HTTPError as err:
        log.warning("cite_paper failed: %s", err)
        return {"error": "Could not fetch that paper. Check the ID and try again."}

    paper = summarize(raw)
    paper["apa"] = format_apa(paper)
    paper["bibtex"] = format_bibtex(paper)

    # Save to the bibliography, skipping papers that are already there.
    entries = load_bibliography()
    if not any(e["paper_id"] == paper["paper_id"] for e in entries):
        entries.append(paper)
        save_bibliography(entries)

    return {
        "paper_id": paper["paper_id"],
        "style": style,
        "citation": paper[style],
        "bibliography_size": len(entries),
    }


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------

@mcp.resource("bibliography://all")
def bibliography() -> str:
    """The bibliography built so far, as an APA reference list."""
    entries = load_bibliography()
    if not entries:
        return "Bibliography is empty. Use cite_paper to add papers."
    lines = [f"{i}. {e['apa']}" for i, e in enumerate(entries, start=1)]
    return "References\n\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

@mcp.prompt()
def literature_review(topic: str, num_papers: int = 5) -> str:
    """Template that walks the assistant through a short literature review."""
    return f"""You are helping me write a short literature review on: {topic}

Follow these steps using the research tools:
1. Call search_papers with a focused query for "{topic}" and limit={num_papers}.
2. Pick the {num_papers} most relevant results. Prefer higher citation counts
   and recent years, but explain your choices in one sentence each.
3. Call cite_paper for each chosen paper_id (style "apa").
4. Read the bibliography://all resource to confirm every paper was saved.
5. Write the review: one paragraph of context, one paragraph per paper
   (what it did, why it matters), and a closing paragraph on open questions.
   Cite papers inline as (FirstAuthor, Year) and end with the reference list
   exactly as returned by the bibliography resource.
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Starting research MCP server (API key %s)",
             "loaded" if API_KEY else "not set - using keyless OpenAlex budget")
    mcp.run()   # stdio transport: Claude Desktop talks to us over stdin/stdout
