FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
COPY packages/feed-service ./packages/feed-service
COPY packages/platform-config ./packages/platform-config
COPY tsconfig.base.json ./
COPY contracts ./contracts
COPY prisma ./prisma
RUN npm install --legacy-peer-deps
RUN npx prisma generate
WORKDIR /app/packages/feed-service
RUN npx nest build

FROM node:20-alpine
RUN apk add --no-cache openssl
WORKDIR /app
COPY package.json package-lock.json* ./
COPY packages/feed-service/package.json ./packages/feed-service/
COPY packages/platform-config ./packages/platform-config
COPY contracts ./contracts
RUN npm install --omit=dev --legacy-peer-deps
COPY --from=builder /app/packages/feed-service/dist ./packages/feed-service/dist
COPY --from=builder /app/node_modules/.prisma ./node_modules/.prisma
COPY prisma ./prisma
WORKDIR /app/packages/feed-service
EXPOSE 3004
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
  CMD node -e "require('http').get('http://localhost:3004/api/v1/health', (r) => {if (r.statusCode !== 200) throw new Error(r.statusCode)})"
CMD ["node", "dist/main.js"]
