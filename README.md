# HPGPT: Conversational Multi-Agentic AI Chatbot  

## 📌 Overview  
**HPGPT** is a **conversational AI platform** that provides **multi-agent, domain-specific assistance** across coding, analytics, database querying, document understanding, and web search—all through a single chat interface.  

The system leverages **LangChain**, **LangGraph**, **Google Gemini (via ADK)**, and **Groq LLaMA models** to orchestrate specialized agents:  
- 🤖 **General Assistant** – fallback & casual queries  
- 💻 **Coding Agent** – generates executable code (Python, C, C++, Java, JS, HTML)  
- 📊 **Analytics Agent** – processes CSV/XLSX, runs pandas queries, renders **Plotly** charts  
- 🗄️ **Database Agent** – executes **SQL queries** against a linked **PostgreSQL/MySQL** database and returns structured results or natural language summaries  
- 📑 **Document Agent** – RAG-powered document Q&A (PDF, Word, Excel, TXT)  
- 🌐 **Websearch Agent** – real-time factual search via Tavily API  

---

## ⚡ Features  
- Multi-Agent Orchestration with LangGraph  
- Database Querying Agent – write & execute SQL securely against a live DB  
- Context persistence with PostgreSQL + LangChain Memory 
- File-aware Q&A (PDF, Excel, CSV, DOCX, TXT)  
- Real-time WebSocket chat with streaming responses  
- Interactive Plotly visualizations in chat  
- Secure file handling with session-based storage  
- Scalable & extensible agent pipeline  

---

## 🏗️ System Architecture  
- **Frontend:** Flask + JS (chat UI, file uploads, streaming charts, syntax highlighting)  
- **Backend:** FastAPI (agent routing, WebSocket streaming, file processing, database queries)  
- **LangGraph:** session/context manager + agent dispatcher  
- **Database:** PostgreSQL (users, sessions, messages, feedback, SQL query execution)  
- **Agents:** Modular Python agents powered by Gemini/Groq  

---

## 📂 Project Structure  
```
HPGPT/
│── backend/
│   ├── agents/ 
│   │   ├── analytics_agent.py
│   │   ├── coding_agent.py
│   │   ├── document_agent.py
│   │   ├── websearch_agent.py
│   │   └── database_agent.py
│   │
│   ├── utils/ 
│   │   ├── groq_client.py
│   │   ├── langgraph_manager.py
│   │   ├── langgraph_pipeline.py
│   │   ├── file_processor.py
│   │   └── file_utils.py
│   │
│   ├── database/
│   │   ├── db_manager.py   # connection & query execution
│   │   └── auth.py         # authentication & sessions
│   │
│   └── main.py  # FastAPI entrypoint
│
│── frontend/
│   ├── app.py  # Flask server
│   ├── templates/index.html
│   ├── static/js/main.js
│   ├── static/css/styles.css
│
│── requirements.txt
│── README.md
```

---

## 🚀 Installation & Setup  

### 0. Prerequisites  
- Python ≥ **3.10**  
- PostgreSQL/MySQL running locally or remote  
- VS Code / IDE recommended  

### 1. Clone Repository  
```bash
git clone https://github.com/CharithKalasi/HPGPT.git
cd HPGPT
```

### 2. Create Virtual Environment  
```bash
python -m venv venv

# Linux / Mac
source venv/bin/activate   

# Windows PowerShell
.\venv\Scripts\Activate
# (If activation fails, run this first to allow script execution)
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### 3. Install Dependencies  
```bash
pip install -r requirements.txt
```

### 4. Configure Database  
Update `.env` with PostgreSQL/MySQL credentials. The system will auto-connect and manage tables via `db_manager.py`.  

### 5. Run Backend (FastAPI)  
```bash
cd backend
uvicorn main:app --reload
```

### 6. Run Frontend (Flask)  
```bash
cd frontend
python app.py
```

### 7. Open in Browser  
```bash
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
**Response:**  
| Customer Name | Total Spent |  
|---------------|-------------|  
| Alice         | 15,200      |  
| Bob           | 12,450      |  
| Charlie       | 9,880       |  

### 📑 Document Agent  
**Prompt:**  
```
Summarize the attached PDF in 5 bullet points.
```  
**Response:**  
- Extracted key points from PDF...  

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
- Reliable RAG-based document Q&A  
- Real-time code execution & data visualization  
- Secure SQL query execution via Database Agent  

---

## ✅ Conclusion  
HPGPT bridges the gap between **general-purpose chatbots** and **enterprise-level intelligent assistants**.  
Its **modular, multi-agent architecture** ensures that each agent—whether for documents, analytics, coding, research, or more—works in unison to handle complex tasks through **simple natural language prompts**.  

This design makes HPGPT both **scalable and adaptable**, empowering users across domains to unlock actionable intelligence without technical barriers.  

---

## 🔗 References & Resources  
- 📂 Code: [GitHub Repo](https://github.com/CharithKalasi/HPGPT)  
- 🎥 Demo: [Sample Video](https://drive.google.com/file/d/14SIY1_HzUe-snkmpDIly-7ofen6a9sle/view?usp=sharing)  
- 📚 Docs:  
  - [LangChain](https://python.langchain.com/)  
  - [LangGraph](https://langchain-ai.github.io/langgraph/concepts/why-langgraph/)  
  - [Flask](https://flask.palletsprojects.com/)  
  - [Google AI SDK](https://ai.google.dev)  
  - [ChromaDB](https://www.trychroma.com/)  
