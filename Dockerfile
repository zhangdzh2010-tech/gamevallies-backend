FROM node:20-alpine AS builder
ARG SERVICE=user-service
WORKDIR /app
RUN apk add --no-cache openssl
COPY package.json package-lock.json tsconfig.base.json ./
COPY packages ./packages
COPY contracts ./contracts
COPY prisma ./prisma
RUN npm ci --no-audit --no-fund
RUN npx prisma generate
RUN npm run build --workspace=packages/${SERVICE}
RUN npm prune --omit=dev --no-audit --no-fund

FROM node:20-alpine
ARG SERVICE=user-service
ARG PORT=3001
RUN apk add --no-cache openssl
WORKDIR /app
COPY --from=builder /app/node_modules ./node_modules
# Workspace symlinks must retain their targets; source is not used as an entrypoint.
COPY --from=builder /app/packages ./packages
COPY --from=builder /app/contracts ./contracts
COPY --from=builder /app/prisma ./prisma
COPY deploy/fc/internal-auth.cjs /app/fc-internal-auth.cjs
ENV NODE_ENV=production
ENV PORT=${PORT}
WORKDIR /app/packages/${SERVICE}
EXPOSE ${PORT}
CMD ["node", "dist/main.js"]
