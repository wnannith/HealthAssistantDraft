"""
For LLM-related tasks
"""

# Core Libraries
import os
import io
import sys
import json
import re
import time
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
ALTER = True

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
    user_id: int | None = None
    messages: list
    question: str
    topic: Literal["ask", "update", "update_ask"] | None = None

    use_info: bool
    is_new_user: bool = False
    user_info: dict | None = None
    user_context: str | None = None
    pending_extraction: dict | None = None  # Data waiting for user approval

    use_rag: bool
    documents: str | None

    invoke_qa: dict | None = {}
    severity_rate: int = 0
    response: str | None = ""
    interrupted: bool = False


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


class ProfileStructure(BaseModel):
    # User Table
    name: Optional[str] = None
    dob: Optional[str] = None
    gender: Optional[str] = None
    occupation: Optional[str] = None
    description: Optional[str] = None
    chronic_disease: Optional[str] = None
    
    # BMI Table
    weight: Optional[int] = None
    height: Optional[int] = None
    
    # Activity Table
    steps: Optional[int] = None
    sleep_hours: Optional[float] = None
    calories_burned: Optional[float] = None
    avg_heart_rate: Optional[float] = None
    active_minutes: Optional[float] = None




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
    opt = node
    if isinstance(opt, list):
        opt = "\n".join(node)

    if isinstance(opt, str) and fmt_vars:
        try:
            return opt.format(**fmt_vars)
        except Exception:
            return opt

    return opt


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

    typhoon_llm = init_chat_model(
        model="typhoon-v2.5-30b-a3b-instruct",
        model_provider="openai",
        api_key=os.getenv("OPENTYPHOON_API_KEY"),
        base_url="https://api.opentyphoon.ai/v1",
        max_tokens=4800
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


def format_messages(messages, max_chars=12000):
    """
    แปลงข้อความเป็น String โดยเลือกเก็บข้อความล่าสุด (Bottom-up) 
    เพื่อให้ไม่เกินขีดจำกัดของ Token (ประมาณ 4,000 tokens สำหรับไทย-อังกฤษ)
    """
    parsed_lines = []
    current_length = 0

    for msg in reversed(messages):
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            content = msg.get("content", "")
        else:
            role = msg.type if hasattr(msg, 'type') else 'user'
            content = msg.content if hasattr(msg, 'content') else str(msg)

        if not content:
            continue

        formatted_line = f"{role}: {content.strip()}\n"

        if current_length + len(formatted_line) > max_chars:
            break
            
        parsed_lines.append(formatted_line)
        current_length += len(formatted_line)

    parsed_lines.reverse()
    
    return "".join(parsed_lines).strip()


def connect_db():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(current_dir, get_prompt("databaseName"))
    conn = sqlite3.connect(db_path)
    return conn


def fetch_user_info(user_id: int):
    """Fetch profile, latest BMI, today's activity, and latest summary."""
    try:
        conn = connect_db()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        today = date.today().isoformat()

        query = """
            SELECT 
                u.*,
                (SELECT weight FROM UserBMIRecords WHERE user_id = u.user_id ORDER BY date DESC LIMIT 1) as latest_weight,
                (SELECT height FROM UserBMIRecords WHERE user_id = u.user_id ORDER BY date DESC LIMIT 1) as latest_height,
                
                -- Activity Data for Today
                (SELECT steps FROM UserActivityRecords WHERE user_id = u.user_id AND date = ?) as t_steps,
                (SELECT sleep_hours FROM UserActivityRecords WHERE user_id = u.user_id AND date = ?) as t_sleep,
                (SELECT calories_burned FROM UserActivityRecords WHERE user_id = u.user_id AND date = ?) as t_cal,
                (SELECT avg_heart_rate FROM UserActivityRecords WHERE user_id = u.user_id AND date = ?) as t_hr,
                (SELECT active_minutes FROM UserActivityRecords WHERE user_id = u.user_id AND date = ?) as t_min,
                
                -- Latest Summary Data
                (SELECT overview FROM UserSummaryRecords WHERE user_id = u.user_id ORDER BY date DESC LIMIT 1) as s_overview,
                (SELECT office_risk FROM UserSummaryRecords WHERE user_id = u.user_id ORDER BY date DESC LIMIT 1) as s_risk,
                (SELECT office_summary FROM UserSummaryRecords WHERE user_id = u.user_id ORDER BY date DESC LIMIT 1) as s_office
            FROM Users u
            WHERE u.user_id = ?
        """
        
        cursor.execute(query, (today, today, today, today, today, user_id))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        # Age calculation
        age = "Unknown"
        if row["dob"]:
            try:
                birth_date = date.fromisoformat(row["dob"])
                today_dt = date.today()
                age = today_dt.year - birth_date.year - ((today_dt.month, today_dt.day) < (birth_date.month, birth_date.day))
            except: pass

        return {
            "name": row["name"] or "Unknown",
            "age": age,
            "gender": row["gender"] or "Not specified",
            "occupation": row["occupation"] or "None",
            "lifestyle": row["description"] or "None",
            "chronic": row["chronic_disease"] or "None",
            "weight": row["latest_weight"],
            "height": row["latest_height"],
            "activity": {
                "steps": row["t_steps"] or 0,
                "sleep_hours": row["t_sleep"] or 0,
                "calories_burned": row["t_cal"] or 0,
                "avg_heart_rate": row["t_hr"] or 0,
                "active_minutes": row["t_min"] or 0
            },
            "summary": {
                "overview": row["s_overview"] or "None",
                "office_risk": row["s_risk"] or "None",
                "office_summary": row["s_office"] or "None"
            }
        }

    except Exception as e:
        print(f"Database error in fetch_user_info: {e}")
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


def format_user_info(user_info: dict):
    if not user_info:
        return ""

    sections = []
    
    # --- 1. User Profile ---
    profile = []
    if user_info.get('name'): profile.append(f"Name: {user_info['name']}")
    if user_info.get('age') and user_info['age'] != "Unknown": profile.append(f"Age: {user_info['age']}")
    if user_info.get('gender') and user_info['gender'] != "Not specified": profile.append(f"Gender: {user_info['gender']}")
    if user_info.get('occupation'): profile.append(f"Occupation: {user_info['occupation']}")
    
    bmi_info = get_bmi_analysis(user_info.get('weight'), user_info.get('height'))
    if bmi_info != "Unknown": profile.append(f"BMI Status: {bmi_info}")
    
    if user_info.get('chronic') and user_info['chronic'] != "None": 
        profile.append(f"Chronic Diseases: {user_info['chronic']}")
    if user_info.get('lifestyle') and user_info['lifestyle'] != "None": 
        profile.append(f"Lifestyle: {user_info['lifestyle']}")
    
    if profile:
        sections.append("### User Profile\n" + "\n".join(profile))

    # --- 2. Today's Activity ---
    act = user_info.get('activity', {})
    activity = []
    # กรองเฉพาะค่าที่มีตัวเลขมากกว่า 0
    if act.get('steps'): activity.append(f"- Steps: {act['steps']}")
    if act.get('sleep_hours'): activity.append(f"- Sleep: {act['sleep_hours']} hours")
    if act.get('calories_burned'): activity.append(f"- Calories: {act['calories_burned']} kcal")
    if act.get('avg_heart_rate'): activity.append(f"- Heart Rate: {act['avg_heart_rate']} bpm")
    if act.get('active_minutes'): activity.append(f"- Active Mins: {act['active_minutes']} mins")
    
    if activity:
        sections.append("### Today's Activity\n" + "\n".join(activity))

    # --- 3. Previous Summary ---
    sum_data = user_info.get('summary', {})
    summary = []
    if sum_data.get('overview') and sum_data['overview'] != 'None':
        summary.append(f"- Last Overview: {sum_data['overview']}")
    if sum_data.get('office_risk') and sum_data['office_risk'] != "None":
        summary.append(f"- Office Syndrome Risk: {sum_data['office_risk']}")
    if sum_data.get('office_summary') and sum_data['office_summary'] != "None":
        summary.append(f"- Office Syndrome Summary: {sum_data['office_summary']}")

    if summary:
        sections.append("### Previous Health Summary\n" + "\n".join(summary))

    return "\n\n".join(sections)


def save_extracted_profile(user_id, extracted_data: ProfileStructure):
    conn = connect_db()
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()
    today = date.today().isoformat()
    data_dict = extracted_data.model_dump(exclude_none=True)

    try:
        # 1. Upsert Users Table
        user_fields = ["name", "dob", "gender", "occupation", "description", "chronic_disease"]
        if any(field in data_dict for field in user_fields):
            cursor.execute(
                """
                    INSERT INTO Users (user_id, name, dob, gender, occupation, description, chronic_disease)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        name = COALESCE(excluded.name, name),
                        dob = COALESCE(excluded.dob, dob),
                        gender = COALESCE(excluded.gender, gender),
                        occupation = COALESCE(excluded.occupation, occupation),
                        description = COALESCE(excluded.description, description),
                        chronic_disease = COALESCE(excluded.chronic_disease, chronic_disease)
                """
                , (
                    user_id,
                    data_dict.get("name"),
                    data_dict.get("dob"),
                    data_dict.get("gender"),
                    data_dict.get("occupation"),
                    data_dict.get("description"),
                    data_dict.get("chronic_disease")
                )
            )

        # 2. Upsert BMI Records (Weight/Height)
        if "weight" in data_dict or "height" in data_dict:
            cursor.execute("""
                INSERT INTO UserBMIRecords (user_id, date, weight, height)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, date) DO UPDATE SET
                    weight = COALESCE(excluded.weight, weight),
                    height = COALESCE(excluded.height, height)
            """, (user_id, today, data_dict.get("weight"), data_dict.get("height")))

        # 3. Upsert Activity Records
        activity_fields = ["steps", "sleep_hours", "calories_burned", "avg_heart_rate", "active_minutes"]
        if any(field in data_dict for field in activity_fields):
            cursor.execute("""
                INSERT INTO UserActivityRecords (user_id, date, steps, sleep_hours, calories_burned, avg_heart_rate, active_minutes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, date) DO UPDATE SET
                    steps = COALESCE(excluded.steps, steps),
                    sleep_hours = COALESCE(excluded.sleep_hours, sleep_hours),
                    calories_burned = COALESCE(excluded.calories_burned, calories_burned),
                    avg_heart_rate = COALESCE(excluded.avg_heart_rate, avg_heart_rate),
                    active_minutes = COALESCE(excluded.active_minutes, active_minutes)
            """, (
                user_id, today, 
                data_dict.get("steps"), data_dict.get("sleep_hours"), 
                data_dict.get("calories_burned"), data_dict.get("avg_heart_rate"), 
                data_dict.get("active_minutes")
            ))

        conn.commit()
    except Exception as e:
        print(f"Error in save_extracted_profile: {e}")
        conn.rollback()
    finally:
        conn.close()


def create_invoke_qa(state: AgentState, node_name, prompt, response):
    """
    Docstring for create_invoke_qa
    
    :param state: Description
    :type state: AgentState
    """
    state["invoke_qa"][node_name] = {
        "prompt": prompt,
        "response": str(response).strip()
    }
    return state




#########################   RESPONSE   #########################


def load_user_info(state: AgentState):
    """
    Loads user context and initializes records for new users.
    """
    # RAG
    retriever = None
    if state.get("use_rag"):
        retriever = load_chroma()
        query = format_messages(state.get("messages"))

        # GUARD: Only invoke retriever if we actually have a query
        if retriever is not None and query:
            try:
                docs = retriever.invoke(query)
                state["documents"] = format_docs(docs)
            except Exception as e:
                print(f"RAG Error: {e}")
                # Fallback: continue without context if retrieval fails
        elif not query:
            print("DEBUG: RAG enabled but question was empty. Skipping retrieval.")

    if not state.get("use_info"):
        return state

    user_id = state.get("user_id")
    if not user_id:
        return state

    # 1. Attempt to fetch existing data
    user_info = fetch_user_info(user_id)

    if not user_info:
        # 2. This is a new user - Create a blank record in SQLite
        try:
            conn = connect_db()
            cursor = conn.cursor()
            # We insert with just the ID; other fields remain NULL until extracted
            cursor.execute(
                'INSERT OR IGNORE INTO "Users" (user_id) VALUES (?)', 
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
        state["user_info"] = user_info
        state["user_context"] = format_user_info(user_info)

    return state


def rate_severity(state: AgentState):
    """
    Rate the severity using structural output for further decisions. Based of triage.
    
    :param state: Description
    :type state: AgentState
    :return: Description
    :rtype: Any
    """

    system_prompt = get_prompt("prompts.nodePrompts.rate_severity")

    user_context = state.get("user_context")
    if user_context:
        system_prompt += f"\n\nAbout User:\n{user_context}"

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
        state = create_invoke_qa(state, "rate_severity", system_prompt, response)
        # match = re.match(r"[0-5]", response.rate)
        # state["severity_rate"] = int(match.group(0)) if match else 0
    except Exception as e:
        print("Error", str(e))
        print("Severity Logic Failed to parse JSON. Defaulting to 0.")
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
    system_prompt = get_prompt("prompts.nodePrompts.extract_topic")

    try:
        response = structured_llm.invoke([
            ("system", system_prompt),
            ("user", state.get("question"))
        ])
        if response.has_info:
            state["topic"] = 'update_ask'
        else:
            state["topic"] = 'ask'

        state = create_invoke_qa(state, "extract_topic", system_prompt, response)

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
    llm = load_llm()
    # Use json_mode for better reliability in Thai/English mixed contexts
    structured_llm = llm.with_structured_output(ProfileStructure, method="json_mode")

    system_prompt = get_prompt("prompts.nodePrompts.extract_profile")

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("user", "{input}")
    ])

    chain = prompt | structured_llm
    extracted = chain.invoke({"input": state.get("question")})

    state["pending_extraction"] = extracted.model_dump(exclude_none=True)
    state = create_invoke_qa(state, "extract_profile", system_prompt, extracted)
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
    Generate a response either from the LLM API.

    :param system_prompt: the system prompt string
    :param message_list: list of tuples (role, content) where role is "user" or "assistant"
    :param use_rag: if True, attempt to use the Chroma retriever + RAG chain
    :return: response string
    """

    messages = state["messages"]
    llm = load_llm()
    system_prompt = get_prompt("prompts.systemPrompt")

    user_context = state.get("user_context")
    if user_context and state.get("use_info"):
        system_prompt += f"\n\nYou are assisting the following user:\n{user_context}"
    elif state.get("is_new_user"):
        system_prompt += get_prompt("prompts.introPrompt")

    documents = state.get("documents")
    if documents:
        system_prompt += "\n\n" + f"Use the context provided below:\n\n{documents}"

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("user", "Statements:\n{input}")
        ]
    )

    formatted_input = format_messages(messages)
    if not formatted_input.strip():
        formatted_input = "Hello"

    chain = prompt | llm
    response = chain.invoke({"input": formatted_input})
    state["response"] = response.content.strip() if hasattr(response, 'content') else str(response)
    state = create_invoke_qa(state, "generate_raw", system_prompt, response)
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
        if role in ["human", "user"]:
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
            "user_context": None,
            "pending_extraction": None,

            "use_rag": use_rag,
            "documents": None,

            "invoke_qa": {},
            "severity_rate": 0,
            "response": "",
            "interrupted": False
        }
    )

    response = result["response"]
    return response, result




#########################   SUMMARY   #########################


def save_summary_to_db(user_id, summary_dict):
    """Saves the LLM-generated health summary to the database."""
    conn = connect_db()
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()

    today = date.today().isoformat()

    cursor.execute(
        'INSERT OR IGNORE INTO "Users" (user_id) VALUES (?)', 
        (user_id,)
    )

    query = """
    INSERT INTO UserSummaryRecords (user_id, date, overview, office_risk, office_summary)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(user_id, date) DO UPDATE SET
        overview = excluded.overview,
        office_risk = excluded.office_risk,
        office_summary = excluded.office_summary;
    """
    
    try:
        cursor.execute(query, (
            user_id, 
            today, 
            summary_dict.get("overview"),
            summary_dict.get("office_risk"),
            summary_dict.get("office_summary")
        ))
        conn.commit()
    except sqlite3.Error as e:
        print(f"Error saving summary: {e}")
    finally:
        conn.close()


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
        user_info = fetch_user_info(user_id)
    if user_info:
        user_context = format_user_info(user_info)
        system_prompt += f"\n\nAbout User:\n{user_context}"

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

        final_dict = summary_result.model_dump() if hasattr(summary_result, 'model_dump') else summary_result
        if user_id:
            save_summary_to_db(user_id, final_dict)

        return final_dict, user_info

    except Exception as e:
        print(f"Summary Generation Error: {e}")
        # Return a clean fallback dict that matches the HealthSummary structure
        return {
            "overview": "ขออภัย ไม่สามารถสรุปข้อมูลได้ในขณะนี้",
            "office_risk": "--",
            "office_summary": "--"
        }, user_info
