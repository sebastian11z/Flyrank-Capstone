import uvicorn

if __name__ == "__main__":
    # Access logs include URLs; keep credentials out of logs even if a client misuses a query string.
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, access_log=False)

