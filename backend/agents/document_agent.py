# backend/agents/document_agent.py

import os
import logging
from enum import Enum
from typing import Dict, Literal, TypedDict, Optional, List, Any
from langgraph.graph import StateGraph, END

from backend.agents.rag_api.summarize import summarize_task
from backend.agents.rag_api.query import query_task
from backend.utils.groq_client import groq_client
from backend.agents.rag_api.compare import run_compare_agent
logger = logging.getLogger(__name__)


class DocumentTask(str, Enum):
    summarize = "summarize"
    compare = "compare"
    query = "query"


class DocumentAgentState(TypedDict):
    input: str
    chat_id: Optional[str]
    doc_id: Optional[str]
    chat_history: Optional[List[Dict[str, str]]]
    task: Optional[DocumentTask]
    response: Optional[str]
    answer_mode: Optional[str]


class DocumentAgent:
    def __init__(self):
        self.graph: Any = self._build_graph()  

    async def _compare_task(self, state: DocumentAgentState) -> DocumentAgentState:
        try:
            chat_id = state.get("chat_id", "default-session")
            print(f"[DEBUG] chat_id: {chat_id}")
            
            from backend.agents.rag_api.compare import run_compare_agent
            result = run_compare_agent(chat_id=chat_id, answer_mode=state.get("answer_mode", "specific"))
            print(f"[DEBUG] compare result: {result}")
            
            return {**state, "response": result.get("comparison") or result.get("response", "❌ Comparison failed.")}
        except Exception as e:
            import traceback
            print(f"[DEBUG] Full error: {traceback.format_exc()}")
            logger.error(f"[❌ CompareTask Error]: {e}")
            return {**state, "response": "❌ Failed to upload or compare the documents."}

    async def _router_node(self, state: DocumentAgentState) -> DocumentAgentState:
        prompt = state.get("input", "")
        history = state.get("chat_history", [])

        try:
            context = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history[-3:]])
            system_prompt = (
                "You are a routing assistant inside the document agent.\n"
                "Your job is to choose a task based on context:\n"
                "- 'summarize' → summarize uploaded document\n"
                "- 'compare' → compare two uploaded documents\n"
                "- 'query' → answer questions based on uploaded document\n"
                "Respond ONLY with one of: summarize, compare, query."
            )
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"History:\n{context}\n\nUser: {prompt}"}
            ]

            result = groq_client.client.chat.completions.create(
                model=groq_client.model,
                messages=messages,
                temperature=0,
                max_tokens=5,
            )
            task = result.choices[0].message.content.strip().lower()

            if task not in {"summarize", "compare", "query"}:
                logger.warning(f"[⚠️ Invalid Task Returned]: {task}, defaulting to 'query'")
                task = "query"

            logger.info(f"[📄 DocumentAgent Routed To]: {task}")
            return {**state, "task": task}

        except Exception as e:
            logger.error(f"[❌ Router Error]: {e}")
            return {**state, "task": "query"}

    def _build_graph(self) -> Any:
        graph = StateGraph(DocumentAgentState)

        graph.add_node("router", self._router_node)
        graph.add_node("summarize", summarize_task)
        graph.add_node("compare", self._compare_task)
        graph.add_node("query", query_task)

        graph.set_entry_point("router")
        graph.add_conditional_edges(
            "router",
            lambda state: state["task"],
            {
                "summarize": "summarize",
                "compare": "compare",
                "query": "query"
            }
        )

        graph.add_edge("summarize", END)
        graph.add_edge("compare", END)
        graph.add_edge("query", END)

        return graph.compile()

    def get_graph(self) -> Any:
        return self.graph

    async def run(self, input: DocumentAgentState) -> DocumentAgentState:
        return await self.graph.ainvoke(input)
