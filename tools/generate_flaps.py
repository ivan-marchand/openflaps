"""
Split-flap colored 3MF generator (double-sided, half-glyph per side)

What it does:
- Loads your STL flap.
- Creates TWO "decal" meshes from text masks:
    * Front face: half of letter A (or any char)
    * Back face: half of letter B (next char), rotated 180°
- Writes a 3MF where:
    * flap = black material
    * front decal = colored material
    * back decal = colored material

Why decals?
Most slicers assign color/material to *geometry*, not to “paint on the same triangles”.
We keep the decal thickness extremely small (e.g. 0.05mm) so it appears flat.

Requirements:
  pip install trimesh pillow scikit-image numpy

Usage:
  python splitflap_3mf.py

It will write: Flap_A_front_B_back_TOP.3mf (edit parameters at bottom)
"""

import os
import zipfile
import numpy as np
import xml.etree.ElementTree as ET
from xml.dom import minidom

import trimesh
from PIL import Image, ImageDraw, ImageFont
from skimage import measure


# ----------------------------
# Font + bitmap helpers
# ----------------------------

def _load_font(size=180):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def apply_half_mask(mask: np.ndarray, half="top", gap_px=0) -> np.ndarray:
    """
    Keep only half of the glyph mask.
    half: "top", "bottom", "left", "right"
    gap_px: remove a small band at the split line so seam doesn't touch
    """
    h, w = mask.shape
    out = mask.copy()

    if half in ("top", "bottom"):
        mid = h // 2
        if half == "top":
            out[mid + gap_px:, :] = 0
        else:
            out[:max(mid - gap_px, 0), :] = 0

    elif half in ("left", "right"):
        mid = w // 2
        if half == "left":
            out[:, mid + gap_px:] = 0
        else:
            out[:, :max(mid - gap_px, 0)] = 0

    else:
        raise ValueError("half must be one of: top, bottom, left, right")

    return out


# ----------------------------
# Text -> watertight mesh (via voxels + marching cubes)
# ----------------------------

def text_to_voxel_mesh(
    text: str,
    thickness_vox: int = 6,
    img_size: int = 256,
    font_size: int = 180,
    pad: int = 2,
    half: str | None = None,
    gap_px: int = 2,
) -> trimesh.Trimesh:
    """
    Render text to a binary mask, extrude in voxel space, marching cubes -> watertight mesh.
    """
    font = _load_font(font_size)

    img = Image.new("L", (img_size, img_size), 0)
    draw = ImageDraw.Draw(img)

    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (img_size - w) // 2 - bbox[0]
    y = (img_size - h) // 2 - bbox[1]
    draw.text((x, y), text, fill=255, font=font)

    mask = (np.array(img) > 128).astype(np.uint8)

    if half is not None:
        mask = apply_half_mask(mask, half=half, gap_px=gap_px)

    # volume is (z, y, x)
    vol = np.repeat(mask[None, :, :], thickness_vox, axis=0)
    vol = np.pad(vol, ((pad, pad), (pad, pad), (pad, pad)), mode="constant", constant_values=0)
    if np.count_nonzero(vol) == 0:
        return None
    verts, faces, _, _ = measure.marching_cubes(vol.astype(float), level=0.5)

    # marching cubes verts are (z,y,x) -> convert to (x,y,z)
    V = np.column_stack([verts[:, 2], verts[:, 1], verts[:, 0]])
    m = trimesh.Trimesh(vertices=V, faces=faces, process=True)
    return m


def make_letter_decal_mesh(
    text: str,
    target_width: float,
    target_height: float,
    thickness_mm: float = 0.05,
    half: str | None = None,
    gap_px: int = 2,
) -> trimesh.Trimesh:
    """
    Make a thin decal mesh from text, scaled to fit target width/height.
    Output mesh uses axes:
      X = width
      Z = "up" on the flap
      Y = stand-off (thickness direction for decal)
    """
    if text == " ":
        return None, None

    # Use the letter A as a reference
    ref_top = text_to_voxel_mesh("A", thickness_vox=6, img_size=256, font_size=180, half="top", gap_px=gap_px)
    ref_bottom = text_to_voxel_mesh("A", thickness_vox=6, img_size=256, font_size=180, half="bottom", gap_px=gap_px)
    ref_height = max(ref_top.extents[1], ref_bottom.extents[1])

    if text in BOTTOM_CHARS:
        if half == "top":
            return None, None
        m_top = None
        m_bottom = text_to_voxel_mesh(text, thickness_vox=6, img_size=256, font_size=180, half=None, gap_px=gap_px)
    elif text in TOP_CHARS:
        if half == "bottom":
            return None, None
        m_top = text_to_voxel_mesh(text, thickness_vox=6, img_size=256, font_size=180, half=None, gap_px=gap_px)
        m_bottom = None
    else:
        m_top = text_to_voxel_mesh(text, thickness_vox=6, img_size=256, font_size=180, half="top", gap_px=gap_px)
        m_bottom = text_to_voxel_mesh(text, thickness_vox=6, img_size=256, font_size=180, half="bottom", gap_px=gap_px)

    top_width = m_top.extents[0] if m_top is not None else 0
    bottom_width = m_bottom.extents[0] if m_bottom is not None else 0
    letter_width = max(top_width, bottom_width)
    #letter_height = max(m_top.extents[1], m_bottom.extents[1])

    # Fit in X/Y extents of this mesh (before remap)
    s_w = target_width / max(letter_width, 1e-9)
    s_h = target_height / max(ref_height, 1e-9)

    # Create a scaling matrix
    matrix = np.eye(4)
    matrix[0, 0] = s_h if letter_width * s_h <= target_width else s_w
    matrix[1, 1] = s_h
    matrix[2, 2] = 1

    # Apply the transformation
    if m_top is not None:
        m_top.apply_transform(matrix)
    if m_bottom is not None:
        m_bottom.apply_transform(matrix)

    # Select correct half
    if half == "top":
        m = m_top
    else:
        m = m_bottom

    if m is None:
        return None, None

    if top_width > bottom_width:
        letter_center = m_top.bounds.mean(axis=0)[0]
    else:
        letter_center = m_bottom.bounds.mean(axis=0)[0]

    # Force thickness by scaling Z only
    tz = m.extents[2]
    if tz > 1e-9:
        m.apply_scale([1, 1, thickness_mm / tz])

    # Center at origin
    #m.apply_translation(-m.bounds.mean(axis=0))

    # Remap axes so it's easy to place on a face:
    # original (x,y,z) -> (X, Y, Z) = (width, stand-off, height)
    V = m.vertices
    V2 = np.column_stack([V[:, 0], V[:, 2], V[:, 1]])
    return trimesh.Trimesh(vertices=V2, faces=m.faces, process=True), letter_center


# ----------------------------
# Placement helpers (front/back face + 180° rotation)
# ----------------------------

def remap_decal_to_base_axes(
    decal: trimesh.Trimesh,
    thickness_axis: int,
    w_axis: int,
    h_axis: int,
) -> trimesh.Trimesh:
    """
    decal coordinates are (X=width, Y=stand-off, Z=height).
    Remap into base axes so:
      width -> w_axis
      stand-off -> thickness_axis
      height -> h_axis
    """
    V = decal.vertices.copy()
    w = V[:, 0]
    stand = V[:, 1]
    h = V[:, 2]

    out = np.zeros_like(V)
    out[:, w_axis] = w
    out[:, thickness_axis] = stand
    out[:, h_axis] = h

    return trimesh.Trimesh(vertices=out, faces=decal.faces, process=True)


def mirror_mesh(mesh: trimesh.Trimesh, axis: int, origin: np.ndarray) -> trimesh.Trimesh:
    """
    Mirrors `mesh` by reflecting coordinates along `axis` (0=X, 1=Y, 2=Z)
    about a plane passing through `origin`.

    Example: axis=w_axis mirrors left-right in the flap plane.
    """
    m = mesh.copy()
    V = m.vertices.copy()

    # reflect around origin: v' = origin + S*(v-origin) where S has -1 on chosen axis
    V = V - origin
    V[:, axis] *= -1
    V = V + origin

    # IMPORTANT: mirroring flips triangle winding; fix by reversing faces
    m.vertices = V
    m.faces = m.faces[:, ::-1]
    m.fix_normals()
    return m

def place_decal_on_face(
    base_mesh: trimesh.Trimesh,
    decal_mesh: trimesh.Trimesh,
    thickness_axis: int,
    face: str,
    thickness_mm: float,
    letter_center: float,
    inset_mm: float = 0.001,
    rotate_180: bool = False,
    rotate_axis: int | None = None,
    center_axis: int | None = None,  # <-- NEW (e.g. width axis)
):
    """
    Places decal on front/back face.
    IMPORTANT: Does NOT recenter on the "vertical" axis, so bottom alignment is preserved.

    center_axis:
      - set this to the flap "width" axis if you want the letter centered left/right
      - set to None to keep current X/Y/Z position (except face push)
    """
    m = decal_mesh.copy()
    b = base_mesh.bounds
    base_center = b.mean(axis=0)

    # Optional 180° rotation about rotate_axis through base center
    if rotate_180:
        ax = thickness_axis if rotate_axis is None else rotate_axis
        axis_vec = np.zeros(3)
        axis_vec[ax] = 1.0
        T1 = trimesh.transformations.translation_matrix(-base_center)
        R  = trimesh.transformations.rotation_matrix(np.pi, axis_vec)
        T2 = trimesh.transformations.translation_matrix(base_center)
        m.apply_transform(T2 @ R @ T1)

    # Center only on the chosen axis (typically width), leave others intact
    if center_axis is not None:
        t = np.zeros(3)
        t[center_axis] = base_center[center_axis] - letter_center
        m.apply_translation(t)

    # Push/pull onto selected face along thickness axis
    if face == "front":
        face_pos = b[1, thickness_axis] + inset_mm
        shift = face_pos - m.bounds[0, thickness_axis] - thickness_mm  # push min onto face
    elif face == "back":
        face_pos = b[0, thickness_axis] - inset_mm
        shift = face_pos - m.bounds[1, thickness_axis] + thickness_mm  # pull max onto face
    else:
        raise ValueError("face must be 'front' or 'back'")

    t = np.zeros(3)
    t[thickness_axis] = shift
    m.apply_translation(t)

    return m


# ----------------------------
# Minimal 3MF writer (core + basematerials with colors)
# ----------------------------

def _prettify_xml(elem) -> bytes:
    rough = ET.tostring(elem, encoding="utf-8", xml_declaration=True)
    reparsed = minidom.parseString(rough)
    return reparsed.toprettyxml(indent="  ", encoding="utf-8")


def _mesh_to_3mf_object(resources_el, obj_id: int, mesh: trimesh.Trimesh,
                        basemats_id: int, mat_index: int, name: str | None = None):
    obj = ET.SubElement(resources_el, "object", {"id": str(obj_id), "type": "model"})
    if name:
        obj.set("name", name)

    mesh_el = ET.SubElement(obj, "mesh")
    verts_el = ET.SubElement(mesh_el, "vertices")

    V = np.asarray(mesh.vertices, dtype=float)
    for v in V:
        ET.SubElement(
            verts_el,
            "vertex",
            {"x": f"{v[0]:.6f}", "y": f"{v[1]:.6f}", "z": f"{v[2]:.6f}"},
        )

    tris_el = ET.SubElement(mesh_el, "triangles")
    F = np.asarray(mesh.faces, dtype=int)
    for f in F:
        ET.SubElement(
            tris_el,
            "triangle",
            {
                "v1": str(int(f[0])),
                "v2": str(int(f[1])),
                "v3": str(int(f[2])),
                "pid": str(basemats_id),
                "p1": str(int(mat_index)),
            },
        )


def write_3mf(path: str, meshes_with_colors: list[tuple[str, trimesh.Trimesh, str]], unit: str = "millimeter"):
    """
    meshes_with_colors: list of (name, mesh, color)
      color can be "#RRGGBB" or "#RRGGBBAA"
    """
    ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    ET.register_namespace("", ns)

    model = ET.Element(f"{{{ns}}}model", {"unit": unit})
    resources = ET.SubElement(model, "resources")

    basemats_id = 1
    basemats = ET.SubElement(resources, "basematerials", {"id": str(basemats_id)})

    def norm_color(c: str) -> str:
        c = c.strip()
        if not c.startswith("#"):
            c = "#" + c
        if len(c) == 7:
            c = c + "FF"
        if len(c) != 9:
            raise ValueError("Color must be #RRGGBB or #RRGGBBAA")
        return c.upper()

    colors = [norm_color(c) for _, _, c in meshes_with_colors]
    unique = []
    for c in colors:
        if c not in unique:
            unique.append(c)

    for i, c in enumerate(unique):
        ET.SubElement(basemats, "base", {"name": f"mat{i}", "displaycolor": c})

    color_to_index = {c: i for i, c in enumerate(unique)}

    obj_ids = []
    next_id = 2
    for name, mesh, c in meshes_with_colors:
        idx = color_to_index[norm_color(c)]
        _mesh_to_3mf_object(resources, next_id, mesh, basemats_id, idx, name=name)
        obj_ids.append(next_id)
        next_id += 1

    build = ET.SubElement(model, "build")
    for oid in obj_ids:
        ET.SubElement(build, "item", {"objectid": str(oid)})

    model_xml = _prettify_xml(model)

    # required package files
    content_types = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>
"""
    rels = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model_xml)

def align_decal_bottom_to_flap(base_mesh: trimesh.Trimesh,
                              decal_mesh: trimesh.Trimesh,
                              ref_mesh: trimesh.Trimesh,
                              h_axis: int,
                              margin_mm: float = 0.0) -> trimesh.Trimesh:
    """
    Moves decal so its bottom (min on h_axis) is flush with the flap bottom (min on h_axis).
    margin_mm: optional small gap above the bottom edge.
    """
    m = decal_mesh.copy()
    base_min = base_mesh.bounds[0, h_axis]
    decal_min = ref_mesh.bounds[0, h_axis]

    t = np.zeros(3)
    t[h_axis] = (base_min + margin_mm) - decal_min
    #t[h_axis] = -48.93923077
    m.apply_translation(t)
    return m

def align_decal_top_to_flap(base_mesh: trimesh.Trimesh,
                            decal_mesh: trimesh.Trimesh,
                            ref_mesh: trimesh.Trimesh,
                            h_axis: int,
                            margin_mm: float = 0.0) -> trimesh.Trimesh:
    """
    Moves decal so its bottom (min on h_axis) is flush with the flap bottom (min on h_axis).
    margin_mm: optional small gap above the bottom edge.
    """
    m = decal_mesh.copy()
    base_max = base_mesh.bounds[-1, h_axis]
    decal_max = ref_mesh.bounds[-1, h_axis]

    t = np.zeros(3)
    t[h_axis] = (base_max - margin_mm) - decal_max
    #t[h_axis] = -28.93923077
    m.apply_translation(t)
    return m

# ----------------------------
# End-to-end: double-sided split-flap
# ----------------------------

def generate_double_sided_splitflap_3mf(
    stl_path: str,
    out_3mf_path: str,
    front_text: str,
    back_text: str,
    flap_color: str = "#000000",
    text_color: str = "#FFFFFF",
    decal_thickness_mm: float = 0.05,   # super thin "paint" layer
    inset_mm: float = 0.001,            # tiny offset to avoid coplanar issues
    gap_px: int = 2,                    # seam clearance in bitmap pixels
    back_rotate_180: bool = True,
    back_rotate_axis: int | None = None,
    size_scale: float = 0.70,           # how much of the face the glyph fills
):
    base = trimesh.load_mesh(stl_path)

    # Find thickness axis as smallest extent
    ext = base.extents
    thickness_axis = int(np.argmin(ext))

    # Two in-plane axes
    axes = [0, 1, 2]
    axes.remove(thickness_axis)
    w_axis, h_axis = axes[0], axes[1]

    target_w = 21.0
    #target_w = 19.439999999999998
    target_h = ext[h_axis] * size_scale

    # Create reference decal (letter A)
    ref_front_decal, front_center = make_letter_decal_mesh(
        "A", target_w, target_h,
        thickness_mm=decal_thickness_mm,
        half="top", gap_px=gap_px
    )
    ref_back_decal, back_center = make_letter_decal_mesh(
        "A", target_w, target_h,
        thickness_mm=decal_thickness_mm,
        half="bottom", gap_px=gap_px
    )
    # Remap into base coordinate axes
    ref_front_decal = remap_decal_to_base_axes(ref_front_decal, thickness_axis, w_axis, h_axis)
    ref_back_decal  = remap_decal_to_base_axes(ref_back_decal,  thickness_axis, w_axis, h_axis)

    # Create decals in their own coordinate system
    front_decal, front_center = make_letter_decal_mesh(
        front_text, target_w, target_h,
        thickness_mm=decal_thickness_mm,
        half="top", gap_px=gap_px
    )
    back_decal, back_center = make_letter_decal_mesh(
        back_text, target_w, target_h,
        thickness_mm=decal_thickness_mm,
        half="bottom", gap_px=gap_px
    )

    # Create Front mesh
    if front_decal is not None:
        front_decal = remap_decal_to_base_axes(front_decal, thickness_axis, w_axis, h_axis)
        # bottom-align both halves to flap bottom edge
        if front_text in TOP_CHARS:
            front_decal = align_decal_top_to_flap(base, front_decal, ref_mesh=front_decal, h_axis=h_axis, margin_mm=(1 - size_scale) * target_h)
        else:
            front_decal = align_decal_top_to_flap(base, front_decal, ref_mesh=ref_front_decal, h_axis=h_axis, margin_mm=0.0)
        # Place on opposite faces
        front_decal = place_decal_on_face(
            base, front_decal, thickness_axis,
            face="front", inset_mm=inset_mm,
            rotate_180=False,
            center_axis = w_axis,
            thickness_mm=decal_thickness_mm,
            letter_center=front_center,
        )

    # Create Back mesh
    if back_decal is not None:
        back_decal  = remap_decal_to_base_axes(back_decal,  thickness_axis, w_axis, h_axis)
        # bottom-align both halves to flap bottom edge
        if back_text in BOTTOM_CHARS:
            back_decal = align_decal_top_to_flap(base, back_decal, ref_mesh=back_decal, h_axis=h_axis, margin_mm=(1 - size_scale) * target_h)
        else:
            back_decal = align_decal_bottom_to_flap(base, back_decal, ref_mesh=ref_back_decal, h_axis=h_axis, margin_mm=0.0)
        # Place on opposite faces
        back_decal = place_decal_on_face(
            base, back_decal, thickness_axis,
            face="back", inset_mm=inset_mm,
            rotate_180=back_rotate_180,
            rotate_axis=w_axis,
            center_axis=w_axis,
            thickness_mm=decal_thickness_mm,
            letter_center=back_center,
        )

    meshes = [("flap", base, flap_color)]
    if front_decal is not None:
        meshes.append((f"{front_text}_top_front", front_decal, text_color))
    if back_decal is not None:
        meshes.append((f"{back_text}_bottom_back", back_decal, text_color))

    # Write 3MF with 3 objects (flap + 2 decals)
    write_3mf(
        out_3mf_path,
        meshes,
    )

CHAR_SET = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/°"
BOTTOM_CHARS = ".,"
TOP_CHARS = "°"

if __name__ == "__main__":
    CHAR_SET += " "
    it = iter(CHAR_SET[::-1])
    front_char = next(it)
    index = 1
    while True:
        back_char = front_char
        try:
            front_char = next(it)
        except:
            # No more characters to read
            break

        # EDIT THESE:
        from pathvalidate import sanitize_filename
        STL_PATH = "flap.stl"
        OUT_3MF  = f"3mf/flap_{index}_{sanitize_filename(front_char)}_{sanitize_filename(back_char)}.3mf"
        index += 1

        generate_double_sided_splitflap_3mf(
            stl_path=STL_PATH,
            out_3mf_path=OUT_3MF,
            front_text=front_char,
            back_text=back_char,
            flap_color="#000000",
            text_color="#FFFFFF",
            decal_thickness_mm=0.3,    # thin "paint"
            inset_mm=0.001,
            gap_px=0,                   # seam clearance
            back_rotate_180=True,
            back_rotate_axis=None,      # None = rotate around thickness axis
            size_scale=0.8,
        )

        print(f"Wrote {OUT_3MF}")
