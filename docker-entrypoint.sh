#!/usr/bin/env bash
# Runs BOTH services in the one container: the bundled Ollama server (background)
# and the Flask web app (foreground). The model is baked into the image at build
# time, so no network is needed at run time.
set -euo pipefail

# Start the bundled Ollama server.
ollama serve &

# Wait until it answers before starting the app.
echo "[entrypoint] waiting for Ollama to come up..."
until curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; do sleep 1; done
echo "[entrypoint] Ollama is up."

# The model is baked in; only pull if it's somehow missing (e.g. an empty
# volume was mounted over /root/.ollama). A failed pull is non-fatal — the app
# just serves the deterministic Wikidata summary instead of an AI article.
if ! ollama list | grep -qi "${AOTD_OLLAMA_MODEL%%:*}"; then
  echo "[entrypoint] model ${AOTD_OLLAMA_MODEL} not found; attempting pull..."
  ollama pull "${AOTD_OLLAMA_MODEL}" \
    || echo "[entrypoint] WARN: pull failed; articles will use the deterministic summary."
fi

echo "[entrypoint] starting web app on port ${PORT}..."
exec python3 serve.py
