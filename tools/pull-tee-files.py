#!/usr/bin/env python3
"""Copy Samsung's TEE stack (TEEGRIS, keymint, gatekeeper) from a rooted phone.

Recovery can only decrypt /data the way Android does it: by starting the
same TEE daemons and keymint/gatekeeper services from /vendor, which ask the
trusted apps in /vendor/tee to unwrap the keys. This copies everything that
takes, so the tree can be set up from the real files instead of guesses:

  - the services the vendor init scripts start for the TEE, keymint,
    keymaster, gatekeeper and weaver, and those init scripts
  - every library the binaries load, followed recursively through /vendor,
    /odm and the VNDK apex (libraries from /system are copied but not
    followed, the recovery has its own)
  - the gatekeeper/keystore HAL modules libhardware opens by name
  - the trusted apps in /vendor/tee
  - the matching vintf manifest fragments and the build.prop files

It reads only read-only partitions: nothing under /data or /efs. The phone
must run the stock firmware you want recovery to decrypt.

usage: pull-tee-files.py <output dir> [-s SERIAL]
Then upload <output dir>.zip.
"""

import argparse
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foxtools  # noqa: E402

SERVICE_RE = re.compile(r"tzdaemon|tzts|teegris|keymint|keymaster|gatekeeper|weaver|hermes", re.I)
VINTF_RE = re.compile(r"keymint|keymaster|gatekeeper|weaver|secureclock|sharedsecret|rkp|"
                      r"remotelyprovision", re.I)
HAL_MODULE_RE = re.compile(r"^(gatekeeper|keystore|keymaster)\.", re.I)
# provided by the recovery's own bionic, never copied
BIONIC = {"libc.so", "libm.so", "libdl.so", "libdl_android.so", "ld-android.so"}
RC_GLOBS = "/vendor/etc/init/*.rc /vendor/etc/init/hw/*.rc /odm/etc/init/*.rc"
BIN_DIRS = "/vendor/bin /vendor/bin/hw /odm/bin /odm/bin/hw"
HAL_DIRS = "/vendor/lib64/hw /vendor/lib/hw /odm/lib64/hw /odm/lib/hw"
VINTF_DIRS = "/vendor/etc/vintf/manifest /odm/etc/vintf/manifest"
PROP_FILES = ["/system/build.prop", "/vendor/build.prop", "/odm/etc/build.prop"]


class Phone:
    def __init__(self, serial=None):
        self.adb = ["adb"] + (["-s", serial] if serial else [])

    def _root(self, mode, command):
        # one quoted argument for su, so Magisk and KernelSU su behave the same
        return self.adb + [mode, "su -c " + shlex.quote(command)]

    def sh(self, command):
        r = subprocess.run(self._root("shell", command), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        return r.stdout.decode("utf-8", "replace").replace("\r", "")

    def lines(self, command):
        return [l for l in self.sh(command).splitlines() if l.strip()]

    def read(self, path):
        r = subprocess.run(self._root("exec-out", f"cat {shlex.quote(path)} 2>/dev/null"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return r.stdout if r.returncode == 0 else b""

    def files_in(self, dirs):
        return self.lines(f'for d in {dirs}; do for f in "$d"/*; do '
                          f'[ -f "$f" ] && echo "$f"; done; done')

    def first_file(self, paths):
        quoted = " ".join(shlex.quote(p) for p in paths)
        found = self.lines(f'for f in {quoted}; do if [ -f "$f" ]; then echo "$f"; break; fi; done')
        return found[0] if found else None


def parse_rc(text):
    """Services in an init script: name, path, arguments and option lines."""
    services, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        if words[0] == "service" and len(words) >= 3:
            cur = {"name": words[1], "path": words[2], "args": words[3:], "options": []}
            services.append(cur)
        elif words[0] in ("on", "import"):
            cur = None
        elif cur is not None:
            cur["options"].append(line)
    return services


def split_rc_dump(text):
    """'### <path>' separated dump -> {path: text}."""
    files, cur = {}, None
    for line in text.splitlines(keepends=True):
        if line.startswith("### /"):
            cur = line[4:].strip()
            files[cur] = ""
        elif cur:
            files[cur] += line
    return files


def origin(path):
    if path.startswith(("/vendor/", "/odm/")):
        return "vendor"
    if path.startswith("/apex/com.android.vndk"):
        return "vndk"
    return "system"


def search_dirs(bits, vndk_apexes):
    lib = "lib64" if bits == 64 else "lib"
    # the vendor linker namespace: odm, vendor, then VNDK, then LL-NDK from system
    return ([f"/odm/{lib}", f"/vendor/{lib}"] + [f"{a}/{lib}" for a in vndk_apexes]
            + [f"/system/{lib}", f"/system_ext/{lib}"])


class Collector:
    def __init__(self, phone, out):
        self.phone, self.out = phone, out
        self.entries = {}          # path -> dict
        self.missing = []          # (what, wanted by)

    def save(self, path, kind, needed_by=None):
        if path in self.entries:
            if needed_by:
                self.entries[path]["needed_by"].add(needed_by)
            return self.entries[path]
        data = self.phone.read(path)
        if not data:
            self.missing.append((path, needed_by or kind))
            return None
        dest = os.path.join(self.out, "root", *path.lstrip("/").split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        entry = {"path": path, "kind": kind, "origin": origin(path), "size": len(data),
                 "sha256": hashlib.sha256(data).hexdigest(), "elf": foxtools.elf_info(data),
                 "needed_by": {needed_by} if needed_by else set()}
        self.entries[path] = entry
        print(f"  {kind:<8} {path} ({len(data)} bytes)")
        return entry

    def follow(self, vndk_apexes):
        """Copy the libraries every saved ELF needs, recursively."""
        queue = [e for e in self.entries.values() if e["elf"]]
        resolved = {}              # (bits, name) -> path or None
        while queue:
            entry = queue.pop()
            if entry["origin"] == "system":
                continue           # recovery provides its own system libraries
            bits, needed, _ = entry["elf"]
            for name in needed:
                if name in BIONIC:
                    continue
                key = (bits, name)
                if key not in resolved:
                    candidates = [f"{d}/{name}" for d in search_dirs(bits, vndk_apexes)]
                    resolved[key] = self.phone.first_file(candidates)
                path = resolved[key]
                if path is None:
                    self.missing.append((f"{name} ({bits}-bit)", entry["path"]))
                    continue
                known = path in self.entries
                lib = self.save(path, "lib", needed_by=entry["path"])
                if lib and not known and lib["elf"]:
                    queue.append(lib)

    def write_manifest(self, services):
        with open(os.path.join(self.out, "MANIFEST.tsv"), "w") as f:
            f.write("kind\torigin\tbits\tpath\tsize\tsha256\tneeded_by\n")
            for e in sorted(self.entries.values(), key=lambda e: (e["kind"], e["path"])):
                bits = e["elf"][0] if e["elf"] else ""
                f.write(f"{e['kind']}\t{e['origin']}\t{bits}\t{e['path']}\t{e['size']}\t"
                        f"{e['sha256']}\t{','.join(sorted(e['needed_by']))}\n")
        with open(os.path.join(self.out, "services.txt"), "w") as f:
            for rc, svc in services:
                f.write(f"service {svc['name']} {svc['path']} {' '.join(svc['args'])}".rstrip()
                        + f"\n    # from {rc}\n")
                for opt in svc["options"]:
                    f.write(f"    {opt}\n")
                f.write("\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", help="output directory (created; a .zip of it is written next to it)")
    ap.add_argument("-s", "--serial", help="adb serial, if more than one device is connected")
    args = ap.parse_args()

    if os.path.exists(args.out) and os.listdir(args.out):
        foxtools.die(f"{args.out} exists and is not empty")
    if shutil.which("adb") is None:
        foxtools.die("adb not found")
    phone = Phone(args.serial)
    if phone.sh("id -u").strip() != "0":
        foxtools.die("no root shell: allow 'Shell' in Magisk/KernelSU and check `adb devices`")

    firmware = phone.sh("getprop ro.build.version.incremental").strip()
    device = phone.sh("getprop ro.product.device").strip()
    print(f"device:   {device}\nfirmware: {firmware}")
    if device != "a15":
        print(f"warning: this is '{device}', not 'a15'", file=sys.stderr)

    os.makedirs(args.out, exist_ok=True)
    col = Collector(phone, args.out)

    # 1. init scripts that start TEE/keystore services, and their binaries
    rc_files = split_rc_dump(phone.sh(f'for f in {RC_GLOBS}; do [ -f "$f" ] || continue; '
                                      f'echo "### $f"; cat "$f"; echo; done'))
    services, binaries = [], {}
    for rc, text in sorted(rc_files.items()):
        hits = [s for s in parse_rc(text)
                if SERVICE_RE.search(s["name"]) or SERVICE_RE.search(os.path.basename(s["path"]))]
        if not hits:
            continue
        col.save(rc, "rc")
        for s in hits:
            services.append((rc, s))
            if s["path"].startswith(("/vendor/", "/odm/")):
                binaries.setdefault(s["path"], f"service {s['name']}")
    for path in phone.files_in(BIN_DIRS):
        if SERVICE_RE.search(os.path.basename(path)):
            binaries.setdefault(path, None)
    print(f"services: {', '.join(s['name'] for _, s in services) or 'none found'}")
    for path, started_by in sorted(binaries.items()):
        col.save(path, "binary", needed_by=started_by)

    # 2. HAL modules libhardware loads by name (gatekeeper.<x>.so, keystore.<x>.so)
    for path in phone.files_in(HAL_DIRS):
        if HAL_MODULE_RE.search(os.path.basename(path)):
            col.save(path, "hal")

    # 3. the libraries all of the above load
    vndk = sorted(phone.lines("ls -d /apex/com.android.vndk.v* 2>/dev/null"))
    vndk = [v for v in vndk if "@" not in v]   # skip the versioned duplicate mounts
    print(f"vndk:     {', '.join(vndk) or 'none'}")
    col.follow(vndk)

    # 4. trusted apps and their secure-world drivers
    for path in phone.lines("find /vendor/tee -type f 2>/dev/null"):
        col.save(path, "ta")

    # 5. vintf fragments and build.prop files
    col.save("/vendor/etc/vintf/manifest.xml", "vintf")
    for path in phone.files_in(VINTF_DIRS):
        if VINTF_RE.search(os.path.basename(path)):
            col.save(path, "vintf")
    for path in PROP_FILES:
        if phone.first_file([path]):
            col.save(path, "prop")

    col.write_manifest(services)
    total = sum(e["size"] for e in col.entries.values())
    counts = {}
    for e in col.entries.values():
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    print("\ncopied:   " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
          + f" ({total / 1048576:.1f} MiB)")
    if col.missing:
        grouped = {}
        for what, by in col.missing:
            grouped.setdefault(what, []).append(by)
        print("not found on the phone (fine for libraries the recovery provides itself):")
        for what, by in sorted(grouped.items()):
            more = f" and {len(by) - 1} more" if len(by) > 1 else ""
            print(f"  {what}  (wanted by {by[0]}{more})")
        with open(os.path.join(args.out, "MISSING.txt"), "w") as f:
            f.writelines(f"{what}\t{by}\n" for what, by in col.missing)

    archive = shutil.make_archive(os.path.abspath(args.out.rstrip("/\\")), "zip", args.out)
    print(f"\nwrote {archive}")
    if not services:
        print("warning: no TEE services found in the vendor init scripts", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
