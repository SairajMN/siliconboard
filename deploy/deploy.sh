#!/usr/bin/env bash
# Deploy siliconboard to one EC2 box, from a laptop, with the aws cli.
#
# Idempotent: safe to run again on an existing box. It also fixes the thing that
# actually breaks this every few days - the client public ip rotates, so the
# security group is re-pinned to whoever is deploying right now.
#
#   bash deploy/deploy.sh                     # update and verify an existing box
#   SKIP_STA=1 bash deploy/deploy.sh          # skip the ~17 min OpenSTA build
#   KEYFILE=~/keys/box.pem bash deploy/deploy.sh   # when the key is not where we look
#   bash deploy/deploy.sh --create            # make the instance first
#   bash deploy/deploy.sh --dry-run           # show what it would do, change nothing
set -euo pipefail

PROFILE="${PROFILE:-sai}"
REGION="${REGION:-ap-south-1}"
NAME="${NAME:-siliconboard}"
TYPE="${TYPE:-t3.micro}"
DISK="${DISK:-20}"
SKIP_STA="${SKIP_STA:-0}"
REPO_URL="${REPO_URL:-https://github.com/SairajMN/siliconboard.git}"
CREATE=0
DRY_RUN=0

for arg in "$@"; do
    case "$arg" in
        --create) CREATE=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --help|-h) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
# The private half of the "$NAME" ec2 key pair. --create only emits a new key
# when the pair is absent, so on an existing box the key already exists on disk
# and we must find it: ~/sb-deploy is where this project keeps it (git-ignored).
# An explicit KEYFILE=... always wins.
KEYFILE="${KEYFILE:-}"
if [ -z "$KEYFILE" ]; then
    for candidate in "$HOME/sb-deploy/$NAME" "$HOME/.ssh/$NAME" \
                     "$HOME/sb-deploy/$NAME.pem" "$HOME/.ssh/$NAME.pem"; do
        if [ -f "$candidate" ]; then KEYFILE="$candidate"; break; fi
    done
fi
KEYFILE="${KEYFILE:-$HOME/sb-deploy/$NAME}"
say() { printf '\n== %s\n' "$*"; }
aws_() { aws --profile "$PROFILE" --region "$REGION" "$@"; }
run() { if [ "$DRY_RUN" = 1 ]; then echo "  would: $*"; else eval "$@"; fi; }

say "identity and region"
aws_ sts get-caller-identity --query '[Account,Arn]' --output text
MYIP="$(curl -s --max-time 15 https://checkip.amazonaws.com | tr -d '\n')"
[ -n "$MYIP" ] || { echo "could not resolve our public ip"; exit 1; }
echo "  client ip $MYIP"

if [ "$CREATE" = 1 ]; then
    say "key pair"
    if aws_ ec2 describe-key-pairs --key-names "$NAME" >/dev/null 2>&1; then
        echo "  $NAME key pair already exists"
    else
        run "mkdir -p \"$(dirname "$KEYFILE")\""
        run "ssh-keygen -t ed25519 -N '' -C '${USER}@${NAME}' -f '$KEYFILE'"
        run "chmod 600 '$KEYFILE'"
        run "aws --profile '$PROFILE' --region '$REGION' ec2 import-key-pair --key-name '$NAME' --public-key-material fileb://'$KEYFILE.pub' >/dev/null"
    fi

    say "security group"
    SG="$(aws_ ec2 describe-security-groups --filters "Name=group-name,Values=$NAME-sg" --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)"
    if [ -z "$SG" ] || [ "$SG" = None ]; then
        VPC="$(aws_ ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
        run "SG=\$(aws --profile '$PROFILE' --region '$REGION' ec2 create-security-group --group-name '$NAME-sg' --description 'siliconboard ssh' --vpc-id '$VPC' --query GroupId --output text)"
        run "aws --profile '$PROFILE' --region '$REGION' ec2 authorize-security-group-ingress --group-id '\$SG' --protocol tcp --port 22 --cidr '$MYIP/32'"
    else
        echo "  security group $SG exists"
    fi

    say "instance"
    AMI="$(aws_ ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query 'Parameter.Value' --output text)"
    echo "  ami $AMI"
    run "aws --profile '$PROFILE' --region '$REGION' ec2 run-instances --image-id '$AMI' --instance-type '$TYPE' --key-name '$NAME' --security-group-ids '$NAME-sg' --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize='$DISK',VolumeType=gp3}' --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value='$NAME'}]' --query 'Instances[0].InstanceId' --output text"
fi

say "find the box"
INSTANCE="$(aws_ ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=running" --query 'Reservations[0].Instances[0].InstanceId' --output text)"
case "$INSTANCE" in ""|None) echo "no running instance tagged $NAME (try --create)"; exit 1 ;; esac
read -r HOST SG < <(aws_ ec2 describe-instances --instance-ids "$INSTANCE" --query 'Reservations[0].Instances[0].[PublicIpAddress,SecurityGroups[0].GroupId]' --output text)
echo "  $INSTANCE at $HOST, sg $SG"

say "pin ssh to this ip"
aws_ ec2 describe-security-group-rules --filters "Name=group-id,Values=$SG" \
    --query 'SecurityGroupRules[?IsEgress==`false` && FromPort==`22`].[SecurityGroupRuleId,CidrIpv4]' --output text \
  | while read -r RID CIDR; do
        [ -n "$RID" ] || continue
        if [ "$CIDR" != "$MYIP/32" ]; then
            run "aws --profile '$PROFILE' --region '$REGION' ec2 revoke-security-group-ingress --group-id '$SG' --security-group-rule-ids '$RID'"
            echo "  dropped stale $CIDR"
        fi
    done
if [ "$DRY_RUN" = 0 ]; then
    if aws_ ec2 describe-security-group-rules --filters "Name=group-id,Values=$SG" --query 'SecurityGroupRules[?IsEgress==`false` && FromPort==`22`].CidrIpv4' --output text | grep -q "$MYIP/32"; then
        echo "  $MYIP/32 already allowed"
    else
        aws_ ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr "$MYIP/32" >/dev/null
        echo "  allowed $MYIP/32"
    fi
fi

if [ ! -f "$KEYFILE" ]; then
    if [ "$DRY_RUN" = 1 ]; then
        echo "  note: no key at $KEYFILE (dry-run continues)"
    else
        echo "no private key at $KEYFILE" >&2
        echo "point at the key behind the '$NAME' key pair:" >&2
        echo "  KEYFILE=/path/to/key bash deploy/deploy.sh" >&2
        exit 1
    fi
fi

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 -o BatchMode=yes -i "$KEYFILE")
ssh_() { ssh "${SSH_OPTS[@]}" "ubuntu@$HOST" "$@"; }

say "wait for ssh"
for i in $(seq 1 30); do
    if ssh_ true 2>/dev/null; then echo "  up"; break; fi
    if [ "$i" = 30 ]; then echo "  ssh never came up on $HOST"; exit 1; fi
    sleep 6
done

say "sync the repo"
if [ "$DRY_RUN" = 1 ]; then
    echo "  would: git fetch + reset --hard origin/main in /opt/siliconboard"
else
    ssh_ 'bash -s' <<REMOTE
set -e
if [ -d /opt/siliconboard/.git ]; then
    cd /opt/siliconboard
    sudo git fetch --quiet origin
    sudo git reset --hard origin/main
else
    sudo git clone --quiet $REPO_URL /opt/siliconboard
fi
sudo chown -R ubuntu:ubuntu /opt/siliconboard
REMOTE
fi

say "secrets and host setup"
if [ "$DRY_RUN" = 0 ]; then
    if [ -f "$ROOT/.env" ]; then
        scp -q -i "$KEYFILE" -o StrictHostKeyChecking=accept-new "$ROOT/.env" "ubuntu@$HOST:/tmp/sb.env"
        ssh_ 'sudo install -m 600 -o root -g root /tmp/sb.env /etc/siliconboard/env; rm -f /tmp/sb.env; echo "  env installed 0600"'
    else
        echo "  no local .env, leaving the box copy alone"
    fi
    # The UI mints a random token per start, which rotates it on every deploy and
    # breaks open tabs. Persist one token instead: keep it if present, else create.
    # 128 bytes in -> tr strips to alnum -> keep 24 (32 bytes of urandom only
    # yields ~8 alnum chars, which is weaker than it looks).
    ssh_ 'if sudo grep -q "^SB_UI_TOKEN=" /etc/siliconboard/env 2>/dev/null; then
              echo "  ui token: kept"
          else
              tok=$(head -c 128 /dev/urandom | tr -dc "A-Za-z0-9" | head -c 24)
              echo "SB_UI_TOKEN=$tok" | sudo tee -a /etc/siliconboard/env >/dev/null
              echo "  ui token: created (survives restarts)"
          fi'
    # Password gate: UI_PASSWORD=... bash deploy/deploy.sh sets or rotates it.
    # When set, ui/server.py refuses the token entirely - the password is the
    # only way in. Value is kept out of argv logs by passing it on stdin.
    if [ -n "${UI_PASSWORD:-}" ]; then
        printf '%s
' "$UI_PASSWORD" | ssh_ 'read -r pw; sudo sed -i \
            -e "/^SB_UI_PASSWORD=/d" /etc/siliconboard/env; \
            printf "SB_UI_PASSWORD=%s\n" "$pw" | sudo tee -a /etc/siliconboard/env >/dev/null; \
            echo "  ui password: set"'
    else
        ssh_ 'sudo grep -q "^SB_UI_PASSWORD=" /etc/siliconboard/env 2>/dev/null \
            && echo "  ui password: set" || echo "  ui password: not set (UI_PASSWORD=... to enable)"'
    fi
    ssh_ "SKIP_STA=$SKIP_STA sudo -E bash /opt/siliconboard/deploy/setup.sh" || echo "  setup.sh complained, see above"
fi

say "web ui"
if [ "$DRY_RUN" = 0 ]; then
    # loopback only: no security-group change, the tunnel is the way in
    ssh_ 'set -e
        sudo install -m 644 -o root -g root /opt/siliconboard/deploy/ui.service /etc/systemd/system/siliconboard-ui.service
        sudo systemctl daemon-reload
        sudo systemctl enable --now siliconboard-ui.service >/dev/null 2>&1 || true
        sudo systemctl restart siliconboard-ui.service
        sleep 2
        echo "  ui service: $(systemctl is-active siliconboard-ui.service)"
        code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8080/api/system || true)
        echo "  ui loopback http: ${code:-down} (401 = up, token required)"'
    UI_URL="$(ssh_ "sudo journalctl -u siliconboard-ui --no-pager 2>/dev/null | grep -o 'http://127.0.0.1:[0-9]*/?t=[A-Za-z0-9_-]*' | tail -1" || true)"
    [ -n "$UI_URL" ] && echo "  ui url (after tunnel): ${UI_URL/127.0.0.1/localhost}"
else
    echo "  would: install + enable siliconboard-ui.service (loopback :8080)"
fi

# Public door: an OUTBOUND Cloudflare quick tunnel to the loopback UI. The box
# still listens on 127.0.0.1 only - no inbound port, no security-group rule,
# https at the edge. The password gate (UI_PASSWORD) is what protects the data.
say "public tunnel (https, no open ports)"
if [ "$DRY_RUN" = 0 ]; then
    ssh_ 'set -e
        if ! command -v cloudflared >/dev/null 2>&1; then
            curl -fsSL -o /tmp/cloudflared.deb \
                https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
            sudo dpkg -i /tmp/cloudflared.deb >/dev/null && rm -f /tmp/cloudflared.deb
            echo "  cloudflared: installed"
        else
            echo "  cloudflared: present ($(cloudflared --version 2>/dev/null | head -1))"
        fi
        sudo install -m 644 -o root -g root /opt/siliconboard/deploy/cloudflared.service /etc/systemd/system/siliconboard-tunnel.service
        sudo systemctl daemon-reload
        sudo systemctl enable --now siliconboard-tunnel.service >/dev/null 2>&1 || true
        sudo systemctl restart siliconboard-tunnel.service
        sleep 6
        echo "  tunnel: $(systemctl is-active siliconboard-tunnel.service)"'
    PUBLIC_URL="$(ssh_ "sudo journalctl -u siliconboard-tunnel --no-pager -n 50 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1" || true)"
    if [ -n "$PUBLIC_URL" ]; then
        echo "  public url: $PUBLIC_URL"
        # the edge must answer, and the gate must be closed in front of it
        code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$PUBLIC_URL/api/auth" || true)
        echo "  edge check: /api/auth -> ${code:-down} (200 = the tunnel is up)"
    else
        echo "  public url: not in the journal yet (journalctl -u siliconboard-tunnel)"
    fi
else
    echo "  would: install cloudflared + siliconboard-tunnel.service (outbound https)"
fi

say "verify"
if [ "$DRY_RUN" = 0 ]; then
    ssh_ "echo \"  service: \$(systemctl is-active siliconboard)\"; sudo docker images --format '    {{.Repository}}:{{.Tag}}' | grep siliconboard || echo '    (no images built yet)'; echo '  recent runs:'; ls /opt/siliconboard/runs 2>/dev/null | tail -3 | sed 's/^/    /' || true"
fi

echo
printf 'done\n'
printf '  ssh -i %s ubuntu@%s\n' "$KEYFILE" "$HOST"
printf "  then: cd /opt/siliconboard && .venv/bin/python main.py run --spec examples/counter/spec.txt --run-id live1\n"
printf '  ui:   bash ui/tunnel.sh          # opens the tunnel and prints the url\n'
printf '        bash ui/tunnel.sh --phone  # same, reachable from a phone on your Wi-Fi\n'
