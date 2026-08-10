"""Per-page cascading PDF text extraction: pdfplumber -> PyMuPDF -> PyPDF2 -> OCR."""
import pdfplumber
import fitz
import PyPDF2
import pytesseract
import re
from pdf2image import convert_from_path
from PIL import Image


def _try_pdfplumber_page(pdf, page_number):
    page = pdf.pages[page_number - 1]
    return page.extract_text(layout=True) or ""


def _try_pymupdf_page(doc, page_number):
    page = doc[page_number - 1]
    return page.get_text() or ""


def _try_pypdf2_page(reader, page_number):
    page = reader.pages[page_number - 1]
    return page.extract_text() or ""


def correct_image_orientation(image):
    try:
        osd = pytesseract.image_to_osd(image)
        angle_match = re.search(r"Rotate:\s*(\d+)", osd)
        conf_match = re.search(r"Orientation confidence:\s*([\d.]+)", osd)
        angle = int(angle_match.group(1)) if angle_match else 0
        confidence = float(conf_match.group(1)) if conf_match else 0.0
        if angle != 0 and confidence >= 1.0:
            print(f"  Detected page rotated {angle} degrees (conf {confidence:.1f}) -- correcting...")
            image = image.rotate(-angle, expand=True)
    except Exception as e:
        print(f"  Orientation detection skipped: {e}")
    return image


def _try_ocr_page(pdf_path, page_number):
    images = convert_from_path(pdf_path, dpi=300, first_page=page_number, last_page=page_number)
    if not images:
        return ""
    image = correct_image_orientation(images[0])
    return pytesseract.image_to_string(image)


def extract_pages_from_pdf(pdf_path, min_chars=20):
    pages = []
    methods_used = []
    with pdfplumber.open(pdf_path) as plumber_pdf, fitz.open(pdf_path) as mupdf_doc, open(pdf_path, "rb") as f:
        pypdf2_reader = PyPDF2.PdfReader(f)
        page_count = len(plumber_pdf.pages)
        for page_number in range(1, page_count + 1):
            text = ""
            method = None
            try:
                text = _try_pdfplumber_page(plumber_pdf, page_number)
                if text.strip() and len(text.strip()) >= min_chars:
                    method = "pdfplumber"
            except Exception as e:
                print(f"Page {page_number}: pdfplumber failed: {e}")
            if not method:
                try:
                    text = _try_pymupdf_page(mupdf_doc, page_number)
                    if text.strip() and len(text.strip()) >= min_chars:
                        method = "PyMuPDF"
                except Exception as e:
                    print(f"Page {page_number}: PyMuPDF failed: {e}")
            if not method:
                try:
                    text = _try_pypdf2_page(pypdf2_reader, page_number)
                    if text.strip() and len(text.strip()) >= min_chars:
                        method = "PyPDF2"
                except Exception as e:
                    print(f"Page {page_number}: PyPDF2 failed: {e}")
            if not method:
                try:
                    print(f"Page {page_number}: no text layer, running OCR...")
                    text = _try_ocr_page(pdf_path, page_number)
                    if text.strip():
                        method = "OCR"
                except Exception as e:
                    print(f"Page {page_number}: OCR failed: {e}")
            if not method:
                print(f"Page {page_number}: ALL methods failed.")
                method = "none"
            pages.append({"page_number": page_number, "text": text, "method": method})
            methods_used.append(method)
    print(f"Method breakdown: { {m: methods_used.count(m) for m in set(methods_used)} }")
    return pages