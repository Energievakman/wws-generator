from flask import Flask, request, send_file, render_template_string, abort, jsonify
from io import BytesIO
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
import os, re, html, uuid, tempfile, requests, threading, shutil
from urllib.parse import quote

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
JOBS = {}
DOWNLOAD_NAMES = {}
JOBS_LOCK = threading.Lock()



def set_job_progress(job_id, step, percent, detail=""):
    percent = max(0, min(100, int(percent)))
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["step"] = step
            JOBS[job_id]["percent"] = percent
            JOBS[job_id]["detail"] = detail


def set_job_done(job_id, download_id, summary, message):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["step"] = "Klaar"
            JOBS[job_id]["percent"] = 100
            JOBS[job_id]["detail"] = "WWS rapport is klaar."
            JOBS[job_id]["download_id"] = download_id
            JOBS[job_id]["summary"] = summary
            JOBS[job_id]["message"] = message


def set_job_error(job_id, message):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["step"] = "Fout"
            JOBS[job_id]["percent"] = 100
            JOBS[job_id]["detail"] = message

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
body{font-family:Arial,sans-serif;max-width:820px;margin:40px auto}
button{padding:10px 16px;cursor:pointer}
.dropzone{border:2px dashed #b8b8b8;border-radius:12px;padding:28px;text-align:center;background:#fafafa;margin:18px 0 22px 0;max-width:620px}
.dropzone.dragover{border-color:#1a73e8;background:#eef5ff}
.dropzone-title{display:block;font-size:17px;font-weight:700;margin-bottom:8px}
.dropzone-sub{display:block;color:#666;font-size:14px;margin-bottom:16px}
.file-name{margin-top:12px;color:#333;font-weight:600}
.hint{color:#666;font-size:14px;line-height:1.45;max-width:640px}
#progressBox{display:none;margin-top:22px;max-width:620px}
.progress-wrap{width:100%;height:18px;background:#eee;border-radius:999px;overflow:hidden}
.progress-bar{height:18px;width:0%;background:#1a73e8;transition:width .25s ease}
.progress-line{margin-top:10px;font-weight:700}.progress-detail{color:#666;margin-top:5px;font-size:14px;line-height:1.4}
</style></head><body><h1>WWS rapport maken</h1><p class="hint">Upload de originele WWS PDF. Adviseur, opnamedatum en adres worden waar mogelijk uit het Softr-record gehaald.</p>
<form id="uploadForm" action="/wws-upload-for-record/{{record_id}}" method="post" enctype="multipart/form-data">
<div class="dropzone" id="dropzone"><span class="dropzone-title">Sleep de WWS PDF hierheen</span><span class="dropzone-sub">of kies handmatig een PDF</span><input id="fileInput" type="file" name="pdf" accept="application/pdf,.pdf"><div id="fileName" class="file-name">Geen bestand geselecteerd</div></div>
<button id="submitBtn" type="submit">Maak WWS rapport</button></form>
<div id="progressBox"><div class="progress-wrap"><div id="progressBar" class="progress-bar"></div></div><div id="progressLine" class="progress-line">Voorbereiden — 0%</div><div id="progressDetail" class="progress-detail">Upload wordt gestart...</div></div>
<script>
const dz=document.getElementById('dropzone'),fi=document.getElementById('fileInput'),fn=document.getElementById('fileName');
const form=document.getElementById('uploadForm'),btn=document.getElementById('submitBtn'),box=document.getElementById('progressBox'),bar=document.getElementById('progressBar'),line=document.getElementById('progressLine'),detail=document.getElementById('progressDetail');
function upd(){fn.innerText=(!fi.files||fi.files.length===0)?'Geen bestand geselecteerd':fi.files[0].name}
fi.addEventListener('change',upd);dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('dragover')});dz.addEventListener('dragleave',()=>dz.classList.remove('dragover'));dz.addEventListener('drop',e=>{e.preventDefault();dz.classList.remove('dragover');fi.files=e.dataTransfer.files;upd()});
function poll(id){fetch('/wws-job-status/'+id).then(r=>r.json()).then(d=>{const p=d.percent||0;bar.style.width=p+'%';line.innerText=(d.step||'Bezig')+' — '+p+'%';detail.innerText=d.detail||'';if(d.status==='done'){window.location.href='/wws-job-result/'+id}else if(d.status==='error'){line.innerText='Fout — 100%';detail.innerText=d.detail||'Er ging iets mis.';btn.disabled=false;btn.innerText='Opnieuw proberen'}else{setTimeout(()=>poll(id),800)}}).catch(()=>{detail.innerText='Verbinding controleren...';setTimeout(()=>poll(id),1500)})}
form.addEventListener('submit',e=>{e.preventDefault();if(!fi.files||fi.files.length===0){alert('Kies eerst een WWS PDF.');return}btn.disabled=true;btn.innerText='Uploaden...';box.style.display='block';bar.style.width='3%';line.innerText='Uploaden — 3%';detail.innerText='PDF wordt naar Render gestuurd...';const data=new FormData(form);fetch(form.action+'?async=1',{method:'POST',body:data}).then(r=>r.json()).then(d=>{if(!d.job_id){throw new Error(d.error||'Geen job_id ontvangen')}btn.innerText='Bezig met verwerken...';poll(d.job_id)}).catch(err=>{line.innerText='Fout';detail.innerText=err.message||'Upload mislukt.';btn.disabled=false;btn.innerText='Opnieuw proberen'})});
</script></body></html>
"""

RESULT_HTML = """
<!doctype html><html><head><title>WWS rapport gemaakt</title><style>body{font-family:Arial,sans-serif;max-width:820px;margin:40px auto}.btn{display:inline-block;padding:12px 18px;background:#1a73e8;color:white;text-decoration:none;border-radius:6px;font-weight:700}.okbox{padding:12px 14px;background:#eef8f0;border:1px solid #b8dfc0;margin:18px 0}.warnbox{padding:12px 14px;background:#fff6e5;border:1px solid #ffd58a;margin:18px 0}pre{white-space:pre-wrap;background:#f7f7f7;padding:12px;border-radius:6px}</style></head><body><h1>WWS rapport gemaakt</h1>{{message|safe}}<a class="btn" href="/download/{{download_id}}">Download WWS rapport</a><h3>Gevonden gegevens</h3><pre>{{summary}}</pre></body></html>
"""

def sanitize_filename_part(value):
    value = safe_text(value, "").strip()
    # Pak bij een volledig adres alleen straat + huisnummer vóór de komma.
    if "," in value:
        value = value.split(",", 1)[0].strip()
    value = re.sub(r"[\\/:*?\"<>|]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .-")
    return value

def output_pdf_name(data):
    address = sanitize_filename_part(data.get("adres"))
    if address:
        return f"Puntentelling {address}.pdf"
    return OUTPUT_PDF_NAME

def save_temp_pdf(pdf_bytes, filename=None):
    download_id = uuid.uuid4().hex
    path = os.path.join(TEMP_DIR, f"{download_id}.pdf")
    with open(path, "wb") as f:
        f.write(pdf_bytes)
    DOWNLOAD_NAMES[download_id] = filename or OUTPUT_PDF_NAME
    return download_id

def absolute_download_url(download_id, filename=None):
    # URL eindigt bewust op .pdf zodat Softr het als PDF-bestand kan herkennen.
    filename = filename or DOWNLOAD_NAMES.get(download_id) or OUTPUT_PDF_NAME
    return BASE_URL.rstrip("/") + f"/download/{download_id}/{quote(filename)}"

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

def update_softr_record_with_pdf_url(record_id, file_url, data=None, filename=None):
    """
    Update apart:
    1. eerst PDF file-field
    2. daarna punten + maximale huurprijs

    Daardoor kan de PDF niet stilletjes misgaan terwijl punten/huurprijs wel worden bijgewerkt.
    """
    if not SOFTR_API_KEY or not SOFTR_DATABASE_ID or not SOFTR_TABLE_ID:
        return False, "Softr API gegevens ontbreken in Render."

    data = data or {}
    filename = filename or output_pdf_name(data)

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
            [{"url": file_url, "filename": filename}],
            [{"url": file_url, "name": filename}],
            [{"url": file_url, "fileName": filename}],
            [{"url": file_url, "filename": filename, "type": "application/pdf"}],
            [{"url": file_url, "name": filename, "type": "application/pdf"}],
            [{"fileUrl": file_url, "filename": filename}],
            [{"fileUrl": file_url, "name": filename}],
            [{"fileUrl": file_url, "fileName": filename}],
            [{"fileUrl": file_url, "filename": filename, "type": "application/pdf"}],
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
    return value.replace(" ", "").replace("−", "-").strip()


def flexible_label_pattern(label):
    # Maakt van bijvoorbeeld "Totaal aantal punten" een regex die ook werkt
    # als pypdf vreemde spaties of regelafbrekingen teruggeeft.
    return r"\s*".join(re.escape(part) for part in label.split())


def find_points_value(text, label):
    """Zoekt een puntenwaarde achter een label, inclusief negatieve waardes."""
    pattern = flexible_label_pattern(label) + r"\s*:?\s*(-?\s*\d+(?:[,.]\d+)?)"
    return clean_points(regex_find(text, pattern))


def find_money_value(text, label):
    pattern = flexible_label_pattern(label) + r"\s*:?\s*(€\s*[\d.,]+)"
    return clean_money(regex_find(text, pattern))


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

    required_parts = [
        parse_number(data.get("woning_punten")),
        parse_number(data.get("binnenruimtes_punten")),
        parse_number(data.get("buitenruimtes_punten")),
    ]
    optional_parts = [
        parse_number(data.get("bijzonderheden_punten")),
    ]
    if all(p is not None for p in required_parts):
        data["totaal_punten"] = format_dutch_number(sum(required_parts) + sum(p for p in optional_parts if p is not None))

    return data


def extract_wws_data(pdf_bytes):
    text = normalize_pdf_text(pdf_text_first_page(pdf_bytes))
    data = {
        "type_woonruimte": regex_find(text, r"Type woonruimte\s+(.+)"),
        "adres": regex_find(text, r"Adres\s+(.+)"),
        "tijdvak": regex_find(text, r"Tijdvak\s+(.+)"),
        "datum_ingevuld": regex_find(text, r"Datum ingevuld\s+(.+)"),
        "totaal_punten": find_points_value(text, "Totaal aantal punten"),
        "maximale_huurprijs": find_money_value(text, "Maximale huurprijs"),
        "woning_punten": find_points_value(text, "Woning"),
        "binnenruimtes_punten": find_points_value(text, "Binnenruimtes"),
        "buitenruimtes_punten": find_points_value(text, "Buitenruimtes"),
        "bijzonderheden_punten": find_points_value(text, "Bijzonderheden"),
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
        value = safe_text(data.get(key))

        if key == "maximale_huurprijs":
            value = value.replace("€", "").strip()
            value = f"€ {value}"

        c.setFont("Helvetica-Bold", 11)
        c.drawString(mx, y, label)
        c.setFont("Helvetica", 11)
        c.drawString(210, y, value)
        y -= 26

    y -= 12
    c.setFont("Helvetica-Bold", 12)
    c.drawString(mx, y, "Punten per onderdeel")
    y -= 26
    onderdelen = [("Woning","woning_punten"),("Binnenruimtes","binnenruimtes_punten"),("Buitenruimtes","buitenruimtes_punten")]
    if data.get("bijzonderheden_punten"):
        onderdelen.append(("Bijzonderheden", "bijzonderheden_punten"))
    for label, key in onderdelen:
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
    keys = ["adres","adviseur","opnamedatum","type_woonruimte","tijdvak","datum_ingevuld","totaal_punten","maximale_huurprijs","woning_punten","binnenruimtes_punten","buitenruimtes_punten","bijzonderheden_punten"]
    return "\n".join(f"{k}: {data.get(k, '')}" for k in keys)



def run_wws_job(job_id, record_id, uploaded_path, job_dir):
    try:
        set_job_progress(job_id, "Upload ontvangen", 8, "PDF is ontvangen. Recordgegevens worden opgehaald...")
        record = get_softr_record(record_id)
        fields = record.get("fields", {})
        set_job_progress(job_id, "Recordgegevens ophalen", 18, "Adviseur, opnamedatum en adres worden gelezen...")
        overrides = {
            "adviseur": first_value(fields, SOFTR_ADVISEUR_FIELD),
            "opnamedatum": first_value(fields, SOFTR_OPNAMEDATUM_FIELD),
            "adres": first_value(fields, SOFTR_ADRES_FIELD),
            "dossiernummer": first_value(fields, SOFTR_DOSSIER_FIELD),
        }
        set_job_progress(job_id, "PDF uitlezen", 32, "Puntenaantal, huurprijs en adres worden uit de Huurcommissie-PDF gehaald...")
        with open(uploaded_path, "rb") as f:
            original_pdf_bytes = f.read()
        final_pdf, data = make_wws_report(original_pdf_bytes, overrides)
        set_job_progress(job_id, "Voorblad maken", 58, "Eigen Energievakman-voorblad wordt toegevoegd...")
        filename = output_pdf_name(data)
        did = save_temp_pdf(final_pdf, filename)
        file_url = absolute_download_url(did, filename)
        set_job_progress(job_id, "PDF klaarzetten", 72, "Downloadlink wordt aangemaakt...")
        set_job_progress(job_id, "Softr bijwerken", 84, "Puntenaantal, maximale huurprijs en PDF worden teruggeschreven...")
        success, msg = update_softr_record_with_pdf_url(record_id, file_url, data, filename)
        set_job_progress(job_id, "Afronden", 96, "Resultaatpagina wordt klaargezet...")
        box = f'<div class="okbox">✅ {html.escape(msg)}</div>' if success else f'<div class="warnbox">⚠️ {html.escape(msg)}<br>Downloadlink: {html.escape(file_url)}</div>'
        set_job_done(job_id, did, data_summary(data), box)
    except Exception as e:
        set_job_error(job_id, str(e))
    finally:
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            pass

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
    download_filename = filename or DOWNLOAD_NAMES.get(download_id) or OUTPUT_PDF_NAME
    return send_file(path, mimetype="application/pdf", as_attachment=True, download_name=download_filename)

@app.route("/process-wws", methods=["POST"])
def process_wws():
    uploaded = request.files.get("pdf")
    if not uploaded or uploaded.filename == "": return "Geen PDF ontvangen", 400
    final_pdf, data = make_wws_report(uploaded.read(), {"adviseur": request.form.get("adviseur", "").strip(), "opnamedatum": request.form.get("opnamedatum", "").strip()})
    filename = output_pdf_name(data)
    did = save_temp_pdf(final_pdf, filename)
    return render_template_string(RESULT_HTML, download_id=did, message='<div class="okbox">✅ WWS rapport is gemaakt.</div>', summary=data_summary(data))

@app.route("/wws-upload-for-record/<record_id>", methods=["GET", "POST"])
def wws_upload_for_record(record_id):
    if request.method == "GET":
        return render_template_string(UPLOAD_FOR_RECORD_HTML, record_id=record_id)

    uploaded = request.files.get("pdf")
    if not uploaded or uploaded.filename == "":
        if request.args.get("async") == "1":
            return jsonify({"error": "Geen PDF ontvangen"}), 400
        return "Geen PDF ontvangen", 400

    if request.args.get("async") == "1":
        job_id = uuid.uuid4().hex
        job_dir = os.path.join(TEMP_DIR, f"wws_job_{job_id}")
        os.makedirs(job_dir, exist_ok=True)
        local_path = os.path.join(job_dir, "upload.pdf")
        uploaded.save(local_path)
        with JOBS_LOCK:
            JOBS[job_id] = {"status":"running","step":"Upload ontvangen","percent":5,"detail":"PDF is ontvangen. Verwerking wordt gestart...","record_id":record_id}
        threading.Thread(target=run_wws_job, args=(job_id, record_id, local_path, job_dir), daemon=True).start()
        return jsonify({"job_id": job_id})

    record = get_softr_record(record_id); fields = record.get("fields", {})
    overrides = {"adviseur": first_value(fields, SOFTR_ADVISEUR_FIELD), "opnamedatum": first_value(fields, SOFTR_OPNAMEDATUM_FIELD), "adres": first_value(fields, SOFTR_ADRES_FIELD), "dossiernummer": first_value(fields, SOFTR_DOSSIER_FIELD)}
    final_pdf, data = make_wws_report(uploaded.read(), overrides); filename = output_pdf_name(data); did = save_temp_pdf(final_pdf, filename); file_url = absolute_download_url(did, filename)
    success, msg = update_softr_record_with_pdf_url(record_id, file_url, data, filename)
    box = f'<div class="okbox">✅ {html.escape(msg)}</div>' if success else f'<div class="warnbox">⚠️ {html.escape(msg)}<br>Downloadlink: {html.escape(file_url)}</div>'
    return render_template_string(RESULT_HTML, download_id=did, message=box, summary=data_summary(data))

@app.route("/wws-job-status/<job_id>", methods=["GET"])
def wws_job_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({"status":"error","percent":100,"step":"Fout","detail":"Job niet gevonden"}), 404
    return jsonify({"status":job.get("status","running"),"step":job.get("step","Bezig"),"percent":job.get("percent",0),"detail":job.get("detail","")})

@app.route("/wws-job-result/<job_id>", methods=["GET"])
def wws_job_result(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return "Job niet gevonden", 404
    if job.get("status") == "error":
        return f"Fout bij verwerken: {html.escape(job.get('detail', 'Onbekende fout'))}", 500
    if job.get("status") != "done":
        return "WWS rapport is nog bezig met verwerken", 202
    return render_template_string(RESULT_HTML, download_id=job["download_id"], message=job["message"], summary=job["summary"])


@app.route("/wws-from-softr/<record_id>", methods=["GET"])
def wws_from_softr(record_id):
    if not SOFTR_WWS_INPUT_FIELD: return "SOFTR_WWS_INPUT_FIELD ontbreekt in Render Environment", 500
    record = get_softr_record(record_id); fields = record.get("fields", {})
    urls = urls_from_softr_record(record, SOFTR_WWS_INPUT_FIELD)
    if not urls: return "Geen WWS PDF gevonden in het Softr inputveld", 404
    overrides = {"adviseur": first_value(fields, SOFTR_ADVISEUR_FIELD), "opnamedatum": first_value(fields, SOFTR_OPNAMEDATUM_FIELD), "adres": first_value(fields, SOFTR_ADRES_FIELD), "dossiernummer": first_value(fields, SOFTR_DOSSIER_FIELD)}
    final_pdf, data = make_wws_report(download_url_to_bytes(urls[0]["url"]), overrides); filename = output_pdf_name(data); did = save_temp_pdf(final_pdf, filename); file_url = absolute_download_url(did, filename)
    success, msg = update_softr_record_with_pdf_url(record_id, file_url, data, filename)
    box = f'<div class="okbox">✅ {html.escape(msg)}</div>' if success else f'<div class="warnbox">⚠️ {html.escape(msg)}<br>Downloadlink: {html.escape(file_url)}</div>'
    return render_template_string(RESULT_HTML, download_id=did, message=box, summary=data_summary(data))

if __name__ == "__main__": app.run(debug=True)
