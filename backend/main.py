from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import json
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
    # Startup
    os.makedirs("uploads", exist_ok=True)
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