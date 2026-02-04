import os
import io
import sys
import json
from pathlib import Path

ctx = Path(__file__).parent / "context.json"
if ctx.exists():
    cfg = json.loads(ctx.read_text(encoding="utf-8"))
else:
    cfg = {}

# Core Langchain
from langchain_core.prompts import ChatPromptTemplate
from langchain_classic.chains import create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain

# Gemini LLMs
from langchain.chat_models import init_chat_model
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai import GoogleGenerativeAIEmbeddings

# OpenAI LLMs
from openai import OpenAI

# RAG
from langchain_chroma import Chroma
from chromadb.config import Settings

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


# Load system prompt from context.json
def load_defaults(path=None):
    """Load the default system prompts from `defaultContext.json`. Returns a dict."""
    try:
        if path is None:
            path = Path(__file__).parent / "defaultContext.json"
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        print(f"Error loading defaultContext.json: {e}")
        return dict()


def get_prompt(key, default=None, fmt_vars=None):
    """
    Retrieve a prompt from the defaults using a dot-separated key path.

    - `key`: e.g. "prompts.systemPrompt" or
      "prompts.symptomPrompts.officeSyndrome.officeSyndrome"
    - `default`: returned when key not found
    - `fmt_vars`: optional dict to format the prompt string via `str.format`
    """
    context = load_defaults()
    if not key:
        return default

    node = context
    for part in key.split('.'):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default

    # If we found a string and formatting vars are provided, try formatting
    if isinstance(node, str) and fmt_vars:
        try:
            return node.format(**fmt_vars)
        except Exception:
            return node

    return node


def load_chroma(chroma_name=''):
    """
    Docstring for load_chroma
    """
    # LOAD THE VECTOR DATABASE AND PREPARE RETRIEVAL
    try:
        if not chroma_name:
            chroma_name = load_defaults().get("chromaName", "")

        embeddings = GoogleGenerativeAIEmbeddings(
            api_key=os.getenv("GOOGLE_API_KEY"),
            model="models/gemini-embedding-001"
        )

        vectorstore = Chroma(
            embedding_function=embeddings,
            persist_directory="/chroma/" + chroma_name,
            client_settings=Settings(
                anonymized_telemetry=False,
                is_persistent=True,
            ),
        )
        return vectorstore.as_retriever()

    except:
        return None


def generate_raw(system_prompt, message_list, use_rag=True):
    """
    Generate a response either via RAG (retrieval-augmented generation) or
    directly from the LLM API.

    :param system_prompt: the system prompt string
    :param message_list: list of tuples (role, content) where role is "user" or "assistant"
    :param use_rag: if True, attempt to use the Chroma retriever + RAG chain
    :return: response string
    """
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-lite",
        temperature=1.0,  # Gemini 3.0+ defaults to 1.0
        max_tokens=1200,
        timeout=None,
        max_retries=2,
    )

    # Extract the last user message as input
    last_user_content = ""
    for role, content in reversed(message_list):
        if role == "user":
            last_user_content = content
            break

    retriever = None
    if use_rag:
        retriever = load_chroma()

    # If RAG requested and retriever available, use RAG chain
    if use_rag and retriever is not None:
        # Build ChatPromptTemplate for RAG
        prompt_template = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "{context}\n\nQuestion: {input}"),
        ])
        question_answer_chain = create_stuff_documents_chain(llm, prompt_template)
        rag_chain = create_retrieval_chain(retriever, question_answer_chain)

        # Invoke RAG chain with the content
        response = rag_chain.invoke({"input": last_user_content})
        return response.get("answer", response.text if hasattr(response, 'text') else str(response))

    # Direct LLM call (non-RAG)
    prompt_list = [("system", system_prompt)]
    prompt_list.extend(message_list)
    response = llm.invoke(prompt_list)
    return response.content.strip() if hasattr(response, 'content') else str(response).strip()


def generate_response(message_list, use_rag=True):
    """
    Generate a basic response.

    :param message_list: Description
    :param use_rag: Description
    """
    system_prompt = get_prompt("prompts.systemPrompt", default="You are a helpful assistant.")
    return generate_raw(system_prompt, message_list, use_rag=use_rag)


def generate_summary(message_list, use_rag=True):
    """
    Generate a daily summary (Overview and Risk by symptoms).
    """
    # Use specific keys from defaultContext.json
    summary_prompt = get_prompt("prompts.summaryPrompt", default="Please produce a short summary.")

    # Office syndrome prompts are nested under symptomPrompts.officeSyndrome
    office_risk_prompt = get_prompt("prompts.symptomPrompts.officeSyndrome.riskLevelPrompt",
                                   default="Rate risk: Low/Medium/High")
    office_summary_prompt = get_prompt("prompts.symptomPrompts.officeSyndrome.officeSyndrome",
                                       default="Provide a short office-syndrome summary.")

    # Call the same response generator (will use RAG if requested)
    summary = generate_raw(summary_prompt, message_list, use_rag=use_rag)
    office_risk = generate_raw(office_risk_prompt, message_list, use_rag=use_rag)
    office_summary = generate_raw(office_summary_prompt, message_list, use_rag=use_rag)

    return summary, office_risk, office_summary


# client = OpenAI(
#     api_key=os.getenv("CHAT_API_KEY"),
#     base_url="https://api.opentyphoon.ai/v1"
# )

# stream = client.chat.completions.create(
#     model="typhoon-v2.5-30b-a3b-instruct",
#     messages=[
#         {"role": "user", "content": content}
#     ],
#     stream=True
# )
