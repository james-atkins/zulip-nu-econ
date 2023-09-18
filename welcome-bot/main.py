#!/usr/bin/env python3

import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import zulip

import jinja2
import requests
from bs4 import BeautifulSoup


@dataclass
class GradStudent:
    name: str
    year: int
    email: str
    fields: List[str]

    def is_kellogg(self) -> bool:
        return self.email.endswith("@kellogg.northwestern.edu")


FIRST_YEAR_COURSES_STREAMS = ["course/ECON 410-1", "course/ECON 411-1", "course/ECON 480-1"]

STREAM_EMOJIS = {
    "course/ECON 410-1": "eddie",
    "course/ECON 411-1": "larry",
    "course/ECON 480-1": "joel",
}

FIELD_STREAMS = {
    "Applied Microeconomics": "appliedmicro",
    "Development": "development",
    "Econometrics": "metrics",
    "Economic History": "history",
    "Environmental": "environmental",
    "Finance": "finance",
    "Health": "health",
    "Industrial Organization": "io",
    "Labor": "labor",
    "Macroeconomics": "macro",
    "Microeconomic Theory": "microtheory",
    "Political Economy": "political",
    "Economics of Organizations": "organizational",
    "Public Economics": "public",
}


class ZulipError(Exception):
    pass



def scrape_grad_students() -> List[GradStudent]:
    resp = requests.get("https://economics.northwestern.edu/people/graduate/index.html", timeout=5)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, features="lxml")
    students_soup = soup.select("#main-content article.people")

    return [_extract_grad_student(student_soup) for student_soup in students_soup]


def _extract_grad_student(soup: BeautifulSoup) -> GradStudent:
    _a_id, h3, p_year, p_info = soup.select_one(".people-content").children
    
    name = h3.text.strip().removesuffix("(Financial Economics Student)").strip()
    year_text = p_year.text
    
    if not year_text.startswith("Year"):
        raise ValueError(f"Invalid year {year_text}")
    
    year = int(year_text.removeprefix("Year").strip())
    
    fields = []
    for string in p_info.stripped_strings:
        if string.startswith("Research Field:"):
            fields = [field.strip() for field in re.split(r",|and", string.removeprefix("Research Field:"))]
        
        elif string.strip().endswith("@u.northwestern.edu") or string.strip().endswith("@kellogg.northwestern.edu"):
            email = string.strip().lower()
    
    return GradStudent(name=name, year=year, email=email, fields=fields)

def _website_field_to_stream(field):
    if field in FIELD_STREAMS:
        return "field/" + FIELD_STREAMS[field]
    
    for potential_field in FIELD_STREAMS.keys():
        if field.startswith(potential_field):
            return "field/" + FIELD_STREAMS[potential_field]
    
    return None

def _website_fields_to_streams(fields):
    streams = []
    for field in fields:
        stream = _website_field_to_stream(field)
        if stream:
            streams.append(stream)

    return streams


def welcome_new_user(client, template: jinja2.Template, students: List[GradStudent], user_id: int, name: str, email: str):
    resp = client.get_streams()
    if resp["result"] != "success":
        raise ZulipError(f"cannot get streams: {resp['msg']}")

    all_streams = [stream["name"] for stream in resp["streams"]]

    course_streams = [ stream for stream in all_streams if stream.startswith("course/")]
    field_streams = [ stream for stream in all_streams if stream.startswith("field/")]

    # Streams that the user will be automatically added to
    auto_streams = []

    # Try and find user on the department website
    student = _find_grad_student(students, name, email)
    if student:
        if student.year == 1:
            auto_streams.extend(FIRST_YEAR_COURSES_STREAMS)
        else:
            auto_streams.extend(_website_fields_to_streams(student.fields))

    # Try and register the user to their field and course streams
    resp = client.add_subscriptions(
        streams=[{"name": stream} for stream in auto_streams],
        principals=[user_id],
    )

    if resp["result"] != "success":
        raise ZulipError(f"cannot register user to streams: {resp['msg']}")

    welcome = template.render(
        name=name, 
        course_streams=course_streams,
        field_streams=field_streams,
        auto_streams=auto_streams,
        auto_field_streams=[stream for stream in auto_streams if stream.startswith("field/")],
        auto_course_streams=[stream for stream in auto_streams if stream.startswith("course/")],
        student=student,
    )

    resp = client.send_message({"type": "direct", "to": [user_id], "content": welcome})
    if resp["result"] != "success":
        raise ZulipError(f"cannot send user message: {resp['msg']}")


def _stream_filter(value):
    emoji = STREAM_EMOJIS.get(value)
    if emoji:
        return f":{emoji}: #**{value}**"

    return f"#**{value}**"


def _find_grad_student(students: List[GradStudent], name: str, email: str) -> Optional[GradStudent]:
    # Try first with the NU email
    for student in students:
        if student.email == email.lower():
            return student
    
    # A common pattern is removing the year suffix from the email address
    # Given an email address with the year suffix, try and generate this
    m = re.match(r"^([a-zA-Z]+)\d{4}@([a-z\.]+)$", email.lower())
    if m:
        first_part, domain = m.groups()
        new_email = f"{first_part}@{domain}"
        
        for student in students:
            if student.email == new_email:
                return student
    
    # Else try to match by name
    for student in students:
        if student.name.lower() == name.lower():
            return student
    
    return None


def send_missing_welcome_messages(client: zulip.Client, template: jinja2.Template, students: List[GradStudent]):
    resp = client.get_members()
    if resp["result"] != "success":
        raise ZulipError(f"cannot get members: {resp['msg']}")

    for member in resp["members"]:
        if member["is_bot"] or not member["is_active"]:
            continue

        user_id = member["user_id"]
        narrow = [{"operator": "dm", "operand": [user_id]}]

        request = {"anchor": "newest", "num_after": 0, "num_before": 100, "narrow": narrow}
        resp = client.get_messages(request)
        if resp["result"] != "success":
            raise ZulipError(f"cannot get messages: {resp['msg']}")

        if not resp["messages"]:
            user_id = member["user_id"]
            name = member["full_name"]
            nu_email = member["delivery_email"]  # the actual email address used to register
            try:
                welcome_new_user(client, template, students, user_id, name, nu_email)
                print(f"Sent belated welcome message to {nu_email}")
            except Exception as e:
                print(e, file=sys.stderr)


if __name__ == "__main__":
    students = scrape_grad_students()
    config_file = os.getenv("ZULIPRC")
    if config_file is None:
        print("error: could not find configuration file", file=sys.stderr)
        sys.exit(1)

    client = zulip.Client(config_file=config_file)

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(os.path.dirname(os.path.realpath(__file__))),
        autoescape=True,
    )
    env.filters["format_stream"] = _stream_filter

    template = env.get_template("welcome.md.jinja2")

    def handle_event(event: Dict[str, Any]) -> None:
        if event["type"] == "realm_user" and event["op"] == "add":
            person = event["person"]
        
            if person["is_bot"]:
                return
        
            user_id = person["user_id"]
            name = person["full_name"]
            nu_email = person["delivery_email"]  # the actual email address used to register

            try:
                welcome_new_user(client, template, students, user_id, name, nu_email)
                print(f"Registered {nu_email}")
            except Exception as e:
                print(e, file=sys.stderr)

    send_missing_welcome_messages(client, template, students)
    client.call_on_each_event(handle_event, event_types=["realm_user"])
    