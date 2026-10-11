# Architecture

```mermaid
flowchart LR
  subgraph A[A. Ingestion - scheduled, backend only]
    S[sources.yaml<br/>national + 8 states] --> C[crawler.py<br/>robots.txt, rate limit, page cache]
    D[Aggregator pages<br/>discovery only] -. official links only .-> C
    C --> X[extractor.py<br/>sarvam chat -> JSON<br/>schema validate, retry once]
    X --> F{open + valid?}
    F -- yes --> K
    F -- expired / invalid --> L[discard + log]
  end
  subgraph B[B. Knowledge store]
    K[(SQLite<br/>records + FTS5 BM25)]
  end
  subgraph Cc[C. Conversational app]
    U[Browser<br/>text / mic / chips / profile] -->|/api/ask NDJSON| P[qa.py]
    P --> LD[Sarvam: language id]
    LD --> T1[Sarvam: translate to English]
    T1 --> SL[Sarvam chat: intent + slots]
    SL --> SE[search.py<br/>deterministic filters + BM25 + rank]
    SE <--> K
    SE -->|no match| NF[fixed 'not found' text - no model call]
    SE -->|records| AN[Sarvam chat: answer from CONTEXT only]
    AN --> VF[Sarvam chat: verify + regex scrub of dates, amounts, URLs]
    VF --> T2[Sarvam: translate answer, transliterate names]
    T2 --> U
    U -->|/api/stt| STT[Sarvam saaras]
    U -->|/api/tts| TTS[Sarvam bulbul]
  end
```

## Rules the code enforces

| Concern | Where |
|---|---|
| Model never answers from memory | `qa.py`: answer prompt gets only retrieved records; no records means a fixed message and **no model call** |
| Eligibility is deterministic | `search.py`: age, category, gender, education, income, state and deadline are plain code |
| Unsupported facts are removed | `qa.unsupported_claims`: every URL, date and money amount in the answer must literally appear in the retrieved records |
| Expired items are not open | `Store.close_expired`, `search.is_open`, `extractor.keep_open` |
| Numbers and names stay exact | `cards.py`: eligibility text is built from structured fields; names are transliterated; dates and URLs are never translated |
| Key stays on the server | `settings.py`; the browser only calls `/api/*` |
| Official sources only | `crawler.is_official`; aggregators are mined for official links only |

## Data flow for one question

1. Detect language, translate to English (cached).
2. Extract slots (type, state, age, category, topic) with the chat model; heuristics fill gaps if the JSON is bad.
3. Merge with the optional profile form (the form wins for personal facts).
4. Search: hard filters, then BM25 ranking blended with profile match, deadline urgency and source confidence.
5. Stream the result cards immediately (translated in parallel), then the answer.
6. Answer is drafted, fact-checked by a second model call, scrubbed by regex, translated, and streamed.
