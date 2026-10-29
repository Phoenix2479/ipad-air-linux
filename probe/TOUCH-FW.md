# Boot the J72 N1 firmware and read its descriptors (Stage C2)

This one-shot module runs the [Stage C1](TOUCH-ID.md) power-up and identity
read, then `apple_n1`'s `boot_stage=2`:

1. Prox calibration: 128 zero bytes to `0x10009600`. iOS 12 sends this on
   J72 before MtCl; `apple_n1` does not.
2. MtCl: this unit's 1,024-byte calibration to `0x10009000`, in a 1,008-byte
   and a 16-byte packet.
3. The 57,016-byte C1F15,1 firmware packet, sent as-is.
4. The version read, then the five calibration register writes (const-cal 3
   for `0x434d15c1`), the `SPI_APU_EN` read and the calibration request.
5. Execute at `0x10003400`, then 42 ms.
6. Discovery: E2 device info, then reports D1, D3, D0, A1 and D9.

Each upload uses the shared Z2 framing (`18 e1 30 01`, length in words,
address, header checksum, halfword-swapped payload, payload checksum) and
expects the ACK `4bc1`, with up to five attempts. Register writes expect
`4ad1`. The calibration request is checked for transport only, like Apple's
path.

The module requests no interrupt and registers no input device. After
discovery it tears down exactly like C1: reset low, SPI device removed, clock
off, rails off.

## Files

The firmware and calibration come from `tools/package-touch-firmware.py`,
in the ignored `source/build/j72-touch-firmware-20261029/`. They are never
committed. The runner uploads both to `/lib/firmware/apple/` on the device's
RAM filesystem (the calibration with mode 0600) and checks their device-side
SHA-256. The module loads them with `request_firmware_direct()`, because this
kernel has `CONFIG_FW_LOADER_USER_HELPER_FALLBACK=y` and a plain
`request_firmware()` would wait for userspace on a missing file. Both files
are loaded and validated before anything is powered. A missing file, a wrong
size, a bad firmware checksum or a blank calibration refuses with no
hardware access.

No firmware or calibration byte is logged. Upload transfers use a NULL phase,
which logs only a failure and its length. The per-packet log line shows the
12-byte header only.

The serial shell echoes everything it receives, and before 29 October
`tools/session.py` appended that echo to `logs/serial.log`, so the first C2
staging left both files' base64 there (ignored, on the Mac only). Upload
chunks now pass `log=False` and are recorded as `[upload chunk not logged]`.

## Long transfers

Every C1 transfer was 16 bytes or less and fit one FIFO fill. C2 is the first
use of the `j72_spi_ctrl` FIFO refill loop and of RXCNT/TXCNT values above
16, up to `0xdeb8` for the firmware, and those count registers' width is not
confirmed. The 144-byte prox packet is the first long transfer, so its ACK is
the first signal. A failure there shows as a PIO timeout or an ACK mismatch
and unwinds safely.

## Display sync

C1 showed the boot ROM answers without display sync. The running firmware
may need it to scan. C2 still leaves pin 119 low. If discovery fails after a
clean upload and execute, add display sync before suspecting anything else.

## Surface identity

`apple_n1` refuses a D9 surface other than the J71t's 14745x19657. C2 logs
the surface and marks whether it matches, but does not refuse, because the
J72 panel geometry is not yet confirmed. It still refuses inverted edges.

## Device run

Run 1 (`logs/probe-j72-touch-fw-20261029.log`, module `8b73551a...`) passed
on 29 October, on the same boot as Stage B run 3 and C1 run 1. The loader
returned 0.

- All four uploads (prox, two MtCl packets, firmware) got `4bc1` on the first
  attempt. The 57,016-byte transfer worked, so the controller's FIFO refill
  and its count registers handle transfers up to at least `0xdeb8` bytes.
- All five calibration register writes got `4ad1`. `SPI_APU_EN` read 0 and
  the calibration request returned `0x4969`.
- After execute, the firmware answered E2 with interface 1 and max packet
  2454, and every report on the first attempt:

```text
report d1 length=1 data=15
report d3 length=12 data=01 28 1e 02 10 00 28 1e 96 09 00 00
report d0 length=8 data=01 01 00 28 01 00 1e 00
report a1 length=6 data=00 00 05 00 00 02
report d9 length=16 data=99 39 00 00 c9 4c 00 00 90 ff 90 ff ac 38 e2 4b
N1 ready: max packet 2454, family 21, sensor 40x30, FW 0x0210,
          surface 14745x19657 (J71t), x=-112..14508 y=-112..19426
stage C2 complete; descriptors read, no input device
final version=0x434d15c1 complete=1 pmgr_after=0x00000200 gpio15=0x00072602
      gpio79=0x00072202 gpio119=0x00072602 cleanup=0
```

Afterwards pwm0 was off, all four pins were unclaimed, there was no SPI
device, and there was no oops, SError or PIO error; taint was 4096.

What this shows:

- The J72 surface is 14745x19657, the same as the J71t, so `apple_n1`'s D9
  identity check holds on this unit. The edges are x -112..14508 and y
  -112..19426.
- The sensor is 40x30 and the firmware reports version 0x0210.
- The firmware boots and answers without display sync. Whether contact
  scanning needs it is still open for Stage D.
- D3 is 12 bytes here, longer than the 5 bytes `apple_n1` uses, so it went
  through E7. `apple_n1` accepts longer descriptors.

## Build and test

Build `j72_touch_power.c`, `j72_touch_id.c` and `j72_touch_fw.c` together.

```sh
python3 -m unittest tests.test_j72_touch_fw -v
```

The fake N1 checks every upload's header, halfword swap and both checksums
against the files the stub loader returned, and that the firmware is sent
verbatim. It answers reports only after execute. The tests inject a fault at
each of the 53 transfers, at every upload and register ACK, and in every file
check, and confirm that each case ends in the safe state with no leak and no
logged payload. Mutations that drop the byte swap, the prox upload or the
execute, or swap the const-cal choice, all fail the suite.

The receipt is in `source/build/j72-touch-fw-20261029/`.

## Load

On a boot where `j72_spi_ctrl` is bound, run `tools/probe_once.py` for
`j72_touch_fw`, first without `--load`. A failed load is freed and leaves the
rerun state, so a corrected module can follow on the same boot.
