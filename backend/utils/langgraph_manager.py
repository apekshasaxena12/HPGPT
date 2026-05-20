from langgraph.graph import MessagesState
from langchain_core.messages import AIMessage
from typing import Literal, TypedDict, List, Dict, Optional
import os
import logging
from datetime import datetime
import asyncio
import json
import uuid

from backend.agents.coding_agent import CodingAgent
from backend.agents.analytics_agent import AnalyticsAgent
from backend.agents.websearch_agent import WebsearchAgent
from backend.agents.general_agent import GeneralAgent
from backend.utils.langgraph_pipeline import build_langgraph
from backend.agents.document_agent import DocumentAgent
from backend.utils.groq_client import groq_client
from backend.database.db_manager import database
from backend.agents.database_agent import build_db_query_graph


from fastapi import WebSocket

from dotenv import load_dotenv
load_dotenv()
logger = logging.getLogger(__name__)

RAG_API_KEY = os.getenv("RAG_API_KEY")
API_BASE_URL = os.getenv("API_BASE_URL")  # Make sure your env var name matches
HEADERS = {"Authorization": f"Bearer {RAG_API_KEY}"}

now = datetime.now()

class GraphConfig(TypedDict):
    agent_type: Literal["general", "document", "analytics", "websearch", "coding"]

class HPGPTGraph:
    def __init__(self):
        self.document_agent = DocumentAgent()
        self.analytics_agent = AnalyticsAgent()
        self.websearch_agent = WebsearchAgent()
        self.coding_agent = CodingAgent()
        self.groq_client = groq_client
        self.general_agent = GeneralAgent(groq_client)
        self.database_agent = build_db_query_graph()
        self.sessions_file = "sessions.json"
        self.conversations_file = "conversations.json"
        self.feedback_file = "feedback.json"
        
        self.langgraph_app = build_langgraph(
            self.coding_agent,
            self.analytics_agent,
            self.websearch_agent,
            self.general_agent,
            self.groq_client,
            self.database_agent
        )
        
        self.load_data()
    
    def load_data(self):
        """Load sessions, conversations, and feedback from JSON files"""
        try:
            # Load sessions
            if os.path.exists(self.sessions_file):
                with open(self.sessions_file, 'r') as f:
                    self.sessions = json.load(f)
            else:
                self.sessions = {}
            
            # Load conversations
            if os.path.exists(self.conversations_file):
                with open(self.conversations_file, 'r') as f:
                    self.conversations = json.load(f)
            else:
                self.conversations = {}
            
            # Load feedback
            if os.path.exists(self.feedback_file):
                with open(self.feedback_file, 'r') as f:
                    self.feedback_data = json.load(f)
            else:
                self.feedback_data = {}
                
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            self.sessions = {}
            self.conversations = {}
            self.feedback_data = {}
    
    def save_data(self):
        """Save sessions, conversations, and feedback to JSON files"""
        try:
            with open(self.sessions_file, 'w') as f:
                json.dump(self.sessions, f, indent=2)
            with open(self.conversations_file, 'w') as f:
                json.dump(self.conversations, f, indent=2)
            with open(self.feedback_file, 'w') as f:
                json.dump(self.feedback_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving data: {e}")

    async def store_feedback(self, session_id: str, message_content: str, feedback_type: str, 
                           agent_type: str, answer_mode: str, timestamp: str) -> Dict:
        try:
            # Agent type mapping
            AGENT_TYPE_MAP = {
                "general": "GeneralAssistant",
                "coding": "CodingAgent",
                "analytics": "AnalyticsAgent",
                "websearch": "WebsearchAgent",
                "document": "DocumentAgent"
            }

            # Map agent_type to actual agentname in DB
            agentname = AGENT_TYPE_MAP.get(agent_type.lower())
            if not agentname:
                raise ValueError(f"Unknown agent type: {agent_type}")

            feedback_id = str(uuid.uuid4())
            
            feedback_entry = {
                "feedback_id": feedback_id,
                "session_id": session_id,
                "message_content": message_content[:500],
                "feedback_type": feedback_type,
                "agent_type": agent_type,
                "answer_mode": answer_mode,
                "timestamp": timestamp,
                "message_length": len(message_content),
                "created_at": datetime.now().isoformat()
            }
            
            if session_id not in self.feedback_data:
                self.feedback_data[session_id] = []
            
            self.feedback_data[session_id].append(feedback_entry)

            # SQL insert
            query = """
            INSERT INTO feedback (
                feedbackid,
                userid,
                chatid,
                msg_content,
                agentid,
                feedback_type,
                answer_mode,
                msg_length
            )
            VALUES (
                :feedbackid,
                (SELECT userid FROM chats WHERE chatid = :chatid),
                :chatid,
                :msg_content,
                (SELECT agentid FROM agents WHERE agentname = :agentname),
                :feedback_type,
                :answer_mode,
                :msg_length
            );
            """

            values = {
                "feedbackid": feedback_id,
                "chatid": session_id,
                "msg_content": message_content[:500],
                "agentname": agentname,
                "feedback_type": feedback_type,
                "answer_mode": answer_mode,
                "msg_length": len(message_content)
            }

            await database.execute(query=query, values=values)

            self.save_data()
            logger.info(f"Stored {feedback_type} feedback for session {session_id}")
            
            return {
                "feedback_id": feedback_id,
                "status": "stored",
                "session_id": session_id
            }
            
        except Exception as e:
            logger.error(f"Error storing feedback: {e}")
            return None

    async def get_feedback_analytics(self) -> Dict:
        try:
            analytics = {
                "total_feedback": 0,
                "positive_feedback": 0,
                "negative_feedback": 0,
                "feedback_by_agent": {},
                "feedback_by_mode": {},
                "recent_feedback": [],
                "improvement_suggestions": []
            }
            all_feedback = []
            for session_feedback in self.feedback_data.values():
                all_feedback.extend(session_feedback)
            analytics["total_feedback"] = len(all_feedback)
            for feedback in all_feedback:
                if feedback["feedback_type"] == "positive":
                    analytics["positive_feedback"] += 1
                else:
                    analytics["negative_feedback"] += 1
                agent = feedback["agent_type"]
                if agent not in analytics["feedback_by_agent"]:
                    analytics["feedback_by_agent"][agent] = {"positive": 0, "negative": 0}
                analytics["feedback_by_agent"][agent][feedback["feedback_type"]] += 1
                mode = feedback["answer_mode"]
                if mode not in analytics["feedback_by_mode"]:
                    analytics["feedback_by_mode"][mode] = {"positive": 0, "negative": 0}
                analytics["feedback_by_mode"][mode][feedback["feedback_type"]] += 1
            sorted_feedback = sorted(all_feedback, key=lambda x: x["timestamp"], reverse=True)
            analytics["recent_feedback"] = sorted_feedback[:10]
            analytics["improvement_suggestions"] = self._generate_improvement_suggestions(analytics)
            return analytics
        except Exception as e:
            logger.error(f"Error getting feedback analytics: {e}")
            return {
                "total_feedback": 0,
                "positive_feedback": 0,
                "negative_feedback": 0,
                "error": str(e)
            }

    def _generate_improvement_suggestions(self, analytics: Dict) -> List[str]:
        """Generate improvement suggestions based on feedback patterns"""
        suggestions = []
        try:
            total = analytics["total_feedback"]
            if total == 0:
                return ["No feedback data available yet."]
            positive_rate = analytics["positive_feedback"] / total
            if positive_rate < 0.7:
                suggestions.append("Consider improving response quality - positive feedback rate is below 70%")
            for agent, feedback in analytics["feedback_by_agent"].items():
                agent_total = feedback["positive"] + feedback["negative"]
                if agent_total > 0:
                    agent_positive_rate = feedback["positive"] / agent_total
                    if agent_positive_rate < 0.6:
                        suggestions.append(f"Focus on improving {agent} agent responses")
            for mode, feedback in analytics["feedback_by_mode"].items():
                mode_total = feedback["positive"] + feedback["negative"]
                if mode_total > 0:
                    mode_positive_rate = feedback["positive"] / mode_total
                    if mode_positive_rate < 0.6:
                        suggestions.append(f"Improve {mode} answer mode responses")
            if not suggestions:
                suggestions.append("Great job! Feedback patterns look positive. Keep up the good work!")
        except Exception as e:
            logger.error(f"Error generating suggestions: {e}")
            suggestions.append("Unable to generate suggestions due to data processing error")
        return suggestions

    def _extract_chat_title(self, first_message: str) -> str:
        """Extract a meaningful title from the first user message with smart summarization"""
        title = first_message.strip()
        prefixes_to_remove = ['hi', 'hello', 'hey', 'can you', 'please', 'i need', 'help me']
        title_lower = title.lower()
        
        for prefix in prefixes_to_remove:
            if title_lower.startswith(prefix):
                title = title[len(prefix):].strip()
                break
        
        title_lower = title.lower()
        
        if any(word in title_lower for word in ['hpcl', 'hindustan petroleum']):
            if 'what is' in title_lower or 'about' in title_lower:
                return "HPCL Company Information"
            elif 'services' in title_lower:
                return "HPCL Services Inquiry"
            elif 'history' in title_lower:
                return "HPCL Company History"
            else:
                return "HPCL Discussion"
        
        if any(word in title_lower for word in ['document', 'pdf', 'file', 'analyze', 'upload']):
            if 'analyze' in title_lower:
                return "Document Analysis Request"
            elif 'upload' in title_lower:
                return "File Upload Query"
            else:
                return "Document Processing"
        
        if any(word in title_lower for word in ['data', 'analytics', 'report', 'dashboard', 'chart']):
            if 'create' in title_lower or 'generate' in title_lower:
                return "Data Visualization Request"
            elif 'analyze' in title_lower:
                return "Data Analysis Query"
            else:
                return "Analytics Discussion"
        
        if any(word in title_lower for word in ['code', 'script', 'python', 'programming', 'api']):
            if 'python' in title_lower:
                return "Python Programming Help"
            elif 'api' in title_lower:
                return "API Development Query"
            elif 'script' in title_lower:
                return "Script Generation Request"
            else:
                return "Coding Assistance"
        
        if any(word in title_lower for word in ['research', 'market', 'trends', 'industry', 'competitor']):
            if 'market' in title_lower:
                return "Market Research Query"
            elif 'trends' in title_lower:
                return "Industry Trends Discussion"
            elif 'competitor' in title_lower:
                return "Competitive Analysis"
            else:
                return "Research Request"
        
        if title_lower.startswith('what'):
            if 'time' in title_lower or 'date' in title_lower:
                return "Time/Date Query"
            elif 'weather' in title_lower:
                return "Weather Information"
            elif 'how to' in title_lower:
                return "How-to Question"
            else:
                subject = title.replace('what is', '').replace('what are', '').replace('?', '').strip()
                if len(subject) > 30:
                    subject = subject[:27] + "..."
                return f"About {subject.title()}" if subject else "General Question"
        
        elif title_lower.startswith('how'):
            if 'how to' in title_lower:
                action = title.replace('how to', '').replace('?', '').strip()
                if len(action) > 25:
                    action = action[:22] + "..."
                return f"How to {action.title()}" if action else "How-to Question"
            elif 'how are you' in title_lower:
                return "Greeting & Status Check"
            else:
                return "How-to Question"
        
        elif title_lower.startswith('why'):
            subject = title.replace('why', '').replace('?', '').strip()
            if len(subject) > 30:
                subject = subject[:27] + "..."
            return f"Why {subject.title()}" if subject else "Why Question"
        
        elif title_lower.startswith('when'):
            return "When/Timing Question"
        
        elif title_lower.startswith('where'):
            return "Location/Where Question"
        
        elif title_lower.startswith('who'):
            return "Who/Person Question"
        
        if any(word in title_lower for word in ['create', 'make', 'build', 'develop']):
            if 'report' in title_lower:
                return "Report Creation Request"
            elif 'dashboard' in title_lower:
                return "Dashboard Development"
            elif 'script' in title_lower:
                return "Script Creation"
            else:
                return "Creation/Development Task"
        
        if any(word in title_lower for word in ['explain', 'describe', 'tell me about']):
            subject = title
            for phrase in ['explain', 'describe', 'tell me about']:
                subject = subject.replace(phrase, '').strip()
            if len(subject) > 30:
                subject = subject[:27] + "..."
            return f"Explanation: {subject.title()}" if subject else "Explanation Request"
        
        if any(word in title_lower for word in ['compare', 'difference', 'vs', 'versus']):
            return "Comparison Query"
        
        if any(word in title_lower for word in ['list', 'show me', 'give me']):
            return "Information Request"
        
        if title_lower in ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening']:
            return "Initial Greeting"
        
        if 'how are you' in title_lower:
            return "Greeting & Status Check"
        
        words = title.split()
        if len(words) <= 3:
            return title.title()
        elif len(words) <= 6:
            return ' '.join(words).title()
        else:
            return ' '.join(words[:5]).title() + "..."

    async def _generate_smart_title(self, first_message: str) -> str:
        try:
            title_prompt = [
                {
                    "role": "system", 
                    "content": "Generate a concise 3-6 word title for a chat conversation based on the user's first message. The title should capture the main topic or question. Examples: 'HPCL Company Information', 'Python Script Help', 'Market Research Query', 'Document Analysis Request'. Only return the title, nothing else."
                },
                {
                    "role": "user", 
                    "content": f"First message: {first_message}"
                }
            ]
            
            title_response = await groq_client.generate_response(title_prompt, stream=False)
            title = title_response.strip().replace('"', '').replace("'", '')
            
            if len(title) > 60:
                title = title[:57] + "..."
            
            return title if title else self._extract_chat_title(first_message)
            
        except Exception as e:
            logger.error(f"Error generating smart title: {e}")
            return self._extract_chat_title(first_message)

    def _get_conversation_context(self, conversation_history, current_message):
        """Extract relevant context from full conversation history, including assistant replies."""
        context = {
            'user_name': None,
            'previous_topics': [],
            'relevant_history': []
        }

        for i, msg in enumerate(conversation_history):
            role = msg.get('role')
            content = msg.get('content', '').lower()

            # Extract name from either user input or assistant reply
            if role == 'user' or role == 'assistant':
                if 'my name is' in content:
                    name_part = content.split('my name is')[1].strip().split()[0]
                    context['user_name'] = name_part.title()
                elif 'i am' in content and len(content.split()) <= 6:
                    name_part = content.split('i am')[1].strip().split()[0]
                    context['user_name'] = name_part.title()
                elif 'nice to meet you' in content or 'hello' in content:
                    tokens = content.split()
                    for j, word in enumerate(tokens):
                        if word in ['mike', 'john', 'rahul']:  # Add fallback known names or use NER later
                            context['user_name'] = word.title()
                            break

            # Extract previous topics from either side
            if any(word in content for word in ['hpcl', 'petroleum', 'oil', 'gas']):
                context['previous_topics'].append('HPCL/Petroleum')
            if any(word in content for word in ['document', 'pdf', 'file']):
                context['previous_topics'].append('Document Analysis')
            if any(word in content for word in ['data', 'analytics', 'report']):
                context['previous_topics'].append('Data Analytics')

        # Build relevant history if current message matches old content
        current_lower = current_message.lower()
        for i, msg in enumerate(conversation_history):
            if context['user_name'] and context['user_name'].lower() in current_lower:
                msg_content = msg.get('content', '').lower()
                if context['user_name'].lower() in msg_content:
                    context['relevant_history'].append(f"Previous context: {msg['content']}")
                    if i + 1 < len(conversation_history):
                        context['relevant_history'].append(f"Response: {conversation_history[i + 1]['content']}")

        return context
    
    def _get_feedback_context(self, session_id: str) -> str:
        """Get feedback context for improving responses"""
        try:
            session_feedback = self.feedback_data.get(session_id, [])
            if not session_feedback:
                return ""
            
            recent_feedback = session_feedback[-5:]
            negative_feedback = [f for f in recent_feedback if f["feedback_type"] == "negative"]
            
            if negative_feedback:
                feedback_context = "\nIMPORTANT: Previous responses received negative feedback. "
                feedback_context += "Focus on being more helpful, accurate, and comprehensive. "
                feedback_context += "Ensure responses are well-formatted and directly address the user's needs."
                return feedback_context
            
            return ""
            
        except Exception as e:
            logger.error(f"Error getting feedback context: {e}")
            return ""

    async def general_agent_node(self, state: MessagesState):
        try:
            system_content = f"""You are hpGPT, an AI assistant for HPCL (Hindustan Petroleum Corporation Limited). 

            Current date and time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

            RESPONSE FORMATTING REQUIREMENTS:
            - Use **bold** for company names, important terms, and key concepts
            - Use *italics* for emphasis and technical terms
            - Use ## for main section headers
            - Use ### for subsection headers
            - Use bullet points (•) for lists and features
            - Add proper spacing between sections with line breaks
            - Use emojis sparingly but effectively (🚀, 📊, 💡, etc.)
            - Format numbers and data clearly
            - Make responses visually appealing and easy to scan

            CONTENT GUIDELINES:
            - Be helpful, accurate, and professional
            - Provide specific, actionable information about HPCL services
            - Structure complex information with clear headers and sections
            - Use examples and practical applications when possible
            - Always provide complete, comprehensive responses
            - NEVER stop mid-sentence or cut off responses
            - Remember and reference previous conversation context when relevant
            """
            
            messages = [{"role": "system", "content": system_content}]
            
            recent_messages = state["messages"][-20:] if len(state["messages"]) > 20 else state["messages"]
            
            for msg in recent_messages:
                if hasattr(msg, 'content'):
                    if msg.__class__.__name__ == 'HumanMessage':
                        messages.append({"role": "user", "content": msg.content})
                    elif msg.__class__.__name__ == 'AIMessage':
                        messages.append({"role": "assistant", "content": msg.content})
            
            response = await groq_client.generate_response(messages, stream=False)
            return {"messages": [AIMessage(content=response)]}
            
        except Exception as e:
            logger.error(f"General agent error: {e}")
            return {"messages": [AIMessage(content=f"I apologize, but I encountered an error: {str(e)}")]}

    
    async def chat(self, message: str, session_id: str, files=None, answer_mode: str = "specific", should_stop=None, user_msg_id: str = None, assistant_msg_id: str = None, user_id: Optional[int] = None,websocket: Optional[WebSocket] = None
    ):
        # Initialize session if new
        if session_id not in self.sessions:
            smart_title = await self._generate_smart_title(message)
            
            self.sessions[session_id] = {
                "user_id":user_id,  
                "title": smart_title,
                "created_at": datetime.now().isoformat(),
                "message_count": 0,
                "last_updated": datetime.now().isoformat()
            }
            self.conversations[session_id] = []
            
            query = """INSERT INTO chats (chatid, userid, chatname) VALUES (:chatid, :userid, :chatname);"""
            values = {
                    "chatid": session_id,
                    "userid": user_id,
                    "chatname": smart_title,
                }
            await database.execute(query=query, values=values)
            
            self.save_data()            
            logger.info(f"Created new session with smart title: {smart_title}")

        # Check for stop request during streaming
        def check_should_stop():
            return should_stop() if should_stop else False

        # Handle greetings with agent-specific responses
        message_clean = message.lower().strip().rstrip('!?.')
        greetings  = ['hi', 'hello', 'hey', 'how are you', 'good morning', 'good afternoon', 'good evening']
  
        if message_clean in greetings :
            quick_response ="👋 Hello! I'm your assistant for HPCL. Ask me anything!"
               
            for word in quick_response.split():
                if check_should_stop():
                    break
                yield word + " "
                await asyncio.sleep(0.02)
            
            # Save to conversation history only if not stopped
            if not check_should_stop():
                self.conversations[session_id].append({"msgid": user_msg_id, "role": "user", "content": message})
                self.conversations[session_id].append({"msgid": assistant_msg_id, "role": "assistant", "content": quick_response})
                self.sessions[session_id]["message_count"] += 2
                self.sessions[session_id]["last_updated"] = datetime.now().isoformat()
                
                await database.execute(
                    "INSERT INTO messages (msgid, chatid, sendertype, content, timestamp) VALUES (:msgid, :chatid, 'user', :content, :timestamp)",
                    {"msgid": user_msg_id, "chatid": session_id, "content": message, "timestamp": now})

                await database.execute(
                    "INSERT INTO messages (msgid, chatid, sendertype, content, agentid, timestamp) VALUES (:msgid, :chatid, 'assistant', :content, (SELECT agentid FROM agents WHERE agentname = :agentname), :timestamp)",
                    {"msgid": assistant_msg_id, "chatid": session_id, "content": quick_response, "agentname":"GeneralAssistant","timestamp": now})

                await database.execute(
                    "UPDATE chats SET last_updated = :timestamp WHERE chatid = :chatid",
                    {"chatid": session_id, "timestamp": now})

                self.save_data()
           
            return

        # For non-greetings, use groq_client with agent-specific system prompt
        try:

            state = {
                "prompt": message,
                "session_id": session_id,
                "files": files,
                "history": self.conversations.get(session_id, []),
                "answer_mode": answer_mode,
                "websocket": websocket,
            }

            result = await self.langgraph_app.ainvoke(state)
            complete_response = result.get("response", "")
            
            if not complete_response.strip():
                complete_response = "I couldn't generate a response. Could you rephrase or try again?"

            for line in complete_response.splitlines():
                if check_should_stop():
                    break
                yield line + "\n"
                await asyncio.sleep(0.01)
                
                            
            # Save conversation
            if not check_should_stop():
                self.conversations[session_id].append({"msgid": user_msg_id, "role": "user", "content": message})
                self.conversations[session_id].append({"msgid": assistant_msg_id,"role": "assistant", "content": complete_response})
                self.sessions[session_id]["message_count"] += 2
                self.sessions[session_id]["last_updated"] = datetime.now().isoformat()
                
                # Insert user message
                await database.execute(
                    "INSERT INTO messages (msgid, chatid, sendertype, content, timestamp) VALUES (:msgid, :chatid, 'user', :content, :timestamp)",
                    {"msgid": user_msg_id, "chatid": session_id, "content": message, "timestamp": now}
                )

                # Insert assistant message
                await database.execute(
                    "INSERT INTO messages (msgid, chatid, sendertype, content, agentid, timestamp) VALUES (:msgid, :chatid, 'assistant', :content, (SELECT agentid FROM agents WHERE agentname = :agentname), :timestamp)",
                    {"msgid": assistant_msg_id, "chatid": session_id, "content": complete_response, "agentname":"GeneralAssistant", "timestamp": now}
                )

                # Update last_updated timestamp in chats
                await database.execute(
                    "UPDATE chats SET last_updated = :timestamp WHERE chatid = :chatid",
                    {"chatid": session_id, "timestamp": now}
                )

                self.save_data()
                
                            
        except Exception as e:
            error_response = f"⚠️ Something went wrong: {str(e)}"
            
            # Stream the error response
            for word in error_response.split():
                if check_should_stop():
                    break
                yield word + " "
                await asyncio.sleep(0.02)
            
            # Save error response to conversation history
            if not check_should_stop():  
                self.conversations[session_id].append({"msgid": user_msg_id, "role": "user", "content": message})
                self.conversations[session_id].append({"msgid": assistant_msg_id, "role": "assistant", "content": error_response})
                self.sessions[session_id]["message_count"] += 2
                self.sessions[session_id]["last_updated"] = datetime.now().isoformat()
                
                # 1. Insert user message
                await database.execute(
                    "INSERT INTO messages (msgid, chatid, sendertype, content, timestamp) VALUES (:msgid, :chatid, 'user', :content, :timestamp)",
                    {"msgid": user_msg_id, "chatid": session_id, "content": message, "timestamp": now}
                )

                # 2. Insert assistant error message
                await database.execute(
                    "INSERT INTO messages (msgid, chatid, sendertype, content, agentid, timestamp) VALUES (:msgid, :chatid, 'assistant', :content,(SELECT agentid FROM agents WHERE agentname = :agentname), :timestamp)",
                    {"msgid": assistant_msg_id, "chatid": session_id, "content": error_response,"agentname":"GeneralAssistant", "timestamp": now}
                )

                # 3. Update chat's last_updated timestamp
                await database.execute(
                    "UPDATE chats SET last_updated = :timestamp WHERE chatid = :chatid",
                    {"chatid": session_id, "timestamp": now}
    )  
                self.save_data()

    async def get_limited_chat_history(self, session_id: str, limit: int):
        """Get conversation history with message limit"""
        try:
            logger.info(f"Retrieving limited chat history for session: {session_id}, limit: {limit}")
            
            conversation = self.conversations.get(session_id, [])
            
            if conversation and limit > 0:
                limited_conversation = conversation[-limit:]
                formatted_history = [{"messages": limited_conversation}]
                logger.info(f"Retrieved {len(limited_conversation)} limited messages for session {session_id}")
                return formatted_history
            else:
                logger.info(f"No conversation found or invalid limit for session {session_id}")
                return []
                
        except Exception as e:
            logger.error(f"Error retrieving limited chat history: {e}")
            return []

    async def get_total_message_count(self, session_id: str) -> int:
        """Get total number of messages in a conversation"""
        try:
            conversation = self.conversations.get(session_id, [])
            total_count = len(conversation)
            logger.info(f"Total message count for session {session_id}: {total_count}")
            return total_count
        except Exception as e:
            logger.error(f"Error getting message count: {e}")
            return 0

    async def get_conversation_stats(self, session_id: str) -> Dict:
        """Get comprehensive conversation statistics"""
        try:
            conversation = self.conversations.get(session_id, [])
            
            user_messages = sum(1 for msg in conversation if msg.get('role') == 'user')
            assistant_messages = sum(1 for msg in conversation if msg.get('role') == 'assistant')
            
            return {
                "total_messages": len(conversation),
                "user_messages": user_messages,
                "assistant_messages": assistant_messages,
                "session_id": session_id
            }
        except Exception as e:
            logger.error(f"Error getting conversation stats: {e}")
            return {
                "total_messages": 0,
                "user_messages": 0,
                "assistant_messages": 0,
                "session_id": session_id
            }
    
    async def get_all_sessions(self) -> List[Dict]:
        """Get all chat sessions from JSON storage"""
        try:
            sessions_list = []
            
            for session_id, session_data in self.sessions.items():
                sessions_list.append({
                    "user_id":session_data.get("user_id"),
                    "session_id": session_id,
                    "title": session_data.get("title", f"Chat {session_id[:8]}"),
                    "created_at": session_data.get("created_at"),
                    "message_count": session_data.get("message_count", 0),
                    "last_updated": session_data.get("last_updated")
                })
            
            sessions_list.sort(key=lambda x: x.get("last_updated", ""), reverse=True)
            
            logger.info(f"Returning {len(sessions_list)} sessions")
            return sessions_list
                
        except Exception as e:
            logger.error(f"Error retrieving sessions: {e}")
            return []
    
    async def get_chat_history(self, session_id: str):
        """Get conversation history from JSON storage"""
        try:
            logger.info(f"Retrieving chat history for session: {session_id}")
            
            conversation = self.conversations.get(session_id, [])
            
            if conversation:
                formatted_history = [{"messages": conversation}]
                logger.info(f"Retrieved {len(conversation)} messages for session {session_id}")
                return formatted_history
            else:
                logger.info(f"No conversation found for session {session_id}")
                return []
                
        except Exception as e:
            logger.error(f"Error retrieving chat history: {e}")
            return []
    
    async def delete_session(self, session_id: str) -> bool:
        """Delete a chat session and its feedback"""
        try:
            if session_id in self.sessions:
                del self.sessions[session_id]
            if session_id in self.conversations:
                del self.conversations[session_id]
            if session_id in self.feedback_data:
                del self.feedback_data[session_id]
            
            await database.execute(
                "DELETE FROM chats WHERE chatid = :chatid",
                {"chatid": session_id}
            )
            self.save_data()
            return True
            
        except Exception as e:
            logger.error(f"Error deleting session {session_id}: {e}")
            return False

hpgpt_graph = HPGPTGraph()
