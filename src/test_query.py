# test_rag.py
# Testet RAG-Funktionalität.
from vector_store import retrieve_context, collection, answer_with_rag

print("COUNT IN COLLECTION =", collection.count())

frage = "Worüber haben wir gesprochen? Gib mir eine kurze Zusammenfassung."
antwort = answer_with_rag(frage)

print("\n=== LLM Antwort mit RAG Kontext ===")
print(antwort)