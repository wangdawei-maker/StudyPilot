from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="StudyPilot API",
    version="0.1.0",
    description="StudyPilot learning platform API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "studypilot-api"}
