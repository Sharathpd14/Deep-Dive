from fastapi import FastAPI

app = FastAPI(title="DeepDive")

@app.get("/health")
def health():
    return {"status": "ok"}