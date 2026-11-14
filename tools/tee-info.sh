#!/system/bin/sh
#
# Collect what recovery needs to start Samsung's TEE (TEEGRIS) services for
# /data decryption. Run as root on the phone, on STOCK firmware, not on the PC:
#
#   adb push tools/tee-info.sh /data/local/tmp/
#   adb shell "su -c 'sh /data/local/tmp/tee-info.sh'" > a15-tee-info.txt
#
# Read-only: it changes nothing. The report contains no keys, passwords,
# serial numbers or IMEIs. Under /data and /efs it lists directory and file
# NAMES only, never contents.
#
# The names listed here are fixed system names (hex ids, UUIDs, service
# names), so plain ls and ps are fine:
# shellcheck disable=SC2012,SC2009

section() {
	echo
	echo "=== $* ==="
}

# u32 <file> <offset>: little-endian 32-bit value at <offset>
u32() {
	od -An -tu4 -j "$2" -N 4 "$1" 2>/dev/null | tr -d ' '
}

# decode the os_version field of a boot image header
decode_os() {
	v=$1
	if [ -z "$v" ] || [ "$v" -eq 0 ]; then
		echo "not set"
		return
	fi
	ver=$((v >> 11))
	pl=$((v & 2047))
	printf 'android %d.%d.%d, patch level %d-%02d\n' \
		$(((ver >> 14) & 127)) $(((ver >> 7) & 127)) $((ver & 127)) \
		$(((pl >> 4) + 2000)) $((pl & 15))
}

# header <partition>: header version and os_version of a boot-type image
header() {
	part=/dev/block/by-name/$1
	if [ ! -e "$part" ]; then
		echo "$1: no such partition"
		return
	fi
	tmp=/data/local/tmp/tee-info-header.$$
	dd if="$part" of="$tmp" bs=4096 count=1 2>/dev/null
	magic=$(dd if="$tmp" bs=8 count=1 2>/dev/null)
	if [ "$magic" = "VNDRBOOT" ]; then
		echo "$1: vendor boot header v$(u32 "$tmp" 8) (no os version)"
		rm -f "$tmp"
		return
	fi
	if [ "$magic" != "ANDROID!" ]; then
		echo "$1: no boot image header"
		rm -f "$tmp"
		return
	fi
	hv=$(u32 "$tmp" 40)
	# os_version sits at byte 44 in header v0-v2 and at byte 16 in v3/v4
	if [ "$hv" -ge 3 ]; then off=16; else off=44; fi
	echo "$1: header v$hv, $(decode_os "$(u32 "$tmp" $off)")"
	rm -f "$tmp"
}

TEE_RE='tzdaemon|tzts|teegris|tee|keymint|keymaster|gatekeeper|weaver|hermes|vault|drk|iccc'

section "firmware"
echo "ro.build.version.incremental=$(getprop ro.build.version.incremental)"
echo "ro.boot.bootloader=$(getprop ro.boot.bootloader)"
echo "ro.product.first_api_level=$(getprop ro.product.first_api_level)"

section "versions read from the build.prop files (what the firmware really is)"
for f in /system/build.prop /system_ext/etc/build.prop /vendor/build.prop \
	/odm/etc/build.prop /vendor_dlkm/etc/build.prop; do
	[ -f "$f" ] || continue
	grep -HE '^ro\.[^=]*(security_patch|version\.release|version\.sdk|hardware\.|keystore|gatekeeper|crypto|keymint|keymaster|weaver)' "$f"
done

section "the same values as getprop reports them (root hiding may change these)"
getprop | grep -iE 'security_patch|version\.release\]|version\.sdk\]|ro\.hardware\.|keystore|gatekeeper|keymint|keymaster|crypto|tzdaemon|tzts|teegris|weaver|hermes|verifiedbootstate|device_state|flash\.locked|warranty'

section "boot state as the bootloader passed it to the kernel"
KEYS='verifiedbootstate|vbmeta\.device_state|flash\.locked|warranty_bit|bootloader'
if [ -r /proc/bootconfig ]; then
	echo "--- /proc/bootconfig"
	grep -E "$KEYS" /proc/bootconfig
fi
if [ -r /proc/cmdline ]; then
	echo "--- /proc/cmdline"
	tr ' ' '\n' < /proc/cmdline | grep -E "$KEYS"
fi

section "os version and patch level in the image headers"
for p in boot init_boot recovery vendor_boot; do
	header "$p"
done

section "TEE device nodes and kernel interfaces"
ls -lZ /dev/tz* /dev/iwsock* /dev/tee* 2>/dev/null
ls -l /proc/iccc_ready /proc/tzdev* 2>/dev/null
ls /sys/module/tzdev/parameters 2>/dev/null | sed 's|^|tzdev parameter: |'

section "running TEE and keystore processes"
ps -A -o USER,PID,PPID,LABEL,NAME 2>/dev/null | grep -iE "USER|$TEE_RE|keystore|vold"

section "binder services (AIDL)"
service list 2>/dev/null | grep -iE "keymint|keymaster|gatekeeper|weaver|secureclock|sharedsecret|remotelyprov|keystore|fkeymaster|hermes|vault|drk"

section "HIDL services"
lshal 2>/dev/null | grep -iE "keymaster|keymint|gatekeeper|weaver|vault|drk|tee"

section "vendor binaries and HAL modules"
find /vendor/bin /vendor/bin/hw /vendor/lib64/hw /vendor/lib/hw /odm/bin /odm/bin/hw -maxdepth 1 2>/dev/null \
	| grep -iE "$TEE_RE|keystore" | while read -r f; do
	echo "$f $(wc -c < "$f" 2>/dev/null)"
done

section "init scripts that start them"
for f in /vendor/etc/init/*.rc /vendor/etc/init/hw/*.rc /odm/etc/init/*.rc; do
	[ -f "$f" ] || continue
	grep -qiE "$TEE_RE|/dev/tz" "$f" || continue
	echo "--- $f"
	grep -vE '^[[:space:]]*(#|$)' "$f"
done

section "vintf manifest fragments"
for d in /vendor/etc/vintf/manifest /odm/etc/vintf/manifest; do
	for f in "$d"/*; do
		echo "${f##*/}" | grep -qiE "$TEE_RE|secureclock|sharedsecret|rkp" && echo "$f"
	done
done
grep -iE -A3 '<name>[^<]*(keymint|keymaster|gatekeeper|weaver|secureclock|sharedsecret)' \
	/vendor/etc/vintf/manifest.xml 2>/dev/null | grep -E '<name>|<version>|<fqname>|<instance>'

section "trusted apps (/vendor/tee)"
ls -lR /vendor/tee 2>/dev/null
ls -ld /vendor/tee_compat /vendor/app/mcRegistry 2>/dev/null

section "secure element behind weaver (names only)"
ls -lZ /dev/k250a /dev/ssp 2>/dev/null
echo "security.securehw.available=$(getprop security.securehw.available)"
ls -l /vendor/etc/secnvm /vendor/firmware/secnvm 2>/dev/null
echo "/data/vendor/gatekeeper: $(ls -a /data/vendor/gatekeeper 2>/dev/null | tr '\n' ' ')"

section "TEE and keystore storage (names only)"
for d in /mnt/vendor/efs /efs; do
	echo "--- $d: $(ls "$d" 2>/dev/null | tr '\n' ' ')"
done
ls -ldZ /mnt/vendor/efs/tee /efs/tee /mnt/vendor/efs/DAK /efs/DAK \
	/data/vendor/tee /data/vendor/tzts /data/vendor/keymaster /data/vendor/gatekeeper \
	/data/vendor/weaver /metadata/vold/metadata_encryption 2>/dev/null
for d in /mnt/vendor/efs/tee /efs/tee /data/vendor/tee; do
	[ -d "$d" ] && echo "$d: $(ls "$d" 2>/dev/null | wc -l) entries"
done

section "users and lock screen protectors (names only)"
echo "users with vold keys: ce=[$(ls /data/misc/vold/user_keys/ce 2>/dev/null | tr '\n' ' ')] de=[$(ls /data/misc/vold/user_keys/de 2>/dev/null | tr '\n' ' ')]"
for u in /data/system_de/*; do
	[ -d "$u/spblob" ] || continue
	# file types only (spblob, pwd, secdis, weaver, ...), without the protector ids
	echo "$u/spblob: $(ls "$u/spblob" 2>/dev/null | sed 's/^[0-9a-fA-F]*\.//' | sort | uniq -c | tr -s ' ' | tr '\n' ' ')"
done

section "kernel log (TEE)"
dmesg 2>/dev/null | grep -iE "tzdev|teegris|tz_iwsock|iwsock|iccc" | tail -n 40

section "logcat (TEE, keymint, gatekeeper)"
logcat -d -b all 2>/dev/null | grep -iE "tzdaemon|tzts|teec|tees_|keymint|gatekeeper|weaver" | tail -n 60

echo
echo "=== end ==="
