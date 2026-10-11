# Nibble

A personal finance assistant that connects your grocery bill to the world.

Tell it what you need in one box. Nibble prices your basket across stores,
traces any price or headline back to the real event behind it on a live globe,
shows which companies and sectors that event touches, and teaches the finance
behind the news with flashcards. News that affects your basket pops up as it
arrives.

Built on **Nimble** (live web data and research agents), **Gemini** (judgment,
vision and teaching) and **Sentry** (agent tracing, logs and profiling).

## Run it

```bash
cp .env.example .env      # add NIMBLE_API_KEY, GEMINI_API_KEY, SENTRY_DSN
./run.sh                  # http://localhost:8000
```

With no keys it runs on clearly labelled sample data, so the page always
works. Each key switches its part to live independently.

### Three models, tried in order

Every model call tries a **hosted model** (if `NIBBLE_LLM_API_KEY` is set),
then a **local model** (if `NIBBLE_LOCAL_MODEL` is set), then **Gemini**.

The hosted model is any OpenAI-compatible API. Groq is the default because its
free tier needs no card and answers in about a second:

```bash
echo 'NIBBLE_LLM_API_KEY=gsk_...' >> .env     # from https://console.groq.com/keys
```

Receipt photos skip the hosted model (the default one is text-only) and go to
the local model or Gemini. Not yet run against the real Groq API; preflight
makes one real call and prints the result.

### A local model, Gemini as backup

Gemini's free tier returns "busy" often. Nibble can use a small model on your
own machine first and fall back to Gemini only when that fails:

```bash
ollama pull gemma3:4b                         # about 3 GB, runs on a 6 GB GPU
echo 'NIBBLE_LOCAL_MODEL=gemma3:4b' >> .env   # then restart ./run.sh
```

"How it works" (top right of the page) shows which model is answering. If the
local model cannot read receipt photos, set `NIBBLE_LOCAL_VISION=0` and photos
go to Gemini.

Before a demo:

```bash
.venv/bin/python scripts/preflight.py                       # one real call per service
.venv/bin/python scripts/prewarm.py 07302 "ground coffee 12 oz"   # cache the demo path
.venv/bin/python -m pytest -q                               # replays captured live responses
```

## The page

- **Ask box and four buttons**: Price my groceries, Today's news, Learn
  investing, In season now. The start panel explains each in one line.
- **The globe** has five kinds of dot, each with a switch in the legend: where
  food comes from (your own list is labelled and joined to your town), shipping
  routes and ports, money markets, today's news, and live quakes, storms and
  fires. Click any dot for a plain description and a question to ask about it.
- **Learn investing** turns one story into a lesson: what happened, why it
  matters, how it spreads (numbered on the globe), who feels it, what named
  forecasters expect, what could prove them wrong, words to know, and one
  check question. Forecasts are always attributed; Nibble makes none itself
  and never says what to buy or sell.
- **Deep dive** ("teach me why semiconductor prices are rising", or the box at
  the top of Learn investing) builds a guide from zero: what it is, how we got
  here, how it works (stages numbered on the globe), what is happening now,
  what moves the price, the main companies, numbers to know, what to
  understand before investing, what named sources expect, common mistakes and
  three check questions. Steps 1 to 4 come from a Nimble Web Search Agent with
  citations. The investing sections are written by the model from three live
  Nimble Answer lookups, which are shown with their sources. Two of those
  lookups use `include_domains` and `search_depth="standard"` to read only
  wire services, business press, regulators and statistics agencies, the
  pattern from Nimble's financial research cookbook.
- **How it works** holds the service status and every agent step.

## The agents

| Agent | What it does | Nimble | Gemini |
|---|---|---|---|
| Basket Scout | List and ZIP to cheapest basket, plus stores near you | Extract Templates `walmart_serp`, `heb_serp`, `google_maps_search` | Picks the same product in each store, honoring the profile |
| Receipt Reader | Photo to lines, then where each was cheaper | via Basket Scout | Reads the receipt image; learns buying habits |
| Price Detective | One item to cited causes on the globe, plus who else is exposed | Web Search Agent `nibble-price-detective` with an output schema and trust report | Plain-language summary and a shopper tip |
| News Desk | Today's headlines pinned to the map, yours first | Search, `focus="news"` | Places each headline and states the household angle |
| News Tracer | Click a headline: is it corroborated, and how does it reach you? | Web Search Agent `nibble-news-tracer` | none |
| Signal Scout | Alternative-data readings and a quote for a company | Web Search Agent `nibble-signal-scout`; Extract Template `morningstar_stock_quote_community_2026_04_24` | none |
| Ask | A follow-up question, answered with numbered sources | Search with `include_answer=True` (Nimble Answer) | none |
| Market Lens | A topic or headline to the companies, ETFs, commodities and sectors it touches, plus what could change | Web Search Agent `nibble-market-lens` | none |
| Season Guide | What is in season in your state this month, one tap to add it to the list | Nimble Answer | Turns the answer into list items |
| Tutor | Finance flashcards with a quiz, built from today's headlines | Search `focus="news"`, Nimble Answer for a chosen topic | Writes the cards from those headlines |
| Router | One text box: "I need eggs and coffee", "why is beef expensive", "teach me inflation" | none | Picks the action (plain rules if Gemini is off) |

**Pop-up news.** The wire is refreshed every minute. New headlines slide in at
the top right, say when they touch your basket, and dismiss themselves after
30 seconds. Large earthquakes and new storms from the live feeds pop up too.

**On investing.** The Market Lens explains exposure: what is happening, who is
tied to it, what could change, each with a source, plus a live quote and
alternative-data signals on request. It is built to never say buy, sell or
hold, and it is not investment advice.

The globe's live layer (earthquakes, wildfires, storms) comes from keyless
public feeds: USGS and NASA EONET.

### Checking a headline

Click any headline on the wire. The News Tracer agent returns two things:

- **Can you trust it?** A verdict (corroborated, single source, disputed), the
  original data source behind the story, and every site the agent read. The
  counts ("4 other sites read, 1 official") are computed in code from the
  agent's trust report, with the story's own site excluded.
- **How it reaches you.** Three to five links from the trigger to a household's
  costs. Each link has a place, a citation and a confidence grade, and the
  globe draws them in order as numbered stops ending at your ZIP.

### The shopper profile and Nimble's memory

Nimble's agent memory is research memory: a named agent remembers which
sources and retrieval paths worked and reuses them on later runs. It is not a
store for facts about a person. Nibble uses it, and adds the personal layer
itself:

- **Persistent named agents.** Every run goes to the same three agents by name,
  so their memory accumulates across runs and users of your key.
- **Follow-up runs.** "Dig deeper" passes `previous_interaction_id`, so the
  medium-effort run continues from the quick one instead of starting over.
- **Profile in the briefing.** The shopper's profile is appended to the agent's
  `skill` on each run.
- **Patterns, counted not guessed.** `.nibble/persona.json` keeps past baskets.
  From them Nibble counts your staples, which store wins most often and what
  splitting trips has saved, and Gemini adds habits it reads off receipts. The
  profile then decides which listings count as a match and which headlines are
  flagged "In your basket". "Forget what you learned" deletes the file.

### Nimble calls, exactly

- `extract.templates.get(name)`: reads each store template's input schema, so
  parameters are built from the template's own definition (`keyword` +
  `zipcode` for Walmart, `keyword` + `zipcode_or_storeid` for H-E-B).
- `extract.templates.async_(template, params)` then
  `tasks.get` / `tasks.results`: retail pages take 10 to 20 s to render, so
  runs are submitted as tasks and polled.
- `search(query, focus="news", search_depth="lite", time_range="day")` for the
  wire, and `search(query, include_answer=True)` for follow-up questions.
- `agents.run(input, agent_name, output_schema, skill, sources, effort,
  use_case="research")` then `agents.runs.get` / `agents.runs.result`.
  Reusing `agent_name` reuses the same agent, so its memory carries over;
  `previous_interaction_id` continues an earlier run.
  `output.trust.claims[].path` ties each citation to the field it backs.

### How prices are compared

Stores report their own unit price (`13.9 ¢/count`, `$0.15/each`). Nibble
divides shelf price by that to get the pack size, which works even when the
name omits it (Kroger lists a 12-count and an 18-count under one name). Gemini
chooses the comparable product; all arithmetic is plain Python.

## Sentry

`app/obs.py` wraps every pipeline in a `gen_ai.invoke_agent` span and every
Nimble call in a `gen_ai.execute_tool` span with its arguments and a result
summary. Gemini calls are traced by Sentry's Google Gen AI integration. Open
**Insights > AI Agents** in Sentry to see each run as one trace.

"Break a tool call" in the activity panel fails one call on purpose and shows
the Sentry event id.

## What was checked, and what was not

Checked against the live Nimble API on 2026-10-11:

- `walmart_serp` (18 to 24 s) and `heb_serp` (16 s) returned parsed prices on
  every try. These two are the default stores.
- `kroger_serp` returned prices once and an empty page twice. `target_serp`
  (125 s) and `amazon_serp` (3 s) returned no parsed products. All three are
  off by default; an empty result is retried once.
- Walmart is sent your ZIP. It priced a nearby store on one run (Secaucus, NJ
  for ZIP 10001) and distant stores on two others, so each price shows the
  store it came from. The stricter `localization=True` run did not finish
  within 75 s and is off by default (`NIBBLE_LOCALIZE=1` turns it on).
- Gemini answered on one preflight and returned 503 (busy) on the next. Calls
  now retry once, then try the fallback models, then use plain rules.
- A low-effort Price Detective run for coffee finished in 35 s with the schema
  accepted and per-field citations.
- News search returned `additional_data.source` and `publish_date`.
- A low-effort News Tracer run on a real headline finished in 17 s with a
  verdict, the original data source and a five-link chain.
- `google_maps_search` returned 20 stores for a ZIP in about 1 s.
- Nimble Answer returned an answer with numbered citations.

- A low-effort Investing Coach run on a beef-price story finished in 67 s
  with the lesson schema accepted. It wrote its places as sentences and left
  out the finance terms, so a model call now adds map places, three terms and
  a check question.

- Deep dive, 2026-10-11, topic "why semiconductor prices are going up", low
  effort. One agent run with the whole guide schema failed once, then finished
  in 90 s with required fields missing or empty. A run with only the basics
  half finished in 57 s with every field filled. A run with only the investing
  half finished in 27 s with filler text in two fields. So the agent is asked
  for the basics only, a failed run is retried once, filler strings are
  dropped, and the investing half is built from Nimble Answer lookups. A
  domain-limited, standard-depth Answer for the forecast question returned
  named forecasts (Counterpoint, TrendForce) with Reuters and CNBC sources. That
  combined path has been tested against the captured basics run and a
  stand-in model, not yet end to end with live keys.

The local-model path was tested against a stand-in for Ollama, not a real
Ollama server. The lesson screen and the new globe layers were checked in a
browser on sample data only.

The Market Lens, Season Guide and Tutor reuse call shapes that were checked
live, but were not themselves run live.

Not yet run with real keys: the Gemini calls, Sentry delivery, the Morningstar
quote template, Signal Scout, and follow-up runs with
`previous_interaction_id` (if Nimble rejects one, the app retries without it).
`scripts/preflight.py` covers the first two. Sentry is initialised with
tracing, logs and profiling, matching the options in Sentry's setup page.

## Limits

- Agents do not always name a place a map can find ("global coffee belt").
  Those links appear in the panel but not on the globe.
- Walmart does not always price your nearest store, and H-E-B is a Texas
  chain priced at its default store. The comparison is real shelf prices
  across chains, not a guaranteed local price.
- Nimble gives today's price, not history. Price changes over time come from
  what the research agent's sources report.
- The exposure panel states factual links with sources. It does not recommend
  buying or selling anything and is not investment advice.

## Layout

```
app/main.py          routes and background jobs
app/pipelines.py     the agents
app/nimble_tools.py  every Nimble call
app/gemini.py        every model call: local model first, then Gemini
app/atlas.py         fixed globe layers: food origins, shipping routes, markets
app/obs.py           Sentry spans and the activity log
app/world.py         public feeds, ZIP and place lookup
app/persona.py       stated preferences and learned habits (.nibble/persona.json)
app/demo.py          sample data
static/              the page; globe.gl is vendored so it loads offline
tests/               replays of captured live responses
```
