# RiVault MCP Apps Dockerfile
# Multi-stage build for production deployment

# Stage 1: Build
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files
COPY package.json package-lock.json* ./

# Install dependencies (includes tsx for running TypeScript)
RUN npm ci

# Copy source
COPY . .

# Build
RUN npm run build

# Stage 2: Production
FROM node:20-alpine AS production

WORKDIR /app

# Copy built assets
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/package.json ./
COPY --from=builder /app/src/server ./src/server
COPY --from=builder /app/node_modules ./node_modules

# Set environment
ENV NODE_ENV=production
ENV UMRS_BASE_URL=http://umrs:8000

# Expose ports:
#   5173 — Vite dev preview (static serving, optional)
#   8085 — MCP SSE transport (when MCP_TRANSPORT=sse)
EXPOSE 5173
EXPOSE 8085

# Run MCP server by default (stdio or SSE depending on MCP_TRANSPORT env)
CMD ["npx", "tsx", "src/server/index.ts"]
