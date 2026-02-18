# vector_store.py
# Erstellt und nutzt Vektor-Datenbank für WhatsApp-Chats.
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
import chromadb
import re

from src.rag import load_whatsapp_text
from src.rag import parse_messages

# .env laden (OPENAI_API_KEY)
load_dotenv()

# OpenAI Client einmalig initialisieren
client = OpenAI()

# Absoluter Pfad Projektroot
BASE_DIR = Path(__file__).resolve().parent.parent
# Ordner für Chroma DB
CHROMA_DIR = BASE_DIR / "chroma_db"
print("📁 CHROMA_DIR =", CHROMA_DIR)

# Persistent Chroma Client initialisieren
chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))

# Collection mit festem Namen anlegen oder holen
collection = chroma_client.get_or_create_collection(name="whatsapp_chats")

def build_vector_store():
    #Baut Vektor-Store für Messages
    global collection

    text = load_whatsapp_text()
    messages = parse_messages(text)  #Messages 

    print(f"🔹 Erstelle Embeddings für {len(messages)} Messages...")

    if collection.count() > 0:
        chroma_client.delete_collection(name="whatsapp_chats")
        collection = chroma_client.get_or_create_collection(name="whatsapp_chats")

    ids = [f"message_{i}" for i in range(len(messages))]   #messages

    #Metadaten pro Message
    metadatas = []
    for i, message in enumerate(messages):
        # Extrahiere Datum und Sender aus der Message
        date_match = re.search(r'\$(\d{2}\.\d{2}\.\d{2}),', message)
        sender_match = re.search(r'\$ (.+?):', message)
        metadata = {
            "message_index": i,
            "date": date_match.group(1) if date_match else "unknown",
            "sender": sender_match.group(1) if sender_match else "unknown"
        }
        metadatas.append(metadata)

    batch_size = 50
    for start in range(0, len(messages), batch_size):
        batch = messages[start:start + batch_size]  
        batch_ids = ids[start:start + batch_size]
        batch_metadatas = metadatas[start:start + batch_size]

        emb = client.embeddings.create(
            model="text-embedding-3-small",
            input=batch
        ).data

        embeddings = [e.embedding for e in emb]

        collection.add(
            ids=batch_ids,
            documents=batch,  #messages als documents
            embeddings=embeddings,
            metadatas=batch_metadatas
        )

    print("✅ Vektor-Datenbank erstellt/aktualisiert! (chroma_db)")
    print("COUNT IN COLLECTION =", collection.count())
    print("📁 CHROMA_DIR =", CHROMA_DIR)


def retrieve_context(question: str, k: int = 3) -> list[str]:
    #Holt relevanten Kontext
    q_emb = client.embeddings.create(
        model="text-embedding-3-small",
        input=question
    ).data[0].embedding

    results = collection.query(query_embeddings=[q_emb], n_results=k)
    return results["documents"][0]


if __name__ == "__main__":
    build_vector_store()


def answer_with_rag(question: str) -> str:
    context_chunks = retrieve_context(question, k=3)
    context_text = "\n".join(context_chunks)

    prompt = f"""
Du bist ein hilfreicher Assistent. Nutze den folgenden WhatsApp-Chat-Kontext, um die Frage präzise zu beantworten:

Kontext:
{context_text}

Frage: {question}

Bitte antworte kurz und prägnant basierend auf obigem Kontext.
"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Du bist ein hilfreicher Assistent."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=300
    )
    return completion.choices[0].message.content
