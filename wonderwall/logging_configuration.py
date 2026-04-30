import logging
import os
import sys
from typing import List

LOG_LEVEL = os.environ.get("LOG_LEVEL", logging.INFO)


def setup_logger():
    # Some libraries manipulate the logger levels, this makes sure they are all reset
    # to a default state:
    loggers = [logging.getLogger(name) for name in logging.root.manager.loggerDict]
    for logger in loggers:
        logger.setLevel(logging.NOTSET)
        logger.propagate = True
        for handler in logger.handlers:
            logger.removeHandler(handler)

    log_file_location = os.environ.get("LOG_FILE", None)
    log_format = "%(asctime)-15s.%(msecs)03d [%(name)-24s][%(levelname)-8s] %(message)s"

    formatter = logging.Formatter(log_format, "%Y-%m-%dT%H:%M:%S")

    handlers: List[logging.Handler] = [logging.StreamHandler(stream=sys.stdout)]

    if log_file_location is not None:
        handlers.append(logging.FileHandler(log_file_location))

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        logging.getLogger().removeHandler(handler)

    for handler in handlers:
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)

    root_logger.setLevel(LOG_LEVEL)
