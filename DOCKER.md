# Self-contained Docker image

One image, one container — the Flask web app **and** the Ollama server **and** a
local LLM are all bundled together. The result needs **no external API**, costs
**nothing per request**, and (because the model is baked in at build time) runs
**fully offline**.

```
┌─ container ─────────────────────────────────────────────┐
│  Waitress → Flask app  ──HTTP──▶  Ollama server          │
│  (:5000, public)                  (:11434, in-container) │
│                                   gemma4:latest (baked)  │
└──────────────────────────────────────────────────────────┘
```

## Build & run

```bash
# Build (downloads the model into the image — see "Image size" below)
docker build -t artwork-of-the-day .

# Run (CPU)
docker run --rm -p 5000:5000 artwork-of-the-day

# Run (NVIDIA GPU — much faster; needs the NVIDIA Container Toolkit)
docker run --rm --gpus all -p 5000:5000 artwork-of-the-day
```

Then open <http://localhost:5000>. Or with Compose:

```bash
docker compose up --build          # uncomment the GPU block in docker-compose.yml first if you have one
```

No `.env` is needed or used — the container is hard-wired to the in-container
Ollama (`AOTD_LLM_BACKEND=ollama`). Your real OpenAI key is **never** baked in
(`.dockerignore` excludes `.env`).

## Choosing the model

The model is a build argument, baked into the image:

```bash
docker build --build-arg OLLAMA_MODEL=gemma3 -t artwork-of-the-day .
docker build --build-arg OLLAMA_MODEL=qwen3.6:27b -t artwork-of-the-day .   # bigger, slower, higher quality
```

> **Important — registry vs. custom models.** The build runs `ollama pull
> $OLLAMA_MODEL`, which downloads from the **public Ollama registry**. If
> `gemma4:latest` is a *custom* model you created locally (not in the registry),
> that pull will fail. In that case either:
> - point `OLLAMA_MODEL` at a registry model (e.g. `gemma3`, `qwen2.5`, `llama3.2`), or
> - import your custom model into the build: drop its `Modelfile` (+ any weights)
>   into the build context and replace the pull with
>   `ollama create $OLLAMA_MODEL -f Modelfile`.
>
> Verify what you have with `ollama show gemma4:latest --modelfile` on the host —
> if its `FROM` line is a registry tag, the default build works as-is.

## Image size, RAM, disk

- **Image size:** base (~1.5 GB) + Python deps (~0.2 GB) + the model. With
  `gemma4:latest` (9.6 GB) the image is roughly **11–12 GB**; `qwen3.6:27b`
  (~17 GB) makes it **~19 GB**. The model dominates — that's the price of being
  fully self-contained and offline.
- **RAM:** the model is loaded into memory at first use. Budget at least the
  model's size plus headroom — **~12–16 GB** for `gemma4`, **~24 GB+** for the
  27B. Give Docker Desktop enough memory in its settings.
- **GPU:** strongly recommended. CPU-only inference works but a full article can
  take minutes; on a GPU it's tens of seconds.

## First request is slow

The first article generation loads the model into memory (cold start), which can
take a minute or two; subsequent ones are much faster. Results are cached per day
per artwork, so each artwork generates only once. Until then (and any time the
model is unavailable) the app still works — it serves the grounded, deterministic
Wikidata summary, so the site is never down waiting on the model.

## How it works

`docker-entrypoint.sh` starts `ollama serve` in the background, waits for it,
ensures the model is present (it's baked in; it only pulls as a fallback), then
runs the web app in the foreground via `serve.py` (Waitress on `0.0.0.0:$PORT`).
The app reaches Ollama at `http://localhost:11434` — same container — through the
OpenAI-compatible endpoint, so the entire grounded-generation + verification
pipeline is identical to the hosted-API path.

## Two containers instead of one?

This image is intentionally a single container per your "self-contained"
requirement. The more idiomatic alternative is two services (app + an
`ollama/ollama` container) sharing a network via Compose, with the model in a
named volume. That keeps the app image small and lets the model cache persist
across rebuilds, at the cost of not being a single portable artifact. Say the
word and I can add that variant alongside this one.
