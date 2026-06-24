from flask import Flask, request, send_file, render_template_string, abort
from io import BytesIO
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
import os, re, html, uuid, tempfile, requests

app = Flask(__name__)

SOFTR_API_KEY = os.environ.get("SOFTR_API_KEY")
SOFTR_DATABASE_ID = os.environ.get("SOFTR_DATABASE_ID")
SOFTR_TABLE_ID = os.environ.get("SOFTR_TABLE_ID")
SOFTR_WWS_INPUT_FIELD = os.environ.get("SOFTR_WWS_INPUT_FIELD")
SOFTR_WWS_OUTPUT_FIELD = os.environ.get("SOFTR_WWS_OUTPUT_FIELD")
SOFTR_WWS_POINTS_FIELD = os.environ.get("SOFTR_WWS_POINTS_FIELD")
SOFTR_WWS_RENT_FIELD = os.environ.get("SOFTR_WWS_RENT_FIELD")
SOFTR_ADVISEUR_FIELD = os.environ.get("SOFTR_ADVISEUR_FIELD", "Adviseur")
SOFTR_OPNAMEDATUM_FIELD = os.environ.get("SOFTR_OPNAMEDATUM_FIELD", "Opnamedatum")
SOFTR_ADRES_FIELD = os.environ.get("SOFTR_ADRES_FIELD", "Adres")
SOFTR_DOSSIER_FIELD = os.environ.get("SOFTR_DOSSIER_FIELD", "Dossiernummer")
BASE_URL = os.environ.get("BASE_URL", "https://photo-api-0iur.onrender.com")
TEMP_DIR = tempfile.gettempdir()
OUTPUT_PDF_NAME = "wws_rapport_energievakman.pdf"
LOGO_URL = os.environ.get("LOGO_URL")

HTML = """
<!doctype html><html><head><title>WWS rapport generator</title><style>
body{font-family:Arial,sans-serif;max-width:820px;margin:40px auto}button{padding:10px 16px;cursor:pointer}.dropzone{border:2px dashed #b8b8b8;border-radius:12px;padding:28px;text-align:center;background:#fafafa;margin:18px 0 22px 0;max-width:620px}.dropzone.dragover{border-color:#1a73e8;background:#eef5ff}.dropzone-title{display:block;font-size:17px;font-weight:700;margin-bottom:8px}.dropzone-sub{display:block;color:#666;font-size:14px;margin-bottom:16px}.file-name{margin-top:12px;color:#333;font-weight:600}.form-row{margin:16px 0}input[type=text]{padding:7px;width:280px}label{display:block;font-weight:700;margin-bottom:5px}.hint{color:#666;font-size:14px;line-height:1.45;max-width:640px}
</style></head><body><h1>WWS rapport generator</h1><p class="hint">Upload de originele Huurcommissie-puntentelling. De app vervangt pagina 1 door een Energievakman-voorblad en behoudt pagina 2 en verder.</p>
<form action="/process-wws" method="post" enctype="multipart/form-data"><div class="dropzone" id="dropzone"><span class="dropzone-title">Sleep de WWS PDF hierheen</span><span class="dropzone-sub">of kies handmatig een PDF</span><input id="fileInput" type="file" name="pdf" accept="application/pdf,.pdf"><div id="fileName" class="file-name">Geen bestand geselecteerd</div></div><div class="form-row"><label>Adviseur</label><input type="text" name="adviseur" placeholder="Bijv. O. Boender"></div><div class="form-row"><label>Opnamedatum</label><input type="text" name="opnamedatum" placeholder="Bijv. 2 juni 2025"></div><button type="submit">Maak definitieve PDF</button></form>
<script>
const dz=document.getElementById('dropzone'),fi=document.getElementById('fileInput'),fn=document.getElementById('fileName');function upd(){fn.innerText=(!fi.files||fi.files.length===0)?'Geen bestand geselecteerd':fi.files[0].name}fi.addEventListener('change',upd);dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('dragover')});dz.addEventListener('dragleave',()=>dz.classList.remove('dragover'));dz.addEventListener('drop',e=>{e.preventDefault();dz.classList.remove('dragover');fi.files=e.dataTransfer.files;upd()});
</script></body></html>
"""

UPLOAD_FOR_RECORD_HTML = """
<!doctype html><html><head><title>WWS rapport voor record</title><style>
body{font-family:Arial,sans-serif;max-width:820px;margin:40px auto}button{padding:10px 16px;cursor:pointer}.dropzone{border:2px dashed #b8b8b8;border-radius:12px;padding:28px;text-align:center;background:#fafafa;margin:18px 0 22px 0;max-width:620px}.dropzone.dragover{border-color:#1a73e8;background:#eef5ff}.dropzone-title{display:block;font-size:17px;font-weight:700;margin-bottom:8px}.dropzone-sub{display:block;color:#666;font-size:14px;margin-bottom:16px}.file-name{margin-top:12px;color:#333;font-weight:600}.hint{color:#666;font-size:14px;line-height:1.45;max-width:640px}
</style></head><body><h1>WWS rapport maken</h1><p class="hint">Upload de originele WWS PDF. Adviseur, opnamedatum en adres worden waar mogelijk uit het Softr-record gehaald.</p>
<form action="/wws-upload-for-record/{{record_id}}" method="post" enctype="multipart/form-data"><div class="dropzone" id="dropzone"><span class="dropzone-title">Sleep de WWS PDF hierheen</span><span class="dropzone-sub">of kies handmatig een PDF</span><input id="fileInput" type="file" name="pdf" accept="application/pdf,.pdf"><div id="fileName" class="file-name">Geen bestand geselecteerd</div></div><button id="submitBtn" type="submit">Maak WWS rapport</button><p id="loadingMessage" style="display:none;color:#0a66c2;font-weight:bold;margin-top:15px;">⏳ Even geduld a.u.b. Het WWS rapport wordt gemaakt...</p></form>
<script>
const dz=document.getElementById('dropzone'),fi=document.getElementById('fileInput'),fn=document.getElementById('fileName');function upd(){fn.innerText=(!fi.files||fi.files.length===0)?'Geen bestand geselecteerd':fi.files[0].name}fi.addEventListener('change',upd);dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('dragover')});dz.addEventListener('dragleave',()=>dz.classList.remove('dragover'));dz.addEventListener('drop',e=>{e.preventDefault();dz.classList.remove('dragover');fi.files=e.dataTransfer.files;upd()});document.querySelector('form').addEventListener('submit',()=>{document.getElementById('submitBtn').disabled=true;document.getElementById('submitBtn').innerText='Bezig...';document.getElementById('loadingMessage').style.display='block'});
</script></body></html>
"""

RESULT_HTML = """
<!doctype html><html><head><title>WWS rapport gemaakt</title><style>body{font-family:Arial,sans-serif;max-width:820px;margin:40px auto}.btn{display:inline-block;padding:12px 18px;background:#1a73e8;color:white;text-decoration:none;border-radius:6px;font-weight:700}.okbox{padding:12px 14px;background:#eef8f0;border:1px solid #b8dfc0;margin:18px 0}.warnbox{padding:12px 14px;background:#fff6e5;border:1px solid #ffd58a;margin:18px 0}pre{white-space:pre-wrap;background:#f7f7f7;padding:12px;border-radius:6px}</style></head><body><h1>WWS rapport gemaakt</h1>{{message|safe}}<a class="btn" href="/download/{{download_id}}">Download WWS rapport</a><h3>Gevonden gegevens</h3><pre>{{summary}}</pre></body></html>
"""

def save_temp_pdf(pdf_bytes):
    download_id = uuid.uuid4().hex
    path = os.path.join(TEMP_DIR, f"{download_id}.pdf")
    with open(path, "wb") as f:
        f.write(pdf_bytes)
    return download_id

def absolute_download_url(download_id):
    # URL eindigt bewust op .pdf zodat Softr het als PDF-bestand kan herkennen.
    return BASE_URL.rstrip("/") + f"/download/{download_id}/wws_rapport_energievakman.pdf"

def first_value(record_fields, field_name_or_id):
    value = record_fields.get(field_name_or_id)
    if isinstance(value, list):
        if not value: return ""
        first = value[0]
        if isinstance(first, dict): return first.get("name") or first.get("filename") or first.get("url") or ""
        return str(first)
    if isinstance(value, dict): return value.get("name") or value.get("filename") or value.get("url") or ""
    return "" if value is None else str(value)

def extract_urls(value):
    urls = []
    if not value: return urls
    if isinstance(value, str):
        if value.startswith("http"): urls.append({"url": value, "name": ""})
        return urls
    if isinstance(value, list):
        for item in value: urls.extend(extract_urls(item))
        return urls
    if isinstance(value, dict):
        url = value.get("url") or value.get("fileUrl") or value.get("downloadUrl") or value.get("signedUrl")
        name = value.get("name") or value.get("filename") or value.get("fileName") or ""
        if isinstance(url, str) and url.startswith("http"):
            urls.append({"url": url, "name": name})
        else:
            for subvalue in value.values():
                if isinstance(subvalue, (dict, list, str)): urls.extend(extract_urls(subvalue))
    return urls

def download_url_to_bytes(url):
    response = requests.get(url, headers={"User-Agent": "EnergievakmanWWSGenerator/1.0"}, timeout=90)
    response.raise_for_status()
    return response.content

def get_softr_record(record_id):
    if not SOFTR_API_KEY or not SOFTR_DATABASE_ID or not SOFTR_TABLE_ID:
        raise RuntimeError("Softr environment variables ontbreken")

    base_url = f"https://tables-api.softr.io/api/v1/databases/{SOFTR_DATABASE_ID}/tables/{SOFTR_TABLE_ID}/records/{record_id}"
    headers = {"Softr-Api-Key": SOFTR_API_KEY}

    # 1) normale response met field IDs
    response = requests.get(base_url, headers=headers, timeout=30)
    response.raise_for_status()
    record = response.json()["data"]

    # 2) extra response met veldnamen, zodat env vars zoals "Adviseur" ook werken
    try:
        response_names = requests.get(base_url + "?fieldNames=true", headers=headers, timeout=30)
        if response_names.status_code in (200, 201):
            record_names = response_names.json()["data"]
            merged_fields = {}
            merged_fields.update(record.get("fields", {}))
            merged_fields.update(record_names.get("fields", {}))
            record["fields"] = merged_fields
    except Exception:
        pass

    return record


def urls_from_softr_record(record, field_name_or_id):
    fields = record.get("fields", {})
    value = fields.get(field_name_or_id)
    if value is None:
        raise RuntimeError(f"Veld '{field_name_or_id}' niet gevonden. Beschikbare fields: {', '.join(fields.keys())}")
    return extract_urls(value)

def update_softr_record_with_pdf_url(record_id, file_url, data=None):
    """
    Update apart:
    1. eerst PDF file-field
    2. daarna punten + maximale huurprijs

    Daardoor kan de PDF niet stilletjes misgaan terwijl punten/huurprijs wel worden bijgewerkt.
    """
    if not SOFTR_API_KEY or not SOFTR_DATABASE_ID or not SOFTR_TABLE_ID:
        return False, "Softr API gegevens ontbreken in Render."

    data = data or {}

    base_url = f"https://tables-api.softr.io/api/v1/databases/{SOFTR_DATABASE_ID}/tables/{SOFTR_TABLE_ID}/records/{record_id}"
    headers = {"Softr-Api-Key": SOFTR_API_KEY, "Content-Type": "application/json"}

    urls_to_try = [
        base_url,
        base_url + "?fieldNames=true",
    ]

    messages = []
    pdf_ok = False
    fields_ok = False

    # 1) PDF apart proberen
    if SOFTR_WWS_OUTPUT_FIELD:
        file_values = [
            [{"url": file_url, "filename": OUTPUT_PDF_NAME}],
            [{"url": file_url, "name": OUTPUT_PDF_NAME}],
            [{"url": file_url, "fileName": OUTPUT_PDF_NAME}],
            [{"url": file_url, "filename": OUTPUT_PDF_NAME, "type": "application/pdf"}],
            [{"url": file_url, "name": OUTPUT_PDF_NAME, "type": "application/pdf"}],
            [{"fileUrl": file_url, "filename": OUTPUT_PDF_NAME}],
            [{"fileUrl": file_url, "name": OUTPUT_PDF_NAME}],
            [{"fileUrl": file_url, "fileName": OUTPUT_PDF_NAME}],
            [{"fileUrl": file_url, "filename": OUTPUT_PDF_NAME, "type": "application/pdf"}],
            file_url,
        ]

        last_pdf_errors = []

        for patch_url in urls_to_try:
            for file_value in file_values:
                payload = {"fields": {SOFTR_WWS_OUTPUT_FIELD: file_value}}
                try:
                    r = requests.patch(patch_url, headers=headers, json=payload, timeout=30)
                    if r.status_code in (200, 201):
                        pdf_ok = True
                        break
                    last_pdf_errors.append(f"{r.status_code}: {r.text[:350]}")
                except Exception as e:
                    last_pdf_errors.append(str(e))
            if pdf_ok:
                break

        if pdf_ok:
            messages.append("PDF gekoppeld")
        else:
            messages.append("PDF niet gekoppeld: " + " | ".join(last_pdf_errors[-2:]))
    else:
        messages.append("SOFTR_WWS_OUTPUT_FIELD ontbreekt; PDF niet gekoppeld")

    # 2) Punten en huurprijs apart updaten
    extra_fields = {}

    if SOFTR_WWS_POINTS_FIELD:
        extra_fields[SOFTR_WWS_POINTS_FIELD] = data.get("totaal_punten", "")

    if SOFTR_WWS_RENT_FIELD:
        extra_fields[SOFTR_WWS_RENT_FIELD] = data.get("maximale_huurprijs", "")

    if extra_fields:
        last_field_errors = []
        for patch_url in urls_to_try:
            try:
                r = requests.patch(patch_url, headers=headers, json={"fields": extra_fields}, timeout=30)
                if r.status_code in (200, 201):
                    fields_ok = True
                    break
                last_field_errors.append(f"{r.status_code}: {r.text[:350]}")
            except Exception as e:
                last_field_errors.append(str(e))

        if fields_ok:
            messages.append("punten/huurprijs bijgewerkt")
        else:
            messages.append("punten/huurprijs niet bijgewerkt: " + " | ".join(last_field_errors[-2:]))
    else:
        fields_ok = True
        messages.append("geen punten/huurprijsvelden ingesteld")

    if pdf_ok:
        return True, "WWS rapport verwerkt: " + "; ".join(messages)

    return False, "WWS rapport gemaakt, maar PDF niet gekoppeld. " + "; ".join(messages)
def pdf_text_first_page(pdf_bytes):
    reader = PdfReader(BytesIO(pdf_bytes))
    if len(reader.pages) == 0: raise RuntimeError("PDF bevat geen pagina's")
    return reader.pages[0].extract_text() or ""

def normalize_pdf_text(text):
    return text.replace("Huurpr!s", "Huurprijs").replace("pr!s", "prijs").replace("T!dvak", "Tijdvak")

def regex_find(text, pattern, fallback=""):
    m = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else fallback


def clean_money(value):
    value = safe_text(value, "")
    return value.replace(" ", "").strip()


def clean_points(value):
    value = safe_text(value, "")
    return value.replace(" ", "").strip()


def parse_number(value):
    value = safe_text(value, "").replace(".", "").replace(",", ".")
    try:
        return float(value)
    except Exception:
        return None


def format_dutch_number(value):
    if value is None:
        return ""
    if abs(value - round(value)) < 0.001:
        return str(int(round(value)))
    return f"{value:.2f}".replace(".", ",")


def calculate_total_points_if_missing(data):
    total = parse_number(data.get("totaal_punten"))
    if total is not None:
        return data

    parts = [
        parse_number(data.get("woning_punten")),
        parse_number(data.get("binnenruimtes_punten")),
        parse_number(data.get("buitenruimtes_punten")),
    ]
    if all(p is not None for p in parts):
        data["totaal_punten"] = format_dutch_number(sum(parts))

    return data


def extract_wws_data(pdf_bytes):
    text = normalize_pdf_text(pdf_text_first_page(pdf_bytes))
    data = {
        "type_woonruimte": regex_find(text, r"Type woonruimte\s+(.+)"),
        "adres": regex_find(text, r"Adres\s+(.+)"),
        "tijdvak": regex_find(text, r"Tijdvak\s+(.+)"),
        "datum_ingevuld": regex_find(text, r"Datum ingevuld\s+(.+)"),
        "totaal_punten": clean_points(regex_find(text, r"Totaal aantal punten\s+([\d,.]+)")),
        "maximale_huurprijs": clean_money(regex_find(text, r"Maximale huurprijs\s+(€\s*[\d.,]+)")),
        "woning_punten": clean_points(regex_find(text, r"Woning\s+([\d,.]+)")),
        "binnenruimtes_punten": clean_points(regex_find(text, r"Binnenruimtes\s+([\d,.]+)")),
        "buitenruimtes_punten": clean_points(regex_find(text, r"Buitenruimtes\s+([\d,.]+)")),
    }
    return calculate_total_points_if_missing(data)

def safe_text(value, fallback="-"):
    if value is None: return fallback
    value = str(value).strip()
    return value if value else fallback

def draw_wrapped_text(c, text, x, y, max_width, font="Helvetica", size=10, line_height=14):
    c.setFont(font, size)
    words, line, cy = safe_text(text, "").split(), "", y
    for word in words:
        test = (line + " " + word).strip()
        if stringWidth(test, font, size) <= max_width:
            line = test
        else:
            c.drawString(x, cy, line); cy -= line_height; line = word
    if line: c.drawString(x, cy, line)
    return cy


def draw_logo_or_text(c, x, y, max_width=280, max_height=70):
    """
    Gebruikt eigen logo als LOGO_URL is ingesteld.
    Fallback: tekstlogo in Courier-Bold.
    """
    if LOGO_URL:
        try:
            response = requests.get(LOGO_URL, timeout=20)
            response.raise_for_status()
            img = ImageReader(BytesIO(response.content))
            iw, ih = img.getSize()

            scale = min(max_width / iw, max_height / ih)
            draw_w = iw * scale
            draw_h = ih * scale

            c.drawImage(img, x, y - draw_h, width=draw_w, height=draw_h, preserveAspectRatio=True, mask="auto")
            return
        except Exception:
            pass

    c.setFont("Courier-Bold", 30)
    c.setFillColor(colors.black)
    c.drawString(x, y - 35, "DE ENERGIEVAKMAN")


def create_energievakman_cover(data):
    buffer = BytesIO(); c = canvas.Canvas(buffer, pagesize=A4); width, height = A4
    mx, rx = 42, 430
    # Logo/naam
    draw_logo_or_text(c, mx, height - 55, max_width=300, max_height=80)
    c.setFont("Helvetica-Bold", 10); c.drawString(rx, height-73, "Adviseur")
    c.setFont("Helvetica", 10); c.drawString(rx, height-98, safe_text(data.get("adviseur")))
    c.setFont("Helvetica-Bold", 10); c.drawString(rx, height-125, "Opnamedatum")
    c.setFont("Helvetica", 10); c.drawString(rx, height-150, safe_text(data.get("opnamedatum")))
    if data.get("dossiernummer"):
        c.setFont("Helvetica-Bold", 10); c.drawString(rx, height-177, "Dossiernummer")
        c.setFont("Helvetica", 10); c.drawString(rx, height-202, safe_text(data.get("dossiernummer")))
    c.setFont("Helvetica-Bold", 18); c.drawString(mx, height-145, "Resultaat Huurprijscheck")
    c.setFont("Helvetica-Bold", 12); c.drawString(mx, height-170, "Puntentelling volgens het Woningwaarderingsstelsel")
    c.setStrokeColor(colors.lightgrey); c.line(mx, height-205, width-mx, height-205)
    y = height-250; c.setFont("Helvetica-Bold", 18); c.drawString(mx, y, "Informatie"); y -= 35
    for label, key in [("Type woonruimte","type_woonruimte"),("Adres","adres"),("Tijdvak","tijdvak"),("Datum ingevuld","datum_ingevuld")]:
        c.setFont("Helvetica-Bold", 11); c.drawString(mx, y, label)
        c.setFont("Helvetica", 11); draw_wrapped_text(c, safe_text(data.get(key)), 210, y, 330, "Helvetica", 11, 14); y -= 28
    y -= 20; c.setFont("Helvetica-Bold", 18); c.drawString(mx, y, "Samenvatting"); y -= 35
    for label, key in [("Totaal aantal punten","totaal_punten"),("Maximale huurprijs","maximale_huurprijs")]:
        c.setFont("Helvetica-Bold", 11); c.drawString(mx, y, label); c.setFont("Helvetica", 11); c.drawString(210, y, safe_text(data.get(key))); y -= 26
    y -= 12; c.setFont("Helvetica-Bold", 12); c.drawString(mx, y, "Punten per onderdeel"); y -= 26
    for label, key in [("Woning","woning_punten"),("Binnenruimtes","binnenruimtes_punten"),("Buitenruimtes","buitenruimtes_punten")]:
        c.setFont("Helvetica-Bold", 11); c.drawString(mx+18, y, label); c.setFont("Helvetica", 11); c.drawString(210, y, safe_text(data.get(key))); y -= 24
    y -= 35; c.setFont("Helvetica-Bold", 18); c.drawString(mx, y, "Goed om te weten"); y -= 35
    paragraphs = [
        "Als gegevens correct zijn ingevuld, is de uitkomst een goede inschatting van het puntentotaal en de bijbehorende maximale huurprijs.",
        "De huurcommissie kan op verzoek een eigen telling uitvoeren, maar door interpretatie en meetverschillen op een ander resultaat uitkomen.",
        "De puntentelling blijft daarom indicatief en er kunnen geen rechten worden ontleend aan de uitkomst.",
    ]
    for p in paragraphs:
        y = draw_wrapped_text(c, p, mx, y, width-2*mx, "Helvetica", 10.5, 15); y -= 18
    c.showPage(); c.save(); buffer.seek(0); return buffer

def replace_first_page_with_cover(original_pdf_bytes, data):
    original_reader = PdfReader(BytesIO(original_pdf_bytes)); cover_reader = PdfReader(create_energievakman_cover(data)); writer = PdfWriter()
    writer.add_page(cover_reader.pages[0])
    for i in range(1, len(original_reader.pages)): writer.add_page(original_reader.pages[i])
    out = BytesIO(); writer.write(out); out.seek(0); return out.getvalue()

def make_wws_report(original_pdf_bytes, overrides=None):
    data = extract_wws_data(original_pdf_bytes)
    for k, v in (overrides or {}).items():
        if v: data[k] = v
    return replace_first_page_with_cover(original_pdf_bytes, data), data

def data_summary(data):
    keys = ["adres","adviseur","opnamedatum","type_woonruimte","tijdvak","datum_ingevuld","totaal_punten","maximale_huurprijs","woning_punten","binnenruimtes_punten","buitenruimtes_punten"]
    return "\n".join(f"{k}: {data.get(k, '')}" for k in keys)

@app.route("/", methods=["GET"])
def index(): return render_template_string(HTML)

@app.route("/health", methods=["GET"])
def health(): return {"status":"ok"}

@app.route("/download/<download_id>", methods=["GET"])
@app.route("/download/<download_id>/<filename>", methods=["GET"])
def download(download_id, filename=None):
    if not re.match(r"^[a-f0-9]{32}$", download_id): abort(404)
    path = os.path.join(TEMP_DIR, f"{download_id}.pdf")
    if not os.path.exists(path): return "Download niet meer beschikbaar. Maak het rapport opnieuw.", 404
    return send_file(path, mimetype="application/pdf", as_attachment=True, download_name=OUTPUT_PDF_NAME)

@app.route("/process-wws", methods=["POST"])
def process_wws():
    uploaded = request.files.get("pdf")
    if not uploaded or uploaded.filename == "": return "Geen PDF ontvangen", 400
    final_pdf, data = make_wws_report(uploaded.read(), {"adviseur": request.form.get("adviseur", "").strip(), "opnamedatum": request.form.get("opnamedatum", "").strip()})
    did = save_temp_pdf(final_pdf)
    return render_template_string(RESULT_HTML, download_id=did, message='<div class="okbox">✅ WWS rapport is gemaakt.</div>', summary=data_summary(data))

@app.route("/wws-upload-for-record/<record_id>", methods=["GET", "POST"])
def wws_upload_for_record(record_id):
    if request.method == "GET": return render_template_string(UPLOAD_FOR_RECORD_HTML, record_id=record_id)
    uploaded = request.files.get("pdf")
    if not uploaded or uploaded.filename == "": return "Geen PDF ontvangen", 400
    record = get_softr_record(record_id); fields = record.get("fields", {})
    overrides = {"adviseur": first_value(fields, SOFTR_ADVISEUR_FIELD), "opnamedatum": first_value(fields, SOFTR_OPNAMEDATUM_FIELD), "adres": first_value(fields, SOFTR_ADRES_FIELD), "dossiernummer": first_value(fields, SOFTR_DOSSIER_FIELD)}
    final_pdf, data = make_wws_report(uploaded.read(), overrides); did = save_temp_pdf(final_pdf); file_url = absolute_download_url(did)
    success, msg = update_softr_record_with_pdf_url(record_id, file_url, data)
    box = f'<div class="okbox">✅ {html.escape(msg)}</div>' if success else f'<div class="warnbox">⚠️ {html.escape(msg)}<br>Downloadlink: {html.escape(file_url)}</div>'
    return render_template_string(RESULT_HTML, download_id=did, message=box, summary=data_summary(data))

@app.route("/wws-from-softr/<record_id>", methods=["GET"])
def wws_from_softr(record_id):
    if not SOFTR_WWS_INPUT_FIELD: return "SOFTR_WWS_INPUT_FIELD ontbreekt in Render Environment", 500
    record = get_softr_record(record_id); fields = record.get("fields", {})
    urls = urls_from_softr_record(record, SOFTR_WWS_INPUT_FIELD)
    if not urls: return "Geen WWS PDF gevonden in het Softr inputveld", 404
    overrides = {"adviseur": first_value(fields, SOFTR_ADVISEUR_FIELD), "opnamedatum": first_value(fields, SOFTR_OPNAMEDATUM_FIELD), "adres": first_value(fields, SOFTR_ADRES_FIELD), "dossiernummer": first_value(fields, SOFTR_DOSSIER_FIELD)}
    final_pdf, data = make_wws_report(download_url_to_bytes(urls[0]["url"]), overrides); did = save_temp_pdf(final_pdf); file_url = absolute_download_url(did)
    success, msg = update_softr_record_with_pdf_url(record_id, file_url, data)
    box = f'<div class="okbox">✅ {html.escape(msg)}</div>' if success else f'<div class="warnbox">⚠️ {html.escape(msg)}<br>Downloadlink: {html.escape(file_url)}</div>'
    return render_template_string(RESULT_HTML, download_id=did, message=box, summary=data_summary(data))

if __name__ == "__main__": app.run(debug=True)
