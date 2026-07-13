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

Canvas-shaped read endpoints (all under /api/v1, all require a token)
---------------------------------------------------------------------
  GET /api/v1/courses/:id/enrollments
  GET /api/v1/courses/:id/assignments
  GET /api/v1/courses/:id/students/submissions
        ?student_ids[]=all&workflow_state=unsubmitted&grouped=true&include[]=user

Auth mirrors Canvas: any /api/v1/* call returns 401 unless a token is present as
either `Authorization: Bearer <token>` or `?access_token=<token>`. Any non-empty
token is accepted (it is a mock).

Live admin UI (mutate the fake data during a demo)
--------------------------------------------------
  GET  /admin?token=<ADMIN_TOKEN>          -> HTML control panel
  GET  /admin/state?token=...              -> current state as JSON
  POST /admin/students?token=...           {"name": "..."}            add a student
  POST /admin/students/delete?token=...    {"id": 5001}               remove a student
  POST /admin/submission?token=...         {"student_id":.., "assignment_id":.., "submitted":bool}
  POST /admin/assignment?token=...         {"id":?, "name":?, "due_at":?}   add/edit an assignment
  POST /admin/reset?token=...                                          reset to defaults
Admin routes require ?token=<ADMIN_TOKEN> (env CANVAS_MOCK_ADMIN_TOKEN, default "changeme").

State persists to CANVAS_MOCK_STATE_FILE (default /data/state.json) when writable,
so edits survive a container restart; otherwise it is kept in memory.

Run
---
  PORT=8913 python3 canvas_mock.py         # or: python3 canvas_mock.py 8913
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

COURSE_ID = os.environ.get("CANVAS_MOCK_COURSE_ID", "101")
ADMIN_TOKEN = os.environ.get("CANVAS_MOCK_ADMIN_TOKEN", "changeme")
STATE_FILE = os.environ.get("CANVAS_MOCK_STATE_FILE", "/data/state.json")
REPO_URL = "https://github.com/adanomad/canvas-mock"
DISCLAIMER = (
    "Mock Canvas API for demos/testing. NOT affiliated with Instructure or "
    "Canvas. Returns FAKE data only; contains no real student information."
)

_LOCK = threading.Lock()


def _defaults():
    return {
        "students": [
            {"id": 5001, "name": "Alice Active", "state": "active"},
            {"id": 5002, "name": "Bob Submitted", "state": "active"},
            {"id": 5003, "name": "Carol Missing", "state": "active"},
        ],
        "assignments": [
            {"id": 9001, "name": "Essay 1", "due_at": "2026-07-11T23:59:00Z", "points_possible": 100},
        ],
        # list of [student_id, assignment_id] pairs that ARE submitted
        "submitted": [[5002, 9001]],
        # DEMO-ONLY attendance. Real Canvas has no attendance REST API (Roll Call
        # is a separate Instructure service), so this endpoint is a mock convenience.
        "session_date": "2026-07-13",
        "attendance": {  # keyed by str(student_id): {"status": present|absent|late, "absences": int}
            "5001": {"status": "present", "absences": 0},
            "5002": {"status": "present", "absences": 1},
            "5003": {"status": "absent", "absences": 3},
        },
    }


def _load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        s["submitted"] = [list(p) for p in s.get("submitted", [])]
        # Backfill any keys added in newer versions (e.g. attendance) so an
        # older persisted state file still gets sensible defaults.
        for k, v in _defaults().items():
            s.setdefault(k, v)
        return s
    except Exception:
        return _defaults()


STATE = _load_state()


def _save_state():
    try:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(STATE, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        sys.stderr.write("state save skipped (%s): %s\n" % (STATE_FILE, e))


def _is_submitted(sid, aid):
    return [sid, aid] in STATE["submitted"]


def _sortable(name):
    parts = name.split()
    return (parts[-1] + ", " + " ".join(parts[:-1])) if len(parts) > 1 else name


def _next_id(items, base):
    ids = [i["id"] for i in items] or [base]
    return max(ids) + 1


def _submission_rows(workflow_filter):
    rows = []
    for s in STATE["students"]:
        for a in STATE["assignments"]:
            submitted = _is_submitted(s["id"], a["id"])
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
    server_version = "canvas-mock/2.0"

    def log_message(self, fmt, *args):
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()

    # ---- helpers ----
    def _send(self, code, payload, ctype="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Canvas-Mock", "true")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self, qs):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and len(auth) > len("Bearer "):
            return True
        return bool(qs.get("access_token", [""])[0])

    def _admin_ok(self, qs):
        return qs.get("token", [""])[0] == ADMIN_TOKEN

    def _body_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    # ---- GET ----
    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query, keep_blank_values=True)
        parts = [p for p in u.path.split("/") if p]

        if not parts:
            self._send(200, {
                "service": "canvas-mock", "version": "2.0", "disclaimer": DISCLAIMER,
                "repo": REPO_URL, "course_id": COURSE_ID,
                "admin_ui": "/admin?token=<ADMIN_TOKEN>",
                "auth": "All /api/v1/* endpoints require a token: "
                        "'Authorization: Bearer <token>' or '?access_token=<token>'.",
                "endpoints": [
                    "/api/v1/courses/%s/enrollments" % COURSE_ID,
                    "/api/v1/courses/%s/assignments" % COURSE_ID,
                    "/api/v1/courses/%s/students/submissions" % COURSE_ID,
                ],
            })
            return
        if parts == ["healthz"]:
            self._send(200, {"status": "ok"})
            return

        # ---- admin ----
        if parts[:1] == ["admin"]:
            if parts == ["admin"]:
                self._send(200, ADMIN_HTML.encode(), ctype="text/html; charset=utf-8")
                return
            if parts == ["admin", "state"]:
                if not self._admin_ok(qs):
                    self._send(401, {"error": "bad admin token"}); return
                self._send(200, {"course_id": COURSE_ID, **STATE}); return
            self._send(404, {"error": "unknown admin route"}); return

        # ---- Canvas API ----
        if parts[:2] == ["api", "v1"] and not self._authed(qs):
            self._send(401, {"errors": [{"message": "user authorization required"}]}); return

        if len(parts) >= 5 and parts[:3] == ["api", "v1", "courses"]:
            cid, resource = parts[3], parts[4]
            if cid != COURSE_ID:
                self._send(404, {"errors": [{"message": "The specified resource does not exist."}]}); return
            if resource == "enrollments":
                self._send(200, [{
                    "id": 60000 + s["id"], "user_id": s["id"], "type": "StudentEnrollment",
                    "enrollment_state": s["state"],
                    "user": {"id": s["id"], "name": s["name"], "sortable_name": _sortable(s["name"])},
                } for s in STATE["students"]]); return
            if resource == "assignments":
                self._send(200, STATE["assignments"]); return
            if resource == "attendance":
                att = STATE.get("attendance", {})
                self._send(200, {
                    "session_date": STATE.get("session_date"),
                    "records": [{
                        "user_id": s["id"], "name": s["name"],
                        "status": att.get(str(s["id"]), {}).get("status", "present"),
                        "absences": att.get(str(s["id"]), {}).get("absences", 0),
                    } for s in STATE["students"]],
                }); return
            if resource == "students" and len(parts) >= 6 and parts[5] == "submissions":
                wf = qs.get("workflow_state", [None])[0]
                grouped = qs.get("grouped", ["false"])[0].lower() in ("true", "1")
                rows = _submission_rows(wf)
                if grouped:
                    by_user = {}
                    for r in rows:
                        by_user.setdefault(r["user_id"], {"user_id": r["user_id"], "user": r["user"], "submissions": []})
                        by_user[r["user_id"]]["submissions"].append(r)
                    self._send(200, list(by_user.values()))
                else:
                    self._send(200, rows)
                return

        self._send(404, {"errors": [{"message": "The specified resource does not exist."}]})

    # ---- POST (admin mutations) ----
    def do_POST(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query, keep_blank_values=True)
        parts = [p for p in u.path.split("/") if p]
        if parts[:1] != ["admin"]:
            self._send(404, {"error": "not found"}); return
        if not self._admin_ok(qs):
            self._send(401, {"error": "bad admin token"}); return
        body = self._body_json()

        with _LOCK:
            route = "/".join(parts)
            if route == "admin/students":
                name = (body.get("name") or "").strip()
                if not name:
                    self._send(400, {"error": "name required"}); return
                sid = _next_id(STATE["students"], 5000)
                STATE["students"].append({"id": sid, "name": name, "state": "active"})
                STATE.setdefault("attendance", {})[str(sid)] = {"status": "present", "absences": 0}
            elif route == "admin/students/delete":
                sid = int(body.get("id"))
                STATE["students"] = [s for s in STATE["students"] if s["id"] != sid]
                STATE["submitted"] = [p for p in STATE["submitted"] if p[0] != sid]
                STATE.get("attendance", {}).pop(str(sid), None)
            elif route == "admin/attendance":
                sid = str(int(body["student_id"]))
                rec = STATE.setdefault("attendance", {}).setdefault(sid, {"status": "present", "absences": 0})
                if body.get("status") in ("present", "absent", "late"):
                    rec["status"] = body["status"]
                if "absences" in body:
                    rec["absences"] = max(0, int(body["absences"]))
            elif route == "admin/submission":
                sid, aid = int(body["student_id"]), int(body["assignment_id"])
                submitted = bool(body.get("submitted"))
                pair = [sid, aid]
                if submitted and pair not in STATE["submitted"]:
                    STATE["submitted"].append(pair)
                elif not submitted and pair in STATE["submitted"]:
                    STATE["submitted"].remove(pair)
            elif route == "admin/assignment":
                aid = body.get("id")
                if aid:  # edit
                    for a in STATE["assignments"]:
                        if a["id"] == int(aid):
                            if body.get("name"):
                                a["name"] = body["name"]
                            if body.get("due_at"):
                                a["due_at"] = body["due_at"]
                else:  # add
                    aid = _next_id(STATE["assignments"], 9000)
                    STATE["assignments"].append({
                        "id": aid, "name": body.get("name") or "New Assignment",
                        "due_at": body.get("due_at") or "2026-08-01T23:59:00Z",
                        "points_possible": 100,
                    })
            elif route == "admin/reset":
                STATE.clear(); STATE.update(_defaults())
            else:
                self._send(404, {"error": "unknown admin route"}); return
            _save_state()
        self._send(200, {"ok": True, "course_id": COURSE_ID, **STATE})


ADMIN_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>canvas-mock admin</title>
<style>
 body{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#1a1a1a}
 h1{font-size:20px} h2{font-size:15px;margin:22px 0 8px;color:#444}
 .note{background:#fff8e1;border:1px solid #ffe08a;padding:8px 12px;border-radius:8px;font-size:13px;color:#664d00}
 table{border-collapse:collapse;width:100%} td,th{border-bottom:1px solid #eee;padding:8px;text-align:left}
 button{cursor:pointer;border:1px solid #ccc;background:#fff;border-radius:7px;padding:5px 10px}
 button.on{background:#0d9488;color:#fff;border-color:#0d9488} button.off{background:#fee2e2;border-color:#fca5a5}
 button.danger{color:#b91c1c}
 input{padding:6px 8px;border:1px solid #ccc;border-radius:7px} .row{display:flex;gap:8px;align-items:center;margin:8px 0;flex-wrap:wrap}
 .miss{color:#b91c1c;font-weight:600} .sub{color:#0d9488;font-weight:600} small{color:#888}
</style></head><body>
<h1>canvas-mock &mdash; live control panel</h1>
<div class="note">Fake data for demos. Changes take effect immediately in the API (and any agent reading it). Not affiliated with Instructure/Canvas.</div>
<div id="app">loading&hellip;</div>
<script>
const qs=new URLSearchParams(location.search); const token=qs.get("token")||"";
// Works whether served at / or behind a reverse-proxy prefix like /canvas-mock/admin.
const BASE=location.pathname.replace(/\/+admin\/?$/,"");
const api=(path,body)=>fetch(BASE+path+"?token="+encodeURIComponent(token),body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{}).then(r=>r.json());
let S=null;
async function load(){ S=await api("/admin/state"); render(); }
function render(){
 if(S.error){document.getElementById("app").innerHTML="<p class='miss'>"+S.error+" &mdash; append ?token=YOUR_TOKEN to the URL.</p>";return;}
 const a=S.assignments[0];
 let h="<h2>Assignment</h2><div class='row'><input id='an' value=\""+a.name.replace(/"/g,'&quot;')+"\" style='width:220px'>"+
   "<input id='ad' value=\""+a.due_at+"\" style='width:210px'><button onclick='saveA("+a.id+")'>Save</button> <small>id "+a.id+"</small></div>";
 h+="<h2>Students &amp; submission of \""+a.name+"\"</h2><table><tr><th>Student</th><th>Status</th><th></th></tr>";
 for(const s of S.students){ const sub=(S.submitted||[]).some(p=>p[0]===s.id&&p[1]===a.id);
  h+="<tr><td>"+s.name+" <small>#"+s.id+"</small></td>"+
     "<td class='"+(sub?'sub':'miss')+"'>"+(sub?"submitted":"MISSING")+"</td>"+
     "<td><button class='"+(sub?'off':'on')+"' onclick='toggle("+s.id+","+a.id+","+(!sub)+")'>mark "+(sub?"missing":"submitted")+"</button> "+
     "<button class='danger' onclick='del("+s.id+")'>remove</button></td></tr>"; }
 h+="</table>";
 // Attendance (demo-only; real Canvas has no attendance API)
 const att=S.attendance||{};
 h+="<h2>Attendance &mdash; session "+(S.session_date||"")+"</h2><table><tr><th>Student</th><th>Today</th><th>Total absences</th></tr>";
 for(const s of S.students){ const r=att[String(s.id)]||{status:'present',absences:0};
  const cls=r.status==='present'?'sub':(r.status==='late'?'':'miss');
  h+="<tr><td>"+s.name+"</td>"+
     "<td><button class='"+(r.status==='present'?'on':'off')+"' onclick=\"setAtt("+s.id+",'present')\">present</button> "+
     "<button class='"+(r.status==='absent'?'off':'')+"' onclick=\"setAtt("+s.id+",'absent')\">absent</button> "+
     "<button class='"+(r.status==='late'?'on':'')+"' onclick=\"setAtt("+s.id+",'late')\">late</button> "+
     "<span class='"+cls+"'>&nbsp;"+r.status+"</span></td>"+
     "<td><button onclick='setAbs("+s.id+","+Math.max(0,r.absences-1)+")'>&minus;</button> <b>"+r.absences+"</b> "+
     "<button onclick='setAbs("+s.id+","+(r.absences+1)+")'>+</button></td></tr>"; }
 h+="</table>";
 h+="<div class='row'><input id='nn' placeholder='New student name' style='width:220px'><button onclick='addS()'>Add student</button>"+
    "<button onclick='reset()' style='margin-left:auto'>Reset demo</button></div>";
 document.getElementById("app").innerHTML=h;
}
async function setAtt(id,status){ S=await api("/admin/attendance",{student_id:id,status}); render(); }
async function setAbs(id,absences){ S=await api("/admin/attendance",{student_id:id,absences}); render(); }
async function toggle(sid,aid,v){ S=await api("/admin/submission",{student_id:sid,assignment_id:aid,submitted:v}); render(); }
async function del(id){ S=await api("/admin/students/delete",{id}); render(); }
async function addS(){ const n=document.getElementById("nn").value.trim(); if(!n)return; S=await api("/admin/students",{name:n}); render(); }
async function saveA(id){ S=await api("/admin/assignment",{id,name:document.getElementById("an").value,due_at:document.getElementById("ad").value}); render(); }
async function reset(){ S=await api("/admin/reset"); render(); }
load();
</script></body></html>"""


def main():
    port = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8913))
    host = os.environ.get("HOST", "0.0.0.0")
    print("canvas-mock v2 on %s:%d (course_id=%s, state=%s)" % (host, port, COURSE_ID, STATE_FILE), flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
