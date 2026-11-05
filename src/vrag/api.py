from dotenv import load_dotenv

load_dotenv()  # Langfuse SDK reads LANGFUSE_* from os.environ

from fastapi import FastAPI  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from vrag import answer, store  # noqa: E402
from vrag.models import AskResponse  # noqa: E402

app = FastAPI(title="versioned-rag")


class AskRequest(BaseModel):
    question: str


@app.get("/health")
def health() -> dict:
    return {"elasticsearch": store.es().ping()}


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    try:
        return answer.ask(req.question)
    finally:
        answer.flush()
