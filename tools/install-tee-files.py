#!/usr/bin/env python3
"""Install Samsung's TEE stack into the device tree, for /data decryption.

usage: install-tee-files.py <pull-tee-files output dir or .zip> --tree <device tree>

Takes what pull-tee-files.py copied from a phone on stock firmware and puts
into the tree's recovery/root/vendor what recovery needs to start TEEGRIS
and the keymint, fkeymaster, gatekeeper and weaver services:

  - the service binaries, including the stock gatekeeper service, its HIDL
    passthrough implementation and the gatekeeper module
  - every library they load from /vendor, and the VNDK libraries the
    recovery does not have itself

These copies are a fallback. At boot, prepare_tee.sh replaces every file on
the list it writes (recovery/root/vendor/etc/tee-files.list) with the
installed firmware's own copy, so the services always match the firmware;
files not in the pulled set (such as the gatekeeper passthrough
implementation from an older pull) are only taken from the firmware at
boot. The trusted apps (/vendor/tee) are always taken from the firmware.

It writes tee-files.txt (the firmware, its Android version and patch levels,
and a sha256 per file) and sets PLATFORM_SECURITY_PATCH in BoardConfig.mk to
the firmware's, so the image header matches the stock recovery of that
firmware. At boot, recovery reports the installed firmware's versions to the
TEE, so the files only need refreshing when Samsung changes the TEE services.
"""

import argparse
import hashlib
import os
import re
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foxtools  # noqa: E402

# what recovery starts (see init.recovery.teegris.rc in the tree)
SERVICES = [
    "/vendor/bin/tzdaemon",
    "/vendor/bin/tzts_daemon",
    "/vendor/bin/hw/android.hardware.security.keymint-service",
    "/vendor/bin/vendor.samsung.hardware.security.fkeymaster-service",
    "/vendor/bin/hermesd",
    "/vendor/bin/hw/android.hardware.gatekeeper@1.0-service",
]
# loaded at run time rather than through DT_NEEDED: the stock gatekeeper
# service opens its HIDL passthrough implementation from /vendor/lib64/hw,
# which opens the gatekeeper module through libhardware
HAL_MODULES = [
    "/vendor/lib64/hw/android.hardware.gatekeeper@1.0-impl.so",
    "/vendor/lib64/hw/gatekeeper.{platform}.so",
]
# may be missing from an older pull: then only copied from the firmware at boot
BOOT_ONLY_OK = {"/vendor/lib64/hw/android.hardware.gatekeeper@1.0-impl.so"}
BOOT_LIST = "recovery/root/vendor/etc/tee-files.list"

BIONIC = {"libc.so", "libm.so", "libdl.so", "libdl_android.so", "ld-android.so"}
# in the recovery's /system/lib64 when TW_INCLUDE_CRYPTO is on (TWRP relinks
# them, or they come with hwservicemanager, keystore2 and the TWRP binary);
# the vendor's VNDK copies of these are not needed
RECOVERY_PROVIDES = {
    "libbase.so", "libc++.so", "libcrypto.so", "libssl.so", "libcutils.so", "libutils.so",
    "libutilscallstack.so", "libhidlbase.so", "libhwbinder.so", "libbinder.so", "libbinder_ndk.so",
    "liblog.so", "libhardware.so", "libhardware_legacy.so", "libprocessgroup.so", "libcgrouprc.so",
    "libvndksupport.so", "libz.so", "libunwindstack.so", "libbacktrace.so",
    "android.system.suspend@1.0.so", "android.hardware.gatekeeper@1.0.so",
    "android.hardware.security.keymint-V1-ndk_platform.so",
    "android.hardware.security.secureclock-V1-ndk_platform.so",
    "android.hardware.security.sharedsecret-V1-ndk_platform.so",
}
TREE_ROOT = "recovery/root"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Pulled:
    """The files pull-tee-files.py wrote, addressed by their phone paths."""

    def __init__(self, root, vndk_version=None):
        self.root = root
        apex = os.path.join(root, "apex")
        self.vndk = sorted(d for d in (os.listdir(apex) if os.path.isdir(apex) else [])
                           if d.startswith("com.android.vndk.v") and "@" not in d)
        # the vendor's linker namespace only sees the VNDK of its own version
        # (an older pull may have taken libraries from other ones too)
        own = f"com.android.vndk.v{vndk_version}"
        if own in self.vndk:
            self.vndk = [own]

    def path(self, phone_path):
        return os.path.join(self.root, *phone_path.lstrip("/").split("/"))

    def exists(self, phone_path):
        return os.path.isfile(self.path(phone_path))

    def read(self, phone_path):
        with open(self.path(phone_path), "rb") as f:
            return f.read()

    def resolve(self, name, bits):
        lib = "lib64" if bits == 64 else "lib"
        for d in [f"/odm/{lib}", f"/vendor/{lib}"] + [f"/apex/{v}/{lib}" for v in self.vndk] + \
                 [f"/system/{lib}", f"/system_ext/{lib}"]:
            if self.exists(f"{d}/{name}"):
                return f"{d}/{name}"
        return None


def origin(phone_path):
    if phone_path.startswith(("/vendor/", "/odm/")):
        return "vendor"
    if phone_path.startswith("/apex/com.android.vndk"):
        return "vndk"
    return "system"


def tree_path(phone_path):
    """Where a phone file goes in the tree: vendor files keep their path
    (odm is folded into vendor), others go next to the vendor libraries."""
    if phone_path.startswith("/odm/"):
        phone_path = "/vendor/" + phone_path[len("/odm/"):]
    if not phone_path.startswith("/vendor/"):
        phone_path = "/vendor/lib64/" + os.path.basename(phone_path)
    return TREE_ROOT + phone_path


def plan(pulled, roots):
    """Phone paths to install, following DT_NEEDED from the roots."""
    chosen, missing, queue = {}, [], list(roots)
    while queue:
        p = queue.pop()
        if p in chosen:
            continue
        data = pulled.read(p)
        info = foxtools.elf_info(data)
        if info is None:
            foxtools.die(f"{p} is not an ELF file")
        bits, needed, _ = info
        if bits != 64:
            foxtools.die(f"{p} is {bits}-bit; recovery runs the 64-bit services only")
        chosen[p] = data
        for name in needed:
            if name in BIONIC:
                continue
            dep = pulled.resolve(name, bits)
            if dep is None:
                if name not in RECOVERY_PROVIDES:
                    missing.append(f"{name} (needed by {p})")
                continue
            if origin(dep) != "vendor" and name in RECOVERY_PROVIDES:
                continue
            queue.append(dep)
    if missing:
        foxtools.die("not in the pulled files: " + "; ".join(sorted(set(missing)))
                     + "\nrun pull-tee-files.py again on the stock firmware")
    return chosen


def set_patch_levels(boardconfig, spl, vendor_spl):
    text = open(boardconfig, encoding="utf-8").read()
    pat = re.compile(r"^(PLATFORM_SECURITY_PATCH\s*:?=\s*)(\S+)", re.M)
    m = pat.search(text)
    if not m:
        foxtools.die("BoardConfig.mk has no PLATFORM_SECURITY_PATCH line")
    old = m.group(2)
    text = pat.sub(lambda m: m.group(1) + spl, text, count=1)
    vpat = re.compile(r"^(VENDOR_SECURITY_PATCH\s*:?=\s*)(\S+)", re.M)
    want_vendor = "$(PLATFORM_SECURITY_PATCH)" if vendor_spl == spl else vendor_spl
    if vpat.search(text):
        text = vpat.sub(lambda m: m.group(1) + want_vendor, text, count=1)
    elif vendor_spl != spl:
        foxtools.die("the vendor patch level differs from the system one, and BoardConfig.mk "
                     "has no VENDOR_SECURITY_PATCH line to set it")
    with open(boardconfig, "w", encoding="utf-8") as f:
        f.write(text)
    return old


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pulled", help="directory or .zip written by pull-tee-files.py")
    ap.add_argument("--tree", required=True, help="device tree to install into")
    args = ap.parse_args()
    tree = os.path.abspath(args.tree)
    boardconfig = os.path.join(tree, "BoardConfig.mk")
    if not os.path.isfile(boardconfig):
        foxtools.die(f"{tree} does not look like a device tree")

    tmp = None
    src = args.pulled
    if zipfile.is_zipfile(src):
        tmp = tempfile.mkdtemp(prefix="tee-files-")
        with zipfile.ZipFile(src) as z:
            for name in z.namelist():
                if name.startswith("/") or ".." in name.split("/"):
                    foxtools.die(f"unsafe path in {src}: {name}")
            z.extractall(tmp)
        src = tmp
    try:
        root = os.path.join(src, "root")
        if not os.path.isdir(root):
            foxtools.die(f"{args.pulled}: no root/ directory; is this pull-tee-files.py output?")
        system = foxtools.read_props(os.path.join(root, "system/build.prop"))
        vendor = foxtools.read_props(os.path.join(root, "vendor/build.prop"))
        pulled = Pulled(root, vendor.get("ro.vndk.version"))
        firmware = system.get("ro.build.version.incremental") or vendor.get("ro.vendor.build.version.incremental")
        vendor_fw = vendor.get("ro.vendor.build.version.incremental")
        # the patch levels and the VNDK libraries come from the system, and a
        # custom ROM's would be written down as the firmware's
        if vendor_fw and firmware != vendor_fw:
            foxtools.die(f"the pulled system ({firmware}) is not the stock firmware of the vendor "
                         f"({vendor_fw}); pull the files from a phone running stock firmware")
        android = system.get("ro.build.version.release")
        spl = system.get("ro.build.version.security_patch")
        vendor_spl = vendor.get("ro.vendor.build.security_patch")
        platform = vendor.get("ro.board.platform")
        for name, value in (("firmware", firmware), ("Android version", android),
                            ("security patch", spl), ("vendor security patch", vendor_spl),
                            ("platform", platform)):
            if not value:
                foxtools.die(f"{name} not found in the pulled build.prop files")
        print(f"firmware: {firmware}, Android {android}, patch {spl}, vendor patch {vendor_spl}")

        roots = SERVICES + [m.format(platform=platform) for m in HAL_MODULES]
        absent = [r for r in roots if not pulled.exists(r)]
        fatal = [r for r in absent if r not in BOOT_ONLY_OK]
        if fatal:
            foxtools.die("not in the pulled files: " + ", ".join(fatal)
                         + "\npull them again with the current pull-tee-files.py")
        for r in absent:
            print(f"note: {r} is not in the pulled files; recovery takes it from the firmware at boot")
        chosen = plan(pulled, [r for r in roots if r not in absent])

        # remove what an earlier run installed
        _, old_files = foxtools.read_tee_manifest(tree)
        for _sha, rel, _phone in old_files:
            p = os.path.join(tree, rel)
            if os.path.isfile(p):
                os.remove(p)

        lines = []
        for phone in sorted(chosen, key=tree_path):
            rel = tree_path(phone)
            dest = os.path.join(tree, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(chosen[phone])
            mode = 0o755 if "/bin/" in rel else 0o644
            os.chmod(dest, mode)
            lines.append(f"{sha256(chosen[phone])} {rel} {phone}")
            print(f"  {rel}")

        with open(os.path.join(tree, foxtools.TEE_MANIFEST), "w", encoding="utf-8") as f:
            f.write("# Samsung TEE files for /data decryption, written by install-tee-files.py\n"
                    "# from the stock firmware below. At boot, recovery reports the installed\n"
                    "# firmware's versions to the TEE, whatever these are.\n")
            f.write(f"firmware={firmware}\nandroid={android}\n"
                    f"security_patch={spl}\nvendor_security_patch={vendor_spl}\n\n")
            f.write("# sha256 tree-path phone-path\n")
            f.write("\n".join(lines) + "\n")

        # what prepare_tee.sh refreshes from the installed vendor at boot
        boot = sorted({p for p in chosen if p.startswith("/vendor/")} | set(absent))
        dest = os.path.join(tree, *BOOT_LIST.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write("# TEE files prepare_tee.sh copies from the installed vendor at boot\n"
                    "# (written by install-tee-files.py)\n")
            f.write("\n".join(boot) + "\n")

        old = set_patch_levels(boardconfig, spl, vendor_spl)
        total = sum(len(d) for d in chosen.values())
        print(f"\ninstalled {len(chosen)} files ({total / 1048576:.1f} MiB), wrote {foxtools.TEE_MANIFEST}"
              f" and {BOOT_LIST} ({len(boot)} files)")
        if old != spl:
            print(f"PLATFORM_SECURITY_PATCH: {old} -> {spl}")
        return 0
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
