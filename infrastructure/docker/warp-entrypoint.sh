#!/bin/bash
# Cloudflare WARP in proxy mode for the backend's YouTube downloads (IH v2 teacher ingestion).
# YouTube refuses the VM's datacenter IP ("Sign in to confirm you're not a bot"); traffic sent
# through WARP leaves from Cloudflare's network instead. WARP's SOCKS5 proxy only listens on
# 127.0.0.1:40000, so socat re-exposes it on :1080 for the compose network (no host port).
# The registration lives in /var/lib/cloudflare-warp (a named volume) so restarts reuse it.
set -uo pipefail

W="warp-cli --accept-tos"

mkdir -p /run/dbus && rm -f /run/dbus/pid
dbus-daemon --system --fork

warp-svc --accept-tos &
SVC=$!

for _ in $(seq 1 30); do
  $W status >/dev/null 2>&1 && break
  sleep 1
done

$W registration show >/dev/null 2>&1 || $W registration new
$W mode proxy
$W proxy port 40000
$W tunnel protocol set MASQUE || true
$W connect

socat TCP-LISTEN:1080,fork,reuseaddr TCP:127.0.0.1:40000 &
FWD=$!

# Exit when either process dies so `restart: unless-stopped` brings the pair back together.
wait -n "$SVC" "$FWD"
exit 1
