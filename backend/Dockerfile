FROM python:3.12-slim

# Install ProjectDiscovery httpx (HTTP toolkit CLI, https://github.com/projectdiscovery/httpx).
# Pinned to v1.6.10 for reproducibility — NOT the Python httpx library.
# linux/amd64 prebuilt release binary; no Go toolchain required.
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip \
    && curl -sSL -o /tmp/httpx.zip \
       https://github.com/projectdiscovery/httpx/releases/download/v1.6.10/httpx_1.6.10_linux_amd64.zip \
    && unzip -o /tmp/httpx.zip -d /usr/local/bin httpx \
    && chmod +x /usr/local/bin/httpx \
    && rm /tmp/httpx.zip \
    && apt-get purge -y curl unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen

COPY . .

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
