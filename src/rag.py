# src/rag.py
# Diese Datei kümmert sich um WhatsApp-Texte (laden + vorbereiten)
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # -> Projekt-Root
WHATSAPP_PATH = BASE_DIR / "data_sources" / "whatsapp" / "whatsapp_export.txt"




def parse_messages(text):
    #Parst WhatsApp-Text zu Messages
    """
    Parst den WhatsApp-Text und extrahiert einzelne Messages als Dokumente.
    Annahme: Format wie [01.01.23, 12:00:00] Name: Nachricht
    Gibt Liste von Messages zurück.
    """
    # Regex: Sucht nach [Datum, Zeit] Name: Nachricht (multiline)
    pattern = r'(\$\d{2}\.\d{2}\.\d{2}, \d{2}:\d{2}:\d{2}\$ .+?: .+?)(?=\$\d{2}\.\d{2}\.\d{2}, \d{2}:\d{2}:\d{2}\$|$)'
    messages = re.findall(pattern, text, re.DOTALL)
    return messages if messages else [text]  # Fallback, falls kein Match


def load_whatsapp_text():
    #Lädt WhatsApp-Text-Datei
    """
    Liest die komplette WhatsApp-TXT-Datei
    und gibt den Inhalt als Text (String) zurück.
    """
    if not WHATSAPP_PATH.exists():
        raise FileNotFoundError(
            "WhatsApp-Datei nicht gefunden. "
            "Bitte prüfe den Pfad: data_sources/whatsapp/whatsapp_export.txt"
        )

    return WHATSAPP_PATH.read_text(encoding="utf-8")


def chunk_text(text, chunk_size=700):
    #Teilt text in Chunks
    """
    Teilt einen langen Text in kleinere Stücke (Chunks),
    damit ein Sprachmodell damit arbeiten kann.
    """
    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start = end

    return chunks
if __name__ == "__main__":
    text = load_whatsapp_text()
    print(text[:200])
