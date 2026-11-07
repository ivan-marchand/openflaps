# PCBs

The three circuit boards of an OpenFlaps display. Each folder has the Gerber zip to send
to a PCB fab and the bill of materials, as exported from EasyEDA (`.xlsx`) and as `.csv`
for reading here. Part numbers are LCSC's.

| Board | Folder | Qty | What it does | Called in the assembly guide |
|---|---|---|---|---|
| Controller | [`controller/`](controller/) | 1 per character | ATtiny84 + ULN2003 stepper driver; drives the motor, reads the limit switch and the button, talks I²C | Character PCB |
| Daisy Chain | [`daisy-chain/`](daisy-chain/) | 1 per block of 4 characters | Strip across the back of the block: carries 5 V and the I²C bus to each character | Backboard PCB |
| Pico Controller | [`pico-controller/`](pico-controller/) | 1 per display | Holds the Pico W; 5 V barrel-jack input, 8 5 V outputs, I²C level converter, 2 accessory I²C connectors (e.g. the BME280 sensor) | Pico PCB |

## Ordering

Upload the Gerber zip as-is to any PCB fab (JLCPCB, PCBWay, OSH Park, …); no design software
is needed. All three are 2-layer boards. The BOMs use LCSC part
numbers, which JLCPCB's assembly service takes directly; with other fabs, look the parts up by
their manufacturer part numbers, also in the BOMs.

- **Controller** uses surface-mount parts (SOIC-14 ATtiny84, SOP-16 ULN2003, 0603 resistor,
  SMD capacitors). For fab assembly, upload the BOM and the pick-and-place file
  (`PickAndPlace_PCB_Controller.xlsx`) with the Gerbers; to assemble it yourself, the
  Gerbers include the top paste layer for a stencil.
- **Pico Controller**: the I²C logic level converter (`I2CConverter` in the BOM) is a
  plug-in module, not an LCSC part: a common 4-channel bi-directional 3.3 V ↔ 5 V I²C
  level shifter board with a 6-pin header on each side, e.g.
  [this pack of 10 on Amazon](https://www.amazon.com/dp/B08C9PFVGP) (VKLSVAN). The Pico W
  sits in the two 20-pin female headers.
- Flash the ATtiny84 on each Controller before the board goes into its character, through the
  ICSP header; see [INSTALL-attiny84.md](../../firmware/attiny84/INSTALL-attiny84.md).
