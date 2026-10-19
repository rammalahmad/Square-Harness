# Connecting to a local MLX-compatible server

Square hosts the harness and its interface. A separate process hosts the model.
This profile connects to an already running OpenAI-compatible MLX endpoint; it
does not install a server, download weights or start/load a model.

## Install and connect

From a checkout, on Python 3.10 or later:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
export MODEL_ID='your-served-model-id'
mkdir -p workspace
square-harness --backend openai --api-profile mlx \
  --host http://localhost:8000 --model "$MODEL_ID" \
  --workspace ./workspace --offline --ctx 32768 --predict 8192 --gui
```

Use your server's URL and exact model ID. If another app occupies Square's GUI
port, add `--gui-port 8780` or any available port. This is independent of the
model server port. `--api-profile default` retains the existing vLLM dialect.
`SQUARE_OPENAI_PROFILE=mlx` is also accepted; the CLI option takes precedence.
Ollama and llama.cpp retain their existing separate backends.

For a terminal proof instead of the GUI, replace `--gui` with:

```bash
--proof-solve-tokens 8192 --proof-verify-tokens 4096 \
--proof-rounds 2 --proof-tokens 32768 --proof-seconds 300 \
--prompt 'Prove that n(n+1) is even for every integer n.'
```

These are example test budgets. Context/output ceilings should match your
checkpoint, machine and task; proof and ordinary-chat budgets are independent.
Resume a paused proof with the same endpoint and API profile using `--resume ID`.
The saved profile and profile implementation hash participate in provenance
checks; changing the transport cannot silently resume an old call.

## API contract and limitations

- `/v1/models` and streaming `/v1/chat/completions`, including reported token
  usage, are required. Streaming/tool/reasoning parsing reuses Square's existing
  OpenAI adapter. Tool availability also depends on the checkpoint/template and
  the server's tool parser.
- `chat_template_kwargs.enable_thinking` is sent; vLLM's `preserve_thinking` is
  omitted. The checkpoint can ignore the thinking toggle. The LFM2.5 checkpoint
  tested here always reasons, even when thinking is requested off.
- Output limits are sent as `max_completion_tokens` and include reasoning.
  Explicit seeds, `reasoning_effort` levels and the two
  `SQUARE_OPENAI_*THINKING_FRACTION` vLLM policies are unsupported in this profile.
- JSON/schema requests are expressed as an additional user instruction. No
  `response_format` or `structured_outputs` is advertised to the server.
  Square's strict verifier validation still decides whether a review is usable;
  malformed JSON remains an operational review failure, not a proof verdict.
- **Local LLM Lab extensions:** `context_limit` forwards `--ctx` on generation;
  optional `/api/tokenize` takes `model`, `messages`, `thinking` and optional
  `tools`, returning integer `prompt_tokens` and `max_context_tokens`. These are
  not universal OpenAI or MLX endpoints. Other servers may ignore the context
  field and require context configuration at startup. HTTP 404/405 disables
  exact tokenization and uses Square's existing conservative input estimate;
  malformed successful replies and other failures remain errors.

## Reproduce protocol checks

Run only against a model you have explicitly loaded. The script sends small
synthetic tasks and two simultaneous requests. It never starts/stops a server
or loads/unloads models; the selected report path is yours to manage.

```bash
python scripts/test-local-mlx.py --host http://localhost:8000 \
  --model "$MODEL_ID" --report .local/reports/mlx-protocol.json
python -m unittest discover -s tests -v
```

Protocol checks are not a model-quality or maximum-context benchmark. The
versioned compatibility report records the tested checkpoint, server versions,
source hashes, settings and observed proof-workflow outcome.

See the [LFM2.5-8B-A1B results](compatibility/local-mlx-lfm.md).
