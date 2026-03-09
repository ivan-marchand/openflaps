# Print & Assembly Guide

Instructions for 3D printing and assembling the OpenFlaps character modules.

---

## Parts list (per character module)

| File | Qty | Notes |
|---|---|---|
| `Split Flap Character - Frame.stl` | 1 | Main structural body |
| `Split Flap Character - Left Wheel.stl` | 1 | |
| `Split Flap Character - Right Wheel.stl` | 1 | |
| `Split Flap Character - Flap.stl` | 45 | One per character in the character set |
| `Split Flap Character - Front Connector.stl` | 1 | Connects modules side-by-side |
| `Split Flap Character - Back Connector.stl` | 1 | |
| `Split Flap Character - Rail.stl` | 1 | |
| `Split Flap Character - Lid.stl` | 1 | |
| `Split Flap Character - Limit Switch Support.stl` | 1 | |
| `Split Flap Character - Stepper Motor Gear Spacer.stl` | 1 | |
| `Gear - Stepper.stl` | 1 | Attaches to motor shaft |
| `Gear - Wheel.stl` | 1 | Attaches to flap wheel |

> The `mechanical/stl/Split Flap parts.3mf` file contains all parts for
> convenience if your slicer supports 3MF.

---

## Print settings

| Setting | Recommended |
|---|---|
| Layer height | 0.2 mm |
| Infill | 20% |
| Supports | None required |
| Material | PLA (fine); PETG for better heat resistance |
| Nozzle | 0.4 mm |

No supports needed — all parts are designed to print flat.

---

## Printing the character flaps

The flaps display white characters on a black background. Each physical flap
shows the **top half of one character** on its front face and the **bottom half
of the next character** on its back face.

Use the generator script to produce print-ready 3MF files:

```bash
pip install trimesh pillow scikit-image numpy
python tools/generate_flaps.py
```

Edit the parameters at the bottom of `generate_flaps.py` to change character
size, font, or color. The script outputs a `.3mf` file you can open directly
in your slicer.

---

## Assembly order

Assemble one module at a time:

1. **Wheels** — press the left and right wheels onto the axle shaft
2. **Flaps** — clip all 45 flaps onto the wheel in character-set order (see
   `char_set` in `firmware/attiny84/split_flap_display_async/split_flap_display_async.ino`)
3. **Gears** — press the wheel gear onto the wheel hub; press the stepper gear
   onto the motor shaft
4. **Frame** — insert the wheel+flap assembly into the frame
5. **Motor** — mount the 28BYJ-48 motor so the gears mesh; secure with screws
6. **Limit switch** — clip into the limit switch support, position so the flap
   wheel triggers it once per revolution
7. **Lid** — snap the lid onto the back of the frame
8. **Connectors** — attach front and back connectors to chain modules together

---

## Tips

- If gears feel stiff, lightly sand the gear teeth with 400-grit sandpaper
- If flaps don't clip on easily, check your extrusion multiplier — PLA shrinkage
  varies by brand
- The limit switch position is critical: too early or too late will cause the
  `home` routine to miscalibrate. Adjust before uploading firmware.
