# moo-web

The Next.js (App Router) front end for **moo**: the search playground, the docs
site, and the "why moo" page.

| Route | What it is |
| --- | --- |
| `/` | the playground: search, claims, evidence graph, deep research |
| `/docs` | index, quickstarts, API-reference pointers |
| `/docs/[slug]` | the repo's own markdown, rendered |
| `/why` | the claim, the comparison table, and where competitors win |

The docs pages read `docs/*.md` from the repository at build time, so the site
and the repo cannot drift: there is one copy of every explanation. Add a page by
adding an entry to `lib/docs.ts`. Repo-relative links are rewritten on the way
through, to a sibling docs route where one exists and to GitHub otherwise.

## Develop

```bash
npm install
npm run dev      # http://localhost:3000
npm run build    # also prerenders every docs page
npm test         # node --test over the pure logic in lib/
npm run lint     # eslint (next/core-web-vitals + next/typescript)
npm run format   # prettier --check
```

Clicking a result or an evidence chip opens the source panel: the chunk's own
text with the cited span marked, its trust tier, author role and date, the
neighbouring sections, and a link to the exact anchor rather than the top of the
page. Which span gets marked is decided by `lib/highlight.ts`, which is plain
functions over offsets so it can be tested without a browser.

## Configuration

| Variable | Effect |
| --- | --- |
| `NEXT_PUBLIC_API_BASE` | the moo API to call (default `http://127.0.0.1:8000`) |
| `NEXT_PUBLIC_DEMO_MODE` | `1` shows the shared-instance notice on the playground |

A public playground should also be rate limited per visitor on the API side:
set `MOO_DEMO_RATE_LIMIT_PER_MIN` there, which meters keyless callers by IP
while keyed callers keep their own quota.

## Deploying

The build needs the repository root available, because it reads `../docs`. On a
host where the project root is `web/` (Vercel and friends), that is already true
for a normal checkout. Set `NEXT_PUBLIC_API_BASE` to the deployed API, and
`MOO_CORS_ORIGINS` on the API to this origin.
