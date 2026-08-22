import os, sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app import app, _bootstrap

if __name__ == "__main__":
    _bootstrap()
    port = int(os.environ.get("PORT", 8000))
    print(f"\n{'='*50}")
    print(f"  >> Flux Monitor Platform v2.0")
    print(f"  >> http://localhost:{port}")
    print(f"{'='*50}\n")
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)