import functools
import time

from loguru import logger

from src.module.settings import settings


def elapsed_time(total_seconds):
    """prints total elapsed time
    Args:
        total_seconds (float): total elapsed seconds
    Returns:
        total_elapsed_time (str): string of hours minutes seconds
    """
    days = int(total_seconds // 86400)
    hours = int(total_seconds // 3600 - total_seconds // 86400 * 24)
    minutes = int(total_seconds // 60 - total_seconds // 3600 * 60)
    seconds = int(total_seconds - total_seconds // 60 * 60)
    ms = int((total_seconds - total_seconds // 60 * 60) % 1 * 1000)
    elapsed_days = f'{days} d ' if days >= 1 else ''
    elapsed_hours = f'{hours} hr ' if hours >= 1 else ''
    elapsed_minutes = f'{minutes} min ' if minutes >= 1 else ''
    elapsed_seconds = f'{seconds} sec ' if seconds >= 1 else ''
    elapsed_ms = f'{ms} ms' if minutes == 0 else ''
    return elapsed_days + elapsed_hours + elapsed_minutes + elapsed_seconds + elapsed_ms


def timeit(func):
    """timer decorator. print time of method after execution"""

    def wrapped(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        elapsed_dur = elapsed_time(time.time() - start_time)
        logger.debug(f"Function '{func.__name__}' executed in {elapsed_dur}")
        return result

    return wrapped


def logging_timer(*, entry=False, exit=False, level=settings.log_level):
    """Decorator for logging and timing of method"""

    def wrapper(func):
        name = func.__name__

        @functools.wraps(func)
        def wrapped(*args, **kwargs):
            logger_ = logger.opt(depth=1)
            if entry:
                logger_.log(level, f"Entering '{name}' -----> ")

            start_time = time.time()
            result = func(*args, **kwargs)
            elapsed_dur = elapsed_time(time.time() - start_time)
            logger_.log(level, f'Function {func.__name__} executed in: {elapsed_dur}')

            if exit:
                logger_.log(level, f"Exiting '{name}' <-----")
            return result

        return wrapped

    return wrapper
