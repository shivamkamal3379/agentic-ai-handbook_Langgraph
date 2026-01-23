from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

load_dotenv()
# simple LLM Call
# Simple one-line prompt
prompt = PromptTemplate.from_template("{question}")

model = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=1.0, 
    max_tokens=None,
    timeout=None, 
    max_retries=2,
) 
parser = StrOutputParser() 

# Chain: prompt → model → parser 
chain = prompt | model | parser 

# Run it 
result = chain.invoke({"question": "What is the capital of Peru?"})
print(result)
 