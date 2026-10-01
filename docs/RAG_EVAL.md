# RAG evaluation (R1, SLT-Mobitel knowledge base)

## What was built and from what

- **www.sltmobitel.lk**, the site named in the brief. robots.txt returns
  404, so nothing is disallowed; the crawler still reads and obeys it. The
  site is a Nuxt app, and its server HTML turned out to hold only
  navigation, footer and the app store (banners, home page tiles in
  English, Sinhala and Tamil, page descriptions). Package tables, prices
  and most page bodies are fetched by the browser from a separate API host
  (apphub.mobitel.lk) that requires a client id header; the crawler does
  not call it (different host, and a credential issued to the website, not
  to us). Playwright is not installed, so client rendered bodies are not
  available. On its own this site gives the index **no package prices**.
- **www.mobitel.lk**, the older official site of Mobitel (Pvt) Ltd, SLT's
  wholly owned subsidiary, still online and server rendered, with package
  tables (prices, data, validity, TRC tariff references), FAQs, USSD
  codes, hotlines and APN settings. It is not linked from
  www.sltmobitel.lk, so it widens the brief's one domain; the owner
  approved including it on 2026-10-01 after seeing the comparison below,
  and it is part of the default build (`--no-include-legacy` leaves it
  out). Its robots.txt (Crawl-delay: 10) is read and obeyed.
- **Public sources** (`backend/knowledge/sources/public_sources.jsonl`,
  committed): hand written faithful summaries of Wikipedia (SLTMobitel),
  the official brand unification press release, Daily Mirror on the
  unification, the official 5G-Advanced trial page, Developing Telecoms on
  the December 2025 commercial 5G launch, and TRCSL's complaint procedure.
- **Curated FAQ** (`backend/knowledge/faq.jsonl`, committed, 59 entries):
  question and answer pairs selected by hand from the FAQ sections the
  normalizer detected on www.mobitel.lk pages, answers verbatim, questions
  reworded only to name the package ("this package" becomes "the MOBITEL
  1598 package"). They are indexed only together with that host
  (dropped with `--no-include-legacy`).

## Method

`python -m backend.knowledge.eval` (see the module docstring):

- 32 questions with expected facts and expected source URL substrings
  (packages and prices, FAQs and procedures, support contacts, company
  history; 3 in Sinhala and 3 in Tamil), plus 4 negative questions (small
  talk and another operator), all in `backend/knowledge/eval_questions.jsonl`
  and all built from facts that are actually in the gathered content.
- **Retrieval hit@k** (k = RETRIEVAL_TOP_K = 4): one of the top k chunks
  comes from an expected source or contains every expected fact.
- **Facts in context**: share of expected facts present in what
  `context_for()` really hands to the model (after RETRIEVAL_MIN_SCORE and
  the relative margin of 0.12 below the best chunk).
- **Answer quality**: the local model (llama3.2 3B through Ollama,
  temperature 0) answers each question twice. Baseline: today's production
  system prompt (voice persona and reply language), no retrieval. With
  retrieval: the same prompt plus the grounding rules in
  `knowledge/prompts.py` and the `context_for()` block. Score = share of
  expected facts present in the answer (case, spaces and thousands
  separators ignored).
- **Latency**: every question searched 3 times with the query cache
  cleared (so each sample includes the bge-m3 embedding call), in a
  process that opted out of Windows power throttling like the server does.
  Cold numbers: index load, first query in a fresh retriever, and first
  query after Ollama unloaded bge-m3.
- The run used RETRIEVAL_MIN_SCORE=0.53, the configured default.

## Why both hosts: the comparison that decided it

Measured on 2026-10-01 with the same 32 questions, RETRIEVAL_MIN_SCORE 0.53:

| Measure | www.sltmobitel.lk only (80 chunks) | both official hosts (708 chunks) |
| --- | --- | --- |
| Retrieval hit@4 | 31% | 97% |
| Expected facts present in the context | 27% | 92% |
| Answer fact score, with retrieval (no retrieval baseline: 5%) | 22% | 75% |

With the brief's site alone the index knows the company, contact numbers
and history, but no package or price, and the local model, told it has no
detail, still sometimes invents a price ("Rs. 1,299"). With both hosts the
package questions are answered from the official tables.

## Threshold (RETRIEVAL_MIN_SCORE)

Top cosine scores measured on the two host index with bge-m3:

| Query type | Top score range |
| --- | --- |
| Small talk and off topic ("hello", "what time is it", "good morning", "tell me a joke", "play some music", "ආයුබෝවන්", "வணக்கம்", 18 queries) | 0.379 to 0.524 |
| Real SLT-Mobitel questions (32) | 0.492 (one Tamil question), otherwise 0.545 to 0.820 |

The first default of 0.45 let most small talk through (a "hello"
would get package chunks as context). **Now the default: 0.53** (config.py), which returns
None for every small talk query measured and keeps every English and
Sinhala question; the cost is the one Tamil question at 0.492 (see below).
The relative margin (0.12 below the best chunk) then trims weak extra
chunks from a good match.

<!-- EVAL RESULTS START default -->
<!-- generated by python -m backend.knowledge.eval; do not edit by hand -->
Run 2026-10-01T04:48:59+00:00, index built 2026-10-01T04:40:07+00:00, embedding model bge-m3, answer model llama3.2:latest, top_k 4, RETRIEVAL_MIN_SCORE 0.53, crawled hosts ['www.sltmobitel.lk', 'www.mobitel.lk'].

| Measure | Value |
| --- | --- |
| Questions (with expected facts / negative) | 32 / 4 |
| Retrieval hit@4 | 97% |
| Retrieval hit@1 | 84% |
| Context returned by context_for | 97% |
| Expected facts present in the context | 92% |
| Negative questions with no context (correct) | 3 of 4 |
| Answer fact score, no retrieval baseline | 5% |
| Answer fact score, with retrieval | 75% |
| Answers with every expected fact, baseline / retrieval | 3% / 72% |
| Answer time p50, baseline / retrieval (full reply, not streamed) | 531 ms / 845 ms |
| Retrieval latency warm, p50 / p95 (108 searches, embedding included) | 36 ms / 51 ms |
| context_for with the query embedding cached | 38.35 ms |
| Cold: index load | 12.2 ms |
| Cold: first query in a fresh retriever (model already loaded) | 161 ms |

By language:

| Language | Questions | hit@k | Facts in context | Baseline | Retrieval |
| --- | --- | --- | --- | --- | --- |
| en | 26 | 96% | 94% | 6% | 88% |
| si | 3 | 100% | 100% | 0% | 33% |
| ta | 3 | 100% | 67% | 0% | 0% |

Per question:

| id | lang | category | question | top score | hit rank | context | facts in context | baseline | retrieval | retrieval answer (truncated) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pkg-rollover-159 | en | package | How much is the anytime data rollover plan with 1.5 GB, and how long is it va... | 0.793 | 1 | yes | 100% | 0% | 100% | The Anytime Data Rollover Plan with 1.5 GB costs Rs. 159, and it's valid for 14 days. Prices and packages can change, so the latest details are on www.sltmob... |
| pkg-rollover-largest | en | package | What is the biggest anytime data rollover plan and its price? | 0.708 | 1 | yes | 100% | 0% | 100% | The biggest anytime data rollover plan is 18.5 GB, priced at Rs. 1,199. Prices and packages can change, so the latest details are on www.sltmobitel.lk or the... |
| pkg-1598 | en | package | What do I get with the MOBITEL 1598 package? | 0.682 | 1 | yes | 100% | 0% | 100% | You get 40GB data, unlimited Any Net Voice, any net 1000 SMS, non-stop data for 9 apps, and a credit limit of Rs 1750. |
| pkg-hello-ai | en | package | How much is the Hello AI package and who can get it? | 0.647 | 1 | yes | 100% | 0% | 100% | The Hello AI package is priced at Rs. 799. It is available to existing SLT-Mobitel mobile customers aged 16 to 25 years who can activate it by submitting a s... |
| pkg-social-combo | en | package | What is the price of the 30 day Social Combo plan? | 0.692 | 1 | yes | 100% | 0% | 100% | The price of the 30-day Social Combo plan is Rs.646 (Tax Inclusive). |
| pkg-youtube-7 | en | package | How much is the 7 day YouTube Combo? | 0.663 | 1 | yes | 100% | 0% | 100% | The 7-day YouTube Combo plan costs Rs. 191 (Tax Inclusive). |
| pkg-tourist | en | package | What tourist SIM plans does Mobitel offer for visitors? | 0.613 | 1 | yes | 100% | 0% | 0% | Mobitel offers three tourist SIM plans: Plan 01 (7 Days), Plan 02 (30 Days), and Plan 03 (30 Days). |
| pkg-streaming | en | package | What is the monthly rental of the Unlimited Streaming package? | 0.782 | 1 | yes | 100% | 0% | 100% | The monthly rental of the Unlimited Streaming package is Rs. 1,798.80 (excluding tax). |
| pkg-anytime-combo | en | package | What are the prices of the Anytime Combo plans? | 0.732 | 1 | yes | 100% | 0% | 100% | The prices of the Anytime Combo plans are Rs. 249, Rs. 333, and Rs. 444, tax inclusive. Prices and packages can change, so the latest details are on www.sltm... |
| pkg-idd-cards | en | package | What IDD cards are available and how long are they valid? | 0.635 | 1 | yes | 100% | 0% | 100% | We have three IDD cards available: IDD (Rs. 589.00, 30 days validity), IDD (Rs. 189.00, 14 days validity), and IDD (Rs. 95.00, 10 days validity). Prices and ... |
| faq-data-addon | en | faq | How do I activate a data add-on package? | 0.597 | 2 | yes | 100% | 0% | 100% | To activate a data add-on package, dial #170# followed by the package code, and then send the SMS to 77100. |
| faq-roaming-activate | en | faq | Do I need to activate roaming on my prepaid SIM before travelling? | 0.744 | 1 | yes | 100% | 0% | 100% | No, you don't need to activate roaming on your prepaid SIM before travelling. Every Pre-paid SIM is roaming pre-enabled. |
| faq-one-rate-roaming | en | faq | How do I activate one rate roaming? | 0.82 | 1 | yes | 100% | 0% | 100% | To activate One rate roaming, dial #999# and select option 3, then select option 1. |
| faq-loan-balance | en | faq | How can I check the balance of my Mobitel loan? | 0.766 | 1 | yes | 100% | 0% | 100% | You can check the available balance of your loan by dialing *100# on your phone. You will receive an SMS with the remaining balance on your loan. |
| faq-apn | en | faq | What APN should I set on my phone for Mobitel internet? | 0.685 | 1 | yes | 100% | 100% | 100% | For Mobitel internet, set the APN to "mobitel". |
| faq-ebill | en | faq | How do I activate the e-bill service for my postpaid connection? | 0.721 | 1 | yes | 100% | 0% | 100% | You can activate the Mobitel e-bill service by dialing #9900#, selecting "activate e-bill", entering your email address, and following the prompts. |
| faq-recharge-100 | en | faq | If I reload 100 rupees, how many days of validity do I get? | 0.652 | 1 | yes | 100% | 0% | 0% | You get 20 days of validity for a reload of Rs. 100. |
| faq-call-me | en | faq | How do I send a call me request when I have no credit? | 0.65 | 1 | yes | 100% | 0% | 100% | You can send a Call Me request even when you have no credit. Just dial the USSD string “*359*1*Mobitelnumber#” and an SMS will be sent requesting for a call. |
| sup-hotline | en | support | What is the Mobitel customer service hotline number? | 0.583 | 2 | yes | 100% | 0% | 100% | You can dial 1717 from any network in Sri Lanka, or +94 (0) 712755777. |
| sup-roaming-hotline | en | support | What number do I call for roaming help while abroad? | 0.668 | 1 | yes | 100% | 0% | 100% | You can call the Mobitel Roaming Hotline on +94 714 555 555 while abroad. |
| sup-email | en | support | What email address can I write to Mobitel for inquiries? | 0.568 | - | yes | 0% | 0% | 100% | You can write to us at info@mobitel.lk for inquiries. |
| sup-trc-complaint | en | support | If Mobitel does not resolve my complaint, how can I escalate it to the regula... | 0.583 | 2 | yes | 100% | 50% | 50% | You can submit a written complaint to the Telecommunications Regulatory Commission of Sri Lanka (TRCSL) if Mobitel does not resolve your complaint. |
| co-brand-unification | en | company | When did SLT and Mobitel start operating under one brand? | 0.644 | 1 | yes | 100% | 0% | 100% | SLT and Mobitel started operating under one brand, SLT-MOBITEL, on 1 January 2021. |
| co-mobitel-founded | en | company | When was Mobitel incorporated and when did it become fully owned by SLT? | 0.545 | 1 | yes | 50% | 0% | 50% | Mobitel was incorporated in 1993. I do not have information on when it became fully owned by SLT. You can check www.sltmobitel.lk or call 1717 for the latest... |
| co-history | en | company | How long has SLT been serving Sri Lanka's connectivity needs? | 0.689 | 1 | yes | 100% | 0% | 100% | SLT has been serving Sri Lanka's connectivity needs for over 163 years. |
| co-5g | en | company | Has SLT-Mobitel launched 5G? | 0.722 | 1 | yes | 100% | 0% | 100% | Yes, SLT-Mobitel launched commercial 5G services in Sri Lanka in December 2025. |
| si-hotline | si | support | Mobitel පාරිභෝගික සේවා දුරකථන අංකය කුමක්ද? | 0.586 | 2 | yes | 100% | 0% | 100% | Mobitel පාරිභෝගික සේවා දුරකථන අංකය 1717 යෙ. |
| si-roaming | si | faq | එක් අනුපාත රෝමිං (one rate roaming) සක්‍රිය කරන්නේ කෙසේද? | 0.743 | 1 | yes | 100% | 0% | 0% | එක් අනුපාත රෝමිං සක්‍රිය නොමන පළර රෝමිං ද රෝමිං ද රෝමිං ද රෝමිං ද රෝමිං ද රෝමිං ද රෝමිං ද රෝමිං ද රෝමිං ද රෝමිං ද රෝමිං ද රෝමිං ද රෝමිං ද රෝමිං ද රෝමිං ද රෝම... |
| si-rollover | si | package | 1.5GB anytime data rollover පැකේජයේ මිල කීයද? | 0.777 | 1 | yes | 100% | 0% | 0% | මිල කීය පැකේ මිල පැකේ මිල පැකේ මිල පැකේ මිල පැකේ මිල පැකේ මිල පැකේ මිල පැකේ මිල පැකේ මිල පැකේ මිල පැකේ මිල පැකේ මිල පැකේ මිල පැකේ මිල පැකේ මිල පැකේ මිල පැකේ ... |
| ta-hotline | ta | support | Mobitel வாடிக்கையாளர் சேவை தொலைபேசி எண் என்ன? | 0.581 | 1 | yes | 100% | 0% | 0% | SLT-Mobitel வாடிக்கையாளர் சேவை தொலைபேசி எண் 0710755777. |
| ta-data-addon | ta | faq | டேட்டா ஆட்-ஆன் பேக்கேஜை எப்படி செயல்படுத்துவது? | 0.492 | 1 | none | 0% | 0% | 0% | டேட்டா ஆட்-ஆன் பேக்கேஜை அதன் உள்ளீடு செய்யும் போது அதன் காட்சி திரையில் தெரியும். அதன் காட்சி திரையில் தெரியாத போது அதன் காட்சி திரையில் தெரியும் வரை அதன் கா... |
| ta-tourist | ta | package | சுற்றுலாப் பயணிகளுக்கான Mobitel tourist SIM பேக்கேஜின் விலை என்ன? | 0.689 | 1 | yes | 100% | 0% | 0% | மொபீல் சுற்றுலாப் பயணிகளுக்கான டூரிஸ்ட் எச்ஐஎம் பேக்கேஜின் விலை ரூ. 5131/= ஆகும். பயன்பாட்டுக்குப் பிறகு மாற்றம் செய்யப்படாத விலை. |
| neg-hello | en | negative | hello | 0.496 | - | none | n/a | n/a | n/a |  |
| neg-time | en | negative | what time is it | 0.524 | - | none | n/a | n/a | n/a |  |
| neg-thanks | en | negative | thank you, that's all | 0.427 | - | none | n/a | n/a | n/a |  |
| neg-other-brand | en | negative | How much is Dialog's monthly data package? | 0.591 | - | yes | n/a | n/a | n/a |  |
<!-- EVAL RESULTS END default -->


## Notes on the results

- **Latency.** Retrieval stays well under the 150 ms budget before the
  first spoken word: 36 ms p50 and 51 ms p95 including the bge-m3 query
  embedding, measured with Windows power throttling turned off as the
  server does. An earlier run of the two host index without that opt out
  measured 122 ms p50 and 203 ms p95; the throttled number is what a
  benchmark started from a background shell sees, not the server.
- **Other operators.** "How much is Dialog's monthly data package?" scores
  0.59 against SLT-Mobitel's own package tables, so it does get context
  (the one negative question that fails). The context is only SLT-Mobitel
  data and the grounding rules tell the model to decline other operators'
  details, so the risk is a reply about SLT-Mobitel packages instead of a
  plain decline, not invented Dialog prices.
- **Sinhala and Tamil.** Retrieval works across languages (every Sinhala
  and Tamil question finds the right English source, one Tamil question
  falls just under the threshold at 0.492), but llama3.2 3B cannot write
  usable Sinhala or Tamil answers from it (repetitive or wrong text in the
  per question table). A model with real Sinhala and Tamil support, such as
  the cloud Gemini path once its credit is restored, is needed for those
  answers; see KNOWN_LIMITATIONS.md.
- **Prices go stale.** Every chunk carries its source URL and retrieval
  date, and the prompt tells the model to say prices can change and point
  to www.sltmobitel.lk or 1717. Rebuild with `python -m backend.knowledge.build`
  to refresh.
