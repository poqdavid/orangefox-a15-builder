#!/system/bin/sh
#
# Collect the values the a15 OrangeFox tree depends on, from a running
# phone. Run as root on the phone, not on the PC:
#
#   adb push tools/device-info.sh /data/local/tmp/
#   adb shell "su -c 'sh /data/local/tmp/device-info.sh'" > a15-device-info.txt
#
# Plug in a USB OTG drive and a MicroSD card first if you have them, so
# their sysfs paths show up. The report contains no serial numbers or IMEIs.
#

section() {
	echo
	echo "=== $* ==="
}

show() {
	# show <file>: print "file: value" if it exists
	[ -e "$1" ] && echo "$1: $(cat "$1" 2>/dev/null)"
}

section "build"
for p in ro.product.model ro.product.device ro.product.board ro.hardware ro.board.platform \
	ro.build.version.release ro.build.version.incremental ro.build.version.security_patch \
	ro.boot.bootloader ro.product.first_api_level ro.boot.dynamic_partitions ro.crypto.type; do
	echo "$p=$(getprop "$p")"
done
echo "kernel: $(uname -r)"

section "boot state"
for p in ro.boot.verifiedbootstate ro.boot.vbmeta.device_state ro.boot.flash.locked \
	ro.boot.warranty_bit ro.boot.hardware ro.boot.boot_devices; do
	echo "$p=$(getprop "$p")"
done

section "display"
wm size
wm density
dumpsys display 2>/dev/null | grep -m 3 -iE "cutout|mDisplayInfos" | cut -c1-400
dumpsys window 2>/dev/null | grep -m 4 -iE "statusBars|status_bar_height|mStableInsets" | cut -c1-300

section "backlight"
for d in /sys/class/backlight/*; do
	echo "$d"
	show "$d/max_brightness"
	show "$d/brightness"
done

section "leds / flashlight / vibrator"
ls /sys/class/leds/ 2>/dev/null
ls /sys/class/camera/flash/ 2>/dev/null
ls /sys/class/timed_output/ 2>/dev/null

section "battery"
show /sys/class/power_supply/battery/capacity
show /sys/class/power_supply/battery/status

section "thermal zones"
for z in /sys/class/thermal/thermal_zone*; do
	[ -e "$z/type" ] && echo "$z: $(cat "$z/type") $(cat "$z/temp" 2>/dev/null)"
done | head -40

section "block devices (by-name, size in bytes)"
for p in /dev/block/by-name/*; do
	echo "$(basename "$p") $(blockdev --getsize64 "$p" 2>/dev/null)"
done

section "disks and their sysfs paths"
for b in /sys/block/sd* /sys/block/mmcblk*; do
	[ -e "$b" ] || continue
	echo "$(basename "$b") -> $(readlink -f "$b")"
done

section "mounts"
grep -E " (/system|/vendor|/product|/odm|/system_ext|/vendor_dlkm|/data|/metadata|/efs|/mnt/vendor[^ ]*|/storage/[^ ]*) " /proc/mounts \
	| awk '{print $1, $2, $3}'

section "vendor fstab"
for f in /vendor/etc/fstab.mt6789 /vendor/etc/fstab.* /odm/etc/fstab.*; do
	[ -f "$f" ] || continue
	echo "--- $f"
	grep -v "^#" "$f" | grep -v "^$"
	break
done

section "touchscreen"
getevent -pl 2>/dev/null | grep -E "^add device|  name:" | paste - - | grep -iE "touch|ts|zt7650|ft3418|focal|zinitix"
for m in zt7650m_ts focaltech_ts_ft3418; do
	f=$(find /vendor_dlkm/lib/modules /vendor/lib/modules -name "$m.ko" 2>/dev/null | head -1)
	echo "$m.ko: ${f:-not found}"
done
for f in /vendor/firmware/*ft3418* /vendor/firmware/*zt7650*; do
	[ -e "$f" ] && echo "firmware: $f"
done
echo "/vendor/lib/modules -> $(readlink /vendor/lib/modules 2>/dev/null || echo 'not a symlink')"
for d in /vendor_dlkm/lib/modules /vendor/lib/modules; do
	[ -f "$d/modules.dep" ] || continue
	echo "--- touch entries in $d/modules.dep"
	grep -E "/(zt7650m_ts|focaltech_ts_ft3418|tuihw-inf)\.ko:" "$d/modules.dep"
	break
done

section "loaded modules (touch, flash, thermal)"
echo "total: $(wc -l < /proc/modules)"
grep -iE "zt7650|focaltech|tuihw|flash|sm5714|thermal" /proc/modules | awk '{print $1}'
