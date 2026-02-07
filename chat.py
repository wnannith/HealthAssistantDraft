"""
For LLM-related tasks
"""
# Core Libraries
import os
import io
import sys
import json
import re
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Annotated, Literal, TypedDict

# Core Langchain
from langchain_classic.schema import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, END

# Gemini LLMs
# from langchain.chat_models import init_chat_model
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai import GoogleGenerativeAIEmbeddings

# OpenAI LLMs
# from openai import OpenAI

# RAG
from langchain_chroma import Chroma
from chromadb.config import Settings


#########################   TOOLS   #########################


# Initial things
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
ctx = Path(__file__).parent / "context.json"
if ctx.exists():
    cfg = json.loads(ctx.read_text(encoding="utf-8"))
else:
    cfg = {}


# Get system propmts for LLM from the .json file
def get_prompt(key, default="", fmt_vars=None):
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


# Load system prompt from .json
def load_defaults(path=None):
    """Load the default system prompts from `defaultContext.json`. Returns a dict."""
    try:
        if path is None:
            path = Path(__file__).parent / "defaultContext.json"
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        print(f"Error loading defaultContext.json: {e}")
        return {}


def load_llm():
    """
    Docstring for load_llm
    
    :return: Description
    :rtype: Any
    """
    google_llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-lite",
        temperature=1.0,  # Gemini 3.0+ defaults to 1.0
        max_tokens=1200,
        timeout=None,
        max_retries=2,
    )

    typhoon_llm = init_chat_model(
        model="typhoon-v2.5-30b-a3b-instruct",
        model_provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url="https://api.opentyphoon.ai/v1"
    )

    return typhoon_llm


def load_chroma(chroma_name=''):
    """
    Docstring for load_chroma
    """
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


def format_docs(docs):
    """
    Docstring for format_docs
    
    :param docs: Description
    :return: Description
    :rtype: Any
    """
    return "\n\n".join(doc.page_content for doc in docs)


def format_messages(messages):
    """
    Docstring for format_messages
    
    :param messages: Description
    :return: Description
    :rtype: Any
    """
    parsed_messages = ""
    for msg in messages:
        # Handle dicts {"role", "content"} and BaseMessage objects
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            content = msg.get("content", "")
        else:
            # BaseMessage objects have .type and .content attributes
            role = msg.type if hasattr(msg, 'type') else 'user'
            content = msg.content if hasattr(msg, 'content') else str(msg)

        if not content:
            continue
        parsed_messages += f"{role}: {content.strip()}\n"
    return parsed_messages.strip()


#########################   RESPONSE   #########################


class AgentState(TypedDict):
    """
    A dataclass to provide states for the agaent
    
    :var Scale: Description
    """
    user_id = int | None
    messages: list
    question: str
    use_rag: bool

    severity_rate: int
    is_new_user: bool
    response: str | None
    interrupted: bool


class SeverityRate(BaseModel):
    """
    Integer value to rate the severity of the statements from 0 to 5. Based on medical triage.
    """
    rate: int = Field(description="Severity rate of the statement from 0-5.")


def rate_severity(state: AgentState):
    """
    Rate the severity using structural output for further decisions. Based of triage.
    
    :param state: Description
    :type state: AgentState
    :return: Description
    :rtype: Any
    """

    system_prompt = """You are a decisive classifier that determines the scale of triage of a set of statements.
    Only return the numeric value.

    Scale:
    0: No risks whatsoever, or without sufficient evidence.
    1: Mild skin rash, seasonal allergies, dry cough, minor bruise, or slight sore throat.
    2: Low-grade fever, persistent vomiting, sprained ankle, or a deep cut requiring a few stitches.
    3: High fever (>39.5°C), moderate dehydration, minor bone fractures, or persistent abdominal pain.
    4: Difficulty breathing (wheezing), sudden high-intensity pain, major bone fractures, or heavy bleeding.
    5: Sharp chest pain (cardiac arrest), unconsciousness, severe head trauma, or anaphylaxis.
    """

    rate_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("user", "Statements:\n{input}")
        ]
    )

    llm = load_llm()
    structured_llm = llm.with_structured_output(SeverityRate(rate=0), method="json_mode")
    rating_llm = rate_prompt | structured_llm

    formatted_msgs = format_messages(state["messages"])
    if not formatted_msgs.strip():
        formatted_msgs = get_prompt("defaultMessage", "Hello")

    # print(f"DEBUG: Input to LLM is: '{formatted_msgs}'") # If this prints '', that's your problem.
    response = rating_llm.invoke({"input": formatted_msgs})

    try:
        # structured_llm returns the Pydantic object directly
        response = rating_llm.invoke({"input": formatted_msgs})
        
        # If it's already the Pydantic model:
        if isinstance(response, SeverityRate):
            state["severity_rate"] = int(response.rate)
        # If it somehow returned a dict instead:
        elif isinstance(response, dict):
            state["severity_rate"] = int(response.get("rate", 0))
        else:
            state["severity_rate"] = 0
            
    except Exception as e:
        print(f"Parsing Error: {e}")
        # Logic for a "Safe Fallback"
        # If the LLM fails to categorize, we default to 0 to allow the flow to continue
        state["severity_rate"] = 0

    # match = re.match(r"[0-5]", response.rate)
    # state["severity_rate"] = int(match.group(0)) if match else 0
    return state


def severity_router(state: AgentState):
    """
    Docstring for severity_router
    
    :param state: Description
    :type state: AgentState
    """
    rate = state["severity_rate"]
    if rate >= 4:
        return "interrupt"
    return "continue"


def severity_interrupt(state: AgentState):
    """
    Docstring for severity_interrupt
    
    :param state: Description
    :type state: AgentState
    """
    state["interrupted"] = True
    return state


def is_new_user(state: AgentState):
    """
    Check if the user is a new user
    
    :param state: Description
    :type state: AgentState
    """
    state["is_new_user"] = False
    ### db stuff.....
    return state


def generate_raw(state: AgentState):
    """
    Generate a response either via RAG (retrieval-augmented generation) or
    directly from the LLM API.

    :param system_prompt: the system prompt string
    :param message_list: list of tuples (role, content) where role is "user" or "assistant"
    :param use_rag: if True, attempt to use the Chroma retriever + RAG chain
    :return: response string
    """

    messages = state["messages"]
    llm = load_llm()
    system = get_prompt("prompts.systemPrompt")
    use_rag = state["use_rag"]


    context = ""
    if use_rag:
        retriever = load_chroma()
        query = state.get("question", "").strip()
        
        # GUARD: Only invoke retriever if we actually have a query
        if retriever is not None and query:
            try:
                docs = retriever.invoke(query)
                context_data = format_docs(docs)
                system += "\n\n" + f"From the context provided below:\n\n{context}"
            except Exception as e:
                print(f"RAG Error: {e}")
                # Fallback: continue without context if retrieval fails
        elif not query:
            print("DEBUG: RAG enabled but question was empty. Skipping retrieval.")

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system),
            ("user", "Statements:\n{input}")
        ]
    )

    formatted_input = format_messages(messages)
    if not formatted_input:
        formatted_input = get_prompt("defaultMessage", "Hello")

    chain = prompt | llm
    if use_rag and retriever is not None:
        context_data = format_docs(retriever.invoke(state["question"]))
        response = chain.invoke({"context": context_data, "input": formatted_input})
    else:
        response = chain.invoke({"input": formatted_input})

    state["response"] = response.text.strip()
    return state


def set_graph_response():
    """
    Docstring for set_graph_response
    """

    workflow = StateGraph(AgentState)

    workflow.add_node("rate_severity", rate_severity)
    workflow.add_node("severity_interrupt", severity_interrupt)
    workflow.add_node("is_new_user", is_new_user)
    workflow.add_node("generate_raw", generate_raw)

    workflow.set_entry_point("rate_severity")
    workflow.add_conditional_edges(
        "rate_severity", 
        severity_router,
        {
            "continue": "is_new_user",
            "interrupt": "severity_interrupt"
        }
    )
    workflow.add_edge("is_new_user", "generate_raw")
    workflow.add_edge("generate_raw", END)
    workflow.add_edge("severity_interrupt", END)

    graph = workflow.compile()
    return graph


def generate_response(messages, use_rag=True):
    """
    Generate an AI response from a message list.
    
    :param messages: Description
    :param use_rag: Description
    """

    question = ""
    for msg in reversed(messages):
        # Handle dicts and BaseMessage objects
        if isinstance(msg, dict):
            role = msg.get("role", "")
            content = msg.get("content", "")
        else:
            # BaseMessage objects have .type and .content attributes
            role = msg.type if hasattr(msg, 'type') else ""
            content = msg.content if hasattr(msg, 'content') else ""
        
        # print(role, content)
        if role == "human":
            question = content.strip()
            if not question:
                question = get_prompt("defaultMessage")
            break

    # print(question)
    graph = set_graph_response()
    result = graph.invoke(
        input = {
            "user_id": None,
            "messages": messages,
            "question": question,
            "use_rag": use_rag,
            "interrupted": False,
            "response": ""
        }
    )

    warning = False
    response = result["response"]
    response += "\n\n-# ไม่ใช่คำวินิจฉัยทางการแพทย์ กรุณาปรึกษากับแพทย์ผู้ชำนาญการก่อนทุกครั้ง"

    if result["interrupted"]:
        return None, True

    if result["severity_rate"] >= 2:
        warning = True

    return response, warning


#########################   SUMMARY   #########################


class HealthSummary(BaseModel):
    """
    Daily Summary for the user.
    """
    summary: None | str = Field(description=get_prompt(
        "prompts.summaryPrompt",
        default="Please produce a short summary.")
    )
    office_risk: None | str = Field(description=get_prompt(
        "prompts.symptomPrompts.officeSyndrome.riskLevelPrompt",
        default="Rate risk: Low/Medium/High")
        )
    office_summary: None | str = Field(description=get_prompt(
        "prompts.symptomPrompts.officeSyndrome.officeSyndrome",
        default="Provide a short office-syndrome summary.")
    )


def generate_summary(message_list, use_rag=True):
    """
    Generate a daily summary (Overview and Risk by symptoms).
    """

    llm = load_llm()

    structured_llm = llm.with_structured_output(HealthSummary)
    # response = generate_raw('', message_list, use_rag=use_rag, llm=structured_llm)

    summary = response.text["summary"]
    office_risk = response.text["office_risk"]
    office_summary = response.text["office_summary"]

    return summary, office_risk, office_summary
