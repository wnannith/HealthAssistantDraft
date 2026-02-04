import os
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup

from langchain_chroma import Chroma
from langchain_community.document_loaders import WebBaseLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from chromadb.config import Settings

load_dotenv()
embeddings = GoogleGenerativeAIEmbeddings(
    api_key=os.getenv("GOOGLE_API_KEY"),
    model="models/gemini-embedding-001"
)


# STEP 1: NIH API
NIH_API = "https://ods.od.nih.gov/api/"

response = requests.get(NIH_API)
soup = BeautifulSoup(response.text, "html.parser")
links = soup.find_all("a", string="HTML")
web_paths = [NIH_API + link["href"] for link in links if "español" not in link["href"]]


# STEP 2: RETRIEVE, CHUNK, AND INDEX WEB PAGES
print("Fetching content...")
loader = WebBaseLoader(
    web_paths=web_paths,
)
docs = loader.load()

text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
splits = text_splitter.split_documents(docs)


# STEP 3: SAVE TO DISK
vector_store = Chroma.from_documents(
    documents=splits,
    collection_name="nih-chroma",
    embedding_function=embeddings,
    persist_directory="./chroma/nih-chroma.db",
    client_settings=Settings(
        anonymized_telemetry=False,
        is_persistent=True,
    ),
)
