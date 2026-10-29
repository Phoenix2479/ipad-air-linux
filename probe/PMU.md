# Read the J72 touch rail registers in the D2089 PMU

This separate one-shot module reads three PMIC registers that iOS uses for the
touch supplies. It registers no driver, alias, parameter or writable interface.
It refuses unless the machine is `apple,j72`, the kernel release is
`7.0.12-j72baseline`, exactly one available node is compatible with
`apple,i2c-pmic`, that node is also `apple,anya-pmic` with `reg = <0x3c>`, its
I2C device is bound to a driver, and that driver's regmap has 8-bit values.

## Operations

The module makes three single-byte `regmap_read` calls through the existing
`simple-mfd-i2c` register map, in this order, and stops at the first error:

| Register | Meaning from iOS | Decoded bits |
| --- | --- | --- |
| `0x0084` | ldo14 enable | `0x04` enable, `0x03` shared enable |
| `0x0085` | ldo18 enable | `0x02` enable |
| `0x0398` | ldo14 voltage | `0x1f` field, `5000 + 50 * field` mV |

It logs the raw values, then the decoded fields. It performs no register
write, I2C transfer of its own or regulator request. The voltage formula is an
inference from the iOS table, not a measurement.

## What this does not establish

It does not show the actual rail voltage or whether the rail is physically
powered; a register bit is only the PMIC's setting. It does not explain bits 0
and 1 of `0x84`. It does not show that a touch controller responds or that any
write is safe.

## Build and load

Build as described in [the status probe instructions](README.md), using a new
module directory and `j72_pmu_read.ko` as the output. Verify
`modinfo -F vermagic` starts with `7.0.12-j72baseline`, upload the module to
iPad RAM, verify its SHA-256 against the host copy, and load it with the
single-call loader from [load-once.c](load-once.c):

```sh
/tmp/j72-load-once /tmp/j72_pmu_read.ko
```

A successful insertion stays loaded because the baseline disables unloading.
Reboot for another run.

## Recorded result

Not yet run on hardware.
