"""Time library: the current date and time."""

import datetime
import time

from . import Library


class TimeLibrary(Library):
    name = "Time"
    summary = "The current date and time."
    commands = (
        ("{Time Now}", "Current time as HH:MM:SS"),
        ("{Time Date}", "Current date as YYYY-MM-DD"),
        ("{Time Hour}", "Current hour (also Minute, Second, Day, Month, Year)"),
        ("{Time Weekday}", "Name of the day, e.g. Monday"),
        ("{Time Stamp}", "Seconds since 1970 (useful for measuring time)"),
        ('{Time Format "%d.%m.%Y %H:%M"}', "Custom format"),
    )

    def query(self, args):
        verb = args.take_word()
        now = datetime.datetime.now()
        simple = {
            "now": lambda: now.strftime("%H:%M:%S"),
            "date": lambda: now.strftime("%Y-%m-%d"),
            "hour": lambda: now.hour,
            "minute": lambda: now.minute,
            "second": lambda: now.second,
            "day": lambda: now.day,
            "month": lambda: now.month,
            "year": lambda: now.year,
            "weekday": lambda: now.strftime("%A"),
            "stamp": lambda: round(time.time(), 3),
        }
        if verb in simple:
            return simple[verb]()
        if verb == "format":
            return now.strftime(str(args.take_value("%Y-%m-%d %H:%M:%S")))
        self.unknown(args)
