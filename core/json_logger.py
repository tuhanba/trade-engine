import logging
import json
from datetime import datetime, timezone

class JSONFormatter(logging.Formatter):
    """
    Formatter that outputs JSON strings after parsing the LogRecord.
    """
    def __init__(self, fmt_dict: dict = None, time_format: str = "%Y-%m-%dT%H:%M:%S.%fZ"):
        self.fmt_dict = fmt_dict if fmt_dict is not None else {"timestamp": "asctime", "level": "levelname", "message": "message"}
        self.time_format = time_format
        super().__init__()

    def usesTime(self):
        return "asctime" in self.fmt_dict.values()

    def formatMessage(self, record):
        return super().formatMessage(record)

    def format(self, record):
        record.message = record.getMessage()
        if self.usesTime():
            record.asctime = self.formatTime(record, self.time_format)

        message_dict = {}
        for key, val in self.fmt_dict.items():
            if hasattr(record, val):
                message_dict[key] = getattr(record, val)
            else:
                message_dict[key] = val

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            message_dict["exc_info"] = record.exc_text

        if record.stack_info:
            message_dict["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(message_dict)

def setup_json_logging(log_file="trade_engine.json.log", level=logging.INFO):
    """
    Configures the root logger to output structured JSON logs.
    """
    logger = logging.getLogger()
    logger.setLevel(level)

    # Remove all handlers associated with the root logger object.
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    json_formatter = JSONFormatter({
        "timestamp": "asctime",
        "level": "levelname",
        "name": "name",
        "message": "message",
        "filename": "filename",
        "lineno": "lineno"
    })

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(json_formatter)
    logger.addHandler(file_handler)

    # Optional: also log to console (can be standard or json)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(console_handler)

    return logger
