#!/usr/bin/env bash
#
# Copy the touchscreen modules, the SoC temperature sensor module
# (soc_temp_lvts.ko, for the CPU temperature), the flash LED module
# (leds-sm5714-fled.ko, for the flashlight), everything they depend on,
# modules.dep and the FocalTech touch firmware from a rooted phone, for
# extract-prebuilts.py --vendor-files. The phone must run the SAME stock
# firmware as the recovery image you extract the kernel from.
#
# usage: pull-vendor-files.sh <output dir> [--recovery]
#
# --recovery also saves the phone's recovery partition as <output dir>/recovery.img
# (about 80 MB). Only useful while the phone still has the STOCK recovery;
# extract-prebuilts.py rejects a dump of a custom one.
#
set -Eeuo pipefail
# say where the script stopped instead of exiting silently
trap 'echo "stopped at line $LINENO: $BASH_COMMAND" >&2' ERR

out=${1:?usage: pull-vendor-files.sh <output dir> [--recovery]}
dump_recovery=${2:-}
modules=(zt7650m_ts.ko focaltech_ts_ft3418.ko)
# skipped with a note if the firmware does not have them
optional=(soc_temp_lvts.ko leds-sm5714-fled.ko)
# not zt7650m_a15.bin: with it in the ramdisk, the Zinitix driver flashes the
# controller at every boot and touch stays dead (see the tree's
# zinitix_touch.sh)
firmware=(ft3418_a15.bin)

command -v adb >/dev/null || { echo "adb not found" >&2; exit 1; }
adb get-state >/dev/null || { echo "no device connected" >&2; exit 1; }
mkdir -p "$out"

# The whole command is passed to su as one quoted argument, so it works the
# same with Magisk and KernelSU-style su. Paths here contain no quotes/spaces.
root() {
	adb shell "su -c '$*'" | tr -d '\r'
}

# the vendor's version, not the system's: on a custom ROM only the vendor
# and the bootloader are the stock firmware's, and the modules come from
# the vendor
vendor_fw=$(adb shell getprop ro.vendor.build.version.incremental | tr -d '\r')
bootloader=$(adb shell getprop ro.boot.bootloader | tr -d '\r')
echo "firmware: $vendor_fw (bootloader $bootloader)"
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
for m in "${modules[@]}" "${optional[@]}"; do
	line=$(grep -E "(^|/)$m:" "$out/modules.dep" || true)
	if [ -z "$line" ]; then
		case " ${optional[*]} " in
		*" $m "*) echo "skipped:  $m is not listed in modules.dep" >&2; continue ;;
		esac
		echo "$m is not listed in modules.dep" >&2
		exit 1
	fi
	for path in ${line//:/ }; do
		needed+=("$(basename "$path")")
	done
done

mapfile -t unique < <(printf '%s\n' "${needed[@]}" | sort -u)
for name in "${unique[@]}"; do
	pull "$moddir/$name" "$name"
done

# find exits non-zero when one of the directories does not exist (this
# phone has no /vendor_dlkm/firmware), and adb passes that status on
for f in "${firmware[@]}"; do
	path=$(root "find /vendor/firmware /vendor_dlkm/firmware -name $f 2>/dev/null; true" | head -n 1) || true
	if [ -n "$path" ]; then pull "$path" "$f"; else echo "missing:  $f" >&2; fi
done

if [ "$dump_recovery" = "--recovery" ]; then
	# exec-out does not pass on the exit status, so check what arrived
	adb exec-out "su -c 'dd if=/dev/block/by-name/recovery bs=1048576 2>/dev/null'" > "$out/recovery.img"
	size=$(wc -c < "$out/recovery.img")
	if [ "$(head -c 8 "$out/recovery.img")" != "ANDROID!" ]; then
		echo "failed to read the recovery partition (got $size bytes, not a boot image)" >&2
		rm -f "$out/recovery.img"
		exit 1
	fi
	echo "pulled:   recovery partition ($size bytes)"
fi

echo "done: ${#unique[@]} modules in $out"
