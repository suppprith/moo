"""The three HTML pages the API serves itself: sign up, here is your key, and
your usage.

They are plain self-contained documents rather than part of the Next.js app, so
the signup flow works on an API-only deployment with nothing else running. The
key is shown exactly once, and the dashboard keeps a pasted key in memory only:
storing it in localStorage would leave an API key sitting in the browser for any
script on the page to read.
"""

from __future__ import annotations

import html

PRIVACY_URL = "https://github.com/suppprith/moo/blob/main/docs/privacy.md"


def _privacy_note() -> str:
    """Stated where a key is handed out, not buried three pages away."""
    return (f'<p class="muted">Your queries are never stored. '
            f'<a href="{PRIVACY_URL}">What moo keeps</a>.</p>')

STYLE = """
:root {
  color-scheme: light dark;
  --bg: #fbfbfd; --panel: #fff; --ink: #16161d; --muted: #6b6b78;
  --line: #e6e6ee; --accent: #5b57d1; --good: #197a5a; --warn: #9a5b00;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #101014; --panel: #17171d; --ink: #ecedf2; --muted: #9a9aa8;
          --line: #26262f; --accent: #8b88f0; --good: #4fd0a3; --warn: #e0a458; }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 3rem 1.25rem; background: var(--bg); color: var(--ink);
  font: 15px/1.6 ui-sans-serif, -apple-system, "Segoe UI", system-ui, sans-serif; }
main { max-width: 46rem; margin: 0 auto; }
h1 { font-size: 1.6rem; letter-spacing: -0.02em; margin: 0 0 0.5rem; }
h2 { font-size: 1rem; margin: 2rem 0 0.75rem; }
p { color: var(--muted); margin: 0 0 1rem; }
a { color: var(--accent); }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  padding: 1.25rem; margin: 1rem 0; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
pre { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 0.9rem; overflow-x: auto; }
.key { font-family: ui-monospace, monospace; font-size: 15px; word-break: break-all;
  padding: 0.9rem; border: 1px dashed var(--accent); border-radius: 10px; }
.btn { display: inline-block; background: var(--accent); color: #fff; text-decoration: none;
  padding: 0.6rem 1.1rem; border: 0; border-radius: 9px; font-size: 15px; cursor: pointer; }
input { width: 100%; padding: 0.6rem 0.75rem; border: 1px solid var(--line); border-radius: 9px;
  background: var(--bg); color: var(--ink); font-family: ui-monospace, monospace; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 0.45rem 0.5rem; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 500; font-size: 13px; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.meter { height: 8px; background: var(--line); border-radius: 99px; overflow: hidden; }
.meter span { display: block; height: 100%; background: var(--accent); }
.warn { color: var(--warn); }
.muted { color: var(--muted); }
"""


def _document(title: str, body: str, *, script: str = "") -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
        f"<body><main>{body}</main>{f'<script>{script}</script>' if script else ''}"
        "</body></html>"
    )


def signup_page(*, enabled: bool, free_credits: int) -> str:
    if enabled:
        body = f"""
        <h1>Get a moo key</h1>
        <p>Sign in with GitHub and start calling. {free_credits} credits a month,
        no card, no waiting on a human.</p>
        <p><a class="btn" href="/signup/github">Continue with GitHub</a></p>
        <p class="muted">moo asks for no scopes. It stores your GitHub id, login and
        public email to own the key, and keeps no token afterwards.</p>
        <h2>What a call costs</h2>
        <table>
          <tr><th>Call</th><th class="num">Credits</th></tr>
          <tr><td>Search or web search</td><td class="num">1</td></tr>
          <tr><td>Extract</td><td class="num">1 per URL</td></tr>
          <tr><td>Deep research</td><td class="num">10 to 25</td></tr>
          <tr><td>Following a handle from a result</td><td class="num">0</td></tr>
        </table>
        <p class="muted">Already have a key? <a href="/account">See your usage</a>.</p>
        """ + _privacy_note()
    else:
        body = """
        <h1>This instance issues keys by hand</h1>
        <p>GitHub sign-in is not configured here, which is the normal state for a
        self-hosted moo: it runs open on your own machine and needs no key at all.</p>
        <pre>uv run python -m app.keys create --label me</pre>
        <p>To turn on self-serve signup, set <code>MOO_GITHUB_CLIENT_ID</code>,
        <code>MOO_GITHUB_CLIENT_SECRET</code> and <code>MOO_PUBLIC_URL</code>.</p>
        """
    return _document("moo: get a key", body)


def key_issued_page(key: str, *, login: str | None, credits: int | None, base_url: str) -> str:
    safe_key = html.escape(key)
    who = f" for {html.escape(login)}" if login else ""
    allowance = f"{credits} credits a month" if credits else "no credit limit"
    body = f"""
    <h1>Your key{who}</h1>
    <p>Copy it now. moo stores only a hash, so this is the one time it can be shown.
    You are on {allowance}.</p>
    <div class="key">{safe_key}</div>
    <h2>Call it</h2>
    <pre>curl -s {html.escape(base_url)}/v1/web_search \\
  -H 'authorization: Bearer {safe_key}' \\
  -H 'content-type: application/json' \\
  -d '{{"query":"why is my postgres connection pool exhausted"}}'</pre>
    <h2>Or point an agent at it</h2>
    <pre>pip install moo-search # python
npm install moo-js     # javascript

export MOO_BASE_URL={html.escape(base_url)}
export MOO_API_KEY={safe_key}</pre>
    <p><a href="/account">Usage dashboard</a> &middot;
       <a href="/docs">API reference</a> &middot;
       <a href="{PRIVACY_URL}">Privacy</a></p>
    """
    return _document("moo: your key", body)


def dashboard_page() -> str:
    body = """
    <h1>Usage</h1>
    <p>Paste a key to see what it has spent. It is held in this tab only, never
    stored and never put in the URL.</p>
    <div class="panel">
      <form id="f"><input id="k" type="password" placeholder="moo_sk_..."
        autocomplete="off" spellcheck="false">
      <p class="muted" style="margin:.75rem 0 0">
        <button class="btn" type="submit">Show usage</button></p></form>
    </div>
    <div id="out"></div>
    """
    script = """
const CONTACT='mailto:suppprith@gmail.com?subject=moo%20higher%20limits';
const f=document.getElementById('f'),out=document.getElementById('out');
const esc=s=>String(s??'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
f.addEventListener('submit',async e=>{
  e.preventDefault();
  const key=document.getElementById('k').value.trim();
  if(!key){return}
  out.innerHTML='<p>Loading...</p>';
  let r;
  try{ r=await fetch('/account/usage',{headers:{authorization:'Bearer '+key}}); }
  catch(err){ out.innerHTML='<p class="warn">Could not reach the API.</p>'; return; }
  if(!r.ok){
    const body=await r.json().catch(()=>({}));
    out.innerHTML='<p class="warn">'+esc(body?.error?.message||'That key was rejected.')+'</p>';
    return;
  }
  const d=await r.json();
  const c=d.credits, pct=c.unmetered?0:Math.min(100,Math.round(100*c.used/(c.included||1)));
  const rows=(d.usage.endpoints||[]).map(e=>
    '<tr><td><code>'+esc(e.endpoint)+'</code></td><td class="num">'+e.requests+
    '</td><td class="num">'+e.credits+'</td></tr>').join('')
    ||'<tr><td colspan="3" class="muted">No calls yet.</td></tr>';
  out.innerHTML=
    '<div class="panel"><h2 style="margin-top:0">'+esc(d.key.label||d.key.prefix)+'</h2>'+
    (c.unmetered?'<p>Unmetered key.</p>':
      '<p>'+c.used+' of '+c.included+' credits used, '+c.remaining+
      ' left. Resets '+esc(c.period_end||'')+'.</p>'+
      '<div class="meter"><span style="width:'+pct+'%"></span></div>')+
    '</div><h2>By endpoint (30 days)</h2><table>'+
    '<tr><th>Endpoint</th><th class="num">Requests</th><th class="num">Credits</th></tr>'+
    rows+'</table>'+
    '<p class="muted">Need more than the free tier? '+
    '<a href="'+CONTACT+'">Ask for higher limits</a>.</p>';
});
"""
    return _document("moo: usage", body, script=script)
