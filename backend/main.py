from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import json
from pathlib import Path
import uuid
import os
import aiofiles
from typing import List, Optional
import logging
from datetime import datetime
from pydantic import BaseModel
from typing import Dict
import asyncio
from urllib.parse import parse_qs
from dotenv import load_dotenv
from backend.agents.collections_agent import suggest_category, save_document_to_collection, list_documents, CATEGORIES
from backend.agents.collections_rag import index_document, query_collections, rebuild_index_from_disk

# Load environment variables
load_dotenv()

from backend.utils.langgraph_manager import hpgpt_graph
from backend.utils.file_processor import FileProcessor

from backend.database.db_manager import database
from backend.database import auth
from backend.database.auth import get_user_id_by_session


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize file processor
file_processor = FileProcessor()

# Track stop requests per session
stop_requests = {}

# Feedback data model
class FeedbackData(BaseModel):
    session_id: str
    message_content: str
    feedback_type: str  # 'positive' or 'negative'
    agent_type: str
    answer_mode: str
    timestamp: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown"""
    os.makedirs("uploads", exist_ok=True)
    rebuild_index_from_disk()
    await database.connect()
    logger.info("hpGPT Backend started successfully")
    yield  # Application runs here
    # Shutdown
    await database.disconnect()
    logger.info("hpGPT Backend shutting down")
    

app = FastAPI(
    title="hpGPT Backend", 
    version="1.0.0",
    description="HPCL AI Assistant Backend with Multi-Agent Support, Feedback System, and Stop Functionality",
    lifespan=lifespan
)

app.include_router(auth.router)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5000", "http://127.0.0.1:5000", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")
    
    async def send_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            self.disconnect(websocket)

manager = ConnectionManager()

# NEW: Enhanced WebSocket endpoint with stop functionality and better error handling
@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """Enhanced WebSocket endpoint with stop functionality and robust streaming"""
    await manager.connect(websocket)
    
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            # Handle stop request from frontend
            if message_data.get("type") == "stop":
                stop_requests[session_id] = True
                logger.info(f"🛑 Stop requested for session {session_id}")
                await manager.send_message(
                    json.dumps({"type": "stopped", "session_id": session_id}),
                    websocket
                )
                continue
            
            # Extract message details
            answer_mode = message_data.get("answer_mode", "specific")
            agent_type = message_data.get("agent_type", "general")
            message_content = message_data.get("content", "")
            
            logger.info(f"Processing message for session {session_id} with answer mode: {answer_mode}")
            
            # Reset stop request for new message
            stop_requests[session_id] = False
            
            # Send typing indicator
            await manager.send_message(
                json.dumps({"type": "typing", "status": "started"}), 
                websocket
            )
            
            try:
                chunk_count = 0
                total_content = ""
                start_time = datetime.now()
                                                
                # ✅ Extract login_session_id from query param
                query_params = parse_qs(websocket.url.query)
                login_session_id = query_params.get("login_session_id", [None])[0]

                if not login_session_id:
                    logger.warning("❌ No login_session_id provided in WebSocket request")
                    await manager.send_message(
                        json.dumps({"type": "error", "message": "Missing login session ID"}), websocket
                    )
                    await websocket.close()
                    return

                # ✅ Fetch user_id from DB
                user_id = await get_user_id_by_session(login_session_id)

                if user_id is None:
                    logger.warning(f"❌ Invalid login_session_id: {login_session_id}")
                    await manager.send_message(
                        json.dumps({"type": "error", "message": "Invalid session ID"}), websocket
                    )
                    await websocket.close()
                    return

                
                # Create stop checker function
                def should_stop():
                    return stop_requests.get(session_id, False)
                
                # Stream response with stop checker and timeout
                timeout = 60  # 60 seconds timeout
                
                user_msg_id = str(uuid.uuid4())
                assistant_msg_id = str(uuid.uuid4())

                async for chunk in hpgpt_graph.chat(
                    message_data["content"], 
                    session_id, 
                    message_data.get("files", []),
                    answer_mode,
                    should_stop,   # Pass stop checker function
                    user_msg_id=user_msg_id,
                    assistant_msg_id=assistant_msg_id,
                    user_id=user_id,
                    websocket=websocket
                                ):
                    
                    # Check if stop was requested
                    if stop_requests.get(session_id, False):
                        logger.info(f"🛑 Streaming stopped mid-response for session {session_id}")
                        stop_requests[session_id] = False  # Reset for next message
                        await manager.send_message(
                            json.dumps({"type": "stopped", "session_id": session_id}),
                            websocket
                        )
                        break
                    
                    # Check for timeout
                    if (datetime.now() - start_time).total_seconds() > timeout:
                        logger.warning(f"⏰ Streaming timeout for session {session_id}")
                        await manager.send_message(
                            json.dumps({"type": "timeout", "session_id": session_id}),
                            websocket
                        )
                        break
                    
                    if chunk and chunk.strip():
                        chunk_count += 1
                        total_content += chunk
                        
                        # Send chunk with error handling
                        try:
                            await manager.send_message(
                                json.dumps({
                                    "type": "stream",
                                    "content": chunk,
                                    "chunk_id": chunk_count
                                }), 
                                websocket
                            )
                            
                            # Small delay to prevent overwhelming the client
                            await asyncio.sleep(0.01)
                            
                        except Exception as send_error:
                            logger.error(f"Error sending chunk {chunk_count}: {send_error}")
                            break
                
                # Only send completion if not stopped or timed out
                if not stop_requests.get(session_id, False):
                    end_time = datetime.now()
                    response_time = (end_time - start_time).total_seconds()
                    
                    logger.info(f"Streaming completed: {chunk_count} chunks, {len(total_content)} characters, {response_time:.2f}s")
                    
                    # Send completion signal with performance metrics
                    await manager.send_message(
                        json.dumps({
                            "type": "complete",
                            "total_chunks": chunk_count,
                            "total_length": len(total_content),
                            "response_time": response_time,
                            "session_id": session_id,
                            "answer_mode": answer_mode
                        }), 
                        websocket
                    )
                
            except asyncio.CancelledError:
                logger.info(f"Streaming cancelled for session {session_id}")
                await manager.send_message(
                    json.dumps({"type": "cancelled", "session_id": session_id}),
                    websocket
                )
            except Exception as e:
                logger.error(f"Error during streaming for session {session_id}: {e}")
                await manager.send_message(
                    json.dumps({
                        "type": "error",
                        "message": f"Streaming interrupted: {str(e)}",
                        "session_id": session_id
                    }), 
                    websocket
                )
            
            finally:
                # Always send typing stopped
                try:
                    await manager.send_message(
                        json.dumps({"type": "typing", "status": "stopped"}), 
                        websocket
                    )
                except Exception as e:
                    logger.error(f"Error sending typing stopped: {e}")
                
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session_id}")
        manager.disconnect(websocket)
        # Clean up stop request
        if session_id in stop_requests:
            del stop_requests[session_id]
    except Exception as e:
        logger.error(f"WebSocket error for session {session_id}: {e}")
        manager.disconnect(websocket)
        # Clean up stop request
        if session_id in stop_requests:
            del stop_requests[session_id]

@app.put("/sessions/{session_id}/rename")
async def rename_session(session_id: str, body: dict):
    try:
        new_title = body.get("title", "").strip()
        if not new_title:
            return JSONResponse(status_code=400, content={"error": "Title cannot be empty"})

        if session_id not in hpgpt_graph.sessions:
            return JSONResponse(status_code=404, content={"error": "Session not found"})

        hpgpt_graph.sessions[session_id]["title"] = new_title
        hpgpt_graph.save_data()

        await database.execute(
            "UPDATE chats SET chatname = :chatname WHERE chatid = :chatid",
            {"chatname": new_title, "chatid": session_id}
        )

        return {"success": True, "session_id": session_id, "title": new_title}
    except Exception as e:
        logger.error(f"Error renaming session: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/sessions")
async def create_session():
    """Create a new chat session"""
    session_id = str(uuid.uuid4())
    logger.info(f"Created new session: {session_id}")
    return {"session_id": session_id, "status": "created"}

@app.get("/sessions")
async def get_all_sessions():
    """Get all chat sessions with total conversation count"""
    try:
        sessions = await hpgpt_graph.get_all_sessions()
        total_conversations = len(sessions)
        
        return {
            "sessions": sessions,
            "total_conversations": total_conversations,
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Error retrieving sessions: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "sessions": [], 
                "total_conversations": 0,
                "error": str(e),
                "status": "error"
            }
        )

@app.get("/sessions/{session_id}/history")
async def get_chat_history(session_id: str, limit: Optional[int] = None):
    """Get conversation history for a specific session with optional limit"""
    try:
        logger.info(f"Fetching chat history for session: {session_id} with limit: {limit}")
        
        # Get full history from LangGraph
        full_history = await hpgpt_graph.get_chat_history(session_id)
        
        # Apply limit if specified
        if limit and limit > 0:
            limited_history = await hpgpt_graph.get_limited_chat_history(session_id, limit)
            total_messages = await hpgpt_graph.get_total_message_count(session_id)
            
            displayed_count = 0
            if limited_history and limited_history[0].get("messages"):
                displayed_count = len(limited_history[0]["messages"])
            
            return {
                "session_id": session_id,
                "history": limited_history,
                "displayed_messages": displayed_count,
                "total_messages": total_messages,
                "limit_applied": limit,
                "status": "success"
            }
        else:
            # Return all messages
            total_messages = 0
            if full_history and full_history[0].get("messages"):
                total_messages = len(full_history[0]["messages"])
            
            return {
                "session_id": session_id,
                "history": full_history,
                "displayed_messages": total_messages,
                "total_messages": total_messages,
                "limit_applied": None,
                "status": "success"
            }
        
    except Exception as e:
        logger.error(f"Error retrieving chat history for session {session_id}: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "session_id": session_id,
                "history": [], 
                "displayed_messages": 0,
                "total_messages": 0,
                "error": str(e),
                "status": "error"
            }
        )

@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a chat session and its history"""
    try:
        success = await hpgpt_graph.delete_session(session_id)
        if success:
            logger.info(f"Deleted session: {session_id}")
            # Clean up stop request if exists
            if session_id in stop_requests:
                del stop_requests[session_id]
            return {"success": True, "message": f"Session {session_id} deleted successfully"}
        else:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": "Session not found"}
            )
    except Exception as e:
        logger.error(f"Error deleting session {session_id}: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.post("/feedback")
async def submit_feedback(feedback: FeedbackData):
    """Submit user feedback for a response"""
    try:
        logger.info(f"Received feedback: {feedback.feedback_type} for session {feedback.session_id}")
        
        # Store feedback in the graph manager
        feedback_result = await hpgpt_graph.store_feedback(
            session_id=feedback.session_id,
            message_content=feedback.message_content,
            feedback_type=feedback.feedback_type,
            agent_type=feedback.agent_type,
            answer_mode=feedback.answer_mode,
            timestamp=feedback.timestamp
        )
        
        if feedback_result:
            # Generate appropriate response message
            if feedback.feedback_type == "positive":
                response_message = "Thank you for the positive feedback! This helps me improve my responses."
            else:
                response_message = "Thank you for the feedback! I'll work on improving my responses based on your input."
            
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "message": response_message,
                    "feedback_id": feedback_result.get("feedback_id"),
                    "status": "stored"
                }
            )
        else:
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": "Failed to store feedback"
                }
            )
            
    except Exception as e:
        logger.error(f"Error submitting feedback: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e)
            }
        )

@app.get("/feedback/analytics")
async def get_feedback_analytics():
    """Get feedback analytics and insights"""
    try:
        analytics = await hpgpt_graph.get_feedback_analytics()
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "analytics": analytics,
                "status": "retrieved"
            }
        )
    except Exception as e:
        logger.error(f"Error retrieving feedback analytics: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e)
            }
        )

@app.post("/upload/{session_id}")
async def upload_file(session_id: str, file: UploadFile = File(...)):
    """Upload and process files"""
    try:
        max_size = 50 * 1024 * 1024
        if file.size and file.size > max_size:
            raise HTTPException(status_code=413, detail="File too large. Maximum size is 50MB.")
        
        file_extension = os.path.splitext(file.filename)[1]
        original_filename = os.path.splitext(file.filename)[0]
        unique_id = uuid.uuid4().hex[:16]
        # unique_filename = f"{session_id}_{unique_id}_{original_filename}{file_extension}"
        unique_filename = f"{session_id}_{original_filename}{file_extension}"
        file_path = f"uploads/{unique_filename}"
        
        async with aiofiles.open(file_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
        
        logger.info(f"File uploaded: {file.filename} -> {file_path}")
        
        processed_content = await file_processor.process_file(file_path, file.content_type)
        
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "filename": file.filename,
                "file_path": file_path,
                "file_type": file.content_type,
                "file_size": len(content),
                "content": processed_content[:500] + "..." if len(processed_content) > 500 else processed_content
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File upload error: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "hpGPT Backend",
        "version": "1.0.0",
        "active_connections": len(manager.active_connections),
        "active_stop_requests": len(stop_requests),
    }

@app.get("/agents")
async def list_agents():
    """List available agents"""
    return {
        "agents": [
            {"id": "general", "name": "General Assistant", "description": "General purpose AI assistant"},
            {"id": "document", "name": "Document Agent", "description": "PDF analysis, document processing"},
            {"id": "analytics", "name": "Analytics Agent", "description": "Data analysis and business intelligence"},
            {"id": "websearch", "name": "Websearch Agent", "description": "Market websearch and industry analysis"},
            {"id": "coding", "name": "Coding Agent", "description": "Code generation and debugging"}
        ]
    }

# Endpoint to check stop status (debugging endpoint)
@app.get("/sessions/{session_id}/stop-status")
async def get_stop_status(session_id: str):
    """Get current stop status for a session (debugging endpoint)"""
    return {
        "session_id": session_id,
        "stop_requested": stop_requests.get(session_id, False),
        "timestamp": datetime.now().isoformat()
    }

# Endpoint to manually clear stop request (debugging endpoint)
@app.post("/sessions/{session_id}/clear-stop")
async def clear_stop_request(session_id: str):
    """Manually clear stop request for a session (debugging endpoint)"""
    if session_id in stop_requests:
        del stop_requests[session_id]
        return {"success": True, "message": f"Stop request cleared for session {session_id}"}
    else:
        return {"success": False, "message": f"No stop request found for session {session_id}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0", 
        port=8000,
        log_level="info",
        reload=True
    )

@app.get("/collections/structure")
async def get_collections_structure():
    """Return the full department/subcategory structure."""
    return {"categories": CATEGORIES}

@app.get("/collections/documents")
async def get_collection_documents(department: str = None, subcategory: str = None):
    """List documents in a collection."""
    docs = list_documents(department, subcategory)
    return {"documents": docs}


@app.post("/collections/upload")
async def upload_to_collection(file: UploadFile = File(...)):
    try:
        temp_path = f"uploads/temp_{file.filename}"
        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)

        from backend.agents.collections_rag import extract_text
        try:
            text = extract_text(temp_path)
        except Exception:
            text = file.filename  # fallback to filename for categorization

        suggestion = suggest_category(text)

        return {
            "success": True,
            "temp_path": temp_path,
            "filename": file.filename,
            "suggested_department": suggestion["department"],
            "suggested_subcategory": suggestion["subcategory"],
            "full_path": suggestion["full_path"]
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    
@app.post("/collections/confirm")
async def confirm_collection_placement(body: dict):
    """Confirm placement of document into a collection after user approval."""
    try:
        temp_path = body.get("temp_path")
        filename = body.get("filename")
        department = body.get("department")
        subcategory = body.get("subcategory")

        dest_path = save_document_to_collection(temp_path, department, subcategory, filename)

        # Index the document
        metadata = {
            "department": department,
            "subcategory": subcategory,
            "filename": filename,
            "path": dest_path
        }
        index_document(dest_path, metadata)

        # Clean up temp file
        if os.path.exists(temp_path):
            os.remove(temp_path)

        return {"success": True, "path": dest_path}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/collections/query")
async def query_collection(body: dict):
    try:
        question = body.get("question", "")
        department = body.get("department") or None
        subcategory = body.get("subcategory") or None
        history = body.get("history") or None
        result = query_collections(question, department=department, subcategory=subcategory, history=history)
        return result
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/collections/document/view")
async def view_document(path: str):
    """Get extracted text content of a document."""
    try:
        from backend.agents.collections_rag import extract_text
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="Document not found.")
        text = extract_text(path)
        return {"content": text, "path": path}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    
@app.post("/collections/query-with-chart")
async def query_collection_with_chart(body: dict):
    try:
        import re
        import pandas as pd
        from backend.agents.analytics_agent import AnalyticsAgent

        question = body.get("question", "")
        department = body.get("department") or None
        subcategory = body.get("subcategory") or None
        history = body.get("history") or None

        # Parse "(use file: X)" override from clarification button clicks
        forced_file = None
        file_override_match = re.search(r'\(use file:\s*([^)]+)\)', question)
        if file_override_match:
            forced_file = file_override_match.group(1).strip()
            question = re.sub(r'\s*\(use file:[^)]+\)', '', question).strip()

        rag_result = query_collections(question, department=department, subcategory=subcategory, history=history)
        sources = rag_result.get("sources", [])

        if rag_result.get("not_in_collection"):
            return rag_result

        if not sources:
            return rag_result

        chart_keywords = ["chart", "graph", "plot", "visuali", "bar", "pie", "trend", "compare"]
        is_chart_request = any(kw in question.lower() for kw in chart_keywords)

        if not is_chart_request:
            return rag_result

        # --- Scan ALL files in the collection, not just FAISS hits ---
        # FAISS may only return one file even when multiple files match.
        # We do a direct disk scan of the active department/subcategory folder.
        collections_dir = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "collections")
        )

        def get_all_data_files(dept: str = None, sub: str = None) -> list:
            """Walk the collections folder and return all xlsx/csv files in scope."""
            results = []
            for root, dirs, files in os.walk(collections_dir):
                for fname in files:
                    if not fname.endswith((".xlsx", ".csv")):
                        continue
                    full_path = os.path.join(root, fname)
                    rel = os.path.relpath(full_path, collections_dir)
                    parts = rel.split(os.sep)
                    file_dept = parts[0] if len(parts) > 0 else ""
                    file_sub  = parts[1] if len(parts) > 1 else ""
                    if dept and file_dept != dept:
                        continue
                    if sub and file_sub != sub:
                        continue
                    results.append({
                        "name": fname,
                        "path": full_path,
                        "department": file_dept,
                        "subcategory": file_sub,
                    })
            return results

        def col_matches_question(col: str, q: str) -> bool:
            """
            True if ≥50% of the column's words appear in the question.
            e.g. 'monthly_revenue_usd' vs 'monthly revenue bar chart'
            → col_words={'monthly','revenue','usd'}, overlap={'monthly','revenue'} → 67% ✓
            """
            col_words = set(re.split(r'[_\s]+', col.lower())) - {''}
            q_words   = set(re.split(r'[_\s]+', q.lower()))
            if not col_words:
                return False
            return len(col_words & q_words) / len(col_words) >= 0.5

        all_data_files = get_all_data_files(department, subcategory)

        # If no data files found on disk, fall back to FAISS sources
        if not all_data_files:
            all_data_files = [s for s in sources if s["name"].endswith((".xlsx", ".csv", ".db"))]

        if not all_data_files:
            return rag_result

        agent = AnalyticsAgent()
        question_lower_full = question.lower()

        # If user clicked a clarification button, use that file directly
        if forced_file:
            source = next((s for s in all_data_files if s["name"] == forced_file), None)
            if not source:
                source = all_data_files[0]

        # If user mentioned a filename explicitly in the question, use it
        elif any(s["name"].lower() in question_lower_full for s in all_data_files):
            source = next(s for s in all_data_files if s["name"].lower() in question_lower_full)

        else:
            # Build column map for every data file on disk
            file_columns: dict = {}
            for src in all_data_files:
                try:
                    if src["name"].endswith(".xlsx"):
                        df_temp = pd.read_excel(src["path"], engine="openpyxl", nrows=0)
                        # Handle CSV-saved-as-xlsx
                        if len(df_temp.columns) == 1 and ',' in df_temp.columns[0]:
                            import io
                            df_temp = pd.read_csv(io.StringIO(df_temp.columns[0]))
                        file_columns[src["name"]] = [c.lower().strip() for c in df_temp.columns.tolist()]
                    elif src["name"].endswith(".csv"):
                        df_temp = pd.read_csv(src["path"], nrows=0)
                        file_columns[src["name"]] = [c.lower().strip() for c in df_temp.columns.tolist()]
                    else:
                        file_columns[src["name"]] = []
                except Exception:
                    file_columns[src["name"]] = []

            # Find all files whose columns match the question
            ambiguous_sources = [
                src for src in all_data_files
                if any(col_matches_question(col, question_lower_full)
                       for col in file_columns.get(src["name"], []))
            ]

            if len(ambiguous_sources) > 1:
                file_list = "\n".join([f"• {s['name']}" for s in ambiguous_sources])
                return {
                    "answer": (
                        f"I found **Monthly_Revenue_USD** (or a similar column) in "
                        f"**{len(ambiguous_sources)} files**. Which one should I use?\n\n"
                        f"{file_list}\n\n"
                        "Click a file button below or re-ask mentioning the file name."
                    ),
                    "clarifying": True,
                    "sources": ambiguous_sources
                }

            # Only one match — use it
            source = ambiguous_sources[0] if ambiguous_sources else all_data_files[0]

        file_info = {"name": source["name"], "path": source["path"]}

        # Inject actual column names into prompt
        try:
            if source["name"].endswith(".xlsx"):
                df_preview = pd.read_excel(source["path"], engine="openpyxl", nrows=2)
                if len(df_preview.columns) == 1 and ',' in df_preview.columns[0]:
                    import io
                    df_preview = pd.read_csv(io.StringIO(
                        df_preview.columns[0] + '\n' + df_preview.iloc[:, 0].str.cat(sep='\n')
                    ))
            elif source["name"].endswith(".csv"):
                df_preview = pd.read_csv(source["path"], nrows=2)
            else:
                df_preview = None

            if df_preview is not None:
                actual_columns = df_preview.columns.tolist()
                enriched_prompt = f"{question}\n\n[Available columns in the file: {actual_columns}]"
            else:
                enriched_prompt = question
        except Exception:
            enriched_prompt = question

        result = await agent.run(file=file_info, user_prompt=enriched_prompt)
        result["sources"] = [source]
        return result

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    
@app.post("/sessions/{session_id}/save-message")
async def save_message_to_session(session_id: str, body: dict):
    try:
        user_msg = body.get("user_message", "")
        assistant_msg = body.get("assistant_message", "")
        import uuid
        from datetime import datetime

        if session_id not in hpgpt_graph.sessions:
            hpgpt_graph.sessions[session_id] = {
                "title": "Collections Chat",
                "created_at": datetime.now().isoformat(),
                "message_count": 0,
                "last_updated": datetime.now().isoformat()
            }
            hpgpt_graph.conversations[session_id] = []

        hpgpt_graph.conversations[session_id].append({"msgid": str(uuid.uuid4()), "role": "user", "content": user_msg})
        hpgpt_graph.conversations[session_id].append({"msgid": str(uuid.uuid4()), "role": "assistant", "content": assistant_msg})
        hpgpt_graph.sessions[session_id]["message_count"] += 2
        hpgpt_graph.sessions[session_id]["last_updated"] = datetime.now().isoformat()
        hpgpt_graph.save_data()

        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    

COLLECTIONS_HISTORY_FILE = "collections_history.json"

@app.get("/collections/history")
async def get_collections_history():
    try:
        if os.path.exists(COLLECTIONS_HISTORY_FILE):
            with open(COLLECTIONS_HISTORY_FILE, "r") as f:
                return json.load(f)
        return {"history": []}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/collections/history/save")
async def save_collections_history(body: dict):
    try:
        history = body.get("history", [])
        slim = []
        for entry in history:
            # Preserve full messages array (including plotHTML per message)
            raw_messages = entry.get("messages", [])
            if raw_messages:
                messages = [
                    {
                        "question": m.get("question", ""),
                        "answer": m.get("answer", ""),
                        "sources": m.get("sources", []),
                        "plotHTML": m.get("plotHTML")
                    }
                    for m in raw_messages
                ]
            else:
                messages = [{
                    "question": entry.get("question", ""),
                    "answer": entry.get("answer", ""),
                    "sources": entry.get("sources", []),
                    "plotHTML": entry.get("plotHTML")
                }]
            slim.append({
                "title": entry.get("title", ""),
                "question": entry.get("question", ""),
                "answer": entry.get("answer", ""),
                "timestamp": entry.get("timestamp", ""),
                "id": entry.get("id", ""),
                "sources": entry.get("sources", []),
                "messages": messages
            })
        with open(COLLECTIONS_HISTORY_FILE, "w") as f:
            json.dump({"history": slim}, f, indent=2)
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.delete("/collections/history/{entry_id}")
async def delete_collections_history_entry(entry_id: str):
    try:
        if os.path.exists(COLLECTIONS_HISTORY_FILE):
            with open(COLLECTIONS_HISTORY_FILE, "r") as f:
                data = json.load(f)
            data["history"] = [e for e in data["history"] if str(e.get("id")) != entry_id]
            with open(COLLECTIONS_HISTORY_FILE, "w") as f:
                json.dump(data, f, indent=2)
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})