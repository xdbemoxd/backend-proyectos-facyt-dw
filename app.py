from fastapi import FastAPI, Depends, HTTPException


app = FastAPI(title="My Backend API")

@app.get("/")
def read_root():
    return {"status": "success", "message": "API is up and running"}