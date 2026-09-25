"""Lecture et écriture de GLB, sans dépendance au-delà de numpy.

La chaîne a besoin de trois gestes sur le glTF :
  - écrire un mesh PBR (le factice de l'étage 3D) ;
  - écrire un mesh skinné sur un squelette nommé (le rig) ;
  - ajouter des clips d'animation à un GLB existant, par nom de nœud
    (les cinq poses de contrôle du rig, puis le bake de la timeline).

Conventions glTF, les mêmes partout dans la chaîne : mètres, Y en haut,
+Z devant, quaternions [x, y, z, w], matrices rangées par colonnes.
"""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import numpy as np

MAGIC, VERSION = 0x46546C67, 2
CHUNK_JSON, CHUNK_BIN = 0x4E4F534A, 0x004E4942

FLOAT, UINT, USHORT, UBYTE = 5126, 5125, 5123, 5121
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
TYPES = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4", 16: "MAT4"}
NP = {FLOAT: np.float32, UINT: np.uint32, USHORT: np.uint16, UBYTE: np.uint8}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _pad(b: bytes | bytearray, fill: bytes) -> bytes:
    return bytes(b) + fill * ((4 - len(b) % 4) % 4)


class GLB:
    def __init__(self) -> None:
        self.doc: dict = {"asset": {"version": "2.0", "generator": "character-factory"},
                          "scene": 0, "scenes": [{"nodes": []}]}
        self.bin = bytearray()

    # ── lecture ────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: str | Path) -> "GLB":
        data = Path(path).read_bytes()
        magic, version, total = struct.unpack_from("<III", data, 0)
        if magic != MAGIC or version != 2:
            raise ValueError(f"{path} n'est pas un GLB 2.0")
        glb = cls()
        off = 12
        while off < total:
            length, kind = struct.unpack_from("<II", data, off)
            chunk = data[off + 8: off + 8 + length]
            if kind == CHUNK_JSON:
                glb.doc = json.loads(chunk.decode("utf-8"))
            elif kind == CHUNK_BIN:
                glb.bin = bytearray(chunk)
            off += 8 + length
        return glb

    def read_accessor(self, index: int) -> np.ndarray:
        acc = self.doc["accessors"][index]
        view = self.doc["bufferViews"][acc["bufferView"]]
        n = NCOMP[acc["type"]]
        dtype = NP[acc["componentType"]]
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = view.get("byteStride")
        count = acc["count"]
        if stride and stride != n * np.dtype(dtype).itemsize:
            raw = np.frombuffer(bytes(self.bin), dtype=np.uint8)
            rows = [np.frombuffer(raw[start + i * stride: start + i * stride + n * np.dtype(dtype).itemsize].tobytes(),
                                  dtype=dtype) for i in range(count)]
            arr = np.stack(rows)
        else:
            arr = np.frombuffer(bytes(self.bin[start: start + count * n * np.dtype(dtype).itemsize]), dtype=dtype)
            arr = arr.reshape(count, n) if n > 1 else arr
        return arr.copy()

    def node_index(self) -> dict[str, int]:
        return {n.get("name", f"node_{i}"): i for i, n in enumerate(self.doc.get("nodes", []))}

    # ── écriture bas niveau ────────────────────────────────────────

    def _list(self, key: str) -> list:
        return self.doc.setdefault(key, [])

    def view(self, data: bytes, target: int | None = None) -> int:
        while len(self.bin) % 4:
            self.bin.append(0)
        bv = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            bv["target"] = target
        self.bin.extend(data)
        views = self._list("bufferViews")
        views.append(bv)
        return len(views) - 1

    def accessor(self, array: np.ndarray, component: int = FLOAT, *, target: int | None = None,
                 minmax: bool = False, normalized: bool = False, kind: str | None = None) -> int:
        arr = np.ascontiguousarray(array, dtype=NP[component])
        count = arr.shape[0]
        n = 1 if arr.ndim == 1 else int(np.prod(arr.shape[1:]))
        acc = {"bufferView": self.view(arr.tobytes(), target), "componentType": component,
               "count": count, "type": kind or TYPES[n]}
        if normalized:
            acc["normalized"] = True
        if minmax:
            flat = arr.reshape(count, n)
            acc["min"] = [float(x) for x in flat.min(axis=0)]
            acc["max"] = [float(x) for x in flat.max(axis=0)]
        accs = self._list("accessors")
        accs.append(acc)
        return len(accs) - 1

    # ── matières ───────────────────────────────────────────────────

    def texture(self, png: bytes) -> int:
        images = self._list("images")
        images.append({"bufferView": self.view(png), "mimeType": "image/png"})
        samplers = self._list("samplers")
        if not samplers:
            samplers.append({"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497})
        textures = self._list("textures")
        textures.append({"sampler": 0, "source": len(images) - 1})
        return len(textures) - 1

    def material(self, name: str, *, base_color: bytes | None = None, metallic_roughness: bytes | None = None,
                 normal: bytes | None = None, base_factor=(1, 1, 1, 1), metallic: float = 1.0,
                 roughness: float = 1.0) -> int:
        pbr: dict = {"baseColorFactor": list(base_factor), "metallicFactor": metallic, "roughnessFactor": roughness}
        mat: dict = {"name": name, "pbrMetallicRoughness": pbr}
        if base_color:
            pbr["baseColorTexture"] = {"index": self.texture(base_color)}
        if metallic_roughness:
            pbr["metallicRoughnessTexture"] = {"index": self.texture(metallic_roughness)}
        if normal:
            mat["normalTexture"] = {"index": self.texture(normal)}
        mats = self._list("materials")
        mats.append(mat)
        return len(mats) - 1

    # ── géométrie ──────────────────────────────────────────────────

    def mesh(self, name: str, *, positions: np.ndarray, normals: np.ndarray, uvs: np.ndarray | None,
             indices: np.ndarray, material: int | None = None, joints: np.ndarray | None = None,
             weights: np.ndarray | None = None) -> int:
        attrs = {"POSITION": self.accessor(positions, target=ARRAY_BUFFER, minmax=True),
                 "NORMAL": self.accessor(normals, target=ARRAY_BUFFER)}
        if uvs is not None:
            attrs["TEXCOORD_0"] = self.accessor(uvs, target=ARRAY_BUFFER)
        if joints is not None:
            attrs["JOINTS_0"] = self.accessor(joints, USHORT, target=ARRAY_BUFFER)
            attrs["WEIGHTS_0"] = self.accessor(weights, target=ARRAY_BUFFER)
        prim = {"attributes": attrs,
                "indices": self.accessor(indices.reshape(-1), UINT, target=ELEMENT_ARRAY_BUFFER, kind="SCALAR"),
                "mode": 4}
        if material is not None:
            prim["material"] = material
        meshes = self._list("meshes")
        meshes.append({"name": name, "primitives": [prim]})
        return len(meshes) - 1

    def node(self, name: str, *, mesh: int | None = None, skin: int | None = None, children: list[int] | None = None,
             translation=None, rotation=None, scale=None, root: bool = False) -> int:
        n: dict = {"name": name}
        if mesh is not None:
            n["mesh"] = mesh
        if skin is not None:
            n["skin"] = skin
        if children:
            n["children"] = list(children)
        if translation is not None:
            n["translation"] = [float(x) for x in translation]
        if rotation is not None:
            n["rotation"] = [float(x) for x in rotation]
        if scale is not None:
            n["scale"] = [float(x) for x in scale]
        nodes = self._list("nodes")
        nodes.append(n)
        if root:
            self.doc["scenes"][0]["nodes"].append(len(nodes) - 1)
        return len(nodes) - 1

    def skin(self, joints: list[int], inverse_bind: np.ndarray, *, skeleton: int | None = None, name: str = "skin") -> int:
        # MAT4 par colonnes : on transpose chaque matrice avant d'aplatir.
        ibm = np.ascontiguousarray(np.transpose(inverse_bind, (0, 2, 1)).reshape(len(joints), 16), dtype=np.float32)
        s = {"name": name, "joints": list(joints), "inverseBindMatrices": self.accessor(ibm, kind="MAT4")}
        if skeleton is not None:
            s["skeleton"] = skeleton
        skins = self._list("skins")
        skins.append(s)
        return len(skins) - 1

    def animation(self, name: str, channels: list[tuple[int, str, np.ndarray, np.ndarray]]) -> int:
        """channels : (nœud, "rotation"|"translation"|"scale", temps (T,), valeurs (T, 3|4))."""
        anim = {"name": name, "channels": [], "samplers": []}
        time_cache: dict[bytes, int] = {}
        for node, path, times, values in channels:
            t = np.asarray(times, dtype=np.float32)
            key = t.tobytes()
            if key not in time_cache:
                time_cache[key] = self.accessor(t, minmax=True, kind="SCALAR")
            out = self.accessor(np.asarray(values, dtype=np.float32))
            anim["samplers"].append({"input": time_cache[key], "output": out, "interpolation": "LINEAR"})
            anim["channels"].append({"sampler": len(anim["samplers"]) - 1, "target": {"node": node, "path": path}})
        anims = self._list("animations")
        anims.append(anim)
        return len(anims) - 1

    # ── sortie ─────────────────────────────────────────────────────

    def to_bytes(self) -> bytes:
        body = _pad(self.bin, b"\x00")
        self.doc["buffers"] = [{"byteLength": len(body)}]
        doc = {k: v for k, v in self.doc.items() if v != [] or k in ("scenes",)}
        js = _pad(json.dumps(doc, separators=(",", ":")).encode("utf-8"), b" ")
        out = io.BytesIO()
        out.write(struct.pack("<III", MAGIC, VERSION, 12 + 8 + len(js) + 8 + len(body)))
        out.write(struct.pack("<II", len(js), CHUNK_JSON))
        out.write(js)
        out.write(struct.pack("<II", len(body), CHUNK_BIN))
        out.write(body)
        return out.getvalue()

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.to_bytes())
        return path


# ── rotations ──────────────────────────────────────────────────────

def quat_from_matrix(m: np.ndarray) -> np.ndarray:
    """(…, 3, 3) → (…, 4) [x, y, z, w], branche stable selon la trace."""
    m = np.asarray(m, dtype=np.float64)
    shape = m.shape[:-2]
    m = m.reshape(-1, 3, 3)
    q = np.empty((m.shape[0], 4))
    for i, r in enumerate(m):
        t = r[0, 0] + r[1, 1] + r[2, 2]
        if t > 0:
            s = np.sqrt(t + 1.0) * 2
            q[i] = [(r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, 0.25 * s]
        elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
            s = np.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
            q[i] = [0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s]
        elif r[1, 1] > r[2, 2]:
            s = np.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
            q[i] = [(r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s]
        else:
            s = np.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
            q[i] = [(r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s, (r[1, 0] - r[0, 1]) / s]
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    return q.reshape(*shape, 4)


def matrix_from_quat(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=np.float64)
    x, y, z, w = np.moveaxis(q / np.linalg.norm(q, axis=-1, keepdims=True), -1, 0)
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
        np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
        np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1),
    ], -2)


def quat_from_axis_angle(axis, angle: float) -> np.ndarray:
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / np.linalg.norm(axis)
    s = np.sin(angle / 2)
    return np.array([axis[0] * s, axis[1] * s, axis[2] * s, np.cos(angle / 2)])


def quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ax, ay, az, aw = np.moveaxis(np.asarray(a, dtype=np.float64), -1, 0)
    bx, by, bz, bw = np.moveaxis(np.asarray(b, dtype=np.float64), -1, 0)
    return np.stack([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz], -1)


def quat_inv(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=np.float64)
    return q * np.array([-1, -1, -1, 1]) / np.sum(q * q, axis=-1, keepdims=True)


def slerp(a: np.ndarray, b: np.ndarray, t) -> np.ndarray:
    """Interpolation sphérique, élément par élément ; t scalaire ou (…,)."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    t = np.asarray(t, dtype=np.float64)[..., None] if np.ndim(t) else float(t)
    dot = np.sum(a * b, axis=-1, keepdims=True)
    b = np.where(dot < 0, -b, b)          # le plus court chemin
    dot = np.abs(dot)
    close = dot > 0.9995
    theta = np.arccos(np.clip(dot, -1, 1))
    sin = np.sin(theta)
    sin = np.where(close, 1.0, sin)
    wa = np.where(close, 1 - t, np.sin((1 - t) * theta) / sin)
    wb = np.where(close, t, np.sin(t * theta) / sin)
    out = wa * a + wb * b
    return out / np.linalg.norm(out, axis=-1, keepdims=True)


def trs_matrix(t=(0, 0, 0), q=(0, 0, 0, 1), s=(1, 1, 1)) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = matrix_from_quat(np.asarray(q)) * np.asarray(s)
    m[:3, 3] = t
    return m
