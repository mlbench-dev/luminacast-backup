# Stage 1: Build
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
# Use `npm install --no-audit --no-fund` (not `npm ci`) so the lockfile is
# refreshed in-place when package.json adds new deps without a regenerated
# lockfile commit. This is more forgiving across the merge train and still
# uses the lockfile when it's in sync.
RUN npm install --legacy-peer-deps --no-audit --no-fund

COPY . .
RUN npx vite build

# Stage 2: Serve with nginx
FROM nginx:alpine

# Copy custom nginx config
COPY --from=build /app/dist /usr/share/nginx/html

# SPA with proper cache headers
RUN echo 'server { \
  listen 80; \
  server_name _; \
  root /usr/share/nginx/html; \
  index index.html; \
  \
  # HTML — never cache (browser always fetches latest index.html with new JS hashes) \
  location = / { \
    add_header Cache-Control "no-cache, no-store, must-revalidate"; \
    try_files /index.html =404; \
  } \
  location ~ \.html$ { \
    add_header Cache-Control "no-cache, no-store, must-revalidate"; \
  } \
  \
  # Hashed assets — cache forever (safe: Vite hash changes on rebuild) \
  location /assets/ { \
    add_header Cache-Control "public, max-age=31536000, immutable"; \
    try_files $uri =404; \
  } \
  \
  # Everything else — short cache + SPA fallback \
  location / { \
    add_header Cache-Control "public, max-age=60"; \
    try_files $uri $uri/ /index.html; \
  } \
  \
  location /api { \
    proxy_pass http://orchestrator:8000; \
    proxy_read_timeout 300s; \
    proxy_send_timeout 300s; \
  } \
  location /ws { \
    proxy_pass http://orchestrator:8000; \
    proxy_http_version 1.1; \
    proxy_set_header Upgrade $http_upgrade; \
    proxy_set_header Connection "upgrade"; \
  } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
