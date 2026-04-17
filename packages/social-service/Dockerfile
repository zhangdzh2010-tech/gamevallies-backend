FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
COPY packages/social-service ./packages/social-service
COPY packages/platform-config ./packages/platform-config
COPY tsconfig.base.json ./
COPY contracts ./contracts
COPY prisma ./prisma
RUN npm install --legacy-peer-deps
RUN npx prisma generate
WORKDIR /app/packages/social-service
RUN npx nest build

FROM node:20-alpine
RUN apk add --no-cache openssl
WORKDIR /app
COPY package.json package-lock.json* ./
COPY packages/social-service/package.json ./packages/social-service/
COPY packages/platform-config ./packages/platform-config
COPY contracts ./contracts
RUN npm install --omit=dev --legacy-peer-deps
COPY --from=builder /app/packages/social-service/dist ./packages/social-service/dist
COPY --from=builder /app/node_modules/.prisma ./node_modules/.prisma
COPY prisma ./prisma
WORKDIR /app/packages/social-service
EXPOSE 3003
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
  CMD node -e "require('http').get('http://localhost:3003/api/v1/health', (r) => {if (r.statusCode !== 200) throw new Error(r.statusCode)})"
CMD ["node", "dist/main.js"]
