import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel
from strands_tools import calculator

app = BedrockAgentCoreApp()

region = boto3.session.Session().region_name or "us-east-1"
nova_pro_model_id = "us.amazon.nova-pro-v1:0"
if region.startswith("eu"):
    nova_pro_model_id = "eu.amazon.nova-pro-v1:0"
elif region.startswith("ap"):
    nova_pro_model_id = "apac.amazon.nova-pro-v1:0"


@tool
def weather(city: str) -> str:
    """Get demo weather information for a city or location."""
    return f"Weather for {city}: Sunny, 35 C"


agent = Agent(
    model=BedrockModel(model_id=nova_pro_model_id),
    system_prompt="You are a helpful assistant that provides concise responses.",
    tools=[weather, calculator],
)


@app.entrypoint
async def strands_agent_bedrock(payload, context):
    """Invoke the Strands agent with an AgentCore Runtime payload."""
    user_input = payload.get("prompt", "No prompt found")
    response = agent(user_input)
    return response


if __name__ == "__main__":
    app.run()