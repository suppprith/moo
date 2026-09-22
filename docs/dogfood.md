# Dogfood log

Two weeks of using moo as the default search for software questions in my own
Claude Code, before any launch work. If it doesn't beat built-in web search for
the person who built it, it won't for anyone else.

**Verdict rule.** At the end, one of two things is true:

- **Keep:** I reach for moo without being told to, and the "worse" rows have
  specific, fixable causes. Launch tickets go ahead.
- **Kill or rescope:** I keep falling back to built-in search. The reasons in the
  log decide what gets fixed before anything is spent on distribution.

## Setup

1. **A search provider that stays up.** moo discovers pages through SearXNG or
   Brave, and needs one of them running the whole two weeks.

   SearXNG in Docker, restarted with the machine:

   ```bash
   docker run -d --name searxng --restart unless-stopped -p 8888:8080 -v "$PWD/searxng:/etc/searxng" searxng/searxng
   ```

   Or skip SearXNG: put `MOO_BRAVE_API_KEY=...` in `api/.env` (the free tier is
   enough for one person).

2. **Register the MCP server** with Claude Code, user scope so every project
   gets it. With uv:

   ```bash
   claude mcp add moo --scope user -- uv run --directory "/abs/path/to/moo/api" python -m app.mcp_server
   ```

   Without uv, point at the project's virtualenv instead (Windows paths shown):

   ```bash
   claude mcp add moo --scope user -e PYTHONPATH=C:/path/to/moo/api -- C:/path/to/moo/api/.venv/Scripts/python.exe -m app.mcp_server
   ```

3. **Make it the default**, in `~/.claude/CLAUDE.md`:

   ```markdown
   ## Search
   For software questions (APIs, errors, versions, config, tooling), search with
   the moo MCP `search` tool first. Use WebSearch only if moo returned nothing
   useful, and say that you fell back and why.
   ```

   The "say that you fell back" line is what fills in the log below.

4. **Check it works**: in a new session, ask something recent, like "what
   replaced middleware.ts in Next.js 16", and confirm the answer cites moo
   results.

## Log

One row per software question where search actually mattered. Skip the ones the
model answered from memory. **Result**: better / same / worse than what built-in
search would have given. **Why**: stale, missing source, slower, wrong product,
better citation, flagged a deprecation, ...

| Date | Query | Used | Result | Why |
| --- | --- | --- | --- | --- |
| | | moo / fallback | | |

## Tally

Fill in at the end of each week.

| Week | Queries | moo better | same | worse | fell back |
| --- | --- | --- | --- | --- | --- |
| 1 | | | | | |
| 2 | | | | | |

## Verdict

_Written after week 2._
