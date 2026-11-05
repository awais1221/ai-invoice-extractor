"""Generate fake sample invoices in ./samples for trying out the extractor.

All companies, people, addresses and numbers are made up.

    python scripts/make_samples.py

Creates:
  invoice_brightwave_studio.pdf        - US layout, USD, correct totals
  invoice_nordlicht_supplies.pdf       - European layout, EUR (1.234,50 format), correct totals
  invoice_pixelforge_wrong_totals.pdf  - GBP, line items DON'T add up to the subtotal (flagged)
  invoice_greenleaf_catering_scanned.pdf - image-only "scanned" PDF (tests the vision path)
  invoice_greenleaf_catering.png       - same invoice as a PNG image
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

OUT_DIR = Path(__file__).resolve().parent.parent / "samples"
A4 = pymupdf.paper_rect("a4")
MARGIN = 40


def money_us(v: float) -> str:
    return f"{v:,.2f}"


def money_eu(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def rows_html(items, fmt, cur="") -> str:
    return "".join(
        f"<tr><td>{d}</td><td class='r'>{q:g}</td><td class='r'>{cur}{fmt(p)}</td>"
        f"<td class='r'>{cur}{fmt(q * p)}</td></tr>"
        for d, q, p in items
    )


def write_pdf(path: Path, html: str, css: str) -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page(width=A4.width, height=A4.height)
    rect = pymupdf.Rect(MARGIN, MARGIN, A4.width - MARGIN, A4.height - MARGIN)
    page.insert_htmlbox(rect, html, css=css)
    doc.save(path)
    return doc


# --------------------------------------------------------------------------- #
# 1. BrightWave Studio - classic US invoice, correct totals
# --------------------------------------------------------------------------- #
def brightwave() -> None:
    items = [
        ("Logo design &amp; concepts", 1, 850.00),
        ("Website mockups (per page)", 3, 420.00),
        ("Brand guidelines document", 1, 600.00),
    ]
    subtotal = sum(q * p for _, q, p in items)          # 2,710.00
    tax = round(subtotal * 0.08, 2)                      # 216.80
    total = subtotal + tax                               # 2,926.80
    css = """
      * { font-family: sans-serif; }
      h1 { color: #1f4e78; font-size: 26px; margin: 0; }
      .muted { color: #666; font-size: 10px; }
      table { width: 100%; border-collapse: collapse; font-size: 11px; }
      th { background: #1f4e78; color: white; padding: 6px; text-align: left; }
      td { padding: 6px; border-bottom: 1px solid #ddd; }
      .r { text-align: right; }
      .tot td { border: none; }
      .grand td { font-weight: bold; font-size: 13px; border-top: 2px solid #1f4e78; }
    """
    html = f"""
      <h1>BrightWave Studio</h1>
      <p class="muted">Creative Design Agency · 742 Maple Ridge Ave, Springfield, OR 97477 · hello@brightwave.example</p>
      <h2>INVOICE</h2>
      <table style="font-size:11px">
        <tr><td><b>Invoice No:</b> BWS-2026-0142</td><td><b>Bill To:</b> Harbor Lane Coffee Co.</td></tr>
        <tr><td><b>Invoice Date:</b> March 3, 2026</td><td>18 Pier Street, Portland, OR</td></tr>
        <tr><td><b>Due Date:</b> April 2, 2026</td><td>Attn: Dana Whitfield</td></tr>
      </table>
      <br>
      <table>
        <tr><th>Description</th><th class="r">Qty</th><th class="r">Unit Price</th><th class="r">Amount</th></tr>
        {rows_html(items, money_us, "$")}
        <tr class="tot"><td></td><td></td><td class="r">Subtotal</td><td class="r">${money_us(subtotal)}</td></tr>
        <tr class="tot"><td></td><td></td><td class="r">Sales Tax (8%)</td><td class="r">${money_us(tax)}</td></tr>
        <tr class="grand"><td></td><td></td><td class="r">TOTAL DUE (USD)</td><td class="r">${money_us(total)}</td></tr>
      </table>
      <p class="muted">Payment terms: Net 30. Thank you for your business!</p>
    """
    write_pdf(OUT_DIR / "invoice_brightwave_studio.pdf", html, css)


# --------------------------------------------------------------------------- #
# 2. Nordlicht Office Supplies - European layout, EUR, correct totals
# --------------------------------------------------------------------------- #
def nordlicht() -> None:
    items = [
        ("Copy paper A4, 80 g (box of 5 reams)", 12, 24.90),
        ("Toner cartridge TX-500, black", 4, 89.50),
        ("Ergonomic office chair 'Fjord'", 2, 249.00),
    ]
    net = sum(q * p for _, q, p in items)                # 1.154,80
    vat = round(net * 0.19, 2)                           # 219,41
    gross = round(net + vat, 2)                          # 1.374,21
    css = """
      * { font-family: serif; }
      .head { text-align: right; }
      .logo { font-size: 22px; font-weight: bold; color: #2e7d32; }
      table { width: 100%; border-collapse: collapse; font-size: 11px; }
      th { border-bottom: 2px solid #2e7d32; padding: 5px; text-align: left; }
      td { padding: 5px; }
      .r { text-align: right; }
      .box { border: 1px solid #2e7d32; padding: 8px; font-size: 11px; }
      .small { font-size: 9px; color: #555; }
    """
    html = f"""
      <div class="head">
        <div class="logo">Nordlicht Office Supplies GmbH</div>
        <div class="small">Lindenweg 27 · 20095 Hamburg · Germany · VAT ID DE000000000</div>
      </div>
      <p class="small">Fernwald Architects BV · Kanaalstraat 9 · 3511 Utrecht · Netherlands</p>
      <table class="box">
        <tr><td><b>Invoice number</b></td><td>NL-48817</td>
            <td><b>Invoice date</b></td><td>14.02.2026</td></tr>
        <tr><td><b>Customer no.</b></td><td>K-2291</td>
            <td><b>Payment due</b></td><td>16.03.2026</td></tr>
      </table>
      <br>
      <table>
        <tr><th>Item</th><th class="r">Quantity</th><th class="r">Unit price (EUR)</th><th class="r">Amount (EUR)</th></tr>
        {rows_html(items, money_eu)}
      </table>
      <br>
      <table>
        <tr><td style="width:60%"></td><td>Net amount</td><td class="r">{money_eu(net)} €</td></tr>
        <tr><td></td><td>VAT 19%</td><td class="r">{money_eu(vat)} €</td></tr>
        <tr><td></td><td><b>Total amount</b></td><td class="r"><b>{money_eu(gross)} €</b></td></tr>
      </table>
      <p class="small">Please transfer the total amount to IBAN DE00 0000 0000 0000 0000 00 quoting the invoice number.</p>
    """
    write_pdf(OUT_DIR / "invoice_nordlicht_supplies.pdf", html, css)


# --------------------------------------------------------------------------- #
# 3. PixelForge Labs - GBP, deliberately WRONG subtotal
# --------------------------------------------------------------------------- #
def pixelforge() -> None:
    items = [
        ("Mobile app UI audit", 1, 1200.00),
        ("Usability testing session", 2, 350.00),
        ("Accessibility report", 1, 450.00),
    ]
    # Real line sum is 2,350.00, but the invoice prints 2,450.00 (a typo
    # the validator should catch). Tax and total are computed from the wrong
    # subtotal, so only the line-items check fails.
    wrong_subtotal = 2450.00
    vat = round(wrong_subtotal * 0.20, 2)                # 490.00
    total = wrong_subtotal + vat                         # 2,940.00
    css = """
      * { font-family: sans-serif; }
      .bar { background: #6a1b9a; color: white; padding: 14px; font-size: 20px; font-weight: bold; }
      table { width: 100%; border-collapse: collapse; font-size: 11px; }
      td, th { padding: 6px; }
      th { background: #f3e5f5; text-align: left; }
      .r { text-align: right; }
      .meta td { font-size: 10px; }
      .sum td { font-size: 12px; }
    """
    html = f"""
      <div class="bar">PixelForge Labs Ltd. — TAX INVOICE</div>
      <table class="meta">
        <tr><td>Unit 4, Copperfield Works<br>Leeds LS1 0AA, United Kingdom</td>
            <td class="r">Invoice #: PFL-7781<br>Date: 21 Jan 2026<br>Due: 20 Feb 2026</td></tr>
        <tr><td colspan="2"><b>Billed to:</b> Silverbirch Health Ltd, 55 Orchard Row, York</td></tr>
      </table>
      <br>
      <table>
        <tr><th>Service</th><th class="r">Qty</th><th class="r">Rate</th><th class="r">Line total</th></tr>
        {rows_html(items, money_us, "£")}
      </table>
      <br>
      <table class="sum">
        <tr><td style="width:60%"></td><td>Subtotal</td><td class="r">£{money_us(wrong_subtotal)}</td></tr>
        <tr><td></td><td>VAT @ 20%</td><td class="r">£{money_us(vat)}</td></tr>
        <tr><td></td><td><b>Amount due (GBP)</b></td><td class="r"><b>£{money_us(total)}</b></td></tr>
      </table>
    """
    write_pdf(OUT_DIR / "invoice_pixelforge_wrong_totals.pdf", html, css)


# --------------------------------------------------------------------------- #
# 4. Greenleaf Catering - "scanned" (image-only) PDF + PNG, correct totals
# --------------------------------------------------------------------------- #
def greenleaf() -> None:
    items = [
        ("Lunch buffet (per person)", 25, 18.50),
        ("Coffee &amp; tea service (per person)", 25, 3.20),
        ("Delivery and setup", 1, 45.00),
    ]
    subtotal = sum(q * p for _, q, p in items)           # 587.50
    tax = round(subtotal * 0.06, 2)                      # 35.25
    total = subtotal + tax                               # 622.75
    css = """
      * { font-family: monospace; }
      h1 { font-size: 22px; text-align: center; margin: 0; }
      .c { text-align: center; font-size: 11px; }
      table { width: 100%; border-collapse: collapse; font-size: 12px; }
      td, th { padding: 5px; border: 1px solid #333; }
      .r { text-align: right; }
    """
    html = f"""
      <h1>GREENLEAF CATERING CO.</h1>
      <p class="c">90 Willow Bend Road, Asheville, NC 28801 · (555) 010-4477</p>
      <p><b>INVOICE</b> No. GC-0593<br>Date: 2026-04-09<br>Due date: 2026-04-23<br>
         Customer: Bluepeak Software Inc. — Team offsite</p>
      <table>
        <tr><th>Item</th><th>Qty</th><th>Price</th><th>Total</th></tr>
        {rows_html(items, money_us, "$")}
        <tr><td colspan="3" class="r">Subtotal</td><td class="r">${money_us(subtotal)}</td></tr>
        <tr><td colspan="3" class="r">Tax 6%</td><td class="r">${money_us(tax)}</td></tr>
        <tr><td colspan="3" class="r"><b>TOTAL</b></td><td class="r"><b>${money_us(total)}</b></td></tr>
      </table>
      <p class="c">All amounts in USD. Thank you!</p>
    """
    # Render the text invoice to an image, then build a PDF containing only
    # that image, just like a scanned paper invoice (no text layer).
    tmp = pymupdf.open()
    page = tmp.new_page(width=A4.width, height=A4.height)
    page.insert_htmlbox(pymupdf.Rect(MARGIN, MARGIN, A4.width - MARGIN, A4.height - MARGIN),
                        html, css=css)
    pix = page.get_pixmap(dpi=130)
    pix.save(OUT_DIR / "invoice_greenleaf_catering.png")

    scanned = pymupdf.open()
    scan_page = scanned.new_page(width=A4.width, height=A4.height)
    scan_page.insert_image(scan_page.rect, stream=pix.tobytes("png"))
    scanned.save(OUT_DIR / "invoice_greenleaf_catering_scanned.pdf", deflate=True)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for build in (brightwave, nordlicht, pixelforge, greenleaf):
        build()
    for path in sorted(OUT_DIR.iterdir()):
        print(f"created {path.relative_to(OUT_DIR.parent)} ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
