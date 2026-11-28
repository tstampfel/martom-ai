# Analytics

How we count visits, clicks and conversations that end in an order, without cookies,
without storing IP addresses or anything that names a person, with the data in our own AWS
account in the EU (#156). Three parts:

| Part | Issue | Status |
| --- | --- | --- |
| Umami, self-hosted, and its tracking script | #163 | this document, below |
| Click events: every clickable element tagged with `data-umami-event` | #164 | after #147 and #152 merge; the event list will live here |
| Funnel events in our Postgres (`advisor_events`), retention and report | #165 | after #147 merges; the rows will be described here |

## Umami

[Umami](https://umami.is) is open-source web analytics. It sets no cookies and stores no
IP address. It does **use** the IP address of each request, briefly: a visitor's session is
a hash of the IP address, the browser's user agent and a salt that changes every month, and
the country is looked up from the IP. Under the GDPR that brief use is still processing of
personal data (an IP address is personal data), so the stored statistics are pseudonymous,
not anonymous, and the privacy text must not say "no personal data" (see "Privacy text"
below). It shows page views,
visitors, referrers, countries, devices and (from #164) custom events. We run the official
image ourselves; nothing goes to Umami's cloud.

### How it runs (Terraform: `infra/umami.tf`)

```text
browser on martom-savjetnik.pages.dev
  │ GET  https://<umami id>.cloudfront.net/script.js   (cached at the edge)
  │ POST https://<umami id>.cloudfront.net/api/send    (never cached; Umami answers CORS itself)
  ▼
CloudFront distribution "umami" (its own free *.cloudfront.net address)
  │ the API's VPC origin, plus a secret X-Martom-Origin header
  ▼
internal ALB: listener rule "X-Martom-Origin = <secret>" → Umami target group (:3000)
              every other request → the API (default action, unchanged)
  ▼
ECS service on FARGATE_SPOT: official image ghcr.io/umami-software/umami (pinned), 0.25 vCPU, 1 GB, 1 task
  ▼
the same RDS instance: database `umami`, role `umami` (no CONNECT on `martom`, at most 5 connections),
  created by `martom umami-db-setup` (src/martom/umami_db.py), which sends Postgres only a SCRAM hash of the password
```

- **No new load balancer, no new database instance**: one ALB listener rule and one database on the existing instance.
- **FARGATE_SPOT**: AWS may take the capacity back; ECS starts a new task by itself, and visits in those minutes aren't counted. Nothing else depends on Umami.
- **Restart on failure**: ECS replaces a task that exits or fails its health check (`/api/heartbeat`); a deploy that keeps failing rolls back.
- **Secrets**: `martom/production/umami` in Secrets Manager holds the database URL, the app secret, the admin password and the origin header secret, set by hand (`infra/README.md`, "Umami"). None is in the repo, `.env.example`, Terraform variables' defaults or a task override.
- **Cost**: about $7.50 a month: ~$3.70 Fargate Spot plus ~$3.65 for the task's public IPv4 address (no NAT gateway); the distribution is in CloudFront's free tier. See the cost table in `infra/README.md`.
- **Logs**: the Umami log group keeps 30 days, like every log group (the Privatnost page promises 30 days for technical logs and IP addresses; `log_retention_days` can't exceed it). No CloudFront or ALB access logs are turned on.

### Why a second CloudFront distribution and not `/umami/*` (checked 28 Nov 2026)

The first plan was a `/umami/*` path on the API's CloudFront distribution. What Umami
supports (Umami 3.5, [environment variables](https://docs.umami.is/docs/environment-variables),
[Dockerfile](https://github.com/umami-software/umami/blob/master/Dockerfile)):

- `BASE_PATH` (serving Umami under a sub-path) is a **build-time** variable: "Build time variables are only recognized during the build process. This also includes building custom Docker images." The Dockerfile bakes it in during `pnpm build:docker`. There is no prebuilt image or runtime option for it, so a sub-path means building and maintaining our own image.
- `TRACKER_SCRIPT_NAME` (default `script.js`) and `COLLECT_API_ENDPOINT` (default `/api/send`) are runtime variables; we keep both defaults.
- The collect endpoint answers CORS for every origin (`Access-Control-Allow-Origin: *`, set in Umami's `next.config.ts`), so the script on pages.dev can post to another hostname. The API's own CORS rules are unchanged.

Tomislav's decision (28 Nov 2026): **a second CloudFront distribution**, Umami at the root
of its own `*.cloudfront.net` hostname, official image, no build. It reuses the API's VPC
origin and ALB.

### How the admin UI is protected

The admin UI is at the root of `umami_url`, on the public internet. It is protected by:

1. **A strong admin password.** Umami creates the login **admin / umami** on its first start and has no setting to choose another. Changing it to `ADMIN_PASSWORD` from the Umami secret is the first thing done after the first deploy, before the tracking script goes live (`infra/README.md`, "Umami", step 4; on the launch checklist, #33).
2. **One account only.** Umami has no public sign-up. There is only `admin`; nobody creates other users or teams (step 5).
3. **Reachable only through its own distribution.** The ALB sends a request to Umami only when it carries the secret `X-Martom-Origin` value, which only the Umami distribution adds. The API's distribution forwards every viewer header, so the value must be secret: with it, nobody can reach Umami through the API's address or by guessing a header. The ALB itself is internal (no public address).
4. **HTTPS only** between browser and CloudFront (HTTP redirects to HTTPS), CloudFront's security headers on every answer, and no telemetry or update checks from the container (`DISABLE_TELEMETRY`, `DISABLE_UPDATES`).

There is no IP allowlist or second factor in front of the admin UI; a CloudFront Function
or WAF rule could add one later if Tomislav wants it.

### The tracking script (`web/umami.ts`, `web/index.html`)

`web/index.html` has a `<!-- umami -->` marker in `<head>`. At build time the Vite plugin
in `web/umami.ts` replaces it with the tracker, or removes it when tracking is off. The app
has no router, so the one script covers every screen of `/` (start, chat, results, cart,
done) and `/privatnost`, in both the web and mobile layouts.

```html
<script defer src="https://<umami id>.cloudfront.net/script.js" data-website-id="<uuid>"
  data-domains="martom-savjetnik.pages.dev" data-exclude-search="true" data-exclude-hash="true"></script>
```

| Setting (public, `VITE_*`) | Value | Where |
| --- | --- | --- |
| `VITE_UMAMI_ENABLED` | `true` turns the script on; anything else (or unset) leaves it out | Cloudflare Pages **production** environment only |
| `VITE_UMAMI_HOST` | `umami_url` from Terraform, e.g. `https://d1234abcd.cloudfront.net` (https, no path) | same |
| `VITE_UMAMI_WEBSITE_ID` | the website's id (a UUID) from the Umami admin UI | same |

- **Off for local and staging**: unset locally, in tests and in Cloudflare Pages preview deploys, so no script is added at all. With the flag on, a missing or malformed host or website id fails the build instead of silently not tracking.
- **`data-domains`**: even with the flag on, only visits on `martom-savjetnik.pages.dev` count; a preview (`<branch>.martom-savjetnik.pages.dev`) or a local copy sends nothing.
- **Nothing that identifies a visitor** (Tomislav, 28 Nov 2026): no `data-distinct-id`, `data-tag` or `data-salt`; credentials are never sent (`fetch-credentials` stays `omit`). `data-exclude-search` and `data-exclude-hash` keep the query string and `#hash` out of the URLs Umami stores. A unit test (`web/src/umami.test.ts`) checks the tag and the flag.
- **Never load Umami's session-replay recorder** (`recorder.js`, which Umami 3.x also serves) and never turn on replay: it would record typed chat text, names and addresses. Heatmaps and session replay are out of scope (#156). Only `script.js` is ever added.
- **Never send** typed chat text, names, phone numbers, addresses or order contents as event data (#156; this applies to #164's events too).

### Excluding our own visits

In each browser you use for Martom (Tomislav, Toni, testers), open the production site,
then the browser's developer console, and run once:

```js
localStorage.setItem('umami.disabled', '1')
```

The tracker checks `umami.disabled` before every send (Umami 3.5, `src/tracker/index.ts`)
and stays silent in that browser. `localStorage.removeItem('umami.disabled')` turns counting
back on. It is per browser and per profile, so repeat it after clearing site data.

### Privacy text for the Privatnost page (#152)

#156 asks for one sentence in the Privatnost rewrite; #152 owns that page, so the text is
only proposed here. #156's wording, "without cookies and without personal data", promises
more than Umami does: it uses each visitor's IP address briefly (the visitor hash and the
country, above), and that counts as processing personal data under the GDPR. Proposed
instead, for Tomislav's privacy review before launch: *visit statistics are collected
without cookies; your IP address is used only briefly to count unique visits and the
country, and is never stored. The statistics are kept on our servers in the EU.*
If the page names a legal basis, it is legitimate interest (Art. 6(1)(f)): that is for the
privacy review to confirm, not decided here.

### Upgrading Umami

Change `umami_image` in `infra/variables.tf` to the new tag after reading Umami's release
notes, then apply. The container runs Umami's database migrations by itself on start.
