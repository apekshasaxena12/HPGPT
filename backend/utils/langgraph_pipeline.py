from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional, List, Dict, Any
import logging, os
from backend.agents.document_agent import DocumentAgent
from backend.agents.database_agent import build_db_query_graph


logger = logging.getLogger(__name__)

# Initialize LangGraph subgraph from DocumentAgent
document_subgraph = DocumentAgent().get_graph()
database_subgraph = build_db_query_graph()



class AgentState(TypedDict):
    prompt: str
    history: Optional[List[Dict[str, Any]]]
    agent_types: Optional[List[str]]
    responses: Optional[Dict[str, str]]
    response: Optional[str]
    answer_mode: Optional[str]
    chat_id: Optional[str]
    doc_id: Optional[str]
    files: Optional[List[Dict[str, Any]]]
    session_id: Optional[str]


def build_langgraph(coding_agent, analytics_agent, websearch_agent, general_agent, groq_client, database_agent):
    async def router_node(state: AgentState) -> AgentState:
        prompt = state["prompt"]
        history = state.get("history", [])

        # If files are attached, always route to document agent — no LLM call needed
        if state.get("files"):
            logger.info("📎 Files detected — routing directly to document agent")
            return {**state, "agent_types": ["document"], "responses": {}}

        try:
            context = "\n".join(
                f"{msg.get('role', 'user')}: {msg.get('content', '')}"
                for msg in history[-4:]
            )

            system_prompt = (
                "You are an intelligent multi-agent router.\n"
                "Given the conversation history and current user prompt, decide which ONE of the following agents should be activated:\n"
                "- 'coding' for programming-related questions\n"
                "- 'analytics' for data analysis, graphs, or file-based insights\n"
                "- 'websearch' for real-time or factual queries\n"
                "- 'document' for queries based on uploaded documents\n"
                "- 'database' for questions that require querying a relational database\n"
                "If none of these apply, respond with only 'general'.\n\n"
                "Respond with only one agent type (no explanation, no punctuation):\n\n"
                f"Conversation History:\n{context.strip()}"
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]

            logger.info(f"🔀 Multi-agent routing with context for prompt: {prompt}")
            response = groq_client.client.chat.completions.create(
                model=groq_client.model,
                messages=messages,
                temperature=0,
                max_tokens=5,
                top_p=1,
            )

            raw_output = response.choices[0].message.content.strip().lower()
            valid_agents = {"coding", "analytics", "websearch", "document", "database", "general"}
            selected_agent = raw_output if raw_output in valid_agents else "general"

            logger.info(f"✅ Routed to agent: {selected_agent}")
            return {
                **state,
                "agent_types": [selected_agent],
                "responses": {},
            }

        except Exception as e:
            logger.error(f"❌ Routing failed: {e}")
            return {
                **state,
                "agent_types": ["general"],
                "responses": {},
            }

    def wrap(agent, agent_type: str):
        async def node(state: AgentState) -> AgentState:
            prompt = state["prompt"]
            history = state.get("history", [])
            responses = state.get("responses", {})
            answer_mode = state.get("answer_mode", "specific")

            try:
                if getattr(agent, "name", "") == "AnalyticsAgent":
                    uploads_dir = "uploads"
                    files = [f for f in os.listdir(uploads_dir) if os.path.isfile(os.path.join(uploads_dir, f))]
                    if not files:
                        raise FileNotFoundError("No uploaded files found for analytics.")

                    latest_file = max(files, key=lambda f: os.path.getctime(os.path.join(uploads_dir, f)))
                    file_path = os.path.join(uploads_dir, latest_file)

                    file_info = {"name": latest_file, "path": file_path}
                    logger.info(f"📊 Running AnalyticsAgent with file: {file_path}")
                    result = await agent.run(file_info, prompt)
                    summary = result.get("summary", "")
                    plot = result.get("response", "")
                    response_text = f"{plot}\n\n{summary}".strip()

                elif agent_type in {"websearch", "general"}:
                    logger.info(f"🌐 Running {agent_type.capitalize()}Agent")
                    result = await agent.run({
                        "prompt": prompt,
                        "history": history,
                        "answer_mode": answer_mode
                    })
                    response_text = result.get("response") or result.get("error", "No output.")

                elif agent_type == "document":
                    files = state.get("files") or []
                    session_id = state.get("session_id", "")

                    if files:
                        import requests as _req
                        from backend.agents.collections_rag import extract_text
                        from langchain_text_splitters import RecursiveCharacterTextSplitter
                        from langchain_community.vectorstores import FAISS
                        from langchain_huggingface import HuggingFaceEmbeddings

                        # Use a session-scoped FAISS index so we build it once and reuse it
                        if not hasattr(wrap, "_doc_indexes"):
                            wrap._doc_indexes = {}

                        index_key = session_id
                        vs = wrap._doc_indexes.get(index_key)

                        if vs is None:
                            # Build FAISS index for this session's files
                            logger.info(f"🔨 Building FAISS index for session {session_id}")
                            all_chunks, all_metas = [], []
                            splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
                            emb = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

                            for f in files:
                                orig_name = f.get("name", "")
                                base, ext = os.path.splitext(orig_name)
                                file_path = os.path.join("uploads", f"{session_id}_{base}{ext}")

                                if os.path.exists(file_path):
                                    try:
                                        text = extract_text(file_path)
                                        chunks = splitter.split_text(text)
                                        all_chunks.extend(chunks)
                                        all_metas.extend([{"source": orig_name}] * len(chunks))
                                        logger.info(f"📄 Indexed {len(chunks)} chunks from {orig_name}")
                                    except Exception as e:
                                        logger.error(f"Failed to index {orig_name}: {e}")
                                else:
                                    logger.warning(f"⚠️ File not found: {file_path}")

                            if all_chunks:
                                vs = FAISS.from_texts(all_chunks, embedding=emb, metadatas=all_metas)
                                wrap._doc_indexes[index_key] = vs
                            else:
                                response_text = "Could not read the uploaded file."
                                responses[agent_type] = response_text
                                return {**state, "responses": responses}

                        # Retrieve the top relevant chunks for this question
                        docs = vs.similarity_search(prompt, k=6)
                        context = "\n\n".join(
                            f"[{d.metadata.get('source','')}]\n{d.page_content}" for d in docs
                        )
                        logger.info(f"🔍 Retrieved {len(docs)} chunks for: {prompt}")

                        messages = [
                            {
                                "role": "system",
                                "content": (
                                    "You are a helpful document assistant. "
                                    "Answer the user's question using ONLY the document excerpts below. "
                                    "If the answer is not in the excerpts, say so clearly."
                                )
                            },
                            {
                                "role": "user",
                                "content": f"Document excerpts:\n{context}\n\nQuestion: {prompt}"
                            }
                        ]

                        try:
                            resp = _req.post(
                                "http://localhost:11434/v1/chat/completions",
                                json={"model": "qwen2.5:7b", "messages": messages,
                                      "temperature": 0.3, "max_tokens": 2048, "stream": False},
                                timeout=60
                            )
                            resp.raise_for_status()
                            response_text = resp.json()["choices"][0]["message"]["content"].strip()
                        except Exception as qwen_err:
                            response_text = f"Error querying document: {qwen_err}"

                    else:
                        logger.info(f"📄 Running DocumentAgent (no files in state)")
                        sub_state = {
                            "input": prompt,
                            "doc_id": state.get("doc_id", ""),
                            "chat_id": state.get("chat_id", "default-session"),
                            "chat_history": history,
                            "answer_mode": answer_mode,
                        }
                        result = await agent.ainvoke(sub_state)
                        response_text = result.get("response", "No response.")

                elif agent_type == "database":
                    logger.info(f"🗃️ Running DatabaseAgent")
                    sub_state = {
                        "question": prompt,
                        "query": "",
                        "result": "",
                        "answer": ""
                    }
                    result = await agent.ainvoke(sub_state)
                    response_text = result.get("answer", "No answer.")

                else:
                    logger.info(f"🧠 Running fallback agent: {agent_type}")
                    result = await agent.run(state)
                    response_text = result.get("response") or result.get("error", "No output.")

            except Exception as e:
                response_text = f"[Error from {agent_type}]: {e}"

            responses[agent_type] = response_text
            return {**state, "responses": responses}

        return node


    async def aggregator_node(state: AgentState) -> AgentState:
        responses = state.get("responses", {})
        combined = "\n\n".join(output for output in responses.values())
        return {
            **state,
            "response": combined
        }

    graph = StateGraph(AgentState)
    graph.add_node("router", router_node)
    graph.set_entry_point("router")

    agents = {
        "general": wrap(general_agent, "general"),
        "coding": wrap(coding_agent, "coding"),
        "analytics": wrap(analytics_agent, "analytics"),
        "websearch": wrap(websearch_agent, "websearch"),
        "document": wrap(document_subgraph, "document"),
        "database": wrap(database_subgraph, "database"),
    }

    for name, node in agents.items():
        graph.add_node(name, node)
        graph.add_edge(name, "aggregator")

    graph.add_node("aggregator", aggregator_node)
    graph.add_edge("aggregator", END)

    def route_all(state: AgentState):
        return state["agent_types"] or ["general"]

    graph.add_conditional_edges("router", route_all)

    return graph.compile()
