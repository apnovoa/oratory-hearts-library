from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root before anything else
load_dotenv(Path(__file__).resolve().parent / ".env")

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=8080)
