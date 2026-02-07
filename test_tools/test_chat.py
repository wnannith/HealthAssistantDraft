"""Simple import/test harness for chat.py

Run this to verify `chat.py` is importable from this repo without starting the Discord bot.
"""
from pathlib import Path
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
import sys
import os
from dotenv import load_dotenv

from IPython.display import Image, display
from langchain_core.runnables.graph import MermaidDrawMethod


ROOT = Path(__file__).resolve().parents[1]

# Load environment variables from parent directory's .env file
load_dotenv(ROOT / ".env")
os.environ['GOOGLE_API_KEY'] = os.getenv('GOOGLE_API_KEY')
os.environ['OPENAI_API_KEY'] = os.getenv('OPENAI_API_KEY')

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import chat
except Exception as e:
    print("Failed to import chat.py:", e)
    raise


def main():
    # print("chat module:", chat)

    msgs = [
        HumanMessage("Hello, my friend.")
    ]

    # try:
    #     out = chat.format_messages(msgs)
    #     print("format_messages output:", '\n', out)
    # except Exception as e:
    #     print("format_messages failed:", e)

    # try:
    #     defaults = chat.load_defaults()
    #     print("load_defaults keys:", list(defaults.keys())[:10])
    # except Exception as e:
    #     print("load_defaults failed:", e)

    # display(
    #     Image(
    #         chat.set_graph_response().get_graph().draw_mermaid_png(
    #             draw_method=MermaidDrawMethod.API
    #         )
    #     )
    # )

    for status in [False, True]:
        print(status)
        response, _ = chat.generate_response(messages=msgs, use_rag=status)
        print(response)
        print()


if __name__ == '__main__':
    main()
