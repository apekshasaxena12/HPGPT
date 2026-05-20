class GeneralAgent:
    def __init__(self, groq_client):
        self.groq_client = groq_client
        self.name = "GeneralAgent"

    async def run(self, state):
        prompt = state["prompt"]
        history = state.get("history", [])
        answer_mode = state.get("answer_mode", "specific")

        response = await self.groq_client.get_response(prompt, history, answer_mode)
        return {
            "response": response,
            "agent_type": "GeneralAgent"
        }

