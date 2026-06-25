# HPGPT: Conversational Multi-Agentic AI Chatbot

## 📌 Overview
**HPGPT** is a **conversational AI platform** that provides **multi-agent, domain-specific assistance** across coding, analytics, database querying, document understanding, and web search—all through a single chat interface.

The system leverages **LangChain**, **LangGraph**, **Google Gemini**, **Sentence Transformers**, and **Groq LLaMA models** to orchestrate specialized agents:
- 🤖 **General Assistant** – fallback & casual queries
- 💻 **Coding Agent** – generates executable code (Python, C, C++, C#, Java, JS, HTML, CSS)
- 📊 **Analytics Agent** – processes CSV/XLSX, runs pandas queries, renders **Plotly** charts
- 🗄️ **Database Agent** – executes **SQL queries** against a linked **SQLite/PostgreSQL** database and returns structured results or natural language summaries
- 📑 **Document Agent** – RAG-powered document Q&A, summarization, and comparison (PDF, Word, Excel, TXT)
- 🌐 **Websearch Agent** – real-time factual search via Tavily API

---

## ⚡ Features
- Multi-Agent Orchestration with LangGraph
- Database Querying Agent – write & execute SQL securely against a live DB
- Context persistence with PostgreSQL + JSON-based session storage
- File-aware Q&A (PDF, Excel, CSV, DOCX, TXT)
- Real-time WebSocket chat with streaming responses
- Interactive Plotly visualizations in chat
- Secure file handling with session-based storage
- Scalable & extensible agent pipeline
- **RAG Server** – local FastAPI server with FAISS vector search + Sentence Transformers embeddings
- **Document Summarization, Q&A, and Comparison** via dedicated RAG endpoints
- **Two answer modes** – Specific (concise) and Detailed (comprehensive)
- **Voice Input (STT)** – speak your questions via microphone
- **Text-to-Speech (TTS)** – hear responses read aloud
- **Copy button** – copy any assistant message with one click
- **Chat Rename** – rename any chat session from the sidebar
- **Chat Delete** – delete sessions with proper sidebar refresh
- **Syntax highlighting** via PrismJS for code responses

---

## 🏗️ System Architecture
- **Frontend:** Flask + JS (chat UI, file uploads, streaming charts, syntax highlighting)
- **Backend:** FastAPI (agent routing, WebSocket streaming, file processing, database queries)
- **RAG Server:** FastAPI (local RAG server on port 8001 — FAISS + Sentence Transformers + Gemini)
- **LangGraph:** session/context manager + agent dispatcher
- **Database:** PostgreSQL (users, sessions, messages, feedback, SQL query execution)
- **Agents:** Modular Python agents powered by Gemini/Groq

---

## 📂 Project Structure
```
HPGPT/
│── backend/
│   ├── agents/
│   │   ├── rag_api/
│   │   │   ├── rag_server.py     # Local RAG server
│   │   │   ├── summarize.py      # Summarization client
│   │   │   ├── query.py          # Query client
│   │   │   └── compare.py        # Comparison client
│   │   ├── agents.py             # Agent dispatcher
│   │   ├── analytics_agent.py
│   │   ├── coding_agent.py
│   │   ├── collections_agent.py  # Collections category management
│   │   ├── collections_rag.py    # FAISS-based collections Q&A
│   │   ├── document_agent.py
│   │   ├── general_agent.py
│   │   ├── websearch_agent.py
│   │   └── database_agent.py
│   │
│   ├── utils/
│   │   ├── groq_client.py
│   │   ├── langgraph_manager.py
│   │   ├── langgraph_pipeline.py
│   │   ├── file_processor.py
│   │   ├── file_uploader.py
│   │   └── file_utils.py
│   │
│   ├── database/
│   │   ├── db_manager.py
│   │   └── auth.py
│   │
│   └── main.py
│
│── frontend/
│   ├── app.py
│   ├── templates/
│   │   ├── index.html
│   │   ├── homepage.html
│   │   ├── collections.html
│   │   ├── login.html
│   │   └── signup.html
│   └── static/
│       ├── js/
│       │   └── main.js
│       ├── css/
│       │   └── styles.css
│       ├── icons/
│       ├── images/
│       ├── fonts/
│       ├── main1.js
│       ├── styles1.css
│       └── particles1.json
│
│── requirements.txt
│── example.env
│── README.md
```

---

## 🚀 Installation & Setup

### 0. Prerequisites
- Python ≥ **3.10**
- PostgreSQL running locally or remote
- VS Code / IDE recommended

### 1. Clone Repository
```bash
git clone https://github.com/apekshasaxena12/HPGPT.git
cd HPGPT
```

### 2. Create Virtual Environment
```bash
python -m venv venv

# Linux / Mac
source venv/bin/activate

# Windows PowerShell
.\venv\Scripts\Activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment
Copy `example.env` to `.env` and fill in your credentials:
```env
GOOGLE_API_KEY=your_google_api_key
GROQ_API_KEY=your_groq_api_key
TAVILY_API_KEY=your_tavily_api_key
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/hpgpt_db
API_BASE_URL=http://localhost:8001
RAG_API_KEY=your_chosen_rag_secret_key
SECRET_KEY=your_flask_secret_key
VOICE_BOT_URL=http://localhost:8088/
DOC_GEN_URL=http://localhost:5001/
```

### 5. Run RAG Server (NEW — required for Document Agent)
```bash
cd backend/agents/rag_api
uvicorn rag_server:app --port 8001
```

### 6. Run Backend (FastAPI)
Open a new terminal:
```bash
cd HPGPT
uvicorn backend.main:app --reload --port 8000
```

### 7. Run Frontend (Flask)
Open another terminal:
```bash
cd frontend
python app.py
```

### 8. Open in Browser
```
http://127.0.0.1:5000/
```

---

## 💡 Usage Examples

### 🤖 General Assistant
**Prompt:**
```
What's the capital of France?
```
**Response:**
```
The capital of France is Paris.
```

### 💻 Coding Agent
**Prompt:**
```
Write a Python function to check if a number is prime.
```
**Response:**
```python
def is_prime(n):
    if n <= 1:
        return False
    for i in range(2, int(n**0.5) + 1):
        if n % i == 0:
            return False
    return True
```

### 📊 Analytics Agent
**Prompt:**
```
Upload sales.csv and show me the total revenue by product category in a bar chart.
```
**Response:**
Interactive Plotly bar chart with revenue grouped by category.

### 🗄️ Database Agent
**Prompt:**
```
Show me the top 5 customers by purchase amount.
```
**Generated SQL:**
```sql
SELECT customer_name, SUM(amount) AS total_spent
FROM orders
GROUP BY customer_name
ORDER BY total_spent DESC
LIMIT 5;
```

### 📑 Document Agent
**Prompt:**
```
Summarize the attached PDF.
```
**Response:**
Concise summary of the document's key points using RAG-based retrieval.

**Prompt:**
```
Compare these two documents.
```
**Response:**
Side-by-side comparison of similarities and differences (respects Specific/Detailed mode).

### 🌐 Websearch Agent
**Prompt:**
```
What's the latest news about electric vehicles in India?
```
**Response:**
Latest web snippets summarizing EV adoption and government policies.

---

## 📊 Results
- Unified conversational interface for multi-domain tasks
- Automatic agent routing without dropdown/manual selection
- Reliable RAG-based document Q&A, summarization, and comparison
- Real-time code generation & data visualization
- Secure SQL query execution via Database Agent
- Answer mode (Specific/Detailed) respected across all agents

---

## ✅ Conclusion
HPGPT bridges the gap between **general-purpose chatbots** and **enterprise-level intelligent assistants**.
Its **modular, multi-agent architecture** ensures that each agent—whether for documents, analytics, coding, research, or more—works in unison to handle complex tasks through **simple natural language prompts**.

This design makes HPGPT both **scalable and adaptable**, empowering users across domains to unlock actionable intelligence without technical barriers.

---

## 🔗 References & Resources
- 📂 Code: [GitHub Repo](https://github.com/apekshasaxena12/HPGPT)
- 📚 Docs:
  - [LangChain](https://python.langchain.com/)
  - [LangGraph](https://langchain-ai.github.io/langgraph/concepts/why-langgraph/)
  - [Flask](https://flask.palletsprojects.com/)
  - [Google AI SDK](https://ai.google.dev)
  - [Sentence Transformers](https://www.sbert.net/)
  - [FAISS](https://faiss.ai/)
