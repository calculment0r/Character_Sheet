"""Relever la pose de plusieurs images par DWPose, pour les mesurer.

À lancer sur DGX2 avec le python de ComfyUI (onnxruntime, cv2) :
  ~/comfyui-env/bin/python tools/remote/pose_measure.py <sortie.json> <image> [<image>…]

Écrit, par image : sa taille, le nombre de personnes vues, et les points
de la personne la plus sûre — corps OpenPose 18 en pixels avec leur
score, mains et visage (nombre de points vus). La note se calcule dans
la chaîne (`factory/apose_pick.py`) : ce script ne fait que relever.
"""
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path.home() / "ComfyUI/custom_nodes/comfyui_controlnet_aux/src"))
from custom_controlnet_aux.dwpose import DwposeDetector  # noqa: E402


def points(kps):
    if kps is None:
        return None
    return [None if k is None else [float(k.x), float(k.y), float(getattr(k, "score", 1.0))] for k in kps]


def main():
    dest, images = Path(sys.argv[1]), [Path(a) for a in sys.argv[2:]]
    det = DwposeDetector.from_pretrained("yzd-v/DWPose", "yzd-v/DWPose", det_filename="yolox_l.onnx",
                                         pose_filename="dw-ll_ucoco_384.onnx", torchscript_device="cuda")
    out = {}
    for path in images:
        img = cv2.imread(str(path))
        if img is None:
            out[str(path)] = {"error": "image illisible"}
            continue
        h, w = img.shape[:2]
        poses = det.detect_poses(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        entry = {"width": w, "height": h, "people": len(poses)}
        if poses:
            pose = max(poses, key=lambda p: p.body.total_score)
            entry.update(body=points(pose.body.keypoints), score=float(pose.body.total_score),
                         left_hand=points(pose.left_hand), right_hand=points(pose.right_hand),
                         face=points(pose.face))
        out[str(path)] = entry
    dest.write_text(json.dumps(out))
    print("mesures :", dest)


if __name__ == "__main__":
    main()
