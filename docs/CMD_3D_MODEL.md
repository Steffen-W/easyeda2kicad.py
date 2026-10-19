# EasyEDA 3D Model Format Reference

This document describes how 3D models are obtained and integrated into KiCad footprints.

**Key Concept**: 3D models are **downloaded** from EasyEDA servers (OBJ + STEP), not generated locally.

---

## Workflow Overview

1. Extract UUID and metadata from SVGNODE in footprint data
2. Download OBJ and STEP files from EasyEDA using UUID
3. Convert OBJ → WRL (VRML) for KiCad visualization
4. Pass-through STEP files unchanged (binary)
5. Reference the WRL model in the footprint with the EasyEDA placement as model offset

---

## SVGNODE Metadata

See [CMD_FOOTPRINT.md](CMD_FOOTPRINT.md#svgnode---3d-model-metadata) for command format.

**Example:**

```
SVGNODE~{"gId":"g1_outline","attrs":{"uuid":"43ba165dae7e4f5b88ae140d98d63cbd","title":"IND-SMD_L7.0-W6.6-H3.0","c_origin":"3987.9646,3009","z":"0","c_rotation":"0,0,0"}}~...
```

**Attributes:**

| Field        | Type   | Description                    | Example                  |
|--------------|--------|--------------------------------|--------------------------|
| `uuid`       | string | Model identifier for download  | "43ba165d..."            |
| `title`      | string | Model name (becomes filename)  | "IND-SMD_L7.0-W6.6-H3.0" |
| `c_origin`   | string | Translation (x,y) in EE units  | "3987.9646,3009"         |
| `z`          | string | Z-height in EE units           | "0"                      |
| `c_rotation` | string | Rotation (rx,ry,rz) in degrees | "0,0,0"                  |

## Download Endpoints

### OBJ Format (Text-based)

**Endpoint:**

```
https://modules.easyeda.com/3dmodel/{uuid}
```

**Response:** Text file with vertices and materials

**Units:** Millimeters

### STEP Format (Binary CAD)

**Endpoint:**

```
https://modules.easyeda.com/qAxj6KHrDKw4blvCG8QJPs7Y/{uuid}
```

**Response:** Binary STEP file (ISO 10303-21)

**Notes:**

- `qAxj6KHrDKw4blvCG8QJPs7Y` is EasyEDA's storage bucket ID
- STEP files are passed through unchanged

---

## OBJ File Structure

**Material Block:**

```obj
newmtl material_name
Ka 0.2 0.2 0.2    # Ambient color (R G B)
Kd 0.8 0.8 0.8    # Diffuse color (R G B)
Ks 0.5 0.5 0.5    # Specular color (R G B)
d 1.0              # Dissolve/opacity (0.0-1.0)
endmtl
```

**Vertices:**

```obj
v 1.234 5.678 9.012
v 2.345 6.789 0.123
```

**Faces:**

```obj
usemtl material_name
f 1 2 3           # Triangle using vertices 1,2,3
f 2 3 4
```

---

## OBJ → WRL Conversion

### Coordinate Conversion

**OBJ (mm) → WRL (KiCad VRML unit = 0.1 inch):**

```python
vrml = mm / 2.54
```

**Example:**

- OBJ: `v 2.54 5.08 7.62` (mm)
- WRL: `1.0 2.0 3.0` (0.1 inch)

### VRML Output Format

```vrml
#VRML V2.0 utf8

Shape {
  appearance Appearance {
    material Material {
      diffuseColor 0.8 0.8 0.8
      specularColor 0.5 0.5 0.5
      ambientIntensity 0.2
      transparency 0
      shininess 0.5
    }
  }
  geometry IndexedFaceSet {
    ccw TRUE
    solid FALSE
    coord Coordinate {
      point [ 1.0 2.0 3.0, 2.0 3.0 4.0, ... ]
    }
    coordIndex [ 0, 1, 2, -1, 1, 2, 3, -1, ... ]
  }
}
```

**Notes:**

- Each OBJ material → separate VRML Shape
- Vertex indices: 1-based (OBJ) → 0-based (VRML)
- Face delimiter: `-1`

---

## Coordinate Transformation

### Centering

During OBJ → WRL conversion the model is centered on its own origin: the XY
center of its bounding box moves to (0, 0) and its lowest point to z = 0. This
is the frame in which EasyEDA places models (`smt-gl-engine.js`, `fi()`).

### Translation

The EasyEDA placement becomes the model offset of the footprint, in mm with y up:

```python
x = (c_origin.x - head.x) * 0.254
y = -(c_origin.y - head.y) * 0.254
z = z * 0.254
```

If the center of the 2D outline in the SVGNODE child nodes differs from
`(x, y)` by more than 0.1 mm, the outline center is used instead.

KiCad applies the model offset after the model rotation
(`CalcModelMatrix()` in `3d-viewer/3d_math.cpp`: offset + Rz·Ry·Rx · point).
The placement must therefore not be part of the WRL vertices, or KiCad would
rotate it along with the model.

### Rotation

```python
# EasyEDA → KiCad (degrees)
rx = 360 - model_3d.rotation.x
ry = 360 - model_3d.rotation.y
rz = 360 - model_3d.rotation.z
```

---

## KiCad Footprint Integration

**Model Reference:**

```
(model "${EASYEDA2KICAD}/DC-IN-TH_L14.4-W9.0-H11.0.wrl"
  (offset (xyz -3.65 2.2 -4))
  (scale (xyz 1 1 1))
  (rotate (xyz 0 0 90))
)
```

**File Structure:**

```
MyLibrary.pretty/
  └── Footprint.kicad_mod
MyLibrary.3dshapes/
  ├── IND-SMD_L7.0-W6.6-H3.0.wrl   # VRML (converted from OBJ)
  └── IND-SMD_L7.0-W6.6-H3.0.step  # STEP (binary pass-through)
```

## Limitations

| Issue                  | Description                                 |
|------------------------|---------------------------------------------|
| Network Required       | 3D models cannot be obtained offline        |
| Material Approximation | VRML may not perfectly match OBJ appearance |
| Server Dependency      | Requires EasyEDA servers to be available    |
| STEP Placement         | The STEP file is passed through unchanged; it matches the WRL model only if EasyEDA stores it centered with its bottom at z = 0 |

---

## Debug Caching

Enable local caching for faster re-conversion:

```python
import logging

logging.basicConfig(level=logging.DEBUG)
```

**Cache Location:** `.easyeda_cache/`
**Files:** `{uuid}.obj`, `{uuid}.step`
