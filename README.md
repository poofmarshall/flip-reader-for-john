# Flip Reader

A Flipboard-style news reader you host yourself. It pulls in websites, RSS feeds, public
Flipboard magazines and topics, and Google News searches, sorts them into sections, and shows
them as flip pages on your phone. Add it to your iPhone or Android home screen and it opens
like an app.

It's one small program in one Docker container. No accounts, no subscriptions, nothing sent
anywhere except the normal requests to the news sites you follow.

## What it does

- **Home screen**: a grid of sections. *For You* at the top mixes everything you follow,
  with every 4th story a Discover pick from a source you don't follow yet.
- **Flip pages**: tap a section, swipe up to flip through pages of 1 to 3 stories.
- **Reader view**: tap a story to read it inside the app as clean text and pictures, with a
  Reader / Website switch at the top.
- **Fewer like this**: the ··· button on a story hides that topic or website.
- **Topics you might like**: suggestions on the home screen based on what you read.
- **Sources you can add** (Settings ⚙︎ → Add stories):
  - any website or RSS feed link
  - any public Flipboard magazine link
  - any Flipboard topic by name, such as "golf" or "home automation"
  - any Google News search, such as "Detroit Lions"
- **Import** a list of feeds from NewsBlur, Feedly, Inoreader or any reader that exports
  an OPML file. Its folders become sections.

## What you need

- A computer that stays on: a home server, a NAS that runs Docker, a Raspberry Pi 4 or 5,
  a mini PC, or an always-on Mac or Windows PC.
- **Docker** on that computer. On a Mac or Windows PC, install Docker Desktop. On Linux,
  install Docker Engine with the Compose plugin.
- About 300 MB of disk and 150 MB of memory.

## Set it up (about 15 minutes)

1. Unzip this folder onto that computer.
2. In the folder, make a copy of `.env.example` named `.env`. Open `.env` and replace
   `change-me-to-a-long-random-code` with your own access code: letters, numbers and dashes
   only, and long, like `blue-lamp-47-orchard-river`.
3. Open a terminal in the folder and run:

   ```bash
   docker compose up -d --build
   ```

   The first run takes a few minutes while it builds. After that it starts in seconds and
   restarts on its own if the computer reboots.
4. Check it's running:

   ```bash
   docker compose logs -f
   ```

   Within a minute you should see `Serving on http://0.0.0.0:8090` and then
   `refreshed 4 feeds`. Press Ctrl+C to stop watching; the app keeps running.
5. On your phone, on the same Wi-Fi, open `http://<the computer's local address>:8090`,
   for example `http://192.168.1.50:8090`. Enter your access code.
6. Add it to your home screen. iPhone: Share → **Add to Home Screen**. Android: the ⋮ menu
   → **Add to Home screen**.
7. Tap ⚙︎ and start adding sources, or import an OPML file. New feeds fill in within a
   couple of minutes.

It starts with a small Discover section (Flipboard News and Technology, plus Google News
searches for "Detroit Tigers" and "Michigan"). Delete or change those in Settings.

## Reaching it away from home (optional)

Out of the box it only works on your home network. To use it anywhere, pick one:

- **Tailscale** (easiest, private): install Tailscale on the server and your phone. Then open
  `http://<the server's Tailscale name>:8090` from anywhere.
- **Cloudflare Tunnel** (a real web address, no router changes): point a tunnel hostname at
  `http://localhost:8090` (or at `http://flip-reader:8090` if cloudflared runs in Docker on
  the same network).
- **A reverse proxy** you already run (Caddy, Nginx Proxy Manager, Traefik): proxy a
  hostname to port 8090 with HTTPS.

Don't forward port 8090 straight through your router. The access code is a simple lock,
fine behind one of the options above but not meant to face the open internet alone.

## Everyday care

- **Your data** lives in the `data` folder: feeds, sections, settings and recent stories.
  Back it up if you like. Losing it only means re-adding your feeds.
- **Stop / start**: `docker compose down` and `docker compose up -d` in the folder.
- **Update to newer code** someone sends you: replace everything except `data` and `.env`,
  then run `docker compose up -d --build`.
- **Change the access code**: edit `.env`, then `docker compose up -d`. Each device asks
  for the new code once.

## Troubleshooting

- **Page won't load on the phone**: check the phone is on the same Wi-Fi, the address uses
  the computer's local IP and `:8090`, and `docker compose ps` shows `flip-reader` as
  `Up (healthy)`. On Windows, allow Docker Desktop through the firewall if asked.
- **"port is already allocated"**: something else uses 8090. In `docker-compose.yml`
  change `"8090:8090"` to, say, `"8095:8090"` and use `:8095` on the phone.
- **A feed has a red dot in Settings**: that site's feed address no longer works. Remove
  it, or try adding the site's homepage and let the app find the feed.
- **No pictures on some stories**: Google News stories never include pictures. Others
  fill in over a few refreshes.

## Known limits

- Flipboard doesn't let outside apps read your personal "For You" feed, so only *public*
  magazines and topics can be added.
- Some paywalled papers block automated readers. A Google News search for the paper's
  name is a good substitute.
- Reader view can't unwrap Google News links or videos; those open the website.

## For an AI helper (Gemini, ChatGPT, Claude)

If you're helping someone run this, here's the shape of it:

- Python 3.12, Flask served by waitress on port 8090, SQLite at `/data/reader.db`
  (`DATA_DIR` env var). Single container, built from the `Dockerfile` here.
- Config is two env vars: `APP_TOKEN` (shared access code, checked against an `fr_token`
  cookie; empty means no lock) and `REFRESH_MINUTES` (default 20).
- Health check: `GET /api/health` returns JSON with `ok`, `feeds` and `last_refresh`.
- A background thread fetches all feeds every `REFRESH_MINUTES`; logs go to stdout
  (`docker compose logs`).
- Code: `app/server.py` (routes and story mixing), `app/fetcher.py` (feeds and images),
  `app/readable.py` (reader view), `app/db.py` (SQLite), `app/static/` (the phone app).
- Tests: `pip install -r requirements.txt pytest` then `python -m pytest app/`.
- Most setup problems are Docker networking: the port mapping, the host firewall, or the
  phone using the wrong IP address. The app itself needs only outbound HTTPS.
