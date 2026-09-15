"""Dev launcher for Meridian. Port 8012, with reload."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("backend.app:app", host="127.0.0.1", port=8012, reload=True)
