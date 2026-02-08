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
from datetime import date
from typing import Annotated, Literal, Optional, TypedDict

# User Database
import sqlite3

# Type Control
from pydantic import BaseModel, Field

# Core Langchain
from langchain_classic.schema import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, END

# Gemini LLMs
# from langchain.chat_models import init_chat_model
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai import GoogleGenerativeAIEmbeddings

# OpenAI LLMs
from langchain_openai import OpenAIEmbeddings

# RAG
from langchain_chroma import Chroma
from chromadb.config import Settings


# Initial things
ALTER = False

try:
    # On normal Python interpreters, replace stdout/stderr with UTF-8 wrappers
    # that use the underlying binary buffer. In environments like Jupyter
    # `sys.stdout` may be an OutStream without a `.buffer` attribute, so
    # guard against AttributeError and other exceptions.
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
except Exception:
    # Leave stdout/stderr as-is (Jupyter/IPython will handle encoding).
    pass




#########################   DATA CLASSES   #########################


class AgentState(TypedDict):
    """
    A dataclass to provide states for the agaent
    
    :var Scale: Description
    """
    user_id: int | None
    messages: list
    question: str
    topic: Literal["ask", "update", "update_ask"] | None

    use_info: bool
    is_new_user: bool
    user_info: dict | None
    pending_extraction: dict | None  # Data waiting for user approval

    use_rag: bool
    severity_rate: int
    response: str | None
    interrupted: bool


class SeverityRate(BaseModel):
    """
    Integer value to rate the severity of the statements from 0 to 5. Based on medical triage.
    """
    rate: int = Field(description="Severity rate of the statement from 0-5.")


class TopicChecklist(BaseModel):
    """
    Checklists for a user's sentiment, used for deciding the topic.
    """
    has_info: bool = Field(description="Whether the sentiment contains their personal info.")
    is_question: bool = Field(description="Whether the sentiment is a question.")


class ProfileStructure(BaseModel):
    """
    Extracted physical and personal details from the conversation.
    """
    name: Optional[str] = Field(description="The user's full name or nickname")
    dob: Optional[str] = Field(description="Date of birth in YYYY-MM-DD format. If only age is mentioned, estimate based on 2026.")
    occupation: Optional[str] = Field(description="The user's job or main daily activity")
    description: Optional[str] = Field(description="Description of lifestyle, e.g., active, sedentary, plays sports")
    chronic_disease: Optional[str] = Field(description="Any mentioned long-term illnesses or conditions")

    weight: Optional[int] = Field(description="Weight in kilograms")
    height: Optional[int] = Field(description="Height in centimeters")




#########################   TOOLS   #########################


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
    """Load the default system prompts from `default_context.json`. Returns a dict."""
    try:
        if path is None:
            path = Path(__file__).parent / "default_context.json"
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        print(f"Error loading default_context.json: {e}")
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
        api_key=os.getenv("OPENTYPHOON_API_KEY"),
        base_url="https://api.opentyphoon.ai/v1",
        max_tokens=1200
    )

    if ALTER:
        return typhoon_llm
    return google_llm


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

        if ALTER:
            embeddings = OpenAIEmbeddings(
                model="text-embedding-3-small",
                openai_api_key=os.getenv("OPENAI_API_KEY")
            )

        vectorstore = Chroma(
            embedding_function=embeddings,
            persist_directory=chroma_name,
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


def fetch_user_context(user_id: int):
    """Fetch profile and the most recent BMI record."""
    try:
        conn = sqlite3.connect(load_defaults().get("databaseName"))
        # Use Row factory to access columns by name
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Query joining Users and their latest BMI record
        query = """
            SELECT u.name, u.dob, u.occupation, u.description, u.chronic_disease, 
                   b.weight, b.height
            FROM Users u
            LEFT JOIN UserBMIRecords b ON u.userID = b.userID
            WHERE u.userID = ?
            ORDER BY b.datetime DESC
            LIMIT 1
        """
        cursor.execute(query, (user_id,))
        row = cursor.fetchone()
        conn.close()

        age = "None"
        if row["dob"]:
            birth_date = date.fromisoformat(row["dob"])
            today = date.today()
            age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))

        if row:
            # Formatting the data into a readable block for the LLM
            return {
                "name": row["name"],
                "age": age,
                "occupation": row["occupation"],
                "lifestyle": row["description"],
                "chronic": row["chronic_disease"] or "None",
                "weight": row["weight"],
                "height": row["height"]
            }

    except Exception as e:
        print(f"Database error: {e}")

    return None


def get_bmi_analysis(weight, height):
    """Calculate BMI and return category."""
    if not weight or not height or height <= 0:
        return "Unknown"

    # Formula: weight (kg) / [height (m)]^2
    height_m = height / 100
    bmi = weight / (height_m ** 2)

    if bmi < 18.5:
        category = "Underweight"
    elif 18.5 <= bmi < 25:
        category = "Normal weight"
    elif 25 <= bmi < 30:
        category = "Overweight"
    else:
        category = "Obese"

    return f"{bmi:.1f} ({category})"


def format_user_persona(user_data: dict):
    """
    Docstring for format_user_persona
    
    :param user_data: Description
    :type user_data: dict
    :return: Description
    :rtype: Any
    """

    if not user_data:
        return ""

    bmi_info = get_bmi_analysis(user_data.get('weight'), user_data.get('height'))

    return (
        f"Name: {user_data['name']}\n"
        f"Age: {user_data['age']}\n"
        f"Occupation: {user_data['occupation']}\n"
        f"BMI Status: {bmi_info}\n"
        f"Known Chronic Diseases: {user_data['chronic']}\n"
        f"Daily Lifestyle: {user_data['lifestyle']}\n"
    )


def save_extracted_profile(user_id, extracted_data: ProfileStructure):
    """
    Docstring for save_extracted_profile
    
    :param user_id: Description
    :param extracted_data: Description
    :type extracted_data: ProfileStructure
    """
    conn = sqlite3.connect(load_defaults().get("databaseName"))
    cursor = conn.cursor()
    
    # Update Users Table (only non-null fields)
    update_fields = []
    params = []
    
    for field, value in extracted_data.model_dump().items():
        if value is not None and field not in ["weight", "height"]:
            update_fields.append(f"{field} = ?")
            params.append(value)
            
    if update_fields:
        params.append(user_id)
        cursor.execute(f"UPDATE Users SET {', '.join(update_fields)} WHERE userID = ?", params)

    # Insert BMI Record if weight/height are found
    if extracted_data.weight or extracted_data.height:
        # Get existing values as fallback if only one is provided
        cursor.execute("SELECT weight, height FROM UserBMIRecords WHERE userID = ? ORDER BY datetime DESC LIMIT 1", (user_id,))
        existing = cursor.fetchone()
        
        w = extracted_data.weight or (existing[0] if existing else None)
        h = extracted_data.height or (existing[1] if existing else None)
        
        import time
        cursor.execute(
            "INSERT INTO UserBMIRecords (userID, datetime, weight, height) VALUES (?, ?, ?, ?)",
            (user_id, int(time.time()), w, h)
        )
    
    conn.commit()
    conn.close()




#########################   RESPONSE   #########################


def load_user_info(state: AgentState):
    """
    Loads user context and initializes records for new users.
    """
    if not state.get("use_info"):
        return state

    user_id = state.get("user_id")
    if not user_id:
        return state

    # 1. Attempt to fetch existing data
    user_data = fetch_user_context(user_id)

    if user_data is None:
        # 2. This is a new user - Create a blank record in SQLite
        try:
            conn = sqlite3.connect(get_prompt("databaseName"))
            cursor = conn.cursor()
            # We insert with just the ID; other fields remain NULL until extracted
            cursor.execute(
                'INSERT OR IGNORE INTO "Users" (userID) VALUES (?)', 
                (user_id,)
            )
            conn.commit()
            conn.close()

            state["is_new_user"] = True
            state["user_info"] = None # Still no data to show yet
            print(f"DEBUG: Initialized new record for user {user_id}")
        except Exception as e:
            print(f"Error initializing new user: {e}")
    else:
        # 3. Existing user found
        state["is_new_user"] = False
        state["user_info"] = user_data

    return state


def rate_severity(state: AgentState):
    """
    Rate the severity using structural output for further decisions. Based of triage.
    
    :param state: Description
    :type state: AgentState
    :return: Description
    :rtype: Any
    """

    system_prompt = """You are a decisive medical classifier.
    You are provided with messages between user and their health assistant.
    You MUST determine the severity scale of a set of statements.
    You MUST Return your response as a JSON object with the key 'rate'.
    Example: {{"rate": 2}}

    Scale:
    0: No risks whatsoever, or without sufficient evidence.
    1: Mild skin rash, seasonal allergies, dry cough, minor bruise, or slight sore throat.
    2: Low-grade fever, persistent vomiting, sprained ankle, or a deep cut requiring a few stitches.
    3: High fever (>39.5°C), moderate dehydration, minor bone fractures, or persistent abdominal pain.
    4: Difficulty breathing (wheezing), sudden high-intensity pain, major bone fractures, or heavy bleeding.
    5: Sharp chest pain (cardiac arrest), unconsciousness, severe head trauma, or anaphylaxis.
    """

    user_info = state.get("user_info")
    persona_context = ""

    if user_info:
        persona_context = format_user_persona(state.get("user_info"))
    # Inject into the system prompt
    if persona_context:
        system_prompt += f"\n\nAbout User:\n{persona_context}"

    rate_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("user", "Statements:\n{input}")
        ]
    )

    llm = load_llm()
    structured_llm = llm.with_structured_output(SeverityRate, method="json_mode")
    rating_llm = rate_prompt | structured_llm

    try:
        formatted_msgs = format_messages(state["messages"])
        response = rating_llm.invoke({"input": formatted_msgs})
        state["severity_rate"] = response.rate
        # match = re.match(r"[0-5]", response.rate)
        # state["severity_rate"] = int(match.group(0)) if match else 0
    except Exception as e:
        print(f"Severity Logic Failed to parse JSON. Raw output was likely chat text. Defaulting to 0.")
        # If it failed, it's usually because the bot was being too 'chatty' (Severity 0)
        state["severity_rate"] = 0
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


def extract_topic(state: AgentState):
    """
    Decides if the latest message contains profile-related information.
    """

    if state.get("topic") is not None:
        return state

    llm = load_llm()
    structured_llm = llm.with_structured_output(TopicChecklist, method="json_mode")

    system_prompt = (
        "You are a linguistic classifier. Analyze the user's message for two specific things:\n"
        "1. has_info: Does the user provide personal details (name, weight, job, etc.)?\n"
        "2. is_question: Is the user asking a health-related question?\n\n"
        "Return ONLY a JSON object: {{\"has_info\": boolean, \"is_question\": boolean}}"
    )

    # We only look at the most recent message to save tokens
    last_message = state["question"]
    response = structured_llm.invoke([
        ("system", system_prompt),
        ("user", last_message)
    ])

    try:
        response = structured_llm.invoke([
            ("system", system_prompt),
            ("user", state["question"])
        ])
        if response.has_info and response.is_question:
            state["topic"] = 'update_ask'
        elif response.has_info:
            state["topic"] = 'update'
        else:
            state["topic"] = 'ask'

    except Exception as e:
        print(f"Topic Extraction Error: {e}. Falling back to 'ask'.")
        # Default to 'ask' to ensure the user gets a response even if classification fails
        state["topic"] = 'ask'

    return state


def topic_router(state: AgentState):
    """
    Docstring for topic_router
    
    :param state: Description
    :type state: AgentState
    """
    if state.get("topic") == 'ask':
        return 'ask'
    return 'update'


def extract_profile(state: AgentState):
    # 1. Extract as usual
    """
    Extracts structured user data from a list of messages.
    """
    messages = state.get("messages")
    llm = load_llm()
    # Use json_mode for better reliability in Thai/English mixed contexts
    structured_llm = llm.with_structured_output(ProfileStructure, method="json_mode")

    system_prompt = """You are an expert data extraction bot specialized in Thai context.
    Extract user profile information from the Thai conversation.

    Rules:
    1. Name: If a user provides a nickname (ชื่อเล่น), store it. If they provide both, prefer the formal name but note the nickname.
    2. DOB: Thai users may use the Buddhist Era (ปี พ.ศ.). 
    - Formula: A.D. = B.E. - 543. 
    - Example: พ.ศ. 2539 becomes 1996.
    3. Occupation: Translate Thai occupations to English (e.g., 'พนักงานออฟฟิศ' -> 'Office Worker').
    4. Current Year: 2026.
    5. If info is missing, return null. Do not hallucinate.
    """

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("user", "{input}")
    ])

    chain = prompt | structured_llm
    formatted_history = format_messages(messages)
    extracted = chain.invoke({"input": formatted_history})

    state["pending_extraction"] = extracted.model_dump(exclude_none=True)
    return state


def after_extract_router(state: AgentState):
    """
    Docstring for after_update_router
    
    :param state: Description
    :type state: AgentState
    """
    if state.get("topic") == "update_ask":
        return "ask"
    return "end"


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
    system_prompt = get_prompt("prompts.systemPrompt")

    user_info = state.get("user_info")
    persona_context = ""

    if user_info:
        persona_context = format_user_persona(state.get("user_info"))
        system_prompt += f"\n\nYou are assisting the following user:\n{persona_context}"
    elif state.get("is_new_user"):
        system_prompt += (
            "\n\nIMPORTANT: You are meeting this user for the first time. "
            "Please introduce yourself and politely ask for their name, age, "
            "and what they do for a living so you can give better health advice."
        )

    retriever = None
    context_data = ""
    if state.get("use_rag"):
        retriever = load_chroma()
        query = state.get("question", "").strip()

        # GUARD: Only invoke retriever if we actually have a query
        if retriever is not None and query:
            try:
                docs = retriever.invoke(query)
                context_data = format_docs(docs)
                system_prompt += "\n\n" + f"From the context provided below:\n\n{context_data}"
            except Exception as e:
                print(f"RAG Error: {e}")
                # Fallback: continue without context if retrieval fails
        elif not query:
            print("DEBUG: RAG enabled but question was empty. Skipping retrieval.")

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("user", "Statements:\n{input}")
        ]
    )

    formatted_input = format_messages(messages)
    if not formatted_input:
        formatted_input = get_prompt("defaultMessage", "Hello")

    chain = prompt | llm
    if retriever is not None:
        context_data = format_docs(retriever.invoke(state["question"]))
        response = chain.invoke({"context": context_data, "input": formatted_input})
    else:
        response = chain.invoke({"input": formatted_input})

    state["response"] = response.content.strip() if hasattr(response, 'content') else str(response)
    return state


def set_graph_response():
    """
    Docstring for set_graph_response
    """

    workflow = StateGraph(AgentState)

    workflow.add_node("load_user_info", load_user_info)
    workflow.add_node("rate_severity", rate_severity)
    workflow.add_node("severity_interrupt", severity_interrupt)
    workflow.add_node("extract_topic", extract_topic)
    workflow.add_node("extract_profile", extract_profile)
    workflow.add_node("generate_raw", generate_raw)

    workflow.set_entry_point("load_user_info")
    workflow.add_edge("load_user_info", "rate_severity")
    workflow.add_conditional_edges(
        "rate_severity", 
        severity_router,
        {
            "continue": "extract_topic",
            "interrupt": "severity_interrupt"
        }
    )
    workflow.add_conditional_edges(
        "extract_topic",
        topic_router,
        {
            "ask": "generate_raw",
            "update": "extract_profile"
        }
    )
    workflow.add_conditional_edges(
        "extract_profile",
        after_extract_router,
        {
            "ask": "generate_raw",
            "end": END
        }
    )
    workflow.add_edge("generate_raw", END)
    workflow.add_edge("severity_interrupt", END)

    graph = workflow.compile()
    return graph


def generate_response(
        messages,
        user_id=None,
        topic=None,
        use_info=True,
        use_rag=True
    ):
    """
    Generate an AI response from a message list.
    
    :param messages: Description
    :param use_rag: Description
    """

    if user_id is None:
        use_info = False

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
            "user_id": user_id,
            "messages": messages,
            "question": question,
            "topic": topic,

            "use_info": use_info,
            "is_new_user": False,
            "user_info": None,
            "pending_extraction": None,

            "use_rag": use_rag,
            "severity_rate": 0,
            "response": "",
            "interrupted": False
        }
    )

    response = result["response"]
    response += "\n\n-# ไม่ใช่คำวินิจฉัยทางการแพทย์ กรุณาปรึกษากับแพทย์ผู้ชำนาญการก่อนทุกครั้ง"

    return response, result




#########################   SUMMARY   #########################


class HealthSummary(BaseModel):
    """
    Daily Summary for the user.
    """
    overview: Optional[str] = Field(
        description=get_prompt(
            "prompts.summaryPrompt",
            default="Please produce a short summary."
        ),
        example="ผู้ใช้งานมีอาการปวดหลังเล็กน้อยจากการนั่งทำงาน"
    )
    office_risk: Optional[Literal["Low", "Medium", "High"]] = Field(
        description=get_prompt(
            "prompts.symptomPrompts.officeSyndrome.riskLevelPrompt",
            default="Rate risk: Low/Medium/High"
            )
        )
    office_summary: Optional[str] = Field(
        description=get_prompt(
            "prompts.symptomPrompts.officeSyndrome.riskSummaryPrompt",
            default="Provide a short office-syndrome summary."
        ),
        example="ควรลุกขึ้นยืดเหยียดทุกๆ 1 ชั่วโมง"
    )


def generate_summary(messages, user_id=None, use_rag=True):
    """
    Generate a daily summary (Overview and Risk by symptoms).
    """
    llm = load_llm()

    # method="json_mode" forces Gemini to output a valid JSON string
    structured_llm = llm.with_structured_output(HealthSummary, method="json_mode")

    # 1. Clear Instructions
    system_prompt = (
        "You are a medical secretary. Summarize the user's health conversation.\n"
        "You MUST return a JSON object with EXACTLY these three keys:\n"
        "1. 'overview': A summary of the user's health status in Thai.\n"
        "2. 'office_risk': Either 'Low', 'Medium', or 'High'.\n"
        "3. 'office_summary': Advice regarding Office Syndrome in Thai.\n\n"
        "Do not use other keys like 'chief_complaint' or 'action'."
    )

    user_info = {}
    if user_id:
        user_info = fetch_user_context(user_id)
    if user_info:
        persona_context = format_user_persona(user_info)
        system_prompt += f"\n\nAbout User:\n{persona_context}"

    formatted_msg = format_messages(messages)
    context_data = ""

    # 2. RAG Logic (Fixed extraction and check)
    if use_rag and formatted_msg.strip():
        retriever = load_chroma()
        if retriever:
            try:
                # We use the full conversation context to find relevant medical docs
                docs = retriever.invoke(formatted_msg)
                context_data = format_docs(docs)
            except Exception as e:
                print(f"RAG Error: {e}")

    # 3. Proper ChatPromptTemplate structure
    # This separates the 'system_prompt' from the 'Data'
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("system", "Related Medical Context:\n{context}"),
        ("user", "Conversation to Summarize:\n{input}")
    ])

    chain = prompt | structured_llm

    # 4. Invoke and Handle Potential Failures

    try:
        # Standard structured call
        summary_result = chain.invoke({
            "context": context_data if context_data else "No additional context.",
            "input": formatted_msg
        })

        # If returns the Pydantic object
        if isinstance(summary_result, HealthSummary):
            return summary_result.model_dump()
        return summary_result # If it's already a dict
  
    except Exception as e:
        print(f"Summary Generation Error: {e}")
        # Return a clean fallback dict that matches the HealthSummary structure
        return {
            "overview": "ขออภัย ไม่สามารถสรุปข้อมูลได้ในขณะนี้",
            "office_risk": "Unknown",
            "office_summary": "Unknown"
        }
