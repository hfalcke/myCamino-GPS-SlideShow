#!/usr/bin/env python3
"""Create the credit-card-sized myCamino Camino flyer."""

from io import BytesIO
from pathlib import Path

from PIL import Image
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "mycamino-camino-flyer.pdf"
SHEET_OUTPUT = ROOT / "output" / "pdf" / "mycamino-camino-flyer-a4-10up.pdf"
LOGO = ROOT / "website" / "siteapp" / "static" / "siteapp" / "images" / "mycamino-logo.png"
URL = "https://mycamino.heinofalcke.de/"

CARD_WIDTH = 85.6 * mm
CARD_HEIGHT = 54 * mm

FOREST = HexColor("#213a31")
FOREST_DARK = HexColor("#14251f")
CREAM = HexColor("#fbf5e8")
GOLD = HexColor("#d89b42")
INK = HexColor("#17201d")
MUTED = HexColor("#52625b")


def colored_logo():
    image = Image.open(LOGO).convert("RGBA")
    alpha = image.getchannel("A")
    colored = Image.new("RGBA", image.size, (33, 58, 49, 255))
    colored.putalpha(alpha)
    buffer = BytesIO()
    colored.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


def draw_qr(pdf, x, y, size):
    quiet = 1.7 * mm
    pdf.setFillColor(white)
    pdf.roundRect(x - quiet, y - quiet, size + 2 * quiet, size + 2 * quiet, 1.5 * mm, fill=1, stroke=0)

    qr = QrCodeWidget(URL, barLevel="M")
    left, bottom, right, top = qr.getBounds()
    qr_width = right - left
    qr_height = top - bottom
    drawing = Drawing(
        size,
        size,
        transform=[size / qr_width, 0, 0, size / qr_height, -left * size / qr_width, -bottom * size / qr_height],
    )
    drawing.add(qr)
    pdf.saveState()
    renderPDF.draw(drawing, pdf, x, y)
    pdf.restoreState()


def bullet(pdf, x, y, title, detail):
    pdf.setFillColor(GOLD)
    pdf.circle(x + 1.1 * mm, y + 1.15 * mm, 0.9 * mm, fill=1, stroke=0)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 7.2)
    pdf.drawString(x + 4 * mm, y, title)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 5.7)
    pdf.drawString(x + 4 * mm, y - 2.55 * mm, detail)


def draw_card(pdf, x=0, y=0):
    """Draw one exact 85.6 x 54 mm flyer at the given lower-left position."""
    pdf.saveState()
    pdf.translate(x, y)
    pdf.setFillColor(CREAM)
    pdf.rect(0, 0, CARD_WIDTH, CARD_HEIGHT, fill=1, stroke=0)

    # A subtle Camino-colored route sweeps behind the content.
    pdf.setStrokeColor(Color(0.84, 0.60, 0.26, alpha=0.18))
    pdf.setLineWidth(1.1)
    route = pdf.beginPath()
    route.moveTo(0, 13 * mm)
    route.curveTo(18 * mm, 24 * mm, 31 * mm, 9 * mm, 51 * mm, 20 * mm)
    route.curveTo(64 * mm, 27 * mm, 72 * mm, 24 * mm, CARD_WIDTH, 34 * mm)
    pdf.drawPath(route, stroke=1, fill=0)

    # Brand block.
    logo_size = 12.5 * mm
    pdf.drawImage(colored_logo(), 4.2 * mm, 38.1 * mm, logo_size, logo_size, preserveAspectRatio=True, mask="auto")
    pdf.setFillColor(FOREST_DARK)
    pdf.setFont("Times-Bold", 16)
    pdf.drawString(17.2 * mm, 45.2 * mm, "myCamino")
    subtitle = pdf.beginText(17.5 * mm, 41.7 * mm)
    subtitle.setFont("Helvetica-Bold", 5.8)
    subtitle.textLine("GPS SLIDESHOW")
    pdf.drawText(subtitle)

    pdf.setFillColor(GOLD)
    pdf.setFont("Helvetica-Bold", 6.3)
    pdf.drawString(4.8 * mm, 35.7 * mm, "RELIVE EVERY STAGE.")

    bullet(pdf, 4.8 * mm, 30.4 * mm, "Powerful slide shows", "Entirely free and open source")
    bullet(pdf, 4.8 * mm, 23.2 * mm, "GPS-aware", "Your pictures know where they belong")
    bullet(pdf, 4.8 * mm, 16.0 * mm, "Pictures, maps & tracks", "See the whole journey together")

    # QR block on the right.
    qr_size = 24.5 * mm
    qr_x = 58.0 * mm
    qr_y = 14.3 * mm
    pdf.setFillColor(FOREST)
    label = pdf.beginText(59.0 * mm, 44.6 * mm)
    label.setFont("Helvetica-Bold", 5.5)
    label.textLine("SCAN TO EXPLORE")
    pdf.drawText(label)
    draw_qr(pdf, qr_x, qr_y, qr_size)
    pdf.setFillColor(FOREST_DARK)
    pdf.setFont("Helvetica-Bold", 4.6)
    pdf.drawCentredString(qr_x + qr_size / 2, 10.5 * mm, "mycamino.heinofalcke.de")

    # Footer keeps the platform limitation visible without competing with the pitch.
    pdf.setFillColor(FOREST_DARK)
    pdf.rect(0, 0, CARD_WIDTH, 7.2 * mm, fill=1, stroke=0)
    pdf.setFillColor(white)
    pdf.setFont("Helvetica", 5.5)
    pdf.drawString(4.8 * mm, 2.65 * mm, "Currently macOS only.")
    pdf.setFillColor(HexColor("#dbe6df"))
    pdf.setFont("Helvetica", 4.8)
    pdf.drawRightString(CARD_WIDTH - 4.8 * mm, 2.65 * mm, "For pilgrims. By a pilgrim.")
    pdf.restoreState()


def set_metadata(pdf, title):
    pdf.setTitle(title)
    pdf.setAuthor("Heino Falcke")
    pdf.setSubject("myCamino GPS SlideShow Camino flyer")


def build_card():
    pdf = canvas.Canvas(str(OUTPUT), pagesize=(CARD_WIDTH, CARD_HEIGHT), pageCompression=1)
    set_metadata(pdf, "myCamino GPS SlideShow - Camino flyer")
    draw_card(pdf)
    pdf.showPage()
    pdf.save()


def build_a4_sheet():
    """Place the maximum ten credit-card flyers on one portrait A4 sheet."""
    page_width, page_height = A4
    columns = 2
    rows = 5
    left = (page_width - columns * CARD_WIDTH) / 2
    bottom = (page_height - rows * CARD_HEIGHT) / 2

    pdf = canvas.Canvas(str(SHEET_OUTPUT), pagesize=A4, pageCompression=1)
    set_metadata(pdf, "myCamino GPS SlideShow - A4 sheet with 10 Camino flyers")
    for row in range(rows):
        for column in range(columns):
            draw_card(pdf, left + column * CARD_WIDTH, bottom + row * CARD_HEIGHT)

    # Hairline trim boundaries and short crop marks make straight cuts easy.
    pdf.saveState()
    pdf.setStrokeColor(HexColor("#9a9a9a"))
    pdf.setLineWidth(0.25)
    right = left + columns * CARD_WIDTH
    top = bottom + rows * CARD_HEIGHT
    for column in range(columns + 1):
        cut_x = left + column * CARD_WIDTH
        pdf.line(cut_x, bottom - 4 * mm, cut_x, top + 4 * mm)
    for row in range(rows + 1):
        cut_y = bottom + row * CARD_HEIGHT
        pdf.line(left - 4 * mm, cut_y, right + 4 * mm, cut_y)
    pdf.restoreState()

    pdf.showPage()
    pdf.save()


def build():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    build_card()
    build_a4_sheet()
    print(OUTPUT)
    print(SHEET_OUTPUT)


if __name__ == "__main__":
    build()
