import pdfplumber
import pymupdf                 # PyMuPDF
import PyPDF2
import pytesseract
import io
import math
import re
from pdf2image import convert_from_path
from PIL import Image
from PIL import ImageOps, ImageFilter
import numpy as np
import os
import time
import contextlib
from collections import Counter
from . import ocr_local as _ocr_local
from dotenv import load_dotenv
load_dotenv()

# =====================================================================
# DEBUG / TIMING HELPERS  (set OCR_DEBUG=0 to silence)
# =====================================================================
OCR_DEBUG = os.getenv("OCR_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}

# True  -> do NOT re-OCR embedded images on a page that was already OCR'd.
#          (On a scan the embedded image IS the page, so this repeats the whole OCR.)
# False -> old behaviour.
SKIP_IMAGE_REGION_OCR_ON_OCR_PAGES = os.getenv("SKIP_IMAGE_REGION_OCR", "1").strip().lower() in {"1", "true", "yes", "on"}


# get_text("dict") normally copies/decodes every embedded image too (~1.4 s on a full-page scan).
# We only need text lines here, so ask PyMuPDF to leave images out.
_TEXT_ONLY_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES


def dbg(msg):
    if OCR_DEBUG:
        print(f"[ocr-debug] {msg}", flush=True)


@contextlib.contextmanager
def stage(label, store=None, page=None):
    """Time a block, log it, and accumulate it into `store[label]`."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        if store is not None:
            store[label] = store.get(label, 0.0) + dt
        tag = f"page {page} | " if page is not None else ""
        dbg(f"{tag}{label}: {dt:.2f}s")
from .ocr_local import deskew, rapid_ocr

# ---------- Rotation detection (unchanged) ----------
def detect_page_rotation(pdf_path, page_number, dpi=150, timings=None):
    try:
        with stage(f"osd.render@{dpi}dpi(poppler)", timings, page_number):
            images = convert_from_path(pdf_path, dpi=dpi, first_page=page_number, last_page=page_number)
        if not images:
            return None, 0.0
        with stage("osd.tesseract", timings, page_number):
            osd = pytesseract.image_to_osd(images[0])
        angle_match = re.search(r"Rotate:\s*(\d+)", osd)
        conf_match = re.search(r"Orientation confidence:\s*([\d.]+)", osd)
        angle = int(angle_match.group(1)) if angle_match else 0
        confidence = float(conf_match.group(1)) if conf_match else 0.0
        dbg(f"page {page_number} | OSD result: angle={angle} confidence={confidence:.2f}")
        return angle, confidence
    except Exception as e:
        print(f"OSD failed for page {page_number}: {e}")
        return None, 0.0

# ---------- Transform word coordinates ----------
def _transform_word(word, page_width, page_height, rotation):
    x0, y0, x1, y1 = word[:4]
    if rotation == 90:
        nx0 = page_height - y1
        ny0 = x0
        nx1 = page_height - y0
        ny1 = x1
    elif rotation == 180:
        nx0 = page_width - x1
        ny0 = page_height - y1
        nx1 = page_width - x0
        ny1 = page_height - y0
    elif rotation == 270:
        nx0 = y0
        ny0 = page_width - x1
        nx1 = y1
        ny1 = page_width - x0
    else:
        return word
    return (nx0, ny0, nx1, ny1) + tuple(word[4:])

# ---------- Build readable text from transformed words ----------
def _words_to_text(words, y_tolerance=4):
    if not words:
        return ""
    words = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines = []
    for word in words:
        x0, y0, x1, y1 = word[:4]
        center_y = (y0 + y1) / 2
        best_line = None
        best_diff = float("inf")
        for line in lines:
            diff = abs(center_y - line["center_y"])
            if diff <= y_tolerance and diff < best_diff:
                best_line = line
                best_diff = diff
        if best_line is None:
            lines.append({"center_y": center_y, "words": [word]})
        else:
            best_line["words"].append(word)
            centers = [(w[1] + w[3]) / 2 for w in best_line["words"]]
            best_line["center_y"] = sum(centers) / len(centers)
    lines.sort(key=lambda line: line["center_y"])
    output = []
    for line in lines:
        line["words"].sort(key=lambda w: w[0])
        output.append(" ".join(w[4] for w in line["words"]))
    return "\n".join(output)

# ---------- PyMuPDF extraction ----------
def _try_pymupdf_page(doc, page_number, rotation=0):
    page = doc[page_number - 1]
    native_rotation = page.rotation

    if rotation and native_rotation != 0:
        print(f"Page {page_number}: skipping manual transform — "
              f"native page.rotation={native_rotation} already applied by PyMuPDF "
              f"(OSD suggested {rotation}, ignored to avoid double-transform)")
        rotation = 0

    if native_rotation != 0:
        # Spatial sorting can reorder content on natively rotated pages.
        text = _extract_mixed_page_text(page)
        if text.strip():
            return text

    # No native rotation — either upright, or an OSD-only rotation that
    # still needs manual coordinate transform via _transform_word.
    words = page.get_text("words", sort=False)
    if not words:
        return ""
    if rotation:
        width = page.rect.width
        height = page.rect.height
        words = [_transform_word(w, width, height, rotation) for w in words]
    return _words_to_text(words)

# ---------- pdfplumber extraction ----------
def _try_pdfplumber_page(pdf, page_number):
    page = pdf.pages[page_number - 1]
    return page.extract_text(layout=True) or ""

# ---------- PyPDF2 extraction ----------
def _try_pypdf2_page(reader, page_number):
    page = reader.pages[page_number - 1]
    return page.extract_text() or ""


def pdf_requires_ocr(pdf_path, min_chars=20):
    """Return whether any page lacks enough native text to avoid OCR."""
    with open(pdf_path, "rb") as pdf_file:
        pdf_data = pdf_file.read()

    mupdf_doc = pymupdf.open(stream=pdf_data, filetype="pdf")
    try:
        with pdfplumber.open(io.BytesIO(pdf_data)) as plumber_pdf:
            pypdf2_reader = PyPDF2.PdfReader(io.BytesIO(pdf_data))
            for page_number in range(1, len(mupdf_doc) + 1):
                has_usable_text = False
                for extractor in (
                    lambda: _try_pymupdf_page(mupdf_doc, page_number),
                    lambda: _try_pdfplumber_page(plumber_pdf, page_number),
                    lambda: _try_pypdf2_page(pypdf2_reader, page_number),
                ):
                    try:
                        page_text = extractor()
                    except Exception as exc:
                        dbg(f"page {page_number} | native text inspection failed: {exc}")
                        continue
                    if (page_text.strip()
                            and len(page_text.strip()) >= min_chars
                            and not _has_repetitive_single_character_noise(page_text)):
                        has_usable_text = True
                        break
                if not has_usable_text:
                    return True
    finally:
        mupdf_doc.close()

    return False


def _detect_text_rotation(page):
    """Orientation of the page's TEXT LAYER.

    Returns 0/90/180/270 when the text layer clearly says how the page is turned
    (0 = upright), or None when there is nothing reliable to judge by.
    The text layer holds exact glyph directions, so it is far more trustworthy
    than OSD, which guesses from a rendered picture.
    """
    orientation_chars = {0: 0, 90: 0, 180: 0, 270: 0}
    blocks = page.get_text("dict", flags=_TEXT_ONLY_FLAGS).get("blocks", [])
    for block in blocks:
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            direction = line.get("dir", (1.0, 0.0))
            direction_angle = math.degrees(math.atan2(direction[1], direction[0])) % 360
            nearest_angle = round(direction_angle / 90) * 90 % 360
            angle_difference = abs((direction_angle - nearest_angle + 180) % 360 - 180)
            if angle_difference <= 15:
                correction = (-nearest_angle) % 360
                orientation_chars[correction] += len(text)

    total_chars = sum(orientation_chars.values())
    if total_chars < 20:
        return None          # no usable text layer -> nothing to judge by (caller may use OSD)

    correction, char_count = max(orientation_chars.items(), key=lambda item: item[1])
    if char_count / total_chars >= 0.7:
        return correction    # 0 = text layer confirmed upright; 90/180/270 = needs correcting
    return None              # mixed / ambiguous directions -> caller may use OSD


# ---------- OCR fallback ----------
def _try_ocr_image_regions(page, existing_text, timings=None, page_number=None):
    """OCR embedded image regions on the first page, such as payer logos."""
    existing_normalized = re.sub(r"\W+", "", existing_text).casefold()
    recognized_lines = []
    recognized_normalized = set()
    page_area = page.rect.width * page.rect.height

    for image_info in page.get_images(full=True):
        xref = image_info[0]
        try:
            image_rects = page.get_image_rects(xref)
        except Exception as e:
            print(f"First-page image {xref}: could not locate image regions: {e}")
            continue

        for image_rect in image_rects:
            coverage = (image_rect.width * image_rect.height) / page_area if page_area else 0
            dbg(f"page {page_number} | image-region xref={xref} covers {coverage:.0%} of page")
            try:
                with stage(f"image_region.render xref={xref}", timings, page_number):
                    pixmap = page.get_pixmap(
                        matrix=pymupdf.Matrix(300 / 72, 300 / 72),
                        clip=image_rect,
                        alpha=False,
                    )
                    image = Image.open(io.BytesIO(pixmap.tobytes("png")))
                image_text = ""
                for config in ("", "--psm 6", "--psm 11"):
                    with stage(f"image_region.tesseract[{config or 'default/psm3'}] {image.width}x{image.height}", timings, page_number):
                        image_text = pytesseract.image_to_string(image, config=config)
                    if image_text.strip():
                        break
            except Exception as e:
                print(f"First-page image {xref}: OCR failed: {e}")
                continue

            for line in image_text.splitlines():
                normalized_line = re.sub(r"\W+", "", line).casefold()
                if (
                    not normalized_line
                    or normalized_line in existing_normalized
                    or normalized_line in recognized_normalized
                ):
                    continue
                recognized_lines.append(line.strip())
                recognized_normalized.add(normalized_line)

    dbg(f"page {page_number} | image-region OCR added {len(recognized_lines)} new line(s)")
    return "\n".join(recognized_lines)


# ---------- Main extraction ----------
def _describe_page_images(page, page_number):
    """Debug: is this page one big picture (a scan)? What is the native resolution?"""
    try:
        area = page.rect.width * page.rect.height
        for info in page.get_images(full=True):
            xref, w_px, h_px = info[0], info[2], info[3]
            for r in page.get_image_rects(xref):
                cov = (r.width * r.height) / area if area else 0
                dpi_x = w_px / (r.width / 72) if r.width else 0
                dbg(f"page {page_number} | embedded image xref={xref} {w_px}x{h_px}px "
                    f"covers {cov:.0%} of page, native ~{dpi_x:.0f} dpi")
    except Exception as e:
        dbg(f"page {page_number} | image inspection failed: {e}")


_TOP_LEVEL_PREFIXES = ("osd.", "rotation.", "text.", "open.", "ocr.TOTAL", "image_region.TOTAL")


def _print_timing_summary(per_page, file_total, pdf_path):
    """Top-level stages add up to the file total; indented rows are parts of the stage above them."""
    if not OCR_DEBUG:
        return
    agg = {}
    for page_no, t in per_page.items():
        for label, secs in t.items():
            key = re.sub(r"\s+\d+x\d+$", "", label)          # merge differently-sized variants
            agg[key] = agg.get(key, 0.0) + secs
    top = {k: v for k, v in agg.items() if k.startswith(_TOP_LEVEL_PREFIXES)}
    nested = {k: v for k, v in agg.items() if k not in top}
    n_pages = len([p for p in per_page if p])
    print("=" * 78)
    print(f"[ocr-debug] TIMING SUMMARY  file={os.path.basename(pdf_path)}  total={file_total:.2f}s  pages={n_pages}")
    print("[ocr-debug]   (top-level rows add up to the total; '  -' rows are parts of the stage above)")
    for label, secs in sorted(top.items(), key=lambda kv: kv[1], reverse=True):
        print(f"[ocr-debug]   {secs:7.2f}s {100 * secs / file_total if file_total else 0:5.1f}%  {label}")
        parent = "ocr." if label.startswith("ocr.") else "image_region." if label.startswith("image_region.") else None
        if parent:
            for nl, nsecs in sorted(nested.items(), key=lambda kv: kv[1], reverse=True):
                is_child = (nl.startswith(("rapid.", "tesseract_ladder", "orientation_fallback", "ocr."))
                            if parent == "ocr." else nl.startswith("image_region."))
                if is_child:
                    print(f"[ocr-debug]       - {nsecs:6.2f}s {100 * nsecs / file_total if file_total else 0:5.1f}%  {nl}")
    print(f"[ocr-debug]   {file_total - sum(top.values()):7.2f}s        (unaccounted / python overhead)")
    print("=" * 78, flush=True)


def extract_pages_from_pdf(pdf_path, min_chars=20, min_rotation_conf=1.0, dpi=150):
    file_t0 = time.perf_counter()
    per_page = {}          # page_number -> {stage label: seconds}
    dbg(f"START {os.path.basename(pdf_path)} ({os.path.getsize(pdf_path)/1024:.0f} KB)")

    print("Detecting page rotations...")
    page_rotations = {}
    rotation_confidences = {}
    temp_doc = pymupdf.open(pdf_path)
    total_pages = len(temp_doc)

    for page_number in range(1, total_pages + 1):
        pt = per_page.setdefault(page_number, {})
        try:
            native_rotation = temp_doc[page_number - 1].rotation
            if native_rotation != 0:
                print(f"Page {page_number}: native /Rotate={native_rotation}, skipping OSD")
                continue
            with stage("rotation.text_layer_check", pt, page_number):
                text_rotation = _detect_text_rotation(temp_doc[page_number - 1])
            if text_rotation:
                page_rotations[page_number] = text_rotation
                print(f"Page {page_number}: detected text-layer rotation {text_rotation}°")
                continue
            if text_rotation == 0:
                # Digital page whose text layer is upright: OSD must not be allowed to
                # override it (it misfires on sparse / graphic pages) and costs ~2-3 s per page.
                dbg(f"page {page_number} | text layer is upright -> OSD skipped")
                continue
            # text_rotation is None -> scanned / no reliable text layer: fall through to OSD
            angle, confidence = detect_page_rotation(pdf_path, page_number, dpi, timings=pt)
            if angle is not None and confidence >= min_rotation_conf and angle != 0:
                page_rotations[page_number] = angle % 360
                rotation_confidences[page_number] = confidence
                print(f"Page {page_number}: detected rotation {angle}° (confidence {confidence:.2f})")
        except Exception as e:
            print(f"Page {page_number}: rotation detection failed: {e}")
    temp_doc.close()

    if page_rotations:
        print(f"Detected rotations on {len(page_rotations)} page(s).")
        print("Using ORIGINAL PDF for text extraction; rotating text coordinates only.")
    else:
        print("No rotation needed – using original PDF.")
    with open(pdf_path, "rb") as f:
        pdf_data = f.read()

    # Give each library its own independent stream
    pdfplumber_source = io.BytesIO(pdf_data)
    fitz_source = io.BytesIO(pdf_data)
    pypdf2_source = io.BytesIO(pdf_data)

    pages = []
    methods_used = []

    try:
        # Open all PDF engines from independent in-memory streams
        with stage("open.engines(pdfplumber+pymupdf+pypdf2)", per_page.setdefault(0, {}), None):
            plumber_ctx = pdfplumber.open(pdfplumber_source)
        with plumber_ctx as plumber_pdf:
            mupdf_doc = pymupdf.open(stream=fitz_source, filetype="pdf")
            pypdf2_reader = PyPDF2.PdfReader(pypdf2_source)

            plumber_count = len(plumber_pdf.pages)
            mupdf_count = len(mupdf_doc)
            pypdf2_count = len(pypdf2_reader.pages)

            # PyMuPDF is the coordinate/text reference; avoid out-of-range access.
            page_count = mupdf_count
            if len({plumber_count, mupdf_count, pypdf2_count}) > 1:
                print(f"PDF page-count mismatch: PyMuPDF={mupdf_count}, "
                      f"pdfplumber={plumber_count}, PyPDF2={pypdf2_count}; "
                      f"processing {page_count} PyMuPDF pages.")

            print("Total pages to process:", page_count)

            for page_number in range(1, page_count + 1):
                pt = per_page.setdefault(page_number, {})
                page_t0 = time.perf_counter()
                text = ""
                method = None
                rotation = page_rotations.get(page_number, 0)
                _describe_page_images(mupdf_doc[page_number - 1], page_number)

                # ----- 1. PyMuPDF -----
                try:
                    with stage("text.pymupdf", pt, page_number):
                        text = _try_pymupdf_page(mupdf_doc, page_number, rotation)
                    dbg(f"page {page_number} | pymupdf text layer: {len(text.strip())} chars")
                    if (text.strip() and len(text.strip()) >= min_chars
                            and not _has_repetitive_single_character_noise(text)):
                        method = "PyMuPDF"
                except Exception as e:
                    print(f"Page {page_number}: PyMuPDF failed: {e}")
                # ----- 2. pdfplumber -----
                if not method:
                    try:
                        with stage("text.pdfplumber(layout)", pt, page_number):
                            text = _try_pdfplumber_page(plumber_pdf, page_number)
                        dbg(f"page {page_number} | pdfplumber text layer: {len(text.strip())} chars")
                        if (text.strip() and len(text.strip()) >= min_chars
                                and not _has_repetitive_single_character_noise(text)):
                            method = "pdfplumber"
                    except Exception as e:
                        print(f"Page {page_number}: pdfplumber failed: {e}")
                # ----- 3. PyPDF2 -----
                if not method:
                    try:
                        with stage("text.pypdf2", pt, page_number):
                            text = _try_pypdf2_page(pypdf2_reader, page_number)
                        dbg(f"page {page_number} | pypdf2 text layer: {len(text.strip())} chars")
                        if (text.strip() and len(text.strip()) >= min_chars
                                and not _has_repetitive_single_character_noise(text)):
                            method = "PyPDF2"
                    except Exception as e:
                        print(f"Page {page_number}: PyPDF2 failed: {e}")
                # ----- 4. OCR -----
                if not method:
                    try:
                        print(f"Page {page_number}: no usable text layer, running OCR...")
                        with stage("ocr.TOTAL", pt, page_number):
                            text = _try_ocr_page(pdf_path, page_number, rotation, timings=pt)
                        if text.strip():
                            method = "OCR"
                        dbg(f"page {page_number} | OCR produced {len(text.strip())} chars, "
                            f"{len(text.splitlines())} lines")
                    except Exception as e:
                        print(f"Page {page_number}: OCR failed: {e}")
                        import traceback; traceback.print_exc()
                if not method:
                    print(f"Page {page_number}: ALL methods failed.")
                    method = "none"
                if page_number == 1:
                    if method == "OCR" and SKIP_IMAGE_REGION_OCR_ON_OCR_PAGES:
                        dbg("page 1 | image-region OCR SKIPPED (page already fully OCR'd; "
                            "set SKIP_IMAGE_REGION_OCR=0 to restore)")
                    else:
                        try:
                            with stage("image_region.TOTAL", pt, page_number):
                                image_text = _try_ocr_image_regions(
                                    mupdf_doc[page_number - 1], text, pt, page_number
                                )
                            if image_text:
                                text = f"{text.rstrip()}\n\n{image_text}".strip()
                        except Exception as e:
                            print(f"Page {page_number}: image OCR failed: {e}")
                if _has_repetitive_single_character_noise(text):
                    print(f"Page {page_number}: ignoring repetitive single-character text noise.")
                    text = ""
                    method = "none"
                pages.append({ "page_number": page_number, "text": text, "method": method, "rotation": rotation})
                methods_used.append(method)
                dbg(f"page {page_number} | DONE method={method} in {time.perf_counter() - page_t0:.2f}s")

            mupdf_doc.close()
    finally:
        # Explicitly close all streams
        pdfplumber_source.close()
        fitz_source.close()
        pypdf2_source.close()

        breakdown = {method: methods_used.count(method) for method in set(methods_used)}
    print(f"Method breakdown: {breakdown}")
    _print_timing_summary(per_page, time.perf_counter() - file_t0, pdf_path)
    return pages


def _uv(angle, x, y):
    """Page coords -> canonical (u = reading direction, v = line-stacking direction)."""
    return {0: (x, y), 90: (y, -x), 180: (-x, -y), 270: (-y, x)}[angle]


def _canon_box(bbox, angle):
    x0, y0, x1, y1 = bbox
    pts = [_uv(angle, x, y) for x in (x0, x1) for y in (y0, y1)]
    us, vs = [p[0] for p in pts], [p[1] for p in pts]
    return min(us), min(vs), max(us), max(vs)


def _page_lines(page):
    out = []
    for b in page.get_text("dict", sort=False, flags=_TEXT_ONLY_FLAGS).get("blocks", []):
        if b.get("type") != 0:
            continue
        for l in b.get("lines", []):
            t = "".join(s.get("text", "") for s in l.get("spans", [])).strip()
            if not t:
                continue
            dx, dy = l.get("dir", (1.0, 0.0))
            a = math.degrees(math.atan2(dy, dx)) % 360
            nearest = round(a / 90) * 90 % 360
            out.append({"t": t, "bbox": l["bbox"], "a": nearest})
    return out


def _rows_text(lines, angle):
    """Cluster lines that share an angle into rows and return (v_center, text) rows."""
    if not lines:
        return []
    boxes = [(_canon_box(l["bbox"], angle), l["t"]) for l in lines]
    heights = sorted(b[3] - b[1] for b, _ in boxes)
    tol = max(2.0, 0.5 * heights[len(heights) // 2])

    boxes.sort(key=lambda it: (it[0][1] + it[0][3]) / 2)
    rows = []
    for box, t in boxes:
        vc = (box[1] + box[3]) / 2
        if rows and abs(vc - rows[-1]["vc"]) <= tol:
            r = rows[-1]
            r["items"].append((box, t))
            r["vc"] = sum((b[1] + b[3]) / 2 for b, _ in r["items"]) / len(r["items"])
        else:
            rows.append({"vc": vc, "items": [(box, t)]})

    result = []
    for r in rows:
        r["items"].sort(key=lambda it: it[0][0])
        parts, prev_u1 = [], None
        for box, t in r["items"]:
            if prev_u1 is not None:
                gap = box[0] - prev_u1
                parts.append("  " if gap > 2 * (box[3] - box[1]) else " ")
            parts.append(t)
            prev_u1 = box[2]
        result.append((r["vc"], "".join(parts)))
    return result


def _group_regions(lines):
    """Merge nearby same-angle lines into spatial regions (page space)."""
    hs = sorted(min(l["bbox"][2] - l["bbox"][0], l["bbox"][3] - l["bbox"][1]) for l in lines)
    g = 1.5 * hs[len(hs) // 2]
    groups = [{"bbox": list(l["bbox"]), "lines": [l]} for l in lines]

    def touches(a, b):
        return not (a[2] + g < b[0] or b[2] + g < a[0] or a[3] + g < b[1] or b[3] + g < a[1])

    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if touches(groups[i]["bbox"], groups[j]["bbox"]):
                    a, b = groups[i], groups[j]
                    a["bbox"] = [min(a["bbox"][0], b["bbox"][0]), min(a["bbox"][1], b["bbox"][1]),
                                 max(a["bbox"][2], b["bbox"][2]), max(a["bbox"][3], b["bbox"][3])]
                    a["lines"] += b["lines"]
                    del groups[j]
                    merged = True
                    break
            if merged:
                break
    return groups


def _extract_mixed_page_text(page):
    lines = _page_lines(page)
    if not lines:
        return ""

    chars = {}
    for l in lines:
        chars[l["a"]] = chars.get(l["a"], 0) + len(l["t"])
    dominant = max(chars, key=chars.get)

    items = []  # (v position in dominant frame, text)

    main = [l for l in lines if l["a"] == dominant]
    items += _rows_text(main, dominant)

    for angle in (a for a in chars if a != dominant):
        side = [l for l in lines if l["a"] == angle]
        for region in _group_regions(side):
            rows = _rows_text(region["lines"], angle)
            rows.sort(key=lambda r: r[0])
            text = "\n".join(t for _, t in rows)
            x0, y0, x1, y1 = region["bbox"]
            # place the region by where it starts on the page, in the dominant frame
            v = _canon_box((x0, y0, x1, y1), dominant)[1]
            items.append((v, text))

    items.sort(key=lambda it: it[0])
    return "\n".join(t for _, t in items)

from pytesseract import Output

_OCR_ANGLES = (0, 90, 180, 270)


def _orientation_scores(image, osd_hint=0):
    """Readable-text score per orientation, measured on a reduced copy for speed."""
    small = image.resize((image.width * 2 // 3, image.height * 2 // 3))
    scores = {}
    for angle in _OCR_ANGLES:
        img = small.rotate(-angle, expand=True) if angle else small
        data = pytesseract.image_to_data(img, config="--psm 11", output_type=Output.DICT)
        score = 0.0
        for txt, conf in zip(data["text"], data["conf"]):
            txt = txt.strip()
            try:
                conf = float(conf)
            except (TypeError, ValueError):
                continue
            if (len(txt) >= 3 and conf >= 70
                    and sum(c.isalnum() for c in txt) / len(txt) >= 0.7):
                score += len(txt) * conf / 100
        scores[angle] = score
    if osd_hint in scores:
        scores[osd_hint] *= 1.1          # OSD is only a tie-breaker, not the decision
    return scores


# --oem 3: default engine (LSTM). preserve_interword_spaces keeps table columns apart.
# user_defined_dpi avoids Tesseract guessing the DPI of the rendered image.
_BASE_CFG = "--oem 3 -c preserve_interword_spaces=1 -c user_defined_dpi=300"
_PSM_MODES = (6, 4, 11)       # 6 = block of text, 4 = columns of varying size, 11 = sparse text
_GOOD_ENOUGH = 85.0           # stop early if mean word confidence reaches this


def _prepare_variants(image):
    """Cleaned versions of the page. Scanned EOBs usually read best in one of these two."""
    gray = ImageOps.autocontrast(image.convert("L"), cutoff=1)

    # Upscale small renders so characters are ~30+ px tall (Tesseract's sweet spot)
    if gray.width < 2500:
        scale = 2500 / gray.width
        gray = gray.resize((int(gray.width * scale), int(gray.height * scale)), Image.LANCZOS)

    soft = gray.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.SHARPEN)

    # Adaptive-style threshold: pixels darker than 85% of the local mean become black
    blurred = soft.filter(ImageFilter.GaussianBlur(25))
    binary = _local_threshold(soft, blurred)

    return {"gray": soft, "binary": binary}


def _local_threshold(img, blurred, ratio=0.85):
    # NumPy avoids millions of slow Python-level pixel operations per page.
    pixels = np.asarray(img, dtype=np.uint8)
    background = np.asarray(blurred, dtype=np.float32)
    binary = np.where(pixels < background * ratio, 0, 255).astype(np.uint8)
    return Image.fromarray(binary, mode="L")


def _ocr_with_conf(img, psm):
    """Run one pass and return (text, mean_conf, word_count) built from word-level data."""
    cfg = f"{_BASE_CFG} --psm {psm}"
    data = pytesseract.image_to_data(img, config=cfg, output_type=Output.DICT)
    lines, confs = {}, []
    for i, txt in enumerate(data["text"]):
        txt = txt.strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            continue
        if not txt or conf < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append((data["left"][i], txt))
        confs.append(conf)
    if not confs:
        return "", 0.0, 0
    text = "\n".join(
        " ".join(t for _, t in sorted(words)) for _, words in sorted(lines.items())
    )
    return text, sum(confs) / len(confs), len(confs)


def _tesseract_ladder(image, timings=None, page_number=None):
    """Original multi-pass Tesseract search (variants x PSM). Slow: up to 6 passes."""
    best_text, best_score = "", 0.0
    with stage("tesseract_ladder.prepare_variants", timings, page_number):
        variants = _prepare_variants(image)
    for name, img in variants.items():
        for psm in _PSM_MODES:
            try:
                with stage(f"tesseract_ladder.{name}/psm{psm}", timings, page_number):
                    text, conf, words = _ocr_with_conf(img, psm)
            except Exception as e:
                print(f"OCR {name}/psm{psm} failed: {e}")
                continue
            dbg(f"page {page_number} | tesseract {name}/psm{psm}: conf={conf:.1f} words={words}")
            score = conf * math.log1p(words)      # reward confident AND complete reads
            if score > best_score:
                best_text, best_score = text, score
            if conf >= _GOOD_ENOUGH and words >= 30:
                return best_text
    return best_text


# =====================================================================
# UPSIDE-DOWN CODE REPAIR
# The OCR text-line angle classifier occasionally flips a short crop (e.g. a
# procedure-code cell) by 180 deg, so "90791(HO)" is read as "(OH)16L06".
# Rotating a string 180 deg = reverse the order + replace each glyph by the
# glyph it looks like upside down (6<->9, 7<->L, 3<->E, 4<->h, '('<->')').
# We only repair a token when the OCR'd form is NOT a valid CPT code and the
# flipped form IS one, so correctly-read text is never touched.
# =====================================================================
_ROT180_CHARS = {
    "0": "0", "1": "1", "2": "2", "5": "5", "8": "8",
    "6": "9", "9": "6", "7": "L", "L": "7", "3": "E", "E": "3",
    "4": "h", "h": "4", "H": "H", "O": "O", "o": "O",
    "(": ")", ")": "(",
}
_CPT_TOKEN_RE = re.compile(r"^\d{5}(?:\([A-Z0-9]{1,2}\))*$")
_FLIPPABLE_TOKEN_RE = re.compile(r"^(?:\([A-Za-z0-9]{1,2}\))*[0-9A-Za-z]{5}(?:\([A-Za-z0-9]{1,2}\))*$")


def _rotate_180(token):
    out = []
    for ch in reversed(token):
        mapped = _ROT180_CHARS.get(ch)
        if mapped is None:
            return None
        out.append(mapped)
    return "".join(out)


def _fix_upside_down_codes(text):
    if not text:
        return text
    fixed_lines = []
    for line in text.splitlines():
        parts = re.split(r"(\s+)", line)
        for idx, tok in enumerate(parts):
            if not tok or tok.isspace() or _CPT_TOKEN_RE.match(tok):
                continue
            if not _FLIPPABLE_TOKEN_RE.match(tok):
                continue
            flipped = _rotate_180(tok)
            if flipped and _CPT_TOKEN_RE.match(flipped):
                dbg(f"upside-down code repaired: {tok!r} -> {flipped!r}")
                parts[idx] = flipped
        fixed_lines.append("".join(parts))
    return "\n".join(fixed_lines)


def _ocr_text(image, timings=None, page_number=None):
    """RapidOCR first; Tesseract ladder only if RapidOCR looks unreliable."""
    with stage("rapid.prep(to_gray+deskew)", timings, page_number):
        gray = deskew(np.asarray(image.convert("L")))
    dbg(f"page {page_number} | OCR input image {gray.shape[1]}x{gray.shape[0]} px")
    with stage("rapid.engine_call", timings, page_number):
        text, conf, words = rapid_ocr(gray)
    el = getattr(_ocr_local, "LAST_ELAPSE", None)
    if el:
        dbg(f"page {page_number} | RapidOCR internals: detect={el[0]:.2f}s "
            f"classify={el[1]:.2f}s recognize={el[2]:.2f}s")
        if timings is not None:
            timings["rapid.detect"] = timings.get("rapid.detect", 0.0) + el[0]
            timings["rapid.recognize"] = timings.get("rapid.recognize", 0.0) + el[2]
    dbg(f"page {page_number} | RapidOCR result: conf={conf:.1f} words={words} chars={len(text)}")
    if conf >= 80 and words >= 20:
        dbg(f"page {page_number} | RapidOCR accepted (conf>=80 and words>=20) -> Tesseract ladder NOT run")
        return _fix_upside_down_codes(text)
    dbg(f"page {page_number} | RapidOCR below threshold -> running Tesseract ladder fallback")
    t_text = _tesseract_ladder(image, timings, page_number)
    chosen = "tesseract" if len(t_text) > len(text) * 1.2 else "rapid"
    dbg(f"page {page_number} | fallback compare: rapid={len(text)} chars, tesseract={len(t_text)} chars -> {chosen}")
    return _fix_upside_down_codes(t_text if chosen == "tesseract" else text)


def _is_readable_line(line):
    chars = re.sub(r"\s+", "", line)
    return (len(chars) >= 4
            and sum(c.isalnum() for c in chars) / len(chars) >= 0.8
            and any(len(w) >= 3 for w in re.findall(r"[A-Za-z0-9]+", line)))


def _has_repetitive_single_character_noise(text):
    tokens = re.findall(r"[A-Za-z0-9]+", text or "")
    if len(tokens) < 30:
        return False

    single_char_counts = Counter(token.casefold() for token in tokens if len(token) == 1)
    single_char_total = sum(single_char_counts.values())
    if single_char_total / len(tokens) < 0.5:
        return False

    return max(single_char_counts.values(), default=0) / len(tokens) >= 0.35


def _ocr_is_usable(text):
    compact = re.sub(r"\s+", "", text or "")
    words = re.findall(r"[A-Za-z0-9]{2,}", text or "")
    if len(compact) < 30 or len(words) < 5:
        return False
    alnum_ratio = sum(ch.isalnum() for ch in compact) / max(len(compact), 1)
    return (alnum_ratio >= 0.55
            and not _has_repetitive_single_character_noise(text))


def _try_ocr_page(pdf_path, page_number, rotation=0, timings=None):
    with stage("ocr.render@300dpi(poppler)", timings, page_number):
        images = convert_from_path(pdf_path, dpi=300, first_page=page_number, last_page=page_number)
    if not images:
        return ""
    image = images[0].convert("L")
    dbg(f"page {page_number} | rendered image {image.width}x{image.height}px, rotation={rotation}")

    # Trust the detected correction first. Four-angle scoring is only a fallback.
    primary = image.rotate(-rotation, expand=True) if rotation else image
    text = _ocr_text(primary, timings, page_number)
    if _ocr_is_usable(text):
        dbg(f"page {page_number} | OCR text usable -> orientation fallback NOT needed")
        return text if _ocr_is_usable(text) else ""

    dbg(f"page {page_number} | OCR text NOT usable -> trying 4-angle orientation scoring")
    with stage("orientation_fallback.scores(4xTesseract)", timings, page_number):
        scores = _orientation_scores(image, osd_hint=rotation)
    dbg(f"page {page_number} | orientation scores: { {k: round(v, 1) for k, v in scores.items()} }")
    for angle, score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
        if angle == rotation or score <= 0:
            continue
        candidate_image = image.rotate(-angle, expand=True) if angle else image
        with stage(f"orientation_fallback.ocr@{angle}deg", timings, page_number):
            candidate_text = _ocr_text(candidate_image, timings, page_number)
        if len(candidate_text.strip()) > len(text.strip()) and _ocr_is_usable(candidate_text):
            print(f"Page {page_number}: fallback OCR orientation {angle}°")
            return candidate_text
    return text