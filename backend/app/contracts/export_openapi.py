import json
from pathlib import Path

from app.main import app


def main() -> None:
    output_path = Path(__file__).resolve().parents[2] / "openapi.json"
    output_path.write_text(
        json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote OpenAPI schema to {output_path}")


if __name__ == "__main__":
    main()
