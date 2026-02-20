# Budget-Coach-Prototyp

Prototypischer KI-gestützter Finanzassistent zur Analyse persönlicher Finanzdaten im Rahmen einer Bachelorarbeit.

## Beschreibung
Dieses Projekt implementiert einen prototypischen Finanzassistenten, der generative Künstliche Intelligenz nutzt, um Nutzer bei der Analyse und dem Verständnis ihrer individuellen Finanzdaten zu unterstützen.

Der Assistent kombiniert:
- strukturierte Finanzdaten (Transaktionen)
- unstrukturierte Textdaten (Chatnachrichten)
- Retrieval-Augmented Generation (RAG)
- Large Language Models

Ziel ist die Untersuchung, inwieweit generative KI als erklärende Schnittstelle zwischen Nutzer und Finanzdaten eingesetzt werden kann.

## Funktionen
- Import von Kontoauszügen (PDF + OCR)
- Automatische Transaktionskategorisierung
- Dialogbasierte Finanzanalyse
- Ausgaben-Zusammenfassungen
- RAG-basierte Analyse von WhatsApp-Chats
- KI-gestützte Erklärung finanzieller Zusammenhänge

## Technologien
- Python
- Streamlit
- SQLite
- OpenAI API
- ChromaDB
- Pandas
- Tesseract OCR

## Installation (Prototype Setup)

1. Abhängigkeiten installieren: pip install -r requirements.txt 
2. `.env` Datei erstellen und OpenAI API Key hinzufügen: OPENAI_API_KEY=sk-xxxxxxxxxxxxx
3. Anwendung starten: streamlit run app.py


## Kontext der Arbeit
Dieses Projekt wurde im Rahmen einer Bachelorarbeit entwickelt:

**Titel:** Untersuchung von Anwendungsmöglichkeiten Generativer Künstlicher Intelligenz in der Finanzassistenz    
**Autorin:** Safae Hamdouchi  
**Jahr:** 2026

## Hinweis
Der Prototyp dient ausschließlich Forschungszwecken.
