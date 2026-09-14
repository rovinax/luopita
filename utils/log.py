import logging
import logging.handlers
import os
import sys
# Singleton Logger for Chatbot Application

class ChatbotLogger:
    _instance = None

    def __new__(cls, log_dir: str = "log", log_file: str = "chatbot.log", log_level: str = "INFO"):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance.logger = logging.getLogger('chatbot')
            level = getattr(logging, (log_level or "INFO").upper(), logging.INFO)
            cls._instance.logger.setLevel(level)
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            cls._instance.logger.addHandler(console_handler)
            # File handler
            os.makedirs(log_dir, exist_ok=True)
            log_filename = os.path.join(log_dir, log_file)
            file_handler = logging.handlers.TimedRotatingFileHandler(
                filename=log_filename,
                when='midnight',
                interval=1,
                backupCount=7,
                encoding='utf-8'
            )
            file_handler.suffix = "%Y-%m-%d_%H-%M-%S.log"
            file_handler.setFormatter(formatter)
            cls._instance.logger.addHandler(file_handler)
        return cls._instance

    def set_level(self, log_level: str) -> None:
        level = getattr(logging, (log_level or "INFO").upper(), logging.INFO)
        self._instance.logger.setLevel(level)
        for handler in self._instance.logger.handlers:
            handler.setLevel(level)

    def info(self, message):
        self._instance.logger.info(message)
    
    def warning(self, message):
        self._instance.logger.warning(message)
    
    def error(self, message):
        self._instance.logger.error(message)
    
    def debug(self, message):
        self._instance.logger.debug(message)
