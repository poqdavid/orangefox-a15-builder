#!/usr/bin/env bash
#
# Copy the touchscreen modules, everything they depend on, modules.dep and
# the touch firmware from a rooted phone, for
# extract-prebuilts.py --vendor-files. The phone must run the SAME stock
# firmware as the recovery image you extract the kernel from.
#
# usage: pull-vendor-files.sh <output dir> [--recovery]
#
# --recovery also saves the phone's recovery partition as <output dir>/recovery.img
# (about 80 MB). Only useful while the phone still has the STOCK recovery;
# extract-prebuilts.py rejects a dump of a custom one.
#
set -euo pipefail

out=${1:?usage: pull-vendor-files.sh <output dir> [--recovery]}
dump_recovery=${2:-}
modules=(zt7650m_ts.ko focaltech_ts_ft3418.ko)
firmware=(zt7650m_a15.bin ft3418_a15.bin)

command -v adb >/dev/null || { echo "adb not found" >&2; exit 1; }
adb get-state >/dev/null || { echo "no device connected" >&2; exit 1; }
mkdir -p "$out"

# The whole command is passed to su as one quoted argument, so it works the
# same with Magisk and KernelSU-style su. Paths here contain no quotes/spaces.
root() {
	adb shell "su -c '$*'" | tr -d '\r'
}

echo "firmware: $(adb shell getprop ro.build.version.incremental | tr -d '\r')"
echo "kernel:   $(adb shell uname -r | tr -d '\r')"

# the directory that holds the touch drivers (vendor_dlkm on this device)
moddir=""
for d in /vendor_dlkm/lib/modules /vendor/lib/modules; do
	if [ -n "$(root "ls $d/${modules[0]} 2>/dev/null")" ]; then
		moddir=$d
		break
	fi
done
[ -n "$moddir" ] || { echo "${modules[0]} not found on the phone" >&2; exit 1; }
echo "modules:  $moddir"

pull() {
	# pull <remote path> <local name>
	adb exec-out "su -c 'cat $1'" > "$out/$2"
	[ -s "$out/$2" ] || { echo "failed to read $1" >&2; rm -f "$out/$2"; return 1; }
	echo "pulled:   $1 ($(wc -c < "$out/$2") bytes)"
}

pull "$moddir/modules.dep" modules.dep

# a modules.dep line lists the module followed by all of its dependencies
needed=()
for m in "${modules[@]}"; do
	line=$(grep -E "(^|/)$m:" "$out/modules.dep" || true)
	[ -n "$line" ] || { echo "$m is not listed in modules.dep" >&2; exit 1; }
	for path in ${line//:/ }; do
		needed+=("$(basename "$path")")
	done
done

mapfile -t unique < <(printf '%s\n' "${needed[@]}" | sort -u)
for name in "${unique[@]}"; do
	pull "$moddir/$name" "$name"
done

for f in "${firmware[@]}"; do
	path=$(root "find /vendor/firmware /vendor_dlkm/firmware -name $f 2>/dev/null" | head -n 1)
	if [ -n "$path" ]; then pull "$path" "$f"; else echo "missing:  $f" >&2; fi
done

if [ "$dump_recovery" = "--recovery" ]; then
	adb exec-out "su -c 'dd if=/dev/block/by-name/recovery bs=1048576 2>/dev/null'" > "$out/recovery.img"
	echo "pulled:   recovery partition ($(wc -c < "$out/recovery.img") bytes)"
fi

echo "done: ${#unique[@]} modules in $out"
