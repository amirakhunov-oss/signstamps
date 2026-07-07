import argparse
import json
import random
import shutil
import zipfile
from pathlib import Path


CLASSES = {
    "signature": 0,
    "stamp": 1,
}


def yolo_box(points, width, height):
    xs = points[0::2]
    ys = points[1::2]
    x1 = max(0.0, min(xs))
    y1 = max(0.0, min(ys))
    x2 = min(float(width), max(xs))
    y2 = min(float(height), max(ys))

    if x2 <= x1 or y2 <= y1:
        return None

    cx = ((x1 + x2) / 2.0) / width
    cy = ((y1 + y2) / 2.0) / height
    bw = (x2 - x1) / width
    bh = (y2 - y1) / height
    return cx, cy, bw, bh


def find_image(extracted_root, image_path):
    name = Path(image_path).name
    matches = list(extracted_root.rglob(name))
    if not matches:
        raise FileNotFoundError(f"Image from CVAT dump not found in ZIP contents: {image_path}")
    if len(matches) > 1:
        matches.sort(key=lambda p: len(str(p)))
    return matches[0]


def write_split(out_root, split, rows, extracted_root, labels_by_frame):
    images_dir = out_root / "images" / split
    labels_dir = out_root / "labels" / split
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    list_lines = []
    for image in rows:
        frame = image["frame"]
        src = find_image(extracted_root, image["path"])
        dst_img = images_dir / src.name
        shutil.copy2(src, dst_img)

        label_path = labels_dir / f"{src.stem}.txt"
        label_path.write_text("\n".join(labels_by_frame.get(frame, [])) + ("\n" if labels_by_frame.get(frame) else ""), encoding="utf-8")
        list_lines.append(f"images/{split}/{src.name}")

    (out_root / f"{split}.txt").write_text("\n".join(list_lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", required=True, type=Path, help="CVAT YOLO export ZIP with images")
    parser.add_argument("--dump", required=True, type=Path, help="JSON dump from CVAT DB with images and shapes")
    parser.add_argument("--out", required=True, type=Path, help="Output YOLO dataset directory")
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)

    extracted = args.out / "_source_zip"
    extracted.mkdir()
    with zipfile.ZipFile(args.zip) as zf:
        zf.extractall(extracted)

    dump = json.loads(args.dump.read_text(encoding="utf-8"))
    frame_to_image = {image["frame"]: image for image in dump["images"]}
    labels_by_frame = {frame: [] for frame in frame_to_image}
    counts = {"signature": 0, "stamp": 0, "ignored": 0, "invalid": 0}

    for shape in dump["shapes"]:
        label = shape["label"]
        if label not in CLASSES or shape.get("outside"):
            counts["ignored"] += 1
            continue

        image = frame_to_image[shape["frame"]]
        points = [float(v) for v in shape["points"]]
        box = yolo_box(points, image["width"], image["height"])
        if box is None:
            counts["invalid"] += 1
            continue

        cls = CLASSES[label]
        labels_by_frame[shape["frame"]].append(
            f"{cls} " + " ".join(f"{v:.6f}" for v in box)
        )
        counts[label] += 1

    images = list(dump["images"])
    random.Random(args.seed).shuffle(images)
    val_count = max(1, round(len(images) * args.val_ratio))
    val_frames = {image["frame"] for image in images[:val_count]}
    train_rows = [image for image in dump["images"] if image["frame"] not in val_frames]
    val_rows = [image for image in dump["images"] if image["frame"] in val_frames]

    write_split(args.out, "train", train_rows, extracted, labels_by_frame)
    write_split(args.out, "val", val_rows, extracted, labels_by_frame)
    shutil.rmtree(extracted)

    data_yaml = "\n".join(
        [
            f"path: {args.out.as_posix()}",
            "train: train.txt",
            "val: val.txt",
            "names:",
            "  0: signature",
            "  1: stamp",
            "",
        ]
    )
    (args.out / "data.yaml").write_text(data_yaml, encoding="utf-8")

    print(json.dumps({
        "images": len(dump["images"]),
        "train_images": len(train_rows),
        "val_images": len(val_rows),
        **counts,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
