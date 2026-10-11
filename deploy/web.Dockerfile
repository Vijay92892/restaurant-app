# The public front door: Caddy (TLS, reverse proxy) serving the customer ordering page.
# Build context: the repository root (see the root .dockerignore).

FROM node:22-bookworm-slim AS page
WORKDIR /web
COPY customer-web/package.json customer-web/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund --fetch-timeout=120000 --fetch-retries=5
COPY customer-web/ ./
RUN npm run build

FROM caddy:2
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=page /web/dist /srv/customer
