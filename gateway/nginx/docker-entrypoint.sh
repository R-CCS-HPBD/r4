#!/bin/sh
# =============================================================================
# NGINX Gateway Entrypoint
# =============================================================================
# Substitutes environment variables in nginx.conf before starting.
# =============================================================================

set -e

# Substitute environment variables in nginx.conf
envsubst '${LITELLM_HOST} ${LITELLM_PORT} ${UMRS_HOST} ${UMRS_PORT} ${A2L_HOST} ${A2L_PORT} ${SIDECAR_HOST} ${SIDECAR_PORT}' \
    < /etc/nginx/nginx.conf.template \
    > /etc/nginx/nginx.conf

envsubst '${GATEWAY_PORT}' \
    < /etc/nginx/conf.d/server.conf \
    > /tmp/server.conf
mv /tmp/server.conf /etc/nginx/conf.d/server.conf

# Validate configuration
nginx -t

# Execute the main command
exec "$@"
