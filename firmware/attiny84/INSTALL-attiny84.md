# ATtiny84 Firmware — Installation Guide

Setup instructions for programming the OpenFlaps ATtiny84 module firmware using Arduino IDE and ATTinyCore.

---

## Requirements

- Arduino IDE 2.x
- One of the following programmers:
  - Arduino Uno, Nano, or Micro (used as ISP programmer), **or**
  - USBasp programmer
- 6-pin ICSP cable or jumper wires
- 10µF capacitor (if using Uno or Nano as ISP)

---

## 1. Install ATTinyCore

ATTinyCore adds ATtiny84 support to the Arduino IDE.

1. Open Arduino IDE
2. Go to **File → Preferences**
3. Paste the following URL into **Additional Boards Manager URLs**:
   ```
   http://drazzy.com/package_drazzy.com_index.json
   ```
4. Click **OK**
5. Go to **Tools → Board → Boards Manager**
6. Search for **ATTinyCore**
7. Install version **1.5.2**

> ⚠️ **Known issue:** ATTinyCore 1.5.2 is incompatible with avrdude 7.x and 8.x.
> If you get `Error: unknown token` or `syntax error` in `avrdude.conf`, see the
> [Troubleshooting](#troubleshooting) section below.

---

## 2. Configure Your Programmer

You need an external programmer to program the ATtiny84 since it has no USB port.
Two options are supported:

---

### Option A — Arduino as ISP

1. Connect your Arduino (Uno, Nano, or Micro) to your computer via USB
2. In Arduino IDE, go to **File → Examples → ArduinoISP**
3. Select your Arduino board under **Tools → Board**
4. Select the correct port under **Tools → Port**
5. Upload the **ArduinoISP** sketch to your Arduino

> ⚠️ If using a **Uno or Nano**, place a **10µF capacitor** between the **RESET**
> and **GND** pins of the programmer Arduino after uploading ArduinoISP. This
> prevents it from resetting during programming. Do **not** add this cap when
> using a Micro.

---

### Option B — USBasp

The USBasp is a dedicated AVR programmer that plugs directly into USB with no
setup sketch required. It's cheap (~$3), reliable, and works out of the box.

**Driver installation:**

- **Windows:** Install the USBasp driver using [Zadig](https://zadig.akeo.ie/) —
  select USBasp and install the WinUSB driver
- **macOS / Linux:** No driver needed, works natively

**In Arduino IDE:** select **Tools → Programmer → USBasp**

> ℹ️ The USBasp is the simpler option if you have one available — no sketch to
> upload, no capacitor needed, and no COM port to select.

---

## 3. Wiring — Programmer to ATtiny84 ICSP Header

Connect your programmer to the OpenFlap module's **ICSP header**.

ICSP header pinout (looking at the connector on the PCB):

```
┌─────────────┐
│ MISO │ VCC  │  pin 1  pin 2
│ SCK  │ MOSI │  pin 3  pin 4
│ RST  │ GND  │  pin 5  pin 6
└─────────────┘
```

**Arduino as ISP wiring:**

| Arduino Pin | ICSP Pin | Signal |
|---|---|---|
| 5V | Pin 2 | VCC |
| GND | Pin 6 | GND |
| Pin 11 | Pin 4 | MOSI (PA6) |
| Pin 12 | Pin 1 | MISO (PA5) |
| Pin 13 | Pin 3 | SCK (PA4) |
| Pin 10 | Pin 5 | RESET (PB3) |

**USBasp wiring:**

The USBasp has a standard 10-pin AVR header. Use a 10-pin to 6-pin ICSP adapter
cable (included with most USBasp boards) to connect directly to the ICSP header.
If wiring manually:

| USBasp Pin | ICSP Pin | Signal |
|---|---|---|
| MOSI (pin 1) | Pin 4 | MOSI (PA6) |
| VCC (pin 2) | Pin 2 | VCC |
| SCK (pin 3) | Pin 3 | SCK (PA4) |
| MISO (pin 9) | Pin 1 | MISO (PA5) |
| RST (pin 5) | Pin 5 | RESET (PB3) |
| GND (pin 10) | Pin 6 | GND |

> If you have a 6-pin ICSP cable you can connect the two ICSP headers directly
> without individual jumper wires.

---

## 4. Configure Board Settings

1. Go to **Tools → Board → ATTinyCore → ATtiny84/84A**
2. Set the following options:

| Setting | Value |
|---|---|
| Chip | ATtiny84A |
| Clock | 8 MHz (internal) |
| BOD | Disabled |
| Save EEPROM | EEPROM retained |
| Timer 1 Clock | CPU |
| LTO | Disabled |
| Programmer | Arduino as ISP **or** USBasp |

---

## 5. Burn the Bootloader (Set Fuses)

This step sets the ATtiny84 fuses to run at 8 MHz internal clock. You only need
to do this once per chip.

1. Make sure your wiring is correct and the programmer Arduino is connected
2. Go to **Tools → Programmer → Arduino as ISP**
3. Go to **Tools → Burn Bootloader**

The IDE will configure the fuses. This takes a few seconds.

### Verify Fuses

To confirm the fuses are correctly set, run the following command.

**Using Arduino as ISP — macOS / Linux:**
```bash
avrdude -c stk500v1 -p t84 -P /dev/ttyUSB0 -b 19200 \
  -U lfuse:r:-:h \
  -U hfuse:r:-:h \
  -U efuse:r:-:h
```

**Using Arduino as ISP — Windows:**
```bash
avrdude -c stk500v1 -p t84 -P COM3 -b 19200 \
  -U lfuse:r:-:h \
  -U hfuse:r:-:h \
  -U efuse:r:-:h
```

**Using USBasp — all platforms:**
```bash
avrdude -c usbasp -p t84 \
  -U lfuse:r:-:h \
  -U hfuse:r:-:h \
  -U efuse:r:-:h
```

Expected output:
```
lfuse = 0xe2   ← 8 MHz internal, no clock divider
hfuse = 0xd7   ← EEPROM retained
efuse = 0xff   ← factory default
```

> ⚠️ If `lfuse` reads `0x62` the chip is running at 1 MHz (CKDIV8 active).
> Re-run Burn Bootloader to fix this.

---

## 6. Upload the Firmware

1. Open `firmware/attiny84/split_flap_display_async/split_flap_display_async.ino` in Arduino IDE
2. Confirm board and programmer settings are still correct (see step 4)
3. Click **Upload** (or press `Ctrl+U`)

The IDE will compile and upload via the Arduino as ISP programmer.

> ⚠️ The ATtiny84 has no hardware UART so you cannot use the Serial Monitor
> for debugging. Use the Pico's I²C interface to query module state instead.

---

## Troubleshooting

### `Error: unknown token` / `syntax error in avrdude.conf`

This is a known incompatibility between ATTinyCore 1.5.2 and avrdude 7.x/8.x.
See [ATTinyCore issue #923](https://github.com/SpenceKonde/ATTinyCore/issues/923).

**Fix on macOS — downgrade avrdude:**
```bash
brew uninstall avrdude
brew install avrdude@6
echo 'export PATH="/usr/local/opt/avrdude@6/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**Alternative — use Arduino IDE 1.8.19** which bundles avrdude 6.3 and is fully
compatible with ATTinyCore 1.5.2.

---

### Upload fails with `programmer is not responding`

**Arduino as ISP:**
- Check all 6 ICSP wires are connected correctly
- Make sure ArduinoISP sketch is loaded on the programmer Arduino
- Confirm the correct COM port is selected
- If using Uno/Nano, verify the 10µF cap is in place on the programmer's RESET pin
- Try a lower baud rate: add `-b 9600` to your avrdude command

**USBasp:**
- On Windows, confirm the WinUSB driver is installed via Zadig
- Try a different USB port or cable
- Some cheap USBasp clones need a firmware update — check the seller's instructions

---

### Stepper runs very slowly after upload

The chip is likely running at 1 MHz instead of 8 MHz. Re-run **Burn Bootloader**
to correct the fuse settings, then re-upload the sketch.

---

### EEPROM wiped after re-upload

Check that `hfuse = 0xd7` (EEPROM retained). If it reads `0xdf` the EEPROM is
being erased on every upload. Re-run Burn Bootloader with **Save EEPROM →
EEPROM retained** selected in the board settings.

---

## Fuse Reference

| Fuse | Value | Meaning |
|---|---|---|
| lfuse | 0xe2 | 8 MHz internal oscillator, no clock divider |
| hfuse | 0xd7 | EEPROM retained, RESET enabled, SPI enabled |
| efuse | 0xff | BOD disabled |

> ⚠️ Never modify the `RSTDISBL` bit in hfuse. Setting it disables the RESET
> pin and permanently prevents ISP programming.
