# Yojana Saathi

A multilingual portal that helps Indian citizens find government schemes, exams, scholarships and jobs, by text or voice, in 11 languages. Every AI capability (chat, speech-to-text, text-to-speech, translation, language detection, transliteration) comes from [Sarvam AI](https://docs.sarvam.ai) through one key, `SARVAM_API_KEY`, which stays on the server.

> This is an independent guide, not an official government website. Always confirm details on the official site before applying.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagram.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env        # then put your key in .env
python -m uvicorn yojana.api:get_app --factory --port 5001
```

Open http://localhost:5001. A Railway deployment uses the `Procfile`; set `SARVAM_API_KEY` as a service variable. With no key the app runs in **demo mode**: bundled sample records, English only, no voice.

```bash
python -m pytest                          # 71 offline tests, no API calls, no credits
python -m yojana.ingest --check           # fetch each official source root (no Sarvam calls)
python -m yojana.ingest --source upsc     # crawl + extract one source (uses Sarvam)
python -m yojana.ingest --all             # crawl everything in sources.yaml
python -m yojana.ingest --reverify        # re-check records closing within 14 days
python -m tests.live_eval golden|roundtrip|hallucination|speech   # live evals (spend credits)
```

Set `ENABLE_SCHEDULER=1` to run the daily crawl and the 6-hourly deadline re-verification inside the API process.

## What is in the repo

| Path | Purpose |
|---|---|
| `sources.yaml` | Official sources: national sites and 8 states. Adding a state is config only. |
| `yojana/crawler.py` | robots.txt, per-host rate limit, cached pages with fetch timestamps |
| `yojana/extractor.py` | Sarvam chat to JSON, schema validation, one retry, expired records dropped |
| `yojana/record.schema.json` | JSON schema every record must satisfy |
| `yojana/store.py`, `search.py` | SQLite + FTS5 (BM25); deterministic filters and ranking |
| `yojana/qa.py` | detect, translate, slots, search, grounded answer, self-check |
| `yojana/cards.py` | result cards; exact facts stay untranslated, names are transliterated |
| `yojana/api.py` | FastAPI: `/api/search`, `/api/ask`, `/api/stt`, `/api/tts`, `/api/sources/status` |
| `static/` | Mobile-first, accessible UI (text size, high contrast, skip link, keyboard friendly) |
| `seed/demo_records.json` | 25 sample records for demo mode: no dates, no amounts, flagged "verify" |
| `tests/` | offline tests with a fake Sarvam client, golden set, live evaluation script |

## Honest status: what was and was not verified

**Verified against the live Sarvam API**
- Text-to-speech then speech-to-text round trip on one English sentence.
- Language detection, translation (en and hi), transliteration, grounded Q&A and the self-check pass, through the running app, for Hindi.
- **Golden set, 50 native-language questions** (Hindi, Bengali, Tamil, Telugu, Marathi; 10 each): the expected record was in the top 3 for 50 of 50 and ranked first for 49 of 50. Mean time to retrieve results was about 9 s per question (max 22 s) while other live tests ran at the same time. This measures retrieval over the 25 sample records only, so it says nothing about accuracy on real crawled data.
- Hallucination test: 3 questions about schemes that do not exist all returned "could not find".
- Translation round trip on 20 scheme names (en to hi to en): mean similarity 0.82, 15 of 20 at 0.7 or higher. Names do get mangled when translated (for example "Combined Graduate" came back as "Joint Post Graduate"), which is why the app transliterates names and never translates them.

**Verified offline only (fake Sarvam client, no network)**
- Filters (age, category, gender, state, education, income, deadline), ranking, expired items never shown as open, closing-soon.
- Extractor: schema validation, retry-then-discard, today's date in the prompt, low-confidence flag, dedup.
- Crawler: robots.txt, rate limiting, caching, official-domain allowlist, discovery-only aggregators.
- API validation, rate limiting, and that the key never reaches the browser.

**NOT verified**
- **Extraction precision against 30 hand-checked records.** There is no gold set in this repo: a person has to check 30 records against the official pages. `scripts/eval_extraction.py` computes the number once `eval/gold_30.json` exists. No figure is claimed.
- **Which official sites a polite crawler can read.** `python -m yojana.ingest --check` on 34 configured source URLs: 12 returned readable text; 8 are disallowed by their robots.txt (for example upsc.gov.in, pib.gov.in, mohfw.gov.in, labour.gov.in), which the crawler respects; 8 failed SSL or timed out from this network; 4 returned little or no text (JavaScript pages); 1 returned 404 (employmentnews.gov.in needs a corrected URL). Real data will need alternative official feeds for the blocked sites.
- **Real crawling and extraction.** Many portals (myScheme, NSP) are JavaScript apps that a plain HTTP fetch cannot read. Run `python -m yojana.ingest --check` to see which sources return text. The extractor has not been run on live official pages at scale, so the knowledge base currently holds only the bundled sample records.
- Languages other than Hindi in the live app; the microphone path in a real browser; scheduler behaviour over days.
- The sample records come from general knowledge and are marked `confidence 0.5` / "verify on the official site". Check each against its official site before showing them to users.

## Known limits

- Answers take several seconds: the draft and the fact-check are two model calls to a reasoning model. Result cards appear first.
- Deadline reminders are a downloadable `.ics` file, so no contact details are stored. There is no server-side alerting.
- Nothing about the user is stored. The profile form lives in the browser tab only.
