# syntax=docker/dockerfile:1
#
# Artwork of the Day — ONE self-contained image.
# Bundles the Flask app (served by Waitress) + the Ollama server + a local LLM,
# so the container needs NO external API and incurs NO per-token cost. The model
# is baked in at build time, so the container also runs fully offline.
#
#   docker build -t artwork-of-the-day .
#   docker run --rm -p 5000:5000 artwork-of-the-day        # add --gpus all if available
#   # open http://localhost:5000
#
# Bake a different model:
#   docker build --build-arg OLLAMA_MODEL=gemma3 -t artwork-of-the-day .
#
# See DOCKER.md for image size, GPU, RAM, and custom-model notes.

FROM ollama/ollama:latest

# --- Python runtime for the Flask app (base image is Ubuntu) ---------------- #
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv curl \
 && rm -rf /var/lib/apt/lists/*

# --- Python dependencies (isolated venv; Ubuntu marks system env managed) ---- #
WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
ENV PATH="/opt/venv/bin:$PATH"

# --- App code --------------------------------------------------------------- #
COPY . .
RUN chmod +x /app/docker-entrypoint.sh

# --- Bake the local model into the image (self-contained, offline-capable) -- #
# This pulls OLLAMA_MODEL from the Ollama registry during the build. If your
# model is a CUSTOM local model (not in the registry), override the arg with a
# registry model or import yours first — see DOCKER.md.
ARG OLLAMA_MODEL=gemma4:latest
RUN ollama serve >/tmp/ollama-build.log 2>&1 & \
    until curl -sf http://localhost:11434/api/tags >/dev/null; do sleep 1; done; \
    echo "Baking model ${OLLAMA_MODEL} into the image..."; \
    ollama pull "${OLLAMA_MODEL}"

# --- Runtime configuration -------------------------------------------------- #
# Talk to the in-container Ollama; no API key, no cost. The real OPENAI key is
# never baked in (.dockerignore excludes .env).
ENV AOTD_LLM_BACKEND=ollama \
    AOTD_OLLAMA_HOST=http://localhost:11434 \
    AOTD_OLLAMA_MODEL=${OLLAMA_MODEL} \
    AOTD_OLLAMA_TIMEOUT=600 \
    PORT=5000

EXPOSE 5000

# The base image's ENTRYPOINT is `ollama`; override to run BOTH the Ollama
# server and the web app in this single container.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
