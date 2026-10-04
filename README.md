# Field Notes API

Read counts, votes and anonymous comments for the [Field Notes](../blog) blog.
Flask, deployed to Vercel as a serverless function.

The blog is a static site on GitHub Pages and cannot host a backend, so the
mutable state lives here. **This is why counts survive a rebuild:** the blog's
build wipes `dist/` on every run (`fs.rmSync(DIST_DIR, …)`), so anything written
into the generated pages would reset constantly. Votes and counts are read from
this database at page load instead, and the build never touches them.

## Deploy to Vercel

```bash
uv sync
vercel link
vercel env add VOTE_PEPPER production       # secrets.token_hex(32)
vercel env add ALLOWED_ORIGINS production   # https://your-blog-domain
vercel deploy --prod
```

**The database needs no configuration.** Provision Postgres through Vercel
(`Storage → Create Database`) or Neon, link it to the project, and the URL is
detected automatically — see below. You only have to set `DATABASE_URL`
yourself if you host the database somewhere unusual.

SQLite is only ever used locally: the function filesystem is ephemeral, so a
SQLite-backed deploy silently loses every count.

Then point the blog at it:

```json
// blog/site.config.json
"apiUrl": "https://your-api.vercel.app"
```

While `apiUrl` is empty the blog emits no widget markup and no script tag at
all, so it keeps working with no backend whatsoever.

> **Local preview caveat.** `community.js` skips itself when the page hostname
> is `localhost`, so the preview iframe cannot inflate your counts. Don't add
> `http://localhost:*` to `ALLOWED_ORIGINS` in production.

## Database autodetection

Which variable Vercel injects depends on how the database was provisioned, so
all the known names are accepted, in this order:

| Priority | Variable | Typical source |
| --- | --- | --- |
| 1 | `DATABASE_URL` | Neon, Supabase, Vercel Postgres (current) |
| 2 | `POSTGRES_URL` | older Vercel Postgres projects |
| 3 | `POSTGRES_URL_PRISMA` | Vercel Postgres with Prisma |
| 4 | `POSTGRES_PRISMA_URL`, `PG_URL` | assorted providers |
| 5 | `PGHOST` `PGPORT` `PGDATABASE` `PGUSER` `PGPASSWORD` | assembled into a URL |

Two provider quirks are corrected automatically:

- `postgres://` is rewritten to `postgresql://`, which SQLAlchemy 2 rejects
  outright but some providers still emit.
- `sslmode=require` is appended when missing, which managed Postgres requires.

If a password contains `@`, `/` or spaces it is percent-encoded when the URL is
assembled from `PG*` parts.

Check what was picked up — this is the fastest way to diagnose a bad deploy:

```bash
curl -s https://your-api.vercel.app/api/health
```

```json
{
  "ok": true,
  "database": { "sqlite": false, "source": "DATABASE_URL", "detected": ["DATABASE_URL"] },
  "problems": []
}
```

`problems` is the thing to read. If the database resolved to SQLite on Vercel it
says so explicitly, rather than leaving you to find out when counts vanish.

Note: this covers SQL databases only. Vercel KV / Upstash Redis expose
`KV_REST_API_URL` and friends, which are deliberately *not* picked up — they are
a key-value store and not a drop-in for these models.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/stats?post=<slug>` | `{ up, down, views, myVote }` |
| `POST` | `/api/vote` | `{ post, value: 1 \| -1 \| 0 }` — `0` clears |
| `POST` | `/api/view` | `{ post }` — once per IP per post per day |
| `GET` | `/api/comments?post=<slug>` | published comments, oldest first |
| `POST` | `/api/comments` | `{ post, body, parentId?, website? }` |
| `GET` | `/api/health` | which database URL was detected, plus config warnings |

`website` is a honeypot: a bot that fills it in gets a fake success.

## How "no login" is enforced

There is no account, so a voter is identified **only** by their IP address:

1. The address is read from `X-Forwarded-For` (first entry) or `CF-Connecting-IP`
   — platform-set headers, never a client-supplied value.
2. It is combined with `VOTE_PEPPER` and SHA-256 hashed. **The raw address is
   never stored.**
3. `votes` is keyed `(post, voter_hash)` and `views` is keyed
   `(post, day, voter_hash)`. Those primary keys *are* the one-vote-per-person
   and one-view-per-day rules — no read-modify-write logic, no race.

Rotating `VOTE_PEPPER` anonymises every stored row retroactively, at the cost of
letting everyone vote again once.

### What this does not buy you

IP is **rate limiting, not identity**. A VPN, an incognito window, or rebooting
the router produces another vote. It stops casual spam; it does not stop anyone
motivated. Add a signed cookie as a second factor if that matters.

## Abuse controls

Comments are the attack surface, so there are several independent layers:

| Control | Default | Env var |
| --- | --- | --- |
| Honeypot field | always on | — |
| Comments per IP per window | 5 / 24h | `COMMENT_RATE_LIMIT`, `COMMENT_RATE_WINDOW_HOURS` |
| Minimum gap between comments | 20s | `COMMENT_MIN_INTERVAL_SECONDS` |
| Max body length | 5000 | `COMMENT_MAX_LENGTH` |
| Max links per comment | 3 | `COMMENT_MAX_LINKS` |
| Hold for review | off | `COMMENTS_REQUIRE_MODERATION` |
| Manual blocklist | — | `flask --app api block <hash>` |

Comment bodies are stored and returned as **plain text**. `community.js` renders
them with `textContent`, never `innerHTML` — keep it that way if you rework the
front-end, or add server-side sanitising.

## Moderation

```bash
flask --app api init-db                       # create tables
flask --app api pending                       # list held comments
flask --app api approve <id>                  # publish one
flask --app api block <voter-hash> --reason "spam"
flask --app api unblock <voter-hash>
```

Voter hashes come from the `X-Forwarded-For` of the request you want to act on;
`/api/health` and the logs don't expose them, so read them off the row or
recompute with `fieldnotes.identity.identify`.

## Tests

```
uv run pytest
```

70 tests covering one-vote-per-IP, re-voting, view dedup, count persistence,
thread flattening, honeypot, rate limits, link/length limits, CORS allowlisting,
database URL detection across provider shapes, and the guarantee that no raw IP
reaches the database.

## Layout

```
api.py             Vercel WSGI entrypoint (must be named `app`)
fieldnotes/
  app.py           routes, CORS, moderation CLI
  models.py        schema; the uniqueness constraints do the real work
  identity.py      IP extraction + peppered hashing
  validation.py    slug/body checks and spam heuristics
  config.py        environment configuration
tests/
```