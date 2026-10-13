# Build from this folder: docker compose build. The event contract is in contracts/ (no network needed).
FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json ./
COPY scripts scripts
COPY contracts contracts
COPY src src
RUN npm run build && npm prune --omit=dev

FROM node:24-slim
WORKDIR /app
ENV NODE_ENV=production
COPY package.json ./
COPY --from=build /app/node_modules node_modules
COPY --from=build /app/dist dist
COPY contracts contracts
COPY migrations migrations
USER node
CMD ["sh", "-c", "node dist/migrate.js && node dist/index.js"]
