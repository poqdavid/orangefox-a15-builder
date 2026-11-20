# Testing the a15 build

Collect a recovery log for every operation (Menu → Logs → copy to MicroSD,
or `adb pull /tmp/recovery.log`) and name it after the test. Without
decryption, save logs to MicroSD or OTG, not internal storage.

## First boot

1. Odin: flash `*-odin.tar` in AP, Auto Reboot off.
2. Hold Volume Up + Power with a USB cable connected until OrangeFox starts.
3. OrangeFox keeps itself installed: on every boot `postrecoveryboot.sh`
   checks whether boot is still the pristine stock image and, if so, runs
   `multidisabler` to repack it. With a rooted or custom boot image it does
   nothing. Check `/tmp/recovery.log` for the "multidisabler:" lines.

If the screen stays black or the phone reboots to Download mode, keep a
copy of `/sys/fs/pstore/console-ramoops*` if you can still get it from
Android, and report the firmware version from `device-info.sh`.

## Values checked on the phone

From a `tools/device-info.sh` report (A155FXXS7CYG4, Android 15). Run
`tools/preflight.py <tree> --device-info <report>` to re-check the
partitions and sizes after a firmware update.

| Setting | Value | Status |
|---------|-------|--------|
| Screen / `OF_SCREEN_H` | 1080 x 2340 | confirmed |
| `OF_STATUS_H` | 80 | confirmed (Android's status bar frame is 80 px) |
| Backlight | `/sys/class/backlight/panel`, `max_brightness` 483 (0-255 normal, above is HBM) | confirmed |
| Battery | `/sys/class/power_supply/battery/{capacity,status}` | confirmed |
| Vibrator | `/sys/class/timed_output/vibrator` | confirmed |
| Partition sizes | recovery 80 MiB, boot 64 MiB, super 11744051200 | confirmed |
| Partitions in fstab | all 20 exist, including dtbo, vbmeta_system, vbmeta_vendor | confirmed |
| EFS | `efs` (Android: `/mnt/vendor/efs`) is `/efs` in recovery; `sec_efs` (Android: `/efs`) is `/sec_efs` | confirmed |
| `/sdcard1` | `/dev/block/mmcblk0p1` (`mmcblk0` is the MicroSD; storage is UFS) | confirmed in Android; check in recovery |
| `/usb_otg` | `/dev/block/sdd1` (first SCSI disk after the UFS LUNs `sda`-`sdc`) | not tested: check with an OTG drive in recovery (`ls /dev/block/sd*`) |
| Touch drivers | `zt7650m_ts.ko`, `focaltech_ts_ft3418.ko` and `tuihw-inf.ko` in `vendor_dlkm` | confirmed |

## Test suite

Basic:

- [ ] Flash a zip (Magisk/KernelSU zip, a ROM) from MicroSD and from OTG
- [ ] ADB sideload a zip
- [ ] Flash an image: boot, recovery (the OrangeFox image itself), vbmeta
- [ ] Backup and restore: Boot, EFS + NVRAM/NVDATA/NVCFG/Protect, Super, Data
- [ ] Backup to MicroSD and to OTG
- [ ] Read and write MicroSD (FAT32 and exFAT) and OTG (FAT32, exFAT, NTFS)
- [ ] Format Data, then boot Android
- [ ] Settings persist after a reboot. They are stored in `/cache/Fox`
      (`FOX_SETTINGS_ROOT_DIRECTORY`), prepared by `runatboot.sh`; check
      that the folder exists and that a changed setting survives a reboot to
      Android and back
- [ ] Built-in features: terminal, file manager, Fox addons, reboot to
      system/recovery/download/power off
- [ ] MTP in recovery

Advanced:

- [ ] Recovery password survives a reboot
- [ ] Theme and splash survive a reboot (splash change repacks the
      recovery image; check that it still boots afterwards)

ROMs (each Android version the device has):

- [ ] Stock One UI 6 (Android 14), One UI 7 (Android 15), One UI 8/8.5
      (Android 16): boot after flashing through Odin, recovery still boots
- [ ] A custom ROM or GSI flashed from the recovery

Device:

- [ ] Touch on both panel types if you can borrow one (Zinitix ZT7650M and
      FocalTech FT3418). `adb shell cat /proc/tsp_msg` says which driver
      bound. On a Zinitix panel it should say `Firmware image
      zt7650m_a15.bin not available` and have no `[UPGRADE]` lines, and
      `dmesg | grep zinitix_touch` that the touch is up
- [ ] Touch with stock firmware installed, and again with `super` wiped or
      a GSI installed (works only once the touch drivers load from the
      ramdisk, see below)
- [ ] Brightness slider covers the full range without jumping to HBM
- [ ] Battery percentage and charging indicator
- [ ] CPU temperature in the status bar, close to what Android shows for
      `soc_max`; `adb shell "ls -l /tmp/cpu_temp"` points at a
      thermal zone and `adb shell "dmesg | grep cpu_temp"` names it. Needs
      `soc_temp_lvts.ko` in the ramdisk (`pull-vendor-files.sh` and
      `extract-prebuilts.py --vendor-files`); without it the temperature is
      hidden
- [ ] Vibration (sec_vibrator, `/sys/class/timed_output/vibrator`)
- [ ] Status bar clears the cutout; clock left and right; rounded corners

## Decryption

Recovery starts Samsung's TEE (TEEGRIS) and the keymint, fkeymaster,
gatekeeper and weaver (hermesd) services, and decrypts /data with them
(`init.recovery.teegris.rc`, `prepare_tee.sh` in the tree). The /data keys
are bound to the Android version, the OS and vendor patch levels and the
boot patch level. The keymint trusted app takes the first three from the
keymint service (the `ro.build.version.release`,
`ro.build.version.security_patch` and `ro.vendor.build.security_patch`
properties) and uses the image header only when those are missing or
malformed; the boot patch level comes from vbmeta, the same under Android
and recovery. `prepare_tee.sh` sets the three properties to the installed
firmware's values before keymint starts, so the TEE sees what it sees under
Android on any firmware date.

Before the first test:

- Back up `efs` and `sec_efs` from Android, to MicroSD or a PC:
  `dd if=/dev/block/by-name/efs of=/sdcard/efs.img` (and `sec_efs`), then
  copy them off the phone. The TEE writes its secure storage to efs, as
  Android does on every boot.
- Back up what matters on internal storage. Decryption only reads keys, but
  a first test build is still a first test build.
- Enter the lock screen PIN carefully: wrong guesses count towards the
  lock screen's attempt limit and timeouts, as on the lock screen.

Tests (save `/tmp/recovery.log`, `/tmp/tee.log`, `dmesg` and `logcat -d`
for each):

- [ ] Lock screen with PIN: the password prompt appears and accepts it;
      internal storage shows the real files
- [ ] The same with a password and with a pattern, and with no lock screen
      (decrypts without a prompt)
- [ ] Wrong PIN: rejected, no crash; the right one works afterwards
- [ ] After decrypting: `getprop vendor.recovery.tee_state` is `stopped`
      and `/mnt/vendor/efs` is no longer mounted
- [ ] Skip the prompt, then `setprop vendor.recovery.tee_stop 1`: efs is
      released, and an EFS backup restores
- [ ] Android boots normally afterwards and the lock screen accepts the PIN
      (the keys are untouched)
- [ ] After a firmware update (a newer security patch than the one in
      `tee-files.txt`): still decrypts, `/tmp/tee.log` shows the new patch
      levels, and Android still accepts the PIN afterwards

If decryption fails, try the PIN once more, then collect everything over
adb while still in recovery:

```bash
adb shell logcat -d > logcat.txt
adb shell dmesg > dmesg.txt
adb pull /tmp/recovery.log
adb pull /tmp/tee.log
adb shell "cat /proc/keys" > keys.txt
```

These work the same from Linux, the Windows command prompt and PowerShell.
For anything longer, run `adb shell` first and type the command on the
phone: single quotes do not group words on Windows.

`/tmp/tee.log` (and the lines tagged `tee:` in dmesg) shows whether the
TEE was started; logcat (vold, keystore2, keymint, gatekeeper, hermesd)
shows where decryption stopped. `keys.txt` must have a `keyring` named
`fscrypt` (init creates it with `install_keyring`); without it, vold logs
"Unable to find device keyring" right after unwrapping the DE key.

## Known limitations to state in every release

- /data decryption needs a stock vendor partition (the TEE trusted apps and
  the versions are read from the installed firmware); without one,
  OrangeFox starts without decryption.
- Settings, themes and the recovery password are stored in the cache
  partition (`/cache/Fox`), so wiping cache resets them.
- No flashlight (the flash LED modules aren't in the recovery ramdisk).
- No CPU temperature unless `soc_temp_lvts.ko` was added to the ramdisk
  from the same firmware as the kernel.
