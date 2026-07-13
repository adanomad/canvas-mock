# canvas-mock

A tiny, self-contained mock of a subset of the [Canvas LMS](https://www.instructure.com/canvas) REST API. Point your client at it instead of a real `https://<school>.instructure.com` while you build, test, or demo tools that read a course roster, assignments, and missing submissions. Swap in the real base URL and token when you go live.

> **Disclaimer.** This project is **not affiliated with, endorsed by, or connected to Instructure or Canvas**. It serves **fake data only** and contains **no real student information**. "Canvas" is a trademark of Instructure, Inc., used here only to describe the API shape this mock imitates. Use it for local development, automated testing, and demos.

## Why

Canvas discontinued its "Free for Teacher" program, so there's no easy way to spin up a throwaway Canvas instance to develop or demo an integration against. `canvas-mock` fills that gap with a stdlib-only HTTP server (no dependencies) that returns Canvas-shaped JSON for the handful of read endpoints most "who hasn't submitted?" / roster tools need.

## Endpoints

All live under `/api/v1` and, like real Canvas, require a token. Auth is accepted as either an `Authorization: Bearer <token>` header **or** an `?access_token=<token>` query param. Any non-empty token is accepted (it's a mock); a missing token returns `401`.

| Method & path | Returns |
|---|---|
| `GET /api/v1/courses/:id/enrollments` | Course roster (student enrollments + names) |
| `GET /api/v1/courses/:id/assignments` | Assignments (with `due_at`, `points_possible`) |
| `GET /api/v1/courses/:id/students/submissions` | Per-student submissions; supports `workflow_state=unsubmitted` and `grouped=true&include[]=user` |

Two unauthenticated helpers: `GET /` (a self-describing disclaimer + endpoint list) and `GET /healthz`.

### Fake dataset

Course id `101`, one assignment ("Essay 1"), three students. **Bob submitted; Alice and Carol did not** — so a missing-submissions query returns Alice + Carol and excludes Bob.

## Quick start

Docker (recommended):

```bash
docker compose up --build        # listens on 127.0.0.1:8913
# or
docker build -t canvas-mock . && docker run --rm -p 8913:8913 canvas-mock
```

No Docker (Python 3 stdlib only):

```bash
PORT=8913 python3 canvas_mock.py   # or: python3 canvas_mock.py 8913
```

Try it:

```bash
# roster
curl -H "Authorization: Bearer x" \
  "http://localhost:8913/api/v1/courses/101/enrollments"

# who hasn't submitted? -> Alice + Carol (Bob excluded)
curl "http://localhost:8913/api/v1/courses/101/students/submissions?student_ids[]=all&workflow_state=unsubmitted&grouped=true&include[]=user&per_page=100&access_token=x"
```

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `PORT` | `8913` | Listen port |
| `HOST` | `0.0.0.0` | Bind address |
| `CANVAS_MOCK_COURSE_ID` | `101` | The course id the mock answers for |

## Public deployment

The server binds `0.0.0.0` inside the container but the compose file publishes only on loopback. To expose it, put a reverse proxy in front. Example nginx location that maps `https://example.com/canvas-mock/` to the container:

```nginx
location /canvas-mock/ {
    proxy_pass http://127.0.0.1:8913/;
    proxy_set_header Host $host;
}
```

Then your client's base URL is `https://example.com/canvas-mock`.

## Scope & limitations

This mock intentionally implements only the read endpoints above. It does **not** cover pagination cursors, write endpoints, OAuth flows, or Canvas Roll Call attendance (which is a separate Instructure service with no public REST API). Contributions that add more read endpoints in the same fake-data spirit are welcome.

## License

MIT — see [LICENSE](LICENSE).
