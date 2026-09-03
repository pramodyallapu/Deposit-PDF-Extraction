import pdfplumber
import pymupdf                 # PyMuPDF
import PyPDF2
import pytesseract
import re
import io
from pdf2image import convert_from_path

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

# ---------- OCR fallback ----------
def _try_ocr_page(pdf_path, page_number, rotation=0):
    images = convert_from_path(pdf_path, dpi=300, first_page=page_number, last_page=page_number)
    if not images:
        return ""
    image = images[0]
    if rotation:
        image = image.rotate(-rotation, expand=True)
    return pytesseract.image_to_string(image)

# ---------- Main extraction ----------
def extract_pages_from_pdf(pdf_path, min_chars=20, min_rotation_conf=1.0, dpi=150):
    print("Detecting page rotations...")
    page_rotations = {}
    rotation_confidences = {}
    temp_doc = pymupdf.open(pdf_path)
    total_pages = len(temp_doc)
    temp_doc.close()

    for page_number in range(1, total_pages + 1):
        angle, confidence = detect_page_rotation(pdf_path, page_number, dpi)
        if angle is not None and confidence >= min_rotation_conf and angle != 0:
            page_rotations[page_number] = angle % 360
            rotation_confidences[page_number] = confidence
            print(f"Page {page_number}: detected rotation {angle}° (confidence {confidence:.2f})")

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

            print("pdfplumber pages:", plumber_count)
            print("PyMuPDF pages:", mupdf_count)
            print("PyPDF2 pages:", pypdf2_count)

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