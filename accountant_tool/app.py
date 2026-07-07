from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import cv2
import fitz
import numpy as np
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageDraw

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
STORAGE_DIR = BASE_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
PAGE_DIR = STORAGE_DIR / "pages"
CROP_DIR = STORAGE_DIR / "crops"
REFERENCE_DIR = STORAGE_DIR / "references"
REFERENCE_SOURCE_DIR = STORAGE_DIR / "reference_sources"
ACTIVE_LEARNING_DIR = BASE_DIR / "active_learning"
DB_PATH = BASE_DIR / "signstamp.sqlite3"
DEFAULT_MODEL = PROJECT_DIR / "runs_download" / "signstamp_first_pachka_sig_stamp_yolo11s_1280_run" / "runs" / "detect" / "signstamp_first_pachka_sig_stamp_yolo11s_1280" / "weights" / "best.pt"
MODEL_PATH = Path(os.getenv("SIGNSTAMP_YOLO_MODEL", str(DEFAULT_MODEL)))

for _directory in [STORAGE_DIR, UPLOAD_DIR, PAGE_DIR, CROP_DIR, REFERENCE_DIR, REFERENCE_SOURCE_DIR, ACTIVE_LEARNING_DIR]:
    _directory.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="SignStamp Accountant Tool")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/files", StaticFiles(directory=str(STORAGE_DIR)), name="files")

_yolo_model = None
MATCH_THRESHOLDS = {
    "signature": 0.46,
    "stamp": 0.50,
}


@contextmanager
def db() -> Any:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    for directory in [UPLOAD_DIR, PAGE_DIR, CROP_DIR, REFERENCE_DIR, REFERENCE_SOURCE_DIR, ACTIVE_LEARNING_DIR]:
        directory.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            create table if not exists documents (
              id text primary key,
              original_filename text not null,
              upload_date text not null,
              status text not null,
              status_reason text not null default '',
              manual_approved integer not null default 0,
              expected_json text not null default '{}',
              created_at text not null
            );
            create table if not exists pages (
              id text primary key,
              document_id text not null references documents(id) on delete cascade,
              page_number integer not null,
              image_path text not null,
              width integer not null,
              height integer not null,
              summary_json text not null default '{}'
            );
            create table if not exists detections (
              id text primary key,
              document_id text not null references documents(id) on delete cascade,
              page_id text not null references pages(id) on delete cascade,
              kind text not null,
              x1 real not null,
              y1 real not null,
              x2 real not null,
              y2 real not null,
              confidence real not null,
              source text not null,
              crop_path text not null default '',
              matched_reference_id text,
              match_score real,
              status text not null default 'unmatched'
            );
            create table if not exists reference_items (
              id text primary key,
              kind text not null,
              organization text not null,
              person_name text,
              image_path text not null,
              created_at text not null
            );
            create table if not exists reference_sources (
              id text primary key,
              original_filename text not null,
              page_number integer not null,
              image_path text not null,
              width integer not null,
              height integer not null,
              created_at text not null
            );
            create table if not exists corrections (
              id text primary key,
              document_id text not null,
              page_id text not null,
              detection_id text,
              action text not null,
              payload_json text not null,
              created_at text not null
            );
            """
        )


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def get_model():
    global _yolo_model
    if _yolo_model is None:
        if not MODEL_PATH.exists():
            raise RuntimeError(f"YOLO model not found: {MODEL_PATH}")
        from ultralytics import YOLO

        _yolo_model = YOLO(str(MODEL_PATH))
    return _yolo_model


def normalize_expected(raw: str | None) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {"organizations": [], "signers": []}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"expected_json is invalid JSON: {exc}") from exc
    return {
        "organizations": [str(x).strip() for x in data.get("organizations", []) if str(x).strip()],
        "signers": [str(x).strip() for x in data.get("signers", []) if str(x).strip()],
    }


def reference_options() -> dict[str, list[str]]:
    with db() as conn:
        organizations = [
            row["organization"]
            for row in conn.execute(
                "select distinct organization from reference_items where trim(organization) != '' order by organization"
            ).fetchall()
        ]
        signers = [
            row["person_name"]
            for row in conn.execute(
                "select distinct person_name from reference_items where person_name is not null and trim(person_name) != '' order by person_name"
            ).fetchall()
        ]
    return {"organizations": organizations, "signers": signers}


def grouped_references(refs: list[sqlite3.Row]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for ref in refs:
        if ref["kind"] == "stamp":
            key = ("stamp", ref["organization"] or "", "")
        else:
            key = ("signature", ref["organization"] or "", ref["person_name"] or "")
        if key not in groups:
            query = {"kind": ref["kind"], "organization": ref["organization"] or ""}
            if ref["kind"] == "signature":
                query["person_name"] = ref["person_name"] or ""
            groups[key] = {
                "kind": ref["kind"],
                "organization": ref["organization"],
                "person_name": ref["person_name"],
                "image_path": ref["image_path"],
                "created_at": ref["created_at"],
                "count": 0,
                "url": f"/references/group?{urlencode(query)}",
            }
        groups[key]["count"] += 1
    return list(groups.values())


def reference_prefill(kind: str = "", organization: str = "", person_name: str = "") -> dict[str, str]:
    kind = kind if kind in {"signature", "stamp"} else ""
    organization = organization.strip()
    person_name = person_name.strip() if kind == "signature" else ""
    query: dict[str, str] = {}
    if kind:
        query["kind"] = kind
    if organization:
        query["organization"] = organization
    if person_name:
        query["person_name"] = person_name
    return {
        "kind": kind,
        "organization": organization,
        "person_name": person_name,
        "query_string": urlencode(query),
    }


def pdf_to_pages(pdf_path: Path, output_dir: Path, zoom: float = 2.0) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    page_paths: list[Path] = []
    matrix = fitz.Matrix(zoom, zoom)
    for index, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        out = output_dir / f"page_{index:03d}.jpg"
        pix.save(out)
        page_paths.append(out)
    return page_paths


def file_to_reference_pages(source_path: Path, output_dir: Path) -> list[Path]:
    if source_path.suffix.lower() == ".pdf":
        return pdf_to_pages(source_path, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "page_001.jpg"
    Image.open(source_path).convert("RGB").save(out)
    return [out]


def normalized_bbox(x1: float, y1: float, x2: float, y2: float, width: int | float, height: int | float) -> tuple[float, float, float, float]:
    left = max(0.0, min(float(x1), float(x2)))
    top = max(0.0, min(float(y1), float(y2)))
    right = min(float(width), max(float(x1), float(x2)))
    bottom = min(float(height), max(float(y1), float(y2)))
    if right <= left or bottom <= top:
        raise HTTPException(status_code=400, detail="Selected rectangle is empty")
    return left, top, right, bottom


def polygon_from_json(polygon_json: str, width: int | float, height: int | float) -> list[tuple[float, float]]:
    if not polygon_json.strip():
        return []
    try:
        raw_points = json.loads(polygon_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid polygon") from exc
    if not isinstance(raw_points, list):
        raise HTTPException(status_code=400, detail="Invalid polygon")
    points: list[tuple[float, float]] = []
    for item in raw_points:
        if not isinstance(item, dict) or "x" not in item or "y" not in item:
            raise HTTPException(status_code=400, detail="Invalid polygon point")
        x = max(0.0, min(float(width), float(item["x"])))
        y = max(0.0, min(float(height), float(item["y"])))
        points.append((x, y))
    if points and len(points) < 3:
        raise HTTPException(status_code=400, detail="Polygon needs at least three points")
    return points


def bbox_from_polygon(points: list[tuple[float, float]], width: int | float, height: int | float) -> tuple[float, float, float, float]:
    return normalized_bbox(
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
        width,
        height,
    )


def crop_image(image_path: Path, bbox: tuple[float, float, float, float], out_path: Path) -> None:
    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    x1, y1, x2, y2 = bbox
    pad_x = max(8, int((x2 - x1) * 0.08))
    pad_y = max(8, int((y2 - y1) * 0.08))
    box = (
        max(0, int(x1) - pad_x),
        max(0, int(y1) - pad_y),
        min(width, int(x2) + pad_x),
        min(height, int(y2) + pad_y),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.crop(box).save(out_path)


def crop_polygon_image(image_path: Path, points: list[tuple[float, float]], out_path: Path) -> None:
    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    x1, y1, x2, y2 = bbox_from_polygon(points, width, height)
    pad_x = max(8, int((x2 - x1) * 0.08))
    pad_y = max(8, int((y2 - y1) * 0.08))
    box = (
        max(0, int(x1) - pad_x),
        max(0, int(y1) - pad_y),
        min(width, int(x2) + pad_x),
        min(height, int(y2) + pad_y),
    )
    shifted_points = [(x - box[0], y - box[1]) for x, y in points]
    crop = image.crop(box)
    mask = Image.new("L", crop.size, 0)
    ImageDraw.Draw(mask).polygon(shifted_points, fill=255)
    canvas = Image.new("RGB", crop.size, "white")
    canvas.paste(crop, (0, 0), mask)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)


def fit_to_canvas(image: np.ndarray, size: int = 256, interpolation: int = cv2.INTER_AREA) -> np.ndarray:
    height, width = image.shape[:2]
    scale = min(size / max(width, 1), size / max(height, 1))
    resized = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=interpolation)
    canvas = np.full((size, size), 255, dtype=resized.dtype)
    y = (size - resized.shape[0]) // 2
    x = (size - resized.shape[1]) // 2
    canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    return canvas


def normalize_mark_image(path: Path, size: int = 256) -> tuple[np.ndarray, np.ndarray] | None:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _, dark_mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    color_mask = np.where((saturation > 35) & (value < 248), 255, 0).astype(np.uint8)
    mask = cv2.bitwise_or(dark_mask, color_mask)
    kernel = np.ones((2, 2), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    points = cv2.findNonZero(mask)
    if points is not None and cv2.countNonZero(mask) > 25:
        x, y, w, h = cv2.boundingRect(points)
        pad = max(4, int(max(w, h) * 0.08))
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(gray.shape[1], x + w + pad)
        y2 = min(gray.shape[0], y + h + pad)
        gray = gray[y1:y2, x1:x2]
        mask = mask[y1:y2, x1:x2]
    gray = cv2.equalizeHist(fit_to_canvas(gray, size=size))
    mask = fit_to_canvas(mask, size=size, interpolation=cv2.INTER_NEAREST)
    _, mask = cv2.threshold(mask, 30, 255, cv2.THRESH_BINARY)
    return gray, mask


def dct_hash(image: np.ndarray, hash_size: int = 8) -> np.ndarray:
    resized = cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    dct = cv2.dct(resized)
    low = dct[:hash_size, :hash_size]
    median = np.median(low[1:, 1:])
    return low > median


def diff_hash(image: np.ndarray, hash_size: int = 8) -> np.ndarray:
    resized = cv2.resize(image, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
    return resized[:, 1:] > resized[:, :-1]


def hash_similarity(a_hash: np.ndarray, b_hash: np.ndarray) -> float:
    return float(1.0 - np.count_nonzero(a_hash != b_hash) / a_hash.size)


def mask_iou(a_mask: np.ndarray, b_mask: np.ndarray) -> float:
    a = a_mask > 0
    b = b_mask > 0
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 0.0
    return float(np.logical_and(a, b).sum() / union)


def mask_dice(a_mask: np.ndarray, b_mask: np.ndarray) -> float:
    a = a_mask > 0
    b = b_mask > 0
    total = a.sum() + b.sum()
    if total == 0:
        return 0.0
    return float(2 * np.logical_and(a, b).sum() / total)


def chamfer_similarity(a_mask: np.ndarray, b_mask: np.ndarray) -> float:
    a = a_mask > 0
    b = b_mask > 0
    if a.sum() == 0 or b.sum() == 0:
        return 0.0
    dist_to_b = cv2.distanceTransform((~b).astype(np.uint8), cv2.DIST_L2, 3)
    dist_to_a = cv2.distanceTransform((~a).astype(np.uint8), cv2.DIST_L2, 3)
    mean_distance = float((dist_to_b[a].mean() + dist_to_a[b].mean()) / 2.0)
    diagonal = float(np.hypot(a_mask.shape[1], a_mask.shape[0]))
    return float(np.exp(-mean_distance / max(1.0, diagonal * 0.025)))


def rotate_normalized(image: np.ndarray, mask: np.ndarray, angle: float) -> tuple[np.ndarray, np.ndarray]:
    if angle == 0:
        return image, mask
    height, width = image.shape[:2]
    center = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated_image = cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_LINEAR, borderValue=255)
    rotated_mask = cv2.warpAffine(mask, matrix, (width, height), flags=cv2.INTER_NEAREST, borderValue=0)
    _, rotated_mask = cv2.threshold(rotated_mask, 30, 255, cv2.THRESH_BINARY)
    return rotated_image, rotated_mask


def orb_similarity(a: np.ndarray, a_mask: np.ndarray, b: np.ndarray, b_mask: np.ndarray) -> float:
    orb = cv2.ORB_create(nfeatures=500)
    kp1, des1 = orb.detectAndCompute(a, a_mask)
    kp2, des2 = orb.detectAndCompute(b, b_mask)
    if des1 is None or des2 is None or not kp1 or not kp2:
        return 0.0
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = matcher.match(des1, des2)
    good = [m for m in matches if m.distance < 78]
    return min(1.0, len(good) / max(10, min(len(kp1), len(kp2))))


def image_similarity(a_path: Path, b_path: Path, kind: str = "signature") -> float:
    a_norm = normalize_mark_image(a_path)
    b_norm = normalize_mark_image(b_path)
    if a_norm is None or b_norm is None:
        return 0.0
    a, a_mask = a_norm
    b, b_mask = b_norm
    angles = [-12, -8, -4, 0, 4, 8, 12] if kind == "signature" else [-15, -10, -5, 0, 5, 10, 15]
    best_score = 0.0
    for angle in angles:
        b_variant, b_mask_variant = rotate_normalized(b, b_mask, angle)
        corr = max(0.0, float(cv2.matchTemplate(a, b_variant, cv2.TM_CCOEFF_NORMED)[0][0]))
        phash = hash_similarity(dct_hash(a), dct_hash(b_variant))
        dhash = hash_similarity(diff_hash(a), diff_hash(b_variant))
        dice = mask_dice(a_mask, b_mask_variant)
        chamfer = chamfer_similarity(a_mask, b_mask_variant)
        orb_score = orb_similarity(a, a_mask, b_variant, b_mask_variant)
        if kind == "stamp":
            score = 0.27 * chamfer + 0.22 * dice + 0.18 * corr + 0.15 * phash + 0.08 * dhash + 0.10 * orb_score
        else:
            score = 0.30 * chamfer + 0.22 * dice + 0.18 * corr + 0.15 * phash + 0.07 * dhash + 0.08 * orb_score
        best_score = max(best_score, score)
    return round(float(best_score), 4)


def best_reference_match(kind: str, crop_path: Path, expected: dict[str, Any]) -> tuple[str | None, float | None]:
    orgs = set(expected.get("organizations", []))
    signers = set(expected.get("signers", []))
    query = "select * from reference_items where kind = ?"
    params: list[Any] = [kind]
    with db() as conn:
        refs = [dict(row) for row in conn.execute(query, params).fetchall()]
    if kind == "stamp" and orgs:
        refs = [r for r in refs if r["organization"] in orgs]
    if kind == "signature" and signers:
        refs = [r for r in refs if r["person_name"] in signers]
    best_id: str | None = None
    best_score = 0.0
    for ref in refs:
        score = image_similarity(crop_path, STORAGE_DIR / ref["image_path"], kind=kind)
        if score > best_score:
            best_id = ref["id"]
            best_score = score
    return best_id, round(best_score, 4) if best_id else None


def match_status(kind: str, score: float | None) -> str:
    return "matched" if score is not None and score >= MATCH_THRESHOLDS.get(kind, 0.5) else "unmatched"


def rematch_document(document_id: str) -> None:
    with db() as conn:
        doc = conn.execute("select * from documents where id = ?", (document_id,)).fetchone()
        if not doc:
            return
        expected = json.loads(doc["expected_json"])
        detections = conn.execute(
            "select * from detections where document_id = ? and source not like '%manual%'",
            (document_id,),
        ).fetchall()
    updates = []
    for detection in detections:
        crop_path = STORAGE_DIR / detection["crop_path"]
        if not crop_path.exists():
            continue
        ref_id, score = best_reference_match(detection["kind"], crop_path, expected)
        updates.append((ref_id, score, match_status(detection["kind"], score), detection["id"]))
    with db() as conn:
        conn.executemany(
            "update detections set matched_reference_id = ?, match_score = ?, status = ? where id = ?",
            updates,
        )
        recompute_page_summaries(conn, document_id)
        recompute_document_status(conn, document_id)


def rematch_red_documents() -> int:
    with db() as conn:
        document_ids = [
            row["id"]
            for row in conn.execute(
                "select id from documents where status = 'red' and manual_approved = 0 order by created_at desc"
            ).fetchall()
        ]
    for document_id in document_ids:
        rematch_document(document_id)
    return len(document_ids)


def recompute_page_summaries(conn: sqlite3.Connection, document_id: str) -> None:
    pages = conn.execute("select id from pages where document_id = ?", (document_id,)).fetchall()
    for page in pages:
        rows = conn.execute("select kind, status from detections where page_id = ?", (page["id"],)).fetchall()
        matched = sum(1 for r in rows if r["status"] == "matched")
        unmatched = sum(1 for r in rows if r["status"] != "matched")
        found = len(rows)
        page_status = "red" if found == 0 or unmatched else "green"
        summary = {
            "signatures_found": sum(1 for r in rows if r["kind"] == "signature"),
            "stamps_found": sum(1 for r in rows if r["kind"] == "stamp"),
            "matched": matched,
            "unmatched": unmatched,
            "status": page_status,
        }
        conn.execute("update pages set summary_json = ? where id = ?", (json.dumps(summary, ensure_ascii=False), page["id"]))


def recompute_document_status(conn: sqlite3.Connection, document_id: str) -> None:
    doc = conn.execute("select * from documents where id = ?", (document_id,)).fetchone()
    if not doc:
        return
    if doc["manual_approved"]:
        conn.execute("update documents set status = 'green', status_reason = 'Проверен вручную' where id = ?", (document_id,))
        return
    expected = json.loads(doc["expected_json"])
    rows = conn.execute("select kind, status from detections where document_id = ?", (document_id,)).fetchall()
    found_signatures = sum(1 for r in rows if r["kind"] == "signature")
    found_stamps = sum(1 for r in rows if r["kind"] == "stamp")
    unmatched_objects = sum(1 for r in rows if r["status"] != "matched")
    required_signers = set(expected.get("signers", []))
    required_organizations = set(expected.get("organizations", []))
    if required_signers or required_organizations:
        matched_rows = conn.execute(
            """
            select d.kind, r.organization, r.person_name
            from detections d
            join reference_items r on r.id = d.matched_reference_id
            where d.document_id = ? and d.status = 'matched'
            """,
            (document_id,),
        ).fetchall()
        matched_signer_names = {r["person_name"] for r in matched_rows if r["kind"] == "signature" and r["person_name"]}
        matched_organization_names = {r["organization"] for r in matched_rows if r["kind"] == "stamp" and r["organization"]}
        matched_signatures = len(required_signers & matched_signer_names) if required_signers else 0
        matched_stamps = len(required_organizations & matched_organization_names) if required_organizations else 0
        required_signatures = len(required_signers)
        required_stamps = len(required_organizations)
        expected_ok = matched_signatures >= required_signatures and matched_stamps >= required_stamps
        ok = expected_ok and unmatched_objects == 0
        reason = (
            f"Совпало подписей {matched_signatures}/{required_signatures}, "
            f"печатей {matched_stamps}/{required_stamps}; "
            f"несопоставлено объектов {unmatched_objects}"
        )
    else:
        found_objects = found_signatures + found_stamps
        ok = found_objects > 0 and unmatched_objects == 0
        if ok:
            reason = "Ожидания не заданы; все найденные объекты сопоставлены"
        elif found_objects:
            reason = f"Ожидания не заданы; несопоставлено объектов {unmatched_objects}"
        else:
            reason = "Ожидания не заданы; подписи и печати не найдены"
    conn.execute("update documents set status = ?, status_reason = ? where id = ?", ("green" if ok else "red", reason, document_id))


def process_document(document_id: str, pdf_path: Path) -> None:
    with db() as conn:
        doc = conn.execute("select * from documents where id = ?", (document_id,)).fetchone()
        expected = json.loads(doc["expected_json"])
        conn.execute("update documents set status = 'processing', status_reason = 'PDF конвертируется и размечается YOLO' where id = ?", (document_id,))
    page_paths = pdf_to_pages(pdf_path, PAGE_DIR / document_id)
    model = get_model()
    with db() as conn:
        for page_number, page_path in enumerate(page_paths, start=1):
            image = Image.open(page_path)
            page_id = str(uuid.uuid4())
            relative_page = page_path.relative_to(STORAGE_DIR).as_posix()
            conn.execute(
                "insert into pages(id, document_id, page_number, image_path, width, height) values (?, ?, ?, ?, ?, ?)",
                (page_id, document_id, page_number, relative_page, image.width, image.height),
            )
            results = model.predict(str(page_path), imgsz=1280, conf=0.25, verbose=False)
            for result in results:
                names = result.names
                for box in result.boxes:
                    cls = int(box.cls[0])
                    kind = names.get(cls, str(cls))
                    if kind not in {"signature", "stamp"}:
                        continue
                    x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
                    detection_id = str(uuid.uuid4())
                    crop_path = CROP_DIR / document_id / f"{page_number:03d}_{detection_id}.jpg"
                    crop_image(page_path, (x1, y1, x2, y2), crop_path)
                    ref_id, score = best_reference_match(kind, crop_path, expected)
                    status = match_status(kind, score)
                    conn.execute(
                        """
                        insert into detections(id, document_id, page_id, kind, x1, y1, x2, y2, confidence, source, crop_path, matched_reference_id, match_score, status)
                        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            detection_id,
                            document_id,
                            page_id,
                            kind,
                            x1,
                            y1,
                            x2,
                            y2,
                            float(box.conf[0]),
                            "yolo",
                            crop_path.relative_to(STORAGE_DIR).as_posix(),
                            ref_id,
                            score,
                            status,
                        ),
                    )
        recompute_page_summaries(conn, document_id)
        recompute_document_status(conn, document_id)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, day: str | None = None) -> HTMLResponse:
    selected_day = day or date.today().isoformat()
    with db() as conn:
        docs = [dict(row) for row in conn.execute("select * from documents where upload_date = ? order by created_at desc", (selected_day,)).fetchall()]
        for doc in docs:
            pages = conn.execute("select page_number, summary_json from pages where document_id = ? order by page_number", (doc["id"],)).fetchall()
            doc["pages"] = []
            for page in pages:
                summary = json.loads(page["summary_json"] or "{}")
                page_status = "green" if doc["manual_approved"] else summary.get("status", "green" if doc["status"] == "green" else "red")
                doc["pages"].append({"number": page["page_number"], "status": page_status})
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "docs": docs, "day": selected_day, "expected_options": reference_options()},
    )


@app.post("/documents")
async def upload_document(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(default=[]),
    file: UploadFile | None = File(default=None),
    expected_json: str = Form(default=""),
    expected_jsons: str = Form(default=""),
) -> RedirectResponse:
    upload_files = [item for item in files if item and item.filename]
    if file and file.filename:
        upload_files.append(file)
    if not upload_files:
        raise HTTPException(status_code=400, detail="Upload at least one PDF")
    try:
        expected_by_index = json.loads(expected_jsons) if expected_jsons.strip() else []
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"expected_jsons is invalid JSON: {exc}") from exc
    document_ids: list[str] = []
    for index, upload_file in enumerate(upload_files):
        if not upload_file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"Only PDF uploads are supported: {upload_file.filename}")
        raw_expected = json.dumps(expected_by_index[index], ensure_ascii=False) if index < len(expected_by_index) else expected_json
        expected = normalize_expected(raw_expected)
        document_id = str(uuid.uuid4())
        document_ids.append(document_id)
        upload_path = UPLOAD_DIR / f"{document_id}.pdf"
        with upload_path.open("wb") as out:
            shutil.copyfileobj(upload_file.file, out)
        now = datetime.now().isoformat(timespec="seconds")
        with db() as conn:
            conn.execute(
                "insert into documents(id, original_filename, upload_date, status, status_reason, expected_json, created_at) values (?, ?, ?, ?, ?, ?, ?)",
                (document_id, upload_file.filename, date.today().isoformat(), "queued", "Ожидает обработки", json.dumps(expected, ensure_ascii=False), now),
            )
        background_tasks.add_task(process_document, document_id, upload_path)
    if len(document_ids) == 1:
        return RedirectResponse(f"/documents/{document_ids[0]}", status_code=303)
    return RedirectResponse(f"/?day={date.today().isoformat()}", status_code=303)


def document_status_payload(conn: sqlite3.Connection, document_id: str) -> dict[str, Any] | None:
    doc = conn.execute("select * from documents where id = ?", (document_id,)).fetchone()
    if not doc:
        return None
    pages = []
    for page in conn.execute("select page_number, summary_json from pages where document_id = ? order by page_number", (document_id,)):
        summary = json.loads(page["summary_json"] or "{}")
        page_status = "green" if doc["manual_approved"] else summary.get("status", "green" if doc["status"] == "green" else "red")
        pages.append(
            {
                "number": page["page_number"],
                "status": page_status,
                "signatures_found": summary.get("signatures_found", 0),
                "stamps_found": summary.get("stamps_found", 0),
                "matched": summary.get("matched", 0),
                "unmatched": summary.get("unmatched", 0),
            }
        )
    return {
        "id": doc["id"],
        "status": doc["status"],
        "status_reason": doc["status_reason"],
        "manual_approved": bool(doc["manual_approved"]),
        "page_count": len(pages),
        "pages": pages,
        "done": doc["status"] not in {"queued", "processing"},
    }


@app.get("/documents/{document_id}/status")
def document_status(document_id: str) -> dict[str, Any]:
    with db() as conn:
        payload = document_status_payload(conn, document_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Document not found")
    return payload


@app.get("/documents/statuses")
def document_statuses(day: str | None = None) -> dict[str, Any]:
    selected_day = day or date.today().isoformat()
    with db() as conn:
        document_ids = [
            row["id"]
            for row in conn.execute("select id from documents where upload_date = ? order by created_at desc", (selected_day,))
        ]
        statuses = [document_status_payload(conn, document_id) for document_id in document_ids]
    return {"day": selected_day, "documents": [status for status in statuses if status]}


@app.get("/documents/{document_id}", response_class=HTMLResponse)
def document_detail(request: Request, document_id: str) -> HTMLResponse:
    with db() as conn:
        doc = row_to_dict(conn.execute("select * from documents where id = ?", (document_id,)).fetchone())
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        pages = [dict(row) for row in conn.execute("select * from pages where document_id = ? order by page_number", (document_id,))]
        detections = [dict(row) for row in conn.execute("select * from detections where document_id = ? order by page_id, kind", (document_id,))]
        refs = [dict(row) for row in conn.execute("select * from reference_items order by kind, organization, person_name")]
    by_page: dict[str, list[dict[str, Any]]] = {}
    for detection in detections:
        by_page.setdefault(detection["page_id"], []).append(detection)
    for page in pages:
        page["summary"] = json.loads(page["summary_json"] or "{}")
        page["summary"].setdefault("status", "green" if doc["status"] == "green" else "red")
        page["detections"] = by_page.get(page["id"], [])
    doc["expected"] = json.loads(doc["expected_json"])
    return templates.TemplateResponse(
        "document.html",
        {"request": request, "doc": doc, "pages": pages, "refs": refs, "expected_options": reference_options()},
    )


@app.post("/documents/{document_id}/expected")
def update_document_expected(document_id: str, expected_json: str = Form(default="")) -> RedirectResponse:
    expected = normalize_expected(expected_json)
    with db() as conn:
        doc = conn.execute("select id from documents where id = ?", (document_id,)).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        conn.execute(
            "update documents set expected_json = ?, manual_approved = 0 where id = ?",
            (json.dumps(expected, ensure_ascii=False), document_id),
        )
    rematch_document(document_id)
    return RedirectResponse(f"/documents/{document_id}", status_code=303)


@app.post("/documents/{document_id}/approve")
def approve_document(document_id: str) -> RedirectResponse:
    with db() as conn:
        conn.execute(
            "update documents set manual_approved = 1, status = 'green', status_reason = 'Проверен вручную' where id = ?",
            (document_id,),
        )
        conn.execute(
            "insert into corrections(id, document_id, page_id, action, payload_json, created_at) values (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), document_id, "", "manual_approve", "{}", datetime.now().isoformat(timespec="seconds")),
        )
    return RedirectResponse(f"/documents/{document_id}", status_code=303)


@app.post("/detections/{detection_id}/assign")
def assign_detection(detection_id: str, reference_id: str = Form(...)) -> RedirectResponse:
    with db() as conn:
        det = conn.execute("select * from detections where id = ?", (detection_id,)).fetchone()
        if not det:
            raise HTTPException(status_code=404, detail="Detection not found")
        conn.execute(
            "update detections set matched_reference_id = ?, match_score = 1.0, status = 'matched', source = source || '+manual' where id = ?",
            (reference_id, detection_id),
        )
        conn.execute(
            "insert into corrections(id, document_id, page_id, detection_id, action, payload_json, created_at) values (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), det["document_id"], det["page_id"], detection_id, "assign_reference", json.dumps({"reference_id": reference_id}), datetime.now().isoformat(timespec="seconds")),
        )
        recompute_page_summaries(conn, det["document_id"])
        recompute_document_status(conn, det["document_id"])
    return RedirectResponse(f"/documents/{det['document_id']}", status_code=303)


@app.post("/pages/{page_id}/manual-detection")
def add_manual_detection(
    page_id: str,
    kind: str = Form(...),
    x1: float = Form(...),
    y1: float = Form(...),
    x2: float = Form(...),
    y2: float = Form(...),
    polygon_json: str = Form(default=""),
    reference_id: str | None = Form(default=None),
) -> RedirectResponse:
    if kind not in {"signature", "stamp"}:
        raise HTTPException(status_code=400, detail="kind must be signature or stamp")
    with db() as conn:
        page = conn.execute("select * from pages where id = ?", (page_id,)).fetchone()
        if not page:
            raise HTTPException(status_code=404, detail="Page not found")
        detection_id = str(uuid.uuid4())
        page_path = STORAGE_DIR / page["image_path"]
        polygon = polygon_from_json(polygon_json, page["width"], page["height"])
        x1, y1, x2, y2 = bbox_from_polygon(polygon, page["width"], page["height"]) if polygon else normalized_bbox(x1, y1, x2, y2, page["width"], page["height"])
        crop_path = CROP_DIR / page["document_id"] / f"{page['page_number']:03d}_{detection_id}_manual.jpg"
        if polygon:
            crop_polygon_image(page_path, polygon, crop_path)
        else:
            crop_image(page_path, (x1, y1, x2, y2), crop_path)
        conn.execute(
            """
            insert into detections(id, document_id, page_id, kind, x1, y1, x2, y2, confidence, source, crop_path, matched_reference_id, match_score, status)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (detection_id, page["document_id"], page_id, kind, x1, y1, x2, y2, 1.0, "manual", crop_path.relative_to(STORAGE_DIR).as_posix(), reference_id or None, 1.0 if reference_id else None, "matched" if reference_id else "unmatched"),
        )
        export_active_learning_sample(conn, page, detection_id, kind, (x1, y1, x2, y2))
        recompute_page_summaries(conn, page["document_id"])
        recompute_document_status(conn, page["document_id"])
    return RedirectResponse(f"/documents/{page['document_id']}", status_code=303)


def export_active_learning_sample(conn: sqlite3.Connection, page: sqlite3.Row, detection_id: str, kind: str, bbox: tuple[float, float, float, float]) -> None:
    image_src = STORAGE_DIR / page["image_path"]
    image_dst_dir = ACTIVE_LEARNING_DIR / "images"
    label_dst_dir = ACTIVE_LEARNING_DIR / "labels"
    image_dst_dir.mkdir(parents=True, exist_ok=True)
    label_dst_dir.mkdir(parents=True, exist_ok=True)
    image_dst = image_dst_dir / f"{page['id']}.jpg"
    if not image_dst.exists():
        shutil.copy2(image_src, image_dst)
    cls = 0 if kind == "signature" else 1
    x1, y1, x2, y2 = bbox
    width = float(page["width"])
    height = float(page["height"])
    line = f"{cls} {((x1 + x2) / 2) / width:.6f} {((y1 + y2) / 2) / height:.6f} {(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f} # {detection_id}\n"
    with (label_dst_dir / f"{page['id']}.txt").open("a", encoding="utf-8") as out:
        out.write(line)
    conn.execute(
        "insert into corrections(id, document_id, page_id, detection_id, action, payload_json, created_at) values (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), page["document_id"], page["id"], detection_id, "manual_bbox_for_training", json.dumps({"kind": kind, "bbox": bbox}), datetime.now().isoformat(timespec="seconds")),
    )


@app.get("/references", response_class=HTMLResponse)
def references_page(
    request: Request,
    kind: str = "",
    organization: str = "",
    person_name: str = "",
) -> HTMLResponse:
    with db() as conn:
        refs = conn.execute("select * from reference_items order by created_at desc").fetchall()
        sources = conn.execute("select * from reference_sources order by created_at desc limit 20").fetchall()
    return templates.TemplateResponse(
        "references.html",
        {
            "request": request,
            "reference_groups": grouped_references(refs),
            "sources": sources,
            "prefill": reference_prefill(kind, organization, person_name),
        },
    )


@app.get("/references/group", response_class=HTMLResponse)
def reference_group_page(
    request: Request,
    kind: str,
    organization: str,
    person_name: str = "",
) -> HTMLResponse:
    if kind not in {"signature", "stamp"}:
        raise HTTPException(status_code=400, detail="kind must be signature or stamp")
    query = "select * from reference_items where kind = ? and organization = ?"
    params: list[Any] = [kind, organization]
    if kind == "signature":
        query += " and coalesce(person_name, '') = ?"
        params.append(person_name)
    query += " order by created_at desc"
    with db() as conn:
        refs = [dict(row) for row in conn.execute(query, params).fetchall()]
    if not refs:
        raise HTTPException(status_code=404, detail="Reference group not found")
    group = {
        "kind": kind,
        "organization": organization,
        "person_name": person_name or None,
        "count": len(refs),
        "add_url": f"/references?{reference_prefill(kind, organization, person_name)['query_string']}",
    }
    return templates.TemplateResponse("reference_group.html", {"request": request, "group": group, "refs": refs})


@app.post("/references/{reference_id}/delete")
def delete_reference(reference_id: str) -> RedirectResponse:
    with db() as conn:
        ref = conn.execute("select * from reference_items where id = ?", (reference_id,)).fetchone()
        if not ref:
            raise HTTPException(status_code=404, detail="Reference not found")
        affected_document_ids = [
            row["document_id"]
            for row in conn.execute(
                "select distinct document_id from detections where matched_reference_id = ?",
                (reference_id,),
            ).fetchall()
        ]
        conn.execute(
            "update detections set matched_reference_id = null, match_score = null, status = 'unmatched' where matched_reference_id = ?",
            (reference_id,),
        )
        conn.execute("delete from reference_items where id = ?", (reference_id,))
        image_path = STORAGE_DIR / ref["image_path"]
        if image_path.exists():
            image_path.unlink()
        for document_id in affected_document_ids:
            recompute_page_summaries(conn, document_id)
            recompute_document_status(conn, document_id)
        query = {"kind": ref["kind"], "organization": ref["organization"]}
        if ref["kind"] == "signature":
            query["person_name"] = ref["person_name"] or ""
        remaining_query = "select count(*) from reference_items where kind = ? and organization = ?"
        remaining_params: list[Any] = [ref["kind"], ref["organization"]]
        if ref["kind"] == "signature":
            remaining_query += " and coalesce(person_name, '') = ?"
            remaining_params.append(ref["person_name"] or "")
        remaining = conn.execute(remaining_query, remaining_params).fetchone()[0]
    rematch_red_documents()
    location = f"/references/group?{urlencode(query)}" if remaining else "/references"
    return RedirectResponse(location, status_code=303)


@app.post("/references")
async def create_reference(
    background_tasks: BackgroundTasks,
    kind: str = Form(...),
    organization: str = Form(...),
    person_name: str = Form(default=""),
    files: list[UploadFile] = File(default=[]),
    file: UploadFile | None = File(default=None),
) -> RedirectResponse:
    if kind not in {"signature", "stamp"}:
        raise HTTPException(status_code=400, detail="kind must be signature or stamp")
    if kind == "stamp":
        person_name = ""
    if kind == "signature" and not person_name.strip():
        raise HTTPException(status_code=400, detail="person_name is required for signatures")
    upload_files = [item for item in files if item and item.filename]
    if file and file.filename:
        upload_files.append(file)
    if not upload_files:
        raise HTTPException(status_code=400, detail="Upload at least one reference image")
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        for upload_file in upload_files:
            ref_id = str(uuid.uuid4())
            suffix = Path(upload_file.filename).suffix.lower() or ".jpg"
            ref_path = REFERENCE_DIR / f"{ref_id}{suffix}"
            with ref_path.open("wb") as out:
                shutil.copyfileobj(upload_file.file, out)
            conn.execute(
                "insert into reference_items(id, kind, organization, person_name, image_path, created_at) values (?, ?, ?, ?, ?, ?)",
                (ref_id, kind, organization.strip(), person_name.strip() or None, ref_path.relative_to(STORAGE_DIR).as_posix(), now),
            )
    background_tasks.add_task(rematch_red_documents)
    return RedirectResponse("/references", status_code=303)


@app.post("/reference-sources")
async def create_reference_source(
    file: UploadFile = File(...),
    kind: str = Form(default=""),
    organization: str = Form(default=""),
    person_name: str = Form(default=""),
) -> RedirectResponse:
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=400, detail="Upload PDF or image")
    source_group_id = str(uuid.uuid4())
    raw_path = REFERENCE_SOURCE_DIR / f"{source_group_id}{suffix}"
    with raw_path.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    page_paths = file_to_reference_pages(raw_path, REFERENCE_SOURCE_DIR / source_group_id)
    now = datetime.now().isoformat(timespec="seconds")
    first_source_id = ""
    with db() as conn:
        for page_number, page_path in enumerate(page_paths, start=1):
            image = Image.open(page_path)
            source_id = str(uuid.uuid4())
            if not first_source_id:
                first_source_id = source_id
            conn.execute(
                "insert into reference_sources(id, original_filename, page_number, image_path, width, height, created_at) values (?, ?, ?, ?, ?, ?, ?)",
                (source_id, file.filename, page_number, page_path.relative_to(STORAGE_DIR).as_posix(), image.width, image.height, now),
            )
    prefill = reference_prefill(kind, organization, person_name)
    suffix = f"?{prefill['query_string']}" if prefill["query_string"] else ""
    return RedirectResponse(f"/reference-sources/{first_source_id}{suffix}", status_code=303)


@app.get("/reference-sources/{source_id}", response_class=HTMLResponse)
def reference_source_detail(
    request: Request,
    source_id: str,
    kind: str = "",
    organization: str = "",
    person_name: str = "",
    error: str = "",
) -> HTMLResponse:
    with db() as conn:
        source = row_to_dict(conn.execute("select * from reference_sources where id = ?", (source_id,)).fetchone())
        if not source:
            raise HTTPException(status_code=404, detail="Reference source not found")
        siblings = conn.execute(
            "select * from reference_sources where original_filename = ? and created_at = ? order by page_number",
            (source["original_filename"], source["created_at"]),
        ).fetchall()
    return templates.TemplateResponse(
        "reference_source.html",
        {
            "request": request,
            "source": source,
            "siblings": siblings,
            "reference_options": reference_options(),
            "prefill": reference_prefill(kind, organization, person_name),
            "error": error,
        },
    )


@app.post("/reference-sources/{source_id}/references")
def create_reference_from_source(
    source_id: str,
    background_tasks: BackgroundTasks,
    kind: str = Form(default=""),
    organization: str = Form(default=""),
    person_name: str = Form(default=""),
    x1: float | None = Form(default=None),
    y1: float | None = Form(default=None),
    x2: float | None = Form(default=None),
    y2: float | None = Form(default=None),
    polygon_json: str = Form(default=""),
) -> RedirectResponse:
    prefill = reference_prefill(kind, organization, person_name)
    error_query = {"error": "Заполните поля и завершите разметку полигона"}
    if prefill["kind"]:
        error_query["kind"] = prefill["kind"]
    if prefill["organization"]:
        error_query["organization"] = prefill["organization"]
    if prefill["person_name"]:
        error_query["person_name"] = prefill["person_name"]
    error_location = f"/reference-sources/{source_id}?{urlencode(error_query)}"
    if kind not in {"signature", "stamp"}:
        return RedirectResponse(error_location, status_code=303)
    if kind == "stamp":
        person_name = ""
    if not organization.strip() or (kind == "signature" and not person_name.strip()):
        return RedirectResponse(error_location, status_code=303)
    if x1 is None or y1 is None or x2 is None or y2 is None:
        return RedirectResponse(error_location, status_code=303)
    with db() as conn:
        source = conn.execute("select * from reference_sources where id = ?", (source_id,)).fetchone()
        if not source:
            raise HTTPException(status_code=404, detail="Reference source not found")
        polygon = polygon_from_json(polygon_json, source["width"], source["height"])
        x1, y1, x2, y2 = bbox_from_polygon(polygon, source["width"], source["height"]) if polygon else normalized_bbox(x1, y1, x2, y2, source["width"], source["height"])
        ref_id = str(uuid.uuid4())
        ref_path = REFERENCE_DIR / f"{ref_id}.jpg"
        if polygon:
            crop_polygon_image(STORAGE_DIR / source["image_path"], polygon, ref_path)
        else:
            crop_image(STORAGE_DIR / source["image_path"], (x1, y1, x2, y2), ref_path)
        conn.execute(
            "insert into reference_items(id, kind, organization, person_name, image_path, created_at) values (?, ?, ?, ?, ?, ?)",
            (ref_id, kind, organization.strip(), person_name.strip() or None, ref_path.relative_to(STORAGE_DIR).as_posix(), datetime.now().isoformat(timespec="seconds")),
        )
    background_tasks.add_task(rematch_red_documents)
    prefill = reference_prefill(kind, organization, person_name)
    suffix = f"?{prefill['query_string']}" if prefill["query_string"] else ""
    return RedirectResponse(f"/reference-sources/{source_id}{suffix}", status_code=303)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "model_path": str(MODEL_PATH), "model_exists": MODEL_PATH.exists()}


@app.post("/admin/rematch-red")
def admin_rematch_red() -> dict[str, Any]:
    return {"rematched_documents": rematch_red_documents()}
