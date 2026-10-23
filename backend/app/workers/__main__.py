import logging
from threading import Event


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).info(
        "Background worker started; durable due-work handling is added in C3."
    )
    Event().wait()


if __name__ == "__main__":
    main()
