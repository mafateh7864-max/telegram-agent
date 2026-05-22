import os
import json
import logging
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq
from duckduckgo_search import DDGS

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
MEMORY_FILE = "memory.json"
MAX_HISTORY = 20

groq_client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """You are Fateh's personal AI assistant, running 24/7 in the cloud.

About Fateh:
- Entrepreneur building AI subscription products (target: $1k+/month recurring revenue)
- Non-technical â€” no jargon, explain things clearly
- Focused on AI tools & automation
- Direct communicator â€” no filler, get to the point
- Wants honest pushback, not just agreement
- Always explain the WHY behind recommendations
- Use bullet points and short tables when helpful

Your role:
- Be a sharp business partner, not a yes-man
- Challenge weak ideas and explain why
- Help plan, strategize, and make decisions
- Search the web when current info is needed
- Remember context across all conversations"""


# --- Memory ---

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    return {"conversations": {}, "facts": [], "tasks": []}

def save_memory(memory):
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)

def get_history(memory, user_id):
    return memory["conversations"].get(str(user_id), [])

def append_history(memory, user_id, role, content):
    uid = str(user_id)
    if uid not in memory["conversations"]:
        memory["conversations"][uid] = []
    memory["conversations"][uid].append({"role": role, "content": content})
    if len(memory["conversations"][uid]) > MAX_HISTORY:
        memory["conversations"][uid] = memory["conversations"][uid][-MAX_HISTORY:]


# --- Web Search ---

def web_search(query: str) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=4))
        if not results:
            return "No results found."
        out = []
        for r in results[:3]:
            out.append(f"{r['title']}\n{r['body']}")
        return "\n\n".join(out)
    except Exception as e:
        return f"Search failed: {e}"


# --- Handlers ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey Fateh. Online 24/7 â€” even when your laptop is closed.\n\n"
        "Commands:\n"
        "/search [query] â€” Search the web\n"
        "/remember [fact] â€” Save something permanently\n"
        "/recall â€” See everything saved\n"
        "/task [task] â€” Add a task\n"
        "/tasks â€” View active tasks\n"
        "/done [number] â€” Mark task complete\n"
        "/clear â€” Clear chat history\n\n"
        "Or just talk normally."
    )

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /search [what you want to find]")
        return
    query = " ".join(context.args)
    await update.message.reply_text(f"Searching: {query}...")
    results = web_search(query)
    await update.message.reply_text(results[:4000])

async def cmd_remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /remember [something to save]")
        return
    fact = " ".join(context.args)
    memory = load_memory()
    memory["facts"].append(fact)
    save_memory(memory)
    await update.message.reply_text(f"Saved: {fact}")

async def cmd_recall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()
    if not memory["facts"]:
        await update.message.reply_text("Nothing saved. Use /remember [fact] to save something.")
        return
    lines = "\n".join(f"{i+1}. {f}" for i, f in enumerate(memory["facts"]))
    await update.message.reply_text(f"Saved facts:\n\n{lines}")

async def cmd_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /task [what needs to be done]")
        return
    task = " ".join(context.args)
    memory = load_memory()
    memory["tasks"].append({"task": task, "done": False})
    save_memory(memory)
    await update.message.reply_text(f"Task added: {task}")

async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()
    active = [t for t in memory["tasks"] if not t["done"]]
    if not active:
        await update.message.reply_text("No active tasks.")
        return
    lines = "\n".join(f"{i+1}. {t['task']}" for i, t in enumerate(active))
    await update.message.reply_text(f"Active tasks:\n\n{lines}")

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /done [task number]")
        return
    num = int(context.args[0]) - 1
    memory = load_memory()
    active = [t for t in memory["tasks"] if not t["done"]]
    if num < 0 or num >= len(active):
        await update.message.reply_text("Invalid number. Use /tasks to see the list.")
        return
    active[num]["done"] = True
    save_memory(memory)
    await update.message.reply_text(f"Done: {active[num]['task']}")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()
    memory["conversations"][str(update.effective_user.id)] = []
    save_memory(memory)
    await update.message.reply_text("Chat history cleared.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    memory = load_memory()
    history = get_history(memory, user_id)

    extras = ""
    if memory["facts"]:
        extras += "\n\nRemembered facts:\n" + "\n".join(f"- {f}" for f in memory["facts"])
    active_tasks = [t for t in memory["tasks"] if not t["done"]]
    if active_tasks:
        extras += "\n\nActive tasks:\n" + "\n".join(f"- {t['task']}" for t in active_tasks)

    messages = [{"role": "system", "content": SYSTEM_PROMPT + extras}]
    messages.extend(history)
    messages.append({"role": "user", "content": text})

    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=1024,
            temperature=0.7,
        )
        reply = resp.choices[0].message.content
    except Exception as e:
        reply = f"AI error: {e}"

    append_history(memory, user_id, "user", text)
    append_history(memory, user_id, "assistant", reply)
    save_memory(memory)
    await update.message.reply_text(reply)


# --- Keep-alive server (stops Render from sleeping) ---

flask_app = Flask(__name__)

@flask_app.route("/health")
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)


# --- Start ---

def main():
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("remember", cmd_remember))
    app.add_handler(CommandHandler("recall", cmd_recall))
    app.add_handler(CommandHandler("task", cmd_task))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot running")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
