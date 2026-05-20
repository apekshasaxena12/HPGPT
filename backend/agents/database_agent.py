# database_agent.py

import os
import json
from typing_extensions import TypedDict, Annotated
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_community.utilities import SQLDatabase
from langchain_community.tools.sql_database.tool import QuerySQLDataBaseTool
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, START

load_dotenv()

# Initialize the LLM
llm = ChatGroq(
    model="llama3-8b-8192",
    temperature=0.1,
    api_key=os.getenv("GROQ_API_KEY")
)

# Connect to the database
db_path = "backend/database/Chinook.db"
db = SQLDatabase.from_uri(f"sqlite:///{db_path}")
table_info_cache = db.get_table_info()

# Reusable SQL tool
sql_tool = QuerySQLDataBaseTool(db=db)

# Shared state
class State(TypedDict):
    question: str
    query: str
    result: dict
    answer: str

# Prompt
system_message = """
Given an input question, create a syntactically correct {dialect} query to run to help find the answer.
Only use the following tables and columns:
{table_info}
Always limit to {top_k} results unless otherwise specified.
"""

user_prompt = "Question: {input}"

query_prompt_template = ChatPromptTemplate.from_messages([
    ('system', system_message),
    ('user', user_prompt)
])

class QueryOutput(TypedDict):
    query: Annotated[str, ..., "SQL query string"]

# Node 1: Generate SQL query
def write_query(state: State):
    try:
        messages = query_prompt_template.format_messages(
        dialect=db.dialect,
        top_k=10,
        table_info=table_info_cache,
        input=state['question']
        )

        structured_llm = llm.with_structured_output(QueryOutput)
        result = structured_llm.invoke(messages)
        return {'query': result['query']}
    except Exception as e:
        return {'query': f"-- ERROR generating query: {str(e)}"}

# Node 2: Execute SQL query
def execute_query(state: State):
    try:
        result = sql_tool.invoke(state['query'])
        return {'result': {"status": "success", "data": result}}
    except Exception as e:
        return {'result': {"status": "error", "message": str(e)}}

# Node 3: Generate final answer
def generate_answer(state: State):
    if state['result'].get('status') == 'error':
        return {"answer": f"Failed to run SQL query: {state['result']['message']}"}
    prompt = (
        f"Given the question:\n{state['question']}\n\n"
        f"The SQL query used:\n{state['query']}\n\n"
        f"And the SQL result:\n{json.dumps(state['result']['data'], indent=2)}\n\n"
        f"Provide a helpful answer."
    )
    try:
        response = llm.invoke(prompt)
        return {"answer": response.content}
    except Exception as e:
        return {"answer": f"Failed to generate final answer: {str(e)}"}

# LangGraph pipeline
def build_db_query_graph():
    builder = StateGraph(State)
    builder.add_node("write_query", write_query)
    builder.add_node("execute_query", execute_query)
    builder.add_node("generate_answer", generate_answer)

    builder.set_entry_point("write_query")
    builder.add_edge("write_query", "execute_query")
    builder.add_edge("execute_query", "generate_answer")
    builder.set_finish_point("generate_answer")

    return builder.compile()
