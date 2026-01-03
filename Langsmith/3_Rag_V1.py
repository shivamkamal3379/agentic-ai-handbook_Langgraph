import os
from dotenv import load_dotenv

from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS

from langchain_google_genai import (
    ChatGoogleGenerativeAI,
    GoogleGenerativeAIEmbeddings
)

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import (
    RunnableParallel,
    RunnablePassthrough,
    RunnableLambda
)
from langchain_core.output_parsers import StrOutputParser



os.environ['LANCGCHAIN_PROJECT'] = 'RAG_PROJECT'
# ------------------------------------------------------------------
load_dotenv()  # expects GOOGLE_API_KEY
PDF_PATH = "islr.pdf"
# ------------------------------------------------------------------

# 1) Load PDF
loader = PyPDFLoader(PDF_PATH)
docs = loader.load()

# 2) Chunk
splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=150
)
splits = splitter.split_documents(docs)

# 3) Gemini Embeddings + FAISS
embeddings = GoogleGenerativeAIEmbeddings(
    model="models/embedding-001"
)

vectorstore = FAISS.from_documents(splits, embeddings)
retriever = vectorstore.as_retriever(
    search_type="similarity",
    search_kwargs={"k": 4}
)

# 4) Prompt
prompt = ChatPromptTemplate.from_messages([
    ("system", "Answer ONLY from the provided context. If the answer is not in the context, say 'I don't know.'"),
    ("human", "Question: {question}\n\nContext:\n{context}")
])

# 5) Gemini LLM
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0
)

def format_docs(docs):
    return "\n\n".join(d.page_content for d in docs)

parallel = RunnableParallel({
    "context": retriever | RunnableLambda(format_docs),
    "question": RunnablePassthrough()
})

chain = parallel | prompt | llm | StrOutputParser()

# 6) Ask
print("📘 Gemini PDF RAG ready (Ctrl+C to exit)")
while True:
    q = input("\nQ: ").strip()
    if not q:
        continue
    ans = chain.invoke(q)
    print("\nA:", ans)
