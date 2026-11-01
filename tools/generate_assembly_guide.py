#!/usr/bin/env python3
"""
Regenerate the illustrated assembly guide from the STL files and the CAD assemblies.

The guide has three parts, each drawn from its own CAD assembly (3MF export):
  --character  one finished character                (Part 1, steps 3-10)
  --four       four characters joined together       (Part 2)
  --pico       the first set of characters + Pico    (Part 3)
Pass only the ones you want re-rendered. A stage without a 3MF keeps its old images.
Until the Part 2 / Part 3 assemblies exist, pass the older Full Assembly for them.

Writes:
  mechanical/images/*.png          figures, rendered from mechanical/stl/ and the 3MFs
  mechanical/ASSEMBLY-GUIDE.md     from tools/assembly_guide/assembly-guide.md.tmpl
  mechanical/PRINT-GUIDE.md        only the block between the GENERATED PARTS markers

Requirements:
  pip install numpy trimesh pillow

Usage:
  python tools/generate_assembly_guide.py --character "Character Assembly.3mf" \\
      --four "Full Assembly.3mf" --pico "Full Assembly.3mf"
  python tools/generate_assembly_guide.py --no-images     # docs only (no 3MF needed)
  python tools/generate_assembly_guide.py --check         # exit 1 if the docs are stale
  --flap-glyphs "3mf dir"                                  # flap 3MFs for the real printed characters
                                                            on the fan of flaps (default: mechanical/stl/flaps)
  --photos "build-log/photos"                              # re-import the photos of a real build
                                                            (shrunk into mechanical/images/photos/)

Where the geometry comes from
  * Printed parts   mechanical/stl/*.stl, in the CAD's shared frame, so one translation
                    away from each assembly (the two flat gears are the exception).
                    Parts that occur several times (Front Connector Peg) come from the 3MF.
  * Everything else the motor, PCBs, micro switch, screws, nuts and cable are not printed.
                    They are read from the 3MF by name/position (see classify() and
                    FASTENER_GROUPS). Screws, nuts and washers need the size in their CAD
                    name, e.g. "Socket button head screw M3x0.5 x 12"; anything named
                    "cable" or "wire" is drawn as the limit-switch cable.
                    Only the fastener list is kept in the repo (fasteners.json), so that
                    --no-images and --check can still write the hardware tables.

If you add, rename or drop an STL, update PRINTED below (the script warns about STLs it
doesn't know). If you add a kind of fastener, add it to FASTENER_GROUPS. The step text
lives in assembly-guide.md.tmpl; figures are listed in FIGURES.
"""

import argparse
import ctypes
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
STL_DIR = ROOT / "mechanical" / "stl"
FLAPS_DIR = STL_DIR / "flaps"  # the 45 print-ready flap 3MFs (tools/generate_flaps.py output)
IMG_DIR = ROOT / "mechanical" / "images"
PHOTO_DIR = IMG_DIR / "photos"  # photos of a real build, see --photos
PHOTO_MAX_PX = 1200
DATA_DIR = ROOT / "tools" / "assembly_guide"
TEMPLATE = DATA_DIR / "assembly-guide.md.tmpl"
FASTENERS_JSON = DATA_DIR / "fasteners.json"
ASSEMBLY_MD = ROOT / "mechanical" / "ASSEMBLY-GUIDE.md"
PRINT_MD = ROOT / "mechanical" / "PRINT-GUIDE.md"
CHAR_SET_SRC = ROOT / "firmware" / "attiny84" / "split_flap_display_async" / "split_flap_controller.h"

PRINT_BEGIN = "<!-- BEGIN GENERATED PARTS (tools/generate_assembly_guide.py) -->"
PRINT_END = "<!-- END GENERATED PARTS -->"

# ---------------------------------------------------------------------------
# Part registry
# ---------------------------------------------------------------------------
# key, STL file, quantity, used in step(s), colour
PRINTED = [
    ("Frame", "Split Flap Character - Frame.stl", "1 per character", "6", (205, 208, 214)),
    ("Left Wheel", "Split Flap Character - Left Wheel.stl", "1 per character", "4", (70, 72, 84)),
    ("Right Wheel", "Split Flap Character - Right Wheel.stl", "1 per character", "4", (120, 124, 150)),
    ("Limit Switch Support", "Split Flap Character - Limit Switch Support.stl", "1 per character", "3", (190, 110, 215)),
    ("Flap", "Split Flap Character - Flap.stl", "{FLAPS} per character", "5", (245, 245, 245)),
    ("Wheel 50", "Gear 50x30 - Wheel 50.stl", "1 per character", "8", (235, 200, 55)),
    ("Stepper 30", "Gear 50x30 - Stepper 30.stl", "1 per character", "8", (235, 200, 55)),
    ("Gear Spacer", "Split Flap Character - Stepper Motor Gear Spacer.stl", "1 per character", "8", (40, 190, 190)),
    ("Lid", "Split Flap Character - Lid.stl", "1 per character", "10", (90, 150, 225)),
    ("Front Connector Peg", "Split Flap Character - Front Connector Peg.stl", "4 per character, none on the one next to the Pico case", "10", (240, 90, 110)),
    ("Character Attachment Clip", "Split Flap Character - Character Attachment Clip.stl", "2 per joint between characters (top and bottom)", "12", (240, 90, 110)),
    ("Pico Case", "Split Flap Character - Pico Case.stl", "1 per display", "14", (120, 190, 130)),
    ("Pico Case Lid", "Split Flap Character - Pico Case Lid.stl", "1 per display", "14", (70, 160, 100)),
]
# STLs kept in mechanical/stl/ that this build doesn't use (the older gear pair), so no warning for them.
NOT_IN_GUIDE = {"Gear - Stepper.stl", "Gear - Wheel.stl"}
# 3MF bodies to ignore: the Rail is out of the design for now (its STL was removed).
IGNORED_BODIES = {"Rail"}
# Same, by the board tag EasyEDA ends a body's name with: the small board at the back of the Pico case,
# hidden until the guide covers it.
IGNORED_BOARD_TAGS = ("~Gjse",)
FLAT_GEARS = {"Wheel 50", "Stepper 30"}  # STLs are laid flat; the 3MF says where they sit
# 3MF body name -> PRINTED key (the CAD names are the key or the STL name without its prefix)
PRINTED_ALIASES = {f.replace("Split Flap Character - ", "").replace(".stl", ""): k for k, f, *_ in PRINTED}
PRINTED_ALIASES.update({k: k for k, *_ in PRINTED})
PRINTED_ALIASES.update({"Stepper Motor Gear Spacer": "Gear Spacer", "Flap 2": "Flap",
                        "Front Connector": "Front Connector Peg", "Coupler": "Character Attachment Clip"})  # old names

# Not printed; meshes come from the 3MF (see classify())
HARDWARE_PARTS = {
    "Motor": ("motor", (170, 176, 188)),
    "Micro switch": ("microsw", (215, 45, 45)),
    "Character PCB": ("char_pcb", (30, 140, 80)),
    "Backboard PCB": ("strip", (30, 120, 70)),
    "Pico PCB": ("pico_pcb", (30, 140, 80)),
    "Cable": ("cable", (255, 140, 0)),
}
COLORS = {k: c for k, _, _, _, c in PRINTED}
COLORS.update({k: c for k, (_, c) in HARDWARE_PARTS.items()})
FLAP_BODY_COLOR = (25, 25, 28)
FLAP_DECAL_COLOR = (245, 245, 245)

# Step 5's "wheel now has all the flaps on it" figure (05b-flaps-gear.png) is the only user of
# flaps(); with the flap 3MFs (mechanical/stl/flaps, or --flap-glyphs) it renders the full loaded wheel with real printed
# characters rather than plain black, using the per-character 3MF files the flap generator
# (tools/generate_flaps.py) produces: each is a black "flap" body plus a white
# "<char>_top_front" / "<char>_bottom_back" decal, named flap_<n>_<top><bottom>.3mf.
# FLAP_GLYPH_FIRST=1 and FLAP_FAN_COUNT=45 load all 45, in the order they're actually pegged on
# (see Step 5's own text). Everywhere else that shows a built wheel
# (RealFlaps) uses just the couple of flaps the CAD assembly itself carries, for legibility —
# see the note in the template about that.
FLAP_GLYPH_FIRST = 1
FLAP_FAN_COUNT = 45  # = all of them; must match the --flap-glyphs load below
FLAP_GLYPHS = None  # set from --flap-glyphs in main(); a list of read_flap_glyph_3mf() results
# The Right Wheel has a small raised registration tab, radially inward of one specific flap-peg
# hole, marking where flap_1 (index i=0 in flaps() below, i.e. FLAP_GLYPHS[0]) is meant to be
# pegged — confirmed against a real build: that hole takes the flap printed with '°' on one
# face and a space on the other, which is flap_1 in generate_flaps.py's numbering (its reversed
# iteration order starts the wrap-around pair '°'/space, not char_set's own front pair
# space/'A' — that one, flap_45, lands one hole further along, at i=44). Position measured from
# Character Assembly.3mf: the tab sits at radius 20.5 mm, the peg-ring hole it marks at radius
# 22.9 mm, both at 89.0° from the wheel's +Y axis (atan2(dz, dy) about the wheel centre).
# Solving flaps()'s out_angle(phi) = atan2(sin phi, -cos phi) for that gives this start angle.
FLAP_START_ANGLE = 91.0
# The two flaps the Character Assembly carries, sitting in the front window: each is the Flap STL
# turned about X (degrees, about its own centre), with its centre at this offset (mm) from the
# Left Wheel's centre. Fitted exactly (0 mm error) from Character Assembly.3mf; used to draw the
# same two flaps in assemblies that have none (see Library.real_flaps()).
SAMPLE_FLAPS = [(175.0, (-0.2, -21.762, 12.994)), (355.0, (-0.2, -23.659, -7.417))]

# Screws, nuts and washers are found by name in the 3MF and sorted into groups by kind,
# size, length (screws) and where they sit along X. First matching group wins.
FASTENER_RE = re.compile(r"\b(screw|bolt|nut|washer)\b", re.I)
FASTENER_COLORS = {"screw": (45, 45, 55), "bolt": (45, 45, 55), "nut": (230, 170, 40), "washer": (190, 190, 200)}
FASTENER_GROUPS = [
    dict(id="pico-corner-screws", stage="pico", kind="screw", size="M3", length=12, x=(10, None),
         note="Fasten the Pico Case to the Frame at its four corners, into the corner nuts (step 14)."),
    dict(id="pico-pcb-screws", stage="pico", kind="screw", size="M3", length=8, x=(10, None),
         note="Fasten the Pico PCB to the standoffs in the Pico Case (step 14)."),
    dict(id="pico-pcb-nuts", stage="pico", kind="nut", size="M3", x=(15, 22.5),
         note="In the Pico Case standoffs, under the Pico PCB (step 14)."),
    dict(id="pico-lid-screws", stage="pico", kind="screw", size="M3", length=20, x=(10, None),
         note="Hold the Pico Case Lid, at its top, bottom and front (step 14)."),
    dict(id="pico-lid-nuts", stage="pico", kind="nut", size="M3", x=(22.5, None),
         note="In the slots in the Pico Case walls, behind the lid screws (step 14)."),
    dict(id="switch-screws", kind="screw", size="M2", length=12, note="Mount the micro limit switch on the Limit Switch Support (step 3)."),
    dict(id="switch-nuts", kind="nut", size="M2", x=(None, 0), note="In the hex pockets of the Limit Switch Support (step 3)."),
    dict(id="wheel-screws", kind="screw", size="M2", length=8, note="Fasten the Right Wheel to the Left Wheel (step 4)."),
    dict(id="wheel-nuts", kind="nut", size="M2", x=(0, None), note="In the pockets of the Left Wheel drum (step 4)."),
    dict(id="motor-screws", kind="screw", size="M3", x=(None, 10), note="Motor ears to the Frame (step 7)."),
    dict(id="motor-nuts", kind="nut", size="M3", x=(None, 0), note="In the hex pockets on the outside of the Frame's −X wall (step 7)."),
    dict(id="corner-screws", kind="screw", size="M3", x=(10, None), note="Through each Front Connector Peg and Lid corner into the Frame (step 10)."),
    dict(id="corner-nuts", kind="nut", size="M3", x=(0, None), note="In the hex channels at the Frame's corners, from the back (−X) face (step 10)."),
]
STAGES = {  # how quantities are worded, and how many finished characters' worth of fasteners it repeats
    "character": dict(scope="per character", repeats=0),
    "four": dict(scope="per set of 4", repeats=4),
    "pico": dict(scope="per display", repeats=1),
}

# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
# Each figure: stage, file, view (azimuth, elevation), size, parts, [pad], [labels].
# A part is (key, (dx, dy, dz) explode offset in mm, label number or None). Keys: a PRINTED
# key, a HARDWARE_PARTS key, "fasteners:<group id>", "Flaps" (illustrates inserting all of them,
# via flaps() — Step 5's own figures only) or "RealFlaps" (the couple already authored in the
# assembly, used everywhere else the wheel appears already loaded).
# Numbers must match the legends in assembly-guide.md.tmpl.
# Camera: azimuth 0 = looking at the front window (along +Y), negative azimuth swings
# round to the +X side. Z is up.
Z = (0, 0, 0)
WHEEL = ["Left Wheel", "RealFlaps", "Limit Switch Support", "Micro switch", "fasteners:switch-screws",
         "fasteners:switch-nuts", "Right Wheel", "fasteners:wheel-screws", "fasteners:wheel-nuts", "Wheel 50"]
DRIVE = ["Motor", "Gear Spacer", "Stepper 30"]
INNER = ["Frame"] + WHEEL + DRIVE + ["fasteners:motor-screws", "fasteners:motor-nuts"]


FIGURES = [
    dict(stage="character", file="02-exploded-character.png", view=(-22, 20), size=1700, pad=0.03, parts=[
        ("Frame", (-75, 0, 0), 1), ("Left Wheel", (-20, 0, 0), 2), ("RealFlaps", (-20, 0, 0), 3),
        ("Limit Switch Support", (24, 0, 0), 4), ("Micro switch", (24, 0, 30), 5),
        ("Right Wheel", (58, 0, 0), 6), ("Wheel 50", (78, 0, 0), 7),
        ("Motor", (-58, 0, -62), 8), ("Gear Spacer", (-12, 0, -62), 9), ("Stepper 30", (8, 0, -62), 10),
        ("Character PCB", (-20, 0, 58), 11), ("Lid", (106, 0, 0), 12), ("Front Connector Peg", (136, 0, 0), 13)]),
    dict(stage="character", file="03-drive-train.png", view=(-35, 22), size=900, parts=[
        ("Motor", Z, 1), ("Gear Spacer", (24, 0, 0), 2), ("Stepper 30", (46, 0, 0), 3)]),
    dict(stage="character", file="04-limit-switch.png", view=(-35, 22), size=1000, parts=[
        ("Limit Switch Support", Z, 1), ("Micro switch", (0, 0, 32), 2),
        ("fasteners:switch-screws", (26, 0, 32), 3), ("fasteners:switch-nuts", (-26, 0, 0), 4)]),
    dict(stage="character", file="05-wheel.png", view=(-30, 20), size=1300, parts=[
        ("Left Wheel", Z, 1), ("fasteners:wheel-nuts", (20, 0, 0), 2),
        ("Limit Switch Support", (34, 0, 0), 3), ("Micro switch", (34, 0, 0), None),
        ("fasteners:switch-screws", (34, 0, 0), None), ("fasteners:switch-nuts", (34, 0, 0), None),
        ("Right Wheel", (66, 0, 0), 4), ("fasteners:wheel-screws", (90, 0, 0), 5)]),
    dict(stage="character", file="05b-flaps-gear.png", view=(-30, 20), size=1300, pad=0.04,
         parts=[(k, Z, None) for k in WHEEL if k not in ("RealFlaps", "Wheel 50")]
         + [("Flaps", Z, 1), ("Wheel 50", (46, 0, 0), 2)]),
    dict(stage="character", file="06-frame.png", view=(-30, 20), size=1300,
         parts=[("Frame", Z, 1)] + [(k, (52, 0, 0), 2 if k == "Left Wheel" else None) for k in WHEEL]),
    dict(stage="character", file="06b-frame-nuts.png", view=(150, 22), size=900,
         parts=[("Frame", Z, None), ("fasteners:motor-nuts", (-30, 0, 0), 1)]),
    dict(stage="character", file="06c-motor.png", view=(-58, 16), size=1300,
         parts=[(k, Z, None) for k in ["Frame"] + WHEEL]
         + [("Motor", (42, 0, 0), 1)]  # bare motor only — the gears go on afterward, in step 8
         + [("fasteners:motor-screws", (68, 0, 0), 2)]),
    dict(stage="character", file="07-pcb.png", view=(-30, 22), size=1300,
         parts=[(k, Z, None) for k in INNER] + [("Character PCB", (0, 0, 70), 1)]),
    dict(stage="character", file="08-cable.png", view=(-55, 38), size=1300,
         parts=[(k, Z, None) for k in INNER] + [("Character PCB", Z, 1), ("Micro switch", Z, 2), ("Cable", Z, 3)]),
    # the corner nuts sit deep in hex channels that open on the Frame's back (−X) face
    dict(stage="character", file="09a-corner-nuts.png", view=(150, 22), size=900,
         parts=[(k, Z, None) for k in INNER + ["Character PCB"]] + [("fasteners:corner-nuts", (-30, 0, 0), 1)]),
    dict(stage="character", file="09-lid-connectors.png", view=(-30, 22), size=1300,
         parts=[(k, Z, None) for k in INNER + ["Character PCB", "Cable", "fasteners:corner-nuts"]]
         + [("Lid", (38, 0, 0), 1), ("Front Connector Peg", (62, 0, 0), 2), ("fasteners:corner-screws", (86, 0, 0), 3)]),
]
CHAR_OUTSIDE = ["Frame", "Lid", "Front Connector Peg", "fasteners:corner-screws"]
FIGURES += [
    dict(stage="character", file="10a-character-front.png", view=(-30, 22), size=900, labels=False,
         parts=[(k, Z, None) for k in CHAR_OUTSIDE]),
    dict(stage="character", file="10b-character-back.png", view=(145, 22), size=900, labels=False,
         parts=[(k, Z, None) for k in CHAR_OUTSIDE]),
]


CHAIN_OUTSIDE = ["Frame", "Lid", "Front Connector Peg", "fasteners:corner-screws"]
# The first character (+X end, index 0) takes the Pico case instead of Front Connector Pegs, so leave its off
CHAIN = [f"{k}@{i}" for i in range(4) for k in CHAIN_OUTSIDE
         if not (i == 0 and k in ("Front Connector Peg", "fasteners:corner-screws"))]
# A reminder cutaway of one character's wheel, with the same parts as Part 1's own figures
CHAIN_INNER_MINI = [f"{k}@0" for k in INNER]
FIGURES += [
    dict(stage="four", file="11-chain-wheel.png", view=(-30, 22), size=1300,
         parts=[(k, Z, 1 if k == "RealFlaps@0" else None) for k in CHAIN_INNER_MINI]),
    dict(stage="four", file="12-chain-join.png", view=(-40, 20), size=1500, pad=0.03,
         parts=[(k, (-int(k.partition("@")[2]) * 60, 0, 0), 1 if k == "Front Connector Peg@1" else None) for k in CHAIN]),
    dict(stage="four", file="13-chain-clips.png", view=(-38, 30), size=1300,
         parts=[(k, Z, None) for k in CHAIN]
         + [("Character Attachment Clip^top", (0, 0, 40), 1), ("Character Attachment Clip^bottom", (0, 0, -40), 2)]),
    dict(stage="four", file="14-chain-backboard.png", view=(150, 22), size=1300,
         parts=[(k, Z, None) for k in CHAIN + ["Character Attachment Clip"]] + [("Backboard PCB", (0, 60, 0), 1)]),
    dict(stage="four", file="15a-chain-front.png", view=(-30, 22), size=900, labels=False,
         parts=[(k, Z, None) for k in CHAIN] + [("Backboard PCB", Z, None)]),
    dict(stage="four", file="15b-chain-back.png", view=(150, 22), size=900, labels=False,
         parts=[(k, Z, None) for k in CHAIN] + [("Backboard PCB", Z, None)]),
    dict(stage="pico", file="16-pico-wheel.png", view=(-30, 22), size=1300,
         parts=[(k, Z, 1 if k == "RealFlaps" else None) for k in INNER]),
    dict(stage="pico", file="17-pico-case.png", view=(-28, 20), size=1500, pad=0.03, parts=[
        ("Frame", Z, None), ("Lid", Z, None), ("fasteners:corner-nuts", Z, None),
        ("Pico Case", (30, 0, 0), 1), ("fasteners:pico-corner-screws", (58, 0, 0), 2),
        ("fasteners:pico-pcb-nuts", (30, 0, 0), 3), ("fasteners:pico-lid-nuts", (30, 0, 0), 6),
        ("Pico PCB", (68, 0, 0), 4), ("fasteners:pico-pcb-screws", (92, 0, 0), 5),
        ("Pico Case Lid", (118, 0, 0), 7), ("fasteners:pico-lid-screws", (150, 0, 0), 8)]),
    dict(stage="pico", file="18a-display-front.png", view=(-30, 22), size=900, labels=False,
         parts=[(k, Z, None) for k in ["Frame", "Lid", "Pico Case", "Pico Case Lid"]]),
    dict(stage="pico", file="18b-display-back.png", view=(150, 22), size=900, labels=False,
         parts=[(k, Z, None) for k in ["Frame", "Lid", "Pico Case", "Pico Case Lid"]]),
]

# ---------------------------------------------------------------------------
# Rasteriser: tiny z-buffer. Uses a C helper when a compiler is available, else numpy.
# ---------------------------------------------------------------------------
C_SRC = r"""
#include <math.h>
void raster(int n,const float*tri,const float*col,int W,int H,float*zbuf,unsigned char*img,int*idb,const int*ids){
  for(int t=0;t<n;t++){
    const float*p=tri+t*9;
    float x0=p[0],y0=p[1],z0=p[2],x1=p[3],y1=p[4],z1=p[5],x2=p[6],y2=p[7],z2=p[8];
    int ix0=(int)floorf(fminf(x0,fminf(x1,x2))),ix1=(int)ceilf(fmaxf(x0,fmaxf(x1,x2)));
    int iy0=(int)floorf(fminf(y0,fminf(y1,y2))),iy1=(int)ceilf(fmaxf(y0,fmaxf(y1,y2)));
    if(ix0<0)ix0=0; if(iy0<0)iy0=0; if(ix1>=W)ix1=W-1; if(iy1>=H)iy1=H-1;
    float den=(y1-y2)*(x0-x2)+(x2-x1)*(y0-y2);
    if(fabsf(den)<1e-12f) continue;
    float inv=1.0f/den;
    for(int y=iy0;y<=iy1;y++){
      float py=y+0.5f;
      for(int x=ix0;x<=ix1;x++){
        float px=x+0.5f;
        float a=((y1-y2)*(px-x2)+(x2-x1)*(py-y2))*inv;
        float b=((y2-y0)*(px-x2)+(x0-x2)*(py-y2))*inv;
        float c=1.0f-a-b;
        if(a<-1e-4f||b<-1e-4f||c<-1e-4f) continue;
        float z=a*z0+b*z1+c*z2;
        int i=y*W+x;
        if(z>zbuf[i]){zbuf[i]=z;idb[i]=ids[t];
          for(int k=0;k<3;k++){float v=col[t*3+k]; img[i*3+k]=(unsigned char)(v>255?255:v);}}
      }
    }
  }
}
"""
_F = ctypes.POINTER(ctypes.c_float)
_lib = None
_lib_tried = False


def _c_rasteriser():
    global _lib, _lib_tried
    if _lib_tried:
        return _lib
    _lib_tried = True
    tag = hashlib.md5(C_SRC.encode()).hexdigest()[:8]
    so = Path(tempfile.gettempdir()) / f"openflaps_zbuf_{tag}.so"
    if not so.exists():
        src = so.with_suffix(".c")
        src.write_text(C_SRC)
        for cc in ("cc", "clang", "gcc"):
            try:
                r = subprocess.run([cc, "-O3", "-shared", "-fPIC", "-o", str(so), str(src), "-lm"],
                                   capture_output=True)
            except OSError:
                continue
            if r.returncode == 0:
                break
        else:
            print("note: no C compiler found, using the slow numpy rasteriser", file=sys.stderr)
            return None
    try:
        _lib = ctypes.CDLL(str(so))
    except OSError:
        _lib = None
    return _lib


def _raster_numpy(tri, col, ids, W, zbuf, img, idb):
    for t in range(len(tri)):
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = tri[t]
        ix0, ix1 = max(int(np.floor(min(x0, x1, x2))), 0), min(int(np.ceil(max(x0, x1, x2))), W - 1)
        iy0, iy1 = max(int(np.floor(min(y0, y1, y2))), 0), min(int(np.ceil(max(y0, y1, y2))), W - 1)
        if ix1 < ix0 or iy1 < iy0:
            continue
        den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(den) < 1e-12:
            continue
        px, py = np.meshgrid(np.arange(ix0, ix1 + 1) + 0.5, np.arange(iy0, iy1 + 1) + 0.5)
        a = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / den
        b = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / den
        c = 1 - a - b
        z = a * z0 + b * z1 + c * z2
        zs = zbuf[iy0:iy1 + 1, ix0:ix1 + 1]
        m = (a >= -1e-4) & (b >= -1e-4) & (c >= -1e-4) & (z > zs)
        zs[m] = z[m]
        img[iy0:iy1 + 1, ix0:ix1 + 1][m] = np.minimum(col[t], 255)
        idb[iy0:iy1 + 1, ix0:ix1 + 1][m] = ids[t]


def view_matrix(az, el):
    """World -> view. Screen up = +Z, camera in front (-Y) at az=0."""
    a, e = np.radians(az), np.radians(el)
    B = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)
    Ry = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(e), -np.sin(e)], [0, np.sin(e), np.cos(e)]])
    return Rx @ Ry @ B


class Renderer:
    """Renders a list of meshes: {'v','f','color','offset','label'} to a PIL image."""

    def __init__(self, view, size=900, pad=0.06, ss=2, bg=(255, 255, 255)):
        self.R = view_matrix(*view)
        self.size, self.pad, self.ss, self.bg = size, pad, ss, bg

    def render(self, parts, edges=True):
        light = np.array([-0.4, 0.6, 0.7])
        light /= np.linalg.norm(light)
        tris, cols, ids = [], [], []
        for k, p in enumerate(parts):
            tri = ((p["v"] + np.asarray(p["offset"], float)) @ self.R.T)[p["f"]]
            n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            ln = np.linalg.norm(n, axis=1, keepdims=True)
            ln[ln == 0] = 1
            shade = np.abs((n / ln) @ light) * 0.6 + 0.4
            tris.append(tri)
            cols.append(np.asarray(p["color"], float)[None, :] * shade[:, None])
            ids.append(np.full(len(tri), k, np.int32))
        tri = np.concatenate(tris)
        col = np.concatenate(cols).astype(np.float32)
        ids = np.concatenate(ids)
        pts = tri.reshape(-1, 3)
        mn, mx = pts.min(0), pts.max(0)
        span = max(mx[0] - mn[0], mx[1] - mn[1]) * (1 + 2 * self.pad)
        W = self.size * self.ss
        sc = W / span
        cx, cy = (mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2
        scr = np.empty(tri.shape, np.float32)
        scr[:, :, 0] = (tri[:, :, 0] - cx) * sc + W / 2
        scr[:, :, 1] = W / 2 - (tri[:, :, 1] - cy) * sc
        scr[:, :, 2] = tri[:, :, 2]
        zbuf = np.full((W, W), -1e30, np.float32)
        img = np.empty((W, W, 3), np.uint8)
        img[:] = np.array(self.bg, np.uint8)
        idb = np.full((W, W), -1, np.int32)
        lib = _c_rasteriser()
        if lib is not None:
            scr = np.ascontiguousarray(scr)
            lib.raster(ctypes.c_int(len(scr)), scr.ctypes.data_as(_F), col.ctypes.data_as(_F),
                       ctypes.c_int(W), ctypes.c_int(W), zbuf.ctypes.data_as(_F),
                       img.ctypes.data_as(ctypes.c_void_p), idb.ctypes.data_as(ctypes.c_void_p),
                       ids.ctypes.data_as(ctypes.c_void_p))
        else:
            _raster_numpy(scr, col, ids, W, zbuf, img, idb)
        out = img.astype(np.float32)
        if edges:  # dark outline where the part id or the depth jumps
            edge = np.zeros((W, W), bool)
            edge[:, 1:] |= idb[:, 1:] != idb[:, :-1]
            edge[1:, :] |= idb[1:, :] != idb[:-1, :]
            zz = np.where(zbuf < -1e29, np.nan, zbuf)
            thr = span * 0.012
            with np.errstate(invalid="ignore"):
                edge[:, 1:] |= np.abs(zz[:, 1:] - zz[:, :-1]) > thr
                edge[1:, :] |= np.abs(zz[1:, :] - zz[:-1, :]) > thr
            out[edge] *= 0.35
        self._proj = (cx, cy, sc / self.ss)
        return Image.fromarray(out.astype(np.uint8)).resize((self.size, self.size), Image.LANCZOS)

    def project(self, pt):
        cx, cy, sc = self._proj
        v = self.R @ np.asarray(pt, float)
        return (v[0] - cx) * sc + self.size / 2, self.size / 2 - (v[1] - cy) * sc


# ---------------------------------------------------------------------------
# Loading geometry
# ---------------------------------------------------------------------------
def stl_raw(key):
    """A printed part's STL vertices/faces exactly as exported (CAD frame)."""
    path = STL_DIR / dict((k, f) for k, f, *_ in PRINTED)[key]
    if not path.exists():
        sys.exit(f"Missing STL: {path.relative_to(ROOT)}")
    m = trimesh.load(path, force="mesh")
    return np.asarray(m.vertices, float), np.asarray(m.faces)


def read_flap_glyph_3mf(path):
    """A single-flap 3MF from tools/generate_flaps.py: {'body', 'front', 'back'} -> (v, f), in
    the same local frame as mechanical/stl/Split Flap Character - Flap.stl (millimeters, no
    Onshape-style component transforms — just three objects at the origin)."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    local = lambda tag: tag.rsplit("}", 1)[-1]  # strips the "{schema-uri}" namespace ET keeps
    resources = next(c for c in root if local(c.tag) == "resources")
    meshes = {}
    for o in resources:
        if local(o.tag) != "object":
            continue
        mesh = next((m for m in o if local(m.tag) == "mesh"), None)
        if mesh is None:
            continue
        vs = next(v for v in mesh if local(v.tag) == "vertices")
        ts = next(t for t in mesh if local(t.tag) == "triangles")
        v = np.array([[float(x.get(c)) for c in "xyz"] for x in vs])
        f = np.array([[int(x.get(c)) for c in ("v1", "v2", "v3")] for x in ts])
        meshes[o.get("name")] = (v, f)
    # a side with no ink (a space, or the empty half of '°', '-', ',', '.') has no decal object
    front = next((k for k in meshes if k.endswith("_top_front")), None)
    back = next((k for k in meshes if k.endswith("_bottom_back")), None)
    if "flap" not in meshes:
        raise ValueError(f"expected a 'flap' object (+ '*_top_front' / '*_bottom_back' decals), got "
                          f"{sorted(meshes) or '(none — probably a sliced/plated 3MF, not a single-flap export)'}")
    return {"body": meshes["flap"], "front": meshes.get(front), "back": meshes.get(back)}


def load_flap_glyphs(dir_path, first, n):
    """The n flap_<i>_..3mf files starting at number `first`, in a dir from generate_flaps.py.
    A file that doesn't parse as a single-flap export (e.g. one accidentally re-saved as a
    sliced print plate) becomes None in the result — flaps() then falls back to a plain flap
    for just that position rather than aborting the whole run."""
    dir_path = Path(dir_path)
    numbered = {}
    for p in dir_path.glob("flap_*.3mf"):
        m = re.match(r"flap_(\d+)_", p.name)
        if m:
            numbered[int(m.group(1))] = p
    missing = [i for i in range(first, first + n) if i not in numbered]
    if missing:
        sys.exit(f"--flap-glyphs {dir_path}: missing flap_{{{','.join(map(str, missing))}}}_*.3mf")
    print(f"Reading {n} flap glyphs from {dir_path} (flap_{first}.. flap_{first + n - 1}) ...")
    out = []
    for i in range(first, first + n):
        try:
            out.append(read_flap_glyph_3mf(numbered[i]))
        except (ValueError, KeyError, ET.ParseError) as e:
            print(f"warning: {numbered[i].name}: {e} — using a plain flap there instead", file=sys.stderr)
            out.append(None)
    return out


def parse_fastener(p):
    """kind ('screw'...), size ('M3') and length (mm or None) from a CAD body's name."""
    kind = FASTENER_RE.search(p["name"]).group(1).lower()
    size = re.search(r"\bM(\d+(?:\.\d+)?)", p["name"])
    length = re.search(r"x\s*(\d+(?:\.\d+)?)\s*$", p["name"])
    return dict(kind="screw" if kind == "bolt" else kind, size="M" + size.group(1) if size else None,
                length=float(length.group(1)) if length else None)


def fastener_group(p, stage, cx):
    """The FASTENER_GROUPS id a 3MF fastener body belongs to in this stage, or None.
    cx: its X, measured from the middle of its character's Frame."""
    info = parse_fastener(p)
    for g in FASTENER_GROUPS:
        lo, hi = g.get("x", (None, None))
        if (g.get("stage", stage) == stage and g["kind"] == info["kind"] and g["size"] == info["size"]
                and g.get("length") in (None, info["length"])
                and (lo is None or cx >= lo) and (hi is None or cx <= hi)):
            return g["id"]
    return None


class Library:
    """One assembly's meshes in its own frame (each 3MF has its own origin)."""

    def __init__(self, assembly_paths, stage):
        self.stage = stage
        world = read_assemblies(assembly_paths)
        by_name = {p["name"]: p for p in world}
        for need in ["Frame"] + sorted(FLAT_GEARS):
            if need not in by_name:
                sys.exit(f"'{need}' not found in {assembly_paths}; is it the right assembly export?")
        groups = {g: [] for g, _ in HARDWARE_PARTS.values()}
        self.fasteners, self.printed_bodies = [], {}
        # several characters in one assembly: number them from the +X end, and tell each body which it is in
        frames = sorted(((p["v"][:, 0].min(), p["v"][:, 0].max()) for p in world if p["name"] == "Frame"),
                        key=lambda r: -r[1])
        self.chars = len(frames)

        def chain_index(p):
            lo_b, hi_b = p["v"][:, 0].min(), p["v"][:, 0].max()
            for k, (lo, hi) in enumerate(frames):  # a body that ends at / sticks out of this Frame's +X face
                if lo_b - 0.5 <= hi <= hi_b + 0.5:
                    return k
            cx = p["v"][:, 0].mean()  # otherwise it sits inside a Frame
            return next((k for k, (lo, hi) in enumerate(frames) if lo <= cx <= hi), 0)

        def rel_x(p, k):  # X of a body's centre, measured from the middle of its own character's Frame
            return p["v"][:, 0].mean() - sum(frames[k]) / 2 if frames else p["v"][:, 0].mean()

        for p in world:
            g = classify(p)
            if g == "fastener":
                k = chain_index(p)
                self.fasteners.append(p | {"group": fastener_group(p, stage, rel_x(p, k)), "chain": k})
            elif g == "printed":
                self.printed_bodies.setdefault(PRINTED_ALIASES[p["name"]], []).append(p | {"chain": chain_index(p)})
            elif g and g != "skip":
                groups[g].append(p | {"chain": chain_index(p)})
        self.hw = {g: ps for g, ps in groups.items() if ps}
        # printed parts: STL frame -> assembly frame is a pure translation, read off the Frame
        self.stl_offset = by_name["Frame"]["v"].min(0) - stl_raw("Frame")[0].min(0)
        self.z_mid = (by_name["Frame"]["v"][:, 2].min() + by_name["Frame"]["v"][:, 2].max()) / 2  # top / bottom
        self.gears = {}
        for key in FLAT_GEARS:
            M, err = fit_gear(stl_raw(key)[0], by_name[key]["v"])
            if err > 0.3:
                print(f"warning: gear '{key}' STL differs from the assembly (bounding box off by {err:.2f} mm); "
                      "placement may be off", file=sys.stderr)
            self.gears[key] = M
        self._cache = {}

    def stl(self, key):
        if key not in self._cache:
            v, f = stl_raw(key)
            if key in FLAT_GEARS:
                M = self.gears[key]
                v = v @ M[:3, :3].T + M[:3, 3]
            else:
                v = v + self.stl_offset
            self._cache[key] = (v, f)
        return self._cache[key]

    def meshes(self, key, chain=None):
        """[(v, f, colour)] for a figure part key; [] if this assembly doesn't have it.
        chain: only the bodies of that character (0 = the +X end) when the assembly has several."""
        fast = re.match(r"^fasteners:(.+)$", key)
        if fast:
            return [(p["v"], p["f"], FASTENER_COLORS[parse_fastener(p)["kind"]]) for p in self.fasteners
                    if p["group"] == fast.group(1) and chain in (None, p["chain"])]
        if key in HARDWARE_PARTS:
            g, color = HARDWARE_PARTS[key]
            ps = [p for p in self.hw.get(g, []) if chain in (None, p["chain"])]
            if not ps:
                return []
            # one mesh per part, so its callout points at the part as a whole, not one of its bodies
            starts = np.cumsum([0] + [len(p["v"]) for p in ps[:-1]])
            return [(np.concatenate([p["v"] for p in ps]),
                     np.concatenate([p["f"] + s for p, s in zip(ps, starts)]), color)]
        bodies = [b for b in self.printed_bodies.get(key, []) if chain in (None, b["chain"])]
        if bodies and key != "Flap":  # prefer this assembly's own body: parts that spin freely on
            return [(b["v"], b["f"], COLORS[key]) for b in bodies]  # their axle (both wheels, the
        return [(*self.stl(key), COLORS[key])]  # gears) aren't at a fixed angle relative to the STL

    def bounds(self, key, chain=None):
        v, _, _ = self.meshes(key, chain)[0]
        return v.min(0), v.max(0)

    def real_flaps(self, chain=None):
        """The couple of flap bodies actually authored in this assembly (e.g. the Character
        Assembly's own two sample flaps), at their real modeled position — used everywhere the
        wheel already appears loaded. Only Step 5's own figure (05b-flaps-gear.png) illustrates
        inserting all of them, with the synthetic fan from flaps() instead.
        The Daisy Chain and Pico Case assemblies don't carry any Flap bodies at all (they were
        already loaded before either was built — see Step 5), so there the same two flaps are
        placed where the Character Assembly has them (SAMPLE_FLAPS), relative to this wheel."""
        color = FLAP_BODY_COLOR if FLAP_GLYPHS else COLORS["Flap"]
        bodies = [b for b in self.printed_bodies.get("Flap", []) if chain in (None, b["chain"])]
        if bodies:
            return [(b["v"], b["f"], color) for b in bodies]
        v, f = self.stl("Flap")
        lo, hi = self.bounds("Left Wheel", chain)
        out = []
        for deg, rel in SAMPLE_FLAPS:
            c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
            p = (v - (v.min(0) + v.max(0)) / 2) @ np.array([[1, 0, 0], [0, c, -s], [0, s, c]]).T
            out.append((p - (p.min(0) + p.max(0)) / 2 + (lo + hi) / 2 + rel, f, color))
        return out

    def flaps(self, n=FLAP_FAN_COUNT, sign=-1, chain=None):
        """A few flaps fanned out around consecutive peg holes, like a loaded wheel: each one
        pivoted about the wheel axis to point radially outward, not just slid along the ring.
        Each element is a list of (v, f, color) — one plain triple normally, or a body + two
        printed-character decals when --flap-glyphs supplied real per-character 3MFs.
        i=0 starts at the Right Wheel's registration tab (FLAP_START_ANGLE, flap_1 = '°'/space).
        sign=-1 (default) winds the way the wheel actually turns; sign=+1 winds the other way,
        which happens to be the side some figures show the Frame's window from — see FIGURES."""
        v, f = self.stl("Flap")
        lo, hi = self.bounds("Left Wheel", chain)
        cy, cz = (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2  # wheel axis
        fy, ztop = (v[:, 1].min() + v[:, 1].max()) / 2, v[:, 2].max()  # the peg: hinge point
        r = cy - fy  # peg ring radius
        cg = v.mean(0)  # which way the flap currently hangs from its peg, in the Y-Z plane
        hang_angle = np.arctan2(cg[2] - ztop, cg[1] - fy)

        def place(vv, phi):
            yz = vv[:, 1:3] - [fy, ztop]
            py, pz = cy - r * np.cos(phi), cz + r * np.sin(phi)  # the peg's position on the ring
            out_angle = np.arctan2(np.sin(phi), -np.cos(phi))  # outward-radial direction there
            c, s = np.cos(out_angle - hang_angle), np.sin(out_angle - hang_angle)
            new_v = vv.copy()
            new_v[:, 1] = yz[:, 0] * c - yz[:, 1] * s + py
            new_v[:, 2] = yz[:, 0] * s + yz[:, 1] * c + pz
            return new_v

        glyphs = FLAP_GLYPHS[:n] if FLAP_GLYPHS else None
        out = []
        for i in range(n):
            phi = np.radians(FLAP_START_ANGLE + sign * i * 8.0)  # 8 deg pitch = 360 / 45
            g = glyphs[i] if glyphs else None
            if g:
                out.append([(place(g["body"][0], phi), g["body"][1], FLAP_BODY_COLOR)]
                           + [(place(g[s][0], phi), g[s][1], FLAP_DECAL_COLOR) for s in ("front", "back") if g[s]])
            else:
                color = FLAP_BODY_COLOR if glyphs else COLORS["Flap"]  # match the loaded ones' color
                out.append([(place(v, phi), f, color)])
        return out


def build_figure(lib, fig):
    parts, labels = [], []
    for key, off, label in fig["parts"]:
        key, _, k = key.partition("@")  # "Frame@2" = the third character's Frame
        key, _, variant = key.partition("^")  # "Character Attachment Clip^top", "Flaps^cw" — see below
        chain = int(k) if k else None
        if key == "Flaps":
            meshes = [triple for group in lib.flaps(sign=1 if variant == "cw" else -1, chain=chain)
                      for triple in group]
        elif key == "RealFlaps":
            meshes = lib.real_flaps(chain)
        else:
            meshes = lib.meshes(key, chain)
            if variant:  # "Character Attachment Clip^top" / "Character Attachment Clip^bottom": only the ones on that edge
                meshes = [m for m in meshes if (m[0][:, 2].mean() > lib.z_mid) == (variant == "top")]
        if not meshes:
            print(f"note: '{key}' is not in this assembly; left out of {fig['file']}", file=sys.stderr)
            continue
        for v, f, color in meshes:
            parts.append(dict(v=v, f=f, color=color, offset=off))
        if label is not None:
            R = view_matrix(*fig["view"])  # label the body of a group nearest the camera
            near = max(meshes, key=lambda m: (R @ (m[0].mean(0) + np.asarray(off, float)))[2])
            labels.append((label, near[0].mean(0) + np.asarray(off, float)))
    return parts, labels


def font(size):
    for p in ("/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


def render_figure(lib, fig):
    parts, labels = build_figure(lib, fig)
    r = Renderer(fig["view"], fig["size"], fig.get("pad", 0.05))
    im = r.render(parts)
    if fig.get("labels", True):
        d = ImageDraw.Draw(im)
        f = font(26)
        for text, pt in labels:
            ax, ay = r.project(pt)  # the spot on the part
            x, y = ax - 34, ay - 34  # the number sits up-left of it, joined by a leader
            d.line([(ax, ay), (x, y)], fill=(255, 255, 255), width=5)
            d.line([(ax, ay), (x, y)], fill=(20, 20, 20), width=2)
            d.ellipse([ax - 4, ay - 4, ax + 4, ay + 4], fill=(20, 20, 20), outline=(255, 255, 255))
            d.ellipse([x - 19, y - 19, x + 19, y + 19], fill=(20, 20, 20), outline=(255, 255, 255), width=2)
            w = d.textlength(str(text), font=f)
            d.text((x - w / 2, y - 15), str(text), fill=(255, 255, 255), font=f)
    return im


def wrap_text(draw, text, fnt, width):
    """Break text into lines no wider than width px."""
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and draw.textlength(trial, font=fnt) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur]


def render_parts_sheet(lib, flaps):
    tiles = [(k, q.replace("{FLAPS}", str(flaps))) for k, _, q, _, _ in PRINTED]
    names = {k: f.replace("Split Flap Character - ", "").replace(".stl", "") for k, f, *_ in PRINTED}
    T, cols, cap = 330, 4, 84  # tile size, columns, caption height (up to 3 wrapped lines)
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * T, rows * (T + cap)), "white")
    d, f = ImageDraw.Draw(sheet), font(20)
    for i, (k, qty) in enumerate(tiles):
        g = next((g for g in FLAP_GLYPHS if g and g["front"] and g["back"]), None) if k == "Flap" and FLAP_GLYPHS else None
        if g:  # show one real printed character (printed on both sides), not a plain blank
            parts = [dict(v=g[part][0], f=g[part][1], offset=(0, 0, 0),
                          color=FLAP_BODY_COLOR if part == "body" else FLAP_DECAL_COLOR)
                     for part in ("body", "front", "back")]
        else:
            v, faces = lib.stl(k)
            parts = [dict(v=v, f=faces, color=COLORS[k], offset=(0, 0, 0))]
        view = (-40, 20) if k in FLAT_GEARS else (-35, 22)
        im = Renderer(view, T, 0.08).render(parts)
        x, y = (i % cols) * T, (i // cols) * (T + cap)
        sheet.paste(im, (x, y))
        short = qty.replace(" per character", "").replace(" ⚠️", "")
        for n, line in enumerate(wrap_text(d, f"{names[k]}  ×{short}", f, T - 20)[:3]):
            d.text((x + 10, y + T - 2 + n * 24), line, fill=(20, 20, 20), font=f)
    return sheet


# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------
def firmware_char_set():
    m = re.search(r'char_set\[\]\s*=\s*"((?:[^"\\]|\\.)*)"', CHAR_SET_SRC.read_text())
    if not m:
        sys.exit(f"Could not find char_set in {CHAR_SET_SRC.relative_to(ROOT)}")
    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda h: chr(int(h.group(1), 16)), m.group(1))


def facts(flaps):
    v, _ = stl_raw("Frame")
    pitch = v[:, 0].max() - v[:, 0].min()
    teeth = {}
    for key in FLAT_GEARS:
        n = int(re.search(r"(\d+)$", key).group(1))
        g, _ = stl_raw(key)
        od = max(np.ptp(g[:, 0]), np.ptp(g[:, 1]))
        teeth[key] = (n, od / (n + 2))  # module = outer diameter / (teeth + 2)
    (n1, m1), (n2, _) = teeth["Wheel 50"], teeth["Stepper 30"]
    return dict(pitch=f"{pitch:.1f}", flaps=str(flaps), gear_distance=f"{m1 * (n1 + n2) / 2:.0f}",
                teeth_wheel=str(n1), teeth_motor=str(n2), gear_r1=f"{round(m1 * n1 / 2, 1):g}",
                gear_r2=f"{round(m1 * n2 / 2, 1):g}")


def save_fasteners(stage, lib):
    """Snapshot a stage's fasteners so docs can be rebuilt without the 3MF. Later stages list only
    what they add on top of the finished characters they contain (they repeat the character's own)."""
    counts = {}
    for p in lib.fasteners:
        c = counts.setdefault((p["group"], p["name"]), {"group": p["group"], "name": p["name"], "count": 0})
        c["count"] += 1
    if not counts:
        return
    snap = json.loads(FASTENERS_JSON.read_text()) if FASTENERS_JSON.exists() else {}
    repeats = STAGES[stage]["repeats"]
    if repeats:
        base = {(r["group"], r["name"]): r["count"] * repeats for r in snap.get("character", [])}
        char_groups = {g["id"] for g in FASTENER_GROUPS if "stage" not in g}
        for (grp, name), c in list(counts.items()):
            c["count"] -= base.get((grp, name), 0)
            if grp in char_groups and c["count"] > 0:
                print(f"warning: {stage}: {c['count']}× '{name}' in group '{grp}' differs from the Character "
                      f"Assembly (which has {[n for (g2, n) in base if g2 == grp]}); is one of the models out of date?",
                      file=sys.stderr)
                c["count"] = 0  # a mismatch, not something the stage adds
            elif c["count"] <= 0:
                c["count"] = 0
        counts = {k: c for k, c in counts.items() if c["count"] > 0}
    by_group = {}
    for r in counts.values():
        by_group.setdefault(r["group"], []).append(r)
    for g, rows in by_group.items():
        if g is None:
            for r in rows:
                print(f"warning: no FASTENER_GROUPS entry matches '{r['name']}' (size/length/position)", file=sys.stderr)
        elif len(rows) > 1:
            print(f"warning: fastener group '{g}' mixes sizes: "
                  + ", ".join(f"{r['count']}× {r['name']}" for r in rows) + " (a typo in the model?)", file=sys.stderr)
    order = {g["id"]: i for i, g in enumerate(FASTENER_GROUPS)}
    snap[stage] = sorted(counts.values(), key=lambda r: (order.get(r["group"], 99), r["name"]))
    FASTENERS_JSON.write_text(json.dumps(snap, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {FASTENERS_JSON.relative_to(ROOT)} [{stage}]: {sum(r['count'] for r in snap[stage])} fasteners")


def fastener_rows(stage):
    if not FASTENERS_JSON.exists():
        return ""
    notes = {g["id"]: g["note"] for g in FASTENER_GROUPS}
    scope = STAGES[stage]["scope"]
    rows = [f"| {r['name']} | {r['count']} {scope} | {notes.get(r['group'], '')} |"
            for r in json.loads(FASTENERS_JSON.read_text()).get(stage, [])]
    return "\n".join(rows) + "\n" if rows else ""


def fastener_size(group_id):
    """'M3×20' / 'M2' for a fastener group, from the snapshot of the CAD (so steps follow the model)."""
    rows = json.loads(FASTENERS_JSON.read_text()) if FASTENERS_JSON.exists() else {}
    found = [r for stage in rows.values() for r in stage if r["group"] == group_id]
    if not found:
        sys.exit(f"No '{group_id}' fasteners in {FASTENERS_JSON.relative_to(ROOT)}; run once with --character")
    best = max(found, key=lambda r: r["count"])  # the most common, if a group is mixed
    info = parse_fastener(dict(name=best["name"], v=np.zeros((1, 3))))
    return info["size"] + (f"×{info['length']:g}" if info["length"] and info["kind"] == "screw" else "")


def parts_table(flaps, steps):
    head = "| File | Qty | Used in step |\n|---|---|---|" if steps else "| File | Qty |\n|---|---|"
    rows = []
    for key, f, q, step, _ in PRINTED:
        q = q.replace("{FLAPS}", str(flaps))
        if not steps:
            q = q.replace(" ⚠️", "") + (" (see below)" if key == "Flap" else "")
        rows.append(f"| `{f}` | {q} | {step} |" if steps else f"| `{f}` | {q} |")
    return head + "\n" + "\n".join(rows)


def render_docs():
    chars = firmware_char_set()
    flaps = len(chars)
    fx = facts(flaps)
    subs = {"PARTS_TABLE": parts_table(flaps, True), "CHAR_SET": chars, **{k.upper(): v for k, v in fx.items()}}
    text = TEMPLATE.read_text()
    for stage in STAGES:  # whole table rows, or nothing
        text = text.replace("{{FASTENER_ROWS_" + stage.upper() + "}}\n", fastener_rows(stage))
    text = re.sub(r"\{\{SIZE:([\w-]+)\}\}", lambda m: fastener_size(m.group(1)), text)
    for k, v in subs.items():
        text = text.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{\w+\}\}", text)
    if left:
        sys.exit(f"Unresolved placeholders in template: {sorted(set(left))}")
    check_photo_refs(text)
    block = (f"{PRINT_BEGIN}\n![Printed parts](images/01-printed-parts.png)\n\n"
             f"{parts_table(flaps, False)}\n{PRINT_END}")
    pm = PRINT_MD.read_text()
    if PRINT_BEGIN not in pm or PRINT_END not in pm:
        sys.exit(f"{PRINT_MD.relative_to(ROOT)} is missing the GENERATED PARTS markers")
    a, b = pm.index(PRINT_BEGIN), pm.index(PRINT_END) + len(PRINT_END)
    return text, pm[:a] + block + pm[b:]


def warn_unknown_stls():
    known = {f for _, f, *_ in PRINTED}
    for p in sorted(STL_DIR.glob("*.stl")):
        if p.name not in known and p.name not in NOT_IN_GUIDE:
            print(f"warning: {p.name} is in mechanical/stl/ but not in PRINTED; the guide ignores it",
                  file=sys.stderr)
    for f in sorted(known):
        if not (STL_DIR / f).exists():
            print(f"warning: {f} is in PRINTED but missing from mechanical/stl/", file=sys.stderr)


# ---------------------------------------------------------------------------
# Reading the CAD's assembly 3MFs
# ---------------------------------------------------------------------------
NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}


def read_3mf(path):
    with zipfile.ZipFile(path) as z:
        root = ET.parse(io.BytesIO(z.read("3D/3dmodel.model"))).getroot()
    objs = {}
    for o in root.find("m:resources", NS).findall("m:object", NS):
        d = {"name": o.get("name"), "mesh": None, "kids": []}
        mesh = o.find("m:mesh", NS)
        if mesh is not None:
            v = np.array([[float(x.get(c)) for c in "xyz"] for x in mesh.find("m:vertices", NS)])
            f = np.array([[int(x.get(c)) for c in ("v1", "v2", "v3")] for x in mesh.find("m:triangles", NS)])
            d["mesh"] = (v, f)
        comps = o.find("m:components", NS)
        if comps is not None:
            for c in comps:
                M = np.eye(4)
                if c.get("transform"):
                    a = list(map(float, c.get("transform").split()))
                    M[:3, :3] = np.array(a[:9]).reshape(3, 3).T  # 3MF uses row vectors
                    M[:3, 3] = a[9:12]
                d["kids"].append((int(c.get("objectid")), M))
        objs[int(o.get("id"))] = d
    top = int(root.find("m:build", NS)[0].get("objectid"))
    out = []

    def walk(oid, M):
        d = objs[oid]
        if d["mesh"] is not None:
            v, f = d["mesh"]
            out.append(dict(name=d["name"], v=((M[:3, :3] @ v.T).T + M[:3, 3]) * 1000.0, f=f))  # metres -> mm
        for cid, C in d["kids"]:
            walk(cid, M @ C)

    walk(top, np.eye(4))
    return out


def read_assemblies(paths):
    """Read one or more 3MFs into one list of bodies. Later files only add bodies that the
    earlier ones lack (same name and box), so a partial export can extend the Full Assembly."""
    world, seen = [], set()
    for path in paths:
        print(f"Reading {path} ...")
        for p in read_3mf(path):
            key = (p["name"], tuple(np.round(p["v"].min(0), 1)), tuple(np.round(p["v"].max(0), 1)))
            if key not in seen:
                seen.add(key)
                world.append(p)
    return world


def classify(p):
    """Bucket a 3MF body: 'printed', 'fastener', or a HARDWARE_PARTS group, by name and position."""
    n = p["name"]
    if n in IGNORED_BODIES or n.endswith(IGNORED_BOARD_TAGS):
        return "skip"  # deliberately out of the guide
    if n in PRINTED_ALIASES:
        return "printed"  # comes from mechanical/stl/ (or, for repeated parts, the 3MF copies)
    if FASTENER_RE.search(n):
        return "fastener"
    if re.search(r"\b(cable|wire)\b", n, re.I):
        return "cable"
    if n.startswith("28BYJ"):
        return "motor"
    if n in ("Plate", "Button___ _________", "PINS___ _________", "High_Part___ _________"):
        return "microsw"
    c = (p["v"].min(0) + p["v"].max(0)) / 2
    if n.startswith("Board~IWs3") or (c[1] > 17 and c[0] < 15) or (c[1] > 20 and c[0] < 5):
        return "strip"
    if c[0] > 18:
        return "pico_pcb"
    if c[0] < 0 and c[2] > 14:
        return "char_pcb"
    print(f"  unassigned 3MF body: {n[:50]}", file=sys.stderr)
    return None


def fit_gear(stl_v, asm_v):
    """Rigid STL -> assembly transform for a gear laid flat in the STL (axis Z) whose
    axis is X in the assembly. Tries both faces and searches the rotation about the axis."""
    ctr_s = (stl_v.min(0) + stl_v.max(0)) / 2
    ctr_a = (asm_v.min(0) + asm_v.max(0)) / 2
    rng = np.random.default_rng(0)
    sa = asm_v[rng.choice(len(asm_v), min(400, len(asm_v)), replace=False)] - ctr_a
    ss = stl_v[rng.choice(len(stl_v), min(400, len(stl_v)), replace=False)] - ctr_s
    bases = [np.array([[0, 0, 1], [1, 0, 0], [0, 1, 0]], float),
             np.array([[0, 0, -1], [1, 0, 0], [0, -1, 0]], float)]
    best = (1e18, None)
    for B in bases:
        base = ss @ B.T
        for deg in np.arange(0, 360, 0.5):
            c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
            Rx = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
            pts = base @ Rx.T
            d = np.linalg.norm(pts[:, None, :] - sa[None, :, :], axis=2)
            err = d.min(1).mean() + d.min(0).mean()
            if err < best[0]:
                best = (err, Rx @ B)
    R = best[1]
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = ctr_a - R @ ctr_s
    # sanity check: the placed STL must occupy the same box as the assembly's gear (the
    # rotation about the axis, i.e. tooth phase, is cosmetic and not checked)
    placed = stl_v @ R.T + M[:3, 3]
    box_err = max(np.abs(placed.min(0) - asm_v.min(0)).max(), np.abs(placed.max(0) - asm_v.max(0)).max())
    return M, box_err


def import_photos(src):
    """Build photos are phone-sized (2-8 MB each); keep a small copy of each in the repo."""
    photos = sorted(p for p in src.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if not photos:
        sys.exit(f"--photos {src}: no .jpg/.png files")
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    for p in photos:
        im = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
        im.thumbnail((PHOTO_MAX_PX, PHOTO_MAX_PX))
        out = PHOTO_DIR / (p.stem + ".jpg")
        im.save(out, quality=82, optimize=True)
    print(f"wrote {len(photos)} photos to {PHOTO_DIR.relative_to(ROOT)}/")


def check_photo_refs(text):
    missing = sorted({m for m in re.findall(r"\((images/photos/[^)]+)\)", text)
                      if not (ASSEMBLY_MD.parent / m).exists()})
    if missing:
        sys.exit("template shows photos that aren't in the repo (run with --photos DIR): " + ", ".join(missing))


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for stage in STAGES:
        ap.add_argument(f"--{stage}", metavar="3MF", action="append", help=f"CAD assembly for the {stage} stage (repeatable)")
    ap.add_argument("--no-images", action="store_true", help="skip rendering, only write the docs")
    ap.add_argument("--check", action="store_true", help="exit 1 if the docs are out of date (no writes)")
    ap.add_argument("--only", metavar="FILE", help="render just this figure, e.g. 05-wheel.png")
    ap.add_argument("--flap-glyphs", metavar="DIR", default=FLAPS_DIR if FLAPS_DIR.is_dir() else None,
                     help="dir of per-character flap_<n>_..3mf files from generate_flaps.py, for real "
                     f"printed characters on the flaps in the figures (default: {FLAPS_DIR.relative_to(ROOT)})")
    ap.add_argument("--photos", metavar="DIR", help="dir of photos of a real build (NN[a-z]-name.jpg); "
                     f"shrunk into {PHOTO_DIR.relative_to(ROOT)}/ for the template to show")
    args = ap.parse_args()
    if args.photos and not args.check:
        import_photos(Path(args.photos))

    global FLAP_GLYPHS
    if args.flap_glyphs and not (args.check or args.no_images):
        FLAP_GLYPHS = load_flap_glyphs(args.flap_glyphs, FLAP_GLYPH_FIRST, FLAP_FAN_COUNT)

    warn_unknown_stls()
    libs = {}
    if not args.check:
        cache = {}
        for stage in STAGES:
            paths = getattr(args, stage)
            if paths:
                key = (tuple(paths), stage)
                if key not in cache:
                    cache[key] = Library(paths, stage)
                libs[stage] = cache[key]
                save_fasteners(stage, libs[stage])
    assembly, print_guide = render_docs()
    if args.check:
        stale = [p.name for p, t in ((ASSEMBLY_MD, assembly), (PRINT_MD, print_guide))
                 if not p.exists() or p.read_text() != t]
        if stale:
            print("stale: " + ", ".join(stale) + "  (run tools/generate_assembly_guide.py)")
            sys.exit(1)
        print("docs up to date")
        return
    if not args.no_images:
        if not libs:
            ap.error("pass at least one of --character/--four/--pico to render images (or use --no-images)")
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        for fig in FIGURES:
            if fig["stage"] not in libs:
                continue
            if args.only and args.only != fig["file"]:
                continue
            render_figure(libs[fig["stage"]], fig).save(IMG_DIR / fig["file"], optimize=True)
            print("wrote", (IMG_DIR / fig["file"]).relative_to(ROOT))
        if "character" in libs and (not args.only or args.only == "01-printed-parts.png"):
            render_parts_sheet(libs["character"], len(firmware_char_set())).save(IMG_DIR / "01-printed-parts.png", optimize=True)
            print("wrote mechanical/images/01-printed-parts.png")
    ASSEMBLY_MD.write_text(assembly)
    PRINT_MD.write_text(print_guide)
    print("wrote", ASSEMBLY_MD.relative_to(ROOT), "and", PRINT_MD.relative_to(ROOT))


if __name__ == "__main__":
    main()
