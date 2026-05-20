from .document_agent import DocumentAgent
from autogen import AssistantAgent
from .coding_agent import CodingAgent
from .analytics_agent import AnalyticsAgent
from .websearch_agent import  WebsearchAgent

# Initialize document agent first
document_agents = DocumentAgent()
websearch_agents = WebsearchAgent()
coding_agents = CodingAgent() 
analytics_agent = AnalyticsAgent()

websearch_agent = AssistantAgent(
    name="WebsearchAgent",
    system_message="You are a websearcher. Search and explain complex topics simply."
)

analytics_agent = AssistantAgent(
    name="AnalyticsAgent",
    system_message="You are a data analyst. Perform data analysis and give results."
)

general_agent = AssistantAgent(
    name="GeneralAssistant",
    system_message="You are a general assistant for all-purpose questions."
)

AGENT_MAP = {
    "Document Agent": document_agents,
    "Coding Agent": coding_agents,
    "Websearch Agent": websearch_agent,
    "Analytics Agent": analytics_agent,
    "General Assistant": general_agent
}
