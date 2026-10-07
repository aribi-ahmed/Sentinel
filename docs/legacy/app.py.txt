import os
import warnings
from typing import List, Optional, Any, Dict
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

warnings.filterwarnings("ignore")
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
load_dotenv()

CHROMA_DB_DIR = "./chroma_db_free"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

app = FastAPI(title="Sentinel Backend")

# Enable CORS for frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load database & LLM
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
vectorstore = Chroma(persist_directory=CHROMA_DB_DIR, embedding_function=embeddings)
retriever = vectorstore.as_retriever(search_kwargs={"k": 6})

llm = ChatGroq(
    model_name="llama-3.3-70b-versatile", 
    temperature=0.2,
    groq_api_key=GROQ_API_KEY
)

SYSTEM_PROMPT = """You are Sentinel, an expert AI analyst.
Answer the user's question using ONLY the provided context below.

Formatting Guidelines:
- Format your response in clean, professional Markdown.
- Use bold text, concise bullet points, and clear headers where applicable.
- If the context contains relevant information, summarize it accurately and directly.
- If the context does not contain enough information, state: "I could not find sufficient details in the ingested dataset." Do not hallucinate.

Context:
{context}

Question:
{question}

Answer:"""

prompt_template = ChatPromptTemplate.from_template(SYSTEM_PROMPT)

# Flexible payload model to accept whatever key your frontend sends
class FlexibleRequest(BaseModel):
    question: Optional[str] = None
    query: Optional[str] = None
    text: Optional[str] = None
    prompt: Optional[str] = None

@app.get("/health")
def health_check():
    return {"status": "ok"}

# Endpoint handles both routes
@app.post("/investigations")
@app.post("/api/chat")
async def run_investigation(request: FlexibleRequest):
    # Extract whichever key the frontend sent
    user_query = request.question or request.query or request.text or request.prompt

    if not user_query or not user_query.strip():
        raise HTTPException(status_code=400, detail="Query string cannot be empty.")

    try:
        retrieved_docs = retriever.invoke(user_query)
        context_text = "\n\n".join(doc.page_content for doc in retrieved_docs)
        
        sources_list = []
        for doc in retrieved_docs:
            source_file = os.path.basename(doc.metadata.get("source", "Unknown Document"))
            snippet = doc.page_content[:150].replace("\n", " ") + "..."
            sources_list.append({"file": source_file, "snippet": snippet})

        chain = prompt_template | llm | StrOutputParser()
        answer_text = chain.invoke({
            "context": context_text, 
            "question": user_query
        })

        # Returns flexible response keys so your UI picks up the answer regardless of expected property name
        return {
            "status": "success",
            "query": user_query,
            "answer": answer_text,
            "result": answer_text,
            "response": answer_text,
            "sources": sources_list
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG Execution Error: {str(e)}")