#!/usr/bin/env python3

import argparse
import html
import os
import sys

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, NamedTuple, Optional

import jinja2
import pytz
import markdownify
import requests
import zulip


TIMEOUT = 5

PLAN_IT_PURPLE_FEED_ID = 2103

STREAM_CALENDAR_IDS: dict[str, list[int]] = {
    "general": [3178, 3561, 3559],
    "field/appliedmicro": [4355],
    "field/development": [4247, 3557],
    "field/health": [4559],
    "field/history": [4389, 3556],
    "field/io": [4483, 3555],
    "field/labor": [4559],
    "field/macro": [3558, 3554],
    "field/metrics": [3553],
    "field/public": [4559],
}

TOPIC = "events"

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(os.path.realpath(__file__))),
    autoescape=True,
)

template_daily = _env.get_template("daily.md.jinja2")
template_weekly = _env.get_template("weekly.md.jinja2")


class ZulipError(Exception):
    pass


class PlanItPurpleEvent(NamedTuple):
    occurrence_id: int
    calendar_id: int
    title: str
    description: Optional[str]
    start: datetime
    end: datetime
    is_all_day: bool
    is_cancelled: bool

    @property
    def url(self):
        return f"https://planitpurple.northwestern.edu/event/{self.occurrence_id}"


def get_pip_events(session: requests.Session):
    resp = session.get(f"https://planitpurple.northwestern.edu/feed/json/{PLAN_IT_PURPLE_FEED_ID}", timeout=TIMEOUT)
    resp.raise_for_status()

    raw_events = resp.json()
    return list(_extract_pip_events(raw_events))


def _extract_pip_events(data: List[Dict[str, Any]]) -> Iterator[PlanItPurpleEvent]:
    # https://www.northwestern.edu/web-resources/developer-resources/planitpurple-feeds/json-feeds.html
    # Events are apparently in Central Standard Time, by which I think they mean Central Time
    tz = pytz.timezone("America/Chicago")

    for raw_event in data:
        occurrence_id = int(raw_event["id"])
        calendar_id = int(raw_event["cal_id"])
        raw_title = raw_event["title"].strip()
        description_html = raw_event["description_html"]
        raw_date = raw_event["eventdate"] # ISO 8601 event date. Each occurrence of an event is limited to one day
        raw_start_time = raw_event["start_time"] # ISO 8601 event start time in Central Standard Time
        raw_end_time = raw_event["end_time"] # ISO 8601 event end time in Central Standard Time
        is_all_day = bool(int(raw_event["is_allday"]))
        is_cancelled = bool(int(raw_event["is_cancelled"]))

        title = html.unescape(raw_title)

        if description_html:
            description = markdownify.markdownify(description_html)
        else:
            description = None

        event_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        start_time = datetime.strptime(raw_start_time, "%H:%M:%S").time()
        end_time = datetime.strptime(raw_end_time, "%H:%M:%S").time()

        start = tz.localize(datetime.combine(event_date, start_time))
        end = tz.localize(datetime.combine(event_date, end_time))

        yield PlanItPurpleEvent(
            occurrence_id=occurrence_id,
            calendar_id=calendar_id,
            title=title,
            description=description,
            start=start,
            end=end,
            is_all_day=is_all_day,
            is_cancelled=is_cancelled,
        )


def _events_per_field(events: List[PlanItPurpleEvent]) -> dict[str, list[PlanItPurpleEvent]]:
    stream_events = defaultdict(list)

    for stream, calendar_ids in STREAM_CALENDAR_IDS.items():
        for event in events:
            if event.calendar_id in calendar_ids:
                stream_events[stream].append(event)

    return dict(stream_events)


def make_messages(all_events: List[PlanItPurpleEvent], period: str) -> Iterator[dict[str, Any]]:
    today = datetime.now().date()
    sunday = today + timedelta(days=(6-today.weekday()))

    if period == "daily":
        def filter_events(events: List[PlanItPurpleEvent]):
            return [event for event in events if event.start.date() == today]

        def render_message(events: List[PlanItPurpleEvent]):
            return template_daily.render(
                events=events,
            )

    elif period == "weekly":
        def filter_events(events: List[PlanItPurpleEvent]):
            return [event for event in events if today <= event.start.date() <= sunday]

        def render_message(events: List[PlanItPurpleEvent]):
            # Split events into days. They are automatically sorted by PlanItPurple
            events_by_date = defaultdict(list)
            for event in events:
                events_by_date[event.start.date()].append(event)

            return template_weekly.render(
                events_by_date=events_by_date,
            )
            
    else:
        raise AssertionError(f"invalid period {args.period}")

    events_per_stream = _events_per_field(all_events)
    for stream, stream_events in events_per_stream.items():
        events_this_period = filter_events(stream_events)

        if not events_this_period:
            continue

        yield {
            "type": "stream",
            "to": stream,
            "topic": TOPIC,
            "content": render_message(events_this_period),
        }


def print_message(request: dict[str, Any]):
    to = request["to"]
    topic = request["topic"]
    content = request["content"]

    print(f"To: {to}\nTopic: {topic}\n\n{content}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("period", choices=["daily", "weekly"])
    parser.add_argument("--dry-run", default=False, action='store_true')
    args = parser.parse_args()

    config_file = os.getenv("ZULIPRC")
    if config_file is None:
        print("error: could not find configuration file", file=sys.stderr)
        sys.exit(1)

    client = zulip.Client(config_file=config_file)

    with requests.Session() as session:
        events = get_pip_events(session)

    messages = list(make_messages(events, args.period))

    if args.dry_run:
        for message in messages:
            print_message(message)
    else:
        for message in messages:
            result = client.send_message(message)

            if result["result"] != "success":
                print(f"could not send message to {message['to']}: {result['msg']}", file=sys.stderr)
                sys.exit(1)

