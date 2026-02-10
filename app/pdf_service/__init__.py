import io
import os
from datetime import UTC

import pikepdf
from flask import current_app
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

LIBRARY_NAME = "Bibliotheca Oratorii Sacratissimorum Cordium"

_font_registered = False


def _register_cormorant_font():
    """Register Cormorant Garamond TTF with reportlab (once)."""
    global _font_registered
    if _font_registered:
        return
    font_path = os.path.join(current_app.root_path, "static", "fonts", "CormorantGaramond-Regular.ttf")
    if os.path.isfile(font_path):
        pdfmetrics.registerFont(TTFont("CormorantGaramond", font_path))
    _font_registered = True


def generate_circulation_copy(loan, book, user):
    """Generate a watermarked circulation PDF for the given loan.

    Returns the filename of the saved circulation copy.
    """
    master_dir = current_app.config["MASTER_STORAGE"]
    master_path = os.path.realpath(os.path.join(master_dir, book.master_filename))
    if not master_path.startswith(os.path.realpath(master_dir) + os.sep):
        raise ValueError(f"Path traversal blocked: {book.master_filename}")
    if not os.path.isfile(master_path):
        raise FileNotFoundError(f"Master PDF not found: {book.master_filename}")

    due_str = loan.due_at.strftime("%B %d, %Y")
    watermark_text = f"Loaned to {user.display_name} \u2014 Due {due_str} \u2014 {LIBRARY_NAME}"

    # Build the cover page PDF (loan slip)
    cover_buf = _build_cover_page(book, user, due_str)

    # Build the end page PDF (return instructions)
    end_buf = _build_end_page(book, user, due_str)

    # Open master and assemble the circulation copy
    with pikepdf.open(master_path) as master_pdf:
        content_page_count = len(master_pdf.pages)

        # Determine which pages to watermark
        if book.watermark_mode == "gentle" and content_page_count >= 2:
            pages_to_watermark = {0, content_page_count - 1}
        else:
            pages_to_watermark = set(range(content_page_count))

        # Apply footer watermark to selected content pages
        for page_idx in pages_to_watermark:
            page = master_pdf.pages[page_idx]
            watermark_buf = _build_watermark_overlay(page, watermark_text)
            watermark_pdf = pikepdf.open(watermark_buf)
            watermark_page = watermark_pdf.pages[0]

            # Merge the watermark overlay onto the content page
            page_obj = page.obj
            if "/Contents" in page_obj:
                # Underlay strategy: prepend a save-state, append watermark + restore
                page.add_overlay(watermark_page)
            else:
                page.add_overlay(watermark_page)
            watermark_pdf.close()

        # Insert cover page at beginning
        cover_pdf = pikepdf.open(cover_buf)
        master_pdf.pages.insert(0, cover_pdf.pages[0])

        # Append end page
        end_pdf = pikepdf.open(end_buf)
        master_pdf.pages.append(end_pdf.pages[0])

        # Embed metadata
        with master_pdf.open_metadata() as meta:
            meta["dc:title"] = book.title
            meta["dc:creator"] = [book.author]
            meta["dc:description"] = f"Circulation copy \u2014 Borrower: {user.display_name} \u2014 Due: {due_str}"
            meta["pdf:Producer"] = LIBRARY_NAME

        # Save to circulation storage
        filename = f"loan_{loan.public_id}.pdf"
        output_path = os.path.join(current_app.config["CIRCULATION_STORAGE"], filename)
        master_pdf.save(output_path)

        cover_pdf.close()
        end_pdf.close()

    return filename


def _get_logo_path():
    """Return the absolute path to the library logo, or None if not found."""
    logo_path = os.path.join(current_app.root_path, "static", "img", "logo.png")
    if os.path.isfile(logo_path):
        return logo_path
    return None


def _build_cover_page(book, user, due_str):
    """Build a loan-slip cover page using reportlab. Returns a BytesIO."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter

    logo_path = _get_logo_path()

    # Logo centered at top
    if logo_path:
        logo_w = 1.8 * inch
        logo_h = 0.9 * inch
        c.drawImage(
            logo_path,
            (width - logo_w) / 2,
            height - 1.2 * inch,
            width=logo_w,
            height=logo_h,
            preserveAspectRatio=True,
            anchor="c",
            mask="auto",
        )
        name_y = height - 1.6 * inch
    else:
        name_y = height - 1.5 * inch

    # Library name header
    c.setFont("Times-Bold", 22)
    c.drawCentredString(width / 2, name_y, LIBRARY_NAME)

    # Decorative line
    line_y = name_y - 0.3 * inch
    c.setStrokeColorRGB(0.42, 0.11, 0.16)
    c.setLineWidth(1.5)
    c.line(1.2 * inch, line_y, width - 1.2 * inch, line_y)

    # Loan Slip heading
    c.setFont("Times-Bold", 18)
    c.drawCentredString(width / 2, line_y - 0.7 * inch, "Loan Slip")

    # Book details
    y = line_y - 1.5 * inch
    c.setFont("Times-Roman", 13)

    fields = [
        ("Title:", book.title),
        ("Author:", book.author),
        ("Borrower:", user.display_name),
        ("Date of Loan:", _format_date(loan_date=None)),
        ("Due Date:", due_str),
    ]

    for label, value in fields:
        c.setFont("Times-Bold", 12)
        c.drawString(1.5 * inch, y, label)
        c.setFont("Times-Roman", 12)
        # Truncate very long values to fit the page
        display_value = value if len(value) <= 70 else value[:67] + "..."
        c.drawString(3.0 * inch, y, display_value)
        y -= 0.35 * inch

    # One-copy-one-loan notice
    y -= 0.5 * inch
    c.setFont("Times-Italic", 10)
    notice_lines = [
        "This digital copy is licensed for a single concurrent loan under the",
        "one-copy-one-loan principle. It may not be redistributed, copied, or",
        "shared. Access expires automatically on the due date above.",
    ]
    for line in notice_lines:
        c.drawCentredString(width / 2, y, line)
        y -= 0.22 * inch

    # Decorative bottom line
    c.setStrokeColorRGB(0.42, 0.11, 0.16)
    c.setLineWidth(1.5)
    c.line(1.2 * inch, 1.2 * inch, width - 1.2 * inch, 1.2 * inch)

    # Footer — small logo + library name
    if logo_path:
        footer_logo_w = 0.5 * inch
        footer_logo_h = 0.25 * inch
        c.drawImage(
            logo_path,
            (width - footer_logo_w) / 2,
            0.85 * inch,
            width=footer_logo_w,
            height=footer_logo_h,
            preserveAspectRatio=True,
            anchor="c",
            mask="auto",
        )
        c.setFont("Times-Roman", 8)
        c.drawCentredString(width / 2, 0.7 * inch, LIBRARY_NAME)
    else:
        c.setFont("Times-Roman", 9)
        c.drawCentredString(width / 2, 0.9 * inch, LIBRARY_NAME)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def _build_end_page(book, user, due_str):
    """Build a return-instructions end page using reportlab. Returns a BytesIO."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter

    contact_email = current_app.config.get("LIBRARY_CONTACT_EMAIL", "library@oratory.example.org")

    logo_path = _get_logo_path()

    # Logo at top of end page
    if logo_path:
        logo_w = 1.4 * inch
        logo_h = 0.7 * inch
        c.drawImage(
            logo_path,
            (width - logo_w) / 2,
            height - 1.1 * inch,
            width=logo_w,
            height=logo_h,
            preserveAspectRatio=True,
            anchor="c",
            mask="auto",
        )

    # Header
    header_y = height - 1.5 * inch
    c.setFont("Times-Bold", 18)
    c.drawCentredString(width / 2, header_y, "Return Instructions")

    c.setStrokeColorRGB(0.42, 0.11, 0.16)
    c.setLineWidth(1.5)
    c.line(1.2 * inch, header_y - 0.3 * inch, width - 1.2 * inch, header_y - 0.3 * inch)

    y = height - 2.5 * inch
    c.setFont("Times-Roman", 12)

    paragraphs = [
        f'This loan of "{book.title}" is due on {due_str}.',
        "",
        "To return this book early, sign in to your library account and",
        'select "Return" from your active loans on your patron dashboard.',
        "",
        "If you do not return the book manually, your access will expire",
        "automatically on the due date and the copy will be released back",
        "into circulation for other patrons.",
        "",
        "Policy Reminder:",
        "",
        "  \u2022 Each digital copy is loaned to one patron at a time.",
        "  \u2022 Do not share, redistribute, or copy this file.",
        "  \u2022 Repeated violations may result in suspension of borrowing privileges.",
        "",
        "If you have questions or need assistance, please contact us:",
        "",
        f"    {contact_email}",
    ]

    for line in paragraphs:
        if line.startswith("Policy Reminder"):
            c.setFont("Times-Bold", 12)
        elif line == "":
            y -= 0.18 * inch
            continue
        else:
            c.setFont("Times-Roman", 12)
        c.drawString(1.5 * inch, y, line)
        y -= 0.28 * inch

    # Footer
    c.setStrokeColorRGB(0.42, 0.11, 0.16)
    c.setLineWidth(1.5)
    c.line(1.2 * inch, 1.2 * inch, width - 1.2 * inch, 1.2 * inch)

    # Footer — small logo + library name
    if logo_path:
        footer_logo_w = 0.5 * inch
        footer_logo_h = 0.25 * inch
        c.drawImage(
            logo_path,
            (width - footer_logo_w) / 2,
            0.85 * inch,
            width=footer_logo_w,
            height=footer_logo_h,
            preserveAspectRatio=True,
            anchor="c",
            mask="auto",
        )
        c.setFont("Times-Roman", 8)
        c.drawCentredString(width / 2, 0.7 * inch, LIBRARY_NAME)
    else:
        c.setFont("Times-Roman", 9)
        c.drawCentredString(width / 2, 0.9 * inch, LIBRARY_NAME)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def generate_public_domain_copy(book):
    """Generate a library-edition PDF for a public domain book.

    Prepends a donate/attribution front page. No watermarks, no end page.
    Returns the filename of the saved copy, or raises on failure.
    """
    master_dir = current_app.config["MASTER_STORAGE"]
    master_path = os.path.realpath(os.path.join(master_dir, book.master_filename))
    if not master_path.startswith(os.path.realpath(master_dir) + os.sep):
        raise ValueError(f"Path traversal blocked: {book.master_filename}")
    if not os.path.isfile(master_path):
        raise FileNotFoundError(f"Master PDF not found: {book.master_filename}")

    # Build the donate front page
    donate_buf = _build_donate_page(book)

    with pikepdf.open(master_path) as master_pdf:
        # Insert donate page at beginning
        donate_pdf = pikepdf.open(donate_buf)
        master_pdf.pages.insert(0, donate_pdf.pages[0])

        # Embed metadata
        with master_pdf.open_metadata() as meta:
            meta["dc:title"] = book.title
            meta["dc:creator"] = [book.formatted_authors]
            meta["dc:description"] = f"Public domain \u2014 {LIBRARY_NAME}"
            meta["pdf:Producer"] = LIBRARY_NAME

        # Save to circulation storage (reuse the same dir)
        filename = f"pd_{book.public_id}.pdf"
        output_path = os.path.join(current_app.config["CIRCULATION_STORAGE"], filename)
        master_pdf.save(output_path)

        donate_pdf.close()

    return filename


def _build_donate_page(book):
    """Build an elegant donate/attribution front page for public domain books.

    Returns a BytesIO containing a single-page PDF.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    cx = width / 2

    donate_url = current_app.config.get("DONATE_URL", "")

    # Library burgundy
    BURG_R, BURG_G, BURG_B = 0.42, 0.11, 0.16
    # Warm grey for secondary text
    WARM_R, WARM_G, WARM_B = 0.35, 0.33, 0.31

    logo_path = _get_logo_path()

    # ── Top decorative rule ──────────────────────────────────
    c.setStrokeColorRGB(BURG_R, BURG_G, BURG_B)
    c.setLineWidth(0.75)
    c.line(1.8 * inch, height - 0.7 * inch, width - 1.8 * inch, height - 0.7 * inch)

    # ── Logo ─────────────────────────────────────────────────
    if logo_path:
        logo_w = 1.5 * inch
        logo_h = 0.75 * inch
        c.drawImage(
            logo_path,
            (width - logo_w) / 2,
            height - 1.55 * inch,
            width=logo_w,
            height=logo_h,
            preserveAspectRatio=True,
            anchor="c",
            mask="auto",
        )
        y = height - 1.85 * inch
    else:
        y = height - 1.2 * inch

    # ── Library name ─────────────────────────────────────────
    c.setFillColorRGB(BURG_R, BURG_G, BURG_B)
    c.setFont("Times-Bold", 16)
    c.drawCentredString(cx, y, LIBRARY_NAME)

    # ── Thin rule under name ─────────────────────────────────
    y -= 0.25 * inch
    c.setStrokeColorRGB(BURG_R, BURG_G, BURG_B)
    c.setLineWidth(0.5)
    c.line(2.5 * inch, y, width - 2.5 * inch, y)

    # ── "From Our Collection" subtitle ───────────────────────
    y -= 0.4 * inch
    c.setFillColorRGB(WARM_R, WARM_G, WARM_B)
    c.setFont("Times-Roman", 10)
    c.drawCentredString(cx, y, "FROM OUR PUBLIC DOMAIN COLLECTION")

    # ── Book title ───────────────────────────────────────────
    y -= 0.55 * inch
    c.setFillColorRGB(0, 0, 0)

    # Scale title font to fit — start at 26pt, shrink if needed
    title_text = book.title or ""
    title_size = 26
    while title_size > 16:
        tw = c.stringWidth(title_text, "Times-Bold", title_size)
        if tw <= width - 3 * inch:
            break
        title_size -= 1

    c.setFont("Times-Bold", title_size)
    c.drawCentredString(cx, y, title_text if len(title_text) <= 80 else title_text[:77] + "...")

    # ── Author ───────────────────────────────────────────────
    y -= 0.4 * inch
    c.setFont("Times-Italic", 14)
    c.setFillColorRGB(WARM_R, WARM_G, WARM_B)
    author_text = book.formatted_authors
    c.drawCentredString(cx, y, author_text if len(author_text) <= 80 else author_text[:77] + "...")

    # ── Year (if known) ──────────────────────────────────────
    if book.publication_year:
        y -= 0.3 * inch
        c.setFont("Times-Roman", 11)
        c.drawCentredString(cx, y, str(book.publication_year))

    # ── Ornamental separator ─────────────────────────────────
    y -= 0.55 * inch
    c.setFillColorRGB(BURG_R, BURG_G, BURG_B)
    c.setFont("Times-Roman", 14)
    c.drawCentredString(cx, y, "\u2726  \u2726  \u2726")

    # ── Public domain notice ─────────────────────────────────
    y -= 0.55 * inch
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Times-Italic", 10.5)
    notice_lines = [
        "This work belongs to the public domain and is made available",
        "freely through the digital library of the Oratory of the Sacred Hearts.",
        "You are welcome to read, share, and redistribute this text.",
    ]
    for line in notice_lines:
        c.drawCentredString(cx, y, line)
        y -= 0.2 * inch

    # ── Donation appeal ──────────────────────────────────────
    y -= 0.45 * inch
    c.setFillColorRGB(BURG_R, BURG_G, BURG_B)
    c.setFont("Times-Bold", 12)
    c.drawCentredString(cx, y, "A Gift for a Gift")

    y -= 0.35 * inch
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Times-Roman", 10.5)
    appeal_lines = [
        "This book has been digitised and preserved for future generations",
        "by the work of our small community. If it has enriched your study",
        "or prayer, would you consider supporting our mission with a donation?",
        "Every gift, however small, helps us keep this library open to all.",
    ]
    for line in appeal_lines:
        c.drawCentredString(cx, y, line)
        y -= 0.2 * inch

    if donate_url:
        y -= 0.2 * inch
        c.setFont("Times-Bold", 11)
        c.setFillColorRGB(BURG_R, BURG_G, BURG_B)
        c.drawCentredString(cx, y, donate_url)

    # ── Bottom decorative rule ───────────────────────────────
    c.setStrokeColorRGB(BURG_R, BURG_G, BURG_B)
    c.setLineWidth(0.75)
    c.line(1.8 * inch, 1.15 * inch, width - 1.8 * inch, 1.15 * inch)

    # ── Footer ───────────────────────────────────────────────
    if logo_path:
        footer_logo_w = 0.4 * inch
        footer_logo_h = 0.2 * inch
        c.drawImage(
            logo_path,
            (width - footer_logo_w) / 2,
            0.82 * inch,
            width=footer_logo_w,
            height=footer_logo_h,
            preserveAspectRatio=True,
            anchor="c",
            mask="auto",
        )
        c.setFillColorRGB(WARM_R, WARM_G, WARM_B)
        c.setFont("Times-Roman", 7.5)
        c.drawCentredString(cx, 0.68 * inch, LIBRARY_NAME)
    else:
        c.setFillColorRGB(WARM_R, WARM_G, WARM_B)
        c.setFont("Times-Roman", 8)
        c.drawCentredString(cx, 0.85 * inch, LIBRARY_NAME)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def _build_watermark_overlay(page, text):
    """Build a single-page PDF with a two-line footer watermark sized to match the given page.

    Uses Cormorant Garamond to match the library's header typeface.
    Returns a BytesIO.
    """
    _register_cormorant_font()

    # Get the page dimensions from the mediabox
    media_box = page.mediabox
    page_width = float(media_box[2]) - float(media_box[0])
    page_height = float(media_box[3]) - float(media_box[1])

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))

    # Choose font — fall back to Times-Roman if Cormorant unavailable
    font_name = "CormorantGaramond"
    try:
        c.setFont(font_name, 7)
    except KeyError:
        font_name = "Times-Roman"

    # Split into two lines: patron/due info on line 1, library name on line 2
    # Parse the text which is formatted as:
    #   "Loaned to Name — Due Date — Bibliotheca ..."
    em_dash = "\u2014"
    parts = text.split(em_dash)
    if len(parts) >= 3:
        line1 = f"{parts[0].strip()} {em_dash} {parts[1].strip()}"
        line2 = parts[2].strip()
    else:
        # Fallback: split roughly in half
        mid = len(text) // 2
        space = text.find(" ", mid)
        if space == -1:
            space = mid
        line1 = text[:space]
        line2 = text[space:].strip()

    font_size = 7
    # Check if lines fit within page width (with margin); reduce if needed
    margin = 40
    available = page_width - margin * 2
    while font_size > 5:
        w1 = c.stringWidth(line1, font_name, font_size)
        w2 = c.stringWidth(line2, font_name, font_size)
        if max(w1, w2) <= available:
            break
        font_size -= 0.5

    # Draw two-line footer watermark
    c.setFillColorRGB(0.45, 0.45, 0.45)
    c.setFont(font_name, font_size)
    line_spacing = font_size + 2
    c.drawCentredString(page_width / 2, 10 + line_spacing, line1)
    c.drawCentredString(page_width / 2, 10, line2)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def _format_date(loan_date=None):
    """Format the current date for the loan slip."""
    from datetime import datetime

    now = loan_date or datetime.now(UTC)
    return now.strftime("%B %d, %Y")
