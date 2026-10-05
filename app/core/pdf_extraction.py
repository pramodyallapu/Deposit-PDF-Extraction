import pdfplumber
import pymupdf                 # PyMuPDF
import PyPDF2
import pytesseract
import io
import math
import re
from pdf2image import convert_from_path
from PIL import Image

# ---------- Rotation detection (unchanged) ----------
def detect_page_rotation(pdf_path, page_number, dpi=150):
    try:
        images = convert_from_path(pdf_path, dpi=dpi, first_page=page_number, last_page=page_number)
        if not images:
            return None, 0.0
        osd = pytesseract.image_to_osd(images[0])
        angle_match = re.search(r"Rotate:\s*(\d+)", osd)
        conf_match = re.search(r"Orientation confidence:\s*([\d.]+)", osd)
        angle = int(angle_match.group(1)) if angle_match else 0
        confidence = float(conf_match.group(1)) if conf_match else 0.0
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
        # PDF has its own /Rotate. Coordinates from get_text("words") are
        # already in rotation-corrected space (confirmed via page.rect),
        # so use MuPDF's own reading-order reconstruction instead of the
        # custom y-tolerance clustering, which collapses adjacent table
        # rows on dense/tabular landscape pages.
        text = page.get_text("text", sort=True)
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


def _detect_text_rotation(page):
    """Return the correction angle for a page whose text layer is sideways."""
    orientation_chars = {0: 0, 90: 0, 180: 0, 270: 0}
    blocks = page.get_text("dict").get("blocks", [])
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
        return 0

    correction, char_count = max(orientation_chars.items(), key=lambda item: item[1])
    if correction and char_count / total_chars >= 0.7:
        return correction
    return 0


# ---------- OCR fallback ----------
def _try_ocr_page(pdf_path, page_number, rotation=0):
    images = convert_from_path(pdf_path, dpi=300, first_page=page_number, last_page=page_number)
    if not images:
        return ""
    image = images[0]
    if rotation:
        image = image.rotate(-rotation, expand=True)
    for config in ("", "--psm 6", "--psm 11"):
        text = pytesseract.image_to_string(image, config=config)
        if text.strip():
            return text
    return ""


def _try_ocr_image_regions(page, existing_text):
    """OCR embedded image regions on the first page, such as payer logos."""
    existing_normalized = re.sub(r"\W+", "", existing_text).casefold()
    recognized_lines = []
    recognized_normalized = set()

    for image_info in page.get_images(full=True):
        xref = image_info[0]
        try:
            image_rects = page.get_image_rects(xref)
        except Exception as e:
            print(f"First-page image {xref}: could not locate image regions: {e}")
            continue

        for image_rect in image_rects:
            try:
                pixmap = page.get_pixmap(
                    matrix=pymupdf.Matrix(300 / 72, 300 / 72),
                    clip=image_rect,
                    alpha=False,
                )
                image = Image.open(io.BytesIO(pixmap.tobytes("png")))
                image_text = ""
                for config in ("", "--psm 6", "--psm 11"):
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

    return "\n".join(recognized_lines)


# ---------- Main extraction ----------
def extract_pages_from_pdf(pdf_path, min_chars=20, min_rotation_conf=1.0, dpi=150):
    print("Detecting page rotations...")
    page_rotations = {}
    rotation_confidences = {}
    temp_doc = pymupdf.open(pdf_path)
    total_pages = len(temp_doc)

    for page_number in range(1, total_pages + 1):
        try:
            native_rotation = temp_doc[page_number - 1].rotation
            if native_rotation != 0:
                print(f"Page {page_number}: native /Rotate={native_rotation}, skipping OSD")
                continue
            text_rotation = _detect_text_rotation(temp_doc[page_number - 1])
            if text_rotation:
                page_rotations[page_number] = text_rotation
                print(f"Page {page_number}: detected text-layer rotation {text_rotation}°")
                continue
            angle, confidence = detect_page_rotation(pdf_path, page_number, dpi)
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
        with pdfplumber.open(pdfplumber_source) as plumber_pdf:
            mupdf_doc = pymupdf.open(stream=fitz_source, filetype="pdf")
            pypdf2_reader = PyPDF2.PdfReader(pypdf2_source)

            plumber_count = len(plumber_pdf.pages)
            mupdf_count = len(mupdf_doc)
            pypdf2_count = len(pypdf2_reader.pages)

            # print("pdfplumber pages:", plumber_count)
            # print("PyMuPDF pages:", mupdf_count)
            # print("PyPDF2 pages:", pypdf2_count)

            # Use the largest page count available
            page_count = max( plumber_count, mupdf_count, pypdf2_count)

            print("Total pages to process:", page_count)

            for page_number in range(1, page_count + 1):
                text = ""
                method = None
                rotation = page_rotations.get(page_number, 0)

                # ----- 1. PyMuPDF -----
                try:
                    text = _try_pymupdf_page(mupdf_doc, page_number, rotation)
                    if text.strip() and len(text.strip()) >= min_chars:
                        method = "PyMuPDF"
                except Exception as e:
                    print(f"Page {page_number}: PyMuPDF failed: {e}")
                # ----- 2. pdfplumber -----
                if not method:
                    try:
                        text = _try_pdfplumber_page(plumber_pdf, page_number)
                        if text.strip() and len(text.strip()) >= min_chars:
                            method = "pdfplumber"
                    except Exception as e:
                        print(f"Page {page_number}: pdfplumber failed: {e}")
                # ----- 3. PyPDF2 -----
                if not method:
                    try:
                        text = _try_pypdf2_page(pypdf2_reader, page_number)
                        if text.strip() and len(text.strip()) >= min_chars:
                            method = "PyPDF2"
                    except Exception as e:
                        print(f"Page {page_number}: PyPDF2 failed: {e}")
                # ----- 4. OCR -----
                if not method:
                    try:
                        print(f"Page {page_number}: no usable text layer, running OCR...")
                        text = _try_ocr_page(pdf_path, page_number, rotation)
                        if text.strip():
                            method = "OCR"
                    except Exception as e:
                        print(f"Page {page_number}: OCR failed: {e}")
                if not method:
                    print(f"Page {page_number}: ALL methods failed.")
                    method = "none"
                if page_number == 1:
                    try:
                        image_text = _try_ocr_image_regions(
                            mupdf_doc[page_number - 1], text
                        )
                        if image_text:
                            # print("IMAGE or LOGO : ", image_text)
                            text = f"{text.rstrip()}\n\n{image_text}".strip()
                    except Exception as e:
                        print(f"Page {page_number}: image OCR failed: {e}")
                pages.append({ "page_number": page_number, "text": text, "method": method, "rotation": rotation})
                methods_used.append(method)

            mupdf_doc.close()
    finally:
        # Explicitly close all streams
        pdfplumber_source.close()
        fitz_source.close()
        pypdf2_source.close()

        breakdown = {method: methods_used.count(method) for method in set(methods_used)}
    print(f"Method breakdown: {breakdown}")
    return pages