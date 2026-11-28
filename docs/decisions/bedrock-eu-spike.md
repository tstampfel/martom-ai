# Spike: Claude and Cohere on Amazon Bedrock in eu-central-1 (#149, M6-6)

Status: **research only, no decision yet.** Tomislav decides. No code, infra or dependency changes come with this doc.
Researched: 28 Nov 2026, from public AWS, Anthropic, Cohere and LangChain docs only. No AWS account or credentials were used, so nothing here was tried against a real endpoint.

Every claim has a source number like [A3]. The sources, with the date each was read, are at the end. **Unconfirmed** marks anything I could not confirm from an official page.

## Why this spike

The privacy and transfer review (Tomislav, 28 Nov 2026) found that the Claude API (Anthropic) and the Cohere API (embed and rerank) process customer text outside the EU. The question is whether moving the three model calls to Amazon Bedrock, in or near eu-central-1 (Frankfurt, where RDS and ECS already run), keeps that text inside the EU. It also asks what the move would cost us in features, money and latency.

What we call today, and where it is in the code:

| Call | Model today | Where | Live limits we rely on |
| --- | --- | --- | --- |
| Agent answer and tools | `claude-sonnet-5-5`, effort `medium`, thinking left at its default (Sonnet 5.5 rejects disabled thinking) | `agent/factory.py` | none beyond the SDK defaults |
| Query parser | `claude-haiku-5-5`, `thinking={"type": "disabled"}`, `effort="low"`, `with_structured_output(..., method="json_schema")`, `max_retries=0` | `search/query_parser.py` | 2.5 s (`PARSE_TIMEOUT_SECONDS`) |
| Understanding, grading | `claude-haiku-5-5`, structured output, thinking disabled, effort `low` | `agent/factory.py` | — |
| Extraction (nightly) | `claude-haiku-5-5`, thinks by default, `json_schema` structured output (forcing a tool isn't allowed while thinking) | `extract/extractor.py` | 4 at a time, `MAX_TOKENS` 16,000 |
| Query embedding | Cohere `embed-multilingual-v3.0`, 1024 numbers | `embeddings.py` | 3 s per request (`COHERE_QUERY_TIMEOUT_SECONDS`), one attempt; `search_hybrid` caps the whole wait at 5 s |
| Rerank | Cohere `rerank-v5.0-fast` | `search/rerank.py` | 1.5 s overall (`RERANK_TIMEOUT_SECONDS`), 2 s HTTP, no retry |

## Summary for the decision

1. **All four models exist on Bedrock for an eu-central-1 caller. Only the two Cohere models run in Frankfurt itself.**
   - Rerank 3.5 and Embed Multilingual v3 run **in-region in eu-central-1**, with no cross-region routing [A4][A5][A6].
   - Haiku 5.5 and Sonnet 5.5 have **no in-region option** on `bedrock-runtime`. From Frankfurt they run only through the **EU geographic cross-region inference profile** (`eu.anthropic.claude-haiku-5-5`, `eu.anthropic.claude-sonnet-5-5`) [A1][A2].
   - That profile routes to six EU Regions: Frankfurt, Stockholm, Milan, Spain, Ireland and Paris [A1][A2]. Requests from an EU source Region are never routed outside the EU [A9].
2. **We can't keep `rerank-v5.0-fast`.** Bedrock has only Cohere **Rerank 3.5** (`cohere.rerank-v3-5:0`) and Amazon Rerank 1.0 [A4][C2]. That means a model change: `RELEVANCE_CUTOFF` (0.1, tuned on v5.0-fast) and the retrieval baselines must be redone before a switch.
3. **The embedding model is the same family, but identical vectors are unconfirmed.** Bedrock serves `cohere.embed-multilingual-v3` with 1024 floats and the same `input_type` values [A6][A7]. Nothing official says the vectors equal the Cohere API's, so plan a full re-embed (cheap, see costs) or prove equality first.
4. **The features we use exist on Bedrock for the Claude models.**
   - Bedrock lists structured outputs for both models [A1][A2].
   - Haiku 5.5's thinking can be turned off with effort up to `high`, so our `low` is fine [A1].
   - Sonnet 5.5 rejects disabled thinking on Bedrock too, the same as our code assumes [A2].
   - The open question is the client: which LangChain class gives us `json_schema` plus `thinking`/`effort` unchanged. See [Feature parity](#feature-parity).
5. **Cost: roughly +10% on the Claude line, which is about +$3 a month at launch traffic and about +$15 a month at five times that.**
   - Bedrock's regional (EU) profiles cost 10% more than the global endpoint [N2][N3]. The global endpoint isn't an option for residency.
   - The Cohere lines are a few dollars either way. Their direct-API prices are unconfirmed.
6. **The risks to weigh:**
   - Latency, which nobody publishes for EU geo routing.
   - Quotas on a new account.
   - The EU profile is a "limited model selection" profile, so future models may not reach it [A1].
   - Conversation threads can't move provider mid-thread, because thinking blocks are bound to the AWS account and model [A1][A2].
   - Re-tuning the rerank cutoff.

## Availability in eu-central-1

| Our model | Bedrock model / profile ID | How a Frankfurt caller reaches it | Where the data is processed | Source |
| --- | --- | --- | --- | --- |
| `claude-haiku-5-5` | `eu.anthropic.claude-haiku-5-5` (base `anthropic.claude-haiku-5-5`) | `bedrock-runtime` in eu-central-1 with the EU geo profile. No in-region endpoint ("In-Region endpoint URL: N/A") | eu-central-1, eu-north-1, eu-south-1, eu-south-2, eu-west-1, eu-west-3 | [A1] |
| `claude-sonnet-5-5` | `eu.anthropic.claude-sonnet-5-5` (base `anthropic.claude-sonnet-5-5`) | same as Haiku | same six Regions | [A2] |
| `rerank-v5.0-fast` | **not on Bedrock.** Closest: `cohere.rerank-v3-5:0` | in-region, through the Bedrock `Rerank` API (`bedrock-agent-runtime`) or InvokeModel. Geo and global profiles "Not supported" | eu-central-1 only | [A4][A5][C2] |
| `embed-multilingual-v3.0` | `cohere.embed-multilingual-v3` | in-region, InvokeModel on `bedrock-runtime`. Geo and global profiles "Not supported" | eu-central-1 only | [A6][A7] |

Notes:

- **Routing rules for the EU profile.**
  - The destination list depends on the source Region: Zurich and London add themselves, Frankfurt doesn't [A1].
  - AWS says a request from an EU source Region "can't get routed to non-EU Regions" [A9].
  - An EU-geo profile's destination list never changes once the profile is created [A9].
  - The current routing can be read with `GetInferenceProfile` from the source Region [A1].
- **What leaves the source Region.**
  - With cross-region inference, prompts and outputs may be processed in any destination Region of the profile [A10].
  - Data is stored only in the source Region by default. If abuse detection needs to store something, it stores it in the destination Region, still inside the EU here [A10].
- **What Bedrock does with the text.**
  - Bedrock doesn't store or log prompts and completions, doesn't use them for training, and doesn't share them with model providers [A12][A13].
  - Anthropic says the Bedrock endpoint runs on AWS-managed infrastructure with no operator access by Anthropic staff [N4]. That page covers `bedrock-mantle`. The `bedrock-runtime` page says 5.5 models are "served by the same infrastructure" [N3].
- **Billing.** Both Claude models and both Cohere models are AWS Marketplace products, billed on the AWS invoice under the model provider [A1][A2][A5][A6].
- **The `bedrock-mantle` endpoint** (Anthropic Messages API, served by `ChatAnthropicMantle` in LangChain) lists Haiku 5.5 only in `us-gov-west-1` [A1]. It isn't an option for us today. Sonnet 5.5's mantle Region list was not visible on the page I read (**unconfirmed**).
- **Claude Platform on AWS** (Anthropic-operated, billed through AWS) is "available in North America" for Haiku 5.5 [A3]. It doesn't solve EU residency.
- **Cohere direct API region:** Cohere's security and data-usage pages don't name a processing region or an EU option [C3][C4]. The USA/Canada finding in the issue comes from the earlier privacy review, not from this spike.

## Feature parity

| What the code uses | On Bedrock | Gap / action | Source |
| --- | --- | --- | --- |
| Structured output (`json_schema`) on Haiku: parser, understanding, extraction | "Structured outputs" listed as supported on `bedrock-runtime` for Haiku 5.5 and Sonnet 5.5 | Supported by the model. Whether our LangChain call reaches it unchanged depends on the client (next table) | [A1][A2][N3] |
| Haiku thinking off, effort `low` (parser) | "adaptive thinking is on by default and can be turned off; effort level configurable — low … max; effort capped at high when thinking is disabled" | none | [A1] |
| Haiku thinking on by default (extraction) | same; default effort `medium` | none | [A1] |
| Sonnet: thinking can't be disabled; effort `medium`; tools | "`thinking.type: "disabled"` is not supported" (use `between_tools`); client tool calling supports `tool_choice` `auto` and `none` only | Same rule our code already follows. We never force a tool on Sonnet, so no gap, but **don't** plan structured output on Sonnet through a forced tool | [A2] |
| No `temperature` | not stated on the Bedrock cards | **unconfirmed** for Bedrock; assume the same rejection as the Claude API | — |
| Prompt caching | explicit cache breakpoints supported; implicit/automatic caching not supported on `bedrock-runtime` | We don't rely on automatic caching today. If M6-3 adds caching, use explicit breakpoints | [A1][A2][N3] |
| Token counting API | "Count tokens" not supported on `bedrock-runtime` | We don't call it | [A1][A2] |
| Message Batches API | not supported on Bedrock (Anthropic page) | We don't use it (extraction is 4 concurrent live calls) | [N3] |
| Anthropic SDK version | the latest SDK sends API version `2026-10-21`. Models numbered 4.6 or lower reject it on Bedrock; 5.5 models are fine | none for 5.5 models | [N3] |
| Embedding `input_type` (`search_document` / `search_query`) | supported, up to 96 texts per call, 512 tokens each, 1024 floats | none on the API. Check that the LangChain class sends `search_query` for queries (**unconfirmed**) | [A7] |
| Rerank | Bedrock `Rerank` API (`bedrock-agent-runtime.rerank`) or the Cohere SDK's `BedrockClientV2`; Rerank 3.5 context 4K tokens; a query holds up to 100 documents (we send 30) | Different model (3.5). New relevance distribution → re-tune `RELEVANCE_CUTOFF` | [A4][A5][A8][C2] |

### LangChain classes

| Our class today | Bedrock option | Status |
| --- | --- | --- |
| `ChatAnthropic` (langchain-anthropic) for Sonnet and Haiku | **A. `ChatBedrockConverse`** (langchain-aws, Converse API) | Supports tool calling and `with_structured_output` [L1]. Its params include `additional_model_request_fields`, the usual way to pass `thinking`/effort [L1]. Whether it supports `method="json_schema"` (native structured output rather than a forced tool), and whether `effort` passes through for Claude 5.5, is **unconfirmed**. The reference page I read is from an older langchain-aws commit. Forced-tool structured output would clash with Haiku's default thinking in extraction (the reason `extractor.py` uses `json_schema`). |
| | **B. Anthropic SDK against `bedrock-runtime`**: `AnthropicBedrock(aws_region="eu-central-1")`, or the Messages API at `https://bedrock-runtime.<region>.amazonaws.com/anthropic` with a token from `aws_bedrock_token_generator.provide_token` (derived from IAM credentials, so the ECS task role works) | AWS shows the Messages API path for Haiku 5.5 [A3]; Anthropic documents `AnthropicBedrock` for `bedrock-runtime` [N3]. This keeps the request shape we use today (`thinking`, `effort`, `json_schema`). Whether `ChatAnthropic` itself can sit on top (custom base URL plus a refreshed token, or a wrapper class) is **unconfirmed**. It's the first thing to prototype, because it would keep `query_parser.py`, `extractor.py` and `factory.py` nearly unchanged. |
| | C. `ChatAnthropicMantle` (inherits `ChatAnthropic`, targets `bedrock-mantle`) | Simplest swap on paper [L2], but Haiku 5.5 isn't on mantle outside GovCloud [A1]. Not usable now. |
| Cohere `ClientV2` embeddings wrapped as LangChain `Embeddings` | `BedrockEmbeddings` (langchain-aws) | Exists [L2]. `input_type` handling for Cohere v3 is **unconfirmed** (see above). A thin `Embeddings` class over boto3 `invoke_model` is the fallback. |
| Cohere `AsyncClientV2.rerank` in `search/rerank.py` | `BedrockRerank` (langchain-aws document compressor, properties `model_arn`, `region_name`, `top_n`) [L3]; or boto3 `bedrock-agent-runtime.rerank`; or Cohere `BedrockClientV2` [C2] | All three are available. boto3 is synchronous, so a live reranker would run it in `asyncio.to_thread`. Async support in `BedrockRerank` is **unconfirmed**. |

### Timeouts and retries

The live limits that matter are **client-side**: `asyncio.wait_for` in `QueryParser.parse_with_fallback`, `search_hybrid`'s embed timeout and `retrieve`'s rerank timeout. They keep working whatever the provider, and the fallbacks (no filters, keyword-only, RRF order) stay the same.

What changes per client:

- **Anthropic SDK:** `default_request_timeout` and `max_retries` carry over as they are today.
- **boto3 (embed, rerank, Converse):** timeouts and retries go in `botocore.config.Config(connect_timeout=..., read_timeout=..., retries={"max_attempts": 1})` (**unconfirmed** for the exact settings we'd pick). The default botocore retry would otherwise retry a live call that the outer `wait_for` already gave up on.

Latency from Frankfurt is the open part: see [Risks](#open-risks).

## Cost

Prices read 28 Nov 2026.

- **Claude, direct API:** Sonnet 5.5 $2 / $10 per million input / output tokens; Haiku 5.5 $0.10 / $0.50 (prompts up to 100,000 tokens) [N1].
- **Claude, Bedrock:**
  - Regional/geo endpoints, which the EU profile is, "include a 10 percent premium over global endpoints" [N2][N3][A11].
  - The Bedrock pricing page's Anthropic table didn't render in the copy I read, so Bedrock's **global** price for these models being equal to Anthropic's list price is **unconfirmed**. The figures below assume it.
- **Rerank:**
  - Bedrock: Rerank 3.5 costs $2.00 per 1,000 queries; one query holds up to 100 document chunks [A8].
  - Cohere's own price for `rerank-v5.0-fast` was not on any official page I could read (**unconfirmed**). The plan's estimate is about $2 per 1,000 searches (`docs/PLAN.md`, Running costs). Cohere counts one search as one query with up to 100 documents [C1].
- **Embed:** Bedrock's on-demand price for Embed Multilingual v3 didn't render on the pricing page (**unconfirmed**). Third-party calculators list the Cohere direct price at about $0.10 per million tokens (**unconfirmed**). At our volume this line is a few cents either way.

### Volume assumptions

These are my assumptions, not measurements. They are tuned so the launch case lands inside the plan's "$10–30 Claude" line. M6-3's cost-per-conversation metric should replace them.

- Launch: **10 chats a day** (300 a month, the plan's "a few hundred"). Busy: **50 a day** (1,500 a month).
- **4 turns per chat**; **1 search per turn** (1 query embedding + 1 rerank of 30 candidates = 1 rerank query).
- Per turn: **2 Sonnet calls** (answer + after a tool), each ~4,000 input and ~400 output tokens. No prompt caching counted, which would lower both columns.
- Per turn: **2 Haiku calls** (parser + understanding/grading), each ~1,200 input and ~100 output tokens.
- Nightly: **50 changed listings** re-extracted (1,500 a month), each ~2,500 input and ~1,500 output tokens (thinking + JSON), and re-embedded at ~200 tokens each.
- Query embedding: ~20 tokens per search.

### Monthly cost, direct vs Bedrock (USD)

| Line | Launch, direct | Launch, Bedrock EU | Busy, direct | Busy, Bedrock EU |
| --- | ---: | ---: | ---: | ---: |
| Sonnet 5.5 agent (9.6M in / 0.96M out at launch; ×5 busy) | 28.80 | 31.68 | 144.00 | 158.40 |
| Haiku 5.5 live (2.88M in / 0.24M out; ×5 busy) | 0.41 | 0.45 | 2.04 | 2.24 |
| Haiku 5.5 nightly extraction (3.75M in / 2.25M out) | 1.50 | 1.65 | 1.50 | 1.65 |
| Rerank (1,200 / 6,000 queries) | ~2.40 (v5.0-fast, plan estimate, unconfirmed) | 2.40 (Rerank 3.5) | ~12.00 (unconfirmed) | 12.00 |
| Embed (~0.32M / ~0.42M tokens) | ~0.03 (unconfirmed) | unconfirmed, likely cents | ~0.04 (unconfirmed) | unconfirmed, likely cents |
| **Total** | **~33.14** | **~36.21** | **~159.58** | **~174.33** |
| **Delta** | | **+~3.07 (+9%)** | | **+~14.75 (+9%)** |

One-off costs:

- A full re-extraction of ~2,600 listings: about $2.60 direct, $2.86 on Bedrock.
- A full re-embed: products, knowledge chunks and manual products, about 2,800 texts × 200 tokens = 0.56M tokens. That's cents at the third-party price (unconfirmed).
- The AWS line (Fargate, RDS, ALB) doesn't change: Bedrock is called over the public endpoint or a VPC endpoint. A VPC interface endpoint would add its own hourly cost (not priced here).

## How the eval would run (next step after the decision)

Not built in this round. When Tomislav decides to try Bedrock, the follow-up issue would:

1. **Rerank comparison.**
   - Add a Bedrock reranker behind the existing `Reranker` interface. Then extend `scripts/eval_retrieve.py --rerank-models` to accept a provider prefix, e.g. `--rerank-models rerank-v5.0-fast,bedrock:cohere.rerank-v3-5:0`. The script already compares rerank models this way (see `evals/baselines.md`, the v4.0-fast vs v5.0-fast run).
   - Score `evals/retrieval.jsonl` with the same metrics: recall@5, recall@10, MRR, forbidden hit rate, and the #102 cases (`ISSUE_102_CASES`, `case_status`).
   - Add the **relevance-score distribution**: the best score per on-topic question vs per off-topic question. That is the evidence for a new `RELEVANCE_CUTOFF`. On v5.0-fast, on-topic best scores were 0.71–0.79 and the off-topic `seed-10` got 0.004 (`evals/baselines.md`).
   - Time each stage (p50/p95 rerank and total) with live settings. Run it once from a laptop and, if possible, once from an ECS task in eu-central-1, since the Dubrovnik–Frankfurt hop isn't production latency.
2. **Embedding comparison (optional second mode).**
   - Add `bedrock:cohere.embed-multilingual-v3` as a model in `scripts/compare_embeddings.py`, with vectors cached in `data/embedding-cache/` as today, and compare recall and MRR against the current Cohere vectors.
   - Also embed ~20 fixed texts through both providers and report the cosine similarity of each pair. If every pair is ~1.0, the stored vectors can stay. Otherwise, treat Bedrock as a **new embedding model name** so `embedding_hash` changes and `martom embed` re-embeds everything; search only compares vectors of the configured model.
3. **Secrets and tests.**
   - Credentials come only from the runner's AWS profile or environment when Tomislav runs it, never from the repo.
   - Tests use `botocore.stub.Stubber` or fake clients, the same as the fake Cohere clients today. No network in CI.
4. **Recording the numbers.** Tomislav adds the numbers to `evals/baselines.md` (date, commit, catalogue, mode, metrics, weak cases).

## Open risks

- **Latency (unconfirmed, must be measured).**
  - AWS publishes no latency for EU geo routing. A Frankfurt request may be served in Stockholm, Spain or Ireland [A1].
  - Our live budget is tight: Haiku's parse was p50 1.33 s / p95 1.60 s against a 2.5 s limit, and the M3-4 target is p95 retrieval < 2.5 s (`evals/baselines.md`).
  - Rerank and embed run in Frankfurt itself, next to ECS, which may come out faster than today's calls across the Atlantic. That is unmeasured.
- **Quotas on a new account.**
  - Bedrock enforces per-model cross-region tokens-per-minute quotas, a per-account "Cross-Model Max Tokens Per Day", and for some models requests per minute [A14].
  - Default quotas "might be updated depending on regional factors, payment history, fraudulent usage" [A6], so a new account may start low.
  - Output tokens count against the TPM quota at a model-specific burndown rate, and `max_tokens` affects the deduction [A14]. Extraction asks for `MAX_TOKENS` 16,000 four times in parallel.
  - Check the Service Quotas console in eu-central-1 before launch and request increases early (exact default values **unconfirmed**).
- **Model version lag and lifecycle.**
  - Haiku 5.5 is served through "inference profiles with limited model selection … new models or model versions might not be released for them" [A1]. A future Haiku may not reach the EU profile.
  - Embed Multilingual v3 on Bedrock is a 2023 model under the pre-Sept-2026 lifecycle policy. Its card says "EOL no sooner than: Nov 2, 2024", which has passed, though the model is still "Active" [A6].
  - Cohere's own site now leads with Embed 5 [C1]. A forced embedding migration later is possible (**unconfirmed** timing).
  - Anthropic says Claude Platform on AWS "typically" gets features the same day [N3], which implies Bedrock may not.
- **Rerank model downgrade.**
  - Rerank 3.5 (Dec 2024) replaces `rerank-v5.0-fast`. Scores aren't comparable across models: Cohere says only that Rerank 5's scores are consistent across queries [C5].
  - `RELEVANCE_CUTOFF`, the "nothing relevant" path and the #102 forbidden-hit checks must be re-verified on 3.5.
  - Retrieval quality on Bosnian may differ either way. Only the eval can tell.
- **Conversation threads can't switch provider mid-thread.**
  - On Bedrock, thinking blocks for Haiku 5.5 and Sonnet 5.5 "are also bound to the AWS account that produced them". Replaying a changed earlier turn returns a 400 [A1][A2].
  - Our Postgres checkpointer keeps earlier turns, including Sonnet's thinking. My inference: threads started on the direct API should not continue on Bedrock, and the reverse too. A switch should start new threads, or drop thinking blocks from replayed history (**unconfirmed** whether the API accepts that).
- **Two vendors' terms instead of one.** The Bedrock EULAs for each model apply [A12], plus the AWS DPA. Which legal basis covers the transfer is out of scope here (the Privatnost rewrite is a separate issue).

## Sources

All read on **28 Nov 2026**.

AWS:

- [A1] Claude Haiku 5.5 model card — https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-5-5.html
- [A2] Claude Sonnet 5.5 model card — https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5-5.html
- [A3] Introducing Claude Haiku 5.5 on AWS (AWS ML blog) — https://aws.amazon.com/blogs/machine-learning/introducing-claude-haiku-5-5-on-aws/
- [A4] Supported Regions and models for reranking — https://docs.aws.amazon.com/bedrock/latest/userguide/rerank-supported.html
- [A5] Cohere Rerank 3.5 model card — https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-cohere-rerank-3-5.html
- [A6] Cohere Embed Multilingual model card — https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-cohere-embed-multilingual.html
- [A7] Cohere Embed v3 request and response — https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v3.html
- [A8] Amazon Bedrock pricing (Cohere section; the Anthropic and Cohere Embed tables didn't render in the copy read) — https://aws.amazon.com/bedrock/pricing/
- [A9] Unlocking AI flexibility in Europe: cross-region inference for EU data processing (AWS ML blog) — https://aws.amazon.com/blogs/machine-learning/unlocking-ai-flexibility-in-europe-a-guide-to-cross-region-inference-for-eu-data-processing-and-model-access/
- [A10] How do I restrict Amazon Bedrock cross-Region inference routing to specific Regions? (AWS re:Post Knowledge Center) — https://repost.aws/knowledge-center/bedrock-cross-region-inference-routing
- [A11] Global cross-Region inference ("approximately 10% savings … compared to geographic") — https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html
- [A12] Data protection and responsible use of Amazon Bedrock (AWS Alps blog: "doesn't store or log your prompts and completions") — https://aws.amazon.com/blogs/alps/security_bedrock/
- [A13] Does Amazon Bedrock share input and output data with model providers? (AWS re:Post Knowledge Center) — https://repost.aws/knowledge-center/amazon-bedrock-model-data-use
- [A14] Quotas for the bedrock-runtime endpoint — https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-runtime.html

Anthropic:

- [N1] Pricing — https://platform.claude.com/docs/en/about-claude/pricing
- [N2] Pricing, "Regional and multi-region endpoint pricing for Claude 4.5 models and beyond" (same page as N1)
- [N3] Claude on Amazon Bedrock (`bedrock-runtime`) — https://platform.claude.com/docs/en/build-with-claude/claude-on-amazon-bedrock-legacy
- [N4] Claude in Amazon Bedrock (`bedrock-mantle`), page description — https://platform.claude.com/docs/en/build-with-claude/claude-in-amazon-bedrock

Cohere:

- [C1] Pricing (search unit definition: "one query with up to 100 documents"; no rerank or embed prices found) — https://cohere.com/pricing
- [C2] Amazon Bedrock (models on Bedrock: Embed English/Multilingual v3, Rerank v3.5; `BedrockClientV2`) — https://docs.cohere.com/docs/amazon-bedrock
- [C3] Security — https://cohere.com/security
- [C4] Data usage policy — https://cohere.com/data-usage-policy
- [C5] Rerank product page (Rerank 5 score consistency) — https://cohere.com/rerank; model list at https://docs.cohere.com/docs/rerank-overview

LangChain:

- [L1] `ChatBedrockConverse` API reference — https://reference.langchain.com/python/langchain-aws/langchain_aws/chat_models/bedrock_converse/ChatBedrockConverse
- [L2] AWS integrations (`ChatAnthropicMantle`, `ChatBedrockConverse`, `BedrockEmbeddings`) — https://docs.langchain.com/oss/python/integrations/providers/aws
- [L3] `BedrockRerank` API reference — https://reference.langchain.com/python/langchain-aws/langchain_aws/document_compressors/rerank/BedrockRerank

Third-party figures (Cohere direct prices) came from cost aggregators found by web search. They are not cited as facts and are marked unconfirmed wherever used.
