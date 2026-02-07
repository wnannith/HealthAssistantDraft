import os
from dotenv import load_dotenv
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from chromadb.config import Settings

load_dotenv()
embeddings = GoogleGenerativeAIEmbeddings(
    api_key=os.getenv("GOOGLE_API_KEY"),
    model="models/gemini-embedding-001"
)


# STEP 1: Read local Thai text file containing office-syndrome info
print("Loading local office syndrome file...")
txt_path = Path(__file__).parent / "txt_office_syndrome.txt"
if not txt_path.exists():
    raise FileNotFoundError(f"Expected file not found: {txt_path}")

with open(txt_path, "r", encoding="utf-8") as fh:
    full_text = fh.read()

# STEP 2: Create a single Document and chunk it.
# For Thai text we use smaller chunks because words are not space-delimited.
text_splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=100)
docs = [Document(page_content=full_text)]
splits = text_splitter.split_documents(docs)


# STEP 3: SAVE TO DISK (Chroma)
vector_store = Chroma.from_documents(
    documents=splits,
    collection_name="office-syndrome",
    embedding=embeddings,
    persist_directory="./chroma/office-syndrome.db",
    client_settings=Settings(
        anonymized_telemetry=False,
        is_persistent=True,
    ),
)
