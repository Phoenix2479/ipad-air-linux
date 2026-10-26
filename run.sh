#!/bin/bash
set -euo pipefail
bundle=$(cd -- "$(dirname -- "$0")" && pwd -P)
cd "$bundle"
shasum -a 256 -c boot/SHA256SUMS
product=$(ideviceinfo -k ProductType 2>/dev/null || true)
if [[ -n $product ]]; then
  [[ $product == iPad4,2 ]] || { echo "Expected iPad4,2, found $product" >&2; exit 1; }
  device_count=$(idevice_id -l | wc -l | tr -d ' ')
  [[ $device_count == 1 ]] || { echo 'Connect only the target iPad' >&2; exit 1; }
  ideviceenterrecovery "$(idevice_id -l)"
else
  product=$(irecovery -q 2>/dev/null | sed -n 's/^PRODUCT: //p')
  [[ $product == iPad4,2 ]] || { echo 'Connect the J72 iPad in normal, recovery, or DFU mode' >&2; exit 1; }
fi
echo 'Enter DFU: Top alone 3 seconds, Top + Home 8 seconds, then release Top and hold Home until detected.'
cd "$bundle/source/remote_boot"
./remoteboot.sh boot "$bundle/boot/m1n1-j72-linux.macho" "$bundle/boot/m1n1-monitor-stub.macho" | tee "$bundle/logs/boot.log"
cd "$bundle"
exec python3 tools/session.py start
