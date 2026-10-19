# Local MLX compatibility check — LFM2.5-8B-A1B

Checked on **2026-10-19**. This is a small protocol/controller check, not a
maximum-context or mathematical-quality benchmark.

## Revisions and settings

- Upstream base: `5459397711dfa944f5f924deb8a7b95ed88c1caa` (Square 0.5.1).
- Tested implementation: `da32249462fe7375b24fd804e526fda013ba69c0`.
- Model: [`LiquidAI/LFM2.5-8B-A1B-MLX-4bit`](https://huggingface.co/LiquidAI/LFM2.5-8B-A1B-MLX-4bit),
  revision `2e92b640a63d47ad4dcf81a19a366b902356b3bc`.
- Checkpoint: unchanged single-file 4-bit weights; about 4.83 GB on disk,
  advertised native context 128,000 tokens.
- Machine: Apple M1 Max, 64 GiB unified memory, macOS 27.0.1 arm64.
- Server: separately running Local LLM Lab router/workers using MLX-LM 0.32.0,
  MLX 0.32.4, Python 3.12.15, Transformers 5.19.0, Tokenizers 0.23.3.
  The tokenizer/context extensions described in the setup guide are supplied
  by Local LLM Lab, not by the universal OpenAI API.
- Model worker: decode concurrency 2, prompt concurrency 1, prefill step 512.
  One model was explicitly loaded; automatic loading remained disabled.
- Requests: context 32,768, temperature 0.6. Protocol output limits were 1,024
  tokens, except reasoning (4,096) and the deliberate one-token cap check.
- Proof: solve 8,192; review 4,096; at most two attempts, 32,768 cumulative
  generated tokens and 300 seconds. Sampling top-p was 0.95; no seed was used.

Implementation fingerprints (SHA256):

| File | Hash |
| --- | --- |
| `mathagent/api_profiles.py` | `e33accfc0cc70df54448f2159aacf44301782f4f5a24b3b0d7a44678c7fd00d5` |
| `mathagent/cli.py` | `e9ef9e41314fe4aa70c660d3a6b9f90fcefdd6053090506d8771e1592d397de7` |
| `mathagent/gui/hub.py` | `9eaa703075b93871a936db961dc335fdeb314878b5395dceb099e58c2464bbff` |
| `mathagent/proof.py` | `13d423e03df1aa1f11a13b9dc7053249cd6ab9606ab386b03af4db5efce5f9f7` |

## Observed results

All seven checks in `scripts/test-local-mlx.py` passed: model discovery and exact
token counting; ordinary streaming; separate reasoning plus a final answer;
one tool call followed by its actual fresh result; prompted JSON; output-cap
handling; and two simultaneous requests.

| Check | Output tokens, including thinking | Elapsed |
| --- | ---: | ---: |
| Arithmetic, thinking requested off | 71 | 0.93 s |
| Arithmetic, thinking requested on | 130 | 1.30 s |
| Prompted JSON | 242 | 2.46 s |
| Two concurrent short responses | 280 combined | 2.28 s |
| Complete proof and model review | 2,595 | 25.95 s |
| Injected-error review, repair and re-review | 2,981 real model tokens | 31.18 s |

Elapsed figures include request and prompt processing. They are not isolated
decoder throughput measurements. MLX allocations were about 4.83 GB immediately
after loading, 5.28 GB after these checks, and peaked at about 5.76 GB.

### Real proof

Goal: **Prove that n(n+1) is even for every integer n.**

The real solver returned a complete proof by the two parity cases (1,789 output
tokens, 17.22 s). A fresh real verifier returned valid JSON with `no_issue_found`
(806 tokens, 8.59 s). The controller finished with `candidate_complete` and
retained the solver's answer unchanged.

### Repair with an explicitly injected error

The initial candidate below was **injected test data, not model-generated**:

> Let n be any integer. Since n is even, write n=2k. Then n(n+1)=2k(2k+1)
> is even. This proves the assertion for every integer n.

A wrapper supplied this first completed solver event with zero generated/input
tokens; subsequent requests used the real MLX client. The real verifier returned
`issues_found` for the unsupported assumption that every integer is even. The
real repair added the missing odd case. A second real verifier returned valid
JSON with `no_issue_found`. The controller finished with `candidate_complete`
after two attempts. This tests the repair path, not the chance of a model
spontaneously making that error.

### GUI and saved chat

The real GUI was launched with `--backend openai --api-profile mlx`. An
authenticated ordinary chat answered 19+23 with 42, saved its transcript, and
preserved it exactly across GUI restart. This checks HTTP integration and chat
persistence; visual browser inspection was not performed.

## Regression suite

`python -m unittest discover -s tests -v` ran **689 tests** on the tested
implementation: **686 passed, two skipped** (optional `latexmk` was unavailable),
and **one existing GUI concurrency race failed**. All 12 new API-profile tests
and the new saved-profile/resume test passed, as did the existing backend tests.

The failing test is
`test_gui_concurrency.GuiConcurrencyTests.test_two_assistant_turns_preserve_separate_full_budgets_and_usage`:
it can observe Assistant status `complete` before the assistant transcript entry
is committed. The identical failure was reproduced **8 times in 20 runs** against
untouched upstream revision `5459397711dfa944f5f924deb8a7b95ed88c1caa`.
This integration does not change that Ollama execution path or weaken the test.
The complete suite is therefore not reported as green.

Tests ran in an isolated checkout with sibling temporary storage, both contained
under the local project. This respects the existing deployment tests' requirement
that synthetic weight-download records live outside the tested checkout.

## Limits

- This checkpoint reasons even when `enable_thinking=false` is supplied.
- JSON is requested through the prompt and validated afterward; no grammar or
  schema-constrained decoding is provided by this profile.
- Explicit seeds, reasoning-effort levels and vLLM thinking-budget fractions are
  unsupported with the tested continuous-batching server.
- Neither full 128K context nor sustained multi-request capacity was tested.
  A model verifier finding no issue is not formal proof certification.

The test GUI was stopped and the model explicitly unloaded afterward. The
inference server and its configuration were unchanged by this integration.
