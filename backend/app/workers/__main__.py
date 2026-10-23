import logging
from threading import Event

from app.workers.scheduler import worker_loop


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).info("Durable scheduled-action worker started")
    worker_loop(stop_event=Event())


if __name__ == "__main__":
    main()
