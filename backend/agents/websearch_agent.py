import os
import requests
import google.generativeai as genai
from dotenv import load_dotenv
from autogen import AssistantAgent
from typing import Dict, Any    

# Load environment variables
load_dotenv()
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

class WebsearchAgent(AssistantAgent):
    def __init__(self):
        super().__init__(
            name="WebsearchAgent",
            system_message="You are a websearch assistant who provides factual, concise answers by combining real-time search with Gemini."
        )
        self.model = genai.GenerativeModel("gemini-2.5-flash")
        self.tavily_api_key = os.getenv("TAVILY_API_KEY")

    def tavily_search(self, query, max_results=5):
        url = "https://api.tavily.com/search"
        headers = {"Content-Type": "application/json"}
        data = {
            "api_key": self.tavily_api_key,
            "query": query,
            "max_results": max_results
        }

        snippets = []
        try:
            response = requests.post(url, json=data, headers=headers)
            response.raise_for_status()
            results = response.json().get("results", [])
            for i, r in enumerate(results):
                snippets.append(f"({i+1}) {r.get('title', 'No Title')}: {r.get('content', '')}")
        except Exception as e:
            snippets = [f"❌ Error during Tavily search: {e}"]
        return snippets

    def generate_answer(self, query, snippets, answer_mode="specific"):
        context = "\n".join(snippets)

        tone_instruction = {
            "specific": "Be concise, fact-based, and avoid unnecessary detail.",
            "detailed": "Provide an elaborate answer using the given snippets. Include reasoning, examples, and comparisons if helpful."
        }.get(answer_mode, "Be concise.")

        prompt = f"""You are a websearch assistant with access to real-time web information.
    Answer the following question using the given search snippets.

    {tone_instruction}

    Question: {query}

    Snippets:
    {context}

    Answer:"""

        response = self.model.generate_content(prompt)
        return response.text.strip()


    async def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        query = state["prompt"]
        answer_mode = state.get("answer_mode", "specific")
        print(f"🔍 WebsearchAgent handling: {query} with mode: {answer_mode}")

        snippets = self.tavily_search(query)
        if not snippets or snippets[0].startswith("❌"):
            return {"response": "No relevant search results found.", "agent_type": "websearch"}

        answer = self.generate_answer(query, snippets, answer_mode)
        return {"response": answer, "agent_type": "websearch"}
