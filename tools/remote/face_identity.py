"""Le contrôle d'identité des cases de la planche : même personne ou non.

À lancer sur DGX2 avec le python de ComfyUI (facenet-pytorch y est, et
ses poids VGGFace2 sont en cache) :
  ~/comfyui-env/bin/python tools/remote/face_identity.py <référence.png> <image.png>…

MTCNN trouve le visage (boîte, yeux), FaceNet (InceptionResnetV1,
VGGFace2) en tire une empreinte ; le score est le cosinus entre
l'empreinte de chaque image et celle de la référence (le visage
verrouillé). Rend du JSON sur la sortie standard : pour chaque image, le
score (null sans visage trouvé), la boîte et les deux yeux, en pixels, et
le nombre de visages nets (`faces`) —
les yeux servent à aligner les expressions sur la planche.

Sur le CPU : quelques images, et le GPU est aux autres.
"""
import json
import sys

import torch
from facenet_pytorch import MTCNN, InceptionResnetV1
from PIL import Image


def main():
    paths = sys.argv[1:]
    torch.set_grad_enabled(False)
    mtcnn = MTCNN(image_size=160, margin=16, select_largest=True, post_process=True, device="cpu")
    net = InceptionResnetV1(pretrained="vggface2").eval()

    def read(path):
        img = Image.open(path).convert("RGB")
        boxes, probs, marks = mtcnn.detect(img, landmarks=True)
        if boxes is None:
            return None, {"file": path, "score": None, "box": None, "eyes": None}
        crop = mtcnn.extract(img, boxes[:1], None)
        emb = torch.nn.functional.normalize(net(crop.unsqueeze(0) if crop.dim() == 3 else crop), dim=1)[0]
        # Les yeux dans l'ordre de l'image : gauche puis droite.
        eyes = sorted(marks[0][:2].tolist())
        # Les visages nets de l'image : une édition qui dédouble la personne
        # (Krea 2, report du visage du 28/09) en montre deux.
        faces = int(sum(1 for q in probs if q is not None and q >= 0.9))
        return emb, {"file": path, "box": [round(float(v), 1) for v in boxes[0]], "prob": round(float(probs[0]), 4),
                     "eyes": [[round(float(x), 1), round(float(y), 1)] for x, y in eyes], "faces": faces}

    ref, ref_info = read(paths[0])
    if ref is None:
        sys.exit(f"aucun visage dans la référence {paths[0]}")
    out = {"reference": ref_info, "images": []}
    for path in paths[1:]:
        emb, info = read(path)
        info["score"] = None if emb is None else round(float(ref @ emb), 4)
        out["images"].append(info)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
