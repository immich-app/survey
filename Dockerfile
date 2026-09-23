FROM node:24-slim@sha256:2fe369e969550cde8e867afc3fe370b260140cab4a23d467074295b42163d553 AS frontend-builder
# pnpm version is pinned via the root package.json "packageManager" field
ENV COREPACK_ENABLE_STRICT=1
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY backend/package.json backend/
RUN pnpm install --frozen-lockfile --ignore-scripts --filter survey...
COPY . .
RUN pnpm run build:frontend

FROM node:24-slim@sha256:2fe369e969550cde8e867afc3fe370b260140cab4a23d467074295b42163d553 AS backend-builder
ENV COREPACK_ENABLE_STRICT=1
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY backend/package.json backend/
RUN pnpm install --frozen-lockfile --filter survey-backend...
COPY shared/ shared/
COPY backend/ backend/
RUN cd backend && pnpm run build:node

FROM node:24-slim@sha256:2fe369e969550cde8e867afc3fe370b260140cab4a23d467074295b42163d553
ENV COREPACK_ENABLE_STRICT=1
RUN corepack enable
WORKDIR /app

COPY --from=frontend-builder /app/build /app/public

COPY --from=backend-builder /app/backend/dist /app/dist
COPY --from=backend-builder /app/backend/migrations /app/migrations
COPY --from=backend-builder /app/backend/package.json /app/
RUN pnpm install --prod

ENV PORT=3000
ENV STATIC_DIR=/app/public
ENV DATABASE_URL=/data/survey.db

EXPOSE 3000
VOLUME /data

CMD ["node", "dist/server.js"]
