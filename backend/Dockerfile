FROM python:3.12-slim

# Pentest tool binaries:
#  - nmap (apt, runtime dep — installed at /usr/bin/nmap, KEPT) for the nmap MCP server.
#  - ProjectDiscovery httpx (HTTP toolkit CLI, https://github.com/projectdiscovery/httpx),
#    pinned to v1.6.10 — NOT the Python httpx library; linux/amd64 prebuilt binary.
# curl + unzip are build-only deps used to fetch httpx and are purged afterwards;
# nmap is a manual install so apt-get autoremove leaves it (and its deps) in place.
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip nmap \
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
