---
name: phone-debugging
description: Diagnose a phone that will not connect, transfer files, charge, pair, or talk to Linux desktop tools — especially Android USB/MTP, ADB, fastboot, Bluetooth, Wi-Fi, and desktop integration. Use when the phone is missing from Thunar, Dolphin, or another file manager, `lsusb` sees it but no volume mounts, `gio mount` fails with "Unable to open MTP device", `mtp-detect` reports the USB interface is busy, `adb devices` shows `unauthorized` or no device, charging is unreliable, Bluetooth pairing fails, or KDE Connect, LocalSend, scrcpy, or a hotspot cannot be discovered.
---

# Linux phone troubleshooting

Use this skill when a phone does not connect, transfer files, appear in a file manager, charge reliably from a computer, pair, or communicate with Linux tools. It is a general troubleshooting workflow, not an MTP-only recipe.

## Operating principles

1. **Find the failing layer before changing anything.** Separate physical connection, kernel/USB detection, protocol/backend detection, mounting or pairing, and the user-facing application.
2. **Gather evidence before installing, removing, restarting, or changing permissions.** A device can be detected by one layer and still be unavailable to the next.
3. **Prefer read-only diagnostics and graceful, targeted actions.** Use `sudo` only when required. Explain what a command does when it changes state.
4. **Do not assume the desktop environment.** Identify the file manager and desktop stack. Thunar commonly uses GVFS; Dolphin commonly uses KDE's KIO integration. Other applications may use ADB, MTP libraries, or their own transport.
5. **Do not treat all phone problems as USB problems.** Determine whether the symptom concerns charging, data transfer, ADB, Bluetooth, Wi-Fi, notifications, or a particular application.
6. **Keep hypotheses distinct from facts.** State what the output proves, what it suggests, and what remains unknown.
7. **Make the smallest reversible change.** Do not remove a desktop component or disable a service globally just because stopping one process temporarily fixes a conflict.
8. **Respect data safety.** Never suggest factory resets, formatting, bootloader unlocking, firmware flashing, `adb` data deletion, or destructive recovery steps without an explicit need, a clear warning, and a verified backup.

## 1. Establish the situation

Collect only details that are not already available in the conversation:

- Phone make/model and Android/iOS version, if relevant.
- Linux distribution and desktop environment/session.
- File manager or application involved.
- Exact symptom and exact error text.
- Connection type: USB, Bluetooth, Wi-Fi, or cellular.
- Whether the phone is unlocked and which USB mode or permission prompt it shows.
- Whether this worked before and what changed recently.

Do not ask the user to repeat information already provided. If one key fact is missing, ask one focused question; otherwise proceed with diagnostics.

## 2. Classify the failing layer

Use this decision sequence for USB-connected phones:

| Observation | Likely layer to investigate next |
|---|---|
| No USB device appears at all | Cable, port, hub, phone connector, host USB/controller, kernel |
| `lsusb` sees the phone, but no phone volume appears | Phone USB mode, MTP/ADB protocol, userspace monitor/backend |
| A volume appears, but mounting fails | Device busy/claimed, permissions, protocol/backend error |
| The mount succeeds, but the file manager shows nothing | File-manager integration, mount path, sidebar refresh |
| ADB sees no device, but MTP works | USB debugging, authorization, ADB server/rules |
| MTP works, but ADB does not | ADB-specific configuration; do not disturb MTP unnecessarily |

Do not jump from “the phone is absent in Thunar” straight to cable replacement or package installation. First determine whether the kernel, GVFS, or KIO can already see it.

## 3. USB baseline checks

Start with low-risk checks:

```bash
lsusb
lsusb -t
```

For live kernel messages, plug and unplug the phone while running:

```bash
sudo journalctl -kf
```

Stop the live log with `Ctrl+C`. On systems without a useful systemd journal, use the distribution's kernel log mechanism.

On the phone:

- Unlock the screen.
- Check the USB notification and explicitly select the intended mode, usually **File transfer / Android Auto (MTP)** for file browsing.
- Accept any data-access or computer-authorization prompt.
- Test a known data-capable cable and a direct computer port if USB enumeration is absent. Some cables are charge-only.

Do not assume that a charging icon proves data connectivity.

## 4. Android file transfer over MTP

### Identify the active file-manager stack

- **Thunar and many GTK file managers:** normally use GVFS and its MTP volume monitor.
- **Dolphin:** normally uses KDE KIO's MTP integration.
- Having multiple stacks installed is not automatically a problem. Investigate actual device ownership before concluding they conflict.

On Arch Linux, inspect package state before installing anything:

```bash
pacman -Q gvfs gvfs-mtp kio-extras libmtp
```

A missing package causes `pacman -Q` to report an error for that package. For Thunar MTP support, `gvfs` and `gvfs-mtp` are the relevant GVFS components. Install only missing, relevant packages:

```bash
sudo pacman -S --needed gvfs gvfs-mtp
```

Do not install Dolphin solely to debug Thunar. On other distributions, identify the equivalent packages using that distribution's package manager and current package documentation.

### Check whether GVFS detects the phone

```bash
gio mount -li
```

Look for a volume of type `GProxyVolumeMonitorMTP`, its `activation_root` URI, and `can_mount=1`.

Interpretation:

- **No MTP volume:** check phone USB mode, GVFS MTP monitor, package state, and logs.
- **MTP volume present:** the phone has been discovered by GVFS. This does not prove that GVFS can open or mount it.
- **`can_mount=1`:** GVFS considers it mountable; it does not guarantee the USB interface is currently available.

Use the exact `activation_root` shown on the user's machine. It is device-specific and may change after reconnecting:

```bash
gio mount 'mtp://<value-from-activation_root>'
```

If it mounts, inspect the GVFS mount directory:

```bash
ls -lah "$XDG_RUNTIME_DIR/gvfs/"
```

Then open that directory or its MTP subdirectory in the user's file manager, for example:

```bash
thunar "$XDG_RUNTIME_DIR/gvfs/"
```

Do not copy a device URI, bus/device number, or serial-like identifier from an old session. Re-read it after reconnecting.

### Diagnose “device busy” or “Unable to open MTP device”

A diagnostic such as `mtp-detect` may report that the USB interface is busy. This can mean another process already has the interface open; it does **not** by itself mean the phone is broken.

Get the current USB device node from `gio mount -li` (`unix-device`, e.g. `/dev/bus/usb/BBB/DDD`) or correlate it with the current `lsusb` output. Then inspect its owner:

```bash
sudo fuser -v /dev/bus/usb/BBB/DDD
ps -eo pid,comm,args | grep -Ei '[k]iod|[k]io.*worker|[g]vfsd|[m]tp'
```

Replace the placeholder with the current device node; never use an old bus/device number blindly.

If a process is listed as holding the device:

1. Identify which component it belongs to and whether it is expected to be using MTP.
2. Close any file-manager windows or applications actively browsing the phone.
3. Prefer that component's graceful quit/release mechanism. Do not use `kill -9` as the first response.
4. Re-run `fuser` to confirm the device was released.
5. Retry the target backend's mount operation.

For a **confirmed KDE `kiod6` holder** when the user wants GVFS/Thunar to handle the phone, a graceful, targeted workaround is:

```bash
kquitapp6 kiod6
```

Then verify that the current USB device node is no longer held and retry the GVFS mount. This is a diagnostic/workaround, not proof that KDE should be uninstalled or globally disabled. Check whether the process respawns and investigate a durable, narrow configuration only after reproducing the conflict. Do not terminate unrelated GVFS processes when GVFS is the desired backend.

`mtp-detect` from libmtp can provide useful protocol diagnostics, but run it only after checking whether another process owns the interface. If it reports “device busy,” find the owner first rather than repeatedly running it.

### Important distinction

The following are separate states:

1. USB enumeration (`lsusb`).
2. MTP volume discovery (`gio mount -li`).
3. Exclusive access to the USB interface (`fuser`).
4. Successful MTP mount (`gio mount`).
5. Visibility in the file manager.

Report exactly which state succeeds and which fails.

## 5. Android Debug Bridge (ADB) and fastboot

Use these only when the task involves debugging, shell access, app installation, logs, or bootloader communication—not as a substitute for MTP file browsing.

```bash
adb version
adb devices -l
```

Check the phone for **Developer options → USB debugging**, then accept the computer's RSA authorization prompt. A device listed as `unauthorized` means the phone has not authorized that host; it is not the same as a missing USB connection.

If the ADB server itself appears stale, a targeted restart may help:

```bash
adb kill-server
adb start-server
adb devices -l
```

Do not run ADB as root by default. Do not run `adb reboot bootloader`, `fastboot flashing unlock`, `fastboot erase`, sideload, or other state-changing commands unless they are explicitly required and the user understands the consequences. Bootloader unlocking commonly wipes data; verify current device/vendor behavior before advising it.

## 6. Charging and battery symptoms

First separate **charging from a computer's USB port** from charging through a wall adapter. A computer port may provide different power from a dedicated charger, and USB data negotiation is separate from charging.

Safe checks:

- Compare a known-good cable, charger, and wall outlet.
- Inspect the port visually for lint, corrosion, or physical damage; do not insert metal tools.
- Check whether charging changes when the cable is moved gently; stop if the connector is loose or hot.
- Note overheating, swelling, smell, or deformation. If a battery is swollen, unusually hot, or damaged, stop using/charging the device and seek qualified repair; do not open or puncture it.

Do not promise that “battery calibration” fixes battery degradation. Linux cannot reliably diagnose every phone battery fault from USB enumeration alone.

## 7. Bluetooth problems

Establish whether the phone, Linux adapter, or pairing state is failing:

```bash
rfkill list
systemctl status bluetooth
bluetoothctl show
bluetoothctl devices
```

For deeper diagnosis, inspect the Bluetooth service log:

```bash
journalctl -u bluetooth -b --no-pager
```

Check adapter power, discoverability, pairing authorization, stale pairings, and whether the device is already connected elsewhere. Do not remove all pairings as a first step. Audio problems may be profile/codec/routing problems rather than pairing failures.

## 8. Wi-Fi, hotspot, and phone-to-PC network features

Determine whether the phone and computer are on the same network, whether a VPN or firewall changes routing, and whether the feature requires local-network discovery or an account/cloud service.

For Linux, inspect the relevant network state using the tools already installed (for example, `nmcli device status` and `ip route`). Do not disable the firewall or VPN wholesale. Test a narrow rule or temporary, reversible change only when evidence points to it, and restore the original state afterward.

For KDE Connect, LocalSend, scrcpy over TCP, or similar applications, separate network reachability from application discovery and authorization. Verify the application's supported ports/protocols against current documentation before changing firewall rules.

## 9. Logs and escalation

When basic checks do not isolate the fault, collect focused logs while reproducing it:

```bash
sudo journalctl -kf
journalctl --user -f
```

Use the relevant service-specific log when known. Avoid dumping the entire journal or exposing unrelated personal data. Review outputs for phone serial numbers, usernames, file paths, Wi-Fi names, IP addresses, and tokens before sharing them externally.

Use web/documentation lookup when package names, service behavior, known bugs, or commands may have changed. Prefer current upstream documentation, distribution documentation, and issue reports that match the exact desktop/backend versions. Do not infer that a bug report describes the user's system merely because the error text looks similar.

## 10. Actions to avoid unless specifically justified

Never use these as generic troubleshooting steps:

- `chmod 666` or broad permission changes on `/dev/bus/usb/*`.
- Disabling all udev rules or changing ownership of USB device nodes globally.
- Killing every `gvfsd`, KIO, DBus, or desktop process.
- Repeatedly running MTP clients while another process owns the interface.
- Uninstalling `kio-extras`, GVFS, or desktop components before proving they are the cause and checking dependencies.
- Running GUI file managers as root.
- Factory resetting the phone, formatting storage, unlocking the bootloader, flashing firmware, or deleting user data as an early diagnostic.
- Installing unrelated packages “just in case.”

## 11. How to report the diagnosis

Give the user a short explanation with four parts:

1. **What works:** for example, “USB enumeration and GVFS MTP discovery both work.”
2. **Where it fails:** for example, “GVFS cannot open the device because `fuser` shows `kiod6` holding the USB node.”
3. **Smallest safe fix:** the one targeted action that addresses the proven failure.
4. **Verification:** the command or visible result that confirms recovery.

Clearly label any remaining uncertainty. Once the immediate issue is fixed, offer a persistent fix only if it can be made narrowly and safely; do not silently leave behind system-wide changes.

## Worked example: Android MTP conflict between KIO and GVFS

A Galaxy A50 on Arch Linux was absent from Thunar and produced a KDE `Malformed URL mtp:udi=...` error.

Evidence collected:

- `lsusb` showed Samsung USB ID `04e8:6860`: the USB device enumerated.
- `gio mount -li` showed a `GProxyVolumeMonitorMTP` volume with an `activation_root` and `can_mount=1`: GVFS discovered the MTP volume.
- `gio mount <activation_root>` failed with `Unable to open MTP device`: discovery had succeeded, but opening/mounting had not.
- `mtp-detect` reported `libusb_claim_interface() ... device is busy`.
- `sudo fuser -v <current-usb-device-node>` identified `/usr/lib/kf6/kiod6` as the process holding the USB node.
- `kquitapp6 kiod6` gracefully released it; GVFS could then mount the phone and Thunar could access it.

Lesson: do not replace cables, reinstall MTP packages, or change USB permissions when the evidence already shows successful enumeration and GVFS discovery. Identify the process holding the current USB node, release it safely, and verify the mount. This example is evidence for that particular machine/session, not a rule that every MTP failure is caused by KIO.
