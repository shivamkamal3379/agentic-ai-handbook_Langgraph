# backend.py

from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Annotated
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.tools import tool
from dotenv import load_dotenv
import sqlite3
import requests
import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient




load_dotenv()

# -------------------
# 1. LLM
# -------------------
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=1.0,
    max_tokens=None,
    timeout=None,
    max_retries=2,
)


# MCP client for local FASTCP Server

client = MultiServerMCPClient(
    {
        "expense":{
            "transport" : "streamable_http", # second alternate to use "sse"
            "url" : "https://splendid-gold-dingo.fastmcp.app/mcp"
        },
        "arith":{
            "transport" : "stdio" , 
            "command" : "python3",
            "args" :["/Users/nitish/Desktop/mcp-math-server/main.py"],
        }
    }
)




# -------------------
# 3. State
# -------------------
class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

async def build_graph():

    tools = await client.get_tools()
    print(tools)
       
    llm_with_tools = llm.bind_tools(tools)


    async def chat_node(state: ChatState):
        """LLM node that may answer or request a tool call."""
        messages = state["messages"]
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}


    tool_node = ToolNode(tools)
    graph = StateGraph(ChatState)
    graph.add_node("chat_node", chat_node)
    graph.add_node("tools", tool_node)

    graph.add_edge(START, "chat_node")

    graph.add_conditional_edges("chat_node", tools_condition)
    graph.add_edge("tools", "chat_node")

    chatbot = graph.compile()

    return chatbot


async def main():

    chatbot =  await build_graph()

    result = await chatbot.ainvoke({'messages': [HumanMessage(content = " add an expense - of rs 500 course purchased on Udemy !! ")]})
    print(result['messages'[-1].content])

if __name__ == '__main__':
    asyncio.run(main())