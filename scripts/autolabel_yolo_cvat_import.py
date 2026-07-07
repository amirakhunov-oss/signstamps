from __future__ import annotations

import argparse
import csv
import shutil
import zipfile
from pathlib import Path

from PIL import Image
from ultralytics import YOLO


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def safe_member_name(name: str) -> str:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe ZIP member path: {name}")
    return path.name


def extract_images(zip_path: Path, images_dir: Path) -> list[Path]:
    images_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    seen: set[str] = set()
    seen_stems: set[str] = set()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = safe_member_name(info.filename)
            if Path(name).suffix.lower() not in IMAGE_EXTS:
                continue
            out_name = name
            if out_name in seen or Path(out_name).stem in seen_stems:
                stem = Path(name).stem
                suffix = Path(name).suffix
                i = 2
                while f"{stem}_{i}{suffix}" in seen or f"{stem}_{i}" in seen_stems:
                    i += 1
                out_name = f"{stem}_{i}{suffix}"
            seen.add(out_name)
            seen_stems.add(Path(out_name).stem)
            out_path = images_dir / out_name
            with zf.open(info) as src, out_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(out_path)
    return sorted(extracted, key=lambda p: p.name.lower())


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return img.size


def write_dataset_zip(dataset_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(dataset_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(dataset_dir).as_posix())


def process_zip(
    model: YOLO,
    zip_path: Path,
    out_root: Path,
    work_root: Path,
    imgsz: int,
    conf: float,
) -> dict[str, int | str]:
    part_name = zip_path.stem
    dataset_dir = out_root / f"{part_name}_yolo_sig_stamp_cvat"
    images_dir = dataset_dir / "images" / "train"
    labels_dir = dataset_dir / "labels" / "train"
    work_dir = work_root / part_name

    clean_dir(dataset_dir)
    clean_dir(work_dir)
    labels_dir.mkdir(parents=True, exist_ok=True)

    images = extract_images(zip_path, images_dir)
    results = model.predict(
        source=[str(p) for p in images],
        imgsz=imgsz,
        conf=conf,
        verbose=False,
        stream=False,
    )

    rows: list[dict[str, str | int | float]] = []
    total_boxes = 0
    total_signature = 0
    total_stamp = 0

    for image_path, result in zip(images, results):
        width, height = image_size(image_path)
        label_path = labels_dir / f"{image_path.stem}.txt"
        lines: list[str] = []
        sig_count = 0
        stamp_count = 0
        max_conf = 0.0

        if result.boxes is not None:
            classes = result.boxes.cls.detach().cpu().numpy().astype(int)
            confs = result.boxes.conf.detach().cpu().numpy()
            xywhn = result.boxes.xywhn.detach().cpu().numpy()
            for cls_id, score, box in zip(classes, confs, xywhn):
                if cls_id not in (0, 1):
                    continue
                x, y, w, h = box.tolist()
                lines.append(f"{cls_id} {x:.6f} {y:.6f} {w:.6f} {h:.6f}")
                total_boxes += 1
                max_conf = max(max_conf, float(score))
                if cls_id == 0:
                    sig_count += 1
                    total_signature += 1
                else:
                    stamp_count += 1
                    total_stamp += 1

        label_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        rows.append(
            {
                "image": image_path.name,
                "width": width,
                "height": height,
                "signature_count": sig_count,
                "stamp_count": stamp_count,
                "box_count": sig_count + stamp_count,
                "max_conf": f"{max_conf:.4f}",
            }
        )

    (dataset_dir / "data.yaml").write_text(
        "path: .\ntrain: train.txt\nnames:\n  0: signature\n  1: stamp\n",
        encoding="utf-8",
    )
    (dataset_dir / "train.txt").write_text(
        "".join(f"images/train/{p.name}\n" for p in images),
        encoding="utf-8",
    )
    with (dataset_dir / "summary.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "image",
                "width",
                "height",
                "signature_count",
                "stamp_count",
                "box_count",
                "max_conf",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    zip_out = out_root / f"{part_name}_yolo_sig_stamp_cvat.zip"
    write_dataset_zip(dataset_dir, zip_out)
    shutil.rmtree(work_dir, ignore_errors=True)

    return {
        "source_zip": zip_path.name,
        "images": len(images),
        "boxes": total_boxes,
        "signatures": total_signature,
        "stamps": total_stamp,
        "output_zip": zip_out.name,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--imgsz", default=1280, type=int)
    parser.add_argument("--conf", default=0.35, type=float)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    work_root = args.output_dir / "_work"
    clean_dir(work_root)

    model = YOLO(str(args.model))
    summaries = []
    for zip_path in sorted(args.input_dir.glob("images_part_*.zip")):
        summaries.append(
            process_zip(
                model=model,
                zip_path=zip_path,
                out_root=args.output_dir,
                work_root=work_root,
                imgsz=args.imgsz,
                conf=args.conf,
            )
        )

    with (args.output_dir / "all_parts_summary.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["source_zip", "images", "boxes", "signatures", "stamps", "output_zip"],
        )
        writer.writeheader()
        writer.writerows(summaries)

    shutil.rmtree(work_root, ignore_errors=True)
    for row in summaries:
        print(
            f"{row['source_zip']}: images={row['images']} boxes={row['boxes']} "
            f"signature={row['signatures']} stamp={row['stamps']} -> {row['output_zip']}"
        )


if __name__ == "__main__":
    main()
