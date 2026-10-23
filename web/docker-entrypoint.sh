#!/bin/sh
set -e

ip="${PUBLIC_IP:-47.253.82.114}"
mkdir -p /etc/nginx/certs
if [ ! -f /etc/nginx/certs/server.crt ] || [ ! -f /etc/nginx/certs/server.key ]; then
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout /etc/nginx/certs/server.key \
    -out /etc/nginx/certs/server.crt \
    -days 3650 \
    -subj "/CN=${ip}" \
    -addext "subjectAltName=IP:${ip}"
fi

exec /docker-entrypoint.sh nginx -g "daemon off;"
