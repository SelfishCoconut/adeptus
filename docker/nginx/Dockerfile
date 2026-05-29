# Edge image: builds the SPA, then serves it over TLS via Nginx.
# Build context is the repo root (see docker-compose.yml) so we can reach frontend/.

# --- Stage 1: build the SPA ---------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# --- Stage 2: Nginx + bundled self-signed cert --------------------------------
FROM nginx:1.27-alpine

# Generate a self-signed cert at BUILD time so HTTPS works out of the box with no
# private key committed to the repo. This is the dev default; replacing it with a
# real cert (and documenting the swap) is Slice 39.
RUN apk add --no-cache --virtual .certgen openssl \
    && mkdir -p /etc/nginx/certs \
    && openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
        -keyout /etc/nginx/certs/adeptus.key \
        -out    /etc/nginx/certs/adeptus.crt \
        -subj   "/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
    && apk del .certgen

COPY docker/nginx/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80 443
