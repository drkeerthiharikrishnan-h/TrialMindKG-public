# NVIDIA hosted NIM pre-flight, 2026-11-04 21:29 IST

Account key from build.nvidia.com (`NVIDIA_API_KEY`). One tiny call per model: chat calls ask for a JSON reply under a server-enforced schema (`response_format`, thinking off), temperature 0.
`GET /v1/models` lists 85 models, 20 of them Nemotron. **Listed is not the same as usable:** 3 of 5 listed backups below return 404 for this account.

| use | model | in catalogue | HTTP | seconds | valid JSON / result |
|---|---|---|---|---|---|
| verifier, judge | `nvidia/nemotron-3.5-lightning-30b-a3b` | True | 200 | 0.6 | True |
| extractor, querier, specialist, baseline, repair, namer, biomarker, protocol | `nvidia/nemotron-3.5-super-vl-120b-a12b` | True | 200 | 0.5 | True |
| embedding | `nvidia/nemotron-3-embed-1b` | True | 200 | 0.2 | 2048-dim vector |
| reranker (resolver) | `nvidia/llama-nemotron-rerank-vl-1b-v2` | - | 200 | 0.4 | ranked: [{'index': 0, 'logit': -0.45654296875}, {'index': 1, 'logit': -5.48046875}] |

Backups (not configured; for a quick swap if a configured model disappears):

| model | HTTP | seconds | valid JSON |
|---|---|---|---|
| `nvidia/nemotron-3-super-120b-a12b` | 404 | 0.6 | None |
| `nvidia/nemotron-3-ultra-550b-a55b` | 200 | 0.8 | True |
| `nvidia/nemotron-nano-3-30b-a3b` | 404 | 0.5 | None |
| `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | 200 | 0.6 | True |
| `nvidia/llama-3.1-nemotron-70b-instruct` | 404 | 0.1 | None |

Consequence for the app: the model check must make a real call per model (not only read `/v1/models`), and fall back to the next working model, then to the labelled offline stand-in. Run again before every demo and on judging day.
