# Research MCP Server

ENMGT 4405 Assignment 4 - David Reeves (dlr266)

A small Model Context Protocol (MCP) server in Python for the **Academic
Research** domain. It lets Claude Desktop search for papers, build citations,
and keep a bibliography, using the free
[OpenAlex API](https://help.openalex.org/api/).

| Endpoint | Type | What it does |
|---|---|---|
| `search_papers(query, limit=5)` | tool | Keyword search; returns id, title, authors, year, venue, citation count |
| `cite_paper(paper_id, style="apa")` | tool | APA or BibTeX citation for one paper; saves it to `bibliography.json` |
| `bibliography://all` | resource | The saved bibliography as an APA reference list |
| `literature_review(topic, num_papers=5)` | prompt | Template that chains search -> cite -> write |

Best practices implemented (see `safety.py` and `run_exploits.py`):

1. **Input sanitization** - search queries are length-capped and stripped of
   control characters; paper IDs must match an allow-list regex before they
   are placed in a URL.
2. **Rate limiting** - a sliding-window limiter caps calls per tool
   (default 10 per 60 s, configurable in `.env`).

## Files

```
research_server.py          the MCP server (tools, resource, prompt)
safety.py                   input sanitization + rate limiter
run_exploits.py             attacks each protection and shows it holds
test_client.py              optional: talks to the server over stdio like Claude Desktop does
requirements.txt            Python dependencies
claude_desktop_config.json  MCP config for Claude Desktop
.env.example                template for secrets (copy to .env)
```

## Setup (macOS, VS Code, uv)

1. Clone the repo and open the folder in VS Code.

2. Create and activate a virtual environment in the VS Code terminal:

   ```bash
   uv venv                       # creates .venv
   source .venv/bin/activate     # prompt now shows (research-mcp-server)
   uv pip install -r requirements.txt
   ```

   Then pick the interpreter in VS Code: `Cmd+Shift+P` -> "Python: Select
   Interpreter" -> `.venv/bin/python`.

3. Create your secrets file:

   ```bash
   cp .env.example .env
   ```

   Get a free OpenAlex key (about 30 seconds): create an account at
   <https://openalex.org>, open your account settings page and copy the API
   key. Paste it into `.env` after `OPENALEX_API_KEY=`. The key is optional:
   without it the server still works on OpenAlex's smaller keyless budget.

4. Smoke test (also demonstrates the two best practices):

   ```bash
   python run_exploits.py
   ```

   Every line should say `BLOCKED`.

5. Confirm the server speaks MCP. This starts `research_server.py` as a
   subprocess over stdio (what Claude Desktop does) and calls every endpoint:

   ```bash
   python test_client.py
   ```

   If OpenAlex ever answers "429 Too Many Requests", the server retries
   three times with a short back-off and then returns a friendly error.

## Connect to Claude Desktop

1. Open Claude Desktop -> Settings -> Developer -> **Edit Config**. This opens
   `~/Library/Application Support/Claude/claude_desktop_config.json`.
2. Paste the contents of `claude_desktop_config.json` from this repo. If your
   clone is not at `/Users/davidreeves/Python/research-mcp-server`, change
   both paths to match (`pwd` in the project folder prints the right prefix).
3. Quit and reopen Claude Desktop. The "research" server should appear under
   the tools icon with `search_papers` and `cite_paper`.

Things to try in Claude Desktop:

* "Search for papers on model context protocol security."
* "Cite paper W7129030749 in APA style."  then  "Now cite the same paper in bibtex style."
* "Cite doi:10.1109/comst.2015.2444095 in APA style."
* Resource: **+** > Connectors > Add from research > **Bibliography**, then
  "Show me the attached bibliography."
* Prompt: **+** > Connectors > Add from research > **Literature review**,
  fill in `topic` and `num_papers`, submit.

Note: Claude Desktop lets *you* attach a resource; it does not let the model
read resources on its own. `test_client.py` shows the resource and prompt
working over a direct MCP connection.

## Demo of the best practices

`python run_exploits.py` runs three attacks against the real tool functions:

1. **Injection through `paper_id`** - the script first proves the attack is
   real (an unprotected request to `works/../authors` is answered by
   OpenAlex's *authors* endpoint), then shows that path traversal, extra
   query parameters, SQL-style payloads and script tags are all rejected by
   `check_paper_id` before any HTTP request is built.
2. **Oversized / control-character queries** - a 6,000-character query is
   rejected; CR, LF and NUL bytes are stripped by `check_query`.
3. **Rate limit** - the limiter is set to 3 calls per 60 s, then `cite_paper`
   is called five times. Calls 4 and 5 are rejected.

To show the rate limit live in Claude Desktop, set `RATE_LIMIT_CALLS=2` in
`.env`, restart Claude Desktop, and ask it to run three searches.

## Code citations

* Server structure (`FastMCP`, `@mcp.tool`, `@mcp.resource`, `@mcp.prompt`,
  `mcp.run()`) follows the Quickstart in the official MCP Python SDK README:
  <https://github.com/modelcontextprotocol/python-sdk>
* OpenAlex endpoint paths, the `select` parameter and Bearer-token
  authentication follow the API reference: <https://help.openalex.org/api/>
  and <https://help.openalex.org/api/authentication/>
* Sliding-window rate limiter uses `collections.deque` as described in the
  Python docs: <https://docs.python.org/3/library/collections.html#collections.deque>
* Claude Desktop config format follows the MCP user quickstart:
  <https://modelcontextprotocol.io/quickstart/user>

Each citation is repeated as a comment next to the code it applies to.

## AI assistance

Code was drafted with Claude (Anthropic) inside this course workflow and
then reviewed, tested and edited by me. See the assignment write-up on
Canvas for the reflection.
