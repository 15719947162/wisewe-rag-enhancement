ARG NODE_IMAGE=m.daocloud.io/docker.io/library/node:22-alpine
FROM ${NODE_IMAGE} AS deps

WORKDIR /app

COPY package.json package-lock.json ./

ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV NPM_CONFIG_REGISTRY=${NPM_REGISTRY}

RUN npm ci

FROM ${NODE_IMAGE} AS builder

WORKDIR /app

ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ARG NPM_REGISTRY=https://registry.npmmirror.com

ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NPM_CONFIG_REGISTRY=${NPM_REGISTRY}

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build

FROM ${NODE_IMAGE} AS runner

WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000

ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000

ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL

COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/package-lock.json ./package-lock.json
COPY --from=builder /app/node_modules ./node_modules

EXPOSE 3000

CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
