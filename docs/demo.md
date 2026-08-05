# Demo: a broken app in a box

`demo/` is a FastAPI + SQLAlchemy application that is broken on purpose, in
two ways you can trigger over HTTP, packaged in an image that already has
pidprobe installed next to it. One image means one interpreter, so the
"target and prober must run the same CPython feature release" rule is
satisfied without you arranging anything.

Every command and every block of output on this page was produced by running
it. Numbers such as timings, addresses and object counts will differ on your
machine; the shapes will not.

## 1. Build the image

```bash
git clone https://github.com/tomada1114/pidprobe
cd pidprobe
docker build -t pidprobe-demo -f demo/Dockerfile .
```

The build context is the repository root because the image installs pidprobe
from this tree rather than from PyPI.

!!! note "Not on a registry yet"

    Once the image is published, `docker pull ghcr.io/tomada1114/pidprobe-demo`
    replaces this step and the rest of the page works unchanged with
    `ghcr.io/tomada1114/pidprobe-demo` in place of `pidprobe-demo`. Until
    then, build it locally.

## 2. Start it

```bash
docker run --rm -d --name pidprobe-demo -p 8000:8000 \
    --cap-add=SYS_PTRACE pidprobe-demo
```

```console
$ curl -s localhost:8000/widgets
{"count":20,"widgets":["widget-001","widget-002","widget-003","widget-004","widget-005"]}
```

uvicorn runs in the foreground of the container with no `--reload` and no
`--workers`, so it is PID 1 and every pidprobe command below says `1`.

!!! tip "About `--cap-add=SYS_PTRACE`"

    Attaching reads and writes another process' memory, which the kernel
    guards. On Docker Desktop the flag turns out to be unnecessary — the
    walkthrough was also verified without it — but on a Linux host with
    `kernel.yama.ptrace_scope=1` the daemon-started app is not an ancestor of
    your `docker exec` shell, and only `CAP_SYS_PTRACE` gets you past that.
    Step 3 tells you which case you are in.

## 3. Ask whether attaching would work at all

`doctor` never injects anything, so this is the safe first move:

```console
$ docker exec pidprobe-demo pidprobe doctor 1
pidprobe doctor: checking this environment against pid 1

  OK      prober_remote_debug   pidprobe runs cpython 3.14.6 with remote debugging enabled
  OK      return_channel        an AF_UNIX return channel binds at /tmp/pidprobe-wgc9774h/s.sock
  OK      collector_plugins     5 collectors will run: stacks, objects, gc, fds, sqlalchemy
  OK      ptrace_scope          no Yama ptrace_scope knob: this kernel does not restrict attaching
  SKIPPED task_for_pid          task_for_pid access is a macOS restriction
  OK      target_process        pid 1 is running and this user may signal it
  OK      target_owner          pid 1 and pidprobe both run as uid 0
  OK      pid_namespace         pidprobe and pid 1 share PID namespace pid:[4026532568]
  OK      target_python_version the target runs CPython 3.14.6
  OK      target_python_match   target and prober both run Python 3.14
  OK      target_remote_debug   PYTHON_DISABLE_REMOTE_DEBUG is not set in pid 1

nothing blocks attaching to pid 1
```

`ptrace_scope` is the line that changes on a Linux host: if it reports a
restrictive Yama setting, that is what `--cap-add=SYS_PTRACE` is for.
[Troubleshooting](troubleshooting.md) has a section per check.

## 4. Break it: the deadlock

Arming starts a background `ledger-writer` thread that takes one of two locks
and stops there. The call returns as soon as the thread is holding it, so the
next step deadlocks every time rather than most of the time:

```console
$ curl -s -X POST localhost:8000/deadlock
{"armed":true,"thread":"ledger-writer","detail":"GET /reports/{name} will now block forever"}
```

Now hang a request. This one never comes back — `--max-time 5` is only there
so you get your prompt back:

```console
$ curl -s --max-time 5 localhost:8000/reports/daily
$ echo $?
28
```

The process itself is fine, which is exactly what makes this kind of failure
annoying: it still answers, so a health check never notices.

```console
$ curl -s localhost:8000/health
{"status":"ok"}
```

## 5. Ask the running process what it is stuck on

No restart, no `gdb`, no code change in the app:

```console
$ docker exec pidprobe-demo pidprobe snap 1 \
    | jq -c '.stacks.threads[] | select(.frames[0].file | endswith("app.py"))
             | {thread: .name, stuck_at: "\(.frames[0].function):\(.frames[0].line)"}'
{"thread":"ledger-writer","stuck_at":"_ledger_writer:96"}
{"thread":"AnyIO worker thread","stuck_at":"read_report:180"}
```

Two threads, two functions, two line numbers — and `demo/app.py:96` wants the
lock that `demo/app.py:180` holds, and the other way round. That is the whole
diagnosis.

Each frame carries its locals, rendered inside the target under the same
bounds and credential masking pidprobe applies everywhere:

```console
$ docker exec pidprobe-demo pidprobe snap 1 \
    | jq '.stacks.threads[] | select(.name == "ledger-writer") | .frames[0]'
{
  "file": "/app/app.py",
  "line": 96,
  "function": "_ledger_writer",
  "locals": {
    "connection": "<sqlalchemy.engine.base.Connection object at 0xffff8d557bb0>"
  }
}
```

That `connection` is the second half of the damage. Both stuck threads
checked one out of the pool before they took a lock, and the pool is never
getting them back — which the shipped SQLAlchemy collector reports without
the application knowing anything about pidprobe:

```console
$ docker exec pidprobe-demo pidprobe snap 1 | jq '.sqlalchemy'
{
  "available": true,
  "max_pools": 50,
  "pool_count": 1,
  "pools": [
    {
      "type": "sqlalchemy.pool.impl.QueuePool",
      "size": 5,
      "checked_out": 2,
      "checked_in": 0,
      "overflow": -3
    }
  ]
}
```

Two of five connections gone, permanently. `meta` says what the snapshot cost
the target:

```console
$ docker exec pidprobe-demo pidprobe snap 1 | jq '.meta.stop_duration_ms, .meta.target'
24.258
{
  "pid": 1,
  "python_version": "3.14.6",
  "implementation": "cpython",
  "platform": "linux",
  "executable": "/usr/local/bin/python3.14"
}
```

## 6. Break it differently: the leak

The other failure is an unbounded cache. `POST /leak/start` runs a thread that
appends a `LeakedRecord` roughly every 20 ms and never evicts one:

```console
$ curl -s -X POST localhost:8000/leak/start
{"leaking":true,"thread":"report-cache","interval_seconds":0.02}
```

One snapshot cannot tell a leak from a large working set. `diff` can, because
it subtracts two of them — here three samples two seconds apart, so two
deltas, each printed as one JSON line as soon as it is ready:

```console
$ docker exec pidprobe-demo pidprobe diff 1 --interval 2 --count 3 \
    | jq -c '{ms: .meta.interval_ms, types: [.objects.types[] | select(.delta > 0)]}'
{"ms":2060.9,"types":[{"type":"app.LeakedRecord","before":null,"after":102,"delta":102},{"type":"list","before":2542,"after":2641,"delta":99},{"type":"tuple","before":12339,"after":12373,"delta":34},{"type":"function","before":23018,"after":23023,"delta":5},{"type":"cell","before":3630,"after":3633,"delta":3},{"type":"dict","before":14027,"after":14028,"delta":1}]}
{"ms":1993.824,"types":[{"type":"app.LeakedRecord","before":102,"after":192,"delta":90},{"type":"list","before":2641,"after":2721,"delta":80}]}
```

`app.LeakedRecord` climbs by about 95 every two seconds and never comes back
down; `function`, `cell` and `dict` wobble by single digits, which is what
normal noise looks like next to a real leak. A `before` of `null` means the
type was not in the earlier sample's top 50 at all — a good sign on its own.

Stopping the thread stops the growth. What already leaked stays leaked, on
purpose:

```console
$ curl -s -X POST localhost:8000/leak/stop
{"leaking":false,"leaked_records":196}
```

`eval` reads a single expression out of the same process, and confirms it
from the application's own point of view:

```console
$ docker exec pidprobe-demo pidprobe eval 1 'len(__import__("app").STATE.leaked)'
{"pid":1,"expression":"len(__import__(\"app\").STATE.leaked)","type":"int","result":"196","masking_enabled":true}
```

## 7. Clean up

```bash
docker stop pidprobe-demo
```

## What is actually in there

[`demo/app.py`](https://github.com/tomada1114/pidprobe/blob/main/demo/app.py)
is a hundred-odd lines and worth a read: it imports FastAPI and SQLAlchemy
and nothing else. It has no idea pidprobe exists, which is the point of the
whole exercise.

| Endpoint | What it does |
| --- | --- |
| `GET /health`, `GET /widgets` | work normally, before and after everything below |
| `POST /deadlock` | arms the lock-order inversion |
| `GET /reports/{name}` | normal until armed, then blocks forever |
| `POST /leak/start`, `POST /leak/stop` | start and stop the unbounded cache |
| `GET /status` | what is currently broken, including `pool.status()` |

The demo's dependencies are listed in `demo/requirements.txt` and deliberately
appear nowhere in `pyproject.toml`: pidprobe has no runtime dependencies and
the demo is not allowed to change that.

## Recording it

`scripts/record_demo.sh` drives the deadlock half of this page
non-interactively in about fifteen seconds — start, arm, hang a request,
`pidprobe snap 1 | jq` — and cleans up its own container. It is the quickest
way to check that this page is still true, and it is what the demo GIF is
recorded from:

```bash
docker build -t pidprobe-demo -f demo/Dockerfile .
asciinema rec demo.cast --overwrite --cols 100 --rows 30 -c scripts/record_demo.sh
agg --cols 100 --rows 30 demo.cast demo.gif
```
