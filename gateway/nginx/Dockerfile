# =============================================================================
# R4 Platform Gateway - NGINX Image
# =============================================================================
# Optimized NGINX configuration for heavy data traffic.
# =============================================================================

FROM docker.io/nginx:stable-alpine-slim

LABEL maintainer="R4 Platform Team"
LABEL description="High-performance gateway for R4 Platform"

# Install envsubst for environment variable substitution
RUN apk add --no-cache gettext

# Create cache and server-scoped extension directories
RUN mkdir -p /var/cache/nginx/proxy_temp /data/files /etc/nginx/app.d && \
    chown -R nginx:nginx /var/cache/nginx /data

# Copy configuration
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/nginx.conf.template
COPY conf.d/ /etc/nginx/conf.d/

# Environment variables with defaults
ENV LITELLM_HOST=litellm
ENV LITELLM_PORT=11434
ENV UMRS_HOST=umrs
ENV UMRS_PORT=8000
ENV GATEWAY_PORT=8080
ENV A2L_HOST=a2l
ENV A2L_PORT=8002
ENV SIDECAR_HOST=127.0.0.1
ENV SIDECAR_PORT=8081

# Substitute environment variables and start NGINX
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD sh -c 'wget -q --spider "http://localhost:${GATEWAY_PORT:-8080}/health" || exit 1'

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
