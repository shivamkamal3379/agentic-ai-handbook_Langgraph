from langgraph.graph import StateGraph
from typing import TypedDict , Annotated
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import add_messages
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()


llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=1.0, 
    max_tokens=None,
    timeout=None,
    max_retries=2,
) 


class ChatState(TypedDict):
    messages : Annotated[list[BaseMessage], add_messages]

def chat_node(state:ChatState):

    messages = state['messages']
    response = llm.invoke(messages)
    return {"messages": [response]}

# checkpointer 

checkpointer = InMemorySaver()

graph = StateGraph(ChatState)

graph.add_node("chat_node" , chat_node)
graph.add_edge(START , "chat_node" )
graph.add_edge('chat_node' , END )

chatbot = graph.compile(checkpointer = checkpointer)

