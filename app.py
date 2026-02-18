# app.py
import os
import re
import json
import hashlib
import logging
import sqlite3
from datetime import date
from typing import List, Dict, Tuple

import pandas as pd
import streamlit as st
import pdfplumber

# OpenAI + RAG
from src.vector_store import client, retrieve_context

DB_PATH = "finance.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# OCR SETUP (Windows)
OCR_AVAILABLE = False
OCR_STATUS_MSG = ""
POPPLER_PATH = r"C:\poppler\Library\bin"

try:
    import pytesseract

    # TESSERACT CONFIG

    TESSERACT_DIR = r"C:\Programme\Tesseract-OCR"
    TESSDATA_DIR = os.path.join(TESSERACT_DIR, "tessdata")

    # Pfad zur EXE
    pytesseract.pytesseract.tesseract_cmd = os.path.join(
        TESSERACT_DIR,
        "tesseract.exe"
    )

    # Pfad zu Sprachdaten
    os.environ["TESSDATA_PREFIX"] = TESSDATA_DIR

    # Test
    _ = pytesseract.get_tesseract_version()

    OCR_AVAILABLE = True
    OCR_STATUS_MSG = (
        f"OCR aktiv ✅ ({pytesseract.pytesseract.tesseract_cmd}) "
        f"| TESSDATA_PREFIX={os.environ.get('TESSDATA_PREFIX')}"
    )

except Exception as e:
    OCR_AVAILABLE = False
    OCR_STATUS_MSG = f"OCR nicht aktiv ❌ ({e})"



# DATABASE
def get_conn():
    #Erstellt SQLite-Verbindung
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    #Initialisiert DB-Tabellen
    conn = get_conn()
    cur = conn.cursor()

    # Kategorien-Tabelle
    cur.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Transactions-Tabelle (mit category_name als TEXT oder optional category_id)
    cur.execute("""
       CREATE TABLE IF NOT EXISTS transactions (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           t_date TEXT NOT NULL,
           amount REAL NOT NULL,
           description TEXT NOT NULL,
           category TEXT
       )
    """)

    conn.commit()
    conn.close()

def ensure_category_exists(category_name: str):
    #Fügt Kategorie hinzu, falls nicht vorhanden.
    category_name = (category_name or "").strip()
    if not category_name:
        return

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO categories (name) VALUES (?)",
            (category_name,)
        )
        conn.commit()
    except Exception as e:
        logging.error(f"Kategorie konnte nicht gespeichert werden: {e}")
    finally:
        conn.close()



def insert_transaction(t_date: str, amount: float, description: str, category: str):
    #Fügt Transaktion ein
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO transactions (t_date, amount, description, category) VALUES (?, ?, ?, ?)",
            (t_date, amount, description, category),
        )
        conn.commit()
    except Exception as e:
        logging.error(f"DB Insert Fehler: {e}")
    finally:
        conn.close()


def fetch_transactions() -> pd.DataFrame:
    #Holt Transaktionen als DataFrame
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT id, t_date as date, amount, description, category FROM transactions ORDER BY t_date DESC, id DESC",
        conn,
    )
    conn.close()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0).astype(float)
        df["category"] = df["category"].fillna("Sonstiges")
    return df


def delete_transaction(row_id: int):
    #Löscht Transaktion per ID
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM transactions WHERE id = ?", (row_id,))
    conn.commit()
    conn.close()


# PDF EXTRACTION
def extract_text_pdfplumber(file) -> str:
    out = []
    with pdfplumber.open(file) as pdf:
        for p in pdf.pages:
            t = p.extract_text() or ""
            if t.strip():
                out.append(t)
    return "\n".join(out).strip()


def looks_like_cid_garbage(text: str) -> bool:
    #Prüft auf CID-Garbage.
    if not text:
        return True
    if "(cid:" in text:
        return True
    letters = sum(ch.isalpha() for ch in text)
    return (letters / max(1, len(text))) < 0.15


def ocr_pdf_to_text(file) -> str:
    # OCR mit pypdfium2 und Tesseract.
    """
    OCR ohne Poppler: PDF -> Render mit pypdfium2 -> PIL -> Tesseract
    Funktioniert auf Windows sehr zuverlässig.
    """
    if not OCR_AVAILABLE:
        raise RuntimeError("OCR ist nicht aktiv (pytesseract/Tesseract prüfen).")

    import pypdfium2 as pdfium

    pdf_bytes = file.getvalue()
    pdf = pdfium.PdfDocument(pdf_bytes)

    parts = []
    scale = 3.0

    for i in range(len(pdf)):
        page = pdf[i]
        # Render to PIL image
        pil_image = page.render(scale=scale).to_pil()
        txt = pytesseract.image_to_string(pil_image, lang="deu+eng")
        if txt.strip():
            parts.append(txt)

    return "\n\n".join(parts).strip()



def extract_text_from_pdf_smart(file) -> Tuple[str, str]:
    # Smart Extraktion: pdfplumber oder OCR.
    """
    Return (text, method) where method in {"text/table", "ocr"}.
    Erzwingt OCR, wenn pdfplumber CID-Garbage liefert.
    """
    text = extract_text_pdfplumber(file)
    if looks_like_cid_garbage(text):
        if OCR_AVAILABLE:
            return ocr_pdf_to_text(file), "ocr"
        return text, "text/table"
    return text, "text/table"

# N26 PARSER 
DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
AMOUNT_RE = re.compile(r"^[+-]?\s*\d{1,3}(?:\.\d{3})*,\s*\d{2}\s*€$|^[+-]?\s*\d+,\s*\d{2}\s*€$")


def de_date_to_iso(d: str) -> str:
    #Konvertiert DD.MM.YYYY zu YYYY-MM-DD
    # "01.02.2026" -> "2026-02-01"
    dd, mm, yyyy = d.split(".")
    return f"{yyyy}-{mm}-{dd}"


def parse_n26_ocr_text(text: str) -> List[Dict]:
    #Parst N26-Text zu Transaktionen
    """
    N26 Kontoauszug OCR-Text:
    - Beschreibungen stehen jeweils direkt vor "Wertstellung DD.MM.YYYY"
    - danach kommen Blöcke mit Verbuchungsdaten (nur Datumszeilen)
    - danach Blöcke mit Beträgen
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Beschreibungen aus "Wertstellung"-Blöcken
    wert_idx = [i for i, ln in enumerate(lines) if ln.startswith("Wertstellung ")]
    descriptions: List[str] = []

    stop_prefixes = (
        "Mastercard",
        "Ursprungsbetrag",
        "IBAN:",
        "BIC:",
        "Gutschriften",
        "Beschreibung",
        "Verbuchungsdatum",
        "Betrag",
        "N26",
        "Vorläufiger Kontoauszug",
    )

    for idx in wert_idx:
        # suche die letzte sinnvolle Zeile davor
        desc = ""
        j = idx - 1
        while j >= 0:
            cand = lines[j].strip()
            if not cand:
                j -= 1
                continue
            if any(cand.startswith(p) for p in stop_prefixes):
                j -= 1
                continue
            # häufige “Zwischenzeilen” ignorieren
            if cand.lower().startswith("wertstellung"):
                j -= 1
                continue
            desc = cand
            break
        descriptions.append(desc or "(ohne Beschreibung)")

    # Verbuchungsdatum: längste zusammenhängende Sequenz reiner Datumszeilen
    date_positions = [i for i, ln in enumerate(lines) if DATE_RE.match(ln)]
    booking_dates: List[str] = []
    if date_positions:
        best_run = []
        run = [date_positions[0]]
        for a, b in zip(date_positions, date_positions[1:]):
            if b == a + 1:
                run.append(b)
            else:
                if len(run) > len(best_run):
                    best_run = run
                run = [b]
        if len(run) > len(best_run):
            best_run = run
        booking_dates = [lines[i] for i in best_run]

    # Betrag: längste zusammenhängende Sequenz von Betrag-Zeilen
    amt_positions = [i for i, ln in enumerate(lines) if AMOUNT_RE.match(ln.replace(" ", "")) or AMOUNT_RE.match(ln)]
    amounts_raw: List[str] = []
    if amt_positions:
        best_run = []
        run = [amt_positions[0]]
        for a, b in zip(amt_positions, amt_positions[1:]):
            if b == a + 1:
                run.append(b)
            else:
                if len(run) > len(best_run):
                    best_run = run
                run = [b]
        if len(run) > len(best_run):
            best_run = run
        amounts_raw = [lines[i] for i in best_run]

    # Align by order
    n = min(len(descriptions), len(booking_dates), len(amounts_raw))
    txns = []
    for i in range(n):
        d = booking_dates[i]
        amt = amounts_raw[i].replace(" ", "").replace("€", "").replace(".", "").replace(",", ".")
        # "-77,64€" -> -77.64
        try:
            amount = float(amt)
        except Exception:
            continue

        txns.append(
            {
                "date": de_date_to_iso(d),
                "description": descriptions[i],
                "amount": amount,
            }
        )

    return txns


# AI FUNCTIONS
def ai_categorize_free(description: str, amount: float) -> str:
    #Kategorisiert mit OpenAI
    prompt = f"""
Du klassifizierst Banktransaktionen in kurze, allgemeine Kategorien.


Aufgabe:
Ordne jede Transaktion anhand ihrer wirtschaftlichen Funktion
einer sinnvollen Finanzkategorie zu.

Die Kategorie soll:
-kurz sein (1-3 Wörter)
-allgemein verständlich sein 
-wiederverwendbar sein
-KEINE Marke enthalten

DENKE FUNKTIONSBASIERT; NICHT MARKENBASIERT:

BEISPIELE FÜR kATEGORIEN (nicht verpflichtend)
-Lebensmittel
-Shopping
-Miete
-Transport
-Lieferdienste
-Abonnement
-Freizeit
-Drogerie
-Gesundheit
-Finanzdienste
-Einnahmen
-Rücherstattung
-Gebühren
-Sonstiges

Regeln:
- Entscheide nach dem Zweck der Zahlung.
- Unbekannte Marken anhand des Kontexts interpretieren.
- Geld senden/empfangen → Finanzdienste.
- Wiederkehrende digitale Dienste → Abonnement.
- Positiver Betrag → meist Einnahmen oder Rückerstattung.
- Wenn Bedeutung unklar → Sonstiges.


WICHTIG:
- Keine Erklärungen schreiben.
- Keine Sätze
- Antworte NUR als JSON: {{"category": "<...>"}}.

Transaktionsbeschreibung: {description}
Betrag: {amount}
"""
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Du gibst nur JSON zurück."},
            {"role": "user", "content": prompt},
        ],
        temperature=0
    )

    content = resp.choices[0].message.content.strip()
    content = re.sub(r"^```json\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    try:
        data = json.loads(content)
        cat = (data.get("category") or "").strip()
        return cat if cat else "Sonstiges"
    except Exception as e:
        logging.error(f"Kategorisierung JSON fehlerhaft: {e} | Antwort: {content}")
        return "Sonstiges"
    
def extract_transactions_with_ai(pdf_text: str) -> List[Dict]:
    #Extrahiert Transaktion mit LLM
    """
    LLM-basierte Information Extraction:
    OCR/PDF-Text -> JSON Liste von Transaktionen
    """
    prompt = f"""
Du bist ein präziser Assistent zur Extraktion von Banktransaktionen aus Kontoauszugs-Text.

Extrahiere ALLE Transaktionen aus dem Text. Jede Transaktion hat:
- date: Datum im Format YYYY-MM-DD
- description: Buchungstext/Händler/Verwendungszweck (kurz, aber eindeutig)
- amount: Betrag als float (Ausgaben negativ, Einnahmen positiv)

Regeln:
- Ignoriere Kopf-/Fußzeilen, Summen, IBAN/BIC, Seitenzahlen, Adressen.
- Beträge können im deutschen Format vorkommen (z.B. 1.234,56 €). Konvertiere zu 1234.56.
- Wenn ein Betrag ohne Vorzeichen steht: nutze Kontext (Belastung = negativ, Gutschrift = positiv). Falls unklar, nimm negativ.
- Antworte NUR mit einer JSON-Liste, ohne Markdown und ohne Erklärung.
- Beispiel:
[
  {{"date":"2026-02-01","description":"REWE","amount":-23.45}},
  {{"date":"2026-02-03","description":"GEHALT","amount":1487.00}}
]

Text:
{pdf_text}
"""

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Du gibst nur gültiges JSON zurück."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=2000,
    )

    content = resp.choices[0].message.content.strip()
    content = re.sub(r"^```json\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    try:
        data = json.loads(content)
        if not isinstance(data, list):
            return []
    except Exception as e:
        logging.error(f"PDF-LMM-Extraktion: JSON Parsing Fehler: {e}")
        logging.error(f"Antwort war:\n{content}")
        return []

    # Minimal-Validation + Normalisierung
    cleaned = []
    for item in data:
        if not isinstance(item, dict):
            continue
        d = (item.get("date") or "").strip()
        desc = (item.get("description") or "").strip()
        amt = item.get("amount", None)

        if not d or not desc or amt is None:
            continue

        # Amount sicher float machen
        try:
            amt_f = float(amt)
        except Exception:
            continue

        cleaned.append({"date": d, "description": desc, "amount": amt_f})

    return cleaned


def df_to_context(df: pd.DataFrame) -> str:
    #Konvertiert DataFrame zu Text-Kontext.
    if df.empty:
        return "Keine Transaktionen vorhanden."
    df2 = df.copy()
    df2["date"] = df2["date"].dt.strftime("%Y-%m-%d")
    income = df[df["amount"] > 0]["amount"].sum()
    expenses = df[df["amount"] < 0]["amount"].sum()
    balance = income + expenses
    table = df2[["date", "amount", "description", "category"]].to_csv(index=False)
    return f"""
FINANZDATEN:

Einnahmen: {income:.2f} EUR
Ausgaben: {abs(expenses):.2f} EUR
Saldo: {balance:.2f} EUR

Transaktionen:
{table}
""".strip()

def route_question(question: str) -> str:
    #Entscheidet Route für Frage
    """
    Entscheidet automatisch, ob WhatsApp-Kontext gebraucht wird.
    Rückgabe: "finance_only" | "whatsapp_only" | "both"
    """
    q = (question or "").lower().strip()

    whatsapp_markers = [
        "whatsapp", "chat", "gespräch", "gesprochen", "geschrieben",
        "habe ich gesagt", "hast du gesagt", "hat er gesagt", "hat sie gesagt",
        "vereinbart", "abgemacht", "plan", "vorhaben", "ziel", "sparziel",
        "miete teilen", "rückzahlung", "schuld", "leihen", "überweisen",
        "wann", "wieso", "warum", "zusammenfassung", "was wurde"
    ]

    finance_markers = [
        "ausgaben", "einnahmen", "saldo", "budget", "kategorie", "summe",
        "diesen monat", "letzten monat", "dieses jahr", "wieviel", "wie viel",
        "durchschnitt", "top", "meiste", "transaktion", "buchung"
    ]

    has_whatsapp = any(m in q for m in whatsapp_markers)
    has_finance = any(m in q for m in finance_markers)

    # Heuristik:
    if has_whatsapp and has_finance:
        return "both"
    if has_whatsapp:
        return "whatsapp_only"
    # Default: finance_only
    return "finance_only"



def ai_chat_orchestrator(question: str, df: pd.DataFrame, coach_style="Neutral", use_rag=False):
    #Orchestriert Chat mit Kontext
    style_hint = {
        "Motivierend": "Sei motivierend, unterstützend und positiv.",
        "Neutral": "Sei sachlich, klar und strukturiert.",
        "Streng": "Sei direkt, diszipliniert und fokussiere auf klare Maßnahmen."
    }.get(coach_style, "Sei sachlich und klar.")

    #Smart Routing
    route = "finance_only"
    if use_rag:
        route = route_question(question)

    #Finanzkontext nur, wenn gebraucht
    financial_context = df_to_context(df) if route in ("finance_only", "both") else "Finanzdaten wurden für diese Antwort nicht genutzt."

    prompt = f"""
Du bist ein KI-gestützter Budget-Coach.

{style_hint}

WICHTIG-Quellenregeln:
- Es gibt zwei Datenquellen: (A) Finanzdaten aus der App und (B) WhatsApp-Kontext.
- Wenn WhatsApp-Kontext vorhanden ist und zur Frage passt, MUSST du ihn aktiv nutzen.
- Wenn du WhatsApp nutzt, zitiere mindestens 1 konkretes Detail daraus (paraphrasiert).
- Erfinde keine Zahlen und keine Aussagen, die nicht im Kontext stehen.
- Antworte immer in dieser Struktur:

Analyse
Ergebnis
Empfehlung
Genutzter Kontext: (Finanzdaten / WhatsApp / Beide)

Weitere Regeln:
- Nutze ausschließlich die bereitgestellten Daten.
- Interpretire Zeitangaben logisch (z.B. "diesen Monat" = neuester Monat).
- Berechne Summen und Durchschnitte korrekt auf Basis der bereitgestellten Daten.
- Wenn Daten fehlen, sag es transparent.

ROUTING-ENTSCHEIDUNG: {route}

{financial_context}
""".strip()

    #WhatsApp Kontext nur, wenn gebraucht
    if use_rag and route in ("whatsapp_only", "both"):
        try:
            rag_chunks = retrieve_context(question, k=5)
            rag_text = "\n".join(rag_chunks)
            prompt += f"""

================ WHATSAPP KONTEXT ================
Die folgenden Informationen stammen aus privaten WhatsApp-Gesprächen.
Das sind Ziele, Pläne, Absprachen oder finanzielle Absichten.

Nutze diese Informationen aktiv in deiner Analyse,
wenn sie zur Frage passen.

WhatsApp Inhalte:
{rag_text}
===================================================
"""
        except Exception as e:
            logging.error(f"RAG Fehler: {e}")
            prompt += "\n\n(WhatsApp-Kontext konnte nicht geladen werden.)"

    prompt += f"\n\nNutzerfrage:\n{question}\n"

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Du bist ein professioneller Finanzanalyst und Budget-Coach."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2,
        max_tokens=700
    )
    return response.choices[0].message.content.strip(), route



# IMPORT PDF -> DB
def import_transactions_from_pdf(file):
    file_hash = hashlib.sha256(file.getvalue()).hexdigest()
    if st.session_state.get("last_import_hash") == file_hash:
        st.warning("Diese PDF wurde in dieser Session bereits importiert (Doppel-Import verhindert).")
        return

    text, method = extract_text_from_pdf_smart(file)

    st.info(f"PDF-Extraktionsmethode: **{method}** | Textlänge: {len(text)} Zeichen")
    with st.expander("🔍 Debug: extrahierter Text (Ausschnitt)"):
        st.text(text[:5000])

    #Parser + KI-Fallback 
    txns = []
    is_n26 = ("N26" in text) or ("Vorläufiger Kontoauszug" in text)
    
    if is_n26: 
        txns = parse_n26_ocr_text(text)
        st.info(f"N26 erkannt -> Parser verwendet. Treffer: {len(txns)}")
    
    if not txns:
        txns = extract_transactions_with_ai(text)
        st.info(f"KI-Fallback verwendet. Treffer: {len(txns)}")

    inserted = 0
    for txn in txns:
        t_date = txn.get("date")
        amount = txn.get("amount")
        description = (txn.get("description") or "").strip()

        if t_date and amount is not None and description:
            category = ai_categorize_free(description, float(amount))
            ensure_category_exists(category)
            insert_transaction(t_date, float(amount), description, category)
            inserted += 1

    st.session_state["last_import_hash"] = file_hash
    st.success(f"✅ Import abgeschlossen. Eingefügt: **{inserted}** Transaktionen.")


# UI
st.set_page_config(page_title="Budget-Coach", layout="wide")

st.markdown(
    """
<style>
.block-container { padding-top: 2rem; padding-bottom: 2rem; }
.card {
  background: rgba(255,255,255,0.70);
  border: 1px solid rgba(0,0,0,0.06);
  border-radius: 14px;
  padding: 16px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.06);
  margin-bottom: 14px;
}
.badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid rgba(0,0,0,0.10);
  background: rgba(0,0,0,0.03);
  margin-right: 6px;
}
.chat-bubble {
  padding: 12px 14px;
  border-radius: 14px;
  border: 1px solid rgba(0,0,0,0.06);
  margin: 8px 0;
}
.user-bubble { background: rgba(0,0,0,0.04); }
.assistant-bubble { background: rgba(0,200,120,0.12); }
</style>
""",
    unsafe_allow_html=True,
)

init_db()
st.title("Budget-Coach")

st.sidebar.title("⚙️ Einstellungen")
use_rag = st.sidebar.toggle("WhatsApp Kontext", value=False)
coach_style = st.sidebar.selectbox("Coach-Stil", ["Motivierend", "Neutral", "Streng"])
st.sidebar.caption("WhatsApp-Daten bleiben lokal.")
st.sidebar.info(OCR_STATUS_MSG)

left, right = st.columns([1, 1])

with left:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("### 📄 PDF-Kontoauszug importieren")

    uploaded_file = st.file_uploader("Wähle eine PDF mit Transaktionen", type=["pdf"])
    if uploaded_file is not None:
        import_transactions_from_pdf(uploaded_file)

    st.markdown("### ➕ Neue Transaktion manuell hinzufügen")

    with st.form("add_tx_form", clear_on_submit=True):
        t_date = st.date_input("Datum", value=date.today())
        amount = st.number_input("Betrag (Einnahme + / Ausgabe -)", step=1.0, format="%.2f")
        description = st.text_input("Beschreibung")

        submitted = st.form_submit_button("💾 Speichern")
        if submitted and description.strip():
            category = ai_categorize_free(description.strip(), float(amount))
            ensure_category_exists(category)
            insert_transaction(str(t_date), float(amount), description.strip(), category) 
            st.success(f"✅ Gespeichert unter Kategorie: {category}")
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

with right:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("### 📒 Gespeicherte Transaktionen")

    df = fetch_transactions()
    st.dataframe(df, width="stretch", hide_index=True)

    if not df.empty:
        income = df[df["amount"] > 0]["amount"].sum()
        expenses = df[df["amount"] < 0]["amount"].sum()
        balance = income + expenses

        c1, c2, c3 = st.columns(3)
        c1.metric("Einnahmen", f"{income:.2f} €")
        c2.metric("Ausgaben", f"{abs(expenses):.2f} €")
        c3.metric("Saldo", f"{balance:.2f} €")

    st.divider()
    st.markdown("*🗑️ Transaktion löschen*")
    del_id = st.number_input("ID zum Löschen", min_value=0, step=1)
    if st.button("Löschen"):
        delete_transaction(int(del_id))
        st.success("✅ Gelöscht!")
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("### 💬 Chat")

    st.markdown(
        f'<span class="badge">Modus: {"WhatsApp RAG" if use_rag else "General LLM"}</span>'
        f'<span class="badge">Stil: {coach_style}</span>',
        unsafe_allow_html=True,
    )

    if "chat" not in st.session_state:
        st.session_state.chat = []

    for msg in st.session_state.chat:
        bubble_class = "user-bubble" if msg["role"] == "user" else "assistant-bubble"
        label = "Du" if msg["role"] == "user" else "Coach"
        st.markdown(
            f'<div class="chat-bubble {bubble_class}"><b>{label}:</b><br>{msg["content"]}</div>',
            unsafe_allow_html=True,
        )

    user_q = st.chat_input("Frag z. B.: 'Wie viel habe ich ausgegeben?'")
    if user_q and user_q.strip():
        st.session_state.chat.append({"role": "user", "content": user_q.strip()})

        with st.spinner("Antwort wird erstellt..."):
            answer_text, used_route = ai_chat_orchestrator(
                question=user_q.strip(),
                df=df,
                coach_style=coach_style,
                use_rag=use_rag,
            )

        st.session_state.chat.append({"role":"assistant","content": f"{answer_text}\n\n🧭 Routing: {used_route}"})
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
