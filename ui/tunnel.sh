#!/usr/bin/env bash
# Open a tunnel to the siliconboard UI. The box serves 127.0.0.1 only, so this
# ssh key is the whole way in: no public port, no certificate, nothing to rotate.
#
#   bash ui/tunnel.sh              # laptop: open the URL it prints
#   bash ui/tunnel.sh --phone      # also listen on this machine for a phone/tablet on your Wi-Fi
#   PORT=8090 bash ui/tunnel.sh    # when 8080 is taken
set -euo pipefail

PROFILE="${PROFILE:-sai}"
REGION="${REGION:-ap-south-1}"
NAME="${NAME:-siliconboard}"
PORT="${PORT:-8080}"
PHONE=0

for arg in "$@"; do
    case "$arg" in
        --phone) PHONE=1 ;;
        --help|-h) sed -n '2,7p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

# same key hunt as deploy/deploy.sh: the ec2 pair's private half lives in ~/sb-deploy
KEYFILE="${KEYFILE:-}"
if [ -z "$KEYFILE" ]; then
    for candidate in "$HOME/sb-deploy/$NAME" "$HOME/.ssh/$NAME" \
                     "$HOME/sb-deploy/$NAME.pem" "$HOME/.ssh/$NAME.pem"; do
        if [ -f "$candidate" ]; then KEYFILE="$candidate"; break; fi
    done
fi
[ -f "$KEYFILE" ] || { echo "no private key found; KEYFILE=/path/to/key bash ui/tunnel.sh" >&2; exit 1; }

HOST="$(aws --profile "$PROFILE" --region "$REGION" ec2 describe-instances \
    --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text 2>/dev/null || true)"
case "$HOST" in ""|None) echo "no running $NAME box; run deploy/deploy.sh first" >&2; exit 1 ;; esac

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=15 -i "$KEYFILE")

# the ui prints its tokenised url at startup; read it straight from the journal
TOKEN_URL="$(ssh "${SSH_OPTS[@]}" "ubuntu@$HOST" \
    "sudo journalctl -u siliconboard-ui --no-pager 2>/dev/null | grep -o 'http://127.0.0.1:[0-9]*/?t=[A-Za-z0-9_-]*' | tail -1" \
    2>/dev/null || true)"
# rewrite host and port for the local end of the tunnel (PORT may differ from the box's)
OPEN_URL="$(printf '%s' "$TOKEN_URL" | sed -E 's#http://127\.0\.0\.1:[0-9]+#http://localhost:'"$PORT"'#')"

BIND="127.0.0.1"
if [ "$PHONE" = 1 ]; then
    BIND="0.0.0.0"
    LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
    if [ -n "$LAN_IP" ]; then
        echo "phone/tablet on this Wi-Fi: http://$LAN_IP:$PORT/?t=<token>"
    else
        echo "no lan ip found; use http://<this-machine-ip>:$PORT/?t=<token>"
    fi
fi

echo "tunnel: local :$PORT -> box loopback (ctrl-c stops; the box never opens a public port)"
if [ -n "$OPEN_URL" ]; then
    echo "open:    $OPEN_URL"
else
    echo "open:    http://localhost:$PORT/  (token url: ssh sudo journalctl -u siliconboard-ui)"
fi

exec ssh "${SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
    -L "$BIND:$PORT:127.0.0.1:$PORT" "ubuntu@$HOST"
