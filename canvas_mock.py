#!/usr/bin/env python3
"""canvas-mock: a tiny mock of a subset of the Canvas LMS REST API.

DISCLAIMER
----------
This is NOT affiliated with, endorsed by, or connected to Instructure or
Canvas. It serves FAKE data only (no real student information) and implements
just enough of the Canvas REST surface to develop and demo tools that read a
course roster, assignments, and missing submissions. Use it for local
development, automated testing, and demos where a real Canvas instance is
unavailable.

Why this exists
---------------
Canvas discontinued its "Free for Teacher" sandbox, so there is no easy way to
get a throwaway Canvas instance to build/test integrations against. This mock
fills that gap: point your client at it instead of `https://<school>.instructure.com`
and swap in the real base URL + token when you go live.

Implemented endpoints (all under /api/v1, all require a token)
--------------------------------------------------------------
  GET /api/v1/courses/:id/enrollments
  GET /api/v1/courses/:id/assignments
  GET /api/v1/courses/:id/students/submissions
        ?student_ids[]=all&workflow_state=unsubmitted&grouped=true&include[]=user

Auth mirrors Canvas: any /api/v1/* call returns 401 unless a token is present as
either `Authorization: Bearer <token>` or `?access_token=<token>`. Any non-empty
token is accepted (it is a mock).

Run
---
  PORT=8913 python3 canvas_mock.py         # or: python3 canvas_mock.py 8913
  curl -H "Authorization: Bearer x" \
    "http://localhost:8913/api/v1/courses/101/students/submissions?student_ids[]=all&workflow_state=unsubmitted&grouped=true&include[]=user&per_page=100"
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

COURSE_ID = os.environ.get("CANVAS_MOCK_COURSE_ID", "101")
REPO_URL = "https://github.com/adanomad/canvas-mock"

DISCLAIMER = (
    "Mock Canvas API for demos/testing. NOT affiliated with Instructure or "
    "Canvas. Returns FAKE data only; contains no real student information."
)

# --- Fake demo data (NO real student data) ---
STUDENTS = [
    {"id": 5001, "name": "Alice Active", "sortable_name": "Active, Alice", "state": "active"},
    {"id": 5002, "name": "Bob Submitted", "sortable_name": "Submitted, Bob", "state": "active"},
    {"id": 5003, "name": "Carol Missing", "sortable_name": "Missing, Carol", "state": "active"},
]
ASSIGNMENTS = [
    {"id": 9001, "name": "Essay 1", "due_at": "2026-07-11T23:59:00Z", "points_possible": 100},
]
# Ground truth: who submitted what. Bob submitted; Alice + Carol did not.
SUBMITTED = {(5002, 9001)}


def _submission_rows(workflow_filter):
    """Synthesize a submission row per (student, assignment), like Canvas does
    for active enrollments even when the student never submitted."""
    rows = []
    for s in STUDENTS:
        for a in ASSIGNMENTS:
            submitted = (s["id"], a["id"]) in SUBMITTED
            state = "submitted" if submitted else "unsubmitted"
            if workflow_filter and state != workflow_filter:
                continue
            rows.append({
                "id": 70000 + s["id"] * 10 + a["id"] % 10,
                "user_id": s["id"],
                "assignment_id": a["id"],
                "workflow_state": state,
                "submitted_at": "2026-07-10T18:22:00Z" if submitted else None,
                "score": None,
                "missing": not submitted,
                "user": {"id": s["id"], "name": s["name"]},
            })
    return rows


class Handler(BaseHTTPRequestHandler):
    server_version = "canvas-mock/1.0"

    def log_message(self, fmt, *args):
        # One-line access log to stdout so `docker logs` shows traffic.
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()

    def _send(self, code, payload, link=None):
        body = json.dumps(payload, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Canvas-Mock", "true")
        if link:
            self.send_header("Link", link)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self, qs):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and len(auth) > len("Bearer "):
            return True
        if qs.get("access_token", [""])[0]:
            return True
        return False

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query, keep_blank_values=True)
        parts = [p for p in u.path.split("/") if p]

        # Root + health: unauthenticated, self-describing (the disclaimer).
        if not parts:
            self._send(200, {
                "service": "canvas-mock",
                "version": "1.0",
                "disclaimer": DISCLAIMER,
                "repo": REPO_URL,
                "course_id": COURSE_ID,
                "auth": "All /api/v1/* endpoints require a token: "
                        "'Authorization: Bearer <token>' or '?access_token=<token>' (any value).",
                "endpoints": [
                    "/api/v1/courses/%s/enrollments" % COURSE_ID,
                    "/api/v1/courses/%s/assignments" % COURSE_ID,
                    "/api/v1/courses/%s/students/submissions"
                    "?student_ids[]=all&workflow_state=unsubmitted&grouped=true&include[]=user" % COURSE_ID,
                ],
            })
            return
        if parts == ["healthz"]:
            self._send(200, {"status": "ok"})
            return

        # Canvas returns 401 for any unauthenticated API call.
        if parts[:2] == ["api", "v1"] and not self._authed(qs):
            self._send(401, {"errors": [{"message": "user authorization required"}]})
            return

        # /api/v1/courses/:id/<resource>
        if len(parts) >= 5 and parts[:3] == ["api", "v1", "courses"]:
            cid, resource = parts[3], parts[4]
            if cid != COURSE_ID:
                self._send(404, {"errors": [{"message": "The specified resource does not exist."}]})
                return

            if resource == "enrollments":
                out = [{
                    "id": 60000 + s["id"],
                    "user_id": s["id"],
                    "type": "StudentEnrollment",
                    "enrollment_state": s["state"],
                    "user": {"id": s["id"], "name": s["name"], "sortable_name": s["sortable_name"]},
                } for s in STUDENTS]
                self._send(200, out)
                return

            if resource == "assignments":
                self._send(200, ASSIGNMENTS)
                return

            if resource == "students" and len(parts) >= 6 and parts[5] == "submissions":
                wf = qs.get("workflow_state", [None])[0]
                grouped = qs.get("grouped", ["false"])[0].lower() in ("true", "1")
                rows = _submission_rows(wf)
                if grouped:
                    by_user = {}
                    for r in rows:
                        by_user.setdefault(r["user_id"], {"user_id": r["user_id"],
                                                           "user": r["user"],
                                                           "submissions": []})
                        by_user[r["user_id"]]["submissions"].append(r)
                    self._send(200, list(by_user.values()))
                else:
                    self._send(200, rows)
                return

        self._send(404, {"errors": [{"message": "The specified resource does not exist."}]})


def main():
    port = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8913))
    host = os.environ.get("HOST", "0.0.0.0")
    print("canvas-mock listening on %s:%d (course_id=%s)" % (host, port, COURSE_ID), flush=True)
    HTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
