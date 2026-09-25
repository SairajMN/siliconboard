#!/usr/bin/env bash
# Fails the systemd start early when the host is missing something the pipeline needs.
set -euo pipefail

fail=0
note() { echo "[check] $*"; }
bad() { echo "[check] FAIL: $*" >&2; fail=1; }

note "host: $(. /etc/os-release && echo "$PRETTY_NAME")  kernel: $(uname -r)"
note "cpu: $(nproc)  mem: $(free -m | awk '/^Mem:/{print $2" MiB total, "$7" MiB free"}')"
note "disk: $(df -h / | awk 'NR==2{print $4" free of "$2}')"

if ! command -v docker >/dev/null; then
    bad "docker not installed"
elif ! docker info >/dev/null 2>&1; then
    bad "docker installed but the daemon is not reachable"
else
    note "docker: $(docker --version)"
fi

for img in siliconboard/eda:1.0 siliconboard/sta:1.0; do
    if docker image inspect "$img" >/dev/null 2>&1; then
        note "image present: $img"
    else
        note "image missing: $img  (the stage that needs it will fall back or fail)"
    fi
done

if [ -f /opt/siliconboard/pdk/sky130hd_tt_025C_1v80.lib ]; then
    note "liberty: $(stat -c %s /opt/siliconboard/pdk/sky130hd_tt_025C_1v80.lib) bytes"
else
    note "liberty: absent, the timing stage cannot run a real STA pass"
fi

if [ -r /etc/siliconboard/env ]; then
    keys=$(grep -cE '^[A-Z_0-9]+_?API_KEY.*=.+' /etc/siliconboard/env || true)
    mode=$(stat -c %a /etc/siliconboard/env)
    note "env: mode $mode, $keys populated key lines"
    [ "$mode" = "600" ] || bad "/etc/siliconboard/env is mode $mode, expected 600"
    [ "$keys" -gt 0 ] || bad "no populated API key lines in /etc/siliconboard/env"
else
    bad "/etc/siliconboard/env is missing or unreadable"
fi

exit "$fail"