# Testing the a15 build

Collect a recovery log for every operation (Menu → Logs → copy to MicroSD,
or `adb pull /tmp/recovery.log`) and name it after the test. Without
decryption, save logs to MicroSD or OTG, not internal storage.

## First boot

1. Odin: flash `*-odin.tar` in AP, Auto Reboot off.
2. Hold Volume Up + Power with a USB cable connected until OrangeFox starts.
3. Before booting Android, keep the recovery: root, flash a custom kernel,
   or run `multidisabler` from the terminal (back up Boot first).

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
| EFS | `sec_efs` is Android's `/efs`; `efs` is `/mnt/vendor/efs` (`/vendor_efs` in recovery) | confirmed |
| `/sdcard1` sysfs | `/devices/platform/soc/11240000.mmc*` | confirmed |
| `/usb_otg` sysfs | `/devices/platform/soc/mt_usb*` (stock vold entry; the musb driver renames `11200000.usb0` to `mt_usb`) | not tested: check with an OTG drive in recovery |
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
- [ ] Settings persist after a reboot
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
      FocalTech FT3418)
- [ ] Touch with stock firmware installed, and again with `super` wiped or
      a GSI installed (works only once the touch drivers load from the
      ramdisk, see below)
- [ ] Brightness slider covers the full range without jumping to HBM
- [ ] Battery percentage and charging indicator
- [ ] Vibration (sec_vibrator, `/sys/class/timed_output/vibrator`)
- [ ] Status bar clears the cutout; clock left and right; rounded corners

## Known limitations to state in every release

- No /data decryption (Samsung TEEGRIS). Internal storage is only readable
  between Format Data and the next Android boot, which encrypts it again;
  use MicroSD, OTG or ADB sideload.
- Settings, themes and the recovery password live under /sdcard/Fox, so
  they are lost once Android encrypts /data again.
  `FOX_USE_DATA_RECOVERY_FOR_SETTINGS=1` (experimental) keeps them in
  /data/recovery instead; worth a separate test build.
- Touch needs a stock-based ROM until the tree is refreshed with
  `extract-prebuilts.py --vendor-files`: the touch drivers need
  `tuihw-inf.ko`, which the stock recovery ramdisk does not have, so the
  recovery takes them from the installed ROM's `vendor_dlkm`.
  `preflight.py` shows which case the tree is in.
- No flashlight (the flash LED modules aren't in the recovery ramdisk).
- No CPU temperature (thermal modules aren't loaded in recovery).
