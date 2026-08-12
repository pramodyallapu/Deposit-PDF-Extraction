import pdfplumber
import fitz                 # PyMuPDF
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

# ---------- Helper extraction functions ----------
def _try_pdfplumber_page(pdf, page_number):
    page = pdf.pages[page_number - 1]
    return page.extract_text(layout=True) or ""


def _try_pymupdf_page(doc, page_number):
    page = doc[page_number - 1]
    return page.get_text() or ""


def _try_pypdf2_page(reader, page_number):
    page = reader.pages[page_number - 1]
    return page.extract_text() or ""

def _try_ocr_page(pdf_path, page_number):
    images = convert_from_path(pdf_path, dpi=300, first_page=page_number, last_page=page_number)
    if not images:
        return ""
    return pytesseract.image_to_string(images[0])

# ---------- Build corrected PDF in memory ----------
def build_corrected_pdf(pdf_path, min_rotation_conf=1.0, dpi=150):
    doc = fitz.open(pdf_path)
    rotations_to_apply = {}
    total_pages = len(doc)

    for page_num in range(1, total_pages + 1):
        angle, conf = detect_page_rotation(pdf_path, page_num, dpi)
        if angle is not None and conf >= min_rotation_conf and angle != 0:
            rotations_to_apply[page_num] = angle

    if not rotations_to_apply:
        doc.close()
        return None

    print(f"Building in‑memory corrected PDF with {len(rotations_to_apply)} rotated pages...")
    new_doc = fitz.open()

    for page_num in range(1, total_pages + 1):
        original_page = doc[page_num - 1]
        new_page = new_doc.new_page(width=original_page.rect.width,
                                    height=original_page.rect.height)
        new_page.show_pdf_page(new_page.rect, doc, page_num - 1)

        if page_num in rotations_to_apply:
            detected_angle = rotations_to_apply[page_num]
            correction = detected_angle % 360
            new_page.set_rotation(correction)
            print(f"  Page {page_num}: applied rotation {correction}° (corrects {detected_angle}°)")

    pdf_bytes = io.BytesIO()
    new_doc.save(pdf_bytes)
    new_doc.close()
    doc.close()
    pdf_bytes.seek(0)
    return pdf_bytes

# ---------- Main extraction with automatic correction ----------
def extract_pages_from_pdf(pdf_path, min_chars=20, min_rotation_conf=1.0, dpi=150):
    corrected_pdf_bytes = build_corrected_pdf(pdf_path, min_rotation_conf, dpi)

    # Create input sources
    if corrected_pdf_bytes is not None:
        print("Using in‑memory corrected PDF for extraction.")
        pdf_data = corrected_pdf_bytes.getvalue()
        # Each library needs its own BytesIO object to avoid position conflicts
        pdfplumber_source = io.BytesIO(pdf_data)
        fitz_source = io.BytesIO(pdf_data)
        pypdf2_source = io.BytesIO(pdf_data)
    else:
        print("No rotation needed – using original PDF.")
        pdfplumber_source = pdf_path
        fitz_source = pdf_path
        pypdf2_source = pdf_path

    pages = []
    methods_used = []

    # Open pdfplumber
    with pdfplumber.open(pdfplumber_source) as plumber_pdf:
        # Open PyMuPDF (fitz) – handle both BytesIO and file path
        if isinstance(fitz_source, io.BytesIO):
            mupdf_doc = fitz.open(stream=fitz_source, filetype="pdf")
        else:
            mupdf_doc = fitz.open(fitz_source)

        # Open PyPDF2 – handle both BytesIO and file path
        if isinstance(pypdf2_source, io.BytesIO):
            pypdf2_reader = PyPDF2.PdfReader(pypdf2_source)
        else:
            with open(pypdf2_source, "rb") as f:
                pypdf2_reader = PyPDF2.PdfReader(f)

        page_count = len(plumber_pdf.pages)
        for page_number in range(1, page_count + 1):
            text = ""
            method = None
            # ----- 1. Try pdfplumber -----
            try:
                text = _try_pdfplumber_page(plumber_pdf, page_number)
                if text.strip() and len(text.strip()) >= min_chars:
                    method = "pdfplumber"
            except Exception as e:
                print(f"Page {page_number}: pdfplumber failed: {e}")
            # ----- 2. Try PyMuPDF -----
            if not method:
                try:
                    text = _try_pymupdf_page(mupdf_doc, page_number)
                    if text.strip() and len(text.strip()) >= min_chars:
                        method = "PyMuPDF"
                except Exception as e:
                    print(f"Page {page_number}: PyMuPDF failed: {e}")
            # ----- 3. Try PyPDF2 -----
            if not method:
                try:
                    text = _try_pypdf2_page(pypdf2_reader, page_number)
                    if text.strip() and len(text.strip()) >= min_chars:
                        method = "PyPDF2"
                except Exception as e:
                    print(f"Page {page_number}: PyPDF2 failed: {e}")
            # ----- 4. Fallback to OCR -----
            if not method:
                try:
                    print(f"Page {page_number}: no text layer or too short, running OCR...")
                    text = _try_ocr_page(pdf_path, page_number)  # original file path for image conversion
                    if text.strip():
                        method = "OCR"
                except Exception as e:
                    print(f"Page {page_number}: OCR failed: {e}")
            if not method:
                print(f"Page {page_number}: ALL methods failed.")
                method = "none"
            pages.append({"page_number": page_number, "text": text, "method": method})
            methods_used.append(method)
        # Close PyMuPDF if it was opened from BytesIO
        if isinstance(fitz_source, io.BytesIO):
            mupdf_doc.close()
    print(f"Method breakdown: { {m: methods_used.count(m) for m in set(methods_used)} }")
    return pages