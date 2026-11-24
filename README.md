# Leeward (weekend version)

A personal feed filter. Every two hours a GitHub Action fetches your feeds, has Claude label each new
item (tone, significance, topics, flags), applies your dial and rules, and publishes clean RSS feeds
to GitHub Pages. Your phone's RSS reader (FeedFlow) subscribes to those feeds instead of the raw ones.

```
feeds.yaml ─► fetch ─► dedupe ─► label new items (Claude Haiku) ─► dial + rules ─► public/
                                     │ cached, so each item is labeled once        ├─ feeds/digest-0..3.xml
                                                                                    ├─ feeds/<section>.xml
                                                                                    ├─ subscriptions.opml
                                                                                    ├─ hidden.html (spam folder)
                                                                                    └─ health.html (source report + cost)
```

## How the dial works

Every item gets a **tone** (uplifting, neutral, negative) and a **significance** score from 0 to 5,
based on real-world impact (scope, permanence, concreteness), not on how upsetting it is.
Uplifting and neutral items show at every level. The dial only decides which negative items get through:

| Level | Name | Negative items shown | Priority topics |
|---|---|---|---|
| 0 | Not today | none, except significance 5 (historic or emergency) | same |
| 1 | Essentials | significance 4 and up | 3 and up |
| 2 | Informed | 3 and up | 2 and up |
| 3 | Everything | all | all |

Each run builds all four digests, so "choosing today's dial" is just opening the matching feed.
Your priority topics (config.yaml, `priorities`) get the lower bar. Sports is set to `pass_all`:
every Rams, Dodgers, Jazz, and Mammoth story shows regardless of tone, minus betting and sponsored posts.

Some flags hide items everywhere they're blocked (outrage bait, callouts and drama, graphic violence,
celebrity gossip, partisan opinion, sponsored). Clickbait isn't hidden; its headline is rewritten
in plain words instead, with the original kept in the item body.

## Setup (about 15 minutes)

1. **Create a GitHub repository** named `leeward` and upload everything in this folder, including the
   hidden `.github` folder. With git:
   ```bash
   cd leeward
   git init && git add . && git commit -m "Leeward weekend version"
   git branch -M main
   git remote add origin https://github.com/YOUR-USERNAME/leeward.git
   git push -u origin main
   ```
   The repository needs to be **public** for free GitHub Pages. That means your feed list and config
   are visible to anyone who finds the repo. Nothing secret is in them; your API key lives in a secret.
   If you'd rather keep it private, GitHub Pro allows Pages on private repos.

2. **Add your Anthropic API key.** In the repo: Settings > Secrets and variables > Actions >
   New repository secret. Name: `ANTHROPIC_API_KEY`. Value: your key.

3. **Turn on Pages.** Settings > Pages > Build and deployment > Source: **GitHub Actions**.

4. **Run it once.** Actions tab > "Build feeds" > Run workflow. The first run labels everything
   currently in your feeds (a few hundred items, a few cents). After that it runs every two hours.

5. **Subscribe.** Open `https://YOUR-USERNAME.github.io/leeward/`, download `subscriptions.opml`,
   and import it in FeedFlow. Each dial level arrives in its own folder, plus a "Leeward sections"
   folder; delete what you don't want. Pick today's level by opening its folder. Avoid the all-feeds
   timeline: the levels overlap, so the same story can appear once per level you subscribe to.
   Every feed uses its own item IDs on purpose; FeedFlow keeps only one copy of an article per ID,
   so shared IDs would make a story show up in just one of your folders.

6. **Check the health page** after a day. It flags feeds that are broken, quiet, mostly hidden, or
   just duplicating another feed. Some sites refuse automated requests from GitHub's servers; those
   show as "Blocked". Leeward doesn't try to get around that. Remove the feed or find another source.

## Testing on your own machine first (optional)

```bash
pip install -r requirements.txt
python -m leeward check-feeds                      # which feeds work, no API calls
python -m leeward build --fake-tagger              # full run with keyword labels, no API, no cost
export ANTHROPIC_API_KEY=sk-ant-...
python -m leeward try "Senator blasts rival in fiery hearing" --category news
python -m leeward build --limit 50                 # real labels for the 50 newest items
```
Output lands in `public/`; open `public/index.html` in a browser.

The `try` command is the fastest way to see how a headline gets labeled and which dial levels would
show it. Use it to sanity-check the significance scoring before trusting it.

## Changing things

Edit `feeds.yaml` or `config.yaml` right on GitHub (pencil icon) and commit. The next run uses the changes.

- **Add a feed:** `url:` for a normal feed, or `google_news: '"Some Topic" when:1d'` for a Google News search.
- **Too much of one source:** lower its `max_items`, or set `enabled: false`.
- **Sports outnumbering the good stuff in the digest:** lower `categories.sports.max_items`.
- **Something slips through:** add a regex to `keywords.block`. Something you always want: `keywords.allow`.
- **Level 0 should be a total shield:** set `dial.emergency_floor: 6`.
- **Change what counts as essential:** edit `priorities` (pick from the `topics` list).

Labels are cached, so config changes take effect on the next run without relabeling anything.

## Cost

Claude Haiku 5.5 is $0.10 per million input tokens and $0.50 per million output tokens. At roughly
500 new items a day this comes to well under a dollar a month. The health page shows the running
total for the current month. Two safety valves:

- `model.monthly_item_cap` in config.yaml stops labeling past a set number of items per month.
- Set a monthly spend limit in the Anthropic Console as a hard ceiling.

If every labeling call fails (bad key, empty balance), the Action run turns red, but the feeds still
publish: sports keeps showing, and other sections hold unlabeled items back until labeling works again.

## Files

| Path | What it is |
|---|---|
| `feeds.yaml` | your feed list |
| `config.yaml` | dial, priorities, categories, flags, model |
| `leeward/` | the code (fetch, items, tagger, rules, build, render, pages, state) |
| `tests/` | `python -m pytest` runs the test suite with sample feeds and a fake model |
| `.github/workflows/build.yml` | the every-two-hours job |

State (labels, seen items, source stats) is stored in the GitHub Actions cache between runs.
If it's ever evicted, the next run just relabels whatever is current.
