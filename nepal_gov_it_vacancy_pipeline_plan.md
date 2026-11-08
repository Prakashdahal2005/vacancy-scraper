# Nepal Government IT-Officer Vacancy Pipeline: Final Plan (v2)

Prepared 6 Nov 2026 (BS 2083). Scope: federal Public Service Commission (PSC), the seven provincial PSCs, Tribhuvan University Service Commission (TUSC), Nepal Electricity Authority (NEA), and similar public bodies. Approach only, no code.

**Evidence labels**
- **[Observed]** seen in documents or pages I retrieved.
- **[Reported]** stated by a third-party site (aggregator, blog), not checked at the official source.
- **[Recommendation]** my design judgement, to be validated on your own sample.
- **[Not verified]** open item.

---

## 0. Read this first: what this document is, and what it is not

This is a researched **design**, not a validated end-to-end system. The current Python prototype checks four PSC sources, discovers likely advertisement links, downloads same-site PDFs within size/page limits, extracts selectable text locally, and includes only notices passing text rules for open competition, an IT/Computer Officer role, and publication date within 60 days. If a level is stated near the role, it must be seventh; if no level is stated, an exact IT/Computer Officer title can pass. Promotion-only notices and explicit non-seventh-level IT posts are excluded. Mixed promotion/open advertisements are included only when the IT role appears near the open-competition marker. Scanned PDFs and documents with ambiguous extracted text are not OCR'd and will generally be omitted. Results and rule evidence are written to a local HTML report, CSV, console summary, and SQLite history. One live low-volume scan returned no qualifying notices; it also exposed stale/unavailable notice links and incomplete listing discovery. This is not enough to validate accuracy, so the source listing URLs and extraction rules still need real-notice review.

So you cannot yet "rely on it to get the latest vacancy info." The prototype is a starting point for discovery, not a source of confirmed vacancy or deadline data. The data only becomes reliable after it is tested and **measured** on real notices (section 9). Until then:

- Treat any output as a **pointer to the official notice**, never as the final word.
- Always confirm the **deadline, post name, level and eligibility** on the official PDF before acting.
- Do not trust a field until its measured accuracy on your own test set is good enough for you.

---

## 1. The architecture in one paragraph

A config-driven crawler watches each body's notice list and discards non-vacancy notices by title before downloading anything. Each downloaded PDF is classified page by page by how its text is stored (clean Unicode, Preeti, scanned, damaged, overprinted) and sent to the cheapest method that passes verification. Fixed-format header fields (notice number, dates, apply portal) are read with rules. The post table is read by an LLM that must quote the source text for every value. A relevance step decides which posts are IT posts. Dates and counts are validated, uncertain items go to a human queue, and every record links to the original notice.

---

## 2. What the evidence shows about each source

### Federal PSC (psc.gov.np)
- [Observed] Notice files sit under two URL layouts (`/assets/uploads/files/` and `/uploads/others/`). Some filenames carry the BS date, for example an advertisement file named `विज्ञापन_2082_10_28`.
- [Observed] The site mixes vacancy ads with other files: exam-hall allocation notices, results bulletins listing roll numbers and candidate names, and notices from regional offices (Dhankuta and Dipayal files were visible).
- [Observed] Ads carry a notice number (example format `1005/081-82`), a BS publication date and a deadline. One ad can bundle promotion, internal and open/inclusive categories.
- [Reported] A later deadline with double fee is standard.
- [Observed] Applications go through `psconline1.psc.gov.np`, which needs a login. Out of scope.
- [Observed] Text-layer quality is inconsistent (section 4).

### Provincial PSCs (7)
- [Observed] Domains are not uniform and have changed over time.
  - **Koshi:** `psc.koshi.gov.np`, and in older material `psc.p1.gov.np`.
  - **Sudurpashchim:** `psc.sudurpashchim.gov.np`, with a separate application portal `ppsconline.sudurpashchim.gov.np`.
  - **Bagmati:** `ppsc.bagamati.gov.np` (an earlier address `spsc.bagamati.gov.np` also appears), application portal `ppsconline.bagamati.gov.np`. The official spelling in web addresses is **bagamati**, not bagmati, so a crawler or search using the common spelling will miss it.
- [Reported] Karnali has a portal at `ppsconline.karnali.gov.np` and Madhesh uses the older `p2` naming (aggregator page titles only).
- [Not verified] Official domains for **Gandaki** and **Lumbini**, and the exact current Karnali and Madhesh addresses. Confirm in Phase 0.
- [Observed] Notice numbering differs by body and year: Sudurpashchim `88/2082-083`, Bagmati `98/078-79`. Bagmati titles use **Devanagari digits** and sometimes a range of ad numbers (for example `३९-४०/०८२-८३`).
- [Observed] Bagmati publishes **additional-vacancy and deadline-extension notices** that refer back to an earlier notice number and change its post count and deadline. These must update the original record, not create a new one.
- [Observed] Bagmati also publishes an **annual work schedule** (for FY 2083-084 a title listing it was visible). [Recommendation] Such schedules can give early warning of when advertisements are planned. Treat as an optional add-on.
- [Reported] Ads are issued per level, split into technical and non-technical, with fee tables per level.

### TUSC (Tribhuvan University Service Commission)
- [Observed] The web address has moved several times. All of these appear in sources: `tuservicecommission.edu.np`, `vacancies.tuservicecommission.edu.np`, `tusc.tu.edu.np`, `vacancy.tu.edu.np`, `teachervacancy.tu.edu.np`.
- [Reported] Most notices are for teaching posts, but administrative and technical posts are also advertised. Aggregators also list individual TU offices advertising posts such as computer operator and chief computer assistant (not checked at source).
- [Observed] Dates are BS, with a regular and a double-fee deadline.

### NEA (nea.org.np)
- [Reported] Open recruitment and internal/promotion notices live on separate paths (`recruitment_open`, `recruitment_internal`). External applications go to `career.nea.org.np`; current NEA staff apply via `hris.nea.org.np`.
- [Observed] Ads are highly structured: post, level, service, group, sub-group, number of posts, open/inclusive quota split, re-publication flags, and an advertisement number like `2082/83 Technical.7/164`.
- [Observed] Many NEA notices are internal promotion results or review-committee notices, which are noise for this purpose.

### Other bodies and aggregators
- [Observed] Banks and corporations (example: ADBL) list career notices over many numbered pages, mostly results. Paging must stop at already-seen items.
- [Reported] Nepal Telecom posts vacancies under the Career section of `ntc.net.np`.
- [Observed] Aggregators (examsanjal, edusanjal, kumarijob, collegenp, nepalijob, loksewatayariapp) repost notices, and one re-summarised PSC notice had lost its deadline. Use aggregators only to detect gaps and cross-check, never as the source of truth.

---

## 3. Design principles

1. **Cheapest check first.** Decide relevance from titles and filenames before fetching or reading anything.
2. **Never trust a text layer.** Validate it, then pick the method.
3. **Deterministic before probabilistic.** Rules and mapping tables before LLMs.
4. **LLMs read and classify, they do not invent.** Every extracted value carries a verbatim quote that is checked against the text.
5. **Sources are data, not code.** A new site or a domain change is a config edit.
6. **Process once.** Cache raw files and per-page results by content hash.
7. **Recall over precision for IT relevance.** A missed vacancy costs more than one extra item to review.

---

## 4. The text-layer problem on Nepali government PDFs

The PSC files I could see show at least six distinct failure types, sometimes in the same file. That is more than the usual three categories (Unicode, legacy font, scanned).

| Class | What it looks like | How to detect | Route |
|---|---|---|---|
| A. Clean Unicode | Proper Devanagari | Unicode font names; anchor words found; few invalid sequences | Use text directly |
| B. Legacy font (Preeti, Kantipur, etc.) | Symbols such as `g]kfn ;/sf/` | Known legacy font name; very low Devanagari share; heavy punctuation like `] ; [` | Deterministic mapping converter, then re-validate |
| C. Scanned | No text layer | Empty text | Render at 300 DPI, OCR or vision model |
| D. Damaged Unicode | Looks Nepali but letters are wrong | Anchor words missing; invalid vowel-sign sequences; low dictionary hit rate | OCR or vision model |
| E. Overprinted duplicates | Every character or phrase doubled | Ratio of adjacent duplicate characters | De-duplicate and re-test, otherwise OCR |
| F. Mixed page | Preeti title with Unicode body | Test line by line | Route by region, otherwise OCR the whole page |

**[Observed] examples from PSC files, as extracted by a search tool (not by me running local extractors):**
- A file whose title line is Preeti while the rest is Unicode.
- A bulletin whose header is Preeti and whose table is Unicode.
- Damaged Unicode: `मिति` appeared as `धमधत`, `विशेष` as `ववशेष`, `प्रकाशित` as `प्रकाशशत`. The damage clusters around the short-i vowel sign (ि), and the substituted letter differs from file to file. [Recommendation] Treat this as a hypothesis. If confirmed, a per-file letter-swap repair could be tried, but only if a dictionary check verifies it. OCR is the safe default.
- Doubled text such as `लोक सेवा आयोगलोक सेवा आयोग`, and Preeti text with every character repeated, in regional office files.

**Detection tests, in order of cost**
1. Font names from PDF metadata (legacy font list, subset prefixes stripped).
2. Share of Devanagari characters versus Latin and symbols.
3. Anchor words expected in any notice: `लोक सेवा आयोग`, `सूचना नं.`, `प्रकाशन मिति`, `विज्ञापन`, `पद`, `तह`. If none are found, the text layer is not usable.
4. Invalid Devanagari sequences (a vowel sign after another vowel sign, or at the start of a word).
5. Dictionary or character n-gram check against a clean Nepali sample.
6. Adjacent duplicate-character ratio.

**Calibration step (one time, not production):** run two or three different PDF text extractors on the same 30 files and compare. Extractors handle font mappings differently, so one may read a file correctly that another garbles. This also tells you whether the damage above is in the files or only in one extraction tool.

---

## 5. Pipeline, stage by stage

### Stage 0. Source registry
One config record per body: all known domains and historical aliases (including spelling variants such as bagamati), listing URL patterns, paging style, notice-type hints, apply portals, expected encodings, last successful run. Because domains drift (Koshi, Bagmati, TUSC), run a health check each week and alert when a source returns nothing new for an unusually long time.

### Stage 1. Discover
Poll listing pages on a schedule. Use conditional requests where the server supports them. Stop paging at the first page where everything is already known. [Recommendation] Before writing HTML parsers, check each portal's browser network requests for a JSON endpoint behind the listing. MeroJob scrapers rely on exactly this kind of undocumented API, and a JSON feed is far more stable than page markup.

### Stage 2. Title and filename triage
Classify each notice from its title and filename before download.
- **Keep:** advertisements (`विज्ञापन`, `खुला तथा समावेशी प्रतियोगितात्मक`, `पदपूर्ति`, vacancy) and additional-vacancy or extension notices (they modify an existing ad).
- **Drop:** results, exam-hall and centre notices, bulletins listing roll numbers, syllabus, admit cards, review committees, candidate recommendations, promotion results.
- **Tag but separate:** internal and promotion competitions (`बढुवा`, `आन्तरिक`). They are not open to the public, but one ad often mixes categories, so category must also be extracted per post.
- Dropping candidate-name bulletins here also keeps personal data out of your system.

### Stage 3. Fetch and cache
Per-host rate limits, retries with backoff, a clear user-agent, raw bytes stored by content hash. Cap page count: a very long PDF is probably a name list, not an ad.

### Stage 4. Per-page encoding router
Apply the section 4 tests to each page and route. Store the class and the extracted text per page so nothing is processed twice.

### Stage 5. OCR and vision tier (classes C, D, E, F)
- Render at 300 DPI.
- [Reported] On real Hindi (Devanagari) scans, general vision models beat specialised OCR models, and strong English OCR did not predict Devanagari results. Specialised OCR models sometimes looped and produced output many times longer than the source. Run your own bake-off on 30 real PSC pages with current models before choosing.
- Use a detailed transcription prompt (verbatim, keep Devanagari digits, no translation, mark unreadable text). [Reported] A detailed prompt cut error up to sixfold on another Devanagari-script language.
- Reject output far longer than expected for the page.
- For critical fields (dates, counts), take a second independent reading (another model, or the text layer) and send disagreements to review.

### Stage 6. Extraction in two layers
**Layer 1, rules (fixed formats):** notice number (Devanagari or ASCII digits, single or range), publication date, normal deadline, double-fee deadline, apply portal URL, fee.

**Layer 2, LLM (variable text):** per post, the name, level, service, group, sub-group, number of posts, quota split (open, women, Janajati, Madhesi, Dalit, disability, backward area), qualification, category (open, internal, promotion), and whether it is a re-publication.

Rules for the LLM step: return the exact source quote and page for each value, allow "not found", and discard any value whose quote is not present in the text.

### Stage 7. IT relevance
1. Match a lexicon of Nepali, English and transliterated terms. Build it from the first 100 real ads, since service and group names differ by body.
2. Apply an exclusion lexicon. [Recommendation] Words such as `सूचना` on their own are ambiguous: an information (public relations) officer is not an IT officer.
3. Send borderline posts to an LLM along with the post's service, group and qualification.
4. Output three labels: **relevant**, **maybe**, **not**. The "maybe" bucket goes to a person.

### Stage 8. Normalise and validate
- Convert BS to AD with a library whose table covers **BS 2083 and later**. One converter I found stops at BS 2082, so it would fail on today's notices. Others claim 1901 to 2199 BS. Test with a current date.
- Keep the original BS string next to the AD date.
- Checks: deadline after publication date; double-fee deadline after the normal one; month and day within valid BS ranges; notice-number year matches the publication year; quota counts sum to the post count.

### Stage 9. Versioning
Treat each advertisement as an entity with versions. Re-publications, additional-vacancy notices, corrections and deadline extensions update the same record (matched by body plus notice number) rather than creating duplicates. Record every change with its source notice.

### Stage 10. Output
Structured record, link to the original PDF, per-field confidence, and a needs-review flag. Daily digest sorted by deadline. Aggregator copies are cross-checks only.

---

## 6. How to use it safely at first

| Output | Trust level at first |
|---|---|
| "A new notice exists on site X, here is the link" | Fairly high. Discovery is simple and checkable. |
| Notice number and publication date | Medium to high on clean files, lower on damaged ones. Verify. |
| Deadlines | **Verify on the PDF every time.** A wrong deadline is the costliest error. |
| Post names, levels, counts | Verify before acting. |
| "This is an IT post" | Treat "relevant" as likely and "maybe" as a prompt to read the notice yourself. |

---

## 7. Quality and monitoring

- **Golden set:** about 120 hand-labelled documents at the start, balanced across source, encoding class and notice type, with fields checked by a person.
- **Measure per field.** For dates and numbers use exact match. For IT relevance, track recall first. Report medians plus a catastrophic-failure rate, not only averages.
- **Weekly audit** of a random sample of auto-accepted records.
- **Drift alarms:** new font names, a change in the mix of encoding classes, a sudden drop in new items, a rising share of "not found" fields, OCR output length ratio outside range, any listing-page layout change.

---

## 8. Build order and exit criteria

- **Phase 0, Sample.** Collect about 150 real notices across PSC, two provinces, TUSC and NEA. Label encoding class. Compare extractors. Confirm the unverified provincial domains and check robots.txt and any JSON endpoints. *Exit:* you know the encoding mix per source.
- **Phase 1, Federal PSC on clean documents.** Discovery, triage, rules, validation. *Exit:* dates and notice numbers correct on the golden set.
- **Phase 2, Router and OCR tier.** Add classes B to F and the bake-off. *Exit:* a text-quality pass rate you accept for every class.
- **Phase 3, Provinces.** One at a time, each as a config plus any layout quirks.
- **Phase 4, TUSC, NEA, other bodies.**
- **Phase 5, LLM post extraction and relevance, review queue, digest.**

Do not add a new source until the previous one meets its exit criteria.

---

## 9. Compliance and politeness

- Respect robots.txt and rate limits, identify the crawler, and cache aggressively. Government sites are often slow.
- Collect only public notices. Do not touch login-based application portals.
- Skip documents that list candidate names and roll numbers.
- Always link the official notice and tell readers to confirm there.

---

## 10. Limits of this research

- **No live testing.** Everything came from search results and extracted text. I did not open the sites, run extractors, or test OCR.
- **Damage patterns in section 4** come from a search tool's extraction. Re-check them with your own tools.
- **Provincial coverage is incomplete.** Koshi, Sudurpashchim and Bagmati are reasonably confirmed. Karnali and Madhesh are partly reported. Gandaki and Lumbini are not verified.
- **OCR evidence is Hindi, not Nepali**, from word-level scans, with model versions that are already dated. The bake-off must decide.
- **Some claims come from aggregators or vendor blogs** and were not checked at the source.
- **I did not check** robots.txt, terms of use, or whether sites provide feeds.
- **Model choice is open on purpose.** Test two or three on the golden set.
- **No published, Nepal-specific accuracy comparison** of this kind of pipeline was found.

---

## 11. If you only do five things

1. Triage by title and filename before downloading.
2. Classify every page's text quality before trusting it.
3. Use OCR or vision only where the text layer fails.
4. Make the LLM quote its source, and verify the quote.
5. Validate BS dates with a library that covers BS 2083 and later.

---

## Sources consulted

- LamiSema, the Nepali PDF encoding problem: https://lamisema.readthedocs.io/en/latest/encoding-types/
- nepali-converter (legacy Nepali font conversion): https://pypi.org/project/nepali-converter/
- Can OCR-VLMs Read Devanagari? (benchmark): https://arxiv.org/pdf/2606.29213
- CoRSAL-OCR, prompt effects: https://aclanthology.org/people/andrew-haynes/unverified/
- Loksewa Tayari aggregator (federal and provincial notice lists): https://loksewatayariapp.com/vacancies/psc
- Koshi PSC notices: https://nepalijob.com/job/17124/koshi-pradesh-loksewa-aayog-vacancy-2082083-in-kathmandu
- Sudurpashchim PSC portal: https://www.kumarijob.com/administrator/58157-vacancy-for-8th-and-9th-level
- Bagmati PSC addresses and notice formats: https://www.collegenp.com/news/bagmati-pradesh-lok-sewa-aayog-notice-for-vacancy-fulfillment-request and https://www.collegenp.com/vacancy/bagmati-pradesh-lok-sewa-aayog-vacancy-for-various-positions-6th-7th-and-8th-level
- Bagmati additional-vacancy notices: https://edusanjal.com/vacancy/additional-vacancy-for-various-positions-at-bagmati-province-lok-sewa-aayog
- NEA vacancy structure: https://www.collegenp.com/vacancy/nepal-electricity-authority-vacancy-2082
- NEA portals: https://loksewatayariapp.com/vacancies/sansthan/nea
- TUSC notices: https://blog.educatenepal.com/2025/02/jobs-at-tu-service-commission-tu-sewa.html
- BS date libraries: https://pypi.org/project/nepalify/ and https://pypi.org/project/ad-bs-converter/
